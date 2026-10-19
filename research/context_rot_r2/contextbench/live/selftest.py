"""Offline deterministic selftests for the W27 live practice pipeline.

Stdlib-only; no parquet and no binary needed. The tests cover the slice
rule, transcript construction, compaction budgets, reference extraction,
retained-tail bookkeeping, trajectory assembly and the W10 evaluation
path with hand-computed expectations.

Run: python -m research.context_rot_r2.contextbench.live.selftest
"""

import json
import os
import sys
import unittest
from typing import List

from . import scenario as live_scenario
from .extract import extract_refs, refs_step, retained_refs
from . import driver as live_driver


def synthetic_row(instance_id, repo, language, items, statement="Fix the bug."):
    return {
        "instance_id": instance_id,
        "repo": repo,
        "language": language,
        "source": "Verified",
        "base_commit": "0" * 40,
        "problem_statement": statement,
        "gold_context": json.dumps(items),
    }


def item(path, start, end, content=None):
    return {
        "file": path,
        "start_line": start,
        "end_line": end,
        "content": content or (f"line of {path} " * 3).strip(),
    }


ROWS: List[dict] = []
for index in range(12):
    ROWS.append(
        synthetic_row(
            f"synth__{index}",
            f"org/repo{index % 3}",
            ["python", "go", "rust"][index % 3],
            [
                item(f"pkg/mod{inner}/file.py", 1 + inner, 20 + inner, "x" * 400)
                for inner in range(5)
            ],
        )
    )
DEV_IDS = {row["instance_id"] for row in ROWS}


class SliceTests(unittest.TestCase):
    def test_slice_is_deterministic(self):
        first = live_scenario.select_slice(ROWS, DEV_IDS)
        second = live_scenario.select_slice(ROWS, DEV_IDS)
        self.assertEqual(
            [row["instance_id"] for row in first],
            [row["instance_id"] for row in second],
        )

    def test_slice_respects_size_window(self):
        rows = ROWS + [
            synthetic_row(
                "too_few_items",
                "org/repo0",
                "python",
                [item("pkg/a.py", 1, 2, "y" * 700) for _ in range(2)],
            ),
            synthetic_row(
                "too_many_items",
                "org/repo1",
                "python",
                [item(f"pkg/f{i}.py", 1, 2, "y" * 150) for i in range(13)],
            ),
        ]
        picked = {row["instance_id"] for row in live_scenario.select_slice(rows, DEV_IDS)}
        self.assertNotIn("too_few_items", picked)
        self.assertNotIn("too_many_items", picked)

    def test_slice_never_takes_holdout(self):
        rows = ROWS + [synthetic_row("holdout_row", "org/other", "python", [item("h.py", 1, 5)] * 4)]
        dev_ids = DEV_IDS
        picked = {row["instance_id"] for row in live_scenario.select_slice(rows, dev_ids)}
        self.assertNotIn("holdout_row", picked)

    def test_slice_covers_every_language(self):
        picked = live_scenario.select_slice(ROWS, DEV_IDS)
        languages = {row["language"] for row in picked}
        self.assertEqual(languages, {"python", "go", "rust"})


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.target = ROWS[0]
        self.plan = live_scenario.build_plan(self.target, ROWS, DEV_IDS)

    def test_turn_structure(self):
        turns = self.plan["turns"]
        self.assertEqual(turns[0]["role"], "user")
        self.assertIn("Issue:", turns[0]["content"])
        self.assertIn(self.target["repo"], turns[0]["content"])
        n_reads = len(self.plan["reads"])
        self.assertEqual(len(turns), 1 + 2 * n_reads)
        for read_index in range(n_reads):
            user = turns[1 + 2 * read_index]
            assistant = turns[2 + 2 * read_index]
            self.assertEqual(user["role"], "user")
            self.assertEqual(assistant["role"], "assistant")
            read = self.plan["reads"][read_index]
            self.assertIn(read["file"], user["content"])
            self.assertTrue(
                assistant["content"].startswith(
                    f"[{read['file']}:{read['start_line']}-{read['end_line']}]"
                )
            )

    def test_read_pair_turn_ids(self):
        for index, pair in enumerate(self.plan["read_pairs"]):
            self.assertEqual(pair["user_turn_id"], 2 * index + 1)
            self.assertEqual(pair["assistant_turn_id"], 2 * index + 2)
            read = self.plan["reads"][index]
            self.assertEqual(pair["file"], read["file"])
            self.assertEqual(pair["kind"], read["kind"])

    def test_chunks_partition_reads(self):
        chunks = self.plan["chunks"]
        self.assertEqual(len(chunks), 5)
        flat = [read for chunk in chunks for read in chunk]
        self.assertEqual(flat, self.plan["reads"])
        sizes = [len(chunk) for chunk in chunks]
        self.assertTrue(all(sizes[i] >= sizes[i + 1] - 1 for i in range(len(sizes) - 1)))

    def test_distractors_are_other_dev_instances(self):
        for distractor in self.plan["distractors"]:
            self.assertNotEqual(distractor["source_instance"], self.target["instance_id"])
            self.assertIn(distractor["source_instance"], DEV_IDS)

    def test_budgets_scale_with_transcript(self):
        reserve, keep = self.plan["settings"]["reserveTokens"], self.plan["settings"]["keepRecentTokens"]
        self.assertGreaterEqual(reserve, 200.0)
        self.assertGreaterEqual(keep, 60.0)
        long_plan = live_scenario.build_plan(
            synthetic_row(
                "long__row",
                "org/repo0",
                "python",
                [item(f"pkg/f{i}.py", 1, 400, "x" * 3000) for i in range(8)],
            ),
            ROWS,
            DEV_IDS,
        )
        long_reserve, long_keep = long_plan["settings"]["reserveTokens"], long_plan["settings"]["keepRecentTokens"]
        self.assertGreater(long_reserve, reserve)
        self.assertGreater(long_keep, keep)

    def test_plan_is_deterministic(self):
        other = live_scenario.build_plan(self.target, ROWS, DEV_IDS)
        self.assertEqual(json.dumps(self.plan, sort_keys=True), json.dumps(other, sort_keys=True))


class ExtractTests(unittest.TestCase):
    def test_bracket_format(self):
        files, spans = extract_refs(
            "We read [django/db/models/aggregates.py:16-29] and kept the query plan."
        )
        self.assertEqual(files, {"django/db/models/aggregates.py"})
        self.assertEqual(spans, [{"file": "django/db/models/aggregates.py", "start_line": 16, "end_line": 29}])

    def test_path_with_explicit_range(self):
        files, spans = extract_refs(
            "Relevant context: pkg/mod1/file.py lines 40-55 must be preserved."
        )
        self.assertEqual(files, {"pkg/mod1/file.py"})
        self.assertEqual(spans[0]["start_line"], 40)
        self.assertEqual(spans[0]["end_line"], 55)

    def test_path_without_range_is_file_only(self):
        files, spans = extract_refs("Earlier we inspected pkg/mod2/other.py.")
        self.assertEqual(files, {"pkg/mod2/other.py"})
        self.assertEqual(spans, [])

    def test_hallucinated_path_is_kept(self):
        files, _ = extract_refs("The fix lives in made/up/hallucination.py.")
        self.assertIn("made/up/hallucination.py", files)

    def test_bare_filename_without_directory_is_ignored(self):
        files, _ = extract_refs("See also readme.txt for context.")
        self.assertEqual(files, set())

    def test_container_prefix_is_normalized(self):
        files, _ = extract_refs("We opened /testbed/pkg/mod1/file.py earlier.")
        self.assertEqual(files, {"pkg/mod1/file.py"})


class RetainedTests(unittest.TestCase):
    PAIRS = [
        {"user_turn_id": 1, "assistant_turn_id": 2, "file": "pkg/a.py", "start_line": 1, "end_line": 10, "kind": "gold"},
        {"user_turn_id": 3, "assistant_turn_id": 4, "file": "pkg/b.py", "start_line": 1, "end_line": 5, "kind": "gold"},
        {"user_turn_id": 5, "assistant_turn_id": 6, "file": "pkg/c.py", "start_line": 1, "end_line": 5, "kind": "distractor"},
    ]

    def test_full_tail_retains_spans(self):
        files, spans = retained_refs([1, 2, 3, 4, 5, 6], self.PAIRS, "", "")
        self.assertEqual(files, {"pkg/a.py", "pkg/b.py", "pkg/c.py"})
        self.assertEqual(len(spans), 3)

    def test_request_only_keeps_file_without_span(self):
        files, spans = retained_refs([1, 5], self.PAIRS, "", "")
        self.assertEqual(files, {"pkg/a.py", "pkg/c.py"})
        self.assertEqual(spans, [])

    def test_summary_refs_merge_with_tail(self):
        files, spans = retained_refs([1, 2], self.PAIRS, "Keep pkg/b.py lines 1-5.", "")
        self.assertEqual(files, {"pkg/a.py", "pkg/b.py"})
        self.assertEqual(
            spans,
            [{"file": "pkg/a.py", "start_line": 1, "end_line": 10},
             {"file": "pkg/b.py", "start_line": 1, "end_line": 5}],
        )

    def test_refs_step_shape(self):
        files, spans = {"a.py"}, [{"file": "a.py", "start_line": 2, "end_line": 4}]
        step = refs_step(files, spans)
        self.assertEqual(step["files"], ["a.py"])
        self.assertEqual(step["spans"], {"a.py": [{"start": 2, "end": 4}]})


class EvaluationTests(unittest.TestCase):
    def _pair(self, retained_files, retained_spans):
        return {
            "instanceId": "synth__eval",
            "arm": "A",
            "cycles": [
                {"cycle": 1, "retainedFiles": sorted(retained_files), "retainedSpans": retained_spans}
            ],
            "calls": [],
            "stops": [],
            "completedCycles": 1,
        }

    def test_evaluate_task_hand_computed(self):
        from .. import gold as cb_gold
        from .. import harness as cb_harness
        from ..arms import ARM_A

        row = synthetic_row(
            "synth__eval",
            "org/repo0",
            "python",
            [item("pkg/a.py", 1, 10), item("pkg/b.py", 1, 5)],
        )
        gold = cb_gold.gold_from_row(row)
        pair = self._pair({"pkg/a.py"}, [{"file": "pkg/a.py", "start_line": 1, "end_line": 5}])
        record = live_driver.pair_to_trajectory(pair)
        record["tool_calls"] = 2
        result = cb_harness.evaluate_task(ARM_A, gold, record)
        self.assertEqual(result["final"]["file"]["coverage"], 0.5)
        self.assertEqual(result["final"]["file"]["pred_size"], 1)
        self.assertAlmostEqual(result["final"]["line"]["coverage"], 5 / 15)
        self.assertEqual(result["patch_success"]["status"], "UNAVAILABLE")

    def test_empty_retention_is_reported_not_dropped(self):
        from .. import gold as cb_gold
        from .. import harness as cb_harness
        from ..arms import ARM_A

        row = synthetic_row(
            "synth__eval2", "org/repo0", "python", [item("pkg/a.py", 1, 10)]
        )
        gold = cb_gold.gold_from_row(row)
        pair = self._pair(set(), [])
        record = live_driver.pair_to_trajectory(pair)
        result = cb_harness.evaluate_task(ARM_A, gold, record)
        self.assertEqual(result["final"]["file"]["coverage"], 0.0)
        self.assertEqual(result["warning"], "no_context_extracted")


class RequestShapeTests(unittest.TestCase):
    def test_cycle_request_shape(self):
        from . import harness_client

        request = harness_client.cycle_request(
            "dir",
            [{"role": "user", "content": "hi", "turnId": 0}],
            {"reserveTokens": 300.0, "keepRecentTokens": 100.0, "taskState": "off"},
            "live",
        )
        self.assertEqual(
            request,
            {
                "sessionDir": "dir",
                "newTurns": [{"role": "user", "content": "hi", "turnId": 0}],
                "settings": {
                    "reserveTokens": 300.0,
                    "keepRecentTokens": 100.0,
                    "taskState": "off",
                },
                "summarizer": "live",
            },
        )


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromModule(
        sys.modules[__name__]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
