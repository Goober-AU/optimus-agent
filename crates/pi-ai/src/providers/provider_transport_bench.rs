
//! Provider transport benchmark harness against local mock HTTP endpoints.
//!
//! Measures PATH A (Optimus -> provider HTTP) without real providers: a
//! configurable loopback HTTP/1.1 fixture server built on `tokio::TcpListener`
//! drives the REAL production entry point `stream_openai_completions`
//! (request building, headers, SSE parsing, event delivery) where reachable,
//! and the shared/pooled/fresh `reqwest` clients directly for transport-only
//! comparisons. All timestamps come from one process clock; nothing is
//! subtracted across processes.
//!
//! Owned by the network/IPC performance campaign (worker provider-bench).
//! The heavy measurements are `#[ignore]` so the default test suite stays fast;
//! run `cargo test -p pi-ai --release provider_transport_bench -- --ignored
//! --nocapture` for the full scorecard, and see
//! research/network_latency/provider/provider-baseline.md for results.

use std::net::Ipv4Addr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::providers::openai_completions::{stream_openai_completions, OpenAICompletionsOptions};
use crate::providers::shared_http::{shared_client, ClientPolicy};
use crate::types::{
    AssistantMessageEvent, Context, InputModality, Message, Model, ModelCost,
    OnStreamObservation, StreamOptions, UserContent, UserMessage,
};

/// Nanosecond clock. All measurements in this harness (client, server fixture,
/// observers) use this single process clock. `SystemTime` is used so absolute
/// timestamps can be cross-referenced between runs; only differences within one
/// process are ever subtracted.
fn now_ns() -> u64 {
    use std::time::SystemTime;
    SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("system clock before UNIX_EPOCH")
        .as_nanos() as u64
}

// ---------------------------------------------------------------------------
// Fixture server
// ---------------------------------------------------------------------------

/// One scripted response shape the fixture server can serve.
#[derive(Clone, Debug)]
pub(crate) enum Behavior {
    /// Immediate complete response (JSON body).
    Immediate,
    /// Hold the status line/headers for `delay_ms` before writing them.
    DelayedHeaders { delay_ms: u64 },
    /// Write headers at once, then hold the first body byte for `delay_ms`.
    DelayedFirstByte { delay_ms: u64 },
    /// SSE stream: `chunks` events of `chunk_bytes` payload, `interval_ms`
    /// between events, plus a finish event and `[DONE]`.
    Sse { chunks: usize, interval_ms: u64, chunk_bytes: usize },
    /// Single large body of `body_bytes` (not SSE).
    Large { body_bytes: usize },
    /// Serve half the SSE stream, then drop the TCP connection.
    DisconnectMidStream { chunks: usize, interval_ms: u64, chunk_bytes: usize },
    /// HTTP 429 with a `Retry-After` header.
    Http429 { retry_after_s: u64 },
    /// HTTP 500.
    Http500,
}

impl Behavior {
    fn path(&self) -> &'static str {
        match self {
            Behavior::Immediate => "/immediate",
            Behavior::DelayedHeaders { .. } => "/delayed-headers",
            Behavior::DelayedFirstByte { .. } => "/delayed-first-byte",
            Behavior::Sse { .. } => "/sse",
            Behavior::Large { .. } => "/large",
            Behavior::DisconnectMidStream { .. } => "/disconnect",
            Behavior::Http429 { .. } => "/429",
            Behavior::Http500 => "/500",
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct RequestTiming {
    pub seq: u64,
    pub path: String,
    pub connected_ns: u64,
    pub request_read_ns: u64,
    pub headers_written_ns: u64,
    pub first_body_byte_ns: Option<u64>,
    pub complete_ns: Option<u64>,
    pub disconnected_early: bool,
}

#[derive(Default)]
struct ServerShared {
    connections_accepted: AtomicU64,
    requests_served: AtomicU64,
    requests_disconnected: AtomicU64,
    timings: Mutex<Vec<RequestTiming>>,
}

/// The fixture server. `start` binds loopback and returns a handle; behavior
/// is chosen per request from the URL path, so one server can serve a whole
/// benchmark sweep. Each connection is keep-alive HTTP/1.1 unless the behavior
/// says otherwise.
pub(crate) struct MockServer {
    pub base_url: String,
    shared: Arc<ServerShared>,
    task: tokio::task::JoinHandle<()>,
}

pub(crate) struct ServerSnapshot {
    pub connections_accepted: u64,
    pub requests_served: u64,
    pub requests_disconnected: u64,
}

impl MockServer {
    pub fn connections_accepted(&self) -> u64 {
        self.shared.connections_accepted.load(Ordering::SeqCst)
    }

    pub fn requests_served(&self) -> u64 {
        self.shared.requests_served.load(Ordering::SeqCst)
    }

    pub fn snapshot(&self) -> ServerSnapshot {
        ServerSnapshot {
            connections_accepted: self.shared.connections_accepted.load(Ordering::SeqCst),
            requests_served: self.shared.requests_served.load(Ordering::SeqCst),
            requests_disconnected: self.shared.requests_disconnected.load(Ordering::SeqCst),
        }
    }

    pub fn timings(&self) -> Vec<RequestTiming> {
        self.shared.timings.lock().unwrap().clone()
    }

    pub async fn start(behaviors: Vec<Behavior>) -> Self {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let shared = Arc::new(ServerShared::default());
        let state = shared.clone();
        let task = tokio::spawn(async move {
            let mut children = tokio::task::JoinSet::new();
            loop {
                let Ok((socket, _)) = listener.accept().await else { break };
                let state = state.clone();
                let behaviors = behaviors.clone();
                children.spawn(async move {
                    serve_connection(socket, behaviors, state).await;
                });
            }
        });
        Self { base_url: format!("http://{address}"), shared, task }
    }

    pub fn abort(&self) {
        self.task.abort();
    }
}

struct HttpRequest {
    path: String,
    content_length: usize,
}

/// Read one full HTTP/1.1 request (headers + content-length body) from the
/// socket. Returns None when the peer closed before a complete request.
async fn read_request(socket: &mut TcpStream) -> std::io::Result<Option<HttpRequest>> {
    let mut buffer: Vec<u8> = Vec::with_capacity(4096);
    let mut chunk = [0u8; 8192];
    let header_end = loop {
        if let Some(end) = buffer.windows(4).position(|window| window == b"\r\n\r\n") {
            break end;
        }
        let count = socket.read(&mut chunk).await?;
        if count == 0 {
            return Ok(None);
        }
        buffer.extend_from_slice(&chunk[..count]);
    };
    let head = String::from_utf8_lossy(&buffer[..header_end]).to_string();
    let mut lines = head.lines();
    let request_line = lines.next().unwrap_or_default();
    let path = request_line.split_whitespace().nth(1).unwrap_or_default().to_string();
    let mut content_length = 0usize;
    for line in lines {
        if let Some((name, value)) = line.split_once(':') {
            if name.trim().eq_ignore_ascii_case("content-length") {
                content_length = value.trim().parse().unwrap_or(0);
            }
        }
    }
    while buffer.len() < header_end + 4 + content_length {
        let count = socket.read(&mut chunk).await?;
        if count == 0 {
            return Ok(None);
        }
        buffer.extend_from_slice(&chunk[..count]);
    }
    Ok(Some(HttpRequest { path, content_length }))
}

fn sse_payload(index: usize, chunk_bytes: usize) -> String {
    let token = deterministic_token(index, chunk_bytes);
    let payload = json!({
        "id": "transport-bench",
        "object": "chat.completion.chunk",
        "created": 1772000000i64,
        "model": "bench-model",
        "choices": [{
            "index": 0,
            "delta": {"content": token},
            "finish_reason": null,
        }],
    });
    format!("data: {payload}\n\n")
}

fn sse_finish() -> String {
    let finish = json!({
        "id": "transport-bench",
        "object": "chat.completion.chunk",
        "created": 1772000000i64,
        "model": "bench-model",
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
    });
    format!("data: {finish}\n\n")
}

/// Deterministic filler token (same scheme as the standalone example bench).
fn deterministic_token(index: usize, bytes: usize) -> String {
    const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789";
    let mut out = String::with_capacity(bytes + 8);
    out.push_str(&format!("t{index:05x}"));
    let mut fill = index.wrapping_mul(7).wrapping_add(13);
    while out.len() < bytes {
        out.push(ALPHABET[fill % ALPHABET.len()] as char);
        fill = fill.wrapping_mul(31).wrapping_add(17);
    }
    out.truncate(bytes);
    out
}

/// Serve one scripted behavior on an accepted socket (loop for keep-alive).
async fn serve_connection(mut socket: TcpStream, behaviors: Vec<Behavior>, state: Arc<ServerShared>) {
    let _ = socket.set_nodelay(true);
    let connected_ns = now_ns();
    state.connections_accepted.fetch_add(1, Ordering::SeqCst);
    loop {
        let request = match tokio::time::timeout(Duration::from_secs(30), read_request(&mut socket)).await {
            Ok(Ok(Some(request))) => request,
            _ => return,
        };
        // The real client appends `/chat/completions` to the model base_url, so a
        // behavior path of `/sse` arrives as `/sse/chat/completions`. Strip the
        // suffix for matching; direct transport probes use the bare path.
        let routed = request
            .path
            .strip_suffix("/chat/completions")
            .unwrap_or(&request.path)
            .to_string();
        let behavior = behaviors
            .iter()
            .find(|behavior| behavior.path() == routed)
            .cloned()
            .unwrap_or(Behavior::Immediate);
        let seq = state.requests_served.fetch_add(1, Ordering::SeqCst) + 1;
        let request_read_ns = now_ns();
        let timing = respond(&mut socket, &behavior, seq, connected_ns, request_read_ns).await;
        state.timings.lock().unwrap().push(timing.clone());
        match timing.disconnected_early {
            true => {
                state.requests_disconnected.fetch_add(1, Ordering::SeqCst);
                return;
            }
            false => continue,
        }
    }
}

/// Write the scripted response; returns the per-request timing record.
async fn respond(
    socket: &mut TcpStream,
    behavior: &Behavior,
    seq: u64,
    connected_ns: u64,
    request_read_ns: u64,
) -> RequestTiming {
    let mut timing = RequestTiming {
        seq,
        path: behavior.path().to_string(),
        connected_ns,
        request_read_ns,
        headers_written_ns: 0,
        first_body_byte_ns: None,
        complete_ns: None,
        disconnected_early: false,
    };
    match behavior {
        Behavior::Immediate => {
            let body = b"{\"ok\":true}";
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
                body.len()
            );
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            let _ = socket.write_all(body).await;
            let _ = socket.flush().await;
            timing.first_body_byte_ns = Some(now_ns());
            timing.complete_ns = Some(now_ns());
        }
        Behavior::DelayedHeaders { delay_ms } => {
            tokio::time::sleep(Duration::from_millis(*delay_ms)).await;
            let event = sse_payload(0, 32);
            let finish = sse_finish();
            let body = format!("{event}{finish}data: [DONE]\n\n");
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\n\r\n",
                body.len()
            );
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            let _ = socket.write_all(body.as_bytes()).await;
            let _ = socket.flush().await;
            timing.first_body_byte_ns = Some(now_ns());
            timing.complete_ns = Some(now_ns());
        }
        Behavior::DelayedFirstByte { delay_ms } => {
            let event = sse_payload(0, 32);
            let finish = sse_finish();
            let body = format!("{event}{finish}data: [DONE]\n\n");
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\n\r\n",
                body.len()
            );
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            tokio::time::sleep(Duration::from_millis(*delay_ms)).await;
            let _ = socket.write_all(body.as_bytes()).await;
            let _ = socket.flush().await;
            timing.first_body_byte_ns = Some(now_ns());
            timing.complete_ns = Some(now_ns());
        }
        Behavior::Sse { chunks, interval_ms, chunk_bytes } => {
            let body_len = sse_body_len(*chunks, *chunk_bytes);
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {body_len}\r\n\r\n"
            );
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            for index in 0..*chunks {
                if index > 0 {
                    tokio::time::sleep(Duration::from_millis(*interval_ms)).await;
                }
                let event = sse_payload(index, *chunk_bytes);
                let _ = socket.write_all(event.as_bytes()).await;
                let _ = socket.flush().await;
                if index == 0 {
                    timing.first_body_byte_ns = Some(now_ns());
                }
            }
            let finish = sse_finish();
            let _ = socket.write_all(finish.as_bytes()).await;
            let _ = socket.write_all(b"data: [DONE]\n\n").await;
            let _ = socket.flush().await;
            timing.complete_ns = Some(now_ns());
        }
        Behavior::Large { body_bytes } => {
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {body_bytes}\r\n\r\n"
            );
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            let block: Vec<u8> = deterministic_token(0, 4096).into_bytes();
            let mut written = 0usize;
            while written < *body_bytes {
                let take = (*body_bytes - written).min(block.len());
                let _ = socket.write_all(&block[..take]).await;
                if written == 0 {
                    timing.first_body_byte_ns = Some(now_ns());
                }
                written += take;
            }
            let _ = socket.flush().await;
            timing.complete_ns = Some(now_ns());
        }
        Behavior::DisconnectMidStream { chunks, interval_ms, chunk_bytes } => {
            let head = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n";
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            for index in 0..*chunks {
                if index > 0 {
                    tokio::time::sleep(Duration::from_millis(*interval_ms)).await;
                }
                let event = sse_payload(index, *chunk_bytes);
                let _ = socket.write_all(event.as_bytes()).await;
                let _ = socket.flush().await;
                if index == 0 {
                    timing.first_body_byte_ns = Some(now_ns());
                }
            }
            // Drop the TCP connection mid-stream: no finish, no [DONE].
            timing.disconnected_early = true;
            let _ = socket.shutdown().await;
        }
        Behavior::Http429 { retry_after_s } => {
            let body = b"{\"error\":{\"message\":\"rate limited\",\"type\":\"rate_limit_error\"}}";
            let head = format!(
                "HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\nRetry-After: {retry_after_s}\r\nContent-Length: {}\r\n\r\n",
                body.len()
            );
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            let _ = socket.write_all(body).await;
            let _ = socket.flush().await;
            timing.first_body_byte_ns = Some(now_ns());
            timing.complete_ns = Some(now_ns());
        }
        Behavior::Http500 => {
            let body = b"{\"error\":{\"message\":\"server error\",\"type\":\"server_error\"}}";
            let head = format!(
                "HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
                body.len()
            );
            let _ = socket.write_all(head.as_bytes()).await;
            timing.headers_written_ns = now_ns();
            let _ = socket.write_all(body).await;
            let _ = socket.flush().await;
            timing.first_body_byte_ns = Some(now_ns());
            timing.complete_ns = Some(now_ns());
        }
    }
    timing
}

fn sse_body_len(chunks: usize, chunk_bytes: usize) -> usize {
    let mut total = 0usize;
    for index in 0..chunks {
        total += sse_payload(index, chunk_bytes).len();
    }
    total + sse_finish().len() + b"data: [DONE]\n\n".len()
}

// ---------------------------------------------------------------------------
// Shared measurement helpers
// ---------------------------------------------------------------------------

fn percentile(sorted: &[f64], fraction: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let index = ((sorted.len() as f64 - 1.0) * fraction).round() as usize;
    sorted[index.min(sorted.len() - 1)]
}

pub(crate) fn stats_ms(samples_ns: &[u64]) -> Value {
    let mut samples: Vec<f64> = samples_ns.iter().map(|ns| *ns as f64 / 1_000_000.0).collect();
    samples.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if samples.is_empty() {
        return json!({"samples": 0});
    }
    let mean = samples.iter().sum::<f64>() / samples.len() as f64;
    json!({
        "samples": samples.len(),
        "p50_ms": percentile(&samples, 0.50),
        "p95_ms": percentile(&samples, 0.95),
        "p99_ms": percentile(&samples, 0.99),
        "mean_ms": mean,
        "min_ms": samples[0],
        "max_ms": samples[samples.len() - 1],
    })
}

fn bench_model(base_url: &str) -> Model {
    Model {
        id: "bench-model".to_string(),
        name: "Bench Fixture Model".to_string(),
        api: "openai-completions".to_string(),
        provider: "bench-fixture".to_string(),
        base_url: base_url.to_string(),
        reasoning: false,
        input: vec![InputModality::Text],
        cost: ModelCost::zero(),
        context_window: 128_000.0,
        max_tokens: 4096.0,
        ..Default::default()
    }
}

fn bench_context(prompt_bytes: usize) -> Context {
    let prompt = deterministic_token(3, prompt_bytes);
    Context::new(
        None,
        vec![Message::user(UserMessage::new(UserContent::Text(prompt), 1))],
        None,
    )
}

/// Phase-recorded observer: local transport phases + first network-read event.
#[derive(Clone, Default)]
struct PhaseLog {
    entries: Arc<Mutex<Vec<(u64, &'static str)>>>,
}

impl PhaseLog {
    fn new() -> Self {
        Self { entries: Arc::new(Mutex::new(Vec::with_capacity(64))) }
    }

    fn observer(&self) -> OnStreamObservation {
        let entries = self.entries.clone();
        Arc::new(move |phase: &str| {
            let known: &[&str] = &[
                "generation_serialize_start",
                "generation_serialize_complete",
                "generation_serialize_returned",
                "generation_send_start",
                "generation_headers_complete",
                "raw_event",
                "text",
                "terminal",
            ];
            if let Some(static_phase) = known.iter().find(|candidate| **candidate == phase) {
                entries.lock().unwrap().push((now_ns(), static_phase));
            }
        })
    }

    fn first(&self, phase: &str) -> Option<u64> {
        self.entries.lock().unwrap().iter().find(|(_, name)| *name == phase).map(|(ts, _)| *ts)
    }

    fn count(&self, phase: &str) -> usize {
        self.entries.lock().unwrap().iter().filter(|(_, name)| *name == phase).count()
    }
}

struct ProviderRun {
    ok: bool,
    stop_reason: String,
    error_message: Option<String>,
    text_delta_ns: Vec<u64>,
    done_ns: Option<u64>,
    content_len: usize,
}

/// One full pass through the REAL `stream_openai_completions` path against the
/// fixture server, with phase observation. Returns client-side timestamps.
async fn run_provider_once(
    base_url: &str,
    path: &str,
    prompt_bytes: usize,
) -> (u64, PhaseLog, ProviderRun) {
    let phases = PhaseLog::new();
    let model = {
        let mut model = bench_model(base_url);
        model.base_url = format!("{base_url}{path}");
        model
    };
    let context = bench_context(prompt_bytes);
    let options = OpenAICompletionsOptions {
        stream: StreamOptions {
            api_key: Some("local-fixture-key".to_string()),
            on_stream_observation: Some(phases.observer()),
            ..Default::default()
        },
        ..Default::default()
    };
    let start_ns = now_ns();
    let stream = stream_openai_completions(&model, &context, Some(options));
    let mut text_delta_ns = Vec::new();
    let mut done_ns: Option<u64> = None;
    let mut stop_reason = String::new();
    let mut error_message = None;
    let mut content_len = 0usize;
    let consumed = tokio::time::timeout(Duration::from_secs(60), async {
        while let Some(event) = stream.next().await {
            match event {
                AssistantMessageEvent::TextDelta { delta, .. } => {
                    text_delta_ns.push(now_ns());
                    content_len += delta.len();
                }
                AssistantMessageEvent::Done { message, .. } => {
                    done_ns = Some(now_ns());
                    stop_reason = message.stop_reason.clone();
                    break;
                }
                AssistantMessageEvent::Error { error, .. } => {
                    done_ns = Some(now_ns());
                    stop_reason = error.stop_reason.clone();
                    error_message = error.error_message.clone();
                    break;
                }
                _ => {}
            }
        }
    })
    .await;
    let ok = consumed.is_ok() && done_ns.is_some() && stop_reason == "stop";
    (
        start_ns,
        phases,
        ProviderRun { ok, stop_reason, error_message, text_delta_ns, done_ns, content_len },
    )
}

// ---------------------------------------------------------------------------
// (a) Connection-reuse proof + (b) client acquisition cost
// ---------------------------------------------------------------------------

/// (a) Sequential requests through the process-wide shared client must be one
/// accepted TCP connection; a fresh client per request must accept per request.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn provider_transport_bench_connection_reuse_proof() {
    let server = MockServer::start(vec![Behavior::Immediate]).await;
    let url = format!("{}/immediate", server.base_url);
    let requests = 20u64;

    // Shared (pooled) client: one accept for the whole sequence.
    let shared = shared_client(ClientPolicy::Default);
    for _ in 0..requests {
        let response = shared.get(&url).timeout(Duration::from_secs(5)).send().await.unwrap();
        assert_eq!(response.status(), 200);
        assert_eq!(response.text().await.unwrap(), "{\"ok\":true}");
    }
    assert_eq!(
        server.connections_accepted(), 1,
        "shared client must reuse one pooled connection"
    );
    assert_eq!(server.requests_served(), requests);

    // Fresh client per request: one accept per request.
    for _ in 0..requests {
        let fresh = reqwest::Client::builder().pool_idle_timeout(Duration::from_secs(90)).build().unwrap();
        let response = fresh.get(&url).timeout(Duration::from_secs(5)).send().await.unwrap();
        assert_eq!(response.status(), 200);
        assert_eq!(response.text().await.unwrap(), "{\"ok\":true}");
    }
    assert_eq!(
        server.connections_accepted(),
        1 + requests,
        "fresh client per request must reconnect every time"
    );
    server.abort();
}

/// (b) Client acquisition cost: fresh `reqwest::Client` build vs pooled hit.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn provider_transport_bench_client_acquisition_cost() {
    let iterations = 1000u64;
    // Pool hit: shared_client lookup after the first build.
    let _ = shared_client(ClientPolicy::Default);
    let mut pool_ns = Vec::with_capacity(iterations as usize);
    for _ in 0..iterations {
        let start = Instant::now();
        let _ = shared_client(ClientPolicy::Default);
        pool_ns.push(start.elapsed().as_nanos() as u64);
    }
    // Fresh build: what the Generation policy pays per guarded request.
    let mut fresh_ns = Vec::with_capacity(iterations as usize);
    for _ in 0..iterations {
        let start = Instant::now();
        let client = reqwest::Client::builder()
            .pool_idle_timeout(Duration::from_secs(90))
            .build()
            .unwrap();
        fresh_ns.push(start.elapsed().as_nanos() as u64);
        drop(client);
    }
    println!("pool_hit_acquisition: {}", stats_ms(&pool_ns));
    println!("fresh_build_acquisition: {}", stats_ms(&fresh_ns));
    // The pooled lookup must not be slower than a full client build on average.
    let pool_mean = pool_ns.iter().sum::<u64>() as f64 / pool_ns.len() as f64;
    let fresh_mean = fresh_ns.iter().sum::<u64>() as f64 / fresh_ns.len() as f64;
    println!("pool_mean_us={:.3} fresh_mean_us={:.3}", pool_mean / 1000.0, fresh_mean / 1000.0);
    assert!(pool_mean <= fresh_mean, "pooled lookup must not cost more than a fresh build");
}

// ---------------------------------------------------------------------------
// (c) TTFT through the real provider path
// ---------------------------------------------------------------------------

/// TTFT: request start -> response headers -> first SSE chunk -> first parsed
/// consumer event, through `stream_openai_completions` with the observer seam.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn provider_transport_bench_ttft() {
    let chunks = 50usize;
    let interval_ms = 20u64;
    let server = MockServer::start(vec![
        Behavior::Sse { chunks, interval_ms, chunk_bytes: 256 },
        Behavior::DelayedHeaders { delay_ms: 120 },
        Behavior::DelayedFirstByte { delay_ms: 120 },
    ])
    .await;
    let runs = 40usize;
    let mut ttft_ns: Vec<u64> = Vec::with_capacity(runs);
    let mut header_ns: Vec<u64> = Vec::with_capacity(runs);
    let mut first_event_ns: Vec<u64> = Vec::with_capacity(runs);
    let mut total_ns: Vec<u64> = Vec::with_capacity(runs);
    for _ in 0..runs {
        let (start, phases, run) = run_provider_once(&server.base_url, "/sse", 256).await;
        assert!(run.ok, "provider run failed: {:?}", run.error_message);
        let headers = phases.first("generation_headers_complete").expect("headers phase");
        let first_chunk = run.text_delta_ns.first().copied().expect("first text delta");
        let done = run.done_ns.expect("done timestamp");
        header_ns.push(headers - start);
        ttft_ns.push(first_chunk - start);
        first_event_ns.push(first_chunk.saturating_sub(headers));
        total_ns.push(done - start);
    }
    println!("ttft_headers_ms: {}", stats_ms(&header_ns));
    println!("ttft_first_chunk_ms: {}", stats_ms(&ttft_ns));
    println!("ttft_headers_to_first_event_ms: {}", stats_ms(&first_event_ns));
    println!("ttft_total_ms: {}", stats_ms(&total_ns));

    // Delayed-headers vs delayed-first-byte distinguish header vs body stalls.
    let mut delayed_header_ttft: Vec<u64> = Vec::with_capacity(10);
    for _ in 0..10 {
        let (start, phases, run) = run_provider_once(&server.base_url, "/delayed-headers", 256).await;
        assert!(run.ok, "delayed-headers run failed: {:?}", run.error_message);
        let headers = phases.first("generation_headers_complete").expect("headers phase");
        delayed_header_ttft.push(headers - start);
    }
    let mut delayed_body_ttft: Vec<u64> = Vec::with_capacity(10);
    for _ in 0..10 {
        let (_start, phases, run) =
            run_provider_once(&server.base_url, "/delayed-first-byte", 256).await;
        assert!(run.ok, "delayed-first-byte run failed: {:?}", run.error_message);
        let headers = phases.first("generation_headers_complete").expect("headers phase");
        let first = run.text_delta_ns.first().copied().expect("first text delta");
        delayed_body_ttft.push(first - headers);
    }
    println!("delayed_headers_observed_ms: {}", stats_ms(&delayed_header_ttft));
    println!("delayed_first_byte_observed_ms: {}", stats_ms(&delayed_body_ttft));
    server.abort();
}

// ---------------------------------------------------------------------------
// (d) Streaming throughput at 1KB and 4KB chunks
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn provider_transport_bench_stream_throughput() {
    let runs = 10usize;
    for chunk_bytes in [1024usize, 4096usize] {
        let chunks = 200usize;
        let server = MockServer::start(vec![Behavior::Sse { chunks, interval_ms: 0, chunk_bytes }])
            .await;
        let mut total_ns: Vec<u64> = Vec::with_capacity(runs);
        let mut bytes_per_s: Vec<f64> = Vec::with_capacity(runs);
        let mut per_event_ns: Vec<u64> = Vec::with_capacity(runs * chunks);
        for _ in 0..runs {
            let (start, _phases, run) = run_provider_once(&server.base_url, "/sse", 256).await;
            assert!(run.ok, "run failed: {:?}", run.error_message);
            let done = run.done_ns.expect("done");
            total_ns.push(done - start);
            bytes_per_s.push(run.content_len as f64 * 1_000_000_000.0 / (done - start) as f64);
            if run.text_delta_ns.len() >= 2 {
                per_event_ns.push(run.text_delta_ns[1] - run.text_delta_ns[0]);
            }
        }
        println!(
            "throughput_chunk_{}b_total_ms: {}",
            chunk_bytes,
            stats_ms(&total_ns)
        );
        println!(
            "throughput_chunk_{}b_first_inter_event_ms: {}",
            chunk_bytes,
            stats_ms(&per_event_ns)
        );
        let mean_bps = bytes_per_s.iter().sum::<f64>() / bytes_per_s.len() as f64;
        let payload_total = (chunks * chunk_bytes) as f64;
        let mean_total = total_ns.iter().sum::<u64>() as f64 / total_ns.len() as f64 / 1_000_000.0;
        println!(
            "throughput_chunk_{}b: mean_bps={:.0} payload_bytes_total={:.0} mean_total_ms={:.1}",
            chunk_bytes, mean_bps, payload_total, mean_total
        );
        server.abort();
    }
}

// ---------------------------------------------------------------------------
// (e) Concurrency sweep 1/2/4/8/16/32
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
async fn provider_transport_bench_concurrency() {
    let server = MockServer::start(vec![Behavior::Sse { chunks: 20, interval_ms: 5, chunk_bytes: 256 }])
        .await;
    let requests_per_level = 32usize;
    for level in [1usize, 2, 4, 8, 16, 32] {
        let before_connections = server.connections_accepted();
        // A semaphore keeps at most `level` requests in flight, so level 1 is
        // strictly sequential and higher levels are bounded parallelism.
        let gate = Arc::new(tokio::sync::Semaphore::new(level));
        let mut handles = Vec::with_capacity(requests_per_level);
        for _ in 0..requests_per_level {
            let base = server.base_url.clone();
            let permit = gate.clone();
            handles.push(tokio::spawn(async move {
                let _permit = permit.acquire_owned().await.unwrap();
                let (start, _phases, run) = run_provider_once(&base, "/sse", 256).await;
                let done = run.done_ns.unwrap_or_else(now_ns);
                (start, run, done)
            }));
        }
        let mut totals: Vec<u64> = Vec::with_capacity(requests_per_level);
        let mut ttfts: Vec<u64> = Vec::with_capacity(requests_per_level);
        let mut failures = 0usize;
        for handle in handles {
            let (start, run, done) = handle.await.unwrap();
            if !run.ok {
                failures += 1;
                continue;
            }
            let first = run.text_delta_ns.first().copied().unwrap_or(done);
            ttfts.push(first - start);
            totals.push(done - start);
        }
        let after_connections = server.connections_accepted();
        let after_requests = server.requests_served();
        println!(
            "concurrency_{}: ttft_ms={} total_ms={} failures={} connections={} requests={} new_connections={}",
            level,
            stats_ms(&ttfts),
            stats_ms(&totals),
            failures,
            after_connections,
            after_requests,
            after_connections - before_connections
        );
        assert_eq!(failures, 0, "concurrency level {level} had failures");
    }
    server.abort();
}

// ---------------------------------------------------------------------------
// (f) Idle expiry: 95s sleep across the 90s pool idle timeout
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore] // 95s wall clock
async fn provider_transport_bench_idle_expiry() {
    let server = MockServer::start(vec![Behavior::Immediate]).await;
    let url = format!("{}/immediate", server.base_url);
    let client = shared_client(ClientPolicy::Default);
    let probe = |client: reqwest::Client, url: String| async move {
        let response = client.get(&url).timeout(Duration::from_secs(5)).send().await.unwrap();
        assert_eq!(response.status(), 200);
        response.text().await.unwrap()
    };
    let _ = probe(client.clone(), url.clone()).await;
    assert_eq!(server.connections_accepted(), 1);
    tokio::time::sleep(Duration::from_secs(95)).await;
    let _ = probe(client.clone(), url.clone()).await;
    let accepted = server.connections_accepted();
    println!("idle_expiry_after_95s_connections: {accepted}");
    // Either 1 (pool kept the socket) or 2 (idle timeout closed it): both are
    // valid outcomes; the scorecard records which one this build does.
    assert!(accepted == 1 || accepted == 2, "unexpected accept count {accepted}");
    server.abort();
}

// ---------------------------------------------------------------------------
// (g) Error paths: 429/500 latency, mid-stream disconnect, cancellation
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn provider_transport_bench_error_paths() {
    let server = MockServer::start(vec![
        Behavior::Http429 { retry_after_s: 1 },
        Behavior::Http500,
        Behavior::DisconnectMidStream { chunks: 5, interval_ms: 10, chunk_bytes: 128 },
        Behavior::Sse { chunks: 20, interval_ms: 50, chunk_bytes: 128 },
    ])
    .await;
    let runs = 20usize;

    // 429: must fail fast with the API error, not hang.
    let mut error_429_ns: Vec<u64> = Vec::with_capacity(runs);
    let mut statuses_429: Vec<String> = Vec::with_capacity(runs);
    for _ in 0..runs {
        let (start, _phases, run) = run_provider_once(&server.base_url, "/429", 256).await;
        let done = run.done_ns.unwrap_or_else(now_ns);
        error_429_ns.push(done - start);
        statuses_429.push(run.stop_reason.clone());
        assert!(!run.ok, "429 must not report success");
        let message = run.error_message.unwrap_or_default();
        assert!(
            message.contains("rate") || message.contains("429") || message.contains("Rate"),
            "expected rate-limit classification, got: {message}"
        );
    }
    println!("error_429_ms: {}", stats_ms(&error_429_ns));

    // 500: same shape.
    let mut error_500_ns: Vec<u64> = Vec::with_capacity(runs);
    for _ in 0..runs {
        let (start, _phases, run) = run_provider_once(&server.base_url, "/500", 256).await;
        let done = run.done_ns.unwrap_or_else(now_ns);
        error_500_ns.push(done - start);
        assert!(!run.ok, "500 must not report success");
        let message = run.error_message.unwrap_or_default();
        assert!(
            message.contains("server") || message.contains("500") || message.contains("error"),
            "expected server-error classification, got: {message}"
        );
    }
    println!("error_500_ms: {}", stats_ms(&error_500_ns));

    // Mid-stream disconnect: stream must end with an error, not hang.
    let mut disconnect_ns: Vec<u64> = Vec::with_capacity(runs);
    let mut disconnect_partial_events = 0usize;
    for _ in 0..runs {
        let (start, _phases, run) = run_provider_once(&server.base_url, "/disconnect", 256).await;
        let done = run.done_ns.unwrap_or_else(now_ns);
        disconnect_ns.push(done - start);
        assert!(!run.ok, "mid-stream disconnect must fail the stream");
        if !run.text_delta_ns.is_empty() {
            disconnect_partial_events += 1;
        }
    }
    println!(
        "error_disconnect_ms: {} runs_with_partial_events={}/{}",
        stats_ms(&disconnect_ns),
        disconnect_partial_events,
        runs
    );
    assert_eq!(server.snapshot().requests_disconnected, runs as u64);

    // Cancellation: cancel after the first events arrive, measure abort time.
    let model = {
        let mut model = bench_model(&server.base_url);
        model.base_url = format!("{}/sse", server.base_url);
        model
    };
    let context = bench_context(256);
    let mut cancel_ns: Vec<u64> = Vec::with_capacity(runs);
    let mut cancelled_after_first: usize = 0;
    for _ in 0..runs {
        let options = OpenAICompletionsOptions {
            stream: StreamOptions {
                api_key: Some("local-fixture-key".to_string()),
                ..Default::default()
            },
            ..Default::default()
        };
        let start = now_ns();
        let stream = stream_openai_completions(&model, &context, Some(options));
        let mut saw_first = false;
        let mut first_ts: Option<u64> = None;
        let consumed = tokio::time::timeout(Duration::from_secs(30), async {
            while let Some(event) = stream.next().await {
                if let AssistantMessageEvent::TextDelta { .. } = event {
                    saw_first = true;
                    if first_ts.is_none() {
                        first_ts = Some(now_ns());
                    }
                    break;
                }
            }
        })
        .await;
        assert!(consumed.is_ok(), "first event never arrived");
        let _ = stream.request_cancel();
        let cancelled_at = now_ns();
        if saw_first {
            cancelled_after_first += 1;
        }
        cancel_ns.push(cancelled_at - start);
    }
    println!(
        "error_cancel_after_first_event_ms: {} cancelled_after_first={}/{}",
        stats_ms(&cancel_ns),
        cancelled_after_first,
        runs
    );
    assert_eq!(cancelled_after_first, runs);
    server.abort();
}

// ---------------------------------------------------------------------------
// Large body transfer through the real path
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn provider_transport_bench_large_body() {
    // 4MB JSON body (non-SSE) through a plain shared client: transport cost
    // of a bulky response on a reused connection.
    let server = MockServer::start(vec![Behavior::Large { body_bytes: 4 * 1024 * 1024 }]).await;
    let url = format!("{}/large", server.base_url);
    let client = shared_client(ClientPolicy::Default);
    let runs = 10usize;
    let mut total_ns: Vec<u64> = Vec::with_capacity(runs);
    let mut bytes_per_s: Vec<f64> = Vec::with_capacity(runs);
    for _ in 0..runs {
        let start = now_ns();
        let response = client.get(&url).timeout(Duration::from_secs(30)).send().await.unwrap();
        let body = response.bytes().await.unwrap();
        let done = now_ns();
        assert_eq!(body.len(), 4 * 1024 * 1024);
        total_ns.push(done - start);
        bytes_per_s.push(body.len() as f64 * 1_000_000_000.0 / (done - start) as f64);
    }
    assert_eq!(server.connections_accepted(), 1, "large-body runs must reuse one connection");
    println!("large_4mb_total_ms: {}", stats_ms(&total_ns));
    let mean_bps = bytes_per_s.iter().sum::<f64>() / bytes_per_s.len() as f64;
    println!("large_4mb_mean_bps: {mean_bps:.0}");
    server.abort();
}

// ---------------------------------------------------------------------------
// Warmup sanity: the fixture server itself behaves
// ---------------------------------------------------------------------------

#[tokio::test]
async fn provider_transport_bench_fixture_shapes() {
    let server = MockServer::start(vec![
        Behavior::Immediate,
        Behavior::Sse { chunks: 3, interval_ms: 1, chunk_bytes: 16 },
        Behavior::Http500,
    ])
    .await;
    // Immediate through the shared client.
    let client = shared_client(ClientPolicy::Default);
    let response = client
        .get(format!("{}/immediate", server.base_url))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(response.text().await.unwrap(), "{\"ok\":true}");

    // SSE through the real provider path.
    let (_start, phases, run) = run_provider_once(&server.base_url, "/sse", 64).await;
    assert!(run.ok, "fixture SSE run failed: {:?}", run.error_message);
    assert_eq!(run.text_delta_ns.len(), 3);
    assert!(phases.first("generation_headers_complete").is_some());
    assert!(phases.first("raw_event").is_some());
    assert_eq!(phases.count("terminal"), 1);

    // 500 through the real provider path.
    let (_start, _phases, run) = run_provider_once(&server.base_url, "/500", 64).await;
    assert!(!run.ok);
    server.abort();
}
