"""E3ADV probe protocol: W17-E3 matchers + the action-number kind.

Reuses the W17-E3 probe machinery verbatim (answer protocol, extraction,
action-level matchers, majority scoring, negation-aware primary +
stricter strict score).  One new matcher kind is added for the
numeric-parameter family:

  action-number   ADV-C2: the current numeric parameter value.  Primary:
                  the gold number present (digit-bounded so 30/53 do not
                  match 3/5; number words "three"/"five" map to digits)
                  AND the forbidden (stale) number not asserted outside a
                  rejection context.  Rejection context = the e3-v1
                  marker set plus number-history words (was/were/from/
                  before/prior/originally/initially): "5 attempts (was
                  3)" states the current value and is primary-correct,
                  strict-incorrect.  Strict: forbidden absent entirely.

The inherited kinds run through the e3-v1 matchers unchanged
(action-approach for ADV-C1, action-acceptable for ADV-C3) so the two
suites stay directly comparable.  Extraction failures are reported
separately and never counted as model failures (M1/M3 discipline).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from context_rot_r2.live.e3 import probe as probe_e3

PROTOCOL_VERSION = "e3adv-v1"

# Reused verbatim from W17-E3 (which reuses W7).
ANSWER_PROTOCOL = probe_e3.ANSWER_PROTOCOL
PROBE_SYSTEM_PROMPT = probe_e3.PROBE_SYSTEM_PROMPT
extract_answer = probe_e3.extract_answer
normalize = probe_e3.normalize
REJECTION_MARKERS = probe_e3.REJECTION_MARKERS
REJECTION_WINDOW = probe_e3.REJECTION_WINDOW

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10",
}
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\b")

# History words that make a stale number a mention, not an assertion.
_NUMBER_HISTORY_MARKERS = re.compile(
    r"\b(was|were|from|before|prior|originally|initially)\b"
)


def _number_occurrences(norm: str, number: str) -> List[re.Match]:
    return list(re.finditer(rf"(?<!\d){re.escape(number)}(?!\d)", norm))


def number_asserted(norm: str, number: str) -> bool:
    """True if the number occurs outside any rejection/history context."""
    for match in _number_occurrences(norm, number):
        window = norm[max(0, match.start() - REJECTION_WINDOW): match.end() + REJECTION_WINDOW]
        if not REJECTION_MARKERS.search(window) and not _NUMBER_HISTORY_MARKERS.search(window):
            return True
    return False


def number_absent(norm: str, number: str) -> bool:
    return not _number_occurrences(norm, number)


def _match_number(probe: Dict[str, Any], norm: str) -> Dict[str, Any]:
    mapped = _NUMBER_WORD_RE.sub(lambda m: NUMBER_WORDS[m.group(1)], norm)
    gold = str(probe["goldNumber"])
    gold_present = bool(_number_occurrences(mapped, gold))
    forbidden = [str(n) for n in probe.get("forbiddenNumbers", [])]
    asserted = [n for n in forbidden if number_asserted(mapped, n)]
    absent = all(number_absent(mapped, n) for n in forbidden)
    return {
        "kind": "action-number",
        "goldPresent": gold_present,
        "forbiddenAsserted": asserted,
        "forbiddenAbsent": absent,
        "numbersFound": sorted({m.group(0) for m in re.finditer(r"\d+", mapped)}),
    }


def match_probe(probe: Dict[str, Any], answer: str) -> Dict[str, Any]:
    """Action-level match: e3-v1 matchers for inherited kinds, action-number here."""
    if probe["kind"] != "action-number":
        return probe_e3.match_probe(probe, answer)
    details = _match_number(probe, normalize(answer))
    details["primary"] = bool(details["goldPresent"]) and not details["forbiddenAsserted"]
    details["strict"] = bool(details["goldPresent"]) and details["forbiddenAbsent"]
    return details


# Majority scoring is matcher-agnostic (it only reads match["primary"]
# and match["strict"]); reused verbatim from W17-E3.
score_reps = probe_e3.score_reps
