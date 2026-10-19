import unittest

from research.context_rot import needles
from research.context_rot.tasks import find_gold_leaks, GoldRecord


class ClockMathTest(unittest.TestCase):
    def test_clock_wraps(self):
        self.assertEqual(needles.clock_after("23:40", 30), "00:10")
        self.assertEqual(needles.clock_after("00:00", 0), "00:00")
        self.assertEqual(needles.clock_after("12:00", 90), "13:30")


class NeedleFamilyTest(unittest.TestCase):
    def test_all_families_build_and_are_deterministic(self):
        for kind in needles.NEEDLE_KINDS:
            first = needles.make_needle(kind, 42)
            second = needles.make_needle(kind, 42)
            self.assertEqual(first, second)
            self.assertEqual(first.kind, kind)
            self.assertTrue(first.question)
            self.assertTrue(first.needle_text)
            self.assertTrue(first.evidence_markers)

    def test_gold_is_absent_from_needle_text(self):
        for kind in needles.NEEDLE_KINDS:
            for seed in (1, 7, 99):
                needle = needles.make_needle(kind, seed)
                gold = GoldRecord(answer=needle.answer, answer_kind=needle.answer_kind,
                                  facts=tuple(needle.facts))
                self.assertEqual(find_gold_leaks(needle.needle_text, gold), (),
                                 "%s/%d leaked" % (kind, seed))

    def test_markers_are_present_in_needle_text(self):
        for kind in needles.NEEDLE_KINDS:
            needle = needles.make_needle(kind, 5)
            survived, missing = needle.marker_survival(needle.needle_text)
            self.assertTrue(survived, "%s missing %r" % (kind, missing))

    def test_multi_fact_has_three_derived_facts(self):
        needle = needles.make_needle("multi_fact", 8)
        self.assertEqual(len(needle.facts), 3)
        self.assertEqual(needle.answer, "|".join(needle.facts))
        for fact in needle.facts:
            self.assertNotIn(fact.casefold(), needle.needle_text.casefold())

    def test_visible_params_drop_gold_keys(self):
        needle = needles.make_needle("semantic_paraphrase", 3)
        self.assertIn("end", needle.params)
        self.assertNotIn("end", needle.visible_params)
        for value in needle.visible_params.values():
            self.assertIn(str(value), needle.needle_text)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            needles.make_needle("nope", 1)

    def test_iter_needles_order_stable(self):
        first = [n.kind for n in needles.iter_needles(2, 2)]
        second = [n.kind for n in needles.iter_needles(2, 2)]
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(needles.NEEDLE_KINDS) * 2)
