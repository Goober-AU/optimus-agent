import json
import unittest

from research.context_rot import corpus


class TokenEstimateTest(unittest.TestCase):
    def test_estimate_is_flagged_and_documented(self):
        detail = corpus.estimate_tokens_detailed("hello world")
        self.assertTrue(detail["is_estimate"])
        self.assertIn("estimate", detail["method"])
        self.assertEqual(detail["method"], corpus.TOKEN_ESTIMATOR)
        self.assertIn("ESTIMATES", corpus.TOKEN_ESTIMATOR_NOTE)

    def test_estimate_is_empty_safe_and_monotonic(self):
        self.assertEqual(corpus.estimate_tokens(""), 0)
        short = corpus.estimate_tokens("a" * 100)
        long = corpus.estimate_tokens("a" * 1000)
        self.assertLess(short, long)
        self.assertGreaterEqual(short, 25)

    def test_digits_cost_more_than_plain_text(self):
        plain = corpus.estimate_tokens("alpha " * 100)
        digits = corpus.estimate_tokens("12345 " * 100)
        self.assertGreater(digits, plain)

    def test_messages_overhead(self):
        messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
        self.assertGreater(corpus.estimate_messages_tokens(messages), 8)


class CorrelationDeterminismTest(unittest.TestCase):
    def test_same_seed_same_text(self):
        a = corpus.CorpusGenerator(11).text(4000)
        b = corpus.CorpusGenerator(11).text(4000)
        self.assertEqual(a, b)

    def test_different_seed_different_text(self):
        a = corpus.CorpusGenerator(11).text(2000)
        b = corpus.CorpusGenerator(12).text(2000)
        self.assertNotEqual(a, b)

    def test_registers_are_varied(self):
        chunks = corpus.generate_filler(3, 4000)
        registers = {chunk.register for chunk in chunks}
        self.assertGreaterEqual(len(registers), 3)
        self.assertTrue(registers.issubset(set(corpus.REGISTERS)))

    def test_number_shift_changes_numbers_only(self):
        base = corpus.CorpusGenerator(5).text(1500)
        shifted = corpus.CorpusGenerator(5, number_shift=1000).text(1500)
        self.assertNotEqual(base, shifted)
        self.assertEqual(len(base) // 100, len(shifted) // 100)

    def test_unknown_register_rejected(self):
        with self.assertRaises(ValueError):
            corpus.CorpusGenerator(1, register_cycle=("nope",))

    def test_chunk_dict_is_json_safe(self):
        chunk = next(iter(corpus.generate_filler(1, 400)))
        json.dumps(chunk.as_dict())
