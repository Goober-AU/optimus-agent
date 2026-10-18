# Bug Ledger - Full-Harness Round 2

Severity: P0 data loss/security/uncontrolled execution, P1 critical user-visible failure,
P2 functional defect, P3 performance/non-critical reliability, P4 cosmetic.

Every entry names the reproduction, the root cause, the fix, and the test that now guards it.
Entries marked OPEN are documented with an exact blocker instead of a fix.

| ID | Sev | Subsystem | State | Summary |
|----|-----|-----------|-------|---------|
| CORE-R2-1 | P2 | agent loop | FIXED | Terminal provider error/abort left assistant tool calls unanswered |
| CORE-R2-2 | P3 | agent loop | FIXED | Cancellation before a tool started recorded provider abort text |
| MEM-R2-1 | P2 | memory store | FIXED | `memory restore` could not recover a corrupt authoritative store |
| MEM-R2-2 | P3 | memory store | FIXED | `memory configure` destroyed an unparseable settings file |
| MEM-R2-3 | P4 | memory store | FIXED | memory apply could write `null` over the authoritative store |
| OCC-R2-1 | P2 | daemon/OCC | FIXED | OCC-2 negotiation ignored name-addressed receivers |
| BASH-R2-1 | P2 | bash tool | FIXED | Overflowing/non-finite JSON timeout panicked the tool call |
| EXEC-R2-1 | P3 | core::exec | OPEN | `exec_command` awaits pipe readers without a bound after child exit (no production callers) |
| BASH-R2-2 | P4 | python bash helper | OPEN (documented) | Windows job containment kills background descendants; POSIX lets them outlive the call |

## CORE-R2-1 (P2) - a terminal provider error/abort left assistant tool calls unanswered

- File: `crates/pi-agent-core/src/agent_loop.rs`, terminal branch of `run_loop`.
- Repro: a stream that emits `Start`, `ToolCallStart`, `ToolCallEnd`, then `Error{reason}` whose
  terminal message carries the same tool call. Before the fix, `TurnEnd.tool_results` was empty
  and the persisted history ended with `assistant[ToolCall(id)]` and no `toolResult(id)`.
- Impact: every provider that requires one result per tool call gets an invalid next request;
  `agent_loop_continue` refuses the context ("Cannot continue from message role: assistant").
- Fix: `answer_unexecuted_tool_calls(...)` synthesizes the dedicated abort result for every
  unanswered call in the terminal message, pushes them into the context and `new_messages`,
  and reports them in `TurnEnd.tool_results`. The sequential-cancel path now shares the helper.
- Tests: `terminal_error_and_abort_answer_every_tool_call`,
  `terminal_error_leaves_a_context_that_can_continue_without_rerunning_tools`.

## CORE-R2-2 (P3) - cancellation before a tool started used the provider abort text

- File: `crates/pi-agent-core/src/agent_loop.rs`, `execute_prepared_tool_call` pre-check.
- Repro: a parallel batch whose signal is already cancelled answers the call with
  `"Request was aborted"` (provider wording) instead of `"Tool execution aborted"`.
- Fix: the pre-check answers with `TOOL_EXECUTION_ABORTED_MESSAGE`, matching the two sibling
  abort paths fixed in round 1.
- Test: `cancellation_before_tool_start_uses_the_dedicated_abort_wording`.

## MEM-R2-1 (P2) - `memory restore` could not recover a corrupt authoritative store

- File: `crates/pi-coding-agent/src/core/memory/store.rs`, `MemoryStore::restore`.
- Repro: `backup(None)`, corrupt `harness_state.json`, `restore(&backup)` -> error, backup never
  applied. `restore` read the current store first, and `read()` fails closed on corruption.
- Impact: the documented recovery command was useless exactly when it was needed.
- Fix: on a read failure, `salvage_damaged_state()` quarantines the damaged bytes to a
  content-addressed copy (fail-closed: a failed quarantine aborts before replacing the file)
  and salvages the revision and delivery receipts when they still parse.
- Tests: `restore_recovers_a_damaged_authoritative_store_and_keeps_its_bytes`,
  `restore_recovers_a_truncated_store_without_salvaging_garbage`.

## MEM-R2-2 (P3) - `memory configure` destroyed an unparseable settings file

- File: `store.rs`, `MemoryStore::configure`.
- Repro: write a truncated `settings.json`, call `configure({...})`: the merge base became `{}`
  and the patched write replaced every other key with no copy and no warning.
- Fix: `quarantine_settings_file()` preserves the bytes under
  `settings.corrupt-<sha256[..16]>.json` and logs both paths before the patch writes.
- Test: `configure_preserves_an_invalid_settings_file_before_replacing_it`.

## MEM-R2-3 (P4) - apply could write `null`

- File: `store.rs`, `MemoryStore::apply`. `to_value(&candidate).unwrap_or(Value::Null)` would
  replace the store with an unvalidatable document on a serialization failure. Unreachable
  from the public API today; hardened to abort the apply and keep the previous state.

## OCC-R2-1 (P2) - OCC-2 negotiation skipped name-addressed receivers

- File: `crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs`, `occ2_send_policy`.
- Detail: the send surface resolves a receiver by session name, active session id or persistent
  session id; the daemon seam matched only the two ids, so a name-addressed send silently stayed
  OCC-1 even though `/occ` reported ON.
- Fix: one shared `occ2_peer_entry(&agents, target)` helper matching all three selector forms.
- Test: `occ2_peer_selection_tests::peer_lookup_accepts_the_selectors_the_send_surface_resolves`.

## BASH-R2-1 (P2) - an absurd JSON timeout panicked the tool call

- File: `crates/pi-coding-agent/src/core/tools/bash.rs`, `LocalBashOperations::exec`.
- Repro: `{"timeout": 1e400}` (serde_json reads it as +inf) or `1e300` reached
  `Duration::from_secs_f64`, which panics on overflow.
- Fix: `effective_timeout_seconds()` maps non-finite and `<= 0` to "no timeout" and clamps finite
  values to `MAX_TIMEOUT_SECONDS` (one week); the executor arms `Duration::try_from_secs_f64` so
  overflow is impossible; the timeout error and the rendered call line report the armed value.
- Tests: `real_absurd_timeout_values_do_not_panic`, `absurd_timeouts_render_the_effective_value`,
  `a_json_timeout_overflow_never_becomes_finite`, plus a real 2 s timeout that must fire.

## EXEC-R2-1 (P3, OPEN) - unbounded pipe-drain wait in `core::exec::exec_command`

- File: `crates/pi-coding-agent/src/core/exec.rs`. After the child exits, the readers are awaited
  without a deadline, so a descendant that keeps the pipe open blocks the call. No production
  caller passes a command that leaves descendants; the TypeScript `exec.ts` has the same shape.
  Documented rather than changed: bounding it changes documented semantics for callers that
  legitimately read a pipe written by a child that outlives the parent.

## BASH-R2-2 (P4, documented divergence) - Windows containment vs POSIX background children

- The Python async helper on Windows terminates the job, so background descendants do not
  outlive the call; on POSIX they can. This is a deliberate containment choice, recorded so it
  is not mistaken for a behavioural guarantee across platforms.

## NODE-R2-1 (P2) - a deferred error in a Node cell destroyed the whole workspace

- File: `crates/pi-coding-agent/src/core/tools/node_worker.cjs`, `core/tools/node.rs`.
- Repro (real node v24.16.0): cell 1 runs `setTimeout(() => { throw new Error('late boom'); }, 20)`
  and returns; cell 2 then fails with
  `The pipe is being closed. (os error 232). Node workspace reset; previous JavaScript variables are no longer available.`
- Impact: work that a previous cell left running is normal JavaScript (timers, listeners, floating
  promises). One throw from it silently discarded every binding the session held.
- Fix: the worker installs `uncaughtException` and `unhandledRejection` handlers that emit a
  `workspace-notice` frame instead of exiting. The Rust side buffers notices while it waits for the
  frame that belongs to its own request, renders a bounded notice block on the next result
  (2000-byte worker cap, 2000-byte render cap, at most 16 notices), reports them in
  `details.backgroundNotices`, and keeps the cell successful because the error did not belong to it.
- Tests: `deferred_async_error_from_one_cell_does_not_break_later_cells`,
  `background_notices_are_bounded_and_never_fail_the_cell`.

## NODE-R2-2 (P3) - `process.exit` was reported as an oversized response

- File: `crates/pi-coding-agent/src/core/tools/node.rs`.
- Repro: `process.exit(3)` reported
  `Node workspace exited or returned an oversized response.`
- Impact: an ordinary exit was indistinguishable from a framing error, so the reader could not tell
  a user-requested exit from a protocol desync.
- Fix: a bounded frame read classifies three cases: a terminated line that parses as JSON is a
  frame, a full buffer with no terminator is oversized, and a short read asks for the exit status.
  The message now names the exit: `Node workspace exited with status 3`.
- Test: `process_exit_reports_the_exit_and_the_next_cell_recovers`.

## Recommendation R1 (harness) - interpreter-gated tests fail instead of skipping

Sixteen `pi-coding-agent` lib tests (`kernel_settlement_*`, `kernel_job::containment_tests::*`,
`retained_stop_real_python_tool_*`) spawn a real Python kernel from an explicit fixture
interpreter. With `KERNEL_CONTAINMENT_TEST_PYTHON` unset they panic with `NotPresent` rather than
skipping, so a plain `cargo test --workspace` on a machine without the fixture reports 16 failures
that look like product defects. On this host all sixteen pass once the variable names a Python with
`dill`. Recommendation: return early with a printed skip reason instead of panicking.

## Recommendation R2 (harness) - the Python runtime suite needs the project environment

`python -m unittest discover -s prime-agent-runtime/test -p 'test_*.py'` with `PYTHONPATH=src`
fails 91 errors because every kernel fixture dies in `setUp` with
`EOFError: runtime closed its protocol stream`. The suite must run under
`uv run --locked --project prime-agent-runtime`. Recorded so a future reviewer does not read the
bare-interpreter run as 91 product defects.
