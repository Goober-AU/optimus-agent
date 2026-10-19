import unittest

from research.context_rot.adapters import FakeAdapter
from research.context_rot.multicycle import (DeterministicTruncatingCompactor,
                                              MultiCycleConfig, render_multicycle_markdown,
                                              run_cycles, summarize_cycles, write_cycles)
from research.context_rot.tasks import generate_suite

import os
import tempfile


def _messages(n=8):
    return [{"role": "system", "content": "sys"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "turn %d" % i}
        for i in range(n)]


class CompactorTest(unittest.TestCase):
    def test_drops_middle_and_is_deterministic(self):
        compactor = DeterministicTruncatingCompactor(keep_tail=2, summary_chars=100)
        first = compactor.compact(_messages(), 100)
        second = compactor.compact(_messages(), 100)
        self.assertEqual(first.messages, second.messages)
        self.assertEqual(first.dropped_messages, 6)
        self.assertLess(first.tokens_after, first.tokens_before)
        self.assertTrue(first.summary_text.startswith("COMPACTION SUMMARY"))
        self.assertEqual(len(first.messages), 1 + 1 + 2)

    def test_short_transcript_is_noop(self):
        compactor = DeterministicTruncatingCompactor(keep_tail=4)
        result = compactor.compact(_messages(3), 1000)
        self.assertEqual(result.dropped_messages, 0)
        self.assertEqual(result.strategy, "noop-below-threshold")

    def test_protocol_shape(self):
        compactor = DeterministicTruncatingCompactor()
        self.assertTrue(hasattr(compactor, "compact"))
        self.assertEqual(compactor.name, "deterministic-truncating")


class CycleSimulationTest(unittest.TestCase):
    def setUp(self):
        self.items = generate_suite("needle-smoke", 31, limit=2)
        self.adapter = FakeAdapter(
            {i.visible.question: (i.gold.answer, i.gold.answer_kind) for i in self.items},
            seed=31)

    def test_evidence_retained_at_start_is_lost_at_end(self):
        compactor = DeterministicTruncatingCompactor(keep_tail=2, summary_chars=2400)
        start_cfg = MultiCycleConfig(cycles=2, budget_tokens=800, context_target_len=2000,
                                     evidence_position="start")
        end_cfg = MultiCycleConfig(cycles=2, budget_tokens=800, context_target_len=2000,
                                   evidence_position="end")
        start_records = run_cycles(self.items[0], self.adapter, compactor, start_cfg,
                                   run_meta={"run_id": "t", "seed": 31, "model": "fake",
                                             "provider": "fake"})
        end_records = run_cycles(self.items[0], self.adapter, compactor, end_cfg,
                                 run_meta={"run_id": "t", "seed": 31, "model": "fake",
                                           "provider": "fake"})
        self.assertTrue(all(r["evidence_survived"] for r in start_records))
        self.assertFalse(any(r["evidence_survived"] for r in end_records))
        self.assertTrue(all(r["invalid_reason"] == "compaction_lost_evidence"
                            for r in end_records))
        self.assertTrue(all(r["correct"] is None for r in end_records))

    def test_records_carry_accounting(self):
        compactor = DeterministicTruncatingCompactor()
        config = MultiCycleConfig(cycles=2, context_target_len=800, budget_tokens=400,
                                  evidence_position="start")
        records = run_cycles(self.items[1], self.adapter, compactor, config,
                             run_meta={"run_id": "t2", "seed": 31, "model": "fake",
                                       "provider": "fake"})
        self.assertEqual(len(records), 2)
        for index, record in enumerate(records):
            self.assertEqual(record["cycle"], index)
            self.assertIn("tokens_original_est", record)
            self.assertIn("tokens_final_est", record)
            self.assertIn("gold_fingerprint", record)
            self.assertTrue(record["token_is_estimate"])
        first = records[0]
        self.assertTrue(first["compacted"])
        self.assertGreater(first["tokens_original_est"], first["tokens_final_est"])

    def test_deterministic(self):
        compactor = DeterministicTruncatingCompactor()
        config = MultiCycleConfig(cycles=2, context_target_len=600, budget_tokens=300,
                                  evidence_position="middle")
        first = run_cycles(self.items[0], self.adapter, compactor, config,
                           run_meta={"run_id": "d", "seed": 31, "model": "f", "provider": "f"})
        second = run_cycles(self.items[0], self.adapter, compactor, config,
                            run_meta={"run_id": "d", "seed": 31, "model": "f", "provider": "f"})
        self.assertEqual([r["raw_answer"] for r in first], [r["raw_answer"] for r in second])
        self.assertEqual([r["correct"] for r in first], [r["correct"] for r in second])

    def test_summaries_and_markdown(self):
        compactor = DeterministicTruncatingCompactor()
        config = MultiCycleConfig(cycles=2, context_target_len=600, budget_tokens=300)
        records = run_cycles(self.items[0], self.adapter, compactor, config,
                             run_meta={"run_id": "s", "seed": 31, "model": "f", "provider": "f"})
        rows = summarize_cycles(records)
        self.assertEqual([row["cycle"] for row in rows], [0, 1])
        self.assertEqual(rows[0]["cells"], 1)
        markdown = render_multicycle_markdown(records, config)
        self.assertIn("## Compaction cycles", markdown)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cycles.jsonl")
            write_cycles(path, records)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(len(handle.readlines()), 2)
