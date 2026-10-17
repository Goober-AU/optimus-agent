//! Existing supervisor RPCs used by worker-side agent messaging.

use std::sync::atomic::AtomicBool;
use std::time::Duration;

use serde_json::{json, Value};

use crate::core::agent_messages::{
    AgentSessionMessageAgentSummary, AgentSessionMessageReceipt,
    DELIVERY_STATUS_DELIVERED, DELIVERY_STATUS_QUEUED,
};
use crate::modes::daemon::daemon_errors::deserialize_daemon_error;

use super::agent_message_connection::{global_manager, ConnectRetry};

pub(super) async fn list_peers(
    socket_path: &str,
    worker_token: &str,
) -> Vec<AgentSessionMessageAgentSummary> {
    // request() checks the existing schema-revision compatibility gate.
    let response = global_manager()
        .request(
            socket_path,
            json!({"type": "list_agent_peers", "workerToken": worker_token})
                .as_object().expect("command object").clone(),
            5_000,
            ConnectRetry::Once,
            None,
        )
        .await;
    let result = match response {
        Ok(response) if response.success => serde_json::from_value::<Vec<AgentSessionMessageAgentSummary>>(
            response.data.and_then(|data| data.get("peers").cloned()).unwrap_or(Value::Null),
        ).map_err(|error| error.to_string()),
        Ok(response) => Err(deserialize_daemon_error(&response).to_string()),
        Err(error) => Err(error.to_string()),
    };
    // Discovery is optional; an older or temporarily unavailable supervisor must
    // not prevent local child messaging or session startup.
    result.unwrap_or_default()
}

pub(super) async fn send_message(
    socket_path: &str,
    from_active_session_id: &str,
    target_selector: &str,
    message: &str,
    shutting_down: &AtomicBool,
) -> Result<AgentSessionMessageReceipt, String> {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
    // Retry connections only. A sent message is never replayed after a lost
    // response: it may already have been accepted by the other worker. The
    // manager sends the command at most once and returns any failure.
    let response = global_manager()
        .request(
            socket_path,
            json!({
                "type": "send_message",
                "fromActiveSessionId": from_active_session_id,
                "targetActiveSessionId": target_selector,
                "message": message,
                "agentOrigin": true,
            }).as_object().expect("command object").clone(),
            30_000,
            ConnectRetry::UntilDeadline {
                deadline,
                delay: Duration::from_millis(250),
            },
            Some(shutting_down),
        )
        .await
        .map_err(|error| error.to_string())?;
    if !response.success {
        return Err(deserialize_daemon_error(&response).to_string());
    }
    let receipt: AgentSessionMessageReceipt = serde_json::from_value(
        response.data.unwrap_or(Value::Null),
    ).map_err(|_| "Supervisor returned an invalid agent-message receipt".to_string())?;
    if !matches!(receipt.delivery_status.as_str(), DELIVERY_STATUS_DELIVERED | DELIVERY_STATUS_QUEUED) {
        return Err("Supervisor returned an invalid agent-message receipt".to_string());
    }
    Ok(receipt)
}

#[cfg(test)]
mod messaging_safety_tests {
    use super::*;
    use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
    use crate::modes::daemon::daemon_protocol::{daemon_protocol_info, DAEMON_SCHEMA_REVISION};

    struct ScriptedSupervisor {
        socket: String,
        requests: Arc<std::sync::Mutex<Vec<Value>>>,
        task: tokio::task::JoinHandle<()>,
        _root: tempfile::TempDir,
    }

    impl Drop for ScriptedSupervisor {
        fn drop(&mut self) { self.task.abort(); }
    }

    async fn serve<S: AsyncRead + AsyncWrite + Unpin>(
        socket: S,
        revision: u32,
        requests: Arc<std::sync::Mutex<Vec<Value>>>,
        answer: Option<Value>,
    ) {
        let (reader, mut writer) = tokio::io::split(socket);
        let mut hello = json!({"type":"daemon_hello", "protocol":daemon_protocol_info(), "schemaRevision":revision, "serverCapabilities":[]}).to_string();
        hello.push('\n');
        writer.write_all(hello.as_bytes()).await.unwrap();
        let mut lines = BufReader::new(reader).lines();
        while let Some(line) = lines.next_line().await.unwrap() {
            if line.trim().is_empty() { continue; }
            let envelope: Value = serde_json::from_str(&line).unwrap();
            let command = envelope.get("command").cloned().unwrap_or_else(|| envelope.clone());
            requests.lock().unwrap().push(command.clone());
            let Some(mut response) = answer.clone() else { return; };
            response["type"] = json!("response");
            response["id"] = envelope["id"].clone();
            response["command"] = command["type"].clone();
            let mut line = response.to_string();
            line.push('\n');
            writer.write_all(line.as_bytes()).await.unwrap();
        }
    }

    impl ScriptedSupervisor {
        fn new(revision: u32, answer: Option<Value>) -> Self {
            let root = tempfile::Builder::new().prefix("optimus-message-transport-").tempdir().unwrap();
            let requests = Arc::new(std::sync::Mutex::new(Vec::new()));
            let captured = Arc::clone(&requests);
            #[cfg(windows)]
            let (socket, task) = {
                let socket = format!(r"\\.\pipe\optimus-message-transport-{}", uuid::Uuid::new_v4());
                let server = tokio::net::windows::named_pipe::ServerOptions::new().first_pipe_instance(true).create(&socket).unwrap();
                let task = tokio::spawn(async move {
                    server.connect().await.unwrap();
                    serve(server, revision, captured, answer).await;
                });
                (socket, task)
            };
            #[cfg(unix)]
            let (socket, task) = {
                let socket = root.path().join("supervisor.sock").to_string_lossy().into_owned();
                let server = tokio::net::UnixListener::bind(&socket).unwrap();
                let task = tokio::spawn(async move {
                    let (stream, _) = server.accept().await.unwrap();
                    serve(stream, revision, captured, answer).await;
                });
                (socket, task)
            };
            Self { socket, requests, task, _root: root }
        }
    }

    fn receipt(status: &str) -> Value {
        json!({"success":true,"data":{
            "id":"message-one", "source":"agent_message", "message":"hello peer",
            "target":{"activeSessionId":"target-active","sessionId":"target-session"},
            "from":{"activeSessionId":"source-active","sessionId":"source-session"},
            "deliveryStatus":status,
        }})
    }

    #[tokio::test]
    async fn messaging_safety_peer_discovery_uses_worker_token() {
        let peer = json!({"activeSessionId":"peer-active","sessionId":"peer-session","cwd":"fixture", "isStreaming":false, "unfinishedActionCount":0});
        let server = ScriptedSupervisor::new(DAEMON_SCHEMA_REVISION, Some(json!({"success":true,"data":{"peers":[peer]}})));
        let peers = list_peers(&server.socket, "private-worker-token").await;
        assert_eq!(peers.len(), 1);
        assert_eq!(peers[0].active_session_id, "peer-active");
        assert_eq!(server.requests.lock().unwrap()[0], json!({"id":"daemon_1","type":"list_agent_peers","workerToken":"private-worker-token"}));
    }

    #[tokio::test]
    async fn messaging_safety_old_supervisor_discovery_degrades_without_request() {
        let server = ScriptedSupervisor::new(22, None);
        assert!(list_peers(&server.socket, "private-worker-token").await.is_empty());
        assert!(server.requests.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn messaging_safety_remote_receipt_preserves_delivered_and_queued() {
        for status in [DELIVERY_STATUS_DELIVERED, DELIVERY_STATUS_QUEUED] {
            let server = ScriptedSupervisor::new(DAEMON_SCHEMA_REVISION, Some(receipt(status)));
            let result = send_message(&server.socket, "source-active", "target-active", "hello peer", &AtomicBool::new(false)).await.unwrap();
            assert_eq!(result.delivery_status, status);
            assert_eq!(result.from.unwrap().active_session_id.as_deref(), Some("source-active"));
            let requests = server.requests.lock().unwrap();
            let sends = send_message_bodies(&requests);
            assert_eq!(sends.len(), 1);
            assert_eq!(sends[0], json!({"id":"daemon_1","type":"send_message","fromActiveSessionId":"source-active","targetActiveSessionId":"target-active","message":"hello peer","agentOrigin":true}));
        }
    }

    #[tokio::test]
    async fn messaging_safety_rejected_admission_and_invalid_receipt_stay_errors() {
        for (response, expected) in [
            (json!({"success":false,"error":"Agent message was not accepted"}), "Agent message was not accepted"),
            (json!({"success":true,"data":{}}), "Supervisor returned an invalid agent-message receipt"),
            (receipt("rejected"), "Supervisor returned an invalid agent-message receipt"),
        ] {
            let server = ScriptedSupervisor::new(DAEMON_SCHEMA_REVISION, Some(response));
            let error = send_message(&server.socket, "source-active", "target-active", "hello peer", &AtomicBool::new(false)).await.unwrap_err();
            assert_eq!(error, expected);
            assert_eq!(send_message_bodies(&server.requests.lock().unwrap()).len(), 1);
        }
    }

    #[tokio::test]
    async fn messaging_safety_lost_response_is_never_replayed() {
        let server = ScriptedSupervisor::new(DAEMON_SCHEMA_REVISION, None);
        let result = tokio::time::timeout(Duration::from_secs(5), send_message(&server.socket, "source-active", "target-active", "hello peer", &AtomicBool::new(false))).await.unwrap();
        assert!(result.is_err());
        assert_eq!(send_message_bodies(&server.requests.lock().unwrap()).len(), 1);
    }

    #[tokio::test]
    async fn messaging_safety_shutdown_cancels_before_connecting() {
        let server = ScriptedSupervisor::new(DAEMON_SCHEMA_REVISION, Some(receipt(DELIVERY_STATUS_DELIVERED)));
        assert_eq!(send_message(&server.socket, "source-active", "target-active", "hello peer", &AtomicBool::new(true)).await.unwrap_err(), "Daemon is shutting down");
        assert!(server.requests.lock().unwrap().is_empty());
    }

    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use super::super::agent_message_connection::AgentMessageConnectionManager;

    /// The `send_message` command bodies the supervisor recorded, ignoring the
    /// client's `ack_result` confirmations (the persistent connection stays open,
    /// so those frames can be read at any time).
    fn send_message_bodies(requests: &[Value]) -> Vec<Value> {
        requests
            .iter()
            .filter(|command| command.get("type").and_then(Value::as_str) == Some("send_message"))
            .cloned()
            .collect()
    }

    /// What the scripted supervisor does with one request.
    enum ServeDecision {
        Respond(Value),
        /// Hold the response for the given milliseconds, keeping the worker's
        /// request in flight on the connection.
        DelayedRespond(Value, u64),
        RespondAndClose(Value),
        MuteAndClose,
    }

    type RequestHandler = Arc<dyn Fn(usize, usize, &Value) -> ServeDecision + Send + Sync>;

    /// Supervisor that accepts any number of connections. Every command goes
    /// through `handler(connection_index, request_index_on_connection, command)`.
    struct MultiConnectionSupervisor {
        socket: String,
        accepts: Arc<AtomicUsize>,
        disconnects: Arc<AtomicUsize>,
        requests: Arc<std::sync::Mutex<Vec<Value>>>,
        task: tokio::task::JoinHandle<()>,
        _root: tempfile::TempDir,
    }

    impl Drop for MultiConnectionSupervisor {
        fn drop(&mut self) {
            self.task.abort();
        }
    }

    async fn serve_persistent_connection<S: AsyncRead + AsyncWrite + Unpin>(
        socket: S,
        connection_index: usize,
        handler: RequestHandler,
        requests: Arc<std::sync::Mutex<Vec<Value>>>,
        disconnects: Arc<AtomicUsize>,
    ) {
        let (reader, mut writer) = tokio::io::split(socket);
        let mut hello = json!({
            "type": "daemon_hello",
            "protocol": daemon_protocol_info(),
            "schemaRevision": DAEMON_SCHEMA_REVISION,
            "serverCapabilities": [],
        })
        .to_string();
        hello.push('\n');
        if writer.write_all(hello.as_bytes()).await.is_err() {
            return;
        }
        let mut lines = BufReader::new(reader).lines();
        let mut request_index = 0usize;
        while let Some(line) = lines.next_line().await.unwrap_or(None) {
            if line.trim().is_empty() {
                continue;
            }
            let Ok(envelope) = serde_json::from_str::<Value>(&line) else {
                continue;
            };
            let command = envelope.get("command").cloned().unwrap_or_else(|| envelope.clone());
            requests.lock().unwrap().push(command.clone());
            let id = envelope["id"].clone();
            let command_type = command["type"].clone();
            let mut response = match handler(connection_index, request_index, &command) {
                ServeDecision::Respond(response) => response,
                ServeDecision::DelayedRespond(response, delay_ms) => {
                    tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                    response
                }
                ServeDecision::RespondAndClose(response) => {
                    let mut line = framed_response(response, id, command_type);
                    line.push('\n');
                    let _ = writer.write_all(line.as_bytes()).await;
                    break;
                }
                ServeDecision::MuteAndClose => break,
            };
            response["type"] = json!("response");
            response["id"] = id;
            response["command"] = command_type;
            let mut line = response.to_string();
            line.push('\n');
            if writer.write_all(line.as_bytes()).await.is_err() {
                break;
            }
            request_index += 1;
        }
        disconnects.fetch_add(1, Ordering::SeqCst);
    }

    fn framed_response(mut response: Value, id: Value, command_type: Value) -> String {
        response["type"] = json!("response");
        response["id"] = id;
        response["command"] = command_type;
        response.to_string()
    }

    async fn wait_until(deadline_ms: u64, condition: impl Fn() -> bool) {
        let deadline = std::time::Instant::now() + Duration::from_millis(deadline_ms);
        while !condition() && std::time::Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    impl MultiConnectionSupervisor {
        fn new(handler: RequestHandler) -> Self {
            let root = tempfile::Builder::new()
                .prefix("optimus-message-persistent-")
                .tempdir()
                .unwrap();
            let accepts = Arc::new(AtomicUsize::new(0));
            let disconnects = Arc::new(AtomicUsize::new(0));
            let requests = Arc::new(std::sync::Mutex::new(Vec::new()));
            let captured_requests = Arc::clone(&requests);
            let counted_accepts = Arc::clone(&accepts);
            let counted_disconnects = Arc::clone(&disconnects);
            #[cfg(windows)]
            let (socket, task) = {
                let socket = format!(r"\\.\pipe\optimus-message-persistent-{}", uuid::Uuid::new_v4());
                let server = tokio::net::windows::named_pipe::ServerOptions::new()
                    .first_pipe_instance(true)
                    .create(&socket)
                    .unwrap();
                let socket_for_task = socket.clone();
                let task = tokio::spawn(async move {
                    let mut server = server;
                    let mut connection_index = 0usize;
                    loop {
                        if server.connect().await.is_err() {
                            return;
                        }
                        counted_accepts.fetch_add(1, Ordering::SeqCst);
                        let handler = Arc::clone(&handler);
                        let requests = Arc::clone(&captured_requests);
                        let disconnects = Arc::clone(&counted_disconnects);
                        let connected = std::mem::replace(
                            &mut server,
                            match tokio::net::windows::named_pipe::ServerOptions::new()
                                .create(&socket_for_task)
                            {
                                Ok(next) => next,
                                Err(_) => return,
                            },
                        );
                        tokio::spawn(async move {
                            serve_persistent_connection(
                                connected,
                                connection_index,
                                handler,
                                requests,
                                disconnects,
                            )
                            .await;
                        });
                        connection_index += 1;
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
                let counted_accepts = Arc::clone(&accepts);
                let counted_disconnects = Arc::clone(&disconnects);
                let task = tokio::spawn(async move {
                    let mut connection_index = 0usize;
                    loop {
                        let Ok((stream, _)) = listener.accept().await else {
                            return;
                        };
                        counted_accepts.fetch_add(1, Ordering::SeqCst);
                        let handler = Arc::clone(&handler);
                        let requests = Arc::clone(&captured_requests);
                        let disconnects = Arc::clone(&counted_disconnects);
                        tokio::spawn(async move {
                            serve_persistent_connection(
                                stream,
                                connection_index,
                                handler,
                                requests,
                                disconnects,
                            )
                            .await;
                        });
                        connection_index += 1;
                    }
                });
                (socket, task)
            };
            Self { socket, accepts, disconnects, requests, task, _root: root }
        }
    }

    fn always_deliver() -> RequestHandler {
        Arc::new(|_, _, _| ServeDecision::Respond(receipt(DELIVERY_STATUS_DELIVERED)))
    }

    #[tokio::test]
    async fn messaging_safety_persistent_connection_is_reused_across_sends() {
        let server = MultiConnectionSupervisor::new(always_deliver());
        for index in 0..5 {
            send_message(
                &server.socket,
                "source-active",
                "target-active",
                &format!("message {index}"),
                &AtomicBool::new(false),
            )
            .await
            .unwrap();
        }
        assert_eq!(server.accepts.load(Ordering::SeqCst), 1, "one connection must serve all sends");
        let bodies = send_message_bodies(&server.requests.lock().unwrap());
        let messages: Vec<&str> = bodies
            .iter()
            .filter_map(|command| command.get("message").and_then(Value::as_str))
            .collect();
        assert_eq!(messages, ["message 0", "message 1", "message 2", "message 3", "message 4"]);
    }

    #[tokio::test]
    async fn messaging_safety_persistent_connection_reconnects_after_server_close() {
        let handler: RequestHandler = Arc::new(|connection, _index, _command| {
            if connection == 0 {
                ServeDecision::RespondAndClose(receipt(DELIVERY_STATUS_DELIVERED))
            } else {
                ServeDecision::Respond(receipt(DELIVERY_STATUS_DELIVERED))
            }
        });
        let server = MultiConnectionSupervisor::new(handler);
        send_message(&server.socket, "source-active", "target-active", "first", &AtomicBool::new(false))
            .await
            .unwrap();
        wait_until(2_000, || server.disconnects.load(Ordering::SeqCst) >= 1).await;
        // Let the worker-side reader observe the closed pipe before the next send.
        tokio::time::sleep(Duration::from_millis(100)).await;
        send_message(&server.socket, "source-active", "target-active", "second", &AtomicBool::new(false))
            .await
            .unwrap();
        assert_eq!(server.accepts.load(Ordering::SeqCst), 2);
        let bodies = send_message_bodies(&server.requests.lock().unwrap());
        let messages: Vec<&str> = bodies
            .iter()
            .filter_map(|command| command.get("message").and_then(Value::as_str))
            .collect();
        assert_eq!(messages, ["first", "second"]);
    }

    #[tokio::test]
    async fn messaging_safety_persistent_connection_never_replays_a_lost_response() {
        let handler: RequestHandler = Arc::new(|connection, _index, command| {
            let is_send = command.get("type").and_then(Value::as_str) == Some("send_message");
            if connection == 0 && is_send {
                // Accept the message, then lose the response and the connection.
                ServeDecision::MuteAndClose
            } else {
                ServeDecision::Respond(receipt(DELIVERY_STATUS_DELIVERED))
            }
        });
        let server = MultiConnectionSupervisor::new(handler);
        let first = tokio::time::timeout(
            Duration::from_secs(5),
            send_message(&server.socket, "source-active", "target-active", "first", &AtomicBool::new(false)),
        )
        .await
        .expect("lost response must fail fast");
        assert!(first.is_err(), "a lost response is an error, never a success");
        wait_until(2_000, || server.disconnects.load(Ordering::SeqCst) >= 1).await;
        tokio::time::sleep(Duration::from_millis(100)).await;
        let second = send_message(
            &server.socket,
            "source-active",
            "target-active",
            "second",
            &AtomicBool::new(false),
        )
        .await
        .unwrap();
        assert_eq!(second.delivery_status, DELIVERY_STATUS_DELIVERED);
        let bodies = send_message_bodies(&server.requests.lock().unwrap());
        let messages: Vec<&str> = bodies
            .iter()
            .filter_map(|command| command.get("message").and_then(Value::as_str))
            .collect();
        assert_eq!(messages, ["first", "second"], "the lost message must not be replayed");
    }

    #[tokio::test]
    async fn messaging_safety_shutdown_closes_cached_connection_and_never_reconnects() {
        let server = MultiConnectionSupervisor::new(always_deliver());
        let shutting_down = AtomicBool::new(false);
        send_message(&server.socket, "source-active", "target-active", "first", &shutting_down)
            .await
            .unwrap();
        shutting_down.store(true, Ordering::SeqCst);
        let error = send_message(&server.socket, "source-active", "target-active", "second", &shutting_down)
            .await
            .unwrap_err();
        assert_eq!(error, "Daemon is shutting down");
        let bodies = send_message_bodies(&server.requests.lock().unwrap());
        let messages: Vec<&str> = bodies
            .iter()
            .filter_map(|command| command.get("message").and_then(Value::as_str))
            .collect();
        assert_eq!(messages, ["first"]);
        wait_until(2_000, || server.disconnects.load(Ordering::SeqCst) >= 1).await;
        assert_eq!(server.disconnects.load(Ordering::SeqCst), 1, "the cached pipe must be released");
    }

    #[tokio::test]
    async fn messaging_safety_concurrent_senders_share_one_connection() {
        let server = MultiConnectionSupervisor::new(always_deliver());
        let socket = server.socket.clone();
        let mut tasks = Vec::new();
        for task_index in 0..4 {
            let socket = socket.clone();
            tasks.push(tokio::spawn(async move {
                for message_index in 0..5 {
                    send_message(
                        &socket,
                        "source-active",
                        "target-active",
                        &format!("task {task_index} message {message_index}"),
                        &AtomicBool::new(false),
                    )
                    .await
                    .expect("concurrent send must succeed");
                }
            }));
        }
        for task in tasks {
            task.await.unwrap();
        }
        assert_eq!(server.accepts.load(Ordering::SeqCst), 1);
        assert_eq!(send_message_bodies(&server.requests.lock().unwrap()).len(), 20);
    }

    #[tokio::test]
    async fn messaging_safety_idle_eviction_releases_the_pipe() {
        let handler: RequestHandler = Arc::new(|_, _, command| {
            if command.get("type").and_then(Value::as_str) == Some("list_agent_peers") {
                ServeDecision::Respond(json!({"success": true, "data": {"peers": []}}))
            } else {
                ServeDecision::Respond(receipt(DELIVERY_STATUS_DELIVERED))
            }
        });
        let server = MultiConnectionSupervisor::new(handler);
        let manager = AgentMessageConnectionManager::with_idle_eviction_ms(150);
        let command = |token: &str| {
            json!({"type": "list_agent_peers", "workerToken": token})
                .as_object()
                .expect("command object")
                .clone()
        };
        let first = manager
            .request(&server.socket, command("token"), 5_000, ConnectRetry::Once, None)
            .await
            .unwrap();
        assert!(first.success);
        assert_eq!(server.accepts.load(Ordering::SeqCst), 1);
        // The janitor must close the idle connection and free the pipe.
        wait_until(5_000, || server.disconnects.load(Ordering::SeqCst) >= 1).await;
        assert_eq!(server.disconnects.load(Ordering::SeqCst), 1);
        let second = manager
            .request(&server.socket, command("token"), 5_000, ConnectRetry::Once, None)
            .await
            .unwrap();
        assert!(second.success);
        assert_eq!(server.accepts.load(Ordering::SeqCst), 2, "a new connection must be established");
    }

    #[tokio::test]
    async fn messaging_safety_idle_deadline_during_in_flight_request_still_evicts() {
        // A delayed response keeps the request in flight exactly when the idle
        // janitor reaches its eviction deadline. The janitor must re-arm and
        // still release the pipe once the request completes.
        let handler: RequestHandler = Arc::new(|_, _, command| {
            if command.get("type").and_then(Value::as_str) == Some("list_agent_peers") {
                ServeDecision::DelayedRespond(json!({"success": true, "data": {"peers": []}}), 400)
            } else {
                ServeDecision::Respond(receipt(DELIVERY_STATUS_DELIVERED))
            }
        });
        let server = MultiConnectionSupervisor::new(handler);
        let manager = AgentMessageConnectionManager::with_idle_eviction_ms(100);
        let command = |token: &str| {
            json!({"type": "list_agent_peers", "workerToken": token})
                .as_object()
                .expect("command object")
                .clone()
        };
        let started = std::time::Instant::now();
        let first = manager
            .request(&server.socket, command("token"), 5_000, ConnectRetry::Once, None)
            .await
            .unwrap();
        assert!(first.success);
        assert!(
            started.elapsed() >= Duration::from_millis(400),
            "the response must stay in flight past the 100ms idle budget"
        );
        wait_until(5_000, || server.disconnects.load(Ordering::SeqCst) >= 1).await;
        assert_eq!(
            server.disconnects.load(Ordering::SeqCst),
            1,
            "the re-armed janitor must release the pipe"
        );
        let second = manager
            .request(&server.socket, command("token"), 5_000, ConnectRetry::Once, None)
            .await
            .unwrap();
        assert!(second.success);
        assert_eq!(server.accepts.load(Ordering::SeqCst), 2, "a new connection must be established");
    }

    #[tokio::test]
    async fn messaging_safety_old_supervisor_degrades_on_every_call_without_requests() {
        let server = ScriptedSupervisor::new(22, None);
        assert!(list_peers(&server.socket, "private-worker-token").await.is_empty());
        assert!(list_peers(&server.socket, "private-worker-token").await.is_empty());
        assert!(server.requests.lock().unwrap().is_empty());
    }

}
