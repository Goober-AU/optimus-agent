#!/usr/bin/env python3
"""Compare OCC benchmark results across arms.

Reads one or more results.json files written by bench_occ3.py, prints an
arm-by-metric comparison table (medians) to stdout, and writes a markdown
report (results.md by default) containing the same table plus percentile
detail for the latency and receiver-prefill metrics.

When a results file keeps raw trial records, statistics are recomputed
from them (files are merged per arm); otherwise the stored aggregate is
used. No emojis are used anywhere in the output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from bench_occ3 import NUMERIC_METRICS, percentile
except ImportError as exc:  # pragma: no cover
    print(f"ERROR: analyze.py must run next to bench_occ3.py ({exc})", file=sys.stderr)
    raise SystemExit(2)

ARM_ORDER = {"text": 0, "occ1": 1, "occ2": 2, "occ3": 3}

# (metric key, column header, formatting kind)
COLUMNS: list[tuple[str, str, str]] = [
    ("trials", "trials", "int"),
    ("wire_chars", "wire chars", "float"),
    ("encode_ms", "encode ms", "float"),
    ("send_ms", "send ms", "float"),
    ("recv_ms", "recv ms", "float"),
    ("e2e_ms", "e2e ms", "float"),
    ("receiver_prefill_tokens", "recv prefill tok", "float"),
    ("cached_tokens", "cached tok", "float"),
    ("carrier_tokens", "carrier tok", "float"),
    ("completion_tokens", "completion tok", "float"),
    ("success_rate", "success %", "pct"),
    ("hit_rate", "hit %", "pct"),
    ("cache_miss_rate", "miss %", "pct"),
]


def load_documents(paths: Sequence[str]) -> list[dict[str, Any]]:
    """Load and validate results documents; exits with status 2 on bad input."""
    documents: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(document, dict):
            print(f"ERROR: {path} is not a results object", file=sys.stderr)
            raise SystemExit(2)
        document["_source"] = str(path)
        documents.append(document)
    return documents


def stats_from_records(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Recompute one arm's statistics from raw trial records."""
    flat: dict[str, Any] = {"trials": len(records)}
    metrics: dict[str, dict[str, float]] = {}
    for metric in NUMERIC_METRICS:
        values: list[float] = []
        for record in records:
            value = record.get(metric)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
        if values:
            entry = {
                "p10": percentile(values, 10),
                "median": percentile(values, 50),
                "p90": percentile(values, 90),
                "count": len(values),
            }
            metrics[metric] = entry
            flat[metric] = entry["median"]
    if records:
        flat["success_rate"] = sum(1 for r in records if r.get("success") is True) / len(records)
        occ3_records = [r for r in records if r.get("arm") == "occ3"]
        if occ3_records:
            hits = sum(1 for r in occ3_records if r.get("hit") is True)
            flat["hit_rate"] = hits / len(occ3_records)
            flat["cache_miss_rate"] = 1.0 - flat["hit_rate"]
    return {"flat": flat, "metrics": metrics}


def stored_aggregate_row(aggregate: dict[str, Any]) -> dict[str, Any]:
    """Build one arm's row from a stored aggregate (when trials were not kept)."""
    flat: dict[str, Any] = {"trials": aggregate.get("n_trials")}
    metrics: dict[str, dict[str, float]] = {}
    for key, value in aggregate.items():
        if isinstance(value, dict) and isinstance(value.get("median"), (int, float)):
            median = float(value["median"])
            metrics[key] = {
                "p10": float(value.get("p10", median)),
                "median": median,
                "p90": float(value.get("p90", median)),
                "count": int(value.get("count", 0)),
            }
            flat[key] = median
    for rate in ("success_rate", "hit_rate", "cache_miss_rate"):
        if isinstance(aggregate.get(rate), (int, float)):
            flat[rate] = float(aggregate[rate])
    return {"flat": flat, "metrics": metrics}


def collect(documents: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge results documents into one row per arm."""
    records_by_arm: dict[str, list[dict[str, Any]]] = {}
    stored_by_arm: dict[str, dict[str, Any]] = {}
    for document in documents:
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        records = [r for r in (document.get("trials") or []) if isinstance(r, dict)]
        if records:
            default_arm = str(meta.get("arm") or "unknown")
            for record in records:
                arm = str(record.get("arm") or default_arm)
                records_by_arm.setdefault(arm, []).append(record)
        else:
            aggregate = document.get("aggregate")
            arm = str(meta.get("arm") or "unknown")
            if isinstance(aggregate, dict) and aggregate and arm not in stored_by_arm:
                stored_by_arm[arm] = aggregate
    rows: dict[str, dict[str, Any]] = {}
    for arm, records in records_by_arm.items():
        rows[arm] = stats_from_records(records)
    for arm, aggregate in stored_by_arm.items():
        if arm not in rows:
            rows[arm] = stored_aggregate_row(aggregate)
    return rows


def _sorted_arms(rows: dict[str, dict[str, Any]]) -> list[str]:
    """Canonical arm order: text, occ1, occ2, occ3, then anything else by name."""
    return sorted(rows, key=lambda arm: (ARM_ORDER.get(arm, len(ARM_ORDER)), arm))


def fmt_value(kind: str, value: Any) -> str:
    """Format one table cell; '-' for missing values."""
    if value is None:
        return "-"
    if kind == "int":
        return f"{int(value)}"
    if kind == "pct":
        return f"{100.0 * float(value):.1f}"
    number = float(value)
    if abs(number) >= 1000.0:
        return f"{number:.0f}"
    if abs(number) >= 100.0:
        return f"{number:.1f}"
    return f"{number:.2f}"


def print_table(rows: dict[str, dict[str, Any]]) -> None:
    """Print the arm-by-metric medians table to stdout."""
    headers = ["arm"] + [header for _, header, _ in COLUMNS]
    table = [headers]
    for arm in _sorted_arms(rows):
        flat = rows[arm]["flat"]
        row = [arm]
        for key, _, kind in COLUMNS:
            row.append(fmt_value(kind, flat.get(key)))
        table.append(row)
    widths = [max(len(row[column]) for row in table) for column in range(len(headers))]
    for index, row in enumerate(table):
        print("  ".join(cell.ljust(widths[column]) for column, cell in enumerate(row)).rstrip())
        if index == 0:
            print("  ".join("-" * widths[column] for column in range(len(headers))))


def markdown_report(rows: dict[str, dict[str, Any]], documents: Sequence[dict[str, Any]]) -> str:
    """Render the markdown comparison report (no emojis)."""
    lines: list[str] = []
    lines.append("# OCC Communication Benchmark Results")
    lines.append("")
    lines.append("Medians per arm; rates are percentages; '-' means not measured.")
    lines.append("")
    lines.append("| arm | " + " | ".join(header for _, header, _ in COLUMNS) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in COLUMNS) + " |")
    for arm in _sorted_arms(rows):
        flat = rows[arm]["flat"]
        cells = [fmt_value(kind, flat.get(key)) for key, _, kind in COLUMNS]
        lines.append("| " + arm + " | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Percentiles for latency and receiver prefill")
    lines.append("")
    lines.append("| arm | e2e p10 ms | e2e med ms | e2e p90 ms | prefill p10 tok | prefill med tok | prefill p90 tok |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for arm in _sorted_arms(rows):
        metrics = rows[arm]["metrics"]
        cells: list[str] = []
        for metric in ("e2e_ms", "receiver_prefill_tokens"):
            entry = metrics.get(metric)
            for stat in ("p10", "median", "p90"):
                cells.append("-" if entry is None else fmt_value("float", entry.get(stat)))
        lines.append("| " + arm + " | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Runs")
    lines.append("")
    for document in documents:
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        lines.append(
            "- source: {source}; arm={arm}; trials_per_task={trials}; temperature={temperature}; "
            "max_new_tokens={max_new_tokens}; eviction_fill={eviction_fill}; flush_cache={flush_cache}; "
            "generated={timestamp}".format(
                source=document.get("_source", "?"),
                arm=meta.get("arm", "?"),
                trials=meta.get("trials_per_task", "?"),
                temperature=meta.get("temperature", "?"),
                max_new_tokens=meta.get("max_new_tokens", "?"),
                eviction_fill=meta.get("eviction_fill", "?"),
                flush_cache=meta.get("flush_cache", "?"),
                timestamp=meta.get("timestamp_utc", "?"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 2 on fatal errors."""
    parser = argparse.ArgumentParser(
        prog="analyze.py",
        description="Compare OCC benchmark results across arms (medians table plus markdown report).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("results", nargs="+", help="one or more results.json files written by bench_occ3.py")
    parser.add_argument("--out", default="results.md", help="markdown report path")
    args = parser.parse_args(argv)

    documents = load_documents(args.results)
    rows = collect(documents)
    if not rows:
        print("ERROR: no per-arm statistics found in the input files", file=sys.stderr)
        return 2

    print_table(rows)
    report = markdown_report(rows, documents)
    out_path = Path(args.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot write {out_path}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
