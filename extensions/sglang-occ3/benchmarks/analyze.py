#!/usr/bin/env python3
"""Compare OCC benchmark results across arms.

Reads one or more results.json files written by bench_occ3.py, prints an
arm-by-metric comparison table (medians) to stdout, and writes a markdown
report (results.md by default) containing the same table, percentile
detail for latency and receiver-prefill metrics, per-arm accuracy with
95 percent Wilson confidence intervals, a pairwise accuracy significance
matrix (two-proportion z-test), and the semantic-transfer gate.

Semantic-transfer gate: the random_latent and zeroed_latent control arms
are the significance floor. A protocol arm (occ3 by default) only claims
semantic transfer when it beats EVERY floor arm with a two-proportion
z-test p-value below --alpha AND an accuracy margin of at least
--min-margin. If OCC-3 performs no better than random latent state, it
has not demonstrated semantic communication.

When a results file keeps raw trial records, statistics are recomputed
from them (files are merged per arm); otherwise the stored aggregate is
used. Arm id "text" from older files maps to "full_text". No emojis are
used anywhere in the output.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from bench_occ3 import ARM_ALIASES, DEFAULT_FLOOR_ARMS, NUMERIC_METRICS, percentile
except ImportError as exc:  # pragma: no cover
    print(f"ERROR: analyze.py must run next to bench_occ3.py ({exc})", file=sys.stderr)
    raise SystemExit(2)

ARM_ORDER = {
    "no_comm": 0,
    "random_latent": 1,
    "unrelated_latent": 2,
    "zeroed_latent": 3,
    "occ1": 4,
    "occ2": 5,
    "occ3": 6,
    "full_text": 7,
}

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
        successes = sum(1 for r in records if r.get("success") is True)
        flat["successes"] = successes
        flat["success_rate"] = successes / len(records)
        flat["errors"] = sum(1 for r in records if r.get("error"))
        arm = str(records[0].get("arm") or "")
        latent_arms = {"occ3", "random_latent", "unrelated_latent", "zeroed_latent"}
        if arm in latent_arms:
            hits = sum(1 for r in records if r.get("hit") is True)
            flat["hit_rate"] = hits / len(records)
            flat["cache_miss_rate"] = 1.0 - flat["hit_rate"]
        by_tier: dict[str, dict[str, int]] = {}
        for record in records:
            tier = str(record.get("tier") or 1)
            bucket = by_tier.setdefault(tier, {"n": 0, "k": 0})
            bucket["n"] += 1
            if record.get("success") is True:
                bucket["k"] += 1
        for tier, bucket in by_tier.items():
            flat[f"success_rate_tier{tier}"] = bucket["k"] / bucket["n"]
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
    n = aggregate.get("n_trials")
    if isinstance(aggregate.get("success_rate"), (int, float)) and isinstance(n, int):
        flat["successes"] = int(round(float(aggregate["success_rate"]) * n))
        flat["errors"] = int(aggregate.get("n_errors", 0))
    return {"flat": flat, "metrics": metrics}


def collect(documents: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge results documents into one row per arm (text alias -> full_text)."""
    records_by_arm: dict[str, list[dict[str, Any]]] = {}
    stored_by_arm: dict[str, dict[str, Any]] = {}
    for document in documents:
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        records = [r for r in (document.get("trials") or []) if isinstance(r, dict)]
        if records:
            default_arm = str(meta.get("arm") or "unknown")
            for record in records:
                arm = ARM_ALIASES.get(str(record.get("arm") or default_arm), str(record.get("arm") or default_arm))
                records_by_arm.setdefault(arm, []).append(record)
        else:
            aggregate = document.get("aggregate")
            arm = ARM_ALIASES.get(str(meta.get("arm") or "unknown"), str(meta.get("arm") or "unknown"))
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
    """Canonical arm order: controls first, then protocols, then upper bound."""
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


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion; None when n is 0."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denominator
    margin = (z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float] | None:
    """Two-proportion z-test (pooled); returns (z, two-tailed p) or None.

    None when either sample is empty or the pooled standard error is zero
    (identical rates with no variance; treated as no difference).
    """
    if n1 <= 0 or n2 <= 0:
        return None
    p1 = k1 / n1
    p2 = k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2))
    if se == 0.0:
        return (0.0, 1.0)
    z = (p1 - p2) / se
    p_value = math.erfc(abs(z) / math.sqrt(2.0))
    return (z, p_value)


def accuracy_of(rows: dict[str, dict[str, Any]], arm: str) -> tuple[int, int, float] | None:
    """Return (successes, trials, rate) for one arm, or None when absent."""
    row = rows.get(arm)
    if row is None:
        return None
    flat = row["flat"]
    n = flat.get("trials")
    if not isinstance(n, int) or n <= 0:
        return None
    k = flat.get("successes")
    if not isinstance(k, int):
        rate = flat.get("success_rate")
        if not isinstance(rate, (int, float)):
            return None
        k = int(round(float(rate) * n))
    return (k, n, k / n)


def pairwise_comparisons(
    rows: dict[str, dict[str, Any]], arms: Sequence[str], alpha: float
) -> list[dict[str, Any]]:
    """All ordered pairs among the given arms with z-test detail."""
    comparisons: list[dict[str, Any]] = []
    for left in arms:
        for right in arms:
            if left >= right:
                continue
            a = accuracy_of(rows, left)
            b = accuracy_of(rows, right)
            entry: dict[str, Any] = {"arm_a": left, "arm_b": right}
            if a is None or b is None:
                entry.update({"status": "missing"})
            else:
                k1, n1, p1 = a
                k2, n2, p2 = b
                entry.update(
                    {
                        "status": "ok",
                        "successes_a": k1,
                        "trials_a": n1,
                        "accuracy_a": p1,
                        "successes_b": k2,
                        "trials_b": n2,
                        "accuracy_b": p2,
                        "delta": p1 - p2,
                    }
                )
                test = two_proportion_z(k1, n1, k2, n2)
                if test is None:
                    entry.update({"z": None, "p_value": None, "significant": None})
                else:
                    z, p_value = test
                    entry.update(
                        {
                            "z": z,
                            "p_value": p_value,
                            "significant": p_value < alpha,
                        }
                    )
            comparisons.append(entry)
    return comparisons


def gate_verdicts(
    rows: dict[str, dict[str, Any]],
    gate_arms: Sequence[str],
    floor_arms: Sequence[str],
    alpha: float,
    min_margin: float,
    reference_arm: str = "no_comm",
) -> list[dict[str, Any]]:
    """Semantic-transfer gate: each gate arm must beat every floor arm."""
    verdicts: list[dict[str, Any]] = []
    for arm in gate_arms:
        treatment = accuracy_of(rows, arm)
        entry: dict[str, Any] = {"arm": arm, "floors": [], "verdict": "PASS"}
        if treatment is None:
            entry["verdict"] = "UNDERSAMPLED"
            entry["reason"] = f"arm {arm!r} has no accuracy data"
            verdicts.append(entry)
            continue
        k1, n1, p1 = treatment
        entry["accuracy"] = p1
        entry["trials"] = n1
        for floor in floor_arms:
            control = accuracy_of(rows, floor)
            if control is None:
                entry["floors"].append({"floor": floor, "status": "missing"})
                entry["verdict"] = "UNDERSAMPLED"
                entry["reason"] = f"floor arm {floor!r} has no accuracy data"
                continue
            k2, n2, p2 = control
            test = two_proportion_z(k1, n1, k2, n2)
            if test is None:
                floor_entry = {
                    "floor": floor,
                    "status": "degenerate",
                    "delta": p1 - p2,
                    "beats": False,
                }
                entry["floors"].append(floor_entry)
                if entry["verdict"] != "UNDERSAMPLED":
                    entry["verdict"] = "FAIL"
                    entry["reason"] = f"no variance against floor {floor!r}"
                continue
            z, p_value = test
            delta = p1 - p2
            beats = p_value < alpha and delta >= min_margin
            entry["floors"].append(
                {
                    "floor": floor,
                    "status": "ok",
                    "accuracy_floor": p2,
                    "delta": delta,
                    "z": z,
                    "p_value": p_value,
                    "significant": p_value < alpha,
                    "beats": beats,
                }
            )
            if not beats and entry["verdict"] != "UNDERSAMPLED":
                entry["verdict"] = "FAIL"
                entry["reason"] = f"does not beat floor {floor!r} at alpha={alpha}"
        # Reference: the no-communication floor, reported but not gating.
        reference = accuracy_of(rows, reference_arm)
        if reference is not None:
            entry["reference_no_comm_accuracy"] = reference[2]
        verdicts.append(entry)
    return verdicts


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


def markdown_report(
    rows: dict[str, dict[str, Any]],
    documents: Sequence[dict[str, Any]],
    *,
    alpha: float,
    min_margin: float,
    floor_arms: Sequence[str],
    gate_arms: Sequence[str],
    comparisons: Sequence[dict[str, Any]],
    verdicts: Sequence[dict[str, Any]],
) -> str:
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

    lines.append("## Accuracy with 95 percent Wilson intervals")
    lines.append("")
    lines.append("| arm | successes | trials | accuracy | 95 percent CI |")
    lines.append("| --- | --- | --- | --- | --- |")
    for arm in _sorted_arms(rows):
        accuracy = accuracy_of(rows, arm)
        if accuracy is None:
            lines.append(f"| {arm} | - | - | - | - |")
            continue
        k, n, rate = accuracy
        interval = wilson_ci(k, n)
        if interval is None:
            interval_text = "-"
        else:
            interval_text = f"[{interval[0]:.3f}, {interval[1]:.3f}]"
        lines.append(f"| {arm} | {k} | {n} | {100.0 * rate:.1f} | {interval_text} |")
    lines.append("")

    lines.append("## Semantic-transfer gate")
    lines.append("")
    lines.append(
        "Floor arms: "
        + ", ".join(floor_arms)
        + ". A gate arm claims semantic transfer only when it beats every floor arm "
        f"with p < {alpha} (two-proportion z-test) and an accuracy margin of at least {min_margin:.3f}. "
        "If OCC-3 performs no better than random latent state, it has not demonstrated semantic communication."
    )
    lines.append("")
    for verdict in verdicts:
        arm = verdict["arm"]
        lines.append(f"### {arm}: {verdict['verdict']}")
        lines.append("")
        if verdict["verdict"] != "UNDERSAMPLED":
            lines.append(
                f"- accuracy: {100.0 * verdict['accuracy']:.1f} percent over {verdict['trials']} trials"
            )
        else:
            lines.append(f"- reason: {verdict.get('reason', 'no data')}")
        if verdict.get("reference_no_comm_accuracy") is not None:
            lines.append(
                f"- reference no_comm accuracy: {100.0 * verdict['reference_no_comm_accuracy']:.1f} percent"
            )
        for floor in verdict["floors"]:
            if floor.get("status") == "missing":
                lines.append(f"- vs {floor['floor']}: no data")
                continue
            if floor.get("status") == "degenerate":
                lines.append(f"- vs {floor['floor']}: degenerate (no variance), delta {floor['delta']:+.3f}")
                continue
            lines.append(
                "- vs {floor}: accuracy {acc:.1f} percent, delta {delta:+.3f}, z {z:+.2f}, p {p_text}, beats floor: {beats}".format(
                    floor=floor["floor"],
                    acc=100.0 * floor["accuracy_floor"],
                    delta=floor["delta"],
                    z=floor["z"],
                    p_text=("<1e-12" if floor["p_value"] < 1e-12 else f"{floor['p_value']:.2g}"),
                    beats=("yes" if floor["beats"] else "no"),
                )
            )
        lines.append("")

    lines.append("## Pairwise accuracy significance")
    lines.append("")
    lines.append(
        f"Two-proportion z-test per pair; significant when p < {alpha}. "
        "These are unadjusted for multiple comparisons."
    )
    lines.append("")
    lines.append("| arm a | arm b | acc a | acc b | delta | z | p | significant |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for comparison in comparisons:
        if comparison.get("status") != "ok":
            lines.append(f"| {comparison['arm_a']} | {comparison['arm_b']} | - | - | - | - | - | - |")
            continue
        p_text = "<1e-12" if comparison["p_value"] < 1e-12 else f"{comparison['p_value']:.2g}"
        z_text = "-" if comparison.get("z") is None else f"{comparison['z']:+.2f}"
        lines.append(
            "| {a} | {b} | {acc_a:.1f} | {acc_b:.1f} | {delta:+.3f} | {z} | {p} | {sig} |".format(
                a=comparison["arm_a"],
                b=comparison["arm_b"],
                acc_a=100.0 * comparison["accuracy_a"],
                acc_b=100.0 * comparison["accuracy_b"],
                delta=comparison["delta"],
                z=z_text,
                p=p_text,
                sig=("yes" if comparison.get("significant") else "no"),
            )
        )
    lines.append("")

    lines.append("## Runs")
    lines.append("")
    for document in documents:
        meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
        lines.append(
            "- source: {source}; arm={arm}; trials_per_task={trials}; temperature={temperature}; "
            "max_new_tokens={max_new_tokens}; eviction_fill={eviction_fill}; flush_cache={flush_cache}; "
            "selftest={selftest}; generated={timestamp}".format(
                source=document.get("_source", "?"),
                arm=meta.get("arm", "?"),
                trials=meta.get("trials_per_task", "?"),
                temperature=meta.get("temperature", "?"),
                max_new_tokens=meta.get("max_new_tokens", "?"),
                eviction_fill=meta.get("eviction_fill", "?"),
                flush_cache=meta.get("flush_cache", "?"),
                selftest=meta.get("selftest", "?"),
                timestamp=meta.get("timestamp_utc", "?"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 2 on fatal errors."""
    parser = argparse.ArgumentParser(
        prog="analyze.py",
        description="Compare OCC benchmark results across arms (medians table, significance gate, markdown report).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("results", nargs="+", help="one or more results.json files written by bench_occ3.py")
    parser.add_argument("--out", default="results.md", help="markdown report path")
    parser.add_argument("--json-out", default=None, help="machine-readable comparisons JSON path (schema occ-bench-analysis/2)")
    parser.add_argument("--alpha", type=float, default=0.05, help="significance level for z-tests")
    parser.add_argument("--min-margin", type=float, default=0.0, help="minimum accuracy margin over a floor arm to claim semantic transfer")
    parser.add_argument(
        "--floor-arms",
        default=",".join(DEFAULT_FLOOR_ARMS),
        help="comma list of control arms forming the significance floor",
    )
    parser.add_argument(
        "--gate-arms",
        default="occ3",
        help="comma list of arms evaluated by the semantic-transfer gate",
    )
    args = parser.parse_args(argv)

    floor_arms = [arm.strip() for arm in args.floor_arms.split(",") if arm.strip()]
    gate_arms = [arm.strip() for arm in args.gate_arms.split(",") if arm.strip()]

    documents = load_documents(args.results)
    rows = collect(documents)
    if not rows:
        print("ERROR: no per-arm statistics found in the input files", file=sys.stderr)
        return 2

    print_table(rows)

    arms_present = _sorted_arms(rows)
    comparisons = pairwise_comparisons(rows, arms_present, args.alpha)
    verdicts = gate_verdicts(rows, gate_arms, floor_arms, args.alpha, args.min_margin)

    print("")
    print(f"--- semantic-transfer gate (alpha={args.alpha}, floors: {', '.join(floor_arms)}) ---")
    for verdict in verdicts:
        accuracy_text = (
            f" accuracy={100.0 * verdict['accuracy']:.1f}% n={verdict['trials']}"
            if verdict["verdict"] != "UNDERSAMPLED"
            else f" reason={verdict.get('reason', 'no data')}"
        )
        print(f"{verdict['arm']}: {verdict['verdict']}{accuracy_text}")
        for floor in verdict["floors"]:
            if floor.get("status") == "ok":
                p_text = "<1e-12" if floor["p_value"] < 1e-12 else f"{floor['p_value']:.2g}"
                print(
                    f"  vs {floor['floor']}: floor={100.0 * floor['accuracy_floor']:.1f}% "
                    f"delta={floor['delta']:+.3f} z={floor['z']:+.2f} p={p_text} beats={'yes' if floor['beats'] else 'no'}"
                )
            elif floor.get("status") == "missing":
                print(f"  vs {floor['floor']}: no data")

    report = markdown_report(
        rows,
        documents,
        alpha=args.alpha,
        min_margin=args.min_margin,
        floor_arms=floor_arms,
        gate_arms=gate_arms,
        comparisons=comparisons,
        verdicts=verdicts,
    )
    out_path = Path(args.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot write {out_path}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out_path}")

    if args.json_out:
        payload = {
            "schema": "occ-bench-analysis/2",
            "alpha": args.alpha,
            "min_margin": args.min_margin,
            "floor_arms": floor_arms,
            "gate_arms": gate_arms,
            "arms": {
                arm: {
                    "trials": rows[arm]["flat"].get("trials"),
                    "successes": rows[arm]["flat"].get("successes"),
                    "success_rate": rows[arm]["flat"].get("success_rate"),
                    "wilson_95": wilson_ci(
                        int(rows[arm]["flat"].get("successes", 0)),
                        int(rows[arm]["flat"].get("trials", 0)),
                    ),
                }
                for arm in arms_present
            },
            "pairwise": comparisons,
            "gate": verdicts,
        }
        json_path = Path(args.json_out)
        try:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"wrote {json_path}")
        except OSError as exc:
            print(f"ERROR: cannot write {json_path}: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
