"""E3 probe protocol: fixed action questions, ANSWER extraction, matchers.

Reuses the W7 answer protocol verbatim (probe_protocol.ANSWER_PROTOCOL,
PROBE_SYSTEM_PROMPT, extract_answer): EXACTLY one line "ANSWER: <value>";
a response without the delimiter is an extraction failure, never a model
failure (M1/M3 discipline).

Matchers are action-level (version "e3-v1").  The gold is not a quote to
recall but the correct next action:
  action-file       TASK-A: the file to edit next (constraint-respecting);
  action-approach   TASK-C/TASK-F: the current approach after correction;
  action-error      TASK-E: the exact failing assertion (both numbers,
                    expected before actual, within a small window);
  action-acceptable TASK-H: >=1 acceptable approach and no rejected
                    approach asserted as acceptable.

Two scores per rep:
  primary (negation-aware): gold present AND no forbidden term asserted.  A
    forbidden term inside an explicit rejection context ("the lock was
    rejected", "not src/auth.rs") is not an assertion of that term.
  strict: gold present AND the forbidden term absent entirely.
Extraction failures are reported separately and never counted as model
failures.  Final points use the majority over repetitions (>= 2 of 3).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from context_rot_r2.live import probe_protocol as pp

PROTOCOL_VERSION = "e3-v1"

# Reused verbatim from W7 (the brief mandates the ANSWER: delimiter
# protocol and the same probe envelope).
ANSWER_PROTOCOL = pp.ANSWER_PROTOCOL
PROBE_SYSTEM_PROMPT = pp.PROBE_SYSTEM_PROMPT
extract_answer = pp.extract_answer
normalize = pp.normalize

REJECTION_MARKERS = re.compile(
    r"\b(not|no|never|instead|rather|reject|rejected|avoid|avoided|without|except|"
    r"superseded|supersedes|replaced|replacing|revert|reverted|forbidden|off|"
    r"drop|dropped|forget|forgot|stale|old|former|previously|earlier)\b"
)
REJECTION_WINDOW = 70

_PATH_RE = re.compile(r"[A-Za-z0-9_.\-/\\]+\.(?:rs|toml|json|py)")


def paths_in(answer: str) -> List[str]:
    return [m.group(0).replace("\\", "/") for m in _PATH_RE.finditer(answer)]


def _occurrences(norm: str, stem: str):
    return list(re.finditer(rf"\b{re.escape(stem)}\w*", norm))


def term_asserted(norm: str, stem: str) -> bool:
    """True if the forbidden stem occurs outside any rejection context."""
    for match in _occurrences(norm, stem):
        window = norm[max(0, match.start() - REJECTION_WINDOW):match.end() + REJECTION_WINDOW]
        if not REJECTION_MARKERS.search(window):
            return True
    return False


def term_absent(norm: str, stem: str) -> bool:
    return not _occurrences(norm, stem)


def _match_error(probe: Dict[str, Any], norm: str) -> Dict[str, Any]:
    numbers: List[str] = probe["requiredNumbers"]
    gap = int(probe.get("numberGapChars", 60))
    keywords: List[str] = probe["keywords"]
    positions = []
    search_from = 0
    for number in numbers:
        match = re.search(rf"(?<!\d){re.escape(number)}(?!\d)", norm[search_from:])
        if match is None:
            return {"goldPresent": False, "numbersPresent": 0}
        positions.append(search_from + match.start())
        search_from += match.end()
    ordered_within_gap = all(
        positions[i + 1] - positions[i] <= gap for i in range(len(positions) - 1)
    )
    keyword_ok = any(keyword in norm for keyword in keywords)
    return {
        "goldPresent": bool(ordered_within_gap and keyword_ok),
        "numbersPresent": len(positions),
        "orderedWithinGap": ordered_within_gap,
        "keywordOk": keyword_ok,
    }


def match_probe(probe: Dict[str, Any], answer: str) -> Dict[str, Any]:
    """Action-level match. Returns primary/strict booleans plus details."""
    norm = normalize(answer)
    kind = probe["kind"]
    details: Dict[str, Any] = {"kind": kind}
    if kind == "action-file":
        gold_stem = probe["goldStem"]
        gold_present = re.search(rf"\b{re.escape(gold_stem)}", norm) is not None
        forbidden = probe.get("forbiddenStems", [])
        details["goldPresent"] = gold_present
        details["forbiddenAsserted"] = [s for s in forbidden if term_asserted(norm, s)]
        details["forbiddenAbsent"] = all(term_absent(norm, s) for s in forbidden)
        details["paths"] = paths_in(answer)
    elif kind == "action-approach":
        positives: List[str] = probe["positives"]
        gold_present = any(p in norm for p in positives)
        forbidden = probe.get("forbiddenStems", [])
        details["goldPresent"] = gold_present
        details["positiveHit"] = next((p for p in positives if p in norm), None)
        details["forbiddenAsserted"] = [s for s in forbidden if term_asserted(norm, s)]
        details["forbiddenAbsent"] = all(term_absent(norm, s) for s in forbidden)
    elif kind == "action-acceptable":
        acceptable: List[str] = probe["acceptable"]
        forbidden = probe.get("forbiddenStems", [])
        details["acceptableHits"] = [a for a in acceptable if a in norm]
        gold_present = bool(details["acceptableHits"])
        details["coverage"] = len(details["acceptableHits"])
        details["forbiddenAsserted"] = [s for s in forbidden if term_asserted(norm, s)]
        details["forbiddenAbsent"] = all(term_absent(norm, s) for s in forbidden)
    elif kind == "action-error":
        details.update(_match_error(probe, norm))
        gold_present = details["goldPresent"]
        details["forbiddenAsserted"] = []
        details["forbiddenAbsent"] = True
    else:
        raise ValueError(f"unknown probe kind: {kind}")
    primary = bool(gold_present) and not details["forbiddenAsserted"]
    strict = bool(gold_present) and details["forbiddenAbsent"]
    details["primary"] = primary
    details["strict"] = strict
    return details


def score_reps(probe: Dict[str, Any], reps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Majority scoring across repetitions (>= 2 of 3, or the single rep).

    correct:   majority of extracted answers pass the primary matcher.
    incorrect: extracted answers that fail the primary matcher.
    extraction_failure: every repetition lacked the ANSWER: delimiter.
    The strict majority is reported alongside, never replacing the primary.
    """
    extracted = [r for r in reps if r.get("extracted") is not None]
    if not extracted:
        status = strict_status = "extraction_failure"
    else:
        correct = sum(1 for r in extracted if r["match"]["primary"])
        strict_correct = sum(1 for r in extracted if r["match"]["strict"])
        if len(reps) == 1:
            status = "correct" if correct == 1 else "incorrect"
            strict_status = "correct" if strict_correct == 1 else "incorrect"
        else:
            status = "correct" if correct * 2 > len(reps) else "incorrect"
            strict_status = "correct" if strict_correct * 2 > len(reps) else "incorrect"
    return {
        "status": status,
        "strictStatus": strict_status,
        "correctReps": sum(1 for r in extracted if r["match"]["primary"]),
        "strictCorrectReps": sum(1 for r in extracted if r["match"]["strict"]),
        "extractedReps": len(extracted),
        "totalReps": len(reps),
    }
