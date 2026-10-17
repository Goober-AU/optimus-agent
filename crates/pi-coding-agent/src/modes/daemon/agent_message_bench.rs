//! Benchmark harness for the agent-message IPC transport (fresh vs persistent).
//! Owned by the network/IPC performance campaign (worker ipc-bench).
//!
//! Drives the REAL worker->supervisor transport functions from
//! `agent_message_transport` against an echo-supervisor fixture that mirrors
//! the production server shape: one new pipe instance (or UDS accept) per
//! connection, a proactive `daemon_hello`, newline JSON requests, and one
//! response envelope per request (native_supervisor.rs:390-4046).
//!
//! Run with:
//! `cargo test -p pi-coding-agent --release agent_message_bench -- --ignored --nocapture`
//!
//! Results are appended as JSON lines under `research/network_latency/ipc/results/`.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use futures::future::BoxFuture;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

use crate::core::agent_messages::{
    AgentSessionMessageAgentSummary, AgentSessionMessageReceipt, DELIVERY_STATUS_DELIVERED,
    DELIVERY_STATUS_QUEUED,
};
use crate::modes::daemon::daemon_client::{DaemonClient, DaemonClientError};
use crate::modes::daemon::daemon_errors::deserialize_daemon_error;
use crate::modes::daemon::daemon_protocol::{
    daemon_protocol_info, DAEMON_SCHEMA_ID, DAEMON_SCHEMA_REVISION,
};
use crate::modes::daemon::daemon_supervisor::supervisor_server_capabilities;

// ---------------------------------------------------------------------------
// Connection mode: the arm under measurement.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ConnectionMode {
    /// Current behavior: `agent_message_transport` builds one fresh
    /// `DaemonClient` (connect -> hello -> request -> close) per call.
    Fresh,
    /// The persistent-connection arm implemented by `agent_message_connection`
    /// (worker ipc-persistent). Wired in via `persistent_driver()`.
    Persistent,
}

impl ConnectionMode {
    fn label(&self) -> &'static str {
        match self {
            Self::Fresh => "fresh",
            Self::Persistent => "persistent",
        }
    }
}

type SendResult = Result<AgentSessionMessageReceipt, String>;

/// One benchmarked transport operation. A driver is the only thing that
/// changes between the fresh and persistent arms, so both are measured
/// against the same fixture, timing points, and metrics.
trait ConnectionDriver: Send + Sync {
    fn send_message(
        &self,
        socket_path: String,
        from_active_session_id: String,
        target_selector: String,
        message: String,
    ) -> BoxFuture<'static, SendResult>;

    fn list_peers(
        &self,
        socket_path: String,
    ) -> BoxFuture<'static, Vec<AgentSessionMessageAgentSummary>>;
}

/// The pre-campaign baseline, replicated verbatim from the transport as it
/// stood at 6423654c1 (git show HEAD:.../agent_message_transport.rs): one fresh
/// `DaemonClient` per call — connect(1s) -> wait_for_hello(1s) -> one request
/// -> close(). Kept inline because `agent_message_transport` now delegates to
/// the persistent manager, so calling it would measure the wrong arm.
struct FreshDriver;

async fn fresh_send_message(
    socket_path: &str,
    from_active_session_id: &str,
    target_selector: &str,
    message: &str,
    shutting_down: &AtomicBool,
) -> Result<AgentSessionMessageReceipt, String> {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
    let mut last_error = format!("Unknown active session: {target_selector}");
    let client = loop {
        if shutting_down.load(Ordering::SeqCst) {
            return Err("Daemon is shutting down".to_string());
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(last_error);
        }
        let candidate = Arc::new(DaemonClient::new(socket_path));
        let connected = async {
            candidate.connect(1_000).await?;
            candidate.wait_for_hello(1_000).await?;
            Ok::<_, DaemonClientError>(())
        }
        .await;
        match connected {
            Ok(()) => break candidate,
            Err(error) => {
                last_error = error.to_string();
                candidate.close().await;
            }
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    };
    let response = client
        .request(
            json!({
                "type": "send_message",
                "fromActiveSessionId": from_active_session_id,
                "targetActiveSessionId": target_selector,
                "message": message,
                "agentOrigin": true,
            })
            .as_object()
            .expect("command object")
            .clone(),
            Some(30_000),
            Default::default(),
        )
        .await;
    client.close().await;
    let response = response.map_err(|error| error.to_string())?;
    if !response.success {
        return Err(deserialize_daemon_error(&response).to_string());
    }
    let receipt: AgentSessionMessageReceipt = serde_json::from_value(
        response.data.unwrap_or(Value::Null),
    )
    .map_err(|_| "Supervisor returned an invalid agent-message receipt".to_string())?;
    if !matches!(
        receipt.delivery_status.as_str(),
        DELIVERY_STATUS_DELIVERED | DELIVERY_STATUS_QUEUED
    ) {
        return Err("Supervisor returned an invalid agent-message receipt".to_string());
    }
    Ok(receipt)
}

async fn fresh_list_peers(
    socket_path: &str,
    worker_token: &str,
) -> Vec<AgentSessionMessageAgentSummary> {
    let client = Arc::new(DaemonClient::new(socket_path));
    let result = async {
        client.connect(1_000).await.map_err(|error| error.to_string())?;
        client.wait_for_hello(1_000).await.map_err(|error| error.to_string())?;
        let response = client
            .request(
                json!({"type": "list_agent_peers", "workerToken": worker_token})
                    .as_object()
                    .expect("command object")
                    .clone(),
                Some(5_000),
                Default::default(),
            )
            .await
            .map_err(|error| error.to_string())?;
        if !response.success {
            return Err(deserialize_daemon_error(&response).to_string());
        }
        serde_json::from_value::<Vec<AgentSessionMessageAgentSummary>>(
            response
                .data
                .and_then(|data| data.get("peers").cloned())
                .unwrap_or(Value::Null),
        )
        .map_err(|error| error.to_string())
    }
    .await;
    client.close().await;
    result.unwrap_or_default()
}

impl ConnectionDriver for FreshDriver {
    fn send_message(
        &self,
        socket_path: String,
        from_active_session_id: String,
        target_selector: String,
        message: String,
    ) -> BoxFuture<'static, SendResult> {
        Box::pin(async move {
            let shutting_down = AtomicBool::new(false);
            fresh_send_message(
                &socket_path,
                &from_active_session_id,
                &target_selector,
                &message,
                &shutting_down,
            )
            .await
        })
    }

    fn list_peers(
        &self,
        socket_path: String,
    ) -> BoxFuture<'static, Vec<AgentSessionMessageAgentSummary>> {
        Box::pin(async move { fresh_list_peers(&socket_path, "bench-worker-token").await })
    }
}

fn fresh_driver() -> Arc<dyn ConnectionDriver> {
    Arc::new(FreshDriver)
}

/// The persistent arm: the real current transport, which now delegates to the
/// process-wide `AgentMessageConnectionManager` (worker ipc-persistent).
struct PersistentDriver;

impl ConnectionDriver for PersistentDriver {
    fn send_message(
        &self,
        socket_path: String,
        from_active_session_id: String,
        target_selector: String,
        message: String,
    ) -> BoxFuture<'static, SendResult> {
        Box::pin(async move {
            let shutting_down = AtomicBool::new(false);
            super::agent_message_transport::send_message(
                &socket_path,
                &from_active_session_id,
                &target_selector,
                &message,
                &shutting_down,
            )
            .await
        })
    }

    fn list_peers(
        &self,
        socket_path: String,
    ) -> BoxFuture<'static, Vec<AgentSessionMessageAgentSummary>> {
        Box::pin(async move {
            super::agent_message_transport::list_peers(&socket_path, "bench-worker-token").await
        })
    }
}

fn persistent_driver() -> Option<Arc<dyn ConnectionDriver>> {
    Some(Arc::new(PersistentDriver))
}

// ---------------------------------------------------------------------------
// Echo-supervisor fixture.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct ServerStats {
    accepted_connections: AtomicU64,
    requests: AtomicU64,
}

/// Cooperative pause used by the daemon-restart scenario: while paused, the
/// accept loop stops offering new pipe instances, exactly like a supervisor
/// that is down between old process exit and new process bind.
struct PauseGate {
    paused: AtomicBool,
    resume: tokio::sync::Notify,
}

impl PauseGate {
    fn new() -> Self {
        Self {
            paused: AtomicBool::new(false),
            resume: tokio::sync::Notify::new(),
        }
    }

    fn pause(&self) {
        self.paused.store(true, Ordering::SeqCst);
    }

    fn resume(&self) {
        self.paused.store(false, Ordering::SeqCst);
        // notify_one (not notify_waiters) stores a permit even without a
        // waiter, so a gate that raced the store cannot sleep forever.
        self.resume.notify_one();
    }

    async fn wait_while_paused(&self) {
        while self.paused.load(Ordering::SeqCst) {
            self.resume.notified().await;
        }
    }
}

struct FixtureOptions {
    /// Artificial per-request processing delay before the response is written.
    per_request_delay: Duration,
    /// Server closes the connection right after each response (churn).
    close_after_response: bool,
    /// Peer entries returned for list_agent_peers.
    peer_count: usize,
}

impl Default for FixtureOptions {
    fn default() -> Self {
        Self {
            per_request_delay: Duration::ZERO,
            close_after_response: false,
            peer_count: 1,
        }
    }
}

/// An echo supervisor over a real Windows named pipe / Unix domain socket.
struct EchoSupervisor {
    socket_path: String,
    stats: Arc<ServerStats>,
    pause: Arc<PauseGate>,
    task: tokio::task::JoinHandle<()>,
    _root: Option<tempfile::TempDir>,
}

impl Drop for EchoSupervisor {
    fn drop(&mut self) {
        self.task.abort();
    }
}

/// Mirrors Supervisor::hello (native_supervisor.rs:458-467): the same key set
/// plus the real protocol info, schema id/revision, and capability list.
fn supervisor_hello() -> Value {
    json!({
        "type": "daemon_hello",
        "socketPath": "bench-supervisor-socket",
        "protocol": daemon_protocol_info(),
        "schemaId": DAEMON_SCHEMA_ID,
        "schemaRevision": DAEMON_SCHEMA_REVISION,
        "appVersion": "bench",
        "runtime": {"pid": std::process::id(), "platform": std::env::consts::OS},
        "supervisorGeneration": "bench-generation",
        "supervisorOwnerToken": "bench-owner-token",
        "supervisorPid": std::process::id(),
        "supervisorProcessStartId": "bench-start-id",
        "supervisorSocketPath": "bench-supervisor-socket",
        "clientId": "bench-client",
        "serverCapabilities": supervisor_server_capabilities(),
    })
}

fn respond_to_command(command: &Value) -> Value {
    let command_type = command.get("type").and_then(Value::as_str).unwrap_or_default();
    match command_type {
        "send_message" => {
            let target = command
                .get("targetActiveSessionId")
                .and_then(Value::as_str)
                .unwrap_or("bench-target-active");
            let from = command
                .get("fromActiveSessionId")
                .and_then(Value::as_str)
                .unwrap_or("bench-from-active");
            json!({
                "type": "response",
                "id": command.get("id").cloned().unwrap_or(Value::Null),
                "command": command_type,
                "success": true,
                "data": {
                    "id": format!("bench-receipt-{}", command.get("id").and_then(Value::as_str).unwrap_or("0")),
                    "source": "agent_message",
                    "message": command.get("message").cloned().unwrap_or(Value::String(String::new())),
                    "target": {"activeSessionId": target, "sessionId": "bench-target-session"},
                    "from": {"activeSessionId": from, "sessionId": "bench-from-session"},
                    "deliveryStatus": DELIVERY_STATUS_DELIVERED,
                },
            })
        }
        "list_agent_peers" => {
            let peers: Vec<Value> = (0..command.get("peerCount").and_then(Value::as_u64).unwrap_or(1))
                .map(|index| {
                    json!({
                        "activeSessionId": format!("peer-{index}-active"),
                        "sessionId": format!("peer-{index}-session"),
                        "cwd": "C:/bench/peer",
                        "isStreaming": false,
                        "unfinishedActionCount": 0,
                    })
                })
                .collect();
            json!({
                "type": "response",
                "id": command.get("id").cloned().unwrap_or(Value::Null),
                "command": command_type,
                "success": true,
                "data": {"peers": peers},
            })
        }
        _ => json!({
            "type": "response",
            "id": command.get("id").cloned().unwrap_or(Value::Null),
            "command": command_type,
            "success": false,
            "error": format!("Unknown daemon command: {command_type}"),
        }),
    }
}

async fn serve_connection<S>(socket: S, options: Arc<FixtureOptions>, stats: Arc<ServerStats>)
where
    S: AsyncRead + AsyncWrite + Unpin,
{
    let (reader, mut writer) = tokio::io::split(socket);
    let mut hello = supervisor_hello().to_string();
    hello.push('\n');
    if writer.write_all(hello.as_bytes()).await.is_err() {
        return;
    }
    let mut lines = BufReader::new(reader).lines();
    loop {
        let line = match lines.next_line().await {
            Ok(Some(line)) => line,
            _ => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let Ok(envelope) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        stats.requests.fetch_add(1, Ordering::SeqCst);
        // Public envelopes (protocol >= 7) nest the command; legacy bodies are it.
        let mut command = envelope
            .get("command")
            .cloned()
            .unwrap_or_else(|| envelope.clone());
        if command.get("peerCount").is_none() {
            command["peerCount"] = json!(options.peer_count);
        }
        if options.per_request_delay > Duration::ZERO {
            tokio::time::sleep(options.per_request_delay).await;
        }
        let mut response = respond_to_command(&command).to_string();
        response.push('\n');
        if writer.write_all(response.as_bytes()).await.is_err() {
            break;
        }
        if options.close_after_response {
            let _ = writer.shutdown().await;
            return;
        }
    }
}

impl EchoSupervisor {
    fn new(options: FixtureOptions) -> Self {
        Self::with_pause(options, Arc::new(PauseGate::new()))
    }

    fn with_pause(options: FixtureOptions, pause: Arc<PauseGate>) -> Self {
        let options = Arc::new(options);
        let stats = Arc::new(ServerStats::default());
        #[cfg(windows)]
        let (socket_path, task, root) = {
            use tokio::net::windows::named_pipe::ServerOptions;
            let socket_path = format!(r"\\.\pipe\optimus-agent-bench-{}", uuid::Uuid::new_v4());
            let server = ServerOptions::new()
                .first_pipe_instance(true)
                .create(&socket_path)
                .expect("first pipe instance");
            let accept_socket_path = socket_path.clone();
            let stats = Arc::clone(&stats);
            let pause = Arc::clone(&pause);
            let task = tokio::spawn(async move {
                let mut server = server;
                loop {
                    // Mirror the production accept loop (native_supervisor.rs:396-401):
                    // wait for one client, create the next instance, serve the
                    // current connection concurrently.
                    if server.connect().await.is_err() {
                        break;
                    }
                    stats.accepted_connections.fetch_add(1, Ordering::SeqCst);
                    let options = Arc::clone(&options);
                    let stats = Arc::clone(&stats);
                    tokio::spawn(async move {
                        serve_connection(server, options, stats).await;
                    });
                    // "Down" supervisor: offer no new instance until resumed.
                    pause.wait_while_paused().await;
                    match ServerOptions::new().create(&accept_socket_path) {
                        Ok(next) => server = next,
                        Err(_) => break,
                    }
                }
            });
            (socket_path, task, None)
        };
        #[cfg(unix)]
        let (socket_path, task, root) = {
            let root = tempfile::Builder::new()
                .prefix("optimus-agent-bench-")
                .tempdir()
                .expect("temp dir");
            let socket_path = root
                .path()
                .join("supervisor.sock")
                .to_string_lossy()
                .into_owned();
            let listener = tokio::net::UnixListener::bind(&socket_path).expect("unix listener");
            let stats = Arc::clone(&stats);
            let pause = Arc::clone(&pause);
            let task = tokio::spawn(async move {
                loop {
                    // A paused gate stops accepting; the kernel backlog still
                    // absorbs a few early connects, unlike the Windows arm.
                    pause.wait_while_paused().await;
                    let Ok((stream, _)) = listener.accept().await else {
                        break;
                    };
                    stats.accepted_connections.fetch_add(1, Ordering::SeqCst);
                    let options = Arc::clone(&options);
                    let stats = Arc::clone(&stats);
                    tokio::spawn(async move {
                        serve_connection(stream, options, stats).await;
                    });
                }
            });
            (socket_path, task, Some(root))
        };
        Self {
            socket_path,
            stats,
            pause,
            task,
            _root: root,
        }
    }
}

// ---------------------------------------------------------------------------
// Measurement.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BenchOp {
    SendMessage { size_bytes: usize },
    ListPeers { peers: usize },
}

impl BenchOp {
    fn label(&self) -> String {
        match self {
            Self::SendMessage { size_bytes } => format!("send_message/{size_bytes}B"),
            Self::ListPeers { peers } => format!("list_peers/{peers}"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Topology {
    OneToOne,
    OneToN { targets: usize },
    NToOne { senders: usize },
    NToN { endpoints: usize },
}

impl Topology {
    fn label(&self) -> &'static str {
        match self {
            Self::OneToOne => "1->1",
            Self::OneToN { targets } if *targets == 4 => "1->4",
            Self::OneToN { .. } => "1->N",
            Self::NToOne { senders } if *senders == 4 => "4->1",
            Self::NToOne { .. } => "N->1",
            Self::NToN { endpoints } if *endpoints == 4 => "4->4",
            Self::NToN { .. } => "N->N",
        }
    }
}

struct Sample {
    latency_ms: f64,
    ok: bool,
}

struct LatencyStats {
    samples: usize,
    errors: usize,
    min_ms: f64,
    p50_ms: f64,
    p90_ms: f64,
    p95_ms: f64,
    p99_ms: f64,
    mean_ms: f64,
    max_ms: f64,
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let index = ((p / 100.0) * (sorted.len() - 1) as f64).round() as usize;
    sorted[index.min(sorted.len() - 1)]
}

fn summarize(samples: &[Sample]) -> LatencyStats {
    let mut ok: Vec<f64> = samples
        .iter()
        .filter(|sample| sample.ok)
        .map(|sample| sample.latency_ms)
        .collect();
    ok.sort_by(|a, b| a.partial_cmp(b).expect("finite latencies"));
    let errors = samples.iter().filter(|sample| !sample.ok).count();
    let mean = if ok.is_empty() {
        0.0
    } else {
        ok.iter().sum::<f64>() / ok.len() as f64
    };
    LatencyStats {
        samples: samples.len(),
        errors,
        min_ms: ok.first().copied().unwrap_or(0.0),
        p50_ms: percentile(&ok, 50.0),
        p90_ms: percentile(&ok, 90.0),
        p95_ms: percentile(&ok, 95.0),
        p99_ms: percentile(&ok, 99.0),
        mean_ms: mean,
        max_ms: ok.last().copied().unwrap_or(0.0),
    }
}

fn message_of_size(size_bytes: usize) -> String {
    "m".repeat(size_bytes)
}

/// One timed operation. The message body and selectors are built first so the
/// timed window wraps the real transport call only.
async fn one_call(
    driver: &Arc<dyn ConnectionDriver>,
    socket_path: &str,
    op: BenchOp,
    sender_index: usize,
    target_index: usize,
) -> Sample {
    let (from, target, message) = match op {
        BenchOp::SendMessage { size_bytes } => (
            format!("bench-from-active-{sender_index}"),
            format!("bench-target-active-{target_index}"),
            message_of_size(size_bytes),
        ),
        BenchOp::ListPeers { .. } => (String::new(), String::new(), String::new()),
    };
    let start = Instant::now();
    let result = match op {
        BenchOp::SendMessage { .. } => driver
            .send_message(socket_path.to_string(), from, target, message)
            .await
            .map(|_| ())
            .map_err(|error| error),
        BenchOp::ListPeers { .. } => {
            let peers = driver.list_peers(socket_path.to_string()).await;
            if peers.is_empty() {
                Err("empty peer list".to_string())
            } else {
                Ok(())
            }
        }
    };
    Sample {
        latency_ms: start.elapsed().as_secs_f64() * 1000.0,
        ok: result.is_ok(),
    }
}

fn target_for(topology: Topology, worker: usize) -> usize {
    match topology {
        Topology::OneToOne | Topology::NToOne { .. } => 0,
        Topology::OneToN { targets } | Topology::NToN { endpoints: targets } => {
            worker % targets.max(1)
        }
    }
}

/// Sender identity per topology: 1->N uses one logical source, N->1 and N->N
/// use one distinct source per worker.
fn sender_for(topology: Topology, worker: usize) -> usize {
    match topology {
        Topology::OneToOne | Topology::OneToN { .. } => 0,
        Topology::NToOne { .. } | Topology::NToN { .. } => worker,
    }
}

/// Steady state: sequential or concurrent request loop collecting per-request
/// latency around the real transport call only.
async fn run_steady(
    driver: &Arc<dyn ConnectionDriver>,
    socket_path: &str,
    op: BenchOp,
    topology: Topology,
    concurrency: usize,
    total_requests: usize,
) -> Vec<Sample> {
    if concurrency <= 1 {
        let mut samples = Vec::with_capacity(total_requests);
        for index in 0..total_requests {
            samples.push(
                one_call(
                    driver,
                    socket_path,
                    op,
                    sender_for(topology, 0),
                    target_for(topology, index),
                )
                .await,
            );
        }
        return samples;
    }
    let per_worker = total_requests / concurrency;
    let remainder = total_requests % concurrency;
    let mut workers = Vec::with_capacity(concurrency);
    for worker in 0..concurrency {
        let worker_requests = per_worker + usize::from(worker < remainder);
        let driver = Arc::clone(driver);
        let socket_path = socket_path.to_string();
        workers.push(tokio::spawn(async move {
            let mut samples = Vec::with_capacity(worker_requests);
            for _ in 0..worker_requests {
                samples.push(
                    one_call(
                        &driver,
                        &socket_path,
                        op,
                        sender_for(topology, worker),
                        target_for(topology, worker),
                    )
                    .await,
                );
            }
            samples
        }));
    }
    let mut samples = Vec::with_capacity(total_requests);
    for worker in workers {
        if let Ok(mut worker_samples) = worker.await {
            samples.append(&mut worker_samples);
        }
    }
    samples
}

/// Burst: every task fires exactly one request, all released at once.
async fn run_burst(
    driver: &Arc<dyn ConnectionDriver>,
    socket_path: &str,
    op: BenchOp,
    concurrency: usize,
    topology: Topology,
) -> (Vec<Sample>, f64) {
    let start_barrier = Arc::new(tokio::sync::Barrier::new(concurrency));
    let mut workers = Vec::with_capacity(concurrency);
    for worker in 0..concurrency {
        let driver = Arc::clone(driver);
        let socket_path = socket_path.to_string();
        let barrier = Arc::clone(&start_barrier);
        workers.push(tokio::spawn(async move {
            barrier.wait().await;
            one_call(
                &driver,
                &socket_path,
                op,
                sender_for(topology, worker),
                target_for(topology, worker),
            )
            .await
        }));
    }
    let wall_start = Instant::now();
    let mut samples = Vec::with_capacity(concurrency);
    for worker in workers {
        if let Ok(sample) = worker.await {
            samples.push(sample);
        }
    }
    let wall_ms = wall_start.elapsed().as_secs_f64() * 1000.0;
    (samples, wall_ms)
}

/// Sustained: keep `concurrency` workers sending until the duration elapses.
async fn run_sustained(
    driver: &Arc<dyn ConnectionDriver>,
    socket_path: &str,
    op: BenchOp,
    concurrency: usize,
    duration: Duration,
) -> (Vec<Sample>, f64) {
    let deadline = Instant::now() + duration;
    let mut workers = Vec::with_capacity(concurrency);
    for worker in 0..concurrency {
        let driver = Arc::clone(driver);
        let socket_path = socket_path.to_string();
        let deadline = deadline;
        workers.push(tokio::spawn(async move {
            let mut samples = Vec::new();
            while Instant::now() < deadline {
                samples.push(
                    one_call(&driver, &socket_path, op, worker, 0).await,
                );
            }
            samples
        }));
    }
    let wall_start = Instant::now();
    let mut samples = Vec::new();
    for worker in workers {
        if let Ok(mut worker_samples) = worker.await {
            samples.append(&mut worker_samples);
        }
    }
    let wall_ms = wall_start.elapsed().as_secs_f64() * 1000.0;
    (samples, wall_ms)
}

// ---------------------------------------------------------------------------
// Results output.
// ---------------------------------------------------------------------------

fn results_dir() -> std::path::PathBuf {
    // Tests run with the package root as cwd; the repo research tree is two
    // levels up from the crate manifest.
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../research/network_latency/ipc/results")
}

struct ResultsWriter {
    path: std::path::PathBuf,
}

impl ResultsWriter {
    fn new(prefix: &str) -> Self {
        let dir = results_dir();
        std::fs::create_dir_all(&dir).expect("create results dir");
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock after epoch")
            .as_millis();
        let path = dir.join(format!("{prefix}-{now_ms}.jsonl"));
        Self { path }
    }

    fn write(&self, value: &Value) {
        use std::io::Write;
        let mut line = value.to_string();
        line.push('\n');
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .expect("open results file");
        file.write_all(line.as_bytes()).expect("write result line");
    }

    fn path_label(&self) -> String {
        self.path.to_string_lossy().into_owned()
    }
}

fn environment_line() -> Value {
    let rustc_version = std::process::Command::new("rustc")
        .arg("--version")
        .output()
        .ok()
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .unwrap_or_default()
        .trim()
        .to_string();
    json!({
        "kind": "env",
        "os": std::env::consts::OS,
        "cpu": std::env::var("PROCESSOR_IDENTIFIER").unwrap_or_else(|_| "unknown".to_string()),
        "rustc": rustc_version,
        "profile": if cfg!(debug_assertions) { "debug" } else { "release" },
        "available_parallelism": std::thread::available_parallelism().map(|value| value.get()).unwrap_or(0),
        "timestamp_unix_ms": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock after epoch")
            .as_millis() as u64,
    })
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

fn result_line(
    writer: &ResultsWriter,
    mode: ConnectionMode,
    op: BenchOp,
    scenario: &str,
    topology: Topology,
    concurrency: usize,
    wall_ms: f64,
    samples: &[Sample],
    server: &ServerStats,
    notes: &str,
) {
    let stats = summarize(samples);
    let ok_samples = samples.iter().filter(|sample| sample.ok).count();
    let throughput = if wall_ms > 0.0 {
        ok_samples as f64 / (wall_ms / 1000.0)
    } else {
        0.0
    };
    writer.write(&json!({
        "kind": "result",
        "mode": mode.label(),
        "op": op.label(),
        "scenario": scenario,
        "topology": topology.label(),
        "concurrency": concurrency,
        "samples": stats.samples,
        "errors": stats.errors,
        "latency_ms": {
            "min": round3(stats.min_ms),
            "p50": round3(stats.p50_ms),
            "p90": round3(stats.p90_ms),
            "p95": round3(stats.p95_ms),
            "p99": round3(stats.p99_ms),
            "mean": round3(stats.mean_ms),
            "max": round3(stats.max_ms),
        },
        "wall_ms": round3(wall_ms),
        "throughput_msg_per_s": round3(throughput),
        "server_accepted_connections": server.accepted_connections.load(Ordering::SeqCst),
        "server_requests": server.requests.load(Ordering::SeqCst),
        "notes": notes,
    }));
}

fn print_config(mode: ConnectionMode, op: BenchOp, scenario: &str) {
    println!(
        "[agent-message-bench] mode={} op={} scenario={}",
        mode.label(),
        op.label(),
        scenario
    );
}

// ---------------------------------------------------------------------------
// The baseline matrix.
// ---------------------------------------------------------------------------

const WARMUP_REQUESTS: usize = 50;
const STEADY_SAMPLES: usize = 500;

/// The two matrix tests must not overlap: they share one process-wide
/// connection manager and one machine, and contention would skew both arms.
static MATRIX_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn sequential_wall_ms(samples: &[Sample]) -> f64 {
    samples.iter().map(|sample| sample.latency_ms).sum()
}

async fn run_baseline_matrix(mode: ConnectionMode, driver: Arc<dyn ConnectionDriver>) {
    let writer = ResultsWriter::new("baseline");
    println!("[agent-message-bench] results: {}", writer.path_label());
    writer.write(&environment_line());

    // A. Message size sweep: warm steady state, one sender, one target.
    for size_bytes in [64, 256, 1024, 4096, 16384, 65536, 262144] {
        let op = BenchOp::SendMessage { size_bytes };
        print_config(mode, op, "warm");
        let fixture = EchoSupervisor::new(FixtureOptions::default());
        let _ = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, WARMUP_REQUESTS).await;
        let samples = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, STEADY_SAMPLES).await;
        result_line(&writer, mode, op, "warm", Topology::OneToOne, 1, sequential_wall_ms(&samples), &samples, &fixture.stats, "sequential; warmup 50");
    }

    // B. Concurrency sweep: warm steady state, 256B messages, single target.
    for concurrency in [1, 2, 4, 8, 16, 32] {
        let op = BenchOp::SendMessage { size_bytes: 256 };
        print_config(mode, op, "warm");
        let fixture = EchoSupervisor::new(FixtureOptions::default());
        let _ = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, concurrency, WARMUP_REQUESTS).await;
        let start = Instant::now();
        let samples = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, concurrency, STEADY_SAMPLES).await;
        let wall_ms = start.elapsed().as_secs_f64() * 1000.0;
        result_line(&writer, mode, op, "warm", Topology::OneToOne, concurrency, wall_ms, &samples, &fixture.stats, "concurrent workers; one fresh client per request");
    }

    // C. Topology sweep: warm steady state, 256B messages, 4 concurrent senders.
    for topology in [
        Topology::OneToOne,
        Topology::OneToN { targets: 4 },
        Topology::NToOne { senders: 4 },
        Topology::NToN { endpoints: 4 },
    ] {
        let op = BenchOp::SendMessage { size_bytes: 256 };
        print_config(mode, op, "warm-topology");
        let fixture = EchoSupervisor::new(FixtureOptions::default());
        let _ = run_steady(&driver, &fixture.socket_path, op, topology, 4, WARMUP_REQUESTS).await;
        let start = Instant::now();
        let samples = run_steady(&driver, &fixture.socket_path, op, topology, 4, STEADY_SAMPLES).await;
        let wall_ms = start.elapsed().as_secs_f64() * 1000.0;
        result_line(&writer, mode, op, "warm-topology", topology, 4, wall_ms, &samples, &fixture.stats, "distinct from/target selectors; one supervisor fixture");
    }

    // D1. Cold: fresh fixture, first requests after server start, no warmup.
    {
        let op = BenchOp::SendMessage { size_bytes: 256 };
        print_config(mode, op, "cold");
        let fixture = EchoSupervisor::new(FixtureOptions::default());
        let samples = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, 50).await;
        result_line(&writer, mode, op, "cold", Topology::OneToOne, 1, sequential_wall_ms(&samples), &samples, &fixture.stats, "first 50 requests after fixture start; no warmup; no tail claims");
    }

    // D2. Burst: 32 tasks released at once, one request each.
    {
        let op = BenchOp::SendMessage { size_bytes: 256 };
        print_config(mode, op, "burst");
        let fixture = EchoSupervisor::new(FixtureOptions::default());
        let _ = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, WARMUP_REQUESTS).await;
        let (samples, wall_ms) = run_burst(&driver, &fixture.socket_path, op, 32, Topology::OneToN { targets: 4 }).await;
        result_line(&writer, mode, op, "burst", Topology::OneToN { targets: 4 }, 32, wall_ms, &samples, &fixture.stats, "single-shot barrier release; 32 samples only; no tail claims");
    }

    // D3. Sustained: fixed 10 s windows at concurrency 1 and 8.
    for concurrency in [1, 8] {
        let op = BenchOp::SendMessage { size_bytes: 256 };
        print_config(mode, op, "sustained");
        let fixture = EchoSupervisor::new(FixtureOptions::default());
        let _ = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, concurrency, WARMUP_REQUESTS).await;
        let (samples, wall_ms) = run_sustained(&driver, &fixture.socket_path, op, concurrency, Duration::from_secs(10)).await;
        result_line(&writer, mode, op, "sustained-10s", Topology::OneToOne, concurrency, wall_ms, &samples, &fixture.stats, "loop until 10s elapsed");
    }

    // D4. Connection churn: the server closes the connection right after each
    // response, forcing connect+hello on every call. With the fresh client the
    // client also closes, so this isolates server-side close ordering.
    {
        let op = BenchOp::SendMessage { size_bytes: 256 };
        print_config(mode, op, "churn");
        let fixture = EchoSupervisor::new(FixtureOptions {
            close_after_response: true,
            ..FixtureOptions::default()
        });
        let _ = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, WARMUP_REQUESTS).await;
        let samples = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, STEADY_SAMPLES).await;
        result_line(&writer, mode, op, "churn", Topology::OneToOne, 1, sequential_wall_ms(&samples), &samples, &fixture.stats, "server closes after each response");
    }

    // D5. Daemon restart mid-run: the supervisor stops accepting briefly,
    // then resumes; the transport must recover via its connect retry loop.
    {
        let op = BenchOp::SendMessage { size_bytes: 256 };
        print_config(mode, op, "restart");
        let fixture = EchoSupervisor::new(FixtureOptions::default());
        let _ = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, WARMUP_REQUESTS).await;
        let pause = Arc::clone(&fixture.pause);
        let pauser = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(200)).await;
            pause.pause();
            tokio::time::sleep(Duration::from_millis(1500)).await;
            pause.resume();
        });
        let samples = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, 60).await;
        let _ = pauser.await;
        result_line(&writer, mode, op, "restart", Topology::OneToOne, 1, sequential_wall_ms(&samples), &samples, &fixture.stats, "accept loop paused ~1.5s around request 20; send_message retries connects up to 30s");
    }

    // E. list_peers with growing roster sizes.
    for peers in [1, 10, 50] {
        let op = BenchOp::ListPeers { peers };
        print_config(mode, op, "warm");
        let fixture = EchoSupervisor::new(FixtureOptions {
            peer_count: peers,
            ..FixtureOptions::default()
        });
        let _ = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, WARMUP_REQUESTS).await;
        let samples = run_steady(&driver, &fixture.socket_path, op, Topology::OneToOne, 1, STEADY_SAMPLES).await;
        result_line(&writer, mode, op, "warm", Topology::OneToOne, 1, sequential_wall_ms(&samples), &samples, &fixture.stats, "peers returned in list_agent_peers response");
    }
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "benchmark: run with --release and --ignored"]
async fn agent_message_bench_baseline_matrix() {
    let _guard = MATRIX_LOCK.lock().await;
    run_baseline_matrix(ConnectionMode::Fresh, fresh_driver()).await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "benchmark: run with --release and --ignored"]
async fn agent_message_bench_persistent_matrix() {
    let _guard = MATRIX_LOCK.lock().await;
    match persistent_driver() {
        Some(driver) => run_baseline_matrix(ConnectionMode::Persistent, driver).await,
        None => panic!("persistent arm unavailable: agent_message_connection manager missing"),
    }
}
