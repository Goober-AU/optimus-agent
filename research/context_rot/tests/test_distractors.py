import unittest

from research.context_rot import distractors, needles
from research.context_rot.tasks import GoldRecord, find_gold_leaks


class DistractorTest(unittest.TestCase):
    def make(self, kind, needle):
        return distractors.build_distractors(kind, needle.answer_kind,
                                             needle.visible_params, seed=17,
                                             forbidden=(needle.answer,) + tuple(needle.facts))

    def test_every_kind_builds_deterministically(self):
        needle = needles.make_needle("low_overlap_semantic", 4)
        for kind in distractors.DISTRACTOR_KINDS:
            first = self.make(kind, needle)
            second = self.make(kind, needle)
            self.assertEqual([d.text for d in first], [d.text for d in second])
            if kind == "none":
                self.assertEqual(first, [])
            else:
                self.assertGreaterEqual(len(first), 1)

    def test_no_distractor_contains_gold(self):
        for kind in needles.NEEDLE_KINDS:
            needle = needles.make_needle(kind, 9)
            gold = GoldRecord(answer=needle.answer, answer_kind=needle.answer_kind,
                              facts=tuple(needle.facts))
            for distractor_kind in distractors.DISTRACTOR_KINDS:
                for distractor in self.make(distractor_kind, needle):
                    self.assertEqual(find_gold_leaks(distractor.text, gold), (),
                                     "%s/%s leaked" % (kind, distractor_kind))

    def test_contradictory_is_flagged_and_differs(self):
        needle = needles.make_needle("semantic_paraphrase", 6)
        blocks = self.make("contradictory", needle)
        self.assertTrue(all(b.contradicts for b in blocks))
        self.assertNotIn(needle.answer, blocks[0].text)

    def test_unknown_kind_rejected(self):
        needle = needles.make_needle("exact_lexical", 1)
        with self.assertRaises(ValueError):
            distractors.build_distractors("bogus", needle.answer_kind,
                                          needle.visible_params, seed=1)

    def test_meta_is_json_safe(self):
        needle = needles.make_needle("exact_lexical", 1)
        for block in self.make("multiple_related", needle):
            self.assertIn("kind", block.as_dict())
