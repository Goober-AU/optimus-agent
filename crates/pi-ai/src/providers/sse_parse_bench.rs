//! SSE parse/forward micro-benchmark harness for the network/IPC performance
//! campaign (worker sse-streaming).
//!
//! Feeds synthetic SSE fixtures through the REAL parse/forward pipeline
//! (`SseLineReader` framing -> `forward_sse_payload` -> typed consumer) with a
//! no-op sink, and reports events/s plus ns/event percentiles.
//!
//! A/B: the pre-change readers are kept verbatim in `reference` and
//! `responses_reference`; every fixture runs through both arms in the same
//! binary, so the printed delta isolates the reader change alone. The
//! consumer hot path gets the same treatment in `consumer_reference`: the
//! pre-change typed consumer (full-snapshot `partial_message` per event)
//! runs against the live consumer, and byte-identity tests pin the event
//! sequence (including partial snapshots) across the change.
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

	println!("\n=== consumer hot-path A/B (typed parse + handle_typed_chunk + partial snapshot, no framing) ===");
	println!("(events stay resident: every partial snapshot is the full message so far)");
	let consumer_fixtures: Vec<(&str, Vec<u8>)> = vec![
		("text 100B", text_fixture(&vec![100; 200])),
		("text 1KB", text_fixture(&vec![1024; 200])),
		("text 4KB", text_fixture(&vec![4096; 200])),
		("reasoning-dense 100B", reasoning_fixture(200, 100)),
		("reasoning-dense 1KB", reasoning_fixture(200, 1024)),
		("tool-call-delta", tool_call_fixture(200)),
	];
	for (name, body) in &consumer_fixtures {
		let payloads = fixture_payloads(body);
		let old = bench_consumer_path::<OldConsumer>(&payloads, &model);
		let new = bench_consumer_path::<NewConsumer>(&payloads, &model);
		let old_mean = old.total_ns / old.events as f64;
		let new_mean = new.total_ns / new.events as f64;
		let (op50, op95, op99) = percentiles(old.per_event.clone());
		let (p50, p95, p99) = percentiles(new.per_event.clone());
		println!(
			"{:<44} old {:>9.0} ns/ev  new {:>9.0} ns/ev  delta {:+6.1}%",
			format!("{name} x{}", old.events),
			old_mean,
			new_mean,
			(new_mean - old_mean) / old_mean * 100.0,
		);
		println!(
			"    old p50 {op50:>8.0} p95 {op95:>8.0} p99 {op99:>8.0}  new p50 {p50:>8.0} p95 {p95:>8.0} p99 {p99:>8.0}"
		);
	}

	println!("\n=== consumer decile growth probe (per-event ns by decile, per arm) ===");
	{
		let body = text_fixture(&vec![4096; 200]);
		let payloads = fixture_payloads(&body);
		decile_growth::<OldConsumer>(&payloads, &model, "old text 4KB x200");
		decile_growth::<NewConsumer>(&payloads, &model, "new text 4KB x200");
		let body = tool_call_fixture(200);
		let payloads = fixture_payloads(&body);
		decile_growth::<OldConsumer>(&payloads, &model, "old tool-call x200");
		decile_growth::<NewConsumer>(&payloads, &model, "new tool-call x200");
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

// ---------------------------------------------------------------------------
// Phase 5: consumer hot-path A/B (partial_message lane) + responses consumer bench
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Phase 5: consumer hot-path A/B (partial_message lane)
// ---------------------------------------------------------------------------

/// The pre-change consumer path, kept verbatim from the committed
/// `openai_completions.rs` (the HEAD of this branch before the
/// partial_message lane landed): typed parse, `handle_typed_chunk`, and the
/// per-event full-snapshot `partial_message` clone. The live consumer must
/// produce a byte-identical event sequence; only cost may change.
// The module keeps the pre-change consumer VERBATIM (byte-for-byte from the
// committed file), so parts of it (finish path, error fields) are not driven
// by the bench loop; dead-code is expected and allowed to keep the reference
// honest.
#[allow(dead_code)]
mod consumer_reference {
	use std::collections::HashMap;

	use indexmap::IndexMap;
	use serde_json::{json, Map, Value};

	use crate::models::{calculate_cost, CostOverrides};
	use crate::providers::openai_completions::OpenAICompletionsOptions;
	use crate::types::{
		AssistantMessage, AssistantMessageEvent, ContentBlock, Model, TextContent, ThinkingContent, ToolCall,
	};
	use crate::utils::event_stream::AssistantMessageEventStream;
	use crate::utils::json_parse::StreamingJsonAccumulator;

/// JavaScript truthiness for the `if (x)` guards the TypeScript uses on raw JSON.
fn js_truthy(value: &Value) -> bool {
	match value {
		Value::Null => false,
		Value::Bool(value) => *value,
		Value::Number(number) => number.as_f64().map(|value| value != 0.0).unwrap_or(true),
		Value::String(value) => !value.is_empty(),
		Value::Array(_) | Value::Object(_) => true,
	}
}


const REASONING_DETAILS_SIGNATURE_TYPE: &str = "openai-completions.reasoning_details.v1";

/// TS: `encodeReasoningDetails(details)`
fn encode_reasoning_details(details: &[Map<String, Value>]) -> String {
	let mut object = Map::new();
	object.insert(
		"type".to_string(),
		Value::String(REASONING_DETAILS_SIGNATURE_TYPE.to_string()),
	);
	object.insert(
		"details".to_string(),
		Value::Array(details.iter().cloned().map(Value::Object).collect()),
	);
	Value::Object(object).to_string()
}

/// One `data:` SSE payload, borrowed from the payload buffer.
///
/// Field tolerances mirror the `Value` consumer exactly: wrong-typed fields
/// read as absent, unknown fields are ignored, `null` is absent, and rare
/// members (`usage`, `error`, `reasoning_details`) keep the `Value` DOM so it
/// is only built when the member is present.
#[derive(serde::Deserialize)]
pub(crate) struct TypedChunk<'a> {
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	id: Option<&'a str>,
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	model: Option<&'a str>,
	#[serde(default, deserialize_with = "crate::utils::typed_json::optional_value")]
	usage: Option<Value>,
	#[serde(default, deserialize_with = "crate::utils::typed_json::optional_value")]
	error: Option<Value>,
	#[serde(default)]
	choices: Vec<TypedChoice<'a>>,
}

#[derive(serde::Deserialize)]
struct TypedChoice<'a> {
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::truthy_lenient_string")]
	finish_reason: Option<crate::utils::typed_json::TruthyLenientString<'a>>,
	#[serde(default, deserialize_with = "crate::utils::typed_json::optional_value")]
	usage: Option<Value>,
	#[serde(default)]
	delta: Option<TypedDelta<'a>>,
}

#[derive(serde::Deserialize)]
struct TypedDelta<'a> {
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	content: Option<&'a str>,
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	reasoning_content: Option<&'a str>,
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	reasoning: Option<&'a str>,
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	reasoning_text: Option<&'a str>,
	#[serde(default)]
	tool_calls: Vec<TypedToolCall<'a>>,
	#[serde(default, deserialize_with = "crate::utils::typed_json::optional_value")]
	reasoning_details: Option<Value>,
}

#[derive(serde::Deserialize)]
struct TypedToolCall<'a> {
	#[serde(default, deserialize_with = "crate::utils::typed_json::optional_i64")]
	index: Option<i64>,
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	id: Option<&'a str>,
	#[serde(default)]
	function: Option<TypedFunction<'a>>,
}

#[derive(serde::Deserialize)]
struct TypedFunction<'a> {
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	name: Option<&'a str>,
	#[serde(borrow, default, deserialize_with = "crate::utils::typed_json::optional_borrowed_str")]
	arguments: Option<&'a str>,
}

/// Parse one payload with the typed shape; `None` routes to the `Value`
/// fallback (not JSON, not an object, or any typed-shape mismatch, so a
/// duplicate-key or exotic document keeps the exact old behavior).
pub(crate) fn parse_typed_chunk(payload: &str) -> Option<TypedChunk<'_>> {
	if !payload.trim_start().starts_with('{') {
		return None;
	}
	serde_json::from_str(payload).ok()
}


/// TS: `parseChunkUsage(rawUsage, model, cacheWriteCost?)`
pub(crate) fn parse_chunk_usage(raw_usage: &Value, model: &Model, cache_write_cost: Option<f64>) -> crate::types::Usage {
	let prompt_tokens = raw_usage
		.get("prompt_tokens")
		.and_then(Value::as_f64)
		.filter(|value| *value != 0.0)
		.unwrap_or(0.0);
	let prompt_tokens_details = raw_usage.get("prompt_tokens_details");
	let reported_cached_tokens = prompt_tokens_details
		.and_then(|details| details.get("cached_tokens"))
		.and_then(Value::as_f64)
		.or_else(|| raw_usage.get("prompt_cache_hit_tokens").and_then(Value::as_f64))
		.unwrap_or(0.0);
	let cache_write_tokens = prompt_tokens_details
		.and_then(|details| details.get("cache_write_tokens"))
		.and_then(Value::as_f64)
		.filter(|value| *value != 0.0)
		.unwrap_or(0.0);

	// Normalize to pi-ai semantics:
	// - cacheRead: hits from cache created by previous requests only
	// - cacheWrite: tokens written to cache in this request
	// Some OpenAI-compatible providers (observed on OpenRouter) report cached_tokens
	// as (previous hits + current writes). In that case, remove cacheWrite from cacheRead.
	let cache_read_tokens = if cache_write_tokens > 0.0 {
		(reported_cached_tokens - cache_write_tokens).max(0.0)
	} else {
		reported_cached_tokens
	};

	let input = (prompt_tokens - cache_read_tokens - cache_write_tokens).max(0.0);
	// OpenAI completion_tokens already includes reasoning_tokens.
	let output_tokens = raw_usage
		.get("completion_tokens")
		.and_then(Value::as_f64)
		.filter(|value| *value != 0.0)
		.unwrap_or(0.0);
	let mut usage = crate::types::Usage {
		input,
		output: output_tokens,
		cache_read: cache_read_tokens,
		cache_write: cache_write_tokens,
		total_tokens: input + output_tokens + cache_read_tokens + cache_write_tokens,
		cost: crate::types::UsageCost::zero(),
	};
	let overrides = cache_write_cost.map(|cache_write| CostOverrides {
		cache_write: Some(cache_write),
	});
	calculate_cost(model, &mut usage, overrides.as_ref());
	usage
}


/// TS: `mapStopReason(reason)` result.
struct StopReasonResult {
	stop_reason: String,
	error_message: Option<String>,
}

/// TS: `mapStopReason(reason)`
fn map_stop_reason(reason: Option<&Value>) -> StopReasonResult {
	let Some(reason) = reason else {
		return StopReasonResult {
			stop_reason: "stop".to_string(),
			error_message: None,
		};
	};
	if reason.is_null() {
		return StopReasonResult {
			stop_reason: "stop".to_string(),
			error_message: None,
		};
	}
	let reason = match reason {
		Value::String(reason) => reason.clone(),
		other => other.to_string(),
	};
	match reason.as_str() {
		"stop" | "end" => StopReasonResult {
			stop_reason: "stop".to_string(),
			error_message: None,
		},
		"length" => StopReasonResult {
			stop_reason: "length".to_string(),
			error_message: None,
		},
		"function_call" | "tool_calls" => StopReasonResult {
			stop_reason: "toolUse".to_string(),
			error_message: None,
		},
		"content_filter" => StopReasonResult {
			stop_reason: "error".to_string(),
			error_message: Some("Provider finish_reason: content_filter".to_string()),
		},
		"network_error" => StopReasonResult {
			stop_reason: "error".to_string(),
			error_message: Some("Provider finish_reason: network_error".to_string()),
		},
		_ => StopReasonResult {
			stop_reason: "error".to_string(),
			error_message: Some(format!("Provider finish_reason: {reason}")),
		},
	}
}


fn observe_chunk_usage(raw: &Value, model: &Model, options: Option<&OpenAICompletionsOptions>, stream: &AssistantMessageEventStream) {
	let Some(observer) = options.and_then(|options| options.stream.on_usage_observation.as_ref()) else { return; };
	let finite = |value: Option<&Value>| value.and_then(Value::as_f64).filter(|value| value.is_finite() && *value >= 0.0);
	let observation = crate::types::ProviderUsageObservation {
		input_tokens: Some(finite(raw.get("prompt_tokens"))),
		cached_input_tokens: Some(finite(raw.get("prompt_tokens_details").and_then(|v| v.get("cached_tokens")))
			.or_else(|| finite(raw.get("prompt_cache_hit_tokens")))),
		output_tokens: Some(finite(raw.get("completion_tokens"))),
		reasoning_tokens: Some(finite(raw.get("completion_tokens_details").and_then(|v| v.get("reasoning_tokens")))),
		total_tokens: Some(finite(raw.get("total_tokens"))),
		// Raw OpenAI-compatible wire totals, not pi-ai's normalized uncached input.
		cached_input_included_in_input: Some(Some(true)),
		reasoning_included_in_output: Some(Some(true)),
	};
	if let Ok(future) = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| observer(observation, model))) {
		// Monitoring stays off the output path, but its completion is owned.
		stream.spawn(future);
	}
}


/// TS: `interface StreamingToolCallBlock extends ToolCall { partialArgs?; streamIndex? }`
#[derive(Debug, Clone, Default)]
struct StreamingToolCallBlock {
	tool_call: ToolCall,
	partial_args: Option<StreamingJsonAccumulator>,
	stream_index: Option<i64>,
}

/// TS: `type StreamingBlock = TextContent | ThinkingContent | StreamingToolCallBlock`
#[derive(Debug, Clone)]
enum StreamingBlock {
	Text(TextContent),
	Thinking(ThinkingContent),
	ToolCall(StreamingToolCallBlock),
}

/// The streaming accumulator for one response: it mirrors the TypeScript closures
/// (`finishBlock`, `ensureTextBlock`, `ensureThinkingBlock`, `ensureToolCallBlock`)
/// and the `blocks` array that `output.content` aliases.
pub(crate) struct StreamState {
	blocks: Vec<StreamingBlock>,
	text_block: Option<usize>,
	thinking_block: Option<usize>,
	tool_call_blocks_by_index: HashMap<i64, usize>,
	tool_call_blocks_by_id: HashMap<String, usize>,
	reasoning_details_by_index: HashMap<i64, Map<String, Value>>,
	next_reasoning_details_index: i64,
	reasoning_details_block: Option<usize>,
}

impl StreamState {
	pub(crate) fn new() -> Self {
		Self {
			blocks: Vec::new(),
			text_block: None,
			thinking_block: None,
			tool_call_blocks_by_index: HashMap::new(),
			tool_call_blocks_by_id: HashMap::new(),
			reasoning_details_by_index: HashMap::new(),
			next_reasoning_details_index: 0,
			reasoning_details_block: None,
		}
	}

	/// `output.content` - the assistant message content the stream events carry.
	fn content(&self) -> Vec<ContentBlock> {
		self.blocks
			.iter()
			.map(|block| match block {
				StreamingBlock::Text(text) => ContentBlock::Text(text.clone()),
				StreamingBlock::Thinking(thinking) => ContentBlock::Thinking(thinking.clone()),
				StreamingBlock::ToolCall(tool_call) => ContentBlock::ToolCall(tool_call.tool_call.clone()),
			})
			.collect()
	}
}


/// The `partial: output` payload: `output.content` aliases the live `blocks` array.
fn partial_message(output: &AssistantMessage, state: &StreamState) -> AssistantMessage {
	let mut partial = output.clone();
	partial.content = state.content();
	partial
}

fn flush_tool_arguments(block: &mut StreamingToolCallBlock) {
	if let Some(mut arguments) = block.partial_args.take() {
		if let Some(parsed) = arguments.flush() {
			block.tool_call.arguments = parsed.as_object().cloned().unwrap_or_default();
		}
	}
	block.stream_index = None;
}

/// TS: `getContentIndex(block)`
fn finish_block(
	state: &mut StreamState,
	content_index: usize,
	output: &AssistantMessage,
	stream: &AssistantMessageEventStream,
) {
	if let StreamingBlock::ToolCall(block) = &mut state.blocks[content_index] {
		flush_tool_arguments(block);
	}
	let partial = partial_message(output, state);
	match &mut state.blocks[content_index] {
		StreamingBlock::Text(block) => {
			let content = block.text.clone();
			stream.push(AssistantMessageEvent::TextEnd {
				content_index,
				content,
				partial,
			});
		}
		StreamingBlock::Thinking(block) => {
			let content = block.thinking.clone();
			stream.push(AssistantMessageEvent::ThinkingEnd {
				content_index,
				content,
				partial,
			});
		}
		StreamingBlock::ToolCall(block) => {
			let tool_call = block.tool_call.clone();
			stream.push(AssistantMessageEvent::ToolCallEnd {
				content_index,
				tool_call,
				partial,
			});
		}
	}
}

/// TS: `ensureTextBlock()`
fn ensure_text_block(
	state: &mut StreamState,
	output: &AssistantMessage,
	stream: &AssistantMessageEventStream,
) -> usize {
	if state.text_block.is_none() {
		state.blocks.push(StreamingBlock::Text(TextContent::new("")));
		let index = state.blocks.len() - 1;
		state.text_block = Some(index);
		let partial = partial_message(output, state);
		stream.push(AssistantMessageEvent::TextStart {
			content_index: index,
			partial,
		});
	}
	state.text_block.expect("text block")
}

/// TS: `ensureThinkingBlock(thinkingSignature)`
fn ensure_thinking_block(
	state: &mut StreamState,
	output: &AssistantMessage,
	stream: &AssistantMessageEventStream,
	thinking_signature: &str,
) -> usize {
	if state.thinking_block.is_none() {
		let mut block = ThinkingContent::new("");
		block.thinking_signature = Some(thinking_signature.to_string());
		state.blocks.push(StreamingBlock::Thinking(block));
		let index = state.blocks.len() - 1;
		state.thinking_block = Some(index);
		let partial = partial_message(output, state);
		stream.push(AssistantMessageEvent::ThinkingStart {
			content_index: index,
			partial,
		});
	}
	state.thinking_block.expect("thinking block")
}


/// `ensure_tool_call_block` for the borrowed tool-call shape. Same block
/// bookkeeping, no `Value` lookups.
fn ensure_typed_tool_call_block(
	state: &mut StreamState,
	output: &AssistantMessage,
	stream: &AssistantMessageEventStream,
	tool_call: &TypedToolCall<'_>,
) -> usize {
	let stream_index = tool_call.index;
	let tool_call_id = tool_call.id.unwrap_or_default();
	let tool_call_name = tool_call
		.function
		.as_ref()
		.and_then(|function| function.name)
		.unwrap_or_default();

	let mut block_index = stream_index.and_then(|index| state.tool_call_blocks_by_index.get(&index).copied());
	if block_index.is_none() && !tool_call_id.is_empty() {
		block_index = state.tool_call_blocks_by_id.get(tool_call_id).copied();
	}
	let index = match block_index {
		Some(index) => index,
		None => {
			let block = StreamingToolCallBlock {
				tool_call: ToolCall::new(tool_call_id.to_string(), tool_call_name.to_string(), Map::new()),
				partial_args: Some(StreamingJsonAccumulator::default()),
				stream_index,
			};
			state.blocks.push(StreamingBlock::ToolCall(block));
			let index = state.blocks.len() - 1;
			if let Some(stream_index) = stream_index {
				state.tool_call_blocks_by_index.insert(stream_index, index);
			}
			if !tool_call_id.is_empty() {
				state.tool_call_blocks_by_id.insert(tool_call_id.to_string(), index);
			}
			let partial = partial_message(output, state);
			stream.push(AssistantMessageEvent::ToolCallStart {
				content_index: index,
				partial,
			});
			index
		}
	};

	let mut register_index: Option<i64> = None;
	if let StreamingBlock::ToolCall(block) = &mut state.blocks[index] {
		if let Some(stream_index) = stream_index {
			if block.stream_index.is_none() {
				block.stream_index = Some(stream_index);
				register_index = Some(stream_index);
			}
		}
	}
	if let Some(stream_index) = register_index {
		state.tool_call_blocks_by_index.insert(stream_index, index);
	}
	if !tool_call_id.is_empty() {
		state.tool_call_blocks_by_id.insert(tool_call_id.to_string(), index);
	}
	index
}


/// The `reasoning_details` merge from the Value consumer, shared by both paths.
fn handle_reasoning_details(
	reasoning_details: &[Value],
	state: &mut StreamState,
	output: &mut AssistantMessage,
	stream: &AssistantMessageEventStream,
) {
	for detail in reasoning_details {
		let Some(detail_record) = detail.as_object() else {
			continue;
		};
		let explicit_index = detail_record.get("index").and_then(Value::as_i64);
		let index = explicit_index.unwrap_or(state.next_reasoning_details_index);
		state.next_reasoning_details_index = state.next_reasoning_details_index.max(index + 1);
		let previous_detail = state.reasoning_details_by_index.get(&index).cloned();
		let mut merged_detail = previous_detail.clone().unwrap_or_default();
		for (key, value) in detail_record {
			merged_detail.insert(key.clone(), value.clone());
		}
		for field in ["text", "summary"] {
			let previous_fragment = previous_detail
				.as_ref()
				.and_then(|detail| detail.get(field))
				.and_then(Value::as_str);
			let fragment = detail_record.get(field).and_then(Value::as_str);
			if let (Some(previous_fragment), Some(fragment)) = (previous_fragment, fragment) {
				merged_detail.insert(
					field.to_string(),
					Value::String(format!("{previous_fragment}{fragment}")),
				);
			}
		}
		state.reasoning_details_by_index.insert(index, merged_detail);
		if detail_record.get("type").and_then(Value::as_str) == Some("reasoning.encrypted")
			&& detail_record.get("id").and_then(Value::as_str).is_some()
			&& detail_record.get("data").map(|data| !data.is_null()).unwrap_or(false)
		{
			let detail_id = detail_record.get("id").and_then(Value::as_str).unwrap_or_default();
			let matching = state.blocks.iter_mut().find(|block| match block {
				StreamingBlock::ToolCall(tool_call) => tool_call.tool_call.id == detail_id,
				_ => false,
			});
			if let Some(StreamingBlock::ToolCall(tool_call)) = matching {
				tool_call.tool_call.thought_signature = Some(Value::Object(detail_record.clone()).to_string());
			}
		}
	}

	if !state.reasoning_details_by_index.is_empty() {
		let index = match state.reasoning_details_block {
			Some(index) => index,
			None => {
				let mut block = ThinkingContent::new("");
				block.redacted = Some(true);
				state.blocks.push(StreamingBlock::Thinking(block));
				let index = state.blocks.len() - 1;
				state.reasoning_details_block = Some(index);
				let partial = partial_message(output, state);
				stream.push(AssistantMessageEvent::ThinkingStart {
					content_index: index,
					partial,
				});
				index
			}
		};
		let mut indexes: Vec<i64> = state.reasoning_details_by_index.keys().copied().collect();
		indexes.sort_unstable();
		let details: Vec<Map<String, Value>> = indexes
			.iter()
			.filter_map(|index| state.reasoning_details_by_index.get(index).cloned())
			.collect();
		if let StreamingBlock::Thinking(block) = &mut state.blocks[index] {
			block.thinking_signature = Some(encode_reasoning_details(&details));
		}
	}
}


/// The borrowed fast path for the common chunk shapes: same observable
/// behavior as `handle_value_chunk`, no `Value` DOM for the hot fields.
#[allow(clippy::too_many_arguments)]
pub(crate) fn handle_typed_chunk(
	typed: TypedChunk<'_>,
	model: &Model,
	options_ref: Option<&OpenAICompletionsOptions>,
	output: &mut AssistantMessage,
	state: &mut StreamState,
	stream: &AssistantMessageEventStream,
	cache_write_cost: Option<f64>,
	response_headers: &IndexMap<String, String>,
	finished: &mut bool,
) -> Result<(), StreamError> {
	if let Some(error) = typed.error.as_ref().filter(|error| js_truthy(error)) {
		return Err(StreamError::in_stream_api_error(error, response_headers));
	}
	if output.response_id.as_deref().map(str::is_empty).unwrap_or(true) {
		if let Some(id) = typed.id {
			output.response_id = Some(id.to_string());
		}
	}
	if let Some(chunk_model) = typed.model {
		if !chunk_model.is_empty() && chunk_model != model.id {
			if output.response_model.as_deref().map(str::is_empty).unwrap_or(true) {
				output.response_model = Some(chunk_model.to_string());
			}
		}
	}
	let chunk_usage = typed.usage.as_ref().filter(|value| js_truthy(value));
	if let Some(chunk_usage) = chunk_usage {
		output.usage = parse_chunk_usage(chunk_usage, model, cache_write_cost);
		observe_chunk_usage(chunk_usage, model, options_ref, stream);
	}
	// `choices.first()` matches the Value consumer; a non-object array element
	// deserializes to a default-shaped choice whose field reads all yield
	// `None`, like `choices[0]` on a non-object did.
	let Some(choice) = typed.choices.first() else {
		return Ok(());
	};
	if chunk_usage.is_none() {
		if let Some(choice_usage) = choice.usage.as_ref().filter(|value| js_truthy(value)) {
			output.usage = parse_chunk_usage(choice_usage, model, cache_write_cost);
			observe_chunk_usage(choice_usage, model, options_ref, stream);
		}
	}
	if let Some(finish_reason) = choice.finish_reason.as_ref().filter(|reason| reason.is_truthy()) {
		*finished = true;
		let reason_value = Value::String(finish_reason.as_str().to_string());
		let finish_reason_result = map_stop_reason(Some(&reason_value));
		output.stop_reason = finish_reason_result.stop_reason;
		if let Some(error_message) = finish_reason_result.error_message {
			output.error_message = Some(error_message);
		}
	}
	let Some(delta) = choice.delta.as_ref() else {
		return Ok(());
	};
	if let Some(content) = delta.content {
		if !content.is_empty() {
			let index = ensure_text_block(state, output, stream);
			if let StreamingBlock::Text(block) = &mut state.blocks[index] {
				block.text.push_str(content);
			}
			let partial = partial_message(output, state);
			stream.push(AssistantMessageEvent::TextDelta {
				content_index: index,
				delta: content.to_string(),
				partial,
			});
		}
	}

	// Some endpoints return reasoning in reasoning_content (llama.cpp),
	// or reasoning (other openai compatible endpoints)
	// Use the first non-empty reasoning field to avoid duplication
	// (e.g., chutes.ai returns both reasoning_content and reasoning with same content)
	let reasoning_fields = [
		("reasoning_content", delta.reasoning_content),
		("reasoning", delta.reasoning),
		("reasoning_text", delta.reasoning_text),
	];
	if let Some((field_name, found_reasoning_field)) = reasoning_fields
		.iter()
		.find_map(|(name, field)| field.filter(|text| !text.is_empty()).map(|text| (*name, text)))
	{
		let index = ensure_thinking_block(state, output, stream, field_name);
		if let StreamingBlock::Thinking(block) = &mut state.blocks[index] {
			block.thinking.push_str(found_reasoning_field);
		}
		let partial = partial_message(output, state);
		stream.push(AssistantMessageEvent::ThinkingDelta {
			content_index: index,
			delta: found_reasoning_field.to_string(),
			partial,
		});
	}

	for tool_call in &delta.tool_calls {
		let index = ensure_typed_tool_call_block(state, output, stream, tool_call);
		let tool_call_id = tool_call.id;
		let tool_call_name = tool_call.function.as_ref().and_then(|function| function.name);
		let mut register_id: Option<&str> = None;
		if let StreamingBlock::ToolCall(block) = &mut state.blocks[index] {
			if block.tool_call.id.is_empty() {
				if let Some(tool_call_id) = tool_call_id {
					block.tool_call.id = tool_call_id.to_string();
					register_id = Some(tool_call_id);
				}
			}
			if block.tool_call.name.is_empty() {
				if let Some(tool_call_name) = tool_call_name {
					block.tool_call.name = tool_call_name.to_string();
				}
			}
		}
		if let Some(register_id) = register_id {
			state.tool_call_blocks_by_id.insert(register_id.to_string(), index);
		}

		let mut delta_text = String::new();
		if let Some(arguments) = tool_call.function.as_ref().and_then(|function| function.arguments) {
			delta_text = arguments.to_string();
			if let StreamingBlock::ToolCall(block) = &mut state.blocks[index] {
				let partial_args = block.partial_args.get_or_insert_with(StreamingJsonAccumulator::default);
				if let Some(parsed) = partial_args.append(arguments) {
					block.tool_call.arguments = parsed.as_object().cloned().unwrap_or_default();
				}
			}
		}
		let partial = partial_message(output, state);
		stream.push(AssistantMessageEvent::ToolCallDelta {
			content_index: index,
			delta: delta_text,
			partial,
		});
	}

	if let Some(reasoning_details) = delta.reasoning_details.as_ref().and_then(Value::as_array) {
		handle_reasoning_details(reasoning_details, state, output, stream);
	}
	Ok(())
}


/// A thrown provider/SDK error: `message` is what the TypeScript catch turns into
/// `output.errorMessage`, `value` is the structured shape `recordStreamFailure`
/// inspects.
#[derive(Debug, Clone)]
pub(crate) struct StreamError {
	message: String,
	value: Value,
}

impl StreamError {
	/// TS SDK `APIError.makeMessage(status, error, message)` text.
	fn api_error_message(status: Option<u16>, error: Option<&Value>, message: Option<&str>) -> String {
		// `const msg = error?.message ? typeof error.message === 'string' ? error.message :
		// JSON.stringify(error.message) : error ? JSON.stringify(error) : message;`
		let msg = error
			.and_then(|error| error.get("message"))
			.filter(|value| js_truthy(value))
			.map(|value| match value {
				Value::String(text) => text.clone(),
				other => other.to_string(),
			})
			.or_else(|| error.filter(|error| js_truthy(error)).map(|error| error.to_string()))
			.or_else(|| message.map(str::to_string));
		match (msg, status) {
			(Some(msg), Some(status)) if !msg.is_empty() => format!("{status} {msg}"),
			(_, Some(status)) => format!("{status} status code (no body)"),
			(Some(msg), None) => msg,
			(None, None) => "(no status code or body)".to_string(),
		}
	}


	/// The `headers` property of a thrown SDK error (`Headers` -> record).
	fn error_headers_value(headers: &IndexMap<String, String>) -> Value {
		let mut header_map = Map::new();
		for (name, header_value) in headers {
			header_map.insert(name.clone(), Value::String(header_value.clone()));
		}
		Value::Object(header_map)
	}


	/// The SDK's in-stream error throw (`Stream.fromSSEResponse`):
	/// `if (data && data.error) throw new APIError(undefined, data.error, undefined, response.headers);`
	fn in_stream_api_error(error: &Value, headers: &IndexMap<String, String>) -> Self {
		let text = Self::api_error_message(None, Some(error), None);
		let mut value = json!({
			"name": "Error",
			"message": text.clone(),
			"error": error.clone(),
		});
		if let Some(object) = value.as_object_mut() {
			object.insert("headers".to_string(), Self::error_headers_value(headers));
		}
		Self { message: text, value }
	}
}

}


/// Consumer-agnostic interface: one arm is the verbatim pre-change consumer
/// (`consumer_reference`), the other is the live production consumer. Both
/// take the same pre-framed payload list, so the printed delta isolates the
/// consumer change alone.
trait ConsumerPath {
	fn new_consumer() -> Self;
	fn consume(
		&mut self,
		payload: &str,
		model: &crate::types::Model,
		output: &mut crate::types::AssistantMessage,
		stream: &crate::utils::event_stream::AssistantMessageEventStream,
		headers: &indexmap::IndexMap<String, String>,
		finished: &mut bool,
	) -> bool;
}

struct NewConsumer {
	state: super::openai_completions::StreamState,
}

impl ConsumerPath for NewConsumer {
	fn new_consumer() -> Self {
		NewConsumer { state: super::openai_completions::StreamState::new() }
	}

	fn consume(
		&mut self,
		payload: &str,
		model: &crate::types::Model,
		output: &mut crate::types::AssistantMessage,
		stream: &crate::utils::event_stream::AssistantMessageEventStream,
		headers: &indexmap::IndexMap<String, String>,
		finished: &mut bool,
	) -> bool {
		let Some(typed) = super::openai_completions::parse_typed_chunk(payload) else {
			return false;
		};
		super::openai_completions::handle_typed_chunk(
			typed,
			model,
			None::<&super::openai_completions::OpenAICompletionsOptions>,
			output,
			&mut self.state,
			stream,
			None,
			headers,
			finished,
		)
		.is_ok()
	}
}

struct OldConsumer {
	state: consumer_reference::StreamState,
}

impl ConsumerPath for OldConsumer {
	fn new_consumer() -> Self {
		OldConsumer { state: consumer_reference::StreamState::new() }
	}

	fn consume(
		&mut self,
		payload: &str,
		model: &crate::types::Model,
		output: &mut crate::types::AssistantMessage,
		stream: &crate::utils::event_stream::AssistantMessageEventStream,
		headers: &indexmap::IndexMap<String, String>,
		finished: &mut bool,
	) -> bool {
		let Some(typed) = consumer_reference::parse_typed_chunk(payload) else {
			return false;
		};
		consumer_reference::handle_typed_chunk(
			typed,
			model,
			None::<&super::openai_completions::OpenAICompletionsOptions>,
			output,
			&mut self.state,
			stream,
			None,
			headers,
			finished,
		)
		.is_ok()
	}
}

/// Payloads (pre-framed `data:` values, `[DONE]` excluded) for the consumer
/// A/B: framing is out of scope, so the arms see identical input lists.
fn fixture_payloads(body: &[u8]) -> Vec<String> {
	let mut reader = SseLineReader::new();
	let mut payloads = Vec::new();
	let _ = reader.push(body, |payload| {
		if !payload.starts_with("[DONE]") {
			payloads.push(payload);
		}
		true
	});
	if let Some(payload) = reader.finish() {
		if !payload.starts_with("[DONE]") {
			payloads.push(payload);
		}
	}
	payloads
}

/// Consumer A/B arm: typed parse + `handle_typed_chunk` (the partial snapshot
/// hot path) over pre-framed payloads, no framing. The event stream is never
/// drained, so each event (and its partial snapshot) stays resident; fixture
/// counts are bounded for that reason.
fn bench_consumer_path<P: ConsumerPath>(payloads: &[String], model: &crate::types::Model) -> Sample {
	let run = |mut collect: Option<&mut Vec<f64>>| -> (f64, usize) {
		let mut consumer = P::new_consumer();
		let mut output = crate::types::AssistantMessage::new(
			model.api.clone(),
			model.provider.clone(),
			model.id.clone(),
			0,
		);
		output.usage = crate::types::Usage::zero();
		let stream = crate::utils::event_stream::create_assistant_message_event_stream();
		let stream = stream.producer_handle();
		let headers: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
		let mut finished = false;
		let mut events = 0usize;
		let start = Instant::now();
		for payload in payloads {
			let event_start = collect.is_some().then(Instant::now);
			if consumer.consume(payload, model, &mut output, &stream, &headers, &mut finished) {
				events += 1;
			}
			if let (Some(samples), Some(event_start)) = (collect.as_mut(), event_start) {
				samples.push(event_start.elapsed().as_nanos() as f64);
			}
		}
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

/// First/last-decile mean of per-event samples for one arm: the linear-in-text
/// per-event growth that makes the total quadratic when every event clones the
/// full snapshot.
fn decile_growth<P: ConsumerPath>(payloads: &[String], model: &crate::types::Model, label: &str) {
	let sample = bench_consumer_path::<P>(payloads, model);
	let samples = sample.per_event;
	if samples.len() >= 20 {
		let decile = samples.len() / 10;
		let first = samples[..decile].iter().sum::<f64>() / decile as f64;
		let last = samples[samples.len() - decile..].iter().sum::<f64>() / decile as f64;
		println!(
			"{label:<24} first decile {first:>9.0} ns/event   last decile {last:>9.0} ns/event   growth {:.1}x",
			last / first
		);
	}
}


// ---------------------------------------------------------------------------
// Consumer byte-identity (partial_message lane)
// ---------------------------------------------------------------------------

/// Runs one arm's consumer over pre-framed payloads and returns every emitted
/// event as serialized JSON with the message `timestamp` normalized (it is
/// construction time, not consumer behavior) so both arms compare equal.
async fn consumer_event_trace<P: ConsumerPath>(payloads: &[String], model: &crate::types::Model) -> Vec<String> {
	let mut consumer = P::new_consumer();
	let mut output = crate::types::AssistantMessage::new(
		model.api.clone(),
		model.provider.clone(),
		model.id.clone(),
		0,
	);
	output.usage = crate::types::Usage::zero();
	let stream = crate::utils::event_stream::create_assistant_message_event_stream();
	let producer = stream.producer_handle();
	let headers: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
	let mut finished = false;
	for payload in payloads {
		let _ = consumer.consume(payload, model, &mut output, &producer, &headers, &mut finished);
	}
	let mut trace = Vec::new();
	// The consumer never ends its stream (the provider caller owns the
	// terminal), so close it here before draining or `next()` waits forever.
	stream.end(None);
	while let Some(event) = stream.next().await {
		let mut value = serde_json::to_value(&event).expect("event serializes");
		normalize_partial_timestamps(&mut value);
		trace.push(serde_json::to_string(&value).expect("event serializes"));
	}
	trace
}

/// Scrubs the construction `timestamp` from every embedded `AssistantMessage`
/// (top-level `partial`, nested `message`/`error`) so both arms compare equal.
fn normalize_partial_timestamps(value: &mut serde_json::Value) {
	fn scrub(message: &mut serde_json::Value) {
		if let Some(object) = message.as_object_mut() {
			object.insert("timestamp".to_string(), serde_json::json!(0));
		}
	}
	match value {
		serde_json::Value::Object(object) => {
			for key in ["partial", "message", "error"] {
				if let Some(message) = object.get_mut(key) {
					scrub(message);
				}
			}
		}
		_ => {}
	}
}

#[tokio::test]
async fn consumer_matches_reference_on_text_fixtures() {
	let model = model();
	for sizes in [vec![100; 24], vec![1024; 12]] {
		let body = text_fixture(&sizes);
		let payloads = fixture_payloads(&body);
		let old = consumer_event_trace::<OldConsumer>(&payloads, &model).await;
		let new = consumer_event_trace::<NewConsumer>(&payloads, &model).await;
		assert_eq!(old.len(), new.len(), "event count diverged (text sizes {sizes:?})");
		for (index, (old_event, new_event)) in old.iter().zip(new.iter()).enumerate() {
			assert_eq!(old_event, new_event, "text sizes {sizes:?}: event #{index} diverged");
		}
		assert!(!old.is_empty(), "fixtures must emit events");
	}
}

#[tokio::test]
async fn consumer_matches_reference_on_reasoning_tool_and_mixed_fixtures() {
	let model = model();
	let cases: Vec<(&str, Vec<u8>)> = vec![
		("reasoning-dense", reasoning_fixture(30, 64)),
		("tool-call-delta", tool_call_fixture(30)),
		(
			"mixed",
			{
				let mut body = reasoning_fixture(3, 16);
				body.extend_from_slice(&text_fixture(&vec![48; 5]));
				body.extend_from_slice(&tool_call_fixture(6));
				body
			},
		),
	];
	for (name, body) in cases {
		let payloads = fixture_payloads(&body);
		let old = consumer_event_trace::<OldConsumer>(&payloads, &model).await;
		let new = consumer_event_trace::<NewConsumer>(&payloads, &model).await;
		assert_eq!(old, new, "{name}: event sequence diverged");
	}
}

#[tokio::test]
async fn consumer_matches_reference_on_finish_reason_usage_and_done_tails() {
	let model = model();
	// finish_reason + usage tail (the chunk shapes after the deltas).
	let mut body = text_fixture(&vec![32; 4]);
	body.extend_from_slice(
		b"data: {\"id\":\"chatcmpl-1\",\"model\":\"repro-model\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}

",
	);
	body.extend_from_slice(
		b"data: {\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":5,\"total_tokens\":15},\"choices\":[]}

",
	);
	let payloads = fixture_payloads(&body);
	let old = consumer_event_trace::<OldConsumer>(&payloads, &model).await;
	let new = consumer_event_trace::<NewConsumer>(&payloads, &model).await;
	assert_eq!(old, new, "finish/usage tail diverged");
	assert!(old.iter().any(|event| event.contains("\"text_delta\"")), "deltas must be present");
}

/// Text-delta events for the responses consumer bench: `output_item.added` +
/// `content_part.added` + N x `output_text.delta` + item.done + completed.
fn responses_consumer_events(count: usize, delta_size: usize) -> Vec<serde_json::Value> {
	let mut events = vec![
		serde_json::json!({ "type": "response.created", "response": { "id": "resp_bench" } }),
		serde_json::json!({ "type": "response.output_item.added", "item": { "type": "message", "id": "msg_bench", "content": [] } }),
		serde_json::json!({ "type": "response.content_part.added", "part": { "type": "output_text", "text": "" } }),
	];
	let text = "x".repeat(delta_size);
	for _ in 0..count {
		events.push(serde_json::json!({ "type": "response.output_text.delta", "delta": text }));
	}
	events.push(serde_json::json!({
		"type": "response.output_item.done",
		"item": { "type": "message", "id": "msg_bench", "phase": "final_answer",
			"content": [{ "type": "output_text", "text": "" }] }
	}));
	events.push(serde_json::json!({
		"type": "response.completed",
		"response": { "id": "resp_bench", "status": "completed",
			"usage": { "input_tokens": 5, "output_tokens": 3, "total_tokens": 8 } }
	}));
	events
}

/// Reasoning-dense variant: summary part + reasoning summary text deltas.
fn responses_reasoning_events(count: usize, delta_size: usize) -> Vec<serde_json::Value> {
	let mut events = vec![
		serde_json::json!({ "type": "response.output_item.added", "item": { "type": "reasoning", "id": "rs_bench", "summary": [] } }),
		serde_json::json!({ "type": "response.reasoning_summary_part.added", "part": { "type": "summary_text", "text": "" } }),
	];
	let text = "r".repeat(delta_size);
	for _ in 0..count {
		events.push(serde_json::json!({ "type": "response.reasoning_summary_text.delta", "delta": text }));
	}
	events.push(serde_json::json!({
		"type": "response.output_item.done",
		"item": { "type": "reasoning", "id": "rs_bench", "summary": [] }
	}));
	events.push(serde_json::json!({ "type": "response.completed", "response": { "status": "completed" } }));
	events
}

/// Tool-call variant: function_call item + argument fragments.
fn responses_tool_events(count: usize) -> Vec<serde_json::Value> {
	let mut events = vec![serde_json::json!({
		"type": "response.output_item.added",
		"item": { "type": "function_call", "id": "fc_bench", "call_id": "call_bench", "name": "run", "arguments": "" }
	})];
	for i in 0..count {
		let fragment = format!("{{\"field_{i}\":\"value {i}\",\"pad\":\"{}\"}}", "p".repeat(24));
		events.push(serde_json::json!({
			"type": "response.function_call_arguments.delta",
			"delta": fragment
		}));
	}
	events.push(serde_json::json!({
		"type": "response.output_item.done",
		"item": { "type": "function_call", "id": "fc_bench", "call_id": "call_bench", "name": "run", "arguments": "{}" }
	}));
	events.push(serde_json::json!({ "type": "response.completed", "response": { "status": "completed" } }));
	events
}

fn responses_consumer_pass(
	events: &[serde_json::Value],
	model: &crate::types::Model,
) -> (f64, usize, Vec<f64>) {
	use super::openai_responses_shared::process_responses_stream;
	use futures::stream::Stream;
	use futures::task::{Context, Poll};

	struct StampingStream {
		events: std::collections::VecDeque<serde_json::Value>,
		stamps: std::sync::Arc<std::sync::Mutex<Vec<Instant>>>,
	}

	impl Stream for StampingStream {
		type Item = serde_json::Value;

		fn poll_next(mut self: std::pin::Pin<&mut Self>, _context: &mut Context<'_>) -> Poll<Option<Self::Item>> {
			let this = self.as_mut().get_mut();
			match this.events.pop_front() {
				Some(event) => {
					this.stamps.lock().unwrap().push(Instant::now());
					Poll::Ready(Some(event))
				}
				None => Poll::Ready(None),
			}
		}
	}

	let stream = crate::utils::event_stream::create_assistant_message_event_stream();
	let producer = stream.producer_handle();
	let mut output = crate::types::AssistantMessage::new(
		model.api.clone(),
		model.provider.clone(),
		model.id.clone(),
		0,
	);
	output.usage = crate::types::Usage::zero();
	let stamps = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
	let stamping = StampingStream { events: events.iter().cloned().collect(), stamps: stamps.clone() };
	let start = Instant::now();
	let result = futures::executor::block_on(process_responses_stream(
		Box::pin(stamping),
		&mut output,
		&producer,
		model,
		None,
	));
	let total_ns = start.elapsed().as_nanos() as f64;
	let _ = result;
	let stamps = stamps.lock().unwrap().clone();
	let count = stamps.len();
	let mut per_event = Vec::with_capacity(count.saturating_sub(1));
	for pair in stamps.windows(2) {
		per_event.push(pair[1].duration_since(pair[0]).as_nanos() as f64);
	}
	(total_ns, count, per_event)
}


#[test]
#[ignore = "micro-benchmark; run with --ignored --nocapture"]
fn responses_consumer_bench() {
	let total_start = Instant::now();
	let model = crate::types::Model {
		id: "repro-model".to_string(),
		name: "Repro Model".to_string(),
		api: "openai-responses".to_string(),
		provider: "repro-provider".to_string(),
		base_url: "http://127.0.0.1:1".to_string(),
		reasoning: true,
		input: vec![crate::types::InputModality::Text],
		cost: crate::types::ModelCost::zero(),
		context_window: 128_000.0,
		max_tokens: 4096.0,
		..Default::default()
	};

	println!("=== responses consumer hot path (process_responses_stream, events stay resident) ===");
	let fixtures: Vec<(&str, Vec<serde_json::Value>)> = vec![
		("text 100B x200", responses_consumer_events(200, 100)),
		("text 1KB x200", responses_consumer_events(200, 1024)),
		("text 4KB x200", responses_consumer_events(200, 4096)),
		("reasoning-dense 1KB x200", responses_reasoning_events(200, 1024)),
		("tool-call-delta x200", responses_tool_events(200)),
	];
	let mut samples_by_fixture: Vec<(&str, Vec<f64>)> = Vec::new();
	for (name, events) in &fixtures {
		let (total_ns, count, per_event) = responses_consumer_pass(events, &model);
		let mean = total_ns / count as f64;
		let (p50, p95, p99) = percentiles(per_event.clone());
		println!(
			"{name:<44} {count:>5} ev  mean {mean:>9.0} ns/ev  p50 {p50:>9.0} p95 {p95:>9.0} p99 {p99:>9.0}"
		);
		samples_by_fixture.push((name, per_event));
	}

	println!("\n=== responses consumer decile growth probe (per-event ns by decile) ===");
	for (name, samples) in &samples_by_fixture {
		if samples.len() >= 20 {
			let decile = samples.len() / 10;
			let first = samples[..decile].iter().sum::<f64>() / decile as f64;
			let last = samples[samples.len() - decile..].iter().sum::<f64>() / decile as f64;
			println!("{name:<44} first decile {first:>9.0} ns/event   last decile {last:>9.0} ns/event   growth {:.1}x", last / first);
		}
	}
	println!("\nTotal bench time: {:.1}s", total_start.elapsed().as_secs_f32());
}


#[tokio::test]
async fn responses_consumer_bench_fixtures_stream_expected_event_kinds() {
	use super::openai_responses_shared::process_responses_stream;

	let model = crate::types::Model {
		id: "repro-model".to_string(),
		name: "Repro Model".to_string(),
		api: "openai-responses".to_string(),
		provider: "repro-provider".to_string(),
		base_url: "http://127.0.0.1:1".to_string(),
		reasoning: true,
		input: vec![crate::types::InputModality::Text],
		cost: crate::types::ModelCost::zero(),
		context_window: 128_000.0,
		max_tokens: 4096.0,
		..Default::default()
	};
	let stream = crate::utils::event_stream::create_assistant_message_event_stream();
	let producer = stream.producer_handle();
	let mut output = crate::types::AssistantMessage::new(
		model.api.clone(),
		model.provider.clone(),
		model.id.clone(),
		0,
	);
	output.usage = crate::types::Usage::zero();
	let events = responses_consumer_events(8, 32);
	process_responses_stream(
		Box::pin(futures::stream::iter(events)),
		&mut output,
		&producer,
		&model,
		None,
	)
	.await
	.unwrap();
	let mut kinds = Vec::new();
	stream.end(None);
	while let Some(event) = stream.next().await {
		kinds.push(crate::types::AssistantMessageEvent::event_type(&event).to_string());
	}
	assert_eq!(
		kinds,
		vec![
			"text_start".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"text_delta".to_string(),
			"text_end".to_string(),
		]
	);
}
