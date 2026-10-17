# Round-2 addendum: hot-path consumer fixes and transport keep-alive work

Prepared for append to PR #167 (branch improve/network-ipc-performance).
Round 1 landed as 7 commits (159bc336d..522913929); round 2 stacks on
522913929 plus uncommitted worktree changes. This file was frozen by the
ledger/PR lane on 2026-10-17 while the round-2 lanes were still in flight:
at freeze the worktree was clean (no round-2 code or scorecards had landed),
so no round-2 lane had reported finals. Every quantitative round-2 table
below is a reserved skeleton; every gap is marked. The parent fills sections
as lane scorecards land, then appends the filled content to the PR body.

Environment (same as round 1): Windows 10.0.26200, AMD EPYC 74F3, rustc
1.98.1 stable-x86_64-pc-windows-msvc, campaign worktree optimus-net-20261017.
Full experiment detail: research/network_latency/EXPERIMENT-LEDGER.md
(round-2 rows QUAD-1 through GW-01, reserved with PENDING cells at this
freeze).

Measurement regime: every filled number must carry the label "measured
under throttled gate (4 CPUs shared, -j 2, BelowNormal)". The inherited
pre-gate contention disclosure still applies, but only to the round-1
artifacts it names (research/network_latency/provider/client-pool-bench.md
tables and the provider-baseline.md primary tables); round-2 numbers must
come from post-directive gate runs.

## Round-2 scope and status at freeze

| ID | Lane | Target (code-grounded) | Status at freeze |
| --- | --- | --- | --- |
| QUAD-1 | completions-quad | Per-event full-message clone in the completions consumer: `partial_message` (crates/pi-ai/src/providers/openai_completions.rs:2756, 13 call sites 2114-2887) clones `output` plus `state.content()` on every delta event | in flight, no finals reported |
| QUAD-2 | responses-quad | Same clone pattern on the responses path: `partial: output.clone()` at 13 sites (crates/pi-ai/src/providers/openai_responses_shared.rs:748-1115) | in flight, no finals reported |
| TCP-01 | tcp-pool | Guarded Count-policy pool caps idle connections at `pool_max_idle_per_host(4)` (crates/pi-ai/src/providers/openai_http_client_pool.rs:98) vs the reqwest default `usize::MAX` (vendored async_impl/client.rs:302); burst-then-steady reconnect churn | in flight, no finals reported |
| TCP-02 | tcp-pool | Keep-alive audit: TCP keepalive defaults, HTTP-layer idle windows, gateway mismatch | in flight, no finals reported |
| HTTP2-01 | http2 | HTTP/2 multiplexing feasibility; offline A/B BLOCKED; feature ENABLED per user instruction (see update) | http2-analysis.md + .changes/http2-alpn-negotiation.md |
| GW-01 | gateway | Gateway keep-alive mismatch: server closes idle connections at 5 s while client pools idle 90 s | in flight, no finals reported |

## Motivation carried from round 1 (landed and sourced; safe to cite)

These numbers are round-1 results already committed in the scorecards; they
motivate the round-2 lanes and may be cited with this provenance:

- Completions consumer cost is clone-dominated and grows with stream length:
  full-consumer text 4KB x200 mean 115762 ns/event vs framing-only
  1594 ns/event; per-event cost grows 31370 -> 186975 ns/event from first
  to last decile (6.0x). Source: research/network_latency/provider/sse-parse-bench.md;
  measured under throttled gate (4 CPUs shared, -j 2, BelowNormal).
- Responses framing+Value-parse reference for the same size class: text 4KB
  at 16KB chunks 9372 -> 4057 ns/event (round-1 SSE-02 result); the
  consumer-side clone on this path was not separately measured in round 1.
  Same source and label.
- Shared-pool concurrency opens one connection per in-flight request and
  reuses them across levels (level 32: 32 connections, 0 failures; pooled
  count-transport c32 made 32 TCP accepts for 320 requests). Sources:
  research/network_latency/provider/provider-baseline.md section e
  (pre-gate contention disclosure) and
  research/network_latency/provider/results/client-pool-bench.json.
- Gateway leg: plain HTTP/1.1, `Connection: keep-alive`,
  `Keep-Alive: timeout=5`; warm 401 in 2.1 ms, cold GET 62 ms, second
  transfer reused the connection. Source:
  research/network_latency/provider/results/gateway-probe.txt.

## Evidence tables (reserved skeletons; no round-2 numbers yet)

Fill protocol: each PENDING cell stays empty until the owning lane's
scorecard lands under research/network_latency/; the parent then copies the
numbers in with the throttle label and the scorecard path, and fills the
matching ledger row (EXPERIMENT-LEDGER.md, round-2 section). A skeleton is
not a result and must not be quoted as one.

### QUAD-1: completions consumer clone fix

Label for filled cells: measured under throttled gate (4 CPUs shared,
-j 2, BelowNormal).

| fixture | arm | ns/event p50 | p95 | p99 | mean | first decile | last decile | delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

Evidence: research/network_latency/provider/round2/completions-quad.md
(reserved; update to the lane's actual scorecard path if it differs).
Claim: GAP (PENDING) - none until the lane reports finals.

### QUAD-2: responses consumer clone fix

Label for filled cells: measured under throttled gate (4 CPUs shared,
-j 2, BelowNormal).

| fixture | arm | ns/event p50 | p95 | p99 | mean | delta |
| --- | --- | --- | --- | --- | --- | --- |
| PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

Evidence: research/network_latency/provider/round2/responses-quad.md
(reserved). Claim: GAP (PENDING).

### TCP-01: pool max-idle churn

Label for filled cells: measured under throttled gate (4 CPUs shared,
-j 2, BelowNormal).

| mode | burst conc | steady p50 | p95 | p99 | reconnects per 100 requests | TCP accepts | failures |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

Evidence: research/network_latency/provider/round2/pool-max-idle-churn.md
(reserved). Claim: GAP (PENDING).

### TCP-02: keep-alive audit (findings table; no latency claim)

| finding | evidence | decision |
| --- | --- | --- |
| PENDING | PENDING | PENDING |

Evidence: research/network_latency/audits/round2-keepalive-audit.md
(reserved). Claim: GAP (PENDING). Note: any keep-alive outcome is framed as
reliability, not latency (see honesty notes).

### HTTP2-01: h2 feasibility (constraint verification; A/B BLOCKED)

Constraint verification performed 2026-10-17 (read-only; no measurements):

| constraint | verification |
| --- | --- |
| client can request cleartext h2 | `http2_prior_knowledge()` exists in reqwest 0.12.28 (vendored src/async_impl/client.rs:1560); the http2 feature is on by default (workspace reqwest declaration uses default features plus json, stream, multipart, socks, gzip, brotli, deflate) |
| no offline h2 server fixture | every in-tree fixture is a hand-rolled HTTP/1.1 TCP listener; h2 0.4.19 is transitive only (Cargo.lock; no workspace Cargo.toml declares it) and may not be declared under the no-new-deps rule; hyper is not a direct pi-ai dependency |
| production path not reproducible | production h2 rides TLS+ALPN to the gateway or remote providers; no server-side TLS fixture exists in-tree and no cert-generation crate (rcgen) may be added |

Decision: BLOCKED for the offline A/B; recorded in the ledger row HTTP2-01.
UPDATE (user-directed, 2026-10-17): after the feasibility analysis landed, the
maintainer directed the feature flip. `native-tls-alpn` is now enabled on the
workspace reqwest, so HTTP/2 is negotiated with any provider that advertises
it via ALPN (previously: never; all provider traffic was HTTP/1.1). Cargo.lock
is unchanged; workspace compilation verified post-flip. The WAN
handshake-elimination benefit remains unmeasured (loopback cannot express
it); recommended h2 client configuration stays in http2-analysis.md section 5
as UNMEASURED. Risk notes (middlebox h2 failures, schannel Win 8.1+) are
documented there. HTTP/2 must not be
presented as measured or as a win anywhere in this PR.

### GW-01: gateway keep-alive

Label for filled cells: measured under throttled gate (4 CPUs shared,
-j 2, BelowNormal).

| probe or metric | baseline | candidate | delta |
| --- | --- | --- | --- |
| PENDING | PENDING | PENDING | PENDING |

Evidence: research/network_latency/provider/round2/gateway-keep-alive.md
(reserved). Claim: GAP (PENDING).

## Updated claims

None at freeze: no round-2 lane has reported finals. GAP (PENDING) - do not
cite. When a lane lands, each added claim must state its scorecard path and
the throttle label, show both raw numbers behind any percentage, and stay
inside the honesty rules below.

## Honesty notes (round 2)

1. BLOCKED h2 A/B. An offline HTTP/2-vs-HTTP/1.1 A/B is not possible under
   the campaign constraints: the client can request cleartext h2 via
   `http2_prior_knowledge()`, but no h2 server fixture can be built without
   new dependencies (h2 transitive-only, hyper not a direct pi-ai dependency,
   all in-tree fixtures HTTP/1.1), and the TLS+ALPN path production uses has
   no offline fixture and no cert-generation crate. Recorded as BLOCKED in
   the ledger (row HTTP2-01); not worked around, not estimated.
2. Keep-alive work is reliability, not latency. The gateway closes idle
   connections at 5 s (`Keep-Alive: timeout=5`, gateway-probe.txt) while
   client pools idle 90 s (shared_http.rs:24,70); fixing that mismatch
   avoids requests meeting dead connections. Warm reconnect costs are
   already sub-3 ms on the measured legs (loopback ~0.5 ms warm-path
   equivalent, provider-baseline.md section b; gateway warm 401 in 2.1-2.3
   ms, gateway-probe.txt), so no latency win may be claimed from keep-alive
   alignment. TCP keepalive is on by default in reqwest 0.12.28 (15 s, 3
   retries, vendored async_impl/client.rs:303-305); it guards half-open
   connections and does not change request latency.
3. TCP_NODELAY is already ON by default in reqwest 0.12.28: the vendored
   default config sets `nodelay: true` (src/async_impl/client.rs:372; the
   `tcp_nodelay` setter is at :1671). Enabling it explicitly is a no-op and
   must never be presented as a round-2 win.
4. Pre-gate contention disclosure (inherited): applies only to the round-1
   artifacts it names; round-2 numbers must come from post-directive gate
   runs.
5. PENDING skeletons are not results. Nothing in the reserved tables may be
   quoted in the PR body, commit messages, or reviews until the lane's
   scorecard lands and the parent fills the cells. A well-proven BLOCKED
   or NO-CHANGE is a valid round-2 result.

## Fill-in protocol for the parent

1. When a lane reports finals: confirm the scorecard exists under
   research/network_latency/, copy its numbers into the skeleton tables
   here with the throttle label, and fill the matching ledger row (PENDING
   cells, decision, evidence path; fix the reserved path if the lane landed
   a different filename).
2. Move claims into "Updated claims" only when every number in the claim
   appears in a landed scorecard with its label.
3. If a lane reports BLOCKED or NO-CHANGE, record exactly that; do not
   soften or inflate it.
4. Do not append any of the GAP (PENDING) sections to the PR body; fill or
   drop them first.
