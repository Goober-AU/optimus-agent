# FINAL REPORT — context rot and compaction fidelity in Optimus (iteration 2, 2026-10-19/20)

Entry point for the r2 campaign. Iteration 1
(`reports/context-rot/FINAL-REPORT.md`) measured compaction policies through Python
mirrors and found one decisive improvement (the evidence-recovery excerpt) and one
negative result (the v1 task-state ledger: no recall gain, a token cost). This
campaign closed the mirror gap: every multicycle number below marked PRODUCTION-PATH
was produced by the real `prepare_compaction` -> `compact` -> `append_compaction` ->
`SessionManager` restore pipeline, offline, through the committed adapter; one
experiment (R2-E2) ran the real prompts against a live model. The headline is split
again, and sharper than iteration 1's: repeated compaction is a confirmed
information-loss mechanism on the production path, the v1 ledger is not a safe fix
for it (under a live model it inverted a user correction), twelve audited defects
are addressed - ten with behaviour changes and regression guards, D6's validator
landed with its call-site wiring pending, D2 verified intact and pinned by tests -
and the designed replacement (typed state v2) is shipped as a module but not yet
wired.

Base `6000d6bc1` (v0.1.36 + #181/#182). Written on branch `rot2/w22-final` at
`021678796` (40 commits over base, `git rev-list --count 6000d6bc1..HEAD`); the
integration branch `improve/context-rot-iteration-2` carries one further docs commit
(`eec1a8ec5`, ledger updates for W11/W13/W8 and the R2-E2 headline), cited below via
`git show`.

## 1. Hypotheses

| id | hypothesis | test | verdict |
| --- | --- | --- | --- |
| H-r2-1 | the default summary-of-summary path loses protected information over repeated compactions on the production path | R2-E1: 19 anchors, 5 cycles, deterministic lossy summarizer; E-INC-1/2a: 8 cycles, 2 seeds | supported as a worst-case bound (0/19 after 5 cycles, 0/19 already after 2); NOT confirmed live - R2-E2's real summarizer retained 19/19 and 37/37 anchors at every cycle |
| H-r2-2 | the v1 ledger does not safely protect state | R2-E2 base+hard (live GLM 5.3); E-INC-2b/2c (W9-F2 id collisions, W9-F4 mutual retirement); the W8 calibration (the health heuristic, AUC 0.604, is no safety net either) | strongly supported: the ledger can invert a user correction; retention is seed- and placement-dependent; nothing else predicts the loss |
| H-r2-3 | provenance-aware typed state fixes the mechanism | typed_state v2 module (design + 60 unit tests, `c948905a3`); W11's content-derived id and correction-safe supersession fixes in v1 (`1754a3e1b`); W16 supersession work in flight | supported by design and unit tests only; the decisive end-to-end measurement (W15 wiring, then a typed arm in the multicycle adapter / R2-E3) is not executed at this writing |
| H-r2-4 | pressure/cycle health does not predict semantic loss | W8 calibration corpus: 66 labelled cases scored by the production scorer | supported on synthetic labels: ROC-AUC 0.604 against `lost_constraint` |

## 2. What was measured

Category labels (defined in `reports/context-rot-r2/BASELINE.md` section 2, extended
here; never mixed within one table):

- SYNTHETIC-DIAGNOSTIC - deterministic fixture, zero model calls; measures
  mechanism, not model quality.
- PRODUCTION-PATH - produced by the real Rust compaction pipeline in this repo.
- CUSTOM-OPTIMUS - official external benchmark infrastructure with custom Optimus
  adapters/settings; not comparable to official scores.
- LIVE-MODEL - real model endpoint as summarizer and/or probe answerer.
- SYNTHETIC-CALIBRATION - generated labels scored by the production scorer.

| id | question | arms / runs | model | artefacts | label |
| --- | --- | --- | --- | --- | --- |
| R2-E1 | does the pipeline protect anchors over 5 compactions | 2 arms (default, taskState), 1 run, 19 probes | none (deterministic lossy fixture) | `research/context_rot_r2/runs/r2-e1-multicycle.{scenario,result,score}.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| R2-E0 | adapter smoke | 2 arms, 3 probes, 5 cycles | none (fixture) | superseded by R2-E1 (same mechanism, smaller probe set) | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-1 | growth, decay, request composition, policy exposure | 2 arms x 2 seeds + env-policy probe (3 runs) | none (fixture) | `research/context_rot_r2/incremental/results/e-inc1.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-2a | decay curves, determinism, path independence | 7 cycle-truncated runs + 1 byte-equal rerun | none (fixture) | `incremental/results/e-inc2a.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-2b | late-planted constraints | 2 runs (`w9-{a,b}-late3c`) | none (fixture) | `incremental/results/e-inc2b.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-2c | late supersession | 3 runs (`w9-{a,b}-latecorr5c`, `w9-a-8c-retired`) | none (fixture) | `incremental/results/e-inc2c.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| E-INC-3 | token cost | accounting over all 16 adapter runs | none (fixture) | `incremental/results/e-inc3.json` | SYNTHETIC-DIAGNOSTIC / PRODUCTION-PATH |
| R2-E2 base | live multicycle, 19 probes x 5 cycles | 2 arms, 95 round records per arm | GLM 5.3 (DGX K3 LaunchPad, SGLang, temperature 0) | `research/context_rot_r2/live/` (`results.json`, `raw-calls.jsonl`, `r2-e2-multicycle.*`) | LIVE-MODEL |
| R2-E2 hard | live multicycle, 37 probes (20 constraints) x 5 cycles | 2 arms, 111 round records per arm | GLM 5.3 (as above) | `live/r2-e2-hard.*`, `live/results.json` | LIVE-MODEL |
| W12 suites | CompactBench public practice suites, 3 drift cycles | 9 arms x 2 suites (18 runs; starter 20 cases, elite 100) | mock-deterministic (mock eval provider, scorer 2.0.0) | `research/context_rot_r2/compactbench_adapter/results/w12/`, `SUITE-RESULTS.md` | CUSTOM-OPTIMUS (PRODUCTION-PATH pipeline) |
| W6 validation | adapter vs harness, 1 drift cycle | 5 arms, 8 starter cases | mock-deterministic | `compactbench_adapter/results/*.jsonl`, README "Validation runs" | CUSTOM-OPTIMUS (superseded mode, kept for the record) |
| W10 pipeline | ContextBench retrieval metrics, arms A/B | 0 runs (offline scaffold; arms C/D emit blocked records only) | none (no trajectories committed) | `research/context_rot_r2/contextbench/` | CUSTOM-OPTIMUS |
| W8 calibration | does the context-health score predict constraint loss | 1 heuristic, 66 cases (30 positives) | none (production scorer over generated labels) | `research/context_rot_r2/health/context_health_calibration.json` | SYNTHETIC-CALIBRATION |
| TypeCompact repro | external reference: pinning outside the summarizer | 4 strategies, n = 20 configs + full suite n = 197 | none (their regex reference classifier) | `research/context_rot_r2/repro/typecompact_repro{,_full_suite}.json` | SYNTHETIC-DIAGNOSTIC (external) |

R2-E2 used 581 wire calls (526 distinct after content-keyed dedupe; the rest are
length-ladder retries) against a ~600-call guide, with zero probe extraction
failures (`live/README.md` "Budget accounting"). Everything else in this table is
offline and deterministic; seeds are blake2b-digest based (iteration-1 measurement
defect M5 fixed, `research/context_rot_r2/scenario.py`).

## 3. What was found

### 3.1 R2-E1: the fixture bound

With an even mildly lossy summarizer, five compactions on the default arm lose every
unprotected anchor: 0/19 retained (one anchor survives the first compaction as a
head/tail excerpt; the second compaction's 400-char carried-summary decay erases the
rest), while the v1 ledger holds 14/19 flat - constraints 8/8, decisions 4/4,
supersession 2/2, but evidence 0/4 and subagent artifact 0/1 - at 18 429 vs
14 765 mean request chars (+3 664, about +916 tokens/cycle, matching iteration-1's
+913..1 751) (`runs/r2-e1-multicycle.score.json`). The two 0-shaped gaps are the v1
design's, not the fixture's: `EVIDENCE_RE` misses `cargo test: N passed` /
`build succeeded` shapes (defect N1) and `entry_text` is blind to `CustomMessage`
subagent entries (N2) (`reports/context-rot-r2/BUG-LEDGER.md` section 2).

### 3.2 E-INC-1/2a/3: the bound is structural, not an artifact of five cycles

Over 8 cycles and 2 seeds the two decay regimes are stable: default decays 1/19 ->
0/19 by the second compaction and stays 0 at every cycle count; taskState holds
14/19 (seed a) and 13/19 (seed b) flat through cycle 8
(`incremental/results/e-inc1.json`). The k-cycle reruns equal cycle k-1 of the
longer run on both arms (path independence) and an identical rerun reproduced
every field (determinism) (`e-inc2a.json`). Summaries reach a fixed point at cycle 1
(default 1 236 chars / 309 tokens; taskState 2 846 chars / 712 tokens), so growth is
non-compounding; the taskState arm pays the ledger twice per steady cycle (render +
instruction 2 091 + 274 chars, plus the protected block copied into the carried
summary), a net +3 979 chars (+995 tokens) per cycle (`e-inc1.json`
`requestComposition`). `summary_update_policy` is not reachable through the
adapter: a `policy-consolidate-repeated-v1` arm and the env var were byte-identical
to `default`, so the policy's designed effect remains NOT EXECUTED.

### 3.3 E-INC-2b/2c: retention is placement- and seed-dependent

Constraints planted in cycle 3 instead of the opening turn survive worse, not
better: taskState constraint retention drops 8/8 -> 4/8 (seed a) and 7/8 -> 3/8
(seed b), total 10/19 and 9/19 (`incremental/results/e-inc2b.json`). Two verified
mechanisms: W9-F1, keyword-less constraint lines are invisible to `CONSTRAINT_RE` /
`SUPERSEDE_RE` when planted as separate entries (baseline capture works only
because all constraints share one "never drop ..." message); W9-F2, ledger ids are
numbered by insertion and the merge's blanket `retain(!retired.contains(id))`
silently deletes a newly captured item that reuses a retired id. Late supersession
(cycle 5) keeps the new decision but does not retire the old one: the two CORRECTION
lines share more tokens with each other than with the old decision and retire each
other (W9-F4), 15/21 vs 16/21 for early placement, the difference being one more
W9-F2 id collision (W9-F5) (`e-inc2c.json`). Anchor-presence scoring also
overstates current-state correctness: surviving baseline anchors can sit in
SUPERSEDED render lines (W9-F3).

### 3.4 R2-E2: the live model is stronger than the bound, and the ledger is worse than nothing on corrections

With the real GLM 5.3 as summarizer and probe answerer, the default arm shows no
measurable rot at this scale: base 95/95, hard 111/111, anchor retention 19/19 and
37/37 at every cycle on both arms (`live/results.json`). R2-E1's 0/19 is therefore a
pipeline-worst-case bound, not a prediction about this model. The taskState arm
scores 89/95 on the base scenario: 19/19 at rounds 1-2, then 17/19 at rounds 3, 4
and 5, failing exactly the correction probes. The headline finding: the v1 ledger
inverts the user's correction. The user says "forget that; use architecture B ... The
earlier A decision is superseded"; `SUPERSEDE_RE` is token/positional, pairs the two
CORRECTION entries with each other (C12 <-> C10) while ordinary constraint lines
churn into spurious pairs, the render lists the corrections under SUPERSEDED
(history) and the pre-correction "architecture A" decision under DECISIONS (current),
and the summarizer - following the protected state faithfully - writes "use
architecture A ... the B correction is superseded" from cycle 3 on. The probe
"After the correction, which architecture is the current choice?" is answered
`architecture A` (3-rep majority at round 5). The default arm handles the same
correction correctly at every cycle (`live/README.md` Finding 1). The hard scenario
does not contradict this: the same defective rule mis-fires there as constraint
churn (15 of 20 constraint lines in spurious pairs, 5 survive ACTIVE) plus
ambiguity, which the model resolves from the raw transcript text (111/111);
correctness under the v1 ledger is luck of how the regex pairing lands. R2-E2
measured the pre-W11 ledger (the mirror was committed before `1754a3e1b`); the W11
fixes target exactly this mechanism but were not re-measured on the R2-E2 corpus.
Operationally (Finding 3): GLM 5.3 reasoning tokens bill against `max_tokens`;
ledger-bearing compactions burned 8k-32k reasoning tokens, the production 8192
budget with a single 2x retry failed `validate_summary` on 3 of 5 base cycles, a
bounded ladder to 65 536 completed all cycles, and taskState summarizer latency
reached 137.8 s per call.

### 3.5 W12 suites: plumbing validated, quality degenerate by construction

All quality scores are bit-identical across all nine arms (overall 0.375 starter /
0.3326 elite) because the mock eval provider returns a constant; these runs validate
plumbing only and no quality claim is made or possible
(`compactbench_adapter/SUITE-RESULTS.md`). The mock-independent observables do
separate the arms: zero method warnings across all 1 920 custom-arm cycles (every
`compact()` landed a valid cut on the ladder's first candidate), task-state
artifacts are the largest (compression 0.48x starter / 0.74x elite), then plain
(0.55x / 0.90x), oracle (0.78x / 0.79x), echo-first-n (0.83x / 1.12x), and the
built-in baselines collapse to the null floor (108.76x / 178.24x). Per-cycle token
trajectories flatten after cycle 1 for the Rust arms while the oracle grows
monotonically, confirming delta-append mode engaged.

### 3.6 W10 pipeline: ready, empty

The committed ContextBench pipeline loads the 500-instance verified subset of the
pinned upstream commit with sha256 verification, splits dev/holdout 308/192 over
disjoint repo sets (byte-reproducible manifest), mirrors the upstream gold-context
metric layer function by function with citations, and provides a matched A/B
harness with an exact sign test and a tie-corrected Wilcoxon
(`contextbench/dataset.py`, `metrics.py`, `harness.py`). 127 offline tests pass
(`86e8ad09b`). No trajectories exist (live phase), arms C (typed state) and D
(source-grounded reconstruction) emit blocked records only, and `patch_success` is
UNAVAILABLE by construction, so no retrieval number is claimed anywhere
(`CODING-TASK-RESULTS.md` section 2).

### 3.7 W8 calibration: the health heuristic does not predict semantic loss

The production context-health score, read as a risk predictor (risk = 1 - score/100)
against `lost_constraint` on the 66-case synthetic corpus (30 positives), scores
ROC-AUC 0.604, Brier 0.252, 10-bin ECE 0.146, and at threshold 0.5 precision 0.542 /
recall 0.433 / FPR 0.306 / FNR 0.567
(`research/context_rot_r2/health/context_health_calibration.json`; generator
`crates/pi-coding-agent/src/core/context_health_calibration.rs`). Utilisation and
compaction cycles are poor predictors of semantic constraint loss. The labels are
generated (they encode the R2-E1 finding that loss is dominated by an unobservable
restatement factor), so this is a harness result, not a field result; nothing in
production reads these metrics.

### 3.8 TypeCompact reproduction: external confirmation of the pinning principle

A deterministic offline reproduction of the CIKM 26 Knowledge Triage
multiturn-stability experiment (upstream pinned `a6ceb01`, their regex reference
classifier, 50 % compaction ratio, seed 11) shows pinned constraints holding
1.000 -> 0.8408 constraint recall by cycle 5 (0.8482 on the 197-config full suite,
stable to cycle 10) while truncation baselines cliff to 0.0071-0.0284
(`repro/typecompact_repro{,_full_suite}.json`). Their committed LLM-arm numbers are
carried as labelled reference-only blocks and are not claimed as ours.

## 4. What shipped

### 4.1 Defect fixes (all on this branch; guards named)

| defect | fix (commit) | guard tests |
| --- | --- | --- |
| D1 threshold hook vs executor (turn stall) | `0a896e639` shared predicate `threshold_auto_compaction_ready` gates both | `d1_toggle_off_over_threshold_does_not_stop_the_turn` (`tests/compaction_turn_flow.rs`) |
| D2 stopped turn not resumed | verified intact on this base; pinned by tests, no production change (`b6487b9cd`) | `d2_interactive_turn_crossing_threshold_compacts_and_continues`, `d2_queued_user_prompt_completes_after_threshold_compaction` |
| D5 no-op compaction is a paid model call | `3b08e3f2e` empty summarize window skipped regardless of `previous_summary` | `repeated_trigger_with_no_new_material_resolves_to_no_op`, `new_material_after_the_boundary_still_prepares`, `a_no_op_skip_leaves_the_next_real_compaction_boundaries_intact` (`tests/compaction_summary_path.rs`) |
| D6 extension summaries bypass validation | `289f3c2e1` `validate_extension_compaction_summary` (same structural handoff check as the model path); the `agent_session.rs` call-site wiring is the remaining step | `extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries` |
| D8 migration drops the compaction boundary | `a56631631` falls back to the first entry after the compaction | `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds`, `migration_ignores_a_boundary_pointing_at_the_session_header` (`src/core/session_manager.rs`) |
| D9 fallback boilerplate becomes `previous_summary` | `720428967` dedicated walk skips `deadlineFallback` and provider checkpoints | `deadline_fallback_boilerplate_is_not_the_next_previous_summary`, `fallback_only_history_yields_no_previous_summary` |
| D10 branch summaries lose tool output | `0e25a7db1` tool results pair with their assistant calls, evidence-aware excerpt, budget walk keeps the call of a kept result (the "never retry" sub-item remains open) | `branch_summary_input_carries_the_failing_tool_result`, `branch_summary_input_skips_tool_results_with_no_text` (`tests/rot2_branch_summary.rs`) |
| D12 memory recall is unfenced instruction-shaped text | `021678796` MEMORY_RECALL_TYPE custom text fenced as `<memory_data>` at `convert_to_llm` time (soft-fence residual documented) | `convert_to_llm_fences_memory_recall_text_but_not_other_custom_types` (`src/core/messages.rs`) |
| D13 recall fails closed silently | `157316268` fail-closed surfaces (foreign-only corpus, short-skip, stale-source) emit rate-limited warnings with counts; partial skips stay silent | `foreign_only_corpus_fails_closed_and_surfaces_the_reason`, `budget_short_skip_fails_closed_and_surfaces_the_reason`, `partial_recall_skips_stay_silent` (`src/core/memory/search.rs`) |
| D14 read-files inventory always empty | `439808412` reads recorded structurally from `path`/`paths` arguments of non-edit tool calls | `non_edit_path_tool_calls_populate_the_read_inventory`, `compact_persists_a_non_empty_read_files_inventory`, `path_arguments_of_non_edit_tool_calls_record_reads` (`src/core/compaction/utils.rs`) |
| D16 user images cost zero tokens | `fa4ccc8f2`/`0bb973e43` shared `IMAGE_BLOCK_CHARS` (4096 chars = 1024 tokens) across estimator paths; `4dea91d72` aligns kernel parity | `rot2_user_image_blocks_cost_tokens_d16` (`tests/rot2_multicycle.rs`), `image_blocks_cost_4096_chars_1024_tokens` (`request_tokens.rs`), `compaction_kernel_parity` 23/23 |
| D17 estimate excludes system prompt and tool schemas | `fa4ccc8f2` `request_tokens.rs` request-level estimator; `0bb973e43` carries the split on `ContextUsageEstimate`, adds parts on the no-usage fallback path only; `2200211fe` documents the tray underestimate | `rot2_request_level_estimate_adds_system_prompt_and_tools_d17`, `message_list_estimates_understate_request_utilisation`, `empty_inputs_score_zero`...`total_is_exactly_the_sum_of_its_parts` |
| W9-F2/F4/F5 ledger ids and supersession (v1) | `1754a3e1b` content-derived ids (`C-a1b2c3d4`, sha256 of the dedupe key), correction carriers excluded as supersession targets, newest correction wins the same target; old positional ids still deserialize | `w9_f4_corrections_supersede_the_older_decisions_not_each_other`, `w9_f2_a_late_decision_reusing_a_retired_id_is_not_deleted`, `w9_f2_and_f5_late_constraints_reusing_retired_ids_survive_with_anchors`, `ids_are_content_derived_and_stable_across_merges`, `a_later_correction_wins_the_same_target_and_keeps_the_earlier_correction`, `a_retired_id_suppresses_only_the_exact_same_content`, `old_positional_ids_round_trip_and_merge_without_losing_new_items` (`tests/rot2_ledger_fixes.rs`) |
| harness F1/F4/F5 | `49f473d8d` `continue_recent` session persistence, chars/4 usage anchors, flush-on-append | `probe_harness_behavior.py` + the 13 adapter checks (`research/context_rot_r2/compactbench_adapter/`) |

Production-path verification of the W11 fix: the `w9-a-late3c` rerun regains the
id-collision constraint (4/8 -> 5/8; the remaining three are the known CONSTRAINT_RE
capture gap) and `w9-a-latecorr5c` keeps both corrections current (`1754a3e1b`
commit message). Subagent constraint propagation across compaction is pinned
end-to-end by `tests/rot2_subagent_constraints.rs` (`0c920a95e`):
`rot2_w8_parent_compaction_then_child_task_prompt_carries_constraints`,
`rot2_w8_agent_message_task_crosses_compaction_mid_child`,
`rot2_w8_child_result_after_parent_compaction_resolves_on_compacted_context`.

### 4.2 The typed state v2 module

`crates/pi-coding-agent/src/core/compaction/typed_state.rs` (`c948905a3`, 3 033
lines, 60 unit tests): 14 `TypedInformationKind` values with role-gated
classification (only user-role text yields user constraints / corrections / safety
boundaries / acceptance criteria; tool output saying "must" never does; validated on
a 71-snippet labelled corpus with per-class precision/recall >= 0.9 asserted), a
cap-free ledger with stable content-derived sha256 ids, dedupe plus fuzzy
restatement merge, explicit supersession (later user correction wins, both records
kept), task succession and revival, a priority budget allocator with an
`OverflowReport` and `UnsafeCompaction` when mandatory material cannot fit, a
deterministic verifier of the compact artifact against the ledger, and stable
renders (`render_hot_state`, `render_durable_index`, `render_allocation`).
`build_typed_state` and `merge` are exposed for `prepare_compaction` wiring with a
documented call sequence in the module header. The module is pure and deterministic
(no I/O, no clocks, no randomness) and changes no runtime behaviour: `compaction.rs`
is untouched and `typed_state` is referenced only from `compaction/mod.rs`.

### 4.3 Benchmark infrastructure

1. The multicycle adapter `crates/pi-coding-agent/tests/rot2_multicycle.rs`
   (`e249930d9`): research entry point `rot2_production_multicycle` (`#[ignore]`d,
   driven by `ROT2_SCENARIO`/`ROT2_RESULT`), executing the full pipeline
   in-process; the D16/D17 guards live in the same file. Python orchestration:
   `research/context_rot_r2/orchestrate.py`, `scenario.py` (blake2b seeds),
   `scoring_r2.py` (`scoringVersion r2.1`).
2. The harness binary `crates/pi-coding-agent/src/bin/compaction_harness.rs`
   (`b04d1f040` + `49f473d8d`): offline external-driver access to the real pipeline
   (append, compact, restore) with the F1/F4/F5 fixes.
3. The live driver `research/context_rot_r2/live/` (`fb089ee6e`): a Python mirror
   of the Rust path (`rust_mirror.py`, prompts auto-extracted in `rust_prompts.py`)
   validated byte-for-byte against the stored R2-E1 result
   (`fixture-validation.json`), a one-line `ANSWER:` probe protocol (`live-v1`
   matchers, majority scoring), and the orchestrator `run_r2e2.py` with retries,
   resume and a raw-call log.
4. The incremental driver `research/context_rot_r2/incremental/` (`0229dee3a`,
   `91ac4b2cb`): 16 adapter runs with analysis outputs.
5. The CompactBench adapter `research/context_rot_r2/compactbench_adapter/`
   (`00cbdd3ab`, `1ac57fe3c`): adapter 0.2.0 in delta-append mode inside the
   official runner, the harness-behavior probe, and 13 offline checks.
6. The ContextBench pipeline `research/context_rot_r2/contextbench/`
   (`ab71234bc`..`86e8ad09b`): dataset, metrics, A/B harness (section 3.6).
7. The calibration harness
   `crates/pi-coding-agent/src/core/context_health_calibration.rs` (`6f0d592c6`,
   17 tests) with the committed corpus under `research/context_rot_r2/health/`.

## 5. What a reviewer should check

1. The typed mode is NOT wired at this writing: `git rev-list --count
   021678796..rot2/w15-tsintegration` = 0 (likewise `rot2/w16-supersession` and
   `rot2/w17-e3`); `typed_state` is imported only by `compaction/mod.rs`; and
   `arm_settings` in `tests/rot2_multicycle.rs` maps only `default`/`taskState`.
   The adapter's typed arm comes after W15 lands. Any claim that typed state v2 is
   measured end-to-end would be false today; R2-E3 is not executed.
2. The D6 call site: the validator exists and is tested, but the `agent_session.rs`
   extension call site does not apply it yet (the wiring is the remaining step, per
   `289f3c2e1` and the updated `BUG-LEDGER.md` D6 row).
3. Gate coverage gap: `REGRESSION-GATES.md` records the pass at the `0ec43c8cb` tip
   (23 commits) and the PR draft's gate table at the `4b0add723` tip
   (`rot2/w19-prprep`, `8698128c3`); the last commits on this branch (`1754a3e1b`
   W11, `6f0d592c6`/`0c920a95e` W8, `0e25a7db1`/`157316268`/`021678796` W13) have
   no recorded full gate pass - re-run the focused suites (`rot2_ledger_fixes`,
   `rot2_branch_summary`, `rot2_subagent_constraints`, `--lib
   context_health_calibration`, `--lib compaction`) at the final tip. The two known
   `--lib compaction` failures are the documented environment pair (uv missing on
   the test child's PATH; the scheduling-sensitive painted-frame tray test that
   passes serialized) and predate this campaign.
4. R2-E2 is a Python mirror, not the Rust binary: it was validated byte-for-byte
   against the stored R2-E1 fixture result, but the live run documents seven
   deviations (`live/README.md` "Deviations from the Rust path"): summarizer budget
   8192 vs the formula's 3200, probe-context rendering with flattened orphan tool
   results, a research probe system prompt, prompt variant `v2` (the adapter keeps
   `legacy`), no retry/checkpoint mirroring, deterministic session ids, and UTF-8
   byte-length request sizes. The inversion finding is nevertheless verified against
   the rendered ledger text itself, not only against probe answers. It also measured
   the pre-W11 ledger; `live/raw-calls.jsonl` is the ready regression corpus for
   re-measuring with the W11 fixes.
5. The hard-scenario first run scored 26/37 because probe questions 8-19 aliased
   onto probes 0-7; the fix re-ran the affected probes and produced the final
   111/111 (`live/README.md` "Post-hoc fixes" item 4). The base scenario was
   unaffected.
6. The calibration corpus labels are generated, not observed; the corpus encodes
   the R2-E1 finding by construction. Treat AUC 0.604 as a harness result; replace
   the generator with real outcome labels before citing it as a model of live loss.
7. CompactBench mock scores measure nothing about retention (constant answers);
   only the mock-independent observables (method warnings, compression, per-cycle
   trajectories) separate arms (`SUITE-RESULTS.md` caveats).
8. Anchor-presence scoring overstates current-state correctness (W9-F3): an anchor
   surviving inside a SUPERSEDED render line counts as retained.
9. The open defects that can still damage a real session: D7 (incompatible provider
   checkpoint replays pre-compaction messages), D11 (stale harness digests; fix in
   flight on `rot2/w20-d11`, 0 commits at this writing), D18 (compaction failure
   invisible to the model), D19 (live-list producers without drain/cap), D20
   (summarizer request divergence; the request-level estimator it needed is now on
   this branch).

## 6. Recommendation for defaults

| mechanism | default | decision | reason |
| --- | --- | --- | --- |
| `compaction.taskState` v1 ledger | `off` (unchanged) | KEEP OFF | iteration-1: no recall gain, +913..1 751 tokens/cycle; R2-E2: the ledger inverted a user correction under a live model (89/95 vs default 95/95) and its summarizer calls burn 8k-32k reasoning tokens with latency up to 137.8 s; the W11 fixes target the measured mechanisms but are unproven on the R2-E2 corpus |
| typed state v2 | not wired; opt-in when it lands | OPT-IN, pending | module shipped and unit-tested (`c948905a3`); needs W15 wiring, the adapter's typed arm, and R2-E3 beating control B (14/19 with evidence and subagent retained at comparable or lower token cost, per `EXPERIMENT-LEDGER.md`) before any default change |
| defect fixes D1/D5/D6/D8/D9/D10/D12/D13/D14/D16/D17 + W11 ids + harness F1/F4/F5 | always on | KEEP | correctness fixes with regression guards, not policies; none adds a model call |
| evidence-recovery tool excerpt (iteration-1) | always on | KEEP | 0/4 -> 4/4 at the same budget (iteration-1 E4); unchanged this campaign |
| CONTEXT HEALTH indicator | display only | KEEP, DO NOT GATE | calibration AUC 0.604: the pressure/cycle heuristic does not predict semantic loss, so it must not gate any compaction decision |
| CompactBench adapter + harness binary | opt-in tooling | KEEP | validated end to end (13/13 checks, 1 920 clean cycles); ready for an authorized model-backed run |

## 7. What this report does not claim

- No official scores. Every CompactBench run is CUSTOM-OPTIMUS (mock eval provider,
  public practice suites, custom adapter methods, scorer 2.0.0); every ContextBench
  arm is offline scaffolding without trajectories; RepoQA, SWE-bench and
  Terminal-Bench were not attempted.
- No cross-model claims. The only live-model evidence is GLM 5.3 on one DGX K3
  endpoint, temperature 0, with the documented repetition plan (3 reps on final
  points, 1 on trend points; `live/README.md`).
- No claim that rot was eliminated or that the typed design works end to end.
  H-r2-3 is supported by design and unit tests only; W15/W16 and R2-E3 are in
  flight and no typed-arm number exists.
- R2-E1/E-INC numbers are pipeline mechanics under a deliberately lossy
  deterministic fixture: worst-case bounds on what the pipeline alone protects, not
  predictions about a specific model. The live result (19/19 anchors at every cycle)
  is the counterweight.
- The 89/95 vs 95/95 base-scenario difference is 6 records over 95 with the
  campaign's repetition plan; it is cited as a mechanism finding (the inversion is
  verified in the rendered ledger), not as a distributional effect size.
- The calibration corpus is synthetic (SYNTHETIC-CALIBRATION); its AUC/Brier/ECE
  describe the heuristic against generated labels only.
- Not a measurement of the shipped binary for R2-E2: that experiment is a Python
  mirror of the Rust path (fixture-validated; deviations listed in section 5.4).
- Not a claim that all audited defects are fixed: D7, D11, D18, D19, D20 remain
  open or in flight; D6's call-site wiring and D10's "never retry" sub-item are
  pending; W9-F1 (keyword-less constraint capture) is a v1 design gap that typed
  v2 removes by classifying on role.
- Not reproducible from the drivers alone: item seeds derive from salted blake2b
  digests recorded per row (iteration-1 M5, fixed for r2; `scenario.py`).

## 8. Report index

- `BASELINE.md` - the commit under test, the measurement frame, the category
  labels, the R2-E1 numbers, the inherited iteration-1 context, the TypeCompact
  reproduction baseline (W14).
- `BUG-LEDGER.md` - D1-D20 status on this base, the new findings N1-N3, the W9
  findings, the harness findings HF1-HF6.
- `CODING-TASK-RESULTS.md` - what coding-task evaluation exists (ContextBench
  pipeline, CompactBench custom runs) and the exact blockers for everything else.
- `COMPATION-CORRECTNESS.md` - the D-defect narrative: what is fixed on the branch,
  what sat on worker branches at its writing, what remains open, and the
  fix-to-guard map.
- `EXPERIMENT-LEDGER.md` - every arm and every NOT-EXECUTED item; the E-INC and
  R2-E2 sections live in the integration-head version of the ledger (`eec1a8ec5`),
  whose content is reflected in sections 3.2-3.4 above.
- `FINAL-REPORT.md` - this file, the campaign entry point.
- `MULTICYCLE-DRIFT.md` - the offline multicycle runs consolidated: decay regimes,
  growth model, late material, late supersession, token cost (W14).
- `REGRESSION-GATES.md` - the test-suite state at the `0ec43c8cb` tip, with the two
  documented environment-bound failures (W14; see section 5.3 for the gap to the
  final tip).
- In flight at this writing, not yet in this worktree: the W21 scorecards (the
  R2-E2 live run, the context-health calibration, the CompactBench suite runs; the
  primary artefacts are `research/context_rot_r2/live/`,
  `research/context_rot_r2/health/` and
  `research/context_rot_r2/compactbench_adapter/SUITE-RESULTS.md`) and the W19 PR
  draft (`PR-BODY.md` on `rot2/w19-prprep`, `8698128c3`). If a file named here is
  missing at read time, the experiment ledger and the cited artefacts hold its
  content.
