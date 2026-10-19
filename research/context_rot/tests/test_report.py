import csv
import os
import tempfile
import unittest

from research.context_rot import report


def record(item_id, length, correct, **extra):
    base = {
        "suite": "needle-smoke", "item_id": item_id, "instance_id": item_id + str(length),
        "category": "exact_lexical", "seed": 1, "model": "fake", "provider": "fake",
        "adapter_kind": "fake", "context_target_len": length, "evidence_position": "middle",
        "distractor_kind": "none", "requested_json_style": False, "prompt_tokens": length,
        "prompt_tokens_source": "estimate", "usage": {"prompt_tokens": None},
        "latency_ms": 0.0, "extraction_rule": "last_line", "correct": correct,
        "invalid_reason": None, "raw_answer": "x", "normalized_answer": "x",
        "gold_fingerprint": "abc", "truncated": False, "tokens_original_est": length,
        "tokens_final_est": length, "evidence_survived": True, "abstained": False,
        "unsupported_claim": False, "adapter_synthetic": True, "category": "exact_lexical",
    }
    base.update(extra)
    return base


MANIFEST = {"run_id": "20261018T000000Z-unit", "suite": "needle-smoke", "seed": 1,
            "model": "fake", "provider": "fake", "adapter_kind": "fake",
            "git_head": "deadbeef", "python_version": "3.11.15",
            "harness_version": "0.1.0", "status": "complete"}


class AggregationTest(unittest.TestCase):
    def setUp(self):
        self.records = [
            record("a", 1000, True), record("b", 1000, True),
            record("a", 4000, True), record("b", 4000, False),
            record("c", 4000, None, invalid_reason="truncated_evidence"),
        ]

    def test_accuracy_ignores_invalid(self):
        self.assertAlmostEqual(report.accuracy(self.records), 0.75)
        self.assertIsNone(report.accuracy([record("x", 1, None)]))

    def test_bucket_by_length(self):
        rows = report.bucket_by_length(self.records)
        self.assertEqual([row["context_target_len"] for row in rows], [1000, 4000])
        self.assertEqual(rows[0]["accuracy"], 1.0)
        self.assertEqual(rows[1]["invalid"], 1)
        self.assertEqual(rows[1]["valid"], 2)
        self.assertAlmostEqual(rows[1]["accuracy"], 0.5)

    def test_degradation_vs_baseline(self):
        result = report.degradation_vs_baseline(self.records)
        self.assertEqual(result["baseline_len"], 1000)
        rows = {row["context_target_len"]: row for row in result["rows"]}
        self.assertEqual(rows[1000]["delta_accuracy"], 0.0)
        self.assertEqual(rows[4000]["n_pairs"], 2)
        self.assertAlmostEqual(rows[4000]["delta_accuracy"], -0.5)
        self.assertIsNotNone(rows[4000]["ci_lo"])

    def test_counts_and_groups(self):
        self.assertEqual(report.counts(self.records, "extraction_rule")[0]["count"], 5)
        group = report.accuracy_by(self.records, "evidence_position")
        self.assertEqual(group[0]["label"], "middle")
        self.assertEqual(report.accuracy_by(self.records, "distractor_kind")[0]["valid"], 4)


class MarkdownTest(unittest.TestCase):
    def test_required_sections(self):
        markdown = report.render_markdown(
            [record("a", 1000, True), record("b", 2000, False)], MANIFEST)
        for heading in ("# Context-rot run", "## Manifest", "## Totals",
                        "## Accuracy vs context length", "## Degradation vs",
                        "## Accuracy by evidence position", "## Extraction rules fired",
                        "## Invalid reasons", "## Truncation accounting", "## Caveats"):
            self.assertIn(heading, markdown)
        self.assertIn("SYNTHETIC ADAPTER", markdown)
        self.assertIn("ESTIMATES", markdown)

    def test_truncation_section_lists_rows(self):
        truncated = record("t", 2000, None, truncated=True, evidence_survived=False,
                           invalid_reason="truncated_evidence", tokens_original_est=5000,
                           tokens_final_est=300)
        markdown = report.render_markdown([truncated], MANIFEST)
        self.assertIn("evidence lost", markdown)
        self.assertIn("| t |", markdown)

    def test_no_synthetic_warning_for_real_adapter(self):
        real = record("r", 1000, True, adapter_synthetic=False, adapter_kind="openai")
        markdown = report.render_markdown([real], MANIFEST)
        self.assertNotIn("SYNTHETIC ADAPTER", markdown)


class WriterTest(unittest.TestCase):
    def test_csv_columns_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "results.csv")
            report.write_csv(path, [record("a", 1000, True), record("b", 2000, None)])
            with open(path, encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(list(rows[0])[:3], ["suite", "item_id", "instance_id"])
            self.assertEqual(rows[0]["correct"], "true")
            self.assertEqual(rows[1]["correct"], "")

    def test_write_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "summary.md")
            report.write_summary(path, [record("a", 1000, True)], MANIFEST)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertTrue(text.startswith("# Context-rot run"))
