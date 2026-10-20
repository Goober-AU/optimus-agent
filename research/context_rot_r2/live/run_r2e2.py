"""R2-E2: live-model multicycle experiment (real GLM 5.3 summarizer + prober).

Runs the production-path multicycle design (R2-E1) with the real model:
  - summarizer: the same request the Rust pipeline would send
    (SUMMARIZATION_PROMPT_V2 family + <conversation> chunk + previous
    summary + retained-state excerpt + ledger block for the taskState arm),
    posted to the GLM 5.3 endpoint;
  - prober: after each configured cycle the restored provider context is
    sent with each probe question and the ANSWER: delimiter protocol
    (3 repetitions on final points, 1 on trend points, temperature 0).

Scenarios:
  r2-e2-multicycle: same spec as R2-E1 (19 probes, 5 cycles, 2 arms).
  r2-e2-hard:       n_constraints=20, n_evidence=8, n_corrections=4,
                    filler_size=200 (37 probes).

Usage (from research/):
  python -m context_rot_r2.live.run_r2e2 --stage smoke     # cycle-0 shakedown
  python -m context_rot_r2.live.run_r2e2 --stage base      # full base scenario
  python -m context_rot_r2.live.run_r2e2 --stage hard
  python -m context_rot_r2.live.run_r2e2 --stage aggregate # rebuild results.json

Credential resolvers come from the environment only: R2E2_GLM_RESOLVER and
R2E2_POMERIUM each name a PowerShell helper that writes one token to stdout,
and the driver refuses to run without them instead of defaulting into a
private credential store.

Every model call is appended to raw-calls.jsonl with its request, response,
usage and latency; completed call ids are reused on restart (resume).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from context_rot_r2.live import driver
from context_rot_r2.live import probe_protocol as pp
from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.scenario import ScenarioSpec, build_scenario

HERE = os.path.dirname(os.path.abspath(__file__))

BASE_URL = os.environ.get(
    "R2E2_GLM_BASE",
    "https://a8349d0f-f602-991e-52c5-963eb2f2e949-in-docker-k3.apps.launchpad.nvidia.com/v1",
)
MODEL_ID = "glm-5.3"

CONFIG = {
    # Brief floor: max_tokens >= 4000 everywhere (M1 fix). The summarizer
    # gets 8192 so reasoning tokens cannot crowd out the 3200-token summary
    # budget the Rust formula computes at reserve=4000.
    "summarizerMaxTokens": 8192,
    "probeMaxTokens": 4000,
    "temperature": 0.0,
    "concurrency": 6,
    "requestTimeoutS": 420.0,
    "maxRetries": 5,
    "backoffS": [2, 5, 15, 45, 90],
    "summarizerValidationAttempts": 3,
}

SPEC_BASE = ScenarioSpec(scenario_id="r2-e2-multicycle", cycles=5,
                         arms=("default", "taskState"), n_constraints=8, n_corrections=2,
                         n_decisions=4, n_evidence=4, n_filler_turns=3, filler_size=120)
SPEC_HARD = ScenarioSpec(scenario_id="r2-e2-hard", cycles=5,
                         arms=("default", "taskState"), n_constraints=20, n_corrections=4,
                         n_decisions=4, n_evidence=8, n_filler_turns=3, filler_size=200)

# Probe points (1-indexed cycles) -> repetitions. Final points get 3 reps
# (the protocol's majority vote); trend points get 1 rep at temperature 0.
PLAN_BASE = {"baseline": 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 3}
PLAN_HARD = {2: 1, 4: 1, 5: 3}


# ---------------------------------------------------------------------------
# GLM endpoint backend
# ---------------------------------------------------------------------------


class GlmBackend:
    def __init__(self, jsonl_path: str, api_key: str, pomerium: str, cfg: Dict[str, Any]) -> None:
        self.jsonl_path = jsonl_path
        self.cfg = cfg
        self.api_key = api_key
        self.pomerium = pomerium
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Pomerium-Authorization": pomerium,
            "Content-Type": "application/json",
        }
        self.done: Dict[str, Dict[str, Any]] = {}
        self.by_request: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._fresh_seq = 0
        if os.path.exists(jsonl_path):
            with open(jsonl_path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.done[record["callId"]] = record
                    self.by_request[self._request_key(record)] = record
        self.client = httpx.Client(timeout=cfg["requestTimeoutS"])
        self.executed = 0
        self.retried = 0

    @staticmethod
    def _request_key(record: Dict[str, Any]) -> str:
        request = record.get("request") or {}
        return hashlib.sha1(
            json.dumps(
                {"messages": request.get("messages"), "maxTokens": request.get("maxTokens")},
                sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:16]

    # -- call id allocation -------------------------------------------------

    def next_summarizer_id(self, scenario: str, arm: str) -> str:
        # Content-based ids keep replays aligned with cached records even
        # when the retry ladder changes between runs.
        return f"{scenario}|{arm}|summarizer"

    def next_fresh_id(self, base_id: str) -> str:
        with self._lock:
            self._fresh_seq += 1
            return f"{base_id}|fresh{self._fresh_seq}"

    # -- wire call ----------------------------------------------------------

    def chat(self, call_id: str, messages: List[Dict[str, str]], max_tokens: int, meta: Dict[str, Any],
             force_fresh: bool = False) -> Dict[str, Any]:
        probe_record = {"messages": messages, "maxTokens": max_tokens}
        request_key = hashlib.sha1(
            json.dumps(probe_record, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        if not force_fresh:
            cached = self.by_request.get(request_key)
            if cached is not None:
                return cached
        else:
            call_id = self.next_fresh_id(call_id)
        payload = {
            "model": MODEL_ID,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.cfg["temperature"],
        }
        attempts = 0
        last_error = ""
        while True:
            attempts += 1
            t0 = time.time()
            try:
                resp = self.client.post(f"{BASE_URL}/chat/completions", json=payload, headers=self.headers)
            except (httpx.TransportError, httpx.TimeoutException) as error:
                last_error = f"transport: {type(error).__name__}: {error}"
                resp = None
            latency = time.time() - t0
            if resp is not None and resp.status_code == 200:
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                record = {
                    "callId": call_id,
                    "ts": time.time(),
                    "meta": meta,
                    "request": {"messages": messages, "maxTokens": max_tokens, "temperature": self.cfg["temperature"]},
                    "response": {
                        "content": message.get("content") or "",
                        "reasoningContent": message.get("reasoning_content") or "",
                        "finishReason": choice.get("finish_reason"),
                        "usage": data.get("usage") or {},
                    },
                    "latencyS": round(latency, 3),
                    "attempts": attempts,
                    "httpStatus": 200,
                }
                break
            status = resp.status_code if resp is not None else None
            body = (resp.text[:400] if resp is not None else "")
            last_error = f"http {status}: {body}"
            retryable = status in (429, 500, 502, 503, 504) or resp is None
            if not retryable or attempts > self.cfg["maxRetries"]:
                raise RuntimeError(f"GLM call {call_id} failed after {attempts} attempts: {last_error}")
            if attempts > 1:
                self.retried += 1
            time.sleep(self.cfg["backoffS"][min(attempts - 1, len(self.cfg["backoffS"]) - 1)])
        with self._lock:
            if request_key in self.by_request and not force_fresh:
                return self.by_request[request_key]
            self.done[call_id] = record
            self.by_request[request_key] = record
            self.executed += 1
            with open(self.jsonl_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def close(self) -> None:
        self.client.close()


# ---------------------------------------------------------------------------
# Live summarizer / prober plumbing
# ---------------------------------------------------------------------------


class LiveRunner:
    def __init__(self, backend: GlmBackend, scenario_name: str) -> None:
        self.backend = backend
        self.scenario = scenario_name

    def _summarizer(self, system: str, user: str, max_tokens: float, force_fresh: bool) -> rm.SummarizerCallResult:
        call_id = self.backend.next_summarizer_id(self.scenario, self._arm)
        meta = {"kind": "summarizer", "scenario": self.scenario, "arm": self._arm}
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
        return self._summarizer(system, user, max_tokens, force_fresh=False)

    def summarizer_retry_fn(self, system: str, user: str, max_tokens: float) -> rm.SummarizerCallResult:
        # Validation retries resample: force-fresh bypasses the content cache
        # so a degenerate greedy reasoning path is not replayed verbatim.
        return self._summarizer(system, user, max_tokens, force_fresh=True)

    _arm = "default"

    def set_arm(self, arm: str) -> None:
        self._arm = arm

    def probe_call(self, arm: str, point: str, probe: Dict[str, Any], rep: int,
                   context_messages: List[Dict[str, str]], question: str) -> Dict[str, Any]:
        call_id = f"{self.scenario}|{arm}|probe|{point}|{probe['id']}|r{rep}"
        messages = ([{"role": "system", "content": pp.PROBE_SYSTEM_PROMPT}]
                    + context_messages
                    + [{"role": "user", "content": pp.ANSWER_PROTOCOL.format(question=question)}])
        meta = {
            "kind": "probe",
            "scenario": self.scenario,
            "arm": arm,
            "point": point,
            "probeId": probe["id"],
            "probeKind": probe["kind"],
            "rep": rep,
        }
        record = self.backend.chat(call_id, messages, self.backend.cfg["probeMaxTokens"], meta)
        response = record["response"]
        extracted = pp.extract_answer(response["content"])
        return {
            "callId": call_id,
            "extracted": extracted,
            "match": bool(extracted is not None and pp.match_answer(probe, extracted)),
            "extractionFailure": extracted is None,
            "rawContent": response["content"],
            "finishReason": response["finishReason"],
            "usage": response["usage"],
            "latencyS": record["latencyS"],
        }

    def run_probe_round(self, arm: str, point, probes: Sequence[Dict[str, Any]],
                        questions: Dict[str, str], context_messages: List[Dict[str, str]],
                        reps: int) -> List[Dict[str, Any]]:
        point_key = str(point)
        tasks = [(probe, rep) for probe in probes for rep in range(reps)]
        results: Dict[Tuple[str, int], Dict[str, Any]] = {}

        def work(task: Tuple[Dict[str, Any], int]) -> None:
            probe, rep = task
            results[(probe["id"], rep)] = self.probe_call(arm, point_key, probe, rep, context_messages, questions[probe["id"]])

        with ThreadPoolExecutor(max_workers=self.backend.cfg["concurrency"]) as pool:
            list(pool.map(work, tasks))

        round_records = []
        for probe in probes:
            rep_records = [results[(probe["id"], rep)] for rep in range(reps)]
            score = pp.score_reps(probe, rep_records)
            round_records.append({
                "id": probe["id"],
                "kind": probe["kind"],
                "authority": probe.get("authority", "unknown"),
                "question": questions[probe["id"]],
                "reps": rep_records,
                "score": score,
            })
        return round_records


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_scenario(backend: GlmBackend, spec: ScenarioSpec, plan: Dict[Any, int],
                 out_dir: str, limit_cycles: Optional[int] = None,
                 only_arms: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    scenario = build_scenario(spec)
    scenario_path = os.path.join(out_dir, f"{spec.scenario_id}.scenario.json")
    with open(scenario_path, "w", encoding="utf-8") as handle:
        json.dump(scenario, handle, indent=1)

    questions = {p["id"]: pp.build_question(p, scenario) for p in scenario["probes"]}
    runner = LiveRunner(backend, spec.scenario_id)
    result: Dict[str, Any] = {
        "scenario": spec.scenario_id,
        "spec": {
            "cycles": spec.cycles, "nConstraints": spec.n_constraints, "nCorrections": spec.n_corrections,
            "nDecisions": spec.n_decisions, "nEvidence": spec.n_evidence, "nFillerTurns": spec.n_filler_turns,
            "fillerSize": spec.filler_size, "contextWindow": scenario["contextWindow"],
            "reserveTokens": scenario["reserveTokens"], "keepRecentTokens": scenario["keepRecentTokens"],
        },
        "probePlan": {str(k): v for k, v in plan.items()},
        "arms": {},
    }

    # Baseline round: the uncompacted seeded context (arm-independent).
    if "baseline" in plan and not limit_cycles:
        seed_entries = [rm.Entry(kind=e["kind"], text=e.get("text", ""), usage_input=e.get("usageInput"),
                                 tool_call_id=e.get("toolCallId", ""), tool_name=e.get("toolName", ""),
                                 from_name=e.get("fromName", ""))
                        for e in scenario["seedEntries"]]
        context = driver.restored_request_messages(None, seed_entries)
        result["baseline"] = runner.run_probe_round("baseline", "baseline", scenario["probes"], questions, context, plan["baseline"])
        print(f"[{spec.scenario_id}] baseline round done ({len(result['baseline'])} probes x {plan['baseline']} rep)", flush=True)

    for arm in (only_arms or scenario["arms"]):
        runner.set_arm(arm)
        probe_rounds: Dict[str, List[Dict[str, Any]]] = {}

        def probe_hook(arm_name: str, summary: Optional[str], kept: List[rm.Entry], cycle: int) -> None:
            point = cycle + 1
            if point not in plan:
                return
            if limit_cycles is not None and cycle + 1 > limit_cycles:
                return
            context = driver.restored_request_messages(summary, kept)
            probe_rounds[str(point)] = runner.run_probe_round(arm_name, str(point), scenario["probes"], questions, context, plan[point])
            correct = sum(1 for r in probe_rounds[str(point)] if r["score"]["status"] == "correct")
            print(f"[{spec.scenario_id}|{arm_name}] cycle {point} probes: {correct}/{len(probe_rounds[str(point)])} correct", flush=True)

        arm_result = driver.run_arm(
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
        )
        result["arms"][arm] = {
            "cycles": arm_result.cycles,
            "failures": arm_result.failures,
            "probes": arm_result.probes,
            "finalSummary": arm_result.final_summary,
            "finalContextMessages": arm_result.final_context_messages,
            "probeRounds": probe_rounds,
        }
        ran = sum(1 for c in arm_result.cycles if c.get("ran"))
        print(f"[{spec.scenario_id}|{arm}] arm done: {ran}/{spec.cycles} cycles ran, "
              f"retention {sum(1 for p in arm_result.probes if p['retained'])}/{len(arm_result.probes)}", flush=True)
    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for scenario_name, scenario_result in results.items():
        scenario_metrics: Dict[str, Any] = {"arms": {}}
        baseline = scenario_result.get("baseline")
        if baseline:
            scenario_metrics["baseline"] = summarize_round(baseline)
        for arm, arm_result in scenario_result["arms"].items():
            arm_metrics: Dict[str, Any] = {
                "cyclesRun": sum(1 for c in arm_result["cycles"] if c.get("ran")),
                "retention": {
                    "retained": sum(1 for p in arm_result["probes"] if p["retained"]),
                    "total": len(arm_result["probes"]),
                },
                "retentionByCycle": [
                    {"cycle": c["cycle"], "anchorsPresent": len(c.get("anchorsPresent", []))}
                    for c in arm_result["cycles"] if c.get("ran")
                ],
                "rounds": {},
            }
            usage_totals = {"promptTokens": 0, "completionTokens": 0, "reasoningTokens": 0}
            latency_probe, latency_summ = [], []
            for point, records in arm_result.get("probeRounds", {}).items():
                arm_metrics["rounds"][point] = summarize_round(records)
                for record in records:
                    for rep in record["reps"]:
                        usage = rep.get("usage") or {}
                        usage_totals["promptTokens"] += usage.get("prompt_tokens") or 0
                        usage_totals["completionTokens"] += usage.get("completion_tokens") or 0
                        usage_totals["reasoningTokens"] += usage.get("reasoning_tokens") or 0
                        latency_probe.append(rep.get("latencyS") or 0.0)
            for cycle in arm_result["cycles"]:
                cycle_usage = cycle.get("summarizerUsage") or {}
                usage_totals["promptTokens"] += cycle_usage.get("promptTokens") or 0
                usage_totals["completionTokens"] += cycle_usage.get("completionTokens") or 0
                usage_totals["reasoningTokens"] += cycle_usage.get("reasoningTokens") or 0
            arm_metrics["usage"] = usage_totals
            arm_metrics["latency"] = {
                "probeMeanS": round(statistics.mean(latency_probe), 2) if latency_probe else 0,
                "probeP90S": round(sorted(latency_probe)[int(0.9 * len(latency_probe))], 2) if latency_probe else 0,
            }
            scenario_metrics["arms"][arm] = arm_metrics
        metrics[scenario_name] = scenario_metrics
    return metrics


def summarize_round(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind: Dict[str, Dict[str, int]] = {}
    correct = incorrect = extraction_failures = 0
    for record in records:
        kind = record["kind"]
        bucket = by_kind.setdefault(kind, {"total": 0, "correct": 0, "incorrect": 0, "extractionFailure": 0})
        bucket["total"] += 1
        status = record["score"]["status"]
        if status == "correct":
            correct += 1
            bucket["correct"] += 1
        elif status == "incorrect":
            incorrect += 1
            bucket["incorrect"] += 1
        else:
            extraction_failures += 1
            bucket["extractionFailure"] += 1
    evaluated = correct + incorrect
    return {
        "probes": len(records),
        "correct": correct,
        "incorrect": incorrect,
        "extractionFailures": extraction_failures,
        "accuracyOverEvaluated": round(correct / evaluated, 4) if evaluated else 0.0,
        "accuracyOverAll": round(correct / len(records), 4) if records else 0.0,
        "byKind": by_kind,
    }


def count_calls(backend: GlmBackend) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for record in backend.done.values():
        kind = record.get("meta", {}).get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {"total": sum(counts.values()), "byKind": counts, "retries": backend.retried}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def load_credentials() -> Tuple[str, str]:
    import subprocess

    def resolver_path(env_name: str) -> str:
        """The resolver script path; fails closed when `env_name` is unset."""
        path = os.environ.get(env_name, "").strip()
        if not path:
            raise RuntimeError(
                f"set {env_name} to the credential-resolver PowerShell script "
                "(a helper that writes the token to stdout)"
            )
        return path

    resolver = resolver_path("R2E2_GLM_RESOLVER")
    pomerium = resolver_path("R2E2_POMERIUM")
    key = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", resolver],
                         capture_output=True, text=True, timeout=120).stdout.strip()
    jwt = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", pomerium],
                          capture_output=True, text=True, timeout=120).stdout.strip()
    if not key or not jwt:
        raise RuntimeError("credential resolvers returned empty output")
    return key, jwt


def pong_check(backend: GlmBackend) -> None:
    record = backend.chat("pong", [{"role": "user", "content": "Reply with exactly: PONG"}],
                          512, {"kind": "pong"})
    content = record["response"]["content"]
    assert "PONG" in content, f"pong check failed: {content!r}"
    print(f"pong ok (latency {record['latencyS']}s, usage {record['response']['usage'].get('completion_tokens')} completion tokens)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["smoke", "base", "hard", "aggregate"], required=True)
    parser.add_argument("--limit-cycles", type=int, default=None)
    parser.add_argument("--arms", default=None, help="comma-separated arm subset")
    args = parser.parse_args()

    results_path = os.path.join(HERE, "results.json")
    raw_path = os.path.join(HERE, "raw-calls.jsonl")
    existing = {}
    if os.path.exists(results_path):
        existing = json.load(open(results_path, encoding="utf-8"))

    if args.stage == "aggregate":
        if not os.path.exists(raw_path):
            print("no raw calls to aggregate; run a stage first", file=sys.stderr)
            return 1
        results = {}
        if os.path.exists(results_path):
            existing_all = json.load(open(results_path, encoding="utf-8"))
            results = existing_all.get("results") or {}
        for name in ("r2-e2-multicycle", "r2-e2-hard"):
            scenario_file = os.path.join(HERE, f"{name}.results-partial.json")
            if os.path.exists(scenario_file):
                partial = json.load(open(scenario_file, encoding="utf-8"))
                if name in results:
                    results[name].setdefault("arms", {}).update(partial.get("arms", {}))
                    for key in ("baseline",):
                        if key in partial:
                            results[name][key] = partial[key]
                else:
                    results[name] = partial
        if not results:
            print("no partial results found", file=sys.stderr)
            return 1
        write_results(results, results_path, raw_path)
        return 0

    api_key, pomerium = load_credentials()
    backend = GlmBackend(raw_path, api_key, pomerium, CONFIG)
    pong_check(backend)

    if args.stage == "smoke":
        spec, plan = SPEC_BASE, PLAN_BASE
    elif args.stage == "base":
        spec, plan = SPEC_BASE, PLAN_BASE
    else:
        spec, plan = SPEC_HARD, PLAN_HARD

    arms = args.arms.split(",") if args.arms else None
    scenario_result = run_scenario(backend, spec, plan, HERE, limit_cycles=args.limit_cycles, only_arms=arms)
    print(f"[{spec.scenario_id}] run_scenario finished (arms: {list(scenario_result['arms'])})", flush=True)
    partial_path = os.path.join(HERE, f"{spec.scenario_id}.results-partial.json")
    with open(partial_path, "w", encoding="utf-8") as handle:
        json.dump(scenario_result, handle, indent=1)

    results = existing.get("results") or {}
    if spec.scenario_id in results and arms is not None:
        merged = results[spec.scenario_id]
        merged.setdefault("arms", {}).update(scenario_result.get("arms", {}))
        for key in ("baseline",):
            if key in scenario_result:
                merged[key] = scenario_result[key]
        results[spec.scenario_id] = merged
    else:
        results[spec.scenario_id] = scenario_result
    write_results(results, results_path, raw_path, backend)
    backend.close()
    return 0


def write_results(results: Dict[str, Any], results_path: str, raw_path: str, backend: Optional[GlmBackend] = None) -> None:
    payload = {
        "experiment": "r2-e2-live",
        "scoringVersion": "r2.1+live-v1",
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
            "taskStateLedger": "taskState arm only",
            "concurrency": CONFIG["concurrency"],
        },
        "results": results,
        "metrics": aggregate(results),
        "budget": count_calls(backend) if backend else None,
    }
    with open(results_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"wrote {results_path}")


if __name__ == "__main__":
    sys.exit(main())
