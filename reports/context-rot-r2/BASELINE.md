# BASELINE — code base, measurement frame, and the numbers every r2 report rests on

Scope: the baseline for the context-rot iteration-2 (r2) campaign. This file
records the commit under test, the r2 baseline measurement (R2-E1), the
iteration-1 context it inherits, and the external reproduction baseline
(TypeCompact). Report writer W14, 2026-10-20. No new measurement was produced
for this file; every number cites a committed artefact.

## 1. Code baseline

| field | value | source |
| --- | --- | --- |
| base commit | `6000d6bc1` (merge of PR #182, v0.1.36 + #181/#182) | `git log --oneline` in this worktree |
| campaign branch content | 23 commits on top of the base (research harness, D-fixes, W9-W10 experiments) | `git rev-list --count 6000d6bc1..HEAD` in this worktree |
| report worktree | `C:/Users/openclawuser/optimus-rot2-w14-reports`, branch `rot2/w14-reports` | `git status` in this worktree |
| offline rule | no network calls, no provider APIs, no model calls in any r2 measurement | campaign common rules; enforced by the committed drivers (no network code in `research/context_rot_r2/`) |

Iteration-1 background (PR #179, reports/context-rot/FINAL-REPORT.md): the
iteration-1 arms were Python mirrors of the production prompt and excerpt
policies, not the shipped binary. The r2 campaign exists to close that gap:
its measurements run the real Rust pipeline through the committed adapter
(`crates/pi-coding-agent/tests/rot2_multicycle.rs`) and the compactbench
harness binary (`crates/pi-coding-agent/src/bin/compaction_harness.rs`).

## 2. Measurement frame for r2

Every r2 multicycle number in this report set comes from one deterministic
offline instrument:

- pipeline: `prepare_compaction` -> `compact` -> `append_compaction` ->
  `SessionManager` restore, executed in-process by the adapter
  (`crates/pi-coding-agent/tests/rot2_multicycle.rs`, research entry point
  `rot2_production_multicycle`, `#[ignore]`d and driven by
  `research/context_rot_r2/orchestrate.py` through `ROT2_SCENARIO` /
  `ROT2_RESULT`).
- summarizer: a deterministic lossy fixture (copies protected blocks
  verbatim, fixed head/tail loss elsewhere, previous-summary decay). It makes
  zero model calls, so arm differences measure what the PIPELINE protects,
  not model skill.
- seeds: blake2b digest (iteration-1 measurement defect M5 fixed;
  `research/context_rot_r2/scenario.py`).
- scoring: anchor presence in the final context (summary + kept entries),
  versioned in `research/context_rot_r2/scoring_r2.py` (`scoringVersion
  r2.1`).

Category labels used across this report set (never mixed within one
table):

- SYNTHETIC-DIAGNOSTIC - measured on a synthetic fixture with a
  deterministic (non-model) summarizer or baseline; measures mechanism,
  not model quality.
- PRODUCTION-PATH - the number was produced by the real Rust compaction
  pipeline in this repo, not a Python mirror of it.
- CUSTOM-OPTIMUS - run on official external benchmark infrastructure with
  custom Optimus adapters/settings (mock provider, starter suite, custom
  scorer version); not comparable to official scores.

Consequence: all r2 multicycle numbers are pipeline-mechanics results,
labelled SYNTHETIC-DIAGNOSTIC on the PRODUCTION-PATH. No r2 number in
this report set is a model-quality measurement.

## 3. R2-E1 — the campaign baseline experiment

Scenario `r2-e1-multicycle`: 19 probes (8 constraints, 4 decisions, 4
evidence, 2 corrections/supersession, 1 subagent artifact), 5 compaction
cycles, filler turns between cycles, deterministic lossy summarizer, no
model calls. Committed artefacts:
`research/context_rot_r2/runs/r2-e1-multicycle.scenario.json`,
`.result.json`, `.score.json`.

Default arm (production compaction with `taskState = "off"`, the shipped
default):

| measure | value | source |
| --- | --- | --- |
| retention after 5 cycles | 0/19 (0.0 %) | `runs/r2-e1-multicycle.score.json` `arms[0]` |
| constraints | 0/8 | same, `byKind.constraint` |
| decisions | 0/4 | same, `byKind.decision` |
| evidence | 0/4 | same, `byKind.evidence` |
| supersession | 0/2 | same, `byKind.supersession` |
| subagent artifact | 0/1 | same, `byKind.task` |
| per-cycle anchors | 1 after the first compaction (cycle 0), then 0 at cycles 1-4 | `runs/r2-e1-multicycle.result.json` `arms[0].cycles[].anchorsPresent` |
| mean summarizer request | 14 765.2 chars | `.score.json` `arms[0].meanRequestChars` |
| ledger cycles | 0 | `.score.json` `arms[0].ledgerCycles` |

One anchor survives the first compaction (a head/tail excerpt);
the second compaction erases everything (see MULTICYCLE-DRIFT.md for the
8-cycle confirmation). This is defect D4 confirmed on the production path
(`reports/context-rot-r2/BUG-LEDGER.md`, D4).

Control arm (`taskState = "prompt"`, the iteration-1 ledger): 14/19 (73.7 %)
retained flat across all 5 cycles - constraints 8/8, decisions 4/4,
supersession 2/2, evidence 0/4, subagent 0/1 - at 18 429.4 mean request
chars (+3 664 over default). The ledger is the CONTROL B baseline for r2:
the typed-state v2 arm must beat 14/19 with evidence and subagent retained
at comparable or lower token cost
(`reports/context-rot-r2/EXPERIMENT-LEDGER.md`, decision).

## 4. Iteration-1 context inherited by r2

From `reports/context-rot/FINAL-REPORT.md` (campaign 2026-10-18/19, model
`dgx-deepseek41/deepseek-v4.1-flash-1017`, one DGX endpoint, `reps = 1`):

| result | number | meaning for r2 |
| --- | --- | --- |
| C1 needle grid | 105/105 across 7 lengths x 5 positions x 3 needlet types (1 565-218 969 actual prompt tokens) | long-context retrieval at ceiling for this model; rot is not a retrieval problem here, so r2 targets compaction loss instead |
| C1 contradictory distractor | 0/21, NOT SCORABLE (protocol defect, no unique gold) | do not cite as a model result; the authority rule is what the ledger implements |
| E2 multicycle ledger | 96/96 both arms, 3 cycles, no measurable recall gain; ledger cost +913 / +1 475 / +1 751 prompt tokens per cycle | the ledger is a token cost with no recall gain for a strong model on easy facts; r2 asks the harder question with a lossy summarizer |
| E4 tool-excerpt policy | 0/4 -> 4/4 probes at the same 2 000-char budget; input 9 496 -> 9 390 tokens, latency 4.0 -> 3.4 s | the one decisive iteration-1 improvement, shipped always-on; its Rust path is what r2 measures end to end |
| iteration-1 caveat | arms were Python mirrors; the Rust implementation was unit-tested, not end-to-end run | r2 replaces the mirror with the production-path adapter |

Full iteration-1 reports: `reports/context-rot/FINAL-REPORT.md` and the
per-experiment files it indexes.

## 5. TypeCompact reproduction baseline (external reference)

Deterministic offline reproduction of the CIKM 26 Knowledge Triage
multiturn-stability experiment (upstream pinned `a6ceb01`, Apache-2.0), on
the shipped 327-file stratified AAC sample, their regex reference
classifier, 50 % compaction ratio, seed 11. Committed artefact:
`research/context_rot_r2/repro/typecompact_repro.json` (n = 20 configs, 10
rounds; cycles 6-10 are our extension beyond their 5-round protocol).

Mean constraint recall vs the original constraint set:

| strategy | c1 | c3 | c5 | c10 |
| --- | --- | --- | --- | --- |
| TypeCompact (pin constraints) | 1.000 | 0.8872 | 0.8408 | 0.8408 |
| naive_head (their hierarchical proxy) | 0.3663 | 0.0607 | 0.0071 | 0.0071 |
| naive_tail (their temporal proxy) | 0.6334 | 0.1126 | 0.0538 | 0.0538 |
| naive_truncate | 0.3663 | 0.0607 | 0.0071 | 0.0071 |

Headline: TypeCompact holds 1.00 -> 0.84 by cycle 5 while the truncation
baselines cliff to 0.007 (naive_head) - a pin-outside-the-summarizer policy
survives repeated compaction that destroys naive summarization. The
full-suite rerun (n = 197 configs,
`research/context_rot_r2/repro/typecompact_repro_full_suite.json`)
reproduces the same shape: 1.000 -> 0.8482 at c5 (stable to c10) vs
naive_head 0.0252 and naive_truncate 0.0284 at c5.

Their committed LLM arm numbers (vanilla gpt-5.4-mini 0.1021, Sonnet 4.6
0.1011 at round 5) are carried in the same JSON as a labelled
reference-only block and are NOT claimed as ours. This reproduction is
SYNTHETIC-DIAGNOSTIC: their Python operators on static rule files, not the
Optimus pipeline.

## 6. What this baseline does and does not support

- It supports: pipeline-mechanics claims about repeated compaction on the
  real Rust path (R2-E1, E-INC), and the external evidence that pinning
  must-keep material outside the summarizer survives compaction.
- It does not support: any live-model claim. Every model-dependent r2 arm
  (real summarizer, real probe answering) is BLOCKED offline and not
  executed; see CODING-TASK-RESULTS.md for the exact blocker list.
- The r2-e1 default-arm 0/19 is a fixture result under a deliberately lossy
  deterministic summarizer. It bounds what the pipeline alone protects
  without the ledger; it is not a prediction about a specific model.
