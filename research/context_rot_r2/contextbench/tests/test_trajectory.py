"""Tests for unified-format trajectory records (offline, stdlib only)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.contextbench.trajectory import (  # noqa: E402
    load_trajectory_records,
    record_from_steps,
    to_steps,
)
from research.context_rot_r2.contextbench.metrics import TrajStep  # noqa: E402


def _traj_data():
    return {
        "pred_steps": [
            {
                "files": ["src/a.py"],
                "spans": {"src/a.py": [{"start": 1, "end": 10}]},
                "symbols": {"src/a.py": ["main"]},
            },
            {
                "files": ["src/b.py"],
                "spans": {"src/b.py": [{"start": 5, "end": 8}]},
            },
        ],
        "pred_files": ["src/a.py", "src/b.py"],
        "pred_spans": {
            "src/a.py": [{"start": 1, "end": 10}],
            "src/b.py": [{"start": 5, "end": 8}],
        },
    }


class ToStepsTests(unittest.TestCase):
    def test_steps_and_final(self):
        steps, final = to_steps(_traj_data())
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].files, ["src/a.py"])
        self.assertEqual(
            steps[0].spans,
            [{"file": "src/a.py", "start_line": 1, "end_line": 10}],
        )
        self.assertEqual(steps[0].symbols, {"src/a.py": ["main"]})
        self.assertEqual(sorted(final.files), ["src/a.py", "src/b.py"])
        self.assertEqual(len(final.spans), 2)

    def test_fallback_single_final_step(self):
        data = {
            "pred_steps": [],
            "pred_files": ["x.py"],
            "pred_spans": {"x.py": [{"start": 1, "end": 5}]},
        }
        steps, final = to_steps(data)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].files, ["x.py"])


class LoadRecordsTests(unittest.TestCase):
    def _write(self, records, suffix):
        fd = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="w")
        if suffix == ".jsonl":
            for r in records:
                fd.write(json.dumps(r) + "\n")
        else:
            json.dump(records, fd)
        fd.close()
        self.addCleanup(os.unlink, fd.name)
        return fd.name

    def test_load_jsonl(self):
        path = self._write(
            [
                {
                    "instance_id": "i1",
                    "traj_data": _traj_data(),
                    "model_patch": "",
                    "tool_calls": 7,
                }
            ],
            ".jsonl",
        )
        records = load_trajectory_records(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["instance_id"], "i1")
        self.assertEqual(records[0]["tool_calls"], 7)

    def test_load_json_list(self):
        path = self._write(
            [{"instance_id": "i2", "traj_data": {"pred_steps": []}}], ".json"
        )
        records = load_trajectory_records(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_calls"], 0)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_trajectory_records("does-not-exist.jsonl")

    def test_missing_instance_id_raises(self):
        path = self._write([{"traj_data": {}}], ".jsonl")
        with self.assertRaises(ValueError):
            load_trajectory_records(path)

    def test_missing_traj_data_raises(self):
        path = self._write([{"instance_id": "i3"}], ".jsonl")
        with self.assertRaises(ValueError):
            load_trajectory_records(path)

    def test_empty_file(self):
        fd = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        fd.close()
        self.addCleanup(os.unlink, fd.name)
        self.assertEqual(load_trajectory_records(fd.name), [])


class RecordFromStepsTests(unittest.TestCase):
    def test_roundtrip(self):
        steps = [
            TrajStep(["a.py"], [{"file": "a.py", "start_line": 1, "end_line": 4}])
        ]
        record = record_from_steps(
            "i9", steps, model_patch="patch", tool_calls=3
        )
        self.assertEqual(record["instance_id"], "i9")
        self.assertEqual(record["tool_calls"], 3)
        steps2, final2 = to_steps(record["traj_data"])
        self.assertEqual(steps2[0].files, ["a.py"])
        self.assertEqual(final2.files, ["a.py"])
        self.assertEqual(
            final2.spans, [{"file": "a.py", "start_line": 1, "end_line": 4}]
        )


if __name__ == "__main__":
    unittest.main()
