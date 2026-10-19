"""Execute every W9 adapter run (E-INC-1/2/3 measurements).

Run inventory (15 adapter invocations, each `cargo test --test rot2_multicycle
rot2_production_multicycle -- --ignored` in this worktree with the isolated W9
target dir):

- w9-a-8c / w9-b-8c         : 8-cycle baselines, seeds a/b (E-INC-1 growth+decay;
                             seed a also carries the policy-arm probe arm)
- w9-a-8c-rerun            : identical rerun of the seed-a baseline (determinism)
- w9-a-8c-envpolicy        : seed a with PRIME_AGENT_SUMMARY_UPDATE_POLICY set
                             (demonstrates the adapter path ignores the env var)
- w9-a-<k>c for k=1..7     : cycle-truncated reruns (E-INC-2a path independence)
- w9-{a,b}-late3c          : constraints planted in cycle 3 (E-INC-2b)
- w9-{a,b}-latecorr5c      : supersessions planted in cycle 5 (E-INC-2c)
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from research.context_rot_r2.incremental import driver, variants  # noqa: E402
from research.context_rot_r2.scenario import ScenarioSpec, build_scenario  # noqa: E402

SEED_A = "r2-w9-inc-a"
SEED_B = "r2-w9-inc-b"
CYCLES = 8
BASE_ARMS = ["default", "taskState"]
POLICY_PROBE_ARM = "policy-consolidate-repeated-v1"


def spec(scenario_id: str, cycles: int = CYCLES) -> ScenarioSpec:
    return ScenarioSpec(scenario_id=scenario_id, cycles=cycles)


def build_runs():
    runs = []
    a8 = build_scenario(spec(SEED_A))
    a8["arms"] = BASE_ARMS + [POLICY_PROBE_ARM]
    runs.append(("w9-a-8c", a8, None))

    a8_rerun = build_scenario(spec(SEED_A))
    a8_rerun["arms"] = BASE_ARMS
    runs.append(("w9-a-8c-rerun", a8_rerun, None))

    a8_env = build_scenario(spec(SEED_A))
    a8_env["arms"] = ["default"]
    runs.append(
        (
            "w9-a-8c-envpolicy",
            a8_env,
            {"PRIME_AGENT_SUMMARY_UPDATE_POLICY": "consolidate-repeated-v1"},
        )
    )

    b8 = build_scenario(spec(SEED_B))
    b8["arms"] = BASE_ARMS
    runs.append(("w9-b-8c", b8, None))

    for k in range(1, CYCLES):
        truncated = build_scenario(spec(SEED_A, cycles=k))
        truncated["arms"] = BASE_ARMS
        runs.append((f"w9-a-{k}c", truncated, None))

    for suffix, seed in (("a", SEED_A), ("b", SEED_B)):
        late = variants.late_constraints(spec(seed), 3)
        late["arms"] = BASE_ARMS
        runs.append((f"w9-{suffix}-late3c", late, None))

    early_retired = variants.with_retired_probes(spec(SEED_A))
    early_retired["arms"] = BASE_ARMS
    runs.append(("w9-a-8c-retired", early_retired, None))

    for suffix, seed in (("a", SEED_A), ("b", SEED_B)):
        late_corr = variants.late_corrections(spec(seed), 5)
        late_corr["arms"] = BASE_ARMS
        runs.append((f"w9-{suffix}-latecorr5c", late_corr, None))

    return runs


def main(argv):
    only = set(argv[1:])
    for tag, scenario, env_extra in build_runs():
        if only and tag not in only:
            continue
        print(f"== adapter run {tag} (arms={scenario['arms']}, cycles={scenario['cycles']})", flush=True)
        result = driver.run_adapter(scenario, tag, env_extra=env_extra)
        arms_summary = ", ".join(
            f"{a['arm']}: {sum(1 for c in a['cycles'] if c['ran'])} cycles, "
            f"{sum(1 for p in a['probes'] if p['retained'])}/{len(a['probes'])} retained"
            for a in result["arms"]
        )
        print(f"   {arms_summary}", flush=True)


if __name__ == "__main__":
    main(sys.argv)
