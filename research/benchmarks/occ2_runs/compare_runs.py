"""OCC-2 official comparison builder.

Reads the adapter scorecards of the occ0 pilot runs (reused, not rerun) and
the OCC-2 encoded runs, plus the raw inter-agent message files, and writes
the comparison tables used by OCC2-OFFICIAL-RESULTS.md:

- run-level matrix at identical measurement points (official S/P/C/D, case
  accuracy, rounds, LLM calls, real prompt tokens, message count, message
  payload bytes, latency p50/p95),
- per-case delta table vs the occ0 pilot per model,
- message encoding compliance (fraction of inter-agent message bodies that
  actually start with the expected header) and per-message byte stats.

    python compare_runs.py   # writes comparison.json / comparison.md
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

HERE = Path(__file__).parent
RUNS = (HERE.parent / "runs").resolve()

# (run_name, mode_label, model, expects_header)
RUNS_SPEC = [
    ("pilot-levelI-n2-msg-glm53", "occ0", "glm-5.3", None),
    ("pilot-levelI-n2-msg-glm53flash", "occ0", "glm-5.3-flash", None),
    ("occ2-levelI-n2-msg-glm53-occ1", "occ1", "glm-5.3", "OCC/1"),
    ("occ2-levelI-n2-msg-glm53flash-occ1", "occ1", "glm-5.3-flash", "OCC/1"),
    ("occ2-levelI-n2-msg-glm53flash-sym", "sym-v1-hand-designed", "glm-5.3-flash", "D/1"),
]


def load_scorecard(run_name):
    p = RUNS / run_name / "scorecard.json"
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def message_stats(run_name, header):
    """Scan the workspace message files of a run for compliance + bytes."""
    ws = RUNS / run_name / "workspace"
    if not ws.exists():
        return None
    per_case = {}
    all_lens = []
    compliant = 0
    total = 0
    for case_dir in sorted(ws.iterdir()):
        if not case_dir.is_dir():
            continue
        msgs = []
        for mp in sorted(case_dir.glob("rounds/round-*/env/messages/*.json")):
            with open(mp, "r", encoding="utf-8") as f:
                d = json.load(f)
            content = str(d.get("content", ""))
            msgs.append({
                "round": d.get("timestamp"),
                "from": d.get("sender_id"),
                "to": d.get("recipient_id"),
                "chars": len(content),
                "header_ok": content.lstrip().startswith(header) if header else None,
            })
            all_lens.append(len(content))
            total += 1
            if header and msgs[-1]["header_ok"]:
                compliant += 1
        per_case[case_dir.name.split("_")[0]] = msgs
    return {
        "messages_total": total,
        "compliant": compliant,
        "compliance_rate": round(compliant / total, 4) if total and header else None,
        "chars_mean": round(statistics.mean(all_lens), 1) if all_lens else None,
        "chars_median": statistics.median(all_lens) if all_lens else None,
        "chars_p95": (sorted(all_lens)[min(len(all_lens) - 1, int(0.95 * len(all_lens)))]
                      if all_lens else None),
        "per_case": per_case,
    }


def main():
    rows = []
    detail = {}
    for run_name, mode, model, header in RUNS_SPEC:
        sc = load_scorecard(run_name)
        if sc is None:
            continue
        agg = sc["adapter_aggregates"]
        mstats = message_stats(run_name, header)
        rows.append({
            "run": run_name,
            "mode": mode,
            "model": model,
            "case_accuracy": agg["case_accuracy"],
            "mean_S": agg["mean_S"],
            "mean_P": agg["mean_P"],
            "mean_C": agg["mean_C"],
            "mean_D": agg["mean_D"],
            "cases_completed": agg["cases_completed"],
            "cases_errored": agg["cases_errored"],
            "rounds_mean": agg["rounds_mean"],
            "total_llm_calls": agg["total_llm_calls"],
            "total_prompt_tokens": agg["total_prompt_tokens"],
            "total_output_tokens_incl_reasoning": agg["total_output_tokens_incl_reasoning"],
            "total_messages": agg["total_inter_agent_messages"],
            "total_message_payload_bytes": agg["total_inter_agent_message_payload_bytes"],
            "bytes_per_message": (round(agg["total_inter_agent_message_payload_bytes"]
                                        / agg["total_inter_agent_messages"], 1)
                                  if agg["total_inter_agent_messages"] else None),
            "case_latency_mean_s": agg["case_latency_s"]["mean"],
            "case_latency_p50_s": agg["case_latency_s"]["p50"],
            "case_latency_p95_s": agg["case_latency_s"]["p95"],
            "header_compliance": mstats["compliance_rate"] if mstats else None,
            "message_chars_mean": mstats["chars_mean"] if mstats else None,
            "message_chars_median": mstats["chars_median"] if mstats else None,
            "message_chars_p95": mstats["chars_p95"] if mstats else None,
        })
        detail[run_name] = {
            "scorecard": sc,
            "message_stats": mstats,
        }

    out = {"rows": rows, "detail": detail}
    with open(HERE / "comparison.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")

    lines = ["# OCC-2 SILO-BENCH comparison (generated)", ""]
    keys = ["mode", "model", "case_accuracy", "mean_S", "mean_P", "mean_C", "mean_D",
            "cases_completed", "rounds_mean", "total_llm_calls", "total_prompt_tokens",
            "total_messages", "total_message_payload_bytes", "bytes_per_message",
            "case_latency_p50_s", "case_latency_p95_s", "header_compliance"]
    lines.append("| " + " | ".join(keys) + " |")
    lines.append("|" + "---|" * len(keys))
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(k)) for k in keys) + " |")
    with open(HERE / "comparison.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {HERE / 'comparison.json'} and comparison.md")
    for r in rows:
        print(r["run"], r["case_accuracy"], r["total_prompt_tokens"],
              r["total_message_payload_bytes"], r["header_compliance"])


if __name__ == "__main__":
    main()
