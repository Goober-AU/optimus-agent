# HTTP/2 for provider transports: feasibility verdict and source analysis

Offline investigation for the network/IPC performance campaign (worker
http2-analysis, PR #167 round 2). Question: should the provider HTTP/SSE
transport use HTTP/2, what would multiplexing buy at our concurrency levels,
and can an h2 A/B be measured offline under the campaign rules?

Method: targeted reads of the vendored reqwest 0.12.28, hyper 1.11.1,
hyper-util 0.1.20, h2 0.4.19 and native-tls 0.2.18 sources under the shared
CARGO_HOME registry; Cargo.lock and feature-resolution checks (`cargo tree
-e features --offline`, metadata only, no builds); one throwaway
manifest-only resolution probe in a temp directory outside the worktree. No
code changed, no gate runs, no benchmarks executed by this worker. All
measurements cited below come from `provider-baseline.md` (provider-bench
worker) and are labeled as such.

## Verdict (summary)

> DECISION (2026-10-17, after this analysis): the enabling change was applied
> by user instruction - `native-tls-alpn` was added to the workspace reqwest
> features, so statements 1 below describes the pre-flip state this analysis
> measured. HTTP/2 is now negotiated with providers that advertise it via
> ALPN. The A/B and WAN-benefit analysis below remains the justification basis;
> the benefit itself remains unmeasured online (loopback cannot express it).
> Lock identity and workspace compilation were verified after the flip.

1. BEFORE THIS ROUND THE BUILD NEVER SPOKE HTTP/2 TO PROVIDERS. All provider traffic is
   HTTP/1.1, on every platform, regardless of server capability. The http2
   reqwest feature is compiled in, but the ALPN offer and the ALPN-result
   detection on the default TLS backend are both cfg-gated behind the
   `native-tls-alpn` feature, which this workspace does not enable. Evidence
   chain in section 1.
2. A real h2 A/B is BLOCKED offline under the campaign rules. Writing an h2
   server requires either a new direct dependency (forbidden: h2 0.4.19 and
   socket2 0.6.5 exist in Cargo.lock only as transitive deps) or a
   hand-rolled byte-level h2 server fixture (documented, not recommended).
   There is no TLS fixture offline, so even the ALPN path cannot be
   exercised. Exact evidence in section 4.
3. The enabling change, if wanted, is one feature word: add `native-tls-alpn`
   to the workspace reqwest features. Verified offline that this resolves
   with zero new packages and zero version changes on Windows, so
   Cargo.lock is expected to stay byte-identical. Risks and the
   recommendation sequence in section 5.
4. For STREAMING workloads at our measured concurrency (1-32 in-flight), the
   existing measurements show no HTTP/1.1 pathology that multiplexing would
   fix offline: 0 failures, TTFT p50 3.10 ms at level 32, one connection per
   in-flight request. A single h2 connection would not be throughput-bound
   until roughly 29 concurrent 4KB-chunk streams (about 120 at 1KB chunks) on
   this box. The realistic h2 win is per-request TLS handshake elimination
   for pooled clients over a WAN, which the loopback fixture cannot express.
   Analysis and assumptions in section 3.
5. Recommended h2 client configuration for whenever h2 becomes active
   (UNMEASURED recommendation, section 5): keep `http2_adaptive_window`
   disabled (defaults already exceed realistic provider BDPs), set
   `http2_keep_alive_interval` to 30 s with `keep_alive_while_idle` off, and
   leave both flow-control windows at hyper's defaults (2 MiB stream, 5 MiB
   connection).

## 1. How reqwest actually negotiates h2, and why it does not here

### The three code paths that must all agree

For an https request to run over HTTP/2, three things must happen, in order:

1. OFFER: the client must offer `h2` via ALPN in the TLS ClientHello.
   reqwest-0.12.28/src/async_impl/client.rs builds the default native-tls
   connector in the `TlsBackend::Default` branch (client.rs:526-616). The
   ALPN list is set at client.rs:531-545:

   ```rust
   #[cfg(all(feature = "native-tls-alpn", not(feature = "http3")))]
   {
       match config.http_version_pref {
           HttpVersionPref::Http1 => { tls.request_alpns(&["http/1.1"]); }
           #[cfg(feature = "http2")]
           HttpVersionPref::Http2 => { tls.request_alpns(&["h2"]); }
           HttpVersionPref::All => { tls.request_alpns(&["h2", "http/1.1"]); }
       }
   }
   ```

   With `native-tls-alpn` disabled, `request_alpns` is never called: no ALPN
   extension is sent at all. (The rustls backend sets `alpn_protocols`
   unconditionally at client.rs:824-843, but that code is under
   `#[cfg(feature = "__rustls")]`, which is also off here.)

2. DETECT: after the handshake, the connector must report the negotiated
   protocol to hyper. reqwest-0.12.28/src/connect.rs:1488-1494 (native-tls
   `Connection::connected`):

   ```rust
   #[cfg(feature = "native-tls-alpn")]
   match self.inner.inner().get_ref().negotiated_alpn().ok() {
       Some(Some(alpn_protocol)) if alpn_protocol == b"h2" => connected.negotiated_h2(),
       _ => connected,
   }
   #[cfg(not(feature = "native-tls-alpn"))]
   connected
   ```

   The `#[cfg(not(...))]` arm is what this build compiles: the negotiated
   protocol is never inspected, so `Connected::negotiated_h2()` is never set.

3. DISPATCH: hyper-util decides per-connection. hyper-util-0.1.20/src/
   client/legacy/client.rs:519-541: the connection is upgraded to HTTP/2
   only if `connected.alpn == Alpn::H2` (set by step 2) or the builder was
   configured `http2_only(true)`. Neither happens for default https requests
   here (reqwest sets `http2_only` only for `http2_prior_knowledge`,
   client.rs:938-941).

### Feature resolution in this workspace (verified)

Workspace Cargo.toml declares:

```toml
reqwest = { version = "0.12", features = ["json", "stream", "multipart", "socks", "gzip", "brotli", "deflate"] }
```

Defaults are not disabled, so reqwest's `default = ["default-tls", "charset",
"http2", "system-proxy"]` applies. `cargo tree -i reqwest -e features
--offline` in the worktree resolves exactly: `default`, `default-tls`,
`__tls`, `charset`, `http2`, `system-proxy`, `json`, `stream`, `multipart`,
`socks`, `gzip`, `brotli`, `deflate`. NOT resolved: `native-tls-alpn`,
`__rustls`, `rustls-*`, `http3`, `native-tls-vendored`.

So the build has: h2 code compiled (feature `http2`, dep `h2`), the
native-tls stack (hyper-tls 0.6.0, native-tls 0.2.18, tokio-native-tls 0.3.1),
but neither the ALPN offer (step 1) nor the ALPN detection (step 2). Result:
every provider https request runs HTTP/1.1, on every OS. A server cannot
force h2 on a client that offers no ALPN.

Consequences for two in-repo comments that assume otherwise (both
teammate-owned, flagged here only for the parent to triage):

- `crates/pi-ai/src/providers/shared_http.rs:29-31`: "reqwest defaults:
  standard redirects, HTTP/2 allowed" - intent, not this build's behavior.
- `crates/pi-ai/src/providers/amazon_bedrock.rs:2202-2204`: describes
  `http1_only()` as "ALPN restricted to http/1.1"; in this build no ALPN is
  offered at all, so `http1_only()` and the default both end at HTTP/1.1 by
  the same non-offer (the pinned path is still correct for its purpose, only
  the ALPN mechanism wording is off).

### What would activate h2

- Path A (negotiation): enable reqwest's `native-tls-alpn` feature. On
  Windows this turns on `native-tls/alpn` + `hyper-tls/alpn`, which are
  plain feature flags over already-resolved crates (see section 5 for the
  lock-identity evidence). Default `HttpVersionPref::All` then offers
  `["h2", "http/1.1"]`, and any h2-capable provider is spoken to over h2.
- Path B (force): call `.http2_prior_knowledge()` (client.rs:1560-1562).
  This sets `http2_only(true)` and works even without ALPN support (raw h2
  over the TLS stream, and h2c over plain TCP). Fails outright against any
  server that is not h2. Not suitable for heterogeneous provider fleets.
- Path C (backend switch): a rustls-based backend sets
  `alpn_protocols = ["h2", "http/1.1"]` unconditionally (client.rs:824-843).
  rustls 0.23.44, hyper-rustls 0.27.9 and tokio-rustls 0.26.5 are already in
  Cargo.lock, but this swaps the whole TLS stack and is a much larger change.

### Pool behavior once h2 IS negotiated (source-verified)

- hyper-util pools the ALPN-h2 connection under the HTTP/2 pool key
  (`alpn_h2`, client.rs:523-538): the first request converts the checkout,
  later requests multiplex over that one connection.
- Requests that raced the first h2 handshake are canceled once and retried
  transparently (`retry_canceled_requests: true` default, client.rs:1034;
  retry loop client.rs:248-271) - a one-time sub-ms hiccup per new h2
  connection, no error surfaces to callers.
- reqwest's pool settings keep applying: `pool_idle_timeout` 90 s
  (client.rs:301, and set explicitly at shared_http.rs:70),
  `pool_max_idle_per_host` usize::MAX (client.rs:302).
- If the server's SETTINGS_MAX_CONCURRENT_STREAMS is exhausted, h2 queues
  new streams on the existing connection; hyper-util does not open a second
  connection to shed load, so provider-side stream caps become a burst
  backpressure point (typical provider caps 100-256, above our 1-32 range).

## 2. h2 semantics from the vendored sources

### Effective client defaults with reqwest defaults (nothing set)

| Setting | Value | Source |
| --- | --- | --- |
| Stream receive window (SETTINGS_INITIAL_WINDOW_SIZE) | 2 MiB | hyper-1.11.1/src/proto/h2/client.rs:49,88 |
| Connection receive window | 5 MiB | hyper-1.11.1/src/proto/h2/client.rs:48,87 |
| Max frame size | 16 KiB | hyper client.rs:50,90; h2 spec default 16,384 |
| Max header list size | 16 KiB | hyper client.rs:52,91 |
| Max send buffer | 1 MiB | hyper client.rs:51,96 |
| Initial max send streams (client assumption before server SETTINGS) | 100 | hyper client.rs:61,89 |
| Server push | disabled (`enable_push(false)`) | hyper client.rs:115 |
| Adaptive window | off | hyper client.rs:86 |
| h2 keep-alive | off (no interval) | hyper client.rs:92 |
| h2 keep-alive timeout | 20 s (unused while interval is None) | hyper client.rs:93 |
| h2 keep-alive while idle | off | hyper client.rs:94 |
| TCP_NODELAY | on (reqwest default, client.rs:372,1671) | reqwest |
| Pool idle timeout | 90 s | reqwest client.rs:301 |

For contrast, the pure-h2/HTTP-spec defaults (what applies if hyper did not
override, e.g. on the peer side): stream and connection windows 65,535 B,
frame 16,384 B, HPACK table 4,096 B (h2-0.4.19/src/frame/settings.rs:41-47).
hyper's client immediately raises the connection window by WINDOW_UPDATE(0)
after handshake (h2-0.4.19/src/client.rs:740-743, 1377-1380).

### What `http2_adaptive_window(true)` actually does

It enables hyper's BDP estimator, not something inside h2
(hyper-1.11.1/src/proto/h2/client.rs:137-148: `bdp_initial_window` is set to
the initial stream window only when `adaptive_window` is on; the ping channel
is only instantiated when BDP or keep-alive is configured). The algorithm
(hyper-1.11.1/src/proto/h2/ping.rs:1-21, 361-420):

- On received DATA, if no BDP ping is outstanding and the ping backoff has
  elapsed, send an opaque PING and count bytes until the PONG.
- On PONG: RTT moving average (1/8 weight), bandwidth = bytes / (rtt * 1.5).
- If the sample is at least 2/3 of the current window, double the sample
  (capped at BDP_LIMIT = 16 MiB, ping.rs:364) and emit a SizeUpdate.
- SizeUpdate drives `set_target_window_size` + `set_initial_window_size`
  (client.rs:239-247), i.e. WINDOW_UPDATE(0) and a fresh
  SETTINGS_INITIAL_WINDOW_SIZE to grow the receive windows mid-connection.
- Ping pacing starts at 100 ms and backs off x4 after two stable samples,
  capped at 10 s (ping.rs:411-420). Windows only ever grow (the estimator
  never shrinks them below the configured initial sizes).
- The doc note in reqwest is accurate: enabling adaptive window overrides
  the fixed `http2_initial_*_window_size` values (reqwest
  client.rs:1596-1603).

### WINDOW_UPDATE pacing (why big default windows matter for SSE)

h2 only queues a stream- or connection-level WINDOW_UPDATE when the
released-but-unclaimed capacity reaches half the current window
(h2-0.4.19/src/proto/streams/flow_control.rs:15-19, 93-108; queued in
proto/streams/recv.rs:467-499, drained at recv.rs:1149-1233). With hyper's
2 MiB stream window, a 4 KB-chunk SSE stream generates a stream-level
WINDOW_UPDATE roughly once per 256 events, and the connection-level one once
per ~2.5 MiB aggregate. Small spec-default windows (64 KiB) would emit an
update every ~8 events instead; hyper's defaults already remove that
per-event tax.

### What `http2_keep_alive_interval` does

hyper's h2 keep-alive (ping.rs:429-515) is a watchdog, not a poller: if no
frame at all is received for `interval`, send PING; if no PONG arrives
within `keep_alive_timeout` (default 20 s), the connection task exits and
in-flight streams fail. With `keep_alive_while_idle` false (default), it
only arms while at least one stream is active, so idle pooled connections
are not pinged. Any received frame (including provider SSE heartbeats)
resets the timer, so a healthy stream never sees a keep-alive PING.

## 3. Streaming concurrency: when would one h2 connection beat N HTTP/1.1 connections

All numbers in this section are from `provider-baseline.md` (provider-bench
worker; loopback, in-process HTTP/1.1 fixture, unthrottled runs confirmed
stable through the gate). This worker measured nothing new.

Measured facts used:

- Single HTTP/1.1 connection bulk throughput: 78.1 MB/s (4 MB body, shared
  client).
- Per-stream SSE throughput through the real path: 649 KB/s at 1 KB chunks,
  2.69 MB/s at 4 KB chunks; per-event pipeline cost ~1.0 ms p50 independent
  of chunk size.
- Concurrency sweep: one connection per in-flight request; TTFT p50 flat
  0.45-0.47 ms through level 4, 1.44 ms at 16, 3.10 ms at 32; 0 failures at
  every level; total p50 122.8 ms (level 1) to 145.0 ms (level 32); 16 of
  32 connections were new at level 32.

### Assumptions (explicit)

- A1: one h2 connection's loopback ceiling is approximately the measured
  single-connection 78.1 MB/s. h2 framing overhead at 4 KB DATA chunks is 9
  bytes per frame (~0.2%) plus the near-zero WINDOW_UPDATE traffic above;
  the residual risk is h2's extra per-frame CPU cost versus the h1 line
  reader, which is exactly what an A/B would measure and what we cannot.
- A2: per-stream demand is transport-agnostic: the ~1.0 ms p50 per-event
  pipeline cost (parse + deliver) dominates and sits above the transport in
  the stack.
- A3: loopback has no loss, so h2's TCP-level head-of-line blocking never
  triggers here. Production WAN behavior is unmeasurable with this fixture.
- A4: the sweep's 16-to-32 degradation is CPU contention (fixture and client
  share cores), not a transport property.

### Arithmetic

- Throughput ceiling: a single connection binds when N x per-stream-rate
  exceeds 78.1 MB/s. At 4 KB chunks (2.69 MB/s per stream) that is N = 29;
  at 1 KB chunks (649 KB/s) N = 120. Below those levels, one multiplexed
  connection is not the binding resource on this box; above them, N
  separate h1 connections spread the load while the single h2 connection
  saturates. Our tested range tops out at 32, i.e. right at the 4 KB-chunk
  crossover, and the sweep shows no failure or cliff there under h1.
- Connection-setup: the only offline-visible h2 advantage is replacing 16
  new loopback accepts at level 32 (~0.5 ms each warm) plus fresh-client
  builds (23.6 us p50) with zero new connections. Sub-millisecond in
  aggregate on loopback; not measurable as a win at our scale.
- Latency: the sweep shows no h1 TTFT pathology that multiplexing would
  fix offline (3.10 ms p50 at 32 on a contended box).

### Conclusion for streaming, honestly framed

On this loopback fixture there is no measured crossover at which h2
multiplexing beats N HTTP/1.1 connections for streaming; both transports
would be CPU-bound, and h2 adds frame-level work in a single connection
where h1 spreads it across sockets. The crossover that matters exists only
in the production configuration the fixture cannot express: per-request
TLS handshake elimination on a WAN. With a warm shared pool, h1 already
amortizes handshakes (measured: 1 accept per 32-request sequence), so the
h2 win concentrates on (a) bursts that exceed the idle pool (N new TLS
handshakes at 1-2 RTT each, typically 50-300 ms inter-region, versus zero
new connections under h2) and (b) socket churn (TIME_WAIT, ephemeral ports,
provider-side accept pressure) at high burst rates. Against that, streaming
over a single TCP connection adds connection-level head-of-line blocking -
one lost packet stalls every stream at once - which N h1 connections do
not have; for latency-sensitive token streams on lossy paths, N
connections can be the better shape. Net: h2 is a plausible neutral-to-
positive change for provider fleets that support it, but nothing in the
existing measurements justifies prioritizing it over the pool and TTFT work
already landed, and the sign of the WAN effect is an open question until a
real A/B exists.

## 4. Why a real h2 A/B is BLOCKED offline (exact evidence)

An honest A/B needs an h2 server reachable by the production client stack.
Every path to one is closed under the campaign rules:

1. Declaring an h2 dependency is forbidden. `h2 0.4.19` and `socket2 0.6.5`
   appear in Cargo.lock only as transitive dependencies (verified: h2 is
   depended on by exactly `hyper` and `reqwest`; socket2 by `hyper-util`
   and `tokio`; no pi-* crate appears in either reverse-dependency list).
   The task rules allow reading their vendored sources but not declaring
   them, and the manifest edit would also violate the "no new dependencies"
   mandate even though the lock file already pins the versions.
2. No other in-graph crate gives pi-ai a server-side h2 or TLS acceptor.
   pi-ai's declared deps with any TLS role are only reqwest (client) and
   tokio-tungstenite (WebSocket client, native-tls feature). native-tls
   (which does have a TlsAcceptor) is not a declared dependency of any
   workspace crate either, so building an ALPN TLS fixture from it hits the
   same forbidden-declaration wall.
3. No external h2 server exists on this machine: no Node.js on PATH (the
   natural http2/h2c option), and the available Python 3.11 has no h2,
   hypercorn or aiohttp package (verified: pip list shows only capstone,
   pip, setuptools). No network access to install one.
4. No TLS fixture: already documented in provider-baseline.md ("TLS and
   HTTP/2 legs: BLOCKED offline... no cert-generation crate (rcgen) exists
   in the tree"). Even with an h2 server, exercising the real ALPN
   negotiation path requires a TLS endpoint with a certificate the client
   accepts.
5. Theoretical escape hatch, documented and rejected: reqwest's
   `http2_prior_knowledge()` speaks h2c over plain TCP, so no TLS is needed
   for a protocol-level A/B; but the server side would have to be a
   hand-rolled byte-level h2 server inside the bench (preface, SETTINGS
   exchange, HPACK literal encoding, DATA framing, flow-control accounting).
   That is days of error-prone work whose result would measure the fixture
   as much as the transport (a single HPACK or flow-control mistake
   invalidates every number), in a teammate-owned bench file. Not a
   credible path for a measured round; recorded so the next round does not
   rediscover it.

Blocking references: Cargo.lock `h2 0.4.19` (deps: hyper, reqwest),
`socket2 0.6.5` (deps: hyper-util, tokio); reqwest feature gates
`http2 = ["h2", "hyper/http2", "hyper-util/http2", ...]` and
`native-tls-alpn = ["native-tls", "native-tls-crate?/alpn",
"hyper-tls?/alpn"]` (reqwest-0.12.28/Cargo.toml [features]).

## 5. Recommendations

### 5a. Enabling h2 later (for the parent to sequence, not this round)

Minimal change: add `"native-tls-alpn"` to the reqwest features list in the
workspace Cargo.toml. Verified offline with a throwaway manifest-only probe
(temp dir outside the worktree, `cargo tree --offline` with the same
registry cache): reqwest =0.12.28 with the current seven features plus
`native-tls-alpn` resolves to 136 packages, every one of which already
appears in the workspace Cargo.lock. The only version difference anywhere
is smallvec (probe 1.16.1 vs lock 1.15.2), a fresh-resolution artifact:
resolving from scratch picks latest-compatible, while enabling a feature in
place keeps existing pins, so no re-resolution of smallvec would occur. Feature selections
are not recorded in Cargo.lock, so the lock is expected to stay
byte-identical; the parent should still confirm with
`cargo metadata --locked` (or `git diff Cargo.lock` after a check) before
pushing. On Windows the feature adds no crate at all (`native-tls`'s alpn
feature only pulls `security-framework/alpn`, a macOS-only dep already in
the lock).

Sequencing caveats, in order of importance:

- Enabling ALPN is a behavior change across every https provider at once:
  h2-capable endpoints switch protocols underneath us with zero A/B data
  (section 4). Gate it behind the same release discipline as any transport
  change, ideally with a temporary kill switch.
- schannel ALPN requires Windows 8.1+ / Server 2012 R2+ (native-tls alpn
  path); acceptable for this user base, but note it for exotic hosts.
- Corporate proxies and TLS-inspecting middleboxes sometimes mishandle
  h2 ALPN results; the failure mode is a handshake or protocol error where
  h1 worked. The pool policy already isolates Bedrock (http1 pinning), but
  a global fallback would need a config knob, not a code revert.
- Do NOT present TCP_NODELAY as an h2-era win: reqwest has it on by default
  (client.rs:372, setter at 1671), unchanged by any of this.

### 5b. Client configuration once h2 IS active (UNMEASURED recommendation)

Grounded in the vendored semantics and our workload shape (section 2), but
NOT backed by any A/B measurement. Treat as defaults to carry into the
first real h2 measurement, not as performance claims.

- `http2_adaptive_window`: leave OFF. The static 2 MiB stream / 5 MiB
  connection windows already exceed the bandwidth-delay product of realistic
  provider links for token streaming (a 100 Mbps x 100 ms path is a 1.25 MB
  BDP; a 2.69 MB/s stream at 100 ms RTT needs 269 KB in flight). Adaptive
  mode's only effect is growing windows toward 16 MiB via periodic BDP
  pings; for latency-bound, low-rate SSE there is nothing for the estimator
  to find, and it adds PING traffic plus window churn bookkeeping. Revisit
  only if a future bulk-download workload shows window-limited throughput.
- `http2_keep_alive_interval`: set to 30 s, keep `keep_alive_while_idle`
  OFF, keep the 20 s default timeout. Rationale: today a silently-dead
  connection mid-stream is bounded only by OS TCP keepalive (hours) or a
  request-level timeout; h2 keep-alive bounds it at interval + timeout
  (50 s worst case) while never firing on healthy streams (any provider
  SSE heartbeat frame resets it). Idle pooled connections stay unpinged,
  which matches the 90 s pool expiry - pinging connections the pool is
  about to close would be pure overhead.
- `http2_initial_stream_window_size` / `http2_initial_connection_window_size`:
  leave at hyper's defaults (2 MiB / 5 MiB). They are already 32x / 80x the
  spec floor, and h2's half-window WINDOW_UPDATE batching makes per-event
  overhead negligible at our chunk sizes.
- No change to pool settings: the 90 s `pool_idle_timeout` and unlimited
  idle-per-host apply to h2 connections unchanged; the ALPN upgrade retry
  (section 1) is transparent.

## Sources

Vendored sources read (all under
C:/Users/openclawuser/optimus-rust-toolchain/cargo/registry/src/
index.crates.io-1949cf8c6b5b557f/):

- reqwest-0.12.28: src/async_impl/client.rs (lines 172-173, 295-302, 342-357,
  372, 526-545, 824-843, 938-969, 1546-1547, 1558-1662, 1671),
  src/connect.rs (1488-1516, 1666-1762), Cargo.toml [features].
- hyper-1.11.1: src/proto/h2/client.rs (48-61, 83-148, 239-247),
  src/proto/h2/mod.rs (29-30), src/proto/h2/ping.rs (1-21, 140-177,
  361-420, 429-515).
- hyper-util-0.1.20: src/client/legacy/client.rs (240-300, 494-545, 1025-1040,
  1324-1537).
- h2-0.4.19: src/client.rs (695-743, 1360-1424), src/frame/settings.rs
  (41-53), src/proto/streams/flow_control.rs (15-19, 93-108),
  src/proto/streams/recv.rs (467-499, 1149-1233), src/proto/connection.rs
  (169-180).
- native-tls-0.2.18 / hyper-tls-0.6.0: Cargo.toml [features].

Workspace files (read-only): Cargo.toml, Cargo.lock,
crates/pi-ai/Cargo.toml, crates/pi-ai/src/providers/shared_http.rs,
crates/pi-ai/src/providers/amazon_bedrock.rs (2202 region),
research/network_latency/provider/provider-baseline.md.
