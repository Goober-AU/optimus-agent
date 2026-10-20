# Context rot iteration 2: compaction defect fixes, production-path benchmarks, typed-state v2 wired as an opt-in mode

Branch: `improve/context-rot-iteration-2` (base `6000d6bc1`, v0.1.36 + #181/#182).
Campaign reports live in `reports/context-rot-r2/`; every number below cites a
committed artefact. This body was finalized at the integration tip
`08f34308b` (175 commits over base) after the D33 and D34 fixes, the W63
landed-tip battery (REGRESSION-GATES section 11), the W72 landed-tip
Python-suite record (section 11.10) and the W65/W70 audit passes landed;
`BUG-LEDGER.md` is the status source of truth (D1-D34 fixed except the
documented deferrals and residues). The integrator adjusts if later
commits land before merge. One practical note up
front: five live-run raw-call logs under `research/context_rot_r2/` are
git-LFS pointers - run `git lfs pull` after checkout or they stay ~130-byte
stubs (details in "What is NOT claimed").

## Summary

Iteration 1 (`reports/context-rot/FINAL-REPORT.md`) measured compaction policies
through Python mirrors of the production prompts. This PR is its follow-up and
does four things:

1. Fixes the audited compaction defects on the production path: D1, D5, D6,
   D8, D9, D10, D11, D12, D13, D14, D16, D17 (estimator; call-site wiring
   recorded as follow-up), D18, D19, D21; pins the D2 continuation contract and
   the D20 wire-request contract with regression tests; repairs the v1
   task-state ledger's id and supersession semantics (W9-F2/F4/F5:
   content-derived ids that survive merges, corrections that retire older
   decisions instead of each other) and refines its supersession
   classification (the recovered W16 work: clause-level correction cues,
   prohibition scrubbing, correction-priority render). Subagent task
   constraints surviving parent compaction are pinned end to end
   (`tests/rot2_subagent_constraints.rs`).
2. Adds production-path benchmark infrastructure: a multicycle adapter that
   drives the real `prepare_compaction -> compact -> append_compaction ->
   SessionManager restore` pipeline (`crates/pi-coding-agent/tests/rot2_multicycle.rs`),
   a `compaction-harness` binary for offline external drivers, a CompactBench
   adapter that runs the real pipeline inside the official runner, an offline
   ContextBench evaluation pipeline, an incremental-variant experiment driver,
   live-model drivers for the R2-E2 multicycle, R2-E3 coding-task and
   R2-E3ADV adversarial-correction experiments, an offline replay (R2-E2-RM)
   that re-measures the ledger fix on the committed live corpus, the typed-arm
   benchmark (R2-E5) and its post-fix re-run (R2-E5-RM), and live-model
   practice runs of both external benchmarks through the production
   `compaction-harness` with a GLM 5.3 summarizer.
3. Wires the typed-state v2 ledger into the compaction flow as an opt-in
   `taskState = "typed"` mode (build/merge/verify in `338b920c3`, settings
   routing in `4be9623b0`, integration suite `40bead288`): the
   provenance-aware replacement for the v1 keyword-regex ledger, with a
   budgeted protected block, a deterministic verifier with one bounded repair,
   and unsafe-allocation refusal before any model call. The default stays
   `"off"`; `"prompt"` (v1) is unchanged.
4. Records the experiment results (below) and what they show: repeated
   compaction is a real information-loss mechanism on the production path
   under a lossy summarizer; the v1 ledger mitigates the kinds it captures but
   is unsafe on corrections (live inversion in R2-E2, argued-down dead weight
   in R2-E3ADV); the W11 id/supersession fix removes the measured inversion
   mechanisms on the live corpus offline (R2-E2-RM); the typed arm measures
   one step over v1 on the fixture (15/19 at the pre-D25 implementation, 13/19
   on this tip - the D25 step boundary correctly retires two step-scoped
   episode-classified probes - against v1's 14/19 and default 0/19) and its
   two live availability defects are fixed and live-confirmed (E5-RM: TASK-E
   5/5 cycles with no halt, E3ADV C1 SUPERSEDED at all five cycles); the
   shipped default (`taskState = "off"`) stays correct pending cross-model
   and longer-session evidence.

## Defect -> fix -> test (evidence table)

Sources: `reports/context-rot-r2/BUG-LEDGER.md`, commit messages, the test
files. Statuses are for this branch's head. D3 and D15 do not exist (the
iteration-1 ledger reserved no such rows); every other D-number from both
campaigns is listed.

| defect | status | fix (commit) | guard test |
| --- | --- | --- | --- |
| D1 (HIGH): threshold stop hook gated on `settings.enabled` alone, so runtime-disabled auto compaction stopped turns past the trigger with nothing compacting | FIXED | `0a896e639` extracts one shared predicate `threshold_auto_compaction_ready` consulted by both the hook and the executor | `d1_toggle_off_over_threshold_does_not_stop_the_turn` (`tests/compaction_turn_flow.rs`) |
| D2 (HIGH): a turn crossing the threshold could stop and never resume | VERIFIED-GUARDED (intact on this base; pinned, no production change) | mid-task resume + queued-prompt paths verified; `b6487b9cd` adds the guards | `d2_interactive_turn_crossing_threshold_compacts_and_continues`, `d2_queued_user_prompt_completes_after_threshold_compaction` (same file) |
| D4 (HIGH): repeated compaction destroys unprotected anchors (the information-loss mechanism itself) | CONFIRMED-MEASURED (not a single-code-fix defect) | R2-E1/E-INC measure it on the production path (default 0/19 after compactions); the opt-in ledger modes are the mitigation, the default stays `"off"` | the R2-E1/E-INC scenario suites (`research/context_rot_r2/runs/`, `incremental/`) |
| D5 (HIGH): compaction ran as a paid no-op when the summarize window held no new material | FIXED | `3b08e3f2e` skips the empty window regardless of `previous_summary` (no model call, no turn stop, no appended entry) | `repeated_trigger_with_no_new_material_resolves_to_no_op`, `new_material_after_the_boundary_still_prepares`, `a_no_op_skip_leaves_the_next_real_compaction_boundaries_intact` (`tests/compaction_summary_path.rs`) |
| D6 (HIGH): extension-supplied summaries bypassed validation; an empty hook summary deleted the prefix it should summarize | FIXED | `289f3c2e` adds `validate_extension_compaction_summary` (the same structural handoff check the model path enforces); `6e1c3a139` applies it at the extension compaction call site | `extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries` (same file) |
| D7 (MED-HIGH): an incompatible/null provider checkpoint made the checkpointed compaction skip as boundary and pre-compaction messages replay (heavy fallback) | VERIFIED-OPEN, fix DEFERRED (documented rationale: restore-semantics risk needs dedicated coverage) | - | `reports/context-rot-r2/BUG-LEDGER.md` D7 row |
| D8 (MED-HIGH): `migrate_v1_to_v2` dropped the compaction boundary when the legacy index was out of bounds or the session header, so every later compaction re-summarized retained history | FIXED | `a56631631` falls back to the first entry after the compaction | `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds`, `migration_ignores_a_boundary_pointing_at_the_session_header` (`src/core/session_manager.rs`) |
| D9 (MED-HIGH): deadline-fallback boilerplate became the next summarizer's `previous_summary` | FIXED | `720428967` selects previous prose through a dedicated walk that skips fallback markers and provider checkpoints | `deadline_fallback_boilerplate_is_not_the_next_previous_summary`, `fallback_only_history_yields_no_previous_summary` (`tests/compaction_summary_path.rs`) |
| D10 (MED): branch summaries were built with every tool result removed, so the evidence needed to continue a branch never reached the summarizer | FIXED ("never retry" sub-item open) | `0e25a7db1` pairs tool results with their assistant calls in the branch-summary input under the same evidence-aware excerpt policy as the main path, and the budget walk keeps the call of a kept result | `branch_summary_input_carries_the_failing_tool_result`, `branch_summary_input_skips_tool_results_with_no_text` (`tests/rot2_branch_summary.rs`) |
| D11 (MED): only the newest harness digest was compared and superseded digest turns were never pruned, so k harness changes left k digest user turns (k-1 stale) in the provider context | FIXED | `30588707d` prunes superseded digest customs from the live provider context on every digest turn (the durable transcript keeps the full history) | `k_harness_changes_leave_exactly_one_digest_in_the_provider_context`, `compaction_keeps_the_pruned_provider_context_digest_clean` (`tests/rot2_harness_digest.rs`, landed with `d9ceea230`) |
| D12 (MED): memory-recall text reached the provider as unfenced, instruction-shaped user turns (stored project data could read as fresh user direction) | PARTIALLY FIXED (soft-fence residual documented) | `021678796` fences `MEMORY_RECALL_TYPE` custom text as `<memory_data>` at `convert_to_llm` time, mirroring the harness-digest fence | `convert_to_llm_fences_memory_recall_text_but_not_other_custom_types` (`src/core/messages.rs`) |
| D13 (MED): fail-closed recall paths were silent (foreign-only corpus, short-skip space, stale-source) | FIXED | `157316268` surfaces one rate-limited warning with drop counts when hits existed but nothing rendered; partial skips stay silent | `foreign_only_corpus_fails_closed_and_surfaces_the_reason`, `budget_short_skip_fails_closed_and_surfaces_the_reason`, `partial_recall_skips_stay_silent` (`src/core/memory/search.rs`) |
| D14 (MED): the read-files inventory in compaction details was always empty | FIXED | `439808412` records reads structurally from any tool call with `path`/`paths` arguments (`edit` stays the one name-based write rule) | `non_edit_path_tool_calls_populate_the_read_inventory`, `compact_persists_a_non_empty_read_files_inventory` + `path_arguments_of_non_edit_tool_calls_record_reads` (`src/core/compaction/utils.rs`) |
| D16 (MED): user-attached images cost zero tokens in `estimate_tokens` while `find_cut_point` charged 4096 chars | FIXED | `fa4ccc8f2`/`0bb973e43` introduce the shared `IMAGE_BLOCK_CHARS` (4096 chars = 1024 tokens) across all estimator paths; `4dea91d72` aligns the kernel-parity reference | `rot2_user_image_blocks_cost_tokens_d16` (`tests/rot2_multicycle.rs`), `image_blocks_cost_4096_chars_1024_tokens` (`request_tokens.rs`), `compaction_kernel_parity` 23/23 |
| D17 (LOW-MED): the context estimate ignored the system prompt and tool schemas | FIXED (estimator; production call-site wiring recorded as follow-up) | `fa4ccc8f2` adds `request_tokens.rs` (`estimate_request_tokens`, `RequestTokenEstimate`); `0bb973e43` carries the split on `ContextUsageEstimate` and adds the parts on the no-usage fallback path only; `2200211fe` documents the tray's inherited underestimate | `rot2_request_level_estimate_adds_system_prompt_and_tools_d17` (`tests/rot2_multicycle.rs`), `message_list_estimates_understate_request_utilisation` (`src/core/context_health.rs`), `empty_inputs_score_zero`...`total_is_exactly_the_sum_of_its_parts` (`request_tokens.rs`) |
| D18 (LOW): a failed automatic compaction was invisible to the model | FIXED | `8c558dd7b`: one bounded model-visible live notice (a 240-character failure excerpt) replaces its predecessor on repeated failures; skipped and cancelled attempts stay silent, a successful compaction drops the notice | `d18_failure_notice_is_model_visible_and_bounded` (`agent_session.rs`), `d18_failed_auto_compaction_surfaces_a_bounded_notice_to_the_model`, `d18_successful_compaction_does_not_surface_a_failure_notice` (`tests/rot2_defect_wave2.rs`) |
| D19 (MED): both live-disclosure producers (deferred next-turn messages, unpersisted outcomes after a persistence failure) grew without bound | FIXED | `4769f42f4`: both lists capped at 200 entries, the evicted count folded into a single dropped marker kept at the front | `d19_live_disclosure_lists_are_bounded_with_a_dropped_marker` (`agent_session.rs`) |
| D20 (MED): no contract held the summarizer wire request equal to the model-visible conversation | PINNED, residue VERIFIED-OPEN (documented) | `e44faff08`: byte-exact pin - captured wire chunks equal `serialize_conversation` over `convert_to_llm` of the same preparation. Residue: the text form cannot carry image blocks; native-compaction providers receive the `compaction_provider_context` rebuild | `d20_summarizer_request_equals_the_serialized_model_visible_conversation` (`tests/rot2_defect_wave2.rs`) |
| D21 (MED): with the churn fixed, `CONSTRAINTS_CAP=12` omitted the four corrections from the rendered ACTIVE list on the hard scenario (strict containment 11/20 pre-fix) | FIXED | `a9035ce8a` (W32): correction carriers are exempt from the section caps and priority-ordered ahead of plain items | the seven `rot2_render_cap` cases (`tests/rot2_render_cap.rs`); offline replay of the hard corpus: strict containment 20/20 (`0b824f358`, `live/remeasure/d21_render.py`) |
| W9-F2/F4/F5 (v1 ledger ids and supersession): ids renumbered per merge so a retired id could be re-issued and silently deleted; corrections superseded each other while the corrected decisions stayed current | FIXED | `1754a3e1b` content-derived ids (`C-a1b2c3d4`, sha256 of the dedupe key), correction carriers excluded as supersession targets, newest correction wins the same target; old positional ids still deserialize | `w9_f4_corrections_supersede_the_older_decisions_not_each_other`, `w9_f2_a_late_decision_reusing_a_retired_id_is_not_deleted`, `w9_f2_and_f5_late_constraints_reusing_retired_ids_survive_with_anchors`, plus id-stability, correction-precedence and old-id interop cases (7 total, `tests/rot2_ledger_fixes.rs`) |
| W9-F1/F3/F4 (v1 supersession misclassification): the v1 ledger fired SUPERSEDE_RE on any user text carrying drop/instead/replace/supersede vocabulary, so standing prohibitions ("never drop the scheduler ledger") entered the corrections list and 15/20 ordinary constraint lines churned into spurious supersession pairs (W9-F3); keyword-less clauses survived only by riding the same message-wide trigger (W9-F1); and two corrections sharing frame tokens could retire each other while the decision they corrected stayed current (W9-F4, the W7 live-inversion family) | FIXED | the recovered W16 work, integrated by W51: `a112034dc` classifies corrections per clause after prohibition scrubbing (explicit displacement cues; negated cue verbs scrubbed first; SUPERSEDE_RE demoted to a loose capture-widening trigger), `611d2312c` anchors pairing on non-scaffold subject tokens under the W11 content-id ordinal gate (only older, non-correction targets; frame words never anchor), `3de7ef222` renders the retired item's old text behind a replaced-by link (corrections render current and never land in SUPERSEDED, cap-exempt with the D21 omitted-count accounting) | `rot2_supersession` 4/4 (the W7 base across 5 cycles, the W9-F4 late correction at cycle 5, the W9-F3 hard-corpus zero churn, build_ledger idempotence), lib `task_state` 21/21 (16 in-module), `rot2_ledger_fixes` 7/7, `rot2_render_cap` 7/7 |
| D22 (MED): supersession rendered with the retirement direction inverted (the retired record shown as superseding its replacer) | FIXED (W38) | `51c9a6a59` | `rot2_typed_adversarial`: `superseded_corrections_and_tasks_render_the_retirement_direction` |
| D23 (MED): bracketed ticket tokens in ordinary user text collided with the verifier's reference grammar, failing the episode permanently | FIXED (W37) | `891702c32` | `bracketed_ticket_ids_in_user_text_verify_cleanly`, `verify_treats_bracketed_user_tokens_as_prose`, `repair_is_bounded_to_one_attempt_with_no_second_model_call`, `repair_that_still_fails_verification_fails_the_episode` (`rot2_typed_adversarial`) |
| D24 (MED): tool-derived record ids recomputed from evolving text, stranding every reference earlier summaries carried | FIXED (W37) | `891702c32` (identity assigned at first sight, kept across merges) | `evolving_tool_state_records_keep_a_stable_id_across_merges`, `three_cycle_chain_with_ticket_text_and_evolving_tool_state_verifies_every_cycle` |
| D25 (MED-HIGH): step-scoped pending actions stayed CURRENT forever, growing ledger and summaries until mandatory material exceeded the hot-state budget and compaction failed permanently | FIXED (W36) | `a3997b5d6` (merge is the step boundary; retired ids tombstoned into the bounded `retiredRefs`, resolved by `verify`; every merged ledger hard-capped at 512 records) + `c5136ce50` (brace-damage repair) | `pending_actions_do_not_accumulate_forever_across_merges`, `step_scoped_records_stay_bounded_across_twelve_merges`, `retired_step_records_keep_summary_references_resolvable`, plus eight in-module pins |
| D26 (MED): a malformed `details.typedState` payload silently dropped every durable record below the boundary | FIXED (W38) | `51c9a6a59` (field-wise salvage with a persisted warning) | `malformed_typed_state_payload_does_not_silently_drop_durable_records` (un-ignored, green) |
| D27 (LOW): all-caps fabricated "verified" claims evaded the fabrication check | FIXED (W38) | `51c9a6a59` (`is_heading_line` admits structural shapes only) | `all_caps_verified_claims_cannot_evade_the_fabricated_check` |
| D28 (LOW-MED): native provider compaction returned `typed_state: None`, resetting the durable chain | FIXED (W39) | `1e6e5ff9d` (the native arm persists the merged ledger unchanged, fallback semantics) | `tests/rot2_native_typed_chain.rs` (2 tests, the dual-mode fixture) |
| D29 (MED-HIGH): supersession pairing was token-overlap-only: a zero-overlap correction retired nothing (the adversarial no-anchor case this campaign set out to fix) | FIXED, live-verified (W41) | `4315f9154` + `e71c21563` (token path: non-correction kinds outrank corrections as targets; provenance path: the most recent prior CURRENT decision retires when the correction is the only un-retired correction since it) | `zero_overlap_correction_retires_the_stale_decision_by_provenance`, `sibling_corrections_retire_their_respective_decisions_without_mutual_retirement`, `unrelated_distant_correction_retires_nothing`; live C1 cell `621dc462f` and E5-RM (below) |
| D30 (MED): a decision stated in plain USER text classified as a step-scoped LowPriorityEpisode and retired at the first merge, while the same sentence from the assistant classified as VerifiedDecision | FIXED (W46) | `10e2b36e6` (a user unit matching DECISION_RE classifies as VerifiedDecision with `verified` provenance; the assistant path remains the corroboration path) + `d00ff2067` (the W41 cycle-5 budget pin) | `user_stated_decision_is_a_verified_decision_with_task_scope`, `user_stated_decision_survives_the_step_boundary_merge`, `user_correction_retires_a_user_stated_decision`, `tight_budget_drops_superseded_history_but_keeps_the_pairing_line` (lib), the smoke seed `7bec70f09` (probe q4) |
| D31 (MED): the D25 retired-reference tombstones polluted the verifier's fabricated-claim check - a "verified" line citing only a retired record id passed while nothing vouched for the text | FIXED (W52) | `6041f71c9` (W52; ledger row `e6e909e16`): the resolver used by the fabricated check builds from `state.records` only (carried record and entry ids), and its non-sigil branch accepts record ids alone - the renderer grammar emits entry references only with the `@` sigil (D23), so a bare `[entry]` token is prose, not a reference; the unresolved-refs check keeps the widened sets (records + `retired_refs`), so references earlier summaries still quote stay resolvable | `retired_ids_do_not_vouch_for_verified_claims` (`tests/rot2_wave_adversarial.rs`, un-ignored); the W35 pin `retired_step_records_keep_summary_references_resolvable` stays green |
| D32 (LOW-MED): the D28 native compaction arm carried the salvaged records through but dropped the D26 diagnostics - a native compaction observing a malformed prior ledger left no trace of the salvage | FIXED (W53) | `53ab2e3d9` (the native arm returns a minimal `typed_verification` carrying the `malformedPriorLedger` flag) | `native_compaction_reports_the_salvage_of_a_malformed_prior_ledger` (`tests/rot2_wave_adversarial.rs`, un-ignored); the D28 carry-through pin (`rot2_native_typed_chain` 2/2) stays green |
| D33 (LOW-MED): a restatement-absorbed tool record stranded references - the absorbed record's id and entry id gained no `retiredRefs` tombstone, so a later summary citing them failed unresolved-ref resolution (observed at cycle 2 of the E5-RM TASK-E offline chain) | FIXED (W54) | `cea3a4496`: the absorption site in `reconcile` retires the absorbed record's id and source entry into `retiredRefs` (the D25 step-boundary treatment); `merge` unions the absorbed tombstones with the step-boundary ones instead of dropping them | `absorption_then_citation_resolves` (`tests/rot2_wave_adversarial.rs`, landed with the fix in `6d85cc48f`: the E5-RM chain shape with the field-exact ids, red on the pre-fix tree, green with the fix) - the suite carries ten tests, zero `#[ignore]`d, at this tip (the W63 battery recorded the pre-pin nine green, REGRESSION-GATES section 11.2; the final-tip matrix re-ran the ten green, section 12); the D24 stable-id and D25 tombstone pins stay green |
| D34 (MED): the typed-arm smoke (`rot2_multicycle::rot2_smoke_typed_arm_persists_typed_ledger_and_verifies`) failed deterministically at the W63 landed-tip battery with "1 unbacked verified claims" - confirmed mechanism (W71, correcting the section 11.7 suspicion): the assistant seed line "Section 3 verified; publishing next." classifies as a Step-scoped PendingAction and renders into the cycle-0 summary, the cycle-1 merge retires it at the step boundary (D25), the fixture's previous-summary decay still quotes the rendered line, and the D31 strict-vouching fix correctly refuses the retired id, so the unbacked-claim check - which had no repair path (missing items get appended back; unbacked claims hard-failed the episode) - refused the compaction with the conversation preserved; the D30 seed was NOT the retiree (it classifies as VERIFIED_DECISION with Task scope and survives every merge) | FIXED (W71) | `6894f6a6d` (the bounded repair, the fix brief's option b): `annotate_unbacked_verified_claims` plus the `UNVERIFIED_CLAIM_MARKER` suffix land in typed_state, `verify` exempts a line already carrying the marker (annotating a claim as unverified is the honest presentation, so the marker cannot smuggle a claim), and the deterministic repair gate widens to fabricated-only findings - the unbacked lines are downgraded in place (content kept) and the amended text is re-verified with `repaired=true`, matching the missing-item repair contract; a retired-id-citing line still fails `verify` itself (D31 intact), only the repair path accepts the downgrade | `unbacked_verified_claim_from_a_retired_record_repairs_to_unverified` and `annotate_unbacked_verified_claims_leaves_backed_lines_alone` (lib, `typed_state`; the first is end-to-end D25 retire -> D31 refuse -> D34 downgrade -> verify), the smoke itself now asserting the repaired cycle exists and its re-verification is clean (`rot2_multicycle`), and the fail-closed pin `repair_that_still_fails_verification_fails_the_episode` moved to a still-unrecoverable shape - an unresolvable `[VD-0badca]` reference blocks the repair (`rot2_typed_adversarial`) |
| harness F1/F4/F5: the harness binary forked a session per invocation, read 0 tokens, lost appends on error paths | FIXED | `49f473d8d` (`continue_recent` session persistence, chars/4 usage anchors, flush-on-append) | `probe_harness_behavior.py` + the 13 adapter checks (`research/context_rot_r2/compactbench_adapter/`) |

Open after the final wave, all documented in `BUG-LEDGER.md`: D7 (deferred
with rationale), D10's "never retry" sub-item, D12's soft-fence residual,
D17's production call-site wiring (follow-up) and D20's documented residue.
D33 (restatement-absorbed ids stranding references) is fixed on this tip
(`cea3a4496`, table above), and D34 (the typed-arm smoke red at the W63
battery) is fixed by W71 (`6894f6a6d`, table above).
N1/N2 (the v1 evidence-regex and custom-entry gaps R2-E1 measured) are the
design constraints typed v2 exists to remove (the W9-F1 clause-capture gap
is fixed by the W51 refinement, table above).

## The typed-state v2 mode (opt-in, wired)

The module landed first as a pure library (`c948905a3`); it is now wired into
the production compaction flow:

- Mode resolution: `compaction.taskState` accepts `"typed"` alongside
  `"off"`/`"prompt"` through `resolve_task_state_mode` (setting or the
  `PRIME_AGENT_COMPACTION_TASK_STATE` env; unrecognized values fail safe to
  `"off"`; the default stays `"off"`) - `338b920c3` - and the settings path
  routes through the same resolver so a configured `typed` reaches the kernel
  - `4be9623b0`. `"prompt"` keeps the v1 keyword ledger unchanged.
- Build and merge: `prepare_compaction` builds via `build_typed_state` over
  the leaving window and merges the result with the previous compaction's
  persisted ledger (deserialized from `details.typedState`, a new field that
  never overloads the v1 `taskState` payload), carried on `CompactionPreparation`.
- Budget: the protected block is the budgeted allocation (`render_allocation`
  of `allocate`) with the hot-state budget derived from the summary output
  budget: `min(2048, floor(summary_budget/4))` tokens
  (`typed_hot_state_budget_tokens`), so the block always fits alongside the
  summary the same request produces. An unsafe allocation (mandatory material
  dropped) fails the episode before any model call through the existing `Err`
  channel, with the overflow detail (ids, kinds, budget) in the error text.
- Verifier: after the summary passes structural `validate_summary`,
  `verify(summary, ledger)` gates acceptance. A failure that is only missing
  required items gets one bounded, deterministic repair (missing protected
  lines appended verbatim to their matching summary sections, no model call)
  and is re-verified; any other failure, or a still-failing repair, is a
  summary-validation failure. The outcome persists as
  `details.typedVerification` (passed/repaired with finding counts).
- Parity: native provider compaction carries the merged ledger through
  unchanged (`1e6e5ff9d`, D28) and reports a malformed prior ledger it
  salvaged (`53ab2e3d9`, D32); the deadline fallback keeps v1 behavior (no
  typed verification) but still persists the ledger so the next cycle merges it.
- Guards: `tests/rot2_typed_integration.rs` (`40bead288`, nine tests) drives
  the real pipeline over real `SessionManager` JSONL persistence: mode
  resolution and fail-safe, the budget formula, `prepare_compaction`
  classifying cargo-test lines as `VERIFIED_TOOL_RESULT` and subagent tool
  reports as `SUBAGENT_STATE`, the protected block and its copy instruction in
  the captured summarizer request, the `details.typedState` round-trip merging
  by record id, the unsafe-allocation refusal, the deterministic repair, a
  superseded-decision-presented-as-current summary failing verification, and
  v1 interop (old `details.taskState` entries deserialize; `"prompt"` mode
  works unchanged end to end).
- Known boundary (deliberate, pinned by the suite): custom `agent_message`
  entries are not classified (`entry_role_text` reads only `Message` entries),
  so a child-agent report is not retained as `SUBAGENT_STATE` by the committed
  module.

### Adversarial red-team wave 1 (W35): eight defects found, all fixed

After the green integration, a dedicated adversarial self-review of the typed
mode found eight defects that the positive tests missed. Each was reproduced
by a failing-first test committed in
`crates/pi-coding-agent/tests/rot2_typed_adversarial.rs` (written red under
`#[ignore = "Dnn: ..."]`, then un-ignored by its fix; 23 tests green, zero
ignored, at the W49 battery):

| # | one-line finding | fix |
| --- | --- | --- |
| D22 (MED) | supersession rendered with the retirement direction inverted (the retired record shown as superseding its replacer) | `51c9a6a59` (W38) |
| D23 (MED) | bracketed ticket tokens in ordinary user text collided with the verifier's reference grammar, failing the episode permanently | `891702c32` (W37) |
| D24 (MED) | tool-derived record ids recomputed from evolving text, stranding every reference earlier summaries carried | `891702c32` (W37) |
| D25 (MED-HIGH) | step-scoped pending actions stayed CURRENT forever, growing ledger and summaries until mandatory material exceeded the hot-state budget and compaction failed permanently | `a3997b5d6` (W36) |
| D26 (MED) | a malformed `details.typedState` payload silently dropped every durable record below the boundary (now salvaged field-wise with a persisted warning) | `51c9a6a59` (W38) |
| D27 (LOW) | all-caps fabricated "verified" claims evaded the fabrication check | `51c9a6a59` (W38) |
| D28 (LOW-MED) | native provider compaction returned `typed_state: None`, resetting the durable chain | `1e6e5ff9d` (W39; pinned in `tests/rot2_native_typed_chain.rs`) |
| D29 (MED-HIGH) | supersession pairing was token-overlap-only: a zero-overlap correction retired nothing (the adversarial no-anchor case this campaign set out to fix) | `4315f9154` (W41), live-verified: E3ADV C1 5/5 cycles retired with the pairing link rendered |

### Adversarial red-team wave 2 (W48): the fix code itself under attack

A second adversarial review attacked the D21-D29 fix code
(`crates/pi-coding-agent/tests/rot2_wave_adversarial.rs`, one test per
surface). Six surfaces pinned green - the fixes hold under attack:

- the 512-record cap invariant: a required record drops only after every
  superseded record dropped, and the render stays honest about it;
- the D24+D29 combination: evolving tool state with a stable id across three
  merges plus provenance-paired retirement, with no resurrection and no
  double retirement;
- additive schema evolution: unknown record/ledger fields are tolerated by
  the strict parse and do not widen the D26 salvage;
- interleaved D1 C1 D2 C2 pairing: positional, no crossing;
- the token-tier rule: a correction sharing more tokens with a sibling
  correction still retires the qualifying decision (W9-F4 stays dead);
- the D22 chain render: A names its direct superseder B, not the current C.

Two surfaces failed red and became defects D31 (retired ids vouching for
fabricated "verified" claims) and D32 (the native arm dropping the D26
salvage diagnostics); both are fixed (W52/W53, table above), their red tests
un-ignored and green. A third defect, D33 (restatement-absorbed record ids
stranding references), was found by the E5-RM offline re-measure (W47) and is
fixed on this tip (`cea3a4496`, its guard landed with it, table above). Every
adversarial test in both suites is green with zero `#[ignore]d defects at
this tip (the D33 pin's green is its landing commit's record - the W63
battery predates it - and the final-tip matrix re-ran the suite green, REGRESSION-GATES section 12). The
one red at the landed-tip battery sat outside both suites: the
`rot2_multicycle` typed-arm smoke, filed as D34 and fixed by W71
(`6894f6a6d`, table above; the pending final matrix re-runs it).

## Experiment results

Category labels (defined in `reports/context-rot-r2/BASELINE.md`, never mixed
within one table):

- SYNTHETIC-DIAGNOSTIC - deterministic fixture, no model calls; measures
  pipeline mechanics, not model quality.
- PRODUCTION-PATH - produced by the real Rust compaction pipeline in this
  repo, not a Python mirror.
- PRODUCTION-PATH-MIRROR - a Python port of the current Rust ledger replayed
  over a committed live corpus; validated byte-for-byte against the stored
  fixture before use; no model calls.
- CUSTOM-OPTIMUS - official external benchmark infrastructure with custom
  Optimus adapters/settings (mock provider, practice suites, custom scorer);
  not comparable to official scores.
- LIVE-MODEL - measured with a real model endpoint (GLM 5.3, one DGX K3
  endpoint, temperature 0) as summarizer and probe answerer; single-model
  evidence, not a cross-model claim.

### R2-E1 - multicycle retention, default vs taskState (SYNTHETIC-DIAGNOSTIC, PRODUCTION-PATH)

19 probes (8 constraints, 4 decisions, 4 evidence, 2 corrections, 1 subagent
artifact), 5 compaction cycles, deterministic lossy summarizer, zero model
calls (`research/context_rot_r2/runs/r2-e1-multicycle.*`).

| arm | retention | constraints | decisions | evidence | supersession | subagent | mean req chars |
| --- | --- | --- | --- | --- | --- | --- | --- |
| default (`taskState = "off"`) | 0/19 | 0/8 | 0/4 | 0/4 | 0/2 | 0/1 | 14 765 |
| taskState (`taskState = "prompt"`) | 14/19 | 8/8 | 4/4 | 0/4 | 2/2 | 0/1 | 18 429 |

What this shows: five repeated compactions with an even mildly lossy summarizer
lose every unprotected anchor (D4 confirmed on the production path); the v1
ledger fully protects the kinds it captures (14/14) at about +916 tokens per
cycle, but is blind to tool-result evidence (regex gap N1) and subagent custom
entries (N2). The 8-cycle, 2-seed extension (E-INC, `research/context_rot_r2/
incremental/`) confirms: default decays to 0/19 after two compactions and stays
there; taskState holds 14/19 flat through 8 cycles; runs are deterministic and
path-independent; the ledger costs +995 tokens/cycle steady state.

### R2-E2 - live-model multicycle (LIVE-MODEL, GLM 5.3 only)

Same pipeline mirrored in Python and validated byte-for-byte against the stored
R2-E1 fixture, then run with the real GLM 5.3 endpoint as summarizer AND probe
answerer (`research/context_rot_r2/live/`, 581 wire calls, zero probe
extraction failures).

| scenario | arm | probe records | correct |
| --- | --- | --- | --- |
| base (19 probes) | default | 95 | 95 (100%) |
| base | taskState | 95 | 89 (93.7%) |
| hard (37 probes) | default | 111 | 111 (100%) |
| hard | taskState | 111 | 111 (100%) |

Headline finding: in the base scenario the v1 ledger INVERTS the user's
correction. The user says "forget that; use architecture B ... the earlier A
decision is superseded"; the ledger's token-overlap supersession rule pairs the
two corrections with each other, renders them under SUPERSEDED (history) while
the pre-correction "architecture A" decision stays current, and the summarizer
faithfully propagates "use architecture A; the B correction is superseded" from
cycle 3 onward. The default arm handles the same correction correctly at every
cycle. The hard scenario does not reproduce the inversion only because the same
defective rule mis-fires as ambiguity-plus-omission there, which the model
recovers from: correctness under the v1 ledger is luck of how the regex pairing
lands, not a property the ledger guarantees. R2-E2 measured the pre-W11 ledger;
the W11 fix is re-measured offline on this corpus below (R2-E2-RM).

### R2-E3 - coding-task action probes (LIVE-MODEL, GLM 5.3 only)

Five coding scenarios x 2 arms x 5 compaction cycles with action-level probes
(next file to edit under a constraint, corrected approach, boundary-adjacent
exact error, acceptable-set exclusion, post-restart approach), 3 reps per
point, majority scoring, one simulated process restart after cycle 3
(`research/context_rot_r2/live/e3/`; 209 wire calls = 155 probes + 53
summarizer records, 3 of them length-ladder retries, + 1 pong).

| arm | cycle points | correct | strict | extraction failures |
| --- | --- | --- | --- | --- |
| default | 50 | 50 (100%) | 50 | 0 |
| taskState | 50 | 50 (100%) | 50 | 0 |

Anchor retention 1/1 at every cycle on both arms (including the exact
failing-test assertion); the TASK-F serialize -> reload round-trip left the
restored provider context byte-identical on both arms. What this shows: no
measurable action-level rot at this scale with the real summarizer, on either
arm. Two mechanisms behind the taskState arm's clean sheet: the corrections in
these scenarios happen to pair correctly under the token-overlap rule (pairing
luck, not design - the adversarial variant below breaks it), and the exact
error survived through the summarizer's verbatim-anchor discipline while the
ledger's VERIFIED EVIDENCE section never captured it (the evidence regex takes
the first matching line of a tool result; the assertion below it is invisible).

### R2-E3ADV - adversarial corrections with no shared anchor (LIVE-MODEL, GLM 5.3 only)

Three adversarial correction families (semantic swap, numeric correction,
retraction-only) where the correction shares no anchor token with the decision
it replaces (supersession score 0-2 < the pairing threshold 3); predictions
pre-registered before any live call, and the offline selftest proves the
zero-pairing property against the real mirror ledger (superseded == [] at
every cycle). 2 arms x 3 families x 5 cycles, 3 reps
(`research/context_rot_r2/live/e3adv/`; 124 wire calls, 0 HTTP retries, all
summarizers within the 8192 budget).

| arm | cycle points | correct | strict | extraction failures |
| --- | --- | --- | --- | --- |
| default | 15 | 15 (100%) | 15 | 0 |
| taskState | 15 | 15 (100%) | 15 | 0 |

The mechanism, verified in `raw-calls.jsonl`: the ledger retired NOTHING. The
stale decision stayed DECISIONS-current at every cycle while the render header
promised "A SUPERSEDED item is history" with no superseded items to show. GLM
5.3 resolved the contradiction in all 15 taskState summarizer outputs by
annotating the stale item as history in its own words ("Treat as history
only", "superseded in practice"). Correctness is the summarizer's achievement
AGAINST the ledger, not a property of it, and the dead weight costs +1.5k-1.9k
request chars per cycle (mean summarizer request 16.8k-17.0k chars vs
15.1k-15.3k default). Combined with R2-E2: the v1 ledger on corrections is
either a coin flip that can invert user intent (shared-anchor mispairing) or
dead weight the model must argue down (no shared anchor). In neither case does
it do the correction's work.

### R2-E2-RM - offline re-measure of the W11 fix on the live corpus (PRODUCTION-PATH-MIRROR)

A Python port of the post-W11 Rust ledger (`w11_port.py`, commit `1754a3e1b`)
replayed over the committed R2-E2 raw-call corpus, no model calls
(`research/context_rot_r2/live/remeasure/`). Gate: the pre-W11 mirror replay
byte-matches every corpus summarizer request and reproduces every cycle field
of the committed results.

- base (the live-inversion scenario): clean at every cycle. Pre-W11 both
  corrections were inverted at every cycle (mutual supersession pair); the
  live failure reproduced exactly by the pre-W11 replay. Post-W11: corrections
  ACTIVE and rendered (12/12 within cap), the superseded architecture-A
  decisions retired to SUPERSEDED with the correction as replacement, the
  constraint churn gone.
- hard: structurally fixed (mutual pair gone, all four A decisions retired,
  17 spurious pairs -> 4 correct pairs) but a new render-level defect appears:
  26 constraints stay active and `CONSTRAINTS_CAP=12` omits the four
  corrections from the rendered ACTIVE list (strict containment 11/20). Filed
  as D21 in the table above, fixed by `a9035ce8a` (post-fix replay: 20/20).
- secondary (W11-independent): `EVIDENCE_RE` never matched this corpus's
  evidence shapes, so VERIFIED EVIDENCE was empty in every render pre and
  post; the live evidence correctness came from the summary chain.

### TypeCompact reproduction (SYNTHETIC-DIAGNOSTIC, external reference)

Deterministic offline reproduction of the CIKM 26 Knowledge Triage
multiturn-stability experiment on their pinned artifact suite
(`research/context_rot_r2/repro/typecompact_repro*.json`): constraint recall
1.000 -> 0.841 by cycle 5 (0.848 on the 197-config full suite) while the
truncation baselines cliff to 0.007-0.028 - external confirmation that pinning
must-keep material outside the summarizer survives repeated compaction. Their
committed LLM-arm numbers are carried as labelled reference-only blocks, never
claimed as ours.

### CompactBench practice suites (CUSTOM-OPTIMUS, PRODUCTION-PATH pipeline)

Full public practice suites (starter: 4 templates x 5 cases; elite_practice:
20 x 5) with 3 drift cycles on nine arms through the committed adapter and
harness binary (`research/context_rot_r2/compactbench_adapter/SUITE-RESULTS.md`).
All quality scores are degenerate by construction (mock eval provider returns a
constant; every arm scores identically); these runs validate plumbing only.
The mock-independent observables separate the arms: zero method warnings
across all 1 920 custom-arm cycles (every `compact()` landed a valid cut),
task-state artifacts are the largest (compression 0.48x starter / 0.74x elite,
plain 0.55x / 0.90x, oracle 0.78x / 0.79x, echo 0.83x / 1.12x, built-ins
collapse to the null floor), and per-cycle token trajectories confirm
delta-append mode engaged.

### CompactBench live practice run (CUSTOM-OPTIMUS + LIVE-MODEL)

A model-backed practice run on the public starter suite (8 of 20 cases per
arm, one live draw per case): GLM 5.3 as BOTH the summarizer - behind the
Rust `compaction-harness` binary on the production path, via a new
env-configured HTTP provider that is offline by default (`6ea85dc10`) - and
the task/eval model behind the stock runner
(`research/context_rot_r2/compactbench_adapter/live/`; 676 HTTP requests
against a 768 ceiling, one fixed-and-resumed provider incident). NOT a
leaderboard number and not comparable to any 1.0.0-scored run. The live run
does what the mock could not - the arms' quality separates:

| arm | LIVE overall | drift_res | constraint_ret | compression |
| --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.6844 | 0.990 | 0.800 | 0.67x |
| echo-first-n | 0.6531 | 0.833 | 0.800 | 1.60x |
| full-transcript-oracle | 0.5781 | 0.721 | 0.650 | 0.92x |
| optimus-rust-plain | 0.4906 | 0.757 | 0.650 | 0.72x |

The two Rust arms differ by 0.194 overall, driven by the ledger-mediated
checks (`recall_latest_decision` 0.625 vs 0.125, assign checks 0.375 vs
0.125, drift resistance 0.99 vs 0.76, contradiction rate 0.094 vs 0.172).
GLM writes tighter summaries than the lossy fixture (task-state compression
0.46x mock -> 0.67x live on identical cycle-0 transcripts), and the verbatim
oracle is not the ceiling under live eval (0.578). Single-draw runs are not
bit-reproducible even at temperature 0, so with n = 8 per arm only the
Rust-arm gap is treated as signal; the pre-registered extension amendment did
not trigger.

### ContextBench offline pipeline (CUSTOM-OPTIMUS)

The committed pipeline (`research/context_rot_r2/contextbench/`): parquet
loading with sha256 verification of the 500-instance verified subset of the
pinned upstream commit, a repo-level dev/holdout split (308/192 instances, no
repo overlap, byte-reproducible manifest), the upstream gold-context metric
layer mirrored function-by-function with citations, and a matched A/B harness
with exact sign test and tie-corrected Wilcoxon. Arms A/B are scaffolded for
recorded trajectories; arms C (typed state) and D (source-grounded
reconstruction) emit blocked records only.

### ContextBench live practice run (CUSTOM-OPTIMUS + LIVE-MODEL)

26 paired dev instances (a deterministic blake2b-seeded slice of the 110
size-window dev candidates; 7 languages, 15 repos; the holdout untouched),
2 arms x 4 append+compact cycles through the real `compaction-harness`
binary with a live GLM 5.3 summarizer, evaluated by the offline-validated
mirror (`research/context_rot_r2/contextbench/live/`). Arms: A = baseline,
B = task-state-ledger (`prompt`; a documented substitution - the offline
arm B, jev-relevance, is not expressible through the compaction surface).
No repository checkouts exist under the offline waiver, so the trajectories
are synthetic explorations built from the gold annotations and the metrics
measure RETENTION of gold context through compaction, not free-form agent
exploration; `patch_success` stays UNAVAILABLE in every record.

| metric | A baseline | B task-state-ledger | delta B-A | sign p | Wilcoxon p |
| --- | --- | --- | --- | --- | --- |
| final file coverage | 0.974 | 0.974 | +0.000 | 1.000 | 1.000 |
| final line coverage | 0.852 | 0.916 | +0.069 | 0.455 | 0.196 |
| final line precision | 0.320 | 0.586 | +0.097 | 0.004 | 0.003 |

Gold file references survive compaction almost fully in both arms (file
coverage 0.974); gold LINE retention separates them (line precision 0.586 vs
0.320, sign p = 0.004; by raw counts arm B ends 69% gold / 31% distractor
lines vs 47% / 53% in arm A). The parsed ledger itself populated in only 34
of 104 arm-B cycles, so the observed effect is prompt-level. Summarizer
cost: 439 requests for 208 cycle attempts, mean latency 20.4 s, 47
max_tokens ladder climbs, 0 error calls.

### R2-E5 and R2-E5-RM - the typed arm, measured and re-measured (fixture + live)

R2-E5 (the first typed-arm measurement; adapter arm `08f16adf0`, e3/e3adv
mirror wiring `fe6b6d966`, results `59832805f`; artifacts
`research/context_rot_r2/live/e5/`):

- Part 1 (PRODUCTION-PATH, offline adapter, the E1 19-probe x 5-cycle
  fixture, all three arms one pass): default 0/19, taskState v1 14/19,
  typed 15/19 - measured at the pre-D25 implementation. The typed ledger
  held 23 records at a stable 777/800 hot-token budget; the verifier passed
  5/5 cycles with zero repairs; +886 mean request chars over v1.
- Part 2 (LIVE-MODEL, e3 tasks A/C/E, typed vs default, 123 wire calls):
  default 15/15 + 3 baselines correct; typed correct on every point that ran
  (A 5/5, C 5/5). TASK-E typed HALTED at cycle 2: cycles 2-5 failed the
  typed verifier with unresolved references (5/3/3/3) - record ids
  recomputed from evolving text stranded the citations (D24). Failure
  direction safe (conversation preserved, compaction refused), recorded as
  the first live falsification of a typed-mode pre-registration.
- Part 3 (LIVE-MODEL, the E3ADV C1 typed cell, 21 wire calls): correct 5/5 +
  baseline, 3/3 strict - but NO SUPERSEDED section at any cycle: the typed
  pairing was still token-overlap-based, the zero-overlap correction retired
  nothing, and GLM reconciled in its own words (D29).

R2-E5-RM (the re-run of the affected cells on the fixed tip; the typed
mirror ported to the tip's `typed_state.rs` first - `6ce030e59` - and
validated by reproducing the committed E5 numbers exactly before the port;
artifacts `research/context_rot_r2/live/e5/rm/`):

- E1 fixture, typed arm, on the integrated tip: 13/19 (was 15/19 at the
  pre-D25 implementation; both numbers are real and the drop is attributed:
  the D25 step boundary correctly retires the two LOW_PRIORITY_EPISODE-
  classified constraint probes at the first merge that does not restate
  them - q-constraint-6/7 - while decisions 4/4, evidence 1/4 and
  supersession 2/2 are unchanged and the verifier passes 5/5 in both runs;
  the ledger is 23 then 19 records at 777 then 665 of 800 hot tokens).
  W46 and W47 measured this independently with the same result.
- TASK-E typed, live: 5/5 cycles, NO HALT (was: halt at cycle 2). The
  verifier passed every cycle (cycles 1-2 via the deterministic
  missing-item repair; 0 unresolved refs anywhere; was 5/3/3/3 unresolved
  at E5); probe q-e-error correct at every cycle point + baseline (3/3
  strict reps); the exact assertion `assertion failed: depth == 4096 (got
  3072)` survives verbatim. 46 wire calls of the <= 120 budget.
- E3ADV C1 typed, live: SUPERSEDED rendered at EVERY cycle 1-5 (the D29
  fix's own live run reached cycles 1-4; the D25 step boundary removes the
  cycle-5 tier-8 budget overflow: 697/800, no overflow), the stale decision
  retired by provenance at every cycle, every accepted summary copies the
  retirement (no GLM reconciliation needed), the verifier passed every
  cycle with zero findings, repairs and retries; probes 5/5 + baseline
  3/3 strict; the anchor is retained.
- The offline TASK-E id chain still stranded exactly two references at the
  cycle-2 merge - the restatement-absorbed record's ids gain no tombstone -
  filed as D33 (the live cells did not trigger it: GLM's compact citation
  shape falls outside the verifier reference grammar); fixed on this tip by
  `cea3a4496`, pinned by `absorption_then_citation_resolves` (table above).

What this shows: on the fixture the typed arm protects everything the v1
ledger protects minus the two step-scoped episode probes the D25 boundary
correctly retires, plus the cargo-test evidence line the v1 regex misses
(13/19 vs 14/19, with the pre-D25 15/19 stated for attribution); under the
live model its two availability defects are gone (no halt, retirement
rendered and copied at every cycle) and it matched the default arm wherever
both ran. The default stays `"off"`: single-model, practice-scale evidence
with two verifier-gated failure modes fixed post-measurement is not enough
to flip a default.

## What is NOT claimed

- No official benchmark scores. Every CompactBench run - mock or live - uses
  the public practice suites with custom adapter methods (the live run: 8 of
  20 starter cases per arm, one draw); the ContextBench live practice run
  measures gold-context retention on synthetic trajectories, not free-form
  agent exploration, and `patch_success` is UNAVAILABLE in every record.
  Nothing here is leaderboard-comparable.
- No cross-model claims. The only live-model evidence is GLM 5.3 on one DGX K3
  endpoint, temperature 0, with the documented repetition plans
  (`research/context_rot_r2/live/README.md`, `live/e3/README.md`,
  `live/e3adv/README.md`, `live/e5/README.md`, `live/e5/rm/README.md`,
  `compactbench_adapter/live/README.md`, `contextbench/live/README.md`). The
  CompactBench live run is a single draw per case; the ContextBench live run
  is one paired dev slice.
- R2-E1/E-INC numbers are pipeline mechanics under a deliberately lossy
  deterministic fixture. They bound what the pipeline alone protects; they are
  not predictions about any specific model.
- The typed arm is measured once and re-measured once (R2-E5, R2-E5-RM:
  fixture 15/19 pre-D25 / 13/19 on this tip vs v1 14/19 and default 0/19;
  live TASK-E 5/5 no halt, E3ADV C1 SUPERSEDED at all five cycles), on one
  model, at practice scale. The default stays `"off"` pending cross-model
  and longer-session evidence.
- The W11 ledger fix is validated offline on the live corpus (R2-E2-RM), not
  by a fresh live run; the model-dependent prediction (live corrections
  answered correctly at every point) is stated as a prediction in the
  experiment ledger, not as a measurement.
- The v1 ledger's deeper supersession semantics are refined in this PR (the
  recovered W16 work: clause-level correction classification, prohibition
  scrubbing, correction-priority render - `tests/rot2_supersession.rs`),
  but the v1 keyword-regex capture gaps (N1, N2) are by design not
  reworked; typed v2 is the replacement path (the W9-F1 clause-capture gap
  is closed by the W51 refinement in the table above).
- Not a measurement of the shipped binary for R2-E2/E3/E3ADV: those runs are
  a Python mirror of the Rust path, fixture-validated byte-for-byte against
  the stored R2-E1 result, with the documented deviations in each README.
- RepoQA, SWE-bench and Terminal-Bench: not executed in this campaign.
- The five live-run raw-call logs
  (`research/context_rot_r2/live/raw-calls.jsonl`, `live/e3/`,
  `live/e3adv/`, `live/e5/live/`, `compactbench_adapter/live/`) are git-LFS
  pointers. After checkout run `git lfs pull` (or clone with LFS enabled);
  without it those files remain ~130-byte pointer stubs and the evidence
  they back is not inspectable. The LFS objects are pushed with the branch
  (see `reports/context-rot-r2/PUSH-CHECKLIST.md`).

## Testing

Every number in this section cites the committed gate record
(`reports/context-rot-r2/REGRESSION-GATES.md`). The landed-tip battery is
section 11 there (the W63 full gate list at `90fcd17db`: 32 of the 33
recorded steps green - the other eleven `rot2_*` suites, all thirteen
`compaction_*` suites, all six lib filters - with the single red the
deterministic D34 typed-arm smoke, section 11.7, since fixed by W71), the
six Python research suites are green at the landed tip (section 11.10,
W72, closing the gap the stopped battery left), and the final-tip matrix
is REGRESSION-GATES section 12, FINAL TIP MATRIX (dba588938) - GREEN (W81), recorded before the final docs passes. The landed record:

- The W25 full pass at `29f1f0c92` (with per-suite SHAs back to `f7bc6306c`):
  the three lib filters (`core::compaction` 146, `typed_state` 60,
  `context_health_calibration` 17), all nine `rot2_*` suites, all twelve
  `compaction_*` gate suites, and every offline Python research suite (the
  138-test discovery set, the ContextBench integration self-check 127, the
  W27 live-pipeline selftest 24, the E3 14 and E3ADV 15 selftests, the 13
  adapter checks against a harness binary built from the branch).
- The W49 wave-2 battery at `71e770c5e` (REGRESSION-GATES section 10), after
  the D21-D29 fix wave: `rot2_typed_adversarial` 23 passed / 0 failed /
  0 ignored (the six formerly-ignored defect tests green), the typed
  integration 9, the native typed chain 2, multicycle 5 + 1 ignored (the
  research adapter entry point), render_cap 7, ledger_fixes 7,
  `--lib typed_state` 74, `--lib core::compaction` 160, plus the
  `compaction_summary_path` 8/8 and `compaction_parity_suite` 14/14 spot
  checks (the W43 T05.4 fixture re-pin `31ce39b84` holds).
- The W63 landed-tip battery at `90fcd17db` (REGRESSION-GATES section 11):
  the full gate list, 33 recorded steps, 32 green - `rot2_typed_adversarial`
  24/0/0 (grew from 23 with the D30 pin), `rot2_wave_adversarial` 9/0/0
  (before the D33 pin landed; ten tests at this tip), the typed integration
  9, the native typed chain 2, `rot2_multicycle` 4 passed / 1 failed /
  1 ignored (the D34 red: the typed-arm smoke, deterministic on an isolated
  rerun), render_cap 7, ledger_fixes 7, defect_wave2 3, harness_digest 2,
  branch_summary 2, subagent_constraints 3, supersession 4, all thirteen
  `compaction_*` suites, and the six lib filters (`core::compaction` 168,
  `typed_state` 78, `task_state` 17, `context_health_calibration` 17,
  `settings_manager` 62, `settings` 100). The battery stopped on the real
  failure per the gate protocol, so its Python steps and the complete
  workspace check did not run there; W72 closed the Python gap at the
  landed tip (section 11.10: all six suites green - the 138-test discovery,
  the ContextBench integration self-check 127, the W27 live-pipeline
  selftest 24, E3 14, E3ADV 15, the adapter checks 13/13).
- The post-wave suites: the W48 `rot2_wave_adversarial` suite (the eight
  wave tests plus the D31 companion pin and the D33 absorption pin, ten
  tests and zero ignored at this tip; the D31/D32 red tests un-ignored and
  green after their fixes; the D33 pin landed with its fix, `6d85cc48f`),
  the D30 guards (`10e2b36e6`:
  four lib tests + corpus cases + the smoke seed), and the
  `rot2_supersession` suite for the v1 refinement (W51).
- Known environment-bound failures, verified at the base in the W25 pass
  (REGRESSION-GATES section 5) and re-verified at base by the W63 battery
  (section 11.8, neither appearing in its 33 steps): `enabled_skill_is_importable`
  needs `uv` on the test child's PATH (absent on this host) and the
  painted-frame tray test is scheduling-sensitive and passes serialized.
  Neither file is touched by this campaign.

The workspace-wide `cargo check --workspace --all-targets` record: 0 errors
at the integration heads checked during the campaign (908 warnings at
`f8ad670c8` vs 903 at the base; the four campaign-added warnings were
dropped by the W42 cleanup `fa3634b7a`); the W63 battery's tip check was
interrupted on the D34 stop with partial numbers only (0 errors, at least
198 warning diagnostics, REGRESSION-GATES section 11.5), so the final-tip
error and warning counts are recorded there (0 errors; 576 warnings = the base count). The campaign's code footprint is one
crate: `git diff --name-only` base..tip lists files under
`crates/pi-coding-agent` plus `.changes/`, `reports/context-rot-r2/`,
`research/context_rot_r2/`, `.gitattributes` and README-adjacent docs only
- no other workspace crate is touched, so no other crate's tests were re-run.

## Reviewer notes

- The defaults are unchanged: `taskState` keeps its `"off"` default (values:
  `"off"` | `"prompt"` | `"typed"`; env `PRIME_AGENT_COMPACTION_TASK_STATE`).
  The typed mode is reachable by opting in through settings or the env
  (`resolve_task_state_mode` fails safe to `"off"` on unknown values);
  `"prompt"` (v1) behaves exactly as before; the `compaction-harness` binary
  is opt-in tooling.
- The typed-state wiring to check first, in order: `resolve_task_state_mode`
  and `typed_hot_state_budget_tokens` in `src/core/compaction/compaction.rs`
  (mode + budget), the `typed_state` field on `CompactionPreparation`
  (`338b920c3`), `verify` + the deterministic repair on the compact path, the
  `details.typedState`/`details.typedVerification` persistence, and
  `tests/rot2_typed_integration.rs` (`40bead288`) for the end-to-end guards.
- The E3ADV finding to keep in mind when weighing the v1 ledger: with no
  shared anchor, the ledger keeps the stale decision current and GLM argues
  it down in its own words - correctness against the ledger, not because of
  it. The W11 fixes remove the measured inversion mechanisms (R2-E2-RM,
  offline on the live corpus) and the W51 refinement tightens classification;
  the mechanism is replaced in typed v2 by the D29 provenance-based pairing.
- The defect table above is the verification map: every FIXED row names its
  commit and its guard test; the open rows (D7, D10 sub-item, D12 residual,
  D17 wiring, D20 residue) state what is open and where it is
  documented. `reports/context-rot-r2/BUG-LEDGER.md` is the source of truth
  for statuses.
- The D34 close-out: the typed-arm smoke
  (`rot2_multicycle::rot2_smoke_typed_arm_persists_typed_ledger_and_verifies`)
  was the one deterministic red at the W63 landed-tip battery
  (REGRESSION-GATES section 11.7, the record at `90fcd17db`) and is fixed on
  this tip (W71 `6894f6a6d`, the row above). The final-tip matrix
  (REGRESSION-GATES section 12, W81) re-ran the smoke green at the final tip -
  cite its record for the green.
- D17 detail: `ContextUsageEstimate.system_prompt_tokens` /
  `tool_schema_tokens` are non-zero only on the no-usage fallback path - the
  usage-anchored path keeps 0.0 to avoid double-counting provider-reported
  usage. The six production estimator call sites still use the message-only
  path; the request-aware wiring is recorded as follow-up work.
- D16 detail: the shared constant is `IMAGE_BLOCK_CHARS` (4096 chars = 1024
  tokens at chars/4) in `src/core/compaction/request_tokens.rs`.
- The experiment numbers: each subsection above cites its artifact directory
  under `research/context_rot_r2/`; the raw wire logs are LFS pointers
  (`git lfs pull`). The category labels (SYNTHETIC-DIAGNOSTIC /
  PRODUCTION-PATH / PRODUCTION-PATH-MIRROR / CUSTOM-OPTIMUS / LIVE-MODEL)
  are defined in `reports/context-rot-r2/BASELINE.md` and never mixed within
  one table.
- Where to look, in order: `src/core/agent_session.rs`
  (`threshold_auto_compaction_ready`, the D11 digest prune, the D18/D19
  bounded notices), `src/core/session_manager.rs`
  (`migrate_v1_to_v2`), `src/core/compaction/compaction.rs` (empty-window
  guard, `previous_summary_prose`, `validate_extension_compaction_summary`,
  estimator paths, typed wiring), `src/core/compaction/utils.rs` (read-files
  inventory), `src/core/compaction/request_tokens.rs`,
  `src/core/compaction/typed_state.rs`,
  `src/core/compaction/task_state.rs` (the v1 ledger: W11 ids, W51
  refinement), `src/core/compaction/branch_summarization.rs` (D10),
  `src/core/messages.rs` (D12), `src/core/memory/search.rs` (D13),
  `src/core/context_health_calibration.rs`, the new and updated test files
  (`tests/rot2_multicycle.rs`, `tests/rot2_typed_integration.rs`,
  `tests/rot2_typed_adversarial.rs`, `tests/rot2_wave_adversarial.rs`,
  `tests/rot2_native_typed_chain.rs`, `tests/rot2_ledger_fixes.rs`,
  `tests/rot2_supersession.rs`, `tests/rot2_branch_summary.rs`,
  `tests/rot2_subagent_constraints.rs`, `tests/rot2_harness_digest.rs`,
  `tests/rot2_render_cap.rs`, `tests/rot2_defect_wave2.rs`,
  `tests/compaction_turn_flow.rs`, `tests/compaction_summary_path.rs`,
  `tests/compaction_kernel_parity.rs`), `src/bin/compaction_harness.rs`,
  then `research/context_rot_r2/` and `reports/context-rot-r2/`.
