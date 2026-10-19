import json
import unittest

from research.context_rot import stress
from research.context_rot.tasks import (build_context, find_gold_leaks, generate_suite,
                                        InstanceConfig, render_messages, visible_item_dict,
                                        visible_payload)


class StressRegistryTest(unittest.TestCase):
    def test_twenty_categories_A_to_T(self):
        letters = [c.letter for c in stress.CATEGORIES]
        self.assertEqual(letters, list("ABCDEFGHIJKLMNOPQRST"))
        self.assertEqual(len(stress.CATEGORIES), 20)
        for index, category in enumerate(stress.CATEGORIES):
            self.assertTrue(category.key)
            self.assertTrue(category.title)
            self.assertEqual(category.category_id[0], "ABCDEFGHIJKLMNOPQRST"[index])

    def test_keys_and_ids_unique(self):
        keys = [c.key for c in stress.CATEGORIES]
        self.assertEqual(len(keys), len(set(keys)))
        ids = [c.category_id for c in stress.CATEGORIES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_items_are_complete_and_deterministic(self):
        first = stress.build_stress_items(77)
        second = stress.build_stress_items(77)
        self.assertEqual(json.dumps([i.as_dict() for i in first], sort_keys=True),
                         json.dumps([i.as_dict() for i in second], sort_keys=True))
        for item in first:
            self.assertTrue(item.visible.question)
            self.assertTrue(item.visible.needle_text)
            self.assertTrue(item.visible.evidence_markers)
            self.assertIn(item.gold.answer_kind, ("numeric", "time", "code", "multi"))
            self.assertEqual(item.gold.provenance.module, "research.context_rot.stress")
            self.assertTrue(item.gold.provenance.derivation)

    def test_gold_is_not_in_visible_fields(self):
        for item in stress.build_stress_items(13):
            blob = json.dumps({"q": item.visible.question, "n": item.visible.needle_text},
                              sort_keys=True)
            self.assertEqual(find_gold_leaks(blob, item.gold), (), item.item_id)
            visible_blob = json.dumps(visible_item_dict(item.visible), sort_keys=True)
            self.assertEqual(find_gold_leaks(visible_blob, item.gold), (), item.item_id)

    def test_answer_kinds_cover_all_shapes(self):
        kinds = {item.gold.answer_kind for item in stress.build_stress_items(1)}
        self.assertEqual(kinds, {"numeric", "time", "code", "multi"})

    def test_contexts_build_cleanly_for_every_category(self):
        for item in stress.build_stress_items(9):
            for position in ("start", "end"):
                bundle = build_context(item, InstanceConfig(
                    context_target_len=600, evidence_position=position,
                    distractor_kind="contradictory"))
                self.assertEqual(find_gold_leaks(bundle.text, item.gold), ())
                self.assertTrue(bundle.evidence_survived, item.item_id)
                prompt = json.dumps(render_messages(visible_payload(item.visible, bundle.text)))
                self.assertEqual(find_gold_leaks(prompt, item.gold), (), item.item_id)

    def test_describe_categories(self):
        described = stress.describe_categories()
        self.assertEqual(len(described), 20)
        self.assertIn("title", described[0])


class StressSuiteTest(unittest.TestCase):
    def test_suite_items_are_stress_family(self):
        items = generate_suite("stress", 4)
        self.assertEqual(len(items), 20)
        self.assertTrue(all(item.visible.family == "stress" for item in items))
        self.assertEqual(len({item.item_id for item in items}), 20)
