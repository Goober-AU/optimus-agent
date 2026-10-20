"""Gold-context metrics: precision/recall of retrieved files and spans.

All formulas mirror ContextBench `contextbench/metrics/compute.py`
(commit 1436c28a) unless noted otherwise:

- `coverage_precision` <- compute.py:7-13 (recall-style `coverage` =
  inter/gold with gold==0 -> 1.0; `precision` = inter/pred with pred==0 ->
  1.0);
- line-interval arithmetic <- `_merge_line_intervals` (compute.py:27-39),
  `_line_interval_length` (41-44), `_line_interval_intersect` (45-61),
  `_line_interval_intersect_size` (63-66), `line_total_lines` (67-69),
  `line_intersection_lines` (71-79);
- granularity metrics <- `compute_granularity_metrics` (compute.py:92-158):
  the file, span and line branches. The symbol branch is NOT mirrored
  because symbol sets require tree-sitter extraction over a checked-out
  repository (see extractors/treesitter.py:1-80); it is BLOCKED offline;
- trajectory metrics <- `compute_trajectory_metrics` (compute.py:159-249):
  running-union per-step coverage, AUC-coverage as the step mean, and
  redundancy = 1 - union_size / sum_of_step_sizes, at the file and line
  granularities (and span granularity when byte spans are supplied);
- aggregation <- `aggregate_results` (contextbench/evaluate.py:533-578):
  micro-average for final metrics, macro-average for trajectory metrics.

Granularity naming follows upstream: `file` (whole files), `span` (bytes),
`line` (lines). Offline span metrics need byte intervals; those are derived
from line spans plus file contents (`line_spans_to_bytes`), which only exist
in the live phase with a pinned checkout.
"""

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .intervals import (
    ByteInterval,
    line_to_byte,
    merge_intervals,
    span_intersection_bytes,
    span_total_bytes,
)

LineInterval = Tuple[int, int]  # (start_line, end_line) inclusive


def coverage_precision(
    pred_size: float, gold_size: float, inter_size: float
) -> Tuple[float, float]:
    """Compute (coverage, precision).

    Mirror of compute.py:7-13. Edge cases: gold size 0 -> coverage 1.0,
    pred size 0 -> precision 1.0.
    """
    cov = inter_size / gold_size if gold_size > 0 else 1.0
    prec = inter_size / pred_size if pred_size > 0 else 1.0
    return cov, prec


def merge_line_intervals(intervals: List[LineInterval]) -> List[LineInterval]:
    """Merge overlapping or adjacent line intervals.

    Mirror of compute.py:27-39 (`_merge_line_intervals`).
    """
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


def line_interval_length(intervals: List[LineInterval]) -> int:
    """Total lines covered by the intervals (after merging).

    Mirror of compute.py:41-44 (`_line_interval_length`).
    """
    return sum(end - start + 1 for start, end in merge_line_intervals(intervals))


def line_interval_intersect(
    a: List[LineInterval], b: List[LineInterval]
) -> List[LineInterval]:
    """Intersection of two line interval lists.

    Mirror of compute.py:45-61 (`_line_interval_intersect`).
    """
    a_m, b_m = merge_line_intervals(a), merge_line_intervals(b)
    result = []
    i, j = 0, 0
    while i < len(a_m) and j < len(b_m):
        overlap = (max(a_m[i][0], b_m[j][0]), min(a_m[i][1], b_m[j][1]))
        if overlap[0] <= overlap[1]:
            result.append(overlap)
        if a_m[i][1] < b_m[j][1]:
            i += 1
        elif b_m[j][1] < a_m[i][1]:
            j += 1
        else:
            i += 1
            j += 1
    return result


def line_interval_intersect_size(
    a: List[LineInterval], b: List[LineInterval]
) -> int:
    """Lines in the intersection of two interval lists.

    Mirror of compute.py:63-66 (`_line_interval_intersect_size`).
    """
    return line_interval_length(line_interval_intersect(a, b))


def line_total_lines(lines_by_file: Dict[str, List[LineInterval]]) -> int:
    """Total lines across all files.

    Mirror of compute.py:67-69 (`line_total_lines`).
    """
    return sum(
        line_interval_length(intervals) for intervals in lines_by_file.values()
    )


def line_intersection_lines(
    a: Dict[str, List[LineInterval]], b: Dict[str, List[LineInterval]]
) -> int:
    """Total intersection lines across all files.

    Mirror of compute.py:71-79 (`line_intersection_lines`).
    """
    total = 0
    for f in set(a.keys()) | set(b.keys()):
        total += line_interval_intersect_size(a.get(f, []), b.get(f, []))
    return total


def file_metrics(pred_files: Set[str], gold_files: Set[str]) -> dict:
    """File-level coverage (recall) and precision.

    Mirror of the file branch of `compute_granularity_metrics`
    (compute.py:103-105).
    """
    file_inter = len(pred_files & gold_files)
    cov, prec = coverage_precision(len(pred_files), len(gold_files), file_inter)
    return {
        "coverage": cov,
        "precision": prec,
        "intersection": file_inter,
        "gold_size": len(gold_files),
        "pred_size": len(pred_files),
    }


def line_metrics(
    pred_lines: Dict[str, List[LineInterval]],
    gold_lines: Dict[str, List[LineInterval]],
) -> dict:
    """Line-level coverage (span recall) and precision.

    Mirror of the line branch of `compute_granularity_metrics`
    (compute.py:131-140). Upstream returns 0.0 for both when either side is
    missing; here the caller passes empty dicts and the edge cases in
    `coverage_precision` apply (gold 0 -> coverage 1.0, pred 0 ->
    precision 1.0), matching the upstream final-metric behaviour.
    """
    line_pred = line_total_lines(pred_lines)
    line_gold = line_total_lines(gold_lines)
    line_inter = line_intersection_lines(pred_lines, gold_lines)
    cov, prec = coverage_precision(line_pred, line_gold, line_inter)
    return {
        "coverage": cov,
        "precision": prec,
        "intersection": line_inter,
        "gold_size": line_gold,
        "pred_size": line_pred,
    }


def span_metrics(
    pred_spans: Dict[str, List[ByteInterval]],
    gold_spans: Dict[str, List[ByteInterval]],
) -> dict:
    """Byte-span coverage and precision.

    Mirror of the span branch of `compute_granularity_metrics`
    (compute.py:125-130).
    """
    span_pred = span_total_bytes(pred_spans)
    span_gold = span_total_bytes(gold_spans)
    span_inter = span_intersection_bytes(pred_spans, gold_spans)
    cov, prec = coverage_precision(span_pred, span_gold, span_inter)
    return {
        "coverage": cov,
        "precision": prec,
        "intersection": span_inter,
        "gold_size": span_gold,
        "pred_size": span_pred,
    }


def line_spans_to_bytes(
    line_spans: Dict[str, List[LineInterval]],
    file_contents: Dict[str, bytes],
) -> Dict[str, List[ByteInterval]]:
    """Convert line spans to byte spans given file contents.

    Content-based mirror of `Gold.byte_spans` (gold.py:46-64) and
    `_step_to_byte_spans` (compute.py:252-271): each inclusive line range is
    converted with the line-offset table, then merged per file. Files whose
    content is missing or whose start line is past EOF are dropped, matching
    the upstream None-span handling.
    """
    result: Dict[str, List[ByteInterval]] = {}
    for file_path, intervals in line_spans.items():
        content = file_contents.get(file_path)
        if content is None:
            continue
        byte_intervals = []
        for start_line, end_line in intervals:
            byte_span = line_to_byte(content, start_line, end_line)
            if byte_span:
                byte_intervals.append(byte_span)
        if byte_intervals:
            result[file_path] = merge_intervals(byte_intervals)
    return result


def granularity_metrics(
    pred_files: Set[str],
    gold_files: Set[str],
    pred_lines: Dict[str, List[LineInterval]],
    gold_lines: Dict[str, List[LineInterval]],
    pred_spans: Optional[Dict[str, List[ByteInterval]]] = None,
    gold_spans: Optional[Dict[str, List[ByteInterval]]] = None,
) -> dict:
    """Metrics at the file, line and (optionally) span granularities.

    Shape mirror of `compute_granularity_metrics` (compute.py:92-158) minus
    the symbol granularity, which requires tree-sitter over a checkout and
    is BLOCKED offline. Span metrics are included only when byte spans are
    supplied (live phase).
    """
    out = {
        "file": file_metrics(pred_files, gold_files),
        "line": line_metrics(pred_lines, gold_lines),
    }
    if pred_spans is not None and gold_spans is not None:
        out["span"] = span_metrics(pred_spans, gold_spans)
    return out


class TrajStep:
    """One retrieval step. Shape mirror of `parsers/trajectory.py::Step`
    (trajectory.py:8-14) with spans normalized to line intervals up front.
    """

    def __init__(self, files=None, spans=None, symbols=None):
        self.files = list(files or [])
        # spans: list of {"file", "start_line", "end_line"} dicts
        self.spans = spans or []
        self.symbols = symbols or {}


def step_line_intervals(step: TrajStep) -> Dict[str, List[LineInterval]]:
    """Convert a step's spans to merged line intervals per file.

    Mirror of `_step_to_line_intervals` (compute.py:141-157).
    """
    result: Dict[str, List[LineInterval]] = {}
    for span in step.spans:
        f = span.get("file")
        if not f:
            continue
        start_line = span.get("start_line", 1)
        end_line = span.get("end_line", 1)
        if start_line > 0 and end_line > 0:
            result.setdefault(f, []).append((start_line, end_line))
    for f in result:
        result[f] = merge_line_intervals(result[f])
    return result


def trajectory_metrics(
    steps: Sequence[TrajStep],
    gold_files: Set[str],
    gold_lines: Dict[str, List[LineInterval]],
) -> dict:
    """AUC-coverage and redundancy at the file and line granularities.

    Mirror of `compute_trajectory_metrics` (compute.py:159-249) restricted
    to the offline granularities: per step, the running union of files and
    line spans updates a union view, coverage is recomputed against gold,
    AUC is the mean over steps, and redundancy is
    `1 - union_size / sum_of_step_sizes` (0.0 when the sum is 0). Symbol
    granularity and byte spans need a checkout and are omitted; the empty-
    steps result mirrors the upstream T == 0 branch (all zeros).
    """
    T = len(steps)
    if T == 0:
        return {
            "steps": [],
            "auc_coverage": {"file": 0.0, "line": 0.0},
            "redundancy": {"file": 0.0, "line": 0.0},
        }

    union_files: Set[str] = set()
    union_lines: Dict[str, List[LineInterval]] = {}
    sum_files = 0
    sum_lines = 0

    per_step_metrics = []
    for t, step in enumerate(steps):
        step_files = set(step.files)
        step_lines = step_line_intervals(step)

        union_files |= step_files
        for f, ivs in step_lines.items():
            union_lines[f] = merge_line_intervals(union_lines.get(f, []) + ivs)

        file_cov = (
            len(union_files & gold_files) / len(gold_files) if gold_files else 1.0
        )
        if gold_lines:
            line_inter = line_intersection_lines(union_lines, gold_lines)
            line_gold = line_total_lines(gold_lines)
            line_cov = line_inter / line_gold if line_gold > 0 else 1.0
        else:
            line_cov = 0.0

        per_step_metrics.append(
            {"step": t + 1, "coverage": {"file": file_cov, "line": line_cov}}
        )

        sum_files += len(step_files)
        sum_lines += line_total_lines(step_lines)

    auc_file = sum(s["coverage"]["file"] for s in per_step_metrics) / T
    auc_line = sum(s["coverage"]["line"] for s in per_step_metrics) / T
    red_file = 1 - len(union_files) / sum_files if sum_files > 0 else 0.0
    red_line = 1 - line_total_lines(union_lines) / sum_lines if sum_lines > 0 else 0.0

    return {
        "steps": per_step_metrics,
        "auc_coverage": {"file": auc_file, "line": auc_line},
        "redundancy": {"file": red_file, "line": red_line},
    }


def aggregate_records(records: Iterable[dict]) -> dict:
    """Micro/macro aggregation over per-task metric records.

    Mirror of `aggregate_results` (evaluate.py:533-578): final file/line
    metrics micro-average over intersection/gold_size/pred_size sums;
    trajectory AUC and redundancy macro-average (plain means). Records
    carrying an "error" key are excluded, as upstream excludes error rows.
    """
    records = list(records)
    valid = [r for r in records if "error" not in r]
    agg = {"num_valid": len(valid), "num_total": len(records)}
    if not valid:
        return agg

    for gran in ("file", "line"):
        key = f"final_{gran}"
        if any(gran in r.get("final", {}) for r in valid):
            intersection = sum(
                r.get("final", {}).get(gran, {}).get("intersection", 0) for r in valid
            )
            gold_size = sum(
                r.get("final", {}).get(gran, {}).get("gold_size", 0) for r in valid
            )
            pred_size = sum(
                r.get("final", {}).get(gran, {}).get("pred_size", 0) for r in valid
            )
            cov, prec = coverage_precision(pred_size, gold_size, intersection)
            agg[key] = {"coverage": cov, "precision": prec}

    for gran in ("file", "line"):
        auc_key = f"traj_auc_{gran}"
        red_key = f"traj_redundancy_{gran}"
        if any(gran in r.get("trajectory", {}).get("auc_coverage", {}) for r in valid):
            auc_vals = [
                r.get("trajectory", {}).get("auc_coverage", {}).get(gran, 0.0)
                for r in valid
            ]
            red_vals = [
                r.get("trajectory", {}).get("redundancy", {}).get(gran, 0.0)
                for r in valid
            ]
            if auc_vals:
                agg[auc_key] = sum(auc_vals) / len(auc_vals)
                agg[red_key] = sum(red_vals) / len(red_vals)

    if any("editloc" in r for r in valid):
        intersection = sum(r.get("editloc", {}).get("intersection", 0) for r in valid)
        gold_size = sum(r.get("editloc", {}).get("gold_size", 0) for r in valid)
        pred_size = sum(r.get("editloc", {}).get("pred_size", 0) for r in valid)
        recall, prec = coverage_precision(pred_size, gold_size, intersection)
        agg["editloc"] = {"recall": recall, "precision": prec}

    return agg
