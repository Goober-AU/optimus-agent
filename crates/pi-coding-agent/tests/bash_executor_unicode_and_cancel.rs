//! Bash executor Unicode/cancellation acceptance tests (T1/T15) and the
//! Windows local-bash containment tests (T2/T3 plus the ports of
//! `direct_bash_lifecycle.rs` semantics that are Unix-only upstream).
//!
//! Isolation: every probe targets a pid recorded from this test's own command;
//! no production pipes, ports or journals are touched.

use pi_coding_agent::core::bash_executor::{execute_bash_with_operations, BashExecutorOptions};
use pi_coding_agent::core::tools::bash::{BashExecOptions, BashExecResult, BashOperations};
use std::sync::{Arc, Mutex};
use tokio_util::sync::CancellationToken;

/// Fake backend used through the public executor API.
struct FakeOperations {
    chunks: Vec<Vec<u8>>,
    exit_code: Option<i32>,
    /// Fires the token after the work completed, right before the future
    /// resolves: a late cancellation racing the report.
    cancel_before_resolve: Option<CancellationToken>,
}

impl BashOperations for FakeOperations {
    fn exec(
        &self,
        _command: &str,
        _cwd: &str,
        options: BashExecOptions,
    ) -> futures::future::BoxFuture<'static, Result<BashExecResult, String>> {
        let chunks = self.chunks.clone();
        let exit_code = self.exit_code;
        let cancel = self.cancel_before_resolve.clone();
        Box::pin(async move {
            for chunk in chunks {
                (options.on_data)(&chunk);
            }
            if let Some(token) = cancel {
                token.cancel();
            }
            Ok(BashExecResult { exit_code })
        })
    }
}

// T1: a multibyte character split across two pipe chunks must reassemble in
// the executor output and in every streamed chunk, never double-replace.
#[tokio::test]
async fn split_utf8_chunks_reassemble_in_executor() {
    let operations = Arc::new(FakeOperations {
        chunks: vec![b"h\xc3".to_vec(), b"\xa9llo".to_vec()],
        exit_code: Some(0),
        cancel_before_resolve: None,
    });
    let seen: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let seen_clone = Arc::clone(&seen);
    let result = execute_bash_with_operations(
        "cmd",
        ".",
        operations,
        Some(BashExecutorOptions {
            on_chunk: Some(Arc::new(move |chunk: &str| {
                seen_clone.lock().unwrap().push(chunk.to_string());
            })),
            signal: None,
        }),
    )
    .await
    .unwrap();
    assert_eq!(result.output, "h\u{e9}llo");
    assert_eq!(result.exit_code, Some(0));
    assert!(!result.cancelled);
    let seen = seen.lock().unwrap().join("");
    assert_eq!(seen, "h\u{e9}llo");
    assert!(
        !seen.contains('\u{FFFD}'),
        "split character corrupted: {seen:?}"
    );
}

// T1: the spill file is written from the same sanitized stream, so the split
// character must land there byte-identical.
#[tokio::test]
async fn split_utf8_reaches_the_spill_file_intact() {
    let filler = "x".repeat(pi_coding_agent::core::tools::truncate::DEFAULT_MAX_BYTES + 1);
    let operations = Arc::new(FakeOperations {
        chunks: vec![
            filler.clone().into_bytes(),
            b"h\xc3".to_vec(),
            b"\xa9llo".to_vec(),
        ],
        exit_code: Some(0),
        cancel_before_resolve: None,
    });
    let result = execute_bash_with_operations("cmd", ".", operations, None)
        .await
        .unwrap();
    assert!(result.truncated);
    assert!(result.output.ends_with("h\u{e9}llo"));
    assert!(!result.output.contains('\u{FFFD}'));
    let path = result.full_output_path.expect("full output path");
    let spilled = std::fs::read(&path).expect("spilled bytes");
    assert_eq!(spilled, format!("{filler}h\u{e9}llo").into_bytes());
    let _ = std::fs::remove_file(path);
}

// T15: a token firing after the operation resolved must not retroactively
// cancel a completed command or discard its real exit code.
#[tokio::test]
async fn a_token_firing_after_resolution_does_not_cancel_a_completed_command() {
    let token = CancellationToken::new();
    let operations = Arc::new(FakeOperations {
        chunks: vec![b"done\n".to_vec()],
        exit_code: Some(0),
        cancel_before_resolve: Some(token.clone()),
    });
    let result = execute_bash_with_operations(
        "cmd",
        ".",
        operations,
        Some(BashExecutorOptions {
            on_chunk: None,
            signal: Some(token),
        }),
    )
    .await
    .unwrap();
    assert!(
        !result.cancelled,
        "a completed command must not report cancelled"
    );
    assert_eq!(result.exit_code, Some(0));
    assert_eq!(result.output, "done\n");
}

// An unresolved kill (`exit_code: None`) still attributes a cancelled token.
#[tokio::test]
async fn an_unresolved_kill_still_reports_a_cancelled_token() {
    let token = CancellationToken::new();
    token.cancel();
    let operations = Arc::new(FakeOperations {
        chunks: vec![b"partial".to_vec()],
        exit_code: None,
        cancel_before_resolve: Some(token.clone()),
    });
    let result = execute_bash_with_operations(
        "cmd",
        ".",
        operations,
        Some(BashExecutorOptions {
            on_chunk: None,
            signal: Some(token),
        }),
    )
    .await
    .unwrap();
    assert!(result.cancelled);
    assert_eq!(result.exit_code, None);
    assert_eq!(result.output, "partial");
}

#[cfg(windows)]
mod windows_containment {
    use super::*;
    use pi_coding_agent::core::tools::bash::LocalBashOperations;
    use std::path::{Path, PathBuf};
    use std::time::{Duration, Instant};

    /// Test liveness oracle: like the OS task list, a terminated process whose
    /// handle is still open somewhere must not count as running.
    fn probe_process_running(pid: i32) -> bool {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::{
            GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        };
        const STILL_ACTIVE: u32 = 259;
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid as u32);
            if handle.is_null() {
                return false;
            }
            let mut exit_code: u32 = 0;
            let ok = GetExitCodeProcess(handle, &mut exit_code) != 0;
            CloseHandle(handle);
            ok && exit_code == STILL_ACTIVE
        }
    }

    fn wait_process_gone(pid: i32, deadline: Duration) -> bool {
        let start = Instant::now();
        while start.elapsed() < deadline {
            if !probe_process_running(pid) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        !probe_process_running(pid)
    }

    fn wait_process_running(pid: i32, deadline: Duration) -> bool {
        let start = Instant::now();
        while start.elapsed() < deadline {
            if probe_process_running(pid) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        probe_process_running(pid)
    }

    fn read_pid_file(path: &Path, deadline: Duration) -> i32 {
        let start = Instant::now();
        loop {
            if let Ok(text) = std::fs::read_to_string(path) {
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    if let Ok(pid) = trimmed.parse::<i32>() {
                        return pid;
                    }
                }
            }
            if start.elapsed() >= deadline {
                panic!("pid file never became readable: {}", path.display());
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    fn operations() -> LocalBashOperations {
        pi_coding_agent::utils::shell::get_shell_config(None)
            .expect("Git Bash is required for the Windows containment tests");
        LocalBashOperations { shell_path: None }
    }

    fn powershell() -> PathBuf {
        PathBuf::from(std::env::var_os("SystemRoot").expect("SystemRoot"))
            .join("System32/WindowsPowerShell/v1.0/powershell.exe")
    }

    fn forward(path: &Path) -> String {
        path.to_string_lossy().replace('\\', "/")
    }

    // T2: the shell spawns a child through powershell Start-Process and keeps
    // running; the timeout must kill both. The Start-Process child is reached
    // by job containment even after its intermediate parent (the powershell)
    // has exited, which a `taskkill /T` tree walk cannot do.
    #[tokio::test(flavor = "multi_thread")]
    async fn timeout_kills_the_shell_and_its_detached_child() {
        let dir = tempfile::tempdir().unwrap();
        let shell_pid_file = dir.path().join("shell.pid");
        let child_pid_file = dir.path().join("child.pid");
        let command = format!(
            "read -r w < /proc/self/winpid\n\
             printf '%s\\n' \"$w\" > '{shell_file}'\n\
             \"{ps}\" -NoLogo -NoProfile -NonInteractive -Command '$p = Start-Process -FilePath ping.exe -ArgumentList \"-n\",\"60\",\"127.0.0.1\" -WindowStyle Hidden -PassThru; Set-Content -Path \"{child_file}\" -Value $p.Id'\n\
             sleep 30",
            shell_file = forward(&shell_pid_file),
            ps = forward(&powershell()),
            child_file = forward(&child_pid_file),
        );
        let started = Instant::now();
        let error = operations()
            .exec(
                &command,
                dir.path().to_str().unwrap(),
                BashExecOptions {
                    on_data: Arc::new(|_| {}),
                    signal: None,
                    timeout: Some(5.0),
                    env: None,
                },
            )
            .await
            .expect_err("the timeout must error");
        assert!(error.starts_with("timeout:"), "{error}");
        assert!(
            started.elapsed() < Duration::from_secs(15),
            "the killed call must stay bounded"
        );
        let shell_pid = read_pid_file(&shell_pid_file, Duration::from_secs(5));
        let child_pid = read_pid_file(&child_pid_file, Duration::from_secs(5));
        assert!(
            wait_process_gone(shell_pid, Duration::from_secs(10)),
            "shell pid {shell_pid} survived the timeout kill"
        );
        assert!(
            wait_process_gone(child_pid, Duration::from_secs(10)),
            "detached child pid {child_pid} survived the timeout kill"
        );
    }

    // T3 (port of direct_bash_lifecycle.rs): a background child holding the
    // output pipe must not block the call; the settle notice is emitted, and
    // the descendant outlives the settled call (the TS background contract).
    #[tokio::test(flavor = "multi_thread")]
    async fn background_child_notice_and_settle() {
        let dir = tempfile::tempdir().unwrap();
        let child_pid_file = dir.path().join("child.pid");
        let command = format!(
            "sleep 30 & p=$!\n\
             cat /proc/$p/winpid > '{child_file}'\n\
             echo hi",
            child_file = forward(&child_pid_file),
        );
        let output: Arc<Mutex<Vec<u8>>> = Arc::new(Mutex::new(Vec::new()));
        let captured = output.clone();
        let started = Instant::now();
        let result = operations()
            .exec(
                &command,
                dir.path().to_str().unwrap(),
                BashExecOptions {
                    on_data: Arc::new(move |data| {
                        captured.lock().unwrap().extend_from_slice(data);
                    }),
                    signal: None,
                    timeout: Some(10.0),
                    env: None,
                },
            )
            .await
            .expect("a background child must not block the call");
        assert_eq!(result.exit_code, Some(0));
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "the call must return at shell exit, not at pipe EOF"
        );
        let text = String::from_utf8(output.lock().unwrap().clone()).unwrap();
        assert!(text.contains("hi"), "output: {text}");
        assert!(
            text.contains("[Shell exited; closed output pipes held by a background process"),
            "settle notice missing: {text}"
        );
        let child_pid = read_pid_file(&child_pid_file, Duration::from_secs(5));
        assert!(
            wait_process_running(child_pid, Duration::from_secs(2)),
            "the background descendant must outlive the settled call"
        );
        // Test-owned cleanup; the registry keeps its job handle armed until
        // process teardown.
        pi_coding_agent::utils::shell::kill_process_tree(child_pid);
        assert!(wait_process_gone(child_pid, Duration::from_secs(10)));
    }

    // Port of direct_bash_lifecycle.rs: a stdin reader settles at the closed
    // pipe (EOF), and timeout/cancellation both settle without hanging.
    #[tokio::test(flavor = "multi_thread")]
    async fn stdin_eof_timeout_and_cancellation_settle() {
        let dir = tempfile::tempdir().unwrap();
        for command in ["grep 8080", "printf 'port 8080\\n' | grep 8080"] {
            tokio::time::timeout(
                Duration::from_secs(10),
                operations().exec(
                    command,
                    dir.path().to_str().unwrap(),
                    BashExecOptions {
                        on_data: Arc::new(|_| {}),
                        signal: None,
                        timeout: Some(5.0),
                        env: None,
                    },
                ),
            )
            .await
            .expect("settles at stdin EOF, not at the timeout")
            .unwrap();
        }
        for cancel in [false, true] {
            let token = CancellationToken::new();
            if cancel {
                let signal = token.clone();
                tokio::spawn(async move {
                    tokio::time::sleep(Duration::from_millis(100)).await;
                    signal.cancel();
                });
            }
            let result = tokio::time::timeout(
                Duration::from_secs(15),
                operations().exec(
                    "sleep 30 | grep 8080",
                    dir.path().to_str().unwrap(),
                    BashExecOptions {
                        on_data: Arc::new(|_| {}),
                        signal: Some(token),
                        timeout: Some(0.5),
                        env: None,
                    },
                ),
            )
            .await
            .expect("the kill path must stay bounded")
            .unwrap_err();
            assert!(
                result.contains(if cancel { "aborted" } else { "timeout" }),
                "{result}"
            );
        }
    }

    // Port of direct_bash_lifecycle.rs: dropping the tool future must kill the
    // process it owns (the RAII containment path).
    #[tokio::test(flavor = "multi_thread")]
    async fn dropping_the_tool_future_kills_its_owned_process() {
        let dir = tempfile::tempdir().unwrap();
        let shell_pid_file = dir.path().join("shell.pid");
        let command = format!(
            "read -r w < /proc/self/winpid\n\
             printf '%s\\n' \"$w\" > '{shell_file}'\n\
             exec sleep 30",
            shell_file = forward(&shell_pid_file),
        );
        let result = tokio::time::timeout(
            Duration::from_millis(1000),
            operations().exec(
                &command,
                dir.path().to_str().unwrap(),
                BashExecOptions {
                    on_data: Arc::new(|_| {}),
                    signal: None,
                    timeout: None,
                    env: None,
                },
            ),
        )
        .await;
        assert!(result.is_err(), "the dropped future must not hang");
        let pid = read_pid_file(&shell_pid_file, Duration::from_secs(5));
        assert!(
            wait_process_gone(pid, Duration::from_secs(5)),
            "dropped Bash future must kill its child (pid {pid})"
        );
    }
}
