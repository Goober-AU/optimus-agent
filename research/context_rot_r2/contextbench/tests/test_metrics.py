"""Tests for the metric mirrors (offline, stdlib only)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.contextbench.metrics import (  # noqa: E402
    TrajStep,
    aggregate_records,
    coverage_precision,
    file_metrics,
    granularity_metrics,
    line_intersection_lines,
    line_interval_intersect,
    line_interval_intersect_size,
    line_interval_length,
    line_metrics,
    line_spans_to_bytes,
    line_total_lines,
    merge_line_intervals,
    span_metrics,
    trajectory_metrics,
)


class CoveragePrecisionTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(coverage_precision(10, 10, 5), (0.5, 0.5))

    def test_gold_zero_coverage_one(self):
        self.assertEqual(coverage_precision(5, 0, 0), (1.0, 0.0))

    def test_pred_zero_precision_one(self):
        self.assertEqual(coverage_precision(0, 5, 0), (0.0, 1.0))


class LineIntervalTests(unittest.TestCase):
    def test_merge_adjacent(self):
        self.assertEqual(merge_line_intervals([(1, 5), (6, 10)]), [(1, 10)])

    def test_merge_overlapping(self):
        self.assertEqual(merge_line_intervals([(1, 10), (5, 7)]), [(1, 10)])

    def test_length(self):
        self.assertEqual(line_interval_length([(1, 10)]), 10)

    def test_intersect(self):
        self.assertEqual(line_interval_intersect([(1, 10)], [(5, 20)]), [(5, 10)])
        self.assertEqual(line_interval_intersect([(1, 5)], [(6, 10)]), [])

    def test_intersect_size(self):
        self.assertEqual(line_interval_intersect_size([(1, 10)], [(5, 20)]), 6)

    def test_totals(self):
        a = {"f": [(1, 10)], "g": [(1, 5)]}
        self.assertEqual(line_total_lines(a), 15)

    def test_intersection_lines(self):
        a = {"f": [(1, 10)], "g": [(1, 5)]}
        b = {"f": [(5, 20)], "h": [(1, 2)]}
        self.assertEqual(line_intersection_lines(a, b), 6)


class FileMetricsTests(unittest.TestCase):
    def test_half_overlap(self):
        m = file_metrics({"a.py", "b.py"}, {"b.py", "c.py"})
        self.assertEqual(m["intersection"], 1)
        self.assertAlmostEqual(m["coverage"], 0.5)
        self.assertAlmostEqual(m["precision"], 0.5)

    def test_empty_gold(self):
        m = file_metrics({"a.py"}, set())
        self.assertEqual(m["coverage"], 1.0)
        self.assertEqual(m["precision"], 0.0)


class LineMetricsTests(unittest.TestCase):
    def test_partial(self):
        pred = {"f": [(1, 10)]}
        gold = {"f": [(5, 20)]}
        m = line_metrics(pred, gold)
        self.assertEqual(m["intersection"], 6)
        self.assertAlmostEqual(m["coverage"], 6 / 16)
        self.assertAlmostEqual(m["precision"], 0.6)

    def test_empty_pred(self):
        m = line_metrics({}, {"f": [(1, 10)]})
        self.assertEqual(m["coverage"], 0.0)
        self.assertEqual(m["precision"], 1.0)


class SpanMetricsTests(unittest.TestCase):
    def test_partial(self):
        m = span_metrics({"f": [(0, 9)]}, {"f": [(5, 14)]})
        self.assertEqual(m["intersection"], 5)
        self.assertAlmostEqual(m["coverage"], 0.5)


class GranularityTests(unittest.TestCase):
    def test_shape(self):
        out = granularity_metrics({"a"}, {"a", "b"}, {"a": [(1, 5)]}, {"a": [(1, 10)]})
        self.assertEqual(set(out), {"file", "line"})
        self.assertEqual(out["file"]["intersection"], 1)

    def test_span_branch_included_when_given(self):
        out = granularity_metrics(
            {"a"},
            {"a"},
            {"a": [(1, 5)]},
            {"a": [(1, 5)]},
            pred_spans={"a": [(0, 9)]},
            gold_spans={"a": [(0, 9)]},
        )
        self.assertEqual(out["span"]["coverage"], 1.0)


class LineSpansToBytesTests(unittest.TestCase):
    def test_conversion(self):
        content = b"aa\nbb\ncc\n"
        spans = {"f": [(2, 3)]}
        out = line_spans_to_bytes(spans, {"f": content})
        self.assertEqual(out, {"f": [(3, 8)]})

    def test_missing_content_dropped(self):
        self.assertEqual(line_spans_to_bytes({"f": [(1, 2)]}, {}), {})

    def test_start_past_eof_dropped(self):
        self.assertEqual(line_spans_to_bytes({"f": [(9, 10)]}, {"f": b"aa\n"}), {})


class TrajectoryMetricsTests(unittest.TestCase):
    def _step(self, files, spans):
        return TrajStep(files, [{"file": f, "start_line": s, "end_line": e} for f, s, e in spans])

    def test_empty_steps_zeros(self):
        out = trajectory_metrics([], {"f"}, {"f": [(1, 10)]})
        self.assertEqual(out["auc_coverage"], {"file": 0.0, "line": 0.0})
        self.assertEqual(out["redundancy"], {"file": 0.0, "line": 0.0})

    def test_auc_and_redundancy(self):
        gold_files = {"f", "g"}
        gold_lines = {"f": [(1, 10)], "g": [(1, 10)]}
        steps = [
            self._step(["f"], [("f", 1, 10)]),
            self._step(["f"], [("f", 1, 10)]),
            self._step(["f", "g"], [("g", 1, 10)]),
        ]
        out = trajectory_metrics(steps, gold_files, gold_lines)
        # file coverage by step: 0.5, 0.5, 1.0 -> auc 2/3
        self.assertAlmostEqual(out["auc_coverage"]["file"], (0.5 + 0.5 + 1.0) / 3)
        self.assertAlmostEqual(out["auc_coverage"]["line"], (0.5 + 0.5 + 1.0) / 3)
        # union 2 files over sum 4 step-files -> redundancy 0.5;
        # union 20 lines over 30 step-lines -> redundancy 1/3
        self.assertAlmostEqual(out["redundancy"]["file"], 0.5)
        self.assertAlmostEqual(out["redundancy"]["line"], 1 - 20 / 30)

    def test_no_gold_files_coverage_one(self):
        steps = [self._step(["f"], [("f", 1, 1)])]
        out = trajectory_metrics(steps, set(), {})
        self.assertEqual(out["steps"][0]["coverage"]["file"], 1.0)
        # no gold lines -> line coverage 0.0 (upstream branch)
        self.assertEqual(out["steps"][0]["coverage"]["line"], 0.0)

    def test_redundancy_zero_when_no_reads(self):
        steps = [TrajStep([], [], {})]
        out = trajectory_metrics(steps, {"f"}, {"f": [(1, 10)]})
        self.assertEqual(out["redundancy"], {"file": 0.0, "line": 0.0})


class AggregateTests(unittest.TestCase):
    def _record(self, cov, prec, inter, gold, pred):
        return {
            "instance_id": "i",
            "final": {
                "file": {"coverage": cov, "precision": prec, "intersection": inter,
                         "gold_size": gold, "pred_size": pred}
            },
            "trajectory": {
                "auc_coverage": {"file": cov, "line": cov},
                "redundancy": {"file": prec, "line": prec},
            },
        }

    def test_micro_final_macro_trajectory(self):
        records = [
            self._record(0.5, 0.5, 5, 10, 10),
            self._record(0.0, 0.0, 0, 10, 10),
        ]
        agg = aggregate_records(records)
        self.assertEqual(agg["num_valid"], 2)
        self.assertEqual(agg["num_total"], 2)
        # micro: 5 / 20 on both sides
        self.assertAlmostEqual(agg["final_file"]["coverage"], 0.25)
        self.assertAlmostEqual(agg["final_file"]["precision"], 0.25)
        # macro: mean of 0.5 and 0.0
        self.assertAlmostEqual(agg["traj_auc_file"], 0.25)
        self.assertAlmostEqual(agg["traj_redundancy_file"], 0.25)

    def test_error_records_excluded(self):
        agg = aggregate_records([{"instance_id": "x", "error": "missing_trajectory"}])
        self.assertEqual(agg["num_valid"], 0)
        self.assertEqual(agg["num_total"], 1)

    def test_editloc_micro(self):
        records = [
            {"instance_id": "a", "editloc": {"intersection": 5, "gold_size": 10, "pred_size": 10}},
            {"instance_id": "b", "editloc": {"intersection": 0, "gold_size": 10, "pred_size": 10}},
        ]
        agg = aggregate_records(records)
        self.assertAlmostEqual(agg["editloc"]["recall"], 0.25)


if __name__ == "__main__":
    unittest.main()
