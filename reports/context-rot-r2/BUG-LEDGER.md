# BUG LEDGER — context rot iteration 2

Status vocabulary: FIXED (behaviour changed and a test covers it), VERIFIED-OPEN
(still present on this base, citation re-checked against current source),
NEEDS-REVERIFICATION (structure evolved since the 8ae54da audit; the cited
lines no longer map cleanly), DEFERRED (verified, fix scoped for a later wave).

Base for this campaign: 6000d6bc1 (v0.1.36 + #181/#182).

## 1. Defect status on this base

| # | severity | status r2 | evidence |
| --- | --- | --- | --- |
| D1 | HIGH | FIXED (W1, this PR) | shared predicate `threshold_auto_compaction_ready` now gates both the hook (~4846) and the executor (~16499): both enable flags + model + compaction-timestamp fence + threshold + cooldown. Regression: d1_toggle_off_over_threshold_does_not_stop_the_turn (red on base, green with fix). |
| D2 | HIGH | VERIFIED-GUARDED (W1) | on this base D2 is already guarded by two mechanisms: mid-task tool turns resume via !last_is_assistant + continue_after_threshold_compaction, queued user work via has_pending_session_work. Two regression tests added (d2_interactive_turn_crossing_threshold_compacts_and_continues, d2_queued_user_prompt_completes_after_threshold_compaction); D1 was the remaining way to strand work. No production change required. |
| D4 | HIGH | CONFIRMED on the production path | R2-E1: default arm loses all 19 anchors over 5 cycles under a deterministic lossy summarizer (see EXPERIMENT-LEDGER.md). taskState mitigates for constraints/decisions/corrections (14/19). |
| D5 | HIGH | FIXED (W2, this PR) | the empty-window guard no longer treats previous_summary as new material; a threshold re-trigger with zero new material is a no-op. Tests: compaction_summary_path (3 D5 cases). |
| D6 | HIGH | FIXED (W2, this PR) | validate_extension_compaction_summary enforces the structural handoff; the shared summary_sections_complete now backs both validators. Recommended agent_session call-site wiring documented for the integrator. |
| D7 | MED-HIGH | VERIFIED-OPEN, fix DEFERRED | session_manager.rs:1146-1164: incompatible/null provider checkpoint => the checkpointed compaction is skipped as boundary and pre-compaction messages replay (heavy fallback, not lossy). Recommended fix: honour the skipped entry's firstKeptEntryId as a truncation boundary while using the older summary for text. Deferred: risk of subtle restore changes needs dedicated test coverage. |
| D8 | MED-HIGH | FIXED (this PR) | migrate_v1_to_v2 dropped firstKeptEntryIndex without writing firstKeptEntryId when the index was out of bounds or pointed at the session header. Now falls back to the entry after the compaction. 2 regression tests. |
| D9 | MED-HIGH | FIXED (W2, this PR) | previous_summary walk-back skips deadlineFallback and provider-checkpoint entries while the fallback entry keeps its boundary semantics. |
| D10 | MED | FIXED (W13, this PR) | branch summaries now include tool results paired with their assistant calls, evidence-aware excerpt policy applied, budget walk keeps the call of a kept result. Tests: rot2_branch_summary (2). The 'never retry' sub-item remains open. |
| D11 | MED | VERIFIED-OPEN, fix in flight (W20) | W13 verified at current main: only the LAST digest is compared; old digest messages are never pruned from state().messages; k harness changes leave k digest user turns (k-1 stale) in the provider context. Fix assigned to W20. |
| D12 | MED | PARTIALLY FIXED (W13, this PR) | memory-recall text now fenced as <memory_data> in the provider context (mirrors the harness-digest fence). Residual: soft fence (closing-tag collision), same as the harness digest fence. Test: convert_to_llm_fences_memory_recall_text_but_not_other_custom_types. |
| D13 | MED | FIXED (W13, this PR) | fail-closed recall paths (foreign-only corpus drops, stale-source skips, short-skip space) now surface rate-limited warnings with counts; partial skips stay silent (rendered ids are the visible outcome). Tests in memory::search. |
| D14 | MED | FIXED (W2, this PR) | read inventory now records path/paths arguments of non-edit tool calls structurally (new tools tracked automatically); edit keeps its name rule. Tests: compaction_summary_path. |
| D16 | MED | FIXED (W3+integrator, this PR) | estimate_tokens charges IMAGE_BLOCK_CHARS per user image block (was 0); content_chars/tool-result/entry_token_cost use the shared constant (was 4800/4096 literals). Test: rot2_user_image_blocks_cost_tokens_d16. |
| D17 | LOW-MED | FIXED (W3+integrator, this PR) | request_tokens.rs module + estimate_context_tokens_with_request; ContextUsageEstimate carries system_prompt_tokens/tool_schema_tokens; parts added on the no-usage path only (usage-anchored path keeps provider totals to avoid double-count). Test: rot2_request_level_estimate_adds_system_prompt_and_tools_d17. |
| D18 | LOW | VERIFIED-OPEN, DEFERRED | compaction failure invisible to the model (agent_session.rs handle_auto_compaction_failure path); wave 3. |
| D19 | MED | VERIFIED-OPEN, DEFERRED | live-list producers without drain/cap (agent_session.rs 16736-16739 at audit time); W1 owns the file, not in its brief. |
| D20 | MED | VERIFIED-OPEN, DEFERRED | summarizer request vs model-visible request divergence (agent_session.rs 17910-17924 at audit time); needs the request-token work (W3) first. |

## 2. New findings this campaign (not in the iteration-1 ledger)

| # | finding | evidence |
| --- | --- | --- |
| N1 | v1 ledger evidence extraction misses the most common evidence shapes: `cargo test: N passed`, `build succeeded`, benchmark lines. EVIDENCE_RE (task_state.rs:45-47) matches only `test result:|error[E\d+]|N files changed|panicked at|FAILED`. | R2-E1: evidence 0/4 in the taskState arm. |
| N2 | v1 ledger is blind to custom entries: `entry_text` (task_state.rs:146-149) handles only `Message`, so subagent reports (agent_message custom entries) never reach the ledger. | R2-E1: subagent artifact 0/1 in the taskState arm. |
| N3 | both N1 and N2 are design constraints for typed_state v2 (W4): classify by role/provenance, not content regex. | brief requirement; R2-E1 measured the gap. |

## 3. Harness binary findings (W6 adapter validation, fixed by integrator)

| id | finding | status |
| --- | --- | --- |
| HF1 | SessionManager::create always started a NEW session file per invocation; per-case dirs forked sessions, breaking the design's delta-append mode | FIXED: continue_recent reopens the latest session |
| HF4 | zero usage anchors made the usage-anchored estimator read 0 tokens | FIXED: assistant turns carry chars/4 self-estimates |
| HF5 | appends flushed only after compaction; error-path exits lost turns | FIXED: flush_now after the append loop |
| HF2 | cut must immediately precede a user turn (unusable-handoff error otherwise) | documented; adapter uses a cut ladder |
| HF3 | tail sizing ~ chars/4 + 0.6 per message | documented; adapter mirrors it |
| HF6 | ledger lines carry per-session entry hashes; output deterministic modulo them | documented |
