//! Behavior tests for the node and clang execution tools: truncation
//! visibility, byte-exact output budgets, and dispose/execute racing.
//!
//! Node-dependent tests skip honestly when no node binary can be started
//! (mirroring the `local_operations_run_the_resolved_shell` skip pattern);
//! clang-repl tests skip when the interpreter cannot be spawned.

use pi_coding_agent::core::tools::clang::ClangRuntime;
use pi_coding_agent::core::tools::node::NodeRuntime;
use serde_json::Value;
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout};

const WORKER_PATH: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/core/tools/node_worker.cjs"
);
const READ_TIMEOUT: Duration = Duration::from_secs(30);

/// Node reachable for spawning tools: the daemon PATH resolution first, then
/// the stock Windows install location.
fn node_executable() -> Option<&'static str> {
    for candidate in ["node", r"C:\Program Files\nodejs\node.exe"] {
        if std::process::Command::new(candidate)
            .arg("--version")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output()
            .is_ok_and(|output| output.status.success())
        {
            return Some(candidate);
        }
    }
    None
}

fn node_on_path() -> bool {
    std::process::Command::new("node")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .is_ok_and(|output| output.status.success())
}

fn result_text(result: &pi_agent_core::types::AgentToolResult) -> String {
    result
        .content
        .first()
        .and_then(|block| block.as_text())
        .expect("text content block")
        .to_owned()
}

// ---------------------------------------------------------------------------
// node_worker.cjs protocol level (drives the worker file directly)
// ---------------------------------------------------------------------------

struct WorkerDriver {
    /// Held for its `kill_on_drop` guard; never read directly.
    #[allow(dead_code)]
    child: Child,
    stdin: ChildStdin,
    reader: BufReader<ChildStdout>,
    counter: usize,
}

impl WorkerDriver {
    async fn spawn(node: &str) -> Self {
        let mut child = tokio::process::Command::new(node)
            .arg(WORKER_PATH)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .expect("spawn node worker");
        let stdin = child.stdin.take().expect("worker stdin");
        let stdout = child.stdout.take().expect("worker stdout");
        let mut driver = Self {
            child,
            stdin,
            reader: BufReader::new(stdout),
            counter: 0,
        };
        let hello = driver.line().await.expect("worker handshake");
        assert_eq!(hello["ready"], true, "worker handshake: {hello}");
        driver
    }

    async fn line(&mut self) -> Option<Value> {
        let mut buffer = Vec::new();
        let read = tokio::time::timeout(READ_TIMEOUT, self.reader.read_until(b'\n', &mut buffer))
            .await
            .expect("worker read within timeout");
        match read {
            Ok(0) | Err(_) => None,
            Ok(_) => serde_json::from_slice(&buffer).ok(),
        }
    }

    /// Runs one cell and returns every protocol frame it produced.
    async fn cell(&mut self, code: &str) -> Vec<Value> {
        self.counter += 1;
        let id = format!("cell-{}", self.counter);
        let request = serde_json::json!({"id": id, "code": code});
        self.stdin
            .write_all(format!("{request}\n").as_bytes())
            .await
            .expect("write cell");
        self.stdin.flush().await.expect("flush cell");
        let mut frames = Vec::new();
        while let Some(frame) = self.line().await {
            assert_eq!(frame["id"], id, "unexpected frame: {frame}");
            let done = frame["done"] == true;
            frames.push(frame);
            if done {
                return frames;
            }
        }
        panic!("worker exited before the cell completed");
    }

    /// Concatenated stdout frames, excluding the truncation notice frame.
    fn output(frames: &[Value]) -> String {
        frames
            .iter()
            .filter_map(|frame| frame["output"].as_str())
            .filter(|text| !text.contains("truncated at 64 KiB"))
            .collect()
    }

    fn noticed(frames: &[Value]) -> bool {
        frames
            .iter()
            .filter_map(|frame| frame["output"].as_str())
            .any(|text| text.contains("truncated at 64 KiB"))
    }
}

#[tokio::test]
async fn worker_stream_budget_is_byte_accurate_and_exact_fit_is_not_flagged() {
    let Some(node) = node_executable() else {
        eprintln!("skipping: no node executable available");
        return;
    };
    let mut driver = WorkerDriver::spawn(node).await;

    // 32768 two-byte characters fill the 64 KiB byte budget exactly.
    let frames = driver
        .cell("process.stdout.write('\\u00e9'.repeat(32768))")
        .await;
    let exact = WorkerDriver::output(&frames);
    assert_eq!(exact.len(), 64 * 1024, "budget must count bytes");
    assert!(
        !WorkerDriver::noticed(&frames),
        "exact fit is not truncation"
    );

    // One byte over: content still capped at the budget, one visible notice.
    let frames = driver
        .cell("process.stdout.write('\\u00e9'.repeat(32769))")
        .await;
    let over = WorkerDriver::output(&frames);
    assert_eq!(over.len(), 64 * 1024);
    assert!(
        WorkerDriver::noticed(&frames),
        "one byte over must be flagged"
    );
}

#[tokio::test]
async fn worker_stream_clip_never_splits_multibyte_characters() {
    let Some(node) = node_executable() else {
        eprintln!("skipping: no node executable available");
        return;
    };
    let mut driver = WorkerDriver::spawn(node).await;
    // 'a' plus 40000 astral characters: the clip boundary lands inside a
    // surrogate pair unless the worker backs off to a sequence boundary.
    let frames = driver
        .cell("process.stdout.write('a' + '\\uD83D\\uDE00'.repeat(40000))")
        .await;
    let clipped = WorkerDriver::output(&frames);
    assert!(
        clipped.len() <= 64 * 1024,
        "clipped: {} bytes",
        clipped.len()
    );
    assert!(!clipped.contains('\u{FFFD}'), "no replacement characters");
    // A lone surrogate would fail JSON parsing above, so reaching this point
    // already proves the frames stayed valid; also check the text directly.
    assert!(
        !clipped
            .chars()
            .any(|c| (0xD800..=0xDFFF).contains(&(c as u32))),
        "no unpaired surrogates"
    );
}

#[tokio::test]
async fn worker_result_truncation_is_marked_and_exact_fit_is_not() {
    let Some(node) = node_executable() else {
        eprintln!("skipping: no node executable available");
        return;
    };
    let mut driver = WorkerDriver::spawn(node).await;

    // Measure this worker's exception-description overhead (message length 5)
    // so the second cell lands exactly on the 64 KiB result budget.
    let probe = driver.cell("throw new Error('probe')").await;
    let description = probe.last().expect("done frame")["result"]
        .as_str()
        .expect("description")
        .len();
    assert!(description > 5, "description: {description}");
    let message = 64 * 1024 - (description - 5);

    let exact = driver
        .cell(&format!("throw new Error('x'.repeat({message}))"))
        .await;
    let text = exact.last().unwrap()["result"].as_str().unwrap();
    assert_eq!(text.len(), 64 * 1024, "exact fit must be complete");
    assert!(!text.contains("truncated"), "nothing was dropped");

    let over = driver
        .cell(&format!("throw new Error('x'.repeat({} + 1))", message))
        .await;
    let text = over.last().unwrap()["result"].as_str().unwrap();
    assert!(text.contains("[Node result truncated at 64 KiB]"), "{text}");
    assert!(text.len() <= 64 * 1024 + 64);
}

// ---------------------------------------------------------------------------
// NodeRuntime level
// ---------------------------------------------------------------------------

#[tokio::test]
async fn node_timeout_validation_rejects_before_spawning() {
    let runtime = NodeRuntime::default();
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    for timeout in [0.0, -1.0, f64::INFINITY, 3601.0] {
        let error = runtime
            .execute(cwd, "1", timeout, None, None)
            .await
            .expect_err("invalid timeout");
        assert!(error.contains("timeout"), "{error}");
    }
}

#[tokio::test]
async fn node_stream_truncation_is_visible_in_the_result() {
    if !node_on_path() {
        eprintln!("skipping: node is not on PATH for the runtime spawn");
        return;
    }
    let runtime = NodeRuntime::default();
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let result = runtime
        .execute(cwd, "console.log('x'.repeat(70000))", 30., None, None)
        .await
        .expect("cell executed");
    let text = result_text(&result);
    assert_eq!(
        text.matches("truncated at 64 KiB").count(),
        1,
        "exactly one visible notice: {text}",
    );
    assert!(text.len() <= 64 * 1024 + 64, "text: {} bytes", text.len());
    runtime.dispose().await;
}

#[tokio::test]
async fn node_result_truncation_is_visible_in_the_result() {
    if !node_on_path() {
        eprintln!("skipping: node is not on PATH for the runtime spawn");
        return;
    }
    let runtime = NodeRuntime::default();
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    // Streams below the budget plus a result above it: the Rust-side cap is
    // what engages, and it must not be silent.
    let result = runtime
        .execute(
            cwd,
            "process.stdout.write('y'.repeat(60000)); throw new Error('z'.repeat(60000))",
            30.,
            None,
            None,
        )
        .await
        .expect("cell executed");
    assert_eq!(result.is_error, Some(true));
    let text = result_text(&result);
    assert!(
        text.contains("truncated at 64 KiB"),
        "combined cap must be visible: {} bytes",
        text.len()
    );
    assert!(text.len() <= 64 * 1024 + 64, "text: {} bytes", text.len());
    runtime.dispose().await;
}

#[tokio::test]
async fn node_exact_64kib_output_has_no_false_truncation_notice() {
    if !node_on_path() {
        eprintln!("skipping: node is not on PATH for the runtime spawn");
        return;
    }
    let runtime = NodeRuntime::default();
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let result = runtime
        .execute(
            cwd,
            // `undefined` suppresses the write()'s boolean return value, which
            // would otherwise be appended as the cell result and trip the cap.
            "process.stdout.write('x'.repeat(65536)); undefined",
            30.,
            None,
            None,
        )
        .await
        .expect("cell executed");
    let text = result_text(&result);
    assert_eq!(text.len(), 65536, "nothing may be dropped");
    assert!(!text.contains("truncated"), "exact fit is not truncation");
    runtime.dispose().await;
}

#[tokio::test]
async fn node_dispose_racing_execute_kills_the_workspace_once_without_deadlock() {
    if !node_on_path() {
        eprintln!("skipping: node is not on PATH for the runtime spawn");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap().to_owned();
    let runtime = std::sync::Arc::new(NodeRuntime::default());
    let executing = runtime.clone();
    let task_cwd = cwd.clone();
    let cell = tokio::spawn(async move {
        executing
            .execute(&task_cwd, "await new Promise(() => {})", 60., None, None)
            .await
    });
    // Let the cell take the workspace out of the mutex, then dispose.
    tokio::time::sleep(Duration::from_millis(500)).await;
    let started = Instant::now();
    tokio::time::timeout(Duration::from_secs(15), runtime.dispose())
        .await
        .expect("dispose must not deadlock while a cell executes");
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "dispose stalled for {:?}",
        started.elapsed()
    );

    let outcome = tokio::time::timeout(Duration::from_secs(15), cell)
        .await
        .expect("execute must complete after dispose")
        .expect("execute task must not panic");
    let error = outcome.expect_err("in-flight cell must be disposed");
    assert!(error.contains("disposed"), "{error}");

    // The workspace stays down: exactly one kill took effect and no zombie
    // respawn happened. A fresh execute must fail fast, not spawn or hang.
    // Which refusal message arrives depends on whether the lock or the
    // shutdown branch wins the select race, so accept either.
    let again = tokio::time::timeout(
        Duration::from_secs(5),
        runtime.execute(&cwd, "1", 10., None, None),
    )
    .await
    .expect("post-dispose execute must not hang")
    .expect_err("disposed runtime must refuse new cells");
    assert!(
        again.contains("disposed") || again.contains("cancelled"),
        "{again}"
    );

    // Idempotent dispose: neither panics nor hangs.
    tokio::time::timeout(Duration::from_secs(5), runtime.dispose())
        .await
        .expect("second dispose must not hang");

    // The runtime machinery itself is still healthy for a fresh workspace.
    let fresh = NodeRuntime::default();
    let sanity = tokio::time::timeout(
        Duration::from_secs(20),
        fresh.execute(&cwd, "21 * 2", 10., None, None),
    )
    .await
    .expect("fresh workspace must start")
    .expect("fresh cell must execute");
    assert!(result_text(&sanity).contains("42"));
    fresh.dispose().await;
}

// ---------------------------------------------------------------------------
// ClangRuntime level
// ---------------------------------------------------------------------------

/// Spawns a clang-repl workspace or skips honestly when the interpreter is
/// unavailable, mirroring `local_operations_run_the_resolved_shell`.
async fn clang_runtime_or_skip(cwd: &str) -> Option<ClangRuntime> {
    let runtime = ClangRuntime::default();
    match runtime
        .execute(cwd, "int optimus_probe = 0;", 30., None, None)
        .await
    {
        Ok(_) => Some(runtime),
        Err(error) if error.contains("Cannot start Clang-Repl") => {
            eprintln!("skipping: {error}");
            None
        }
        Err(error) => panic!("clang-repl probe failed unexpectedly: {error}"),
    }
}

#[tokio::test]
async fn clang_validation_rejects_bad_timeouts_and_oversized_code_without_spawning() {
    let runtime = ClangRuntime::default();
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    for timeout in [0.0, -1.0, f64::INFINITY, 3601.0] {
        let error = runtime
            .execute(cwd, "int x = 0;", timeout, None, None)
            .await
            .expect_err("invalid timeout");
        assert!(error.contains("timeout"), "{error}");
    }
    let oversized = "int padding = 0; ".repeat(100_000);
    let error = runtime
        .execute(cwd, &oversized, 10., None, None)
        .await
        .expect_err("oversized cell");
    assert!(error.contains("1 MB"), "{error}");
}

#[tokio::test]
async fn clang_exact_64kib_stdout_has_no_false_truncation_notice() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let Some(runtime) = clang_runtime_or_skip(cwd).await else {
        return;
    };
    let result = runtime
        .execute(
            cwd,
            "#include <string>\n[] { std::fputs(std::string(65536, 'x').c_str(), stdout); return 0; }();",
            60.,
            None,
            None,
        )
        .await
        .expect("cell executed");
    assert_eq!(result.is_error, Some(false));
    let text = result_text(&result);
    assert_eq!(text.len(), 65536, "nothing may be dropped");
    assert!(!text.contains("truncated"), "exact fit is not truncation");
    runtime.dispose().await;
}

#[tokio::test]
async fn clang_stdout_beyond_64kib_notice_names_the_stream() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let Some(runtime) = clang_runtime_or_skip(cwd).await else {
        return;
    };
    let result = runtime
        .execute(
            cwd,
            "#include <string>\n[] { std::fputs(std::string(65537, 'x').c_str(), stdout); return 0; }();",
            60.,
            None,
            None,
        )
        .await
        .expect("cell executed");
    let text = result_text(&result);
    assert!(text.contains("truncated"), "must announce the cap");
    assert!(
        text.contains("stdout") && !text.contains("stderr"),
        "notice must name the truncated stream: {text}"
    );
    runtime.dispose().await;
}

#[tokio::test]
async fn clang_stderr_beyond_64kib_notice_names_the_stream() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let Some(runtime) = clang_runtime_or_skip(cwd).await else {
        return;
    };
    let result = runtime
        .execute(
            cwd,
            "#include <string>\n[] { std::fputs(std::string(65537, 'e').c_str(), stderr); return 0; }();",
            60.,
            None,
            None,
        )
        .await
        .expect("cell executed");
    let text = result_text(&result);
    assert!(
        text.contains("stderr") && !text.contains("stdout"),
        "notice must name the truncated stream: {text}"
    );
    runtime.dispose().await;
}
