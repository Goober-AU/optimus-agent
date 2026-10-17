# IPC topology audit: daemon agent messaging

Scope: fresh-connection behavior of `agent_message_transport` and the transports
it rides on. File:line references are to the campaign worktree
`C:/Users/openclawuser/optimus-net-20261017` @ 6423654c1.

## Processes and endpoints

Three process roles talk over local IPC (Windows named pipe, Unix domain socket):

1. **Supervisor process** (`run_daemon_supervisor_mode`, `crates/pi-coding-agent/src/modes/daemon/native_supervisor.rs:273`). Binds the default daemon socket: `\\.\pipe\prime-agent-daemon` on Windows, `$TMP/prime-agent-<uid>/daemon.sock` on Unix (`daemon_socket.rs:110-119`).
2. **Worker daemons** (one OS process per session, spawned by the supervisor via `spawn_worker_process`, `native_supervisor.rs:4050`; env `PRIME_AGENT_INTERNAL_DAEMON_SUPERVISOR_SOCKET` carries the supervisor socket path, `native_supervisor.rs:1748`, constant `DAEMON_WORKER_SUPERVISOR_SOCKET_ENV` in `daemon_worker_protocol.rs:30`).
3. **Session clients** (CLI/UI frontends) connecting to the supervisor or, via a transport ticket, directly to a worker.

## Transports per hop

| Hop | Transport | Framing | Code |
| --- | --- | --- | --- |
| client/worker -> supervisor (public) | Windows named pipe / UDS | newline-delimited JSON (JSONL) | `daemon_client.rs:544` (`Framed<LinesCodec>`), server side `native_supervisor.rs:3995-4046` |
| worker peer -> worker (direct peer transport) | named pipe / UDS | binary private frames, 8-byte length prefix + JSON header + payload | `daemon_worker_client.rs:26-72` (`encode_private_frame`, `PrivateFrameDecoder`) |
| supervisor -> worker (worker socket serving) | named pipe / UDS | `private-framed` binary when `is_worker()`, else JSONL | `daemon_server.rs:78-120` |

`DaemonRoutedClient` (`daemon_routed_client.rs:95`) prefers the direct worker
transport for commands the worker serves itself (`serves_direct`,
`daemon_routed_client.rs:249`) and falls back to the supervisor control plane.

## The code under benchmark: `agent_message_transport.rs`

Worker-side agent messaging (the fresh-connection path this campaign measures):

- `list_peers` (`agent_message_transport.rs:14-43`): builds a fresh `DaemonClient`, `connect(1_000)` -> `wait_for_hello(1_000)` -> `request(list_agent_peers, 5_000)` -> `close()`. Failures return an empty vec (`unwrap_or_default`, :43).
- `send_message` (`agent_message_transport.rs:45-100`): fresh `DaemonClient` per call; the connect+hello loop retries with 250 ms sleeps up to a 30 s deadline (:63-77); exactly one `request(send_message, 30_000)`; close. A sent message is never replayed after a lost response (:79-80).
- Callers: `AgentDaemon::send_remote_agent_session_message` (`daemon_mode.rs:15806-15828`) and `AgentDaemon::list_supervisor_agent_peers` (`daemon_mode.rs:14084-14092`). So every cross-worker agent message costs one full pipe/UDS connect + hello exchange + one request + teardown.

## Connection lifecycle (DaemonClient)

- `DaemonClient::new` (`daemon_client.rs:723-737`): per-client UUID `protocol_client_id`, state mutex, listener registries.
- `connect(timeout)` (:810-864): `connect_daemon_socket` with a timeout; on Windows retries `ERROR_PIPE_BUSY` (os error 231) with 10 ms sleeps (:614-632). Then wraps the stream in `Framed<LinesCodec>` with `DAEMON_MAX_LINE_LENGTH = 256 MiB` (:396, :851), splits it, and spawns the `read_frames` reader task (:859, :572-590) which holds the client weakly and dispatches each line to `handle_line`.
- `request` (:942-990): re-waits hello (3 s) if the synchronous hello slot is empty (:962-965), evaluates per-command capability gates (:972-984), then `request_wire`.
- `request_wire` (:1021-1124): assigns `daemon_N` ids (:1043-1047), serializes the command to one JSON line (:1058), registers the pending entry (id, deadline, wake `Notify`) (:1065-1083), writes the line, then `select!` on the notify / deadline sleep / optional CancellationToken (:1095-1117).
- `close()` (:1126-1145): marks closed, rejects all pending requests with an error, aborts the reader task.

## Handshake and auth

- The server sends `daemon_hello` proactively on connect: supervisor `native_supervisor.rs:4007`, daemon-mode `daemon_server.rs:76-88`. Client parses it in `DaemonHello::from_value` (`daemon_client.rs:100-120`); `wait_for_hello` (:779-808) parks a oneshot waiter.
- Worker auth is command-level: `DaemonClient::authenticate_worker` sends `worker_auth` with the token from `PRIME_AGENT_INTERNAL_DAEMON_WORKER_TOKEN` (`daemon_client.rs:992-1010`, env constant `daemon_worker_protocol.rs:29`). `list_agent_peers` carries the `workerToken` per request (`agent_message_transport.rs:25`).
- Direct peer transport uses single-use grants: `DaemonPeerTransportTicket` + `peer_auth` (`daemon_worker_protocol.rs:120-153`, `daemon_worker_client.rs:436-453`), TTL 10 s (`daemon_supervisor.rs`, `PEER_TRANSPORT_GRANT_TTL_MS`).

## Server-side accept and framing

- Supervisor accept loop: Unix `accept()`; Windows creates the next pipe instance after each connect (`native_supervisor.rs:390-401`), mirroring `daemon_server.rs:24-50` for worker sockets. Both follow the "new pipe instance per connection" pattern the bench fixture must replicate.
- Supervisor per-connection reader scans **byte-by-byte** for `\n` with a 256 MiB line cap (`native_supervisor.rs:4038-4042`); each complete line is dispatched with its own `tokio::spawn` (:4040). Outbound writes go through an `mpsc::channel(1024)` (:3998) drained by one writer task (:4011-4033).
- Daemon-mode (worker) server uses `JsonlLineReader` + `StringDecoder` with 8192-byte reads (`daemon_server.rs:107-120`) and an unbounded outbound mpsc (:65).

## Routing

- The supervisor resolves `send_message` (no `activeSessionId` -> cross-worker path) via roster lookup: source resolution, summary lookup, delivery journaling, then forward to the target worker (`native_supervisor.rs:2787-2830+`).
- The roster (`agent_roster.rs`) tracks worker and agent entries; `list_agent_peers` answers from it. Roster heartbeats every 15 s (`daemon_worker_protocol.rs:42`).

## Serialization

All public/control-plane messages are `serde_json::Value` serialized to one
newline-terminated line (`serialize_json_line`, `daemon_client.rs:399`). The
direct worker path uses binary frames: 8-byte length prefix, JSON routing
header, raw payload (`daemon_worker_client.rs:26-72`), with an optional
`assistant-delta`/`jsonl` payload encoding (`daemon_worker_protocol.rs:66-72`).

## Queueing and buffering

- Client: `LinesCodec` buffered framing; one writer mutex per client.
- Supervisor: bounded write channel (1024) per connection, one writer task; line dispatch spawns a task per line (unbounded task creation).
- Daemon-mode server: unbounded write channel.

## Retry, cancellation, recovery

- `send_message` retries only the connection (not the request) up to 30 s (`agent_message_transport.rs:63-77`); `list_peers` does not retry at all.
- `DaemonClient` has an auto-reconnect ladder [1000, 2000, 5000, 10 000] ms (`daemon_client.rs:387`) and request recovery that replays `wire_data` after a fresh hello (:1168-1180), but `agent_message_transport` enables neither.
- Cancellation: `shutting_down` AtomicBool checked before each connect attempt (`agent_message_transport.rs:58-61`); `DaemonClientRequestOptions.signal` CancellationToken in `request` (:949, :1094).
- Durable side: `CommandRecoveryJournal`, `WorkerRecoveryJournal`, `AgentMessageDeliveryJournal` on the supervisor (`native_supervisor.rs:298-304`).

## Existing performance instrumentation

- `daemon_lifecycle_metrics.rs`: supervisor lifecycle stages only (start/stop), opt-in sidecar. `MAX_SUPERVISOR_PERFORMANCE_RECORDERS = 32` (`daemon_supervisor.rs:114`).
- No per-request or per-connection latency metrics exist anywhere on this path. The bench harness in `agent_message_bench.rs` adds them for the worker->supervisor hop.

## Consequences for the benchmark design

- Every `send_message`/`list_peers` call pays: pipe/UDS connect, server-side `daemon_hello` write, client parse+wait, one request/response, close. The harness must time the whole real function (that is the user-visible cost), while the fixture must model a supervisor that answers hello then one response per request.
- Windows named-pipe servers create the next instance only after a connect lands, so the fixture must do the same to be representative (and to avoid `ERROR_PIPE_BUSY` retries, `daemon_client.rs:622-627`).
- The transport serializes nothing globally: each call is independent, so concurrency exercises the server accept loop and per-connection costs, not a client-side lock.

## Bench-verified facts (2026-10-17, agent_message_bench matrix)

Measured against the echo-supervisor fixture in `agent_message_bench.rs` (debug
profile, measured under throttled gate (4 CPUs shared, -j 2, BelowNormal); full data
in `../ipc/ipc-baseline.md`):

- Every fresh-transport `send_message` costs one full pipe connect + hello +
  request + close: 550 requests produced 550 server accepts. The persistent
  manager amortizes this to 1 accept per config (550 requests).
- `send_message` is a mutating command, so `DaemonClient` emits an `ack_result`
  line after each response (`daemon_client.rs:1198-1210`). A supervisor must
  therefore expect up to 2 client lines per logical send on persistent
  connections; on fresh connections the ack races the immediate close and is
  usually lost (1.0-1.2x lines observed).
- Under server-side churn (close after each response), the persistent manager
  fails ~49% of sends fast and recovers on the next send; it never replays a
  sent message by design (`agent_message_transport.rs:51-53`).
- Under concurrency the persistent arm multiplexes one connection: throughput
  scales (2.3x at c32 vs fresh) but per-request median grows with queue depth
  (head-of-line blocking), while the fresh arm keeps low p50 and pays in p99
  (177 ms at c32 from connect contention).
