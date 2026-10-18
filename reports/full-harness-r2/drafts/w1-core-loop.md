# W1 - Core agent loop, streaming lifecycle and tool dispatch

Area owner: w1-core-loop (round 2). Target dir: C:/Users/openclawuser/optimus-fh2-target-w1.
Every claim below is backed by a recorded command and its result log in this directory.
Worktree: C:/Users/openclawuser/optimus-fh2-20261018, base 5feb47596 (v0.1.35 + PR #174).

## 1. Work items

| # | Item | Where | Status |
|---|------|-------|--------|
| C1 | Tool-call/result contract on a terminal provider `error`/`aborted` (goal 2) | agent_loop.rs `run_loop` | DEFECT found + fixed + test |
| C2 | Cancellation that lands before a tool starts leaked the provider abort text (goal 3/2 wording contract) | agent_loop.rs `execute_prepared_tool_call` | DEFECT found + fixed + test |
| C3 | Full sequence request -> response -> tool -> result -> continuation -> final answer (goal 1) | new event-order test | VERIFIED |
| C4 | Sequential/parallel dispatch semantics: ordering, identity, one result per call (goal 4) | existing round-1 suite re-run | VERIFIED |
| C5 | Execution-scope pruning cannot drop in-flight entries or resurrect settled ones (goal 5) | execution_scope.rs | VERIFIED (existing tests) + analysis |
| C6 | Per-tool dispatch overhead / multi-tool latency (goal 6) | new ignored measurement test | MEASURED, no optimisation kept |
| C7 | PR #174 streaming metric hooks around the loop (streaming_metrics_tests.rs) | agent_loop.rs | VERIFIED (no new defect reproduced) |

## 2. Environment and baseline

- Host: shared; all cargo commands used `-j 2` and `CARGO_TARGET_DIR=C:/Users/openclawuser/optimus-fh2-target-w1`.
- Shell prelude: per BRIEF.md (RUSTUP_HOME/CARGO_HOME/PATH exported, then `cd` worktree).
- `cargo check --locked -p pi-agent-core --all-targets -j 2` -> `Finished dev profile in 9m 22s`,
  pre-existing warnings only (no errors).
- Pre-change baseline (the round-1 state, nothing of mine applied):
  `cargo test --locked -p pi-agent-core --lib -- --test-threads=1`
  -> `test result: ok. 99 passed; 0 failed`, EXIT=0 (log: `_w1-baseline-lib.log`).

## 3. Defects

### CORE-R2-1 (P2) - a terminal provider `error`/`aborted` left assistant tool calls with no tool result

- File: `crates/pi-agent-core/src/agent_loop.rs`, `run_loop` terminal branch
  (`if message.stop_reason == STOP_REASON_ERROR || ... STOP_REASON_ABORTED`).
- Behaviour before: the branch emitted `TurnEnd { tool_results: Vec::new() }` and `AgentEnd`
  and returned `Ok(())`, without answering any tool call the terminal message carried.
- Root cause: the branch treated a terminal failure as "no tool work". But a provider can
  deliver a *complete* tool call (or a truncated one) in the message that ends the stream:
  a real transport dies after the tool-call block, the fake/real `Error` event carries that
  partial, and `create_aborted_assistant_message` deliberately keeps partial content
  (`clone_assistant_content`) on an abort. Nothing answers those calls, so the persisted
  history ends with `assistant[ToolCall(id)]` and no `toolResult(id)`.
- Why it matters: the next request is protocol-invalid for every provider that requires one
  result per tool call, and `agent_loop_continue` refuses the context outright
  ("Cannot continue from message role: assistant"). Round 1 fixed exactly this class of
  dangling call for the sequential-cancel path; the terminal-failure path was missed.
- Reproducer (deterministic, offline, no provider): a stream function that emits
  `Start`, `ToolCallStart`, `ToolCallEnd` and then `Error { reason: error|aborted, error:
  terminal }` where the terminal message carries the same tool call, driven through the real
  `agent_loop` entry point.
- Failing assertion before the fix (`_w1-red.log`):
  `assertion left == right failed: a terminal error must answer every tool call it carries`
  `left: []  right: ["call-1"]`.
- Fix: new helper `answer_unexecuted_tool_calls(...)` synthesizes the same dedicated abort
  result the parallel path produces for every unanswered call (dedicated wording
  `TOOL_EXECUTION_ABORTED_MESSAGE`, `is_error = true`, no `tool_execution_start`/`end` pair,
  tool-result message events still emitted, one bounded Tool performance metric). The
  terminal branch now answers before `TurnEnd`, pushes the results into the context and
  `new_messages`, and reports them through `TurnEnd.tool_results`.
  The sequential-cancel path was refactored onto the same helper (behaviour unchanged: the
  synthesized entries never requested batch termination, so `should_terminate_tool_batch`
  is identical).
- Tests added (`agent_loop.rs`, `mod tests`):
  - `terminal_error_and_abort_answer_every_tool_call` (both stop reasons; ids, wording,
    `is_error`, tail role, `TurnEnd.tool_results`).
  - `terminal_error_leaves_a_context_that_can_continue_without_rerunning_tools` (recovery:
    `run_agent_loop_continue` accepts the context and never re-runs the aborted tool).

### CORE-R2-2 (P3) - cancellation before a tool starts used the provider-request abort text

- File: `crates/pi-agent-core/src/agent_loop.rs`, `execute_prepared_tool_call`, the
  `throw_if_aborted(signal)` pre-check.
- Behaviour before: `create_error_tool_result(&format!("{error}"))`, i.e. the text
  `"Request was aborted"` (`ABORT_ERROR_MESSAGE`, the provider-request wording) became the
  persisted tool-result text.
- Root cause: the pre-check predates the round-1 wording split. `TOOL_EXECUTION_ABORTED_MESSAGE`
  is documented as covering "whether mid-execution, mid-before-hook, or before it started",
  and the two sibling paths (before-hook abort, mid-execution abort) were fixed in round 1;
  this third path still leaked the raw abort display into history.
- Reachable from the real loop: a `before_tool_call` hook that cancels the run and returns
  normally leaves the batch admitted with a cancelled signal, so the tool's own pre-check
  is what answers the call. (Sequential mode usually breaks before the call; parallel mode
  always reaches the pre-check.)
- Failing assertion before the fix (`_w1-red.log`):
  `left: Some("Request was aborted")  right: Some("Tool execution aborted")`.
- Fix: the pre-check now answers with `TOOL_EXECUTION_ABORTED_MESSAGE`.
- Test added: `cancellation_before_tool_start_uses_the_dedicated_abort_wording` (also
  asserts the tool body never ran and that the call is still answered).

## 4. Verified properties (no defect found)

- C3 full sequence: `full_turn_sequence_emits_request_response_tool_result_and_final_answer`
  asserts the exact emitted event order
  `agent_start, turn_start, user_start, user_end, assistant_start, assistant_end,
   tool_start, tool_end, tool_result_start, tool_result_end, turn_end, turn_start,
   assistant_start, assistant_end, turn_end, agent_end`, plus one request per turn, the tool
  running exactly once, one result per call, and the final assistant text.
  PASS.
- C4 round-1 cancellation contract re-verified on 5feb47596:
  `cargo test --locked -p pi-agent-core --test agent_loop_cancellation_contract
   --test rlm_t10 -- --test-threads=1`
  -> 3/3 (sequential cancel answers every call, parallel cancel yields one result per call in
  source order, before-hook abort wording) and 4/4 (tool update ordering/publish order),
  EXIT=0 (`_w1-integration.log`).
- C5 execution scope: `prune_settled` keeps unfinished tasks (`prune_settled_keeps_unfinished_tasks_cancellable`),
  keeps receipts that own pending joins (`prune_settled_keeps_receipts_that_own_pending_work`),
  folds pruned contributions into sticky bits so settlement history is not rewritten
  (`prune_settled_releases_terminal_tasks_and_inert_receipts`), and `reset_after_settlement`
  still refuses an unsettled scope. All PASS in the crate suite.
  Scope lifetime analysis: the scope is per-`Agent` (`Agent::execution_scope` is created per
  agent and shared with its loop config only), and the loop serialises runs with the
  `is_running` fence, so `prune_settled` cannot run concurrently with the request whose
  receipt it would drop.
- C7 PR #174 streaming hooks: the 435-line `streaming_metrics_tests.rs` suite passes
  unchanged (synthetic clock, per-attempt fresh window, `StreamingMs` only for a positive
  finite first-to-last nonempty delta interval, empty/usage/terminal drain excluded,
  cancellation keeps the last delta time, TPS consumer guards `ms <= 0.0` in
  `performance_monitor::throughput`). No defect reproduced; nothing changed.

## 5. Measurements (goal 6)

`cargo test --locked -p pi-agent-core --lib -- measure_tool_dispatch_overhead --ignored
 --nocapture --test-threads=1` (debug build, no-op tool, 200 rounds per point, 5 warm-up
rounds discarded, real `execute_tool_calls` entry point, 4 emitted events per tool)
(`_w1-measure.log`):

| mode | tools | us/batch | us/tool | marginal us/tool | events/batch |
|------|-------|----------|---------|------------------|--------------|
| parallel | 1 | 277.8 | 277.8 | - | 4 |
| parallel | 2 | 543.1 | 271.5 | 265.3 | 8 |
| parallel | 4 | 870.8 | 217.7 | 163.9 | 16 |
| parallel | 8 | 2209.3 | 276.2 | 334.6 | 32 |
| parallel | 16 | 4416.5 | 276.0 | 275.9 | 64 |
| sequential | 1 | 273.6 | 273.6 | - | 4 |
| sequential | 2 | 437.7 | 218.8 | 164.1 | 8 |
| sequential | 4 | 1473.2 | 368.3 | 517.7 | 16 |
| sequential | 8 | 2333.4 | 291.7 | 215.0 | 32 |
| sequential | 16 | 20778.6 | 1298.7 | 2305.7 | 64 |

- Per-tool dispatch overhead is ~0.27 ms in a debug build and linear in the tool count
  (parallel 1 -> 16 tools: 0.28 ms/tool; no super-linear behaviour).
- Parallel and sequential agree within noise at equal tool counts: the cost is dominated by
  the four events per tool (start, end, result message start/end), the tool task
  spawn/join and the result message construction, not by the dispatch mode.
- The sequential-16 point is a shared-host outlier (4.7x the trend; other workers were
  compiling on this host). It is not reproducible evidence of a mode difference and is
  reported as noise, not as a finding.
- No optimisation was kept: nothing showed a reproducible gain that also preserved the
  contract, so the loop's dispatch code is unchanged apart from the two fixes above.
- The measurement is an `#[ignore]`d test, so it never runs in the normal suite.

## 6. Commands and results

| Command | Result |
|---------|--------|
| `cargo check --locked -p pi-agent-core --all-targets -j 2` | PASS (9m 22s, warnings only) |
| `cargo test --locked -p pi-agent-core --lib -- --test-threads=1` (before changes) | PASS 99/99, EXIT=0 |
| same, with the two new failing tests | FAIL 2 (the two defects), 100 passed, EXIT=101 (`_w1-red.log`) |
| same, after the fixes | PASS 102 passed / 0 failed / 1 ignored, EXIT=0 (`_w1-green.log`) |
| `cargo test --locked -p pi-agent-core --test agent_loop_cancellation_contract --test rlm_t10 -- --test-threads=1` | PASS 3/3 and 4/4, EXIT=0 (`_w1-integration.log`) |
| `cargo test --locked -p pi-agent-core --lib -- measure_tool_dispatch_overhead --ignored --nocapture --test-threads=1` | PASS, numbers in section 5 (`_w1-measure.log`) |
| `cargo test --locked -p pi-coding-agent --test execution_mode --test sessionkernel_t04_process_cleanup -- --test-threads=1` | see section 7 |

## 7. pi-coding-agent side (session loop integration)

(pending run)

## 8. Deliberately not touched

- `crates/pi-ai/**` (provider streams, `StreamTaskReceipt`, validation): outside the map.
  Facts read there are used only to reason about behaviour; no edits.
- `crates/pi-coding-agent/src/core/messages.rs` (`convert_to_llm`): outside the map. It does
  not sanitize unanswered tool calls, which is why CORE-R2-1 had to be fixed in the loop.
- `tests/agent_loop_cancellation_contract.rs`: outside the map (round-1 suite). Re-run only,
  so the round-1 contract remains an independent oracle for the new fix.
- `performance_metrics.rs` / `performance_monitor/**` (PR #174 metric consumers): outside the
  map, verified by running their suites, not edited.
- Retry policy (session-level provider retry): not in this map; goal-4 "no duplicate side
  effects on retry" is verified only at the loop level (a continuation never re-runs an
  already answered call).
