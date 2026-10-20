"""Render the W9 summary tables (markdown) from results/*.json."""

import json
import os
import sys
from typing import Any, Dict, List

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def load(name: str) -> Dict[str, Any]:
    with open(os.path.join(RESULTS_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def fmt(value, digits: int = 1):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def inc1_tables() -> List[str]:
    data = load("e-inc1.json")
    lines = []
    lines.append("### E-INC-1: policy exposure and arms\n")
    exposure = data["policyCharacterization"]["exposure"]
    lines.append(
        "- Adapter arms exposed: `default`, `taskState` only "
        f"(policy-arm probe identical to default: {exposure['empiricalPolicyArmProbe']['identicalToDefault']}; "
        f"env-var probe identical to baseline: {exposure['empiricalEnvVarProbe']['identicalToBaselineDefault']})"
    )
    comp = data["policyCharacterization"]["requestComposition"]
    lines.append(
        f"- Policy request delta per summarizer call: {comp['policyRequestDeltaPerCallChars']} chars "
        f"({comp['policyRequestDeltaPerCallTokens']} tokens) after the first compaction"
    )
    lines.append(
        f"- Carried summary term: policy-invariant, decayed to {data['arms']['default']['growthModel']['carryoverCapChars']} chars"
    )
    lines.append("")
    lines.append("| arm | seed | summary chars by cycle | stabilize after | steady chars | final retention |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for arm_key in ("default", "taskState"):
        arm = data["arms"][arm_key]
        for seed, payload in sorted(arm["seeds"].items()):
            growth = payload["growth"]
            decay = arm["decayModel"]["finalRetentionRate"][seed]
            lines.append(
                f"| {arm_key} | {seed} | {growth['summaryChars']} | cycle {growth['stabilizeAfterCycle']} | "
                f"{fmt(growth['steady'])} | {fmt(decay, 3)} |"
            )
    return "\n".join(lines)


def inc2a_tables() -> str:
    data = load("e-inc2a.json")
    lines = ["### E-INC-2a: per-cycle retention curves\n"]
    lines.append(
        f"- Determinism (identical rerun): default={data['determinism']['default']['identical']}, "
        f"taskState={data['determinism']['taskState']['identical']}"
    )
    path_ok = all(
        v["finalCycleEqualsLongRunCycle"] for v in data["pathIndependence"].values()
    )
    lines.append(f"- Path independence (k-cycle final state == cycle k-1 of 8-cycle run): {path_ok}")
    lines.append("")
    lines.append("| seed | arm | retention by cycle (rate) |")
    lines.append("| --- | --- | --- |")
    for seed, arms in data["retentionCurves"].items():
        for arm, rows in arms.items():
            series = " -> ".join(fmt(r["rate"], 2) for r in rows)
            lines.append(f"| {seed} | {arm} | {series} |")
    return "\n".join(lines)


def inc2b_tables() -> str:
    data = load("e-inc2b.json")
    lines = ["### E-INC-2b: late constraints (planted cycle 3)\n"]
    lines.append("| seed | arm | baseline final constraint retention | late final constraint retention | mean survival gain (cycles) |")
    lines.append("| --- | --- | --- | --- | --- |")
    for seed, arms in data["seeds"].items():
        for arm, payload in arms.items():
            lines.append(
                f"| {seed} | {arm} | {fmt(payload['baselineFinalConstraintRetention'], 2)} | "
                f"{fmt(payload['lateFinalConstraintRetention'], 2)} | {fmt(payload['meanSurvivalGainCycles'], 2)} |"
            )
    return "\n".join(lines)


def inc2c_tables() -> str:
    data = load("e-inc2c.json")
    lines = ["### E-INC-2c: late supersession (planted cycle 5)\n"]
    early = data["earlyBaseline"]["arms"]
    early_ts = early["taskState"]
    early_kept = sum(
        1 for last in early_ts["newDecisionLastCyclePresent"].values() if last is not None
    )
    early_of = len(early_ts["newDecisionLastCyclePresent"])
    lines.append(
        f"- Early baseline (seed a, supersession in seed entries): taskState keeps "
        f"{early_kept}/{early_of} corrections at the final cycle, and the retired old "
        f"decisions are still present at cycle {max(early_ts['retiredDecisionLastCyclePresent'].values())}"
    )
    lines.append("")
    lines.append("| seed | arm | new kept at final | lingering retired anchors |")
    lines.append("| --- | --- | --- | --- |")
    for seed, arms in data["seeds"].items():
        for arm, payload in arms.items():
            lingering = payload["lingeringRetiredAnchors"]
            lines.append(
                f"| {seed} | {arm} | {payload['newKeptAtFinal']}/{payload['newKeptAtFinalOf']} | "
                f"{len(lingering)} {sorted(lingering.values()) if lingering else ''} |"
            )
    return "\n".join(lines)


def inc3_tables() -> str:
    data = load("e-inc3.json")
    lines = ["### E-INC-3: token cost (chars / 4)\n"]
    lines.append("| run | arm | cycles | mean req chars | mean req tokens | mean summary chars | mean summary tokens | ledger cycles | total req tokens |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in data["baseline"]["means"] + data["means"]:
        lines.append(
            f"| {row['run']} | {row['arm']} | {row['cycles']} | {fmt(row['meanRequestChars'])} | "
            f"{fmt(row['meanRequestTokens'])} | {fmt(row['meanSummaryChars'])} | {fmt(row['meanSummaryTokens'])} | "
            f"{row['ledgerCycles']} | {fmt(row['totalRequestTokens'])} |"
        )
    return "\n".join(lines)


def main():
    parts = [inc1_tables(), inc2a_tables(), inc2b_tables(), inc2c_tables(), inc3_tables()]
    print("\n\n".join(parts))


if __name__ == "__main__":
    main()
