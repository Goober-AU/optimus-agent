"""Tests for gold-context parsing mirrors (offline, stdlib only)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.contextbench.gold import (  # noqa: E402
    GoldContext,
    gold_from_row,
    normalize_rel_path,
    parse_gold_items,
)


class NormalizePathTests(unittest.TestCase):
    def test_testbed_prefix(self):
        self.assertEqual(normalize_rel_path("/testbed/src/a.py"), "src/a.py")

    def test_workspace_prefix_two_components(self):
        self.assertEqual(normalize_rel_path("/workspace/repo/src/a.py"), "src/a.py")

    def test_workspace_prefix_single_component(self):
        self.assertEqual(normalize_rel_path("/workspace/a.py"), "a.py")

    def test_leading_slash(self):
        self.assertEqual(normalize_rel_path("/src/a.py"), "src/a.py")

    def test_dot_slash(self):
        self.assertEqual(normalize_rel_path("./src/a.py"), "src/a.py")

    def test_backslashes(self):
        self.assertEqual(normalize_rel_path("\\src\\a.py"), "src/a.py")

    def test_empty(self):
        self.assertEqual(normalize_rel_path(""), "")
        self.assertEqual(normalize_rel_path(None), "")


class GoldContextTests(unittest.TestCase):
    def _gold(self, items, **kw):
        base = dict(
            instance_id="SWE-Bench-Verified__python__maintenance__bugfix__deadbeef",
            original_inst_id="astropy__astropy-13398",
            repo="astropy/astropy",
            repo_url="https://github.com/astropy/astropy.git",
            base_commit="6500928dc0e5",
            language="python",
            source="Verified",
            init=items,
        )
        base.update(kw)
        return GoldContext(**base)

    def test_files_sorted_unique_normalized(self):
        gold = self._gold(
            [
                {"file": "/testbed/b.py", "start_line": 1, "end_line": 5},
                {"file": "a.py", "start_line": 2, "end_line": 4},
                {"file": "a.py", "start_line": 9, "end_line": 12},
            ]
        )
        self.assertEqual(gold.files(), ["a.py", "b.py"])

    def test_line_spans_merged_adjacent(self):
        gold = self._gold(
            [
                {"file": "a.py", "start_line": 1, "end_line": 5},
                {"file": "a.py", "start_line": 6, "end_line": 10},
                {"file": "a.py", "start_line": 20, "end_line": 25},
            ]
        )
        self.assertEqual(gold.line_spans(), {"a.py": [(1, 10), (20, 25)]})
        self.assertEqual(gold.line_count(), 16)

    def test_line_spans_overlap_merges(self):
        gold = self._gold(
            [
                {"file": "a.py", "start_line": 1, "end_line": 10},
                {"file": "a.py", "start_line": 5, "end_line": 7},
            ]
        )
        self.assertEqual(gold.line_spans(), {"a.py": [(1, 10)]})

    def test_init_add_merge(self):
        gold = self._gold(
            [{"file": "a.py", "start_line": 1, "end_line": 5}],
            add=[{"file": "b.py", "start_line": 1, "end_line": 5}],
        )
        self.assertEqual(gold.files(), ["a.py", "b.py"])


class ParseGoldItemsTests(unittest.TestCase):
    def test_json_string(self):
        raw = json.dumps([{"file": "a.py", "start_line": 1, "end_line": 2}])
        items = parse_gold_items(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["file"], "a.py")

    def test_list_passthrough(self):
        items = parse_gold_items([{"file": "a.py", "start_line": 1, "end_line": 2}])
        self.assertEqual(len(items), 1)

    def test_invalid_json(self):
        self.assertEqual(parse_gold_items("not json"), [])

    def test_none(self):
        self.assertEqual(parse_gold_items(None), [])

    def test_drops_items_without_file(self):
        items = parse_gold_items([{"start_line": 1, "end_line": 2}, {"file": "a.py"}])
        self.assertEqual(len(items), 1)


class GoldFromRowTests(unittest.TestCase):
    def test_parquet_row_gold_context(self):
        row = {
            "instance_id": "X__1",
            "original_inst_id": "owner__repo-1",
            "repo": "owner/repo",
            "repo_url": "https://github.com/owner/repo.git",
            "base_commit": "abc123",
            "language": "rust",
            "source": "Multi",
            "gold_context": json.dumps(
                [{"file": "src/main.rs", "start_line": 3, "end_line": 8, "content": "fn"}]
            ),
            "patch": "--- a/x\n+++ b/x\n",
            "test_patch": "",
        }
        gold = gold_from_row(row)
        self.assertEqual(gold.instance_id, "X__1")
        self.assertEqual(gold.original_inst_id, "owner__repo-1")
        self.assertEqual(gold.repo, "owner/repo")
        self.assertEqual(gold.files(), ["src/main.rs"])
        self.assertEqual(gold.line_spans(), {"src/main.rs": [(3, 8)]})
        self.assertEqual(gold.patch, "--- a/x\n+++ b/x\n")

    def test_init_add_row_kept(self):
        row = {
            "instance_id": "X__2",
            "init_ctx": [{"file": "a.py", "start_line": 1, "end_line": 2}],
            "add_ctx": [{"file": "b.py", "start_line": 3, "end_line": 4}],
        }
        gold = gold_from_row(row)
        self.assertEqual(gold.files(), ["a.py", "b.py"])

    def test_empty_gold_context(self):
        gold = gold_from_row({"instance_id": "X__3", "gold_context": "[]"})
        self.assertEqual(gold.files(), [])
        self.assertEqual(gold.line_count(), 0)


if __name__ == "__main__":
    unittest.main()
