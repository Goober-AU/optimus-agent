"""R2-E2 probe protocol: questions, answer extraction, matchers (M1/M3/M4 fixes).

M1/M3: every probe request demands EXACTLY one line "ANSWER: <value>";
a response without the delimiter is an extraction failure, never a model
failure.  M4: one versioned matcher per probe kind ("live-v1").

Questions are derived deterministically from the scenario probe records
plus the template families in context_rot_r2/scenario.py: the probe id
carries the item index, the template index is i % len(templates), and the
subject name is parsed from the planted seed text via the same template.
The question names the subject (the lookup key a real user would give) but
never the answer.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from context_rot_r2.scenario import (
    CONSTRAINT_TEMPLATES,
    DECISION_TEMPLATES,
    EVIDENCE_TEMPLATES,
)

PROTOCOL_VERSION = "live-v1"

ANSWER_PROTOCOL = (
    "Answer the question below using only the conversation context above. "
    "Respond with EXACTLY one line in this format:\n"
    "ANSWER: <value>\n"
    "No other text and no explanation. If the context does not contain the "
    "answer, respond:\n"
    "ANSWER: unknown\n\n"
    "Question: {question}"
)

PROBE_SYSTEM_PROMPT = (
    "You are a careful assistant. Answer questions using only the "
    "conversation context provided to you."
)


def _template_pattern(template: str) -> re.Pattern:
    """Escape a scenario template, turning {name}/{count}/{tag} into groups."""
    pattern = ""
    i = 0
    while i < len(template):
        if template[i] == "{":
            end = template.index("}", i)
            field = template[i + 1 : end]
            if field == "name":
                pattern += r"([A-Za-z]+)"
            elif field == "count":
                pattern += r"(\d+)"
            else:
                pattern += r"\S+"
            i = end + 1
        else:
            pattern += re.escape(template[i])
            i += 1
    return re.compile(pattern)


def _seed_text_for_anchor(scenario: Dict[str, Any], anchor: str) -> Optional[str]:
    for spec in scenario["seedEntries"]:
        if anchor in spec.get("text", ""):
            return spec["text"]
    return None


def _anchor_line(scenario: Dict[str, Any], anchor: str) -> Optional[str]:
    """Return the single line that carries the anchor tag.

    Constraint probes in the hard scenario share one opening seed entry with
    20 tagged lines, so name extraction must anchor on the probe's own line;
    matching the first line with the template would alias probes 8-19 onto
    probes 0-7 (same template, different domain) and produce duplicate
    questions with mismatched golds.
    """
    for spec in scenario["seedEntries"]:
        text = spec.get("text", "")
        if anchor in text:
            for line in text.split("\n"):
                if anchor in line:
                    return line
    return None


CONSTRAINT_QUESTIONS = {
    0: "In this session, the user stated a constraint about the {name} ledger. What is that constraint? Quote it exactly as stated.",
    1: "In this session, the user stated a constraint about running the {name} suite before publishing. What is that constraint? Quote it exactly as stated.",
    2: "In this session, the user stated a constraint about modifying files under the {name} directory. What is that constraint? Quote it exactly as stated.",
    3: "In this session, the user stated a constraint about the {name} protocol version. What is that constraint? Quote it exactly as stated.",
    4: "In this session, the user stated a constraint about the {name} migration. What is that constraint? Quote it exactly as stated.",
    5: "In this session, the user stated a constraint about new dependencies in {name}. What is that constraint? Quote it exactly as stated.",
    6: "In this session, the user stated a constraint about the {name} audit trail. What is that constraint? Quote it exactly as stated.",
    7: "In this session, the user stated a constraint about the {name} service. What is that constraint? Quote it exactly as stated.",
}

DECISION_QUESTIONS = {
    0: "In this session, a decision was made about which architecture to use for the parser. What was decided? Quote the decision exactly as stated.",
    1: "In this session, a decision was made about the {name} client. What was decided? Quote the decision exactly as stated.",
    2: "In this session, a decision was made about the {name} rewrite. What was decided? Quote the decision exactly as stated.",
    3: "In this session, a decision was made about the {name} fix. What was decided? Quote the decision exactly as stated.",
}

# Templates 0 and 1 carry no service name (scenario.py EVIDENCE_TEMPLATES),
# so their questions cannot name the subject; with n_evidence > 4 the same
# template repeats and the ordinal disambiguates which run is asked for.
EVIDENCE_QUESTIONS = {
    0: "According to the tool results in this session, how many tests passed in the cargo test run?",
    1: "According to the tool results in this session, how many warnings did the successful build report?",
    2: "According to the tool results in this session, how many findings did the audit report for {name} contain?",
    3: "According to the tool results in this session, what p95 latency in milliseconds did the benchmark for {name} report?",
}

_EVIDENCE_DISAMBIGUATOR = {
    0: " If several cargo test runs are mentioned, use the {ordinal} one.",
    1: " If several builds are mentioned, use the {ordinal} one.",
}

CORRECTION_QUESTION = (
    "In this session, the user corrected an earlier decision about the {name} "
    "module's architecture. After the correction, which architecture is the "
    "current choice for the {name} module? Answer with the architecture."
)

SUBAGENT_QUESTION = (
    "In this session, a subagent sent a completion report. Which subagent sent "
    "it, which section did it finish, and which artifact did it produce?"
)


def _probe_index(probe_id: str) -> int:
    if probe_id.endswith("subagent"):
        return 0
    return int(probe_id.rsplit("-", 1)[1])


def build_question(probe: Dict[str, Any], scenario: Dict[str, Any]) -> str:
    kind = probe["kind"]
    index = _probe_index(probe["id"])
    seed_text = _seed_text_for_anchor(scenario, probe["anchor"]) or ""
    gold = probe.get("gold", "")
    if kind == "constraint":
        template = CONSTRAINT_TEMPLATES[index % len(CONSTRAINT_TEMPLATES)]
        anchor_line = _anchor_line(scenario, probe["anchor"]) or seed_text or ""
        match = _template_pattern(template).search(gold) or _template_pattern(template).search(anchor_line)
        name = match.group(1) if match else "subject"
        return CONSTRAINT_QUESTIONS[index % len(CONSTRAINT_QUESTIONS)].format(name=name)
    if kind == "decision":
        template = DECISION_TEMPLATES[index % len(DECISION_TEMPLATES)]
        anchor_line = _anchor_line(scenario, probe["anchor"]) or seed_text or ""
        match = _template_pattern(template).search(gold) or _template_pattern(template).search(anchor_line)
        name = match.group(1) if match else "subject"
        return DECISION_QUESTIONS[index % len(DECISION_QUESTIONS)].format(name=name)
    if kind == "evidence":
        template = EVIDENCE_TEMPLATES[index % len(EVIDENCE_TEMPLATES)]
        has_name = "{name}" in template
        name = "subject"
        if has_name:
            match = _template_pattern(template).search(seed_text)
            if match:
                name = match.group(1)
        question = EVIDENCE_QUESTIONS[index % len(EVIDENCE_QUESTIONS)].format(name=name)
        n_evidence = sum(1 for p in scenario["probes"] if p["kind"] == "evidence")
        if n_evidence > len(EVIDENCE_TEMPLATES) and not has_name:
            ordinal = {0: "first", 1: "second", 2: "third"}.get(index // len(EVIDENCE_TEMPLATES), "last")
            question += _EVIDENCE_DISAMBIGUATOR[index % len(EVIDENCE_TEMPLATES)].format(ordinal=ordinal)
        return question
    if kind == "supersession":
        match = re.search(r"for the ([A-Za-z]+) module instead", seed_text)
        name = match.group(1) if match else "subject"
        return CORRECTION_QUESTION.format(name=name)
    if kind == "task":
        return SUBAGENT_QUESTION
    raise ValueError(f"unknown probe kind: {kind}")


# ---------------------------------------------------------------------------
# Extraction (M1/M3)
# ---------------------------------------------------------------------------


def extract_answer(content: str) -> Optional[str]:
    """The text after 'ANSWER:' on the last line carrying the delimiter."""
    if not content:
        return None
    value = None
    for line in content.split("\n"):
        if "ANSWER:" in line:
            value = line.split("ANSWER:", 1)[1].strip().strip('"').strip("'").strip()
    return value


# ---------------------------------------------------------------------------
# Matchers (M4: one versioned matcher per kind)
# ---------------------------------------------------------------------------

_NORMAL_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return _NORMAL_RE.sub(" ", text.lower()).strip()


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: len(word) - len(suffix)]
            break
    if len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "aeiouss":
        word = word[:-1]
    return word


def _content_words(text: str) -> List[str]:
    return [w for w in normalize(text).split() if len(w) > 1]


def _phrase_match(core: str, answer: str) -> bool:
    """Normalized containment, or full stemmed content-word coverage."""
    norm_core = normalize(core)
    norm_answer = normalize(answer)
    if not norm_core:
        return False
    if norm_core in norm_answer:
        return True
    core_words = set(_stem(w) for w in _content_words(core))
    if not core_words:
        return False
    answer_words = set(_stem(w) for w in _content_words(answer))
    return core_words.issubset(answer_words)


def match_answer(probe: Dict[str, Any], answer: str) -> bool:
    kind = probe["kind"]
    gold = probe.get("gold", "")
    if kind in ("constraint", "decision"):
        core = gold
        for prefix in ("CONSTRAINT: ", "Decision: "):
            if core.startswith(prefix):
                core = core[len(prefix):]
        return _phrase_match(core, answer)
    if kind == "evidence":
        return re.search(rf"(?<!\d){re.escape(gold)}(?!\d)", answer) is not None
    if kind == "supersession":
        return "architecture b" in normalize(answer)
    if kind == "task":
        norm = normalize(answer)
        return "reviewer 1" in norm and "section 3" in norm
    raise ValueError(f"unknown probe kind: {kind}")


def score_reps(probe: Dict[str, Any], reps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Majority scoring across repetitions.

    correct:   >= 2 of 3 (or the single rep) match.
    incorrect:  extracted answers that fail the matcher.
    extraction_failure: every repetition lacked the ANSWER: delimiter.
    """
    extracted = [r for r in reps if r.get("extracted") is not None]
    correct = sum(1 for r in extracted if r["match"])
    if not extracted:
        status = "extraction_failure"
    elif len(reps) == 1:
        status = "correct" if correct == 1 else "incorrect"
    else:
        status = "correct" if correct >= 2 else "incorrect"
    return {
        "status": status,
        "correctReps": correct,
        "extractedReps": len(extracted),
        "totalReps": len(reps),
    }
