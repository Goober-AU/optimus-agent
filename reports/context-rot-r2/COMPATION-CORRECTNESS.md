# COMPATION-CORRECTNESS — the D-defect ledger narrative for the r2 branch

Scope: what the r2 campaign's integrated branch fixes, what is still in
flight, and what remains open, narrated from the verified ledger and the
worker commits. Report writer W14, 2026-10-20. Sources:
`reports/context-rot-r2/BUG-LEDGER.md` (status vocabulary and evidence),
`reports/context-rot-r2/EXPERIMENT-LEDGER.md`, the W9 findings index in
`research/context_rot_r2/incremental/README.md`, and `git log`/`git show`
in this worktree. Base `6000d6bc1`; branch content = 23 commits
(`git rev-list --count 6000d6bc1..HEAD`).

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

## 2. Fixed on the worker branch, not yet integrated

The r2 ledger's "in flight (W2)" items are committed on branch
`rot2/w2-summarypath` but are NOT in this branch at report time
(`git rev-list --count HEAD..rot2/w2-summarypath` = 3):

| defect | commit (rot2/w2-summarypath) | change |
| --- | --- | --- |
| D5 | `1fda4ee7f` | skip no-op compaction when the summarize window is empty, regardless of `previous_summary`; no model call, no turn stop, no appended entry |
| D6 | `7aade7492` | `validate_extension_compaction_summary`: the same structural handoff check `validate_summary` enforces on model output, applied to extension-supplied summaries; the `agent_session.rs` call-site still needs wiring by the file owner |
| D9 | `0bd1b1888` | previous prose for the next summarizer comes from a dedicated walk that skips deadline-fallback markers and provider checkpoints, so fallback boilerplate no longer becomes `previous_summary` |

Each carries tests in `crates/pi-coding-agent/tests/compaction_summary_path.rs`
(new file on that branch). D14 (read-files inventory always empty) is also
listed "in flight (W2)" in BUG-LEDGER.md with no commit on the branch yet.

## 3. Open and deferred on this base

From `reports/context-rot-r2/BUG-LEDGER.md`:

- D7 (MED-HIGH), VERIFIED-OPEN, DEFERRED: a null or incompatible provider
  checkpoint makes the checkpointed compaction skip as boundary and
  pre-compaction messages replay (heavy fallback, not lossy). The
  recommended fix (honour the skipped entry's `firstKeptEntryId` as a
  truncation boundary while using the older summary for text) is deferred
  because it needs dedicated restore test coverage.
- D10 (MED), VERIFIED-OPEN, DEFERRED to wave 3, in flight (W13):
  `branch_summarization.rs` skips toolResult entries entirely, so branch
  summaries lose tool output. Branch `rot2/w13-branchsummary` currently
  has no commits beyond the integration head, so no fix is committed yet.
- D18 (LOW), VERIFIED-OPEN, DEFERRED: compaction failure is invisible to
  the model.
- D19 (MED), VERIFIED-OPEN, DEFERRED: two live-list producers have no
  drain or cap.
- D20 (MED), VERIFIED-OPEN, DEFERRED: the summarizer request diverges from
  the model-visible request; the ledger noted it needs the request-token
  work first - that estimator is now on this branch (`request_tokens.rs`),
  so D20 can be scheduled.
- D11, D12, D13 (MED): NEEDS-REVERIFICATION (stale digests, unfenced
  memory/harness text, memory recall failing closed on a foreign
  projectId).
- D4 (HIGH): CONFIRMED on the production path (R2-E1, default arm 0/19
  after 5 cycles). The v1 ledger mitigates it only when
  `taskState = "prompt"`, and the shipped default is `off`
  (`reports/context-rot/FINAL-REPORT.md` section 6). The durable fix is
  the typed-state v2 work (W4), not landed.

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
the typed_state v2 requirement (W4), not yet landed
(branch `rot2/w4-typedstate` has no commits beyond the base).

## 5. Guard map (fix -> test on this branch)

| fix | guard | file |
| --- | --- | --- |
| D1 | `d1_toggle_off_over_threshold_does_not_stop_the_turn` | `crates/pi-coding-agent/tests/compaction_turn_flow.rs` |
| D2 contract | `d2_interactive_turn_crossing_threshold_compacts_and_continues`, `d2_queued_user_prompt_completes_after_threshold_compaction` | same |
| D8 | `migration_keeps_a_durable_boundary_when_first_kept_index_is_out_of_bounds`, `migration_ignores_a_boundary_pointing_at_the_session_header` | `crates/pi-coding-agent/src/core/session_manager.rs` |
| D16 | `rot2_user_image_blocks_cost_tokens_d16`; `image_blocks_cost_4096_chars_1024_tokens` | `tests/rot2_multicycle.rs`; `src/core/compaction/request_tokens.rs` |
| D17 | `rot2_request_level_estimate_adds_system_prompt_and_tools_d17`; `message_list_estimates_understate_request_utilisation`; request_tokens unit tests | `tests/rot2_multicycle.rs`; `src/core/context_health.rs`; `src/core/compaction/request_tokens.rs` |
| harness F1/F4/F5 | reopen/anchor/flush behaviour verified by `probe_harness_behavior.py` and the adapter checks | `research/context_rot_r2/compactbench_adapter/` |

Execution state of these suites on this branch: REGRESSION-GATES.md.
