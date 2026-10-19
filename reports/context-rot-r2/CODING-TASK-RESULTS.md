# CODING-TASK RESULTS — what exists, what does not, and what blocks it

Scope: the honest status of coding-task evaluation after the r2 campaign.
Report writer W14, 2026-10-20, refreshed by W34 at the integration tip
`f8ad670c8` and reconciled by the W50 consistency sweep at `34c27c07d`.
No number in this file is a coding-benchmark capability
score; every figure cites a committed artefact.

## 1. Where iteration-1 left this

Iteration-1's only coding-shaped measurement was E4, a synthetic
single-error fixture: the evidence-recovery excerpt moved 4 probes from
0/4 to 4/4 at the same 2 000-char budget
(`reports/context-rot/CODING-TASK-RESULTS.md` sections 2-3). The final
report explicitly disclaims it: "Not a coding-task capability score. E4's
fixture is synthetic and single-error" (`reports/context-rot/FINAL-REPORT.md`
section 7). RepoQA, SWE-bench and Terminal-Bench were NOT EXECUTED in
iteration-1 and remain NOT EXECUTED.

## 2. What r2 committed: the ContextBench offline pipeline (CUSTOM-OPTIMUS)

Worker W10, commits `ab71234bc`, `29be98f76`, `3a1d5ee4d`, `832dfd8e2`,
`86e8ad09b`; package `research/context_rot_r2/contextbench/`; narrative in
`research/context_rot_r2/contextbench/RETRIEVAL-SCORECARD-DRAFT.md` and
`CONTEXTBENCH-EVAL-DESIGN.md`.

What runs offline, deterministically (scorecard section 2, all verified by
its test suite):

- parquet loading + sha256 verification of the 500-instance verified
  subset of the pinned upstream ContextBench commit `1436c28a`
  (`dataset.py`, `data/dev_holdout_split.json` `provenance`: rows 500,
  no network fetches).
- repo-level dev/holdout split with no repo overlap: 308 dev instances
  over 28 repos (61.6 %) / 192 holdout instances over 30 repos (38.4 %),
  seed 11237061071491451299 from blake2b over stable identifiers; the
  committed manifest `data/dev_holdout_split.json` regenerates
  byte-identically.
- a 20-instance stratified dev smoke subset, all 8 languages present
  (`data/dev_smoke20.json`: python 7, ts 3, go 3, js 2, rust 2, c 1, cpp 1,
  java 1).
- gold-context parsing and the upstream metric layer mirrored function by
  function with citations: coverage/precision with the upstream edge
  cases, file and line-span granularities, byte-interval math with a
  content-based line_to_byte, trajectory AUC-coverage and redundancy,
  EditLoc with the gold-patch fallback, micro/macro aggregation
  (`metrics.py`, `intervals.py`, `editloc.py`, `gold.py`,
  `trajectory.py`; scorecard section 5 cites the upstream file and line
  for each).
- a matched A/B harness (`harness.py` `run_arm`/`compare_arms`): arms
  A (baseline) and B (jev-relevance) evaluate recorded trajectories with
  per-instance pairing, an exact sign test and a tie-corrected Wilcoxon
  normal approximation.
- predicted-path normalization mirroring the upstream
  `_filter_step_to_repo` normalization half (`86e8ad09b`).

What is scaffolded but emits blocked records only: arms C
(type-aware-task-context; the typed-state ledger landed and is wired in
compaction, but no retrieval consumer queries it) and D
(source-grounded-reconstruction; policy not implemented)
(`arms.py`, scorecard section 6).

Test suite at the last commit: 127 offline tests
(`86e8ad09b` commit message: "3 new tests (127 total)"); execution state
on this branch is in REGRESSION-GATES.md.

Not run, with the exact blocker (scorecard section 3):

| item | blocker |
| --- | --- |
| repository checkouts at `base_commit` (byte spans on real data, symbol extraction, path existence filtering) | `core/repo.py::checkout` needs network; only the ContextBench repo checkout itself is network-waived |
| trajectories for arms A/B (actual agent runs) | requires a live model; live phase |
| `patch_success` (f2p/p2p resolution) | requires a live model run + test harness; reported `UNAVAILABLE` in every record by construction |
| any ContextBench leaderboard-comparable number | the two rows above |

## 3. What r2 committed: CompactBench custom runs (CUSTOM-OPTIMUS)

Worker W6, commit `00cbdd3ab`; package
`research/context_rot_r2/compactbench_adapter/` driving the committed
harness binary (`crates/pi-coding-agent/src/bin/compaction_harness.rs`,
commits `b04d1f040` + `49f473d8d`). Method: the official CompactBench
runner, public `starter` suite (v1.0.0), mock eval provider
(`mock-deterministic`), scorer 2.0.0, 8 cases (2 per template x 4
templates), 1 drift cycle per case (`results/task-state.jsonl` row 1,
`run_start`).

Arms: `optimus-rust-task-state` (real pipeline, ledger on),
`optimus-rust-plain` (real pipeline, ledger off), `echo-first-n`
(head-truncation floor control), `full-transcript-oracle` (ceiling
control), `built-in:null` (empty artifact floor). Results
(`results/*.jsonl`, `run_end` rows):

| arm | overall | drift_res | constraint_ret | contradiction | compression | eval prompt tokens |
| --- | --- | --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.375 | 1.000 | 0.400 | 0.000 | 0.54x | 17 777 |
| optimus-rust-plain | 0.375 | 1.000 | 0.400 | 0.000 | 0.64x | 15 881 |
| echo-first-n | 0.375 | 1.000 | 0.400 | 0.000 | 0.80x | 14 071 |
| full-transcript-oracle | 0.375 | 1.000 | 0.400 | 0.000 | 0.80x | 14 071 |
| built-in:null | 0.375 | 1.000 | 0.400 | 0.000 | 101.00x | 7 522 |

How to read this (README "Validation runs"):

- All quality scores are degenerate by construction: the mock provider
  returns canned strings, so every arm including the null floor and the
  oracle ceiling scores the same 0.375. These runs validate plumbing
  only. No quality claim is made or possible offline.
- The mock-independent observables do separate the arms: the Rust arms'
  artifacts are larger with the ledger on (0.54x vs 0.64x compression)
  because the ledger lines and structured-state sections add mass.
- Every Rust-arm cycle ran the real pipeline: zero method warnings across
  all 16 cycles of both Rust arms; every `compact()` call landed a valid
  cut on the first ladder candidate, no fallbacks, no "unusable handoff"
  errors.
- Compression below 1x is a small-transcript effect (starter cases run
  ~8 short turns; the fixture summary's fixed boilerplate plus lossy
  excerpts exceed the source size). Nothing was tuned to hide it.

Labelling (README "Labelling rules"): these are CUSTOM runs - public
starter suite, mock eval provider, scorer 2.0.0, custom adapter methods.
They are not CompactBench leaderboard numbers, not comparable to any
1.0.0-scored run, and not usable as quality evidence for Optimus
compaction. The pipeline behind them is PRODUCTION-PATH (real Rust
compaction per cycle); the scores are mock-eval artifacts and are not
mixed with any production-path measurement in this report set.

W12 status (updated at the W34 refresh): the full practice suites ARE
committed - 18 runs (9 arms x 2 suites: starter 20 cases, elite 100; 80
and 400 cycles per arm), artifacts
`research/context_rot_r2/compactbench_adapter/results/w12/` and
`SUITE-RESULTS.md`; the consolidated reading is
`reports/context-rot-r2/COMPACTBENCH-RESULTS.md` (whose section 9 adds
the W26 live practice run, 4 arms x 8 starter cases, GLM 5.3 in both
roles).

## 4. What is NOT run, and the exact blockers (updated at the W34 refresh, reconciled at the W50 sweep)

| planned item | status | exact blocker |
| --- | --- | --- |
| R2-E2 live-model arms | EXECUTED (W7) | no longer blocked: `research/context_rot_r2/live/` (`fb089ee6e`); base 95/95 default vs 89/95 taskState, hard 111/111 both arms |
| R2-E3 live coding-task suite | EXECUTED (W17) | no longer blocked: `research/context_rot_r2/live/e3/` (`c5107fc31`); 50/50 action-probe points on both arms |
| live TYPED arm (R2-E5) | EXECUTED (W29) | no longer blocked: the typed arm is wired in the adapter (`08f16adf0`) and the E3 mirror (`fe6b6d966`); artifacts `research/context_rot_r2/live/e5/` (`59832805f`, `c248a101f`) - fixture 15/19 (v1 14/19, default 0/19), live parity on the e3 tasks that ran, TASK-E halted pre-fix (D24, fixed W37), E3ADV C1 pairing gap (D29, fixed W41); re-measure E5-RM (W47) in flight |
| CompactBench model-backed run at full suite scale | NOT EXECUTED | the W26 practice run (8 of 20 starter cases per arm, one live draw, `compactbench_adapter/live/`) is the only live data; full-suite live runs and the elite suite need a further authorized budget |
| CompactBench official suites beyond the public starter practice suite | NOT EXECUTED | starter (and elite practice) are the public suites; no leaderboard-scored run exists |
| ContextBench arms A/B on real agent trajectories | NOT EXECUTED | the W27 practice run used synthetic gold-derived trajectories (`contextbench/live/`); free-form exploration needs repository checkouts + a live agent, harness ready (`contextbench/runs/.gitignore`) |
| ContextBench `patch_success` | UNAVAILABLE by construction | requires live model run + test harness (`harness.py` `PATCH_SUCCESS_UNAVAILABLE`) |
| ContextBench holdout run | NOT EXECUTED | live phase runs dev first; holdout runs once, at the end (scorecard section 7) |
| RepoQA / SWE-bench / Terminal-Bench | NOT EXECUTED | not attempted in r2; no run artefact, no official scorer (inherited from iteration-1 `reports/context-rot/CODING-TASK-RESULTS.md` section 5) |

## 5. Status summary (reconciled at the W50 sweep)

The r2 campaign moved coding-task evaluation from "one synthetic fixture"
(iteration-1 E4) to committed, tested measurement infrastructure on two
official benchmarks: a fully mirrored ContextBench metric layer with a
repo-level dev/holdout split and a matched A/B harness, and a
CompactBench adapter that runs the real Optimus compaction pipeline
inside the official runner with controls. Model-backed numbers now exist
at practice scale only - R2-E2/R2-E3 (live multicycle and coding-task
action probes, GLM 5.3), R2-E5 (the typed arm: fixture plus live cells),
the W26 CompactBench practice run and the W27
ContextBench retention run - all labelled CUSTOM-OPTIMUS + LIVE-MODEL and
none leaderboard-comparable. The remaining unblocks: the post-fix
re-measure of the typed arm (E5-RM, W47, in flight), repository checkouts
with real agent trajectories, and any official-scored run.
