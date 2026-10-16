"""Synthetic, offline unittest coverage for the PersonaMem-v1 converter (pmem).

Fixtures: the committed tiny 32k split under bench/fixtures/raw/
(questions_32k.csv + shared_contexts_32k.jsonl; 4 rows, 2 personas, 2 envs).
The tests pin the PR-1 review behaviors: per-question env slicing (a
question never sees context beyond its end_index), gold-letter extraction,
question_type alias normalization, manifest schema, and byte determinism.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bench.converters import pmem
from bench.converters.common import SEED
from bench.converters.pmem import (build_env, build_persona_variants, canonical_type,
                                   context_sessions, gold_letter, parse_options,
                                   presented_question)
from bench.protocol import QUESTION_PROTOCOLS, SCORING_PROTOCOLS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(ROOT, "bench", "fixtures", "raw")


def load_fixture_rows():
    with open(os.path.join(FIXTURES, "questions_32k.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_fixture_contexts():
    contexts = {}
    with open(os.path.join(FIXTURES, "shared_contexts_32k.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                contexts.update(json.loads(line))
    return contexts


def with_roots(fn):
    """Run fn(data_root, common_root) inside temp dirs (monkeypatched module roots)."""
    import bench.converters.common as common
    tmp = tempfile.TemporaryDirectory()
    try:
        with mock.patch.object(common, "DATA_ROOT", FIXTURES), \
                mock.patch.object(common, "COMMON_ROOT", os.path.join(tmp.name, "common")):
            return fn(FIXTURES, os.path.join(tmp.name, "common")), tmp
    finally:
        pass


class GoldLetterTests(unittest.TestCase):
    def test_parenthesized_and_bare_letters_normalize(self):
        self.assertEqual(gold_letter("(A)"), "a")
        self.assertEqual(gold_letter(" b "), "b")
        self.assertEqual(gold_letter("(D)"), "d")

    def test_rejects_non_single_letter(self):
        for bad in ("", "(e)", "ab", "(a)(b)"):
            with self.assertRaises(ValueError):
                gold_letter(bad)


class AliasNormalizationTests(unittest.TestCase):
    def test_generation_aliases_map_to_canonical(self):
        self.assertEqual(canonical_type("recalling_facts_mentioned_by_the_user"),
                         "recall_user_shared_facts")
        self.assertEqual(canonical_type("identifying_new_things_not_mentioned_by_the_user"),
                         "suggest_new_ideas")
        self.assertEqual(canonical_type("tracking_the_full_sequence_of_preference_updates"),
                         "track_full_preference_evolution")

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            canonical_type("not_a_real_type")


class SlicingTests(unittest.TestCase):
    def test_env_contains_only_messages_up_to_end_index(self):
        context = [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "FUTURE-u2"},
            {"role": "assistant", "content": "FUTURE-a2"},
        ]
        env = build_env("ctx", context[:3], 3, (1, 1, 1))
        events = [e for s in env["sessions"] for e in s["events"]]
        self.assertEqual(len(events), 3)
        joined = "\n".join(e["text"] for e in events)
        self.assertNotIn("FUTURE", joined)
        self.assertEqual(env["notes"]["env_mode"], "per_question_slice")
        self.assertEqual(env["notes"]["end_index_in_shared_context"], 3)

    def test_sessions_split_on_system_messages(self):
        context = [
            {"role": "system", "content": "p1"},
            {"role": "user", "content": "u1"},
            {"role": "system", "content": "p2"},
            {"role": "user", "content": "u2"},
        ]
        sessions = context_sessions(context)
        self.assertEqual([s["session_id"] for s in sessions], ["s1", "s2"])
        self.assertEqual([e["text"] for e in sessions[0]["events"]],
                         ["System: p1", "u1"])

    def test_person_a_variant_metadata(self):
        rows = load_fixture_rows()
        meta = build_persona_variants(rows)
        self.assertEqual(meta["c-p1v1"], (1, 1, 1))
        self.assertEqual(meta["c-p2v1"], (2, 1, 1))


class BuildTests(unittest.TestCase):
    def run_build(self, common_root):
        import bench.converters.common as common
        with mock.patch.object(common, "DATA_ROOT", FIXTURES), \
                mock.patch.object(common, "COMMON_ROOT", common_root):
            return pmem.build(dev_only=False)

    def test_manifest_schema_and_counts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        common_root = os.path.join(tmp.name, "common")
        manifest = self.run_build(common_root)
        self.assertEqual(manifest["family"], "pmem")
        self.assertEqual(manifest["seed"], SEED)
        self.assertEqual(manifest["n_envs"], 2)
        self.assertEqual(manifest["n_questions"], 4)
        split_dir = os.path.join(common_root, "pmem", "32k")
        for entry in manifest["envs"]:
            self.assertTrue(os.path.isfile(os.path.join(split_dir, entry["file"])))
            self.assertTrue(os.path.isfile(os.path.join(split_dir, entry["qs_file"])))
            self.assertGreater(entry["n_questions"], 0)
        full_dir = os.path.join(split_dir, "full")
        self.assertTrue(os.path.isfile(os.path.join(full_dir, "manifest.json")))

    def test_question_shape_gold_and_alias_canonicalized(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        common_root = os.path.join(tmp.name, "common")
        self.run_build(common_root)
        split_dir = os.path.join(common_root, "pmem", "32k")
        questions = []
        for name in sorted(os.listdir(split_dir)):
            if name.startswith("qs_"):
                with open(os.path.join(split_dir, name), encoding="utf-8") as f:
                    questions += [json.loads(line) for line in f if line.strip()]
        by_qid = {q["qid"]: q for q in questions}
        # row 0: alias type recalling_facts_mentioned_by_the_user, gold (A)
        q0 = by_qid["pm1_r0"]
        self.assertEqual(q0["answer"], "a")
        self.assertEqual(q0["category"], "recall_user_shared_facts")
        # row 1: alias identifying_new_things_not_mentioned_by_the_user, gold (B)
        self.assertEqual(by_qid["pm1_r1"]["category"], "suggest_new_ideas")
        self.assertEqual(by_qid["pm1_r1"]["answer"], "b")
        for q in questions:
            self.assertEqual(q["family"], "pmem")
            self.assertEqual(q["question_protocol"], QUESTION_PROTOCOLS["pmem"])
            self.assertEqual(q["eval"]["protocol"], SCORING_PROTOCOLS["pmem"])
            self.assertEqual(q["eval"]["type"], "pmem_mcq")
            self.assertIn(q["eval"]["params"]["answer"], "abcd")

    def test_env_of_question_matches_row_end_index(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        common_root = os.path.join(tmp.name, "common")
        self.run_build(common_root)
        split_dir = os.path.join(common_root, "pmem", "32k")
        questions = []
        for name in sorted(os.listdir(split_dir)):
            if name.startswith("qs_"):
                with open(os.path.join(split_dir, name), encoding="utf-8") as f:
                    questions += [json.loads(line) for line in f if line.strip()]
        by_qid = {q["qid"]: q for q in questions}
        rows = load_fixture_rows()
        for idx, row in enumerate(rows):
            env_id = by_qid[f"pm1_r{idx}"]["env_id"]
            self.assertIn(f"e{row['end_index_in_shared_context']}", env_id)
            env_path = os.path.join(split_dir, f"env_{env_id}.json")
            env = json.load(open(env_path, encoding="utf-8"))
            n_events = sum(len(s["events"]) for s in env["sessions"])
            self.assertEqual(n_events, int(row["end_index_in_shared_context"]))

    def test_build_is_deterministic(self):
        out = []
        for _ in range(2):
            tmp = tempfile.TemporaryDirectory()
            try:
                common_root = os.path.join(tmp.name, "common")
                self.run_build(common_root)
                split_dir = os.path.join(common_root, "pmem", "32k")
                out.append({name: open(os.path.join(split_dir, name), "rb").read()
                            for name in sorted(os.listdir(split_dir))
                            if os.path.isfile(os.path.join(split_dir, name))})
            finally:
                tmp.cleanup()
        self.assertEqual(out[0], out[1])


if __name__ == "__main__":
    unittest.main()
