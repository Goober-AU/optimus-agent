import inspect
import json
import unittest

from research.context_rot import tasks
from research.context_rot.needles import NEEDLE_KINDS


def suite_blob(items):
    return json.dumps([item.as_dict() for item in items], sort_keys=True).encode("utf-8")


class DeterminismTest(unittest.TestCase):
    def test_same_seed_byte_identical(self):
        for suite in ("needle-smoke", "needles", "stress", "mixed"):
            first = tasks.generate_suite(suite, 4242)
            second = tasks.generate_suite(suite, 4242)
            self.assertEqual(suite_blob(first), suite_blob(second), suite)

    def test_context_assembly_deterministic(self):
        item = tasks.generate_suite("needle-smoke", 7)[0]
        config = tasks.InstanceConfig(context_target_len=800,
                                      distractor_kind="multiple_related")
        first = tasks.build_context(item, config)
        second = tasks.build_context(item, config)
        self.assertEqual(first.text, second.text)
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_seed_changes_filler(self):
        item_a = tasks.generate_suite("needle-smoke", 7)[0]
        item_b = tasks.generate_suite("needle-smoke", 8)[0]
        config = tasks.InstanceConfig(context_target_len=600)
        self.assertNotEqual(tasks.build_context(item_a, config).text,
                            tasks.build_context(item_b, config).text)


class GoldIsolationTest(unittest.TestCase):
    def test_payload_builder_takes_visible_fields_only(self):
        params = list(inspect.signature(tasks.visible_payload).parameters)
        self.assertEqual(params, ["visible", "context_text"])

    def test_payload_keys_are_the_visible_set(self):
        for item in tasks.generate_suite("mixed", 11):
            payload = tasks.visible_payload(item.visible, item.visible.needle_text)
            self.assertEqual(set(payload), set(tasks.VISIBLE_PAYLOAD_KEYS))

    def test_gold_never_in_rendered_prompt(self):
        for suite in ("needle-smoke", "stress"):
            for item in tasks.generate_suite(suite, 123):
                for position in tasks.POSITIONS:
                    config = tasks.InstanceConfig(context_target_len=700,
                                                  evidence_position=position,
                                                  distractor_kind="contradictory")
                    bundle = tasks.build_context(item, config)
                    prompt = json.dumps(
                        tasks.render_messages(tasks.visible_payload(item.visible, bundle.text)),
                        sort_keys=True)
                    self.assertEqual(tasks.find_gold_leaks(prompt, item.gold), (), item.item_id)
                    self.assertEqual(tasks.find_gold_leaks(bundle.text, item.gold), ())

    def test_leak_scan_is_case_insensitive(self):
        gold = tasks.GoldRecord(answer="leaked-answer-xyz", answer_kind="code")
        self.assertEqual(tasks.find_gold_leaks("nothing here", gold), ())
        self.assertEqual(tasks.find_gold_leaks("prefix Leaked-Answer-XYZ suffix", gold),
                         ("leaked-answer-xyz",))

    def test_assert_gold_absent_raises(self):
        gold = tasks.GoldRecord(answer="23:25", answer_kind="time")
        with self.assertRaises(tasks.GoldLeakError):
            tasks.assert_gold_absent("the hold ends at 23:25", gold)

    def test_clock_literals_do_not_count_as_numeric_leaks(self):
        gold = tasks.GoldRecord(answer="45", answer_kind="numeric")
        self.assertEqual(tasks.find_gold_leaks("log 2026-10-9T21:45:53Z ok", gold), ())
        self.assertEqual(tasks.find_gold_leaks("value 45 stated", gold), ("45",))

    def test_time_gold_leak_is_detected(self):
        gold = tasks.GoldRecord(answer="23:25", answer_kind="time")
        self.assertEqual(tasks.find_gold_leaks("the hold ends at 23:25", gold), ("23:25",))
        self.assertEqual(tasks.find_gold_leaks("the hold ends at 23:35", gold), ())

    def test_boundary_scan_ignores_substrings(self):
        gold = tasks.GoldRecord(answer="48", answer_kind="numeric")
        self.assertEqual(tasks.find_gold_leaks("value 1480 and 2048", gold), ())
        self.assertEqual(tasks.find_gold_leaks("value 48 exactly", gold), ("48",))

    def test_visible_item_has_no_gold_field(self):
        fields = set(tasks.VisibleItem.__dataclass_fields__)
        self.assertNotIn("answer", fields)
        self.assertNotIn("gold", fields)
        for item in tasks.generate_suite("needles", 5):
            blob = json.dumps(tasks.visible_item_dict(item.visible), sort_keys=True)
            self.assertEqual(tasks.find_gold_leaks(blob, item.gold), (), item.item_id)


class TruncationTest(unittest.TestCase):
    def test_truncation_records_accounting(self):
        item = tasks.generate_suite("needle-smoke", 3)[0]
        config = tasks.InstanceConfig(context_target_len=3000, evidence_position="end",
                                      max_prompt_tokens=400, truncate_strategy="head_only")
        bundle = tasks.build_context(item, config)
        self.assertTrue(bundle.truncated)
        self.assertGreater(bundle.tokens_original, bundle.tokens_final)
        self.assertFalse(bundle.evidence_survived)
        self.assertTrue(bundle.missing_markers)
        self.assertTrue(bundle.token_is_estimate)
        self.assertEqual(bundle.truncate_strategy, "head_only")

    def test_no_truncation_flag_when_budget_is_generous(self):
        item = tasks.generate_suite("needle-smoke", 3)[0]
        config = tasks.InstanceConfig(context_target_len=500, max_prompt_tokens=100000)
        bundle = tasks.build_context(item, config)
        self.assertFalse(bundle.truncated)
        self.assertTrue(bundle.evidence_survived)

    def test_truncate_text_helper(self):
        text = "\n".join("line %d %s" % (i, "x" * 40) for i in range(200))
        short, info = tasks.truncate_text(text, 200, "head_tail")
        self.assertTrue(info["truncated"])
        self.assertLess(info["tokens_final"], info["tokens_original"])
        self.assertLessEqual(tasks.estimate_tokens(short), 200)

    def test_unknown_strategy_rejected(self):
        with self.assertRaises(ValueError):
            tasks.truncate_text("abc", 5, "bogus")

    def test_unknown_position_rejected(self):
        with self.assertRaises(ValueError):
            tasks.InstanceConfig(evidence_position="nowhere")


class SuiteShapeTest(unittest.TestCase):
    def test_suite_sizes(self):
        self.assertEqual(len(tasks.generate_suite("needle-smoke", 1)), len(NEEDLE_KINDS))
        self.assertEqual(len(tasks.generate_suite("needles", 1)), 2 * len(NEEDLE_KINDS))
        self.assertEqual(len(tasks.generate_suite("stress", 1)), 20)
        self.assertEqual(len(tasks.generate_suite("mixed", 1)), 20 + len(NEEDLE_KINDS))

    def test_limit_applies(self):
        self.assertEqual(len(tasks.generate_suite("stress", 1, limit=3)), 3)

    def test_unknown_suite_rejected(self):
        with self.assertRaises(ValueError):
            tasks.generate_suite("nope", 1)

    def test_plan_counts_cells(self):
        configs = [tasks.InstanceConfig(context_target_len=500),
                   tasks.InstanceConfig(context_target_len=900, evidence_position="end")]
        plan = tasks.suite_plan("needle-smoke", 1, configs)
        self.assertEqual(plan["cell_count"], 4 * 2)
        self.assertEqual(plan["item_count"], 4)

    def test_item_ids_unique(self):
        for suite in ("needles", "stress", "mixed"):
            ids = [item.item_id for item in tasks.generate_suite(suite, 2)]
            self.assertEqual(len(ids), len(set(ids)))

    def test_gold_fingerprint_matches_answer(self):
        item = tasks.generate_suite("needle-smoke", 1)[0]
        self.assertEqual(item.gold.fingerprint, tasks.gold_fingerprint(item.gold.answer))
        self.assertNotEqual(item.gold.fingerprint, item.gold.answer)
