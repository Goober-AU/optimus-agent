"""R2-E3: coding-task scenario suite on the live GLM 5.3 endpoint.

Runs the W7 live harness (real GLM 5.3 summarizer + prober, content-keyed
response cache, length-retry ladder, ANSWER: delimiter protocol) over the
E3 coding-task scenarios (TASK-A/C/E/H/F): 2 arms (default, taskState) x
5 cycles x probes-at-every-cycle x 3 reps, plus a 1-rep pre-compaction
baseline (seeds + cycle-1 entries - the context the cycle-1 summarizer
sees).

Usage (from research/):
  python -m context_rot_r2.live.e3.run_e3 --arm both            # full suite
  python -m context_rot_r2.live.e3.run_e3 --arm default --tasks A --limit-cycles 1 --reps 1   # smoke
  python -m context_rot_r2.live.e3.run_e3 --stage aggregate      # rebuild results.json
  python -m context_rot_r2.live.e3.run_e3 --arm typed                     # typed protected-state arm

Every model call is appended to e3/raw-calls.jsonl with its request,
response, usage and latency; identical requests resume from the cache.
A hard budget guard stops all chains when the call count in the raw log
would exceed the campaign budget.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

from context_rot_r2.live import driver as w7_driver
from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.live.e3 import driver as e3_driver
from context_rot_r2.live.e3 import probe as probe_e3
from context_rot_r2.live.e3 import scenarios as e3_scenarios
from context_rot_r2.live.run_r2e2 import BASE_URL, CONFIG, MODEL_ID, GlmBackend, load_credentials, pong_check

HERE = os.path.dirname(os.path.abspath(__file__))

# Full plan: 3 reps at every cycle point (brief: probes-at-every-cycle,
# 3 reps per probe) plus a 1-rep pre-compaction baseline.
PLAN_FULL = {"baseline": 1, 1: 3, 2: 3, 3: 3, 4: 3, 5: 3}
BUDGET_MAX_CALLS = 400
ARMS_AVAILABLE = ("default", "taskState", "typed")


class BudgetExceeded(RuntimeError):
    pass


def _budget_count(backend: Optional[GlmBackend] = None) -> int:
    """Wire calls made so far.

    Counts the append-only raw log, NOT backend.done: the in-memory dict is
    keyed by callId, and summarizer call ids collide per chain (one id per
    scenario|arm, reused every cycle), which would undercount the true
    number of wire calls by up to (cycles - 1) per chain.
    """
    path = os.path.join(HERE, "raw-calls.jsonl")
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


class E3Runner:
    """Per-(scenario, arm) live runner: summarizer + probe calls."""

    def __init__(self, backend: GlmBackend, scenario_name: str, arm: str) -> None:
        self.backend = backend
        self.scenario = scenario_name
        self.arm = arm

    def _summarizer(self, system: str, user: str, max_tokens: float, force_fresh: bool) -> rm.SummarizerCallResult:
        call_id = f"{self.scenario}|{self.arm}|summarizer"
        meta = {"kind": "summarizer", "scenario": self.scenario, "arm": self.arm}
        record = self.backend.chat(
            call_id,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            int(max_tokens),
            meta,
            force_fresh=force_fresh,
        )
        response = record["response"]
        return rm.SummarizerCallResult(
            text=response["content"],
            stop_reason=response["finishReason"] or "",
            usage=response["usage"],
            latency_s=record["latencyS"],
            attempts=record["attempts"],
        )

    def summarizer_fn(self, system: str, user: str, max_tokens: float) -> rm.SummarizerCallResult:
        if _budget_count(self.backend) >= BUDGET_MAX_CALLS:
            raise BudgetExceeded(f"call budget exhausted ({BUDGET_MAX_CALLS})")
        return self._summarizer(system, user, max_tokens, force_fresh=False)

    def summarizer_retry_fn(self, system: str, user: str, max_tokens: float) -> rm.SummarizerCallResult:
        # Validation retries resample (cache-bypassing), as in W7.
        if _budget_count(self.backend) >= BUDGET_MAX_CALLS:
            raise BudgetExceeded(f"call budget exhausted ({BUDGET_MAX_CALLS})")
        return self._summarizer(system, user, max_tokens, force_fresh=True)

    def probe_call(self, point: str, probe: Dict[str, Any], rep: int,
                   context_messages: List[Dict[str, str]]) -> Dict[str, Any]:
        if _budget_count(self.backend) >= BUDGET_MAX_CALLS:
            raise BudgetExceeded(f"call budget exhausted ({BUDGET_MAX_CALLS})")
        call_id = f"{self.scenario}|{self.arm}|probe|{point}|{probe['id']}|r{rep}"
        messages = (
            [{"role": "system", "content": probe_e3.PROBE_SYSTEM_PROMPT}]
            + context_messages
            + [{"role": "user", "content": probe_e3.ANSWER_PROTOCOL.format(question=probe["question"])}]
        )
        meta = {
            "kind": "probe",
            "scenario": self.scenario,
            "arm": self.arm,
            "point": point,
            "probeId": probe["id"],
            "probeKind": probe["kind"],
            "rep": rep,
        }
        # rep 0 resumes from the content cache; reps 1+ are genuine
        # resamples (fresh sampling) so the 3-rep majority is real.
        record = self.backend.chat(
            call_id, messages, self.backend.cfg["probeMaxTokens"], meta, force_fresh=(rep > 0)
        )
        response = record["response"]
        extracted = probe_e3.extract_answer(response["content"])
        match = probe_e3.match_probe(probe, extracted) if extracted is not None else None
        return {
            "callId": call_id,
            "extracted": extracted,
            "match": match,
            "extractionFailure": extracted is None,
            "rawContent": response["content"],
            "finishReason": response["finishReason"],
            "usage": response["usage"],
            "latencyS": record["latencyS"],
        }

    def run_probe_round(self, point: str, probes: Sequence[Dict[str, Any]],
                        context_messages: List[Dict[str, str]], reps: int) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for probe in probes:
            rep_records = [
                self.probe_call(point, probe, rep, context_messages) for rep in range(reps)
            ]
            records.append(
                {
                    "id": probe["id"],
                    "kind": probe["kind"],
                    "question": probe["question"],
                    "gold": probe["gold"],
                    "reps": rep_records,
                    "score": probe_e3.score_reps(probe, rep_records),
                }
            )
        return records


def _baseline_context(scenario: Dict[str, Any]) -> List[Dict[str, str]]:
    """Seeds + cycle-1 entries, uncompacted (what the cycle-1 summarizer sees)."""
    entries = [
        rm.Entry(
            kind=e["kind"],
            text=e.get("text", ""),
            usage_input=e.get("usageInput"),
            tool_call_id=e.get("toolCallId", ""),
            tool_name=e.get("toolName", ""),
            from_name=e.get("fromName", ""),
        )
        for e in scenario["seedEntries"] + scenario["cycleEntries"][0]
    ]
    return w7_driver.restored_request_messages(None, entries)


def run_task(backend: GlmBackend, task_id: str, arm: str, plan: Dict[Any, int],
             limit_cycles: Optional[int] = None) -> Dict[str, Any]:
    scenario = e3_scenarios.build_e3_scenario(task_id)
    scenario["arms"] = [arm]
    runner = E3Runner(backend, scenario["name"], arm)
    result: Dict[str, Any] = {
        "task": task_id,
        "scenario": scenario["name"],
        "arm": arm,
        "spec": {
            "cycles": scenario["cycles"],
            "restartAfterCycle": scenario["restartAfterCycle"],
            "probePlan": {str(k): v for k, v in plan.items()},
            "limitCycles": limit_cycles,
        },
        "cycles": [],
        "failures": [],
        "probes": [],
        "restarts": [],
        "probeRounds": {},
    }

    if "baseline" in plan and limit_cycles is None:
        context = _baseline_context(scenario)
        result["baseline"] = runner.run_probe_round("baseline", scenario["probes"], context, plan["baseline"])
        print(f"[{scenario['name']}|{arm}] baseline round done", flush=True)

    def probe_hook(arm_name: str, summary: Optional[str], kept: List[rm.Entry], cycle: int) -> None:
        point = cycle + 1
        if point not in plan:
            return
        context = w7_driver.restored_request_messages(summary, kept)
        result["probeRounds"][str(point)] = runner.run_probe_round(str(point), scenario["probes"], context, plan[point])
        correct = sum(1 for r in result["probeRounds"][str(point)] if r["score"]["status"] == "correct")
        print(
            f"[{scenario['name']}|{arm_name}] cycle {point} probes: {correct}/{len(result['probeRounds'][str(point)])} correct "
            f"(budget {_budget_count(backend)})",
            flush=True,
        )

    arm_result = e3_driver.run_arm_e3(
        scenario,
        arm,
        runner.summarizer_fn,
        prompt_version=rm.COMPACTION_PROMPT_V2,
        wire_max_tokens=CONFIG["summarizerMaxTokens"],
        model=rm.ModelConfig(context_window=1_048_576.0, max_tokens=64_000.0),
        probe_hook=probe_hook,
        limit_cycles=limit_cycles,
        summarizer_retry=runner.summarizer_retry_fn,
        validation_attempts=CONFIG["summarizerValidationAttempts"],
        restart_after_cycle=scenario["restartAfterCycle"],
    )
    result["cycles"] = arm_result.cycles
    result["failures"] = arm_result.failures
    result["probes"] = arm_result.probes
    result["restarts"] = arm_result.restarts
    result["finalSummary"] = arm_result.final_summary
    return result


def summarize_round(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    correct = incorrect = extraction_failures = 0
    strict_correct = 0
    for record in records:
        status = record["score"]["status"]
        if record["score"].get("strictStatus") == "correct":
            strict_correct += 1
        if status == "correct":
            correct += 1
        elif status == "incorrect":
            incorrect += 1
        else:
            extraction_failures += 1
    return {
        "probes": len(records),
        "correct": correct,
        "incorrect": incorrect,
        "extractionFailures": extraction_failures,
        "strictCorrect": strict_correct,
    }


def aggregate(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for key, task_result in results.items():
        arm = task_result["arm"]
        entry = metrics.setdefault(
            key,
            {
                "task": task_result["task"],
                "scenario": task_result["scenario"],
                "arms": {},
            },
        )
        arm_metrics: Dict[str, Any] = {
            "cyclesRun": sum(1 for c in task_result["cycles"] if c.get("ran")),
            "failures": task_result["failures"],
            "restarts": task_result["restarts"],
            "retention": {
                "retained": sum(1 for p in task_result["probes"] if p["retained"]),
                "total": len(task_result["probes"]),
            },
            "rounds": {},
        }
        usage_totals = {"promptTokens": 0, "completionTokens": 0, "reasoningTokens": 0}
        for point, records in task_result.get("probeRounds", {}).items():
            arm_metrics["rounds"][point] = summarize_round(records)
            for record in records:
                for rep in record["reps"]:
                    usage = rep.get("usage") or {}
                    usage_totals["promptTokens"] += usage.get("prompt_tokens") or 0
                    usage_totals["completionTokens"] += usage.get("completion_tokens") or 0
                    usage_totals["reasoningTokens"] += usage.get("reasoning_tokens") or 0
        if task_result.get("baseline"):
            arm_metrics["baseline"] = summarize_round(task_result["baseline"])
            for record in task_result["baseline"]:
                for rep in record["reps"]:
                    usage = rep.get("usage") or {}
                    usage_totals["promptTokens"] += usage.get("prompt_tokens") or 0
                    usage_totals["completionTokens"] += usage.get("completion_tokens") or 0
        for cycle in task_result["cycles"]:
            cycle_usage = cycle.get("summarizerUsage") or {}
            usage_totals["promptTokens"] += cycle_usage.get("promptTokens") or 0
            usage_totals["completionTokens"] += cycle_usage.get("completionTokens") or 0
            usage_totals["reasoningTokens"] += cycle_usage.get("reasoningTokens") or 0
        arm_metrics["usage"] = usage_totals
        entry["arms"][arm] = arm_metrics
    return metrics


def count_calls(backend: Optional[GlmBackend] = None) -> Optional[Dict[str, Any]]:
    """Budget accounting from the raw log (the file is authoritative)."""
    path = os.path.join(HERE, "raw-calls.jsonl")
    if not os.path.exists(path):
        return None
    counts: Dict[str, int] = {}
    finish_reasons: Dict[str, int] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = record.get("meta", {}).get("kind", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
            reason = (record.get("response") or {}).get("finishReason") or "unknown"
            finish_reasons[f"{kind}:{reason}"] = finish_reasons.get(f"{kind}:{reason}", 0) + 1
    result = {"total": sum(counts.values()), "byKind": counts, "byFinishReason": finish_reasons}
    if backend is not None:
        result["httpRetries"] = backend.retried
    return result


def write_results(results: Dict[str, Dict[str, Any]], backend: Optional[GlmBackend] = None) -> None:
    payload = {
        "experiment": "r2-e3-live",
        "scoringVersion": "r2.3+e3-v1",
        "config": {
            "model": MODEL_ID,
            "baseUrl": BASE_URL,
            "temperature": CONFIG["temperature"],
            "summarizerMaxTokens": CONFIG["summarizerMaxTokens"],
            "probeMaxTokens": CONFIG["probeMaxTokens"],
            "promptVersion": "v2",
            "summaryUpdatePolicy": "off",
            "summaryBudgetTokens": 3200,
            "retainedStateAnchor": True,
            "taskStateLedger": "taskState arm (v1 keyword ledger); typed arm (W15 typed protected state)",
            "concurrency": CONFIG["concurrency"],
            "probePlan": {"baseline": 1, "cycles 1-5": 3},
        },
        "results": results,
        "metrics": aggregate(results),
        "budget": count_calls(backend),
    }
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"wrote {os.path.join(HERE, 'results.json')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["run", "aggregate"], default="run")
    parser.add_argument(
        "--arm",
        default="both",
        help="'default', 'taskState', 'typed', or 'both' (default+taskState)",
    )
    parser.add_argument("--tasks", default="A,C,E,H,F", help="comma-separated task ids")
    parser.add_argument("--limit-cycles", type=int, default=None)
    parser.add_argument("--reps", type=int, default=None, help="override reps at every cycle point")
    args = parser.parse_args()

    if args.arm == "both":
        arms = list(ARMS_AVAILABLE)
    else:
        if args.arm not in ARMS_AVAILABLE:
            print(f"unknown arm {args.arm!r}; available: {', '.join(ARMS_AVAILABLE)}", file=sys.stderr)
            return 2
        arms = [args.arm]
    tasks = [t.strip().upper() for t in args.tasks.split(",") if t.strip()]
    for task in tasks:
        if task not in e3_scenarios.TASK_IDS:
            print(f"unknown task {task!r}; available: {', '.join(e3_scenarios.TASK_IDS)}", file=sys.stderr)
            return 2

    results_path = os.path.join(HERE, "results.json")
    existing: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as handle:
            existing = json.load(handle).get("results") or {}

    if args.stage == "aggregate":
        if not existing:
            print("no results to aggregate; run first", file=sys.stderr)
            return 1
        write_results(existing)
        return 0

    plan = dict(PLAN_FULL)
    if args.reps is not None:
        plan = {k: args.reps for k in PLAN_FULL}
    if args.limit_cycles is not None:
        plan = {k: v for k, v in plan.items() if k == "baseline" or (isinstance(k, int) and k <= args.limit_cycles)}

    for task in tasks:
        scenario = e3_scenarios.build_e3_scenario(task)
        scenario["arms"] = arms
        with open(os.path.join(HERE, f"{scenario['name']}.scenario.json"), "w", encoding="utf-8") as handle:
            json.dump(scenario, handle, indent=1)

    api_key, pomerium = load_credentials()
    backend = GlmBackend(os.path.join(HERE, "raw-calls.jsonl"), api_key, pomerium, CONFIG)
    already = _budget_count(backend)
    if already >= BUDGET_MAX_CALLS:
        print(f"budget already exhausted ({already} calls in raw-calls.jsonl >= {BUDGET_MAX_CALLS})", file=sys.stderr)
        backend.close()
        return 1
    pong_check(backend)

    chains = [(task, arm) for task in tasks for arm in arms]
    results = dict(existing)
    lock = threading.Lock()

    def run_chain(chain: Tuple[str, str]) -> None:
        task, arm = chain
        key = f"{task}|{arm}"
        try:
            task_result = run_task(backend, task, arm, plan, limit_cycles=args.limit_cycles)
        except BudgetExceeded as error:
            with lock:
                results[key] = results.get(key) or {
                    "task": task, "scenario": f"r2-e3-task-{task.lower()}", "arm": arm,
                    "cycles": [], "failures": [f"budget: {error}"], "probes": [], "restarts": [],
                    "probeRounds": {},
                }
                results[key]["failures"].append(str(error))
            print(f"[{key}] stopped: {error}", flush=True)
            return
        except Exception as error:  # keep sibling chains running
            with lock:
                results[key] = {
                    "task": task, "scenario": f"r2-e3-task-{task.lower()}", "arm": arm,
                    "cycles": [], "failures": [f"{type(error).__name__}: {error}"], "probes": [],
                    "restarts": [], "probeRounds": {},
                }
            print(f"[{key}] FAILED: {type(error).__name__}: {error}", flush=True)
            return
        with lock:
            results[key] = task_result
            write_results(results, backend)

    workers = min(CONFIG["concurrency"], len(chains))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run_chain, chains))

    write_results(results, backend)
    total = _budget_count(backend)
    print(f"done; total raw calls in log: {total} (budget {BUDGET_MAX_CALLS})", flush=True)
    backend.close()
    if total > BUDGET_MAX_CALLS:
        print("WARNING: budget exceeded", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
