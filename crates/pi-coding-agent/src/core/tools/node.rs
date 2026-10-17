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

    async fn message(&mut self) -> Result<Value, String> {
        // Bound malformed/native writes as well as ordinary protocol messages.
        let mut bytes = Vec::new();
        (&mut self.output)
            .take((LIMIT * 8) as u64)
            .read_until(b'\n', &mut bytes)
            .await
            .map_err(|e| e.to_string())?;
        if bytes.last() != Some(&b'\n') {
            return Err("Node workspace exited or returned an oversized response".into());
        }
        serde_json::from_slice(&bytes).map_err(|e| format!("Invalid Node workspace response: {e}"))
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
                if message["id"] != id {
                    return Err("Unexpected Node workspace response".into());
                }
                if let Some(text) = message["output"].as_str() {
                    truncated |= append_bounded(&mut output, text);
                    if last_update.elapsed() >= Duration::from_millis(100) {
                        if let Some(update) = &on_update {
                            update(result(&output, false));
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
                    note_truncation(&mut output, truncated);
                    return Ok(result(&output, message["error"] == true));
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
fn result(output: &str, error: bool) -> AgentToolResult {
    let mut value = AgentToolResult::new(
        vec![ContentBlock::text(if output.is_empty() {
            "(no output)"
        } else {
            output
        })],
        json!({"language":"javascript"}),
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
}
