"""Python mirror of the production compaction pipeline (R2-E2 live phase).

Mirrors, for the multicycle scenario shapes used by R2-E1/R2-E2
(tests/rot2_multicycle.rs), the Rust pipeline:

  prepare_compaction -> compact -> append_compaction -> SessionManager restore

ported from crates/pi-coding-agent/src/core/compaction/{compaction.rs,
task_state.rs,utils.rs} (read-only) plus the deterministic lossy summarizer
fixture from tests/rot2_multicycle.rs.  The summarizer wire call itself is a
pluggable function so the live driver can substitute the real GLM 5.3
endpoint (research/context_rot_r2/live/run_r2e2.py).

What is NOT mirrored (documented deviations, none exercised by these
scenarios):
  - provider-native compaction checkpoints, retry pacing, deadlines
    (no provider supports_compaction here; every cycle uses the text path);
  - thinking-level reasoning adjustments (wire max_tokens is set by the
    driver per the R2-E2 brief);
  - assistant tool-call blocks (the scenarios contain no assistant tool
    calls; orphan tool results render with the historical
    "call outside excerpt" identity);
  - file-operation inventories (no tool calls => empty file ops; the
    <read-files>/<modified-files> sections are always empty here).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .rust_prompts import (
    CONSOLIDATING_UPDATE_SUMMARIZATION_PROMPT,
    CONSOLIDATING_UPDATE_SUMMARIZATION_PROMPT_V2,
    KERNEL_PERSIST_SUMMARY_NOTE,
    LEDGER_INSTRUCTION,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_PROMPT_V2,
    SUMMARIZATION_SYSTEM_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT_V2,
)

# ---------------------------------------------------------------------------
# Settings (compaction.rs CompactionSettings / default_compaction_settings)
# ---------------------------------------------------------------------------

SUMMARY_UPDATE_POLICY_OFF = "off"
CONSOLIDATE_REPEATED_SUMMARY_POLICY = "consolidate-repeated-v1"
COMPACTION_PROMPT_LEGACY = "legacy"
COMPACTION_PROMPT_V2 = "v2"
TASK_STATE_MODE_OFF = "off"
TASK_STATE_MODE_PROMPT = "prompt"


@dataclass
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: float = 16384.0
    keep_recent_tokens: float = 20000.0
    summary_update_policy: str = SUMMARY_UPDATE_POLICY_OFF
    prompt: str = COMPACTION_PROMPT_LEGACY
    summary_budget_mode: str = "legacy"
    trigger_threshold: Optional[float] = None
    deadline_ms: float = 300_000.0
    task_state: str = TASK_STATE_MODE_OFF


def arm_settings(scenario: Dict[str, Any], arm: str) -> CompactionSettings:
    """Same overrides as arm_settings() in tests/rot2_multicycle.rs."""
    settings = CompactionSettings()
    settings.reserve_tokens = scenario["reserveTokens"]
    settings.keep_recent_tokens = scenario["keepRecentTokens"]
    settings.task_state = TASK_STATE_MODE_PROMPT if arm == "taskState" else TASK_STATE_MODE_OFF
    return settings


def summary_output_budget(reserve_tokens: float, mode: str, compacted_input_tokens: float) -> float:
    """compaction.rs summary_output_budget (B3). Legacy: flat 0.8 * reserve."""
    if mode == "scaled":
        scaled = compacted_input_tokens / 40.0 if compacted_input_tokens > 0 else 0.0
        return math.floor(min(8000.0, max(2000.0, scaled)))
    return math.floor(0.8 * reserve_tokens)


# ---------------------------------------------------------------------------
# Session model (subset of SessionManager needed by the multicycle flow)
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    kind: str  # "user" | "assistant" | "toolResult" | "agentMessage" | "compaction"
    text: str = ""
    usage_input: Optional[float] = None
    tool_call_id: str = ""
    tool_name: str = ""
    from_name: str = ""
    # compaction-only fields
    summary: str = ""
    first_kept_entry_id: str = ""
    tokens_before: float = 0.0
    details: Optional[Dict[str, Any]] = None
    id: str = ""

    @property
    def is_message(self) -> bool:
        return self.kind in ("user", "assistant", "toolResult")

    @property
    def is_compaction(self) -> bool:
        return self.kind == "compaction"


class Session:
    """Ordered entry list with the adapter's restore semantics.

    build_context() mirrors build_session_context_with_entry_ids(None):
    the latest compaction's summary becomes the first message, followed by
    every entry from that compaction's first_kept_entry_id to the head
    (compaction entries themselves excluded).
    """

    def __init__(self, salt: str = "rot2") -> None:
        self.entries: List[Entry] = []
        self._next = 0
        self._id_salt = salt

    def _id(self) -> str:
        # SessionManager persists UUID entry ids; the ledger renders their
        # 8-char prefixes.  Deterministic uuid-shaped ids keep the rendered
        # width identical to the Rust run (the values are session-specific).
        self._next += 1
        digest = hashlib.sha256(f"{self._id_salt}|{self._next}".encode()).hexdigest()
        return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"

    def append(self, entry: Entry) -> Entry:
        entry.id = self._id()
        self.entries.append(entry)
        return entry

    def append_scenario_entry(self, spec: Dict[str, Any]) -> Entry:
        return self.append(
            Entry(
                kind=spec["kind"],
                text=spec.get("text", ""),
                usage_input=spec.get("usageInput"),
                tool_call_id=spec.get("toolCallId", ""),
                tool_name=spec.get("toolName", ""),
                from_name=spec.get("fromName", ""),
            )
        )

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: float,
        details: Optional[Dict[str, Any]],
    ) -> Entry:
        return self.append(
            Entry(
                kind="compaction",
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
                details=details,
            )
        )

    def live_messages(self) -> List[Entry]:
        """Non-compaction entries (the adapter's `live` list)."""
        return [e for e in self.entries if not e.is_compaction]

    def build_context(self) -> Tuple[Optional[str], List[Entry]]:
        """(latest summary or None, kept tail entries)."""
        last_comp = None
        for entry in reversed(self.entries):
            if entry.is_compaction:
                last_comp = entry
                break
        if last_comp is None:
            return None, [e for e in self.entries if not e.is_compaction]
        start = 0
        for index, entry in enumerate(self.entries):
            if entry.id == last_comp.first_kept_entry_id:
                start = index
                break
        kept = [e for e in self.entries[start:] if not e.is_compaction]
        return last_comp.summary, kept


# ---------------------------------------------------------------------------
# Token estimates (compaction.rs estimate_tokens / estimate_context_tokens)
# ---------------------------------------------------------------------------


def ceil_div4(chars: int) -> float:
    return math.ceil(chars / 4.0)


def estimate_tokens(entry: Entry) -> float:
    if entry.kind == "compaction":
        return ceil_div4(len(entry.summary))
    return ceil_div4(len(entry.text))


def entry_token_cost(entry: Entry) -> Optional[float]:
    if entry.is_message or entry.kind == "agentMessage":
        return estimate_tokens(entry)
    return None  # Compaction | Other


def estimate_context_tokens(live: Sequence[Entry]) -> Dict[str, Any]:
    """estimate_context_tokens: last assistant usage + chars/4 trailing."""
    usage_index = None
    for index in range(len(live) - 1, -1, -1):
        if live[index].kind == "assistant":
            usage_index = index
            break
    if usage_index is None:
        total = 0.0
        for entry in live:
            total += estimate_tokens(entry)
        return {"tokens": total, "usage_tokens": 0.0, "trailing_tokens": total, "last_usage_index": None}
    usage = live[usage_index].usage_input or 0.0
    trailing = 0.0
    for entry in live[usage_index + 1 :]:
        trailing += estimate_tokens(entry)
    return {
        "tokens": usage + trailing,
        "usage_tokens": usage,
        "trailing_tokens": trailing,
        "last_usage_index": usage_index,
    }


# ---------------------------------------------------------------------------
# Cut point (compaction.rs find_cut_point / find_valid_cut_points)
# ---------------------------------------------------------------------------


def find_valid_cut_points(entries: Sequence[Entry], start_index: int, end_index: int) -> List[int]:
    cut_points: List[int] = []
    for index in range(start_index, end_index):
        entry = entries[index]
        if entry.is_message:
            if entry.kind in ("user", "assistant"):
                cut_points.append(index)
        if entry.kind == "agentMessage":
            cut_points.append(index)
    return cut_points


def find_turn_start_index(entries: Sequence[Entry], entry_index: int, start_index: int) -> Optional[int]:
    if entry_index < start_index:
        return None
    for index in range(entry_index, start_index - 1, -1):
        entry = entries[index]
        if entry.kind == "agentMessage":
            return index
        if entry.kind == "user":
            return index
    return None


def find_cut_point(
    entries: Sequence[Entry], start_index: int, end_index: int, keep_recent_tokens: float
) -> Dict[str, Any]:
    cut_points = find_valid_cut_points(entries, start_index, end_index)
    if not cut_points:
        return {"first_kept_entry_index": start_index, "turn_start_index": None, "is_split_turn": False}
    accumulated = 0.0
    cut_index = cut_points[0]
    if end_index > start_index:
        for index in range(end_index - 1, start_index - 1, -1):
            entry = entries[index]
            cost = entry_token_cost(entry)
            if cost is not None:
                accumulated += cost
            if accumulated >= keep_recent_tokens:
                cut_index = cut_points[-1]
                for candidate in cut_points:
                    if candidate >= index:
                        cut_index = candidate
                        break
                break
    while cut_index > start_index:
        prev = entries[cut_index - 1]
        if prev.is_compaction or prev.is_message:
            break
        cut_index -= 1
    cut_entry = entries[cut_index]
    is_user_message = cut_entry.kind == "user"
    turn_start_index = None if is_user_message else find_turn_start_index(entries, cut_index, start_index)
    return {
        "first_kept_entry_index": cut_index,
        "turn_start_index": turn_start_index,
        "is_split_turn": (not is_user_message) and turn_start_index is not None,
    }


# ---------------------------------------------------------------------------
# Task-state ledger (task_state.rs, verbatim rules)
# ---------------------------------------------------------------------------

CONSTRAINTS_CAP = 12
DECISIONS_CAP = 10
SUPERSEDED_CAP = 8
EVIDENCE_CAP = 8
ANCHORS_CAP = 12
OPEN_WORK_CAP = 6
RETRACTIONS_CAP = 4
ITEM_TEXT_MAX = 320
RENDER_MAX_CHARS = 6_000

CONSTRAINT_RE = re.compile(
    r"(?i)\b(must|must not|never|do not|don't|only|always|keep|frozen|unchanged|offline|not exceed|no emojis|should be)\b"
)
DECISION_RE = re.compile(r"(?i)\bdecision\b|\bchosen\b|\bchose\b|\bwe will use\b")
SUPERSEDE_RE = re.compile(
    r"(?i)\b(drop|instead|change of plan|no longer|rather than|replace[sd]?|revert|withdraw|supersed\w*)\b"
)
EVIDENCE_RE = re.compile(r"test result:|error\[E\d+\]|\d+ files changed|panicked at|FAILED")
ANCHOR_RE = re.compile(
    r"`([^`]{2,60})`"
    r"|\b([A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+)\b"
    r"|\b([A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b"
    r"|\b([A-Za-z0-9_.\-/]+\.(?:rs|toml|json|jsonl|md|py|sh))\b"
    r"|\b(E\d{4})\b"
    r"|\b(\d{4,})\b"
)
CLAUSE_SPLIT_RE = re.compile(r"\s\(\d\)\s")
OPEN_WORK_RE = re.compile(r"(?i)\b(remaining|left to do|next step|todo)\b")
RATIONALE_RE = re.compile(r"(?i)^rationale\b")
RETRACTION_RE = re.compile(r"(?i)^(superseded|withdrawn|reverted)\b")
LOG_LEVEL_RE = re.compile(r"^(INFO|DEBUG|WARN|ERROR|TRACE)$")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
DIGITS_RE = re.compile(r"\d+")

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "is", "are", "was", "were", "be", "been", "to", "of", "in", "on", "for", "with", "without",
    "by", "from", "as", "at", "it", "its", "their", "our", "your", "we", "you", "i", "they", "he",
    "she",
}


def ledger_entry_text(entry: Entry) -> Optional[Tuple[str, str, bool]]:
    """(role, text, is_error); only Message entries (task_state.rs entry_text)."""
    if not entry.is_message:
        return None
    if entry.kind == "user":
        return ("user", entry.text, False)
    if entry.kind == "assistant":
        if not entry.text.strip():
            return None
        return ("assistant", entry.text, False)
    return ("tool", entry.text, False)


def sentences(text: str) -> List[str]:
    out: List[str] = []
    current: List[str] = []
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch == "\n":
            out.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        if ch in ".!?" and i + 1 < len(chars) and chars[i + 1].isspace():
            out.append("".join(current))
            current = []
        i += 1
    out.append("".join(current))
    return [part.strip() for part in out if part.strip()]


def word_tokens(text: str) -> "set":
    return {
        m.group(0).lower()
        for m in TOKEN_RE.finditer(text)
        if m.group(0).lower() not in STOPWORDS
    }


def clip(text: str, limit: int) -> str:
    squeezed = " ".join(text.split())
    if len(squeezed) <= limit:
        return squeezed
    return squeezed[: limit - 1] + "\u2026"


def dedupe_key(text: str) -> str:
    return " ".join(sorted(word_tokens(text)))


def short_id(entry_id: str) -> str:
    return "summary" if not entry_id else entry_id[:8]


def split_clauses(sentence: str) -> List[str]:
    if not CLAUSE_SPLIT_RE.search(f" {sentence} "):
        return [sentence]
    parts = CLAUSE_SPLIT_RE.split(f" {sentence}")
    out: List[str] = []
    if parts:
        head = parts[0].strip()
        if len(head) > 8:
            out.append(head)
    for part in parts[1:]:
        part = part.strip()
        if part:
            out.append(part)
    return out


def anchor_weight(token: str) -> int:
    weight = 0
    if "_" in token or "/" in token or "." in token:
        weight += 3
    if any(c.isdigit() for c in token):
        weight += 2
    if len(token) >= 8:
        weight += 1
    return weight


def evidence_key(line: str) -> str:
    squeezed = " ".join(line.split())
    replaced = DIGITS_RE.sub("#", squeezed)
    return replaced[:160]


def empty_ledger() -> Dict[str, Any]:
    return {
        "constraints": [],
        "decisions": [],
        "superseded": [],
        "evidence": [],
        "anchors": [],
        "openWork": [],
        "retractions": [],
        "retired": [],
    }


def ledger_is_empty(ledger: Dict[str, Any]) -> bool:
    return not (ledger["constraints"] or ledger["decisions"] or ledger["superseded"]
                or ledger["evidence"] or ledger["openWork"])


def build_ledger(entries: Sequence[Entry], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ledger = previous if previous is not None else empty_ledger()
    seen = set()
    for item in ledger["constraints"] + ledger["decisions"] + ledger["openWork"]:
        seen.add(dedupe_key(item["text"]))

    corrections: List[Dict[str, Any]] = []
    evidence_seen: Dict[str, int] = {}
    for index, item in enumerate(ledger["evidence"]):
        evidence_seen[evidence_key(item["text"])] = index
    anchor_counts: Dict[str, set] = {}
    anchor_first: Dict[str, int] = {}

    for position, entry in enumerate(entries):
        info = ledger_entry_text(entry)
        if info is None:
            continue
        role, text, _is_error = info
        entry_id = entry.id

        if role == "user":
            is_correction = SUPERSEDE_RE.search(text) is not None
            for sentence in sentences(text):
                for clause in split_clauses(sentence):
                    if RATIONALE_RE.search(clause):
                        target = corrections[-1] if corrections else (ledger["constraints"][-1] if ledger["constraints"] else None)
                        if target is not None:
                            parts = clause.split(":", 1)
                            why_text = parts[1].strip() if len(parts) == 2 else clause.strip()
                            target.setdefault("why", []).append(clip(why_text, 200))
                        continue
                    if not (is_correction or CONSTRAINT_RE.search(clause)):
                        continue
                    if len(clause) < 12:
                        continue
                    key = dedupe_key(clause)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    item = {
                        "id": f"C{len(ledger['constraints']) + 1}",
                        "text": clip(clause, ITEM_TEXT_MAX),
                        "entry": short_id(entry_id),
                        "why": [],
                    }
                    if is_correction:
                        corrections.append(item)
                    ledger["constraints"].append(item)

        if role in ("user", "assistant"):
            for sentence in sentences(text):
                if role == "assistant" and RETRACTION_RE.search(sentence):
                    ledger["retractions"].append({
                        "id": f"R{len(ledger['retractions']) + 1}",
                        "text": clip(sentence, 240),
                        "entry": short_id(entry_id),
                        "why": [],
                    })
                    continue
                if not DECISION_RE.search(sentence):
                    continue
                key = dedupe_key(sentence)
                if not key or key in seen:
                    continue
                seen.add(key)
                ledger["decisions"].append({
                    "id": f"D{len(ledger['decisions']) + 1}",
                    "text": clip(sentence, ITEM_TEXT_MAX),
                    "entry": short_id(entry_id),
                    "why": [],
                })
            for capture in ANCHOR_RE.finditer(text):
                token = next((g for g in capture.groups() if g is not None), None)
                if token is None:
                    continue
                if LOG_LEVEL_RE.match(token):
                    continue
                anchor_counts.setdefault(token, set()).add(entry_id)
                anchor_first.setdefault(token, position)

        if role == "tool" and EVIDENCE_RE.search(text):
            line = next((l for l in text.split("\n") if EVIDENCE_RE.search(l)), text)
            key = evidence_key(line)
            if key in evidence_seen:
                existing = ledger["evidence"][evidence_seen[key]]
                existing["repeats"] = existing.get("repeats", 1) + 1
                existing["lastEntry"] = short_id(entry_id)
            else:
                evidence_seen[key] = len(ledger["evidence"])
                ledger["evidence"].append({
                    "id": f"E{len(ledger['evidence']) + 1}",
                    "text": clip(line, 200),
                    "entry": short_id(entry_id),
                    "repeats": 1,
                    "lastEntry": short_id(entry_id),
                })

    # Supersession: a later user correction retires the best earlier item that
    # shares a specific anchor (identifier, path, digit-bearing token).
    for correction in corrections:
        correction_tokens = word_tokens(correction["text"])
        best = None  # (is_decision, index, score)
        candidates = ([(True, i, item) for i, item in enumerate(ledger["decisions"])]
                      + [(False, i, item) for i, item in enumerate(ledger["constraints"])])
        for is_decision, index, item in candidates:
            if item["id"] == correction["id"]:
                continue
            item_tokens = word_tokens(item["text"])
            shared = correction_tokens & item_tokens
            specific = sum(
                1
                for token in shared
                if "_" in token or "/" in token or any(c.isdigit() for c in token) or len(token) > 8
            )
            score = specific * 3 + len(shared)
            if best is None or score > best[2]:
                best = (is_decision, index, score)
        if best is None or best[2] < 3:
            continue
        is_decision, index, _score = best
        section = ledger["decisions"] if is_decision else ledger["constraints"]
        old = section[index]
        if any(existing["oldId"] == old["id"] for existing in ledger["superseded"]):
            continue
        ledger["superseded"].append({
            "oldId": old["id"],
            "oldEntry": old["entry"],
            "newId": correction["id"],
            "newText": correction["text"],
            "newEntry": correction["entry"],
        })

    retired = {item["oldId"] for item in ledger["superseded"]}
    for item_id in sorted(retired):
        if item_id not in ledger["retired"]:
            ledger["retired"].append(item_id)
    ledger["constraints"] = [i for i in ledger["constraints"] if i["id"] not in retired]
    ledger["decisions"] = [i for i in ledger["decisions"] if i["id"] not in retired]

    # Open work: the newest first-person statement of what remains.
    for entry in reversed(entries):
        info = ledger_entry_text(entry)
        if info is None or info[0] != "assistant":
            continue
        text = info[1]
        if not OPEN_WORK_RE.search(text):
            continue
        candidate = {
            "id": "O1",
            "text": clip(text, 240),
            "entry": short_id(entry.id),
            "why": [],
        }
        key = dedupe_key(candidate["text"])
        if key not in seen:
            ledger["openWork"] = [i for i in ledger["openWork"] if i["id"] != "O1"]
            seen.add(key)
        ledger["openWork"] = [i for i in ledger["openWork"] if i["id"] != "O1"]
        ledger["openWork"].insert(0, candidate)
        break

    # Anchors: identifiers that recur, weighted to exact, specific shapes.
    ranked = [
        (token, len(entry_ids), anchor_first.get(token, len(entries)))
        for token, entry_ids in sorted(anchor_counts.items())
        if len(entry_ids) >= 2
    ]
    ranked.sort(key=lambda t: (-t[1], -anchor_weight(t[0]), t[2]))
    for token, _, _ in ranked:
        if token in ledger["anchors"]:
            continue
        if len(ledger["anchors"]) >= ANCHORS_CAP:
            break
        ledger["anchors"].append(token)

    return ledger


RENDER_HEADER = (
    "Derived from the transcript by fixed rules; no item is inferred or invented. "
    "Use it to keep constraints, current decisions and verified evidence exact. "
    "A SUPERSEDED item is history, not a current requirement.\n"
)


def _push_items(out: List[str], title: str, items: List[Dict[str, Any]], cap: int, dropped: List[Tuple[int, str]], render) -> None:
    if not items:
        return
    out.append(f"\n**{title}**\n")
    for item in items[:cap]:
        out.append(f"- {render(item)}\n")
    if len(items) > cap:
        label = {
            "ACTIVE CONSTRAINTS": "active constraint",
            "DECISIONS (current)": "current decision",
            "OPEN WORK": "open-work item",
        }.get(title, "item")
        dropped.append((len(items) - cap, label))


def render_ledger(ledger: Dict[str, Any]) -> str:
    if ledger_is_empty(ledger):
        return ""
    out: List[str] = []
    out.append("## Protected Task State (deterministic, provenance-tagged)\n")
    out.append(RENDER_HEADER)
    dropped: List[Tuple[int, str]] = []
    _push_items(out, "ACTIVE CONSTRAINTS", ledger["constraints"], CONSTRAINTS_CAP, dropped,
                lambda item: (f"{item['id']} [{item['entry']}]: {item['text']}"
                              + (f" (why: {'; '.join(item.get('why', []))})" if item.get("why") else "")))
    _push_items(out, "DECISIONS (current)", ledger["decisions"], DECISIONS_CAP, dropped,
                lambda item: f"{item['id']} [{item['entry']}]: {item['text']}")
    if ledger["superseded"]:
        out.append("\n**SUPERSEDED (history, not current)**\n")
        for item in ledger["superseded"][:SUPERSEDED_CAP]:
            out.append(f"- {item['oldId']} [{item['oldEntry']}] replaced by {item['newId']} [{item['newEntry']}]: {item['newText']}\n")
        if len(ledger["superseded"]) > SUPERSEDED_CAP:
            dropped.append((len(ledger["superseded"]) - SUPERSEDED_CAP, "supersession"))
    if ledger["evidence"]:
        out.append("\n**VERIFIED EVIDENCE**\n")
        for item in ledger["evidence"][:EVIDENCE_CAP]:
            repeats = f" (x{item['repeats']}, latest [{item['lastEntry']}])" if item.get("repeats", 1) > 1 else ""
            out.append(f"- {item['id']} [{item['entry']}]: {item['text']}{repeats}\n")
        if len(ledger["evidence"]) > EVIDENCE_CAP:
            dropped.append((len(ledger["evidence"]) - EVIDENCE_CAP, "evidence item"))
    if ledger["anchors"]:
        out.append("\n**EXACT ANCHORS**: ")
        out.append(", ".join(ledger["anchors"]))
        out.append("\n")
    if ledger["retractions"]:
        out.append("\n**RETRACTIONS (assistant-recorded history)**\n")
        for item in ledger["retractions"][:RETRACTIONS_CAP]:
            out.append(f"- [{item['entry']}] {item['text']}\n")
    _push_items(out, "OPEN WORK", ledger["openWork"], OPEN_WORK_CAP, dropped,
                lambda item: f"[{item['entry']}] {item['text']}")
    if dropped:
        total = sum(count for count, _ in dropped)
        detail = ", ".join(f"{count} {label}(s)" for count, label in dropped)
        out.append(f"\n_({total} lower-priority item(s) omitted from this block: {detail}; the full ledger is persisted on the compaction entry.)_\n")
    text = "".join(out)
    if len(text) > RENDER_MAX_CHARS:
        text = text[:RENDER_MAX_CHARS]
        text += "\n_(ledger block truncated to its character budget; the full ledger is persisted on the compaction entry.)_\n"
    return text


# ---------------------------------------------------------------------------
# Conversation serialization (utils.rs serialize_conversation; exercised shapes)
# ---------------------------------------------------------------------------

TOOL_RESULT_MAX_CHARS = 2000


def truncate_for_summary(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    total_chars = len(text)
    tail_chars = min(500, max_chars // 4)
    marker_max = len(f"[... {total_chars} characters truncated; first {max_chars} and last {tail_chars} kept ...]") + 4
    if max_chars <= marker_max + tail_chars:
        return text[:max_chars]
    head = text[:max_chars]
    tail = text[len(text) - tail_chars :]
    # The evidence-line preservation of the Rust helper is not exercised: the
    # scenario's tool results are far below TOOL_RESULT_MAX_CHARS.
    return f"{head}\n[... {total_chars} characters truncated; first {max_chars} and last {tail_chars} kept ...]\n{tail}"


def serialize_conversation(messages: Sequence[Tuple[str, Any]]) -> str:
    """messages: ("user", text) | ("assistant", text) | ("toolResult", entry).

    Renders the historical shapes: [User]/[Assistant] parts and tool results
    with their numbering identity; the scenario contains no assistant tool
    calls, so every tool result renders "call outside excerpt" exactly like
    the Rust helper.
    """
    parts: List[str] = []
    calls: Dict[str, int] = {}
    for kind, payload in messages:
        if kind == "user":
            if payload:
                parts.append(f"[User]: {payload}")
        elif kind == "assistant":
            if payload:
                parts.append(f"[Assistant]: {payload}")
        elif kind == "toolResult":
            entry = payload
            content = entry.text
            if content:
                identity = (
                    f"#{calls[entry.tool_call_id]} {entry.tool_name}"
                    if entry.tool_call_id in calls
                    else f"{entry.tool_name} (call outside excerpt)"
                )
                parts.append(
                    f"[Tool result {identity}]: {truncate_for_summary(content, TOOL_RESULT_MAX_CHARS)}"
                )
    return "\n\n".join(parts)


def convert_to_llm(live: Sequence[Entry]) -> List[Tuple[str, Any]]:
    """Agent messages -> LLM message shapes for serialization.

    Custom agent messages become user-role text (messages.rs
    convert_single_message), exactly as the production converter does.
    """
    out: List[Tuple[str, Any]] = []
    for entry in live:
        if entry.kind in ("user", "assistant"):
            out.append((entry.kind, entry.text))
        elif entry.kind == "toolResult":
            out.append(("toolResult", entry))
        elif entry.kind == "agentMessage":
            out.append(("user", entry.text))
    return out


FILE_OPS_BLOCKS = re.compile(
    r"(?ms)^<read-files>\r?\n.*?^</read-files>[ \t]*\r?$|^<modified-files>\r?\n.*?^</modified-files>[ \t]*\r?$"
)


def strip_file_operations(summary: str) -> str:
    return FILE_OPS_BLOCKS.sub("", summary).strip()


# ---------------------------------------------------------------------------
# Prompt assembly (compaction.rs build_summarization_prompt / with_retained_state)
# ---------------------------------------------------------------------------


def build_summarization_prompt(
    custom_instructions: Optional[str],
    previous_summary: Optional[str],
    summary_update_policy: str,
    prompt_version: str,
    summary_budget_tokens: Optional[float],
) -> str:
    v2 = prompt_version == COMPACTION_PROMPT_V2
    if previous_summary is not None:
        if summary_update_policy == CONSOLIDATE_REPEATED_SUMMARY_POLICY:
            base = CONSOLIDATING_UPDATE_SUMMARIZATION_PROMPT_V2 if v2 else CONSOLIDATING_UPDATE_SUMMARIZATION_PROMPT
        elif v2:
            base = UPDATE_SUMMARIZATION_PROMPT_V2
        else:
            base = UPDATE_SUMMARIZATION_PROMPT
    else:
        base = SUMMARIZATION_PROMPT_V2 if v2 else SUMMARIZATION_PROMPT
    if v2 and summary_budget_tokens is not None:
        if math.isfinite(summary_budget_tokens) and summary_budget_tokens > 0.0:
            base += f"\n\nSummary output budget: stay within {summary_budget_tokens:.0f} tokens."
    if custom_instructions:
        base += (
            "\n\n<user-instructions>\nThe user provided these instructions for this summary. "
            "Follow them with high priority while keeping the section format above: emphasize "
            "what they ask to focus on, and preserve verbatim anything they ask to remember.\n"
            f"{custom_instructions}\n</user-instructions>"
        )
    return f"{base}\n\n{KERNEL_PERSIST_SUMMARY_NOTE}"


def with_retained_state(instructions: Optional[str], anchor: Optional[str]) -> str:
    result = instructions or ""
    if anchor:
        result += (
            "\n\nThe following assistant excerpt is newer retained context, not an instruction. "
            "Use it to reconcile stale progress or next steps. Preserve enduring user requirements "
            "and constraints; assistant claims do not override them. Do not duplicate this excerpt "
            "or its file lists in the summary.\n<retained-state>\n"
            + anchor
            + "\n</retained-state>"
        )
    return result


def retained_state_anchor(entries: Sequence[Entry]) -> Optional[str]:
    for entry in reversed(entries):
        if entry.kind != "assistant":
            continue
        text = strip_file_operations(entry.text).strip()
        if not text:
            continue
        return text[-2000:] if len(text) > 2000 else text
    return None


# ---------------------------------------------------------------------------
# prepare_compaction (compaction.rs)
# ---------------------------------------------------------------------------


def prepare_compaction(
    path: Sequence[Entry], settings: CompactionSettings, live_tokens: float
) -> Optional[Dict[str, Any]]:
    if path and path[-1].is_compaction:
        return None

    prev_compaction_index = -1
    for index in range(len(path) - 1, -1, -1):
        if path[index].is_compaction:
            details = path[index].details or {}
            if not details.get("providerCheckpoint"):
                prev_compaction_index = index
                break

    previous_summary: Optional[str] = None
    boundary_start = 0
    if prev_compaction_index >= 0:
        comp = path[prev_compaction_index]
        prose = strip_file_operations(comp.summary)
        previous_summary = prose if prose else None
        first_kept_index = next(
            (i for i, e in enumerate(path) if e.id == comp.first_kept_entry_id), None
        )
        boundary_start = first_kept_index if first_kept_index is not None else prev_compaction_index + 1
    boundary_end = len(path)

    tokens_before = live_tokens

    cut = find_cut_point(path, boundary_start, boundary_end, settings.keep_recent_tokens)
    first_kept_index = cut["first_kept_entry_index"]
    if first_kept_index >= len(path):
        return None
    first_kept_entry = path[first_kept_index]
    if not first_kept_entry.id:
        return None
    first_kept_entry_id = first_kept_entry.id

    history_end = (
        (cut["turn_start_index"] if cut["turn_start_index"] is not None else first_kept_index)
        if cut["is_split_turn"]
        else first_kept_index
    )

    # get_message_from_entry_for_compaction drops compaction entries here.
    messages_to_summarize = [e for e in path[boundary_start:history_end] if not e.is_compaction]

    if not messages_to_summarize and previous_summary is None:
        return None

    anchor = retained_state_anchor(path[first_kept_index:])

    task_state = None
    if settings.task_state == TASK_STATE_MODE_PROMPT:
        previous_ledger = None
        if prev_compaction_index >= 0:
            details = path[prev_compaction_index].details or {}
            if details.get("taskState"):
                previous_ledger = details["taskState"]
        ledger = build_ledger(path[boundary_start:history_end], previous_ledger)
        task_state = ledger if not ledger_is_empty(ledger) else None

    return {
        "first_kept_entry_id": first_kept_entry_id,
        "first_kept_entry_index": first_kept_index,
        "messages_to_summarize": messages_to_summarize,
        "tokens_before": tokens_before,
        "previous_summary": previous_summary,
        "retained_state_anchor": anchor,
        "task_state": task_state,
        "boundary_start": boundary_start,
        "history_end": history_end,
    }


# ---------------------------------------------------------------------------
# Summary generation (generate_bounded_summary; pluggable summarizer)
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = ["Goal", "Constraints & Preferences", "Progress", "Key Decisions", "Next Steps", "Critical Context"]


def validate_summary(stop_reason: str, text: str) -> Optional[str]:
    """validate_summary port; returns an error reason or None."""
    if stop_reason != "stop":
        return f"response did not complete (stop_reason={stop_reason or 'unknown'})"
    if not text.strip():
        return "empty summary"
    text = text.strip()
    if text.startswith("```"):
        opening, _, body = text.partition("\n")
        if opening.strip() in ("```", "```md", "```markdown") and body.rstrip().endswith("```"):
            text = body.rstrip()[:-3].strip()
    seen = [False] * len(REQUIRED_SECTIONS)
    bodies = [False] * len(REQUIRED_SECTIONS)
    current = None
    fence = None
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            marker = line[:3]
            if fence == marker:
                fence = None
            elif fence is None:
                fence = marker
            continue
        if fence is None and line.startswith("#"):
            heading = line.lstrip("#")
            if heading and heading[0].isspace():
                heading = heading.strip().rstrip("#").strip().strip("*").strip().rstrip(":")
                for i, req in enumerate(REQUIRED_SECTIONS):
                    if heading.lower() == req.lower():
                        seen[i] = True
                        current = i
                continue
        if current is not None and any(c.isalnum() for c in line):
            bodies[current] = True
    if not all(seen) or not all(bodies) or fence is not None:
        return "missing, empty or incomplete handoff sections"
    return None


@dataclass
class SummarizerCallResult:
    text: str
    stop_reason: str
    usage: Dict[str, Any]
    latency_s: float
    attempts: int


SummarizerFn = Callable[[str, str, float], SummarizerCallResult]  # (system, user, max_tokens)


@dataclass
class ModelConfig:
    context_window: float = 1_048_576.0
    max_tokens: float = 64_000.0


def generate_bounded_summary(
    messages_to_summarize: Sequence[Entry],
    model: ModelConfig,
    reserve_tokens: float,
    previous_summary: Optional[str],
    custom_instructions: Optional[str],
    summary_update_policy: str,
    prompt_version: str,
    summary_budget_tokens: float,
    protected_state: Optional[str],
    summarizer_fn: SummarizerFn,
    wire_max_tokens: float,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Returns (summary, call_records). Mirrors generate_bounded_summary."""
    def instructions(previous: Optional[str]) -> str:
        prompt = build_summarization_prompt(
            custom_instructions, previous, summary_update_policy, prompt_version, summary_budget_tokens
        )
        if protected_state and protected_state.strip():
            return f"{prompt}\n\n{protected_state}\n\n{LEDGER_INSTRUCTION}"
        return prompt

    conversation = serialize_conversation(convert_to_llm(messages_to_summarize))
    conversation_chars = len(conversation)
    summary = strip_file_operations(previous_summary) if previous_summary else None
    if summary is not None and not summary:
        summary = None
    call_records: List[Dict[str, Any]] = []
    offset = 0
    # Bounded length retries. The Rust path retries once at 2x; GLM 5.3
    # bills reasoning tokens against max_tokens and can burn 8k-16k tokens
    # of reasoning on ledger-bearing requests, so a second step (4x, capped
    # at the Rust ceiling logic's 65,536) is allowed before giving up.
    retry_ladder = [wire_max_tokens]
    step = wire_max_tokens * 2.0
    while step <= 65_536.0 and len(retry_ladder) < 4:
        retry_ladder.append(step)
        step *= 2.0
    retry_index = 0
    while True:
        suffix = (
            (f"<previous-summary>\n{summary}\n</previous-summary>\n\n" if summary is not None else "")
            + instructions(summary)
        )
        budget = (
            math.floor((min(model.context_window, model.context_window - retry_ladder[-1]) - 1024.0) * 3.0)
            - len(suffix)
            - len(SUMMARIZATION_SYSTEM_PROMPT)
            - 64.0
        )
        if budget <= 0:
            raise RuntimeError("Compaction instructions and previous summary exceed the model input budget")
        chunk_char_len = min(int(budget), conversation_chars - offset)
        chunk = conversation[offset : offset + chunk_char_len]
        offset += chunk_char_len
        user_text = f"<conversation>\n{chunk}\n</conversation>\n\n{suffix}"
        record = {
            # The Rust adapter sums text.len() over captured requests: UTF-8
            # BYTE length (the note's em-dash costs 2 extra bytes).
            "userChars": len(user_text.encode("utf-8")),
            "userCharsUnicode": len(user_text),
            "conversationChunkChars": len(chunk),
            "maxTokens": wire_max_tokens,
        }
        call_records.append(record)
        result = summarizer_fn(SUMMARIZATION_SYSTEM_PROMPT, user_text, wire_max_tokens)
        while result.stop_reason == "length" and retry_index + 1 < len(retry_ladder):
            retry_index += 1
            retry_max_tokens = retry_ladder[retry_index]
            result = summarizer_fn(SUMMARIZATION_SYSTEM_PROMPT, user_text, retry_max_tokens)
            record["lengthRetryMaxTokens"] = retry_max_tokens
        record["stopReason"] = result.stop_reason
        record["usage"] = result.usage
        record["latencyS"] = result.latency_s
        record["attempts"] = result.attempts
        error = validate_summary(result.stop_reason, result.text)
        if error:
            raise RuntimeError(f"Summarization returned an unusable handoff ({error}); existing conversation preserved.")
        summary = strip_file_operations(result.text)
        if offset >= conversation_chars:
            break
    return summary or "", call_records


# ---------------------------------------------------------------------------
# Deterministic lossy fixture (tests/rot2_multicycle.rs lossy_summary)
# ---------------------------------------------------------------------------

LOSSY_HEAD_LINES = 2
LOSSY_TAIL_LINES = 2
LOSSY_LINE_CLIP = 160
PREVIOUS_SUMMARY_KEEP_CHARS = 400


def _clip_line(line: str, limit: int) -> str:
    if len(line) <= limit:
        return line
    return line[:limit] + "..."


def _conversation_chunks(request: str) -> List[str]:
    chunks = []
    rest = request
    while True:
        start = rest.find("<conversation>")
        if start < 0:
            break
        after = rest[start + len("<conversation>") :]
        end_rel = after.find("</conversation>")
        if end_rel < 0:
            break
        chunks.append(after[:end_rel])
        rest = after[end_rel + len("</conversation>") :]
    return chunks


def lossy_summary(request: str) -> str:
    constraints: List[str] = []
    decisions: List[str] = []
    evidence: List[str] = []
    section = ""
    in_block = False
    for line in request.split("\n"):
        if line.startswith("## Protected Task State"):
            in_block = True
            continue
        if in_block:
            if line.startswith("## ") or line.startswith("Copy every ACTIVE CONSTRAINT"):
                if not line.startswith("## Protected"):
                    in_block = False
                    continue
            if line.startswith("- "):
                if section == "constraints":
                    constraints.append(line)
                elif section == "decisions":
                    decisions.append(line)
                elif section == "evidence":
                    evidence.append(line)
            elif "ACTIVE CONSTRAINTS" in line:
                section = "constraints"
            elif "DECISIONS (current)" in line:
                section = "decisions"
            elif "VERIFIED EVIDENCE" in line:
                section = "evidence"

    excerpts = []
    for chunk in _conversation_chunks(request):
        lines = [l for l in chunk.split("\n") if l.strip()]
        if not lines:
            continue
        head = min(len(lines), LOSSY_HEAD_LINES)
        tail = max(len(lines) - LOSSY_TAIL_LINES, head)
        kept = [_clip_line(l, LOSSY_LINE_CLIP) for l in lines[:head]]
        if tail > head:
            kept.extend(_clip_line(l, LOSSY_LINE_CLIP) for l in lines[tail:])
        excerpts.append("(lossy excerpt) " + " / ".join(kept))

    previous_kept = ""
    start = request.find("<previous-summary>")
    if start >= 0:
        end_rel = request.find("</previous-summary>", start)
        if end_rel >= 0:
            previous = request[start + len("<previous-summary>") : end_rel].strip()
            if len(previous) > PREVIOUS_SUMMARY_KEEP_CHARS:
                previous_kept = "(decayed prior summary) " + previous[:PREVIOUS_SUMMARY_KEEP_CHARS] + "..."
            else:
                previous_kept = previous

    summary = ""
    summary += "## Goal\nContinue the user's task per the retained state.\n"
    summary += "## Constraints & Preferences\n"
    if not constraints:
        summary += "- None retained by the protected block.\n"
    else:
        for line in constraints:
            summary += line + "\n"
    summary += "## Progress\n### Done\n- [x] Prior turns summarized (lossy).\n\n### In Progress\n- [ ] Continuing the task.\n\n### Blocked\n- None.\n"
    summary += "## Key Decisions\n"
    if not decisions:
        summary += "- None recorded by the protected block.\n"
    else:
        for line in decisions:
            summary += line + "\n"
    summary += "## Next Steps\n1. Continue the task from the retained state.\n"
    summary += "## Critical Context\n"
    for line in evidence:
        summary += line + "\n"
    if previous_kept:
        summary += previous_kept + "\n"
    if excerpts:
        summary += "\n".join(excerpts) + "\n"
    if len(summary.split("\n")) < 8:
        summary += "(no further context retained)\n"
    return summary


def fixture_summarizer(system: str, user: str, max_tokens: float) -> SummarizerCallResult:
    assert system == SUMMARIZATION_SYSTEM_PROMPT
    return SummarizerCallResult(
        text=lossy_summary(user),
        stop_reason="stop",
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        latency_s=0.0,
        attempts=1,
    )
