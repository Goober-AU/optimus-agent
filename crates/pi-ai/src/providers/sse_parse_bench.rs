//! SSE parse/forward micro-benchmark harness for the network/IPC performance
//! campaign (worker sse-streaming).
//!
//! Feeds synthetic SSE fixtures through the REAL parse/forward pipeline
//! (`SseLineReader` framing -> `forward_sse_payload` -> typed consumer) with a
//! no-op sink, and reports events/s plus ns/event percentiles.
//!
//! A/B: the pre-change readers are kept verbatim in `reference` and
//! `responses_reference`; every fixture runs through both arms in the same
//! binary, so the printed delta isolates the reader change alone.
//!
//! Run: cargo test -p pi-ai --release sse_parse_bench -- --ignored --nocapture

use std::time::Instant;

use super::openai_completions::SseLineReader;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/// One synthetic chat-completions chunk.
fn chunk_event(delta_json: &str) -> String {
	format!("data: {{\"id\":\"chatcmpl-bench\",\"object\":\"chat.completion.chunk\",\"created\":1760000000,\"model\":\"repro-model\",\"choices\":[{{\"index\":0,\"delta\":{delta_json},\"finish_reason\":null}}]}}\n\n")
}

/// Text deltas of `sizes` bytes each (UTF-8-safe ASCII payloads).
fn text_fixture(sizes: &[usize]) -> Vec<u8> {
	let mut body = Vec::new();
	for size in sizes {
		let text = "x".repeat(*size);
		body.extend_from_slice(chunk_event(&format!("{{\"content\":\"{text}\"}}")).as_bytes());
	}
	body.extend_from_slice(b"data: [DONE]\n\n");
	body
}

/// Reasoning-dense variant: every chunk carries a reasoning delta plus content.
fn reasoning_fixture(count: usize, delta_size: usize) -> Vec<u8> {
	let mut body = Vec::new();
	for _ in 0..count {
		let text = "r".repeat(delta_size);
		body.extend_from_slice(
			chunk_event(&format!("{{\"reasoning_content\":\"{text}\",\"content\":\"\"}}")).as_bytes(),
		);
	}
	body.extend_from_slice(b"data: [DONE]\n\n");
	body
}

/// Tool-call-delta variant: argument fragments streamed across chunks.
fn tool_call_fixture(count: usize) -> Vec<u8> {
	let mut body = Vec::new();
	body.extend_from_slice(
		chunk_event(
			"{\"tool_calls\":[{\"index\":0,\"id\":\"call_bench\",\"type\":\"function\",\"function\":{\"name\":\"run\",\"arguments\":\"\"}}]}",
		)
		.as_bytes(),
	);
	for i in 0..count {
		let fragment = format!("{{\\\"field_{i}\\\":\\\"value {i}\\\"}}");
		body.extend_from_slice(
			chunk_event(&format!("{{\"tool_calls\":[{{\"index\":0,\"function\":{{\"arguments\":\"{fragment}\"}}}}]}}"))
				.as_bytes(),
		);
	}
	body.extend_from_slice(b"data: [DONE]\n\n");
	body
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/// Splits a body into network-sized chunks like a real transport.
fn chunked(body: &[u8], chunk_size: usize) -> Vec<Vec<u8>> {
	body.chunks(chunk_size).map(<[u8]>::to_vec).collect()
}

/// One measurement: clean-pass total plus per-event samples from the
/// instrumented pass.
struct Sample {
	total_ns: f64,
	events: usize,
	per_event: Vec<f64>,
}

/// Nearest-rank p50/p95/p99 over per-event samples.
fn percentiles(mut samples: Vec<f64>) -> (f64, f64, f64) {
	if samples.is_empty() {
		return (f64::NAN, f64::NAN, f64::NAN);
	}
	samples.sort_by(|a, b| a.partial_cmp(b).unwrap());
	let pick = |fraction: f64| -> f64 {
		let index = ((samples.len() as f64 - 1.0) * fraction).round() as usize;
		samples[index.min(samples.len() - 1)]
	};
	(pick(0.50), pick(0.95), pick(0.99))
}

/// Reader-agnostic framing interface so one harness runs the reference
/// (pre-change) reader and the current reader against the same sink.
trait FramingReader {
	fn new_read() -> Self;
	fn push_read(&mut self, bytes: &[u8], on_payload: &mut dyn FnMut(String) -> bool) -> bool;
	fn finish_read(&mut self) -> Option<String>;
}

impl FramingReader for reference::OldReader {
	fn new_read() -> Self {
		reference::OldReader::new()
	}

	fn push_read(&mut self, bytes: &[u8], on_payload: &mut dyn FnMut(String) -> bool) -> bool {
		reference::OldReader::push(self, bytes, |payload| on_payload(payload))
	}

	fn finish_read(&mut self) -> Option<String> {
		reference::OldReader::finish(self)
	}
}

impl FramingReader for SseLineReader {
	fn new_read() -> Self {
		SseLineReader::new()
	}

	fn push_read(&mut self, bytes: &[u8], on_payload: &mut dyn FnMut(String) -> bool) -> bool {
		SseLineReader::push(self, bytes, |payload| on_payload(payload))
	}

	fn finish_read(&mut self) -> Option<String> {
		SseLineReader::finish(self)
	}
}

/// Framing A/B arm: reader + a no-op forward sink, no consumer. A clean pass
/// produces the headline total; an instrumented pass then collects one sample
/// per event (one `Instant::now` per event, ~20 ns, charged equally to both
/// arms).
fn bench_framing<R: FramingReader>(body: &[u8], chunk_size: usize) -> Sample {
	let chunks = chunked(body, chunk_size);
	// Best of 3 clean passes: ms-scale runs on a shared, throttled machine
	// swing with neighbor load, and the minimum is the stable estimator.
	let total_ns = (0..3)
		.map(|_| {
			let mut reader = R::new_read();
			let start = Instant::now();
			for chunk in &chunks {
				let _ = reader.push_read(chunk, &mut |payload| !payload.starts_with("[DONE]"));
			}
			let _ = reader.finish_read();
			start.elapsed().as_nanos() as f64
		})
		.fold(f64::INFINITY, f64::min);

	let mut reader = R::new_read();
	let mut stamps: Vec<Instant> = Vec::new();
	for chunk in &chunks {
		let _ = reader.push_read(chunk, &mut |payload| {
			stamps.push(Instant::now());
			!payload.starts_with("[DONE]")
		});
	}
	let _ = reader.finish_read();
	let events = stamps.len();
	let mut per_event = Vec::with_capacity(events.saturating_sub(1));
	for pair in stamps.windows(2) {
		per_event.push(pair[1].duration_since(pair[0]).as_nanos() as f64);
	}
	Sample { total_ns, events, per_event }
}

/// Full consumer A/B arm: framing + typed parse + `handle_typed_chunk` on a
/// real `StreamState`/`AssistantMessageEventStream` with a no-op event sink.
/// The stream is never drained, so each event (and its `partial_message` deep
/// clone) stays resident: fixture counts are bounded for that reason.
fn bench_consumer<R: FramingReader>(body: &[u8], chunk_size: usize, model: &crate::types::Model) -> Sample {
	use super::openai_completions::{handle_typed_chunk, parse_typed_chunk, StreamState};
	use crate::types::AssistantMessage;
	use indexmap::IndexMap;

	let chunks = chunked(body, chunk_size);
	let run = |mut collect: Option<&mut Vec<f64>>| -> (f64, usize) {
		let mut output = AssistantMessage::new(
			model.api.clone(),
			model.provider.clone(),
			model.id.clone(),
			0,
		);
		output.usage = crate::types::Usage::zero();
		let mut state = StreamState::new();
		let stream = crate::utils::event_stream::create_assistant_message_event_stream();
		let stream = stream.producer_handle();
		let headers: IndexMap<String, String> = IndexMap::new();
		let mut finished = false;
		let mut events = 0usize;
		let mut reader = R::new_read();
		let start = Instant::now();
		for chunk in &chunks {
			let _ = reader.push_read(chunk, &mut |payload| {
				if payload.starts_with("[DONE]") {
					return false;
				}
				let event_start = collect.is_some().then(Instant::now);
				if let Some(typed) = parse_typed_chunk(&payload) {
					let _ = handle_typed_chunk(
						typed,
						model,
						None::<&super::openai_completions::OpenAICompletionsOptions>,
						&mut output,
						&mut state,
						&stream,
						None,
						&headers,
						&mut finished,
					);
					events += 1;
				}
				if let (Some(samples), Some(event_start)) = (collect.as_mut(), event_start) {
					samples.push(event_start.elapsed().as_nanos() as f64);
				}
				true
			});
		}
		let _ = reader.finish_read();
		(start.elapsed().as_nanos() as f64, events)
	};
	// Best of 3 clean passes (see `bench_framing`).
	let (total_ns, events) = (0..3)
		.map(|_| run(None))
		.reduce(|best, next| if next.0 < best.0 { next } else { best })
		.unwrap_or((0.0, 0));
	let mut per_event = Vec::new();
	let _ = run(Some(&mut per_event));
	Sample { total_ns, events, per_event }
}

fn model() -> crate::types::Model {
	crate::types::Model {
		id: "repro-model".to_string(),
		name: "Repro Model".to_string(),
		api: "openai-completions".to_string(),
		provider: "repro-provider".to_string(),
		base_url: "http://127.0.0.1:1".to_string(),
		reasoning: true,
		input: vec![crate::types::InputModality::Text],
		cost: crate::types::ModelCost::zero(),
		context_window: 128_000.0,
		max_tokens: 4096.0,
		..Default::default()
	}
}

// ---------------------------------------------------------------------------
// Benchmarks
// ---------------------------------------------------------------------------

fn bench_all() {
	let total_start = Instant::now();
	let model = model();

	println!("=== completions SSE framing A/B (reader + forward only, 16KB chunks) ===");
	let fixtures: Vec<(&str, Vec<u8>)> = vec![
		("text 100B", text_fixture(&vec![100; 2000])),
		("text 1KB", text_fixture(&vec![1024; 2000])),
		("text 4KB", text_fixture(&vec![4096; 2000])),
		("reasoning-dense 100B", reasoning_fixture(2000, 100)),
		("reasoning-dense 1KB", reasoning_fixture(2000, 1024)),
		("tool-call-delta", tool_call_fixture(2000)),
	];
	for (name, body) in &fixtures {
		let old = bench_framing::<reference::OldReader>(body, 16 * 1024);
		let new = bench_framing::<SseLineReader>(body, 16 * 1024);
		let old_mean = old.total_ns / old.events as f64;
		let new_mean = new.total_ns / new.events as f64;
		let (p50, p95, p99) = percentiles(new.per_event);
		println!(
			"{:<44} {:>5} ev  old {:>8.0} ns/ev  new {:>8.0} ns/ev  delta {:+6.1}%  new p50 {:>8.0} p95 {:>8.0} p99 {:>8.0}",
			format!("{name} x{}", old.events),
			old.events,
			old_mean,
			new_mean,
			(new_mean - old_mean) / old_mean * 100.0,
			p50,
			p95,
			p99
		);
	}

	println!("\n=== chunk-size sensitivity (text 1KB x2000, framing only) ===");
	let body = fixtures[1].1.clone();
	for chunk_size in [512usize, 4 * 1024, 16 * 1024, 64 * 1024] {
		let old = bench_framing::<reference::OldReader>(&body, chunk_size);
		let new = bench_framing::<SseLineReader>(&body, chunk_size);
		let old_mean = old.total_ns / old.events as f64;
		let new_mean = new.total_ns / new.events as f64;
		println!(
			"chunk {chunk_size:>6}B  old {old_mean:>8.0} ns/ev  new {new_mean:>8.0} ns/ev  delta {:+.1}%",
			(new_mean - old_mean) / old_mean * 100.0
		);
	}

	println!("\n=== full consumer A/B (framing + typed parse + event build) ===");
	println!("(events stay resident: every partial_message clone is the full snapshot)");
	for (name, body) in [
		("text 100B x200", text_fixture(&vec![100; 200])),
		("text 1KB x200", text_fixture(&vec![1024; 200])),
		("text 4KB x200", text_fixture(&vec![4096; 200])),
		("reasoning-dense 1KB x200", reasoning_fixture(200, 1024)),
		("tool-call-delta x200", tool_call_fixture(200)),
	] {
		let old = bench_consumer::<reference::OldReader>(&body, 16 * 1024, &model);
		let new = bench_consumer::<SseLineReader>(&body, 16 * 1024, &model);
		let old_mean = old.total_ns / old.events as f64;
		let new_mean = new.total_ns / new.events as f64;
		let (p50, p95, p99) = percentiles(new.per_event);
		println!(
			"{:<44} old {:>9.0} ns/ev  new {:>9.0} ns/ev  delta {:+6.1}%  new p50 {:>9.0} p95 {:>9.0} p99 {:>9.0}",
			name,
			old_mean,
			new_mean,
			(new_mean - old_mean) / old_mean * 100.0,
			p50,
			p95,
			p99
		);
	}

	println!("\n=== partial_message growth probe (text 4KB x200, per-event ns by decile) ===");
	{
		let body = text_fixture(&vec![4096; 200]);
		let new = bench_consumer::<SseLineReader>(&body, 16 * 1024, &model);
		let samples = new.per_event;
		if samples.len() >= 20 {
			let decile = samples.len() / 10;
			let first = samples[..decile].iter().sum::<f64>() / decile as f64;
			let last = samples[samples.len() - decile..].iter().sum::<f64>() / decile as f64;
			println!("first decile {first:>9.0} ns/event   last decile {last:>9.0} ns/event   growth {:.1}x", last / first);
		}
	}

	println!("\n=== framing vs consumer split (text 1KB x200) ===");
	{
		let body = text_fixture(&vec![1024; 200]);
		let framing = bench_framing::<SseLineReader>(&body, 16 * 1024);
		let full = bench_consumer::<SseLineReader>(&body, 16 * 1024, &model);
		let framing_mean = framing.total_ns / framing.events as f64;
		let full_mean = full.total_ns / full.events as f64;
		println!("framing-only: {framing_mean:.0} ns/event");
		println!("full:         {full_mean:.0} ns/event ({} events)", full.events);
		println!("consumer delta (parse + event build + partial_message): {:.0} ns/event", full_mean - framing_mean);
	}

	println!("\nTotal bench time: {:.1}s", total_start.elapsed().as_secs_f32());
}

#[test]
#[ignore = "micro-benchmark; run with --ignored --nocapture"]
fn sse_parse_bench() {
	bench_all();
}

// ---------------------------------------------------------------------------
// Phase 4: before/after regression tests
// ---------------------------------------------------------------------------

/// The pre-optimization reader, kept verbatim as the behavioral reference:
/// the optimized reader must produce byte-identical payload sequences for
/// every fixture and every chunk split.
mod reference {
	pub struct OldReader {
		line: Vec<u8>,
		data: Vec<u8>,
		has_data: bool,
		saw_event: bool,
	}

	impl OldReader {
		pub fn new() -> Self {
			OldReader { line: Vec::new(), data: Vec::new(), has_data: false, saw_event: false }
		}

		pub fn push(&mut self, bytes: &[u8], mut on_payload: impl FnMut(String) -> bool) -> bool {
			for &byte in bytes {
				if byte == b'\n' {
					let line = std::mem::take(&mut self.line);
					if let Some(payload) = self.decode_line(&line) {
						if !on_payload(payload) {
							return false;
						}
					}
				} else {
					self.line.push(byte);
				}
			}
			true
		}

		pub fn finish(&mut self) -> Option<String> {
			let line = std::mem::take(&mut self.line);
			self.decode_line(&line)
		}

		fn decode_line(&mut self, line: &[u8]) -> Option<String> {
			let line = String::from_utf8_lossy(line);
			let line = match line.strip_suffix('\r') {
				Some(stripped) => stripped,
				None => line.as_ref(),
			};
			if line.is_empty() {
				return self.flush();
			}
			if line.starts_with(':') {
				return None;
			}
			let (fieldname, value) = match line.find(':') {
				Some(index) => (&line[..index], &line[index + 1..]),
				None => (line.as_ref(), ""),
			};
			let value = value.strip_prefix(' ').unwrap_or(value);
			if fieldname == "event" {
				self.saw_event = true;
			} else if fieldname == "data" {
				if self.has_data {
					self.data.push(b'\n');
				}
				self.data.extend_from_slice(value.as_bytes());
				self.has_data = true;
			}
			None
		}

		fn flush(&mut self) -> Option<String> {
			if !self.has_data && !self.saw_event {
				return None;
			}
			let payload = String::from_utf8_lossy(&self.data).into_owned();
			self.data.clear();
			self.has_data = false;
			self.saw_event = false;
			Some(payload)
		}
	}
}

fn payloads_new(body: &[u8], splits: &[usize]) -> Vec<String> {
	let mut reader = super::openai_completions::SseLineReader::new();
	let mut out = Vec::new();
	let mut offset = 0;
	for &split in splits {
		let next = offset + split;
		if next > body.len() {
			break;
		}
		reader.push(&body[offset..next], |payload| {
			out.push(payload);
			true
		});
		offset = next;
	}
	reader.push(&body[offset..], |payload| {
		out.push(payload);
		true
	});
	if let Some(tail) = reader.finish() {
		out.push(tail);
	}
	out
}

fn payloads_old(body: &[u8], splits: &[usize]) -> Vec<String> {
	let mut reader = reference::OldReader::new();
	let mut out = Vec::new();
	let mut offset = 0;
	for &split in splits {
		let next = offset + split;
		if next > body.len() {
			break;
		}
		reader.push(&body[offset..next], |payload| {
			out.push(payload);
			true
		});
		offset = next;
	}
	reader.push(&body[offset..], |payload| {
		out.push(payload);
		true
	});
	if let Some(tail) = reader.finish() {
		out.push(tail);
	}
	out
}

/// A deterministic pseudo-random chunking (xorshift) so every run covers the
/// same splits; no external RNG dependency.
fn chunkings(len: usize, seed: u64, rounds: usize) -> Vec<Vec<usize>> {
	let mut state = seed;
	let mut next = move || {
		state ^= state << 13;
		state ^= state >> 7;
		state ^= state << 17;
		state
	};
	(0..rounds)
		.map(|_| {
			let mut splits = Vec::new();
			let mut offset = 0;
			while offset < len {
				let step = (next() % 97 + 1) as usize;
				offset += step;
				if offset >= len {
					break;
				}
				splits.push(offset);
			}
			splits
		})
		.collect()
}

fn assert_reader_equivalence(body: &[u8], label: &str) {
	let mut variants: Vec<Vec<usize>> = chunkings(body.len(), 0x2545F4914F6CDD1D, 24);
	// Deterministic systematic splits: every single-byte boundary near event
	// edges, plus whole-body and per-event chunks.
	variants.push(vec![body.len()]);
	variants.push((1..body.len()).collect());
	for (i, splits) in variants.iter().enumerate() {
		let old = payloads_old(body, splits);
		let new = payloads_new(body, splits);
		assert_eq!(old, new, "{label}: chunking #{i} (splits {splits:?}) diverged");
	}
}

#[test]
fn reader_matches_reference_on_text_fixtures() {
	assert_reader_equivalence(&text_fixture(&vec![100; 12]), "text 100B");
	assert_reader_equivalence(&text_fixture(&vec![1024; 5]), "text 1KB");
}

#[test]
fn reader_matches_reference_on_reasoning_and_tool_fixtures() {
	assert_reader_equivalence(&reasoning_fixture(20, 64), "reasoning-dense");
	assert_reader_equivalence(&tool_call_fixture(20), "tool-call");
}

#[test]
fn reader_handles_crlf_and_cr_only_lines() {
	let body = b"data: one\r\ndata: two\r\rdata: [DONE]\r\n\r\n";
	assert_reader_equivalence(body, "crlf-mix");
	// The reader terminates lines on `\n` only; a bare `\r` is payload text
	// (SDK `LineDecoder` parity), so `two\r\rdata: [DONE]` stays one line
	// until its terminating CRLF and the CR is stripped once.
	let payloads = payloads_new(body, &[]);
	assert_eq!(payloads, vec!["one\ntwo\r\rdata: [DONE]".to_string()]);
}

#[test]
fn reader_handles_multibyte_utf8_split_across_chunks() {
	let body = "data: {\"content\":\"Ready \\u{2713} \\u{65E5}\\u{672C}\"}\n\n".as_bytes();
	assert_reader_equivalence(body, "utf8-payload");
	// Also raw multibyte bytes split mid-scalar.
	let body = "data: \u{1F600}\u{1F600}\n\n".as_bytes();
	assert_reader_equivalence(body, "emoji");
}

#[test]
fn reader_handles_event_boundaries_split_across_chunks() {
	let body = b"data: {\"a\":1}\n\ndata: {\"b\":2}\n\ndata: [DONE]\n\n";
	for split in 0..body.len() {
		let old = payloads_old(body, &[split]);
		let new = payloads_new(body, &[split]);
		assert_eq!(old, new, "split at {split} diverged");
	}
}

#[test]
fn reader_ignores_comments_and_empty_lines_and_event_only_flush() {
	let body = b": keepalive\n\nevent: ping\n\ndata: x\n\n\n\ndata: [DONE]\n\n";
	assert_reader_equivalence(body, "comments-and-event-only");
	// event-only block flushes an empty payload (SDK SSEDecoder parity).
	let payloads = payloads_new(b"event: ping\n\n", &[]);
	assert_eq!(payloads, vec![String::new()]);
}

#[test]
fn reader_invalid_utf8_becomes_replacement_chars_like_reference() {
	let body = b"data: \xFF\xFE broken\n\n";
	assert_reader_equivalence(body, "invalid-utf8");
	let payloads = payloads_new(body, &[]);
	assert_eq!(payloads.len(), 1);
	assert!(payloads[0].contains('\u{FFFD}'));
}

#[test]
fn reader_stop_returned_from_callback_halts_scan() {
	let body = b"data: one\n\ndata: two\n\ndata: [DONE]\n\n";
	for split in 0..=body.len() {
		let mut seen_old = Vec::new();
		let mut old = reference::OldReader::new();
		let (a, b) = body.split_at(split);
		old.push(a, |p| {
			seen_old.push(p);
			!seen_old.last().is_some_and(|p| p == "one")
		});
		old.push(b, |p| {
			seen_old.push(p);
			!seen_old.last().is_some_and(|p| p == "one")
		});
		let mut seen_new = Vec::new();
		let mut new = super::openai_completions::SseLineReader::new();
		new.push(a, |p| {
			seen_new.push(p);
			!seen_new.last().is_some_and(|p| p == "one")
		});
		new.push(b, |p| {
			seen_new.push(p);
			!seen_new.last().is_some_and(|p| p == "one")
		});
		assert_eq!(seen_old, seen_new, "stop-at split {split} diverged");
	}
}

#[test]
fn reader_unterminated_tail_line_is_dropped_like_reference() {
	let body = b"data: partial-no-newline";
	let old = payloads_old(body, &[]);
	let new = payloads_new(body, &[]);
	assert_eq!(old, new);
	assert!(old.is_empty(), "tail without a terminating empty line never flushes");
}

// ---------------------------------------------------------------------------
// SseBuffer (responses) reference equivalence
// ---------------------------------------------------------------------------

mod responses_reference {
	pub struct OldBuffer {
		line: Vec<u8>,
		data: Vec<String>,
		skip_lf: bool,
	}

	impl OldBuffer {
		pub fn new() -> Self {
			OldBuffer { line: Vec::new(), data: Vec::new(), skip_lf: false }
		}

		pub fn push(&mut self, bytes: &[u8]) -> Vec<serde_json::Value> {
			let mut events: Vec<serde_json::Value> = Vec::new();
			for &byte in bytes {
				if self.skip_lf {
					self.skip_lf = false;
					if byte == b'\n' {
						continue;
					}
				}
				if byte != b'\r' && byte != b'\n' {
					self.line.push(byte);
					continue;
				}
				self.skip_lf = byte == b'\r';
				if self.line.is_empty() {
					let data = self.data.join("\n");
					self.data.clear();
					if !data.is_empty() && data.trim() != "[DONE]" {
						if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&data) {
							events.push(parsed);
						}
					}
				} else {
					let line = String::from_utf8_lossy(&self.line);
					if let Some(data) = line.strip_prefix("data:") {
						self.data.push(data.strip_prefix(' ').unwrap_or(data).to_string());
					}
					self.line.clear();
				}
			}
			events
		}
	}
}

fn responses_events_new(body: &[u8], splits: &[usize]) -> Vec<String> {
	let mut buffer = super::openai_responses::SseBuffer::default();
	let mut out = Vec::new();
	let mut offset = 0;
	for &split in splits {
		let next = offset + split;
		if next > body.len() {
			break;
		}
		for event in buffer.push(&body[offset..next]) {
			out.push(serde_json::to_string(&event).unwrap());
		}
		offset = next;
	}
	for event in buffer.push(&body[offset..]) {
		out.push(serde_json::to_string(&event).unwrap());
	}
	out
}

fn responses_events_old(body: &[u8], splits: &[usize]) -> Vec<String> {
	let mut buffer = responses_reference::OldBuffer::new();
	let mut out = Vec::new();
	let mut offset = 0;
	for &split in splits {
		let next = offset + split;
		if next > body.len() {
			break;
		}
		for event in buffer.push(&body[offset..next]) {
			out.push(serde_json::to_string(&event).unwrap());
		}
		offset = next;
	}
	for event in buffer.push(&body[offset..]) {
		out.push(serde_json::to_string(&event).unwrap());
	}
	out
}

fn assert_responses_equivalence(body: &[u8], label: &str) {
	let mut variants: Vec<Vec<usize>> = chunkings(body.len(), 0x9E3779B97F4A7C15, 16);
	variants.push(vec![body.len()]);
	variants.push((1..body.len()).collect());
	for (i, splits) in variants.iter().enumerate() {
		let old = responses_events_old(body, splits);
		let new = responses_events_new(body, splits);
		assert_eq!(old, new, "{label}: chunking #{i} (splits {splits:?}) diverged");
	}
}

#[test]
fn responses_buffer_matches_reference_on_typical_bodies() {
	let body = b"data: {\"type\":\"response.created\"}\r\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"hi\"}\r\n\r\ndata: [DONE]\r\n\r\n";
	assert_responses_equivalence(body, "crlf-events");
	let body = b": keepalive\r\rdata: {\"type\":\"a\"}\rdata: {\"type\":\"b\"}\r\rdata: [DONE]\n\n";
	assert_responses_equivalence(body, "cr-only");
}

#[test]
fn responses_buffer_matches_reference_on_split_crlf_and_utf8() {
	let body = "data: {\"delta\":\"Ready \u{2713} \u{65E5}\u{672C}\"}\r\n\r\ndata: [DONE]\r\n\r\n".as_bytes();
	assert_responses_equivalence(body, "utf8");
	let body = b"data: x\r\n\r\ndata: [DONE]\r\n\r\n";
	for split in 0..body.len() {
		let old = responses_events_old(body, &[split]);
		let new = responses_events_new(body, &[split]);
		assert_eq!(old, new, "split at {split} diverged");
	}
}

#[test]
fn responses_buffer_keeps_multi_data_line_join_and_done_filter() {
	let body = b"data: {\"a\":\ndata: 1}\n\ndata: [DONE]\n\n";
	let new = responses_events_new(body, &[]);
	assert_eq!(new, vec!["{\"a\":1}".to_string()]);
	let old = responses_events_old(body, &[]);
	assert_eq!(old, new);
}

#[test]
fn responses_buffer_preserves_skip_lf_across_empty_chunks() {
	// Regression: a CR that ends a chunk, then an EMPTY chunk, then the LF
	// that starts the next chunk. The skip_lf flag must survive the empty
	// chunk, or the LF terminates an empty line and both body halves fail
	// JSON parse separately: the event was silently LOST before the
	// `!bytes.is_empty()` guard (defect caught in adversarial review).
	let body = b"data: {\"a\":\r\ndata: 1}\r\n\r\n";
	// Splits are deltas: 12 bytes (ends on the CR), 0 (the empty chunk), rest.
	let splits: [usize; 3] = [12, 0, 13];
	let old = responses_events_old(body, &splits);
	let new = responses_events_new(body, &splits);
	assert_eq!(old, new);
	assert_eq!(new, vec!["{\"a\":1}".to_string()], "event lost on empty chunk between CR and LF");

	// Byte-at-a-time with an empty chunk after every byte: the flag must
	// survive any number of consecutive empty chunks.
	let mut splits: Vec<usize> = Vec::with_capacity(body.len() * 2);
	for _ in 0..body.len() {
		splits.push(1);
		splits.push(0);
	}
	let old = responses_events_old(body, &splits);
	let new = responses_events_new(body, &splits);
	assert_eq!(old, new);
	assert_eq!(new, vec!["{\"a\":1}".to_string()]);
}

#[test]
fn reader_handles_empty_chunks_like_reference() {
	// Same class of check for the completions reader: empty chunks are no-ops
	// there (lines terminate on `\n` only), asserted against the reference.
	let body = b"data: one\r\n\r\ndata: two\n\ndata: [DONE]\n\n";
	let splits: [usize; 3] = [10, 0, 20];
	let old = payloads_old(body, &splits);
	let new = payloads_new(body, &splits);
	assert_eq!(old, new);
	let mut splits: Vec<usize> = Vec::with_capacity(body.len() * 2);
	for _ in 0..body.len() {
		splits.push(1);
		splits.push(0);
	}
	assert_eq!(payloads_old(body, &splits), payloads_new(body, &splits));
}

// ---------------------------------------------------------------------------
// End-to-end event sequence regression (full stream through a local server)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn stream_event_sequence_is_stable_for_mixed_fixture() {
	use crate::types::{InputModality, ModelCost};
	use tokio::io::{AsyncReadExt, AsyncWriteExt};

	let body = "data: {\"id\":\"chatcmpl-1\",\"model\":\"repro-model\",\"choices\":[{\"index\":0,\"delta\":{\"reasoning_content\":\"think\"},\"finish_reason\":null}]}\r\n\r\n\
		data: {\"id\":\"chatcmpl-1\",\"model\":\"repro-model\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hello \"},\"finish_reason\":null}]}\r\n\r\n\
		data: {\"id\":\"chatcmpl-1\",\"model\":\"repro-model\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"✓\"},\"finish_reason\":null}]}\r\n\r\n\
		data: {\"id\":\"chatcmpl-1\",\"model\":\"repro-model\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\r\n\r\n\
		data: {\"id\":\"chatcmpl-1\",\"model\":\"repro-model\",\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":5,\"total_tokens\":15},\"choices\":[]}\r\n\r\n\
		data: [DONE]\r\n\r\n";

	let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await.unwrap();
	let address = listener.local_addr().unwrap();
	let server = tokio::spawn(async move {
		let (mut socket, _) = listener.accept().await.unwrap();
		let mut request = Vec::new();
		loop {
			let mut bytes = [0; 4096];
			let count = socket.read(&mut bytes).await.unwrap();
			assert_ne!(count, 0);
			request.extend_from_slice(&bytes[..count]);
			if let Some(end) = request.windows(4).position(|w| w == b"\r\n\r\n") {
				let headers = String::from_utf8_lossy(&request[..end]).to_string();
				let length: usize = headers
					.lines()
					.filter_map(|line| line.split_once(':'))
					.find(|(key, _)| key.eq_ignore_ascii_case("content-length"))
					.map(|(_, value)| value.trim().parse().unwrap())
					.unwrap();
				if request.len() >= end + 4 + length {
					break;
				}
			}
		}
		let response = format!(
			"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n{body}\r\n0\r\n\r\n",
			body.len()
		);
		socket.write_all(response.as_bytes()).await.unwrap();
	});

	let mut model = crate::types::Model {
		id: "repro-model".to_string(),
		name: "Repro Model".to_string(),
		api: "openai-completions".to_string(),
		provider: "repro-provider".to_string(),
		base_url: format!("http://{address}"),
		reasoning: true,
		input: vec![InputModality::Text],
		cost: ModelCost::zero(),
		context_window: 128_000.0,
		max_tokens: 4096.0,
		..Default::default()
	};
	model.base_url = format!("http://{address}");

	let context = crate::types::Context::new(
		None,
		vec![crate::types::Message::user(crate::types::UserMessage::new(
			crate::types::UserContent::Text("fixture".to_string()),
			1,
		))],
		None,
	);
	let options = super::openai_completions::OpenAICompletionsOptions {
		stream: crate::types::StreamOptions {
			api_key: Some("local-fixture-key".to_string()),
			..Default::default()
		},
		..Default::default()
	};
	let stream = super::openai_completions::stream_openai_completions(&model, &context, Some(options));

	let mut kinds: Vec<String> = Vec::new();
	let mut deltas: Vec<String> = Vec::new();
	let output = tokio::time::timeout(std::time::Duration::from_secs(5), stream.result()).await.unwrap();
	loop {
		let event = match tokio::time::timeout(std::time::Duration::from_secs(5), stream.next()).await {
			Ok(Some(event)) => event,
			_ => break,
		};
		match event {
			crate::types::AssistantMessageEvent::Start { .. } => kinds.push("start".into()),
			crate::types::AssistantMessageEvent::ThinkingStart { content_index, .. } => {
				kinds.push(format!("thinking_start:{content_index}"));
			}
			crate::types::AssistantMessageEvent::ThinkingDelta { delta, .. } => {
				kinds.push("thinking_delta".into());
				deltas.push(delta);
			}
			crate::types::AssistantMessageEvent::ThinkingEnd { content, .. } => {
				kinds.push("thinking_end".into());
				deltas.push(content);
			}
			crate::types::AssistantMessageEvent::TextStart { content_index, .. } => {
				kinds.push(format!("text_start:{content_index}"));
			}
			crate::types::AssistantMessageEvent::TextDelta { delta, .. } => {
				kinds.push("text_delta".into());
				deltas.push(delta);
			}
			crate::types::AssistantMessageEvent::TextEnd { content, .. } => {
				kinds.push("text_end".into());
				deltas.push(content);
			}
			crate::types::AssistantMessageEvent::Done { reason, message, .. } => {
				kinds.push(format!("done:{reason}"));
				assert_eq!(message.response_id.as_deref(), Some("chatcmpl-1"));
				assert_eq!(message.usage.total_tokens, 15.0);
			}
			_ => kinds.push("other".into()),
		}
	}
	// Blocks close at end of stream in block order (finish_block iterates 0..n),
	// so thinking_end follows the last text_delta, not the thinking delta.
	assert_eq!(
		kinds,
		vec![
			"start".to_string(),
			"thinking_start:0".to_string(),
			"thinking_delta".to_string(),
			"text_start:1".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"thinking_end".to_string(),
			"text_end".to_string(),
			"done:stop".to_string(),
		]
	);
	assert_eq!(deltas[0], "think");
	assert_eq!(deltas[1], "Hello ");
	assert_eq!(deltas[2], "\u{2713}");
	assert_eq!(deltas[3], "think");
	assert_eq!(deltas[4], "Hello \u{2713}");
	assert_eq!(output.stop_reason, "stop");
	server.await.unwrap();
}


// ---------------------------------------------------------------------------
// Responses SSE bench (SseBuffer)
// ---------------------------------------------------------------------------

fn responses_fixture(count: usize, delta_size: usize) -> Vec<u8> {
	let mut body = Vec::new();
	for _ in 0..count {
		let text = "y".repeat(delta_size);
		body.extend_from_slice(
			format!("data: {{\"type\":\"response.output_text.delta\",\"delta\":\"{text}\"}}\r\n\r\n").as_bytes(),
		);
	}
	body.extend_from_slice(b"data: {\"type\":\"response.completed\"}\r\n\r\n");
	body
}

/// Splits a CRLF-framed SSE body into whole-event slices (terminator included)
/// for the one-event-per-push percentile pass.
fn responses_event_slices(body: &[u8]) -> Vec<Vec<u8>> {
	let mut slices = Vec::new();
	let mut start = 0;
	let mut index = 0;
	while index + 3 < body.len() {
		if &body[index..index + 4] == b"\r\n\r\n" {
			slices.push(body[start..index + 4].to_vec());
			start = index + 4;
			index += 4;
		} else {
			index += 1;
		}
	}
	if start < body.len() {
		slices.push(body[start..].to_vec());
	}
	slices
}

/// Buffer-agnostic interface so one harness runs the reference (pre-change)
/// `SseBuffer` and the current one.
trait ResponsesBuffer {
	fn new_buffer() -> Self;
	fn push_buffer(&mut self, bytes: &[u8]) -> Vec<serde_json::Value>;
}

impl ResponsesBuffer for responses_reference::OldBuffer {
	fn new_buffer() -> Self {
		responses_reference::OldBuffer::new()
	}

	fn push_buffer(&mut self, bytes: &[u8]) -> Vec<serde_json::Value> {
		responses_reference::OldBuffer::push(self, bytes)
	}
}

impl ResponsesBuffer for super::openai_responses::SseBuffer {
	fn new_buffer() -> Self {
		super::openai_responses::SseBuffer::default()
	}

	fn push_buffer(&mut self, bytes: &[u8]) -> Vec<serde_json::Value> {
		super::openai_responses::SseBuffer::push(self, bytes)
	}
}

fn responses_bench<B: ResponsesBuffer>(body: &[u8], chunk_size: usize) -> (f64, usize) {
	let chunks = chunked(body, chunk_size);
	// Best of 3 clean passes (see `bench_framing`).
	let mut best = (f64::INFINITY, 0usize);
	for _ in 0..3 {
		let mut buffer = B::new_buffer();
		let mut events = 0usize;
		let start = Instant::now();
		for chunk in &chunks {
			events += buffer.push_buffer(chunk).len();
		}
		let total_ns = start.elapsed().as_nanos() as f64;
		if total_ns < best.0 {
			best = (total_ns, events);
		}
	}
	best
}

fn responses_bench_events<B: ResponsesBuffer>(events: &[Vec<u8>]) -> Vec<f64> {
	let mut buffer = B::new_buffer();
	let mut samples = Vec::with_capacity(events.len());
	for event in events {
		let start = Instant::now();
		buffer.push_buffer(event);
		samples.push(start.elapsed().as_nanos() as f64);
	}
	samples
}

#[test]
#[ignore = "micro-benchmark; run with --ignored --nocapture"]
fn responses_sse_bench() {
	let total_start = Instant::now();
	println!("=== responses SseBuffer A/B (framing + Value parse) ===");
	for (name, count, size) in [
		("text 100B x2000", 2000, 100),
		("text 1KB x2000", 2000, 1024),
		("text 4KB x2000", 2000, 4096),
	] {
		let body = responses_fixture(count, size);
		for chunk_size in [4 * 1024usize, 16 * 1024] {
			let (old_ns, events) = responses_bench::<responses_reference::OldBuffer>(&body, chunk_size);
			let (new_ns, _) = responses_bench::<super::openai_responses::SseBuffer>(&body, chunk_size);
			let old_mean = old_ns / events as f64;
			let new_mean = new_ns / events as f64;
			println!(
				"{name:<34} chunk {chunk_size:>6}B  old {old_mean:>8.0} ns/ev  new {new_mean:>8.0} ns/ev  delta {:+.1}%  {events} events",
				(new_mean - old_mean) / old_mean * 100.0
			);
		}
		// Percentile pass: one event per push, two clock reads per sample
		// (~40 ns, charged equally to both arms).
		let slices = responses_event_slices(&body);
		let old = responses_bench_events::<responses_reference::OldBuffer>(&slices);
		let new = responses_bench_events::<super::openai_responses::SseBuffer>(&slices);
		let (old_p50, old_p95, old_p99) = percentiles(old);
		let (p50, p95, p99) = percentiles(new);
		println!(
			"{name:<34} per-event  old p50 {old_p50:>8.0} p95 {old_p95:>8.0} p99 {old_p99:>8.0}  new p50 {p50:>8.0} p95 {p95:>8.0} p99 {p99:>8.0}"
		);
	}
	println!("\nTotal bench time: {:.1}s", total_start.elapsed().as_secs_f32());
}
