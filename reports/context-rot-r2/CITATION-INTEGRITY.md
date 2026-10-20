# CITATION INTEGRITY - every test name cited by the r2 reports, verified at the tip

Worker W70, 2026-10-20. Worktree `C:/Users/openclawuser/optimus-rot2-w70-cites`,
branch `rot2/w70-cites` off the integration tip. Scope: all 16 committed
`.md` files under `reports/context-rot-r2/` (the brief estimated ~17; the
directory holds 16, all processed). Offline pass; no test binary was built and
no test was executed - this is a source-inventory verification of the cited
names, not a re-run of the gates.

## 1. Method

1. Extracted every backticked token from the 16 report files (1 092 distinct
   tokens; 215 pure snake_case plus 15 `::`-qualified names and ~150
   path-shaped citations).
2. Kept the test-name-looking subset: snake_case identifiers on lines naming
   tests, pins, guards, suites, gates, filters, or pass/fail/ignore results,
   then re-swept all remaining snake_case tokens with a substring/fuzzy match
   against the test inventory so a citation on a line without test vocabulary
   could not slip through.
3. Built the verification inventory at this tip: every `#[test]` /
   `#[tokio::test]` / `#[tokio::test(...)]` function under
   `crates/pi-coding-agent/tests/` (87 `.rs` files, 871 distinct test fns) and
   `crates/pi-coding-agent/src/core/compaction/` (11 `.rs` files, 168 test fns),
   plus the `tests/<name>.rs` suite files and the lib filters documented in
   REGRESSION-GATES sections 2 and 10.
4. Every cited test name was classified: exists in the map; exists outside
   the map; or miss (cited but absent).

## 2. Result summary

| class | distinct citations | verdict |
| --- | --- | --- |
| integration suites (`tests/<name>.rs`) | 25 | all 25 exist at the tip |
| test fns resolved inside the file map (tests/ dir + compaction module) | 69 | all 69 exist at the tip |
| lib test fns cited with sources outside this worker's read map | 15 | all 15 exist (see 3.2 for the method) |
| lib filters (`core::compaction`, `typed_state`, `context_health_calibration`, `compaction`, `task_state`) | 5 | all 5 documented with results in REGRESSION-GATES; modules exist and counts reconcile (section 5) |
| **total distinct test-name citations verified** | **114** | **113 verified, 1 miss** |
| true miss (cited, does not exist) | 1 | `absorption_then_citation_resolves` - section 4 |
| paraphrase (renamed) | 0 | none |
| historical (old name, dated section) | 0 | none; the dated count rows are covered by section 5 |

## 3. Verified inventory

### 3.1 Inside the file map (94 names)

Suites (25): every cited suite file exists; the tip's test-fn count is in
parentheses (cited pass counts are the dated gate rows - section 5):

| cited suite | exists at the tip | cited in report |
| --- | --- | --- |
| `compaction_anthropic_length_recovery` | tests/compaction_anthropic_length_recovery.rs (4 test fns at tip) | REGRESSION-GATES.md |
| `compaction_chat_length_recovery` | tests/compaction_chat_length_recovery.rs (4 test fns at tip) | REGRESSION-GATES.md |
| `compaction_fidelity` | tests/compaction_fidelity.rs (1 test fn at tip) | REGRESSION-GATES.md |
| `compaction_kernel_parity` | tests/compaction_kernel_parity.rs (23 test fns at tip) | FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `compaction_length_recovery` | tests/compaction_length_recovery.rs (7 test fns at tip) | REGRESSION-GATES.md |
| `compaction_model_threshold` | tests/compaction_model_threshold.rs (10 test fns at tip) | REGRESSION-GATES.md |
| `compaction_observability` | tests/compaction_observability.rs (7 test fns at tip) | REGRESSION-GATES.md |
| `compaction_parallel_fallback` | tests/compaction_parallel_fallback.rs (2 test fns at tip) | REGRESSION-GATES.md |
| `compaction_parity_suite` | tests/compaction_parity_suite.rs (14 test fns at tip) | EXPERIMENT-LEDGER.md, FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `compaction_recovery` | tests/compaction_recovery.rs (9 test fns at tip) | REGRESSION-GATES.md |
| `compaction_summary_guard` | tests/compaction_summary_guard.rs (2 test fns at tip) | REGRESSION-GATES.md |
| `compaction_summary_path` | tests/compaction_summary_path.rs (8 test fns at tip) | EXPERIMENT-LEDGER.md, FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md, REVIEW-AUDIT.md |
| `compaction_turn_flow` | tests/compaction_turn_flow.rs (3 test fns at tip) | REGRESSION-GATES.md |
| `rot2_branch_summary` | tests/rot2_branch_summary.rs (2 test fns at tip) | REGRESSION-GATES.md, REVIEW-AUDIT.md |
| `rot2_defect_wave2` | tests/rot2_defect_wave2.rs (3 test fns at tip) | REGRESSION-GATES.md |
| `rot2_harness_digest` | tests/rot2_harness_digest.rs (2 test fns at tip) | REGRESSION-GATES.md |
| `rot2_ledger_fixes` | tests/rot2_ledger_fixes.rs (7 test fns at tip) | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `rot2_multicycle` | tests/rot2_multicycle.rs (6 test fns at tip) | EXPERIMENT-LEDGER.md, FINAL-REPORT.md, REGRESSION-GATES.md |
| `rot2_native_typed_chain` | tests/rot2_native_typed_chain.rs (2 test fns at tip) | EXPERIMENT-LEDGER.md, FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `rot2_render_cap` | tests/rot2_render_cap.rs (7 test fns at tip) | EXPERIMENT-LEDGER.md, FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `rot2_subagent_constraints` | tests/rot2_subagent_constraints.rs (3 test fns at tip) | REGRESSION-GATES.md |
| `rot2_supersession` | tests/rot2_supersession.rs (4 test fns at tip) | MULTICYCLE-DRIFT.md, PR-BODY.md |
| `rot2_typed_adversarial` | tests/rot2_typed_adversarial.rs (24 test fns at tip) | FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `rot2_typed_integration` | tests/rot2_typed_integration.rs (9 test fns at tip) | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, REGRESSION-GATES.md |
| `rot2_wave_adversarial` | tests/rot2_wave_adversarial.rs (9 test fns at tip) | PR-BODY.md |

Test functions (69) - every name below was found as a `#[test]`/`#[tokio::test]`
fn at the stated `file:line` at this tip:

| cited test fn | exists at | cited in report |
| --- | --- | --- |
| `a_later_correction_wins_the_same_target_and_keeps_the_earlier_correction` | tests/rot2_ledger_fixes.rs:227 | FINAL-REPORT.md |
| `a_no_op_skip_leaves_the_next_real_compaction_boundaries_intact` | tests/compaction_summary_path.rs:115 | FINAL-REPORT.md, PR-BODY.md |
| `a_retired_id_suppresses_only_the_exact_same_content` | tests/rot2_ledger_fixes.rs:269 | FINAL-REPORT.md |
| `all_caps_verified_claims_cannot_evade_the_fabricated_check` | tests/rot2_typed_adversarial.rs:1517 | BUG-LEDGER.md, PR-BODY.md |
| `bracketed_ticket_ids_in_user_text_verify_cleanly` | tests/rot2_typed_adversarial.rs:651 | BUG-LEDGER.md, PR-BODY.md |
| `branch_summary_input_carries_the_failing_tool_result` | tests/rot2_branch_summary.rs:165 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `branch_summary_input_skips_tool_results_with_no_text` | tests/rot2_branch_summary.rs:227 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `compact_persists_a_non_empty_read_files_inventory` | tests/compaction_summary_path.rs:327 | FINAL-REPORT.md, PR-BODY.md |
| `compact_typed_mode_feeds_the_protected_block_and_round_trips_the_ledger` | tests/rot2_typed_integration.rs:558 | FINAL-REPORT.md |
| `compaction_keeps_the_pruned_provider_context_digest_clean` | tests/rot2_harness_digest.rs:395 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `d18_failed_auto_compaction_surfaces_a_bounded_notice_to_the_model` | tests/rot2_defect_wave2.rs:433 | FINAL-REPORT.md, PR-BODY.md |
| `d18_successful_compaction_does_not_surface_a_failure_notice` | tests/rot2_defect_wave2.rs:483 | FINAL-REPORT.md, PR-BODY.md |
| `d1_toggle_off_over_threshold_does_not_stop_the_turn` | tests/compaction_turn_flow.rs:327 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `d20_summarizer_request_equals_the_serialized_model_visible_conversation` | tests/rot2_defect_wave2.rs:529 | FINAL-REPORT.md, PR-BODY.md |
| `d2_interactive_turn_crossing_threshold_compacts_and_continues` | tests/compaction_turn_flow.rs:370 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `d2_queued_user_prompt_completes_after_threshold_compaction` | tests/compaction_turn_flow.rs:420 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `deadline_fallback_boilerplate_is_not_the_next_previous_summary` | tests/compaction_summary_path.rs:190 | FINAL-REPORT.md, PR-BODY.md |
| `empty_inputs_score_zero` | src/core/compaction/request_tokens.rs:138 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `evolving_tool_state_records_keep_a_stable_id_across_merges` | tests/rot2_typed_adversarial.rs:889 | BUG-LEDGER.md, PR-BODY.md |
| `explicit_off_setting_wins_over_the_typed_environment_variable` | tests/rot2_typed_adversarial.rs:1292 | REVIEW-AUDIT.md |
| `extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries` | tests/compaction_summary_path.rs:147 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `fallback_only_history_yields_no_previous_summary` | tests/compaction_summary_path.rs:232 | FINAL-REPORT.md, PR-BODY.md |
| `ids_are_content_derived_and_stable_across_merges` | tests/rot2_ledger_fixes.rs:328 | FINAL-REPORT.md |
| `image_blocks_cost_4096_chars_1024_tokens` | src/core/compaction/request_tokens.rs:174 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `k_harness_changes_leave_exactly_one_digest_in_the_provider_context` | tests/rot2_harness_digest.rs:319 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `live_record_ids_still_vouch_for_verified_claims` | tests/rot2_wave_adversarial.rs:227 | BUG-LEDGER.md |
| `malformed_typed_state_payload_silently_drops_durable_records` | tests/rot2_typed_adversarial.rs:1109 | BUG-LEDGER.md, PR-BODY.md |
| `missing_constraint_triggers_the_deterministic_repair` | tests/rot2_typed_integration.rs:706 | FINAL-REPORT.md |
| `native_compaction_reports_the_salvage_of_a_malformed_prior_ledger` | tests/rot2_wave_adversarial.rs:716 | BUG-LEDGER.md, PR-BODY.md |
| `new_material_after_the_boundary_still_prepares` | tests/compaction_summary_path.rs:98 | FINAL-REPORT.md, PR-BODY.md |
| `non_edit_path_tool_calls_populate_the_read_inventory` | tests/compaction_summary_path.rs:288 | FINAL-REPORT.md, PR-BODY.md |
| `old_positional_ids_round_trip_and_merge_without_losing_new_items` | tests/rot2_ledger_fixes.rs:360 | FINAL-REPORT.md |
| `path_arguments_of_non_edit_tool_calls_record_reads` | src/core/compaction/utils.rs:598 | FINAL-REPORT.md, PR-BODY.md |
| `pending_actions_do_not_accumulate_forever_across_merges` | tests/rot2_typed_adversarial.rs:1404 | BUG-LEDGER.md, PR-BODY.md |
| `post_compaction_usage_is_not_stale` | tests/compaction_parity_suite.rs:2072 | REGRESSION-GATES.md |
| `prepare_compaction_builds_a_typed_ledger_from_tool_results_and_subagent_state` | tests/rot2_typed_integration.rs:491 | FINAL-REPORT.md |
| `repair_is_bounded_to_one_attempt_with_no_second_model_call` | tests/rot2_typed_adversarial.rs:680 | BUG-LEDGER.md, PR-BODY.md |
| `repair_that_still_fails_verification_fails_the_episode` | tests/rot2_typed_adversarial.rs:720 | BUG-LEDGER.md, PR-BODY.md |
| `repeated_trigger_with_no_new_material_resolves_to_no_op` | tests/compaction_summary_path.rs:81 | FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `retired_ids_do_not_vouch_for_verified_claims` | tests/rot2_wave_adversarial.rs:144 | BUG-LEDGER.md, PR-BODY.md, REVIEW-AUDIT.md |
| `retired_step_records_keep_summary_references_resolvable` | tests/rot2_typed_adversarial.rs:1487 | BUG-LEDGER.md, PR-BODY.md |
| `rot2_production_multicycle` | tests/rot2_multicycle.rs:916 | BASELINE.md, EXPERIMENT-LEDGER.md, FINAL-REPORT.md, REGRESSION-GATES.md, REVIEW-AUDIT.md |
| `rot2_request_level_estimate_adds_system_prompt_and_tools_d17` | tests/rot2_multicycle.rs:1143 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `rot2_smoke_typed_arm_persists_typed_ledger_and_verifies` | tests/rot2_multicycle.rs:1039 | REGRESSION-GATES.md |
| `rot2_user_image_blocks_cost_tokens_d16` | tests/rot2_multicycle.rs:1112 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `rot2_w8_agent_message_task_crosses_compaction_mid_child` | tests/rot2_subagent_constraints.rs:901 | FINAL-REPORT.md |
| `rot2_w8_child_result_after_parent_compaction_resolves_on_compacted_context` | tests/rot2_subagent_constraints.rs:1031 | FINAL-REPORT.md |
| `rot2_w8_parent_compaction_then_child_task_prompt_carries_constraints` | tests/rot2_subagent_constraints.rs:740 | FINAL-REPORT.md |
| `sibling_corrections_retire_their_respective_decisions_without_mutual_retirement` | tests/rot2_typed_adversarial.rs:1650 | PR-BODY.md |
| `step_scoped_records_stay_bounded_across_twelve_merges` | tests/rot2_typed_adversarial.rs:1438 | BUG-LEDGER.md, PR-BODY.md |
| `superseded_corrections_and_tasks_render_the_retirement_direction` | tests/rot2_typed_adversarial.rs:803 | BUG-LEDGER.md, PR-BODY.md |
| `superseded_decision_presented_as_current_fails_verification` | tests/rot2_typed_integration.rs:758 | FINAL-REPORT.md |
| `three_cycle_chain_with_ticket_text_and_evolving_tool_state_verifies_every_cycle` | tests/rot2_typed_adversarial.rs:929 | BUG-LEDGER.md, PR-BODY.md |
| `tight_budget_drops_superseded_history_but_keeps_the_pairing_line` | src/core/compaction/typed_state.rs:3613 | BUG-LEDGER.md, FINAL-REPORT.md, PR-BODY.md |
| `total_is_exactly_the_sum_of_its_parts` | src/core/compaction/request_tokens.rs:250 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `typed_hot_state_budget_is_a_capped_quarter_of_the_summary_budget` | tests/rot2_typed_integration.rs:79 | FINAL-REPORT.md |
| `typed_mode_resolution_accepts_typed_and_fails_safe_on_unknown_values` | tests/rot2_typed_integration.rs:43 | FINAL-REPORT.md |
| `unrelated_distant_correction_retires_nothing` | tests/rot2_typed_adversarial.rs:1732 | PR-BODY.md |
| `unsafe_typed_allocation_fails_the_episode_before_any_model_call` | tests/rot2_typed_integration.rs:666 | FINAL-REPORT.md |
| `user_correction_retires_a_user_stated_decision` | src/core/compaction/typed_state.rs:2681 | PR-BODY.md |
| `user_stated_decision_is_a_verified_decision_with_task_scope` | src/core/compaction/typed_state.rs:2635 | PR-BODY.md |
| `user_stated_decision_survives_the_step_boundary_merge` | src/core/compaction/typed_state.rs:2653 | PR-BODY.md |
| `v1_prompt_mode_still_works_unchanged` | tests/rot2_typed_integration.rs:854 | FINAL-REPORT.md |
| `v1_task_state_details_still_deserialize_alongside_the_typed_fields` | tests/rot2_typed_integration.rs:798 | FINAL-REPORT.md |
| `verify_treats_bracketed_user_tokens_as_prose` | src/core/compaction/typed_state.rs:3797 | BUG-LEDGER.md, PR-BODY.md |
| `w9_f2_a_late_decision_reusing_a_retired_id_is_not_deleted` | tests/rot2_ledger_fixes.rs:126 | FINAL-REPORT.md, PR-BODY.md |
| `w9_f2_and_f5_late_constraints_reusing_retired_ids_survive_with_anchors` | tests/rot2_ledger_fixes.rs:180 | FINAL-REPORT.md, PR-BODY.md |
| `w9_f4_corrections_supersede_the_older_decisions_not_each_other` | tests/rot2_ledger_fixes.rs:63 | FINAL-REPORT.md, PR-BODY.md |
| `zero_overlap_correction_retires_the_stale_decision_by_provenance` | tests/rot2_typed_adversarial.rs:1546 | PR-BODY.md |

### 3.2 Outside the file map (15 names), verification method disclosed

These are cited as lib tests whose source files the reports name explicitly
(`agent_session.rs`, `messages.rs`, `memory/search.rs`, `session_manager.rs`,
`context_health.rs`, `context_health_calibration.rs`, the modes tree) - all
outside this brief's read map. ROT2-COMMON permits `git show` on specific
paths, so each was confirmed with one targeted
`git show HEAD:<explicit path> | rg "fn <name>"` - an existence check only;
no out-of-map file was opened in the worktree and none was edited. All 15
exist at HEAD:

| cited test fn | exists at | cited in report |
| --- | --- | --- |
| `b1_resync_catchup_republishes_independent_decision_and_compaction` | src/modes/daemon/daemon_parity_tests.rs:1902 | REGRESSION-GATES.md |
| `budget_short_skip_fails_closed_and_surfaces_the_reason` | src/core/memory/search.rs:1395 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md, RETRIEVAL-SCORECARD.md |
| `compaction_count_stays_before_the_meter_with_goal_and_heartbeat` | src/modes/interactive/native_host_status_tests.rs:223 | REGRESSION-GATES.md (cited as the full `modes::interactive::...::native_status::tests::` path) |
| `convert_to_llm_fences_memory_recall_text_but_not_other_custom_types` | src/core/messages.rs:553 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md, RETRIEVAL-SCORECARD.md |
| `core002_refinement_plans_and_baselines_use_the_requested_scope` | src/core/agent_session.rs:25477 | REGRESSION-GATES.md |
| `corpus_scores_with_the_production_scorer` | src/core/context_health_calibration.rs:578 | CONTEXT-HEALTH-CALIBRATION.md |
| `current_heuristic_scores_poorly_against_semantic_constraint_loss` | src/core/context_health_calibration.rs:619 | CONTEXT-HEALTH-CALIBRATION.md |
| `d18_failure_notice_is_model_visible_and_bounded` | src/core/agent_session.rs:23338 | FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `d19_live_disclosure_lists_are_bounded_with_a_dropped_marker` | src/core/agent_session.rs:23451 | FINAL-REPORT.md, PR-BODY.md, REGRESSION-GATES.md |
| `enabled_skill_is_importable` | src/core/agent_session.rs:22254 | PR-BODY.md; REGRESSION-GATES.md (as the full `core::agent_session::post_compaction_continuation_tests::` path) |
| `foreign_only_corpus_fails_closed_and_surfaces_the_reason` | src/core/memory/search.rs:1355 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md, RETRIEVAL-SCORECARD.md |
| `message_list_estimates_understate_request_utilisation` | src/core/context_health.rs:277 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `migration_ignores_a_boundary_pointing_at_the_session_header` | src/core/session_manager.rs:6564 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds` | src/core/session_manager.rs:6531 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md |
| `partial_recall_skips_stay_silent` | src/core/memory/search.rs:1445 | COMPATION-CORRECTNESS.md, FINAL-REPORT.md, PR-BODY.md, RETRIEVAL-SCORECARD.md |

Supporting checks for the same citations: the module path
`post_compaction_continuation_tests::compaction_retry_backoff_tests` named in
REGRESSION-GATES section 2 exists (`agent_session.rs:19631` / `:22507`) and
contains the two d18/d19 guards above; REVIEW-AUDIT's
`native_host_status_tests.rs:251` points inside the body of the test fn that
starts at `:223` of `src/modes/interactive/native_host_status_tests.rs`.

## 4. Misses by class

### 4.1 (a) True miss - cited but does not exist: 1, BLOCKER for the integrator

- `absorption_then_citation_resolves` - PR-BODY.md:104, the D33 row of the
  defect table, guard column: "`absorption_then_citation_resolves`
  (`tests/rot2_wave_adversarial.rs`, lands with W54)". The row status is
  "VERIFIED-OPEN, fix in flight (W54)".
- Facts at this tip: `tests/rot2_wave_adversarial.rs` exists and holds 9
  test fns; none is the D33 guard (no function matching `absorption` exists
  anywhere under `crates/pi-coding-agent/tests/` or the compaction module);
  `git log` shows no W54/D33-fix commit landed (the last code commits are
  `6041f71c9` D31 and `53ab2e3d9` D32; everything after is docs-only).
- Classification: the citation is forward-looking and the row itself says
  "lands with W54", so it is not a silent fabrication - but the name does
  not exist at the tip, and PR-BODY.md is the artifact a reviewer reads
  first. PR-BODY.md:583 ("the D33 pin lands with W54") and
  PUSH-CHECKLIST.md section 0 already instruct the integrator to flip the
  row when W54 lands or keep D33 listed as open.
- Fix list (integrator, exactly one of):
  1. land W54 (the absorption tombstone fix plus the
     `absorption_then_citation_resolves` guard in
     `crates/pi-coding-agent/tests/rot2_wave_adversarial.rs`) before the PR
     opens, and flip the D33 row and its PR-BODY.md:583 mention to FIXED; or
  2. if W54 does not land, reword PR-BODY.md:104 to mark the guard as planned
     (for example "planned guard, not yet in-tree") so no cited test name is
     absent from the tree at review time.

### 4.2 (b) Paraphrase - renamed test: 0

No cited test name is a near-miss rename of an existing test. The closest
pairs are deliberate function-vs-test distinctions, not renames:
`render_hot_state` / `render_durable_index` / `render_allocation` are the
typed-state renderer functions (the guard tests are
`render_hot_state_golden` / `render_durable_index_golden` /
`render_allocation_respects_overflow`, all present and cited correctly where
the reports cite tests).

### 4.3 (c) Historical - old names in dated sections: 0 misses

No report cites a test under an old name. The names that appear only inside
dated run records (REGRESSION-GATES section 5: `b1_resync_catchup_...`,
`core002_refinement_plans_...`, `compaction_count_stays_before_the_meter_...`,
`enabled_skill_is_importable`) all still exist at the tip (section 3.2).

## 5. Count drift in dated rows (disclosed by the reports, not misses)

The gate rows carry per-row "run at" SHAs and the reports disclose the tip
moving under them (REGRESSION-GATES header and W50 note; section 10). For the
integrator, the current-tip reconciliation:

| filter / suite | recorded (dated) | at this tip |
| --- | --- | --- |
| `--lib core::compaction` | 146 at `f7bc6306c`; 160 at `71e770c5e` | 168 test fns in the compaction module |
| `--lib typed_state` | 60 at `f7bc6306c`; 74 at `71e770c5e` | 78 `#[test]` fns in `typed_state.rs` |
| `--lib context_health_calibration` | 17 at `f7bc6306c` | exactly 17 `#[test]` fns in `src/core/context_health_calibration.rs` |
| `rot2_typed_adversarial` | 8 passed + 6 ignored at `29f1f0c92`; 23 at `71e770c5e` | 24 test fns |
| "`rot2_*` suites" count | "all nine" at the W25 pass | 11 `rot2_*.rs` files (W48 `rot2_wave_adversarial`, `rot2_native_typed_chain` came after) |
| `lib task_state` (PR-BODY.md:92) | 21/21, "16 in-module" | 16 test fns in `task_state.rs` - matches the in-module claim |

Per-suite pass counts recorded in REGRESSION-GATES section 3 reconcile exactly
with the tip for every suite that did not grow after its recorded run
(compaction_turn_flow 3, compaction_recovery 9, compaction_model_threshold 10,
compaction_fidelity 1, compaction_summary_guard 2, compaction_observability 7,
compaction_kernel_parity 23, compaction_parallel_fallback 2,
compaction_length_recovery 7, compaction_chat_length_recovery 4,
compaction_anthropic_length_recovery 4, compaction_summary_path 8,
compaction_parity_suite 14, rot2_ledger_fixes 7, rot2_subagent_constraints 3,
rot2_branch_summary 2, rot2_typed_integration 9, rot2_harness_digest 2,
rot2_defect_wave2 3, rot2_render_cap 7, rot2_wave_adversarial 9,
rot2_native_typed_chain 2, rot2_supersession 4, rot2_multicycle 6 fns =
5 passed + 1 ignored).

## 6. Non-test identifiers (out of scope, spot-checked)

The reports also cite code symbols, config keys, and benchmark check names in
backticks. They are not test citations; every one spot-checked exists:
`prepare_compaction` (compaction.rs:1914), `compact_with_metrics` (:2825),
`estimate_tokens` (:1564), `find_cut_point` (:1749), `resolve_task_state_mode`
(:728), `validate_extension_compaction_summary` (:2783),
`estimate_context_tokens_with_request` (:1467), `serialize_conversation`
(utils.rs:296), `render_ledger` (task_state.rs:717), `prioritize_corrections`
(task_state.rs:844), `stable_id` / `is_empty` / `is_heading_line` /
`classify_user_unit` / `render_allocation` / `render_durable_index` /
`render_hot_state` / `retire_path` (typed_state.rs), `estimate_request_tokens`
(request_tokens.rs:95), `lossy_summary` (src/bin/compaction_harness.rs:257),
`continue_recent` (session_manager.rs:5466), `threshold_auto_compaction_ready`
(agent_session.rs:4839), `threshold_compaction_needed` (:4871),
`threshold_context_tokens_from_state` (:273), `model_and_context_tokens`
(:389), `has_pending_session_work` (:11157), `prune_superseded_harness_digests`
(:16269), `compaction_provider_context` (:18057). `recall_latest_decision`,
`assign_a/b`, `patch_success`, `run_arm`, `compare_arms` are CompactBench /
ContextBench check and harness identifiers (research tree, outside the read
map), not Rust test names. The cited research-side entry points
(`run_r2e2.py`, `live/e3/run_e3.py`, `live/e3adv/run_e3adv.py`,
`remeasure/replay.py`, `remeasure/d21_render.py`, `remeasure/w11_port.py`,
`rust_mirror.py`, `rust_prompts.py`, `probe_harness_behavior.py`,
`optimus_compactor.py`, `test_optimus_compactor.py`, `harness_client.py`,
`orchestrate.py`, `scenario.py`, `scoring_r2.py`) were existence-checked and
all resolve; their contents are outside this worker's read map. The
`experiments/multiturn_stability.py` and `classifier.py` citations in
KNOWLEDGE-TRIAGE-REPRODUCTION.md are labelled as upstream ContextBench files
(they live in the external refs root, not this repo).

## 7. Limits of this pass

- No test was executed (offline inventory pass); the pass/fail numbers cited
  by the reports are their own dated gate records, not re-verified here.
- The 15 out-of-map names in section 3.2 were verified name-for-name via
  targeted `git show` (permitted by ROT2-COMMON); their bodies were not read
  and their assertions were not checked.
- Python research suites are cited at file/command level only (REGRESSION-GATES
  section 4); no Python test method names are cited anywhere in the reports
  (no `test_*` backticked token exists), so none required verification.

