//! OCC-2 negotiation end to end over the real daemon (audit W3, T1/T2).
//!
//! This is the flagship wire-level test for PR #166's negotiation seam:
//! a real model turn drives a real Python kernel, the kernel issues
//! `agent_message.send` as a host request, and the send seam (S1,
//! `occ2_target_send_policy` in `subagent_runs.rs`) negotiates `sym-v1`,
//! wraps the OCC-1 body into the dual OCC-2 wire format and delivers it
//! through the live daemon to a sibling session. The receiving session
//! decodes the symbolic section and records the delivery kind in its
//! durable transcript.
//!
//! Covered behaviors:
//! * negotiated send: delivered `details.message` carries the
//!   `OCC/2 D=sym-v1@1 C=...` header, the `#FALLBACK-OCC1` sentinel and
//!   the untouched OCC-1 section; `occ2Delivery.kind` is `symbolic`;
//!   the receiver decodes (no strike ledger entry is written for the
//!   speaker/listener pair).
//! * opted-out peer: after the listener's persisted preference is set to
//!   OCC-1 (`session_settings::save(..., false)`), the next send is
//!   byte-identical OCC-1 with kind `occ1_native`.
//! * restart recoverability: after a daemon shutdown and restart, both
//!   sessions are reopened from their files; the persisted preference
//!   still forces OCC-1, and re-enabling it renegotiates `sym-v1`.
//!
//! The Python kernel is the only external process that is not a fixture:
//! it is resolved from `PRIME_AGENT_KERNEL_PYTHON` (a python with a
//! current prime-agent-runtime and the bundled python skills importable).
//! Without that variable the test skips: the offline bootstrap cannot
//! create a kernel venv without network access.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use pi_coding_agent::core::agent_messages::occ2_strike_ledger_path;
use pi_coding_agent::core::occ::contract::{AgentCommMessage, Finding};
use pi_coding_agent::core::occ::session_settings;
use pi_coding_agent::modes::daemon::daemon_client::{DaemonClient, DaemonClientRequestOptions};
use pi_coding_agent::modes::daemon::daemon_protocol::daemon_protocol_info;
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Local model fixture (offline loopback HTTP; adapted from tests/native_cli.rs,
// whose helpers are private to that test target).
// ---------------------------------------------------------------------------

struct LocalModel {
    address: std::net::SocketAddr,
    requests: Arc<Mutex<Vec<Value>>>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<Result<(), String>>>,
}

impl LocalModel {
    fn with_response(respond: fn(&Value) -> (Value, &'static str)) -> Self {
        let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let stopped = stop.clone();
        let received = requests.clone();
        let worker = thread::spawn(move || {
            while !stopped.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((stream, peer)) => {
                        if !peer.ip().is_loopback() {
                            return Err("Non-loopback model client".into());
                        }
                        // Windows accept() makes the accepted socket inherit the
                        // listener's non-blocking mode (Unix does not); the fixture
                        // is a blocking HTTP server.
                        stream.set_nonblocking(false).unwrap();
                        let body = serve_completion(stream, respond)?;
                        received.lock().unwrap().push(body);
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5))
                    }
                    Err(error) => return Err(error.to_string()),
                }
            }
            Ok(())
        });
        Self {
            address,
            requests,
            stop,
            worker: Some(worker),
        }
    }

    fn finish(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            worker.join().unwrap().unwrap();
        }
    }
}

impl Drop for LocalModel {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn serve_completion(mut stream: TcpStream, respond: fn(&Value) -> (Value, &'static str)) -> Result<Value, String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| error.to_string())?;
    let mut bytes = Vec::new();
    let header_end = loop {
        if let Some(end) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            break end + 4;
        }
        if bytes.len() >= 64 * 1024 {
            return Err("HTTP headers exceed fixture limit".into());
        }
        read_more(&mut stream, &mut bytes)?;
    };
    let headers = std::str::from_utf8(&bytes[..header_end]).map_err(|error| error.to_string())?;
    if !headers.starts_with("POST /v1/chat/completions HTTP/1.1\r\n") {
        return Err(format!(
            "Unexpected request: {}",
            headers.lines().next().unwrap_or_default()
        ));
    }
    let content_length = headers
        .lines()
        .filter_map(|line| line.split_once(':'))
        .find(|(key, _)| key.eq_ignore_ascii_case("content-length"))
        .and_then(|(_, value)| value.trim().parse::<usize>().ok())
        .ok_or_else(|| "Missing HTTP content length".to_string())?;
    if content_length > 1024 * 1024 {
        return Err("HTTP body exceeds fixture limit".into());
    }
    while bytes.len() < header_end + content_length {
        read_more(&mut stream, &mut bytes)?;
    }
    let body: Value = serde_json::from_slice(&bytes[header_end..header_end + content_length])
        .map_err(|error| error.to_string())?;
    if body["model"] != "fixture-model" || body["stream"] != true {
        return Err(format!("Unexpected completion body: {body}"));
    }
    let (delta, finish) = respond(&body);
    let text = json!({"id":"fixture-1","object":"chat.completion.chunk","created":1,"model":"fixture-model","choices":[{"index":0,"delta":delta,"finish_reason":null}]});
    let end = json!({"id":"fixture-1","object":"chat.completion.chunk","created":1,"model":"fixture-model","choices":[{"index":0,"delta":{},"finish_reason":finish}],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}});
    let response = format!("data: {text}\n\ndata: {end}\n\ndata: [DONE]\n\n");
    write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{response}", response.len())
        .map_err(|error| error.to_string())?;
    Ok(body)
}

fn read_more(stream: &mut TcpStream, bytes: &mut Vec<u8>) -> Result<(), String> {
    let mut buffer = [0u8; 4096];
    let count = stream
        .read(&mut buffer)
        .map_err(|error| error.to_string())?;
    if count == 0 {
        return Err("HTTP client closed before complete request".into());
    }
    bytes.extend_from_slice(&buffer[..count]);
    Ok(())
}

// ---------------------------------------------------------------------------
// Private CLI/daemon fixture (adapted from tests/native_cli.rs).
// ---------------------------------------------------------------------------

struct PrivateCli {
    root: tempfile::TempDir,
    agent_dir: PathBuf,
    workspace: PathBuf,
    socket: String,
    kernel_python: Option<String>,
}

impl PrivateCli {
    fn new(base_url: &str, kernel_python: Option<String>) -> Self {
        // Keep the fixture independent of checkout depth: the daemon also puts
        // worker sockets under this root's tmp directory.
        let scratch = std::env::temp_dir().canonicalize().unwrap();
        let root = tempfile::Builder::new()
            .prefix("occ2-e2e-")
            .tempdir_in(scratch)
            .unwrap();
        let agent_dir = root.path().join("agent");
        let workspace = root.path().join("workspace");
        for directory in [
            &agent_dir,
            &workspace,
            &root.path().join("home"),
            &root.path().join("sessions"),
            &root.path().join("tmp"),
        ] {
            fs::create_dir_all(directory).unwrap();
        }
        fs::write(agent_dir.join("settings.json"), serde_json::to_vec(&json!({
            "defaultProvider":"local-cli-fixture", "defaultModel":"fixture-model", "defaultThinkingLevel":"off",
            "telemetry":{"enabled":false}, "autoCompaction":{"enabled":false}, "quietStartup":true,
        })).unwrap()).unwrap();
        // This literal is fixture data, never a credential or environment lookup.
        fs::write(agent_dir.join("models.json"), serde_json::to_vec(&json!({"providers":{"local-cli-fixture":{
            "baseUrl":base_url, "apiKey":"local-fixture-not-a-secret", "api":"openai-completions",
            "models":[{"id":"fixture-model","name":"Local fixture","reasoning":false,"input":["text"],"contextWindow":32768,"maxTokens":128,
                "cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0}}]
        }}})).unwrap()).unwrap();
        let socket = if cfg!(windows) {
            format!(r"\\.\pipe\optimus-occ2-e2e-{}", uuid::Uuid::new_v4())
        } else {
            root.path()
                .join("daemon.sock")
                .to_string_lossy()
                .into_owned()
        };
        Self {
            root,
            agent_dir,
            workspace,
            socket,
            kernel_python,
        }
    }

    fn command(&self) -> Command {
        let binary = std::env::var_os("OPTIMUS_TEST_BINARY")
            .unwrap_or_else(|| env!("CARGO_BIN_EXE_optimus-rust").into());
        let mut command = Command::new(binary);
        command
            .env_clear()
            .current_dir(&self.workspace)
            .stdin(Stdio::null())
            .env("HOME", self.root.path().join("home"))
            .env("USERPROFILE", self.root.path().join("home"))
            .env("TMPDIR", self.root.path().join("tmp"))
            .env("TMP", self.root.path().join("tmp"))
            .env("TEMP", self.root.path().join("tmp"))
            .env("XDG_CONFIG_HOME", self.root.path().join("home/config"))
            .env("XDG_CACHE_HOME", self.root.path().join("home/cache"))
            .env("PRIME_AGENT_CODING_AGENT_DIR", &self.agent_dir)
            .env("PRIME_AGENT_SESSION_DIR", self.root.path().join("sessions"))
            .env("PI_OFFLINE", "1")
            .env("NO_PROXY", "127.0.0.1,localhost")
            .env("TERM", "dumb");
        // The kernel bootstrap reads this in the daemon process; without it the
        // offline bootstrap cannot build a venv and the test would not be e2e.
        if let Some(python) = &self.kernel_python {
            command.env("PRIME_AGENT_KERNEL_PYTHON", python);
        }
        if let Some(path) = std::env::var_os("PATH") {
            command.env("PATH", path);
        }
        if let Some(system_root) = std::env::var_os("SystemRoot") {
            command.env("SystemRoot", system_root);
        }
        command
    }

    fn spawn(&self, label: &str, args: &[&str]) -> OwnedChild {
        let stderr = self.root.path().join(format!("{label}.stderr"));
        let mut command = self.command();
        command
            .args(args)
            .args(["--daemon-socket", &self.socket])
            .stdout(Stdio::null())
            .stderr(File::create(&stderr).unwrap());
        OwnedChild {
            child: command.spawn().expect("launch real optimus-rust"),
            stderr,
        }
    }
}

struct OwnedChild {
    child: Child,
    stderr: PathBuf,
}

impl Drop for OwnedChild {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

struct OwnedDaemon {
    process: OwnedChild,
    socket: String,
}

impl Drop for OwnedDaemon {
    fn drop(&mut self) {
        if self.process.child.try_wait().ok().flatten().is_some() {
            return;
        }
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let client = DaemonClient::create(&self.socket);
            if client.connect(300).await.is_ok() {
                let _ = client
                    .request(
                        json!({"type":"shutdown"}).as_object().unwrap().clone(),
                        Some(1000),
                        DaemonClientRequestOptions::default(),
                    )
                    .await;
                client.close().await;
            }
        });
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if self.process.child.try_wait().ok().flatten().is_some() {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        // OwnedChild supplies the bounded fallback if protocol shutdown failed.
    }
}

fn wait_for_daemon(daemon: &mut OwnedDaemon) {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        assert!(
            daemon.process.child.try_wait().unwrap().is_none(),
            "daemon exited: {}",
            fs::read_to_string(&daemon.process.stderr).unwrap()
        );
        let ready = runtime.block_on(async {
            let client = DaemonClient::create(&daemon.socket);
            let result = if client.connect(200).await.is_ok() {
                client.wait_for_hello(200).await.ok()
            } else {
                None
            };
            client.close().await;
            result
        });
        if let Some(hello) = ready {
            assert_eq!(hello.protocol, daemon_protocol_info());
            return;
        }
        assert!(
            Instant::now() < deadline,
            "daemon handshake timed out: {}",
            fs::read_to_string(&daemon.process.stderr).unwrap()
        );
        thread::sleep(Duration::from_millis(25));
    }
}

/// Stops a daemon through the protocol shutdown command so a restart on the
/// same socket observes a clean exit (the OwnedDaemon drop is then a no-op).
fn stop_daemon(daemon: &mut OwnedDaemon) {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let client = DaemonClient::create(&daemon.socket);
        if client.connect(300).await.is_ok() {
            let _ = client
                .request(
                    json!({"type":"shutdown"}).as_object().unwrap().clone(),
                    Some(1000),
                    DaemonClientRequestOptions::default(),
                )
                .await;
            client.close().await;
        }
    });
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if daemon.process.child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(10));
    }
    let _ = daemon.process.child.kill();
    let _ = daemon.process.child.wait();
}

// ---------------------------------------------------------------------------
// OCC-1 report body and the scripted model responder.
// ---------------------------------------------------------------------------

/// A >floor OCC-1 contract body (the small-message floor is 220 chars):
/// the shape a real negotiated send wraps into the dual OCC-2 payload.
fn report_body() -> String {
    let mut message = AgentCommMessage::new("RESULT", "occ2-e2e", "done");
    message.findings.push(Finding {
        id: "F1".to_string(),
        statement: "negotiated wire round-trip".repeat(16),
        confidence: Some(0.9),
        evidence: Vec::new(),
    });
    message
        .to_occ1()
        .expect("contract body encodes")
        .encode()
        .expect("OCC-1 body encodes")
}

/// The Python the model runs in the sender's kernel: a real
/// `agent_message.send` host request to the named sibling.
fn ipython_send_code() -> String {
    format!(
        "BODY = '''{body}'''\nimport importlib\n_am = globals().get('agent_message') or importlib.import_module('agent_message')\n_receipt = await _am.send(BODY, receiver_role='sibling', receiver_name='occ-target')\nprint('OCC2_E2E_SENT', _receipt.get('id'))\n",
        body = report_body()
    )
}

/// The scripted model: on a fresh sender trigger turn it calls the ipython
/// tool with the agent_message send; after the tool result (or for the
/// target session's own turns, which never carry the trigger marker) it
/// replies with plain text and stops.
fn occ2_e2e_response(body: &Value) -> (Value, &'static str) {
    let messages = body["messages"].as_array().unwrap();
    let last_user = messages
        .iter()
        .rposition(|message| message["role"] == "user")
        .expect("completion request carries a user message");
    let trigger = messages[last_user]["content"].to_string();
    let tool_results = messages[last_user..]
        .iter()
        .filter(|message| message["role"] == "tool")
        .count();
    if !trigger.contains("OCC2_E2E_SEND") || tool_results > 0 {
        return (json!({"role":"assistant","content":"OCC2_E2E_ACK"}), "stop");
    }
    (
        json!({"role":"assistant","tool_calls":[{
            "index":0,
            "id":format!("occ2-e2e-{}", uuid::Uuid::new_v4()),
            "type":"function",
            "function":{"name":"ipython","arguments":json!({"code": ipython_send_code()}).to_string()}
        }]}),
        "tool_calls",
    )
}

// ---------------------------------------------------------------------------
// Shared test helpers.
// ---------------------------------------------------------------------------

fn kernel_python() -> Option<String> {
    let value = std::env::var_os("PRIME_AGENT_KERNEL_PYTHON")?;
    let value = value.to_string_lossy().into_owned();
    if value.trim().is_empty() || !Path::new(&value).is_file() {
        return None;
    }
    Some(value)
}

async fn request(client: &Arc<DaemonClient>, body: Value) -> Value {
    let response = client
        .request(
            body.as_object().unwrap().clone(),
            Some(120_000),
            DaemonClientRequestOptions::default(),
        )
        .await
        .unwrap();
    assert!(response.success, "{:?}", response.error);
    response.data.unwrap_or(Value::Null)
}

fn session_rows(session_file: &str) -> Vec<Value> {
    fs::read_to_string(session_file)
        .unwrap()
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

/// Delivered agent-message details in one session file, in delivery order.
fn delivered_rows(rows: &[Value]) -> Vec<Value> {
    rows.iter()
        .filter(|row| row["message"]["customType"] == "agent_message")
        .map(|row| row["message"]["details"].clone())
        .collect()
}

/// Waits until the target session holds more delivered agent messages than
/// `before`, then returns the newly delivered details.
async fn wait_for_new_delivery(target_file: &str, before: usize) -> Value {
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        let delivered = delivered_rows(&session_rows(target_file));
        if delivered.len() > before {
            return delivered.last().unwrap().clone();
        }
        assert!(
            Instant::now() < deadline,
            "delivery missing from {target_file}: {delivered:?}"
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

/// Asserts one delivered row is the negotiated dual OCC-2 payload and was
/// consumed through the symbolic section.
fn assert_symbolic_delivery(delivered: &Value) {
    let message = delivered["message"].as_str().unwrap();
    assert!(
        message.starts_with("OCC/2 D=sym-v1@1 C="),
        "negotiated body must carry the OCC-2 header: {message:?}"
    );
    assert!(
        message.contains("\n#FALLBACK-OCC1\n"),
        "dual payload must embed the fallback sentinel: {message:?}"
    );
    assert!(
        message.ends_with(&report_body()),
        "the embedded OCC-1 section must be the original body: {message:?}"
    );
    assert_eq!(
        delivered["occ2Delivery"]["kind"], "symbolic",
        "the receiver must decode the symbolic section: {delivered:?}"
    );
    assert_eq!(delivered["fromRelationship"], "sibling");
}

/// Asserts one delivered row is byte-identical OCC-1 with no OCC-2 attempt.
fn assert_occ1_delivery(delivered: &Value) {
    assert_eq!(
        delivered["message"].as_str().unwrap(),
        report_body(),
        "an opted-out peer must receive the unmodified OCC-1 body"
    );
    assert_eq!(
        delivered["occ2Delivery"]["kind"], "occ1_native",
        "no OCC/2 header may be involved: {delivered:?}"
    );
}

/// Sends one report from the sender session and waits for the delivery.
async fn send_report_and_await_delivery(
    client: &Arc<DaemonClient>,
    sender: &str,
    target_file: &str,
) -> Value {
    let before = delivered_rows(&session_rows(target_file)).len();
    request(
        client,
        json!({"type":"prompt_and_wait","activeSessionId":sender,
            "message":format!("OCC2_E2E_SEND_{}: deliver the report to your sibling occ-target.", uuid::Uuid::new_v4().simple())}),
    )
    .await;
    wait_for_new_delivery(target_file, before).await
}

// ---------------------------------------------------------------------------
// T1: negotiated send encodes, delivers, decodes and records — then the
// opted-out peer falls back to OCC-1 in the same daemon lifetime.
// ---------------------------------------------------------------------------

#[test]
fn occ2_e2e_negotiated_send_encodes_decodes_and_records() {
    let Some(kernel_python) = kernel_python() else {
        eprintln!(
            "skipping: PRIME_AGENT_KERNEL_PYTHON is not set to a python with a current prime-agent-runtime; \
             the offline bootstrap cannot build a kernel venv"
        );
        return;
    };
    assert!(
        report_body().chars().count() > 220,
        "the report body must clear the 220-char small-message floor"
    );
    let mut model = LocalModel::with_response(occ2_e2e_response);
    let fixture = PrivateCli::new(&format!("http://{}/v1", model.address), Some(kernel_python));
    let mut daemon = OwnedDaemon {
        process: fixture.spawn("occ2-e2e-daemon", &["--mode", "daemon", "--offline"]),
        socket: fixture.socket.clone(),
    };
    wait_for_daemon(&mut daemon);
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let client = Arc::new(DaemonClient::create(&fixture.socket));
        client.connect(1000).await.unwrap();
        let hello = client.wait_for_hello(1000).await.unwrap();
        assert!(hello.supports("agent_communication_activity"));
        assert!(hello.supports("occ_session_preferences"));
        let create_config = json!({
            "cwd": fixture.workspace, "agentDir": fixture.agent_dir,
            "sessionDir": fixture.root.path().join("sessions"),
            "provider":"local-cli-fixture", "model":"fixture-model", "noExtensions":true
        });
        let created = request(&client, json!({"type":"create", "config":create_config})).await;
        let sender = created["activeSessionId"].as_str().unwrap().to_string();
        let created = request(&client, json!({"type":"create", "config":create_config})).await;
        let target = created["activeSessionId"].as_str().unwrap().to_string();
        request(&client, json!({"type":"rename","activeSessionId":target,"name":"occ-target"})).await;
        let sender_file = request(&client, json!({"type":"get_connection_state","activeSessionId":sender}))
            .await["sessionFile"].as_str().unwrap().to_string();
        let target_file = request(&client, json!({"type":"get_connection_state","activeSessionId":target}))
            .await["sessionFile"].as_str().unwrap().to_string();
        assert!(session_settings::load(&sender_file).unwrap(), "file-backed sessions default to OCC-2 on");
        assert!(session_settings::load(&target_file).unwrap());

        // Negotiated send: model turn -> ipython kernel -> agent_message.send
        // host request -> S1 policy -> dual OCC-2 wire -> daemon delivery.
        let delivered = send_report_and_await_delivery(&client, &sender, &target_file).await;
        assert_symbolic_delivery(&delivered);
        let sender_rows = session_rows(&sender_file);
        assert!(
            sender_rows
                .iter()
                .any(|row| row["message"]["role"] == "toolResult"
                    && row["message"]["isError"] == false
                    && row["message"].to_string().contains("OCC2_E2E_SENT")),
            "the kernel send must succeed and print its marker: {sender_rows:?}"
        );
        // A successful symbolic decode never writes a strike for the pair.
        assert!(
            !occ2_strike_ledger_path(&sender_file).exists(),
            "no strike ledger may be created for a clean symbolic delivery"
        );
        // The sender's model window really offered the kernel tool the turn used.
        {
            let requests = model.requests.lock().unwrap();
            assert!(
                requests.iter().any(|request| request["tools"]
                    .as_array()
                    .is_some_and(|tools| tools.iter().any(|tool| tool["function"]["name"] == "ipython"))),
                "the sender session must expose the ipython tool: {requests:?}"
            );
        }
        // The listener's roster row records the delivery as symbolic.
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            let roster = request(&client, json!({"type":"roster_subscribe"})).await;
            let symbolic = roster["roster"].as_array().unwrap().iter().any(|row| {
                row["summary"]["activeSessionId"] == target.as_str()
                    && row["summary"]["communicationActivity"]["deliveries"]
                        .as_array()
                        .is_some_and(|deliveries| {
                            deliveries
                                .iter()
                                .any(|delivery| delivery["kind"] == "symbolic")
                        })
            });
            if symbolic {
                break;
            }
            assert!(Instant::now() < deadline, "roster missing the symbolic delivery: {roster}");
            tokio::time::sleep(Duration::from_millis(100)).await;
        }

        // Opted-out peer: the persisted listener preference forces OCC-1.
        session_settings::save(&target_file, false).unwrap();
        assert!(!session_settings::load(&target_file).unwrap());
        let delivered = send_report_and_await_delivery(&client, &sender, &target_file).await;
        assert_occ1_delivery(&delivered);

        client.close().await;
    });
    drop(daemon);
    model.finish();
}

// ---------------------------------------------------------------------------
// T2: the negotiation inputs are durable — a daemon restart preserves the
// persisted preference, and re-enabling it renegotiates sym-v1.
// ---------------------------------------------------------------------------

#[test]
fn occ2_e2e_negotiation_inputs_survive_daemon_restart() {
    let Some(kernel_python) = kernel_python() else {
        eprintln!(
            "skipping: PRIME_AGENT_KERNEL_PYTHON is not set to a python with a current prime-agent-runtime; \
             the offline bootstrap cannot build a kernel venv"
        );
        return;
    };
    let mut model = LocalModel::with_response(occ2_e2e_response);
    let fixture = PrivateCli::new(&format!("http://{}/v1", model.address), Some(kernel_python));
    let mut daemon = OwnedDaemon {
        process: fixture.spawn("occ2-e2e-restart-a", &["--mode", "daemon", "--offline"]),
        socket: fixture.socket.clone(),
    };
    wait_for_daemon(&mut daemon);
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let (sender_file, target_file) = runtime.block_on(async {
        let client = Arc::new(DaemonClient::create(&fixture.socket));
        client.connect(1000).await.unwrap();
        client.wait_for_hello(1000).await.unwrap();
        let create_config = json!({
            "cwd": fixture.workspace, "agentDir": fixture.agent_dir,
            "sessionDir": fixture.root.path().join("sessions"),
            "provider":"local-cli-fixture", "model":"fixture-model", "noExtensions":true
        });
        let created = request(&client, json!({"type":"create", "config":create_config})).await;
        let sender = created["activeSessionId"].as_str().unwrap().to_string();
        let created = request(&client, json!({"type":"create", "config":create_config})).await;
        let target = created["activeSessionId"].as_str().unwrap().to_string();
        request(&client, json!({"type":"rename","activeSessionId":target,"name":"occ-target"})).await;
        let sender_file = request(&client, json!({"type":"get_connection_state","activeSessionId":sender}))
            .await["sessionFile"].as_str().unwrap().to_string();
        let target_file = request(&client, json!({"type":"get_connection_state","activeSessionId":target}))
            .await["sessionFile"].as_str().unwrap().to_string();
        assert!(session_settings::load(&sender_file).unwrap());
        assert!(session_settings::load(&target_file).unwrap());

        // Before the restart: one negotiated, symbolic delivery.
        let delivered = send_report_and_await_delivery(&client, &sender, &target_file).await;
        assert_symbolic_delivery(&delivered);
        // Then opt the listener out: the next send is plain OCC-1.
        session_settings::save(&target_file, false).unwrap();
        let delivered = send_report_and_await_delivery(&client, &sender, &target_file).await;
        assert_occ1_delivery(&delivered);
        client.close().await;
        (sender_file, target_file)
    });
    stop_daemon(&mut daemon);
    drop(daemon);

    // Restart on the same socket and reopen both sessions from their files.
    let mut daemon = OwnedDaemon {
        process: fixture.spawn("occ2-e2e-restart-b", &["--mode", "daemon", "--offline"]),
        socket: fixture.socket.clone(),
    };
    wait_for_daemon(&mut daemon);
    runtime.block_on(async {
        let client = Arc::new(DaemonClient::create(&fixture.socket));
        client.connect(1000).await.unwrap();
        client.wait_for_hello(1000).await.unwrap();
        let create_config = json!({
            "cwd": fixture.workspace, "agentDir": fixture.agent_dir,
            "sessionDir": fixture.root.path().join("sessions"),
            "provider":"local-cli-fixture", "model":"fixture-model", "noExtensions":true
        });
        let created = request(&client, json!({"type":"create", "config":create_config, "sessionPath":sender_file})).await;
        let sender = created["activeSessionId"].as_str().unwrap().to_string();
        let created = request(&client, json!({"type":"create", "config":create_config, "sessionPath":target_file})).await;
        let target = created["activeSessionId"].as_str().unwrap().to_string();
        request(&client, json!({"type":"rename","activeSessionId":target,"name":"occ-target"})).await;
        assert!(
            !session_settings::load(&target_file).unwrap(),
            "the opt-out must persist across the daemon restart"
        );

        // The persisted preference still forces OCC-1 after the restart.
        let delivered = send_report_and_await_delivery(&client, &sender, &target_file).await;
        assert_occ1_delivery(&delivered);

        // Re-enabling the listener renegotiates sym-v1 on the next send.
        session_settings::save(&target_file, true).unwrap();
        let delivered = send_report_and_await_delivery(&client, &sender, &target_file).await;
        assert_symbolic_delivery(&delivered);
        client.close().await;
    });
    drop(daemon);
    model.finish();
}
