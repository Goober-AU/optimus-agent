"""Answer extraction, scoring, uncertainty and abstention helpers.

The DGX DeepSeek endpoint sometimes puts reasoning text in ``content`` before
the final line, so extraction is explicit and rule-tagged. Every score record
says which rule fired (``extraction_rule``) and how the value was pulled out of
the chosen span (``extraction_detail["value_rule"]``).
"""

import json
import math
import random
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_QUOTES = "\"'`\u201c\u201d"
_TRAILING = ".,;:!?"

REASONING_MARKERS: Tuple[str, ...] = (
    "thinking process", "chain of thought", "reasoning:", "analysis:",
    "let me think", "let's think", "step 1:", "first,", "we need to",
    "<reasoning>", "lethink", "scratchpad:",
)

_ANSWER_PREAMBLE_RE = re.compile(
    r"^(?:so[, ]+|thus[, ]+|therefore[, ]+|hence[, ]+|final answer\s*[:\-]\s*|"
    r"final\s*[:\-]\s*|answer\s*[:\-]\s*|the answer is\s*[:\-]?\s*|"
    r"it (?:is|was)\s+|therefore the answer is\s*)\s*",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    """Casefold, collapse whitespace, strip surrounding quotes/punctuation."""
    if text is None:
        return ""
    out = str(text).strip()
    out = out.strip(_QUOTES + " ")
    out = _WS_RE.sub(" ", out)
    out = out.strip(_QUOTES + " ")
    out = out.rstrip(_TRAILING)
    return out.strip()


def normalize_answer(text: str) -> str:
    return normalize_text(text).casefold()


def _strip_preamble(text: str) -> str:
    out = text
    for _ in range(3):
        new = _ANSWER_PREAMBLE_RE.sub("", out, count=1)
        if new == out:
            break
        out = new
    return out.strip()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

EXTRACTION_RULES: Tuple[str, ...] = (
    "json:field",
    "delimiter:final_answer",
    "delimiter:brackets",
    "last_line",
    "last_time",
    "last_number",
    "whole_text",
    "empty",
)

_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

_DELIM_RE = re.compile(
    r"(?:FINAL\s+ANSWER|Final\s+Answer|final\s+answer|FINAL|Final|ANSWER|Answer)\s*[:\-]\s*(.+)"
)
_BRACKET_RE = re.compile(r"\[\[(.+?)\]\]", re.DOTALL)


@dataclass(frozen=True)
class Extraction:
    """Result of pulling a candidate answer out of raw model text."""

    answer: str
    rule: str
    value_rule: str
    stripped_reasoning: bool
    raw_chars: int
    detail: Dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "answer": self.answer,
            "rule": self.rule,
            "value_rule": self.value_rule,
            "stripped_reasoning": self.stripped_reasoning,
            "raw_chars": self.raw_chars,
        }


def _looks_like_reasoning(text: str) -> bool:
    low = text.casefold()
    return any(marker in low for marker in REASONING_MARKERS)


def _json_answer(raw: str) -> Optional[str]:
    text = raw.strip()
    candidates: List[str] = []
    if text.startswith("{") or text.startswith("["):
        candidates.append(text)
    for match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            for key in ("answer", "final", "final_answer", "result", "value"):
                if key in obj and obj[key] not in (None, ""):
                    value = obj[key]
                    if isinstance(value, (list, tuple)):
                        return ", ".join(str(v) for v in value)
                    return str(value)
    return None


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _last_time(text: str) -> Optional[str]:
    matches = _TIME_RE.findall(text)
    if not matches:
        return None
    hh, mm = matches[-1]
    return "%02d:%02d" % (int(hh), int(mm))


def _last_number(text: str) -> Optional[str]:
    matches = _NUM_RE.findall(text)
    return matches[-1] if matches else None


def _value_from_span(span: str, answer_kind: str) -> Tuple[str, str]:
    """Pull the answer value out of a chosen span; return (value, value_rule)."""
    stripped = _strip_preamble(normalize_text(span))
    if answer_kind == "numeric":
        number = _last_number(stripped)
        if number is not None:
            return number, "last_number_in_span"
        return stripped, "verbatim"
    if answer_kind == "time":
        time_value = _last_time(stripped)
        if time_value is not None:
            return time_value, "last_time_in_span"
        return stripped, "verbatim"
    return stripped, "verbatim"


def extract_answer(raw: str, answer_kind: str, json_style: bool = False) -> Extraction:
    """Extract the final answer from raw model text.

    Rule order: json:field -> delimiter:final_answer -> delimiter:brackets ->
    last_line -> last_time/last_number -> whole_text -> empty.
    """
    raw = "" if raw is None else str(raw)
    if not raw.strip():
        return Extraction("", "empty", "empty", False, len(raw), {"json_style": json_style})

    stripped_reasoning = _looks_like_reasoning(raw)

    json_value = _json_answer(raw)
    if json_value is not None:
        value, value_rule = _value_from_span(json_value, answer_kind)
        return Extraction(value, "json:field", value_rule, stripped_reasoning,
                          len(raw), {"json_style": json_style})

    matches = list(_DELIM_RE.finditer(raw))
    if matches:
        span = matches[-1].group(1).strip()
        value, value_rule = _value_from_span(span, answer_kind)
        return Extraction(value, "delimiter:final_answer", value_rule,
                          stripped_reasoning, len(raw),
                          {"json_style": json_style, "delimiter": matches[-1].group(0)[:24]})

    brackets = list(_BRACKET_RE.finditer(raw))
    if brackets:
        value, value_rule = _value_from_span(brackets[-1].group(1), answer_kind)
        return Extraction(value, "delimiter:brackets", value_rule,
                          stripped_reasoning, len(raw), {"json_style": json_style})

    line = _last_line(raw)
    if line:
        if answer_kind == "time":
            value = _last_time(line)
            if value is not None:
                return Extraction(value, "last_line", "last_time_in_line",
                                  stripped_reasoning, len(raw), {"json_style": json_style})
        if answer_kind == "numeric":
            value = _last_number(line)
            if value is not None:
                return Extraction(value, "last_line", "last_number_in_line",
                                  stripped_reasoning, len(raw), {"json_style": json_style})
        if len(line.split()) <= 12:
            value, value_rule = _value_from_span(line, answer_kind)
            return Extraction(value, "last_line", value_rule, stripped_reasoning,
                              len(raw), {"json_style": json_style})

    if answer_kind == "time":
        value = _last_time(raw)
        if value is not None:
            return Extraction(value, "last_time", "last_time_in_text",
                              stripped_reasoning, len(raw), {"json_style": json_style})
    if answer_kind == "numeric":
        value = _last_number(raw)
        if value is not None:
            return Extraction(value, "last_number", "last_number_in_text",
                              stripped_reasoning, len(raw), {"json_style": json_style})

    value, value_rule = _value_from_span(raw, answer_kind)
    return Extraction(value, "whole_text", value_rule, stripped_reasoning,
                      len(raw), {"json_style": json_style})


# ---------------------------------------------------------------------------
# Kind-aware comparison
# ---------------------------------------------------------------------------

def _norm_code(text: str) -> str:
    out = normalize_answer(text)
    out = out.replace("`", "").strip()
    return out


def _as_number(text: str) -> Optional[float]:
    if text is None:
        return None
    cleaned = normalize_text(str(text)).replace(",", "").replace("_", "")
    match = _NUM_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _as_clock(text: str) -> Optional[str]:
    if text is None:
        return None
    match = _TIME_RE.search(str(text))
    if match:
        return "%02d:%02d" % (int(match.group(1)), int(match.group(2)))
    # tolerate "10.15" and "1015" forms
    match = re.search(r"\b(\d{1,2})[.](\d{2})\b", str(text))
    if match:
        return "%02d:%02d" % (int(match.group(1)), int(match.group(2)))
    return None


def _contains_phrase(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    pattern = r"(?<![0-9a-z])" + re.escape(needle) + r"(?![0-9a-z])"
    return re.search(pattern, haystack) is not None


def match_value(answer: str, gold: str, answer_kind: str,
                aliases: Sequence[str] = ()) -> bool:
    """Kind-aware match of an extracted answer against gold (+ aliases)."""
    if not answer or not gold:
        return False
    candidates = [gold] + [a for a in aliases if a]
    if answer_kind == "numeric":
        got = _as_number(answer)
        if got is None:
            return False
        for cand in candidates:
            want = _as_number(cand)
            if want is not None and math.isclose(got, want, rel_tol=0, abs_tol=1e-9):
                return True
        return False
    if answer_kind == "time":
        got = _as_clock(answer)
        if got is None:
            return False
        for cand in candidates:
            want = _as_clock(cand)
            if want is not None and got == want:
                return True
        return False
    norm_answer = _norm_code(answer)
    norm_gold = _norm_code(gold)
    if norm_answer == norm_gold:
        return True
    if norm_answer.endswith(norm_gold) and len(norm_answer) - len(norm_gold) <= 24:
        return True
    for cand in candidates:
        norm_cand = _norm_code(cand)
        if norm_cand and _contains_phrase(norm_answer, norm_cand):
            return True
    return False


def _guess_kind(value: str) -> str:
    if _TIME_RE.search(value):
        return "time"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", normalize_text(value)):
        return "numeric"
    return "code"


# ---------------------------------------------------------------------------
# Abstention / unsupported claims
# ---------------------------------------------------------------------------

ABSTAIN_PATTERNS: Tuple[str, ...] = (
    "abstain", "i don't know", "i do not know", "cannot determine",
    "can't determine", "cannot be determined", "not enough information",
    "insufficient information", "no information", "not mentioned",
    "not stated", "not specified", "unanswerable", "cannot answer",
    "not in the context", "unable to determine",
)

STOPWORDS = frozenset(
    "a an the of to in for with on by from that this which it is was were and or "
    "as at be been being do does did not no yes answer value number time".split()
)


def detect_abstention(text: str) -> bool:
    """True when the model refused / declined to answer the question."""
    if text is None:
        return False
    low = str(text).casefold()
    return any(pattern in low for pattern in ABSTAIN_PATTERNS)


def _content_words(text: str) -> List[str]:
    """Alphabetic content words only: ids, numbers and derived values are excluded."""
    words = re.findall(r"[a-z][a-z\-]{3,}", str(text).casefold())
    out = []
    for word in words:
        for part in word.split("-"):
            if len(part) > 3 and part not in STOPWORDS:
                out.append(part)
    return out


def detect_unsupported_claim(answer: str, context_text: str) -> Tuple[bool, str]:
    """Heuristic hallucination flag for prose answers.

    The check needs at least two distinct content words in the answer; ids,
    numbers and short derived values are not judged (they are expected to be
    absent from the context by design). Returns ``(flag, reason)``.
    """
    if not answer:
        return False, ""
    words = sorted(set(_content_words(answer)))
    if len(words) < 2:
        return False, ""
    context_low = str(context_text or "").casefold()
    if any(word in context_low for word in words):
        return False, ""
    return True, "no_content_word_in_context"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreResult:
    correct: Optional[bool]
    extraction_rule: str
    extraction_detail: Dict[str, object]
    normalized_answer: str
    matched: bool
    abstained: bool
    unsupported_claim: bool
    unsupported_reason: str
    invalid_reason: Optional[str]
    matched_facts: Tuple[str, ...] = ()
    missing_facts: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "correct": self.correct,
            "extraction_rule": self.extraction_rule,
            "extraction_detail": self.extraction_detail,
            "normalized_answer": self.normalized_answer,
            "matched": self.matched,
            "abstained": self.abstained,
            "unsupported_claim": self.unsupported_claim,
            "unsupported_reason": self.unsupported_reason,
            "invalid_reason": self.invalid_reason,
            "matched_facts": list(self.matched_facts),
            "missing_facts": list(self.missing_facts),
        }


def score_answer(raw_answer: str, gold_answer: str, answer_kind: str,
                 aliases: Sequence[str] = (), facts: Sequence[str] = (),
                 context_text: Optional[str] = None,
                 json_style: bool = False,
                 invalid_reason: Optional[str] = None) -> ScoreResult:
    """Score one answer.

    ``invalid_reason`` short-circuits scoring: an invalid item never becomes a
    wrong answer (requirement 4), it gets ``correct=None``.
    """
    extraction = extract_answer(raw_answer, answer_kind, json_style=json_style)
    abstained = detect_abstention(raw_answer) or detect_abstention(extraction.answer)
    unsupported, unsupported_reason = (False, "")
    if context_text is not None:
        unsupported, unsupported_reason = detect_unsupported_claim(
            extraction.answer, context_text)

    if invalid_reason:
        return ScoreResult(
            correct=None,
            extraction_rule=extraction.rule,
            extraction_detail=extraction.as_dict(),
            normalized_answer=normalize_answer(extraction.answer),
            matched=False,
            abstained=abstained,
            unsupported_claim=unsupported,
            unsupported_reason=unsupported_reason,
            invalid_reason=invalid_reason,
        )

    if answer_kind == "multi":
        fact_list = list(facts) or [f for f in str(gold_answer).split("|") if f]
        matched: List[str] = []
        missing: List[str] = []
        for fact in fact_list:
            fact_kind = _guess_kind(fact)
            if match_value(extraction.answer, fact, fact_kind):
                matched.append(fact)
            else:
                missing.append(fact)
        correct = bool(fact_list) and not missing
        return ScoreResult(
            correct=correct,
            extraction_rule=extraction.rule,
            extraction_detail=extraction.as_dict(),
            normalized_answer=normalize_answer(extraction.answer),
            matched=correct,
            abstained=abstained,
            unsupported_claim=unsupported,
            unsupported_reason=unsupported_reason,
            invalid_reason=None,
            matched_facts=tuple(matched),
            missing_facts=tuple(missing),
        )

    matched = match_value(extraction.answer, gold_answer, answer_kind, aliases)
    return ScoreResult(
        correct=matched,
        extraction_rule=extraction.rule,
        extraction_detail=extraction.as_dict(),
        normalized_answer=normalize_answer(extraction.answer),
        matched=matched,
        abstained=abstained,
        unsupported_claim=unsupported,
        unsupported_reason=unsupported_reason,
        invalid_reason=None,
    )


# ---------------------------------------------------------------------------
# Paired bootstrap
# ---------------------------------------------------------------------------

def paired_bootstrap_ci(deltas: Sequence[float], iters: int = 2000, seed: int = 1234,
                        alpha: float = 0.05) -> dict:
    """Percentile bootstrap CI for the mean of paired deltas.

    Deterministic: the resampling RNG is seeded. Returns ``{}`` when empty.
    """
    values = [float(d) for d in deltas]
    if not values:
        return {"n": 0, "mean": None, "lo": None, "hi": None, "iters": iters, "seed": seed}
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iters):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    lo_idx = max(0, int(math.floor(alpha / 2.0 * iters)))
    hi_idx = min(iters - 1, int(math.ceil((1.0 - alpha / 2.0) * iters)) - 1)
    mean = sum(values) / n
    return {
        "n": n,
        "mean": mean,
        "lo": means[lo_idx],
        "hi": means[hi_idx],
        "iters": iters,
        "seed": seed,
        "alpha": alpha,
        "method": "paired_percentile_bootstrap",
    }


def paired_deltas(records_a: Sequence[dict], records_b: Sequence[dict],
                  key: str = "item_id", score: str = "correct") -> List[float]:
    """Pair two result sets by ``key``; only comparable, valid pairs are returned."""
    index_a = {}
    for rec in records_a:
        if rec.get(score) is None:
            continue
        index_a[rec.get(key)] = float(rec[score])
    deltas = []
    for rec in records_b:
        if rec.get(score) is None:
            continue
        other = index_a.get(rec.get(key))
        if other is None:
            continue
        deltas.append(float(rec[score]) - other)
    return deltas
