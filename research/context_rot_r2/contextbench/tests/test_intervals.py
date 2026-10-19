"""Tests for the byte-interval mirrors (offline, stdlib only)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from research.context_rot_r2.contextbench.intervals import (  # noqa: E402
    intersect_intervals,
    intersect_size,
    interval_length,
    line_to_byte,
    merge_intervals,
    span_intersection_bytes,
    span_total_bytes,
)


class MergeTests(unittest.TestCase):
    def test_merge_overlapping(self):
        self.assertEqual(merge_intervals([(0, 10), (5, 20)]), [(0, 20)])

    def test_merge_adjacent(self):
        self.assertEqual(merge_intervals([(0, 10), (11, 20)]), [(0, 20)])

    def test_merge_disjoint_stays(self):
        self.assertEqual(merge_intervals([(0, 10), (12, 20)]), [(0, 10), (12, 20)])

    def test_merge_unsorted_input(self):
        self.assertEqual(merge_intervals([(11, 20), (0, 10)]), [(0, 20)])

    def test_empty(self):
        self.assertEqual(merge_intervals([]), [])


class LengthTests(unittest.TestCase):
    def test_length_counts_once(self):
        self.assertEqual(interval_length([(0, 9), (5, 14)]), 15)

    def test_empty(self):
        self.assertEqual(interval_length([]), 0)


class IntersectTests(unittest.TestCase):
    def test_intersect_partial(self):
        self.assertEqual(intersect_intervals([(0, 10)], [(5, 20)]), [(5, 10)])

    def test_intersect_disjoint(self):
        self.assertEqual(intersect_intervals([(0, 10)], [(11, 20)]), [])

    def test_intersect_multiple(self):
        a = [(0, 10), (20, 30)]
        b = [(5, 7), (22, 25)]
        self.assertEqual(intersect_intervals(a, b), [(5, 7), (22, 25)])

    def test_intersect_size(self):
        self.assertEqual(intersect_size([(0, 10)], [(5, 20)]), 6)


class LineToByteTests(unittest.TestCase):
    def test_empty_content(self):
        self.assertEqual(line_to_byte(b"", 1, 3), (0, 0))

    def test_single_line(self):
        self.assertEqual(line_to_byte(b"hello", 1, 1), (0, 4))

    def test_multi_line(self):
        content = b"aa\nbb\ncc\n"
        # offsets [0, 3, 6, 9]; line 2 = bytes 3..5, lines 1-3 = 0..8
        self.assertEqual(line_to_byte(content, 2, 2), (3, 5))
        self.assertEqual(line_to_byte(content, 1, 3), (0, 8))

    def test_no_trailing_newline(self):
        content = b"aa\nbb"
        self.assertEqual(line_to_byte(content, 2, 2), (3, 4))

    def test_start_past_eof(self):
        self.assertIsNone(line_to_byte(b"aa\n", 5, 6))

    def test_end_past_eof_clamps(self):
        self.assertEqual(line_to_byte(b"aa\nbb", 2, 99), (3, 4))

    def test_start_clamped_to_one(self):
        self.assertEqual(line_to_byte(b"aa\n", 0, 1), (0, 2))


class SpanDictTests(unittest.TestCase):
    def test_span_total_and_intersection(self):
        a = {"f.py": [(0, 9)]}
        b = {"f.py": [(5, 14)]}
        self.assertEqual(span_total_bytes(a), 10)
        self.assertEqual(span_intersection_bytes(a, b), 5)

    def test_disjoint_files_no_intersection(self):
        a = {"f.py": [(0, 9)]}
        b = {"g.py": [(0, 9)]}
        self.assertEqual(span_intersection_bytes(a, b), 0)


if __name__ == "__main__":
    unittest.main()
