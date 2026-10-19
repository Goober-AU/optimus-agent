"""Offline tests for the r2 harness (no cargo, no network)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from research.context_rot_r2.scenario import (  # noqa: E402
    ScenarioSpec,
    build_scenario,
    stable_seed,
)


class SeedTests(unittest.TestCase):
    def test_stable_seed_is_not_python_hash(self):
        a = stable_seed("d4-drift", "v1")
        b = stable_seed("d4-drift", "v1")
        self.assertEqual(a, b)
        self.assertNotEqual(a, hash(("d4-drift", "v1")))
        self.assertGreaterEqual(a, 0)

    def test_different_ids_differ(self):
        self.assertNotEqual(stable_seed("a", "v1"), stable_seed("b", "v1"))


class ScenarioTests(unittest.TestCase):
    def test_scenario_is_deterministic(self):
        spec = ScenarioSpec(scenario_id="determinism-check")
        first = build_scenario(spec)
        second = build_scenario(spec)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_scenario_shape(self):
        spec = ScenarioSpec(
            scenario_id="shape-check",
            cycles=5,
            n_constraints=8,
            n_corrections=2,
            n_decisions=4,
            n_evidence=4,
        )
        scenario = build_scenario(spec)
        self.assertEqual(scenario["cycles"], 5)
        self.assertEqual(len(scenario["cycleEntries"]), 5)
        self.assertTrue(scenario["seedEntries"])
        # every probe anchor is unique (M2: unique gold per probe)
        anchors = [p["anchor"] for p in scenario["probes"]]
        self.assertEqual(len(anchors), len(set(anchors)))
        # anchors actually appear in the seed entries
        joined = json.dumps(scenario["seedEntries"])
        for probe in scenario["probes"]:
            self.assertIn(probe["anchor"], joined, probe["id"])
        # correction probes carry explicit supersession language
        corrections = [p for p in scenario["probes"] if p["kind"] == "supersession"]
        self.assertEqual(len(corrections), 2)

    def test_authority_metadata_present(self):
        scenario = build_scenario(ScenarioSpec(scenario_id="authority-check"))
        for probe in scenario["probes"]:
            self.assertIn(probe["authority"], {"user", "assistant", "tool", "child"})


if __name__ == "__main__":
    unittest.main()
