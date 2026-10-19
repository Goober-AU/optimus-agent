# ContextBench offline evaluation pipeline (rot2 W10)

Offline-capable parts of the ContextBench evaluation design
(`research/context_rot_r2/CONTEXTBENCH-EVAL-DESIGN.md`): dataset
preparation, gold-context metrics, and the matched A/B harness scaffold.
Everything here is deterministic and offline; model-dependent quantities are
reported as UNAVAILABLE, never fabricated.

## Provenance

| item | value |
| --- | --- |
| Upstream repo | https://github.com/EuniAI/ContextBench (pinned `1436c28a8eb95496da4ea69ad458b9f8a8eb7d61`, Apache-2.0) |
| Local checkout | `C:/Users/openclawuser/optimus-rot2-w5-research/refs/ContextBench` (read-only) |
| Dataset | `data/contextbench_verified.parquet` in that checkout (500 instances, 58 repos, 8 languages) |
| Parquet sha256 | `e9dcfd504cbfb849ac815a79040c793d0d92f94eecc9b5a4ee3e1445a2f8a791` |
| Network fetches | none; the mirror ships in the repo, no Hugging Face download was performed |

Metric formulas mirror the upstream implementation at that commit; each
mirrored function cites the upstream file and lines in its docstring.

## Layout

```
contextbench/
  __init__.py      provenance constants, UNAVAILABLE marker
  intervals.py     byte-interval math (mirrors core/intervals.py, core/fileio.py)
  gold.py          gold-context parsing (mirrors parsers/gold.py)
  metrics.py       coverage/precision, file/line/span metrics, AUC,
                   redundancy, aggregation (mirrors metrics/compute.py,
                   evaluate.py::aggregate_results)
  editloc.py       diff parsing + EditLoc (mirrors parsers/diff.py,
                   evaluate.py:231-295)
  trajectory.py    unified-format record IO (mirrors parsers/trajectory.py,
                   custom_parser.py contract) + tool_calls extension
  dataset.py       parquet loading, repo-level dev/holdout split,
                   stratified smoke subset, manifest IO
  arms.py          A/B/C/D arm configs (C/D placeholders)
  harness.py       run_arm / compare_arms / run summaries
  __main__.py      CLI: verify | split | smoke | arms
  data/            committed split manifest + smoke subset (metadata only)
  runs/            generated artifacts (gitignored)
  tests/           124 offline tests (parquet tests gated on CB_REFS_ROOT)
```

## Usage

```bash
# unit tests (stdlib only, no data needed)
python -m unittest discover -s research/context_rot_r2/contextbench/tests -t .

# integration tests need the pinned checkout + pyarrow (cb-venv):
CB_REFS_ROOT=C:/Users/openclawuser/optimus-rot2-w5-research/refs/ContextBench \
  <cb-venv-python> -m unittest discover \
  -s research/context_rot_r2/contextbench/tests -t .

# regenerate / verify the split manifest (offline, reads the pinned parquet)
python -m research.context_rot_r2.contextbench verify --refs-root <checkout>
python -m research.context_rot_r2.contextbench split  --refs-root <checkout> \
    --out research/context_rot_r2/contextbench/data/dev_holdout_split.json
python -m research.context_rot_r2.contextbench smoke --refs-root <checkout> \
    --manifest research/context_rot_r2/contextbench/data/dev_holdout_split.json \
    --out research/context_rot_r2/contextbench/data/dev_smoke20.json
```

The parquet stage needs `pyarrow`; reuse
`C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv` (has pandas +
pyarrow). All other stages are stdlib-only.

## Split

`data/dev_holdout_split.json` records the deterministic repo-level split of
the 500 verified instances (seed derives from blake2b over
`("contextbench", "dev-holdout", "w10.1")`):

- dev: 308 instances, 28 repos
- holdout: 192 instances, 30 repos
- no repo appears on both sides; every language is represented on both sides
  (per-language greedy assignment)
- `data/dev_smoke20.json`: 20-instance stratified smoke subset of dev (all 8
  languages present), per design doc section 3

Regeneration is byte-identical (verified; also enforced by
`test_dataset.RealParquetTests.test_split_matches_committed_manifest`).

## Arms

- A `baseline`: unmodified production policy - RUNNABLE on recorded
  trajectories
- B `jev-relevance`: current Jev relevance filtering - RUNNABLE on recorded
  trajectories
- C `type-aware-task-context`: PLACEHOLDER (typed-state not landed)
- D `source-grounded-reconstruction`: PLACEHOLDER

RUNNABLE means the arm can be *evaluated* offline over recorded
trajectories. *Producing* trajectories requires a live model run and is the
live phase; `patch_success` stays UNAVAILABLE offline by construction.

## Offline limits (documented, not worked around)

- predicted paths are not filtered against a repository checkout (upstream
  `evaluate.py` does this via `_filter_step_to_repo`); the live phase must
  add it;
- symbol granularity (tree-sitter) and byte-span metrics need checkouts;
  byte math is mirrored and testable, but real spans need the live phase;
- repo caching/checkout (`core/repo.py::checkout`) needs network for new
  repos - BLOCKED offline.
