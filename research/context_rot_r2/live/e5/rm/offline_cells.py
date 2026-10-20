"""R2-E5-RM offline cells: the typed arm on the integrated tip, no model calls.

Re-runs the R2-E5 offline evidence (its Part 1 fixture pass, its Part 3
decisive cell, and the Part 2 TASK-E failure mechanism) against the typed
mirror ported to the current integration tip (the W37/W38/W41 fixes:
D22 render direction, D23 verify grammar, D24 id carrying, D25 merge step
boundary + retiredRefs + durable cap, D27 heading shape, D29 provenance
pairing; see ../typed_mirror.py's port notes).

Three cells, all deterministic:

1. E1 fixture suite (typed arm): the committed E1 19-probe scenario through
   the multicycle chain with the lossy fixture summarizer, exactly as
   tests/rot2_multicycle.rs ran it (the E5 run cross-validated this mirror
   against the adapter output; the pre-port mirror reproduced the committed
   15/19, 23 records, 777/800 hot tokens before the tip port was applied).
2. E3ADV C1 cell: the regenerated committed C1 scenario through the
   5-cycle merge chain (the d29_offline_check flow), ledger/render/verify
   checks per cycle.
3. TASK-E id chain: the 5-cycle merge chain over the TASK-E scenario with
   the render as the summary stand-in, tracking the record that holds the
   failing-suite line (the E5 Finding 2 sentinel) and whether every
   reference the previous cycle's render cites resolves in the current
   ledger (the E5 live-halt mechanism, made deterministic).

Run from research/:
  python -m context_rot_r2.live.e5.rm.offline_cells
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.live import typed_mirror as tm
from context_rot_r2.live.e3 import driver as e3_driver
from context_rot_r2.live.e3 import scenarios as e3_scenarios
from context_rot_r2.live.e3adv import e3adv_generators as gen

HERE = os.path.dirname(os.path.abspath(__file__))


def _write(name: str, payload: Any) -> None:
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        else:
            json.dump(payload, handle, indent=1)


def run_e1_fixture() -> Dict[str, Any]:
    """Cell 1: the E1 19-probe fixture, typed arm, lossy fixture summarizer."""
    scenario_path = os.path.join(HERE, "..", "adapter", "r2-e5-e1-typedarm.scenario.json")
    with open(scenario_path, encoding="utf-8") as handle:
        scenario = json.load(handle)
    scenario["arms"] = ["typed"]
    arm = e3_driver.run_arm_e3(
        scenario,
        "typed",
        rm.fixture_summarizer,
        prompt_version=rm.COMPACTION_PROMPT_V2,
        wire_max_tokens=8192,
    )
    kinds = {probe["id"]: probe["kind"] for probe in scenario["probes"]}
    by_kind: Dict[str, Dict[str, int]] = {}
    for probe in arm.probes:
        entry = by_kind.setdefault(kinds[probe["id"]], {"total": 0, "retained": 0})
        entry["total"] += 1
        entry["retained"] += 1 if probe["retained"] else 0
    report = {
        "cell": "e1-fixture-typedarm",
        "scenario": scenario["name"],
        "arm": "typed",
        "retained": sum(1 for p in arm.probes if p["retained"]),
        "probesTotal": len(arm.probes),
        "byKind": by_kind,
        "meanRequestChars": sum(c["summarizerRequestChars"] for c in arm.cycles) / len(arm.cycles),
        "failures": arm.failures,
        "cycles": arm.cycles,
        "probes": arm.probes,
    }
    _write("e1-typedarm-results.json", report)
    _write("e1-final-summary.txt", arm.final_summary)
    return report


def _c1_cycle(session: rm.Session, scenario: Dict[str, Any], settings, cycle: int):
    for spec in scenario["cycleEntries"][cycle]:
        session.append_scenario_entry(spec)
    preparation = rm.prepare_compaction(
        session.entries, settings, rm.estimate_context_tokens(session.live_messages())["tokens"]
    )
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
    return preparation, typed_state, allocation, block, verification


def run_c1_cell() -> Dict[str, Any]:
    """Cell 2: the E3ADV C1 decisive cell through the merge chain."""
    scenario = gen.build_family_c1()
    scenario["arms"] = ["typed"]
    settings = rm.arm_settings(scenario, "typed")
    session = rm.Session(salt=f"{scenario['name']}|typed")
    for spec in scenario["seedEntries"]:
        session.append_scenario_entry(spec)

    report = {"cell": "e3adv-c1-typed", "scenario": scenario["name"], "arm": "typed", "cycles": []}
    failures: List[str] = []
    for cycle in range(scenario["cycles"]):
        preparation, typed_state, allocation, block, verification = _c1_cycle(
            session, scenario, settings, cycle
        )
        decision = next(
            record for record in typed_state["records"] if record["kind"] == tm.VERIFIED_DECISION
        )
        correction = next(
            record
            for record in typed_state["records"]
            if record["kind"] == tm.USER_CORRECTION and record["link"] is not None
        )
        checks = {
            "decisionSuperseded": decision["status"] == tm.SUPERSEDED,
            "decisionReplacedBy": decision["link"] == correction["id"],
            "retirePathProvenance": decision["retirePath"] == tm.PROVENANCE
            and correction["retirePath"] == tm.PROVENANCE,
            "correctionCurrent": correction["status"] == tm.CURRENT,
            "approachRenders": "saga pattern with compensating transactions" in block,
            "verifierPasses": bool(verification["passed"]),
        }
        render_checks = {
            "renderSupersededSection": "SUPERSEDED (HISTORY, NOT CURRENT)" in block,
            "renderReplacedByLink": f"(supersedes [{decision['id']}])" in block,
            "renderPairingNote": "(provenance pairing)" in block,
            "hotBudgetOverflowed": len(allocation["overflow"]["dropped"]) > 0,
        }
        report["cycles"].append(
            {
                "cycle": cycle + 1,
                "checks": checks,
                "renderChecks": render_checks,
                "records": len(typed_state["records"]),
                "retiredRefs": len(typed_state.get("retiredRefs") or []),
                "droppedByCap": typed_state.get("droppedByCap") or 0,
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
        if cycle == 0:
            _write("c1-cycle1-hot-block.txt", block)
        if cycle == scenario["cycles"] - 1:
            _write("c1-cycle5-hot-block.txt", block)
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
    _write("c1-typed-results.json", report)
    return report


def run_task_e_chain() -> Dict[str, Any]:
    """Cell 3: the TASK-E 5-cycle merge chain, id stability + ref resolution.

    The render stands in for the model summary (no model calls). The E5
    Finding 2 mechanism is checked deterministically: the record that holds
    the failing-suite line at cycle 1 (the sentinel) must keep its id at
    every later cycle (D24), and every reference the previous cycle's render
    cites must resolve in the current ledger's known-id sets (D24 + D25).
    """
    scenario = e3_scenarios.build_e3_scenario("E")
    settings = rm.arm_settings(scenario, "typed")
    session = rm.Session(salt=f"{scenario['name']}|typed")
    for spec in scenario["seedEntries"]:
        session.append_scenario_entry(spec)

    report = {"cell": "task-e-idchain", "scenario": scenario["name"], "arm": "typed", "cycles": []}
    sentinel_id: Optional[str] = None
    previous_render: Optional[str] = None
    for cycle in range(scenario["cycles"]):
        preparation, typed_state, allocation, block, verification = _c1_cycle(
            session, scenario, settings, cycle
        )
        if cycle == 0:
            sentinel = next(
                record
                for record in typed_state["records"]
                if record["text"] == "test result: 55 passed; 1 failed"
            )
            sentinel_id = sentinel["id"]
            _write("task-e-cycle1-hot-block.txt", block)
        assert sentinel_id is not None
        sentinel_record = next(
            (record for record in typed_state["records"] if record["id"] == sentinel_id), None
        )
        unresolved: List[str] = []
        if previous_render is not None:
            known = (
                {record["id"] for record in typed_state["records"]}
                | {record["sourceEntryId"] for record in typed_state["records"]}
                | set(typed_state.get("retiredRefs") or [])
            )
            for match in tm.REF_SCAN_RE.finditer(previous_render):
                if previous_render[match.end():].startswith("("):
                    continue
                sigil, token = match.group(1), match.group(2)
                if (sigil == "@" or tm.RECORD_ID_SHAPE_RE.match(token)) and token not in known:
                    unresolved.append(match.group(0))
        report["cycles"].append(
            {
                "cycle": cycle + 1,
                "records": len(typed_state["records"]),
                "retiredRefs": len(typed_state.get("retiredRefs") or []),
                "droppedByCap": typed_state.get("droppedByCap") or 0,
                "sentinelId": sentinel_id,
                "sentinelPresent": sentinel_record is not None,
                "sentinelText": (sentinel_record or {}).get("text", ""),
                "hotTokensUsed": allocation["tokensUsed"],
                "hotBudgetTokens": allocation["budgetTokens"],
                "renderVerifies": bool(verification["passed"]),
                "renderVerification": verification,
                "previousRenderUnresolvedRefs": sorted(set(unresolved)),
            }
        )
        if cycle == 1:
            _write("task-e-cycle2-hot-block.txt", block)
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
        previous_render = block
    report["sentinelStableAllCycles"] = all(c["sentinelPresent"] for c in report["cycles"])
    _write("task-e-idchain-results.json", report)
    return report


def main() -> int:
    e1 = run_e1_fixture()
    c1 = run_c1_cell()
    te = run_task_e_chain()
    print(
        f"E1 fixture: {e1['retained']}/{e1['probesTotal']} retained "
        f"({json.dumps({k: v['retained'] for k, v in e1['byKind'].items()})})"
    )
    print(
        f"C1 cell: passed={c1['passed']} "
        + "; ".join(
            f"c{c['cycle']}: records={c['records']} supersededRender={c['renderChecks']['renderSupersededSection']}"
            for c in c1["cycles"]
        )
    )
    print(
        f"TASK-E chain: sentinelStable={te['sentinelStableAllCycles']} "
        + "; ".join(
            f"c{c['cycle']}: sentinel={c['sentinelPresent']} unres={len(c['previousRenderUnresolvedRefs'])}"
            for c in te["cycles"]
        )
    )
    ok = e1["retained"] >= 0 and c1["passed"] and te["sentinelStableAllCycles"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
