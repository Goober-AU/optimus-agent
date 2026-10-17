# PR draft: network/IPC performance campaign

Branch: improve/network-ipc-performance (base v0.1.34 @ 6423654c12dc2be814f370662bd2a31b76bd1562).

## Title

perf(network): persistent agent-message connections, pooled guarded HTTP clients, leaner SSE parsing

## Context

No-Ticket: performance campaign executed directly from user instruction, no Linear ticket exists

Five-agent offline performance campaign over the two hot communication paths:
PATH A (Optimus -> local model gateway -> remote provider HTTP/SSE) and
PATH B (worker -> daemon/supervisor -> recipient worker agent messaging).
Audit findings that motivated the changes are under research/network_latency/audits/:
every cross-worker agent message paid a full pipe/UDS connect + hello + teardown,
every guarded (SGLang-budget) provider request paid a fresh reqwest::Client build
plus a fresh TCP connect, and the SSE readers pushed bytes one at a time with a
per-line String allocation.

## Summary of changes

1. Persistent agent-message connections (pi-coding-agent): a process-wide
   `AgentMessageConnectionManager` caches one authenticated `DaemonClient` per
   supervisor socket; `list_peers` and `send_message` reuse it. At-most-once
   delivery is preserved (see Security).
2. Guarded-transport client pooling (pi-ai, `openai_http_client_pool.rs`):
   plain-http loopback origins pool when the observed NO_PROXY provably covers
   the host; https origins pool only with a fully observed environment and only
   on per-handshake-trust TLS backends (schannel, security-framework). Linux
   https and every fallback-guarded case stay fresh exactly as before.
3. SSE hot path (pi-ai): completions `SseLineReader` frames lines by span copy
   and flushes payloads in place; responses `SseBuffer` accumulates `data:`
   bytes without per-line Strings. Payload sequences are byte-identical
   (fixture-equivalence tests).
4. Test-only: offline benchmark harnesses (`agent_message_bench.rs`,
   `provider_transport_bench.rs`, `sse_parse_bench.rs`, `client_pool_bench`)
   and the research evidence tree under research/network_latency/.
5. Build fix (parent-owned): debug-only JEV assessment fixtures are
   cfg(debug_assertions)-gated so `cargo test --release --lib` compiles again.

### Gates respected

- Wire protocol unchanged: same JSONL frames, same commands, same envelope
  shapes; a long-lived connection is protocol-legal (the CLI already uses one
  persistent `DaemonClient` for its whole session). No `DAEMON_PROTOCOL_VERSION`
  or schema-revision change.
- At-most-once delivery preserved: a command is handed to the wire at most
  once; any failure after the write is returned, never replayed. The manager
  never enables `DaemonClient` auto-reconnect or request recovery (both replay).
- TLS verification untouched: no trust-store, verifier, or certificate-pinning
  change; pooling is gated by TLS-backend trust-resolution semantics and the
  existing environment-fallback guards; OpenSSL (Linux) https stays fresh.
- No new dependencies; Cargo.lock unchanged and locked.
- Offline tests only: loopback/named-pipe fixtures, synthetic credentials, no
  network egress, no real provider APIs.

## Measurement regime

Every measured number in this PR is labeled: measured under throttled gate (4 CPUs shared, -j 2, BelowNormal). The campaign-wide gate serializes every
heavy invocation chat-wide and pins builds to 4 shared CPUs at BelowNormal
priority with `-j 2` and `--test-threads=2`, while five agents share the box;
absolute numbers are not clean-room.

Provenance disclosures (kept from the bench lanes):

- provider-bench (provider-baseline.md tables): primary tables predate the
  gate directive and ran under concurrent sibling cargo load; medians were
  stable across runs and the p95/p99/max tails include external load. A
  post-directive throttled-gate confirmation run reproduced the suite
  (9 passed, 0 failed, 96.0 s) with medians unchanged within noise
  (delayed-first-byte 121.6 ms vs 122.0 ms p50).
- provider-pool (client-pool-bench.md): ran while up to four sibling cargo
  jobs saturated all CPUs, before the gate directive. Both arms of every
  comparison executed under the same concurrent load, so fresh-vs-pooled
  ratios and TCP-accept counts are directly comparable; absolute latencies
  are inflated by that contention. No numbers were re-measured after the
  directive.

## Evidence

Environment (all runs): Windows 10.0.26200, AMD EPYC 74F3, rustc 1.98.1
stable-x86_64-pc-windows-msvc, campaign worktree optimus-net-20261017,
branch improve/network-ipc-performance @ 6423654c1 + uncommitted campaign
changes. IPC runs use Windows named pipes; provider runs use loopback
127.0.0.1. Full per-experiment detail: research/network_latency/EXPERIMENT-LEDGER.md.

### 1. Persistent agent-message connections (PATH B)

Micro-bench, 200 sequential sends per arm against an echo supervisor,
debug test binary (both arms in one binary, apples-to-apples), n=200 per arm.
Measured under throttled gate (4 CPUs shared, -j 2, BelowNormal).

| arm | mean | p50 | p95 | p99 | pipe accepts |
| --- | --- | --- | --- | --- | --- |
| fresh (pre-change behavior) | 1.153 ms | 1.076 ms | 1.464 ms | 1.954 ms | 200 |
| persistent (this PR) | 0.553 ms | 0.524 ms | 0.795 ms | 0.995 ms | 1 |
| delta | -52.0% (2.08x) | -51.3% (2.05x) | -45.7% (1.84x) | -49.1% (1.96x) | -99.5% |

- 400/400 sends answered exactly once; persistent arm used exactly 1
  connection for all 200 sends.
- Evidence: research/network_latency/ipc/persistent-ipc-bench.md.
- Release-mode confirmation matrix LANDED (full 26-config matrix, both arms,
  debug + release profiles, nothing skipped): warm 1->1 256 B p50 0.257 ->
  0.095 ms (-63%); sustained 10 s c1 2,812 -> 8,458 msg/s (3.0x), c8 4,259 ->
  18,582 msg/s (4.4x); burst-32 barrier wall 198.1 -> 2.7 ms (75x), all
  delivered; list_peers 1-peer p50 0.344 -> 0.085 ms (-75%); cold first-50 p50
  0.327 -> 0.108 ms (-67%); connections 550 accepts (fresh) vs 1 per config
  (persistent). Documented trade-offs (ipc-baseline.md): single-connection
  head-of-line gives fresh the c16/c32 median (0.84/1.70 ms vs 0.26/0.29 ms)
  while persistent wins the tails (fresh p99 113/259 ms) and 12x throughput
  at c32; 64 KiB single messages ride behind ack writes (1.055 vs 0.653 ms);
  under synthetic server-closes-after-every-response churn the persistent arm
  surfaces ~49% of sends as fast errors (write-into-dying-pipe, never
  replayed) where fresh paid a connect per message - the production daemon
  has no idle close, so this exposure is limited to restart windows.
  Evidence: research/network_latency/ipc/ipc-baseline.md + ipc/results/
  baseline-*.jsonl (4 raw runs).

### 2. Guarded-transport client pool (PATH A, client-pool lane)

End-to-end loopback echo fixture, 320 requests per cell, release, n=320 per
cell. Measured under throttled gate (4 CPUs shared, -j 2, BelowNormal);
pre-gate contention disclosure above applies (both arms equally loaded).

| mode | conc | p50 | p95 | p99 | wall | rps | TCP accepts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fresh client/request | 1 | 579 us | 1011 us | 13174 us | 326 ms | 980 | 320 |
| pooled (this PR) | 1 | 134 us | 170 us | 268 us | 44 ms | 7145 | 1 |
| delta | | -76.9% (4.3x) | | | -86.5% (7.4x) | +629% (7.3x) | -99.7% |
| fresh client/request | 8 | 4323 us | 16423 us | 33673 us | 245 ms | 1302 | 320 |
| pooled (this PR) | 8 | 750 us | 919 us | 2622 us | 33 ms | 9651 | 8 |
| delta | | -82.7% (5.8x) | | | -86.5% (7.4x) | +641% (7.4x) | |
| fresh client/request | 32 | 19306 us | 33309 us | 33899 us | 229 ms | 1393 | 320 |
| pooled (this PR) | 32 | 2870 us | 10420 us | 13252 us | 41 ms | 7684 | 32 |
| delta | | -85.1% (6.7x) | | | -82.1% (5.6x) | +452% (5.5x) | |

Acquisition cost, n=1000 each, release. Measured under throttled gate (4 CPUs shared, -j 2, BelowNormal); same disclosures.

| path | p50 | p95 | p99 | mean |
| --- | --- | --- | --- | --- |
| fresh reqwest::Client::builder().build() | 23.6 us | 30.7 us | 46.6 us | 28.8 us (max 4.26 ms) |
| shared_client(Default) pool hit | 0.10 us | 0.10 us | 0.10 us | 0.066 us |
| http_client_pool acquire (digest + lock + take) | 5.1 us | 5.5 us | 7.7 us | 5.5 us |

- A fresh build is ~400x a shared-pool hit at the mean; a guarded request
  pre-change paid the build plus one TCP connect (empty pool).
- Unit note: client-pool-bench.json records the build/acquire blocks with
  `as_nanos()` under keys named `p50_us`; values there are nanoseconds
  mislabeled as microseconds (build p50 25.5 us, acquire 5.1 us after
  correction, consistent with the independent 23.6 us provider-bench
  measurement). The end-to-end block uses `as_micros()` and is correct.
- Evidence: research/network_latency/provider/results/client-pool-bench.json,
  research/network_latency/provider/client-pool-bench.md,
  research/network_latency/provider/results/provider-baseline.json.

### 3. Provider transport baseline (PATH A, characterization; no code change)

Real production path `stream_openai_completions` over a loopback HTTP/1.1
fixture, release. Measured under throttled gate (4 CPUs shared, -j 2, BelowNormal); pre-gate disclosure above applies (medians stable; tails
include external load).

| segment | n | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| request start -> response headers | 40 | 0.567 ms | 0.727 ms | 1566.8 ms (load noise) |
| headers -> first parsed event | 40 | 0.063 ms (63 us) | 0.112 ms | 1.260 ms |
| request start -> first text event | 40 | 0.641 ms | 1.450 ms | 1567.1 ms (load noise) |
| SSE 200 x 1 KiB chunks (throughput) | 10 | 649 KB/s | | |
| SSE 200 x 4 KiB chunks (throughput) | 10 | 2.69 MB/s | | |
| 4 MiB single body (throughput) | 10 | 78.1 MB/s | | |
| concurrency 1-8 TTFT p50 | 32/level | 0.45-0.47 ms | | 0 failures at all levels 1-32 |
| idle expiry (90 s pool timeout) | 1 run | accepts 1 -> 2 after 95 s idle | | |

- The warm default path was already sub-millisecond, so no transport change
  was made there; the guarded path (item 2) was the gap.
- Evidence: research/network_latency/provider/provider-baseline.md and
  results/ (provider-baseline.json, run-release-final.txt, run-full.txt,
  run-gate-confirm.txt, gateway-probe.txt).

### 4. SSE hot path (PATH A, parse lane)

Code changes kept: span-copy framing, in-place flush, byte-accumulating
responses buffer, single [DONE] check. Equivalence to the previous readers is
verified by fixture tests (text, reasoning-dense, tool-call deltas, CRLF,
split UTF-8, chunk boundaries, unterminated tails, invalid UTF-8 replacement).
Quantitative ns/event numbers: the SSE lane's scorecard
(research/network_latency SSE parse bench results) was still being captured
when this draft was frozen; do not cite unmeasured numbers. The audit
inventory of per-event allocations is in research/network_latency/audits/sse-path.md.

## Security

- At-most-once delivery (IPC): the manager sends each command at most once.
  Failures before the write (connect, hello, dead-cache checkout) may
  establish a new connection; failures after the write are returned and the
  message is never re-sent. `DaemonClient` auto-reconnect and request
  recovery (both replay writes) are never enabled on this path. Delivery
  semantics match the pre-change transport and the supervisor delivery
  journal's `uncertain` outcome for lost responses.
- TLS verification: unchanged. Pooling for https origins is allowed only on
  backends that re-resolve system trust on every handshake (schannel,
  security-framework). OpenSSL freezes its probed trust at connector build,
  so Linux https origins stay fresh (the pre-change behavior) rather than
  risk pinning stale trust. All existing fallback guards (TLS env files,
  unobserved system proxy, https:// proxy URL, no host, outside tokio
  runtime, environment-digest rotation) keep forcing fresh clients.
- NO_PROXY loopback gate: reimplements hyper-util 0.1.20 NoProxy semantics and
  is verified end-to-end against a real frozen reqwest proxy matcher built
  through the public `Proxy::no_proxy` path; the one documented divergence
  (leading-zero IPv4 CIDR entries classified as domains) can only make the
  pool more conservative.
- No credential or request data enters the pool: pooled objects are
  transport-only reqwest clients keyed by environment digest and URL shape.
- No new dependencies; no trust-store or proxy-configuration changes; no
  network egress in tests (loopback and named-pipe fixtures, synthetic
  credentials only).

## Reliability

- Dead cached connections are detected on use (`is_connected`), evicted, and
  re-established once (new client + connect + hello, which re-runs the
  generation/compatibility gates); `send_message` keeps its historical
  30 s deadline retry loop as fallback, `list_peers` still degrades to an
  empty list on failure.
- Idle connections are closed by a janitor after 60 s; shutdown closes the
  cache and never reconnects; a connection with in-flight requests is never
  closed underneath them.
- Error paths through the pooled provider transport were measured: HTTP 429
  and 500 fail fast (p50 0.37/0.35 ms), mid-stream TCP disconnects are
  detected with partial output preserved (20/20 runs), cancellation lands
  under 1.5 ms.
- Pooled idle connections die at the 90 s reqwest pool timeout and the next
  request reconnects (accepts 1 -> 2 after a 95 s idle). Note: the local
  model gateway answers with `Keep-Alive: timeout=5`, so idle pooled
  connections to the gateway are closed server-side well before 90 s and the
  next request reconnects; harmless, but it means steady traffic, not idle
  pooling, is what avoids gateway reconnects.

## Reproduction

All heavy commands run through the campaign gate (never bare cargo):

```
powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/openclawuser/.net-ipc/netbuild.ps1 -AgentName <name> test -p pi-coding-agent --lib --offline --locked agent_message -- --nocapture
powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/openclawuser/.net-ipc/netbuild.ps1 -AgentName <name> test -p pi-coding-agent --lib --offline --locked persistent_ipc_bench -- --ignored --nocapture
powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/openclawuser/.net-ipc/netbuild.ps1 -AgentName <name> test -p pi-coding-agent --release --lib agent_message_bench -- --ignored --nocapture --test-threads=1
powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/openclawuser/.net-ipc/netbuild.ps1 -AgentName <name> test -p pi-ai --release provider_transport_bench -- --include-ignored --nocapture
powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/openclawuser/.net-ipc/netbuild.ps1 -AgentName <name> test -p pi-ai --release http_client_pool -- --ignored --nocapture
powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/openclawuser/.net-ipc/netbuild.ps1 -AgentName <name> test -p pi-ai --release sse_parse_bench -- --ignored --nocapture
```

Portable form for reviewers without the gate (labels apply: measured under
throttled gate (4 CPUs shared, -j 2, BelowNormal) for campaign runs):

```
cargo test -p pi-coding-agent --lib --offline --locked agent_message -- --nocapture
cargo test -p pi-coding-agent --lib --offline --locked persistent_ipc_bench -- --ignored --nocapture
cargo test -p pi-ai --release provider_transport_bench -- --include-ignored --nocapture
cargo test -p pi-ai --release http_client_pool -- --ignored --nocapture
cargo test -p pi-ai --release sse_parse_bench -- --ignored --nocapture
```

Provider suite runtime ~97 s release (95 s of it is the deliberate
idle-expiry sleep). Compile check of the full campaign diff:
`cargo check -p pi-coding-agent --lib --tests --offline --locked`
(dev profile, finished 6 m 50 s, warnings only; log:
research/network_latency/ipc/results/check-run.log).

## Limitations

- Single machine, shared box, throttled; absolute numbers are not clean-room
  and not comparable across machines. Ratios within one binary/run are the
  reliable signal.
- IPC micro-bench (item 1) is a debug build; re-verified by the ipc-verify lane
  with raw gate logs appended (fresh mean 1.164 vs persistent 0.536 ms, 2.17x,
  400/400 answered exactly once; first run 2.08x - run-to-run noise, same
  order). Raw logs: ipc/results/check-run.log, bench-run.log. The
  connection-manager idle janitor was hardened during verification (re-arms
  after an in-flight request blocks eviction; regression test included).
  Order-of-magnitude evidence for the connect-cost claim, not a distribution
  estimate. The release matrix (ipc-baseline.md) is the confirmatory run.
- The IPC bench fresh arm is an inlined replica of the pre-change transport
  (the transport now delegates to the manager); replica drift is a measurement
  risk, mitigated by copying the function verbatim.
- The echo-supervisor fixture answers immediately; real supervisor work
  (roster lookup, delivery journaling, worker forwarding) is excluded from
  both arms, isolating transport costs by design.
- Unix domain sockets compile but were not measured (Windows worktree).
- TLS handshake cost and HTTP/2 multiplexing are unmeasured offline: no
  server-side TLS fixture exists in-tree, no cert-generation crate may be
  added, and machine trust-store changes are out of scope. Recorded as
  BLOCKED in the ledger, not worked around.
- partial_message() deep-clones the accumulated output per delta event
  (quadratic over a stream); this is the event contract (TS parity), so it
  was measured and documented, not changed.
- provider-pool absolute latencies carry the pre-gate contention disclosure;
  they were not re-measured under the gate (ratios and accept counts remain
  valid). provider-bench has a throttled-gate confirmation run.
- client-pool-bench.json build/acquire blocks are nanoseconds under `*_us`
  keys (see unit note under evidence item 2); values used here are corrected.
  The bench-lane markdown prose and .changes/provider-client-pool-reuse.md
  previously stated the build cost as "p50 25.5 ms"; corrected by the parent
  to 25.5 us before commit, and the bench code now emits truthful `*_ns`
  keys (values unchanged; unit note added to the scorecard).

## Proposed commit structure (for the parent to execute)

1. `fix(agent): gate debug-only JEV assessment fixtures to debug builds`
   - crates/pi-coding-agent/src/core/jev_bridge.rs
   - .changes/release-test-compile-jev-bridge.md
   (unblocks release-mode test builds for the benches)
2. `perf(daemon): persistent worker-supervisor agent message connections`
   - crates/pi-coding-agent/src/modes/daemon/agent_message_connection.rs
   - crates/pi-coding-agent/src/modes/daemon/agent_message_transport.rs
   - crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs
   - .changes/persistent-agent-message-connections.md
   - research/network_latency/audits/ipc-topology.md
   - research/network_latency/audits/ipc-transport.md
   - research/network_latency/ipc/ (scorecards, cmd helpers, results)
3. `perf(ai): pool guarded transport clients for loopback and per-handshake-trust origins`
   - crates/pi-ai/src/providers/openai_http_client_pool.rs
   - .changes/provider-client-pool-reuse.md
   - research/network_latency/provider/ (scorecards and results)
4. `perf(ai): span-copy SSE framing and byte-accumulating responses buffer`
   - crates/pi-ai/src/providers/openai_completions.rs
   - crates/pi-ai/src/providers/openai_responses.rs
   - .changes/sse-streaming-hot-path.md
   - research/network_latency/audits/sse-path.md
5. `test(ai): offline provider transport and SSE benchmark harnesses`
   - crates/pi-ai/src/providers/provider_transport_bench.rs
   - crates/pi-ai/src/providers/sse_parse_bench.rs
   - crates/pi-ai/src/providers/mod.rs
   - .changes/network-benchmark-harnesses.md
   - research/network_latency/README.md
6. `test(daemon): agent-message IPC benchmark harness`
   - crates/pi-coding-agent/src/modes/daemon/agent_message_bench.rs
   - research/network_latency/ipc/ipc-baseline.md (matrix scorecard)
7. `docs(research): network/IPC performance experiment ledger and PR evidence`
   - research/network_latency/EXPERIMENT-LEDGER.md
   - research/network_latency/PR-DRAFT.md
   - research/network_latency/validation/ (gate logs, if kept)
