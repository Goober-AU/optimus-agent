"""ContextBench dataset preparation: load, split, subset, manifest.

The dataset is the 500-instance verified subset selected by the upstream
authors (data/contextbench_verified.parquet in the pinned checkout,
commit 1436c28a). The mirror ships inside the repo, so no Hugging Face
download is performed; the parquet sha256 is verified against
`GOLD_PARQUET_SHA256`.

Split discipline (worker W10 brief): a dev/holdout split at 60/40 (of
instances) by repository, with no repo overlap between the two sides.
Repository-level splits prevent leakage through shared codebases between
the side used for iteration and the side used for the final measurement;
the 60/40 target is the instance share, since that is the statistical
unit. The split is deterministic: the seed derives from blake2b over
stable identifiers (the same pattern as
`research/context_rot_r2/scenario.py::stable_seed`), the repo order
derives from blake2b digests of repo names, and the assignment walks each
language group once (see `split_by_repo`).

Reading parquet requires pyarrow; everything else is stdlib. Tests that
need the real parquet are skipped unless it is reachable.
"""

import hashlib
import json
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import (
    GOLD_PARQUET_RELPATH,
    GOLD_PARQUET_ROWS,
    GOLD_PARQUET_SHA256,
    SPLIT_VERSION,
    UPSTREAM_COMMIT,
    UPSTREAM_REPO_URL,
)

# Columns mirrored from GoldLoader._load_parquet
# (contextbench/parsers/gold.py:161-215, commit 1436c28a).
PARQUET_COLUMNS = [
    "instance_id",
    "original_inst_id",
    "repo",
    "repo_url",
    "language",
    "base_commit",
    "gold_context",
    "patch",
    "test_patch",
    "source",
]

SPLIT_SEED_PARTS = ("contextbench", "dev-holdout", SPLIT_VERSION)
SMOKE_SEED_PARTS = ("contextbench", "dev-smoke", SPLIT_VERSION)


def stable_seed(*parts: str) -> int:
    """Deterministic seed from blake2b of the identifier parts.

    Copied from research/context_rot_r2/scenario.py::stable_seed
    (fixes M5: never Python hash()).
    """
    digest = hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def repo_digest_order(repos: Sequence[str]) -> List[str]:
    """Deterministic repo order from blake2b digests of repo names."""
    return sorted(repos, key=lambda r: hashlib.blake2b(r.encode("utf-8")).hexdigest())


def verify_parquet_sha256(parquet_path: str) -> bool:
    """Check the parquet sha256 against the pinned provenance value."""
    h = hashlib.sha256()
    with open(parquet_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == GOLD_PARQUET_SHA256


def load_rows(parquet_path: str) -> List[dict]:
    """Load the verified-subset rows from the pinned parquet mirror.

    Column selection mirrors GoldLoader._load_parquet
    (contextbench/parsers/gold.py:161-215).
    """
    try:
        import pyarrow.dataset as ds  # type: ignore
    except Exception as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "pyarrow is required to read the ContextBench parquet; "
            "use the cb-venv interpreter (refs/cb-venv)"
        ) from e
    dataset = ds.dataset(parquet_path, format="parquet")
    table = dataset.to_table(columns=PARQUET_COLUMNS)
    rows = table.to_pylist()
    if len(rows) != GOLD_PARQUET_ROWS:
        raise ValueError(
            f"expected {GOLD_PARQUET_ROWS} rows in {parquet_path}, got {len(rows)}"
        )
    return rows


@dataclass
class RepoSplit:
    """Repo-level dev/holdout split result."""

    dev_rows: List[dict] = field(default_factory=list)
    holdout_rows: List[dict] = field(default_factory=list)
    dev_repos: List[str] = field(default_factory=list)
    holdout_repos: List[str] = field(default_factory=list)
    seed: int = 0
    dev_fraction: float = 0.6

    def side_of_repo(self, repo: str) -> Optional[str]:
        if repo in set(self.dev_repos):
            return "dev"
        if repo in set(self.holdout_repos):
            return "holdout"
        return None


def _repo_language(rows: Sequence[dict], repo: str) -> str:
    """Dominant language of a repo's rows (deterministic tie-break)."""
    counts: Dict[str, int] = {}
    for row in rows:
        if row.get("repo") == repo:
            lang = str(row.get("language", ""))
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return ""
    best = max(counts.items(), key=lambda kv: (kv[1], [-ord(c) for c in kv[0]]))
    return best[0]


def split_by_repo(
    rows: Sequence[dict],
    dev_fraction: float = 0.6,
    seed_parts: Sequence[str] = SPLIT_SEED_PARTS,
) -> RepoSplit:
    """Language-stratified repo-level dev/holdout split.

    Deterministic algorithm:
    1. seed = stable_seed(*seed_parts) (blake2b over stable identifiers);
    2. repos are grouped by language, ordered within each group by
       blake2b(repo) digest, then shuffled with random.Random(seed) - the
       only use of randomness (one rng instance walks the groups in sorted
       language order);
    3. within each language group, walking that order once, each repo goes
       to the side whose normalized fill for that language
       (side_lang_count / side_lang_target) is lower, ties to dev.

    Because languages correlate with repositories, an unstratified repo
    split can empty a language from one side entirely; the per-language
    greedy keeps every language represented on both sides (when a language
    spans at least two repos) while keeping the overall instance ratio near
    dev_fraction. Every repo lands entirely on one side (no repo overlap).
    Exact 60/40 is not always reachable at repo granularity; the achieved
    counts are recorded in the manifest.
    """
    if not 0.0 < dev_fraction < 1.0:
        raise ValueError(f"dev_fraction must be in (0, 1), got {dev_fraction}")
    if not rows:
        return RepoSplit(dev_fraction=dev_fraction)

    seed = stable_seed(*seed_parts)
    repos = sorted({row["repo"] for row in rows})

    sizes = {repo: 0 for repo in repos}
    for row in rows:
        sizes[row["repo"]] += 1

    by_language: Dict[str, List[str]] = {}
    for repo in repos:
        by_language.setdefault(_repo_language(rows, repo), []).append(repo)

    rng = random.Random(seed)
    dev_repos: List[str] = []
    holdout_repos: List[str] = []
    for language in sorted(by_language):
        group = repo_digest_order(by_language[language])
        rng.shuffle(group)
        lang_total = sum(sizes[repo] for repo in group)
        target_dev = dev_fraction * lang_total
        target_holdout = (1.0 - dev_fraction) * lang_total
        dev_count = 0
        holdout_count = 0
        for repo in group:
            # Lower normalized fill first; ties go to dev.
            dev_fill = (dev_count / target_dev) if target_dev > 0 else float("inf")
            hold_fill = (
                (holdout_count / target_holdout) if target_holdout > 0 else float("inf")
            )
            if dev_fill <= hold_fill:
                dev_repos.append(repo)
                dev_count += sizes[repo]
            else:
                holdout_repos.append(repo)
                holdout_count += sizes[repo]

    split = RepoSplit(
        dev_repos=dev_repos,
        holdout_repos=holdout_repos,
        seed=seed,
        dev_fraction=dev_fraction,
    )
    dev_set = set(dev_repos)
    split.dev_rows = [row for row in rows if row["repo"] in dev_set]
    split.holdout_rows = [row for row in rows if row["repo"] not in dev_set]
    return split


def stratified_subset(
    rows: Sequence[dict],
    k: int,
    stratum_key: str = "language",
    seed_parts: Sequence[str] = SMOKE_SEED_PARTS,
) -> List[dict]:
    """Deterministic stratified subset of `k` rows.

    When `k` is at least the number of non-empty strata, every stratum
    first gets one slot (so no language disappears from a smoke run); the
    remaining slots are allocated proportionally to stratum sizes with the
    largest-remainder rule. Within a stratum the sample order comes from
    random.Random(stable_seed(*seed_parts)) over rows sorted by
    instance_id, so the subset is reproducible byte-for-byte.
    """
    if k <= 0:
        return []
    rows = sorted(rows, key=lambda r: str(r.get("instance_id", "")))
    strata: Dict[str, List[dict]] = {}
    for row in rows:
        strata.setdefault(str(row.get(stratum_key, "")), []).append(row)

    total = len(rows)
    if k >= total:
        return list(rows)

    if k >= len(strata):
        alloc = {stratum: 1 for stratum in strata}
        remaining = k - len(strata)
    else:
        alloc = {stratum: 0 for stratum in strata}
        remaining = k

    floors = {}
    remainders = {}
    for stratum, members in strata.items():
        exact = remaining * len(members) / total
        floors[stratum] = int(exact)
        remainders[stratum] = exact - int(exact)
    for stratum, floor in floors.items():
        alloc[stratum] += floor
    leftover = remaining - sum(floors.values())
    for stratum in sorted(strata, key=lambda s: (-remainders[s], s)):
        if leftover <= 0:
            break
        if remainders[stratum] > 0:
            alloc[stratum] += 1
            leftover -= 1

    # Cap at stratum capacity; push overflow to the largest strata.
    overflow = 0
    for stratum in sorted(strata):
        if alloc[stratum] > len(strata[stratum]):
            overflow += alloc[stratum] - len(strata[stratum])
            alloc[stratum] = len(strata[stratum])
    while overflow > 0:
        for stratum in sorted(strata, key=lambda s: (-len(strata[s]), s)):
            if overflow <= 0:
                break
            if alloc[stratum] < len(strata[stratum]):
                alloc[stratum] += 1
                overflow -= 1

    rng = random.Random(stable_seed(*seed_parts))
    picked: List[dict] = []
    for stratum in sorted(strata):
        take = min(alloc[stratum], len(strata[stratum]))
        picked.extend(rng.sample(strata[stratum], take))
    picked.sort(key=lambda r: str(r.get("instance_id", "")))
    return picked


def _instance_meta(row: dict) -> dict:
    return {
        "instance_id": row.get("instance_id", ""),
        "original_inst_id": row.get("original_inst_id", ""),
        "repo": row.get("repo", ""),
        "language": row.get("language", ""),
        "source": row.get("source", ""),
    }


def _counts(rows: Sequence[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        out[str(row.get(key, ""))] = out.get(str(row.get(key, "")), 0) + 1
    return dict(sorted(out.items()))


def build_manifest(
    split: RepoSplit,
    parquet_path: str,
    extra: Optional[dict] = None,
) -> dict:
    """Build the committed split manifest (metadata only, no gold content)."""
    manifest = {
        "split_version": SPLIT_VERSION,
        "generator": "research.context_rot_r2.contextbench.dataset",
        "provenance": {
            "upstream_repo": UPSTREAM_REPO_URL,
            "upstream_commit": UPSTREAM_COMMIT,
            "parquet_relpath": GOLD_PARQUET_RELPATH,
            "parquet_sha256": GOLD_PARQUET_SHA256,
            "rows": GOLD_PARQUET_ROWS,
            "network_fetches": [],
            "note": (
                "dataset read from the pinned local checkout mirror; "
                "no Hugging Face download performed"
            ),
        },
        "split": {
            "seed_parts": list(SPLIT_SEED_PARTS),
            "seed": split.seed,
            "dev_fraction": split.dev_fraction,
            "method": (
                "repos grouped by language, ordered by blake2b(repo) "
                "digest, shuffled with random.Random(stable_seed(seed_parts)), "
                "assigned per language to the side with the lower normalized "
                "fill; no repo overlap"
            ),
            "dev": {
                "repos": sorted(split.dev_repos),
                "repo_count": len(split.dev_repos),
                "instance_count": len(split.dev_rows),
                "instances": [_instance_meta(r) for r in split.dev_rows],
                "language_counts": _counts(split.dev_rows, "language"),
                "source_counts": _counts(split.dev_rows, "source"),
            },
            "holdout": {
                "repos": sorted(split.holdout_repos),
                "repo_count": len(split.holdout_repos),
                "instance_count": len(split.holdout_rows),
                "instances": [_instance_meta(r) for r in split.holdout_rows],
                "language_counts": _counts(split.holdout_rows, "language"),
                "source_counts": _counts(split.holdout_rows, "source"),
            },
        },
    }
    if extra:
        manifest.update(extra)
    return manifest


def write_manifest(manifest: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=False)
        f.write("\n")


def load_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def manifest_instance_ids(manifest: dict, side: str) -> List[str]:
    """instance_id list for one side ('dev' or 'holdout')."""
    if side not in ("dev", "holdout"):
        raise ValueError(f"side must be dev or holdout, got {side}")
    return [i["instance_id"] for i in manifest["split"][side]["instances"]]


def subset_rows_by_ids(rows: Sequence[dict], ids: Sequence[str]) -> List[dict]:
    """Rows restricted to `ids`, keeping the row order of `rows`."""
    wanted = set(ids)
    return [row for row in rows if row.get("instance_id") in wanted]
