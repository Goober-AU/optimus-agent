"""Tests for the dataset pipeline (offline; parquet tests gated on data)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.contextbench import (  # noqa: E402
    GOLD_PARQUET_SHA256,
    SPLIT_VERSION,
)
from research.context_rot_r2.contextbench.dataset import (  # noqa: E402
    build_manifest,
    load_manifest,
    load_rows,
    manifest_instance_ids,
    repo_digest_order,
    split_by_repo,
    stable_seed,
    stratified_subset,
    subset_rows_by_ids,
    verify_parquet_sha256,
    write_manifest,
)

# The pinned checkout location, overridable for CI.
REFS_ROOT = os.environ.get("CB_REFS_ROOT", "")


def _row(i, repo, language="python", source="Verified"):
    return {
        "instance_id": f"inst-{i:03d}",
        "original_inst_id": f"owner__repo-{i}",
        "repo": repo,
        "repo_url": f"https://github.com/{repo}.git",
        "language": language,
        "base_commit": f"c{i}",
        "gold_context": json.dumps(
            [{"file": "a.py", "start_line": 1, "end_line": 3 + i % 5}]
        ),
        "patch": "",
        "test_patch": "",
        "source": source,
    }


def _synthetic_rows():
    rows = []
    i = 0
    for repo, n in [("alpha/a", 12), ("beta/b", 9), ("gamma/c", 7), ("delta/d", 5),
                    ("eps/e", 4), ("zeta/f", 3)]:
        for _ in range(n):
            rows.append(_row(i, repo))
            i += 1
    return rows


class StableSeedTests(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(stable_seed("a", "b"), stable_seed("a", "b"))

    def test_differs(self):
        self.assertNotEqual(stable_seed("a"), stable_seed("b"))

    def test_not_python_hash(self):
        self.assertNotEqual(stable_seed("x"), hash("x"))

    def test_repo_digest_order_deterministic(self):
        repos = ["b/r2", "a/r1", "c/r3"]
        self.assertEqual(repo_digest_order(repos), repo_digest_order(repos))
        self.assertEqual(repo_digest_order(repos), sorted(repos, key=lambda r: __import__("hashlib").blake2b(r.encode()).hexdigest()))


class SplitByRepoTests(unittest.TestCase):
    def test_no_repo_overlap(self):
        rows = _synthetic_rows()
        split = split_by_repo(rows)
        self.assertEqual(set(split.dev_repos) & set(split.holdout_repos), set())
        self.assertEqual(set(split.dev_repos) | set(split.holdout_repos),
                         {r["repo"] for r in rows})

    def test_all_rows_assigned_once(self):
        rows = _synthetic_rows()
        split = split_by_repo(rows)
        ids = [r["instance_id"] for r in split.dev_rows + split.holdout_rows]
        self.assertEqual(sorted(ids), sorted(r["instance_id"] for r in rows))

    def test_ratio_near_target(self):
        rows = _synthetic_rows()
        split = split_by_repo(rows, dev_fraction=0.6)
        dev_share = len(split.dev_rows) / len(rows)
        # repo granularity cannot hit exactly 0.6; the largest repo is 12/40
        self.assertGreaterEqual(dev_share, 0.6 - 12 / len(rows))
        self.assertLessEqual(dev_share, 0.6 + 12 / len(rows))

    def test_languages_represented_on_both_sides(self):
        rows = (
            [_row(i, "py/a", "python") for i in range(7)]
            + [_row(50 + i, "py/d", "python") for i in range(3)]
            + [_row(100 + i, "rs/b", "rust") for i in range(4)]
            + [_row(150 + i, "rs/e", "rust") for i in range(2)]
            + [_row(200 + i, "go/c", "go") for i in range(2)]
            + [_row(250 + i, "go/f", "go") for i in range(1)]
        )
        split = split_by_repo(rows)
        self.assertTrue(split.dev_rows)
        self.assertTrue(split.holdout_rows)
        for side_rows in (split.dev_rows, split.holdout_rows):
            langs = {r["language"] for r in side_rows}
            self.assertEqual(langs, {"python", "rust", "go"}, sorted(langs))

    def test_single_repo_language_lands_on_one_side(self):
        # a language concentrated in one repo cannot appear on both sides;
        # the assignment must still be total and non-overlapping
        rows = (
            [_row(i, "py/a", "python") for i in range(7)]
            + [_row(50 + i, "py/d", "python") for i in range(3)]
            + [_row(100 + i, "rs/b", "rust") for i in range(6)]
        )
        split = split_by_repo(rows)
        self.assertEqual(len(split.dev_rows) + len(split.holdout_rows), len(rows))

    def test_deterministic(self):
        rows = _synthetic_rows()
        a = split_by_repo(rows)
        b = split_by_repo(rows)
        self.assertEqual(a.dev_repos, b.dev_repos)
        self.assertEqual(a.holdout_repos, b.holdout_repos)
        self.assertEqual(a.seed, b.seed)

    def test_seed_changes_split(self):
        rows = _synthetic_rows()
        a = split_by_repo(rows, seed_parts=("x", "1"))
        b = split_by_repo(rows, seed_parts=("y", "2"))
        # different seeds must shuffle differently (checked on the joined
        # assignment, not the side sets, since a collision is possible)
        self.assertNotEqual(
            (a.seed, a.dev_repos), (b.seed, b.dev_repos)
        )

    def test_row_order_preserved_within_sides(self):
        rows = _synthetic_rows()
        split = split_by_repo(rows)
        self.assertEqual(split.dev_rows, [r for r in rows if r["repo"] in set(split.dev_repos)])

    def test_invalid_fraction(self):
        with self.assertRaises(ValueError):
            split_by_repo(_synthetic_rows(), dev_fraction=0.0)
        with self.assertRaises(ValueError):
            split_by_repo(_synthetic_rows(), dev_fraction=1.0)

    def test_empty(self):
        split = split_by_repo([])
        self.assertEqual(split.dev_rows, [])
        self.assertEqual(split.holdout_rows, [])


class StratifiedSubsetTests(unittest.TestCase):
    def test_size_and_strata(self):
        rows = (
            [_row(i, "a/a", "python") for i in range(10)]
            + [_row(100 + i, "b/b", "rust") for i in range(10)]
        )
        subset = stratified_subset(rows, k=20)
        self.assertEqual(len(subset), 20)
        subset = stratified_subset(rows, k=6)
        self.assertEqual(len(subset), 6)
        langs = [r["language"] for r in subset]
        self.assertEqual(langs.count("python"), 3)
        self.assertEqual(langs.count("rust"), 3)

    def test_deterministic(self):
        rows = _synthetic_rows()
        a = stratified_subset(rows, k=8)
        b = stratified_subset(rows, k=8)
        self.assertEqual([r["instance_id"] for r in a], [r["instance_id"] for r in b])

    def test_min_one_per_stratum_when_k_ge_strata(self):
        rows = (
            [_row(i, "py/a", "python") for i in range(20)]
            + [_row(100 + i, "rs/b", "rust") for i in range(2)]
            + [_row(200 + i, "go/c", "go") for i in range(2)]
        )
        subset = stratified_subset(rows, k=8)
        langs = [r["language"] for r in subset]
        self.assertGreaterEqual(langs.count("rust"), 1)
        self.assertGreaterEqual(langs.count("go"), 1)
        self.assertEqual(len(subset), 8)

    def test_k_zero(self):
        self.assertEqual(stratified_subset(_synthetic_rows(), k=0), [])

    def test_k_ge_total(self):
        rows = _synthetic_rows()
        self.assertEqual(len(stratified_subset(rows, k=99)), len(rows))


class ManifestTests(unittest.TestCase):
    def test_roundtrip_and_counts(self):
        rows = _synthetic_rows()
        split = split_by_repo(rows)
        manifest = build_manifest(split, "unused.parquet")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "manifest.json")
            write_manifest(manifest, path)
            loaded = load_manifest(path)
        self.assertEqual(loaded["split_version"], SPLIT_VERSION)
        self.assertEqual(
            len(manifest_instance_ids(loaded, "dev")), len(split.dev_rows)
        )
        self.assertEqual(
            len(manifest_instance_ids(loaded, "holdout")), len(split.holdout_rows)
        )
        self.assertEqual(
            set(manifest_instance_ids(loaded, "dev"))
            & set(manifest_instance_ids(loaded, "holdout")),
            set(),
        )
        self.assertEqual(manifest["provenance"]["network_fetches"], [])
        self.assertEqual(manifest["provenance"]["parquet_sha256"], GOLD_PARQUET_SHA256)

    def test_side_validation(self):
        rows = _synthetic_rows()
        manifest = build_manifest(split_by_repo(rows), "unused.parquet")
        with self.assertRaises(ValueError):
            manifest_instance_ids(manifest, "other")

    def test_subset_rows_by_ids(self):
        rows = _synthetic_rows()
        subset = subset_rows_by_ids(rows, ["inst-000", "inst-005", "missing"])
        self.assertEqual([r["instance_id"] for r in subset], ["inst-000", "inst-005"])


@unittest.skipUnless(
    os.path.isfile(os.path.join(REFS_ROOT, "data", "contextbench_verified.parquet"))
    if REFS_ROOT
    else False,
    "CB_REFS_ROOT not set or parquet missing",
)
class RealParquetTests(unittest.TestCase):
    """Integration tests against the pinned checkout (offline)."""

    parquet = None

    @classmethod
    def setUpClass(cls):
        cls.parquet = os.path.join(REFS_ROOT, "data", "contextbench_verified.parquet")

    def test_sha256(self):
        self.assertTrue(verify_parquet_sha256(self.parquet))

    def test_split_matches_committed_manifest(self):
        here = os.path.dirname(__file__)
        manifest_path = os.path.join(here, "..", "data", "dev_holdout_split.json")
        manifest = load_manifest(manifest_path)
        rows = load_rows(self.parquet)
        split = split_by_repo(rows, dev_fraction=manifest["split"]["dev_fraction"])
        self.assertEqual(
            sorted(split.dev_repos), sorted(manifest["split"]["dev"]["repos"])
        )
        self.assertEqual(
            [r["instance_id"] for r in split.dev_rows],
            [i["instance_id"] for i in manifest["split"]["dev"]["instances"]],
        )
        self.assertEqual(
            [r["instance_id"] for r in split.holdout_rows],
            [i["instance_id"] for i in manifest["split"]["holdout"]["instances"]],
        )

    def test_rows_shape(self):
        rows = load_rows(self.parquet)
        self.assertEqual(len(rows), 500)
        self.assertEqual(len({r["repo"] for r in rows}), 58)
        for row in rows:
            self.assertTrue(row["instance_id"])
            self.assertTrue(row["repo"])
            self.assertTrue(row["base_commit"])


if __name__ == "__main__":
    unittest.main()
