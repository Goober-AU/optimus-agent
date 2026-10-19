"""Markdown and CSV reporting.

All numbers come from result records; nothing is invented. When a run used a
synthetic adapter the report says so in the header and in the totals section.
"""

import csv
import json
import os
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Sequence

from .corpus import TOKEN_ESTIMATOR_NOTE
from .scoring import paired_bootstrap_ci, paired_deltas

CSV_COLUMNS = (
    "suite", "item_id", "instance_id", "category", "seed", "model", "provider",
    "adapter_kind", "context_target_len", "evidence_position", "distractor_kind",
    "requested_json_style", "prompt_tokens", "prompt_tokens_source",
    "usage_prompt_tokens", "usage_completion_tokens", "latency_ms",
    "extraction_rule", "correct", "invalid_reason", "raw_answer",
    "normalized_answer", "gold_fingerprint", "truncated", "tokens_original_est",
    "tokens_final_est", "evidence_survived", "abstained", "unsupported_claim",
    "adapter_synthetic",
)


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _scored(records: Sequence[dict]) -> List[dict]:
    return [r for r in records if r.get("correct") is not None]


def _rate(part: int, whole: int) -> Optional[float]:
    return (part / whole) if whole else None


def accuracy(records: Sequence[dict]) -> Optional[float]:
    scored = _scored(records)
    return _rate(sum(1 for r in scored if r.get("correct")), len(scored))


def bucket_by_length(records: Sequence[dict], key: str = "context_target_len") -> List[dict]:
    """Accuracy per context-length bucket, ascending."""
    buckets: "OrderedDict[int, List[dict]]" = OrderedDict()
    for record in records:
        buckets.setdefault(int(record.get(key) or 0), []).append(record)
    rows = []
    for length in sorted(buckets):
        group = buckets[length]
        scored = _scored(group)
        tokens = [r.get("prompt_tokens") for r in group if r.get("prompt_tokens")]
        rows.append({
            "context_target_len": length,
            "cells": len(group),
            "valid": len(scored),
            "correct": sum(1 for r in scored if r.get("correct")),
            "accuracy": _rate(sum(1 for r in scored if r.get("correct")), len(scored)),
            "invalid": sum(1 for r in group if r.get("correct") is None),
            "abstained": sum(1 for r in group if r.get("abstained")),
            "unsupported_claim": sum(1 for r in group if r.get("unsupported_claim")),
            "median_prompt_tokens_est": _median(tokens),
        })
    return rows


def _median(values: Sequence[float]) -> Optional[float]:
    items = sorted(float(v) for v in values if v is not None)
    if not items:
        return None
    mid = len(items) // 2
    if len(items) % 2:
        return items[mid]
    return (items[mid - 1] + items[mid]) / 2.0


def degradation_vs_baseline(records: Sequence[dict],
                            baseline_len: Optional[int] = None,
                            iters: int = 2000, seed: int = 1234) -> dict:
    """Paired accuracy delta of every length bucket against the short baseline.

    Pairs are formed by ``item_id`` only, so the comparison is within item.
    """
    lengths = sorted({int(r.get("context_target_len") or 0) for r in records})
    if not lengths:
        return {"baseline_len": None, "rows": []}
    baseline = int(baseline_len) if baseline_len is not None else lengths[0]
    base_records = [r for r in records if int(r.get("context_target_len") or 0) == baseline]
    rows = []
    for length in lengths:
        group = [r for r in records if int(r.get("context_target_len") or 0) == length]
        deltas = paired_deltas(base_records, group)
        ci = paired_bootstrap_ci(deltas, iters=iters, seed=seed)
        rows.append({
            "context_target_len": length,
            "n_pairs": ci.get("n", 0),
            "delta_accuracy": ci.get("mean"),
            "ci_lo": ci.get("lo"),
            "ci_hi": ci.get("hi"),
            "accuracy": accuracy(group),
        })
    return {"baseline_len": baseline, "rows": rows, "ci": {"iters": iters, "seed": seed}}


def accuracy_by(records: Sequence[dict], key: str) -> List[dict]:
    groups: "OrderedDict[str, List[dict]]" = OrderedDict()
    for record in records:
        groups.setdefault(str(record.get(key)), []).append(record)
    rows = []
    for label, group in groups.items():
        scored = _scored(group)
        rows.append({
            "label": label,
            "cells": len(group),
            "valid": len(scored),
            "accuracy": _rate(sum(1 for r in scored if r.get("correct")), len(scored)),
            "invalid": sum(1 for r in group if r.get("correct") is None),
        })
    rows.sort(key=lambda row: str(row["label"]))
    return rows


def counts(records: Sequence[dict], key: str) -> List[dict]:
    counter = Counter(str(r.get(key)) for r in records)
    return [{"label": label, "count": count} for label, count in counter.most_common()]


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _fmt(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return ("%." + str(digits) + "f") % value
    return str(value)


def render_markdown(records: Sequence[dict], manifest: dict) -> str:
    """Render the summary markdown for one run."""
    synthetic = any(bool(r.get("adapter_synthetic")) for r in records)
    lines: List[str] = []
    lines.append("# Context-rot run %s" % manifest.get("run_id", "unknown"))
    lines.append("")
    if synthetic:
        lines.append("> OFFLINE / SYNTHETIC ADAPTER: these numbers validate the harness plumbing.")
        lines.append("> They do not measure a real model.")
        lines.append("")
    lines.append("## Manifest")
    lines.append("")
    for key in ("suite", "seed", "model", "provider", "adapter_kind", "git_head",
                "git_branch", "python_version", "started_at", "finished_at", "status",
                "harness_version", "slug", "concurrency", "requested_json_style",
                "max_prompt_tokens", "truncate_strategy", "compactor"):
        if key in manifest:
            lines.append("- %s: `%s`" % (key, manifest.get(key)))
    lines.append("")

    scored = _scored(records)
    correct = sum(1 for r in scored if r.get("correct"))
    invalid = sum(1 for r in records if r.get("correct") is None)
    lines.append("## Totals")
    lines.append("")
    lines.append("- cells: %d" % len(records))
    lines.append("- scored: %d" % len(scored))
    lines.append("- correct: %d" % correct)
    lines.append("- accuracy (valid cells only): %s" % _fmt(accuracy(records)))
    lines.append("- invalid cells (never scored wrong): %d" % invalid)
    lines.append("- abstentions: %d" % sum(1 for r in records if r.get("abstained")))
    lines.append("- unsupported claims: %d" % sum(1 for r in records if r.get("unsupported_claim")))
    lines.append("- truncated contexts: %d" % sum(1 for r in records if r.get("truncated")))
    lines.append("")

    lines.append("## Accuracy vs context length")
    lines.append("")
    lines.append("| context_target_len (est tokens) | cells | valid | accuracy | invalid "
                 "| abstained | unsupported | median prompt tokens (est) |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in bucket_by_length(records):
        lines.append("| %d | %d | %d | %s | %d | %d | %d | %s |" % (
            row["context_target_len"], row["cells"], row["valid"], _fmt(row["accuracy"]),
            row["invalid"], row["abstained"], row["unsupported_claim"],
            _fmt(row["median_prompt_tokens_est"], 1)))
    lines.append("")

    degradation = degradation_vs_baseline(records)
    lines.append("## Degradation vs short-context baseline (len=%s)"
                 % degradation.get("baseline_len"))
    lines.append("")
    lines.append("| context_target_len | n pairs | accuracy | delta vs baseline | 95% CI |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in degradation.get("rows", []):
        ci = "n/a"
        if row["ci_lo"] is not None and row["ci_hi"] is not None:
            ci = "[%s, %s]" % (_fmt(row["ci_lo"]), _fmt(row["ci_hi"]))
        lines.append("| %d | %d | %s | %s | %s |" % (
            row["context_target_len"], row["n_pairs"], _fmt(row["accuracy"]),
            _fmt(row["delta_accuracy"]), ci))
    lines.append("")
    lines.append("Paired by item_id; percentile bootstrap, %s iterations, seed %s."
                 % (degradation.get("ci", {}).get("iters"),
                    degradation.get("ci", {}).get("seed")))
    lines.append("")

    lines.append("## Accuracy by evidence position")
    lines.append("")
    lines.append("| position | cells | valid | accuracy | invalid |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in accuracy_by(records, "evidence_position"):
        lines.append("| %s | %d | %d | %s | %d |" % (
            row["label"], row["cells"], row["valid"], _fmt(row["accuracy"]), row["invalid"]))
    lines.append("")

    lines.append("## Accuracy by distractor kind")
    lines.append("")
    lines.append("| distractor_kind | cells | valid | accuracy | invalid |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in accuracy_by(records, "distractor_kind"):
        lines.append("| %s | %d | %d | %s | %d |" % (
            row["label"], row["cells"], row["valid"], _fmt(row["accuracy"]), row["invalid"]))
    lines.append("")

    categories = accuracy_by(records, "category")
    if len(categories) > 1:
        lines.append("## Accuracy by item category")
        lines.append("")
        lines.append("| category | cells | valid | accuracy | invalid |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in categories:
            lines.append("| %s | %d | %d | %s | %d |" % (
                row["label"], row["cells"], row["valid"], _fmt(row["accuracy"]), row["invalid"]))
        lines.append("")

    lines.append("## Extraction rules fired")
    lines.append("")
    lines.append("| rule | count |")
    lines.append("| --- | --- |")
    for row in counts(records, "extraction_rule"):
        lines.append("| %s | %d |" % (row["label"], row["count"]))
    lines.append("")

    invalid_rows = [r for r in counts(records, "invalid_reason") if r["label"] != "None"]
    lines.append("## Invalid reasons")
    lines.append("")
    if invalid_rows:
        lines.append("| reason | count |")
        lines.append("| --- | --- |")
        for row in invalid_rows:
            lines.append("| %s | %d |" % (row["label"], row["count"]))
    else:
        lines.append("none")
    lines.append("")

    truncated = [r for r in records if r.get("truncated")]
    lines.append("## Truncation accounting")
    lines.append("")
    if truncated:
        survived = sum(1 for r in truncated if r.get("evidence_survived"))
        lines.append("- truncated cells: %d" % len(truncated))
        lines.append("- evidence survived truncation: %d" % survived)
        lines.append("- evidence lost (scored INVALID, never wrong): %d" % (len(truncated) - survived))
        lines.append("")
        lines.append("| item | target len | tokens before | tokens after | evidence survived | invalid_reason |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for record in truncated[:20]:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                record.get("item_id"), record.get("context_target_len"),
                record.get("tokens_original_est"), record.get("tokens_final_est"),
                record.get("evidence_survived"), record.get("invalid_reason")))
    else:
        lines.append("no truncated contexts in this run")
    lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append("- " + TOKEN_ESTIMATOR_NOTE)
    lines.append("- correct=null marks an INVALID cell (truncated evidence, adapter error, "
                 "build error); invalid cells are excluded from accuracy and never counted wrong.")
    lines.append("- Accuracy counts only cells where the answer was scored.")
    if synthetic:
        lines.append("- The adapter was synthetic (fake/echo). Treat accuracy as a plumbing check.")
    lines.append("")
    return "\n".join(lines)


def _csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return str(value)


def write_csv(path: str, records: Sequence[dict]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for record in records:
            row = []
            for column in CSV_COLUMNS:
                if column.startswith("usage_"):
                    usage = record.get("usage") or {}
                    row.append(_csv_value(usage.get(column[len("usage_"):])))
                else:
                    row.append(_csv_value(record.get(column)))
            writer.writerow(row)
    return path


def write_summary(path: str, records: Sequence[dict], manifest: dict) -> str:
    text = render_markdown(records, manifest)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
    return path
