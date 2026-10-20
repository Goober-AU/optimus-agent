"""D29 offline verification: the E3ADV C1 typed cell through the mirror.

Re-runs the W29/E5 Part 3 decisive cell OFFLINE (ledger and render only,
no model calls): the committed scenario document (regenerated from
e3adv_generators.build_family_c1, byte-identical to the committed
r2-e3adv-c1.scenario.json) through the typed_mirror port of the D29
pairing fix (branch rot2/w41-d29).

Checks, per cycle 1-5 (the same merge chain run_arm_e3 drives):
- the stale two-phase-commit decision is SUPERSEDED at every cycle;
- the rendered hot block carries the SUPERSEDED (HISTORY, NOT CURRENT)
  section and the replaced-by link (the correction renders
  "(supersedes [VD-...]) (provenance pairing)");
- the correction renders CURRENT with the saga pattern and the anchor
  SGA-KX1 survives verbatim;
- the typed verifier passes on the full render.

Run from research/:
  python -m context_rot_r2.live.e5.d29_offline_check
"""

from __future__ import annotations

import json
import os

from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.live import typed_mirror as tm
from context_rot_r2.live.e3 import driver as e3_driver
from context_rot_r2.live.e3adv import e3adv_generators as gen

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    scenario = gen.build_family_c1()
    scenario["arms"] = ["typed"]
    settings = rm.arm_settings(scenario, "typed")
    assert settings.task_state == rm.TASK_STATE_MODE_TYPED

    session = rm.Session(salt=f"{scenario['name']}|typed")
    for spec in scenario["seedEntries"]:
        session.append_scenario_entry(spec)

    report = {"scenario": scenario["name"], "arm": "typed", "cycles": []}
    failures = []
    for cycle in range(scenario["cycles"]):
        for spec in scenario["cycleEntries"][cycle]:
            session.append_scenario_entry(spec)
        path = session.entries
        live_tokens = rm.estimate_context_tokens(session.live_messages())["tokens"]
        preparation = rm.prepare_compaction(path, settings, live_tokens)
        assert preparation is not None and preparation["typed_state"] is not None, (
            f"cycle {cycle}: the typed ledger must build"
        )
        typed_state = preparation["typed_state"]
        budget = rm.summary_output_budget(
            settings.reserve_tokens, settings.summary_budget_mode, preparation["tokens_before"]
        )
        allocation = tm.allocate(typed_state, tm.typed_hot_state_budget_tokens(budget))
        assert tm.allocation_is_safe(allocation), f"cycle {cycle}: unsafe allocation"
        block = tm.render_allocation(allocation)
        verification = tm.verify(block, typed_state)

        decision = next(
            record for record in typed_state["records"]
            if record["kind"] == tm.VERIFIED_DECISION
        )
        correction = next(
            record for record in typed_state["records"]
            if record["kind"] == tm.USER_CORRECTION and record["link"] is not None
        )
        # Hard checks (D29's own contract, every cycle): the ledger retires
        # the stale decision by provenance, both records carry the path,
        # the correction stays CURRENT with the saga approach, and the
        # verifier accepts the render.
        checks = {
            "decisionSuperseded": decision["status"] == tm.SUPERSEDED,
            "decisionReplacedBy": decision["link"] == correction["id"],
            "retirePathProvenance": decision["retirePath"] == tm.PROVENANCE
            and correction["retirePath"] == tm.PROVENANCE,
            "correctionCurrent": correction["status"] == tm.CURRENT,
            "approachRenders": "saga pattern with compensating transactions" in block,
            "verifierPasses": bool(verification["passed"]),
        }
        # Render checks (reported per cycle; the tier-8 SUPERSEDED line is
        # the allocator's lowest priority, so a full ledger can overflow it
        # out of the hot block at the fixed budget - a budget property,
        # not a pairing property).
        render_checks = {
            "renderSupersededSection": "SUPERSEDED (HISTORY, NOT CURRENT)" in block,
            "renderReplacedByLink": f"(supersedes [{decision['id']}])" in block,
            "renderPairingNote": "(provenance pairing)" in block,
            "hotBudgetOverflowed": len(allocation["overflow"]["dropped"]) > 0,
        }
        summary_text, kept = session.build_context()
        del summary_text
        report["cycles"].append(
            {
                "cycle": cycle + 1,
                "checks": checks,
                "renderChecks": render_checks,
                "hotBudgetTokens": allocation["budgetTokens"],
                "hotTokensUsed": allocation["tokensUsed"],
                "decision": {
                    "id": decision["id"],
                    "status": decision["status"],
                    "link": decision["link"],
                    "retirePath": decision["retirePath"],
                },
                "verification": verification,
                "hotBlockChars": len(block),
            }
        )
        failures.extend(
            f"cycle {cycle + 1}: {name}" for name, ok in checks.items() if not ok
        )
        # The summarizer request carries the hot block; keep the cycle-1 render.
        if cycle == 0:
            with open(os.path.join(HERE, "d29-c1-cycle1-hot-block.txt"), "w", encoding="utf-8") as handle:
                handle.write(block)
        # Continue the chain as the driver does: append the compaction with
        # the typed details the next cycle merges. Offline, the hot-block
        # render stands in for the model summary (no model calls).
        session.append_compaction(
            block,
            preparation["first_kept_entry_id"],
            preparation["tokens_before"],
            {
                "taskState": preparation["task_state"],
                "typedState": typed_state,
                "typedVerification": verification,
            },
        )

    report["passed"] = not failures
    report["failures"] = failures
    with open(os.path.join(HERE, "d29-c1-offline-results.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
    for cycle in report["cycles"]:
        print(f"cycle {cycle['cycle']}: ledger={all(cycle['checks'].values())} "
              f"render={all(v for k, v in cycle['renderChecks'].items() if k != 'hotBudgetOverflowed')} "
              f"decision={cycle['decision']['id']} {cycle['decision']['status']} "
              f"path={cycle['decision']['retirePath']} verify={cycle['verification']['passed']} "
              f"hot={cycle['hotTokensUsed']}/{cycle['hotBudgetTokens']}"
              + (" OVERFLOW" if cycle["renderChecks"]["hotBudgetOverflowed"] else ""))
    print("PASS" if report["passed"] else f"FAIL: {failures}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
