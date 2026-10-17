# SSE parse/forward path A/B bench (completions reader + responses buffer)

Scope: `crates/pi-ai/src/providers/openai_completions.rs` (SSE framing:
`SseLineReader::push`/`decode_line`/`flush`, `forward_sse_payload`) and
`crates/pi-ai/src/providers/openai_responses.rs` (`SseBuffer::push`), plus the
harness `crates/pi-ai/src/providers/sse_parse_bench.rs`. Everything measured
through the REAL parse pipeline with synthetic fixtures; no network and no
provider APIs.

Environment: Windows worktree `optimus-net-20261017` (branch
improve/network-ipc-performance @ 6423654c1), rustc 1.98.1. All numbers on
this page are labeled: measured under throttled gate (4 CPUs shared, -j 2,
BelowNormal), `--test-threads=2`, five agents sharing one worktree and one
build gate. Release tables are canonical; debug tables show the same deltas
under the unoptimized build.

## Methodology

- A/B arms: baseline = the pre-change readers kept verbatim inside the bench
  (`reference::OldReader`, `responses_reference::OldBuffer`); candidate = the
  current production code. Both arms run in the same binary, same fixtures,
  same pass order, so the printed delta isolates the reader change alone.
- Fixtures (completions): text deltas 100B/1KB/4KB, reasoning-dense (every
  chunk carries `reasoning_content` + empty content), tool-call-delta
  (argument fragments streamed across chunks). Responses: text deltas
  100B/1KB/4KB.
- Each arm runs a clean pass (headline total, zero instrumentation, best of 3
  repetitions; the minimum is the stable estimator on this shared, throttled
  machine) and a single instrumented percentile pass. Framing percentiles
  come from one `Instant::now` per event (~20 ns); consumer percentiles from
  two per event (~40 ns); both charged equally to both arms. `p50/p95/p99`
  are nearest-rank over per-event samples; the mean is from the clean pass.
- events/s is derived from the clean-pass mean (1e9 / ns-per-event).
- Full-consumer arms use 200-event fixtures, not 2000: the bench event stream
  is never drained, so every event (and its `partial_message` deep clone)
  stays resident; at x2000 the 4KB fixture would retain GBs. Framing arms
  stay at x2000.
- Run: `cargo test -p pi-ai --lib --offline --locked sse_parse_bench -- --include-ignored --nocapture`
  (debug) and the same with `--release` (release tables).

## What changed in the candidate (summary)

1. `SseLineReader::push`: per-byte `line.push(byte)` loop replaced by a
   newline scan with span `extend_from_slice` per line (C1 in
   `audits/sse-path.md`).
2. `decode_line`: byte-slice field split (`strip_suffix(b"\r")`,
   `strip_prefix(b" ")`) instead of `String::from_utf8_lossy` + string ops;
   lossy UTF-8 handling preserved on the error path only.
3. `flush`: `String::from_utf8(mem::take(data))` zero-copy conversion when the
   buffer is valid UTF-8, lossy fallback unchanged (C3).
4. `forward_sse_payload`: the `[DONE]` `starts_with` result computed once (C5).
5. `SseBuffer` (responses): per-byte loop replaced by CR/LF span scan; the
   `data: Vec<String>` join replaced by one byte accumulator with `
`
   separators; empty-line flush parses `String::from_utf8(mem::take(data))`.
6. Review fix: `SseBuffer::push` guards the chunk-start `skip_lf` swallow on
   `!bytes.is_empty()`; an empty chunk between a CR-final chunk and an
   LF-initial chunk must preserve the flag (see "Defect caught in review").

Equivalence guarantee: `reference`/`responses_reference` run the pre-change
code verbatim; 14 equivalence tests assert byte-identical payload/event
sequences across deterministic pseudo-random chunkings, whole-body,
per-byte and every-single-split variants, plus CRLF/CR-only, UTF-8 split
mid-scalar, invalid UTF-8, comment/event-only, stop-callback,
unterminated-tail, and empty-chunk cases (including the reviewer's
CR / empty / LF split).

## Completions framing A/B (reader + no-op forward, 16KB chunks)

Release, measured under throttled gate (4 CPUs shared, -j 2, BelowNormal):

| fixture (x2000) | old ns/ev | old ev/s | new ns/ev | new ev/s | delta | new p50 | new p95 | new p99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| text 100B | 915 | 1092.9k | 209 | 4784.7k | -77.1% | 200 | 300 | 400 |
| text 1KB | 3376 | 296.2k | 541 | 1848.4k | -84.0% | 600 | 1100 | 1700 |
| text 4KB | 9643 | 103.7k | 1594 | 627.4k | -83.5% | 2700 | 3800 | 4600 |
| reasoning-dense 100B | 942 | 1061.6k | 388 | 2577.3k | -58.8% | 400 | 500 | 700 |
| reasoning-dense 1KB | 4513 | 221.6k | 1013 | 987.2k | -77.5% | 1000 | 1300 | 1900 |
| tool-call-delta | 1441 | 694.0k | 359 | 2785.5k | -75.1% | 400 | 400 | 600 |

Debug, same gate, same command without `--release` (post-fix run):

| fixture (x2000) | old ns/ev | old ev/s | new ns/ev | new ev/s | delta | new p50 | new p95 | new p99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| text 100B | 5827 | 171.6k | 2289 | 436.9k | -60.7% | 2100 | 2600 | 3800 |
| text 1KB | 19489 | 51.3k | 4242 | 235.7k | -78.2% | 7400 | 8600 | 17000 |
| text 4KB | 54814 | 18.2k | 18090 | 55.3k | -67.0% | 23400 | 27100 | 60700 |
| reasoning-dense 100B | 3651 | 273.9k | 1464 | 683.1k | -59.9% | 2400 | 3000 | 5400 |
| reasoning-dense 1KB | 14988 | 66.7k | 4520 | 221.2k | -69.8% | 4100 | 7300 | 9100 |
| tool-call-delta | 3558 | 281.1k | 1326 | 754.1k | -62.7% | 1300 | 1400 | 1900 |

## Chunk-size sensitivity (text 1KB x2000, framing only, release)

| chunk | old ns/ev | new ns/ev | delta |
|---|---:|---:|---:|
| 512B | 4214 | 1516 | -64.0% |
| 4096B | 2604 | 607 | -76.7% |
| 16384B | 3275 | 1008 | -69.2% |
| 65536B | 4489 | 654 | -85.4% |

Debug (post-fix run): 512B 17578 -> 5321 (-69.7%), 4096B 15593 -> 6790
(-56.5%), 16384B 15568 -> 6095 (-60.8%), 65536B 14883 -> 4090 (-72.5%).

The candidate wins at every chunk size; the win does not depend on
multi-event chunk amortization.

## Full consumer A/B (framing + typed parse + event build, release)

| fixture (x200) | old ns/ev | new ns/ev | delta | new p50 | new p95 | new p99 |
|---|---:|---:|---:|---:|---:|---:|
| text 100B | 7862 | 6180 | -21.4% | 7500 | 18600 | 19900 |
| text 1KB | 45521 | 39909 | -12.3% | 38000 | 76200 | 92200 |
| text 4KB | 114936 | 115762 | +0.7% | 106000 | 222300 | 298500 |
| reasoning-dense 1KB | 45352 | 41226 | -9.1% | 41700 | 83500 | 113600 |
| tool-call-delta | 408600 | 274500 | -32.8% | 1200 | 1200 | 1300 |

Debug (post-fix run): text 100B 19666 -> 20144 (+2.4%), text 1KB 76824 ->
75252 (-2.0%), text 4KB 234824 -> 230273 (-1.9%), reasoning-dense 1KB
119646 -> 78358 (-34.5%), tool-call-delta 3403400 -> 4306100 (+26.5%).

Framing vs consumer split (text 1KB x200, release): framing-only 518
ns/event, full consumer 42399 ns/event, consumer delta (typed parse + event
build + `partial_message`) 41881 ns/event. After this change the reader is a
minor cost; the consumer dominates at roughly 80x the framing cost.

Notes:
- The consumer delta is dominated by `partial_message` (below); its share
  grows with payload size, which is why the reader delta shrinks in relative
  terms for the consumer arms (down to noise, +0.7%, at text 4KB).
- The consumer arms are 200 events and load-sensitive on this shared
  machine; the debug tool-call +26.5% vs release -32.8% cell is run-to-run
  variance in the outlier-dominated mean (p50 is 1200-14800 ns in both
  profiles), not a systematic regression. Both arms share one binary, so the
  sign of the tool-call delta is not stable at these sample counts.

## Responses `SseBuffer` A/B (framing + Value parse, release)

| fixture (x2000) | chunk | old ns/ev | new ns/ev | delta |
|---|---|---:|---:|---:|
| text 100B | 4096B | 878 | 696 | -20.7% |
| text 100B | 16384B | 851 | 693 | -18.5% |
| text 1KB | 4096B | 3320 | 1664 | -49.9% |
| text 1KB | 16384B | 2681 | 1645 | -38.6% |
| text 4KB | 4096B | 7536 | 8678 | +15.2% (outlier, see note) |
| text 4KB | 16384B | 9372 | 4057 | -56.7% |

Note: the text 4KB @4096B cell is a min-of-3 outlier on this shared machine:
the same fixture at 16KB chunks shows -56.7%, and the per-event pass (below)
shows old p50 14400 vs new p50 8600 (-40%). Debug (post-fix run): text 100B
10376 -> 9040 (-12.9%) @4KB, 10644 -> 8572 (-19.5%) @16KB; text 1KB 23461 ->
17509 (-25.4%) @4KB, 22320 -> 16894 (-24.3%) @16KB; text 4KB 80216 -> 65504
(-18.3%) @4KB, 82386 -> 66822 (-18.9%) @16KB.

Per-event percentile pass (one event per push, release): text 100B old
p50 800 / p95 900 / p99 1500, new p50 700 / p95 700 / p99 1200; text 1KB old
p50 2400 / p95 4100 / p99 5600, new p50 1400 / p95 3000 / p99 3100; text
4KB old p50 14400 / p95 16400 / p99 31100, new p50 8600 / p95 9000 /
p99 14700. Debug (post-fix run): text 100B old p50 10200, new p50 8900; text
1KB old p50 32300, new p50 13300; text 4KB old p50 95000, new p50 40400.

The remaining cost here is the per-event `serde_json::Value` DOM parse: this
transport has no typed fast path (pre-existing architecture, out of scope
for this lane).

## `partial_message` quadratic clone (semantic; future work only)

`partial_message` (openai_completions.rs) clones `output` plus
`state.content()` on EVERY delta event, so an n-event stream with total text
T does O(T x n) deep-clone work and each event carries a full snapshot. This
is semantic (the TS implementation did the same; every event's `partial`
carries the full message so far), so it was NOT changed. Measured, release,
measured under throttled gate (4 CPUs shared, -j 2, BelowNormal):

- text 4KB x200 per-event cost: first decile 31370 ns/event, last decile
  186975 ns/event -> 6.0x growth across the stream (the linear-in-text
  per-event growth that makes the total quadratic). Debug post-fix run:
  first decile 147260, last decile 273665, 1.9x.
- text 4KB x200 full-consumer mean 115762 ns/event vs framing-only 1594
  ns/event (x2000): the consumer (parse + event build + `partial_message`)
  is >98% of the per-event cost at this size.

Future work options (not implemented): a CoW/snapshot scheme for `partial`,
or emitting deltas without the full snapshot and materializing `partial`
lazily for consumers that need it. Both change the event contract, so they
need their own lane.

## Defect caught in review (fixed pre-commit)

An adversarial diff review found a real defect in the rewritten
`SseBuffer::push`: the chunk-start `skip_lf` swallow executed even for an
EMPTY chunk, so a CR-final chunk followed by an empty chunk followed by the
LF-initial chunk consumed the flag; the LF then terminated an empty line and
the two body halves failed JSON parse separately, silently losing the event
(repro: `data: {"a":` + empty chunk + LF-rest; OLD emitted `{"a":1}`, NEW
emitted nothing). Fixed with the `!bytes.is_empty()` guard. Regression tests:
`responses_buffer_preserves_skip_lf_across_empty_chunks` (reviewer's split
plus byte-at-a-time with an empty chunk after every byte, both asserting
old == new == `[{"a":1}]`) and `reader_handles_empty_chunks_like_reference`
(same class for the completions reader, which was already immune). The
random chunkings never generated empty chunks (`% 97 + 1`), which is why
the original equivalence suite missed it.

## Negative results / candidates not implemented

- C2 (line-buffer reuse: `clear` + capacity retention instead of
  `mem::take` per line) was evaluated and NOT implemented: `decode_line`
  takes `&mut self` while the line bytes must stay borrowed, so reuse needs a
  borrow restructure of the reader internals. Expected win is one small
  realloc per SSE line; with C1+C3 already removing -59% to -84% of release
  framing time, the remaining line-alloc share is small. Deferred rather
  than risk a late-in-campaign regression.
- C4 (observer-attached double parse: reader parses a `Value` for the
  observer, consumer then takes the DOM path) was NOT implemented: observers
  are opt-in, not the default hot path. Documented in `audits/sse-path.md`.
- No production-code change was reverted during this lane (the empty-chunk
  defect above was fixed forward with a guard, matching OldBuffer behavior).

## Residual risks

- The e2e regression test `stream_event_sequence_is_stable_for_mixed_fixture`
  failed once during the cancelled shift (see root-cause note below); it
  passes on the current tree (verified in five consecutive full-module
  runs). If it fails again, suspect the shared HTTP transport
  (`shared_http.rs` / `openai_http_client_pool.rs`), not the reader: the
  reader is byte-equivalence-proven against the reference on the exact
  fixture.
- Both bench arms share one binary, so the A/B deltas are robust, but the
  absolute values move with neighbor load on the 4 shared CPUs; single cells
  (noted above) are min-of-3 outliers. The release tables are the canonical
  reference.
- The bench keeps the pre-change readers as the baseline; if someone later
  edits `reference`/`responses_reference`, the A/B baseline silently drifts.
  The equivalence tests would still pass (they compare behavior, not perf).

## Root cause of the previously failing e2e test (summary)

`stream_event_sequence_is_stable_for_mixed_fixture` failed at shift
cancellation with kinds `[start, other]` and error `Completions stream ended
before finish_reason or [DONE]` (`StreamError::dropped()`: the consumer loop
ended with neither `finish_reason` nor `[DONE]`). Evidence chain:

1. All 12 other non-ignored module tests passed at cancellation, including
   every OldReader-vs-SseLineReader equivalence test.
2. A byte-exact simulation of the new push/decode/flush algorithm on the
   test's exact fixture produces the 6 correct payloads ending in `[DONE]`.
3. `git show HEAD` confirms the typed consumer path pre-existed unchanged;
   the only semantic diffs in this lane are the reader rewrite (proven
   equivalent) and the `[DONE]`-check fold (trivially equivalent).
4. The test exercises `post_chat_completions` -> the shared HTTP transport,
   which a teammate was actively editing during the cancelled shift
   (`openai_http_client_pool.rs` still carried uncommitted mid-flight edits
   after this lane's last source change).
5. On the current tree the test passes in five consecutive full-module runs
   (debug and release, with and without `--include-ignored`).

Conclusion: not a regression in the changed reader/state code and not a
broken fixture (the mock HTTP response framing was re-verified by hand: valid
chunked encoding, byte-length hex chunk size, CRLF SSE framing). The failure
was a cross-lane casualty of the concurrently edited shared transport.

## Suite counts (this lane's module, `sse_parse_bench`)

- At cancellation: 13 passed, 1 failed (`stream_event_sequence_is_stable...`),
  2 ignored benches.
- On pickup, current tree, before any edit: 13 passed, 0 failed, 2 ignored.
- After the A/B harness rewrite + 2 new empty-chunk tests + review fix:
  15 passed, 0 failed, 2 ignored (non-ignored run), and 17 passed, 0 failed
  with `--include-ignored` (both debug and release, two consecutive runs
  each).
