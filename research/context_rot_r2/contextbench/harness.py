"""Matched A/B harness scaffold: (arm-config, task-subset) -> per-task records.

The runner evaluates recorded trajectories against gold contexts. It never
runs a model: producing trajectories is the live phase. Per-task records
carry:

- `final` file recall/precision and line-span recall/precision of the final
  retrieved context (upstream `compute_granularity_metrics` semantics);
- `trajectory` AUC-coverage and redundancy over the retrieval steps
  (upstream `compute_trajectory_metrics` semantics);
- `editloc` recall/precision when a patch exists (patch or gold patch
  fallback, upstream evaluate.py:231-295);
- `tool_calls` from the trajectory record;
- `patch_success`: always UNAVAILABLE offline (requires a live model run
  and the f2p test harness). Never fabricated.

Offline deviation from upstream `evaluate_instance`
(contextbench/evaluate.py:116-229), documented in the scorecard: predicted
paths are NOT filtered against a repository checkout (no network, no repo
cache), and symbol/byte-span granularities are not computed (they need
tree-sitter and checkout contents).

Statistics: `compare_arms` computes per-instance paired differences, an
exact two-sided sign test and a Wilcoxon signed-rank test (normal
approximation, zeros discarded, tie-corrected variance), matching the
design doc section 5 statistics plan.
"""

import math
from typing import Dict, List, Optional, Sequence

from . import PATCH_SUCCESS_UNAVAILABLE
from .arms import ArmConfig
from .editloc import editloc_metrics
from .gold import GoldContext
from .metrics import (
    aggregate_records,
    file_metrics,
    line_metrics,
    step_line_intervals,
    trajectory_metrics,
)
from .trajectory import to_steps


def _task_record_error(instance_id: str, arm: str, error: str) -> dict:
    return {"instance_id": instance_id, "arm": arm, "error": error}


def evaluate_task(arm: ArmConfig, gold: GoldContext, traj_record: dict) -> dict:
    """Evaluate one task's recorded trajectory against its gold context."""
    steps, final_step = to_steps(traj_record["traj_data"])

    gold_files = set(gold.files())
    gold_lines = gold.line_spans()

    final_files = set(final_step.files)
    final_lines = step_line_intervals(final_step)

    record = {
        "instance_id": gold.instance_id,
        "original_inst_id": gold.original_inst_id,
        "arm": arm.arm,
        "arm_name": arm.name,
        "final": {
            "file": file_metrics(final_files, gold_files),
            "line": line_metrics(final_lines, gold_lines),
        },
        "trajectory": trajectory_metrics(steps, gold_files, gold_lines),
        "tool_calls": int(traj_record.get("tool_calls", 0) or 0),
        "patch_success": {
            "status": PATCH_SUCCESS_UNAVAILABLE,
            "reason": "requires a live model run and the f2p test harness",
        },
    }

    # EditLoc: predicted patch, gold-patch fallback (evaluate.py:234-237).
    model_patch = traj_record.get("model_patch", "") or gold.patch or ""
    editloc = editloc_metrics(model_patch, gold_lines)
    if editloc:
        record["editloc"] = editloc

    if not final_files and not final_lines:
        record["warning"] = "no_context_extracted"
    return record


def run_arm(
    arm: ArmConfig,
    tasks: Sequence[GoldContext],
    trajectories: Dict[str, dict],
) -> List[dict]:
    """Run one arm over a task subset.

    `trajectories` maps instance_id -> unified-format record. Placeholder
    arms produce blocked records without metric numbers; missing
    trajectories produce error records. The subset order is the task order,
    so arms over the same subset stay paired.
    """
    if not arm.runnable:
        return [
            {
                "instance_id": gold.instance_id,
                "arm": arm.arm,
                "arm_name": arm.name,
                "status": "PLACEHOLDER",
                "blocked_reason": arm.blocked_reason,
            }
            for gold in tasks
        ]

    records = []
    for gold in tasks:
        traj = trajectories.get(gold.instance_id)
        if traj is None:
            records.append(
                _task_record_error(gold.instance_id, arm.arm, "missing_trajectory")
            )
            continue
        records.append(evaluate_task(arm, gold, traj))
    return records


def summarize_arm(records: Sequence[dict]) -> dict:
    """Aggregate one arm's records (upstream aggregate_results semantics)."""
    return aggregate_records(records)


def _metric_value(record: dict, metric_path: Sequence[str]) -> Optional[float]:
    """Extract a numeric metric from a task record, None if unavailable."""
    if "error" in record or "status" in record:
        return None
    node = record
    for key in metric_path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if isinstance(node, (int, float)):
        return float(node)
    return None


def _sign_test_pvalue(positives: int, n: int) -> float:
    """Exact two-sided sign-test p-value: 2 * P[X >= max(p, n-p)] for
    X ~ Binomial(n, 1/2), capped at 1.0."""
    if n <= 0:
        return 1.0
    k = max(positives, n - positives)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def _wilcoxon_normal(deltas: List[float]) -> dict:
    """Wilcoxon signed-rank statistic and normal-approximation p-value.

    Zeros are discarded; ties get average ranks; the variance carries the
    tie correction sum(t^3 - t). Exact tables are not enumerated; at the
    design-doc n=20 the approximation is advisory, the sign test is exact.
    """
    nonzero = [d for d in deltas if d != 0.0]
    n = len(nonzero)
    if n == 0:
        return {"w": 0.0, "n": 0, "p_value": 1.0}
    ordered = sorted(nonzero, key=abs)
    ranks = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(ordered[j + 1]) == abs(ordered[i]):
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        ranks.extend([avg_rank] * (j - i + 1))
        i = j + 1
    w_plus = sum(r for r, d in zip(ranks, ordered) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, ordered) if d < 0)
    w = min(w_plus, w_minus)
    tie_sizes = {}
    for d in ordered:
        tie_sizes[abs(d)] = tie_sizes.get(abs(d), 0) + 1
    tie_term = sum(t ** 3 - t for t in tie_sizes.values())
    mean_w = n * (n + 1) / 4.0
    var_w = n * (n + 1) * (2 * n + 1) / 24.0 - tie_term / 48.0
    if var_w <= 0:
        return {"w": w, "n": n, "p_value": 1.0}
    z = (w - mean_w) / math.sqrt(var_w)
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))
    return {"w": w, "n": n, "p_value": min(1.0, max(0.0, p))}


def compare_arms(
    records_a: Sequence[dict],
    records_b: Sequence[dict],
    metric_path: Sequence[str] = ("final", "file", "coverage"),
) -> dict:
    """Paired comparison of two arms over the same task subset.

    Records are paired by instance_id in subset order. For each pair with
    both metrics present, the delta is b - a. Returns the paired deltas,
    descriptive statistics, an exact two-sided sign test and a Wilcoxon
    signed-rank normal approximation.
    """
    a_by_id = {r.get("instance_id"): r for r in records_a}
    pairs = []
    for rb in records_b:
        ra = a_by_id.get(rb.get("instance_id"))
        if ra is None:
            continue
        va = _metric_value(ra, metric_path)
        vb = _metric_value(rb, metric_path)
        if va is None or vb is None:
            continue
        pairs.append((rb["instance_id"], va, vb))

    deltas = [vb - va for _, va, vb in pairs]
    positives = sum(1 for d in deltas if d > 0)
    negatives = sum(1 for d in deltas if d < 0)
    zeros = len(deltas) - positives - negatives
    sorted_deltas = sorted(deltas)
    median = (
        sorted_deltas[len(sorted_deltas) // 2]
        if len(deltas) % 2 == 1
        else (
            (sorted_deltas[len(sorted_deltas) // 2 - 1]
            + sorted_deltas[len(sorted_deltas) // 2])
            / 2.0
        )
        if deltas
        else 0.0
    )
    return {
        "metric_path": list(metric_path),
        "n_pairs": len(pairs),
        "pairs": [
            {"instance_id": iid, "a": va, "b": vb, "delta": vb - va}
            for iid, va, vb in pairs
        ],
        "mean_delta": (sum(deltas) / len(deltas)) if deltas else 0.0,
        "median_delta": median,
        "positives": positives,
        "negatives": negatives,
        "zeros": zeros,
        "sign_test_pvalue": _sign_test_pvalue(positives, positives + negatives),
        "wilcoxon": _wilcoxon_normal(deltas),
    }


def write_run_summary(
    arm: ArmConfig,
    records: Sequence[dict],
    task_ids: Sequence[str],
    trajectory_source: str,
    path: str,
) -> dict:
    """Persist a run manifest for one arm (arm, tasks, source, aggregate)."""
    import json
    import os

    summary = {
        "arm": arm.arm,
        "arm_name": arm.name,
        "arm_status": arm.status,
        "task_ids": list(task_ids),
        "trajectory_source": trajectory_source,
        "aggregate": summarize_arm(list(records)),
        "records": list(records),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    return summary
