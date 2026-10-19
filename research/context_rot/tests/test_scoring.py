import unittest

from research.context_rot import scoring


class NormalisationTest(unittest.TestCase):
    def test_strips_quotes_and_whitespace(self):
        self.assertEqual(scoring.normalize_text('  "hello   world."  '), "hello world")
        self.assertEqual(scoring.normalize_answer("HELLO"), "hello")

    def test_strips_answer_preamble(self):
        extraction = scoring.extract_answer("The answer is 17.", "numeric")
        self.assertEqual(extraction.answer, "17")
        self.assertEqual(extraction.rule, "last_line")


class ExtractionRuleTest(unittest.TestCase):
    def test_delimiter_rule_fires_after_reasoning(self):
        raw = "Let me think. 18 * 2 = 36.\nFINAL ANSWER: 36"
        extraction = scoring.extract_answer(raw, "numeric")
        self.assertEqual(extraction.rule, "delimiter:final_answer")
        self.assertEqual(extraction.answer, "36")
        self.assertTrue(extraction.stripped_reasoning)

    def test_bracket_rule(self):
        extraction = scoring.extract_answer("so [[TC-9f21-final]] then", "code")
        self.assertEqual(extraction.rule, "delimiter:brackets")
        self.assertEqual(extraction.answer, "TC-9f21-final")

    def test_json_field_rule(self):
        extraction = scoring.extract_answer('{"answer": "23:25"}', "time")
        self.assertEqual(extraction.rule, "json:field")
        self.assertEqual(extraction.answer, "23:25")

    def test_last_line_rule_for_bare_value(self):
        extraction = scoring.extract_answer("reasoning text here\n42", "numeric")
        self.assertEqual(extraction.rule, "last_line")
        self.assertEqual(extraction.value_rule, "last_number_in_line")

    def test_last_number_fallback_for_chatty_answer(self):
        extraction = scoring.extract_answer("I checked three notes and the value is 30 units", "numeric")
        self.assertEqual(extraction.answer, "30")

    def test_empty_answer_rule(self):
        extraction = scoring.extract_answer("   ", "numeric")
        self.assertEqual(extraction.rule, "empty")
        self.assertEqual(extraction.answer, "")

    def test_whole_text_fallback(self):
        extraction = scoring.extract_answer("optimus bench run --profile soak --no-cache", "code")
        self.assertEqual(extraction.rule, "last_line")
        self.assertEqual(extraction.answer, "optimus bench run --profile soak --no-cache")

    def test_rules_are_reported(self):
        for raw, kind in (("FINAL ANSWER: 1", "numeric"), ("[[x]]", "code"),
                          ('{"answer": "y"}', "code"), ("z", "code")):
            extraction = scoring.extract_answer(raw, kind)
            self.assertIn(extraction.rule, scoring.EXTRACTION_RULES)


class MatchTest(unittest.TestCase):
    def test_numeric_match_tolerates_formatting(self):
        self.assertTrue(scoring.match_value("1,200", "1200", "numeric"))
        self.assertTrue(scoring.match_value("1200 items", "1200", "numeric"))
        self.assertFalse(scoring.match_value("1201", "1200", "numeric"))

    def test_time_match_tolerates_dot_form(self):
        self.assertTrue(scoring.match_value("10.15", "10:15", "time"))
        self.assertTrue(scoring.match_value("at 10:15 UTC", "10:15", "time"))
        self.assertFalse(scoring.match_value("10:16", "10:15", "time"))

    def test_code_match_is_case_insensitive_and_suffix_tolerant(self):
        self.assertTrue(scoring.match_value("kx-1234-final", "KX-1234-final", "code"))
        self.assertTrue(scoring.match_value("The value is KX-1234-final.", "KX-1234-final", "code"))
        self.assertTrue(scoring.match_value("KX-1234-final", "GX-1-final", "code", aliases=("KX-1234-final",)))
        self.assertFalse(scoring.match_value("KX-1234", "KX-1234-final", "code"))

    def test_multi_fact_requires_all_facts(self):
        result = scoring.score_answer("1200, 23:25, KX-1234-final", "x", "multi",
                                      facts=("1200", "23:25", "KX-1234-final"))
        self.assertTrue(result.correct)
        self.assertEqual(len(result.matched_facts), 3)
        partial = scoring.score_answer("1200, 23:25", "x", "multi",
                                       facts=("1200", "23:25", "KX-1234-final"))
        self.assertFalse(partial.correct)
        self.assertEqual(partial.missing_facts, ("KX-1234-final",))
        self.assertEqual(partial.extraction_rule, "last_line")


class InvalidTest(unittest.TestCase):
    def test_invalid_never_scores_wrong(self):
        result = scoring.score_answer("nonsense", "12", "numeric",
                                      invalid_reason="truncated_evidence")
        self.assertIsNone(result.correct)
        self.assertEqual(result.invalid_reason, "truncated_evidence")
        self.assertFalse(result.matched)

    def test_wrong_answer_scores_false(self):
        result = scoring.score_answer("13", "12", "numeric")
        self.assertIs(result.correct, False)


class AbstentionTest(unittest.TestCase):
    def test_abstention_detected(self):
        for text in ("I don't know.", "Not in the context.", "ABSTAIN", "insufficient information"):
            self.assertTrue(scoring.detect_abstention(text), text)
        self.assertFalse(scoring.detect_abstention("the value is 12"))

    def test_abstention_recorded_in_score(self):
        result = scoring.score_answer("ABSTAIN", "12", "numeric")
        self.assertTrue(result.abstained)
        self.assertIs(result.correct, False)

    def test_unsupported_claim_prose(self):
        flag, reason = scoring.detect_unsupported_claim(
            "the quantum banana protocol", "storage tiering and release gating notes")
        self.assertTrue(flag)
        self.assertEqual(reason, "no_content_word_in_context")

    def test_unsupported_claim_supported(self):
        flag, _ = scoring.detect_unsupported_claim(
            "storage tiering is scheduled", "the storage tiering note is scheduled")
        self.assertFalse(flag)

    def test_unsupported_claim_skips_derived_values(self):
        flag, reason = scoring.detect_unsupported_claim("KX-1234-final", "nothing here")
        self.assertFalse(flag)
        self.assertEqual(reason, "")


class BootstrapTest(unittest.TestCase):
    def test_deterministic(self):
        first = scoring.paired_bootstrap_ci([1, 1, 0, 1, 0], iters=200, seed=7)
        second = scoring.paired_bootstrap_ci([1, 1, 0, 1, 0], iters=200, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(first["n"], 5)
        self.assertLessEqual(first["lo"], first["mean"])
        self.assertLessEqual(first["mean"], first["hi"])

    def test_empty_and_constant(self):
        self.assertEqual(scoring.paired_bootstrap_ci([])["n"], 0)
        constant = scoring.paired_bootstrap_ci([0, 0, 0])
        self.assertEqual(constant["mean"], 0.0)
        self.assertEqual(constant["lo"], 0.0)
        self.assertEqual(constant["hi"], 0.0)

    def test_paired_deltas_skips_invalid(self):
        a = [{"item_id": "1", "correct": True}, {"item_id": "2", "correct": None}]
        b = [{"item_id": "1", "correct": False}, {"item_id": "2", "correct": True}]
        self.assertEqual(scoring.paired_deltas(a, b), [-1.0])
