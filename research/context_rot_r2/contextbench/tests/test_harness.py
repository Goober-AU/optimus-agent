"""Tests for the A/B harness scaffold (offline, stdlib only)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.contextbench import PATCH_SUCCESS_UNAVAILABLE  # noqa: E402
from research.context_rot_r2.contextbench.arms import (  # noqa: E402
    ARMS,
    get_arm,
    placeholder_arms,
    runnable_arms,
)
from research.context_rot_r2.contextbench.gold import gold_from_row  # noqa: E402
from research.context_rot_r2.contextbench.harness import (  # noqa: E402
    compare_arms,
    evaluate_task,
    run_arm,
    summarize_arm,
    write_run_summary,
)
from research.context_rot_r2.contextbench.trajectory import (  # noqa: E402
    record_from_steps,
)
from research.context_rot_r2.contextbench.metrics import TrajStep  # noqa: E402


GOLD_PATCH = (
    "diff --git a/a.py b/a.py\n"
    "--- a/a.py\n+++ b/a.py\n"
    "@@ -3,2 +3,2 @@\n ctx\n-old\n+new\n ctx\n"
)


def _gold():
    row = {
        "instance_id": "inst-001",
        "original_inst_id": "owner__repo-1",
        "repo": "owner/repo",
        "repo_url": "https://github.com/owner/repo.git",
        "base_commit": "abc",
        "language": "python",
        "source": "Verified",
        "gold_context": json.dumps(
            [
                {"file": "a.py", "start_line": 1, "end_line": 10},
                {"file": "b.py", "start_line": 20, "end_line": 30},
            ]
        ),
        "patch": GOLD_PATCH,
        "test_patch": "",
    }
    return gold_from_row(row)


def _trajectory(instance_id="inst-001", tool_calls=4, model_patch=""):
    steps = [
        TrajStep(["a.py"], [{"file": "a.py", "start_line": 1, "end_line": 10}]),
        TrajStep(["a.py", "c.py"], [{"file": "a.py", "start_line": 1, "end_line": 12}]),
    ]
    return record_from_steps(
        instance_id, steps, model_patch=model_patch, tool_calls=tool_calls
    )


class ArmTests(unittest.TestCase):
    def test_registry(self):
        self.assertEqual(sorted(ARMS), ["A", "B", "C", "D"])
        self.assertEqual([a.arm for a in runnable_arms()], ["A", "B"])
        self.assertEqual([a.arm for a in placeholder_arms()], ["C", "D"])

    def test_unknown_arm(self):
        with self.assertRaises(KeyError):
            get_arm("E")


class EvaluateTaskTests(unittest.TestCase):
    def test_record_shape_and_values(self):
        gold = _gold()
        record = evaluate_task(get_arm("A"), gold, _trajectory())
        self.assertEqual(record["instance_id"], "inst-001")
        self.assertEqual(record["arm"], "A")
        # final files: pred {a.py, c.py} vs gold {a.py, b.py}
        self.assertEqual(record["final"]["file"]["intersection"], 1)
        self.assertAlmostEqual(record["final"]["file"]["coverage"], 0.5)
        self.assertAlmostEqual(record["final"]["file"]["precision"], 0.5)
        # final lines: pred a.py 1-12 vs gold a.py 1-10 + b.py 20-30
        self.assertEqual(record["final"]["line"]["intersection"], 10)
        self.assertAlmostEqual(record["final"]["line"]["coverage"], 10 / 21)
        self.assertAlmostEqual(record["final"]["line"]["precision"], 10 / 12)
        self.assertEqual(record["tool_calls"], 4)
        self.assertEqual(
            record["patch_success"]["status"], PATCH_SUCCESS_UNAVAILABLE
        )
        # trajectory: 2 steps
        self.assertEqual(len(record["trajectory"]["steps"]), 2)

    def test_editloc_gold_patch_fallback(self):
        gold = _gold()
        record = evaluate_task(get_arm("A"), gold, _trajectory(model_patch=""))
        # gold patch deletes a.py line 4; gold ranges include a.py 1-10
        self.assertIn("editloc", record)
        self.assertEqual(record["editloc"]["pred_size"], 1)
        self.assertEqual(record["editloc"]["intersection"], 1)

    def test_editloc_from_model_patch(self):
        gold = _gold()
        patch = (
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n+++ b/b.py\n"
            "@@ -25,2 +25,2 @@\n ctx\n-xx\n+yy\n"
        )
        record = evaluate_task(get_arm("A"), gold, _trajectory(model_patch=patch))
        self.assertEqual(record["editloc"]["pred_size"], 1)
        self.assertEqual(record["editloc"]["intersection"], 1)

    def test_no_context_warning(self):
        gold = _gold()
        empty = record_from_steps("inst-001", [TrajStep([], [])])
        record = evaluate_task(get_arm("A"), gold, empty)
        self.assertEqual(record["warning"], "no_context_extracted")
        self.assertEqual(record["final"]["file"]["coverage"], 0.0)


class RunArmTests(unittest.TestCase):
    def test_runnable_arm_missing_trajectory(self):
        records = run_arm(get_arm("A"), [_gold()], {})
        self.assertEqual(records[0]["error"], "missing_trajectory")

    def test_runnable_arm_with_trajectory(self):
        gold = _gold()
        records = run_arm(get_arm("A"), [gold], {"inst-001": _trajectory()})
        self.assertNotIn("error", records[0])
        self.assertEqual(records[0]["arm"], "A")

    def test_placeholder_arm_never_fabricates(self):
        gold = _gold()
        records = run_arm(get_arm("C"), [gold], {"inst-001": _trajectory()})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "PLACEHOLDER")
        self.assertIn("blocked_reason", records[0])
        for key in ("final", "trajectory", "editloc"):
            self.assertNotIn(key, records[0])
        records_d = run_arm(get_arm("D"), [gold], {"inst-001": _trajectory()})
        self.assertEqual(records_d[0]["status"], "PLACEHOLDER")

    def test_task_order_preserved(self):
        golds = [_gold(), _gold()]
        golds[1].instance_id = "inst-002"
        records = run_arm(get_arm("B"), golds, {})
        self.assertEqual(
            [r["instance_id"] for r in records], ["inst-001", "inst-002"]
        )

    def test_summarize(self):
        gold = _gold()
        records = run_arm(get_arm("A"), [gold], {"inst-001": _trajectory()})
        agg = summarize_arm(records)
        self.assertEqual(agg["num_valid"], 1)
        self.assertIn("final_file", agg)
        self.assertIn("traj_auc_file", agg)


class CompareArmsTests(unittest.TestCase):
    def _records(self, values):
        out = []
        for i, v in enumerate(values):
            out.append(
                {
                    "instance_id": f"i{i}",
                    "final": {"file": {"coverage": v}},
                }
            )
        return out

    def test_paired_deltas_and_sign_test(self):
        a = self._records([0.1, 0.2, 0.3, 0.4, 0.5])
        b = self._records([0.2, 0.3, 0.4, 0.5, 0.6])
        cmp = compare_arms(a, b)
        self.assertEqual(cmp["n_pairs"], 5)
        self.assertEqual(cmp["positives"], 5)
        self.assertEqual(cmp["negatives"], 0)
        self.assertAlmostEqual(cmp["mean_delta"], 0.1)
        # all positive: exact two-sided sign test p = 2 * (1/2)^5
        self.assertAlmostEqual(cmp["sign_test_pvalue"], 2 / 32)
        self.assertLess(cmp["wilcoxon"]["p_value"], 0.05)

    def test_no_difference(self):
        a = self._records([0.5, 0.5, 0.5])
        b = self._records([0.5, 0.5, 0.5])
        cmp = compare_arms(a, b)
        self.assertEqual(cmp["zeros"], 3)
        self.assertEqual(cmp["positives"] + cmp["negatives"], 0)
        self.assertEqual(cmp["sign_test_pvalue"], 1.0)

    def test_missing_pairs_skipped(self):
        a = self._records([0.1, 0.2])
        b = self._records([0.2]) + [{"instance_id": "x", "error": "missing"}]
        cmp = compare_arms(a, b)
        self.assertEqual(cmp["n_pairs"], 1)

    def test_placeholder_records_skipped(self):
        a = self._records([0.1])
        b = [{"instance_id": "i0", "status": "PLACEHOLDER"}]
        cmp = compare_arms(a, b)
        self.assertEqual(cmp["n_pairs"], 0)

    def test_median_delta_even(self):
        a = self._records([0.1, 0.2])
        b = self._records([0.2, 0.2])
        cmp = compare_arms(a, b)
        self.assertAlmostEqual(cmp["median_delta"], 0.05)


class RunSummaryTests(unittest.TestCase):
    def test_write_and_read(self):
        gold = _gold()
        records = run_arm(get_arm("A"), [gold], {"inst-001": _trajectory()})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "run.json")
            summary = write_run_summary(
                get_arm("A"), records, ["inst-001"], "traj.jsonl", path
            )
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        self.assertEqual(summary["arm"], "A")
        self.assertEqual(loaded["task_ids"], ["inst-001"])
        self.assertEqual(loaded["aggregate"]["num_valid"], 1)
        self.assertEqual(
            loaded["records"][0]["patch_success"]["status"], "UNAVAILABLE"
        )


if __name__ == "__main__":
    unittest.main()
