use pi_coding_agent::core::tools::node::NodeRuntime;
use std::time::Duration;
use tokio_util::sync::CancellationToken;

fn text(result: &pi_agent_core::types::AgentToolResult) -> String {
    serde_json::to_string(&result.content).unwrap()
}

#[tokio::test]
async fn persistent_javascript_await_files_modules_errors_and_reset() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let runtime = NodeRuntime::default();
    let first = runtime
        .execute(
            cwd,
            "const counter = 41; console.log('ready'); counter",
            10.,
            None,
            None,
        )
        .await
        .unwrap();
    assert!(text(&first).contains("ready") && text(&first).contains("41"));
    let second = runtime
        .execute(cwd, "await Promise.resolve(counter + 1)", 10., None, None)
        .await
        .unwrap();
    assert!(text(&second).contains("42"));
    let io = runtime.execute(cwd, "var fs = require('node:fs/promises'); await fs.writeFile('node-test.txt', '8080'); await fs.readFile('node-test.txt', 'utf8')", 10., None, None).await.unwrap();
    assert!(text(&io).contains("8080"));
    let import = runtime
        .execute(
            cwd,
            "var imported = await nodeImport('node:path'); imported.basename('/tmp/test')",
            10.,
            None,
            None,
        )
        .await
        .unwrap();
    assert!(text(&import).contains("test"));
    let package = dir.path().join("node_modules/optimus-esm-fixture");
    std::fs::create_dir_all(&package).unwrap();
    std::fs::write(
        package.join("package.json"),
        r#"{"type":"module","exports":{"import":"./index.mjs"}}"#,
    )
    .unwrap();
    std::fs::write(package.join("index.mjs"), "export default 42;").unwrap();
    let esm = runtime
        .execute(
            cwd,
            "(await nodeImport('optimus-esm-fixture')).default",
            10.,
            None,
            None,
        )
        .await
        .unwrap();
    assert_eq!(esm.is_error, Some(false));
    assert!(text(&esm).contains("42"));
    let error = runtime
        .execute(cwd, "throw new Error('deliberate')", 10., None, None)
        .await
        .unwrap();
    assert_eq!(error.is_error, Some(true));
    assert!(text(
        &runtime
            .execute(cwd, "counter + 2", 10., None, None)
            .await
            .unwrap()
    )
    .contains("43"));
    let timeout = runtime
        .execute(cwd, "while (true) {}", 0.1, None, None)
        .await
        .unwrap_err();
    assert!(timeout.contains("timed out") && timeout.contains("reset"));
    let reset = runtime
        .execute(cwd, "typeof counter", 10., None, None)
        .await
        .unwrap();
    assert!(text(&reset).contains("undefined"));
    runtime.dispose().await;
}

#[tokio::test]
async fn cancellation_and_large_output_do_not_jam_the_next_cell() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let runtime = NodeRuntime::default();
    let large = runtime
        .execute(cwd, "console.log('🌄'.repeat(100000)); 42", 10., None, None)
        .await
        .unwrap();
    assert!(text(&large).len() < 70000);
    let token = CancellationToken::new();
    let cancel = token.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        cancel.cancel();
    });
    let cancelled = tokio::time::timeout(
        Duration::from_secs(5),
        runtime.execute(cwd, "await new Promise(() => {})", 60., Some(token), None),
    )
    .await
    .unwrap()
    .unwrap_err();
    assert!(cancelled.contains("cancelled") && cancelled.contains("reset"));
    assert!(text(
        &runtime
            .execute(cwd, "21 * 2", 10., None, None)
            .await
            .unwrap()
    )
    .contains("42"));
    runtime.dispose().await;
}

#[tokio::test]
async fn dropped_execution_future_discards_busy_workspace() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let runtime = NodeRuntime::default();
    assert!(tokio::time::timeout(
        Duration::from_millis(200),
        runtime.execute(
            cwd,
            "const abandoned = true; while(true) {}",
            60.,
            None,
            None
        )
    )
    .await
    .is_err());
    let next = tokio::time::timeout(
        Duration::from_secs(5),
        runtime.execute(cwd, "typeof abandoned", 10., None, None),
    )
    .await
    .unwrap()
    .unwrap();
    assert!(text(&next).contains("undefined"));
    runtime.dispose().await;
}

/// A cell can leave asynchronous work running (a timer, a listener). When that
/// work throws after the cell already reported `done`, Node's default
/// `uncaughtException` handling terminates the whole worker process, so every
/// later cell of the session fails with "Node workspace exited" even though the
/// user did nothing wrong in those cells. The workspace must survive a deferred
/// error from an earlier cell and keep its bindings.
#[tokio::test]
async fn deferred_async_error_from_one_cell_does_not_break_later_cells() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let runtime = NodeRuntime::default();
    let first = runtime
        .execute(
            cwd,
            "var survived = 'binding'; setTimeout(() => { throw new Error('late boom'); }, 20); survived",
            10.,
            None,
            None,
        )
        .await
        .unwrap();
    assert!(text(&first).contains("binding"));
    // Let the detached timer fire outside any cell.
    tokio::time::sleep(Duration::from_millis(400)).await;
    let next = tokio::time::timeout(
        Duration::from_secs(10),
        runtime.execute(cwd, "survived", 10., None, None),
    )
    .await
    .expect("the workspace must still answer after a deferred error")
    .expect("a deferred error from an earlier cell must not kill the workspace");
    assert_eq!(next.is_error, Some(false), "{}", text(&next));
    assert!(text(&next).contains("binding"), "{}", text(&next));
    runtime.dispose().await;
}

/// The deferred error must not vanish with the cell that scheduled it: the next
/// result carries a visible background notice that names the error and says the
/// cell was not the failure, and the structured details carry the same notice.
#[tokio::test]
async fn deferred_async_error_is_reported_as_a_background_notice() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let runtime = NodeRuntime::default();
    runtime
        .execute(
            cwd,
            "var noticeProbe = 1; setTimeout(() => { throw new Error('late probe'); }, 60); noticeProbe",
            10.,
            None,
            None,
        )
        .await
        .unwrap();
    tokio::time::sleep(Duration::from_millis(400)).await;
    let next = tokio::time::timeout(
        Duration::from_secs(10),
        runtime.execute(cwd, "noticeProbe + 1", 10., None, None),
    )
    .await
    .expect("the workspace must still answer after a deferred error")
    .expect("a deferred error from an earlier cell must not kill the workspace");
    assert_eq!(next.is_error, Some(false), "{}", text(&next));
    let rendered = text(&next);
    assert!(rendered.contains("late probe"), "{rendered}");
    assert!(rendered.contains("Node background error"), "{rendered}");
    assert!(rendered.contains("2"), "{rendered}");
    assert_eq!(
        next.details["backgroundNotices"].as_array().map(Vec::len),
        Some(1),
        "{}",
        next.details
    );
    assert_eq!(
        next.details["backgroundNotices"][0]["kind"],
        "uncaughtException"
    );
    runtime.dispose().await;
}

/// User code that exits the workspace is a cell failure, not a transport
/// accident: the message must say the workspace exited (not that the peer
/// "returned an oversized response"), and the next cell must get a fresh
/// workspace instead of hanging on the dead pipe.
#[tokio::test]
async fn process_exit_reports_the_exit_and_the_next_cell_recovers() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let runtime = NodeRuntime::default();
    let error = runtime
        .execute(cwd, "process.exit(3)", 15., None, None)
        .await
        .expect_err("an exiting cell cannot succeed");
    assert!(
        error.contains("exited"),
        "the error must name the exit, got: {error}"
    );
    assert!(
        !error.contains("oversized response"),
        "an exit is not an oversized response, got: {error}"
    );
    let next = tokio::time::timeout(
        Duration::from_secs(15),
        runtime.execute(cwd, "6 * 7", 10., None, None),
    )
    .await
    .expect("a fresh workspace must start after the previous one exited")
    .expect("cells must run again");
    assert!(text(&next).contains("42"), "{}", text(&next));
    runtime.dispose().await;
}

/// Two cells submitted at the same time share one workspace, so they must run
/// one after the other and each result must carry only its own output. A frame
/// from the other cell would be a protocol mismatch, so this also proves the
/// request ids stay paired with their responses.
#[tokio::test]
async fn concurrent_cells_are_serialized_with_their_own_output() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().to_str().unwrap();
    let runtime = std::sync::Arc::new(NodeRuntime::default());
    let slow = runtime.clone();
    let slow_cwd = cwd.to_owned();
    let fast = runtime.clone();
    let fast_cwd = cwd.to_owned();
    let slow_cell = tokio::spawn(async move {
        slow.execute(
            &slow_cwd,
            "await new Promise(resolve => setTimeout(resolve, 150)); console.log('slow-cell'); 'slow'",
            20.,
            None,
            None,
        )
        .await
    });
    let fast_cell = tokio::spawn(async move {
        fast.execute(
            &fast_cwd,
            "console.log('fast-cell'); 'fast'",
            20.,
            None,
            None,
        )
        .await
    });
    let slow_result = tokio::time::timeout(Duration::from_secs(30), slow_cell)
        .await
        .expect("slow cell must finish")
        .expect("slow task must not panic")
        .expect("slow cell must succeed");
    let fast_result = tokio::time::timeout(Duration::from_secs(30), fast_cell)
        .await
        .expect("fast cell must finish")
        .expect("fast task must not panic")
        .expect("fast cell must succeed");
    let slow_text = text(&slow_result);
    let fast_text = text(&fast_result);
    assert!(
        slow_text.contains("slow-cell") && !slow_text.contains("fast-cell"),
        "{slow_text}"
    );
    assert!(
        fast_text.contains("fast-cell") && !fast_text.contains("slow-cell"),
        "{fast_text}"
    );
    runtime.dispose().await;
}
