## What this PR is

Round 2 of the full-harness validation campaign, on top of v0.1.35 + PR #174. Round 1 shipped as
PR #172. This round verifies the merged state empirically, fixes the reproducible defects it
found, and adds the end-to-end coverage the campaign requires.

Every heavy job (cargo check/test, python suites) ran through one serialized build queue with
`CARGO_BUILD_JOBS=2`, `-j 2`, `--test-threads=1`, BelowNormal priority and a shared 4-CPU
affinity mask. Wall times are labelled throttled; the host also ran the agent and Defender.
The full evidence pack is in `reports/full-harness-r2/` (round 1 used `reports/full-harness/`,
which stays untouched).

## Bugs fixed

| ID | Sev | Area | Fix |
|----|-----|------|-----|
| CORE-R2-1 | P2 | agent loop | A provider stream that ends with a terminal `error`/`aborted` now answers every tool call it already streamed. Before, history kept `assistant[ToolCall]` without a matching result, so the next request was protocol-invalid and `continue()` refused the context. |
| CORE-R2-2 | P3 | agent loop | A tool call stopped by cancellation before it started now records the dedicated tool-abort wording instead of the provider request abort text. |
| MEM-R2-1 | P2 | memory | `memory restore` recovers a damaged authoritative store. The damaged bytes are quarantined (content-addressed, fail-closed) before the backup is applied; before, `restore` failed closed forever and the backup was unusable. |
| MEM-R2-2 | P3 | memory | `memory configure` no longer drops every other key of a settings file that stopped parsing: the previous bytes are quarantined and logged. |
| MEM-R2-3 | P4 | memory | `memory apply` aborts on a serialization failure instead of writing `null` over the authoritative store. |
| OCC-R2-1 | P2 | daemon/OCC | OCC-2 peer lookup resolves a receiver by session name, active session id and persistent session id, matching the send surface. Before, a name-addressed receiver silently stayed OCC-1 while `/occ` reported ON. |
| BASH-R2-1 | P2 | bash tool | A JSON `timeout` above `u64::MAX` seconds reached `Duration::from_secs` and panicked. The value is clamped to one week and armed with `Duration::try_from_secs_f64`. |
| NODE-R2-1 | P2 | node tool | An uncaught exception raised by work a previous cell left running (timer, listener, floating promise) terminated the worker, so the next cell failed with a closed pipe and every JavaScript binding was lost. The worker now reports those errors as notices; the next result shows a bounded block and stays successful. |
| NODE-R2-2 | P3 | node tool | `process.exit(3)` was reported as `Node workspace exited or returned an oversized response.` The reader now distinguishes a parsed frame, an oversized frame, and an exit, and names the exit status. |

## New coverage

- `tests/full_harness_scenarios.rs`: tool loop and cancellation through the real session (A, B, G, H).
- `tests/scenario_memory_e2e.rs`: project isolation and search freshness on the real store (E, F).
- `tests/scenario_stream_persist.rs`: interrupted stream answered with an error result, durable
  partial transcript, failed write leaves the previous bytes and no temp file (K, L).
- `tests/ipython_output_bounds.rs`: kernel snapshot isolation between two clients plus a latency bound.
- `tests/node_workspace.rs`: three new tests, two of which reproduced NODE-R2-1 and NODE-R2-2.
- `store.rs` and `memory_source_integrity.rs`: quarantine/salvage matrix, non-UTF-8 bytes,
  fabricated receipts, read-only authoritative file, plus a portable twin.

## Verification

| Gate | Result |
|---|---|
| `cargo test -p pi-agent-core --lib` | 103 passed, 0 failed, 1 ignored |
| `cargo test -p pi-coding-agent --lib` (campaign) | 3747 passed, 0 failed, 10 ignored |
| `cargo test -p pi-coding-agent --lib` (pristine baseline) | 3708 passed, 16 failed, 10 ignored |
| `cargo test -p pi-ai --lib` | 899 passed, 0 failed, 6 ignored |
| `cargo test --workspace --lib` | `pi-coding-agent` and `pi-ai` green; `pi-jev` 97 passed, 1 failed |
| Integration batch | `bash_executor_unicode_and_cancel` 8/8, `compaction_recovery` 9/9, `full_harness_scenarios` 4/4, `ipython_output_bounds` 2/2, `node_workspace` 7/7 |
| `prime-agent-runtime` bash/guard/winjob | 128 tests OK, 37 skipped |

The 16 pristine failures are interpreter-gated tests: they need a real kernel started from
`KERNEL_CONTAINMENT_TEST_PYTHON`. With it set, all sixteen pass. Recommendation: skip with a
printed reason instead of panicking `NotPresent`.

The single `pi-jev` failure is `retain_waits_for_actual_sink_and_leaves_other_session_running`
(`Elapsed(())`). It fails the same way in the pristine baseline worktree, so it is a pre-existing
timing flake, not a regression.

## Not verified

- clang-repl kernels (no clang-repl on the host), Linux and macOS (Windows only here).
- Official LongMemEval / LoCoMo scoring (no dataset or scorer on the host).
- Real HTTP providers (the campaign is offline by design).
- `scenario_mult_agent.rs` (scenarios C, D, I, J) is written, compiles, and runs, but is **not**
  part of this PR: every child session ends with
  `protocol_exhausted_after_3_continuations` because the scripted fixture serves one response
  script for the parent and cannot give each child its own. The suite needs per-session fixture
  scripts; shipping it red would hide the real cause. The evidence is recorded in
  `reports/full-harness-r2/REGRESSION-GATES.md`.
- The Python runtime suite shows 19 failures / 91 errors under a full `discover` run, identically
  in the pristine and campaign trees; individual classes pass (10/10) and a manually driven
  `python -m rlm.repl` child answers correctly. It is order- or resource-sensitive on this host
  and is recorded as-is rather than claimed green.

## Documentation

- Documented, not changed: `EXEC-R2-1` (the pipe drain in `core::exec::exec_command` has no
  deadline after the child exits; bounding it would change documented behaviour for callers that
  read a pipe written by a longer-lived child) and `BASH-R2-2` (Windows terminates the job, so
  background descendants do not outlive the call; on POSIX they can).

## Review notes

Commits are grouped by area: agent loop, memory, bash, node, daemon, then tests, then docs.
Reports: `reports/full-harness-r2/{FINAL-REPORT,FEATURE-COVERAGE-MATRIX,BUG-LEDGER,PERFORMANCE-SCORECARD,EXPERIMENT-LEDGER,REGRESSION-GATES}.md`,
with per-gate evidence summaries in `reports/full-harness-r2/artifacts/summary/*.txt`.
