# SSE streaming path audit (openai-completions + responses transport)

Worker: sse-streaming. Branch improve/network-ipc-performance @ 6423654c1.
Scope: SSE parse/forward overhead from raw body chunk to consumer event.

## 1. Chat completions path (openai_completions.rs)

Raw chunk -> read buffer -> line assembly -> payload framing -> JSON decode ->
event construction -> channel send -> consumer. File:line references below.

### 1.1 Network read loop: `read_sse_data` (1751-1809)

- `response.chunk()` per network chunk inside `tokio::select!` with 4 branches
  (chunk, idle-timeout `Sleep`, cancellation, `sender.closed()`).
  The `Sleep` + pending-cancel futures are constructed per chunk (not per event).
- Cancellation checked between chunks only (comment at 1766-1767 explains the
  select also aborts a pending body read).
- `reader.push(&chunk, |payload| forward_sse_payload(payload, observer, &sender))`
  at 1799; `reader.finish()` flush at 1804.

### 1.2 Line framing: `SseLineReader` (1531-1619)

- `push` (1552-1566): per-byte loop. Every byte goes through
  `self.line.push(byte)` (bounds + capacity check per byte). No span copy.
- Per line: `std::mem::take(&mut self.line)` (1555) hands the Vec to
  `decode_line`; the next line starts from an empty Vec and reallocates. One
  heap alloc per SSE line, capacity never reused.
- `decode_line` (1578-1605): `String::from_utf8_lossy(line)` is a borrowed
  `Cow` for valid UTF-8 (no alloc), `strip_suffix('\r')`, `find(':')`.
  `data:` values are copied into `self.data` via `extend_from_slice`
  (1601): one memcpy per line.
- `flush` (1609-1618): `String::from_utf8_lossy(&self.data).into_owned()`:
  ALWAYS a fresh String copy of the whole accumulated payload even when the
  bytes are already valid UTF-8. One alloc + one full memcpy per event.
- `finish` (1572-1575): tail line decode only.

### 1.3 Forward: `forward_sse_payload` (1730-1749)

- `payload.starts_with("[DONE]")` checked twice (1738, 1744).
- Observer attached: one `serde_json::from_str::<Value>(&payload)` (1739) for
  `observe_sse_payload_parsed`; the parsed Value is forwarded to the consumer,
  which then takes the `handle_value_chunk` DOM path (2666-2687) instead of the
  typed fast path. So observer-attached streams pay Value-DOM construction for
  every event AND the DOM consumer.
- Observer absent: payload String is moved into the channel; no parse here.
- Channel: `tokio::sync::mpsc::unbounded_channel` (2632); `sender.send` per
  event is a mutex + node alloc; `sender.closed()` branch stops the reader when
  the consumer drops.

### 1.4 Consumer: run_stream loop (2642-2688)

- `parse_typed_chunk` (1997-2002): `payload.trim_start().starts_with('{')`
  guard + `serde_json::from_str` into borrowed `TypedChunk<'_>` structs
  (1935-1991): no DOM for hot fields; `usage`/`error`/`reasoning_details` stay
  `Option<Value>` and are only built when present.
- `handle_typed_chunk` (2253-2392): borrowed path; per text delta one
  `content.to_string()` (event payload) and one `partial_message`.
- `handle_value_chunk` (2012-2248): DOM fallback (observer-attached streams,
  duplicate keys, exotic shapes). `reasoning_details` merge clones per detail.
- **Dominant per-event cost: `partial_message` (2727-2731)** clones
  `output.clone()` + `state.content()` (1339-1348): a deep copy of every block's
  full accumulated text on EVERY delta event. Total work is O(total_text x
  events) = quadratic over a stream. This is semantic (each event's `partial`
  carries the full snapshot; TS did the same), so it cannot be removed, only
  measured and reported.
- End of stream: `finish_block` per block (2743), `output.content` replaced
  once (2713).

### 1.5 Event queue: `utils/event_stream.rs` (209-360)

- `EventStream::push` (247): `Mutex<VecDeque>` + `Notify::notify_waiters` per
  event. Queue is unbounded; consumers drain via `next()`.

## 2. Responses path

### 2.1 SSE: `openai_responses.rs` `SseBuffer::push` (848-890)

- Same per-byte loop; `data: Vec<String>` allocates one String PER `data:` line
  (883), then `self.data.join("\n")` (873) allocates the joined payload;
  `serde_json::from_str::<Value>` per event (876). Full Value DOM per event;
  there is no typed fast path on this transport.
- NOTE: `openai_responses.rs` is NOT in this worker's ownership list; the
  struct is shared with `azure_openai_responses.rs`. Reported, not edited.
  The completions `SseLineReader` improvements would apply the same way.

### 2.2 WebSocket: `responses_transport.rs` `try_websocket` unfold (591-713)

- Per text frame: `serde_json::from_str::<Value>(&text)` (620), then
  `normalize_event(&mut event)` (700) mutates the DOM
  (`event["type"]`, `event["response"]["status"]` index writes alloc key
  strings), then `event["type"].as_str()` + `event["response"]["status"]`
  re-index reads (701-703). Ping/Pong handled inline. Events flow into
  `process_responses_stream` (openai_responses_shared.rs:720) which does
  `get_str(&event, "type")` string scans per event and `Value` clones per item.

## 3. Allocation/copy inventory (completions path, per event, observer absent)

| # | Where | Cost |
|---|-------|------|
| 1 | push: per-byte `line.push` | ~1 branch + capacity check per byte |
| 2 | push: `mem::take(line)` | 1 alloc/line (realloc from 0) |
| 3 | decode_line: `extend_from_slice(data)` | 1 memcpy of payload line |
| 4 | flush: `into_owned` | 1 alloc + 1 full payload memcpy |
| 5 | channel send | 1 node alloc + mutex lock |
| 6 | parse_typed_chunk | serde_json parse (zero-copy borrows) |
| 7 | handle_typed_chunk: `content.to_string()` | 1 alloc + memcpy (event payload) |
| 8 | partial_message | full snapshot clone (O(text so far)) - semantic |
| 9 | stream.push | mutex + VecDeque |

## 4. Optimization candidates (to be measured before/after)

- C1 span-copy framing: newline scan + `extend_from_slice` instead of per-byte
  push (SseLineReader::push).
- C2 line-buffer reuse: keep one line Vec, clear instead of take (restructure
  decode_line to take disjoint field borrows).
- C3 zero-copy flush: `String::from_utf8(mem::take(data))` when valid UTF-8,
  lossy fallback. Removes one memcpy per event.
- C4 observer-attached double parse: reader parses for observer, consumer then
  uses the Value DOM path; a typed fast path for observer streams would need
  the parsed Value AND typed borrow of the same payload - defer unless bench
  shows it matters (observer is opt-in, not the default).
- C5 [DONE] double starts_with - trivial, fold into one check.

Out of scope for edits (ownership): SseBuffer (openai_responses.rs),
utils/sse_frames.rs, request building/client pool.
## 5. Update (2026-10-17, lane completion)

Ownership widened after this audit was written: the SSE regions of
`openai_responses.rs` (the `SseBuffer`) and `responses_transport.rs` were
added to this lane. Final state of the candidates:

- C1 (span-copy framing) implemented in `SseLineReader::push`.
- C3 (zero-copy flush) implemented in `flush` (`String::from_utf8` of the
  taken buffer, lossy fallback preserved).
- C5 (single `[DONE]` check) implemented in `forward_sse_payload`.
- The `SseBuffer` in `openai_responses.rs` received the same span-scan
  rewrite plus a single byte accumulator replacing `Vec<String>` + `join`.
- C2 (line-buffer reuse) evaluated, not implemented (borrow restructure,
  small remaining win). C4 (observer double parse) deferred (opt-in path).
- Measured A/B results: `research/network_latency/provider/sse-parse-bench.md`
  (framing -51% to -71% ns/event across fixtures, debug profile, measured
  under throttled gate (4 CPUs shared, -j 2, BelowNormal)).
- The `partial_message` O(text x events) deep clone is semantic and was left
  untouched; measured and documented as future work in the bench doc.
- The previously failing e2e regression test
  (`stream_event_sequence_is_stable_for_mixed_fixture`) passes on the final
  tree; root-cause analysis is in the bench doc (cross-lane casualty of the
  concurrently edited shared HTTP transport, not the reader rewrite; the
  fixture itself is valid).
- Post-audit review fix (pre-commit): `SseBuffer::push` now guards the
  chunk-start `skip_lf` swallow on `!bytes.is_empty()`; an empty chunk
  between a CR-final and an LF-initial chunk previously lost the event
  silently. Regression test:
  `responses_buffer_preserves_skip_lf_across_empty_chunks`.
