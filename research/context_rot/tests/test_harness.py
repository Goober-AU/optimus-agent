import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from research.context_rot.adapters import FakeAdapter
from research.context_rot.harness import (RunConfig, RunDirExistsError, load_results,
                                          new_run_dir, run_one, run_suite)
from research.context_rot.tasks import InstanceConfig, generate_suite


def answers(items):
    return {item.visible.question: (item.gold.answer, item.gold.answer_kind)
            for item in items}


REQUIRED = ("suite", "item_id", "seed", "model", "provider", "context_target_len",
            "evidence_position", "distractor_kind", "requested_json_style",
            "prompt_tokens", "usage", "latency_ms", "extraction_rule", "correct",
            "invalid_reason", "raw_answer", "gold_fingerprint")


class RunDirTest(unittest.TestCase):
    def test_creates_timestamped_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = new_run_dir(tmp, "unit test/slug")
            self.assertTrue(os.path.isdir(path))
            self.assertRegex(os.path.basename(path), r"^\d{8}T\d{6}Z-unit-test-slug$")

    def test_same_second_gets_a_suffixed_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            moment = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
            first = new_run_dir(tmp, "slug", now=moment)
            second = new_run_dir(tmp, "slug", now=moment)
            self.assertNotEqual(first, second)
            self.assertTrue(os.path.basename(second).endswith("-2"))
            self.assertTrue(os.path.isdir(first))
            self.assertTrue(os.path.isdir(second))

    def test_existing_dirs_are_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            moment = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
            paths = {new_run_dir(tmp, "slug", now=moment) for _ in range(4)}
            self.assertEqual(len(paths), 4)
            for path in paths:
                self.assertTrue(os.path.isdir(path))

    def test_exhausted_suffixes_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            moment = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
            new_run_dir(tmp, "slug", now=moment, max_suffix=1)
            with self.assertRaises(RunDirExistsError):
                new_run_dir(tmp, "slug", now=moment, max_suffix=1)


class RunOneTest(unittest.TestCase):
    def setUp(self):
        self.items = generate_suite("needle-smoke", 21)
        self.adapter = FakeAdapter(answers(self.items), seed=21)

    def test_record_has_required_fields(self):
        record = run_one(self.items[0], InstanceConfig(context_target_len=600),
                         self.adapter, {"run_id": "r1", "seed": 21, "model": "fake",
                                        "provider": "fake", "adapter_kind": "fake"})
        for field in REQUIRED:
            self.assertIn(field, record)
        self.assertEqual(record["gold_fingerprint"], self.items[0].gold.fingerprint)
        self.assertNotIn(self.items[0].gold.answer, json.dumps(record["gold_fingerprint"]))
        self.assertTrue(record["token_is_estimate"])
        self.assertEqual(record["prompt_tokens_source"], "estimate")

    def test_truncated_evidence_is_invalid(self):
        instance = InstanceConfig(context_target_len=3000, evidence_position="end",
                                  max_prompt_tokens=400, truncate_strategy="head_only")
        record = run_one(self.items[0], instance, self.adapter,
                         {"run_id": "r1", "seed": 21, "model": "fake",
                          "provider": "fake", "adapter_kind": "fake"})
        self.assertTrue(record["truncated"])
        self.assertFalse(record["evidence_survived"])
        self.assertIsNone(record["correct"])
        self.assertEqual(record["invalid_reason"], "truncated_evidence")

    def test_truncation_that_keeps_evidence_can_score(self):
        instance = InstanceConfig(context_target_len=900, evidence_position="start",
                                  max_prompt_tokens=700, truncate_strategy="head_tail")
        record = run_one(self.items[0], instance, self.adapter,
                         {"run_id": "r1", "seed": 21, "model": "fake",
                          "provider": "fake", "adapter_kind": "fake"})
        self.assertTrue(record["truncated"])
        self.assertTrue(record["evidence_survived"])
        self.assertIsNotNone(record["correct"])

    def test_adapter_error_becomes_invalid(self):
        class Boom:
            name = "boom"
            provider = "fake"

            def complete(self, *args, **kwargs):
                raise RuntimeError("adapter exploded")

        record = run_one(self.items[0], InstanceConfig(context_target_len=400), Boom(),
                         {"run_id": "r1", "seed": 21, "model": "boom",
                          "provider": "fake", "adapter_kind": "fake"})
        self.assertIsNone(record["correct"])
        self.assertEqual(record["invalid_reason"], "adapter_error")
        self.assertIn("exploded", record["error"])


class RunSuiteTest(unittest.TestCase):
    def test_suite_writes_artefacts_and_manifest(self):
        items = generate_suite("needle-smoke", 5)
        adapter = FakeAdapter(answers(items), seed=5)
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(suite="needle-smoke", seed=5,
                               context_target_lens=(500,), evidence_positions=("middle",),
                               distractor_kinds=("none", "contradictory"),
                               runs_root=tmp, slug="unit", concurrency=2)
            outcome = run_suite(config, adapter, items=items)
            run_dir = outcome["run_dir"]
            for name in ("manifest.json", "results.jsonl", "results.csv", "summary.md"):
                self.assertTrue(os.path.exists(os.path.join(run_dir, name)), name)
            with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["suite"], "needle-smoke")
            self.assertEqual(manifest["seed"], 5)
            self.assertIn("python_version", manifest)
            self.assertIn("args", manifest)
            self.assertEqual(manifest["cell_count"], len(items) * 2)
            self.assertEqual(len(load_results(run_dir)), len(items) * 2)
            json.dumps(manifest)

    def test_second_run_does_not_overwrite(self):
        items = generate_suite("needle-smoke", 5, limit=1)
        adapter = FakeAdapter(answers(items), seed=5)
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(suite="needle-smoke", seed=5, context_target_lens=(500,),
                               evidence_positions=("start",), distractor_kinds=("none",),
                               runs_root=tmp, slug="unit")
            first = run_suite(config, adapter, items=items)["run_dir"]
            import time
            time.sleep(0.01)
            second = run_suite(config, adapter, items=items)["run_dir"]
            self.assertNotEqual(first, second)
            self.assertTrue(os.path.isdir(first))
            with self.assertRaises(RunDirExistsError):
                run_suite(config, adapter, items=items, run_dir=first)

    def test_resume_skips_finished_cells(self):
        items = generate_suite("needle-smoke", 6, limit=2)
        adapter = FakeAdapter(answers(items), seed=6)
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(suite="needle-smoke", seed=6, context_target_lens=(400,),
                               evidence_positions=("start",), distractor_kinds=("none",),
                               runs_root=tmp, slug="resume")
            outcome = run_suite(config, adapter, items=items)
            run_dir = outcome["run_dir"]
            before = len(load_results(run_dir))
            resumed = run_suite(config, adapter, items=items, run_dir=run_dir, resume=True)
            self.assertEqual(len(resumed["records"]), before)
            self.assertEqual(resumed["manifest"]["cells_written_this_invocation"], 0)

    def test_partial_file_is_resumed_and_extended(self):
        items = generate_suite("needle-smoke", 7, limit=2)
        adapter = FakeAdapter(answers(items), seed=7)
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(suite="needle-smoke", seed=7, context_target_lens=(400, 700),
                               evidence_positions=("start",), distractor_kinds=("none",),
                               runs_root=tmp, slug="resume2")
            outcome = run_suite(config, adapter, items=items)
            run_dir = outcome["run_dir"]
            path = os.path.join(run_dir, "results.jsonl")
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
            keep = lines[:2]
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.writelines(keep)
            resumed = run_suite(config, adapter, items=items, run_dir=run_dir, resume=True)
            self.assertEqual(len(resumed["records"]), len(items) * 2)
