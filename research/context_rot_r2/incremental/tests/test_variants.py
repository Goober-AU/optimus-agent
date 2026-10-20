"""Offline tests for the W9 variant driver (no cargo, no network)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.scenario import ScenarioSpec, build_scenario  # noqa: E402
from research.context_rot_r2.incremental import variants  # noqa: E402


class LateConstraintsTests(unittest.TestCase):
    def test_moves_all_constraints_and_keeps_goal_and_probes(self):
        spec = ScenarioSpec(scenario_id="w9-unit-lc", cycles=8)
        baseline = build_scenario(spec)
        late = variants.late_constraints(spec, 3)
        self.assertEqual(
            late["seedEntries"][0]["text"],
            "TASK GOAL: migrate the billing service to API v2 before Friday.",
        )
        planted = late["cycleEntries"][3][: late["variant"]["movedEntries"]]
        self.assertEqual(len(planted), spec.n_constraints)
        self.assertTrue(all(e["kind"] == "user" for e in planted))
        # Paired comparison: identical probes and identical tags elsewhere.
        self.assertEqual(late["probes"], baseline["probes"])
        self.assertEqual(late["cycleEntries"][3][len(planted):], baseline["cycleEntries"][3])
        self.assertEqual(late["cycleEntries"][0], baseline["cycleEntries"][0])
        # Every constraint anchor is planted verbatim in cycle 3.
        anchors = {p["anchor"] for p in late["probes"] if p["kind"] == "constraint"}
        planted_text = "\n".join(e["text"] for e in planted)
        self.assertTrue(all(a in planted_text for a in anchors))

    def test_rejects_plant_cycle_out_of_range(self):
        spec = ScenarioSpec(scenario_id="w9-unit-lc2", cycles=4)
        with self.assertRaises(ValueError):
            variants.late_constraints(spec, 4)


class LateCorrectionsTests(unittest.TestCase):
    def test_moves_pairs_and_adds_retired_probes(self):
        spec = ScenarioSpec(scenario_id="w9-unit-cr", cycles=8)
        baseline = build_scenario(spec)
        late = variants.late_corrections(spec, 5)
        moved = late["variant"]["movedEntries"]
        self.assertEqual(moved, 2 * spec.n_corrections)
        planted = late["cycleEntries"][5][:moved]
        self.assertEqual([e["kind"] for e in planted], ["assistant", "user"] * spec.n_corrections)
        # Seed entries no longer contain the old decisions or corrections.
        seed_text = "\n".join(e.get("text", "") for e in late["seedEntries"])
        self.assertNotIn("architecture A", seed_text)
        self.assertNotIn("CORRECTION:", seed_text)
        # Retired probes anchor on the old decision tags.
        retired = [p for p in late["probes"] if p["kind"] == "superseded-retired"]
        self.assertEqual(len(retired), spec.n_corrections)
        old_tags = {e["text"].split()[-1] for e in planted if e["kind"] == "assistant"}
        self.assertEqual({p["anchor"] for p in retired}, old_tags)
        # Base probes are untouched.
        self.assertEqual(late["probes"][: len(baseline["probes"])], baseline["probes"])

    def test_with_retired_probes_moves_nothing(self):
        spec = ScenarioSpec(scenario_id="w9-unit-rr", cycles=8)
        baseline = build_scenario(spec)
        probed = variants.with_retired_probes(spec)
        self.assertEqual(probed["seedEntries"], baseline["seedEntries"])
        self.assertEqual(probed["cycleEntries"], baseline["cycleEntries"])
        self.assertEqual(len(probed["probes"]), len(baseline["probes"]) + spec.n_corrections)


class DeterminismTests(unittest.TestCase):
    def test_variants_are_deterministic(self):
        spec = ScenarioSpec(scenario_id="w9-unit-det", cycles=8)
        self.assertEqual(variants.late_constraints(spec, 3), variants.late_constraints(spec, 3))
        self.assertEqual(variants.late_corrections(spec, 5), variants.late_corrections(spec, 5))

    def test_truncated_scenarios_prefix_the_full_scenario(self):
        full = build_scenario(ScenarioSpec(scenario_id="w9-unit-trunc", cycles=8))
        for k in (1, 4, 7):
            short = build_scenario(ScenarioSpec(scenario_id="w9-unit-trunc", cycles=k))
            self.assertEqual(short["seedEntries"], full["seedEntries"])
            self.assertEqual(short["cycleEntries"], full["cycleEntries"][:k])
            self.assertEqual(short["probes"], full["probes"])


if __name__ == "__main__":
    unittest.main()
