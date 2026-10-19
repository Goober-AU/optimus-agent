# COMPATION-CORRECTNESS — the D-defect ledger narrative for the r2 branch

Scope: what the r2 campaign's integrated branch fixes, what is still in
flight, and what remains open, narrated from the verified ledger and the
worker commits. Report writer W14, 2026-10-20, refreshed by W34 at the
integration tip `f8ad670c8` (64 commits over base). Sources:
`reports/context-rot-r2/BUG-LEDGER.md` (status vocabulary and evidence),
`reports/context-rot-r2/EXPERIMENT-LEDGER.md`, the W9 findings index in
`research/context_rot_r2/incremental/README.md`, and `git log`/`git show`
in this worktree. Base `6000d6bc1`.

Status vocabulary (from BUG-LEDGER.md): FIXED (behaviour changed and a test
covers it), VERIFIED-OPEN, NEEDS-REVERIFICATION, DEFERRED, in flight.

## 1. Fixed on this branch

### D1 (HIGH) - threshold stop hook versus executor

The turn-end hook `threshold_compaction_needed` gated on `settings.enabled`
alone while the executor `check_compaction` additionally required the
runtime `auto_compaction_enabled` toggle, so with compaction enabled in
settings but the runtime toggle off, every turn past the trigger was
stopped, the executor declined to compact, and nothing resumed the loop:
the session stalled mid-task with the tool result never answered.

Fix: commit `0a896e639` "fix(agent): align the threshold stop hook with the
compaction executor (D1)" (worker W1) extracts one shared predicate,
`threshold_auto_compaction_ready` (both enable flags, model,
compaction-timestamp fence, token estimate against the trigger, retry
cooldown), consulted by both the hook and the executor. Requested
compaction still runs with the toggle off; `set_auto_compaction_enabled`
keeps disabling automatic compaction but no longer strands a stopped turn.

Guard: `b6487b9cd` adds `crates/pi-coding-agent/tests/compaction_turn_flow.rs`
with the failing reproduction
`d1_toggle_off_over_threshold_does_not_stop_the_turn` through the public
session API on an offline `register_api_provider_simple` fixture.

### D2 (HIGH) - stopped turn not resumed: verified unfixed-by-necessity, now guarded

The interactive continuation contract was audited and found intact on this
base: a mid-task turn resumes through the existing `!last_is_assistant`
contract and a prompt queued while the crossing turn streams resumes
through `has_pending_session_work`. Both paths are now pinned by regression
tests in the same file:
`d2_interactive_turn_crossing_threshold_compacts_and_continues` (one
compaction, exactly one further provider request) and
`d2_queued_user_prompt_completes_after_threshold_compaction`
(`b6487b9cd`, commit message "D2 (guards)").

### D8 (MED-HIGH) - migration can drop the compaction boundary permanently

`migrate_v1_to_v2` dropped `firstKeptEntryIndex` without writing
`firstKeptEntryId` when the index was out of bounds or pointed at the
session header, so `prepare_compaction` re-summarized the retained region
on every later compaction.

Fix: commit `a56631631` "fix(session): keep a durable compaction boundary
when legacy index cannot resolve" (integrator) falls back to the first
entry after the compaction - the durable equivalent of the runtime
fallback. Two regression tests cover the out-of-bounds and header cases.

### D16 (MED) - user-attached images counted as zero tokens

`estimate_tokens` charged user image blocks zero while `find_cut_point`
charged 4096 chars, so the two estimators disagreed.

Fix (worker W3 + integrator): `fa4ccc8f2` adds the shared convention
`IMAGE_BLOCK_CHARS` (4096 chars = 1024 tokens at chars/4) in the new
`crates/pi-coding-agent/src/core/compaction/request_tokens.rs`;
`0bb973e43` applies it to the estimator paths so user image blocks now
cost `IMAGE_BLOCK_TOKENS` and custom/tool-result image costs share the
constant. Guards: `rot2_user_image_blocks_cost_tokens_d16`
(`crates/pi-coding-agent/tests/rot2_multicycle.rs`) and
`image_blocks_cost_4096_chars_1024_tokens`
(`src/core/compaction/request_tokens.rs`).

### D17 (LOW-MED) - token estimate excludes the system prompt and tool schemas

Fix (worker W3 + integrator): `fa4ccc8f2` adds the request-level
estimator `estimate_request_tokens(messages, system_prompt,
tool_definitions)` returning `RequestTokenEstimate` with
`messages_tokens`, `system_prompt_tokens`, `tool_schema_tokens`, `total`;
the message part delegates to `estimate_tokens` per message so the two
estimators cannot drift. `0bb973e43` applies it to the estimator paths:
`ContextUsageEstimate` now carries the system-prompt and tool-schema
parts, and `estimate_context_tokens_with_request` adds those parts on the
no-usage fallback path only (provider-reported usage already counts them,
so the usage-anchored path keeps zero parts to avoid double-counting).
`2200211fe` documents the tray's inherited underestimate on
`ContextHealthInput::tokens` and pins the consequence in
`message_list_estimates_understate_request_utilisation`
(`src/core/context_health.rs`): feeding the request-level total instead
of the message-list estimate raises utilisation from 0.72 to 0.80 and
drops the score from 72.25 to 66.25, with no scoring-semantics change.
Guards: `rot2_request_level_estimate_adds_system_prompt_and_tools_d17`
(`tests/rot2_multicycle.rs`) plus the request_tokens unit tests
(`empty_inputs_score_zero` ... `total_is_exactly_the_sum_of_its_parts`).

### Harness F1/F4/F5 - the CompactBench harness defects

Found by W6's adapter validation and fixed by `49f473d8d` "fix(compaction):
harness session persistence, usage anchors and flush-on-append" in
`crates/pi-coding-agent/src/bin/compaction_harness.rs`:

- F1: `SessionManager::create` always started a new session file, so
  per-case dirs forked a fresh session per invocation and the design's
  delta-append mode was unimplementable. `continue_recent` now reopens the
  latest session in the dir (falling back to a new one).
- F4: zero usage anchors made the usage-anchored estimator read 0 tokens;
  assistant turns now carry a conservative chars/4 self-estimate as their
  usage anchor.
- F5: appended turns were only flushed after compaction, so an error-path
  exit could leave a partially-written session next to a complete
  turn-map sidecar; `flush_now` now runs after the append loop.

F2 (the pipeline only accepts cuts that immediately precede a user turn)
and F3 (tail sizing is ~chars/4 + 0.6 per message) are accommodated in the
adapter's cut-point ladder rather than changed in the harness; F6 (ledger
lines carry per-session entry hashes) is inherent and masked by the
adapter's determinism test
(`research/context_rot_r2/compactbench_adapter/README.md`, "Harness
findings").

### D5/D9/D14 (W2) and the D6 call site - integrated since the first edition

The items this report first carried as "fixed on the worker branch, not yet
integrated" (`rot2/w2-summarypath`, worker commits `1fda4ee7f` D5,
`7aade7492` D6 validator, `0bd1b1888` D9) are integrated on this branch as
`3b08e3f2e` (D5), `289f3c2e1` (D6 validator) and `720428967` (D9), each with
guards in `crates/pi-coding-agent/tests/compaction_summary_path.rs`; D14
followed as `439808412`. D6's `agent_session.rs` call site - the remaining
step at the first edition - is wired by `6e1c3a139` (the extension
compaction arm applies the validator; the commit also drops unused harness
settings).

## 2. Fixed since this report's first edition (W11-W25 integrations)

Everything the first edition listed as in flight has landed on the
integration branch, plus the fixes below. All are behaviour changes with
regression guards; the guard names are in section 5 and
FINAL-REPORT.md section 4.1.

| defect | integrated commit(s) | change |
| --- | --- | --- |
| D10 branch summaries lose tool output (W13) | `0e25a7db1` | tool results pair with their assistant calls, evidence-aware excerpt, budget walk keeps the call of a kept result ("never retry" sub-item still open); `tests/rot2_branch_summary.rs` |
| D12 memory-recall text unfenced | `021678796` | `MEMORY_RECALL_TYPE` custom text fenced as `<memory_data>` at `convert_to_llm` time (soft-fence residual documented); partial fix |
| D13 recall fails closed silently | `157316268` | fail-closed surfaces emit rate-limited warnings with counts; guards in `src/core/memory/search.rs` |
| W9-F2/F4/F5 v1 ledger ids and supersession (W11) | `1754a3e1b` | content-derived ids, correction carriers excluded as supersession targets, newest correction wins the same target; `tests/rot2_ledger_fixes.rs`. Offline-verified on the R2-E2 live corpus by R2-E2-RM (`2fbb14f33`): the inversion is removed, the render cap (D21) is the residual |
| D11 stale harness digests (W25) | `30588707d` + `d9ceea230` | prune superseded digest user turns from the provider context; `tests/rot2_harness_digest.rs` pins one live digest after k harness changes |
| D6 call site | `6e1c3a139` | see section 1 |

The typed-state work the first edition listed as "not yet landed" is now
shipped and wired: module `c948905a3` (see FINAL-REPORT.md section 4.2),
integration `338b920c3` + `40bead288` + `4be9623b0` (settings, kernel,
verifier, budget; `tests/rot2_typed_integration.rs`).

## 3. Open and deferred at the W34 refresh tip

From `reports/context-rot-r2/BUG-LEDGER.md` and the post-W22 findings:

- D7 (MED-HIGH), VERIFIED-OPEN, DEFERRED: a null or incompatible provider
  checkpoint makes the checkpointed compaction skip as boundary and
  pre-compaction messages replay (heavy fallback, not lossy). The
  recommended fix (honour the skipped entry's `firstKeptEntryId` as a
  truncation boundary while using the older summary for text) is deferred
  because it needs dedicated restore test coverage.
- D18 (LOW), VERIFIED-OPEN: compaction failure is invisible to the model.
- D19 (MED), VERIFIED-OPEN: two live-list producers have no drain or cap.
- D20 (MED), VERIFIED-OPEN: the summarizer request diverges from the
  model-visible request; the request-level estimator it needed is on this
  branch (`request_tokens.rs`). D18/D19/D20 recovery is assigned to W30;
  `rot2/w18-defects` has no commits over this head at this writing.
- D21 (new from R2-E2-RM; severity not yet assigned in BUG-LEDGER.md):
  with the W11 churn gone, CONSTRAINTS_CAP=12
  omits authoritative corrections from the rendered ACTIVE list of the v1
  ledger (they appear only as SUPERSEDED replacement texts). Found by the
  offline re-measure on the hard corpus (`2fbb14f33`,
  `research/context_rot_r2/live/remeasure/README.md` finding 2); fix in
  flight as W32, drafted on `rot2/w28-d21` (`ba26fb120` exempt correction
  carriers from the ledger render caps, `59d870a02` the replay
  re-measure).
- D4 (HIGH): CONFIRMED on the production path (R2-E1, default arm 0/19
  after 5 cycles). The v1 ledger mitigates it only when
  `taskState = "prompt"`, and the shipped default is `off`
  (`reports/context-rot/FINAL-REPORT.md` section 6). The durable fix,
  typed-state v2, is now shipped AND wired as an opt-in mode (section 2);
  its decisive live measurement (R2-E5, W29) is still in flight.
- D10's "never retry" sub-item (an assistant call whose kept tool result
  must not be retried) remains open after `0e25a7db1`.

Resolved since the first edition: D10 (fixed, `0e25a7db1`), D11 (fixed,
`30588707d`), D12 (partially fixed, `021678796`), D13 (fixed, `157316268`),
the W9-F2/F4/F5 ledger mechanisms (fixed, `1754a3e1b`, offline-verified by
R2-E2-RM) and the D6 call site (`6e1c3a139`).

## 4. New defects found this campaign

Ledger section 2 (`reports/context-rot-r2/BUG-LEDGER.md`):

- N1: the v1 evidence extraction misses the most common evidence shapes -
  `EVIDENCE_RE` (`task_state.rs`) matches only
  `test result:|error[E\d+]|N files changed|panicked at|FAILED`, so
  `cargo test: N passed; 0 failed`, `build succeeded` and benchmark lines
  never become evidence. Measured: evidence 0/4 in the taskState arm
  (R2-E1).
- N2: the v1 ledger is blind to custom entries - `entry_text` handles
  only `Message`, so subagent reports (`agent_message` custom entries)
  never reach the ledger. Measured: subagent artifact 0/1 (R2-E1).
- N3: N1 and N2 are design constraints for typed_state v2 (W4): classify
  by role/provenance, not content regex.

W9 findings (`research/context_rot_r2/incremental/README.md`, "Findings
index", each verified against a rendered ledger):

- W9-F1: constraint capture depends on sibling text (`is_correction` marks
  the whole message), so isolated keyword-less constraints are invisible.
- W9-F2: auto-renumbered ledger ids are unstable across merges; the
  blanket `retain(!retired.contains(id))` silently deletes newly captured
  items that reuse a retired id, making retention seed- and
  placement-dependent (seed b loses q-constraint-3 this way).
- W9-F3: mutual retirement demotes constraints to SUPERSEDED (history)
  while anchor scoring counts them retained; baseline retention
  overstates current-state correctness.
- W9-F4: supersession misfire - the two corrections retire each other
  instead of the old decision; "forget that" is not honored, old and new
  coexist as current decisions.
- W9-F5: late supersession loses one more constraint anchor than early
  (id renumbering at the merge boundary, W9-F2 again).

N1/N2 and W9-F1/F2/F4 all point at the same conclusion the experiments
reached: capture must be role/provenance-based with stable ids, which is
the typed_state v2 requirement (W4). That module landed as `c948905a3`
(`rot2/w4-typedstate` content) and is wired end to end as an opt-in mode
(`338b920c3`, `40bead288`, `4be9623b0`); the pinned boundary is that
custom `agent_message` entries are still not classified, so subagent
reports are not retained as SUBAGENT_STATE by the committed classifier
(FINAL-REPORT.md section 4.2).

## 5. Guard map (fix -> test on this branch)

| fix | guard | file |
| --- | --- | --- |
| D1 | `d1_toggle_off_over_threshold_does_not_stop_the_turn` | `crates/pi-coding-agent/tests/compaction_turn_flow.rs` |
| D2 contract | `d2_interactive_turn_crossing_threshold_compacts_and_continues`, `d2_queued_user_prompt_completes_after_threshold_compaction` | same |
| D5/D6/D9/D14 | the D5 no-op cases, `extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries`, the D9 walk cases, the D14 read-inventory cases | `crates/pi-coding-agent/tests/compaction_summary_path.rs` |
| D8 | `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds`, `migration_ignores_a_boundary_pointing_at_the_session_header` | `crates/pi-coding-agent/src/core/session_manager.rs` |
| D10 | `branch_summary_input_carries_the_failing_tool_result`, `branch_summary_input_skips_tool_results_with_no_text` | `crates/pi-coding-agent/tests/rot2_branch_summary.rs` |
| D11 | `k_harness_changes_leave_exactly_one_digest_in_the_provider_context`, `compaction_keeps_the_pruned_provider_context_digest_clean` | `crates/pi-coding-agent/tests/rot2_harness_digest.rs` |
| D12/D13 | `convert_to_llm_fences_memory_recall_text_but_not_other_custom_types`; `foreign_only_corpus_fails_closed_and_surfaces_the_reason`, `budget_short_skip_fails_closed_and_surfaces_the_reason`, `partial_recall_skips_stay_silent` | `crates/pi-coding-agent/src/core/messages.rs`; `crates/pi-coding-agent/src/core/memory/search.rs` |
| D16 | `rot2_user_image_blocks_cost_tokens_d16`; `image_blocks_cost_4096_chars_1024_tokens` | `tests/rot2_multicycle.rs`; `src/core/compaction/request_tokens.rs` |
| D17 | `rot2_request_level_estimate_adds_system_prompt_and_tools_d17`; `message_list_estimates_understate_request_utilisation`; request_tokens unit tests | `tests/rot2_multicycle.rs`; `src/core/context_health.rs`; `src/core/compaction/request_tokens.rs` |
| W9-F2/F4/F5 (W11) | the seven `rot2_ledger_fixes` cases (corrections supersede older decisions, retired-id reuse survives, content-derived ids stable across merges) | `crates/pi-coding-agent/tests/rot2_ledger_fixes.rs` |
| typed state v2 wiring | the nine `rot2_typed_integration` cases (mode resolution, budget formula, VERIFIED_TOOL_RESULT/SUBAGENT_STATE capture, protected-block round-trip, unsafe allocation, repair, superseded-as-current fails verification, v1 interop) | `crates/pi-coding-agent/tests/rot2_typed_integration.rs` |
| harness F1/F4/F5 | reopen/anchor/flush behaviour verified by `probe_harness_behavior.py` and the adapter checks | `research/context_rot_r2/compactbench_adapter/` |

Execution state of these suites on this branch: REGRESSION-GATES.md (the
W14 record at `0ec43c8cb`; the post-`4b0add723` commits have no recorded
full gate pass - FINAL-REPORT.md section 5.2).
