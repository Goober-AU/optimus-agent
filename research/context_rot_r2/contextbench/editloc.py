"""Edit localization: predicted patch location vs gold context ranges.

`parse_diff_lines` and `_parse_hunks` mirror ContextBench
`contextbench/parsers/diff.py` (commit 1436c28a):

- `parse_diff_lines` <- diff.py:30-50 (`parse_diff_lines`);
- `_merge_line_intervals` <- diff.py:52-66;
- `_parse_hunks` <- diff.py:69-150, including the `+++ b/<path>` header
  capture, the `@@ -a,b +c,d @@` hunk regex, and the deletions-only walk
  in OLD file coordinates.

`editloc_metrics` mirrors the EditLoc block of `evaluate_instance`
(contextbench/evaluate.py:231-295): predicted deleted lines (expanded one
by one) are checked for membership in the gold line ranges; recall and
precision are both `hits / pred_line_count` (upstream sets them equal,
evaluate.py:270-273); `gold_size` is the total gold line count
(evaluate.py:276-280).
"""

import re
from typing import Dict, List, Tuple

LineInterval = Tuple[int, int]

_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _merge_line_intervals(intervals: List[LineInterval]) -> List[LineInterval]:
    """Merge overlapping or adjacent line intervals. Mirror of diff.py:52-66."""
    if not intervals:
        return []
    sorted_intervals = sorted(intervals)
    merged = [sorted_intervals[0]]
    for current in sorted_intervals[1:]:
        last = merged[-1]
        if current[0] <= last[1] + 1:
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)
    return merged


def _parse_hunks(
    diff_text: str, deletions_only: bool = False
) -> Dict[str, List[Tuple[int, int, str]]]:
    """Parse diff hunks. Returns {file: [(start_line, end_line, change_type)]}.

    Mirror of diff.py:69-150. With `deletions_only=True` the ranges are in
    OLD file line numbers; otherwise in NEW file line numbers.
    """
    result = {}
    current_file = None
    lines = diff_text.split("\n")

    for i, line in enumerate(lines):
        if line.startswith("+++"):
            match = re.match(r"^\+\+\+\s+b/(.+)", line)
            if match:
                current_file = match.group(1)
        elif line.startswith("@@") and current_file:
            match = _HUNK_RE.match(line)
            if match:
                old_start = int(match.group(1))
                new_start = int(match.group(3))

                if deletions_only:
                    j = i + 1
                    old_line = old_start
                    edit_start = None
                    while j < len(lines) and not lines[j].startswith(("@@", "diff ", "---")):
                        if lines[j].startswith("-") and not lines[j].startswith("---"):
                            if edit_start is None:
                                edit_start = old_line
                            old_line += 1
                        elif lines[j].startswith("+") and not lines[j].startswith("+++"):
                            pass
                        elif lines[j].startswith(" "):
                            if edit_start is not None:
                                result.setdefault(current_file, []).append(
                                    (edit_start, old_line - 1, "del")
                                )
                                edit_start = None
                            old_line += 1
                        j += 1
                    if edit_start is not None:
                        result.setdefault(current_file, []).append(
                            (edit_start, old_line - 1, "del")
                        )
                else:
                    j = i + 1
                    new_line = new_start
                    edit_start = None
                    while j < len(lines) and not lines[j].startswith(("@@", "diff ", "---")):
                        if lines[j].startswith("+") and not lines[j].startswith("+++"):
                            if edit_start is None:
                                edit_start = new_line
                            new_line += 1
                        elif lines[j].startswith("-") and not lines[j].startswith("---"):
                            pass
                        elif lines[j].startswith(" "):
                            if edit_start is not None:
                                result.setdefault(current_file, []).append(
                                    (edit_start, new_line - 1, "add")
                                )
                                edit_start = None
                            new_line += 1
                        j += 1
                    if edit_start is not None:
                        result.setdefault(current_file, []).append(
                            (edit_start, new_line - 1, "add")
                        )
    return result


def parse_diff_lines(
    diff_text: str, deletions_only: bool = False
) -> Dict[str, List[LineInterval]]:
    """Extract edited line ranges per file from a unified diff.

    Mirror of diff.py:30-50 (`parse_diff_lines`).
    """
    hunks = _parse_hunks(diff_text, deletions_only=deletions_only)
    result = {}
    for file_path, line_ranges in hunks.items():
        line_intervals = [(start, end) for start, end, _ in line_ranges]
        if line_intervals:
            result[file_path] = _merge_line_intervals(line_intervals)
    return result


def editloc_metrics(
    model_patch: str, gold_line_spans: Dict[str, List[LineInterval]]
) -> dict:
    """EditLoc recall/precision of deleted patch lines vs gold ranges.

    Mirror of evaluate.py:231-295: each predicted deleted line is expanded
    individually and counted as a hit when it falls inside a gold range for
    the same file. Recall and precision are both hits/pred_lines (upstream
    defines them identically); an empty prediction yields 0.0, and a
    missing patch yields an empty dict rather than fabricated numbers.
    """
    if not model_patch:
        return {}

    pred_line_edits = parse_diff_lines(model_patch, deletions_only=True)
    pred_line_count = 0
    hit_count = 0
    for file_path, intervals in pred_line_edits.items():
        gold_ranges = gold_line_spans.get(file_path, [])
        for start, end in intervals:
            for line_num in range(start, end + 1):
                pred_line_count += 1
                for gold_start, gold_end in gold_ranges:
                    if gold_start <= line_num <= gold_end:
                        hit_count += 1
                        break

    recall = hit_count / pred_line_count if pred_line_count > 0 else 0.0
    precision = hit_count / pred_line_count if pred_line_count > 0 else 0.0
    gold_line_count = sum(
        end - start + 1
        for intervals in gold_line_spans.values()
        for start, end in intervals
    )
    return {
        "recall": recall,
        "precision": precision,
        "intersection": hit_count,
        "gold_size": gold_line_count,
        "pred_size": pred_line_count,
    }
