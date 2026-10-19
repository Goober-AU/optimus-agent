# CONTEXTBENCH RESULTS — the offline pipeline, and what has actually run

Scope: the ContextBench coding-task evaluation pipeline committed by W10 and
its execution state. Report writer W21, 2026-10-20; sections 3 and 5
updated by W34 at the integration tip `f8ad670c8`. No number in this file
is a ContextBench capability score for Optimus: the only model-backed run
is the W27 practice run (section 6, CUSTOM-OPTIMUS + LIVE-MODEL labels),
and every leaderboard-dependent quantity is UNAVAILABLE or NOT EXECUTED,
never fabricated. Sources: the committed package
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

## 3. What has run (updated at the W34 refresh)

Offline self-check: the test suite, the manifest-regeneration check, the
sha256 verification, and metric functions over synthetic inputs and
recorded sample trajectories. Since the first edition, one model-backed
practice run has executed (W27, section 6): 26 paired dev instances x 2
arms x 4 cycles through the real compaction pipeline with a live GLM 5.3
summarizer, evaluated by this mirror - with synthetic gold-derived
trajectories, not agent runs (`contextbench/runs/` stays empty and
gitignored). `patch_success` is reported `UNAVAILABLE` in every record by
construction. No ContextBench leaderboard-comparable number exists in any
category (SYNTHETIC-DIAGNOSTIC / CUSTOM-OPTIMUS / PRODUCTION-PATH /
LIVE-MODEL): free-form exploration, repository checkouts and the holdout
run remain live-phase prerequisites.

## 4. Blockers for real runs

| item | blocker |
| --- | --- |
| repository checkouts at `base_commit` (byte spans on real data, symbol extraction via tree-sitter, predicted-path existence filtering and suffix matching) | `core/repo.py::checkout` needs network for new repos; offline only the ContextBench repo checkout itself is network-waived |
| trajectories for arms A/B (actual agent runs) | requires a live model - live phase, offline rule |
| `patch_success` (f2p / p2p resolution) | requires a live model run plus the test harness; `UNAVAILABLE` in every record by construction |
| any ContextBench leaderboard-comparable number | the three rows above |

## 5. Live phase status (updated at the W34 refresh)

The campaign's live coding-task phase (R2-E3) IS integrated on this branch
(scenario suite `d8e318f81` - five coding-task scenarios TASK-A/C/E/H/F,
action-level probes with negation-aware matchers, restart driver, 13
offline selftests; orchestrator `346f6c4f3`; live results `c5107fc31`:
GLM 5.3, 5 tasks x 2 arms x 5 cycles, 50/50 action-probe points on both
arms, 0 extraction failures). Its artifacts are committed under
`research/context_rot_r2/live/e3/`; it is a Python-mirror live run, not a
ContextBench run, and this pipeline does not evaluate it (the E3 suite
has its own action matchers). The W27 practice run (section 6) is the run
this pipeline evaluated. The holdout still runs once, at the end (W10's
live-phase checklist, draft scorecard section 7).


## 6. LIVE practice run (W27, CUSTOM-OPTIMUS + LIVE-MODEL labels)

A model-backed practice run exists as of 2026-10-20 (worker W27, branch
`rot2/w27-cblivecb`): the dev-subset slice ran through the REAL
compaction pipeline (`compaction-harness` binary) with a live GLM 5.3
summarizer and was evaluated by the offline-validated mirror above.
Every number in this section carries the labels CUSTOM-OPTIMUS and
LIVE-MODEL: it is a practice run, not an official scorecard, and no
number is comparable to a ContextBench leaderboard result. Artifacts:
`research/context_rot_r2/contextbench/live/` (run config, results JSON,
raw-calls JSONL, README with method, slice rule and limits).

What ran: 26 paired dev instances (deterministic blake2b-seeded slice of
the 110 size-window dev candidates; 7 languages, 15 repos; holdout
untouched), 2 arms x 4 append+compact cycles, delta-append sessions,
temperature 0, max_tokens ladder 8192->65536, wire-call budget 581 of
600 requests (439 in the corrected run; 123 in an invalidated blind
first attempt plus 17 smoke + 2 PONG verification - all documented in
the live README). Arms: A = baseline (taskState off), B =
task-state-ledger (taskState prompt) - a documented substitution, since
W10's offline arm B (jev-relevance) is not expressible through the
compaction surface.

Method caveat (full detail in the live README): no repository checkouts
exist under the offline waiver, so trajectories are synthetic
explorations built from the gold annotations (gold reads + same-repo
distractor reads from other dev instances) and the metrics measure
RETENTION of the gold context through compaction, not free-form agent
exploration. Coverage = what the working context still carries after
each compaction; precision penalizes retained distractor material.
EditLoc uses the upstream gold-patch fallback (arm-independent);
patch_success stays UNAVAILABLE in every record.

Headline numbers (paired, n = 26; exact sign test and tie-corrected
Wilcoxon normal approximation from the W10 mirror):

| metric | A baseline | B task-state-ledger | delta B-A | sign p | Wilcoxon p |
| --- | --- | --- | --- | --- | --- |
| final file coverage | 0.974 | 0.974 | +0.000 | 1.000 | 1.000 |
| final file precision | 0.357 | 0.326 | -0.029 | 0.057 | 0.064 |
| final line coverage | 0.852 | 0.916 | +0.069 | 0.455 | 0.196 |
| final line precision | 0.320 | 0.586 | +0.097 | 0.004 | 0.003 |
| AUC coverage file / line | 0.860 / 0.583 | 0.863 / 0.605 | +0.003 / +0.022 | 0.625 / 0.815 | 0.715 / 0.420 |
| redundancy file / line | 0.672 / 0.587 | 0.661 / 0.605 | -0.011 / +0.017 | 0.629 / 1.000 | 0.190 / 0.954 |
| EditLoc recall / precision | 0.046 / 1.000 | 0.046 / 1.000 | 0 (gold-patch fallback) | - | - |

Practice-run observations (descriptive, not confirmatory):

- Gold FILE references survive compaction almost fully in both arms
  (coverage 0.974): the summarizer keeps naming every file it saw, so
  file granularity barely separates the arms.
- Gold LINE retention separates them: arm B keeps a significantly larger
  share of gold lines among retained lines (line precision 0.586 vs
  0.320, 18+/4-, sign p = 0.004) - by raw counts arm B ends 69% gold /
  31% distractor lines, arm A 47% / 53%. The protected task-state
  prompt biases the summary toward issue-relevant spans; the parsed
  ledger itself populated in only 34 of 104 arm-B cycles, so the effect
  observed is prompt-level.
- Summarizer wire cost: 439 requests for 208 cycle attempts (206
  fired), mean latency 20.4 s, 0 transport retries, 47 max_tokens ladder
  climbs, 0 error calls.

Remaining blockers are unchanged from section 4: repository checkouts
(byte spans, symbols, path filtering), real agent trajectories, and
patch_success all still need their live-phase prerequisites; this run
does not clear them. The holdout stays untouched.
