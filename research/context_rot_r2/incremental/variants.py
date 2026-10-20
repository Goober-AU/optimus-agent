"""Scenario variants for E-INC-2: material placement manipulation.

The committed generator (`research/context_rot_r2/scenario.py`) is reused
unchanged; a variant is a deterministic post-processing step on the produced
scenario dict. Probe anchors and golds stay identical to the baseline for the
same `scenario_id`, so baseline vs variant is a paired comparison over the
same anchors, differing only in WHEN the source material enters the session.
"""

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from research.context_rot_r2.scenario import ScenarioSpec, build_scenario  # noqa: E402


def late_constraints(spec: ScenarioSpec, plant_cycle: int) -> Dict[str, Any]:
    """Move every constraint line from the opening turn (cycle 0) into the
    first entries of `plant_cycle`. The opening turn keeps only the goal."""
    if spec.cycles <= plant_cycle:
        raise ValueError("plant_cycle must be a compaction cycle index < spec.cycles")
    scenario = build_scenario(spec)
    opening = scenario["seedEntries"][0]
    lines = opening["text"].split("\n")
    goal, constraints = lines[0], [line for line in lines[1:] if line.strip()]
    if not constraints or not all(line.startswith("CONSTRAINT:") for line in constraints):
        raise AssertionError("opening turn layout changed; update the variant")
    opening["text"] = goal
    planted = [{"kind": "user", "text": line} for line in constraints]
    scenario["cycleEntries"][plant_cycle] = planted + scenario["cycleEntries"][plant_cycle]
    scenario["variant"] = {
        "kind": "late-constraints",
        "plantCycle": plant_cycle,
        "movedEntries": len(planted),
    }
    return scenario


def late_corrections(spec: ScenarioSpec, plant_cycle: int) -> Dict[str, Any]:
    """Move every supersession pair (old assistant decision + user correction)
    from the seed entries into the first entries of `plant_cycle`, and add
    probes for the retired decisions (`q-retired-*`): the old anchor must NOT
    survive as current state once the correction has been summarized."""
    if spec.cycles <= plant_cycle:
        raise ValueError("plant_cycle must be a compaction cycle index < spec.cycles")
    scenario = build_scenario(spec)
    keep: List[Dict[str, Any]] = []
    moved: List[Dict[str, Any]] = []
    old_tags: List[str] = []
    for entry in scenario["seedEntries"]:
        text = entry.get("text", "")
        if (
            entry["kind"] == "assistant"
            and "architecture A" in text
            and entry.get("usageInput") == 20.0
        ):
            moved.append(entry)
            old_tags.append(text.split()[-1])
            continue
        if entry["kind"] == "user" and text.startswith("CORRECTION:"):
            moved.append(entry)
            continue
        keep.append(entry)
    if len(moved) != 2 * spec.n_corrections or len(old_tags) != spec.n_corrections:
        raise AssertionError("correction layout changed; update the variant")
    scenario["seedEntries"] = keep
    scenario["cycleEntries"][plant_cycle] = moved + scenario["cycleEntries"][plant_cycle]
    for index, tag in enumerate(old_tags):
        scenario["probes"].append(
            {
                "id": f"q-retired-{index}",
                "anchor": tag,
                "gold": "retired",
                "kind": "superseded-retired",
                "authority": "assistant",
            }
        )
    scenario["variant"] = {
        "kind": "late-corrections",
        "plantCycle": plant_cycle,
        "movedEntries": len(moved),
        "retiredProbes": [f"q-retired-{i}" for i in range(len(old_tags))],
    }
    return scenario

def with_retired_probes(spec: ScenarioSpec) -> Dict[str, Any]:
    """Baseline scenario (no entry moves) plus q-retired probes, so the old
    decisions' cycle-level presence is measured when the supersession lands
    in the opening seed entries (the early counterpart of late_corrections)."""
    scenario = build_scenario(spec)
    old_tags: List[str] = []
    for entry in scenario["seedEntries"]:
        text = entry.get("text", "")
        if (
            entry["kind"] == "assistant"
            and "architecture A" in text
            and entry.get("usageInput") == 20.0
        ):
            old_tags.append(text.split()[-1])
    if len(old_tags) != spec.n_corrections:
        raise AssertionError("correction layout changed; update the variant")
    for index, tag in enumerate(old_tags):
        scenario["probes"].append(
            {
                "id": f"q-retired-{index}",
                "anchor": tag,
                "gold": "retired",
                "kind": "superseded-retired",
                "authority": "assistant",
            }
        )
    scenario["variant"] = {
        "kind": "retired-probes-only",
        "retiredProbes": [f"q-retired-{i}" for i in range(len(old_tags))],
    }
    return scenario
