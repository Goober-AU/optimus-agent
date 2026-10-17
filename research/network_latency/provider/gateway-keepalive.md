# Gateway keep-alive: the 5s server close vs 90s client pool idle

Read-only investigation of the local model gateway leg
(`http://127.0.0.1:43120`, OptimusAgentCloudModelGateway). Question: the
gateway closes idle keep-alive connections after ~5 s while our shared client
pools them for 90 s (`crates/pi-ai/src/providers/shared_http.rs:24`,
`POOL_IDLE_TIMEOUT`). Does that mismatch cost a failed-reuse retry, an error,
or nothing?

Method: Python 3.11 socket-level probes (uv cpython), no credentials, no
model calls, only unauthenticated `GET /` and `GET /health` (both answer 401
JSON). Source analysis of vendored hyper 1.11.1, hyper-util 0.1.20 and
reqwest 0.12.28. No code changed; no builds or tests run, so nothing was
measured under the throttled build gate. All probe timings below are local
loopback observations on this shared 4-CPU box, recorded while sibling agents
may have been building; the probes are single-socket and sub-millisecond, so
contention effects on them are minimal, but treat absolute values as
indicative, not benchmark-grade.

## Probe evidence

Raw probe log (JSON lines): reproduced in full in the appendix table below.
Probes run 2026-10-17 06:30:54-06:32:04Z, script
`%TEMP%/gateway_keepalive_probe.py` (kept outside the repo; not committed).

### (a) Reuse within the window, and what a 10 s-gap client sees

Three rounds, one connection per round, request 1 s apart:

| round | TCP connect ms | 1st response ms | reused response ms |
|---|---|---|---|
| 0 | 0.315 | 0.344 | 0.459 |
| 1 | 0.278 | 0.399 | 0.553 |
| 2 | 0.262 | 0.353 | 0.557 |

Reuse works inside the window; second request on the same socket answers in
~0.5 ms.

10 s gap (three rounds, fresh connection each): the request write succeeds
locally (0.10-0.15 ms) because the server closed gracefully (FIN, below) and
the local kernel still accepts the send; the following `recv` then fails
within ~0.1 ms with `WinError 10053` (WSAECONNABORTED, the local stack
aborting after the peer's RST to data on a closed socket). No hang, no
multi-second timeout: a naive client that reuses a dead connection learns of
it in under 0.2 ms. A fresh reconnect immediately after costs 0.25-0.26 ms
TCP connect (one 9.7 ms outlier, first socket in that process) and
0.38-0.65 ms connect+request+401 response.

### (b) How the server actually closes

Socket-level watch after one response (three rounds): the server sends a
graceful TCP FIN exactly 6.0 s after the last response byte in all three
runs; `recv` then returns EOF. No RST, no extra bytes, and no
`Connection: close` header ever appears (responses keep
`Connection: keep-alive`). The advertised `Keep-Alive: timeout=5`
understates the measured 6.0 s close. A request sent 5.5 s into idleness is
still served (0.51 ms), so the usable window is ~6 s. A second probe
(request at t=0, another at t=3) closed 6.0 s after the *second* response:
the idle timer resets per request.

### (c) Header shapes for retry

Both `/` and `/health`: `HTTP/1.1 401`, `content-type: application/json`,
`content-length: 86`, `Date`, `Connection: keep-alive`,
`Keep-Alive: timeout=5`; body
`{"error":{"message":"Invalid local gateway credential","type":"authentication_error"}}`.
No `Retry-After`, no `WWW-Authenticate`, nothing else. The gateway leg
therefore offers no header-based retry pacing signal on 401. A 429 was not
probed: triggering it requires credentials or a model call, both out of
scope for this read-only mission.

## What the real client does with a dead pooled connection

The naive-socket view above is not what reqwest/hyper-util experience. Three
defense layers handle the mismatch (all citations from the vendored crates
in `C:/Users/openclawuser/optimus-rust-toolchain/cargo/registry/src`):

1. **Asynchronous EOF detection while pooled.** The HTTP/1 connection task is
   spawned at connect time and keeps running while the connection sits in
   the pool (hyper-util `client/legacy/client.rs:575`, "http1 handshake
   complete, spawning background dispatcher task"). While idle, the
   dispatcher parks in `require_empty_read`
   (hyper `proto/h1/conn.rs:454-477`, entered via `poll_read_keep_alive`,
   `conn.rs:431`). When the gateway's FIN arrives, the task wakes, reads 0
   bytes, logs "found EOF on idle connection, closing"
   (`conn.rs:471`), closes the read side, and the connection future
   completes (`proto/h1/dispatch.rs:143-161`, `Dispatched::Shutdown`),
   dropping the socket. From then on `SendRequest::is_ready()` is false
   (hyper `client/conn/http1.rs:184`).

2. **Checkout-time filtering.** The pool drops dead entries when handing
   them out: `IdlePopper::pop` skips any entry failing `is_open()`
   (hyper-util `client/legacy/pool.rs:304-306`, "removing closed
   connection"; trait comment at `pool.rs:30-36` exists precisely for this,
   hyper issue 1429). For `PoolClient`, `is_open()` is
   `!is_poisoned() && is_ready()` (`client/legacy/client.rs:854`), i.e. the
   state maintained by layer 1. A waiter that loses the race gets
   `Error::CheckedOutClosedValue` (`pool.rs:628-639`), and
   `connection_for` loops and retries the checkout
   (`client/legacy/client.rs:368-383`). Net effect: a request whose pooled
   connection died at 6 s never writes to the dead socket; it transparently
   pays one fresh loopback TCP connect instead.

3. **Dispatch-race retry.** If the FIN lands between the `is_open()` check
   and the send, hyper-util still retries: `try_send_request` returns the
   un-started request via `err.take_message()` and classifies the failure
   `TrySendError::Retryable` (`client/legacy/client.rs:322-338`), and
   `send_request` retries the loop when the connection was reused and
   `retry_canceled_requests` holds (`client/legacy/client.rs:241-272`,
   default `true` at `client.rs:1034`). reqwest 0.12.28 never disables it
   (no occurrence of `retry_canceled` in reqwest sources) and wires
   `pool_idle_timeout(90s)` plus `pool_timer(TokioTimer)` into the same
   legacy client (`reqwest/src/async_impl/client.rs:974-976`, default
   `pool_idle_timeout` at line 301).

The 90 s idle timeout itself is enforced only client-side
(`Expiration::expires`, hyper-util `client/legacy/pool.rs:765-780`, checked
at checkout `pool.rs:314` and by the 90 s idle interval `pool.rs:483-505`,
which also evicts entries already closed at `pool.rs:492`). hyper's client
does not parse the server's `Keep-Alive: timeout=5` hint (no such parsing in
hyper `proto/h1/role.rs` client path), so nothing shrinks the client's 90 s
retention from the server's advertisement.

## Verdict

**NO-CHANGE: keep `POOL_IDLE_TIMEOUT = 90 s`.** The mismatch is fully
handled for the common case and the residual cost is not measurable against
this leg's traffic profile:

- When a gateway request follows a >6 s gap (the normal case: generation
  requests take seconds and inter-turn gaps exceed 6 s), the pooled entry is
  already dead and filtered locally by layer 2. The request pays one fresh
  loopback connect: measured 0.26-0.44 ms TCP connect, 0.38-0.65 ms full
  exchange; the in-repo ceiling for the whole fresh-vs-pooled difference on
  loopback (including reqwest path overhead) is ~0.45 ms at p50
  (`research/network_latency/provider/client-pool-bench.md`, 579 us fresh
  vs 134 us pooled). That is noise against a network leg whose model
  inference takes seconds.
- When the gap is <6 s, the connection is still alive and reuse works
  (measured 0.5 ms second response).
- The only user-visible failure mode is the narrow race where a request is
  written onto a connection whose FIN was in flight and not yet processed
  (write succeeds, RST follows, response never arrives). That surfaces as a
  hyper "connection closed before message completed"-class error and is NOT
  retried (the request already started; `take_message()` returns None). The
  window is sub-millisecond per ~6 s idle boundary and requires the dispatch
  to straddle the exact expiry instant; layer 3 covers every earlier
  instant. Shortening the client idle would narrow but not remove this
  window.

Would a shorter loopback pool idle or keep-alive probes help? No:

- A ~5 s idle for the gateway origin would eliminate most of the race
  window but save at most the ~0.45 ms fresh-connect premium, only for gaps
  in the 5-6 s band (longer gaps pay the connect either way; shorter gaps
  already reuse). reqwest exposes no per-origin idle timeout, so this needs
  a separate client instance per origin (the shared pool is policy-keyed,
  not origin-keyed), for a sub-millisecond return on a leg dominated by
  model latency.
- `POOL_IDLE_TIMEOUT` is global to the shared clients (`shared_http.rs`).
  Remote providers with TLS handshakes benefit from the 90 s retention;
  shortening it to shave a loopback reconnect trades a real 10-100 ms
  reconnect for a 0.5 ms one.
- TCP keep-alive probes (reqwest `tcp_keepalive` -> `SO_KEEPALIVE`) do not
  interact with the gateway's HTTP idle close at all, and HTTP/2 pings do
  not apply to this HTTP/1.1 leg. Nothing to enable.

Risks accepted with NO-CHANGE: (1) the dead-entry race above, pre-existing
hyper-util behavior, cost one failed request per straddled boundary at
worst; (2) up to 90 s of a closed-socket handle lingering in the pool map
before the idle interval sweeps it (`pool.rs:483-505`) - bounded by
`max_idle_per_host` and per-origin request counts, negligible here.

## Appendix: full probe log

| probe | key values |
|---|---|
| headers / | connect 0.435 ms; first response 0.504 ms; 401; content-type application/json; content-length 86; Date; Connection: keep-alive; Keep-Alive: timeout=5 |
| headers /health | connect 9.898 ms (first socket in process); response 0.349 ms; identical shape |
| reuse_1s r0/r1/r2 | connect 0.315/0.278/0.262 ms; first response 0.344/0.399/0.353 ms; reused response 0.459/0.553/0.557 ms |
| boundary 4.5 s | response ok, 0.561 ms |
| boundary 5.5 s | response ok, 0.506 ms |
| timeout_reset | request at t=3 s served; FIN 6.0 s after that response |
| idle_close r0/r1/r2 | FIN (recv EOF) at 6.0/6.0/6.0 s after last response; no RST, no stray bytes |
| gap_10s r0/r1/r2 | send ok 0.145/0.106/0.103 ms; recv fails 0.096/0.052/0.054 ms with WinError 10053 |
| gap_10s_recovery r0/r1/r2 | fresh connect 0.254/0.259/9.706 ms; full exchange 0.648/0.648/0.377 ms, all 401 |

Measurement conditions: single-socket Python probes on 127.0.0.1, Windows;
loopback; sub-millisecond exchanges; recorded 2026-10-17 ~06:31Z. These are
local probe timings, not throttled-gate measurements; no cargo build, check,
or test was run for this investigation.
