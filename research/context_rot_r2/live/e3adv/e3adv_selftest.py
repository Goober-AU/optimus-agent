"""Offline self-test for the E3ADV suite (no network, fixture summarizer).

Run from research/:
  python -m unittest -v context_rot_r2.live.e3adv.e3adv_selftest

Covers: scenario determinism and anchor planting; the ADVERSARIAL
PRECONDITION itself (no ledger supersession anywhere: score matrix < 3,
C1 shares zero tokens with the old decision, the stale decision stays
current at every cycle, the rendered ledger carries no SUPERSEDED
section); the action-level matchers (C1 approach, C2 action-number with
digit boundaries and number words, C3 acceptable, negation contexts,
majority scoring); and the W17-E3 cut invariant (no split turns; every
cut lands on the cycle-final user message).
"""

from __future__ import annotations

import json
import unittest
from typing import Any, Dict, List

from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.live.e3 import driver as e3_driver
from context_rot_r2.live.e3adv import e3adv_generators as gen
from context_rot_r2.live.e3adv import e3adv_probe


FIXTURE_SUMMARY = (
    "## Goal\nx\n## Constraints & Preferences\n- y\n## Progress\n### Done\n- [x] a\n\n"
    "### In Progress\n- [ ] b\n\n### Blocked\n- None.\n## Key Decisions\n- **d**: r\n"
    "## Next Steps\n1. n\n## Critical Context\n- c\n"
)


def _correction_clauses(scenario: Dict[str, Any]) -> List[str]:
    """The clauses build_ledger treats as corrections (same scan rules)."""
    clauses: List[str] = []
    for spec in scenario["seedEntries"]:
        if spec["kind"] != "user":
            continue
        text = spec.get("text", "")
        if not rm.SUPERSEDE_RE.search(text):
            continue
        for sentence in rm.sentences(text):
            for clause in rm.split_clauses(sentence):
                if len(clause) < 12:
                    continue
                if not rm.dedupe_key(clause):
                    continue
                clauses.append(clause)
    return clauses


def _cycle1_ledger(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """The ledger the cycle-1 compaction builds (taskState arm)."""
    settings = rm.arm_settings(scenario, "taskState")
    session = rm.Session(salt=f"{scenario['name']}|taskState")
    for spec in scenario["seedEntries"]:
        session.append_scenario_entry(spec)
    for spec in scenario["cycleEntries"][0]:
        session.append_scenario_entry(spec)
    live_tokens = rm.estimate_context_tokens(session.live_messages())["tokens"]
    prep = rm.prepare_compaction(session.entries, settings, live_tokens)
    assert prep is not None and prep["task_state"] is not None
    return prep["task_state"]


def _supersession_scores(ledger: Dict[str, Any], clauses: List[str]) -> List[Dict[str, Any]]:
    """Every (correction clause x ledger item) pair with its score.

    Mirrors the scoring in build_ledger's supersession pass: specific
    anchors (underscore / slash / digit / len > 8) count 3, other shared
    tokens 1; pairing fires at score >= 3.  Self-pairs (the clause's own
    constraint item) are excluded like the real pass excludes them.
    rows carry (score, shared) for assertion messages.
    """
    rows: List[Dict[str, Any]] = []
    correction_ids = {rm.dedupe_key(c) for c in clauses}
    for clause in clauses:
        clause_tokens = rm.word_tokens(clause)
        for is_decision, section in ((True, ledger["decisions"]), (False, ledger["constraints"])):
            for item in section:
                if rm.dedupe_key(item["text"]) in correction_ids and item["text"] == clause:
                    continue  # the correction's own item, skipped by id in the real pass
                item_tokens = rm.word_tokens(item["text"])
                shared = clause_tokens & item_tokens
                specific = sum(
                    1
                    for token in shared
                    if "_" in token or "/" in token or any(c.isdigit() for c in token) or len(token) > 8
                )
                rows.append(
                    {
                        "clause": clause,
                        "itemId": item["id"],
                        "section": "decisions" if is_decision else "constraints",
                        "shared": sorted(shared),
                        "score": specific * 3 + len(shared),
                    }
                )
    return rows


class ScenarioTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        for family in gen.FAMILY_IDS:
            first = gen.build_e3adv_scenario(family)
            second = gen.build_e3adv_scenario(family)
            self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_anchors_planted_exactly_once(self) -> None:
        for family in gen.FAMILY_IDS:
            scenario = gen.build_e3adv_scenario(family)
            for probe in scenario["probes"]:
                texts = [e.get("text", "") for e in scenario["seedEntries"] + scenario["cycleEntries"][0]]
                count = sum(text.count(probe["anchor"]) for text in texts)
                self.assertEqual(count, 1, f"{scenario['name']}: anchor {probe['anchor']} planted {count}x")

    def test_shapes(self) -> None:
        for family in gen.FAMILY_IDS:
            scenario = gen.build_e3adv_scenario(family)
            self.assertEqual(scenario["cycles"], 5)
            self.assertEqual(len(scenario["cycleEntries"]), 5)
            self.assertEqual(scenario["arms"], ["default", "taskState"])
            self.assertIsNone(scenario["restartAfterCycle"])
            for probe in scenario["probes"]:
                self.assertIn(probe["kind"], ("action-approach", "action-number", "action-acceptable"))
                self.assertIn("question", probe)
                self.assertIn("gold", probe)


class AdversarialPreconditionTests(unittest.TestCase):
    """The property this suite exists to test: the ledger cannot pair the
    corrections, so the stale decision stays current (no pairing luck)."""

    def test_no_supersession_anywhere(self) -> None:
        for family in gen.FAMILY_IDS:
            scenario = gen.build_e3adv_scenario(family)
            ledger = _cycle1_ledger(scenario)
            self.assertEqual(ledger["superseded"], [], msg=family)
            self.assertEqual(ledger["retired"], [], msg=family)

    def test_supersession_scores_below_threshold(self) -> None:
        for family in gen.FAMILY_IDS:
            scenario = gen.build_e3adv_scenario(family)
            ledger = _cycle1_ledger(scenario)
            rows = _supersession_scores(ledger, _correction_clauses(scenario))
            for row in rows:
                self.assertLess(
                    row["score"], 3,
                    msg=f"{family}: {row['clause']!r} vs {row['itemId']} shares {row['shared']} (score {row['score']})",
                )

    def test_c1_shares_zero_tokens_with_old_decision(self) -> None:
        scenario = gen.build_family_c1()
        ledger = _cycle1_ledger(scenario)
        decision = next(d["text"] for d in ledger["decisions"])
        self.assertEqual(decision, gen.C1_DECISION_TEXT)
        decision_tokens = rm.word_tokens(decision)
        for clause in _correction_clauses(scenario):
            self.assertEqual(
                decision_tokens & rm.word_tokens(clause), set(),
                msg=f"C1 correction clause {clause!r} shares tokens with the old decision",
            )

    def test_stale_decision_and_correction_in_ledger(self) -> None:
        expectations = {
            "C1": (gen.C1_DECISION_TEXT, gen.C1_CORRECTION_TEXT),
            "C2": (gen.C2_DECISION_TEXT, gen.C2_CORRECTION_TEXT),
            "C3": (gen.C3_DECISION_TEXT, gen.C3_RETRACTION_TEXT),
        }
        for family, (decision_text, correction_text) in expectations.items():
            scenario = gen.build_e3adv_scenario(family)
            ledger = _cycle1_ledger(scenario)
            self.assertIn(decision_text, [d["text"] for d in ledger["decisions"]], msg=family)
            # the correction's first sentence is an ACTIVE CONSTRAINT item
            first_sentence = rm.sentences(correction_text)[0]
            self.assertIn(first_sentence, [c["text"] for c in ledger["constraints"]], msg=family)

    def test_rendered_ledger_asserts_stale_current(self) -> None:
        for family in gen.FAMILY_IDS:
            scenario = gen.build_e3adv_scenario(family)
            ledger = _cycle1_ledger(scenario)
            rendered = rm.render_ledger(ledger)
            self.assertIn("**DECISIONS (current)**", rendered, msg=family)
            self.assertNotIn("**SUPERSEDED", rendered, msg=family)
            for stem in gen.STALE_DECISION_STEMS[family]:
                self.assertIn(stem, rendered, msg=f"{family}: stale stem {stem!r} missing")

    def test_no_supersession_at_every_cycle(self) -> None:
        for family in gen.FAMILY_IDS:
            scenario = gen.build_e3adv_scenario(family)
            settings = rm.arm_settings(scenario, "taskState")
            session = rm.Session(salt=f"{scenario['name']}|taskState")
            for spec in scenario["seedEntries"]:
                session.append_scenario_entry(spec)
            for cycle in range(scenario["cycles"]):
                for spec in scenario["cycleEntries"][cycle]:
                    session.append_scenario_entry(spec)
                live_tokens = rm.estimate_context_tokens(session.live_messages())["tokens"]
                prep = rm.prepare_compaction(session.entries, settings, live_tokens)
                assert prep is not None and prep["task_state"] is not None
                self.assertEqual(prep["task_state"]["superseded"], [], msg=f"{family}/{cycle}")
                stale_text = {
                    "C1": gen.C1_DECISION_TEXT, "C2": gen.C2_DECISION_TEXT, "C3": gen.C3_DECISION_TEXT,
                }[family]
                self.assertIn(
                    stale_text, [d["text"] for d in prep["task_state"]["decisions"]],
                    msg=f"{family}/{cycle}: stale decision not current anymore",
                )
                session.append_compaction(
                    FIXTURE_SUMMARY, prep["first_kept_entry_id"], live_tokens,
                    {"taskState": prep["task_state"]},
                )


class MatcherTests(unittest.TestCase):
    def test_action_approach_c1(self) -> None:
        probe = gen.build_family_c1()["probes"][0]
        cases = [
            ("the saga pattern with compensating transactions", True, True),
            ("saga with compensating transactions on the billing service", True, True),
            ("compensating transactions (the two-phase commit coordinator was rejected)", True, False),
            ("two-phase commit coordinator", False, False),
            ("the 2pc_coordinator.rs patch stays", False, False),
            ("a two-phase commit coordinator; also some saga steps", False, False),
            ("unknown", False, False),
        ]
        for answer, primary, strict in cases:
            match = e3adv_probe.match_probe(probe, answer)
            self.assertEqual(match["primary"], primary, msg=answer)
            self.assertEqual(match["strict"], strict, msg=answer)

    def test_action_number_c2(self) -> None:
        probe = gen.build_family_c2()["probes"][0]
        cases = [
            ("5 attempts", True, True),
            ("5", True, True),
            ("5 attempts with exponential backoff", True, True),
            ("five attempts", True, True),
            ("5 attempts (previously 3)", True, False),
            ("5 attempts, was 3", True, False),
            ("3 attempts", False, False),
            ("3", False, False),
            ("three retries", False, False),
            ("35 attempts", False, False),  # digit boundary: 35 is neither 3 nor 5
            ("unknown", False, False),
        ]
        for answer, primary, strict in cases:
            match = e3adv_probe.match_probe(probe, answer)
            self.assertEqual(match["primary"], primary, msg=answer)
            self.assertEqual(match["strict"], strict, msg=answer)

    def test_action_acceptable_c3(self) -> None:
        probe = gen.build_family_c3()["probes"][0]
        cases = [
            ("CSV export, per-tenant usage metering", True, True),
            ("per-tenant usage metering", True, True),
            ("CSV export and per-tenant usage metering (audit log dropped)", True, False),
            ("the audit log, CSV export, and usage metering", False, False),
            ("audit log and CSV export", False, False),
            ("unknown", False, False),
        ]
        for answer, primary, strict in cases:
            match = e3adv_probe.match_probe(probe, answer)
            self.assertEqual(match["primary"], primary, msg=answer)
            self.assertEqual(match["strict"], strict, msg=answer)

    def test_score_reps_majority(self) -> None:
        probe = gen.build_family_c2()["probes"][0]

        def rep(answer):
            extracted = e3adv_probe.extract_answer(f"ANSWER: {answer}")
            return {
                "extracted": extracted,
                "match": e3adv_probe.match_probe(probe, extracted),
                "extractionFailure": False,
            }

        self.assertEqual(e3adv_probe.score_reps(probe, [rep("5 attempts")] * 3)["status"], "correct")
        mixed = [rep("5 attempts"), rep("3 attempts"), rep("5 attempts")]
        self.assertEqual(e3adv_probe.score_reps(probe, mixed)["status"], "correct")
        self.assertEqual(e3adv_probe.score_reps(probe, [rep("3 attempts")] * 3)["status"], "incorrect")
        no_answer = {"extracted": None, "match": None, "extractionFailure": True}
        self.assertEqual(e3adv_probe.score_reps(probe, [no_answer] * 3)["status"], "extraction_failure")


class CutPointTests(unittest.TestCase):
    def test_no_split_turns(self) -> None:
        for family in gen.FAMILY_IDS:
            scenario = gen.build_e3adv_scenario(family)
            for arm in ("default", "taskState"):
                session = rm.Session(salt=f"{scenario['name']}|{arm}")
                for spec in scenario["seedEntries"]:
                    session.append_scenario_entry(spec)
                settings = rm.arm_settings(scenario, arm)
                for cycle in range(scenario["cycles"]):
                    for spec in scenario["cycleEntries"][cycle]:
                        session.append_scenario_entry(spec)
                    live_tokens = rm.estimate_context_tokens(session.live_messages())["tokens"]
                    prep = rm.prepare_compaction(session.entries, settings, live_tokens)
                    self.assertIsNotNone(prep, msg=f"{family}/{arm}/{cycle}")
                    cut = rm.find_cut_point(
                        session.entries, prep["boundary_start"], len(session.entries),
                        settings.keep_recent_tokens,
                    )
                    self.assertFalse(cut["is_split_turn"], msg=f"{family}/{arm}/{cycle}")
                    first_kept = session.entries[prep["first_kept_entry_index"]]
                    self.assertEqual(first_kept.kind, "user", msg=f"{family}/{arm}/{cycle}")
                    session.append_compaction(
                        FIXTURE_SUMMARY, prep["first_kept_entry_id"], live_tokens,
                        {"taskState": prep["task_state"]},
                    )


class OfflinePipelineTests(unittest.TestCase):
    def test_all_families_offline_pipeline(self) -> None:
        for family in gen.FAMILY_IDS:
            for arm in ("default", "taskState"):
                scenario = gen.build_e3adv_scenario(family)
                result = e3_driver.run_arm_e3(
                    scenario,
                    arm,
                    rm.fixture_summarizer,
                    prompt_version=rm.COMPACTION_PROMPT_V2,
                    wire_max_tokens=8192.0,
                    restart_after_cycle=scenario["restartAfterCycle"],
                )
                self.assertEqual(result.failures, [], msg=f"{family}/{arm}")
                self.assertTrue(all(c["ran"] for c in result.cycles), msg=f"{family}/{arm}")
                self.assertEqual(len(result.cycles), scenario["cycles"], msg=f"{family}/{arm}")


if __name__ == "__main__":
    unittest.main()
