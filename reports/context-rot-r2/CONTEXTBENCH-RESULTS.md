# CONTEXTBENCH RESULTS — the offline pipeline, and what has actually run

Scope: the ContextBench coding-task evaluation pipeline committed by W10 and
its execution state. Report writer W21, 2026-10-20. No number in this file
is a ContextBench capability score for Optimus: no model-backed run exists
on this branch, and every model-dependent quantity is UNAVAILABLE or NOT
EXECUTED, never fabricated. Sources: the committed package
`research/context_rot_r2/contextbench/` (code, data manifests, tests),
W10's draft scorecard
`research/context_rot_r2/contextbench/RETRIEVAL-SCORECARD-DRAFT.md` (kept
at its research path), and `git log` in this worktree.

## 1. Provenance

| item | value |
| --- | --- |
| upstream repo | https://github.com/EuniAI/ContextBench (Apache-2.0, code) |
| pinned commit | `1436c28a8eb95496da4ea69ad458b9f8a8eb7d61` |
| local checkout | `optimus-rot2-w5-research/refs/ContextBench` (read-only) |
| dataset | `data/contextbench_verified.parquet` - 500 instances, 58 repos, 8 languages; sources Verified 174 / Multi 156 / Poly 116 / Pro 54 |
| parquet sha256 | `e9dcfd504cbfb849ac815a79040c793d0d92f94eecc9b5a4ee3e1445a2f8a791` |
| network fetches | none (the dataset mirror ships inside the pinned checkout; no Hugging Face download was performed) |

## 2. Pipeline state (what is committed)

Everything below runs offline and deterministically, verified by the
package's test suite (124 tests; re-run for this report at this HEAD with
`python -m pytest research/context_rot_r2/contextbench/tests/ -q`:
124 passed, 3 skipped; the 3 skipped are the parquet integration tests
gated on `CB_REFS_ROOT` + pyarrow, which passed in the W14 gate run -
127 passed, 0 skipped - against the pinned checkout):

- dataset loading with sha256 verification, and the repo-level dev/holdout
  split with no repo overlap (seed 11237061071491451299 from blake2b over
  `("contextbench", "dev-holdout", "w10.1")`); the committed manifest
  `data/dev_holdout_split.json` regenerates byte-identically
- a 20-instance stratified smoke subset of dev, all 8 languages present
  (`data/dev_smoke20.json`)
- gold-context parsing mirroring upstream `parsers/gold.py`
- the metric layer mirroring upstream function by function with citations:
  coverage/precision, file and line-span metrics, byte-interval math
  (content-based `line_to_byte`), trajectory AUC-coverage and redundancy,
  EditLoc with the gold-patch fallback, micro/macro aggregation - each
  mirrored function cites the upstream file and lines in its docstring
  (the full citation table is W10's draft scorecard, section 5)
- the matched A/B harness: `run_arm` evaluates recorded trajectories,
  `compare_arms` pairs per instance and reports per-instance deltas, an
  exact sign-test p, and a tie-corrected Wilcoxon signed-rank normal
  approximation
- predicted-path normalization mirroring the upstream
  `_filter_step_to_repo` normalization half (container prefixes `/testbed/`,
  `/workspace/<x>/`, leading `/`, `./`)

Split of the 500 verified instances (split unit = repository, instance
share target 60/40; exact 60/40 is unreachable at repo granularity -
django alone is 80 instances):

| side | instances | repos | python | ts | js | go | c | rust | java | cpp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev | 308 (61.6 %) | 28 | 163 | 37 | 36 | 36 | 10 | 17 | 4 | 5 |
| holdout | 192 (38.4 %) | 30 | 103 | 29 | 24 | 4 | 13 | 3 | 11 | 5 |

Every language appears on both sides (per-language greedy assignment;
languages correlate with repos, so an unstratified repo split would empty
C and Rust from one side). Source mix - dev: Verified 119 / Multi 115 /
Poly 64 / Pro 10; holdout: Verified 55 / Poly 52 / Multi 41 / Pro 44.
The smoke subset: 20 dev instances (python 7, ts 3, go 3, js 2, rust 2,
c 1, cpp 1, java 1), 10 repos.

Arms scaffold:

| arm | name | offline meaning |
| --- | --- | --- |
| A | baseline | evaluates recorded trajectories produced under the unmodified policy |
| B | jev-relevance | evaluates recorded trajectories produced under current Jev relevance filtering |
| C | type-aware-task-context | placeholder - blocked records only (typed state not integrated with retrieval) |
| D | source-grounded-reconstruction | placeholder - blocked records only (policy not implemented) |

## 3. What has run

Offline self-check only: the test suite, the manifest-regeneration check,
the sha256 verification, and metric functions over synthetic inputs and
recorded sample trajectories. No agent run has been executed on this
branch: `contextbench/runs/` is empty and gitignored (no trajectories
exist), so no arm - not even A/B over recorded trajectories - has produced
a number, and `patch_success` is reported `UNAVAILABLE` in every record by
construction. No ContextBench leaderboard-comparable number exists in any
category (SYNTHETIC-DIAGNOSTIC / CUSTOM-OPTIMUS / PRODUCTION-PATH /
LIVE-MODEL): the pipeline is infrastructure awaiting its live phase.

## 4. Blockers for real runs

| item | blocker |
| --- | --- |
| repository checkouts at `base_commit` (byte spans on real data, symbol extraction via tree-sitter, predicted-path existence filtering and suffix matching) | `core/repo.py::checkout` needs network for new repos; offline only the ContextBench repo checkout itself is network-waived |
| trajectories for arms A/B (actual agent runs) | requires a live model - live phase, offline rule |
| `patch_success` (f2p / p2p resolution) | requires a live model run plus the test harness; `UNAVAILABLE` in every record by construction |
| any ContextBench leaderboard-comparable number | the three rows above |

## 5. Live phase status (not this report's dependency)

The campaign's live coding-task phase (R2-E3) is in flight on branch
`rot2/w17-e3` and is not integrated into this branch: scenario suite
`fb27f02ec` (five coding-task scenarios TASK-A/C/E/H/F, action-level
probes with negation-aware matchers, restart driver, 13 offline selftests),
orchestrator `f08d92c36`, and live results `97e1be671` (GLM 5.3, 5 tasks x
2 arms x 5 cycles; 50/50 action-probe points on both arms, 0 extraction
failures). Those runs are committed on their own branch, are not visible
under `research/context_rot_r2/` in this worktree, and are deliberately
not consumed by this report; the integrator merges and scores them. When
the live phase lands, the offline pipeline above is what evaluates it -
and the holdout runs once, at the end (W10's live-phase checklist, draft
scorecard section 7).
