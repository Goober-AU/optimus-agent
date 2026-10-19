"""Fixture-mode validation of the Python pipeline mirror against R2-E1.

Regenerates the exact r2-e1-multicycle scenario (same spec + scenario id,
blake2b-seeded) and runs both arms through the Python mirror with the
deterministic lossy summarizer fixture and the R2-E1 adapter settings
(legacy prompt variant), then compares every recorded cycle field and probe
record against research/context_rot_r2/runs/r2-e1-multicycle.result.json.

A byte-identical match validates:
  - the cut-point / prepare_compaction port,
  - the ledger build + render port,
  - the summarizer request construction (chars) and the lossy fixture port,
  - the append_compaction / restore semantics,
  - the anchor-retention bookkeeping.

Usage:  python -m context_rot_r2.live.validate_fixture  (from research/)
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List

from context_rot_r2.scenario import ScenarioSpec, build_scenario
from context_rot_r2.live import driver
from context_rot_r2.live import rust_mirror as rm

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RUNS = os.path.join(REPO, "research", "context_rot_r2", "runs")


def main() -> int:
    spec = ScenarioSpec(scenario_id="r2-e1-multicycle", cycles=5,
                        arms=("default", "taskState"), n_constraints=8, n_corrections=2,
                        n_decisions=4, n_evidence=4, n_filler_turns=3, filler_size=120)
    scenario = build_scenario(spec)
    stored_scenario = json.load(open(os.path.join(RUNS, "r2-e1-multicycle.scenario.json"), encoding="utf-8"))
    assert scenario == stored_scenario, "scenario regeneration mismatch (M5 regression?)"

    stored = json.load(open(os.path.join(RUNS, "r2-e1-multicycle.result.json"), encoding="utf-8"))

    fields = ["ran", "skipReason", "summarizedEntries", "retainedEntries", "summaryChars",
              "summarizerRequestChars", "compactedContextTokens", "ledgerPresent", "anchorsPresent"]
    mismatches: List[str] = []
    report: Dict[str, Any] = {"scenario": "r2-e1-multicycle", "arms": {}}
    for arm_index, arm in enumerate(["default", "taskState"]):
        result = driver.run_arm(scenario, arm, rm.fixture_summarizer,
                                prompt_version=rm.COMPACTION_PROMPT_LEGACY,
                                wire_max_tokens=64000,
                                model=rm.ModelConfig(context_window=1_000_000.0, max_tokens=64_000.0))
        stored_arm = stored["arms"][arm_index]
        arm_report: Dict[str, Any] = {"cycles": [], "probesMatch": True, "finalSummaryMatch": True}
        assert len(result.cycles) == len(stored_arm["cycles"])
        for mine, theirs in zip(result.cycles, stored_arm["cycles"]):
            for field_name in fields:
                if mine.get(field_name) != theirs.get(field_name):
                    mismatches.append(
                        f"{arm} cycle {mine['cycle']} field {field_name}: "
                        f"mirror={mine.get(field_name)!r} stored={theirs.get(field_name)!r}"
                    )
            # ledgerItems is serialized as a string count in the stored result
            if str(mine.get("ledgerItems")) != str(theirs.get("ledgerItems")):
                mismatches.append(
                    f"{arm} cycle {mine['cycle']} field ledgerItems: "
                    f"mirror={mine.get('ledgerItems')!r} stored={theirs.get('ledgerItems')!r}"
                )
            arm_report["cycles"].append({f: mine.get(f) for f in fields})
        assert len(result.probes) == len(stored_arm["probes"])
        for mine, theirs in zip(result.probes, stored_arm["probes"]):
            if mine != theirs:
                mismatches.append(f"{arm} probe {mine['id']}: mirror={mine} stored={theirs}")
        # Entry-id prefixes are session-file UUIDs (run-specific values);
        # compare with them masked so only content is checked.
        mask = lambda s: re.sub(r"\[[0-9a-f]{8}\]", "[ID]", s)
        if mask(result.final_summary) != mask(stored_arm["finalSummary"]):
            arm_report["finalSummaryMatch"] = False
            mismatches.append(
                f"{arm} final summary mismatch (ids masked): mirror {len(result.final_summary)} chars "
                f"vs stored {len(stored_arm['finalSummary'])} chars"
            )
        report["arms"][arm] = arm_report

    report["mismatches"] = mismatches
    out_path = os.path.join(os.path.dirname(__file__), "fixture-validation.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)

    if mismatches:
        print(f"VALIDATION FAILED: {len(mismatches)} mismatches")
        for m in mismatches[:20]:
            print(" -", m)
        return 1
    print("VALIDATION PASSED: Python mirror reproduces the R2-E1 adapter result exactly")
    print(f"  (scenario regeneration identical; both arms x 5 cycles x all fields; probes; final summaries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
