"""Deterministic slice and transcript construction (W27 live practice).

Everything here is a pure function of the parquet rows and stable
blake2b-derived seeds: the same inputs always produce the same slice, the
same exploration order and the same distractor picks.

Slice rule (documented in README.md, never silently applied):

1. candidate pool = dev-side instances of the committed split manifest
   (`contextbench/data/dev_holdout_split.json`) whose gold context has
   4..12 annotation items and 1200..9000 total content characters;
2. within each language, candidates are ordered by
   blake2b("contextbench|w27-live-slice|w27.1|" + instance_id);
3. languages are ordered by descending candidate count (ties: language
   name) and instances are taken round-robin across languages until
   SLICE_SIZE instances are selected, so every language in the pool is
   represented;
4. the holdout side is never touched, not even for distractors.

Exploration transcript per instance (deterministic):

- turn 0 (user): repo, base commit, the problem statement (capped at
  12000 chars with an explicit truncation marker), and the instruction to
  explore the repository;
- then one user/assistant pair per read: the user asks for a file span,
  the assistant echoes the span content with absolute line numbers (the
  environment/tool-result stand-in);
- the read list = gold items + distractor items (gold annotations of
  OTHER dev instances, same repo preferred, then same language, then any
  dev; exact (file, start, end) duplicates of the target's gold excluded;
  distractor item content capped at 2500 chars to bound the transcript),
  shuffled with random.Random(stable_seed("contextbench",
  "w27-explore-order", "w27.1", instance_id));
- the read list is split into CYCLES near-equal chunks (earlier chunks
  take the remainder), one chunk appended per cycle.
"""

import hashlib
import json
import random
from typing import Dict, List, Optional, Sequence, Tuple

from .. import dataset as cb_dataset
from ..gold import normalize_rel_path, parse_gold_items
from .. import gold as cb_gold
from . import (
    CYCLES,
    DISTRACTOR_SEED_PARTS,
    EXPLORE_SEED_PARTS,
    KEEP_FRACTION,
    KEEP_MIN,
    RESERVE_FRACTION,
    RESERVE_MIN,
    SLICE_MAX_CHARS,
    SLICE_MAX_ITEMS,
    SLICE_MIN_CHARS,
    SLICE_MIN_ITEMS,
    SLICE_SEED_PARTS,
    SLICE_SIZE,
)

PROBLEM_STATEMENT_CAP = 12_000
DISTRACTOR_ITEM_MIN_CHARS = 100
DISTRACTOR_ITEM_MAX_CHARS = 2_500
DISTRACTOR_MAX_COUNT = 6


def stable_seed(*parts: str) -> int:
    """blake2b-derived seed (same pattern as dataset.stable_seed)."""
    digest = hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def blake2b_hex(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8")).hexdigest()


def gold_items(row: dict) -> List[dict]:
    """Normalized gold annotation items of a parquet row."""
    items = []
    for item in parse_gold_items(row.get("gold_context")):
        path = normalize_rel_path(item.get("file", ""))
        if not path:
            continue
        start = int(item.get("start_line") or 1)
        end = int(item.get("end_line") or 1)
        items.append(
            {
                "file": path,
                "start_line": start,
                "end_line": end,
                "content": str(item.get("content") or ""),
            }
        )
    return items


def gold_chars(row: dict) -> int:
    return sum(len(item["content"]) for item in gold_items(row))


def slice_candidates(rows: Sequence[dict], dev_ids: set) -> List[dict]:
    """Candidate pool: dev-side rows inside the size window."""
    candidates = []
    for row in rows:
        if row["instance_id"] not in dev_ids:
            continue
        items = gold_items(row)
        n_items = len(items)
        chars = sum(len(i["content"]) for i in items)
        if SLICE_MIN_ITEMS <= n_items <= SLICE_MAX_ITEMS and SLICE_MIN_CHARS <= chars <= SLICE_MAX_CHARS:
            candidates.append(row)
    return candidates


def select_slice(rows: Sequence[dict], dev_ids: set) -> List[dict]:
    """The deterministic W27 live slice (see module docstring)."""
    candidates = slice_candidates(rows, dev_ids)
    by_language: Dict[str, List[dict]] = {}
    for row in candidates:
        by_language.setdefault(row["language"], []).append(row)
    for language in by_language:
        by_language[language].sort(
            key=lambda row: blake2b_hex("|".join(SLICE_SEED_PARTS) + "|" + row["instance_id"])
        )
    languages = sorted(
        by_language, key=lambda lang: (-len(by_language[lang]), lang)
    )
    picked: List[dict] = []
    cursor = {lang: 0 for lang in languages}
    round_index = 0
    while len(picked) < SLICE_SIZE and any(cursor[l] < len(by_language[l]) for l in languages):
        language = languages[round_index % len(languages)]
        round_index += 1
        pool = by_language[language]
        if cursor[language] < len(pool):
            picked.append(pool[cursor[language]])
            cursor[language] += 1
    return picked[:SLICE_SIZE]


def pick_distractors(target: dict, rows: Sequence[dict], dev_ids: set) -> List[dict]:
    """Deterministic distractor items from other dev instances.

    Preference order: same repo, then same language, then any dev row.
    Ordering inside each tier is blake2b over stable identifiers, so the
    pick is reproducible. Items whose content is outside
    [DISTRACTOR_ITEM_MIN_CHARS, DISTRACTOR_ITEM_MAX_CHARS] are skipped to
    bound the transcript; items matching a target gold (file, start, end)
    exactly are skipped (same file, different lines is allowed and counts
    as a realistic re-read).
    """
    target_items = gold_items(target)
    target_keys = {(i["file"], i["start_line"], i["end_line"]) for i in target_items}
    want = min(DISTRACTOR_MAX_COUNT, len(target_items))

    def tier_key(source: dict, item: dict, index: int) -> str:
        return blake2b_hex(
            "|".join(DISTRACTOR_SEED_PARTS)
            + f"|{target['instance_id']}|{source['instance_id']}|{index}"
        )

    tiers = [
        lambda row: row["repo"] == target["repo"],
        lambda row: row["language"] == target["language"],
        lambda row: True,
    ]
    chosen: List[dict] = []
    seen = set()
    for tier in tiers:
        if len(chosen) >= want:
            break
        candidates = []
        for row in rows:
            if row["instance_id"] == target["instance_id"] or row["instance_id"] not in dev_ids:
                continue
            if not tier(row):
                continue
            for index, item in enumerate(gold_items(row)):
                path = normalize_rel_path(item.get("file", ""))
                if not path:
                    continue
                chars = len(item.get("content") or "")
                if not (DISTRACTOR_ITEM_MIN_CHARS <= chars <= DISTRACTOR_ITEM_MAX_CHARS):
                    continue
                key = (path, item["start_line"], item["end_line"])
                if key in target_keys or key in seen:
                    continue
                candidates.append((tier_key(row, item, index), key, row, item))
        candidates.sort(key=lambda entry: entry[0])
        for _, key, row, item in candidates:
            if len(chosen) >= want:
                break
            if key in seen:
                continue
            seen.add(key)
            chosen.append(
                {
                    "file": key[0],
                    "start_line": key[1],
                    "end_line": key[2],
                    "content": item["content"],
                    "source_instance": row["instance_id"],
                }
            )
    return chosen


def explore_order(target: dict, distractors: Sequence[dict]) -> List[dict]:
    """Gold + distractor reads, deterministically shuffled."""
    reads = []
    for item in gold_items(target):
        reads.append(dict(item, kind="gold"))
    for item in distractors:
        reads.append(dict(item, kind="distractor"))
    rng = random.Random(stable_seed(*EXPLORE_SEED_PARTS, target["instance_id"]))
    rng.shuffle(reads)
    return reads


def number_span_content(content: str, start_line: int) -> str:
    """Line-numbered rendering of a span's content."""
    lines = content.splitlines()
    return "\n".join(
        f"{start_line + index:6d}\t{line}" for index, line in enumerate(lines)
    )


def build_transcript(target: dict, reads: Sequence[dict]) -> List[dict]:
    """The full turn list: problem statement + one pair per read."""
    statement = str(target.get("problem_statement") or "").strip()
    if len(statement) > PROBLEM_STATEMENT_CAP:
        statement = statement[:PROBLEM_STATEMENT_CAP] + "\n...[truncated]"
    turns = [
        {
            "role": "user",
            "content": (
                f"Repository task. Repo: {target['repo']} at commit "
                f"{target['base_commit']}.\n\nIssue:\n{statement}\n\n"
                "Explore the repository files to resolve this issue."
            ),
        }
    ]
    for read in reads:
        turns.append(
            {
                "role": "user",
                "content": (
                    f"Show repository file {read['file']} lines "
                    f"{read['start_line']}-{read['end_line']}."
                ),
            }
        )
        turns.append(
            {
                "role": "assistant",
                "content": (
                    f"[{read['file']}:{read['start_line']}-{read['end_line']}]\n"
                    + number_span_content(read["content"], read["start_line"])
                ),
            }
        )
    return turns


def chunk_reads(reads: Sequence[dict], cycles: int = CYCLES) -> List[List[dict]]:
    """Near-equal chunks; earlier chunks take the remainder."""
    n = len(reads)
    base, remainder = divmod(n, cycles)
    chunks = []
    start = 0
    for index in range(cycles):
        size = base + (1 if index < remainder else 0)
        chunks.append(list(reads[start : start + size]))
        start += size
    return chunks


def estimate_tokens(turns: Sequence[dict]) -> float:
    """The harness tail estimator's token estimate (chars/4 + 0.6/message)."""
    return sum(len(t["content"]) / 4.0 + 0.6 for t in turns)


def compaction_budgets(turns: Sequence[dict]) -> Tuple[float, float]:
    """Practice-scaled (reserveTokens, keepRecentTokens) for a transcript."""
    total = estimate_tokens(turns)
    reserve = max(RESERVE_FRACTION * total, RESERVE_MIN)
    keep = max(KEEP_FRACTION * total, KEEP_MIN)
    return round(reserve, 1), round(keep, 1)


def build_plan(target: dict, rows: Sequence[dict], dev_ids: set) -> dict:
    """Full deterministic run plan for one instance.

    Contains gold and distractor contents (evaluation inputs); it is an
    uncommitted runs/ artifact, never shipped in the tree.
    """
    distractors = pick_distractors(target, rows, dev_ids)
    reads = explore_order(target, distractors)
    turns = build_transcript(target, reads)
    reserve, keep = compaction_budgets(turns)
    chunks = chunk_reads(reads)
    # turn ids: 0 = problem statement; read i -> user 2i+1, assistant 2i+2
    read_pairs = []
    for index, read in enumerate(reads):
        read_pairs.append(
            {
                "user_turn_id": 2 * index + 1,
                "assistant_turn_id": 2 * index + 2,
                "file": read["file"],
                "start_line": read["start_line"],
                "end_line": read["end_line"],
                "kind": read["kind"],
            }
        )
    return {
        "instance_id": target["instance_id"],
        "repo": target["repo"],
        "language": target["language"],
        "source": target["source"],
        "base_commit": target["base_commit"],
        "problem_statement_chars": len(str(target.get("problem_statement") or "")),
        "gold_items": gold_items(target),
        "patch": str(target.get("patch") or ""),
        "distractors": distractors,
        "reads": reads,
        "read_pairs": read_pairs,
        "turns": turns,
        "chunks": chunks,
        "settings": {"reserveTokens": reserve, "keepRecentTokens": keep},
        "estimated_tokens": estimate_tokens(turns),
    }


def load_rows_and_dev_ids(refs_root: str) -> Tuple[List[dict], set]:
    """Load the pinned parquet rows and the committed dev id set.

    Same columns as `dataset.load_rows` plus `problem_statement` (the
    live transcripts need the issue text; the W10 column list predates
    the live phase and does not select it).
    """
    import os

    import pyarrow.dataset as ds

    parquet = os.path.join(refs_root, "data", "contextbench_verified.parquet")
    columns = list(cb_dataset.PARQUET_COLUMNS) + ["problem_statement"]
    table = ds.dataset(parquet, format="parquet").to_table(columns=columns)
    rows = table.to_pylist()
    if len(rows) != cb_dataset.GOLD_PARQUET_ROWS:
        raise ValueError(
            f"expected {cb_dataset.GOLD_PARQUET_ROWS} rows in {parquet}, got {len(rows)}"
        )
    manifest_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "dev_holdout_split.json",
    )
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    dev_ids = {
        item["instance_id"] for item in manifest["split"]["dev"]["instances"]
    }
    return rows, dev_ids
