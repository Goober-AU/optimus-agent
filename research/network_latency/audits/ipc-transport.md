# IPC transport audit: worker-to-supervisor agent messaging

Scope: `crates/pi-coding-agent/src/modes/daemon/agent_message_transport.rs` and the
client plumbing it uses. Goal: persistent authenticated worker->supervisor IPC.

## Topology

- A session worker process (daemon worker, `PRIME_AGENT_INTERNAL_DAEMON_WORKER=1`)
  talks to its supervisor over the supervisor's client-facing JSONL socket
  (`PRIME_AGENT_INTERNAL_DAEMON_SUPERVISOR_SOCKET`, normalized via
  `supervisor_socket_path_from_env`). Windows: named pipe `\\.\pipe\...`; Unix: UDS.
- Two worker-side RPCs exist, both in `agent_message_transport.rs`:
  - `list_peers` -> `list_agent_peers` (body carries `workerToken`, per-request auth;
    supervisor resolves the token against live worker descriptors in
    `native_supervisor.rs:2632-2651`).
  - `send_message` -> `send_message` with `agentOrigin: true` (routed by the
    supervisor to the target worker; supervisor side is stateless per request,
    `daemon_mode.rs:5321-5363`).
- Neither command requires per-connection auth state: auth is in the request body
  (`workerToken` / `fromActiveSessionId` + supervisor-side token table). A
  long-lived connection is protocol-legal; the CLI itself uses one persistent
  `DaemonClient` for its whole session.

## Connection lifecycle cost points (today)

Both call sites build a FRESH `DaemonClient` per call:

1. `DaemonClient::connect(1000ms)`: pipe/UDS connect (Windows named-pipe open;
   `connect_daemon_socket` retries ERROR_PIPE_BUSY with 10ms sleeps).
2. `wait_for_hello(1000ms)`: waits for the `daemon_hello` frame.
3. One `request()` (id correlation, response wait): `list_agent_peers` 5000ms
   timeout, `send_message` 30000ms.
4. `client.close()`: aborts reader task, drops the pipe.

So every agent-to-agent message pays connect + hello + teardown on top of the
actual request round trip. `send_message` additionally runs a connect retry loop
(250ms sleep, 30s deadline) before its single request.

## `DaemonClient` capabilities relevant to persistence (daemon_client.rs)

- **Concurrent in-flight requests on one connection: SUPPORTED.** `request_wire`
  registers each request in `state.pending: HashMap<String, PendingDaemonRequest>`
  keyed by `daemon_{n}` id; `handle_line` correlates responses by id; the write path
  serializes through `socket.writer` (tokio Mutex). The CLI/supervisor already
  multiplex many commands over one connection.
- **Generation/compatibility validation on every (re)connect: `wait_for_hello` +
  per-request gate.** `request()` re-derives `command_compatibilities` per request
  against the current hello (schema revision 23 gate for `list_agent_peers`,
  envelope protocol version, capabilities). A reconnect re-runs `connect` +
  `wait_for_hello`, so a restarted/new-generation supervisor is revalidated.
- **`close()` is terminal** (`closed` AtomicBool; `connect` refuses afterwards).
  Reconnection therefore means a NEW `DaemonClient` instance, not `reconnect()` on
  the same one.
- **Request recovery / auto-reconnect: OFF by default** (`request_recovery_enabled`
  defaults false; only `enable_request_recovery`/`enable_auto_reconnect` turn it
  on, and those REPLAY pending writes). The messaging transport must NOT enable
  them: replay would violate the at-most-once invariant.
- **Timeout handling**: per-request deadline in `request_wire`; on timeout the
  pending entry is removed and an error returned; the socket stays usable.
- **Socket-close handling**: `notify_closed` settles all pending with
  `SocketClosed`; a request that was already written and loses its response fails
  with an error - never a replay (matches the transport invariant, and the
  delivery journal records `uncertain`).

## Where a persistent connection can live

- The transport functions are free functions with no worker state handle;
  `send_message` receives only `&AtomicBool` (shutting_down) plus strings.
  The call sites (`daemon_mode.rs:14091`, `daemon_mode.rs:15820`) pass
  `socket_path` derived from env, stable for the worker process lifetime.
- Therefore: a process-global manager keyed by `socket_path`
  (`agent_message_connection.rs`, already declared as a private submodule of
  `daemon_mode.rs`) is the natural home. No changes needed in `daemon_mode.rs`
  as long as the two transport function signatures stay unchanged.

## Failure classification (safety-critical)

- Error before the request bytes are written (connect/hello failure, or
  `request`'s "not connected" pre-write check): safe to establish a new
  connection and send the request once.
- Error after the request was written (timeout waiting for the response,
  `SocketClosed` while pending, write/flush failure after partial delivery):
  the message may already have been accepted; the error must be returned and the
  message NEVER replayed. This preserves the invariant at
  `agent_message_transport.rs:75-76` (and the delivery-journal `uncertain` outcome).

## Idle/restart considerations

- Daemon restart: cached pipe dies; reader EOF flips `is_connected()` to false.
  Next call detects a dead cached connection, does ONE reconnect (new client,
  connect 1000ms + hello 1000ms), and on failure falls back to today's
  fresh-connection retry loop (250ms sleeps, 30s deadline).
- Idle connections hold a supervisor-side client handle each; eviction after ~60s
  idle releases the pipe (supervisor cleanup runs on disconnect).
- Shutdown: `shutting_down` AtomicBool is checked before any (re)connect; on
  shutdown the cached connection is closed and never re-established.

## Concurrency of call sites

- `list_peers` runs from `create_agent_message_list_result` (agent_message_list,
  roster building) and `send_message` from `send_remote_agent_session_message`
  (agent-origin sends when the target is not local). Multiple sessions in one
  worker can issue these concurrently; `DaemonClient`'s pending-map design
  supports concurrent in-flight requests without a queue.

## Verification (2026-10-17, post-cancellation shift)

Code-level re-review of `agent_message_connection.rs` + `agent_message_transport.rs`
in full, plus `DaemonClient`/protocol contracts they rely on. One defect found and
fixed; everything else verified as designed.

Verified in code:

- At-most-once delivery: `AgentMessageConnectionManager::request` hands the
  command to `DaemonClient::request` exactly once per call; the `ConnectRetry`
  loop only retries establishment (connect/hello/dead-cached-checkout), and the
  per-command compatibility gate in `DaemonClient::request` rejects pre-write.
  Post-write failures (timeout, SocketClosed, write error) are returned, never
  replayed. Neither `enable_request_recovery` nor `enable_auto_reconnect` is ever
  called on manager-owned clients, so the DaemonClient replay paths stay off.
- Shutdown: `shutting_down` is checked before any (re)connect; an idle cached
  connection is evicted and an error returned. No reconnect happens after
  shutdown. An in-flight request at shutdown time is allowed to finish; the pipe
  is later released by the idle janitor or process exit.
- Idle eviction releases the pipe (60s budget, 1s sweep) - covered by
  `messaging_safety_idle_eviction_releases_the_pipe`.
- Old-schema daemon degrades: `list_agent_peers` requires schema revision 23;
  the gate rejects pre-write, so `list_peers` returns an empty list with zero
  wire requests (single and repeated calls covered). `send_message` to an
  old-schema daemon fails once with the compatibility error; it does not enter
  the 30s connect-retry loop because the failure is post-lease.

Defect found and fixed: `spawn_idle_janitor` exited permanently when its
eviction deadline coincided with an in-flight request (`in_flight > 0`), leaving
the connection cached with no janitor, so the pipe could stay open indefinitely.
The janitor now re-arms after one sweep interval; the request's lease guard
resets the idle clock on drop, so eviction is retried for the next quiet
period. Regression test:
`messaging_safety_idle_deadline_during_in_flight_request_still_evicts`
(delayed-response supervisor holds the request in flight past the 100ms
eviction budget; asserts the pipe is still released and a new connection is
established afterwards).

Gate runs (measured under throttled gate (4 CPUs shared, -j 2, BelowNormal),
--test-threads=2; debug build):

- `test -p pi-coding-agent --lib --offline --locked agent_message -- --nocapture
  --test-threads=2`: 58 passed, 0 failed, 3 ignored (the 3 ignored are the two
  `agent_message_bench` benches and `persistent_ipc_bench`, all `#[ignore]`d by
  design). Includes all 20 messaging_safety tests.
- `test -p pi-coding-agent --lib --offline --locked persistent_ipc_bench --
  --ignored --nocapture --test-threads=2`: 1 passed. fresh mean/p50/p95/p99 =
  1.164/1.127/1.599/2.541 ms (200 accepts); persistent = 0.536/0.484/0.721/0.754
  ms (1 accept); 2.17x mean speedup. See persistent-ipc-bench.md.

Residual risks:

- A cached connection whose in-flight request straddles process shutdown stays
  open until the (re-arming) idle janitor evicts it or the process exits; it is
  never reused after shutdown (checked before checkout).
- `list_peers` does not receive `shutting_down`; a discovery call racing
  shutdown could still establish one connection (unchanged from pre-campaign
  behavior; call sites do not invoke it after shutdown begins).
- The shared gate script's `--test-threads=2` injection appends a second `--`
  when the caller already passes one, turning `--test-threads=2` into a libtest
  filter instead of an option. Workaround used here: pass `--test-threads=2`
  explicitly in the cargo args so the gate appends nothing. The gate script is
  not owned by this lane; flagged to the parent.
