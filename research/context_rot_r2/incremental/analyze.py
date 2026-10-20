"""Analyze W9 adapter runs into per-experiment result JSONs.

Outputs (under results/):
- e-inc1.json : summary_update_policy characterization + per-arm growth/decay
- e-inc2a.json : per-cycle retention curves, determinism, path independence
- e-inc2b.json : late-constraint variant vs baseline (paired anchors)
- e-inc2c.json : late-supersession variant vs baseline (retirement behavior)
- e-inc3.json : per-arm, per-cycle token cost table with the R2-E1 baseline
"""

import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from research.context_rot_r2.incremental import analysis, driver  # noqa: E402

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# Committed R2-E1 baseline (5-cycle multicycle run, scenario r2-e1-multicycle),
# cycle-level values as recorded from
# research/context_rot_r2/runs/r2-e1-multicycle.result.json; the arm means
# match reports/context-rot-r2/EXPERIMENT-LEDGER.md (14 765 / 18 429 chars).
R2E1_BASELINE = {
    "run": "r2-e1-multicycle",
    "cycles": 5,
    "default": {
        "requestChars": [11991, 15086, 15583, 15583, 15583],
        "summaryChars": [739, 1236, 1236, 1236, 1236],
        "ledgerPresent": [False] * 5,
        "contextTokens": [30.0] * 5,
    },
    "taskState": {
        "requestChars": [14364, 19073, 19570, 19570, 19570],
        "summaryChars": [2353, 2850, 2850, 2850, 2850],
        "ledgerPresent": [True] * 5,
        "contextTokens": [30.0] * 5,
    },
}


def write_result(name: str, payload: Dict[str, Any]) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"wrote {path}")


def cycle_fields(cycle: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cycle": cycle["cycle"],
        "ran": cycle["ran"],
        "summarizedEntries": cycle["summarizedEntries"],
        "retainedEntries": cycle["retainedEntries"],
        "summaryChars": cycle["summaryChars"],
        "summarizerRequestChars": cycle["summarizerRequestChars"],
        "compactedContextTokens": cycle["compactedContextTokens"],
        "ledgerPresent": cycle["ledgerPresent"],
        "ledgerItems": cycle["ledgerItems"],
    }


def arm_series(result: Dict[str, Any], arm_name: str) -> Dict[str, Any]:
    arm_result = analysis.arm(result, arm_name)
    if arm_result is None:
        raise KeyError(f"arm {arm_name} missing from {result.get('scenario')}")
    return {
        "cycles": [cycle_fields(c) for c in analysis.ran_cycles(arm_result)],
        "finalRetained": sum(1 for p in arm_result["probes"] if p["retained"]),
        "probes": len(arm_result["probes"]),
    }


def per_seed_arm(tag: str, arm_name: str) -> Dict[str, Any]:
    result = driver.load_result(tag)
    scenario = driver.load_scenario(tag)
    arm_result = analysis.arm(result, arm_name)
    series = arm_series(result, arm_name)
    return {
        "tag": tag,
        "arm": arm_name,
        "series": series,
        "retention": analysis.retention_by_cycle(arm_result, scenario),
        "probeSurvival": analysis.probe_last_cycle_present(arm_result, scenario),
        "growth": analysis.growth_model(analysis.ran_cycles(arm_result)),
    }


# ---------------------------------------------------------------------------
# E-INC-1
# ---------------------------------------------------------------------------

def analyze_e1() -> Dict[str, Any]:
    seed_a = per_seed_arm("w9-a-8c", "default")
    seed_b = per_seed_arm("w9-b-8c", "default")
    seed_a_ts = per_seed_arm("w9-a-8c", "taskState")
    seed_b_ts = per_seed_arm("w9-b-8c", "taskState")

    # Policy-arm probe: the adapter maps every arm other than "taskState" to
    # the default settings, so a policy-named arm must be byte-equivalent.
    result_a = driver.load_result("w9-a-8c")
    default_arm = analysis.arm(result_a, "default")
    policy_arm = analysis.arm(result_a, "policy-consolidate-repeated-v1")
    policy_diffs = driver.compare_cycle_measurements(policy_arm["cycles"], default_arm["cycles"])
    policy_probe_diffs = [
        f"{a['id']}: {a['retained']} vs {b['retained']}"
        for a, b in zip(policy_arm["probes"], default_arm["probes"])
        if a["retained"] != b["retained"]
    ]

    # Env-var probe: setting PRIME_AGENT_SUMMARY_UPDATE_POLICY must not change
    # the adapter output because compact() reads the setting, not the env.
    env_result = driver.load_result("w9-a-8c-envpolicy")
    env_diffs = driver.compare_cycle_measurements(
        analysis.arm(env_result, "default")["cycles"], default_arm["cycles"]
    )

    # Request decomposition per arm (seed a).
    default_cycles = analysis.ran_cycles(default_arm)
    ts_cycles = analysis.ran_cycles(analysis.arm(result_a, "taskState"))
    decomp_default = analysis.request_decomposition(default_cycles)
    decomp_ts = analysis.request_decomposition(ts_cycles)
    ledger_term = [
        ts["summarizerRequestChars"] - d["summarizerRequestChars"]
        for ts, d in zip(ts_cycles, default_cycles)
    ]
    summaries_d = [c["summaryChars"] for c in default_cycles]
    summaries_t = [c["summaryChars"] for c in ts_cycles]
    ledger_render = [ledger_term[0] - 278] + [
        ledger_term[i]
        - (summaries_t[i - 1] - summaries_d[i - 1])
        - 278
        for i in range(1, len(ledger_term))
    ]

    payload = {
        "experiment": "E-INC-1",
        "question": "summary_update_policy arms through the real path: what is "
        "exposed, what each arm sends, and the per-arm summary growth and "
        "retention decay over 8 cycles x 2 seeds.",
        "answer": "Both exposed arms reach a fixed-point summary at cycle 1 "
        "(non-compounding growth; the 400-char carried-summary decay caps the "
        "carry term). Default decays to 0/19 by the second compaction; "
        "taskState holds 14/19 and 13/19 flat across all 8 cycles (the "
        "missing anchors are the known v1 gaps: evidence regex and subagent "
        "custom messages, plus one id-collision loss in seed b). The "
        "summary_update_policy setting itself is not reachable through the "
        "adapter: the only mechanical difference between policies is "
        "+1393 request chars (+348 tokens) per summarizer call after the "
        "first compaction.",
        "policyCharacterization": {
            "exposure": {
                "adapterArms": "arm_settings (rot2_multicycle.rs) maps only "
                "'taskState' to task_state=prompt; every other arm name gets "
                "task_state=off and the default settings",
                "settingsPin": "default_compaction_settings() pins "
                "summary_update_policy=Some(off); compact() reads "
                "settings.summary_update_policy directly (compaction.rs) and "
                "never consults PRIME_AGENT_SUMMARY_UPDATE_POLICY on this path",
                "empiricalPolicyArmProbe": {
                    "arm": "policy-consolidate-repeated-v1",
                    "cycleDiffs": policy_diffs,
                    "probeDiffs": policy_probe_diffs,
                    "identicalToDefault": not policy_diffs and not policy_probe_diffs,
                },
                "empiricalEnvVarProbe": {
                    "tag": "w9-a-8c-envpolicy",
                    "env": {"PRIME_AGENT_SUMMARY_UPDATE_POLICY": "consolidate-repeated-v1"},
                    "cycleDiffs": env_diffs,
                    "identicalToBaselineDefault": not env_diffs,
                },
            },
            "requestComposition": {
                "carriedSummary": "policy-invariant: after the first compaction "
                "every summarizer request carries the FULL previous summary in "
                "<previous-summary> under both policies; the fixture then decays "
                "it to its first 400 chars in the next summary",
                "promptChars": analysis.PROMPT_CHARS,
                "policyRequestDeltaPerCallChars": (
                    analysis.PROMPT_CHARS["consolidating"] - analysis.PROMPT_CHARS["update"]
                ),
                "policyRequestDeltaPerCallTokens": analysis.tokens(
                    analysis.PROMPT_CHARS["consolidating"] - analysis.PROMPT_CHARS["update"]
                ),
                "additivityDefault": decomp_default,
                "additivityTaskState": decomp_ts,
                "ledgerTermChars": ledger_term,
                "ledgerRenderChars": ledger_render,
            },
            "limitation": "the deterministic lossy summarizer follows the request "
            "structure, not the instruction text, so the consolidate policy's "
            "designed effect (instruction-driven consolidation) is a "
            "model-dependent claim and is NOT EXECUTED offline; what is measured "
            "is the mechanical dataflow both policies share",
        },
        "arms": {
            "default": {
                "seeds": {"a": seed_a, "b": seed_b},
                "growthModel": {},
                "decayModel": {},
            },
            "taskState": {
                "seeds": {"a": seed_a_ts, "b": seed_b_ts},
                "growthModel": {},
                "decayModel": {},
            },
        },
    }

    for arm_key, seeds in (
        ("default", {"a": seed_a, "b": seed_b}),
        ("taskState", {"a": seed_a_ts, "b": seed_b_ts}),
    ):
        chars_by_seed = {s: v["growth"]["summaryChars"] for s, v in seeds.items()}
        stabilize = [v["growth"]["stabilizeAfterCycle"] for v in seeds.values()]
        steady = {s: v["growth"]["steady"] for s, v in seeds.items()}
        initial = {s: v["growth"]["initial"] for s, v in seeds.items()}
        final_retention = {
            s: v["retention"][-1]["rate"] for s, v in seeds.items()
        }
        payload["arms"][arm_key]["growthModel"] = {
            "summaryCharsBySeed": chars_by_seed,
            "stabilizeAfterCycle": stabilize,
            "steadyStateChars": steady,
            "initialChars": initial,
            "perCycleGrowthAfterStabilization": 0,
            "carryoverCapChars": analysis.PREVIOUS_SUMMARY_DECAY_KEEP_CHARS,
            "statement": (
                "summary(c) = scaffold + carried-decay(min(summary(c-1), 400 chars)) "
                "+ lossy excerpt of the cycle chunk"
                + (
                    " + verbatim protected ledger copy (taskState)"
                    if arm_key == "taskState"
                    else ""
                )
                + "; the carry term is capped, so growth is non-compounding and the "
                "series reaches its fixed point at cycle 1"
            ),
        }
        survival = {
            s: v["probeSurvival"] for s, v in seeds.items()
        }
        first_loss = {}
        for s, surv in survival.items():
            for probe, last in surv.items():
                first_loss.setdefault(probe, {})[s] = last
        payload["arms"][arm_key]["decayModel"] = {
            "retentionByCycleSeedA": seed_a["retention"] if arm_key == "default" else seed_a_ts["retention"],
            "retentionByCycleSeedB": seed_b["retention"] if arm_key == "default" else seed_b_ts["retention"],
            "finalRetentionRate": final_retention,
            "probeLastCyclePresent": first_loss,
        }
    return payload


# ---------------------------------------------------------------------------
# E-INC-2a
# ---------------------------------------------------------------------------

def analyze_e2a() -> Dict[str, Any]:
    curves = {
        "a": {
            "default": per_seed_arm("w9-a-8c", "default"),
            "taskState": per_seed_arm("w9-a-8c", "taskState"),
        },
        "b": {
            "default": per_seed_arm("w9-b-8c", "default"),
            "taskState": per_seed_arm("w9-b-8c", "taskState"),
        },
    }

    # Determinism: identical rerun of the seed-a 8-cycle scenario.
    base = driver.load_result("w9-a-8c")
    rerun = driver.load_result("w9-a-8c-rerun")
    determinism = {}
    for arm_name in ("default", "taskState"):
        diffs = driver.compare_cycle_measurements(
            analysis.arm(rerun, arm_name)["cycles"], analysis.arm(base, arm_name)["cycles"]
        )
        determinism[arm_name] = {"cycleDiffs": diffs, "identical": not diffs}

    # Path independence: the k-cycle run's final state equals cycle k-1 of the
    # 8-cycle run (same entry history, deterministic pipeline).
    path_independence = {}
    for k in range(1, 8):
        short = driver.load_result(f"w9-a-{k}c")
        for arm_name in ("default", "taskState"):
            short_final = analysis.ran_cycles(analysis.arm(short, arm_name))[-1]
            long_cycle = analysis.ran_cycles(analysis.arm(base, arm_name))[k - 1]
            diffs = driver.compare_cycle_measurements([short_final], [long_cycle])
            path_independence[f"{arm_name}-k{k}"] = {
                "finalCycleEqualsLongRunCycle": not diffs,
                "diffs": diffs,
            }
    return {
        "experiment": "E-INC-2a",
        "question": "per-cycle retention curves from same-scenario reruns at "
        "cycles 1..8; determinism and path independence of the pipeline.",
        "retentionCurves": {
            seed: {
                arm_name: data["retention"]
                for arm_name, data in arms.items()
            }
            for seed, arms in curves.items()
        },
        "probeSurvival": {
            seed: {
                arm_name: data["probeSurvival"]
                for arm_name, data in arms.items()
            }
            for seed, arms in curves.items()
        },
        "determinism": determinism,
        "pathIndependence": path_independence,
    }


# ---------------------------------------------------------------------------
# E-INC-2b
# ---------------------------------------------------------------------------

def analyze_e2b() -> Dict[str, Any]:
    out = {}
    for suffix in ("a", "b"):
        baseline = per_seed_arm(f"w9-{suffix}-8c", "default")
        baseline_ts = per_seed_arm(f"w9-{suffix}-8c", "taskState")
        late = per_seed_arm(f"w9-{suffix}-late3c", "default")
        late_ts = per_seed_arm(f"w9-{suffix}-late3c", "taskState")
        constraint_ids = [
            p["id"]
            for p in driver.load_scenario(f"w9-{suffix}-late3c")["probes"]
            if p["kind"] == "constraint"
        ]
        comparison = {}
        for arm_pair in (("default", baseline, late), ("taskState", baseline_ts, late_ts)):
            arm_name, base_arm, late_arm = arm_pair
            per_probe = {}
            for probe in constraint_ids:
                base_last = base_arm["probeSurvival"].get(probe)
                late_last = late_arm["probeSurvival"].get(probe)
                per_probe[probe] = {
                    "baselineLastCyclePresent": base_last,
                    "lateLastCyclePresent": late_last,
                    "lateSurvivalGainCycles": (
                        None
                        if late_last is None or base_last is None
                        else late_last - base_last
                    ),
                }
            gains = [
                v["lateSurvivalGainCycles"]
                for v in per_probe.values()
                if v["lateSurvivalGainCycles"] is not None
            ]
            comparison[arm_name] = {
                "perProbe": per_probe,
                "meanSurvivalGainCycles": (
                    round(sum(gains) / len(gains), 2) if gains else None
                ),
                "baselineFinalConstraintRetention": base_arm["retention"][-1]["byKind"]["constraint"]["rate"],
                "lateFinalConstraintRetention": late_arm["retention"][-1]["byKind"]["constraint"]["rate"],
                "lateRetentionByCycle": late_arm["retention"],
                "baselineRetentionByCycle": base_arm["retention"],
            }
        out[suffix] = comparison
    return {
        "experiment": "E-INC-2b",
        "question": "does material planted late (cycle 3 instead of the opening "
        "turn) survive better across the remaining compactions?",
        "plantCycle": 3,
        "answer": "No: late constraints survive WORSE (4/8 and 3/8 retained vs "
        "8/8 and 7/8 when the same lines open the session). Two pipeline "
        "mechanisms cause it; both verified against the rendered ledger.",
        "findings": [
            {
                "id": "W9-F1",
                "title": "keyword-less constraint lines are invisible to the "
                "ledger when isolated",
                "evidence": "In the baseline all constraints share ONE user "
                "message with the line 'never drop ...' (SUPERSEDE_RE matches "
                "'drop'), so is_correction=true and every clause of that "
                "message is captured. Planted as separate entries, "
                "'no new dependencies ...', 'preserve ... audit trail "
                "verbatim' and 'stays single-writer' match neither "
                "CONSTRAINT_RE nor SUPERSEDE_RE and are never captured.",
            },
            {
                "id": "W9-F2",
                "title": "retired-id collision silently deletes newly captured "
                "items (auto-renumbered ids are not stable across merges)",
                "evidence": "build_ledger assigns ids by insertion count "
                "(format!(\"C{}\", constraints.len()+1)) and ends with "
                "ledger.constraints.retain(|item| !retired.contains(&item.id)). "
                "At cycle 0 the two correction items retire each other "
                "(mutual retirement, ids C1/C3). At cycle 3 the newly captured "
                "'always run the reporting suite ...' item is numbered C3, "
                "collides with the carried retired set, and is deleted with no "
                "supersession record. Rendered ledger of w9-a-late3c shows "
                "C2, C4, C5, C6 with C3 missing; w9-b-8c taskState loses "
                "q-constraint-3 the same way.",
            },
            {
                "id": "W9-F3",
                "title": "baseline anchors overstate current-state retention: "
                "mutual retirement demotes constraints to history",
                "evidence": "In the baselines the 'never drop ...' and 'always "
                "run ...' correction-clauses retire each other; both anchors "
                "survive only inside SUPERSEDED (history) render lines, not as "
                "ACTIVE CONSTRAINTS. Anchor presence counts them as retained.",
            },
        ],
        "seeds": out,
    }


# ---------------------------------------------------------------------------
# E-INC-2c
# ---------------------------------------------------------------------------

def analyze_e2c() -> Dict[str, Any]:
    early_tag = "w9-a-8c-retired"
    early_scenario = driver.load_scenario(early_tag)
    early_retired_ids = [
        p["id"] for p in early_scenario["probes"] if p["kind"] == "superseded-retired"
    ]
    early_correction_ids = [
        p["id"] for p in early_scenario["probes"] if p["kind"] == "supersession"
    ]
    early_arms = {}
    for arm_name in ("default", "taskState"):
        early = per_seed_arm(early_tag, arm_name)
        survival = early["probeSurvival"]
        early_arms[arm_name] = {
            "newDecisionLastCyclePresent": {
                probe: survival.get(probe) for probe in early_correction_ids
            },
            "retiredDecisionLastCyclePresent": {
                probe: survival.get(probe) for probe in early_retired_ids
            },
            "lingeringRetiredAnchors": {
                probe: last
                for probe, last in (
                    (probe, survival.get(probe)) for probe in early_retired_ids
                )
                if last is not None and last >= 1
            },
            "retentionByCycle": early["retention"],
        }

    out = {}
    for suffix in ("a", "b"):
        late_tag = f"w9-{suffix}-latecorr5c"
        late_scenario = driver.load_scenario(late_tag)
        retired_ids = [p["id"] for p in late_scenario["probes"] if p["kind"] == "superseded-retired"]
        correction_ids = [p["id"] for p in late_scenario["probes"] if p["kind"] == "supersession"]
        arms = {}
        for arm_name in ("default", "taskState"):
            late = per_seed_arm(late_tag, arm_name)
            survival = late["probeSurvival"]
            new_kept = {
                probe: survival.get(probe) for probe in correction_ids
            }
            old_seen = {probe: survival.get(probe) for probe in retired_ids}
            # A correct retirement: the correction anchor is present at the
            # final cycle while the retired anchor is absent from cycle 6 on.
            correct = sum(
                1
                for probe in correction_ids
                if survival.get(probe) == 7
            )
            lingering = {
                probe: last
                for probe, last in old_seen.items()
                if last is not None and last >= 6
            }
            arms[arm_name] = {
                "newDecisionLastCyclePresent": new_kept,
                "retiredDecisionLastCyclePresent": old_seen,
                "newKeptAtFinal": correct,
                "newKeptAtFinalOf": len(correction_ids),
                "lingeringRetiredAnchors": lingering,
                "retentionByCycle": late["retention"],
            }
        out[suffix] = arms
    return {
        "experiment": "E-INC-2c",
        "question": "when the supersession lands late (cycle 5), is the old "
        "decision retired while the new one is kept?",
        "plantCycle": 5,
        "answer": "The new decision is kept in both placements, but the old "
        "decision is NOT retired in either: it stays in DECISIONS (current) "
        "with its anchor at every cycle after planting. The ledger's "
        "supersession misfires the same way early and late.",
        "findings": [
            {
                "id": "W9-F4",
                "title": "supersession retires the corrections, not the old "
                "decision (mutual retirement misfire)",
                "evidence": "The retirement loop scores candidates by shared "
                "specific tokens; the two CORRECTION lines share more tokens "
                "with each other (correction/architecture/module/instead plus "
                "the 'CONSTRAINT'-style scaffold words) than either shares "
                "with the old 'Decision: use architecture A ...' line, so they "
                "retire each other. The rendered ledger lists "
                "'C10 replaced by C8: CORRECTION ...' and 'C8 replaced by "
                "C10: CORRECTION ...' while D5/D6 ('use architecture A ...') "
                "remain under DECISIONS (current).",
            },
            {
                "id": "W9-F5",
                "title": "late supersession costs one extra constraint anchor "
                "vs early (id-renumbering again)",
                "evidence": "w9-a-latecorr5c retains 15/21 vs 16/21 for the "
                "early baseline w9-a-8c-retired; the only difference is "
                "q-constraint-6 ('preserve the auth audit trail verbatim'). "
                "Moving the corrections to cycle 5 shifts the auto-numbered "
                "constraint ids at the merge boundary, and the carried "
                "retired-id set deletes the renumbered item (same mechanism "
                "as W9-F2).",
            },
        ],
        "earlyBaseline": {
            "tag": early_tag,
            "arms": early_arms,
        },
        "seeds": out,
    }


# ---------------------------------------------------------------------------
# E-INC-3
# ---------------------------------------------------------------------------

def analyze_e3() -> Dict[str, Any]:
    runs = [
        ("w9-a-8c", "default"),
        ("w9-a-8c", "taskState"),
        ("w9-b-8c", "default"),
        ("w9-b-8c", "taskState"),
        ("w9-a-late3c", "default"),
        ("w9-a-late3c", "taskState"),
        ("w9-b-late3c", "default"),
        ("w9-b-late3c", "taskState"),
        ("w9-a-latecorr5c", "default"),
        ("w9-a-latecorr5c", "taskState"),
        ("w9-b-latecorr5c", "default"),
        ("w9-b-latecorr5c", "taskState"),
    ]
    rows = []
    means = []
    for tag, arm_name in runs:
        series = arm_series(driver.load_result(tag), arm_name)
        for cycle in series["cycles"]:
            rows.append(
                {
                    "run": tag,
                    "arm": arm_name,
                    "cycle": cycle["cycle"],
                    "requestChars": cycle["summarizerRequestChars"],
                    "requestTokens": analysis.tokens(cycle["summarizerRequestChars"]),
                    "summaryChars": cycle["summaryChars"],
                    "summaryTokens": analysis.tokens(cycle["summaryChars"]),
                    "ledgerPresent": cycle["ledgerPresent"],
                    "ledgerItems": cycle["ledgerItems"],
                    "compactedContextTokens": cycle["compactedContextTokens"],
                }
            )
        cycles = series["cycles"]
        means.append(
            {
                "run": tag,
                "arm": arm_name,
                "cycles": len(cycles),
                "meanRequestChars": round(
                    sum(c["summarizerRequestChars"] for c in cycles) / len(cycles), 1
                ),
                "meanRequestTokens": analysis.tokens(
                    sum(c["summarizerRequestChars"] for c in cycles) / len(cycles)
                ),
                "meanSummaryChars": round(
                    sum(c["summaryChars"] for c in cycles) / len(cycles), 1
                ),
                "meanSummaryTokens": analysis.tokens(
                    sum(c["summaryChars"] for c in cycles) / len(cycles)
                ),
                "ledgerCycles": sum(1 for c in cycles if c["ledgerPresent"]),
                "totalRequestTokens": analysis.tokens(
                    sum(c["summarizerRequestChars"] for c in cycles)
                ),
                "totalSummaryTokens": analysis.tokens(
                    sum(c["summaryChars"] for c in cycles)
                ),
            }
        )
    baseline_rows = []
    for arm_name in ("default", "taskState"):
        data = R2E1_BASELINE[arm_name]
        n = R2E1_BASELINE["cycles"]
        for cycle in range(n):
            baseline_rows.append(
                {
                    "run": R2E1_BASELINE["run"],
                    "arm": arm_name,
                    "cycle": cycle,
                    "requestChars": data["requestChars"][cycle],
                    "requestTokens": analysis.tokens(data["requestChars"][cycle]),
                    "summaryChars": data["summaryChars"][cycle],
                    "summaryTokens": analysis.tokens(data["summaryChars"][cycle]),
                    "ledgerPresent": data["ledgerPresent"][cycle],
                    "ledgerItems": "22" if arm_name == "taskState" else "0",
                    "compactedContextTokens": data["contextTokens"][cycle],
                }
            )
    baseline_means = []
    for arm_name in ("default", "taskState"):
        data = R2E1_BASELINE[arm_name]
        n = R2E1_BASELINE["cycles"]
        baseline_means.append(
            {
                "run": R2E1_BASELINE["run"],
                "arm": arm_name,
                "cycles": n,
                "meanRequestChars": round(sum(data["requestChars"]) / n, 1),
                "meanRequestTokens": analysis.tokens(sum(data["requestChars"]) / n),
                "meanSummaryChars": round(sum(data["summaryChars"]) / n, 1),
                "meanSummaryTokens": analysis.tokens(sum(data["summaryChars"]) / n),
                "ledgerCycles": sum(1 for p in data["ledgerPresent"] if p),
                "totalRequestTokens": analysis.tokens(sum(data["requestChars"])),
                "totalSummaryTokens": analysis.tokens(sum(data["summaryChars"])),
            }
        )
    return {
        "experiment": "E-INC-3",
        "conversion": "chars / 4 = tokens (fixed factor per the W9 brief)",
        "baseline": {
            "source": "committed R2-E1 run (5 cycles); means match "
            "reports/context-rot-r2/EXPERIMENT-LEDGER.md",
            "rows": baseline_rows,
            "means": baseline_means,
        },
        "policyPromptDelta": {
            "charsPerCall": analysis.PROMPT_CHARS["consolidating"]
            - analysis.PROMPT_CHARS["update"],
            "tokensPerCall": analysis.tokens(
                analysis.PROMPT_CHARS["consolidating"] - analysis.PROMPT_CHARS["update"]
            ),
            "appliesTo": "every summarizer call after the first compaction",
        },
        "rows": rows,
        "means": means,
    }


def main():
    write_result("e-inc1.json", analyze_e1())
    write_result("e-inc2a.json", analyze_e2a())
    write_result("e-inc2b.json", analyze_e2b())
    write_result("e-inc2c.json", analyze_e2c())
    write_result("e-inc3.json", analyze_e3())


if __name__ == "__main__":
    main()
