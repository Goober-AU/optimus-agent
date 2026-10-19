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

## 4. W35 red-team findings (typed-state v2 adversarial review, rot2/w35-redteam)

All findings are present on this base (integration tip f8ad670c8, typed commits
338b920c3/40bead288/4be9623b0). No production fixes applied (report only);
each defect has a failing-first adversarial test in
crates/pi-coding-agent/tests/rot2_typed_adversarial.rs carrying an
`#[ignore = "Dnn: ..."]` note, runnable red with `cargo test -- --ignored`.
Status vocabulary as above; all are VERIFIED-OPEN pending integrator routing.

| # | severity | status r2 | finding |
| --- | --- | --- | --- |
| D22 | MED | VERIFIED-OPEN | `typed_state::record_line` (typed_state.rs:1318) matches the `UserCorrection \| CurrentTask` arm before the `status == Superseded` arm, so a superseded correction or task renders `(supersedes [X])` with X = the record that retired it: the retirement direction is inverted. The render is what the summarizer copies (tier-8 SUPERSEDED section, `render_hot_state`), teaching the model that the retired record supersedes its replacer. Red: `superseded_corrections_and_tasks_render_the_retirement_direction` renders `- [CO-6947ab] [@u2] Correction: ... (supersedes [CO-be885a])` for a record that CO-be885a retired. |
| D23 | MED | VERIFIED-OPEN | User text containing a bracketed ticket-style token (`[JX-4f1c2a]`: two capitals, dash, six lowercase hex; also entry-shaped `[a-z]+\d+` like `[issue42]`) collides with the verifier's reference grammar (typed_state.rs:1455, 1573). The summarizer is instructed to copy constraints verbatim, so the summary carries the token; `verify` reports an unresolved reference, the finding is not a missing item, so the bounded repair does not apply, and the episode fails (`...not represented: 0 missing, 0 mis-superseded, 1 unresolved references...`). Fail-closed, but compaction is permanently blocked while the record lives in the ledger — and per D25 it never leaves. Red: `bracketed_ticket_ids_in_user_text_fail_typed_verification`. |
| D24 | MED | VERIFIED-OPEN | Tool-derived records keep the latest text on restatement (typed_state.rs:969) but the id is recomputed from the current text at the next reconcile (typed_state.rs:977), so an evolving repository/subagent/evidence record changes its id across cycles, violating the documented "same fact ... resolves to the same id" contract. Red: `evolving_tool_state_records_keep_a_stable_id_across_merges` shows `RS-920746` becoming `RS-1cdf29`. Consequence: `[id]` references carried by earlier summaries go stale; a later model faithfully copying a previous summary's refs fails verification (unresolved, unrepairable). |
| D25 | MED-HIGH | VERIFIED-OPEN | `RecordScope` is decorative: `scope()` is assigned (typed_state.rs:201) but never read by `reconcile`, `allocate` or `merge`. Step-scoped `PENDING_ACTION`s stay CURRENT forever (red: 4 distinct pending actions, all CURRENT, after 4 merges) and are `required_present` (typed_state.rs:1461) in every later summary; the missing-item repair re-appends them unbudgeted. Both the durable ledger (persisted as `details.typedState` each cycle) and every summary grow without bound, and accumulated mandatory material eventually exceeds the 2048-token hot-state budget, turning compaction into a permanent unsafe-allocation failure. Long sessions degrade over cycles. Red: `pending_actions_do_not_accumulate_forever_across_merges`. |
| D26 | MED | VERIFIED-OPEN | A `details.typedState` payload that fails to deserialize is silently dropped: prepare_compaction (compaction.rs:2060) reads it through `.and_then(...ok())`, so every durable record below that boundary vanishes without a warning and the ledger rebuilds from the current window only. Red: one malformed field (`ordinal: "zero"`) drops the cycle-1 constraint entirely (`malformed_typed_state_payload_silently_drops_durable_records`). Schema drift between versions or a partially written JSONL line silently discards protected state. |
| D27 | LOW | VERIFIED-OPEN | The fabricated-verified-claim check exempts all-caps lines: `is_heading_line` (typed_state.rs:1625) treats `trimmed == trimmed.to_uppercase()` as a heading, so a summary line `ALL TESTS VERIFIED PASSING` with no reference evades the check (the heuristic exists to skip rendered headings like `VERIFIED EVIDENCE`). Red: `all_caps_verified_claims_cannot_evade_the_fabricated_check`. |
| D28 | LOW-MED | VERIFIED-OPEN (code-cited, no test; native fixture outside this worker's file map) | The native provider-compaction path returns early with `typed_state: None` (compaction.rs:2936-2941, "v1 parity"). With a compaction-capable provider (`supports_compaction`), one native compaction silently resets the typed durable chain: the next cycle merges `previous = None`, and everything below that boundary is lost from the ledger (it survives only as prose in the server summary). The typed chain is the feature's core claim, so the reset is more significant than its v1 counterpart. |

Observations recorded without D entries (see the W35 report): the starved-budget
error path persists nothing, so `details.typedVerification` cannot record it
(the failure surfaces only through the error channel, the pre-existing D18
pattern); subagent-state lines clip at `ITEM_TEXT_MAX` (320) while the module
doc promises 200 for tool-derived lines; `format_file_operations` output is
appended to the summary after verification, so the persisted text is not
byte-identical to the verified artifact.
| D29 | MED-HIGH | OPEN (W29 live evidence) | typed_state supersession pairing is token-overlap-based, not provenance-based: a zero-overlap correction (E3ADV C1) scores 0, the stale decision stays CURRENT, and no SUPERSEDED section is ever rendered (verified in all 6 typed summarizer requests; GLM reconciled in its own words). The campaign's structural fix for the no-anchor adversarial case is NOT achieved; the pairing function needs role/provenance-aware retirement (a user correction retires an older decision when their provenance chains relate, not when their tokens overlap). Evidence: research/context_rot_r2/live/e5/live/e3adv-typed-c1-results.json + e5/README.md Part 3. |
