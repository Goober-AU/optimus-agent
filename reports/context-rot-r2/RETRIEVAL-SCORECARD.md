# RETRIEVAL SCORECARD — every retrieval surface, iteration 1 to r2

Scope: the consolidated retrieval scorecard for the context-rot campaign.
Report writer W21, 2026-10-20. This file consolidates four sources and adds
no new measurement:

- W10's offline-pipeline scorecard draft, kept in place at
  `research/context_rot_r2/contextbench/RETRIEVAL-SCORECARD-DRAFT.md`
  (referenced, not moved; its content is summarized in section 4 below)
- iteration 1's retrieval scorecard,
  `reports/context-rot/RETRIEVAL-SCORECARD.md`
- the D12/D13 fixes on this branch (commits `021678796`, `157316268`)
- W4's typed-state v2 module (commit `c948905a3`) for the type-aware
  retrieval surface

Category labels are never mixed within one table. LIVE-MODEL rows are
iteration-1 model-run numbers; PRODUCTION-PATH rows describe the shipped
Rust code and its guards; infrastructure status rows carry no measurement
claim at all.

## 1. Measured surfaces inherited from iteration 1 (LIVE-MODEL)

From `reports/context-rot/RETRIEVAL-SCORECARD.md` (model
`dgx-deepseek41/deepseek-v4.1-flash-1017`, one DGX endpoint, `reps = 1`):

| # | surface | cells | result | status |
| --- | --- | --- | --- | --- |
| 1 | planted value from a long document, 1 565-218 969 actual tokens (C1 main) | 105 | 105 / 105 | measured, ceiling |
| 2 | discrimination against 10 same-shape related distractors (C1) | 21 | 21 / 21 | measured |
| 3 | contradictory same-label distractor (C1) | 21 | 0 / 21 | NOT SCORABLE (protocol defect, no unique gold) |
| 4 | session facts from a compaction summary after 1-3 cycles, 12 probes (E1) | 36 summary + 36 summary+tail per arm | 71 / 72 per arm, pooled over both readouts | measured, one-item differences |
| 5 | session facts from a compaction summary, 32 probes (E2) | 96 per arm | 96 / 96 per arm | measured, at ceiling |
| 6 | failure evidence from a 2 000-char tool excerpt, old policy (E4) | 4 | 0 / 4 | measured |
| 7 | same, new always-on excerpt policy (E4) | 4 | 4 / 4 | measured |

r2 did not re-run these surfaces. r2's multicycle experiments measure
retention (anchor presence in the restored context), a different quantity;
see `reports/context-rot-r2/MULTICYCLE-DRIFT.md`.

## 2. The memory path (PRODUCTION-PATH, code state; quality NOT EXECUTED)

Iteration 1 scored memory recall `NOT EXECUTED` and predicted it "fails
closed and silently when one entry has a foreign `projectId`"
(`reports/context-rot/RETRIEVAL-SCORECARD.md` section 2, rows 10-13 and
section 3). r2 fixed the two defects behind that prediction (D13 fully;
D12's fence landed with the soft-fence residual, status PARTIALLY FIXED in
`reports/context-rot-r2/BUG-LEDGER.md`); it did not add a quality
measurement.

| defect | fix on this branch | guard | effect on the retrieval path |
| --- | --- | --- | --- |
| D13 - recall fails closed silently (foreign-scope corpus yields zero hits with no marker; render-loop short-skip `space < 80` and stale-source skip could drop every hit without a trace) | `157316268` (`crates/pi-coding-agent/src/core/memory/search.rs`) | `foreign_only_corpus_fails_closed_and_surfaces_the_reason`, `budget_short_skip_fails_closed_and_surfaces_the_reason`, `partial_recall_skips_stay_silent` | every skip class is counted; a fail-closed outcome (hits existed, nothing rendered) emits one rate-limited warning naming the reason; partial skips stay silent because the rendered id list is the visible outcome |
| D12 - memory-recall text arrived as an unfenced, instruction-shaped user turn (stored project data could read as fresh user direction) | `021678796` (`crates/pi-coding-agent/src/core/messages.rs`) | `convert_to_llm_fences_memory_recall_text_but_not_other_custom_types` | `MEMORY_RECALL_TYPE` text is wrapped in `<memory_data>` tags at `convert_to_llm` time, mirroring the harness digest's `<harness_state>` fence; every other custom type keeps its verbatim rendering |

What this does and does not change: the memory recall surface now fails
loudly, and retrieved text is bounded as data. The quality of memory
retrieval (precision / recall of the recall block against a gold) remains
unmeasured - iteration-1 row 10 (`NOT EXECUTED`) is still open in r2.

## 3. Unmeasured surfaces (carried over from iteration 1)

| surface | iteration-1 status | r2 status |
| --- | --- | --- |
| Jev retrieval and Jev compaction filters | NOT EXECUTED | NOT EXECUTED (untouched in r2) |
| the same tasks on a second model or endpoint | NOT EXECUTED | still NOT EXECUTED for the iteration-1 surfaces: the r2 live family ran on a second model (GLM 5.3 - R2-E2/R2-E3/R2-E3ADV/R2-E5 + R2-E5-RM, W26, W27) but these surfaces themselves were not re-run on it |
| official benchmark scorers (Chroma context-rot, NoLiMa, RULER, LongBench, RepoQA, CompactBench, SWE-bench, Terminal-Bench) | NOT EXECUTED | partially changed: CompactBench and ContextBench now have committed custom infrastructure (sections 4-5); no official-scored number exists |

## 4. The r2 coding-task retrieval surface (offline infrastructure)

W10's draft (`research/context_rot_r2/contextbench/RETRIEVAL-SCORECARD-DRAFT.md`,
kept at its research path) records the ContextBench offline pipeline: 13
surfaces run offline and deterministically (parquet loading + sha256
verification, repo-level dev/holdout split 308 / 192 with no repo overlap,
stratified 20-instance smoke subset, gold-context parsing, file and
line-span precision/recall, byte-span math on synthetic inputs, AUC-coverage
and redundancy on recorded trajectories, EditLoc with the gold-patch
fallback, micro/macro aggregation, the A/B arm harness, paired statistics),
verified by 124 offline tests (127 with the 3 parquet-integration tests
gated on `CB_REFS_ROOT`; re-run for this report at this HEAD: 124 passed,
3 skipped). Six further items are blocked offline (repository checkouts,
symbol metrics, checkout-based path filtering, arms A/B on real agent
trajectories, `patch_success`, any leaderboard-comparable number; the W27
practice run substituted synthetic gold-derived trajectories, which is why
"arms A/B trajectories" is only partially addressed). Full status, split
counts and per-metric upstream citations: the draft above and
`reports/context-rot-r2/CONTEXTBENCH-RESULTS.md` (sections 5-6).

## 5. Type-aware retrieval: NOT EXECUTED

What is committed: W4's typed-state v2
(`crates/pi-coding-agent/src/core/compaction/typed_state.rs`, commit
`c948905a3`, 60 unit tests at introduction - 74 at the tip, plus the W35
red team and the D21-D29 fix wave, BUG-LEDGER.md) renders a durable index -
`render_durable_index` emits one line per current record with its stable id,
kind label and source-entry reference, plus the superseded count - and the
priority allocator, verifier and `render_hot_state` / `render_allocation`
behind it. The typed state is wired into compaction as an opt-in mode
(documented in the README and the settings reference, commit `0a35fbfa6`)
and measured live by R2-E5, re-measured post-fix by R2-E5-RM (W47:
TASK-E typed 5/5 cycles no halt; E3ADV C1 SUPERSEDED at every cycle).

What does not exist: any retrieval consumer. Nothing queries the durable
index at recall time; ranking or selecting over it (and hydrating selected
records from the ledger) is future work. Status: NOT EXECUTED.

External reference only, not an Optimus number (their committed LLM runs,
`research/context_rot_r2/repro/typecompact_repro.json` reference block and
`research/context_rot_r2/KNOWLEDGE-TRIAGE-STUDY.md` section 5.1): their
TypeRetrieve reaches recall@50 = 1.000 in scope versus 0.7045
(vanilla gpt-5.4-mini) and 0.7255 (vanilla Sonnet 4.6), but recall@5 =
0.3973 because it pins constraints ahead of relevance. That is the trade a
type-aware retriever inherits and must be designed around; it is evidence
about their operator, not a claim about ours.

## 6. Decision and gaps

- KEEP the iteration-1 regression-gated surfaces as they are (C1 main rows,
  E4 always-on excerpt policy); do not re-litigate them in r2.
- The two surfaces iteration 1 named as the largest gap - memory-recall
  quality and the Jev filters - are still the priority order. r2 made the
  memory path safe and observable (D12/D13, section 2) without measuring
  its retrieval quality; the Jev filters remain untouched and unmeasured.
- The ContextBench pipeline (section 4) is the committed instrument for the
  coding-task retrieval surface; its first model-backed numbers exist as
  the W27 live practice run (CUSTOM-OPTIMUS + LIVE-MODEL: retention through
  compaction over synthetic gold-derived trajectories, arm B line precision
  0.586 vs 0.320, sign p = 0.004; `research/context_rot_r2/contextbench/live/`).
- Type-aware retrieval stays NOT EXECUTED until a consumer of the durable
  index exists; the committed renderer is the input it would read (the
  typed-state ledger is now wired into compaction, but nothing queries the
  index at recall time).
