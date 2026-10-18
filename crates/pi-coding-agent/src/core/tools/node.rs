//! One lazy Node workspace per session. Cell cancellation resets only this workspace.
use super::ToolDefinition;
use crate::utils::child_process::{spawn_kernel, KernelProcess, SpawnOptions};
use pi_agent_core::types::{
    AgentToolResult, AgentToolUpdateCallback, ContentBlock, ToolExecutionMode,
};
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio_util::sync::CancellationToken;

const WORKER: &str = include_str!("node_worker.cjs");
const LIMIT: usize = 64 * 1024;
const STREAM_NOTICE: &str = "\n[Node output truncated at 64 KiB]\n";
const RESULT_NOTICE: &str = "\n[Node result truncated at 64 KiB]\n";
/// One protocol frame larger than this is a peer framing violation, never an exit.
const FRAME_CAP: usize = LIMIT * 8;
/// Worker marker for an error raised by work an earlier cell left running.
const NOTICE_EVENT: &str = "workspace-notice";
/// Notices are user-visible text, so each one is bounded and only a few are kept.
const NOTICE_LIMIT: usize = 2_000;
const MAX_NOTICES: usize = 16;
/// How long to wait for an exit status before reporting the pipe instead.
const EXIT_WAIT: Duration = Duration::from_secs(3);

#[derive(Default)]
pub struct NodeRuntime {
    process: tokio::sync::Mutex<Option<NodeProcess>>,
    shutdown: CancellationToken,
}

struct NodeProcess {
    child: KernelProcess,
    input: tokio::process::ChildStdin,
    output: BufReader<tokio::process::ChildStdout>,
    stderr: tokio::task::JoinHandle<()>,
}

impl NodeProcess {
    fn spawn(cwd: &str) -> Result<Self, String> {
        let mut child = spawn_kernel("node", &["--input-type=commonjs".into(), "-e".into(), WORKER.into()], SpawnOptions {
            cwd: Some(cwd.into()), detached: true, stdin_piped: true,
            capture_stdout: true, capture_stderr: true, ..Default::default()
        }).map_err(|e| format!("Cannot start Node: {e}. Install Node.js 22 or newer and make node available on the daemon's PATH."))?;
        let input = child.stdin.take().ok_or("Node stdin unavailable")?;
        let output = BufReader::new(child.stdout.take().ok_or("Node stdout unavailable")?);
        let mut stderr = child.stderr.take().ok_or("Node stderr unavailable")?;
        let stderr = tokio::spawn(async move {
            let _ = tokio::io::copy(&mut stderr, &mut tokio::io::sink()).await;
        });
        Ok(Self {
            child,
            input,
            output,
            stderr,
        })
    }

    /// One protocol line, bounded so a malformed or native write cannot grow
    /// the buffer without limit. A partial line means the peer closed its pipe.
    async fn read_frame(&mut self) -> std::io::Result<Vec<u8>> {
        let mut bytes = Vec::new();
        let mut limited = (&mut self.output).take(FRAME_CAP as u64);
        limited.read_until(b'\n', &mut bytes).await?;
        Ok(bytes)
    }

    /// One protocol frame. A line that never terminates inside the cap is a
    /// framing violation; a short read without a terminator means the workspace
    /// closed its output, and then the exit status - not the pipe - is the
    /// honest explanation.
    async fn message(&mut self) -> Result<Value, String> {
        // Take the frame first: the read borrows the pipe, the explanation below
        // borrows the process, so they must not overlap.
        let frame = self.read_frame().await;
        let bytes = match frame {
            Ok(bytes) => bytes,
            Err(error) => {
                return Err(self
                    .gone_message(&format!("its response pipe failed: {error}"))
                    .await)
            }
        };
        if bytes.last() == Some(&b'\n') {
            return serde_json::from_slice(&bytes)
                .map_err(|error| format!("Invalid Node workspace response: {error}"));
        }
        if bytes.len() >= FRAME_CAP {
            return Err(format!(
                "Node workspace returned an oversized response ({} bytes in one frame, cap {FRAME_CAP})",
                bytes.len()
            ));
        }
        Err(self
            .gone_message("its output pipe closed in the middle of a response")
            .await)
    }

    /// Names the exit when the root process has stopped, so an ordinary exit is
    /// never reported as a transport or framing accident. Only a peer that is
    /// still alive after `EXIT_WAIT` falls back to the I/O detail.
    async fn gone_message(&mut self, detail: &str) -> String {
        match tokio::time::timeout(EXIT_WAIT, self.child.wait()).await {
            Ok(Ok(status)) => match status.code() {
                Some(code) => format!("Node workspace exited with status {code}"),
                None => format!("Node workspace exited on a signal ({status})"),
            },
            Ok(Err(error)) => format!(
                "Node workspace exited or closed its output; its exit status could not be read: {error}"
            ),
            Err(_) => format!("Node workspace stopped answering: {detail}"),
        }
    }

    fn terminate(&mut self) {
        #[cfg(windows)]
        let _ = self.child.containment.terminate();
        #[cfg(not(windows))]
        if let Some(pid) = self.child.id() {
            crate::utils::shell::kill_process_tree(pid as i32);
            let _ = self.child.start_kill();
        }
    }

    async fn stop(&mut self) {
        self.terminate();
        #[cfg(windows)]
        let _ = self
            .child
            .containment
            .wait_settled(tokio::time::Instant::now() + Duration::from_secs(3))
            .await;
        let _ = tokio::time::timeout(Duration::from_secs(3), self.child.wait()).await;
        self.stderr.abort();
    }
}
impl Drop for NodeProcess {
    fn drop(&mut self) {
        self.terminate();
        self.stderr.abort();
    }
}

impl NodeRuntime {
    pub fn cancel(&self) {
        self.shutdown.cancel();
        if let Ok(mut guard) = self.process.try_lock() {
            if let Some(mut process) = guard.take() {
                process.terminate();
                if let Ok(handle) = tokio::runtime::Handle::try_current() {
                    handle.spawn(async move {
                        process.stop().await;
                    });
                }
            }
        }
    }
    pub async fn dispose(&self) {
        self.shutdown.cancel();
        // Bind first: holding the lock across `stop()` would stall any executor
        // waiting on the mutex for the whole teardown window.
        let process = self.process.lock().await.take();
        if let Some(mut process) = process {
            process.stop().await;
        }
    }

    pub async fn execute(
        &self,
        cwd: &str,
        code: &str,
        timeout: f64,
        signal: Option<CancellationToken>,
        on_update: Option<AgentToolUpdateCallback>,
    ) -> Result<AgentToolResult, String> {
        if !timeout.is_finite() || timeout <= 0.0 || timeout > 3600.0 {
            return Err(
                "Node timeout must be between 0 and 3600 seconds (exclusive of zero).".into(),
            );
        }
        let signal = signal.unwrap_or_default();
        let mut guard = tokio::select! {
            guard = self.process.lock() => guard,
            _ = signal.cancelled() => return Err("Node cell cancelled before execution".into()),
            _ = self.shutdown.cancelled() => return Err("Node workspace disposed".into()),
        };
        if signal.is_cancelled() || self.shutdown.is_cancelled() {
            return Err("Node cell cancelled before execution".into());
        }
        // Move the process out while executing. If the caller drops this future
        // (for example an agent-level abort), RAII kills the in-flight workspace
        // instead of leaving a busy process for the next cell to inherit.
        let fresh = guard.is_none();
        let mut process = match guard.take() {
            Some(process) => process,
            None => NodeProcess::spawn(cwd)?,
        };
        let mut output = String::new();
        let mut truncated = false;
        // Notices describe work an earlier cell left running, so they survive
        // until the next result instead of failing the cell that reads them.
        let mut notices: Vec<Value> = Vec::new();
        let work = async {
            if fresh {
                let hello = process.message().await?;
                if hello["ready"] != true {
                    return Err("Node workspace did not become ready".into());
                }
                let major = hello["version"]
                    .as_str()
                    .and_then(|v| v.split('.').next())
                    .and_then(|v| v.parse::<u32>().ok())
                    .unwrap_or(0);
                if major < 22 {
                    return Err("Node mode requires Node.js 22 or newer".into());
                }
            }
            let id = uuid::Uuid::new_v4().to_string();
            let request = format!("{}\n", json!({"id":id, "code":code}));
            process
                .input
                .write_all(request.as_bytes())
                .await
                .map_err(|e| e.to_string())?;
            process.input.flush().await.map_err(|e| e.to_string())?;
            let mut last_update = tokio::time::Instant::now();
            loop {
                let message = process.message().await?;
                if message["event"] == NOTICE_EVENT {
                    // Not this cell's failure: buffer it and keep reading until
                    // the frame that answers this request arrives.
                    push_notice(&mut notices, &message);
                    continue;
                }
                if message["id"] != id {
                    return Err("Unexpected Node workspace response".into());
                }
                if let Some(text) = message["output"].as_str() {
                    truncated |= append_bounded(&mut output, text);
                    if last_update.elapsed() >= Duration::from_millis(100) {
                        if let Some(update) = &on_update {
                            update(result(&output, false, &notices));
                        }
                        last_update = tokio::time::Instant::now();
                    }
                }
                if message["done"] == true {
                    if let Some(text) = message["result"].as_str().filter(|s| !s.is_empty()) {
                        if !output.is_empty() && !output.ends_with('\n') {
                            output.push('\n');
                        }
                        truncated |= append_bounded(&mut output, text);
                    }
                    append_notices(&mut output, &notices, &mut truncated);
                    note_truncation(&mut output, truncated);
                    return Ok(result(&output, message["error"] == true, &notices));
                }
            }
        };
        let outcome = tokio::select! {
            result = work => result,
            _ = tokio::time::sleep(Duration::from_secs_f64(timeout)) => Err(format!("Node cell timed out after {timeout} seconds")),
            _ = signal.cancelled() => Err("Node cell cancelled".into()),
            _ = self.shutdown.cancelled() => Err("Node workspace disposed".into()),
        };
        match outcome {
            Ok(result) => {
                *guard = Some(process);
                Ok(result)
            }
            Err(error) => {
                append_notices(&mut output, &notices, &mut truncated);
                note_truncation(&mut output, truncated);
                process.stop().await;
                Err(format!("{output}\n{error}. Node workspace reset; previous JavaScript variables are no longer available."))
            }
        }
    }
}

/// Appends `text` capped at `LIMIT` bytes on a char boundary, returning
/// whether any bytes of `text` were dropped.
fn append_bounded(output: &mut String, text: &str) -> bool {
    let remaining = LIMIT.saturating_sub(output.len());
    let mut end = text.len().min(remaining);
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    output.push_str(&text[..end]);
    end < text.len()
}

/// The Rust-side cap is silent on its own, so make it visible: one marker
/// after the capped text, unless the worker already announced its own.
fn note_truncation(output: &mut String, truncated: bool) {
    if truncated && !output.contains(STREAM_NOTICE.trim()) && !output.contains(RESULT_NOTICE.trim())
    {
        output.push_str(STREAM_NOTICE);
    }
}
/// Keeps one bounded, structured copy of a worker notice, so a noisy reporter
/// cannot grow either the buffer or the result without limit.
fn push_notice(notices: &mut Vec<Value>, message: &Value) {
    if notices.len() >= MAX_NOTICES {
        return;
    }
    notices.push(json!({
        "kind": message["kind"].as_str().unwrap_or("background"),
        "message": bounded_text(
            message["message"].as_str().unwrap_or("(no detail)"),
            NOTICE_LIMIT,
        ),
    }));
}

/// Renders buffered notices as a visible block. Each one is attributed to the
/// background work that raised it: the result that carries it is not marked as
/// a failure and the cell's own output stays first.
fn append_notices(output: &mut String, notices: &[Value], truncated: &mut bool) {
    for notice in notices {
        let label = match notice["kind"].as_str().unwrap_or("background error") {
            "uncaughtException" => "Uncaught exception",
            "unhandledRejection" => "Unhandled promise rejection",
            other => other,
        };
        let message = bounded_text(
            notice["message"].as_str().unwrap_or("(no detail)"),
            NOTICE_LIMIT,
        );
        let block = format!(
            "\n[Node background error] {label} in background work (a timer, listener, or floating promise) that outlived the cell which started it; this result is not a failure:\n{message}\n[Node workspace kept alive; JavaScript bindings are intact]\n"
        );
        if !output.is_empty() && !output.ends_with('\n') {
            output.push('\n');
        }
        *truncated |= append_bounded(output, &block);
    }
}

/// Bounds user-visible error text without splitting a character.
fn bounded_text(text: &str, limit: usize) -> String {
    let mut end = text.len().min(limit);
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    if end < text.len() {
        format!("{}...(truncated)", &text[..end])
    } else {
        text.to_string()
    }
}

fn result(output: &str, error: bool, notices: &[Value]) -> AgentToolResult {
    let mut details = json!({"language":"javascript"});
    if !notices.is_empty() {
        details["backgroundNotices"] = Value::Array(notices.to_vec());
    }
    let mut value = AgentToolResult::new(
        vec![ContentBlock::text(if output.is_empty() {
            "(no output)"
        } else {
            output
        })],
        details,
    );
    value.is_error = Some(error);
    value
}

pub fn create_node_tool_definition(cwd: &str, runtime: Arc<NodeRuntime>) -> ToolDefinition<Value> {
    let cwd = cwd.to_string();
    ToolDefinition {
        name: "node".into(), label: "Node".into(),
        description: "Execute JavaScript in a persistent Node.js workspace. Top-level await and bindings persist across cells. Use require() for modules or await nodeImport() for ESM. Prints console output and the final expression. Timeout/cancellation resets this workspace. Requires Node.js 22+.".into(),
        parameters: json!({"type":"object","properties":{
            "code":{"type":"string","description":"JavaScript source, without Markdown fences"},
            "timeout":{"type":"number","description":"Seconds, default 60, maximum 3600", "exclusiveMinimum":0, "maximum":3600}
        },"required":["code"]}),
        execution_mode: Some(ToolExecutionMode::Sequential),
        execute: Arc::new(move |_, args, signal, update, _| {
            let runtime = runtime.clone(); let cwd = cwd.clone();
            Box::pin(async move {
                let code = args["code"].as_str().ok_or_else(|| anyhow::anyhow!("code must be a string"))?;
                runtime.execute(&cwd, code, args["timeout"].as_f64().unwrap_or(60.0), signal, update).await.map_err(anyhow::Error::msg)
            })
        }),
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn append_bounded_keeps_the_limit_and_reports_drops() {
        let mut output = String::new();
        assert!(!append_bounded(&mut output, "hello"));
        assert_eq!(output, "hello");
        // Fill to the cap: an exact fit is not a drop.
        let fill = "x".repeat(LIMIT - output.len());
        assert!(!append_bounded(&mut output, &fill));
        assert_eq!(output.len(), LIMIT);
        // Anything past the cap is dropped and reported.
        assert!(append_bounded(&mut output, "more"));
        assert_eq!(output.len(), LIMIT);
    }

    #[test]
    fn append_bounded_backs_off_to_char_boundaries() {
        let mut output = String::new();
        let fill = "x".repeat(LIMIT - 1);
        assert!(!append_bounded(&mut output, &fill));
        // One byte of budget left: a multibyte character cannot fit and must
        // not be split into invalid UTF-8.
        assert!(append_bounded(&mut output, "é"));
        assert!(output.ends_with('x'));
        assert_eq!(output.len(), LIMIT - 1);
    }

    #[test]
    fn note_truncation_appends_one_marker_and_deduplicates_worker_notices() {
        let mut output = String::from("x".repeat(LIMIT));
        note_truncation(&mut output, true);
        assert!(output.ends_with(STREAM_NOTICE));
        // A second call must not stack another marker.
        note_truncation(&mut output, true);
        assert_eq!(output.matches(STREAM_NOTICE).count(), 1);
        // The worker already announced its own truncation: stay silent.
        let mut announced = format!("x{}", STREAM_NOTICE);
        note_truncation(&mut announced, true);
        assert_eq!(announced.matches(STREAM_NOTICE).count(), 1);
        let mut announced = format!("x{}", RESULT_NOTICE);
        note_truncation(&mut announced, true);
        assert_eq!(announced.matches(STREAM_NOTICE).count(), 0);
        assert_eq!(announced.matches(RESULT_NOTICE).count(), 1);
        // No drops: no notice at all.
        let mut clean = String::from("fine");
        note_truncation(&mut clean, false);
        assert_eq!(clean, "fine");
    }

    #[test]
    fn background_notices_are_bounded_and_never_fail_the_cell() {
        let frame = json!({
            "event": NOTICE_EVENT,
            "kind": "uncaughtException",
            "message": "Error: late boom\n    at Timeout._onTimeout (<anonymous>:1:52)",
        });
        let mut notices = Vec::new();
        push_notice(&mut notices, &frame);
        assert_eq!(notices.len(), 1);
        assert_eq!(notices[0]["kind"], "uncaughtException");
        // The notice belongs to background work, so the cell that carries it
        // stays successful and keeps its own result.
        let mut output = String::from("binding");
        let mut truncated = false;
        append_notices(&mut output, &notices, &mut truncated);
        // `append_notices` keeps exactly one blank separator line between the
        // cell's own result and the notice block.
        assert!(
            output.starts_with("binding\n\n[Node background error] Uncaught exception"),
            "{output}"
        );
        assert!(output.contains("late boom"), "{output}");
        assert!(!truncated);
        let carried = result(&output, false, &notices);
        assert_eq!(carried.is_error, Some(false));
        assert_eq!(
            carried.details["backgroundNotices"]
                .as_array()
                .map(Vec::len),
            Some(1)
        );
        // Worker error text is bounded before it can reach the model.
        let huge = json!({
            "event": NOTICE_EVENT,
            "kind": "unhandledRejection",
            "message": "x".repeat(NOTICE_LIMIT * 4),
        });
        let mut noisy = Vec::new();
        push_notice(&mut noisy, &huge);
        let message = noisy[0]["message"].as_str().unwrap();
        assert!(message.len() <= NOTICE_LIMIT + "...(truncated)".len());
        assert!(message.ends_with("...(truncated)"));
        // A noisy reporter cannot grow the buffer without limit either.
        let mut flooded = Vec::new();
        for _ in 0..(MAX_NOTICES * 4) {
            push_notice(&mut flooded, &frame);
        }
        assert_eq!(flooded.len(), MAX_NOTICES);
        // No notices: the details keep their previous shape.
        let clean = result("42", false, &[]);
        assert!(clean.details.get("backgroundNotices").is_none());
    }
}
