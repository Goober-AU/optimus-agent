"""ContextBench unified-format trajectory records.

Defines the record shape the harness consumes and the loader that reads it.
The shape follows the upstream custom-agent contract
(`contextbench/parsers/custom_parser.py:12-51`, commit 1436c28a): each
record is `{"instance_id": str, "traj_data": {...}, "model_patch": str}` with

- `traj_data.pred_steps`: list of steps, each `{"files": [...],
  "spans": {file: [{"start": int, "end": int}]}, "symbols": {...}}`;
- `traj_data.pred_files` / `pred_spans`: final-context view;
- `model_patch`: final diff, used by EditLoc.

`to_steps` mirrors `parse_trajectory` (contextbench/parsers/trajectory.py:16-
70): the spans dict is flattened to `{"file", "start_line", "end_line"}`
items and the final step falls back to a single-step trajectory when
`pred_steps` is empty.

One extension over the upstream contract: an optional integer
`tool_calls` field per record (and per step) recording how many tool calls
the agent made, which the A/B scorecard reports. Upstream ignores unknown
keys, so records stay loadable by `contextbench.evaluate` itself.

The Optimus session->trajectory exporter that will produce these records
from session JSONL is live-phase work; nothing here fabricates records.
"""

import json
import os
from typing import Dict, List, Optional, Tuple

from .gold import normalize_rel_path
from .metrics import TrajStep


def normalize_step_paths(step: TrajStep) -> TrajStep:
    """Normalize a step's file paths like the gold side.

    Offline stand-in for the path normalization half of the upstream
    `_filter_step_to_repo` (evaluate.py:139-190): container prefixes
    (`/testbed/`, `/workspace/<x>/`), leading `/`, `./` and backslashes
    are stripped from predicted paths so they compare against the
    normalized gold paths. The checkout-existence and suffix-matching
    halves of the upstream filter need a worktree and stay live-phase.
    """
    files = [normalize_rel_path(f) for f in step.files if normalize_rel_path(f)]
    spans = []
    for span in step.spans:
        f = normalize_rel_path(span.get("file", ""))
        if f:
            spans.append(dict(span, file=f))
    symbols = {}
    for f, names in (step.symbols or {}).items():
        rel = normalize_rel_path(f)
        if rel and names:
            symbols[rel] = list(names)
    return TrajStep(files, spans, symbols)


def _spans_dict_to_items(
    spans_dict: Optional[Dict[str, List[dict]]]
) -> List[dict]:
    """Flatten {file: [{"start", "end"}]} to step span items.

    Mirror of the span conversion in `parse_trajectory`
    (trajectory.py:29-37).
    """
    spans = []
    for file_path, file_spans in (spans_dict or {}).items():
        for span in file_spans:
            spans.append(
                {
                    "file": file_path,
                    "start_line": span["start"],
                    "end_line": span["end"],
                }
            )
    return spans


def to_steps(traj_data: dict) -> Tuple[List[TrajStep], TrajStep]:
    """Split a record's traj_data into (trajectory_steps, final_step).

    Mirror of `parse_trajectory` (trajectory.py:16-70), including the
    fallback that an empty `pred_steps` list becomes a single final step.
    """
    steps = []
    for step_data in traj_data.get("pred_steps", []):
        files = step_data.get("files", [])
        spans = _spans_dict_to_items(step_data.get("spans", {}))
        symbols = step_data.get("symbols", {}) or {}
        steps.append(TrajStep(files, spans, symbols))

    final_files = traj_data.get("pred_files", [])
    final_spans = _spans_dict_to_items(traj_data.get("pred_spans", {}))
    final_symbols = traj_data.get("pred_symbols", {}) or {}
    final_step = TrajStep(final_files, final_spans, final_symbols)

    if not steps:
        steps = [final_step]

    return steps, final_step


def load_trajectory_records(path: str) -> List[dict]:
    """Load unified-format records from a JSONL or JSON file.

    Unlike the upstream `load_pred` (trajectory.py:235-329) this loader
    accepts only the unified custom format: it never inspects agent-specific
    layouts and never touches the network. Invalid records raise ValueError
    instead of being silently dropped, so a broken export is visible.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return []

    if path.endswith(".jsonl"):
        raw_records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        obj = json.loads(text)
        raw_records = obj if isinstance(obj, list) else [obj]

    records = []
    for i, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise ValueError(f"record {i} is not an object: {type(raw).__name__}")
        instance_id = raw.get("instance_id") or raw.get("original_inst_id")
        if not instance_id:
            raise ValueError(f"record {i} has no instance_id")
        traj_data = raw.get("traj_data")
        if not isinstance(traj_data, dict):
            raise ValueError(f"record {i} ({instance_id}) has no traj_data object")
        records.append(
            {
                "instance_id": str(instance_id),
                "traj_data": traj_data,
                "model_patch": str(raw.get("model_patch", "") or ""),
                "repo_url": str(raw.get("repo_url", "") or ""),
                "commit": str(raw.get("commit", "") or ""),
                "tool_calls": int(raw.get("tool_calls", 0) or 0),
            }
        )
    return records


def record_from_steps(
    instance_id: str,
    steps: List[TrajStep],
    final_step: Optional[TrajStep] = None,
    model_patch: str = "",
    tool_calls: int = 0,
    repo_url: str = "",
    commit: str = "",
) -> dict:
    """Build a unified-format record from TrajStep objects.

    The inverse of `to_steps`; used by exporters and tests.
    """
    if final_step is None:
        final_step = steps[-1] if steps else TrajStep()

    def _items_to_dict(spans):
        out: Dict[str, List[dict]] = {}
        for span in spans:
            out.setdefault(span["file"], []).append(
                {"start": span["start_line"], "end": span["end_line"]}
            )
        return out

    return {
        "instance_id": instance_id,
        "traj_data": {
            "pred_steps": [
                {
                    "files": list(step.files),
                    "spans": _items_to_dict(step.spans),
                    "symbols": dict(step.symbols),
                }
                for step in steps
            ],
            "pred_files": list(final_step.files),
            "pred_spans": _items_to_dict(final_step.spans),
        },
        "model_patch": model_patch,
        "repo_url": repo_url,
        "commit": commit,
        "tool_calls": tool_calls,
    }
