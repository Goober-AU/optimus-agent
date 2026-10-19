import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from research.context_rot import cli


def run_cli(argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ListPlanTest(unittest.TestCase):
    def test_list_json(self):
        code, out, _ = run_cli(["list", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["stress_categories"]), 20)
        self.assertEqual(len(payload["needle_kinds"]), 4)
        self.assertEqual(set(payload["suites"][0]), {"name", "description"})
        self.assertIn("token_estimator", payload)

    def test_list_human(self):
        code, out, _ = run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("stress categories:", out)
        self.assertIn("A_buried_constraint", out)

    def test_plan_counts(self):
        code, out, _ = run_cli(["plan", "--suite", "needle-smoke", "--seed", "3",
                                "--context-lens", "1000,2000", "--positions", "start,end",
                                "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["item_count"], 4)
        self.assertEqual(payload["cell_count"], 4 * 4)

    def test_plan_rejects_bad_suite(self):
        with self.assertRaises(SystemExit):
            run_cli(["plan", "--suite", "nope"])


class RunAndReportTest(unittest.TestCase):
    def test_run_then_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_cli([
                "run", "--suite", "needle-smoke", "--seed", "8", "--adapter", "fake",
                "--context-lens", "600", "--positions", "middle",
                "--distractors", "none", "--concurrency", "2",
                "--runs-root", tmp, "--slug", "cli-unit", "--quiet"])
            self.assertEqual(code, 0, out)
            run_dirs = [d for d in os.listdir(tmp) if d.endswith("-cli-unit")]
            self.assertEqual(len(run_dirs), 1)
            run_dir = os.path.join(tmp, run_dirs[0])
            self.assertTrue(os.path.exists(os.path.join(run_dir, "summary.md")))

            code, out, _ = run_cli(["report", "--run-dir", run_dir, "--print"])
            self.assertEqual(code, 0)
            self.assertIn("## Accuracy vs context length", out)

    def test_run_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["run", "--suite", "needle-smoke", "--seed", "8", "--adapter", "fake",
                    "--context-lens", "500", "--runs-root", tmp, "--slug", "twice",
                    "--quiet"]
            code, _, _ = run_cli(argv)
            self.assertEqual(code, 0)
            run_dir = os.path.join(tmp, sorted(os.listdir(tmp))[0])
            code, out, err = run_cli(argv + ["--run-dir", run_dir])
            self.assertEqual(code, 2)
            self.assertIn("refusing to write", err)

    def test_run_multicycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_cli([
                "run", "--suite", "needle-smoke", "--seed", "9", "--adapter", "fake",
                "--context-lens", "1200", "--positions", "start", "--cycles", "2",
                "--budget-tokens", "600", "--runs-root", tmp, "--slug", "cyc"])
            self.assertEqual(code, 0, out)
            run_dir = os.path.join(tmp, sorted(os.listdir(tmp))[0])
            self.assertTrue(os.path.exists(os.path.join(run_dir, "cycles.jsonl")))
            self.assertIn("cycle 0", out)

    def test_selftest_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_cli(["selftest", "--runs-root", tmp, "--seed", "1234"])
            self.assertEqual(code, 0, out[-3000:])
            self.assertIn("SELFTEST PASSED", out)
            self.assertNotIn("[FAIL]", out)
            run_dir = os.path.join(tmp, sorted(os.listdir(tmp))[0])
            for name in ("manifest.json", "results.jsonl", "results.csv",
                         "summary.md", "cycles.jsonl"):
                self.assertTrue(os.path.exists(os.path.join(run_dir, name)), name)
