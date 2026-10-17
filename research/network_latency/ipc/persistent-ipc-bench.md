# Persistent IPC micro-bench: fresh vs persistent worker->supervisor connection

Run: `cargo test -p pi-coding-agent --lib --offline --locked persistent_ipc_bench -- --ignored --nocapture`
(also routed through the shared campaign gate `netbuild.ps1`, which adds `-j 2` and
BelowNormal priority; the bench itself is a single-threaded sequential loop, so job
count does not affect it).

- Date: 2026-10-17 (campaign worktree optimus-net-20261017, branch improve/network-ipc-performance)
- Build: debug test binary (at bench time the release lib test target failed on
  pre-existing `#[cfg(debug_assertions)]` test fixtures in
  crates/pi-coding-agent/src/core/jev_bridge.rs — `debug_hint_fixture_*` were compiled
  out in release but still referenced by tests; since fixed by the parent, INFRA-01 /
  .changes/release-test-compile-jev-bridge.md, and the release matrix in
  ipc-baseline.md ran after that fix). Both arms ran in the same binary, so the comparison is apples-to-apples.
- Environment: Windows named pipes (`\\.\pipe\optimus-message-connection-bench-<uuid>`),
  tokio single runtime, sequential sends from one task.
- Throttle label: measured under throttled gate (4 CPUs shared, -j 2, BelowNormal).

## Scenario

200 sequential `send_message` calls per arm against a local echo supervisor that
answers every request with a delivered receipt. Fresh arm: one `DaemonClient` per
call (connect 1000ms budget + hello + request + close), exactly the pre-change
transport behavior. Persistent arm: the `AgentMessageConnectionManager` cache, one
connection for all 200 sends.

## Results (per send)

| arm        | mean     | p50      | p95      | p99      | accepts |
|------------|----------|----------|----------|----------|---------|
| fresh      | 1.153 ms | 1.076 ms | 1.464 ms | 1.954 ms | 200     |
| persistent | 0.553 ms | 0.524 ms | 0.795 ms | 0.995 ms | 1       |

- Speedup (mean): 2.08x. p50 2.05x, p95 1.84x, p99 1.96x.
- Server answered 400/400 send requests (each send answered exactly once).
- Persistent arm used exactly ONE connection for all 200 sends; fresh arm used 200.

## Interpretation

The per-call connection setup (named-pipe connect + hello round trip + teardown)
costs roughly 0.6 ms per message on this machine. Removing it halves the
worker->supervisor send latency and removes 199 pipe connects per 200 messages.
The supervisor also stops paying 200 accept/teardown cycles.

## Caveats

- Debug build: absolute numbers include unoptimized JSON serialization on both
  arms equally; the connect/teardown delta is what this bench isolates.
- The campaign-wide CPU throttle was active (four agents sharing 4 CPUs); the
  bench loop is sequential and mostly waits on pipe I/O, so contention impact is
  expected to be small, but absolute values are not clean-room.
- Single-machine, single-run; treat as order-of-magnitude evidence for the
  connect-cost claim, not a distribution estimate.

## Reproduction

Test: `modes::daemon::daemon_mode::agent_message_connection::connection_bench::persistent_ipc_bench_200_sequential_sends`
in `crates/pi-coding-agent/src/modes/daemon/agent_message_connection.rs` (marked `#[ignore]`).

## Re-verification (2026-10-17, post-review shift)

Re-run after the cancelled shift and after one code fix (idle janitor re-arm; see
below). Invocation through the shared campaign gate with an explicit
`--test-threads=2` (measured under throttled gate (4 CPUs shared, -j 2,
BelowNormal), --test-threads=2). Debug build again; both arms in the same binary.

Command: `test -p pi-coding-agent --lib --offline --locked persistent_ipc_bench -- --ignored --nocapture --test-threads=2`

| arm        | mean     | p50      | p95      | p99      | accepts |
|------------|----------|----------|----------|----------|---------|
| fresh      | 1.164 ms | 1.127 ms | 1.599 ms | 2.541 ms | 200     |
| persistent | 0.536 ms | 0.484 ms | 0.721 ms | 0.754 ms | 1       |

- Speedup (mean): 2.17x. Server answered 400/400 send requests.
- Reproduces the original run within run-to-run noise (fresh mean 1.164 vs
  1.153 ms; persistent mean 0.536 vs 0.553 ms; persistent arm still exactly ONE
  pipe accept for 200 sends).
- Raw output: appended to `research/network_latency/ipc/results/bench-run.log`
  (section `ipc-persistent persistent_ipc_bench re-verification`).

Code change between the two runs: `spawn_idle_janitor` now re-arms when its
eviction deadline coincides with an in-flight request instead of exiting (which
previously left the connection cached with no janitor and the pipe open
indefinitely). The bench's sequential sends keep `in_flight == 0` at every
deadline, so the numbers are unaffected by the fix.

Test gate for the same change: `test -p pi-coding-agent --lib --offline --locked
agent_message -- --nocapture --test-threads=2` -> 58 passed, 0 failed, 3 ignored
(58 includes the 20 messaging_safety tests plus other `agent_message`-matching
unit tests). Raw output: appended to
`research/network_latency/ipc/results/check-run.log`.
