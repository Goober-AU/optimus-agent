"""Aggregate the W26 live run into the verdict tables (CUSTOM-OPTIMUS labels).

Offline: reads only the results files and raw call logs this run produced.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from compactbench.runner.persistence import to_run_result

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RAW = HERE / "raw"

ARMS = [
    "optimus-rust-task-state",
    "optimus-rust-plain",
    "echo-first-n",
    "full-transcript-oracle",
]


def arm_table(path: Path) -> dict:
    result = to_run_result(path)
    cases = result.cases
    per_case = [
        {
            "case_id": case.case_id,
            "template": case.case_id.split(":")[0],
            "case_score": case.case_score,
            "drift_resistance": case.drift_resistance,
            "cycles": len(case.cycles),
        }
        for case in cases
    ]
    return {
        "file": path.name,
        "cases": len(cases),
        "overall": result.overall_score,
        "drift_resistance": result.drift_resistance,
        "constraint_retention": result.constraint_retention,
        "contradiction_rate": result.contradiction_rate,
        "compression_ratio": result.compression_ratio,
        "token_usage": result.token_usage.model_dump() if result.token_usage else None,
        "per_case": per_case,
        "target_provider": result.target_provider,
        "target_model": result.target_model,
        "endpoint_kind": result.endpoint_kind,
    }


def summarizer_stats(arm: str) -> dict:
    directory = RAW / f"{arm}-summarizer"
    requests = []
    if directory.exists():
        for path in sorted(directory.glob("*.jsonl")):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        requests.append(json.loads(line))
    latencies = [r["latencyMs"] for r in requests if r.get("latencyMs") is not None]
    rungs = {}
    finishes = {}
    for record in requests:
        rungs[record.get("maxTokens")] = rungs.get(record.get("maxTokens"), 0) + 1
        finishes[record.get("finishReason")] = finishes.get(record.get("finishReason"), 0) + 1
    return {
        "requests": len(requests),
        "latency_ms_mean": round(statistics.mean(latencies)) if latencies else None,
        "latency_ms_p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1]) if latencies else None,
        "max_tokens_rungs": rungs,
        "finish_reasons": finishes,
        "reasoning_tokens_total": sum(r.get("reasoningTokens") or 0 for r in requests),
        "completion_tokens_total": sum(r.get("completionTokens") or 0 for r in requests),
        "prompt_tokens_total": sum(r.get("promptTokens") or 0 for r in requests),
    }


def task_stats(arm: str) -> dict:
    path = RAW / f"{arm}-task.jsonl"
    requests = []
    if path.exists():
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    requests.append(json.loads(line))
    purposes = {}
    rungs = {}
    latencies = []
    for record in requests:
        if record.get("purpose") == "pong":
            continue
        purposes[record.get("purpose")] = purposes.get(record.get("purpose"), 0) + 1
        rungs[record.get("maxTokens")] = rungs.get(record.get("maxTokens"), 0) + 1
        if record.get("latencyMs") is not None:
            latencies.append(record["latencyMs"])
    return {
        "requests": sum(purposes.values()),
        "purposes": purposes,
        "max_tokens_rungs": rungs,
        "latency_ms_mean": round(statistics.mean(latencies)) if latencies else None,
        "backoff_events": sum(1 for r in requests if r.get("transportRetries")),
    }


def main() -> None:
    report = {"arms": {}, "smoke": {}, "mock_at_head": {}}
    for arm in ARMS:
        report["arms"][arm] = arm_table(RESULTS / f"{arm}.jsonl")
        report["arms"][arm]["summarizer"] = summarizer_stats(arm)
        report["arms"][arm]["task"] = task_stats(arm)
    smoke_path = RESULTS / "smoke.jsonl"
    if smoke_path.exists():
        report["smoke"] = arm_table(smoke_path)
    for arm in ARMS:
        mock_path = RESULTS / f"mock-{arm}.jsonl"
        if mock_path.exists():
            report["mock_at_head"][arm] = arm_table(mock_path)
    out = HERE / "aggregate.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    for arm, data in report["arms"].items():
        print(
            f"{arm}: overall {data['overall']:.4f} drift {data['drift_resistance']:.3f} "
            f"constraint {data['constraint_retention']:.3f} compression {data['compression_ratio']:.2f}x "
            f"task-calls {data['task']['requests']} summ-calls {data['summarizer']['requests']}"
        )


if __name__ == "__main__":
    main()
