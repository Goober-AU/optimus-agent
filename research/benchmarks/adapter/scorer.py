"""SILO-BENCH adapter scorer.

Reads the case summaries produced by runner.py and computes run-level
aggregates. Official metrics (S, P, C, D) come from the engine's own
results.json (src/utils/metrics.py) - they are read, not reimplemented.
Adapter-level aggregates (accuracy over cases, token totals, latency
percentiles, message volume) are computed here and labeled as such.

Deviations from the official scorer: NONE. compute_success_rate,
compute_partial_correctness, compute_token_consumption and
compute_communication_density are executed by the official engine inside
engine.evaluate(); this module only aggregates their per-case outputs.
"""

from __future__ import annotations

import json
from pathlib import Path


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def score_run(summaries: list[dict], run_spec: dict) -> dict:
    """Aggregate per-case summaries into a run scorecard dict."""
    n = len(summaries)
    completed = [s for s in summaries if s.get("status") == "completed"]
    errored = [s for s in summaries if s.get("status") != "completed"]

    def off_metric(s, key):
        return ((s.get("official", {}).get("metrics") or {}).get(key))

    success_flags = [1.0 if (s.get("official", {}).get("success") is True) else 0.0 for s in summaries]
    s_vals = [off_metric(s, "S_success_rate") for s in completed if off_metric(s, "S_success_rate") is not None]
    p_vals = [off_metric(s, "P_partial_correctness") for s in completed if off_metric(s, "P_partial_correctness") is not None]
    c_vals = [off_metric(s, "C_token_consumption") for s in completed if off_metric(s, "C_token_consumption") is not None]
    d_vals = [off_metric(s, "D_communication_density") for s in completed if off_metric(s, "D_communication_density") is not None]

    rounds_vals = [s["official"]["rounds_executed"] for s in completed if s.get("official", {}).get("rounds_executed")]
    elapsed_vals = [s["elapsed_s"] for s in summaries if s.get("elapsed_s") is not None]
    call_lat = []
    prompt_tok = []
    output_tok = []
    msg_counts = []
    msg_bytes = []
    for s in summaries:
        ad = s.get("adapter", {})
        lat = ad.get("llm_latency_s") or {}
        # per-case mean latency contributes each case once to the pool
        if lat.get("mean") is not None:
            call_lat.append(lat["mean"])
        prompt_tok.append(ad.get("prompt_tokens_sum") or 0)
        output_tok.append(ad.get("output_tokens_sum") or 0)
        msg_counts.append(ad.get("inter_agent_messages") or 0)
        msg_bytes.append(ad.get("inter_agent_message_payload_bytes") or 0)

    elapsed_sorted = sorted(elapsed_vals)
    lat_sorted = sorted(call_lat)

    per_case = []
    for s in summaries:
        off = s.get("official", {})
        per_case.append({
            "task_file": s.get("task_file"),
            "case_id": off.get("case_id"),
            "case_name": off.get("case_name"),
            "status": s.get("status"),
            "rounds": off.get("rounds_executed"),
            "S": off_metric(s, "S_success_rate"),
            "P": off_metric(s, "P_partial_correctness"),
            "C": off_metric(s, "C_token_consumption"),
            "D": off_metric(s, "D_communication_density"),
            "success": off.get("success"),
            "prompt_tokens": s.get("adapter", {}).get("prompt_tokens_sum"),
            "output_tokens_incl_reasoning": s.get("adapter", {}).get("output_tokens_sum"),
            "llm_calls": s.get("adapter", {}).get("llm_calls"),
            "messages": s.get("adapter", {}).get("inter_agent_messages"),
            "message_payload_bytes": s.get("adapter", {}).get("inter_agent_message_payload_bytes"),
            "elapsed_s": s.get("elapsed_s"),
            "error": s.get("error"),
        })

    return {
        "run_name": run_spec["name"],
        "comm_mode": run_spec.get("comm_mode"),
        "model": run_spec["model"],
        "protocol": run_spec["protocol"],
        "max_rounds": run_spec["max_rounds"],
        "task_count": n,
        "adapter_aggregates": {
            "case_accuracy": _mean(success_flags),           # fraction of cases with S == 1.0
            "mean_S": _mean(s_vals),
            "mean_P": _mean(p_vals),
            "mean_C": _mean(c_vals),
            "mean_D": _mean(d_vals),
            "cases_completed": len(completed),
            "cases_errored": len(errored),
            "rounds_mean": _mean(rounds_vals),
            "total_prompt_tokens": sum(prompt_tok),
            "total_output_tokens_incl_reasoning": sum(output_tok),
            "total_llm_calls": sum(s.get("adapter", {}).get("llm_calls") or 0 for s in summaries),
            "total_inter_agent_messages": sum(msg_counts),
            "total_inter_agent_message_payload_bytes": sum(msg_bytes),
            "case_latency_s": {
                "mean": _mean(elapsed_vals),
                "p50": _percentile(elapsed_sorted, 0.50) if elapsed_sorted else None,
                "p95": _percentile(elapsed_sorted, 0.95) if elapsed_sorted else None,
            },
            "per_case_mean_llm_latency_s": {
                "mean": _mean(call_lat),
                "p50": _percentile(lat_sorted, 0.50) if lat_sorted else None,
                "p95": _percentile(lat_sorted, 0.95) if lat_sorted else None,
            },
        },
        "output_tokens_caveat": (
            "usage.completion_tokens from the gateway includes hidden reasoning tokens; "
            "do not use for wire-cost claims. prompt_tokens are real server-side counts."
        ),
        "per_case": per_case,
    }


def load_summaries(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "summaries" in data:
        return data["summaries"]
    if isinstance(data, list):
        return data
    raise ValueError(f"unrecognized summaries format: {path}")


def write_scorecards(score: dict, out_dir: str | Path) -> tuple[Path, Path]:
    """Write results JSON + markdown scorecard. Returns (json_path, md_path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "scorecard.json"
    md_path = out_dir / "scorecard.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(score, f, indent=2)
        f.write("\n")

    agg = score["adapter_aggregates"]
    lines = []
    lines.append(f"# SILO-BENCH run: {score['run_name']}")
    lines.append("")
    lines.append("| field | value |")
    lines.append("|---|---|")
    lines.append(f"| comm_mode | {score['comm_mode']} |")
    lines.append(f"| model | {score['model']} |")
    lines.append(f"| protocol | {score['protocol']} |")
    lines.append(f"| max_rounds | {score['max_rounds']} |")
    lines.append(f"| task_count | {score['task_count']} |")
    lines.append(f"| case_accuracy (S == 1.0) | {agg['case_accuracy']:.3f} |" if agg["case_accuracy"] is not None else "| case_accuracy (S == 1.0) | n/a |")
    for key, label in [
        ("mean_S", "mean S (official success rate)"),
        ("mean_P", "mean P (official partial correctness)"),
        ("mean_C", "mean C (official token consumption)"),
        ("mean_D", "mean D (official communication density)"),
        ("cases_completed", "cases completed"),
        ("cases_errored", "cases errored/timed out"),
        ("rounds_mean", "mean rounds to completion"),
        ("total_llm_calls", "total LLM calls"),
        ("total_prompt_tokens", "total prompt tokens (real, server-side)"),
        ("total_output_tokens_incl_reasoning", "total completion tokens (INCLUDES hidden reasoning)"),
        ("total_inter_agent_messages", "total inter-agent messages"),
        ("total_inter_agent_message_payload_bytes", "total inter-agent message payload bytes"),
    ]:
        v = agg[key]
        lines.append(f"| {label} | {v if v is not None else 'n/a'} |")
    cl = agg["case_latency_s"]
    lines.append(f"| case latency mean / p50 / p95 (s) | {cl['mean']:.1f} / {cl['p50']:.1f} / {cl['p95']:.1f} |" if cl["mean"] is not None else "| case latency mean / p50 / p95 (s) | n/a |")
    ll = agg["per_case_mean_llm_latency_s"]
    lines.append(f"| LLM call latency (per-case mean) mean / p50 / p95 (s) | {ll['mean']:.1f} / {ll['p50']:.1f} / {ll['p95']:.1f} |" if ll["mean"] is not None else "| LLM call latency mean / p50 / p95 (s) | n/a |")
    lines.append("")
    lines.append("## Per-case results")
    lines.append("")
    lines.append("| task | status | rounds | S | P | messages | msg bytes | prompt tok | out tok* | elapsed s |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in score["per_case"]:
        lines.append(
            "| {task} | {status} | {rounds} | {S} | {P} | {msgs} | {mbytes} | {ptok} | {otok} | {el} |".format(
                task=c["case_id"] or (Path(c["task_file"]).stem if c.get("task_file") else "?"),
                status=c["status"],
                rounds=c["rounds"],
                S=c["S"] if c["S"] is not None else "n/a",
                P=c["P"] if c["P"] is not None else "n/a",
                msgs=c["messages"],
                mbytes=c["message_payload_bytes"],
                ptok=c["prompt_tokens"],
                otok=c["output_tokens_incl_reasoning"],
                el=c["elapsed_s"],
            )
        )
    lines.append("")
    lines.append("* completion tokens include hidden reasoning tokens reported by the gateway;")
    lines.append("  prompt tokens are real server-side counts. S/P/C/D are official engine metrics.")
    lines.append("")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return json_path, md_path
