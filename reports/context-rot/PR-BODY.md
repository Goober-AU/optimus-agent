# Compaction: protected task state, honest retention accounting, evidence-keeping excerpts, context health

## Summary

Compaction replaces a window of history with model-written prose. Four problems follow; this
PR fixes three in code and makes the fourth visible:

- The cut-point walk charged zero tokens for custom messages and branch summaries, so
  `keepRecentTokens` was a lower bound, not a budget, and every injected block enlarged the
  retained tail.
- The summarizer's tool-result excerpt kept only the head and tail of a long log, so a
  failure, test total or compiler error in the middle never reached the summary.
- Repeated cycles had no durable carrier for constraints, decisions and verified evidence,
  while the tray showed a raw token counter with no indication of compaction cost.

The ledger is the one mechanism the campaign could not justify enabling: it is a measured
negative result and ships `off`. The excerpt, the accounting fix and the indicator ship on.

## What changed

1. `core/compaction/task_state.rs` (new, 924 lines): deterministic ledger built by fixed rules
   from the entries leaving the window. Caps per section (12 constraints, 10 decisions, 8
   superseded, 8 evidence, 12 anchors, 6 open work, 4 retractions), 320 characters per item,
   6000 rendered characters, overflow counts, supersession links, evidence dedup with repeat
   counts, provenance, camelCase JSON round trip, 12 unit tests.
2. `compaction.rs`: `compaction.taskState` = `off` (default) | `prompt`, env
   `PRIME_AGENT_COMPACTION_TASK_STATE`. Built in `prepare_compaction` from
   `[boundary_start..history_end)`, merged with the ledger on the previous compaction entry,
   appended to the summarizer prompt after `<user-instructions>`, persisted under
   `details.taskState`, carried by the deadline-fallback path, absent on the provider-checkpoint
   arm.
3. `compaction.rs`: `find_cut_point` charges custom-message text at chars/4, custom-message
   blocks at chars/4 (images 4096 characters) and branch summaries at chars/4; `Compaction`
   and `Other` entries still cost zero.
4. `compaction/utils.rs`: the tool-result excerpt keeps evidence-bearing lines from the elided
   middle (compiler errors, test totals, failing test names, source positions, panic and exit
   lines) inside the same 2000-character head/tail budget, with the kept count, the elided
   character count and the number of evidence lines that did not fit; a result with no middle
   evidence is byte-identical. 5 unit tests.
5. `core/context_health.rs` (new, 409 lines) plus tray wiring: the indicator beside the
   model/effort label is `CONTEXT HEALTH <score>`, 0-100, from window utilisation (nothing
   below 35%, 45 points at 95%) and compaction cycles (6 points each, capped at 30), with bands
   at 80/60/40 and labels degraded to `CTX HEALTH n` then `CH n` on narrow rows. 13 tests.
6. `research/context_rot/` (28 files, offline, 125 unit tests, run by `test.sh`) and
   `reports/context-rot/` (12 evidence files, including `REGRESSION-GATES.md`), plus
   `.changes/compaction-task-state-and-context-health.md`.

## Evidence: three measurements on one model, one source-level proof

1. C1 needle grid: 105/105 correct. Actual prompt tokens span 1,565 to 218,969; accuracy is
   1.00 at every nominal length (2k to 128k), position and needlet type. Long context is at
   ceiling for this model, so the ledger had no rot to repair. The contradictory-distractor
   arm (0/21) is a protocol defect with no unique gold, not a model result.
2. E2, the negative result: 96/96 in both arms at every cycle, over 30 planted items and 32
   probes. The summary retained the facts for three cycles on its own; the ledger produced no
   measurable recall gain. It is not free: prompt tokens per cycle were 72,742/44,502/45,281
   baseline versus 73,655/45,977/47,032 with it (+913, +1,475, +1,751), summaries 3,695-4,246
   characters versus 4,841-6,677, and the ledger block is 761 estimated tokens. E1 is 71/72 in
   both arms, one item each, and supports no direction.
3. E4: same prompt, same budget, only the excerpt policy differs. Old head/tail: 0/4 probes.
   New evidence recovery: 4/4. Run #7's tool body is 7,033 characters and the failure sits at
   character 3,421 (49% in); the old excerpt kept the first 1,431 and last 500 characters, the
   new one keeps 4 evidence lines in 1,769. The strings are absent from one conversation and
   present in the other, so this needs no model. Tokens fell 9,496 to 9,390, latency 4.0 to
   3.4 s.
4. Cut-point accounting: no measurement exists. The audit proved it from the source at
   `8ae54da` (`find_cut_point` skipped non-message entries); nothing quantifies the overshoot.

## Defaults

| mechanism | default | reason |
| --- | --- | --- |
| `compaction.taskState` | `off` | E2 shows no recall gain; +913 to +1,751 prompt tokens per cycle, longer summaries |
| cut-point accounting | always on | correctness: `keepRecentTokens` becomes a budget, not a lower bound |
| evidence excerpt | always on, no setting | 0/4 to 4/4, smaller input, byte-identical without middle evidence |
| CONTEXT HEALTH | always on, display only | no compaction decision reads it; it makes the cycle cost visible |

## What was not validated

- One model (`dgx-deepseek41/deepseek-v4.1-flash-1017`), one endpoint, default temperature,
  one seed per item, `reps = 1`; no cross-model claim.
- Synthetic corpus, no official benchmark scorer, no SWE-bench or Terminal-Bench run; Chroma's
  text was not redistributed, their scorer was not run, and no figure here is comparable to a
  published number.
- The measured arms are Python mirrors of the prompt and excerpt policies, not the shipped
  binary: the Rust code is unit-tested, not driven end to end. The offline JSONL in
  `reports/context-rot/` was produced by those mirrors outside this tree.
- The Rust suite ran here, on this branch, not on the host that wrote the measurements.
  `reports/context-rot/REGRESSION-GATES.md` records both trees side by side: serialized, the
  branch passes 3779 and fails 1, the baseline `8ae54da` passes 3748 and fails the same test,
  which is an environment gap (`uv` is not on `PATH`). The default-thread run is flaky in both
  trees for painted-frame tests that read global theme state and wall-clock timings.
- n = 1 per cell in E1 and E4; item seeds come from salted `hash()`, so an item is only
  regenerable from the `seed` in its JSONL row (`BUG-LEDGER.md` M5).

## Reviewer checklist

1. `cargo test -p pi-coding-agent --lib` offline, then `compaction_kernel_parity`,
   `compaction_recovery`, `compaction_length_recovery`, `compaction_observability`,
   `compaction_summary_guard`, `jev_ui_tests`.
2. Default off; an unrecognised `taskState` fails safe to `off` without reading the
   environment; with `off` the prompt is byte-identical to the base commit.
3. Ledger placement after `<user-instructions>`, never inside it; `taskState` persistence uses
   `skip_serializing_if`, is absent on the provider-checkpoint arm, and the merge deduplicates
   while retiring superseded ids.
4. Cut-point: charging injected blocks changes default-path retention. Confirm no fixture moves
   in a way you did not expect.
5. Excerpt: plain path unchanged (`truncate_for_summary_is_unchanged_without_middle_evidence`);
   the excerpt may exceed the budget by the marker text (test allows 2000 + 400).
6. Tray: a fresh session renders nothing; the counter drops before the score; the two
   source-text assertions in `tests/jev_ui_tests.rs` name the new accessor.
7. The five test struct literals set the new `task_state` field (see `COMMIT-PLAN.md` 2.1), and
   `resources/agent/docs/settings.md` documents `compaction.taskState` with its measured cost.
8. Not fixed here: D1 and D2 (a turn can be stopped and not resumed), D4 (summary-of-summary),
   D5, D6. See `reports/context-rot/BUG-LEDGER.md`.
9. `test.sh` runs the offline harness suite with the other python suites; it makes no network
   call and needs no extra dependency.

## Rollback

- Ledger: set `compaction.taskState` to `off` (the default) or revert its commit.
- Cut-point accounting: revert its commit; it shares `compaction.rs` with the ledger, so revert
  the ledger commit first if both are in.
- Evidence excerpt: revert its commit; `compaction/utils.rs` only.
- CONTEXT HEALTH: revert its commit; display only, no consumer.
- Harness and reports: revert those commits; the product does not load them.
