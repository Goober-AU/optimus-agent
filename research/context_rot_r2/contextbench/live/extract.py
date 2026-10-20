"""Retained-reference extraction from compaction artifacts.

The working context after a compaction cycle is the summary text, the
task-state ledger (arm B) and the kept tail turns. File and line-span
references are recovered from all three sources:

- kept tail: exact - the driver knows which read pairs the tail still
  contains (a read is retained with its span iff its assistant turn is in
  the kept tail; a user request alone retains the file without the span);
- summary/ledger: regex over path tokens and nearby line ranges.

Extracted paths are normalized like the gold side
(`gold.normalize_rel_path`). Paths are NOT filtered against a repository
checkout (offline rule; upstream `_filter_step_to_repo` existence filtering
stays a live-phase-with-checkout item), so a hallucinated-but-real path
counts against precision. This is a documented deviation.
"""

import re
from typing import Dict, List, Set, Tuple

from ..gold import normalize_rel_path

# repo-relative path with at least one directory and a file extension
PATH_RE = re.compile(r"(?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+\.[A-Za-z0-9]{1,8}")
# "[path:start-end]" echo of the transcript read format
BRACKET_RE = re.compile(r"\[([^\]\s:]+):(\d+)-(\d+)\]")
# a line range following a path mention on the same line
RANGE_AFTER_RE = re.compile(r"lines?\s+(\d+)\s*(?:-|to|\u2013)\s*(\d+)", re.IGNORECASE)
RANGE_BARE_RE = re.compile(r"\b(\d{1,5})\s*-\s*(\d{1,5})\b")
MAX_LOOKAHEAD = 60
# trailing characters stripped from a path token
_PATH_STRIP = ".,;:)" + chr(34) + chr(39)


def _add_span(spans: List[dict], path: str, start: int, end: int) -> None:
    if start > end:
        start, end = end, start
    if start <= 0:
        return
    spans.append({"file": path, "start_line": start, "end_line": end})


def _normalize_path(raw: str) -> str:
    """Normalize a path, with a container-prefix second try.

    A model echo like `testbed/pkg/file.py` (leading slash lost) goes
    through the `/testbed/` branch by retrying with the slash; plain
    repo-relative paths are unchanged.
    """
    normalized = normalize_rel_path(raw)
    if normalized == raw:
        normalized = normalize_rel_path("/" + raw)
    return normalized


def extract_refs(text: str) -> Tuple[Set[str], List[dict]]:
    """Extract (files, spans) referenced in a text (summary or ledger)."""
    files: Set[str] = set()
    spans: List[dict] = []
    bracket_regions = []
    for match in BRACKET_RE.finditer(text):
        path = _normalize_path(match.group(1))
        if not path:
            continue
        files.add(path)
        _add_span(spans, path, int(match.group(2)), int(match.group(3)))
        bracket_regions.append(match.span())
    for line in text.splitlines():
        for match in PATH_RE.finditer(line):
            if any(start <= match.start() < end for start, end in bracket_regions):
                continue
            path = _normalize_path(match.group(0).rstrip(_PATH_STRIP))
            if not path:
                continue
            files.add(path)
            window = line[match.end() : match.end() + MAX_LOOKAHEAD]
            range_match = RANGE_AFTER_RE.search(window) or RANGE_BARE_RE.search(window)
            if range_match:
                _add_span(
                    spans, path, int(range_match.group(1)), int(range_match.group(2))
                )
    return files, spans


def retained_refs(
    kept_turn_ids,
    read_pairs: List[dict],
    summary_text: str,
    ledger_text: str,
) -> Tuple[Set[str], List[dict]]:
    """Retained (files, spans) for one compaction cycle.

    `read_pairs` maps turn ids to read spans (see scenario.build_plan);
    a read keeps its span iff its assistant (content) turn survived in the
    kept tail, and keeps a bare file mention if only the user request
    survived. Summary and ledger references are added on top.
    """
    kept = set(kept_turn_ids or [])
    files: Set[str] = set()
    spans: List[dict] = []
    for pair in read_pairs:
        assistant_kept = pair["assistant_turn_id"] in kept
        user_kept = pair["user_turn_id"] in kept
        if not (assistant_kept or user_kept):
            continue
        files.add(pair["file"])
        if assistant_kept:
            _add_span(spans, pair["file"], pair["start_line"], pair["end_line"])
    summary_files, summary_spans = extract_refs(summary_text or "")
    ledger_files, ledger_spans = extract_refs(ledger_text or "")
    files |= summary_files | ledger_files
    spans.extend(summary_spans)
    spans.extend(ledger_spans)
    return files, spans


def refs_step(files: Set[str], spans: List[dict]) -> dict:
    """One trajectory step in the unified pred_steps shape."""
    return {
        "files": sorted(files),
        "spans": {
            path: [
                {"start": span["start_line"], "end": span["end_line"]}
                for span in spans
                if span["file"] == path
            ]
            for path in sorted({span["file"] for span in spans})
        },
    }
