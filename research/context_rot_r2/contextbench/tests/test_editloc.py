"""Tests for the EditLoc mirrors (offline, stdlib only)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.contextbench.editloc import (  # noqa: E402
    editloc_metrics,
    parse_diff_lines,
)

DIFF = 'diff --git a/src/app.py b/src/app.py\nindex 1111111..2222222 100644\n--- a/src/app.py\n+++ b/src/app.py\n@@ -10,4 +10,5 @@ def main():\n     keep1\n-    drop1\n-    drop2\n+    add1\n     keep2\n@@ -40,3 +41,2 @@ def other():\n     keep3\n-    drop3\n     keep4\ndiff --git a/src/other.py b/src/other.py\nindex 3333333..4444444 100644\n--- a/src/other.py\n+++ b/src/other.py\n@@ -5,3 +5,4 @@\n     keep5\n+    add5\n-    drop5\n     keep6\n'


class ParseDiffLinesTests(unittest.TestCase):
    def test_deletions_only_old_coordinates(self):
        edits = parse_diff_lines(DIFF, deletions_only=True)
        self.assertEqual(
            edits,
            {"src/app.py": [(11, 12), (41, 41)], "src/other.py": [(6, 6)]},
        )

    def test_additions_new_coordinates(self):
        edits = parse_diff_lines(DIFF, deletions_only=False)
        self.assertEqual(
            edits,
            {"src/app.py": [(11, 11)], "src/other.py": [(6, 6)]},
        )

    def test_empty(self):
        self.assertEqual(parse_diff_lines("", deletions_only=True), {})


class EditLocMetricsTests(unittest.TestCase):
    def test_hits_and_misses(self):
        gold = {"src/app.py": [(11, 14), (30, 40)]}
        m = editloc_metrics(DIFF, gold)
        # pred deleted lines: app 11,12,41 + other 6 = 4 total; in gold: 11,12 = 2
        self.assertEqual(m["pred_size"], 4)
        self.assertEqual(m["intersection"], 2)
        self.assertAlmostEqual(m["recall"], 0.5)
        self.assertAlmostEqual(m["precision"], 0.5)
        self.assertEqual(m["gold_size"], 15)

    def test_no_patch_returns_empty(self):
        self.assertEqual(editloc_metrics("", {"a.py": [(1, 2)]}), {})

    def test_patch_without_deletions(self):
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n+++ b/x.py\n"
            "@@ -1,2 +1,3 @@\n line1\n+newline\n line2\n"
        )
        m = editloc_metrics(diff, {"x.py": [(1, 5)]})
        self.assertEqual(m["pred_size"], 0)
        self.assertEqual(m["recall"], 0.0)

    def test_gold_ranges_other_files_do_not_count(self):
        gold = {"src/other.py": [(1, 100)]}
        m = editloc_metrics(DIFF, gold)
        self.assertEqual(m["intersection"], 1)
        self.assertAlmostEqual(m["recall"], 0.25)


if __name__ == "__main__":
    unittest.main()
