"""R2-E5-RM live cells: the typed arm on the integrated tip, GLM 5.3.

Runs the two previously-failing live cells of R2-E5 against the
tip-ported typed mirror, with this directory's own raw log and results
(the e3/e3adv packages' committed state is untouched):

- TASK-E (evidence family), typed arm: 5 cycles, probes at every cycle
  point (3 reps) + a 1-rep baseline;
- E3ADV C1 (semantic-swap adversarial correction), typed arm: same plan.

Pre-registrations: ../README.md (written before any call). Budget: 120
new wire calls, counted from this directory's append-only raw log.
Endpoint/protocol identical to the committed E5 live runs (temperature 0,
ladder 8192->65536, ANSWER: delimiter probes, PONG first).

Usage (from research/):
  python -m context_rot_r2.live.e5.rm.run_live --cell task-e
  python -m context_rot_r2.live.e5.rm.run_live --cell c1
  python -m context_rot_r2.live.e5.rm.run_live --stage aggregate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from context_rot_r2.live import driver as w7_driver
from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.live.e3 import driver as e3_driver
from context_rot_r2.live.e3 import probe as probe_e3
from context_rot_r2.live.e3 import scenarios as e3_scenarios
from context_rot_r2.live.e3adv import e3adv_generators, e3adv_probe
from context_rot_r2.live.run_r2e2 import BASE_URL, CONFIG, MODEL_ID, GlmBackend, load_credentials, pong_check

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_LOG = os.path.join(HERE, "raw-calls.jsonl")
PLAN = {"baseline": 1, 1: 3, 2: 3, 3: 3, 4: 3, 5: 3}
BUDGET_MAX_CALLS = 120


class BudgetExceeded(RuntimeError):
    pass


def _budget_count() -> int:
    if not os.path.exists(RAW_LOG):
        return 0
    with open(RAW_LOG, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


class RmRunner:
    """Per-(scenario, arm) live runner bound to this directory's raw log."""

    def __init__(self, backend: GlmBackend, scenario_name: str, arm: str, probe_module) -> None:
        self.backend = backend
        self.scenario = scenario_name
        self.arm = arm
        self.probe_module = probe_module

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
        if _budget_count() >= BUDGET_MAX_CALLS:
            raise BudgetExceeded(f"call budget exhausted ({BUDGET_MAX_CALLS})")
        return self._summarizer(system, user, max_tokens, force_fresh=False)

    def summarizer_retry_fn(self, system: str, user: str, max_tokens: float) -> rm.SummarizerCallResult:
        if _budget_count() >= BUDGET_MAX_CALLS:
            raise BudgetExceeded(f"call budget exhausted ({BUDGET_MAX_CALLS})")
        return self._summarizer(system, user, max_tokens, force_fresh=True)

    def probe_call(self, point: str, probe: Dict[str, Any], rep: int,
                   context_messages: List[Dict[str, str]]) -> Dict[str, Any]:
        if _budget_count() >= BUDGET_MAX_CALLS:
            raise BudgetExceeded(f"call budget exhausted ({BUDGET_MAX_CALLS})")
        call_id = f"{self.scenario}|{self.arm}|probe|{point}|{probe['id']}|r{rep}"
        messages = (
            [{"role": "system", "content": self.probe_module.PROBE_SYSTEM_PROMPT}]
            + context_messages
            + [{"role": "user", "content": self.probe_module.ANSWER_PROTOCOL.format(question=probe["question"])}]
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
        record = self.backend.chat(
            call_id, messages, self.backend.cfg["probeMaxTokens"], meta, force_fresh=(rep > 0)
        )
        response = record["response"]
        extracted = self.probe_module.extract_answer(response["content"])
        match = self.probe_module.match_probe(probe, extracted) if extracted is not None else None
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
            rep_records = [self.probe_call(point, probe, rep, context_messages) for rep in range(reps)]
            records.append(
                {
                    "id": probe["id"],
                    "kind": probe["kind"],
                    "question": probe["question"],
                    "gold": probe["gold"],
                    "reps": rep_records,
                    "score": self.probe_module.score_reps(probe, rep_records),
                }
            )
        return records


def _baseline_context(scenario: Dict[str, Any]) -> List[Dict[str, str]]:
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


def run_cell(backend: GlmBackend, cell: str) -> Dict[str, Any]:
    if cell == "task-e":
        scenario = e3_scenarios.build_e3_scenario("E")
        probe_module = probe_e3
    else:
        scenario = e3adv_generators.build_e3adv_scenario("C1")
        probe_module = e3adv_probe
    scenario["arms"] = ["typed"]
    runner = RmRunner(backend, scenario["name"], "typed", probe_module)
    result: Dict[str, Any] = {
        "cell": cell,
        "scenario": scenario["name"],
        "arm": "typed",
        "spec": {"cycles": scenario["cycles"], "probePlan": {str(k): v for k, v in PLAN.items()}},
        "cycles": [],
        "failures": [],
        "probes": [],
        "restarts": [],
        "probeRounds": {},
        "budgetAtStart": _budget_count(),
    }

    context = _baseline_context(scenario)
    result["baseline"] = runner.run_probe_round("baseline", scenario["probes"], context, PLAN["baseline"])
    print(f"[{scenario['name']}|typed] baseline round done (budget {_budget_count()})", flush=True)

    def probe_hook(arm_name: str, summary: Optional[str], kept, cycle: int) -> None:
        point = cycle + 1
        if point not in PLAN:
            return
        context = w7_driver.restored_request_messages(summary, kept)
        result["probeRounds"][str(point)] = runner.run_probe_round(str(point), scenario["probes"], context, PLAN[point])
        correct = sum(1 for r in result["probeRounds"][str(point)] if r["score"]["status"] == "correct")
        print(
            f"[{scenario['name']}|{arm_name}] cycle {point} probes: {correct}/{len(result['probeRounds'][str(point)])} correct "
            f"(budget {_budget_count()})",
            flush=True,
        )

    arm_result = e3_driver.run_arm_e3(
        scenario,
        "typed",
        runner.summarizer_fn,
        prompt_version=rm.COMPACTION_PROMPT_V2,
        wire_max_tokens=CONFIG["summarizerMaxTokens"],
        model=rm.ModelConfig(context_window=1_048_576.0, max_tokens=64_000.0),
        probe_hook=probe_hook,
        limit_cycles=None,
        summarizer_retry=runner.summarizer_retry_fn,
        validation_attempts=CONFIG["summarizerValidationAttempts"],
        restart_after_cycle=scenario.get("restartAfterCycle"),
    )
    result["cycles"] = arm_result.cycles
    result["failures"] = arm_result.failures
    result["probes"] = arm_result.probes
    result["restarts"] = arm_result.restarts
    result["finalSummary"] = arm_result.final_summary
    result["budgetAtEnd"] = _budget_count()
    return result


def summarize_round(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    correct = incorrect = extraction_failures = strict_correct = 0
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


def write_results() -> None:
    payload: Dict[str, Any] = {
        "experiment": "r2-e5-rm",
        "model": MODEL_ID,
        "baseUrl": BASE_URL,
        "config": {
            "temperature": CONFIG["temperature"],
            "summarizerMaxTokens": CONFIG["summarizerMaxTokens"],
            "probeMaxTokens": CONFIG["probeMaxTokens"],
            "promptVersion": "v2",
            "summaryBudgetTokens": 3200,
            "taskStateLedger": "typed (W15 + D22/D23/D24/D25/D27/D29, the integrated tip)",
            "probePlan": {"baseline": 1, "cycles 1-5": 3},
        },
        "cells": {},
        "budget": {"maxCalls": BUDGET_MAX_CALLS, "used": _budget_count()},
    }
    for cell, name in (("task-e", "live-task-e-results.json"), ("c1", "live-c1-results.json")):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            cell_result = json.load(handle)
        metrics: Dict[str, Any] = {
            "cyclesRun": sum(1 for c in cell_result["cycles"] if c.get("ran")),
            "failures": cell_result["failures"],
            "rounds": {},
            "baseline": summarize_round(cell_result["baseline"]) if cell_result.get("baseline") else None,
            "retention": {
                "retained": sum(1 for p in cell_result["probes"] if p["retained"]),
                "total": len(cell_result["probes"]),
            },
        }
        usage_totals = {"promptTokens": 0, "completionTokens": 0, "reasoningTokens": 0}
        for point, records in cell_result.get("probeRounds", {}).items():
            metrics["rounds"][point] = summarize_round(records)
            for record in records:
                for rep in record["reps"]:
                    usage = rep.get("usage") or {}
                    usage_totals["promptTokens"] += usage.get("prompt_tokens") or 0
                    usage_totals["completionTokens"] += usage.get("completion_tokens") or 0
                    usage_totals["reasoningTokens"] += usage.get("reasoning_tokens") or 0
        if cell_result.get("baseline"):
            for record in cell_result["baseline"]:
                for rep in record["reps"]:
                    usage = rep.get("usage") or {}
                    usage_totals["promptTokens"] += usage.get("prompt_tokens") or 0
                    usage_totals["completionTokens"] += usage.get("completion_tokens") or 0
        for cycle in cell_result["cycles"]:
            cycle_usage = cycle.get("summarizerUsage") or {}
            usage_totals["promptTokens"] += cycle_usage.get("promptTokens") or 0
            usage_totals["completionTokens"] += cycle_usage.get("completionTokens") or 0
            usage_totals["reasoningTokens"] += cycle_usage.get("reasoningTokens") or 0
        metrics["usage"] = usage_totals
        payload["cells"][cell] = metrics
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"wrote {os.path.join(HERE, 'results.json')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cell", choices=["task-e", "c1"], required=True)
    parser.add_argument("--stage", choices=["run", "aggregate"], default="run")
    args = parser.parse_args()

    if args.stage == "aggregate":
        write_results()
        return 0

    api_key, pomerium = load_credentials()
    backend = GlmBackend(RAW_LOG, api_key, pomerium, CONFIG)
    already = _budget_count()
    if already >= BUDGET_MAX_CALLS:
        print(f"budget already exhausted ({already} calls >= {BUDGET_MAX_CALLS})", file=sys.stderr)
        backend.close()
        return 1
    pong_check(backend)

    try:
        result = run_cell(backend, args.cell)
    except BudgetExceeded as error:
        print(f"stopped: {error}", file=sys.stderr)
        backend.close()
        return 1
    out_name = "live-task-e-results.json" if args.cell == "task-e" else "live-c1-results.json"
    with open(os.path.join(HERE, out_name), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1)
    write_results()
    total = _budget_count()
    print(f"done; total raw calls in log: {total} (budget {BUDGET_MAX_CALLS})", flush=True)
    backend.close()
    return 0 if total <= BUDGET_MAX_CALLS else 1


if __name__ == "__main__":
    sys.exit(main())
