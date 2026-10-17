# Provider Transport Baseline (PATH A: Optimus -> provider HTTP)

Offline benchmark of the provider HTTP/SSE transport stack against local loopback
mock endpoints. No real providers, no credentials, no network egress. Produced by
the network/IPC performance campaign (worker provider-bench).

- Harness: `crates/pi-ai/src/providers/provider_transport_bench.rs` (cfg(test))
- Raw logs: `results/run-release-final.txt` (primary), `results/run-release-all.txt`
  (earlier run, lighter machine load), `results/run-full.txt`
- Structured data: `results/provider-baseline.json`
- Gateway leg evidence: `results/gateway-probe.txt`

## Environment

| Item | Value |
| --- | --- |
| OS | Windows, git-bash; loopback 127.0.0.1 |
| Rust | stable-x86_64-pc-windows-msvc (1.98.x), cargo test --release |
| Branch | improve/network-ipc-performance @ 6423654c1 |
| Fixture | in-process tokio TcpListener HTTP/1.1 server (this harness) |
| Load caveat | Shared box: four sibling agents compiled/ran concurrently during measurement; medians are stable across runs, p95/p99/max include external load |

## What was and was not covered

Covered (real production code, no replication):
- `stream_openai_completions` (openai_completions.rs:2890) driven end-to-end:
  request building, `post_chat_completions` transport (shared pooled client),
  SSE line reader, typed chunk parsing, `AssistantMessageEvent` delivery.
- Instrumentation through the production `OnStreamObservation` seam
  (`generation_serialize_*`, `generation_send_start`, `generation_headers_complete`,
  `raw_event`, `text`, `terminal`).
- `shared_http::shared_client(ClientPolicy::Default)` (pub(crate)) directly for
  transport-only comparisons.

Not covered (documented, not worked around silently):
- `post_chat_completions` is private to openai_completions.rs (teammate-owned);
  it is reached only through the public entry point above.
- `http_client_pool::acquire` is `pub(super)` inside `openai_completions` and not
  reachable from this module. The guarded (SGLang audit) path pays exactly
  `reqwest::Client::builder().build()` per request for `RedirectPolicy::Generation`
  (verified by reading acquire()), so the fresh-build cost below stands in for it;
  the pool itself is covered by the provider-pool worker's tests.
- TLS and HTTP/2 legs: BLOCKED offline. reqwest's default features enable
  http2+native-tls, but a local TLS fixture needs a certificate the Windows
  trust store accepts; no cert-generation crate (rcgen) exists in the tree, no
  direct `h2` dependency is declared for pi-ai, no new dependencies are allowed,
  and modifying the machine trust store is out of scope. Documented as blocked.

## (a) Connection-reuse proof

32-64 sequential/concurrent requests through the fixture server, TCP accepts counted server-side.

| Client strategy | Requests | TCP accepts | Verdict |
| --- | --- | --- | --- |
| `shared_http::shared_client(ClientPolicy::Default)` | 20 sequential | 1 | one pooled connection serves all |
| fresh `reqwest::Client` per request | 20 sequential | 20 | reconnects every request |

The same effect shows in the concurrency sweep: 32 sequential requests through the
real provider path use exactly 1 connection (level 1 below).

## (b) Client acquisition cost (1000 samples each)

| Path | p50 | p95 | p99 | mean | max |
| --- | --- | --- | --- | --- | --- |
| Pool hit (`shared_client(Default)`) | 0.10 us | 0.10 us | 0.10 us | 0.07 us | 0.2 us |
| Fresh `reqwest::Client::builder().build()` | 23.6 us | 30.7 us | 46.6 us | 28.8 us | 4.26 ms |

A fresh client build is roughly 400x the pooled lookup. Every request that pays a
fresh build (the guarded Generation path, pre-pool code) also pays a TCP connect on
first use: level-1-vs-2 TTFT below shows ~18-19 ms cold vs ~0.5 ms warm on loopback
(first run; final run's cold-accept cost was visible as p99 2.6 ms at level 1).

## (c) TTFT through the real provider path (40 runs)

SSE fixture: 50 chunks, 20 ms interval, 256-byte payloads, warm pooled connection.

| Segment | p50 | p95 | p99 | mean | max |
| --- | --- | --- | --- | --- | --- |
| request start -> response headers | 0.567 ms | 0.727 ms | 1566.8 ms | 39.7 ms | 1566.8 ms |
| request start -> first text event | 0.641 ms | 1.450 ms | 1567.1 ms | 39.9 ms | 1567.1 ms |
| headers -> first parsed event | 0.063 ms | 0.112 ms | 1.260 ms | 0.105 ms | 1.260 ms |
| request start -> done (50 x 20 ms stream) | 1242.5 ms | 1516.6 ms | 2719.1 ms | 1304.0 ms | 2719.1 ms |

The p50 pipeline overhead from parsed headers to the consumer-visible first text
event is 63 us; the entire request-to-first-event median is 0.64 ms on a warm
connection. The p99 tail (1.5 s in the final run, 0.46 s in the lighter earlier
run) is machine-load noise, not transport behavior: the fixture wrote headers in
<1 ms in both runs and the outliers coincide with sibling cargo builds.

Header vs body stall attribution (fixture delays 120 ms):

| Fixture | Observed delay (p50) | Overhead vs fixture |
| --- | --- | --- |
| Headers held 120 ms | 131.8 ms | ~12 ms |
| First body byte held 120 ms | 122.0 ms | ~2 ms |

## (d) Streaming throughput

| Scenario | Payload | Mean throughput | First inter-event gap p50 | Total p50 |
| --- | --- | --- | --- | --- |
| SSE 200 x 1KB chunks (real path) | 204,800 B | 649 KB/s | 0.91 ms | 292.4 ms |
| SSE 200 x 4KB chunks (real path) | 819,200 B | 2.69 MB/s | 0.99 ms | 292.3 ms |
| 4 MB single body (shared client) | 4,194,304 B | 78.1 MB/s | n/a | 82.2 ms |

The per-event pipeline cost is ~1.0 ms p50 regardless of chunk size: throughput
scales with chunk size (4KB chunks deliver ~4.1x the bytes per second of 1KB
chunks at the same event rate). Consumers that need high token throughput should
prefer larger SSE chunks where the provider allows it.

## (e) Concurrency sweep (32 requests per level, semaphore-gated)

| Level | Connections | New connections | TTFT p50 | TTFT p95 | TTFT p99 | Total p50 | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 1 | 0.47 ms | 0.70 ms | 2.63 ms | 122.8 ms | 0 |
| 2 | 2 | 1 | 0.47 ms | 0.56 ms | 0.96 ms | 126.0 ms | 0 |
| 4 | 4 | 2 | 0.45 ms | 0.60 ms | 1.40 ms | 125.0 ms | 0 |
| 8 | 8 | 4 | 0.47 ms | 1.14 ms | 30.9 ms | 161.6 ms | 0 |
| 16 | 16 | 8 | 1.44 ms | 32.4 ms | 58.9 ms | 187.7 ms | 0 |
| 32 | 32 | 16 | 3.10 ms | 23.6 ms | 25.6 ms | 145.0 ms | 0 |

The shared pool opens exactly one connection per in-flight request (no multiplexing
on HTTP/1.1) and reuses them across levels. TTFT stays flat through level 4 and
degrades at 16-32, where the in-process fixture server and the client compete for
the same cores. No request failed at any level.

## (f) Idle expiry (90 s pool idle timeout)

| Step | Accept count |
| --- | --- |
| request 1 | 1 |
| sleep 95 s | - |
| request 2 | 2 |

The pooled idle connection is closed at the 90 s `pool_idle_timeout`; the second
request reconnects. A request issued in the 90-95 s window pays one extra TCP
connect (~0.5 ms warm-path equivalent, tens of ms cold on this box).

## (g) Error paths

| Scenario | p50 | p95 | p99 | Outcome |
| --- | --- | --- | --- | --- |
| HTTP 429 + Retry-After: 1 | 0.37 ms | 357.2 ms | 1289.8 ms | stream fails fast, error classified rate-limit; tail is machine load |
| HTTP 500 | 0.35 ms | 0.40 ms | 0.43 ms | stream fails fast, error classified server error |
| Mid-stream TCP disconnect (5 of 10 chunks delivered) | 46.5 ms | 48.7 ms | 49.2 ms | stream fails; partial events preserved 20/20 runs |
| Cancellation after first event | 0.77 ms | 1.12 ms | 1.23 ms | abort lands <1.5 ms after request_cancel |

Disconnect detection costs ~6 ms beyond the fixture's last-sent event; no hang, no
retry, partial output preserved.

## Local gateway leg (read-only probe)

| Probe | Result |
| --- | --- |
| GET http://127.0.0.1:43120/ | 401 JSON, 62 ms (first, includes connect) |
| GET http://127.0.0.1:43120/health | 401 JSON, 2.1 ms |
| Protocol | plain HTTP/1.1, no TLS |
| Keep-alive | `Connection: keep-alive`, `Keep-Alive: timeout=5` |
| Connection reuse | second sequential transfer on one client reused the connection (num_connects=0) |
| Body (no credentials) | `{"error":{"message":"Invalid local gateway credential","type":"authentication_error"}}` |

The gateway is reachable, plain HTTP/1.1, keep-alive with a 5 s idle window, and
answers unauthenticated requests with a 401 JSON error in ~2 ms warm.

## Findings summary

1. The process-wide shared client gives real reuse: 1 TCP accept for a whole
   sequence (and for the 32-request level-1 sweep); fresh-client-per-request pays
   1 accept per request plus a ~24 us build (release).
2. Warm-path TTFT is sub-millisecond: 0.64 ms median request-to-first-event,
   of which 63 us is headers-to-first-event parsing/delivery overhead.
3. Per-event SSE pipeline cost is ~1.0 ms p50, independent of chunk size; bytes
   throughput scales with chunk size (4KB chunks: 2.7 MB/s vs 1KB: 0.65 MB/s).
4. Concurrency scales without failures to 32 in-flight requests, one connection
   each; TTFT degrades past 8 concurrent on this box (fixture and client share cores).
5. Idle connections die at the 90 s pool timeout; the next request reconnects.
6. Error paths are fast and clean: 429/500 fail in ~0.4 ms median, mid-stream
   disconnects are detected ~6 ms after the last event with partial output kept,
   cancellation lands in <1.5 ms.
7. The guarded `http_client_pool` Generation path (SGLang audit) builds a fresh
   client per request (no pooling by design); its per-request cost is the fresh
   build row in (b) plus a TCP connect on first use. The provider-pool worker owns
   that file's evolution.
8. HTTP/2 and TLS legs are blocked offline under the no-new-deps constraint; see
   the coverage section.

## Reproduction

```
export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup
export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo
export RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-msvc
export PATH=/c/Users/openclawuser/optimus-rust-toolchain/cargo/bin:$PATH
export CARGO_TARGET_DIR=C:/Users/openclawuser/optimus-net-target-w5
cargo test -p pi-ai --release provider_transport_bench -- --ignored --nocapture
# full sweep including the 95 s idle-expiry test:
cargo test -p pi-ai --release provider_transport_bench -- --include-ignored --nocapture
```

Total suite runtime: ~97 s release, of which 95 s is the deliberate idle-expiry
sleep. Every other test stays under 50 s.


## Throttled confirmation run (post-directive)

After the campaign-wide CPU directive, the full suite was re-run through the
shared gate (`netbuild.ps1`: one invocation at a time, -j 2, BelowNormal
priority, 4-CPU affinity, --test-threads=2): 9 passed, 0 failed, 96.0 s
(`results/run-gate-confirm.txt`). Median-level numbers were unchanged within
noise (delayed-first-byte fixture: 121.6 ms p50 vs 122.0 ms unthrottled).
The scorecard tables above use the unthrottled pre-directive runs.
