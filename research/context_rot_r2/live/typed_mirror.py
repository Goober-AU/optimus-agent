"""Typed protected-state mirror (typed_state.rs + the W15 compaction wiring).

Python port of the W15 typed protected-state implementation so the live
harness can run the `typed` arm against the real GLM 5.3 endpoint with the
same policy the Rust pipeline executes:

- `crates/pi-coding-agent/src/core/compaction/typed_state.rs`
  (classification, reconcile, merge, allocate, render_allocation, verify);
  the D29 provenance-based supersession pairing (branch rot2/w41-d29,
  commit "fix(compaction): provenance-based supersession pairing"):
  two-tier token targets (non-correction kinds outrank corrections,
  typed_state.rs:1107) plus the provenance fallback
  (`provenance_target`, typed_state.rs:978) and the `retirePath` field
  (token | provenance) rendered as a pairing annotation
  (typed_state.rs:261/269/1770);
- the W38/W37 typed-state fixes, ported for R2-E5-RM (the integrated tip):
  D24 (commit 8bc0811fb) - identity is assigned at first sight and carried,
  so `reconcile` only fills empty record ids and a restatement that
  replaces a tool record's text keeps the id (the text is a version, not
  the identity); D23 (same commit) - the verifier's reference grammar
  matches exactly what the renderer emits (kind-prefixed record ids, `@`
  entry refs, no plain entry-shape heuristic) and a bracketed token quoted
  inside a ledger record's text is exempt as verbatim source material;
  D25 (commit a3997b5d6) - `merge` is the step boundary (a Step-scoped
  record carried from the previous ledger retires unless the new window
  restates it), retired ids/entry ids live in the bounded `retiredRefs`
  list and stay resolvable in `verify`, and every merged ledger is capped
  at `MAX_DURABLE_RECORDS` (512) with the cumulative drop count rendered;
  D22 (commit 51c9a6a59) - a superseded correction/task renders
  "(superseded by [retiring])", not "(supersedes [X])"; D27 (same commit)
  - `is_heading_line` admits structural shapes only (markdown/bold prefix
  or an exact render section title), so an all-caps claim line still goes
  through the fabricated-verified-claim check. D26 (the strict-parse
  salvage rider) is out of scope for the mirror: the research drivers
  always read well-formed `details.typedState` payloads, and the Rust
  salvage path only differs on payloads that fail strict deserialization;
- `crates/pi-coding-agent/src/core/compaction/compaction.rs`
  (TYPED_STATE_INSTRUCTION, typed_hot_state_budget_tokens, the unsafe
  allocation failure, the deterministic missing-item repair and the
  verification failure text; commit 338b920c3).

The port is line-faithful: every function below cites the Rust original it
mirrors, and the E5 run cross-validates the Python ledger against the Rust
adapter output on the same scenario (see live/e5/README.md).

Data shapes match the Rust serde JSON exactly (camelCase enums), so the
ledger persisted under details.typedState round-trips the session
serialize/reload path unchanged.

Documented deviation: the research Entry model carries no `is_error` flag
on tool results (the scenario schema has no such field), so every tool
result classifies as non-error, exactly as the v1 mirror does.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Vocabulary (typed_state.rs TypedInformationKind)
# ---------------------------------------------------------------------------

USER_CONSTRAINT = "userConstraint"
USER_CORRECTION = "userCorrection"
SAFETY_OR_PERMISSION_BOUNDARY = "safetyOrPermissionBoundary"
ACCEPTANCE_CRITERION = "acceptanceCriterion"
VERIFIED_DECISION = "verifiedDecision"
DECISION_RATIONALE = "decisionRationale"
CURRENT_TASK = "currentTask"
PENDING_ACTION = "pendingAction"
VERIFIED_TOOL_RESULT = "verifiedToolResult"
SOURCE_EVIDENCE = "sourceEvidence"
REPOSITORY_STATE = "repositoryState"
SUBAGENT_STATE = "subagentState"
HISTORICAL_FACT = "historicalFact"
LOW_PRIORITY_EPISODE = "lowPriorityEpisode"

_KINDS = (
    USER_CONSTRAINT, USER_CORRECTION, SAFETY_OR_PERMISSION_BOUNDARY, ACCEPTANCE_CRITERION,
    VERIFIED_DECISION, DECISION_RATIONALE, CURRENT_TASK, PENDING_ACTION, VERIFIED_TOOL_RESULT,
    SOURCE_EVIDENCE, REPOSITORY_STATE, SUBAGENT_STATE, HISTORICAL_FACT, LOW_PRIORITY_EPISODE,
)

# TypedInformationKind::prefix (two-letter id prefixes).
_PREFIX = {
    USER_CONSTRAINT: "UC",
    USER_CORRECTION: "CO",
    SAFETY_OR_PERMISSION_BOUNDARY: "SB",
    ACCEPTANCE_CRITERION: "AC",
    VERIFIED_DECISION: "VD",
    DECISION_RATIONALE: "DR",
    CURRENT_TASK: "TK",
    PENDING_ACTION: "PA",
    VERIFIED_TOOL_RESULT: "VT",
    SOURCE_EVIDENCE: "SE",
    REPOSITORY_STATE: "RS",
    SUBAGENT_STATE: "SS",
    HISTORICAL_FACT: "HF",
    LOW_PRIORITY_EPISODE: "LP",
}

# TypedInformationKind::label (human-readable, used by the durable index).
_LABEL = {
    USER_CONSTRAINT: "USER_CONSTRAINT",
    USER_CORRECTION: "USER_CORRECTION",
    SAFETY_OR_PERMISSION_BOUNDARY: "SAFETY_OR_PERMISSION_BOUNDARY",
    ACCEPTANCE_CRITERION: "ACCEPTANCE_CRITERION",
    VERIFIED_DECISION: "VERIFIED_DECISION",
    DECISION_RATIONALE: "DECISION_RATIONALE",
    CURRENT_TASK: "CURRENT_TASK",
    PENDING_ACTION: "PENDING_ACTION",
    VERIFIED_TOOL_RESULT: "VERIFIED_TOOL_RESULT",
    SOURCE_EVIDENCE: "SOURCE_EVIDENCE",
    REPOSITORY_STATE: "REPOSITORY_STATE",
    SUBAGENT_STATE: "SUBAGENT_STATE",
    HISTORICAL_FACT: "HISTORICAL_FACT",
    LOW_PRIORITY_EPISODE: "LOW_PRIORITY_EPISODE",
}


# D25 (typed_state.rs): the hard durable bound on every merged ledger, and
# the bound on retired-reference tombstones kept for reference resolution.
MAX_DURABLE_RECORDS = 512
RETIRED_REFS_MAX = 512


def kind_prefix(kind: str) -> str:
    return _PREFIX[kind]


def kind_label(kind: str) -> str:
    return _LABEL[kind]


def kind_tier(kind: str) -> int:
    """TypedInformationKind::tier (allocator priority, lower fits first)."""
    if kind in (USER_CONSTRAINT, SAFETY_OR_PERMISSION_BOUNDARY, ACCEPTANCE_CRITERION):
        return 0
    if kind == USER_CORRECTION:
        return 1
    if kind in (CURRENT_TASK, PENDING_ACTION):
        return 2
    if kind in (VERIFIED_DECISION, DECISION_RATIONALE):
        return 3
    if kind in (VERIFIED_TOOL_RESULT, SOURCE_EVIDENCE):
        return 4
    if kind in (REPOSITORY_STATE, SUBAGENT_STATE):
        return 5
    if kind == HISTORICAL_FACT:
        return 6
    return 7  # LOW_PRIORITY_EPISODE


def kind_scope(kind: str) -> str:
    """TypedInformationKind::scope."""
    if kind in (
        USER_CONSTRAINT, USER_CORRECTION, SAFETY_OR_PERMISSION_BOUNDARY, ACCEPTANCE_CRITERION,
        VERIFIED_TOOL_RESULT, SOURCE_EVIDENCE, REPOSITORY_STATE, HISTORICAL_FACT,
    ):
        return "session"
    if kind in (VERIFIED_DECISION, DECISION_RATIONALE, CURRENT_TASK, SUBAGENT_STATE):
        return "task"
    return "step"  # PENDING_ACTION, LOW_PRIORITY_EPISODE


def is_mandatory(kind: str) -> bool:
    """compaction.rs is_mandatory via typed_state allocator tiers."""
    return kind in (USER_CONSTRAINT, SAFETY_OR_PERMISSION_BOUNDARY, ACCEPTANCE_CRITERION, CURRENT_TASK)


CURRENT = "current"
SUPERSEDED = "superseded"

# typed_state.rs SupersessionPath (D29, typed_state.rs:261): which pairing
# path matched when a correction retired a record.
TOKEN = "token"
PROVENANCE = "provenance"

# SupersessionPath::pairing_note (typed_state.rs:269): the annotation
# render_allocation appends after a paired record's line.
_PAIRING_NOTE = {TOKEN: "token-overlap pairing", PROVENANCE: "provenance pairing"}

VERIFIED = "verified"
CORROBORATED = "corroborated"
UNVERIFIED = "unverified"

_VERIFICATION_RANK = {VERIFIED: 0, CORROBORATED: 1, UNVERIFIED: 2}

# ---------------------------------------------------------------------------
# Classification regexes (typed_state.rs, verbatim patterns)
# ---------------------------------------------------------------------------

SAFETY_MODAL_RE = re.compile(
    r"(?i)\b(never|must\s+not|do\s+not|don'?t|no)\s+(share|send|upload|leak|expose|commit|push|force-?push|log|print)\b"
)
SAFETY_OBJECT_RE = re.compile(
    r"(?i)\b(api\s*keys?|credentials?|passwords?|secrets?|ssh\s+keys?|private\s+keys?|access\s*tokens?|personal\s+data|pii|customer\s+data|\.env)\b"
)
SAFETY_PROCESS_RE = re.compile(
    r"(?i)\b(ask|confirm|check)\s+(me\s+)?(first|before)\b|\bwithout\s+(asking|permission|approval|confirmation)\b|\b(rm\s+-rf|force-?push|push(ing)?\s+to\s+main|drop\s+(table|database)|factory\s+reset)\b"
)
CORRECTION_RE = re.compile(
    r"(?i)\b(actually|instead|correction|forget\s+(that|the|this|it|about)|scratch\s+that|no\s+longer|change\s+of\s+plan|on\s+second\s+thought|rather\s+than|revert|was\s+wrong|drop\s+(that|the|this))\b"
)
ACCEPTANCE_RE = re.compile(
    r"(?i)\b(acceptance\s+criteria|definition\s+of\s+done|success\s+criteria|done\s+when|complete[sd]?\s+when|finished(\s+only)?\s+when|must\s+pass|verify\s+that|sign-?off|(acceptance|exit)\s+gate|before\s+(you\s+)?(finish|commit|declaring|closing))\b"
)
CONSTRAINT_RE = re.compile(
    r"(?i)\b(must(\s+not)?|never|always|do\s+not|don'?t|shall|only|keep|frozen|unchanged|not\s+exceed|no\s+more\s+than|should\s+be|required|has\s+to|make\s+sure|be\s+sure)\b"
)
TASK_RE = re.compile(
    r"(?i)\b((the\s+)?(task|goal|mission|assignment)\s+(is|was|for)|your\s+(task|job|mission)|I\s+(want|need|would\s+like)\s+you\s+to|please\s+(implement|build|create|write|design|migrate|port|refactor)|(implement|build|create|write|design|migrate|port|refactor)\b)"
)
PENDING_RE = re.compile(
    r"(?i)\b(after\s+that|next\b|don'?t\s+forget|remember\s+to|still\s+(need|have\s+to)|left\s+to\s+do|todo|follow\s+up|before\s+you\s+(finish|commit|close)|when\s+(you|that)('s|s)?\s+(done|finished)|once\s+(you|that))\b"
)
HISTORICAL_RE = re.compile(
    r"(?i)\b(last\s+(time|week|month|year|night|campaign)|yesterday|previously|earlier|used\s+to|we\s+(tried|used)\s|in\s+the\s+(past|previous)|history|originally|prior\s+to|before\s+this)\b"
)
DECISION_RE = re.compile(
    r"(?i)\b(decision\b|decided|chose|chosen|going\s+with|we\s+(will|'ll)\s+use|I('ll|\s+will)\s+use|approach\s+(is|will\s+be)|plan\s+is\s+to|opted\s+for|settle[d]?\s+on)\b"
)
RATIONALE_RE = re.compile(
    r"(?i)^(rationale|reasoning)\b|\b(because|the\s+reason\s+(is|was)|this\s+(lets|allows|avoids|prevents|ensures)|so\s+that|which\s+avoids|why\s+we)\b"
)
TASK_ASSISTANT_RE = re.compile(
    r"(?i)\b(I\s+am\s+(now\s+)?(implementing|working\s+on|writing|adding|refactoring|migrating)|I'?m\s+(now\s+)?(implementing|working)|currently\s+(implementing|working|writing|adding)|next\s+step\s+is)\b"
)
SUBAGENT_RE = re.compile(
    r"(?i)\b(sub-?agents?|child\s+agents?|worker\s+agents?|delegat(ed|ion)|spawned\s+agents?|agent\s+report|subtask\s+report|RLM_CHILD_STATUS)\b"
)
EVIDENCE_LINE_RE = re.compile(
    r"(?i)(test\s+result:|tests?\s+(failed|passed)|FAILED|panicked\s+at|assertion.*failed|error(\[E\d+\])?:|-->|exit\s+(code|status)|Traceback\s+\(most\s+recent)"
)
SOURCE_LINE_RE = re.compile(
    r"(?im)^\s*(pub\s|fn\s|use\s|impl\s|struct\s|enum\s|mod\s|const\s|let\s|match\s|async\s|trait\s|type\s|\+\+\+|---|@@|```|//|/\*)|::|->"
)
REPO_LINE_RE = re.compile(
    r"(?i)\b(on\s+branch|nothing\s+to\s+commit|files?\s+changed|insertions?\s*\(|deletions?\s*\(|modified:|untracked|working\s+tree|HEAD|staged|no\s+changes\s+added)\b"
)
CLAUSE_SPLIT_RE = re.compile(r"\s\(\d\)\s")
TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_]{2,}")
ANCHOR_RE = re.compile(r"[A-Za-z0-9_./\-]{4,}")
CAMEL_RE = re.compile(r"[a-z][A-Z]")

# KEY_STOPWORDS: words ignored by the content key.
KEY_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "is", "are", "was", "were", "be", "been", "to", "of", "in", "on", "for", "with", "without",
    "by", "from", "as", "at", "it", "its", "their", "our", "your", "we", "you", "i", "they", "he",
    "she", "must", "shall", "should", "will", "would", "could", "cannot", "can", "may", "might",
    "please",
})

# CORRECTION_STOPWORDS: additionally ignored while scoring supersession.
CORRECTION_STOPWORDS = frozenset({
    "actually", "instead", "correction", "forget", "scratch", "change", "plan", "drop", "revert",
    "reverted", "rather", "than", "replace", "replaced", "wait", "sorry", "second", "thought",
    "longer", "now", "new", "wrong", "idea",
})

SCORING_STOPWORDS = KEY_STOPWORDS | CORRECTION_STOPWORDS

ITEM_TEXT_MAX = 320
TOOL_TEXT_MAX = 200
TOOL_EVIDENCE_LINES = 4
TOOL_SOURCE_LINES = 3
TOOL_REPO_LINES = 3
MIN_UNIT_CHARS = 12
MIN_EPISODE_CHARS = 24

# ---------------------------------------------------------------------------
# Unit splitting (typed_state.rs)
# ---------------------------------------------------------------------------


def sentences(text: str) -> List[str]:
    """typed_state.rs sentences(): sentence ends after .!? + whitespace; every newline ends one."""
    out: List[str] = []
    current: List[str] = []
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch == "\n":
            out.append("".join(current))
            current = []
            continue
        current.append(ch)
        if ch in ".!?" and i + 1 < len(chars) and chars[i + 1].isspace():
            out.append("".join(current))
            current = []
    out.append("".join(current))
    return [part.strip() for part in out if part.strip()]


def split_clauses(sentence: str) -> List[str]:
    """typed_state.rs split_clauses(): split enumerated clauses on ` (1) ` markers."""
    if not CLAUSE_SPLIT_RE.search(f" {sentence} "):
        return [sentence]
    prefixed = f" {sentence}"
    out: List[str] = []
    for index, part in enumerate(CLAUSE_SPLIT_RE.split(prefixed)):
        part = part.strip()
        if not part:
            continue
        if index == 0 and len(part) <= 8:
            continue
        out.append(part)
    return out if out else [sentence.strip()]


def user_units(text: str) -> List[str]:
    return [
        unit
        for sentence in sentences(text)
        for unit in split_clauses(sentence)
        if len(unit) >= MIN_UNIT_CHARS
    ]


def assistant_units(text: str) -> List[str]:
    return [unit for unit in sentences(text) if len(unit) >= MIN_UNIT_CHARS]


# ---------------------------------------------------------------------------
# Entry shapes (typed_state.rs entry_role_text)
# ---------------------------------------------------------------------------


def entry_role_text(entry: Any) -> Optional[Tuple[str, str, bool, str]]:
    """(role, text, is_error, tool_name); only Message entries classify.

    The mirror Entry carries no is_error flag (documented deviation), so
    tool results are always non-error here.
    """
    if not getattr(entry, "is_message", False):
        return None
    if entry.kind == "user":
        return ("user", entry.text, False, "")
    if entry.kind == "assistant":
        if not entry.text.strip():
            return None
        return ("assistant", entry.text, False, "")
    return ("tool", entry.text, False, entry.tool_name)


# ---------------------------------------------------------------------------
# Unit classification (typed_state.rs classify_*_unit)
# ---------------------------------------------------------------------------

_IMPERATIVE_VERBS = (
    "fix", "add", "run", "make", "set", "update", "read", "study", "test", "prepare",
    "investigate", "analyze", "configure", "extend", "clean", "implement", "build", "create",
    "write", "design", "migrate", "port", "refactor",
)


def starts_with_imperative(unit: str) -> bool:
    words = unit.split()
    if not words:
        return False
    first = words[0].strip("".join(c for c in words[0] if not c.isalnum()))
    if not first:
        return False
    return first.lower() in _IMPERATIVE_VERBS


def classify_user_unit(unit: str, first_user_unit: bool) -> Optional[str]:
    if (
        SAFETY_MODAL_RE.search(unit)
        or SAFETY_PROCESS_RE.search(unit)
        or (SAFETY_OBJECT_RE.search(unit) and CONSTRAINT_RE.search(unit))
    ):
        return SAFETY_OR_PERMISSION_BOUNDARY
    if CORRECTION_RE.search(unit):
        return USER_CORRECTION
    if ACCEPTANCE_RE.search(unit):
        return ACCEPTANCE_CRITERION
    if PENDING_RE.search(unit):
        return PENDING_ACTION
    if CONSTRAINT_RE.search(unit):
        return USER_CONSTRAINT
    if TASK_RE.search(unit) or (first_user_unit and starts_with_imperative(unit)):
        return CURRENT_TASK
    if HISTORICAL_RE.search(unit):
        return HISTORICAL_FACT
    if len(unit) >= MIN_EPISODE_CHARS:
        return LOW_PRIORITY_EPISODE
    return None


def classify_assistant_unit(unit: str) -> Optional[str]:
    if DECISION_RE.search(unit):
        return VERIFIED_DECISION
    if RATIONALE_RE.search(unit):
        return DECISION_RATIONALE
    if TASK_ASSISTANT_RE.search(unit):
        return CURRENT_TASK
    if PENDING_RE.search(unit):
        return PENDING_ACTION
    if HISTORICAL_RE.search(unit):
        return HISTORICAL_FACT
    if len(unit) >= MIN_EPISODE_CHARS:
        return LOW_PRIORITY_EPISODE
    return None


def is_subagent_tool_name(tool_name: str) -> bool:
    lower = tool_name.lower()
    return any(hint in lower for hint in ("subagent", "agent", "delegate", "spawn", "worker", "child"))


def clip(text: str, limit: int) -> str:
    squeezed = " ".join(text.split())
    if len(squeezed) <= limit:
        return squeezed
    return squeezed[: limit - 1] + "\u2026"


def classify_tool_text(text: str, is_error: bool, tool_name: str) -> List[Tuple[str, str]]:
    """(kind, text) per retained line; typed_state.rs classify_tool_text."""
    if SUBAGENT_RE.search(text) or is_subagent_tool_name(tool_name):
        return [
            (SUBAGENT_STATE, clip(line, ITEM_TEXT_MAX))
            for line in text.split("\n")
            if len(line.strip()) >= MIN_UNIT_CHARS
        ][:1]
    if is_error:
        return [
            (VERIFIED_TOOL_RESULT, clip(line, TOOL_TEXT_MAX))
            for line in text.split("\n")
            if len(line.strip()) >= MIN_UNIT_CHARS
        ][:2]
    out: List[Tuple[str, str]] = []
    evidence = source = repo = 0
    for line in text.split("\n"):
        trimmed = line.strip()
        if len(trimmed) < MIN_UNIT_CHARS:
            continue
        if EVIDENCE_LINE_RE.search(trimmed) and evidence < TOOL_EVIDENCE_LINES:
            evidence += 1
            out.append((VERIFIED_TOOL_RESULT, clip(trimmed, TOOL_TEXT_MAX)))
            continue
        if SOURCE_LINE_RE.search(trimmed) and source < TOOL_SOURCE_LINES:
            source += 1
            out.append((SOURCE_EVIDENCE, clip(trimmed, TOOL_TEXT_MAX)))
            continue
        if REPO_LINE_RE.search(trimmed) and repo < TOOL_REPO_LINES:
            repo += 1
            out.append((REPOSITORY_STATE, clip(trimmed, TOOL_TEXT_MAX)))
    return out


# ---------------------------------------------------------------------------
# Keys, ids, small helpers (typed_state.rs)
# ---------------------------------------------------------------------------


def content_tokens(text: str) -> Set[str]:
    return {m.group(0).lower() for m in TOKEN_RE.finditer(text)}


def content_key(text: str) -> str:
    return " ".join(sorted(t for t in content_tokens(text) if t not in KEY_STOPWORDS))


def scoring_tokens(text: str) -> Set[str]:
    return {t for t in content_tokens(text) if t not in SCORING_STOPWORDS}


def anchors(text: str) -> Set[str]:
    out: Set[str] = set()
    for m in ANCHOR_RE.finditer(text):
        token = m.group(0).strip(",.;:!?'\u201c\u201d")
        if (
            "/" in token
            or "." in token
            or "_" in token
            or any(c.isdigit() for c in token)
            or CAMEL_RE.search(token)
        ):
            out.add(token)
    return out


def estimate_tokens(text: str) -> int:
    """typed_state.rs estimate_tokens: characters divided by four, rounded up."""
    return -(-len(text) // 4)


def normalized(text: str) -> str:
    return " ".join(text.split()).lower()


def hex_prefix(digest: bytes) -> str:
    return "".join(f"{byte:02x}" for byte in digest[:3])


def stable_id(kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{kind_prefix(kind)}:{key}".encode("utf-8")).digest()
    return f"{kind_prefix(kind)}-{hex_prefix(digest)}"


# ---------------------------------------------------------------------------
# Window classification and reconcile (typed_state.rs)
# ---------------------------------------------------------------------------


def new_record(kind: str, entry_id: str, role: str, ordinal: int, text: str, order: int) -> Dict[str, Any]:
    limit, verification = (
        (TOOL_TEXT_MAX, VERIFIED) if role == "tool"
        else (ITEM_TEXT_MAX, VERIFIED) if role == "user"
        else (ITEM_TEXT_MAX, UNVERIFIED)
    )
    if role != "tool":
        text = clip(text, limit)
    return {
        "id": "",
        "kind": kind,
        "sourceEntryId": entry_id,
        "sourceRole": role,
        "ordinal": ordinal,
        "text": text,
        "status": CURRENT,
        "scope": kind_scope(kind),
        "verification": verification,
        "revision": {"entryId": entry_id, "order": order},
        "link": None,
        "retirePath": None,
    }


def classify_window(entries: Sequence[Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    ordinal = 0
    seen_user_unit = False
    for entry in entries:
        role_text = entry_role_text(entry)
        if role_text is None:
            continue
        role, text, is_error, tool_name = role_text
        entry_id = entry.id
        if role == "user":
            for order, unit in enumerate(user_units(text)):
                first = not seen_user_unit
                seen_user_unit = True
                kind = classify_user_unit(unit, first)
                if kind is None:
                    continue
                records.append(new_record(kind, entry_id, role, ordinal, unit, order))
                ordinal += 1
        elif role == "assistant":
            for order, unit in enumerate(assistant_units(text)):
                kind = classify_assistant_unit(unit)
                if kind is None:
                    continue
                records.append(new_record(kind, entry_id, role, ordinal, unit, order))
                ordinal += 1
        else:
            for order, (kind, line) in enumerate(classify_tool_text(text, is_error, tool_name)):
                records.append(new_record(kind, entry_id, role, ordinal, line, order))
                ordinal += 1
    return records


def token_set_from_key(key: str) -> Set[str]:
    return {token for token in key.split(" ") if token}


def token_set(text: str) -> Set[str]:
    return token_set_from_key(content_key(text))


def is_restatement(a: Set[str], b: Set[str]) -> bool:
    shared = len(a & b)
    return shared >= 3 and shared == min(len(a), len(b))


def supersession_target_kinds(kind: str) -> bool:
    return kind in (
        USER_CONSTRAINT, USER_CORRECTION, SAFETY_OR_PERMISSION_BOUNDARY, ACCEPTANCE_CRITERION,
        VERIFIED_DECISION, CURRENT_TASK, PENDING_ACTION,
    )


def provenance_target(out: List[Dict[str, Any]], slot: int) -> Optional[int]:
    """typed_state.rs provenance_target() (D29, typed_state.rs:978): the most
    recent prior CURRENT decision, when the correction is the only un-retired
    correction introduced since that decision and no other decision
    intervened between them. The correction's position after the decision is
    the pairing evidence, not token overlap; the two guards keep the path
    from firing when the positional evidence is ambiguous."""
    correction_ordinal = out[slot]["ordinal"]
    candidates = [
        (candidate_slot, record)
        for candidate_slot, record in enumerate(out)
        if record["kind"] == VERIFIED_DECISION
        and record["status"] == CURRENT
        and record["ordinal"] < correction_ordinal
    ]
    if not candidates:
        return None
    target_slot, target = max(candidates, key=lambda pair: pair[1]["ordinal"])
    target_ordinal = target["ordinal"]
    decision_intervened = any(
        record["kind"] == VERIFIED_DECISION
        and target_ordinal < record["ordinal"] < correction_ordinal
        for record in out
    )
    sibling_correction = any(
        record["kind"] == USER_CORRECTION
        and record["status"] == CURRENT
        and target_ordinal < record["ordinal"] < correction_ordinal
        for record in out
    )
    if decision_intervened or sibling_correction:
        return None
    return target_slot


def reconcile(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """typed_state.rs reconcile(): ids, dedupe, supersession, succession,
    revival, corroboration, rationale links."""
    records = sorted(records, key=lambda record: record["ordinal"])

    # Dedupe by (kind, key), keeping first-seen identity.
    index: Dict[Tuple[str, str], int] = {}
    out: List[Dict[str, Any]] = []
    restated: List[List[int]] = []
    for record in records:
        key = content_key(record["text"])
        if not key:
            continue
        record_tokens = token_set_from_key(key)
        slot = index.get((record["kind"], key))
        if slot is None:
            for position, existing in enumerate(out):
                if existing["kind"] == record["kind"] and is_restatement(
                    token_set(existing["text"]), record_tokens
                ):
                    slot = position
                    break
        if slot is not None:
            existing = out[slot]
            existing["revision"] = dict(record["revision"])
            if _VERIFICATION_RANK[record["verification"]] < _VERIFICATION_RANK[existing["verification"]]:
                existing["verification"] = record["verification"]
            if record["kind"] in (
                VERIFIED_TOOL_RESULT, SOURCE_EVIDENCE, REPOSITORY_STATE, SUBAGENT_STATE,
            ):
                existing["text"] = record["text"]
            existing["status"] = record["status"]
            existing["link"] = record["link"]
            existing["retirePath"] = record.get("retirePath")
            restated[slot].append(record["ordinal"])
        else:
            record = dict(record)
            # D24 (typed_state.rs, commit 8bc0811fb): identity is assigned
            # at first sight and then carried. A record that arrives with
            # an id (persisted from a previous cycle) keeps it even when a
            # restatement replaced its text; recomputing from the current
            # text would churn the id across merges and strand the [id]
            # references earlier summaries carry.
            if not record["id"]:
                record["id"] = stable_id(record["kind"], key)
            index[(record["kind"], key)] = len(out)
            restated.append([record["ordinal"]])
            out.append(record)

    # Resolve id collisions deterministically.
    used: Dict[str, int] = {}
    for slot in range(len(out)):
        record_id = out[slot]["id"]
        owner = used.get(record_id)
        if owner is not None and owner != slot:
            key = content_key(out[slot]["text"])
            attempt = 1
            while True:
                digest = hashlib.sha256(
                    f"{kind_prefix(out[slot]['kind'])}:{key}#{attempt}".encode("utf-8")
                ).digest()
                candidate = f"{kind_prefix(out[slot]['kind'])}-{hex_prefix(digest)}"
                if candidate not in used:
                    out[slot]["id"] = candidate
                    break
                attempt += 1
        used[out[slot]["id"]] = slot

    # Supersession (D29, typed_state.rs:1088): a user correction retires the
    # best earlier CURRENT record by token overlap, or by provenance position
    # when no token target qualifies. Corrections never outrank other kinds as
    # token targets, so a later correction retires its stale decision or
    # requirement instead of swallowing the previous correction.
    correction_slots = [
        slot for slot, record in enumerate(out)
        if record["kind"] == USER_CORRECTION and record["link"] is None
    ]
    for slot in correction_slots:
        correction_text = out[slot]["text"]
        correction_ordinal = out[slot]["ordinal"]
        correction_id = out[slot]["id"]
        correction_toks = scoring_tokens(correction_text)
        correction_anchors = anchors(correction_text)
        # Best token target per tier (typed_state.rs:1107): non-correction
        # kinds outrank corrections, so the sibling-correction overlap never
        # wins while a qualifying decision or requirement stays CURRENT.
        best_primary: Optional[Tuple[int, int]] = None
        best_correction: Optional[Tuple[int, int]] = None
        for candidate_slot, candidate in enumerate(out):
            if (
                candidate_slot == slot
                or candidate["status"] != CURRENT
                or not supersession_target_kinds(candidate["kind"])
                or candidate["ordinal"] >= correction_ordinal
            ):
                continue
            candidate_toks = scoring_tokens(candidate["text"])
            shared = correction_toks & candidate_toks
            specific = sum(
                1
                for token in shared
                if "_" in token
                or "/" in token
                or "." in token
                or any(c.isdigit() for c in token)
                or len(token) >= 8
            )
            shared_anchors = len(correction_anchors & anchors(candidate["text"]))
            score = 3 * shared_anchors + len(shared)
            qualifies = (shared_anchors >= 1 and score >= 4) or (specific >= 1 and len(shared) >= 2)
            if not qualifies:
                continue
            best = best_correction if candidate["kind"] == USER_CORRECTION else best_primary
            if score > (best[1] if best else 0):
                if candidate["kind"] == USER_CORRECTION:
                    best_correction = (candidate_slot, score)
                else:
                    best_primary = (candidate_slot, score)
        token_target = best_primary if best_primary is not None else best_correction
        if token_target is not None:
            target_slot = token_target[0]
            path = TOKEN
        else:
            # No token target (typed_state.rs:1152): retire the most recent
            # prior decision the correction positionally replaces.
            provenance = provenance_target(out, slot)
            if provenance is None:
                continue
            target_slot = provenance
            path = PROVENANCE
        target_id = out[target_slot]["id"]
        out[target_slot]["status"] = SUPERSEDED
        out[target_slot]["link"] = correction_id
        out[target_slot]["retirePath"] = path
        out[slot]["link"] = target_id
        out[slot]["retirePath"] = path

    # Task succession: a newer user CURRENT_TASK retires earlier CURRENT_TASKs.
    task_slots = [
        slot for slot, record in enumerate(out)
        if record["kind"] == CURRENT_TASK
        and record["sourceRole"] == "user"
        and record["status"] == CURRENT
    ]
    for slot in task_slots:
        task_ordinal = out[slot]["ordinal"]
        task_id = out[slot]["id"]
        for candidate_slot, candidate in enumerate(out):
            if (
                candidate_slot == slot
                or candidate["kind"] != CURRENT_TASK
                or candidate["status"] != CURRENT
                or candidate["ordinal"] >= task_ordinal
            ):
                continue
            candidate["status"] = SUPERSEDED
            candidate["link"] = task_id

    # Revival: a restatement later than the retiring record revives the target.
    id_to_ordinal = {record["id"]: record["ordinal"] for record in out}
    for slot in range(len(out)):
        if out[slot]["status"] != SUPERSEDED:
            continue
        link = out[slot]["link"]
        if link is None:
            continue
        retiring_ordinal = id_to_ordinal.get(link)
        if retiring_ordinal is None:
            continue
        if any(ordinal > retiring_ordinal for ordinal in restated[slot]):
            out[slot]["status"] = CURRENT
            out[slot]["link"] = None
            out[slot]["retirePath"] = None

    # Corroboration: an assistant decision a later verified tool record supports.
    for slot in range(len(out)):
        if out[slot]["kind"] != VERIFIED_DECISION or out[slot]["verification"] != UNVERIFIED:
            continue
        decision_anchors = anchors(out[slot]["text"])
        decision_toks = scoring_tokens(out[slot]["text"])
        decision_ordinal = out[slot]["ordinal"]
        corroborated = any(
            candidate["kind"] in (VERIFIED_TOOL_RESULT, SOURCE_EVIDENCE, REPOSITORY_STATE)
            and candidate["ordinal"] > decision_ordinal
            and (
                len(anchors(candidate["text"]) & decision_anchors) >= 1
                or len(scoring_tokens(candidate["text"]) & decision_toks) >= 3
            )
            for candidate in out
        )
        if corroborated:
            out[slot]["verification"] = CORROBORATED

    # Rationale links: point each DECISION_RATIONALE at the latest earlier decision.
    last_decision: Optional[str] = None
    for record in out:
        if record["kind"] == VERIFIED_DECISION:
            last_decision = record["id"]
        elif record["kind"] == DECISION_RATIONALE and record["link"] is None:
            record["link"] = last_decision

    return {"records": out}


def ledger_is_empty(state: Dict[str, Any]) -> bool:
    """typed_state.rs TypedStateLedger::is_empty (D25): records and retired
    reference tombstones both count, so a ledger that only carries retired
    refs is still a ledger."""
    return not (state["records"] or state.get("retiredRefs"))


def build_typed_state(entries: Sequence[Any]) -> Dict[str, Any]:
    return reconcile(classify_window(entries))


def restated_in(record: Dict[str, Any], new_keys: Sequence[Tuple[str, str, Set[str]]]) -> bool:
    """typed_state.rs restated_in (D25, a3997b5d6): does any new-window record
    restate `record`? Mirrors the reconcile dedupe rule (same kind, equal
    content key or a subset restatement with at least three shared tokens)
    so exactly the records reconcile would merge are carried across the
    step boundary."""
    key = content_key(record["text"])
    tokens = token_set_from_key(key)
    return any(
        kind == record["kind"] and (candidate_key == key or is_restatement(tokens, candidate_tokens))
        for kind, candidate_key, candidate_tokens in new_keys
    )


def retire_ref(retired_refs: List[str], reference: str) -> None:
    """typed_state.rs retire_ref (D25): append one retired reference,
    keeping the list duplicate-free."""
    if reference not in retired_refs:
        retired_refs.append(reference)


def cap_drop_rank(record: Dict[str, Any]) -> Tuple[int, int]:
    """typed_state.rs cap_drop_rank (D25): drop-first rank for the durable
    cap. Superseded history drops before any CURRENT record (the allocator
    renders it last), then higher (less protected) tiers drop first, then
    the oldest. Deterministic."""
    effective_tier = 8 if record["status"] != CURRENT else kind_tier(record["kind"])
    return (effective_tier, kind_tier(record["kind"]))


def enforce_durable_cap(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """typed_state.rs enforce_durable_cap (D25): the hard durable bound.
    Records above MAX_DURABLE_RECORDS drop in cap_drop_rank order, their
    ids and entry ids are retired (so existing references keep resolving),
    the cumulative count is kept in droppedByCap, and retiredRefs is trimmed
    to the RETIRED_REFS_MAX most recent entries."""
    if len(ledger["records"]) > MAX_DURABLE_RECORDS:
        drop_count = len(ledger["records"]) - MAX_DURABLE_RECORDS
        order = sorted(
            range(len(ledger["records"])),
            key=lambda slot: (
                -cap_drop_rank(ledger["records"][slot])[0],
                -cap_drop_rank(ledger["records"][slot])[1],
                ledger["records"][slot]["ordinal"],
            ),
        )
        drop_indexes = set(order[:drop_count])
        retired_refs = list(ledger.get("retiredRefs") or [])
        kept = []
        for index, record in enumerate(ledger["records"]):
            if index in drop_indexes:
                retire_ref(retired_refs, record["id"])
                retire_ref(retired_refs, record["sourceEntryId"])
            else:
                kept.append(record)
        ledger["records"] = kept
        ledger["droppedByCap"] = (ledger.get("droppedByCap") or 0) + drop_count
        if retired_refs:
            ledger["retiredRefs"] = retired_refs
    overflow = len(ledger.get("retiredRefs") or []) - RETIRED_REFS_MAX
    if overflow > 0:
        ledger["retiredRefs"] = ledger["retiredRefs"][overflow:]
    return ledger


def merge(previous: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    """typed_state.rs merge(): the D25 step boundary, then reconcile.
    A Step-scoped record carried from the previous ledger (a window that
    has been summarized) retires unless the new window restates it; new
    records are renumbered after the previous maximum ordinal and reconcile
    re-runs over the combination. Retired record ids and source entry ids
    move to the bounded retiredRefs list; the durable cap applies to every
    merged ledger."""
    if previous is None:
        return enforce_durable_cap(new)
    # Step boundary: a restatement in the new window keeps the previous
    # record alive as the current step's record; every other Step-scoped
    # record from `previous` retires here.
    new_keys = [
        (record["kind"], content_key(record["text"]), token_set(record["text"]))
        for record in new["records"]
    ]
    base = (max((record["ordinal"] for record in previous["records"]), default=-1)) + 1
    retired_refs = list(previous.get("retiredRefs") or [])
    combined: List[Dict[str, Any]] = []
    for record in previous["records"]:
        if record["scope"] == "step" and not restated_in(record, new_keys):
            retire_ref(retired_refs, record["id"])
            retire_ref(retired_refs, record["sourceEntryId"])
            continue
        combined.append(dict(record))
    new_records = sorted(new["records"], key=lambda record: record["ordinal"])
    for offset, record in enumerate(new_records):
        record = dict(record)
        record["ordinal"] = base + offset
        combined.append(record)
    merged = reconcile(combined)
    if retired_refs:
        merged["retiredRefs"] = retired_refs
    if previous.get("droppedByCap"):
        merged["droppedByCap"] = previous["droppedByCap"]
    return enforce_durable_cap(merged)


# ---------------------------------------------------------------------------
# Budget allocator (typed_state.rs allocate)
# ---------------------------------------------------------------------------


def section_title(kind: str, status: str) -> str:
    if status != CURRENT:
        return "SUPERSEDED (HISTORY, NOT CURRENT)"
    return {
        USER_CONSTRAINT: "ACTIVE CONSTRAINTS",
        USER_CORRECTION: "USER CORRECTIONS",
        SAFETY_OR_PERMISSION_BOUNDARY: "SAFETY BOUNDARIES",
        ACCEPTANCE_CRITERION: "ACCEPTANCE CRITERIA",
        VERIFIED_DECISION: "DECISIONS (CURRENT)",
        DECISION_RATIONALE: "DECISION RATIONALE",
        CURRENT_TASK: "CURRENT TASK",
        PENDING_ACTION: "PENDING ACTIONS",
        VERIFIED_TOOL_RESULT: "VERIFIED EVIDENCE",
        SOURCE_EVIDENCE: "SOURCE EVIDENCE",
        REPOSITORY_STATE: "REPOSITORY STATE",
        SUBAGENT_STATE: "SUBAGENT STATE",
        HISTORICAL_FACT: "HISTORICAL FACTS",
        LOW_PRIORITY_EPISODE: "LOW-PRIORITY EPISODES",
    }[kind]


def _verification_tag(verification: str) -> str:
    return {"verified": "verified", "corroborated": "corroborated", "unverified": "unverified"}[verification]


def record_line(record: Dict[str, Any]) -> str:
    """typed_state.rs record_line (the rendered allocation line)."""
    entry_ref = f"[@{record['sourceEntryId']}]"
    kind = record["kind"]
    if kind == VERIFIED_DECISION:
        return f"- [{record['id']}] {entry_ref} {_verification_tag(record['verification'])}: {record['text']}"
    if kind == DECISION_RATIONALE:
        if record["link"]:
            return f"- [{record['id']}] {entry_ref} (for [{record['link']}]): {record['text']}"
        return f"- [{record['id']}] {entry_ref}: {record['text']}"
    if kind in (USER_CORRECTION, CURRENT_TASK):
        # D22 (typed_state.rs, commit 51c9a6a59): the link direction
        # depends on the status. A CURRENT correction/task points at the
        # record it retired ("supersedes"); reconcile overwrites a retired
        # record's link with the id of the record that retired it
        # ("superseded by"). Rendering the kind arm before the status arm
        # inverted the retirement direction for superseded corrections and
        # tasks. No link: the clause is omitted rather than guessed.
        if record["status"] == CURRENT and record["link"]:
            return f"- [{record['id']}] {entry_ref} {record['text']} (supersedes [{record['link']}])"
        if record["status"] != CURRENT and record["link"]:
            return f"- [{record['id']}] {record['text']} (superseded by [{record['link']}])"
        return f"- [{record['id']}] {entry_ref} {record['text']}"
    if record["status"] == SUPERSEDED:
        if record["link"]:
            return f"- [{record['id']}] {record['text']} (superseded by [{record['link']}])"
        return f"- [{record['id']}] {record['text']}"
    return f"- [{record['id']}] {entry_ref} {record['text']}"


def allocate(state: Dict[str, Any], budget_tokens: int) -> Dict[str, Any]:
    """typed_state.rs allocate(): priority order, overflow report, unsafe flag."""
    ranked = sorted(
        state["records"],
        key=lambda record: (
            kind_tier(record["kind"]) if record["status"] == CURRENT else 8,
            _VERIFICATION_RANK[record["verification"]],
            -record["ordinal"],
        ),
    )

    allocation: Dict[str, Any] = {
        "included": [],
        "tokensUsed": 0,
        "budgetTokens": budget_tokens,
        "overflow": {"dropped": []},
        "unsafeCompaction": None,
        # D25: records the durable cap has dropped from this ledger
        # (cumulative), rendered so a capped ledger is visibly truncated.
        "droppedByCap": state.get("droppedByCap") or 0,
    }
    opened_sections: Set[str] = set()
    for record in ranked:
        line = record_line(record)
        section = section_title(record["kind"], record["status"])
        cost = estimate_tokens(line)
        if section not in opened_sections:
            cost += estimate_tokens(section) + estimate_tokens("\n")
        if allocation["tokensUsed"] + cost <= budget_tokens:
            if section not in opened_sections:
                opened_sections.add(section)
                allocation["tokensUsed"] += estimate_tokens(section) + estimate_tokens("\n")
            allocation["tokensUsed"] += estimate_tokens(line)
            allocation["included"].append(
                {
                    "id": record["id"],
                    "kind": record["kind"],
                    "status": record["status"],
                    "tokens": estimate_tokens(line),
                    "section": section,
                    "line": line,
                    "retirePath": record.get("retirePath"),
                }
            )
        else:
            entry = {
                "id": record["id"],
                "kind": record["kind"],
                "tokens": cost,
                "sourceEntryId": record["sourceEntryId"],
                "ordinal": record["ordinal"],
                "text": record["text"],
            }
            if record["status"] == CURRENT and is_mandatory(record["kind"]):
                if allocation["unsafeCompaction"] is None:
                    allocation["unsafeCompaction"] = {"mandatoryDropped": []}
                allocation["unsafeCompaction"]["mandatoryDropped"].append(entry)
            else:
                allocation["overflow"]["dropped"].append(entry)
    if allocation["unsafeCompaction"] is not None:
        allocation["overflow"]["dropped"].extend(allocation["unsafeCompaction"]["mandatoryDropped"])
    return allocation


def allocation_is_safe(allocation: Dict[str, Any]) -> bool:
    return allocation["unsafeCompaction"] is None


# ---------------------------------------------------------------------------
# Verifier (typed_state.rs verify)
# ---------------------------------------------------------------------------

REF_ALLOWLIST = ("none", "x", "y", "TODO", "TBD", "N/A", "WIP", "DONE")
REF_SCAN_RE = re.compile(r"\[(@?)([A-Za-z0-9_./\-]+)\]")
# D23 (typed_state.rs, commit 8bc0811fb): the two-letter id prefixes
# `stable_id` can emit, in kind order, kept in lockstep with _PREFIX. The
# record-id grammar is exactly what the renderer emits: one of the kind
# prefixes, a dash, six lowercase hex. A bracketed token is a record
# reference only when it matches this grammar, so ticket-style user tokens
# ([JX-4f1c2a], [issue42]) stay prose.
KIND_PREFIXES = ("UC", "CO", "SB", "AC", "VD", "DR", "TK", "PA", "VT", "SE", "RS", "SS", "HF", "LP")
RECORD_ID_SHAPE_RE = re.compile(r"^(?:" + "|".join(KIND_PREFIXES) + r")-[0-9a-f]{6}$")
VERIFIED_WORD_RE = re.compile(r"(?i)\bverified\b")


def required_present(kind: str) -> bool:
    return kind in (
        USER_CONSTRAINT, SAFETY_OR_PERMISSION_BOUNDARY, ACCEPTANCE_CRITERION, CURRENT_TASK,
        PENDING_ACTION,
    )


def artifact_mentions(artifact: str, record: Dict[str, Any]) -> bool:
    if f"[{record['id']}]" in artifact:
        return True
    record_norm = normalized(record["text"])
    if record_norm and record_norm in normalized(artifact):
        return True
    half = record["text"][: len(record["text"]) // 2]
    half_norm = normalized(half)
    return len(half_norm) >= 16 and half_norm in normalized(artifact)


# D27 (typed_state.rs, commit 51c9a6a59): the render's section titles, the
# only stand-alone all-caps lines the artifact carries as structure.
SECTION_TITLE_LINES = (
    "ACTIVE CONSTRAINTS",
    "USER CORRECTIONS",
    "SAFETY BOUNDARIES",
    "ACCEPTANCE CRITERIA",
    "DECISIONS (CURRENT)",
    "DECISION RATIONALE",
    "CURRENT TASK",
    "PENDING ACTIONS",
    "VERIFIED EVIDENCE",
    "SOURCE EVIDENCE",
    "REPOSITORY STATE",
    "SUBAGENT STATE",
    "HISTORICAL FACTS",
    "LOW-PRIORITY EPISODES",
    "SUPERSEDED (HISTORY, NOT CURRENT)",
)


def _is_heading_line(line: str) -> bool:
    trimmed = line.strip()
    if trimmed.startswith("#") or trimmed.startswith("**"):
        return True
    # D27: casing alone must not mark a heading. An all-caps claim line
    # ("ALL TESTS VERIFIED PASSING") still has to pass through the
    # fabricated-verified-claim check.
    return trimmed in SECTION_TITLE_LINES


def verify(compact_artifact: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """typed_state.rs verify(): required material present, superseded
    material not current, references resolve, no fabricated verified claims."""
    known_record_ids = {record["id"] for record in state["records"]}
    known_entry_ids = {record["sourceEntryId"] for record in state["records"]}
    # D23: bracketed tokens the ledger itself quotes inside record texts.
    # The summarizer is instructed to copy statement texts verbatim, so such
    # a token is quoted source material (a ticket id inside a user
    # constraint), not a reference the model invented.
    quoted_tokens = {match.group(0) for record in state["records"] for match in REF_SCAN_RE.finditer(record["text"])}
    # D25: ids and entry ids retired at a step boundary or by the durable
    # cap stay resolvable; the previous summary may still quote them.
    for reference in state.get("retiredRefs") or []:
        known_record_ids.add(reference)
        known_entry_ids.add(reference)

    verification: Dict[str, Any] = {
        "passed": True,
        "missing": [],
        "misSuperseded": [],
        "unresolvedRefs": [],
        "fabricated": [],
    }

    for record in state["records"]:
        if record["status"] != CURRENT or not required_present(record["kind"]):
            continue
        if not artifact_mentions(compact_artifact, record):
            verification["missing"].append(
                {"subject": record["id"], "detail": f"required {kind_label(record['kind'])} not present"}
            )

    for record in state["records"]:
        if record["status"] != SUPERSEDED:
            continue
        if not artifact_mentions(compact_artifact, record):
            continue
        retiring_present = False
        if record["link"]:
            if f"[{record['link']}]" in compact_artifact:
                retiring_present = True
            else:
                retiring_present = any(
                    other["id"] == record["link"] and artifact_mentions(compact_artifact, other)
                    for other in state["records"]
                )
        if not retiring_present:
            verification["misSuperseded"].append(
                {
                    "subject": record["id"],
                    "detail": "superseded record presented without its supersession",
                }
            )

    for match in REF_SCAN_RE.finditer(compact_artifact):
        whole_end = match.end()
        # Skip markdown links: [text](url).
        if compact_artifact[whole_end:].startswith("("):
            continue
        # Skip quoted material (D23): the ledger carries this exact
        # bracketed token inside a record's text, so the copy is verbatim
        # source material, not a model-invented reference.
        if match.group(0) in quoted_tokens:
            continue
        sigil, token = match.group(1), match.group(2)
        if any(allowed.lower() == token.lower() for allowed in REF_ALLOWLIST):
            continue
        if sigil == "@":
            if token not in known_entry_ids:
                verification["unresolvedRefs"].append(
                    {
                        "subject": f"[@{token}]",
                        "detail": "entry reference does not resolve to a known source entry",
                    }
                )
        elif RECORD_ID_SHAPE_RE.match(token) and token not in known_record_ids:
            # D23: only kind-prefixed ids are references; the plain
            # entry-shape heuristic is gone, so [issue42]-style tokens stay
            # prose instead of unresolvable references.
            verification["unresolvedRefs"].append(
                {
                    "subject": f"[{token}]",
                    "detail": "record id does not resolve to a known record",
                }
            )

    verified_texts = [
        normalized(record["text"])
        for record in state["records"]
        if record["verification"] != UNVERIFIED and record["text"].strip()
    ]
    for line in compact_artifact.split("\n"):
        trimmed = line.strip()
        if not trimmed or _is_heading_line(trimmed) or not VERIFIED_WORD_RE.search(trimmed):
            continue
        line_norm = normalized(trimmed)
        maps = any(
            (match.group(1) == "@" and match.group(2) in known_entry_ids)
            or (
                match.group(1) != "@"
                and (match.group(2) in known_record_ids or match.group(2) in known_entry_ids)
            )
            for match in REF_SCAN_RE.finditer(trimmed)
        ) or any(text in line_norm for text in verified_texts)
        if not maps:
            verification["fabricated"].append(
                {
                    "subject": trimmed[:80],
                    "detail": "line claims verified material without a resolvable reference",
                }
            )

    verification["passed"] = not (
        verification["missing"]
        or verification["misSuperseded"]
        or verification["unresolvedRefs"]
        or verification["fabricated"]
    )
    return verification


# ---------------------------------------------------------------------------
# Render (typed_state.rs render_allocation)
# ---------------------------------------------------------------------------

HOT_STATE_HEADER = "## Typed Protected State (deterministic, provenance-tagged)"
HOT_STATE_INTRO = (
    "Derived from the transcript by fixed provenance rules; no item is inferred or invented. "
    "Copy every item below into the matching summary section without paraphrasing identifiers, "
    "paths, numbers or error codes. Items under SUPERSEDED are history, not current requirements."
)


def render_allocation(allocation: Dict[str, Any]) -> str:
    if not allocation["included"]:
        return ""
    out = HOT_STATE_HEADER + "\n" + HOT_STATE_INTRO + "\n"
    # D25: the durable cap's dropped count is visible to the summarizer,
    # never a silent truncation.
    if allocation.get("droppedByCap"):
        out += (
            f"- {allocation['droppedByCap']} record(s) omitted by the durable ledger cap "
            f"({MAX_DURABLE_RECORDS}); lowest-priority records were dropped first\n"
        )
    current_section = ""
    for item in allocation["included"]:
        if item["section"] != current_section:
            out += "\n" + item["section"] + "\n"
            current_section = item["section"]
        out += item["line"]
        retire_path = item.get("retirePath")
        if retire_path is not None:
            # typed_state.rs render_allocation (D29, typed_state.rs:1770):
            # the pairing annotation after a paired record's line.
            out += f" ({_PAIRING_NOTE[retire_path]})"
        out += "\n"
    return out


def render_durable_index(state: Dict[str, Any]) -> str:
    if not state["records"]:
        return ""
    out = "## Durable State Index\n"
    superseded = 0
    for record in state["records"]:
        if record["status"] == SUPERSEDED:
            superseded += 1
            continue
        out += f"- {record['id']} {kind_label(record['kind'])} @{record['sourceEntryId']}\n"
    if superseded:
        out += f"- {superseded} SUPERSEDED (history)\n"
    if state.get("droppedByCap"):
        out += (
            f"- {state['droppedByCap']} record(s) omitted by the durable ledger cap "
            f"({MAX_DURABLE_RECORDS})\n"
        )
    return out


def render_hot_state(state: Dict[str, Any]) -> str:
    if not state["records"]:
        return ""
    return render_allocation(allocate(state, 2**64))


# ---------------------------------------------------------------------------
# W15 compaction wiring (compaction.rs, commit 338b920c3)
# ---------------------------------------------------------------------------

# compaction.rs TYPED_STATE_INSTRUCTION, verbatim (line continuations collapsed).
TYPED_STATE_INSTRUCTION = (
    "Copy every ACTIVE CONSTRAINT, SAFETY BOUNDARY, ACCEPTANCE CRITERION, CURRENT TASK and "
    "PENDING ACTION item into the matching summary section without paraphrasing identifiers, "
    "paths, numbers or error codes, keeping each [id] and [@entry] reference. Keep SUPERSEDED "
    "items out of the current state; mention a superseded item only as history together with "
    "the record that retired it."
)


def typed_hot_state_budget_tokens(summary_budget_tokens: float) -> int:
    """compaction.rs typed_hot_state_budget_tokens: min(2048, floor(budget/4))."""
    if math.isfinite(summary_budget_tokens) and summary_budget_tokens > 0.0:
        quarter = min(math.floor(summary_budget_tokens / 4.0), 2048.0)
    else:
        quarter = 0.0
    return int(max(quarter, 0.0))


def typed_allocation_failure(allocation: Dict[str, Any]) -> str:
    """compaction.rs typed_allocation_failure (mirrors the validate_summary channel)."""
    unsafe_ = allocation["unsafeCompaction"] or {"mandatoryDropped": []}
    dropped = ", ".join(
        f"{entry['id']} ({kind_label(entry['kind'])})" for entry in unsafe_["mandatoryDropped"]
    )
    return (
        f"Typed protected state did not fit the hot-state budget ({allocation['budgetTokens']} tokens; "
        f"mandatory material dropped: {dropped}); existing conversation preserved. Raise the summary "
        "budget or reduce the protected window before retrying compaction."
    )


def typed_verification_failure(verification: Dict[str, Any]) -> str:
    """compaction.rs typed_verification_failure."""
    return (
        "Summarization returned an unusable handoff (typed protected state not represented: "
        f"{len(verification['missing'])} missing, {len(verification['misSuperseded'])} mis-superseded, "
        f"{len(verification['unresolvedRefs'])} unresolved references, "
        f"{len(verification['fabricated'])} unbacked verified claims); existing conversation preserved. "
        "Retry compaction or select another summarization model."
    )


def typed_summary_section(kind: str) -> str:
    """compaction.rs typed_summary_section."""
    if kind == CURRENT_TASK:
        return "Goal"
    if kind in (USER_CONSTRAINT, SAFETY_OR_PERMISSION_BOUNDARY, ACCEPTANCE_CRITERION, USER_CORRECTION):
        return "Constraints & Preferences"
    if kind == PENDING_ACTION:
        return "Next Steps"
    if kind == VERIFIED_DECISION:
        return "Key Decisions"
    return "Critical Context"


def typed_protected_line(record: Dict[str, Any]) -> str:
    """compaction.rs typed_protected_line (the render-shaped repair line)."""
    entry_ref = f"[@{record['sourceEntryId']}]"
    if record["link"]:
        return f"- [{record['id']}] {entry_ref} {record['text']} (supersedes [{record['link']}])"
    return f"- [{record['id']}] {entry_ref} {record['text']}"


def _split_inclusive(text: str) -> List[str]:
    """str::split_inclusive('\\n'): lines keep their trailing newline."""
    parts = text.split("\n")
    out = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def summary_section_insert_offset(summary: str, section: str) -> Optional[int]:
    """compaction.rs summary_section_insert_offset (fence-aware heading scan)."""
    fence: Optional[str] = None
    in_section = False
    offset = 0
    for line in _split_inclusive(summary):
        line_start = offset
        offset += len(line)
        trimmed = line.strip()
        if trimmed.startswith("```") or trimmed.startswith("~~~"):
            marker = trimmed[:3]
            if fence == marker:
                fence = None
            elif fence is None:
                fence = marker
            continue
        if fence is None:
            heading = trimmed.lstrip("#")
            if len(heading) != len(trimmed) and heading[:1].isspace():
                heading = heading.strip().rstrip("#").strip().strip("*").strip().rstrip(":")
                if heading.lower() == section.lower():
                    in_section = True
                elif in_section:
                    return line_start
                continue
    return len(summary) if in_section else None


def append_missing_protected_items(
    summary: str, state: Dict[str, Any], missing: Sequence[Dict[str, Any]]
) -> str:
    """compaction.rs append_missing_protected_items (bounded deterministic repair)."""
    sections: List[Tuple[str, List[str]]] = []
    for finding in missing:
        record = next((r for r in state["records"] if r["id"] == finding["subject"]), None)
        if record is None:
            continue
        section = typed_summary_section(record["kind"])
        line = typed_protected_line(record)
        for name, lines in sections:
            if name == section:
                lines.append(line)
                break
        else:
            sections.append((section, [line]))
    insertions: List[Tuple[int, str]] = []
    for section, lines in sections:
        offset = summary_section_insert_offset(summary, section)
        if offset is None:
            continue
        text = "\n" + "\n".join(lines) + "\n"
        insertions.append((offset, text))
    insertions.sort(key=lambda item: item[0], reverse=True)
    out = summary
    for offset, text in insertions:
        out = out[:offset] + text + out[offset:]
    return out


def verify_and_repair(summary: str, state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The compaction.rs verify gate: one bounded deterministic repair for
    missing-only findings; any other failure raises.

    Returns (summary, verification_record) where verification_record is the
    persisted details.typedVerification shape. Raises RuntimeError on a
    failing verification (the caller surfaces it through the same retry
    channel as validate_summary failures).
    """
    artifact = summary
    verification = verify(artifact, state)
    repaired = False
    if (
        not verification["passed"]
        and not verification["misSuperseded"]
        and not verification["unresolvedRefs"]
        and not verification["fabricated"]
    ):
        artifact = append_missing_protected_items(artifact, state, verification["missing"])
        verification = verify(artifact, state)
        repaired = True
    if not verification["passed"]:
        raise RuntimeError(typed_verification_failure(verification))
    record = {
        "passed": True,
        "repaired": repaired,
        "missing": len(verification["missing"]),
        "misSuperseded": len(verification["misSuperseded"]),
        "unresolvedRefs": len(verification["unresolvedRefs"]),
        "fabricated": len(verification["fabricated"]),
    }
    return artifact, record
