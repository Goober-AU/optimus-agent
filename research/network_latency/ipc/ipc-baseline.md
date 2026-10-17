# IPC baseline scorecard: agent-message transport (fresh vs persistent)

Campaign: network/IPC performance, telemusai/optimus-agent v0.1.34 @ 6423654c1,
branch `improve/network-ipc-performance`, worktree `optimus-net-20261017`.

## What this measures

The worker->supervisor agent-message path (`send_message`, `list_agent_peers`)
against an echo-supervisor fixture that mirrors the production server shape: a
proactive `daemon_hello`, newline-JSON requests, one response per request, and
one new pipe instance per connection. Two arms run the identical fixture,
timing, and metrics:

- **fresh**: the pre-campaign behavior replicated verbatim from
  `agent_message_transport.rs` @ 6423654c1: one `DaemonClient` per call
  (connect 1s -> hello 1s -> one request -> close). This arm is an inlined
  replica, not a call into current transport code, because the transport now
  delegates to the persistent manager. Any drift between the replica and the
  original is a measurement risk (see Caveats).
- **persistent**: the current real transport
  (`agent_message_transport::send_message` / `list_peers`), which delegates to
  the process-wide `AgentMessageConnectionManager`.

Harness: `crates/pi-coding-agent/src/modes/daemon/agent_message_bench.rs`.
Timing wraps the transport call only (message body and selectors are built
before the timed window). Each scenario uses a fresh fixture with 50 warmup
requests unless noted; steady-state scenarios collect 500 samples per config
for tail percentiles.

## Environment

| field | value |
| --- | --- |
| OS | Windows 10.0.26200 (named pipes `\\.\pipe\optimus-agent-bench-<uuid>`) |
| CPU | AMD EPYC 74F3 24-Core Processor |
| rustc | 1.98.1 (48a229cea 2026-09-01), stable-x86_64-pc-windows-msvc |
| profile | release (headline tables) and debug (secondary section), one gate invocation each |
| runtime | tokio multi_thread (default worker threads) |

All numbers below are measured under throttled gate (4 CPUs shared, -j 2,
BelowNormal): the campaign-wide build gate pins builds to 4 shared CPUs at
BelowNormal priority, `-j 2`, and the test harness to `--test-threads=2`.
Five agents share the machine and the gate queue concurrently. Absolute
numbers are therefore not clean-room; cross-arm comparisons within one run
share the binary, the fixture, and the machine state.

## Matrix coverage

| group | configs | samples per config |
| --- | --- | --- |
| A. message size sweep (64B, 256B, 1KiB, 4KiB, 16KiB, 64KiB, 256KiB) | 7 | 500 |
| B. concurrency sweep (1, 2, 4, 8, 16, 32; 256B) | 6 | 500 |
| C. topology sweep (1->1, 1->4, 4->1, 4->4; 256B, 4 senders) | 4 | 500 |
| D1. cold (first 50 requests, no warmup) | 1 | 50 |
| D2. burst (32 tasks, barrier release, one request each) | 1 | 32 |
| D3. sustained 10s (concurrency 1 and 8) | 2 | duration-bound |
| D4. churn (server closes after each response) | 1 | 500 |
| D5. restart (accept loop paused ~1.5s mid-run) | 1 | 60 |
| E. list_peers (1, 10, 50 peers) | 3 | 500 |

Both arms run the identical matrix via `MATRIX_LOCK` (the two tests never
overlap in one process; both share the process-wide connection manager).

Coverage actually run: the full 26-config matrix per arm in BOTH profiles
(release and debug), 2 gate invocations total (one debug + one release), all
green. Nothing skipped. One scenario limitation: the restart pause (1.5 s at
+200 ms) fires AFTER the 60-request measured window completes, so the restart
scenario did not actually exercise a mid-run listener pause in either arm or
profile; treat restart rows as a second warm run (harness gap, see findings).

## Caveats

- The fresh arm is an inlined replica of the pre-change transport, not a call
  into the 6423654c1 code itself. It was copied function-for-function
  (connect 1s -> hello 1s -> one request 30s -> close) from
  `agent_message_transport.rs` @ 6423654c1; drift between the replica and the
  original would distort the fresh numbers.
- The fixture is an echo supervisor, not the real supervisor: it answers
  `send_message` with a delivered receipt and `list_agent_peers` with N
  synthetic peers. Real supervisor work (roster lookup, delivery journaling,
  worker forwarding) is NOT included in either arm. The measurement isolates
  transport/connection costs.
- Numbers are throttled (see Environment) and not comparable across machines
  or against clean-room runs.
- Burst collects only 32 samples; its percentiles beyond p50 are indicative
  only. Cold collects 50 samples with no warmup and no tail claims.
## Release-profile results (headline)

Both matrix tests green: `2 passed; 0 failed` in 54.1 s (single gate invocation,
`--test-threads=2`; arms ran sequentially inside one process). Raw data:
`results/baseline-1792212479204.jsonl` (persistent), `results/baseline-1792212505009.jsonl`
(fresh). All numbers measured under throttled gate (4 CPUs shared, -j 2, BelowNormal).

### fresh arm (pre-change behavior, inlined replica) - release

| op | scenario | topo | conc | n | err | p50 | p90 | p95 | p99 | mean | max | tput/s | accepts | server reqs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| send_message/64B | warm | 1->1 | 1 | 500 | 0 | 0.269 | 0.318 | 0.350 | 1.470 | 0.312 | 7.511 | 3202.7 | 550 | 1048 |
| send_message/256B | burst | 1->4 | 32 | 32 | 0 | 70.764 | 163.806 | 175.149 | 198.020 | 83.381 | 198.020 | 161.5 | 82 | 154 |
| send_message/256B | churn | 1->1 | 1 | 500 | 0 | 0.358 | 0.424 | 0.460 | 0.641 | 0.380 | 5.438 | 2628.2 | 550 | 550 |
| send_message/256B | cold | 1->1 | 1 | 50 | 0 | 0.327 | 0.351 | 0.371 | 0.383 | 0.320 | 0.383 | 3126.1 | 50 | 95 |
| send_message/256B | restart | 1->1 | 1 | 60 | 0 | 0.347 | 0.424 | 0.450 | 0.487 | 0.358 | 0.513 | 2791.4 | 110 | 214 |
| send_message/256B | sustained-10s | 1->1 | 1 | 28125 | 0 | 0.353 | 0.393 | 0.409 | 0.463 | 0.355 | 15.840 | 2812.4 | 28175 | 56346 |
| send_message/256B | sustained-10s | 1->1 | 8 | 42715 | 0 | 0.366 | 0.461 | 10.911 | 33.712 | 1.874 | 248.178 | 4259.1 | 42765 | 85528 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 0.268 | 0.310 | 0.325 | 0.353 | 0.269 | 0.392 | 3722.9 | 550 | 1058 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 0.257 | 0.306 | 0.328 | 0.359 | 0.263 | 0.467 | 3789.9 | 550 | 1068 |
| send_message/256B | warm | 1->1 | 2 | 500 | 0 | 0.252 | 0.294 | 0.310 | 0.374 | 0.410 | 32.611 | 3884.6 | 550 | 1100 |
| send_message/256B | warm | 1->1 | 4 | 500 | 0 | 0.280 | 0.355 | 0.393 | 11.351 | 0.874 | 66.907 | 4072.7 | 550 | 1100 |
| send_message/256B | warm | 1->1 | 8 | 500 | 0 | 0.276 | 0.391 | 1.834 | 33.007 | 1.654 | 112.687 | 2949.2 | 550 | 1100 |
| send_message/256B | warm | 1->1 | 16 | 500 | 0 | 0.257 | 0.315 | 0.365 | 113.397 | 2.982 | 168.976 | 2833.3 | 550 | 1100 |
| send_message/256B | warm | 1->1 | 32 | 500 | 0 | 0.286 | 0.478 | 54.618 | 259.091 | 10.488 | 326.682 | 1510.1 | 550 | 1100 |
| send_message/256B | warm-topology | 1->1 | 4 | 500 | 0 | 0.338 | 0.399 | 0.421 | 22.314 | 0.901 | 66.399 | 3207.2 | 550 | 1100 |
| send_message/256B | warm-topology | 1->4 | 4 | 500 | 0 | 0.319 | 0.382 | 0.400 | 21.730 | 0.837 | 44.338 | 3690.9 | 550 | 1100 |
| send_message/256B | warm-topology | 4->1 | 4 | 500 | 0 | 0.293 | 0.382 | 0.419 | 11.462 | 0.869 | 88.745 | 3150.2 | 550 | 1100 |
| send_message/256B | warm-topology | 4->4 | 4 | 500 | 0 | 0.340 | 0.510 | 0.672 | 21.887 | 1.026 | 54.771 | 3158.5 | 550 | 1100 |
| send_message/1024B | warm | 1->1 | 1 | 500 | 0 | 0.289 | 0.337 | 0.348 | 0.395 | 0.292 | 0.585 | 3426.6 | 550 | 1075 |
| send_message/4096B | warm | 1->1 | 1 | 500 | 0 | 0.307 | 0.373 | 0.402 | 0.462 | 0.315 | 0.545 | 3172.1 | 550 | 1085 |
| send_message/16384B | warm | 1->1 | 1 | 500 | 0 | 0.350 | 0.403 | 0.423 | 0.503 | 0.359 | 0.549 | 2785.7 | 550 | 1093 |
| send_message/65536B | warm | 1->1 | 1 | 500 | 0 | 0.653 | 0.993 | 1.098 | 2.713 | 0.769 | 8.885 | 1301.1 | 550 | 1089 |
| send_message/262144B | warm | 1->1 | 1 | 500 | 0 | 2.097 | 2.853 | 2.992 | 3.508 | 2.260 | 12.685 | 442.5 | 550 | 1096 |
| list_peers/1 | warm | 1->1 | 1 | 500 | 0 | 0.344 | 0.386 | 0.403 | 0.561 | 0.407 | 11.739 | 2458.4 | 550 | 550 |
| list_peers/10 | warm | 1->1 | 1 | 500 | 0 | 0.406 | 0.452 | 0.491 | 2.575 | 0.460 | 7.598 | 2172.4 | 550 | 550 |
| list_peers/50 | warm | 1->1 | 1 | 500 | 0 | 0.682 | 0.774 | 0.882 | 2.563 | 0.760 | 8.187 | 1316.5 | 550 | 550 |

### persistent arm (current transport via AgentMessageConnectionManager) - release

| op | scenario | topo | conc | n | err | p50 | p90 | p95 | p99 | mean | max | tput/s | accepts | server reqs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| send_message/64B | warm | 1->1 | 1 | 500 | 0 | 0.088 | 0.108 | 0.113 | 0.130 | 0.088 | 0.159 | 11323.8 | 1 | 1100 |
| send_message/256B | burst | 1->4 | 32 | 32 | 0 | 1.828 | 2.219 | 2.241 | 2.407 | 1.795 | 2.407 | 12053.2 | 1 | 164 |
| send_message/256B | churn | 1->1 | 1 | 500 | 247 | 0.281 | 0.328 | 0.343 | 0.373 | 0.293 | 1.817 | 3169.6 | 279 | 279 |
| send_message/256B | cold | 1->1 | 1 | 50 | 0 | 0.108 | 0.136 | 0.161 | 0.369 | 0.118 | 0.369 | 8490.4 | 1 | 99 |
| send_message/256B | restart | 1->1 | 1 | 60 | 0 | 0.105 | 0.140 | 0.147 | 0.167 | 0.108 | 0.221 | 9270.0 | 1 | 220 |
| send_message/256B | sustained-10s | 1->1 | 1 | 84582 | 0 | 0.116 | 0.142 | 0.153 | 0.184 | 0.118 | 9.980 | 8457.8 | 1 | 169264 |
| send_message/256B | sustained-10s | 1->1 | 8 | 185852 | 0 | 0.413 | 0.504 | 0.542 | 0.698 | 0.429 | 10.649 | 18581.9 | 1 | 371804 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 0.084 | 0.111 | 0.123 | 0.143 | 0.086 | 0.157 | 11653.3 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 0.095 | 0.116 | 0.127 | 0.169 | 0.096 | 0.220 | 10329.8 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 2 | 500 | 0 | 0.134 | 0.168 | 0.177 | 0.196 | 0.136 | 0.259 | 14626.5 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 4 | 500 | 0 | 0.219 | 0.280 | 0.305 | 0.338 | 0.222 | 0.362 | 17777.8 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 8 | 500 | 0 | 0.411 | 0.493 | 0.533 | 2.415 | 0.448 | 2.515 | 17658.6 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 16 | 500 | 0 | 0.836 | 0.924 | 0.951 | 1.031 | 0.836 | 1.214 | 18834.9 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 32 | 500 | 0 | 1.695 | 1.845 | 1.901 | 2.681 | 1.708 | 2.975 | 18012.7 | 1 | 1100 |
| send_message/256B | warm-topology | 1->1 | 4 | 500 | 0 | 0.214 | 0.270 | 0.286 | 0.322 | 0.217 | 0.351 | 18191.1 | 1 | 1100 |
| send_message/256B | warm-topology | 1->4 | 4 | 500 | 0 | 0.210 | 0.272 | 0.286 | 0.325 | 0.215 | 0.367 | 18454.0 | 1 | 1100 |
| send_message/256B | warm-topology | 4->1 | 4 | 500 | 0 | 0.216 | 0.271 | 0.288 | 0.324 | 0.221 | 0.365 | 17961.0 | 1 | 1100 |
| send_message/256B | warm-topology | 4->4 | 4 | 500 | 0 | 0.222 | 0.273 | 0.292 | 0.333 | 0.223 | 0.372 | 17721.4 | 1 | 1100 |
| send_message/1024B | warm | 1->1 | 1 | 500 | 0 | 0.091 | 0.113 | 0.122 | 0.140 | 0.089 | 0.158 | 11224.7 | 1 | 1100 |
| send_message/4096B | warm | 1->1 | 1 | 500 | 0 | 0.130 | 0.180 | 0.192 | 0.228 | 0.137 | 0.329 | 7305.6 | 1 | 1100 |
| send_message/16384B | warm | 1->1 | 1 | 500 | 0 | 0.202 | 0.247 | 0.272 | 0.297 | 0.208 | 0.320 | 4805.0 | 1 | 1100 |
| send_message/65536B | warm | 1->1 | 1 | 500 | 0 | 1.055 | 1.277 | 1.377 | 1.767 | 1.113 | 8.121 | 898.5 | 1 | 1099 |
| send_message/262144B | warm | 1->1 | 1 | 500 | 0 | 1.967 | 2.736 | 3.029 | 3.594 | 2.169 | 9.704 | 461.1 | 1 | 1099 |
| list_peers/1 | warm | 1->1 | 1 | 500 | 0 | 0.085 | 0.101 | 0.117 | 0.135 | 0.083 | 0.180 | 12013.3 | 1 | 550 |
| list_peers/10 | warm | 1->1 | 1 | 500 | 0 | 0.120 | 0.142 | 0.157 | 0.178 | 0.120 | 0.204 | 8366.3 | 1 | 550 |
| list_peers/50 | warm | 1->1 | 1 | 500 | 0 | 0.279 | 0.324 | 0.342 | 0.429 | 0.285 | 0.523 | 3508.6 | 1 | 550 |

### fresh vs persistent (release)

`d%` is the persistent-arm reduction versus fresh ((fresh-persistent)/fresh); negative
means persistent is slower on that metric.

| op | scenario | topo | c | fresh p50 | pers p50 | d p50 | d% | fresh mean | pers mean | d mean | d% | fresh tput | pers tput |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| list_peers/1 | warm | 1->1 | 1 | 0.344 | 0.085 | +0.259 | +75% | 0.407 | 0.083 | +0.324 | +80% | 2458.4 | 12013.3 |
| list_peers/10 | warm | 1->1 | 1 | 0.406 | 0.120 | +0.286 | +70% | 0.460 | 0.120 | +0.340 | +74% | 2172.4 | 8366.3 |
| list_peers/50 | warm | 1->1 | 1 | 0.682 | 0.279 | +0.403 | +59% | 0.760 | 0.285 | +0.475 | +62% | 1316.5 | 3508.6 |
| send_message/1024B | warm | 1->1 | 1 | 0.289 | 0.091 | +0.198 | +69% | 0.292 | 0.089 | +0.203 | +70% | 3426.6 | 11224.7 |
| send_message/16384B | warm | 1->1 | 1 | 0.350 | 0.202 | +0.148 | +42% | 0.359 | 0.208 | +0.151 | +42% | 2785.7 | 4805.0 |
| send_message/256B | burst | 1->4 | 32 | 70.764 | 1.828 | +68.936 | +97% | 83.381 | 1.795 | +81.586 | +98% | 161.5 | 12053.2 |
| send_message/256B | churn | 1->1 | 1 | 0.358 | 0.281 | +0.077 | +22% | 0.380 | 0.293 | +0.087 | +23% | 2628.2 | 3169.6 |
| send_message/256B | cold | 1->1 | 1 | 0.327 | 0.108 | +0.219 | +67% | 0.320 | 0.118 | +0.202 | +63% | 3126.1 | 8490.4 |
| send_message/256B | restart | 1->1 | 1 | 0.347 | 0.105 | +0.242 | +70% | 0.358 | 0.108 | +0.250 | +70% | 2791.4 | 9270.0 |
| send_message/256B | sustained-10s | 1->1 | 1 | 0.353 | 0.116 | +0.237 | +67% | 0.355 | 0.118 | +0.237 | +67% | 2812.4 | 8457.8 |
| send_message/256B | sustained-10s | 1->1 | 8 | 0.366 | 0.413 | -0.047 | -13% | 1.874 | 0.429 | +1.445 | +77% | 4259.1 | 18581.9 |
| send_message/256B | warm | 1->1 | 1 | 0.257 | 0.095 | +0.162 | +63% | 0.263 | 0.096 | +0.167 | +63% | 3789.9 | 10329.8 |
| send_message/256B | warm | 1->1 | 2 | 0.252 | 0.134 | +0.118 | +47% | 0.410 | 0.136 | +0.274 | +67% | 3884.6 | 14626.5 |
| send_message/256B | warm | 1->1 | 4 | 0.280 | 0.219 | +0.061 | +22% | 0.874 | 0.222 | +0.652 | +75% | 4072.7 | 17777.8 |
| send_message/256B | warm | 1->1 | 8 | 0.276 | 0.411 | -0.135 | -49% | 1.654 | 0.448 | +1.206 | +73% | 2949.2 | 17658.6 |
| send_message/256B | warm | 1->1 | 16 | 0.257 | 0.836 | -0.579 | -225% | 2.982 | 0.836 | +2.146 | +72% | 2833.3 | 18834.9 |
| send_message/256B | warm | 1->1 | 32 | 0.286 | 1.695 | -1.409 | -493% | 10.488 | 1.708 | +8.780 | +84% | 1510.1 | 18012.7 |
| send_message/256B | warm-topology | 1->1 | 4 | 0.338 | 0.214 | +0.124 | +37% | 0.901 | 0.217 | +0.684 | +76% | 3207.2 | 18191.1 |
| send_message/256B | warm-topology | 1->4 | 4 | 0.319 | 0.210 | +0.109 | +34% | 0.837 | 0.215 | +0.622 | +74% | 3690.9 | 18454.0 |
| send_message/256B | warm-topology | 4->1 | 4 | 0.293 | 0.216 | +0.077 | +26% | 0.869 | 0.221 | +0.648 | +75% | 3150.2 | 17961.0 |
| send_message/256B | warm-topology | 4->4 | 4 | 0.340 | 0.222 | +0.118 | +35% | 1.026 | 0.223 | +0.803 | +78% | 3158.5 | 17721.4 |
| send_message/262144B | warm | 1->1 | 1 | 2.097 | 1.967 | +0.130 | +6% | 2.260 | 2.169 | +0.091 | +4% | 442.5 | 461.1 |
| send_message/4096B | warm | 1->1 | 1 | 0.307 | 0.130 | +0.177 | +58% | 0.315 | 0.137 | +0.178 | +57% | 3172.1 | 7305.6 |
| send_message/64B | warm | 1->1 | 1 | 0.269 | 0.088 | +0.181 | +67% | 0.312 | 0.088 | +0.224 | +72% | 3202.7 | 11323.8 |
| send_message/65536B | warm | 1->1 | 1 | 0.653 | 1.055 | -0.402 | -62% | 0.769 | 1.113 | -0.344 | -45% | 1301.1 | 898.5 |

### Release-profile findings (headline)

1. **Sequential latency: ~2.7x faster.** Warm 1->1 256B: p50 0.257 -> 0.095 ms
   (-63%), mean 0.263 -> 0.096 ms (-63%). The removed per-call cost (pipe connect +
   hello + teardown) is now ~0.17 ms of a 0.26 ms call.
2. **Throughput: 3-12x.** Sustained 10 s at c1: 2,812 -> 8,458 msg/s (3.0x). At c8:
   4,259 -> 18,582 msg/s (4.4x). Concurrency sweep peaks at 18,835 msg/s (c16,
   persistent) vs 4,073 (c4, fresh); fresh throughput COLLAPSES above c8 (1,510
   msg/s at c32) from 32-way connect contention.
3. **Burst 32: 75x wall-clock.** 198.1 -> 2.7 ms wall; p50 70.8 -> 1.8 ms; all 32
   delivered on one connection.
4. **Churn remains the persistent arm's failure mode.** Server closes after each
   response: 247/500 sends fail (49%), same fast-fail/alternate-recover pattern as
   debug. Fresh: 0 errors. See debug findings item 5 for the mechanism and the
   never-replay design trade-off.
5. **Concurrency median trade.** Persistent p50 grows with in-connection queue
   depth (0.095 ms at c1 -> 1.70 ms at c32) while fresh keeps p50 flat (0.26 ->
   0.29 ms). Fresh instead pays catastrophic tails at c16/c32: p99 113/259 ms and
   max 169/327 ms (timer+connect contention), vs persistent p99 1.0/2.7 ms.
6. **64KiB anomaly.** Persistent is SLOWER at 64KiB (p50 1.055 vs 0.653 ms, -62%):
   one connection serializes the 64KiB line copy behind the ack_result writes of
   prior responses, while fresh gets a fresh pipe per send. At 256KiB the two arms
   tie (2.0 vs 2.0 ms p50; line serialization dominates both).
7. **list_peers: 59-80% faster** (1 peer p50 0.344 -> 0.085 ms; 50 peers mean
   0.760 -> 0.285 ms).
8. **Cold start: 67% faster** (p50 0.327 -> 0.108 ms, first 50 requests after
   fixture start, no warmup).
9. **Server request count 2x on the persistent arm** = one `ack_result` protocol
   line per mutating-command response (see debug findings item 9); fresh shows
   ~1.9x (ack usually loses the race against close in release).


## Debug-profile results (secondary)

Same matrix in the debug test binary. Both arms share the binary, so the
comparison stays apples-to-apples; absolute values are inflated by unoptimized
serde/JSON work on both arms equally.

Both matrix tests green: `2 passed; 0 failed` in 109.4 s (single gate invocation,
`--test-threads=2`, so the two arms ran sequentially; each arm sees the other's
pipe traffic only as ambient machine load). Raw data:
`results/baseline-1792211115558.jsonl` (persistent), `results/baseline-1792211166256.jsonl`
(fresh). All numbers measured under throttled gate (4 CPUs shared, -j 2, BelowNormal).

### fresh arm (pre-change behavior, inlined replica)

| op | scenario | topo | conc | n | err | p50 | p90 | p95 | p99 | mean | max | tput/s | accepts | server reqs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| send_message/64B | warm | 1->1 | 1 | 500 | 0 | 0.938 | 1.073 | 1.139 | 1.363 | 0.968 | 3.604 | 1032.6 | 550 | 562 |
| send_message/256B | burst | 1->4 | 32 | 32 | 0 | 68.167 | 222.715 | 223.163 | 232.127 | 91.602 | 232.127 | 137.5 | 82 | 115 |
| send_message/256B | churn | 1->1 | 1 | 500 | 0 | 0.998 | 1.182 | 1.268 | 1.464 | 1.023 | 2.252 | 977.9 | 550 | 550 |
| send_message/256B | cold | 1->1 | 1 | 50 | 0 | 0.911 | 1.027 | 1.038 | 1.044 | 0.928 | 1.044 | 1078.1 | 50 | 51 |
| send_message/256B | restart | 1->1 | 1 | 60 | 0 | 1.045 | 1.150 | 1.190 | 1.222 | 1.058 | 1.255 | 945.5 | 110 | 115 |
| send_message/256B | sustained-10s | 1->1 | 1 | 8266 | 0 | 1.119 | 1.363 | 1.463 | 1.966 | 1.207 | 42.869 | 826.5 | 8316 | 16584 |
| send_message/256B | sustained-10s | 1->1 | 8 | 16197 | 0 | 1.455 | 12.334 | 23.435 | 56.202 | 4.938 | 145.502 | 1617.2 | 16247 | 32488 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 1.090 | 1.244 | 1.321 | 1.580 | 1.117 | 4.809 | 894.9 | 550 | 619 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 1.102 | 1.322 | 1.437 | 5.553 | 1.287 | 46.688 | 774.4 | 550 | 600 |
| send_message/256B | warm | 1->1 | 2 | 500 | 0 | 1.185 | 1.412 | 1.563 | 12.530 | 1.568 | 56.136 | 1119.9 | 550 | 1100 |
| send_message/256B | warm | 1->1 | 4 | 500 | 0 | 1.291 | 1.592 | 11.927 | 34.092 | 2.509 | 56.324 | 1388.5 | 550 | 1100 |
| send_message/256B | warm | 1->1 | 8 | 500 | 0 | 1.548 | 12.405 | 23.367 | 55.654 | 4.636 | 100.578 | 1401.6 | 550 | 1099 |
| send_message/256B | warm | 1->1 | 16 | 500 | 0 | 1.550 | 22.976 | 35.250 | 68.032 | 7.216 | 166.855 | 1672.7 | 550 | 1100 |
| send_message/256B | warm | 1->1 | 32 | 500 | 0 | 1.501 | 33.849 | 56.363 | 177.508 | 12.112 | 221.284 | 1634.2 | 550 | 1100 |
| send_message/256B | warm-topology | 1->1 | 4 | 500 | 0 | 1.361 | 1.901 | 12.177 | 39.469 | 3.075 | 61.595 | 1233.0 | 550 | 1100 |
| send_message/256B | warm-topology | 1->4 | 4 | 500 | 0 | 1.280 | 1.510 | 11.828 | 23.324 | 2.429 | 67.089 | 1306.3 | 550 | 1100 |
| send_message/256B | warm-topology | 4->1 | 4 | 500 | 0 | 1.308 | 2.072 | 12.122 | 43.113 | 3.026 | 77.124 | 1160.8 | 550 | 1100 |
| send_message/256B | warm-topology | 4->4 | 4 | 500 | 0 | 1.362 | 1.812 | 12.021 | 33.701 | 2.791 | 67.124 | 1326.7 | 550 | 1100 |
| send_message/1024B | warm | 1->1 | 1 | 500 | 0 | 1.098 | 1.269 | 1.376 | 1.598 | 1.115 | 3.469 | 896.6 | 550 | 592 |
| send_message/4096B | warm | 1->1 | 1 | 500 | 0 | 1.455 | 1.783 | 1.899 | 2.962 | 1.532 | 5.328 | 652.6 | 550 | 584 |
| send_message/16384B | warm | 1->1 | 1 | 500 | 0 | 2.604 | 3.308 | 3.495 | 3.883 | 2.767 | 8.278 | 361.5 | 550 | 571 |
| send_message/65536B | warm | 1->1 | 1 | 500 | 0 | 8.269 | 10.183 | 10.791 | 14.480 | 8.992 | 86.294 | 111.2 | 550 | 614 |
| send_message/262144B | warm | 1->1 | 1 | 500 | 0 | 29.738 | 35.134 | 38.017 | 98.617 | 31.871 | 131.020 | 31.4 | 550 | 665 |
| list_peers/1 | warm | 1->1 | 1 | 500 | 0 | 0.960 | 1.085 | 1.169 | 5.279 | 1.122 | 41.973 | 891.1 | 550 | 550 |
| list_peers/10 | warm | 1->1 | 1 | 500 | 0 | 1.221 | 1.501 | 1.551 | 1.750 | 1.259 | 1.921 | 794.3 | 550 | 550 |
| list_peers/50 | warm | 1->1 | 1 | 500 | 0 | 2.530 | 3.308 | 3.470 | 3.863 | 2.855 | 40.439 | 350.3 | 550 | 550 |

### persistent arm (current transport via AgentMessageConnectionManager)

| op | scenario | topo | conc | n | err | p50 | p90 | p95 | p99 | mean | max | tput/s | accepts | server reqs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| send_message/64B | warm | 1->1 | 1 | 500 | 0 | 0.478 | 0.595 | 0.643 | 0.748 | 0.499 | 2.488 | 2005.7 | 1 | 1100 |
| send_message/256B | burst | 1->4 | 32 | 32 | 0 | 6.579 | 8.350 | 8.481 | 9.995 | 6.506 | 9.995 | 3075.6 | 1 | 164 |
| send_message/256B | churn | 1->1 | 1 | 500 | 245 | 0.943 | 1.135 | 1.166 | 1.331 | 0.989 | 1.441 | 918.6 | 281 | 281 |
| send_message/256B | cold | 1->1 | 1 | 50 | 0 | 0.453 | 0.546 | 0.555 | 1.029 | 0.465 | 1.029 | 2152.4 | 1 | 99 |
| send_message/256B | restart | 1->1 | 1 | 60 | 0 | 0.530 | 0.620 | 0.660 | 0.693 | 0.526 | 0.821 | 1899.6 | 1 | 220 |
| send_message/256B | sustained-10s | 1->1 | 1 | 14636 | 0 | 0.619 | 0.765 | 0.823 | 1.182 | 0.679 | 51.890 | 1463.6 | 1 | 29372 |
| send_message/256B | sustained-10s | 1->1 | 8 | 32118 | 0 | 2.229 | 2.900 | 3.260 | 4.889 | 2.487 | 100.188 | 3211.2 | 1 | 64336 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 0.507 | 0.617 | 0.652 | 1.165 | 0.567 | 10.840 | 1762.1 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 1 | 500 | 0 | 0.468 | 0.580 | 0.621 | 0.777 | 0.486 | 1.202 | 2043.6 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 2 | 500 | 0 | 0.648 | 0.816 | 0.900 | 1.411 | 0.703 | 4.589 | 2827.4 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 4 | 500 | 0 | 1.094 | 1.373 | 1.450 | 1.634 | 1.115 | 2.025 | 3548.4 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 8 | 500 | 0 | 2.095 | 2.578 | 2.745 | 3.034 | 2.149 | 3.308 | 3686.5 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 16 | 500 | 0 | 4.180 | 4.745 | 4.903 | 5.334 | 4.192 | 5.521 | 3765.5 | 1 | 1100 |
| send_message/256B | warm | 1->1 | 32 | 500 | 0 | 8.204 | 9.370 | 9.866 | 10.625 | 8.214 | 10.849 | 3788.4 | 1 | 1087 |
| send_message/256B | warm-topology | 1->1 | 4 | 500 | 0 | 1.102 | 1.397 | 1.539 | 2.032 | 1.142 | 3.063 | 3455.9 | 1 | 1100 |
| send_message/256B | warm-topology | 1->4 | 4 | 500 | 0 | 1.062 | 1.350 | 1.423 | 1.544 | 1.088 | 1.851 | 3651.3 | 1 | 1100 |
| send_message/256B | warm-topology | 4->1 | 4 | 500 | 0 | 1.097 | 1.398 | 1.519 | 3.109 | 1.167 | 7.559 | 3381.6 | 1 | 1100 |
| send_message/256B | warm-topology | 4->4 | 4 | 500 | 0 | 1.089 | 1.391 | 1.503 | 1.757 | 1.113 | 2.042 | 3541.9 | 1 | 1100 |
| send_message/1024B | warm | 1->1 | 1 | 500 | 0 | 0.584 | 0.682 | 0.725 | 1.094 | 0.598 | 1.328 | 1671.8 | 1 | 1100 |
| send_message/4096B | warm | 1->1 | 1 | 500 | 0 | 0.949 | 1.159 | 1.222 | 1.351 | 0.971 | 2.755 | 1030.2 | 1 | 1100 |
| send_message/16384B | warm | 1->1 | 1 | 500 | 0 | 2.201 | 2.856 | 3.025 | 4.259 | 2.364 | 6.901 | 423.0 | 1 | 1100 |
| send_message/65536B | warm | 1->1 | 1 | 500 | 0 | 7.869 | 10.063 | 10.675 | 13.986 | 8.566 | 66.588 | 116.7 | 1 | 1100 |
| send_message/262144B | warm | 1->1 | 1 | 500 | 0 | 28.217 | 33.441 | 35.299 | 41.434 | 28.994 | 61.645 | 34.5 | 1 | 1099 |
| list_peers/1 | warm | 1->1 | 1 | 500 | 0 | 0.400 | 0.478 | 0.504 | 0.584 | 0.414 | 1.762 | 2416.5 | 1 | 550 |
| list_peers/10 | warm | 1->1 | 1 | 500 | 0 | 0.657 | 0.901 | 0.974 | 3.478 | 0.787 | 23.494 | 1269.9 | 1 | 550 |
| list_peers/50 | warm | 1->1 | 1 | 500 | 0 | 1.815 | 2.566 | 2.856 | 2.978 | 1.969 | 3.115 | 508.0 | 1 | 550 |

### fresh vs persistent (debug)

`d%` is the persistent-arm reduction versus fresh ((fresh-persistent)/fresh); negative
means persistent is slower on that metric.

| op | scenario | topo | c | fresh p50 | pers p50 | d p50 | d% | fresh mean | pers mean | d mean | d% | fresh tput | pers tput |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| list_peers/1 | warm | 1->1 | 1 | 0.960 | 0.400 | +0.560 | +58% | 1.122 | 0.414 | +0.708 | +63% | 891.1 | 2416.5 |
| list_peers/10 | warm | 1->1 | 1 | 1.221 | 0.657 | +0.564 | +46% | 1.259 | 0.787 | +0.472 | +37% | 794.3 | 1269.9 |
| list_peers/50 | warm | 1->1 | 1 | 2.530 | 1.815 | +0.715 | +28% | 2.855 | 1.969 | +0.886 | +31% | 350.3 | 508.0 |
| send_message/1024B | warm | 1->1 | 1 | 1.098 | 0.584 | +0.514 | +47% | 1.115 | 0.598 | +0.517 | +46% | 896.6 | 1671.8 |
| send_message/16384B | warm | 1->1 | 1 | 2.604 | 2.201 | +0.403 | +15% | 2.767 | 2.364 | +0.403 | +15% | 361.5 | 423.0 |
| send_message/256B | burst | 1->4 | 32 | 68.167 | 6.579 | +61.588 | +90% | 91.602 | 6.506 | +85.096 | +93% | 137.5 | 3075.6 |
| send_message/256B | churn | 1->1 | 1 | 0.998 | 0.943 | +0.055 | +6% | 1.023 | 0.989 | +0.034 | +3% | 977.9 | 918.6 |
| send_message/256B | cold | 1->1 | 1 | 0.911 | 0.453 | +0.458 | +50% | 0.928 | 0.465 | +0.463 | +50% | 1078.1 | 2152.4 |
| send_message/256B | restart | 1->1 | 1 | 1.045 | 0.530 | +0.515 | +49% | 1.058 | 0.526 | +0.532 | +50% | 945.5 | 1899.6 |
| send_message/256B | sustained-10s | 1->1 | 1 | 1.119 | 0.619 | +0.500 | +45% | 1.207 | 0.679 | +0.528 | +44% | 826.5 | 1463.6 |
| send_message/256B | sustained-10s | 1->1 | 8 | 1.455 | 2.229 | -0.774 | -53% | 4.938 | 2.487 | +2.451 | +50% | 1617.2 | 3211.2 |
| send_message/256B | warm | 1->1 | 1 | 1.102 | 0.468 | +0.634 | +58% | 1.287 | 0.486 | +0.801 | +62% | 774.4 | 2043.6 |
| send_message/256B | warm | 1->1 | 2 | 1.185 | 0.648 | +0.537 | +45% | 1.568 | 0.703 | +0.865 | +55% | 1119.9 | 2827.4 |
| send_message/256B | warm | 1->1 | 4 | 1.291 | 1.094 | +0.197 | +15% | 2.509 | 1.115 | +1.394 | +56% | 1388.5 | 3548.4 |
| send_message/256B | warm | 1->1 | 8 | 1.548 | 2.095 | -0.547 | -35% | 4.636 | 2.149 | +2.487 | +54% | 1401.6 | 3686.5 |
| send_message/256B | warm | 1->1 | 16 | 1.550 | 4.180 | -2.630 | -170% | 7.216 | 4.192 | +3.024 | +42% | 1672.7 | 3765.5 |
| send_message/256B | warm | 1->1 | 32 | 1.501 | 8.204 | -6.703 | -447% | 12.112 | 8.214 | +3.898 | +32% | 1634.2 | 3788.4 |
| send_message/256B | warm-topology | 1->1 | 4 | 1.361 | 1.102 | +0.259 | +19% | 3.075 | 1.142 | +1.933 | +63% | 1233.0 | 3455.9 |
| send_message/256B | warm-topology | 1->4 | 4 | 1.280 | 1.062 | +0.218 | +17% | 2.429 | 1.088 | +1.341 | +55% | 1306.3 | 3651.3 |
| send_message/256B | warm-topology | 4->1 | 4 | 1.308 | 1.097 | +0.211 | +16% | 3.026 | 1.167 | +1.859 | +61% | 1160.8 | 3381.6 |
| send_message/256B | warm-topology | 4->4 | 4 | 1.362 | 1.089 | +0.273 | +20% | 2.791 | 1.113 | +1.678 | +60% | 1326.7 | 3541.9 |
| send_message/262144B | warm | 1->1 | 1 | 29.738 | 28.217 | +1.521 | +5% | 31.871 | 28.994 | +2.877 | +9% | 31.4 | 34.5 |
| send_message/4096B | warm | 1->1 | 1 | 1.455 | 0.949 | +0.506 | +35% | 1.532 | 0.971 | +0.561 | +37% | 652.6 | 1030.2 |
| send_message/64B | warm | 1->1 | 1 | 0.938 | 0.478 | +0.460 | +49% | 0.968 | 0.499 | +0.469 | +48% | 1032.6 | 2005.7 |
| send_message/65536B | warm | 1->1 | 1 | 8.269 | 7.869 | +0.400 | +5% | 8.992 | 8.566 | +0.426 | +5% | 111.2 | 116.7 |

### Debug-profile findings

1. **Sequential latency halves.** Warm 1->1 256B: p50 1.102 -> 0.468 ms (-58%),
   mean 1.287 -> 0.486 ms (-62%). The 0.6 ms gap is the per-call pipe connect +
   hello + teardown the manager removes; it matches the ipc-persistent
   micro-bench (2.08x, `persistent-ipc-bench.md`).
2. **Connections collapse.** Fresh opens exactly one pipe per request (550 accepts
   per 550-request config, including warmup). Persistent uses 1 accept per config.
3. **Concurrency: throughput up, median traded.** At c32 fresh peaks at 1634 msg/s
   while persistent reaches 3788 msg/s (2.3x). Persistent p50 at c32 is higher
   (8.20 vs 1.50 ms) because all 32 workers share one pipe (head-of-line
   blocking), but fresh pays in tails instead: p99 177.5 vs 10.6 ms (17x) and max
   221 vs 10.8 ms, from 32 simultaneous connects racing the one-at-a-time pipe
   instance creation. Sustained 10 s at c8: 1617 -> 3211 msg/s (2.0x), p99
   56.2 -> 4.9 ms.
4. **Burst 32 (barrier release): 22x wall-clock.** Fresh 232.7 ms wall, p50
   68.2 ms; persistent 10.4 ms wall, p50 6.6 ms, all 32 delivered.
5. **Churn is the persistent arm's failure mode.** With the server closing after
   each response, persistent fails 245/500 sends (49%): the next send after a
   close writes into the dying cached pipe and errors fast (no 30 s hang;
   wall 277.6 ms total), then the following send reconnects and succeeds -
   alternating ok/fail. Fresh is immune (0 errors) because it never reuses a
   connection. The manager deliberately never replays a sent message
   (`agent_message_transport.rs:51-53`), so this is a design trade-off, not a
   bug in the retry loop; a fix would retry only failures observed before the
   write reaches the wire.
6. **Cold start: 50% faster.** First 50 requests, no warmup: p50 0.911 -> 0.453 ms.
7. **Message size: the advantage shrinks.** 64B -48% mean, 256B -62%, 4KiB -37%,
   16KiB -15%, 64KiB -5%, 256KiB -9%: at large sizes line serialization
   dominates and the fixed connect+hello cost becomes noise.
8. **list_peers: 28-63% faster** (1 peer p50 0.96 -> 0.40 ms; 50 peers mean
   2.86 -> 1.97 ms; roster serialization dominates at 50 peers).
9. **Server-side request count includes ack_result.** send_message is a mutating
   command, so DaemonClient sends an `ack_result` line after every response
   (`daemon_client.rs:1198-1210`); the fixture counts it as a request. Persistent
   shows exactly 2 server requests per send; fresh shows 1.0-1.2x because its
   immediate `close()` usually wins the race against the async ack write. The
   real supervisor consumes ack_result as protocol traffic; no duplicate
   send_message commands are sent by either arm.
10. **Restart scenario did not exercise the pause.** The pauser fires at +200 ms
    for 1500 ms, but the 60-request measured window completes in ~30-63 ms, so
    neither arm ever saw a paused accept loop (max latency 1.255 ms fresh /
    0.821 ms persistent). The scenario needs a pause trigger inside the measured
    window (e.g. pause at +5 ms) to be meaningful; reported as a harness gap, not
    as a passing robustness result.
