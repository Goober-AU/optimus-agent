"""Cycle runner and evaluation for the W27 live practice run.

Per instance and arm the runner executes CYCLES append+compact cycles
against the compaction-harness binary (live GLM 5.3 summarizer), tracks
the logical kept tail across cycles (a no-fire cycle keeps the previous
tail and grows it by the new turns), extracts the retained references
after every cycle, and assembles a unified-format trajectory record that
the offline-validated W10 harness evaluates (`harness.evaluate_task`).

Wire-call budget: every HTTP request the binary makes is counted from its
JSONL call log; the runner refuses to start a new attempt once the hard
cap is reached and records the stop (never silently).
"""

import json
import os
import statistics
import threading
import time
from typing import Dict, List, Optional, Sequence

from . import (
    ARM_SETTINGS,
    CYCLES,
    LIVE_LABEL,
    WIRE_CALL_HARD_CAP,
)
from . import harness_client
from .extract import refs_step, retained_refs
from .scenario import build_plan

from ..arms import ArmConfig, RUNNABLE
from .. import harness as cb_harness
from .. import gold as cb_gold

# Live practice arm mapping (see __init__.py: the binary cannot express
# W10's offline arm B, so arm B here is the shipped task-state ledger).
LIVE_ARM_A = ArmConfig(
    arm="A",
    name="baseline",
    status=RUNNABLE,
    description=(
        "live practice: production compaction pipeline, default settings, "
        "taskState off (no protected ledger)"
    ),
)
LIVE_ARM_B = ArmConfig(
    arm="B",
    name="task-state-ledger",
    status=RUNNABLE,
    description=(
        "live practice: production compaction pipeline, taskState prompt "
        "(protected task-state ledger on); substitutes W10's offline arm B "
        "(jev-relevance), which the compaction surface cannot express"
    ),
)
LIVE_ARMS = {"A": LIVE_ARM_A, "B": LIVE_ARM_B}

MAX_KEEP_BUMPS = 3  # total attempts per cycle on unusable-handoff errors


class CallBudget:
    """Thread-safe wire-call accounting with a hard cap."""

    def __init__(self, hard_cap: int = WIRE_CALL_HARD_CAP):
        self.hard_cap = hard_cap
        self.used = 0
        self.lock = threading.Lock()
        self.stopped = False

    def take(self, projected: int = 1) -> bool:
        """Reserve room for `projected` calls; False when over cap."""
        with self.lock:
            if self.stopped or self.used + projected > self.hard_cap:
                self.stopped = self.used + projected > self.hard_cap
                return False
            self.used += projected
            return True

    def refund(self, actual: int, projected: int) -> None:
        """Reconcile the reservation with the observed call count."""
        with self.lock:
            self.used += actual - projected


def _atomic_write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def run_pair(
    plan: dict,
    arm: str,
    binary: str,
    run_root: str,
    summarizer: str,
    budget: CallBudget,
    bearer: str,
    pomerium: str,
    timeout_s: float = 2400.0,
) -> dict:
    """Run all cycles for one (instance, arm) pair. Returns the pair result."""
    arm_settings = dict(plan["settings"])
    arm_settings["taskState"] = ARM_SETTINGS[arm]["taskState"]
    session_dir = os.path.join(run_root, "sessions", arm, plan["instance_id"])
    os.makedirs(session_dir, exist_ok=True)

    cycles_out = []
    # logical state across cycles
    appended_turn_ids: List[int] = []
    kept_ids: List[int] = []
    summarized_ids: List[int] = []
    last_summary = ""
    all_call_records: List[dict] = []

    turn_cursor = 0  # next global turn id to append
    stops = []

    for cycle_index in range(CYCLES):
        chunk_turns = []
        if cycle_index == 0:
            chunk_turns.append(plan["turns"][0])  # problem statement, id 0
            turn_cursor = 1
        for read in plan["chunks"][cycle_index]:
            chunk_turns.append({"role": "user", "content": _read_request(read)})
            chunk_turns.append({"role": "assistant", "content": _read_content(read)})
        new_ids = list(range(turn_cursor, turn_cursor + len(chunk_turns)))
        turn_cursor += len(chunk_turns)

        keep = arm_settings["keepRecentTokens"]
        attempts = []
        response = None
        for attempt in range(MAX_KEEP_BUMPS):
            if not budget.take(projected=1):
                stops.append(
                    f"wire-call hard cap reached before cycle {cycle_index + 1} "
                    f"attempt {attempt + 1}"
                )
                break
            request = harness_client.cycle_request(
                session_dir,
                [
                    {"role": t["role"], "content": t["content"], "turnId": tid}
                    for t, tid in zip(chunk_turns, new_ids)
                ]
                if attempt == 0
                else [],
                {**arm_settings, "keepRecentTokens": keep},
                summarizer,
            )
            log_dir = os.path.join(
                run_root, "calls", arm, plan["instance_id"], f"cycle{cycle_index + 1}"
            )
            response, stderr, records = harness_client.run_cycle(
                binary, request, log_dir, bearer, pomerium, timeout_s
            )
            budget.refund(len(records), 1)
            for record in records:
                record["arm"] = arm
                record["instanceId"] = plan["instance_id"]
                record["cycle"] = cycle_index + 1
                record["attempt"] = attempt + 1
            all_call_records.extend(records)
            attempts.append(
                {
                    "keepRecentTokens": keep,
                    "status": response.get("status"),
                    "calls": len(records),
                    "error": response.get("error"),
                    "warnings": response.get("warnings", []),
                }
            )
            ok = response.get("status") == "ok" or response.get("status") == "unsafe"
            error_text = str(response.get("error") or "")
            handoff_failure = (
                not ok
                and ("unusable handoff" in error_text or "compaction failed" in error_text)
            )
            if ok or not handoff_failure or attempt == MAX_KEEP_BUMPS - 1:
                break
            keep = keep * 2  # retry with a larger kept tail, zero new turns
        if response is None:
            stops.append(f"no response for cycle {cycle_index + 1}")
            break

        appended_turn_ids.extend(new_ids)
        status = response.get("status")
        fired = bool(response.get("summarizedTurnIds"))
        if fired:
            summarized_ids = list(
                set(summarized_ids) | set(response.get("summarizedTurnIds") or [])
            )
            kept_ids = list(response.get("keptTurnIds") or [])
            last_summary = response.get("summary") or ""
        else:
            kept_ids = list(set(kept_ids) | set(new_ids))
        ledger = response.get("ledger")
        ledger_text = (
            json.dumps(ledger, sort_keys=True) if ledger not in (None, {}) else ""
        )
        files, spans = retained_refs(
            kept_ids, plan["read_pairs"], last_summary, ledger_text
        )
        cycles_out.append(
            {
                "cycle": cycle_index + 1,
                "status": status,
                "fired": fired,
                "keepRecentTokens": attempts[-1]["keepRecentTokens"] if attempts else None,
                "attempts": attempts,
                "summaryChars": len(last_summary or ""),
                "ledgerPresent": bool(ledger),
                "keptTurnIds": kept_ids,
                "summarizedTurnIds": summarized_ids,
                "retainedFiles": sorted(files),
                "retainedSpans": spans,
                "warnings": response.get("warnings", []),
                "error": response.get("error"),
            }
        )
        if stops:
            break

    result = {
        "instanceId": plan["instance_id"],
        "arm": arm,
        "taskState": arm_settings["taskState"],
        "cycles": cycles_out,
        "calls": all_call_records,
        "stops": stops,
        "completedCycles": len(cycles_out),
    }
    _atomic_write(
        os.path.join(run_root, "pairs", arm, plan["instance_id"] + ".json"), result
    )
    return result


def _read_request(read: dict) -> str:
    return (
        f"Show repository file {read['file']} lines "
        f"{read['start_line']}-{read['end_line']}."
    )


def _read_content(read: dict) -> str:
    lines = read["content"].splitlines()
    numbered = "\n".join(
        f"{read['start_line'] + index:6d}\t{line}"
        for index, line in enumerate(lines)
    )
    return f"[{read['file']}:{read['start_line']}-{read['end_line']}]\n{numbered}"


def pair_to_trajectory(pair: dict) -> dict:
    """Unified-format record from a pair result (pred_steps per cycle)."""
    steps = []
    for cycle in pair["cycles"]:
        files = set(cycle["retainedFiles"])
        spans = [
            {"file": span["file"], "start_line": span["start_line"], "end_line": span["end_line"]}
            for span in cycle["retainedSpans"]
        ]
        steps.append(refs_step(files, spans))
    final = steps[-1] if steps else {"files": [], "spans": {}}
    return {
        "instance_id": pair["instanceId"],
        "traj_data": {
            "pred_steps": steps,
            "pred_files": final["files"],
            "pred_spans": final["spans"],
        },
        "model_patch": "",  # EditLoc uses the gold-patch fallback (documented)
        "repo_url": "",
        "commit": "",
        "tool_calls": 0,  # filled by the caller from the plan
    }


def evaluate_pair(pair: dict, gold: cb_gold.GoldContext, tool_calls: int) -> dict:
    record = pair_to_trajectory(pair)
    record["tool_calls"] = tool_calls
    arm = LIVE_ARMS[pair["arm"]]
    return cb_harness.evaluate_task(arm, gold, record)


def call_stats(call_records: Sequence[dict]) -> dict:
    """Aggregates over the raw call records."""
    if not call_records:
        return {"count": 0}
    latencies = [r.get("latencyMs") for r in call_records if r.get("latencyMs") is not None]
    retries = [r.get("transportRetries") or 0 for r in call_records]
    errors = [r for r in call_records if r.get("error")]
    rungs = [r for r in call_records if (r.get("maxTokens") or 0) > 8192]
    finish = {}
    for record in call_records:
        reason = record.get("finishReason")
        if reason:
            finish[reason] = finish.get(reason, 0) + 1
    return {
        "count": len(call_records),
        "latencyMsMean": round(statistics.fmean(latencies), 1) if latencies else None,
        "latencyMsMedian": round(statistics.median(latencies), 1) if latencies else None,
        "latencyMsMax": max(latencies) if latencies else None,
        "transportRetryEvents": sum(1 for r in retries if r > 0),
        "ladderClimbs": len(rungs),
        "errorCalls": len(errors),
        "finishReasons": finish,
    }
