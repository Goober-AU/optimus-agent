"""Interval arithmetic shared by the gold-context metrics.

Byte-interval operations mirror ContextBench `contextbench/core/intervals.py`
(commit 1436c28a) in full: `merge_intervals` <- merge (lines 7-18),
`interval_length` <- length (21-23), `intersect_intervals` <- intersect
(25-40), `intersect_size` <- intersect_size (43-45). Line arithmetic lives in
`metrics.py`; here we keep the byte semantics only.

`line_to_byte` mirrors `contextbench/core/fileio.py::line_to_byte`
(lines 5-31) with one deviation for testability: it takes the file content as
bytes instead of a path, so it is pure and offline. The upstream function
returns None for unreadable files; the content-based mirror returns None when
the start line is past the end of the content.
"""

from typing import List, Optional, Tuple

ByteInterval = Tuple[int, int]  # (start, end) inclusive


def merge_intervals(intervals: List[ByteInterval]) -> List[ByteInterval]:
    """Merge overlapping or adjacent byte intervals."""
    if not intervals:
        return []
    sorted_intervals = sorted(intervals)
    merged = [sorted_intervals[0]]
    for current in sorted_intervals[1:]:
        last = merged[-1]
        if current[0] <= last[1] + 1:
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)
    return merged


def interval_length(intervals: List[ByteInterval]) -> int:
    """Total bytes covered by the intervals."""
    return sum(end - start + 1 for start, end in merge_intervals(intervals))


def intersect_intervals(
    a: List[ByteInterval], b: List[ByteInterval]
) -> List[ByteInterval]:
    """Intersection of two interval lists."""
    a_m, b_m = merge_intervals(a), merge_intervals(b)
    result = []
    i, j = 0, 0
    while i < len(a_m) and j < len(b_m):
        overlap = (max(a_m[i][0], b_m[j][0]), min(a_m[i][1], b_m[j][1]))
        if overlap[0] <= overlap[1]:
            result.append(overlap)
        if a_m[i][1] < b_m[j][1]:
            i += 1
        elif b_m[j][1] < a_m[i][1]:
            j += 1
        else:
            i += 1
            j += 1
    return result


def intersect_size(a: List[ByteInterval], b: List[ByteInterval]) -> int:
    """Bytes in the intersection of two interval lists."""
    return interval_length(intersect_intervals(a, b))


def line_to_byte(
    content: bytes, start_line: int, end_line: int
) -> Optional[ByteInterval]:
    """Convert a 1-based inclusive line span to byte offsets in `content`.

    Mirrors the line-offset table construction of the upstream
    `line_to_byte` (fileio.py:5-31): offsets are built from every b"\n",
    `start_line` is clamped to >= 1, `end_line` to >= `start_line`, and the
    end byte is the byte before the next newline (or the last byte of the
    content). An empty content yields (0, 0), matching upstream.
    """
    if not content:
        return (0, 0)

    offsets = [0]
    for i, byte in enumerate(content):
        if byte == ord("\n"):
            offsets.append(i + 1)

    start_line = max(1, start_line)
    end_line = max(start_line, end_line)
    start_idx = start_line - 1
    end_idx = end_line - 1

    if start_idx >= len(offsets):
        return None

    start_byte = offsets[start_idx]
    end_byte = offsets[end_idx + 1] - 1 if end_idx + 1 < len(offsets) else len(content) - 1
    end_byte = max(start_byte, end_byte)

    return (start_byte, end_byte)


def span_total_bytes(spans_by_file: dict) -> int:
    """Total bytes across all files. Mirrors compute.py::span_total_bytes."""
    return sum(interval_length(intervals) for intervals in spans_by_file.values())


def span_intersection_bytes(a: dict, b: dict) -> int:
    """Total intersection bytes across all files.

    Mirrors compute.py::span_intersection_bytes.
    """
    total = 0
    for f in set(a.keys()) | set(b.keys()):
        total += intersect_size(a.get(f, []), b.get(f, []))
    return total
