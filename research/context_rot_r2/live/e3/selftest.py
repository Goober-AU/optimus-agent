"""Offline self-test for the E3 suite (no network, fixture summarizer).

Run from research/:
  python -m unittest -v context_rot_r2.live.e3.selftest

Covers: scenario determinism and anchor planting, the action-level matchers
(primary/strict, negation contexts, extraction), the TASK-F restart
round-trip through the persisted session shape, and the typed-arm CLI
stub (must fail loudly with exit code 2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.live.e3 import driver as e3_driver
from context_rot_r2.live.e3 import probe as probe_e3
from context_rot_r2.live.e3 import scenarios as e3_scenarios


class ScenarioTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        for task in e3_scenarios.TASK_IDS:
            first = e3_scenarios.build_e3_scenario(task)
            second = e3_scenarios.build_e3_scenario(task)
            self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_anchors_planted_exactly_once(self) -> None:
        for task in e3_scenarios.TASK_IDS:
            scenario = e3_scenarios.build_e3_scenario(task)
            for probe in scenario["probes"]:
                texts = [e.get("text", "") for e in scenario["seedEntries"] + scenario["cycleEntries"][0]]
                count = sum(text.count(probe["anchor"]) for text in texts)
                self.assertEqual(count, 1, f"{scenario['name']}: anchor {probe['anchor']} planted {count}x")

    def test_shapes(self) -> None:
        for task in e3_scenarios.TASK_IDS:
            scenario = e3_scenarios.build_e3_scenario(task)
            self.assertEqual(scenario["cycles"], 5)
            self.assertEqual(len(scenario["cycleEntries"]), 5)
            self.assertTrue(scenario["probes"])
            for probe in scenario["probes"]:
                self.assertIn(probe["kind"], ("action-file", "action-approach", "action-error", "action-acceptable"))
                self.assertIn("question", probe)
                self.assertIn("gold", probe)


class MatcherTests(unittest.TestCase):
    def test_action_file(self) -> None:
        probe = e3_scenarios.build_e3_scenario("A")["probes"][0]
        cases = [
            ("src/parser.rs", True, True),
            ("src/parser.rs:88", True, True),
            ("src/auth.rs", False, False),
            ("src/auth.rs (the scope check lives there)", False, False),
            ("src/parser.rs, not src/auth.rs", True, False),
            ("src/lexer.rs", False, False),
            ("unknown", False, False),
        ]
        for answer, primary, strict in cases:
            match = probe_e3.match_probe(probe, answer)
            self.assertEqual(match["primary"], primary, msg=answer)
            self.assertEqual(match["strict"], strict, msg=answer)

    def test_action_approach_task_c(self) -> None:
        probe = e3_scenarios.build_e3_scenario("C")["probes"][0]
        cases = [
            ("idempotency keys on the request handler", True, True),
            ("Idempotency keys (the synchronous lock was rejected)", True, False),
            ("synchronous lock on the order table", False, False),
            ("the lock.rs wrapper", False, False),
            ("unknown", False, False),
        ]
        for answer, primary, strict in cases:
            match = probe_e3.match_probe(probe, answer)
            self.assertEqual(match["primary"], primary, msg=answer)
            self.assertEqual(match["strict"], strict, msg=answer)

    def test_action_approach_task_f(self) -> None:
        probe = e3_scenarios.build_e3_scenario("F")["probes"][0]
        cases = [
            ("event-driven sync over the change-data-capture stream", True, True),
            ("CDC stream (nightly reconciliation was rejected)", True, False),
            ("nightly reconciliation job", False, False),
            ("a nightly job is too stale; CDC instead", True, False),
        ]
        for answer, primary, strict in cases:
            match = probe_e3.match_probe(probe, answer)
            self.assertEqual(match["primary"], primary, msg=answer)
            self.assertEqual(match["strict"], strict, msg=answer)

    def test_action_error(self) -> None:
        probe = e3_scenarios.build_e3_scenario("E")["probes"][0]
        cases = [
            ("assertion failed: depth == 4096 (got 3072)", True),
            ("the test panicked: expected depth 4096 but got 3072", True),
            ("assertion failed: depth == 3072 (got 4096)", False),
            ("assertion failed: depth == 4096", False),
            ("assertion failed: depth == 8192 (got 4096)", False),
            ("unknown", False),
        ]
        for answer, expected in cases:
            match = probe_e3.match_probe(probe, answer)
            self.assertEqual(match["primary"], expected, msg=answer)

    def test_action_acceptable_task_h(self) -> None:
        probe = e3_scenarios.build_e3_scenario("H")["probes"][0]
        cases = [
            ("per-fragment caching, incremental materialized views, precomputed per-customer rollups", True, True),
            ("per-fragment caching and materialized views (no blob cache)", True, False),
            ("whole-report blob cache", False, False),
            ("fragment caching, blob cache, rollups", False, False),
            ("unknown", False, False),
        ]
        for answer, primary, strict in cases:
            match = probe_e3.match_probe(probe, answer)
            self.assertEqual(match["primary"], primary, msg=answer)
            self.assertEqual(match["strict"], strict, msg=answer)

    def test_score_reps_majority(self) -> None:
        probe = e3_scenarios.build_e3_scenario("C")["probes"][0]

        def rep(answer):
            extracted = probe_e3.extract_answer(f"ANSWER: {answer}")
            return {"extracted": extracted, "match": probe_e3.match_probe(probe, extracted), "extractionFailure": False}

        self.assertEqual(probe_e3.score_reps(probe, [rep("idempotency keys")] * 3)["status"], "correct")
        mixed = [rep("idempotency keys"), rep("synchronous lock"), rep("idempotency keys")]
        self.assertEqual(probe_e3.score_reps(probe, mixed)["status"], "correct")
        self.assertEqual(probe_e3.score_reps(probe, [rep("synchronous lock")] * 3)["status"], "incorrect")
        no_answer = {"extracted": None, "match": None, "extractionFailure": True}
        self.assertEqual(probe_e3.score_reps(probe, [no_answer] * 3)["status"], "extraction_failure")


class CutPointTests(unittest.TestCase):
    def test_no_split_turns_and_e_boundary(self) -> None:
        for task in e3_scenarios.TASK_IDS:
            scenario = e3_scenarios.build_e3_scenario(task)
            for arm in ("default", "taskState"):
                session = rm.Session(salt=f"{scenario['name']}|{arm}")
                for spec in scenario["seedEntries"]:
                    session.append_scenario_entry(spec)
                settings = rm.arm_settings(scenario, arm)
                for cycle in range(scenario["cycles"]):
                    for spec in scenario["cycleEntries"][cycle]:
                        session.append_scenario_entry(spec)
                    live_tokens = rm.estimate_context_tokens(session.live_messages())["tokens"]
                    preparation = rm.prepare_compaction(session.entries, settings, live_tokens)
                    self.assertIsNotNone(preparation, msg=f"{task}/{arm}/{cycle}")
                    cut = rm.find_cut_point(
                        session.entries, preparation["boundary_start"], len(session.entries),
                        settings.keep_recent_tokens,
                    )
                    self.assertFalse(cut["is_split_turn"], msg=f"{task}/{arm}/{cycle}")
                    first_kept = session.entries[preparation["first_kept_entry_index"]]
                    self.assertEqual(first_kept.kind, "user", msg=f"{task}/{arm}/{cycle}")
                    if task == "E" and cycle == 0:
                        messages = preparation["messages_to_summarize"]
                        self.assertIn("assertion failed: depth == 4096 (got 3072)", messages[-1].text)
                    session.append_compaction(
                        "## Goal\nx\n## Constraints & Preferences\n- y\n## Progress\n### Done\n- [x] a\n\n### In Progress\n- [ ] b\n\n### Blocked\n- None.\n## Key Decisions\n- **d**: r\n## Next Steps\n1. n\n## Critical Context\n- c\n",
                        preparation["first_kept_entry_id"], live_tokens,
                        {"taskState": preparation["task_state"]},
                    )


class RestartTests(unittest.TestCase):
    def test_task_f_restart_round_trip_offline(self) -> None:
        scenario = e3_scenarios.build_e3_scenario("F")
        points: list = []

        def probe_hook(arm, summary, kept, cycle):
            points.append(cycle + 1)

        result = e3_driver.run_arm_e3(
            scenario,
            "taskState",
            rm.fixture_summarizer,
            prompt_version=rm.COMPACTION_PROMPT_LEGACY,
            wire_max_tokens=8192.0,
            probe_hook=probe_hook,
            restart_after_cycle=scenario["restartAfterCycle"],
        )
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.restarts), 1)
        self.assertEqual(result.restarts[0]["afterCycle"], 3)
        self.assertTrue(result.restarts[0]["contextRoundTripIdentical"])
        self.assertTrue(result.restarts[0]["entriesRoundTripped"] > 0)
        self.assertEqual(points, [1, 2, 3, 4, 5])
        self.assertTrue(all(c["ran"] for c in result.cycles))

    def test_all_tasks_offline_pipeline(self) -> None:
        for task in e3_scenarios.TASK_IDS:
            scenario = e3_scenarios.build_e3_scenario(task)
            result = e3_driver.run_arm_e3(
                scenario,
                "default",
                rm.fixture_summarizer,
                prompt_version=rm.COMPACTION_PROMPT_LEGACY,
                wire_max_tokens=8192.0,
                restart_after_cycle=scenario["restartAfterCycle"],
            )
            self.assertEqual(result.failures, [], msg=task)
            self.assertTrue(all(c["ran"] for c in result.cycles), msg=task)


class TypedArmTests(unittest.TestCase):
    def test_typed_arm_runs_offline_pipeline(self) -> None:
        for task in e3_scenarios.TASK_IDS:
            scenario = e3_scenarios.build_e3_scenario(task)
            result = e3_driver.run_arm_e3(
                scenario,
                "typed",
                rm.fixture_summarizer,
                prompt_version=rm.COMPACTION_PROMPT_LEGACY,
                wire_max_tokens=8192.0,
                restart_after_cycle=scenario["restartAfterCycle"],
            )
            self.assertEqual(result.failures, [], msg=task)
            self.assertTrue(all(c["ran"] for c in result.cycles), msg=task)
            for cycle in result.cycles:
                self.assertTrue(cycle.get("typedLedgerPresent"), msg=(task, cycle["cycle"]))
                self.assertTrue(cycle.get("typedVerification", {}).get("passed"), msg=(task, cycle["cycle"]))

    def test_typed_arm_accepted_by_cli(self) -> None:
        # The stub is gone: --arm typed now parses; an unknown task id still
        # exits 2 BEFORE any credential resolution or wire call.
        research = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        proc = subprocess.run(
            [sys.executable, "-m", "context_rot_r2.live.e3.run_e3", "--arm", "typed", "--tasks", "Z"],
            capture_output=True,
            text=True,
            cwd=research,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown task", proc.stderr)


if __name__ == "__main__":
    unittest.main()
