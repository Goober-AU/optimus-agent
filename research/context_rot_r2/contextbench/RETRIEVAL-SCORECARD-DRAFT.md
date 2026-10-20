# RETRIEVAL SCORECARD (DRAFT) - ContextBench offline pipeline, rot2 W10

Integrator note: this file belongs at
`reports/context-rot-r2/RETRIEVAL-SCORECARD-DRAFT.md`; it is kept under
`research/context_rot_r2/contextbench/` until the integrator moves it.

Worker W10, 2026-10-20. Everything in this scorecard is offline. No
model-backed ContextBench number exists yet: agent runs are the live
phase, and per the campaign rules no model-dependent number may be claimed
from this worktree.

## 1. Provenance

| item | value |
| --- | --- |
| Upstream | https://github.com/EuniAI/ContextBench, commit `1436c28a8eb95496da4ea69ad458b9f8a8eb7d61`, Apache-2.0 (code) |
| Local checkout | `optimus-rot2-w5-research/refs/ContextBench` (read-only) |
| Dataset | `data/contextbench_verified.parquet` (the 500-instance verified selection; 500 rows, 58 repos, 8 languages, sources: Verified 174 / Multi 156 / Poly 116 / Pro 54) |
| Parquet sha256 | `e9dcfd504cbfb849ac815a79040c793d0d92f94eecc9b5a4ee3e1445a2f8a791` |
| Network fetches | none. The dataset mirror ships inside the pinned checkout; no Hugging Face download was needed or performed. |

## 2. What runs now (offline, deterministic)

| # | surface | status | evidence |
| --- | --- | --- | --- |
| 1 | parquet loading + sha256 verification | RUNS | `dataset.load_rows` / `dataset.verify_parquet_sha256`; `__main__.py verify` |
| 2 | dev/holdout split, repo-level, no repo overlap | RUNS | `dataset.split_by_repo`; manifest `data/dev_holdout_split.json`; regeneration byte-identical |
| 3 | 20-instance stratified smoke subset of dev | RUNS | `dataset.stratified_subset`; `data/dev_smoke20.json`; all 8 languages present |
| 4 | gold-context parsing (paths, files, line spans) | RUNS | `gold.py` (mirror of upstream `parsers/gold.py`); tests `test_gold.py` |
| 5 | file precision/recall vs gold | RUNS | `metrics.file_metrics`; tests `test_metrics.py` |
| 6 | line-span precision/recall vs gold | RUNS | `metrics.line_metrics` + interval helpers; tests `test_metrics.py` |
| 7 | byte-span precision/recall (pure math + content-based conversion) | RUNS on synthetic inputs | `intervals.py`, `metrics.span_metrics`, `metrics.line_spans_to_bytes`; real checkouts blocked (row 14) |
| 8 | AUC-coverage + redundancy (trajectory level) | RUNS on recorded trajectories | `metrics.trajectory_metrics`; tests |
| 9 | EditLoc recall/precision | RUNS on recorded trajectories | `editloc.editloc_metrics` (gold-patch fallback included); tests |
| 10 | micro/macro aggregation | RUNS | `metrics.aggregate_records` |
| 11 | A/B harness: evaluate arms A/B over recorded trajectories | RUNS | `harness.run_arm`; tests `test_harness.py` |
| 12 | paired statistics (exact sign test, Wilcoxon normal approx) | RUNS | `harness.compare_arms`; tests |
| 13 | arms C/D scaffolding (blocked records, no numbers) | RUNS as scaffolding | `arms.py`, `harness.run_arm` placeholder branch |

Test suite: 124 tests, all passing offline
(`python -m unittest discover -s research/context_rot_r2/contextbench/tests -t .`;
3 of them are integration tests gated on `CB_REFS_ROOT` + pyarrow and verify
the real parquet sha256, the row shape, and that the committed manifest
regenerates exactly).

## 3. What is BLOCKED offline (never fabricated)

| # | item | blocker |
| --- | --- | --- |
| 14 | repository checkouts at `base_commit` (needed for byte spans on real data, symbol extraction, path filtering) | `core/repo.py::checkout` needs network for new repos; only the ContextBench repo checkout itself is network-waived |
| 15 | symbol-granularity metrics | tree-sitter extraction over a checkout (`extractors/treesitter.py`); no checkout offline |
| 16 | predicted-path filtering against the checkout | upstream `evaluate.py::_filter_step_to_repo` (139-190) requires the worktree. Offline, `trajectory.normalize_step_paths` applies the same container-prefix normalization (`/testbed/`, `/workspace/<x>/`, leading `/`, `./`); the existence filter and suffix matching stay live-phase |
| 17 | trajectories for arms A/B (actual agent runs) | requires a live model; live phase |
| 18 | `patch_success` (f2p/p2p resolution) | requires live model run + test harness; reported as `UNAVAILABLE` in every record by construction |
| 19 | any ContextBench leaderboard-comparable number | 17 + 18 |

## 4. Split counts

Dev/holdout split of the 500 verified instances, split unit = repository,
no repo overlap, instance share target 60/40 (seed from blake2b over
`("contextbench", "dev-holdout", "w10.1")` = 11237061071491451299):

| side | instances | repos | python | ts | js | go | c | rust | java | cpp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev | 308 (61.6%) | 28 | 163 | 37 | 36 | 36 | 10 | 17 | 4 | 5 |
| holdout | 192 (38.4%) | 30 | 103 | 29 | 24 | 4 | 13 | 3 | 11 | 5 |

Every language appears on both sides (per-language greedy assignment;
languages correlate with repos, so an unstratified repo split would empty
C and Rust from one side). Source mix dev: Verified 119 / Multi 115 /
Poly 64 / Pro 10; holdout: Verified 55 / Poly 52 / Multi 41 / Pro 44. Exact
60/40 is unreachable at repo granularity (django alone is 80 instances);
the achieved 308/192 is recorded in the manifest.

Smoke subset (design doc section 3): 20 dev instances, stratified by
language (c 1, cpp 1, go 3, java 1, js 2, python 7, rust 2, ts 3), 10
repos, all inside the dev split.

## 5. Metric definitions and citations

All citations refer to the pinned upstream commit `1436c28a`. Mirrors live
in `research/context_rot_r2/contextbench/`.

| metric | definition | upstream citation | mirror |
| --- | --- | --- | --- |
| coverage (recall) | `inter / gold`, `1.0` when gold is empty | `contextbench/metrics/compute.py:7-13` (`coverage_precision`) | `metrics.coverage_precision` |
| precision | `inter / pred`, `1.0` when pred is empty | `compute.py:7-13` | `metrics.coverage_precision` |
| file metrics | intersection = `|pred_files & gold_files|` | `compute.py:103-105` (file branch of `compute_granularity_metrics`, 92-158) | `metrics.file_metrics` |
| line-span metrics | per-file inclusive line intervals, adjacent merged; intersection via two-pointer walk | `compute.py:27-79` (`_merge_line_intervals` 27-39, `_line_interval_length` 41-44, `_line_interval_intersect` 45-61, `line_total_lines` 67-69, `line_intersection_lines` 71-79) and 125-140 (line branch) | `metrics.merge_line_intervals` etc., `metrics.line_metrics` |
| byte-span metrics | byte intervals from a line-offset table; merge/intersect/size | `contextbench/core/intervals.py:7-45`, `contextbench/core/fileio.py:5-31` (`line_to_byte`), `compute.py:112-116` (span branch) | `intervals.py` (content-based `line_to_byte`), `metrics.span_metrics`, `metrics.line_spans_to_bytes` |
| gold parsing | init+add merge, `gold_ctx` fallback, path normalization (`/testbed/`, `/workspace/<x>/`, leading `/`, `./`) | `contextbench/parsers/gold.py:10-21` (`_normalize_rel_path`), 25-44 (`Gold.__init__`, `files`), 83-125 (`line_spans_init`), 161-215 (`_load_parquet`) | `gold.normalize_rel_path`, `gold.GoldContext`, `gold.gold_from_row` |
| AUC-coverage | mean over steps of running-union coverage | `compute.py:159-249` (`compute_trajectory_metrics`; AUC at 233-237) | `metrics.trajectory_metrics` |
| redundancy | `1 - union_size / sum_of_step_sizes` per granularity, `0.0` when the sum is 0 | `compute.py:239-243` | `metrics.trajectory_metrics` |
| step -> line intervals | spans flattened to `{file, start_line, end_line}` then merged per file | `compute.py:141-157` (`_step_to_line_intervals`) | `metrics.step_line_intervals` |
| EditLoc recall/precision | predicted deleted lines (old-file coordinates) expanded one by one; hit iff inside a gold init range; both recall and precision = `hits / pred_lines`; gold-patch fallback when the record has no patch | `contextbench/evaluate.py:231-295` (fallback 234-237, recall/precision 270-273, gold size 276-280); diff parsing `contextbench/parsers/diff.py:30-50` (`parse_diff_lines`), 52-66, 69-150 (`_parse_hunks`) | `editloc.parse_diff_lines`, `editloc.editloc_metrics` |
| aggregation | final metrics micro-averaged (sums of intersection/gold/pred); trajectory metrics macro-averaged (means) | `evaluate.py:533-567` (`aggregate_results`) | `metrics.aggregate_records` |
| trajectory record shape | `pred_steps[].files/spans/symbols`, `pred_files`, `pred_spans`, `model_patch`; empty `pred_steps` falls back to one final step | `contextbench/parsers/trajectory.py:8-14` (`Step`), 16-70 (`parse_trajectory`); contract `contextbench/parsers/custom_parser.py:12-51` | `trajectory.to_steps`, `trajectory.load_trajectory_records` |

Known upstream quirk kept in the mirror: EditLoc recall and precision are
the same number by construction (upstream sets them equal at
evaluate.py:272-273); the gold size denominator is the full gold line
count, not the hit count, so `recall` here is not `hits / gold_lines`.

## 6. Arms

| arm | name | status | offline meaning |
| --- | --- | --- | --- |
| A | baseline | RUNNABLE | evaluates recorded trajectories produced under the unmodified policy |
| B | jev-relevance | RUNNABLE | evaluates recorded trajectories produced under current Jev relevance filtering |
| C | type-aware-task-context | PLACEHOLDER | blocked records only; typed-state not landed |
| D | source-grounded-reconstruction | PLACEHOLDER | blocked records only; policy not implemented |

Matched-pair discipline (design doc section 5): same instance set, same
order, per-instance seeds; `harness.compare_arms` pairs by `instance_id`
and reports per-instance deltas, exact sign-test p, and Wilcoxon
signed-rank (normal approximation, zeros discarded, tie-corrected).

## 7. Live-phase checklist (what unblocks rows 14-19)

1. Authorize a model-backed run; produce trajectories per arm with the
   session->trajectory exporter (unified format; `tool_calls` extension).
2. Warm the repo cache once (network waiver covers the ContextBench repo
   checkout only; target-repo clones need their own authorization).
3. Re-run `harness.run_arm` per arm; add checkout-based path filtering and
   byte-span/symbol metrics at that point.
4. Resolve `patch_success` via the f2p/p2p harness; replace the
   UNAVAILABLE marker.
5. Run the holdout once, at the end.
