//! S-5 / T14: the ipython tool result is bounded against a real rlm.repl kernel.
//!
//! The tool text is assembled from stdout + stderr + result + traceback +
//! background output. `repl_manager.rs` caps stdout/stderr/result at
//! `DEFAULT_MAX_OUTPUT_CHARS` (65536 chars) with a `[... output truncated ...]`
//! notice and caps background output at 64 KiB; the error-event traceback and
//! evalue are capped on the same budget at the protocol boundary. This test
//! drives a real kernel so every layer (repl.py framing, repl_manager caps,
//! the tool assembly inputs) is exercised together:
//!
//! - a 2 MB `print` cell produces bounded stdout with the notice, and later
//!   cells still run (the kernel is not poisoned by the flood);
//! - stderr, the trailing-expression result, and an error traceback are each
//!   bounded with the notice;
//! - a raw fd write from a subprocess during a cell lands in
//!   `background_output`, never in the cell's stdout (attribution).

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use pi_coding_agent::core::kernel::repl_manager::{new_repl_kernel_manager, ReplKernelManager};
use pi_coding_agent::core::kernel::shared::{
    ExecuteOptions, ExecuteResult, ExecuteStatus, KernelClient, KernelManagerOptions,
    KernelShutdownOptions, KernelSnapshotConfig,
};

/// `shared::DEFAULT_MAX_OUTPUT_CHARS`.
const MAX_OUTPUT_CHARS: usize = 65536;
const TRUNCATION_NOTICE: &str = "[... output truncated at 65536 chars ...]";

struct OwnedKernel(Arc<ReplKernelManager>);
impl Drop for OwnedKernel {
    fn drop(&mut self) {
        self.0.dispose_sync();
    }
}

fn kernel(root: &Path) -> OwnedKernel {
    std::fs::create_dir_all(root).unwrap();
    let root_string = root.to_string_lossy();
    let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../prime-agent-runtime/src");
    let mut env = HashMap::new();
    for key in [
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
    ] {
        env.insert(key.into(), root_string.to_string());
    }
    env.insert("PYTHONPATH".into(), source.to_string_lossy().into_owned());
    env.insert("PYTHONDONTWRITEBYTECODE".into(), "1".into());
    env.insert("PYTHONNOUSERSITE".into(), "1".into());
    let python = std::env::var("PRIME_AGENT_KERNEL_PYTHON")
        .expect("test requires an explicitly provisioned Python with the runtime; never bootstrap production");
    OwnedKernel(new_repl_kernel_manager(KernelManagerOptions {
        python: Some(python),
        cwd: Some(root_string.to_string()),
        env: Some(env),
        session_id: Some("private-ipython-output-bounds".into()),
        snapshot: Some(KernelSnapshotConfig {
            path: pi_coding_agent::core::kernel::state_snapshot::snapshot_path_in(&root_string),
            manifest_path: pi_coding_agent::core::kernel::state_snapshot::manifest_path_in(&root_string),
            cas_root_path: Some(pi_coding_agent::core::kernel::state_snapshot::cas_snapshot_root_in(
                &root_string,
            )),
            ..Default::default()
        }),
        stderr_log_path: Some(root.join("kernel.stderr.log").to_string_lossy().into_owned()),
        ..Default::default()
    }))
}

async fn execute(client: &ReplKernelManager, code: &str) -> ExecuteResult {
    client
        .execute(code.to_string(), ExecuteOptions::default())
        .await
        .expect("kernel execution must not fail at the transport level")
}

async fn stop(client: &ReplKernelManager) {
    assert!(
        client
            .shutdown(KernelShutdownOptions { snapshot: false, drain_host_requests: true })
            .await
            .unwrap()
    );
    assert!(!client.is_running());
}

fn assert_bounded(text: &str, label: &str) {
    assert!(
        text.chars().count() <= MAX_OUTPUT_CHARS + TRUNCATION_NOTICE.chars().count() + 1,
        "{label} must be capped at {MAX_OUTPUT_CHARS} chars plus the notice, got {}",
        text.chars().count()
    );
    assert!(text.contains(TRUNCATION_NOTICE), "{label} must carry the truncation notice");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn oversized_outputs_are_bounded_and_the_kernel_survives() {
    tokio::time::timeout(Duration::from_secs(180), async {
        let root = tempfile::Builder::new().prefix("optimus-ipython-bounds-").tempdir().unwrap();
        let owned = kernel(&root.path().join("state"));
        let client = &owned.0;

        // 1. stdout: a 2 MB print floods only the capped stdout.
        let result = execute(client, r#"print("x" * 2_000_000)"#).await;
        assert_eq!(result.status, ExecuteStatus::Ok, "{:?}", result.error);
        assert!(result.stdout.starts_with(&"x".repeat(1024)));
        assert_bounded(&result.stdout, "stdout");
        assert!(!result.stdout.contains('\u{FFFD}'), "cap must cut whole characters");
        assert!(result.stderr.is_empty());

        // 2. stderr: the same flood through sys.stderr.
        let result = execute(client, r#"import sys; sys.stderr.write("e" * 2_000_000)"#).await;
        assert_eq!(result.status, ExecuteStatus::Ok, "{:?}", result.error);
        assert!(result.stderr.starts_with(&"e".repeat(1024)));
        assert_bounded(&result.stderr, "stderr");

        // 3. trailing-expression result: the Python side allows 1 MB of repr,
        //    the Rust side re-caps it on the same 65536-char budget.
        let result = execute(client, r#""y" * 2_000_000"#).await;
        assert_eq!(result.status, ExecuteStatus::Ok, "{:?}", result.error);
        let result_text = result.result.expect("trailing expression produces a result");
        // `repr` of a str without single quotes uses single quotes.
        assert!(result_text.starts_with('\''));
        assert!(result_text.starts_with(&format!("'{}", "y".repeat(1024))));
        assert_bounded(&result_text, "result");

        // 4. traceback and evalue: a huge error message must not push a ~1 MB
        //    traceback into the tool text.
        let result = execute(client, r#"raise ValueError("z" * 2_000_000)"#).await;
        assert_eq!(result.status, ExecuteStatus::Error);
        let error = result.error.expect("error status carries the error event");
        assert_eq!(error.ename, "ValueError");
        let traceback = error.traceback.join("\n");
        assert_bounded(&traceback, "traceback");
        assert!(traceback.contains("ValueError"));
        assert_bounded(&error.evalue, "evalue");

        // 5. background attribution: a raw fd write from a subprocess during a
        //    cell is unattributed (id: null) and lands in background_output,
        //    never merged into the cell's stdout.
        let result = execute(
            client,
            r#"import subprocess, sys
subprocess.run([sys.executable, "-c", "import os; os.write(1, b'raw-fd-bytes')"], check=True)
print("tagged-output")"#,
        )
        .await;
        assert_eq!(result.status, ExecuteStatus::Ok, "{:?}", result.error);
        assert!(result.stdout.contains("tagged-output"), "stdout: {:?}", result.stdout);
        assert!(
            !result.stdout.contains("raw-fd-bytes"),
            "raw fd writes must not be attributed to the cell's stdout"
        );
        let background = result.background_output.expect("raw fd write is background output");
        assert!(background.contains("raw-fd-bytes"), "background: {background}");

        // 6. the kernel is not poisoned by any of the floods above.
        let result = execute(client, r#"print("after-bounds")"#).await;
        assert_eq!(result.status, ExecuteStatus::Ok, "{:?}", result.error);
        assert_eq!(result.stdout, "after-bounds\n");
        assert!(result.background_output.is_none());

        stop(client).await;
    })
    .await
    .expect("bounded-output kernel round trip exceeded the deadline");
}

/// Latency smoke test that reports the round-2 kernel numbers the campaign
/// asks for (cold bootstrap, warm no-op cell, checkpoint write). It asserts
/// only generous upper bounds so a loaded host cannot make it flaky; the real
/// values are printed for the report.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn kernel_bootstrap_and_warm_cell_latency_are_reported() {
    use std::time::Instant;
    tokio::time::timeout(Duration::from_secs(240), async {
        let root = tempfile::Builder::new().prefix("optimus-ipython-latency-").tempdir().unwrap();
        let owned = kernel(&root.path().join("state"));
        let client = std::sync::Arc::clone(&owned.0);

        let cold = Instant::now();
        let result = execute(&client, "1 + 1")
            .await;
        assert_eq!(result.status, ExecuteStatus::Ok, "{:?}", result.error);
        let cold_ms = cold.elapsed().as_secs_f64() * 1000.0;
        eprintln!("kernel cold cell (spawn + first result): {cold_ms:.0} ms");

        let mut warm = Vec::new();
        for _ in 0..5 {
            let started = Instant::now();
            let result = execute(&client, "pass").await;
            assert_eq!(result.status, ExecuteStatus::Ok, "{:?}", result.error);
            warm.push(started.elapsed().as_secs_f64() * 1000.0);
        }
        warm.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let median = warm[warm.len() / 2];
        eprintln!(
            "kernel warm no-op cell: median {median:.1} ms (min {:.1}, max {:.1})",
            warm[0],
            warm[warm.len() - 1]
        );
        assert!(
            median < 5_000.0,
            "a warm no-op cell must not take seconds: {median:.1} ms"
        );

        let started = Instant::now();
        let snapshot = std::sync::Arc::clone(&client)
            .snapshot_state()
            .await
            .expect("checkpoint write");
        let snapshot_ms = started.elapsed().as_secs_f64() * 1000.0;
        eprintln!(
            "kernel checkpoint write: {snapshot_ms:.1} ms (saved: {}, bytes: {})",
            snapshot.saved.len(),
            snapshot.bytes
        );
        assert!(snapshot.bytes > 0, "a checkpoint payload must be written");

        stop(&client).await;
    })
    .await
    .expect("kernel latency round trip exceeded the deadline");
}
