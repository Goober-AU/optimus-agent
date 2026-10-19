# FINAL REPORT — context rot and compaction fidelity in Optimus (campaign 2026-10-18/19)

Entry point for the campaign. Status: measurement complete for three experiments (E1, E2,
E4) and one long-context grid (C1); implementation shipped as an uncommitted PR on
`8ae54da78d94920b543e22567403b7e939cb5bd2`. The headline is split: the single decisive
improvement is in the tool-excerpt policy; the task-state ledger is a negative result on
recall and a real cost in tokens; long-context retrieval is at ceiling for this model.

## 1. Hypothesis

| id | hypothesis | test |
| --- | --- | --- |
| H1 | accuracy falls as the prompt grows, or as evidence moves to the middle | C1 needle grid: 7 lengths x 5 positions x 3 needlet types |
| H2 | a model-written compaction summary loses specific session facts (paths, counts, error codes, superseded decisions) | E1: 12 planted facts, 3 cycles, summary and summary+tail readouts |
| H3 | repeated compaction compounds the loss, and a deterministic ledger stops it | E2: 30 planted items, 32 probes, 3 cycles, baseline versus ledger |
| H4 | at least one loss is caused by the summarizer's *input*, not by the model's summarising ability, and is therefore fixable in code | E4: same prompt and budget, only the tool-excerpt policy differs |

## 2. What was measured

| id | arms | cells | model calls | artefact |
| --- | --- | --- | --- | --- |
| C1 main | 1 | 105 | 105 | `runs/c1/niah-main.jsonl` |
| C1 distract | 2 (`related`, `contradictory`) | 42 | 42 | `runs/c1/niah-distract.jsonl` |
| E1 | 2 (baseline, ledger) | 6 compactions + 144 probes | 150 | `runs/e1/compact.jsonl` |
| E2 | 2 (baseline, ledger) | 6 compactions + 192 probes | 198 | `runs/e2/compact.jsonl` |
| E4 | 2 (old excerpt, new excerpt) | 2 compactions + 8 probes | 10 | `runs/e4/tool.jsonl` |
| smoke | 1 | 4 | 4 | `runs/smoke/niah.jsonl` |

Model: `dgx-deepseek41/deepseek-v4.1-flash-1017` on one DGX endpoint, temperature default,
one seed per item, `reps = 1` everywhere. Corpus: synthetic, deterministic filler in four
registers. Protocol shape follows Chroma's context-rot NIAH extension; their text was not
redistributed and their scorer was not run.

## 3. What was found

### 3.1 H1 is not supported: long-context retrieval is at ceiling

105 / 105 correct. Actual prompt tokens span 1565 to 218969. Accuracy is 1.00 at every
length (2k to 128k nominal), at every position (0.0, 0.25, 0.5, 0.75, 1.0) and for every
needlet type (exact lexical, semantic paraphrase, causal). Median latency rises from about
6.7 s at 2k to 37.9 s at 128k nominal. On this task, for this model, there is no rot to
measure: the grid is a saturation gate, not a capability score.

### 3.2 The contradictory-distractor arm is NOT SCORABLE

`summary.json` reports 21 / 42 for the distractor arm. The two sub-arms must be split:
`related` (10 same-shape statements with different labels) scores 21 / 21; `contradictory`
(the same label restated with a different code) scores 0 / 21. The second number is a
protocol defect: there is no unique gold, all 21 records hit the 200-token cap, emitted no
`</think>`, and were parsed from deliberation text that names both codes, which the scoring
rule rejects by construction. The correct design states authority explicitly (later
statement supersedes, or "change of plan"), which is what the ledger implements. Do not cite
0 / 21 as a model result.

### 3.3 H2 is weakly supported at best: one item, in one cell, in each arm

E1 graded over both readouts: each arm scores 71 / 72. The baseline loses `q6_test_count` at
cycle 3 of the summary readout by answering `583 passed; 0 failed; 0` (the runtime-suite
count) instead of `4785` (the lib count). The ledger loses `q10_remaining` at cycle 3 of the
tail readout because its answer was unsplit deliberation text with no gold token in it.
Different cells, different causes, one item each, n = 12 per cell. **This is not
statistically meaningful and no direction of effect can be claimed.**

### 3.4 H3 is NOT supported: this is the campaign's negative result

E2 scores 96 / 96 in both arms at every cycle, over 30 planted items and 32 probes. For this
model on this fixture, the model-written summary retains simple facts for three cycles on
its own, and the ledger produced **no measurable recall gain**. The ledger is not free:

| cost | baseline | ledger |
| --- | --- | --- |
| prompt tokens, cycle 1 / 2 / 3 | 72742 / 44502 / 45281 | 73655 / 45977 / 47032 |
| delta per cycle | - | +913 / +1475 / +1751 |
| summary length, cycle 1 to 3 | 3695 to 4246 chars (+15 %) | 4841 to 6677 chars (+38 %) |

The ledger block itself is 761 estimated tokens; the rest of each delta is the longer
carried summary. Latency per compaction is 6.4-15.5 s, so the cost is tokens, not time.

### 3.5 H4 is supported, and it is the decisive result

E4 changes one thing: how a tool result is excerpted into the summarizer input, at the same
2000-character budget and the same prompt. Old head/tail policy: 0 / 4 probes. New
evidence-recovery policy: 4 / 4. The failing test name, the test totals and the compiler
error were absent from one conversation and present in the other, which is verifiable
without a model: run #7's body is 7033 chars, the failure sits at char 3421 (49 % in), and
the old excerpt keeps only the first 1431 and last 500 chars. Input tokens fall (9496 to
9390) and latency falls (4.0 s to 3.4 s). Every old-arm answer was an explicit "NOT STATED",
which is the correct behaviour for the summary it was given.

## 4. What shipped

1. `crates/pi-coding-agent/src/core/compaction/task_state.rs` (new, 924 lines): deterministic
   ledger built by fixed rules from the leaving window, with provenance (`tN` entry refs),
   caps (12 constraints, 10 decisions, 8 superseded, 8 evidence, 12 anchors, 6 open, 4
   retractions; 320 chars per item; 6000 rendered chars), a supersession pass, camelCase
   JSON round trip, and 12 `#[test]` functions (counted in the file; the campaign brief
   said 13).
2. `compaction.rs`: `compaction.taskState = "off" | "prompt"` (default `off`, env
   `PRIME_AGENT_COMPACTION_TASK_STATE`). The ledger is built in `prepare_compaction` from
   `[boundary_start..history_end)`, merged with the previous compaction's
   `details.taskState`, appended to the summarizer prompt outside the `<user-instructions>`
   block, persisted under `details.taskState`, and carried by the deadline-fallback path.
   It is not attached when a provider checkpoint is used.
3. `compaction.rs`: `find_cut_point` now charges custom messages (text at chars/4, blocks at
   chars/4 with images at 4096 chars) and branch summaries to the retention budget, so
   `keep_recent_tokens` is a budget instead of a lower bound. `Compaction` and `Other`
   entries still cost zero by design.
4. `compaction/utils.rs`: evidence-recovery excerpt inside `TOOL_RESULT_MAX_CHARS`. Keeps
   evidence-bearing lines from the elided middle, pulls indented continuation lines (failing
   test names, source positions), states the kept count, the elided character count and
   "N further evidence line(s) beyond the excerpt budget". Text with no middle evidence is
   byte-identical to the old behaviour. 5 new unit tests.
5. `core/context_health.rs` (new, 409 lines) plus tray wiring: the indicator beside the
   streaming-TPS/model row is `CONTEXT HEALTH <score>`. Score 0 to 100: utilisation below
   35 % costs nothing, 45 points maximum at 95 %, and 6 points per compaction cycle capped
   at 30. Bands at 80 / 60 / 40, documented labels (`CONTEXT HEALTH 92`, `CTX HEALTH 92`,
   `CH 92`), unknown usage renders nothing. 12 `#[test]` functions in the module.

## 5. What a reviewer should check

1. `cargo test -p pi-coding-agent --lib` offline. This campaign ran NO cargo command: the
   tests were read, not executed. That is the largest unverified claim in this PR.
2. Default is off: `default_compaction_settings()` sets `task_state: Some("off")`, and an
   unrecognised configured value fails safe to `off` without reading the environment.
3. With `off`, the summarizer prompt is byte-identical to baseline: the ledger block is only
   appended when `prepare_compaction` produced a non-empty ledger.
4. Ledger placement: appended to the instruction closure output, after the
   `<user-instructions>` block, never inside it.
5. Persistence: `CompactionDetails.task_state` with `skip_serializing_if = "Option::is_none"`;
   the JSON key is `taskState`; `previous_ledger` is read from the last compaction without a
   provider checkpoint; the merge deduplicates by token key and retires superseded ids.
6. Cut-point accounting: confirm that charging custom messages does not change any existing
   fixture's retained tail in a way that surprises you. This is a behaviour change on the
   default path, unlike the ledger.
7. Excerpt: confirm the plain path is unchanged (test
   `truncate_for_summary_is_unchanged_without_middle_evidence`) and that the marker text
   satisfies the "never silent" rule.
8. Tray: confirm a fresh session renders nothing, and that the narrow-terminal ladder drops
   the counter before the score.
9. The three open defects that can still damage a real session: D1 (threshold hook versus
   executor), D2 (turn stopped and not resumed), D4 (summary-of-summary under both policies).

## 6. Recommendation for defaults

| mechanism | default | decision | reason |
| --- | --- | --- | --- |
| `compaction.taskState` ledger | `off` (shipped) | OPT-IN | no measurable recall gain (E2 negative), +608-761 estimated ledger tokens per request (measured prompt deltas +755 to +1751), 15-57 % longer summaries |
| evidence-recovery tool excerpt | always on, no setting | KEEP | 0 / 4 to 4 / 4, smaller input, byte-identical when no middle evidence exists |
| cut-point token accounting | always on | KEEP | correctness fix: retention is a budget, not a lower bound |
| CONTEXT HEALTH indicator | always on, display only | KEEP | no compaction decision reads it; it makes cycle cost visible |
| C1 needle grid | keep boundary cells for release checks | KEEP | saturated at 219k tokens; full grid costs 7.0M tokens for no extra signal |
| contradictory distractor arm | do not run as designed | REVERT | no unique gold; replace with an authority-rule arm |

## 7. What this report does not claim

- Not that context rot was eliminated. No mechanism was shown to prevent rot in general, and
  the ledger did not move a single probe in E2.
- Not cross-model generality. One model, one endpoint, one seed per item, `reps = 1`.
- Not benchmark comparability. Chroma, NoLiMa, RULER, LongBench, CompactBench, RepoQA,
  SWE-bench and Terminal-Bench were not run, and no official scorer was used.
- Not statistical significance. Every arm difference here is a one-item or four-probe
  difference with no repetition, except E4, whose 0/4 to 4/4 rests on a mechanism that is
  verifiable in the policy code.
- Not a coding-task capability score. E4's fixture is synthetic and single-error.
- Not a measurement of the shipped binary. The arms are Python mirrors of the production
  prompt and excerpt policies; the Rust implementation is unit-tested, not end-to-end run.
- Not a statement about compilation on the measurement host. The Rust suites ran in the
  branch worktree, not next to these measurements; `REGRESSION-GATES.md` records that gate.
- Not reproducible from the drivers alone. Item seeds come from salted `hash()`, so an item
  is only regenerable from the `seed` recorded in its JSONL row (`BUG-LEDGER.md` M5).

## 8. Report index

`BASELINE.md` (commit, model, caveats), `CONTEXT-ROT-CURVES.md` (C1), `COMPACTION-FIDELITY.md`
(E1), `MULTICYCLE-DRIFT.md` (E2), `CODING-TASK-RESULTS.md` (E4 and coding gaps),
`RETRIEVAL-SCORECARD.md` (all surfaces in one table), `EXPERIMENT-LEDGER.md` (every arm and
every NOT EXECUTED item), `BUG-LEDGER.md` (D1-D20, M1-M5, O1-O4), `REGRESSION-GATES.md`
(branch and baseline suites, environment used, parity verdict).
