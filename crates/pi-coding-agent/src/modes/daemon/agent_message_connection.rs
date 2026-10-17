//! Persistent authenticated worker-to-supervisor agent message connection manager.
//! Owned by the network/IPC performance campaign (worker ipc-persistent).
//!
//! `agent_message_transport.rs` pays a pipe/UDS connect + hello + teardown for every
//! agent-to-agent message. This module caches ONE authenticated `DaemonClient` per
//! supervisor socket path and reuses it across `list_peers` and `send_message`.
//!
//! Safety contract (mirrors the transport's invariant): once a command has been
//! handed to `DaemonClient::request`, a failure is returned to the caller and the
//! command is never re-sent, because the supervisor may already have accepted it.
//! Only failures observed before the write (connect, hello, dead cached
//! connection checkout) may establish a new connection and send once.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex as StdMutex, OnceLock};
use std::time::Duration;

use tokio::sync::Mutex as TokioMutex;

use crate::modes::daemon::daemon_client::{
    DaemonClient, DaemonClientError, DaemonClientResult, DaemonCommandBody,
};
use crate::modes::daemon::daemon_protocol::DaemonResponse;

/// Same connect budget the fresh-per-call transport used.
const CONNECT_TIMEOUT_MS: u64 = 1_000;
/// Same hello budget the fresh-per-call transport used.
const HELLO_TIMEOUT_MS: u64 = 1_000;
/// An idle connection releases its pipe after this long.
const IDLE_EVICTION_MS: u64 = 60_000;
/// Janitor wake-up cadence while a connection is idle but not yet evictable.
const IDLE_SWEEP_INTERVAL_MS: u64 = 1_000;

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

/// How hard `request` tries to obtain a live connection before the command is sent.
pub(super) enum ConnectRetry {
    /// One establishment attempt; discovery-style calls that degrade locally.
    Once,
    /// Retry establishment until `deadline` with a fixed delay, the transport's
    /// historical send loop.
    UntilDeadline {
        deadline: tokio::time::Instant,
        delay: Duration,
    },
}

/// One cached connection plus its idle/in-flight bookkeeping.
struct LeasedClient {
    client: Arc<DaemonClient>,
    last_used_ms: AtomicU64,
    in_flight: AtomicUsize,
}

impl LeasedClient {
    fn new(client: Arc<DaemonClient>) -> Self {
        Self {
            client,
            last_used_ms: AtomicU64::new(now_ms()),
            in_flight: AtomicUsize::new(0),
        }
    }

    fn mark_used(&self) {
        self.last_used_ms.store(now_ms(), Ordering::SeqCst);
    }

    fn idle_ms(&self) -> u64 {
        now_ms().saturating_sub(self.last_used_ms.load(Ordering::SeqCst))
    }
}

/// Drops the in-flight reservation when the request completes.
struct LeaseGuard {
    leased: Arc<LeasedClient>,
}

impl Drop for LeaseGuard {
    fn drop(&mut self) {
        self.leased.mark_used();
        self.leased.in_flight.fetch_sub(1, Ordering::SeqCst);
    }
}

struct ConnectionSlot {
    /// Held only while checking out, establishing, or evicting the connection;
    /// never across request I/O.
    state: TokioMutex<Option<Arc<LeasedClient>>>,
}

/// Process-wide cache of persistent supervisor connections, keyed by socket path.
pub(super) struct AgentMessageConnectionManager {
    slots: StdMutex<HashMap<String, Arc<ConnectionSlot>>>,
    idle_eviction_ms: u64,
}

static GLOBAL_MANAGER: OnceLock<AgentMessageConnectionManager> = OnceLock::new();

/// The manager the agent-message transport uses.
pub(super) fn global_manager() -> &'static AgentMessageConnectionManager {
    GLOBAL_MANAGER.get_or_init(|| AgentMessageConnectionManager {
        slots: StdMutex::new(HashMap::new()),
        idle_eviction_ms: IDLE_EVICTION_MS,
    })
}

impl AgentMessageConnectionManager {
    /// A manager with a custom idle eviction budget (tests and benches).
    #[cfg(test)]
    pub(super) fn with_idle_eviction_ms(idle_eviction_ms: u64) -> Self {
        Self {
            slots: StdMutex::new(HashMap::new()),
            idle_eviction_ms,
        }
    }

    fn slot_for(&self, socket_path: &str) -> Arc<ConnectionSlot> {
        let mut slots = self.slots.lock().expect("connection slots poisoned");
        Arc::clone(slots.entry(socket_path.to_string()).or_insert_with(|| {
            Arc::new(ConnectionSlot {
                state: TokioMutex::new(None),
            })
        }))
    }

    /// Send one command over the persistent connection for `socket_path`.
    ///
    /// Establishment (connect + hello) may be retried per `connect`; the command
    /// itself is sent at most once. `shutting_down` cancels before any
    /// (re)connect and closes an idle cached connection.
    pub(super) async fn request(
        &self,
        socket_path: &str,
        command: DaemonCommandBody,
        request_timeout_ms: u64,
        connect: ConnectRetry,
        shutting_down: Option<&AtomicBool>,
    ) -> DaemonClientResult<DaemonResponse> {
        let is_shutting_down = || shutting_down.is_some_and(|flag| flag.load(Ordering::SeqCst));
        if is_shutting_down() {
            self.evict_if_idle(socket_path).await;
            return Err(DaemonClientError::Message("Daemon is shutting down".to_string()));
        }
        loop {
            if is_shutting_down() {
                self.evict_if_idle(socket_path).await;
                return Err(DaemonClientError::Message("Daemon is shutting down".to_string()));
            }
            match self.lease_once(socket_path, &is_shutting_down).await {
                Ok(guard) => {
                    // Safety boundary: the command may reach the wire from here.
                    // Any failure is returned; it is never re-sent on another
                    // connection.
                    let result = guard
                        .leased
                        .client
                        .request(command, Some(request_timeout_ms), Default::default())
                        .await;
                    drop(guard);
                    return result;
                }
                Err(error) => match connect {
                    ConnectRetry::Once => return Err(error),
                    ConnectRetry::UntilDeadline { deadline, delay } => {
                        if tokio::time::Instant::now() >= deadline {
                            return Err(error);
                        }
                        tokio::time::sleep(delay).await;
                    }
                },
            }
        }
    }

    /// Checkout-or-establish the cached connection. The slot mutex is held across
    /// establishment so concurrent callers single-flight one connect + hello.
    async fn lease_once(
        &self,
        socket_path: &str,
        is_shutting_down: impl Fn() -> bool,
    ) -> Result<LeaseGuard, DaemonClientError> {
        let slot = self.slot_for(socket_path);
        let mut state = slot.state.lock().await;
        if let Some(cached) = state.clone() {
            if !is_shutting_down() && cached.client.is_connected() {
                cached.mark_used();
                cached.in_flight.fetch_add(1, Ordering::SeqCst);
                return Ok(LeaseGuard { leased: cached });
            }
            // Dead or shutting down. Drop the cache; only close when nothing is
            // in flight so a concurrent request is never cut off.
            *state = None;
            if cached.in_flight.load(Ordering::SeqCst) == 0 {
                cached.client.close().await;
            }
        }
        if is_shutting_down() {
            return Err(DaemonClientError::Message("Daemon is shutting down".to_string()));
        }
        let candidate = Arc::new(DaemonClient::new(socket_path));
        // A new supervisor generation revalidates here: connect + hello checks.
        let connected = async {
            candidate.connect(CONNECT_TIMEOUT_MS).await?;
            candidate.wait_for_hello(HELLO_TIMEOUT_MS).await?;
            Ok::<_, DaemonClientError>(())
        }
        .await;
        match connected {
            Ok(()) => {
                let leased = Arc::new(LeasedClient::new(Arc::clone(&candidate)));
                leased.mark_used();
                leased.in_flight.fetch_add(1, Ordering::SeqCst);
                *state = Some(Arc::clone(&leased));
                self.spawn_idle_janitor(Arc::clone(&slot), Arc::clone(&leased));
                Ok(LeaseGuard { leased })
            }
            Err(error) => {
                candidate.close().await;
                Err(error)
            }
        }
    }

    /// Release an idle connection's pipe (never one with a request in flight).
    async fn evict_if_idle(&self, socket_path: &str) {
        let slot = self.slot_for(socket_path);
        let mut state = slot.state.lock().await;
        let Some(cached) = state.clone() else {
            return;
        };
        if cached.in_flight.load(Ordering::SeqCst) != 0 {
            return;
        }
        *state = None;
        cached.client.close().await;
    }

    /// Close the cached connection once it has been idle past the eviction
    /// budget. Exits as soon as the slot caches a different connection. If the
    /// deadline coincides with an in-flight request, the loop re-arms instead of
    /// abandoning the connection: the request's guard resets the idle clock on
    /// drop, so eviction is retried after the next sweep.
    fn spawn_idle_janitor(&self, slot: Arc<ConnectionSlot>, leased: Arc<LeasedClient>) {
        let idle_eviction_ms = self.idle_eviction_ms;
        tokio::spawn(async move {
            loop {
                let remaining = idle_eviction_ms.saturating_sub(leased.idle_ms());
                if remaining > 0 {
                    tokio::time::sleep(Duration::from_millis(remaining.min(IDLE_SWEEP_INTERVAL_MS))).await;
                    continue;
                }
                let mut state = slot.state.lock().await;
                if !state.as_ref().is_some_and(|cached| Arc::ptr_eq(cached, &leased)) {
                    return;
                }
                if leased.in_flight.load(Ordering::SeqCst) == 0 {
                    *state = None;
                    leased.client.close().await;
                    return;
                }
                drop(state);
                tokio::time::sleep(Duration::from_millis(IDLE_SWEEP_INTERVAL_MS)).await;
            }
        });
    }
}

#[cfg(test)]
mod connection_bench {
    use super::*;
    use serde_json::{json, Value};
    use std::sync::atomic::AtomicUsize;
    use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

    /// Echo supervisor that accepts any number of sequential connections and
    /// answers every `send_message` with a delivered receipt.
    struct EchoSupervisor {
        socket: String,
        accepts: Arc<AtomicUsize>,
        send_messages: Arc<AtomicUsize>,
        task: tokio::task::JoinHandle<()>,
        _root: tempfile::TempDir,
    }

    impl Drop for EchoSupervisor {
        fn drop(&mut self) {
            self.task.abort();
        }
    }

    async fn serve_connection<S: AsyncRead + AsyncWrite + Unpin>(
        socket: S,
        send_messages: Arc<AtomicUsize>,
    ) {
        let (reader, mut writer) = tokio::io::split(socket);
        let mut hello = json!({
            "type": "daemon_hello",
            "protocol": crate::modes::daemon::daemon_protocol::daemon_protocol_info(),
            "schemaRevision": crate::modes::daemon::daemon_protocol::DAEMON_SCHEMA_REVISION,
            "serverCapabilities": [],
        })
        .to_string();
        hello.push('\n');
        if writer.write_all(hello.as_bytes()).await.is_err() {
            return;
        }
        let mut lines = BufReader::new(reader).lines();
        while let Some(line) = lines.next_line().await.unwrap_or(None) {
            if line.trim().is_empty() {
                continue;
            }
            let Ok(envelope) = serde_json::from_str::<Value>(&line) else {
                continue;
            };
            let command = envelope.get("command").cloned().unwrap_or_else(|| envelope.clone());
            if command.get("type").and_then(Value::as_str) != Some("send_message") {
                continue;
            }
            send_messages.fetch_add(1, Ordering::SeqCst);
            let mut response = json!({"success": true, "data": {
                "id": "bench-message",
                "source": "agent_message",
                "message": command.get("message").and_then(Value::as_str).unwrap_or_default(),
                "target": {"activeSessionId": "target-active", "sessionId": "target-session"},
                "from": {"activeSessionId": "source-active", "sessionId": "source-session"},
                "deliveryStatus": "delivered",
            }});
            response["type"] = json!("response");
            response["id"] = envelope["id"].clone();
            response["command"] = json!("send_message");
            let mut line = response.to_string();
            line.push('\n');
            if writer.write_all(line.as_bytes()).await.is_err() {
                return;
            }
        }
    }

    impl EchoSupervisor {
        fn new() -> Self {
            let root = tempfile::Builder::new()
                .prefix("optimus-message-connection-bench-")
                .tempdir()
                .unwrap();
            let accepts = Arc::new(AtomicUsize::new(0));
            let send_messages = Arc::new(AtomicUsize::new(0));
            let counted_accepts = Arc::clone(&accepts);
            let counted_messages = Arc::clone(&send_messages);
            #[cfg(windows)]
            let (socket, task) = {
                let socket = format!(
                    r"\\.\pipe\optimus-message-connection-bench-{}",
                    uuid::Uuid::new_v4()
                );
                let server = tokio::net::windows::named_pipe::ServerOptions::new()
                    .first_pipe_instance(true)
                    .create(&socket)
                    .unwrap();
                let socket_for_task = socket.clone();
                let task = tokio::spawn(async move {
                    let mut server = server;
                    loop {
                        if server.connect().await.is_err() {
                            return;
                        }
                        let next = match tokio::net::windows::named_pipe::ServerOptions::new().create(&socket_for_task) {
                            Ok(next) => next,
                            Err(_) => return,
                        };
                        let connected = std::mem::replace(&mut server, next);
                        counted_accepts.fetch_add(1, Ordering::SeqCst);
                        let messages = Arc::clone(&counted_messages);
                        tokio::spawn(async move {
                            serve_connection(connected, messages).await;
                        });
                    }
                });
                (socket, task)
            };
            #[cfg(unix)]
            let (socket, task) = {
                let socket = root
                    .path()
                    .join("supervisor.sock")
                    .to_string_lossy()
                    .into_owned();
                let listener = tokio::net::UnixListener::bind(&socket).unwrap();
                let task = tokio::spawn(async move {
                    loop {
                        let Ok((stream, _)) = listener.accept().await else {
                            return;
                        };
                        counted_accepts.fetch_add(1, Ordering::SeqCst);
                        let messages = Arc::clone(&counted_messages);
                        tokio::spawn(async move {
                            serve_connection(stream, messages).await;
                        });
                    }
                });
                (socket, task)
            };
            Self { socket, accepts, send_messages, task, _root: root }
        }
    }

    fn bench_command(index: usize) -> DaemonCommandBody {
        json!({
            "type": "send_message",
            "fromActiveSessionId": "source-active",
            "targetActiveSessionId": "target-active",
            "message": format!("bench message {index}"),
            "agentOrigin": true,
        })
        .as_object()
        .expect("command object")
        .clone()
    }

    /// Fresh-per-call transport, the pre-persistent behavior, for comparison.
    async fn fresh_request(socket: &str, command: DaemonCommandBody) -> DaemonClientResult<DaemonResponse> {
        let client = Arc::new(DaemonClient::new(socket));
        let result = async {
            client.connect(CONNECT_TIMEOUT_MS).await?;
            client.wait_for_hello(HELLO_TIMEOUT_MS).await?;
            client
                .request(command, Some(30_000), Default::default())
                .await
        }
        .await;
        client.close().await;
        result
    }

    fn report(samples: &[u128]) -> (f64, f64, f64, f64) {
        let mut sorted = samples.to_vec();
        sorted.sort_unstable();
        let mean = sorted.iter().sum::<u128>() as f64 / sorted.len() as f64;
        let percentile = |fraction: f64| {
            let index = ((sorted.len() as f64 - 1.0) * fraction).round() as usize;
            sorted[index.min(sorted.len() - 1)] as f64
        };
        (mean, percentile(0.5), percentile(0.95), percentile(0.99))
    }

    /// `cargo test -p pi-coding-agent --lib --release -- --ignored connection_bench --nocapture`
    #[ignore]
    #[tokio::test]
    async fn persistent_ipc_bench_200_sequential_sends() {
        const SENDS: usize = 200;
        let server = EchoSupervisor::new();
        let manager = AgentMessageConnectionManager::with_idle_eviction_ms(IDLE_EVICTION_MS);

        let mut fresh_samples = Vec::with_capacity(SENDS);
        for index in 0..SENDS {
            let started = std::time::Instant::now();
            fresh_request(&server.socket, bench_command(index)).await.expect("fresh send");
            fresh_samples.push(started.elapsed().as_micros());
        }
        let fresh_accepts_before = server.accepts.load(Ordering::SeqCst);

        let mut persistent_samples = Vec::with_capacity(SENDS);
        for index in 0..SENDS {
            let started = std::time::Instant::now();
            manager
                .request(
                    &server.socket,
                    bench_command(index),
                    30_000,
                    ConnectRetry::Once,
                    None,
                )
                .await
                .expect("persistent send");
            persistent_samples.push(started.elapsed().as_micros());
        }
        let persistent_accepts = server.accepts.load(Ordering::SeqCst) - fresh_accepts_before;
        let sends = server.send_messages.load(Ordering::SeqCst);

        let (fresh_mean, fresh_p50, fresh_p95, fresh_p99) = report(&fresh_samples);
        let (persistent_mean, persistent_p50, persistent_p95, persistent_p99) = report(&persistent_samples);
        println!(
            "ipc bench: fresh mean/p50/p95/p99 = {:.3}/{:.3}/{:.3}/{:.3} ms, accepts {}",
            fresh_mean / 1000.0,
            fresh_p50 / 1000.0,
            fresh_p95 / 1000.0,
            fresh_p99 / 1000.0,
            fresh_accepts_before
        );
        println!(
            "ipc bench: persistent mean/p50/p95/p99 = {:.3}/{:.3}/{:.3}/{:.3} ms, accepts {}",
            persistent_mean / 1000.0,
            persistent_p50 / 1000.0,
            persistent_p95 / 1000.0,
            persistent_p99 / 1000.0,
            persistent_accepts
        );
        println!("ipc bench: total sends answered = {sends}");
        assert_eq!(sends, 2 * SENDS, "every send must be answered exactly once");
        assert_eq!(persistent_accepts, 1, "persistent arm must use exactly one connection");
        assert!(
            persistent_mean < fresh_mean,
            "persistent arm should not be slower than fresh per-call connections"
        );
    }
}
