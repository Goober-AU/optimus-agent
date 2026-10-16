"""Machine checkers for the OCC-2/OCC-3 benchmark task suite.

One checker per task family. All checkers accept a task dict (with at least
`family` and `gold_answer`) and a candidate answer (str, int, float, or a
list of strings for set answers), and return True only when the candidate
matches the gold answer under the family comparison rule:

- A (fact handoff):         exact match after whitespace collapse, casefold.
- B (state update):         exact match after whitespace collapse, casefold.
- C (procedural transfer):  exact match after whitespace collapse, casefold.
- D (multi-fact reasoning): order-insensitive set comparison on comma-separated
                            entity names; the canonical empty answer is "none".
- E (dependent calculation): numeric comparison with a small tolerance that
                            accepts int/float/numeric-string forms.

`check_answer` dispatches on `task["family"]`.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")

# Answers treated as "no entities" for family D set questions.
_EMPTY_MARKERS = {"", "none", "n/a", "-", "none."}


def normalize_text(value) -> str:
    """Collapse whitespace and casefold a value for exact-text comparison."""
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).strip().casefold()


def _parse_set(value) -> set:
    """Parse a comma-separated list answer (or a JSON list) into a name set."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = str(value).split(",")
    normalized = [normalize_text(item) for item in items]
    return {item for item in normalized if item not in _EMPTY_MARKERS}


def to_number(value):
    """Parse a value into a float, or None when it is not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("\"", "'"):
        text = text[1:-1].strip()
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def check_text(task, answer) -> bool:
    """Families A, B, C: whitespace-collapsed, case-insensitive exact match."""
    return normalize_text(answer) == normalize_text(task["gold_answer"])


def check_set(task, answer) -> bool:
    """Family D: order-insensitive comparison of comma-separated name sets."""
    return _parse_set(answer) == _parse_set(task["gold_answer"])


def check_number(task, answer) -> bool:
    """Family E: numeric comparison tolerant of int/float/string forms."""
    gold = to_number(task["gold_answer"])
    got = to_number(answer)
    if gold is None or got is None:
        return False
    tolerance = 1e-6 * max(1.0, abs(gold))
    return abs(got - gold) <= tolerance


CHECKERS = {
    "A": check_text,
    "B": check_text,
    "C": check_text,
    "D": check_set,
    "E": check_number,
}


def check_answer(task, answer) -> bool:
    """Dispatch to the family checker. Raises KeyError on unknown family."""
    return CHECKERS[task["family"]](task, answer)
