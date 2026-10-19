"""Gold-context representations for ContextBench instances.

Mirrors the parsing semantics of ContextBench
`contextbench/parsers/gold.py` (commit 1436c28a):

- path normalization <- `_normalize_rel_path` (gold.py:10-21): `/testbed/`
  prefixes are stripped, `/workspace/<name>/` keeps the tail after the first
  component, leading `/` and `./` are dropped, separators become `/`;
- init/add merge and the `gold_ctx` fallback <- `Gold.__init__`
  (gold.py:25-40): `init_ctx` + `add_ctx` are used when present, otherwise
  `gold_ctx`. The shipped parquets have a single `gold_context` column, which
  maps to `gold_ctx`;
- file list <- `Gold.files()` (gold.py:41-44): normalized, unique, sorted;
- line spans <- `Gold.line_spans_init()` (gold.py:83-125): per-file inclusive
  line intervals with adjacent ranges merged (`current[0] <= last[1] + 1`).

Byte spans (`Gold.byte_spans`, gold.py:46-64) require the checked-out repo
contents; the pure content-based equivalent is
`metrics.line_spans_to_bytes` and is only usable in the live phase.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


def normalize_rel_path(path_str: str) -> str:
    """Normalize a dataset file path to a repo-relative path.

    Mirror of gold.py:10-21 (`_normalize_rel_path`).
    """
    if not path_str:
        return ""
    p = path_str.replace("\\", "/")
    if p.startswith("/testbed/"):
        return p[len("/testbed/"):]
    if p.startswith("/workspace/"):
        rest = p[len("/workspace/"):]
        parts = rest.split("/", 1)
        return parts[1] if len(parts) == 2 else parts[0]
    if p.startswith("/"):
        return p.lstrip("/")
    return p.lstrip("./")


@dataclass
class GoldContext:
    """Gold context for one instance (parquet row)."""

    instance_id: str
    original_inst_id: str
    repo: str
    repo_url: str
    base_commit: str
    language: str
    source: str
    init: List[dict] = field(default_factory=list)
    add: List[dict] = field(default_factory=list)
    patch: str = ""
    test_patch: str = ""

    @property
    def items(self) -> List[dict]:
        """Merged init+add annotation items (gold.py:26-34 merge order)."""
        return self.init + self.add

    def files(self) -> List[str]:
        """Normalized, unique, sorted file list.

        Mirror of `Gold.files()` (gold.py:41-44).
        """
        return sorted(
            set(
                normalize_rel_path(item.get("file", ""))
                for item in self.items
                if item.get("file")
            )
        )

    def line_spans(self) -> Dict[str, List[Tuple[int, int]]]:
        """Inclusive line intervals per file, adjacent ranges merged.

        Mirror of `Gold.line_spans_init()` (gold.py:83-125). The upstream
        function reads `init_ctx` only because the add_ctx split is used for
        the Multi variant; in the shipped parquets init == gold_ctx, so the
        merged and init-only views coincide. We keep one view over all items
        and document the equivalence.
        """
        result: Dict[str, List[Tuple[int, int]]] = {}
        for item in self.items:
            file_path = normalize_rel_path(item.get("file", ""))
            if not file_path:
                continue
            start_line = item.get("start_line", 1)
            end_line = item.get("end_line", 1)
            result.setdefault(file_path, []).append((start_line, end_line))

        for f in result:
            intervals = result[f]
            if not intervals:
                continue
            sorted_intervals = sorted(intervals)
            merged = [sorted_intervals[0]]
            for current in sorted_intervals[1:]:
                last = merged[-1]
                if current[0] <= last[1] + 1:
                    merged[-1] = (last[0], max(last[1], current[1]))
                else:
                    merged.append(current)
            result[f] = merged
        return result

    def line_count(self) -> int:
        """Total gold lines (sum of merged interval lengths)."""
        return sum(
            end - start + 1
            for intervals in self.line_spans().values()
            for start, end in intervals
        )


def parse_gold_items(raw) -> List[dict]:
    """Parse a `gold_context` cell (JSON string or list) into items."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("file")]


def gold_from_row(row: dict) -> GoldContext:
    """Build a GoldContext from a parquet row dict.

    Mirrors the row conversion in `GoldLoader._load_parquet`
    (gold.py:161-215): the parquet `gold_context` column is the `gold_ctx`
    fallback of the upstream `Gold.__init__` (gold.py:25-40). Rows that
    already carry `init_ctx`/`add_ctx` keys (annot.json format) keep the
    init/add split.
    """
    init = row.get("init_ctx") or []
    add = row.get("add_ctx") or []
    if not init and not add:
        init = parse_gold_items(row.get("gold_context"))
        add = []
    return GoldContext(
        instance_id=str(row.get("instance_id") or row.get("inst_id") or ""),
        original_inst_id=str(row.get("original_inst_id") or ""),
        repo=str(row.get("repo") or ""),
        repo_url=str(row.get("repo_url") or ""),
        base_commit=str(row.get("base_commit") or row.get("commit") or ""),
        language=str(row.get("language") or ""),
        source=str(row.get("source") or ""),
        init=init,
        add=add,
        patch=str(row.get("patch") or ""),
        test_patch=str(row.get("test_patch") or ""),
    )
