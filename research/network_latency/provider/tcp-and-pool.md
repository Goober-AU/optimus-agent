# TCP and pool options for provider HTTP clients

Scope: the transport-level `reqwest::ClientBuilder` options of the two
provider client layers - the process-wide policy clients
(`crates/pi-ai/src/providers/shared_http.rs`) and the guarded pool
(`openai_http_client_pool.rs` `build()`). Every option reqwest 0.12.28
exposes on this path is audited with its vendored-source default and a
KEEP/NO-CHANGE decision. Raw measurements:
`results/tcp-pool-churn-bench.json`, from
`cargo test --package=pi-ai --release --lib tcp_pool_churn_bench -- --ignored --nocapture`,
measured under throttled gate (4 CPUs shared, -j 2, BelowNormal) on this
Windows worktree (rustc stable MSVC, reqwest 0.12.28, hyper-util 0.1.20,
socket2 0.6.5 read as a transitive dep only). Two repeats per cell; both
shown as r0/r1. Latency is loopback HTTP/1.1 and understates any WAN effect.

Applicability: the provider path is TCP-only (loopback gateways and remote
providers). The daemon IPC named pipes (`tokio::net::windows::named_pipe` in
`pi-coding-agent`, not reqwest) never see these socket options, and `pi-ai`
contains no reqwest `windows_named_pipe` usage, so every TCP option below
reaches every provider connection the process makes.

## Option audit

| option | reqwest 0.12.28 default | shared_http policies | guarded Count client | decision |
|---|---|---|---|---|
| `pool_idle_timeout` | 90s | set 90s explicitly | unset (90s) | KEEP |
| `pool_max_idle_per_host` | `usize::MAX` (unlimited) | unset (unlimited) | 4 | RAISE to 32 (one line, parent to apply) |
| `tcp_nodelay` | `true` (client.rs "Default is `true`") | on by default | on by default | KEEP - ALREADY ON, no claim |
| `tcp_keepalive` | `Some(15s)` (client.rs `Config::default`) | on by default | on by default | KEEP - ALREADY ON, no claim |
| `tcp_keepalive_interval` | `Some(15s)` | on by default | on by default | KEEP - ALREADY ON, no claim |
| `tcp_keepalive_retries` | `Some(3)` | set, but no-op on Windows | set, but no-op on Windows | KEEP - documented no-op |
| `http2_prior_knowledge` | off | off | off | NO-CHANGE |
| `http2_adaptive_window` | off | off | off | NO-CHANGE |
| `http2_keep_alive_interval`/`timeout`/`while_idle` | disabled | disabled | disabled | NO-CHANGE |

Two options are default-ON in reqwest 0.12.28 and must never be presented as
wins of this change: `tcp_nodelay` (client.rs `tcp_nodelay`, "Default is
`true`") and `tcp_keepalive` (client.rs `Config::default`:
`tcp_keepalive: Some(Duration::from_secs(15))`,
`tcp_keepalive_interval: Some(Duration::from_secs(15))`,
`tcp_keepalive_retries: Some(3))`). Every client built in this repository,
including all four `shared_http` policies and both guarded-pool redirect
policies, already has TCP keepalive armed at 15s idle / 15s interval. The
round-1 audit already verified the same for `TCP_NODELAY`.

## Pool semantics that decide the cap

hyper-util 0.1.20 `pool.rs` enforces `max_idle_per_host` at connection
RETURN time, not at checkout: `put()` drops a connection outright when the
per-(scheme, authority) idle list is already at capacity
(`if self.max_idle_per_host <= idle_list.len() { return; }` -
"max idle per host, dropping connection"). Consequences:

- A connection only churns when MORE THAN `max_idle` connections of one
  host are idle simultaneously. Back-to-back sequential reuse never trips
  the cap: each worker re-acquires its own connection within microseconds.
- Barrier-synchronized bursts separated by an idle gap do trip it: after a
  wave of C concurrent requests, the first `max_idle` returns are pooled and
  the remaining C - `max_idle` are closed, so the next wave reconnects.
- `pool_max_idle_per_host(0)` disables pooling entirely (`Config::is_enabled`
  is `max_idle_per_host > 0`); neither layer sets 0.

The guarded Count client serves exactly the burst shape: `request_count`
(`openai_sglang_budget.rs`) issues one tokenize/prefill POST per agent turn,
turns are seconds apart, and sibling agents count concurrently. The prior
client-pool bench (back-to-back loops, 320 requests) measured exactly
`concurrency` accepts even at the cap of 4 - that pattern structurally
cannot trip the cap, which is why the churn question needed the burst-gap
measurement below.

## Measurement: idle cap vs TCP accepts (burst-gap, the Count shape)

12 barrier-synchronized waves, one request per worker per wave, 25 ms idle
gap between waves, keep-alive HTTP/1.1 echo fixture, loopback. Accept counts
are deterministic (no variance across repeats); latency is r0/r1.

| arm (burst-gap 25ms, 12 waves) | conc | requests | TCP accepts | accepts/req | p50 us | p95 us |
|---|---|---|---|---|---|---|
| Count policy, max_idle 4 | 8 | 96 | 52/52 | 0.542 | 1460/1546 | 1969/1964 |
| Count policy, max_idle 4 | 16 | 192 | 148/148 | 0.771 | 3522/3046 | 4862/4330 |
| Count policy, max_idle 4 | 32 | 384 | 340/340 | 0.885 | 6328/5865 | 8257/8188 |
| max_idle 32 | 8 | 96 | 8/8 | 0.083 | 480/468 | 1531/3506 |
| max_idle 32 | 16 | 192 | 16/16 | 0.083 | 780/840 | 2894/2651 |
| max_idle 32 | 32 | 384 | 32/32 | 0.083 | 1518/1699 | 7307/5846 |
| reqwest default (unlimited) | 8 | 96 | 8/8 | 0.083 | 473/511 | 1277/1363 |
| reqwest default (unlimited) | 16 | 192 | 16/16 | 0.083 | 832/1037 | 2595/3476 |
| reqwest default (unlimited) | 32 | 384 | 32/32 | 0.083 | 1973/1547 | 5594/5105 |
| shared client (Default policy) | 8 | 96 | 8/8 | 0.083 | 825/429 | 2757/1744 |
| shared client (Default policy) | 16 | 192 | 16/16 | 0.083 | 1216/751 | 3427/2666 |
| shared client (Default policy) | 32 | 384 | 32/32 | 0.083 | 1656/1876 | 7356/4781 |

The claim is proven, and the accept counts match the hyper-util `put()`
semantics exactly, cell for cell, with zero run-to-run variance:

- cap 4, conc 8: first wave 8 connects + 11 waves x 4 pooled returns kept
  (8 + 11 x 4 = 52 accepts; 4 workers of 8 reconnect every wave).
- cap 4, conc 16: 16 + 11 x 12 = 148 accepts (12 of 16 reconnect per wave).
- cap 4, conc 32: 32 + 11 x 28 = 340 accepts (28 of 32 reconnect per wave).
- cap 32 (or unlimited): exactly `concurrency` accepts total, both repeats.

So at concurrency C > 4 with per-turn count requests, the Count policy
reconnects (C - 4) workers on every wave after the first: 10.6x the TCP
accepts of the raised cap at C=32 (340 vs 32). On loopback the reconnect
cost is visible but modest (p50 6.3 ms vs 1.5 ms at C=32, measured under
the throttled gate); on a real remote provider each dropped connection
would additionally pay a fresh TCP + TLS handshake (not measurable offline,
no TLS fixture in-repo). Latency at cap 4 grows with concurrency because
each wave serializes 28 fresh loopback connects behind 2 test threads.

## Measurement: steady pattern (no churn anywhere)

Back-to-back requests per worker, 320 requests total - the prior
client-pool bench shape. Every arm, including cap 4, opens exactly
`concurrency` connections; back-to-back reuse never returns more than one
idle connection per worker, so the cap never fires.

| arm (steady, 320 requests) | conc | TCP accepts | p50 us | p95 us |
|---|---|---|---|---|
| Count policy, max_idle 4 | 8 | 8/8 | 375/348 | 451/512 |
| Count policy, max_idle 4 | 16 | 16/16 | 1212/754 | 2403/1210 |
| Count policy, max_idle 4 | 32 | 32/32 | 1453/1414 | 6663/6184 |
| reqwest default (unlimited) | 8 | 8/8 | 353/349 | 413/448 |
| reqwest default (unlimited) | 16 | 16/16 | 691/694 | 765/1229 |
| reqwest default (unlimited) | 32 | 32/32 | 1414/1333 | 6237/6624 |
| keepalive OFF | 8 | 8/8 | 348/347 | 461/423 |
| keepalive OFF | 16 | 16/16 | 697/698 | 972/779 |
| keepalive OFF | 32 | 32/32 | 1403/1359 | 6889/5396 |

This is why round 1 saw no churn at the cap: its loops were back-to-back.
The churn is real but pattern-dependent, and the guarded Count pool's
real pattern (per-turn bursts with idle gaps between turns) is the one that
trips it.

## TCP keepalive: default-on, semantics, and the honest latency story

Exact plumbing for a `Client::builder()` client on this worktree:
reqwest `ClientBuilder::tcp_keepalive/interval/retries` ->
hyper-util `HttpConnector::TcpKeepaliveConfig {time, interval, retries}`
-> socket2 0.6.5 `TcpKeepalive`. On Windows, `set_tcp_keepalive` issues one
`WSAIoctl(SIO_KEEPALIVE_VALS)` with
`{onoff: 1, keepalivetime: 15_000 ms, keepaliveinterval: 15_000 ms}` per
connected socket during connect. On Unix the same fields map to
`TCP_KEEPIDLE`/`TCP_KEEPALIVE`, `TCP_KEEPINTVL`, and `TCP_KEEPCNT`.

One Windows caveat verified in the vendored sources: hyper-util 0.1.20
compiles `ka_with_retries` only for Unix-like targets, so reqwest's
`tcp_keepalive_retries(3)` default is a NO-OP on Windows (the OS default
retransmit behavior applies); setting it explicitly would change nothing
here. A `set_tcp_keepalive` failure is logged (`warn!`) and the connection
proceeds without keepalive - it never fails a request.

Reliability rationale (not a latency claim): during long server-side
reasoning with no SSE bytes flowing, a provider/LB idle timeout (Azure LB
240s, AWS NLB 350s are the common floors) can drop the flow state;
SO_KEEPALIVE probes every 15s keep the path alive. Because reqwest 0.12.28
defaults this ON for every client, no code change was needed for streaming
reliability, and none is claimed. Measured latency delta of the default:
keepalive fully OFF vs default-ON, steady pattern, is within run-to-run
noise (p50 348-353 us at conc 8 in both arms; the only cost is one
`WSAIoctl` per connect, and no probe fires inside the bench because no
connection is idle 15s). No latency win is claimed from keepalive.

## HTTP/2 options: why NO-CHANGE

- `http2_prior_knowledge`: forces h2 without upgrade/ALPN. The local model
  gateways are HTTP/1.1 servers; prior knowledge would break them. Remote
  providers already negotiate h2 over TLS when both sides support it.
- `http2_adaptive_window`: h2 flow-control tuning; the loopback bench
  fixture is HTTP/1.1, so no local measurement is possible, and a remote
  A/B would need live providers (out of scope offline).
- `http2_keep_alive_interval` (+`timeout`, `while_idle`): sends h2 PING
  frames. The local gateway leg is HTTP/1.1, so this is unmeasurable
  offline; providers differ in ping tolerance, and a missed ping timeout
  closes the connection - a new failure mode for streams that today work.
  TCP keepalive (default-on, above) already covers silent-peer detection
  for the provider HTTP/1.1 and h2 legs alike.

## Decisions

1. KEEP `pool_idle_timeout(90s)` in `shared_http.rs`: matches the pooled
   `responses_transport` precedent; idle retention is already explicit.
2. KEEP no `pool_max_idle_per_host` in `shared_http.rs`: reqwest default is
   unlimited, and the shared-client burst measurement shows zero churn
   (accepts = concurrency at 8/16/32). There is nothing to fix here.
3. KEEP TCP keepalive as-is everywhere: reqwest 0.12.28 default-arms
   15s/15s/3 on every client. Adding `tcp_keepalive(...)` calls would be a
   no-op restatement of the default, and the retries field is a Windows
   no-op anyway.
4. PROPOSE (parent to apply, one line, `openai_http_client_pool.rs`
   `build()`): change `.pool_max_idle_per_host(4)` to
   `.pool_max_idle_per_host(32)` in the `RedirectPolicy::Count` arm.
   Measured effect at the Count pool's real per-turn burst shape:
   TCP accepts 340 -> 32 at concurrency 32 (10.6x fewer connects),
   148 -> 16 at concurrency 16, 52 -> 8 at concurrency 8; p50 6.3ms ->
   1.5ms at concurrency 32 (measured under throttled gate, 4 CPUs shared,
   -j 2, BelowNormal, loopback). Rationale for 32 over unlimited: the
   guarded pool itself caps clients at `MAX_CLIENTS = 16`, and 32 matches
   the campaign's measured maximum agent concurrency while still bounding
   idle sockets. Because hyper-util only drops at return time, a cap of 32
   never closes a connection that a later request would have reused at
   any concurrency measured.
5. NO-CHANGE on all HTTP/2 options (reasons above).

## Guards added

Three unit tests in `shared_http.rs` (from the round-2 coverage audit):
`no_policy_ever_sets_a_client_builder_total_timeout` (no total
`timeout`/`read_timeout` on any policy; Kiro's intended 15s
`connect_timeout` is the only allowed timeout field; also asserts
TCP_NODELAY stays on), `keepalive_retention_is_stated_and_testable`
(pins `POOL_IDLE_TIMEOUT = 90s` to the documented policy; the builder Debug
does not print pool options), and
`distinct_policies_do_not_share_pooled_connections` (Default and
RedirectNone against one fixture: 2 accepts; a second Default request
reuses: still 2). The churn bench itself is `#[ignore]`-gated and writes
`results/tcp-pool-churn-bench.json` (a new file; the round-1
`client-pool-bench.json` evidence is untouched).
