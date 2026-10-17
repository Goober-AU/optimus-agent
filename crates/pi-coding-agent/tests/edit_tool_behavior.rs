//! End-to-end behavior tests for the edit tool, `mcp_call_` argument
//! validation, CRLF-aware output truncation, and the Node workspace worker.
//!
//! These tests drive the public tool entry points with real arguments instead
//! of the in-file unit tests, covering the full chain from tool input to file
//! contents on disk.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use pi_coding_agent::core::kernel::shared::KernelManagerOptions;
use pi_coding_agent::core::tools::acp_mcp::{
    create_acp_mcp_tool_definitions, mcp_call_python_code, validate_mcp_call_arguments,
    AcpMcpServerConfig,
};
use pi_coding_agent::core::tools::edit::{
    create_edit_tool, execute_edit, prepare_edit_arguments, EditOperations, EditToolInput,
};
use pi_coding_agent::core::tools::edit_diff::{compute_edits_diff, Edit, EditDiffOutcome};
use pi_coding_agent::core::tools::ipython::{IpythonKernelProvisioner, KernelClient, KernelClientFactory};
use pi_coding_agent::core::tools::truncate::{
    truncate_head, truncate_tail, TruncationOptions, TruncatedBy,
};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio_util::sync::CancellationToken;

fn write_file(dir: &tempfile::TempDir, name: &str, contents: &[u8]) -> PathBuf {
    let path = dir.path().join(name);
    std::fs::write(&path, contents).expect("write fixture");
    path
}

fn edit_input(path: &str, edits: Vec<Edit>) -> EditToolInput {
    EditToolInput {
        path: path.to_string(),
        edits,
    }
}

fn edit(old_text: &str, new_text: &str) -> Edit {
    Edit {
        old_text: old_text.to_string(),
        new_text: new_text.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Edit tool: end-to-end through execute_edit and the AgentTool definition.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn edit_tool_happy_path_applies_multiple_edits_and_reports_diff() {
    let dir = tempfile::tempdir().expect("temp dir");
    let file = write_file(&dir, "notes.txt", b"alpha\nbeta\ngamma\n");
    let cwd = dir.path().to_string_lossy().into_owned();
    let input = edit_input("notes.txt", vec![edit("beta", "BETA"), edit("gamma", "GAMMA")]);

    let (message, details) = execute_edit(&cwd, None, &input, None).await.expect("edited");

    assert_eq!(message, "Successfully replaced 2 block(s) in notes.txt.");
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "alpha\nBETA\nGAMMA\n");
    assert!(details.diff.contains("-2 beta"), "diff: {}", details.diff);
    assert!(details.diff.contains("+2 BETA"), "diff: {}", details.diff);
    assert_eq!(details.first_changed_line, Some(2));
}

#[tokio::test]
async fn edit_tool_reports_missing_old_text_and_ambiguous_matches() {
    let dir = tempfile::tempdir().expect("temp dir");
    let file = write_file(&dir, "target.txt", b"one\ntwo\nthree\n");
    let cwd = dir.path().to_string_lossy().into_owned();

    let missing = execute_edit(&cwd, None, &edit_input("target.txt", vec![edit("four", "4")]), None)
        .await
        .expect_err("missing text must fail");
    assert!(
        missing.contains("Could not find the exact text in target.txt"),
        "error: {missing}"
    );
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\nthree\n");

    let ambiguous = execute_edit(&cwd, None, &edit_input("target.txt", vec![edit("t", "T")]), None)
        .await
        .expect_err("ambiguous text must fail");
    assert!(
        ambiguous.contains("Found 2 occurrences of the text in target.txt"),
        "error: {ambiguous}"
    );
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\nthree\n");
}

#[tokio::test]
async fn edit_tool_rejects_non_utf8_files_without_rewriting_them() {
    let dir = tempfile::tempdir().expect("temp dir");
    // 0xE9 is a lone Latin-1 byte: invalid UTF-8.
    let file = write_file(&dir, "latin1.txt", b"caf\xe9\nline\n");
    let cwd = dir.path().to_string_lossy().into_owned();
    let input = edit_input("latin1.txt", vec![edit("line", "LINE")]);

    let error = execute_edit(&cwd, None, &input, None).await.expect_err("non-UTF-8 must fail");
    assert!(
        error.contains("Could not edit file: latin1.txt") && error.contains("not valid UTF-8"),
        "error: {error}"
    );
    // The file must be byte-for-byte unchanged: no U+FFFD replacement written back.
    assert_eq!(std::fs::read(&file).unwrap(), b"caf\xe9\nline\n");

    // The preview must fail with the same non-UTF-8 reason, not a different error.
    let outcome = compute_edits_diff("latin1.txt", &[edit("line", "LINE")], &cwd).await;
    match outcome {
        EditDiffOutcome::Error(error) => {
            assert!(error.error.contains("not valid UTF-8"), "preview error: {}", error.error);
            assert!(error.error.contains("latin1.txt"), "preview error: {}", error.error);
        }
        EditDiffOutcome::Result(result) => panic!("preview must fail, got diff: {}", result.diff),
    }
}

#[tokio::test]
async fn edit_tool_retry_after_success_fails_instead_of_applying_twice() {
    let dir = tempfile::tempdir().expect("temp dir");
    let file = write_file(&dir, "once.txt", b"value = 1\n");
    let cwd = dir.path().to_string_lossy().into_owned();
    let input = edit_input("once.txt", vec![edit("value = 1", "value = 2")]);

    execute_edit(&cwd, None, &input, None).await.expect("first edit");
    let retry = execute_edit(&cwd, None, &input, None).await.expect_err("retry must fail");
    assert!(
        retry.contains("Could not find the exact text in once.txt"),
        "error: {retry}"
    );
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "value = 2\n");
}

/// Cancels the token inside `read_file`, i.e. exactly between the access check
/// and the write, so the second cancellation gate must stop the edit.
struct CancelBetweenAccessAndWrite {
    token: CancellationToken,
}

impl EditOperations for CancelBetweenAccessAndWrite {
    fn read_file(&self, absolute_path: &str) -> std::io::Result<Vec<u8>> {
        self.token.cancel();
        std::fs::read(absolute_path)
    }

    fn write_file(&self, _absolute_path: &str, _content: &str) -> std::io::Result<()> {
        panic!("write_file must not run after cancellation")
    }

    fn access(&self, _absolute_path: &str) -> std::io::Result<()> {
        Ok(())
    }
}

#[tokio::test]
async fn edit_tool_aborts_between_access_and_write_leaving_the_file_unchanged() {
    let dir = tempfile::tempdir().expect("temp dir");
    let file = write_file(&dir, "cancel-mid.txt", b"one\ntwo\n");
    let cwd = dir.path().to_string_lossy().into_owned();
    let token = CancellationToken::new();
    let operations = Arc::new(CancelBetweenAccessAndWrite { token: token.clone() });
    let input = edit_input("cancel-mid.txt", vec![edit("two", "TWO")]);

    let error = execute_edit(&cwd, Some(operations), &input, Some(token))
        .await
        .expect_err("cancelled edit must fail");

    assert_eq!(error, "Operation aborted");
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\n");
}

#[tokio::test]
async fn edit_tool_aborts_before_access_when_already_cancelled() {
    let dir = tempfile::tempdir().expect("temp dir");
    let file = write_file(&dir, "cancel-early.txt", b"one\ntwo\n");
    let cwd = dir.path().to_string_lossy().into_owned();
    let token = CancellationToken::new();
    token.cancel();
    let input = edit_input("cancel-early.txt", vec![edit("two", "TWO")]);

    let error = execute_edit(&cwd, None, &input, Some(token))
        .await
        .expect_err("pre-cancelled edit must fail");

    assert_eq!(error, "Operation aborted");
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\n");
}

#[tokio::test]
async fn edit_tool_end_to_end_through_the_agent_tool_definition() {
    let dir = tempfile::tempdir().expect("temp dir");
    let file = write_file(&dir, "legacy.txt", b"keep\nreplace me\n");
    let cwd = dir.path().to_string_lossy().into_owned();
    let tool = create_edit_tool(&cwd, None);

    // Legacy single-edit shape (oldText/newText), normalized by the same
    // prepare_arguments shim the session applies before schema validation.
    let prepared = prepare_edit_arguments(json!({
        "path": "legacy.txt",
        "oldText": "replace me",
        "newText": "replaced"
    }));
    let result = (tool.execute)("call-1".into(), prepared, None, None).await.expect("tool ran");
    assert_eq!(result.is_error, None);
    let text = result.content.iter().map(|block| block.as_text().unwrap_or("")).collect::<String>();
    assert_eq!(text, "Successfully replaced 1 block(s) in legacy.txt.");
    assert!(result.details["diff"].as_str().unwrap().contains("+2 replaced"));
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "keep\nreplaced\n");

    // Invalid arguments surface as tool errors, not panics.
    let error = (tool.execute)("call-2".into(), json!({ "path": "legacy.txt" }), None, None)
        .await
        .expect_err("missing edits must fail");
    assert!(format!("{error}").contains("edits"), "error: {error}");
}

// ---------------------------------------------------------------------------
// mcp_call_<server>: argument validation and generated kernel code.
// ---------------------------------------------------------------------------

#[test]
fn mcp_call_argument_validation_rejects_invalid_inputs() {
    for params in [
        json!({}),
        json!({"arguments": {}}),
        json!({"tool": 42, "arguments": {}}),
        json!({"tool": null, "arguments": {}}),
        json!({"tool": "", "arguments": {}}),
    ] {
        let error = validate_mcp_call_arguments(&params).expect_err("tool must be rejected");
        assert!(error.contains("\"tool\""), "error: {error} for params {params}");
    }

    for params in [
        json!({"tool": "read_file"}),
        json!({"tool": "read_file", "arguments": null}),
        json!({"tool": "read_file", "arguments": "not an object"}),
        json!({"tool": "read_file", "arguments": [1, 2]}),
    ] {
        let error = validate_mcp_call_arguments(&params).expect_err("arguments must be rejected");
        assert!(error.contains("\"arguments\""), "error: {error} for params {params}");
    }

    let (tool, arguments) =
        validate_mcp_call_arguments(&json!({"tool": "read_file", "arguments": {"path": "a.txt"}}))
            .expect("valid arguments");
    assert_eq!(tool, "read_file");
    assert_eq!(arguments, json!({"path": "a.txt"}));
}

#[test]
fn mcp_call_python_code_quotes_tool_and_arguments() {
    let arguments = json!({"path": "a\"b\nc", "count": 1});
    let code = mcp_call_python_code("files", "read\"file", &arguments);

    let arguments_json = serde_json::to_string(&arguments).unwrap();
    let arguments_literal = serde_json::to_string(&arguments_json).unwrap();
    let expected = format!(
        "print(__import__(\"json\").dumps(await mcp.call_tool(\"files\", \"read\\\"file\", \
         __import__(\"json\").loads({arguments_literal})), default=str))"
    );
    assert_eq!(code, expected);
}

#[tokio::test]
async fn mcp_call_tool_rejects_invalid_arguments_before_touching_the_kernel() {
    // A factory that panics proves invalid arguments never reach the kernel:
    // validation must fail before any process is started.
    let factory: KernelClientFactory = Arc::new(
        |_options: KernelManagerOptions| -> Arc<dyn KernelClient> {
            unreachable!("invalid mcp_call_ arguments must fail before the kernel starts")
        },
    );
    let provisioner = IpythonKernelProvisioner::new(".", None, factory);
    let definitions = create_acp_mcp_tool_definitions(
        &[AcpMcpServerConfig { name: "files".to_string(), extra: serde_json::Map::new() }],
        provisioner,
    )
    .expect("definitions");

    let call = definitions.iter().find(|d| d.name == "mcp_call_files").expect("call tool");
    for params in [
        json!({"tool": 42, "arguments": {}}),
        json!({}),
        json!({"tool": "read_file"}),
        json!({"tool": "read_file", "arguments": "x"}),
    ] {
        let error = (call.execute)(
            "call-1".into(),
            params,
            None,
            None,
            pi_coding_agent::core::tools::ExtensionContext::default(),
        )
        .await
        .expect_err("invalid arguments must fail");
        let message = format!("{error:#}");
        assert!(message.contains("Invalid arguments"), "error: {message}");
    }
}

// ---------------------------------------------------------------------------
// Truncation: CRLF line accounting.
// ---------------------------------------------------------------------------

#[test]
fn truncate_tail_preserves_crlf_terminators_and_reports_exact_bytes() {
    let result = truncate_tail("a\r\nb\r\nc\r\nd", TruncationOptions { max_lines: Some(2), max_bytes: None });
    assert_eq!(result.content, "c\r\nd");
    assert_eq!(result.output_bytes, 4);
    assert_eq!(result.output_bytes, result.content.len());
    assert_eq!(result.truncated_by, Some(TruncatedBy::Lines));

    let result = truncate_tail("aaa\r\nbbb\r\nccc", TruncationOptions { max_lines: None, max_bytes: Some(8) });
    assert_eq!(result.content, "bbb\r\nccc");
    assert_eq!(result.output_bytes, 8);
    assert_eq!(result.output_bytes, result.content.len());
    assert_eq!(result.truncated_by, Some(TruncatedBy::Bytes));

    let result = truncate_tail("a\r\nb\r\nc", TruncationOptions { max_lines: None, max_bytes: Some(5) });
    assert_eq!(result.content, "b\r\nc");
    assert_eq!(result.output_bytes, 4);
    assert_eq!(result.output_bytes, result.content.len());
}

#[test]
fn truncate_head_preserves_crlf_terminators_and_reports_exact_bytes() {
    let result = truncate_head("aaa\r\nbbb\r\nccc", TruncationOptions { max_lines: Some(2), max_bytes: None });
    // The kept lines keep their own "\r"; only the separator of the dropped
    // remainder is not re-inserted.
    assert_eq!(result.content, "aaa\r\nbbb\r");
    assert_eq!(result.output_bytes, result.content.len());
    assert_eq!(result.truncated_by, Some(TruncatedBy::Lines));

    let result = truncate_head("aaa\r\nbbb\r\nccc", TruncationOptions { max_lines: None, max_bytes: Some(7) });
    assert_eq!(result.content, "aaa\r");
    assert_eq!(result.output_bytes, 4);
    assert_eq!(result.output_bytes, result.content.len());
    assert_eq!(result.truncated_by, Some(TruncatedBy::Bytes));
}

#[test]
fn truncate_tail_rescues_oversized_crlf_line_with_trailing_blank_lines() {
    // "\r" is the blank line CRLF content produces; the rescue must recognize
    // it and account its real byte cost (1 byte + separator) in the budget.
    let result = truncate_tail("abcdefghij\r\n\r\n", TruncationOptions { max_lines: None, max_bytes: Some(6) });
    assert!(result.last_line_partial);
    assert_eq!(result.content, "ij\r\n\r\n");
    assert_eq!(result.output_bytes, 6);
    assert_eq!(result.output_bytes, result.content.len());
    assert_eq!(result.output_lines, 3);

    // LF behavior is unchanged.
    let result = truncate_tail("abcdefghij\n\n", TruncationOptions { max_lines: None, max_bytes: Some(6) });
    assert!(result.last_line_partial);
    assert_eq!(result.content, "ghij\n\n");
    assert_eq!(result.output_bytes, 6);
}

#[test]
fn truncate_tail_crlf_multibyte_budget_is_never_overshot() {
    let content = "ééé\r\née\r\née\r\nxyz";
    for max_bytes in 1..=24usize {
        let result = truncate_tail(content, TruncationOptions { max_lines: None, max_bytes: Some(max_bytes) });
        assert_eq!(
            result.output_bytes,
            result.content.len(),
            "output_bytes must equal content bytes for max_bytes {max_bytes}"
        );
        if result.truncated_by == Some(TruncatedBy::Bytes) {
            assert!(
                result.content.len() <= max_bytes,
                "byte budget overshot: {} > {max_bytes}",
                result.content.len()
            );
        }
        assert!(!result.content.contains('\u{FFFD}'), "no replacement chars: {}", result.content);
    }
}

// ---------------------------------------------------------------------------
// Node workspace worker: byte-accurate clipping and truncation markers.
// ---------------------------------------------------------------------------

const WORKER: &str = include_str!("../src/core/tools/node_worker.cjs");
const WORKER_LIMIT: usize = 64 * 1024;
const OUTPUT_MARKER: &str = "[Node output truncated at 64 KiB]";
const RESULT_MARKER: &str = "[Node result truncated at 64 KiB]";

fn node_binary() -> Option<PathBuf> {
    let candidates = [
        "node".to_string(),
        r"C:\Program Files\nodejs\node.exe".to_string(),
        r"C:\Program Files (x86)\nodejs\node.exe".to_string(),
    ];
    for candidate in candidates {
        let status = std::process::Command::new(&candidate)
            .arg("--version")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
        if status.map(|status| status.success()).unwrap_or(false) {
            return Some(PathBuf::from(candidate));
        }
    }
    None
}

/// One fed cell: the emitted output messages and the final done message.
struct WorkerCell {
    output: String,
    done: Value,
}

async fn run_worker_cell(node: &Path, code: &str) -> WorkerCell {
    let mut child = tokio::process::Command::new(node)
        .arg("--input-type=commonjs")
        .arg("-e")
        .arg(WORKER)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .expect("spawn node worker");
    let mut stdin = child.stdin.take().expect("worker stdin");
    let stdout = child.stdout.take().expect("worker stdout");
    let mut reader = BufReader::new(stdout);

    let request = format!("{}\n", serde_json::json!({ "id": "t1", "code": code }));
    stdin.write_all(request.as_bytes()).await.expect("write request");
    // Keep stdin open until the done message arrives so the worker does not
    // exit early on EOF.

    let mut output = String::new();
    let done = loop {
        let mut line = String::new();
        let read = tokio::time::timeout(Duration::from_secs(60), reader.read_line(&mut line))
            .await
            .expect("worker response timed out")
            .expect("read worker line");
        if read == 0 {
            panic!("worker exited before the done message; output so far: {output}");
        }
        let message: Value = serde_json::from_str(line.trim()).expect("worker message JSON");
        if message.get("ready").is_some() {
            continue;
        }
        if let Some(fatal) = message.get("fatal") {
            panic!("worker fatal error: {fatal}");
        }
        if let Some(text) = message.get("output").and_then(Value::as_str) {
            output.push_str(text);
        }
        if message.get("done") == Some(&Value::Bool(true)) {
            break message;
        }
    };
    drop(stdin);
    let _ = tokio::time::timeout(Duration::from_secs(10), child.wait()).await;
    WorkerCell { output, done }
}

#[tokio::test]
async fn node_worker_reports_small_output_and_result_without_markers() {
    let Some(node) = node_binary() else {
        eprintln!("skipping: node not found on PATH or in default install locations");
        return;
    };
    let cell = run_worker_cell(&node, "console.log('ok'); 42").await;
    assert_eq!(cell.output, "ok\n");
    assert_eq!(cell.done["error"], Value::Bool(false));
    assert_eq!(cell.done["result"].as_str().unwrap(), "42");
    assert!(!cell.output.contains(OUTPUT_MARKER));
    assert!(!cell.done["result"].as_str().unwrap().contains(RESULT_MARKER));
}

#[tokio::test]
async fn node_worker_clips_multibyte_output_in_bytes_and_marks_truncation() {
    let Some(node) = node_binary() else {
        eprintln!("skipping: node not found on PATH or in default install locations");
        return;
    };
    // 100k two-byte characters: a code-unit clip would overshoot the byte
    // budget by 2x; a byte clip must land on a character boundary.
    let cell = run_worker_cell(&node, "console.log('é'.repeat(100000))").await;
    assert!(cell.output.contains(OUTPUT_MARKER), "truncation marker missing");
    let clipped = cell.output.split(&format!("\n{OUTPUT_MARKER}")).next().unwrap().to_string();
    assert!(clipped.len() <= WORKER_LIMIT, "clipped output {} > limit", clipped.len());
    assert!(clipped.len() >= WORKER_LIMIT - 8, "clipped output {} too small", clipped.len());
    assert!(!cell.output.contains('\u{FFFD}'), "replacement character introduced");
    assert!(
        clipped.chars().all(|c| c == 'é' || c == '\n'),
        "unexpected characters in clipped output"
    );
}

#[tokio::test]
async fn node_worker_clips_result_and_marks_truncation() {
    let Some(node) = node_binary() else {
        eprintln!("skipping: node not found on PATH or in default install locations");
        return;
    };
    // inspect() renders ~8.2 KB per element, so the result exceeds 64 KiB.
    // Contract: the 64 KiB budget caps the clipped content; the truncation
    // notice ("\n[marker]\n", 35 bytes) is appended outside that budget, so
    // the total is content + notice (matches f11's node worker pinning test).
    let cell = run_worker_cell(&node, "Array(30).fill('y'.repeat(8192))").await;
    let result = cell.done["result"].as_str().expect("result text");
    assert_eq!(cell.done["error"], Value::Bool(false));
    let notice = format!("\n{RESULT_MARKER}\n");
    assert!(result.ends_with(&notice), "truncation notice missing or misplaced at the end of the result");
    let clipped = result.strip_suffix(&notice).expect("notice-suffixed result");
    assert_eq!(clipped.len(), WORKER_LIMIT, "clipped result must fill the byte budget exactly");
    assert_eq!(result.len(), clipped.len() + notice.len(), "total is content plus notice");
    assert!(clipped.starts_with("[\n  'yyy"), "unexpected result head: {}", &clipped[..16]);
}

#[tokio::test]
async fn node_worker_never_splits_surrogate_pairs_when_clipping() {
    let Some(node) = node_binary() else {
        eprintln!("skipping: node not found on PATH or in default install locations");
        return;
    };
    // The leading 'x' shifts the clip position between the surrogates of the
    // first clipped character; a code-unit slice would emit a lone surrogate
    // that breaks the JSON protocol, a byte clip backs off to the boundary.
    let cell = run_worker_cell(&node, "console.log('x' + '🌄'.repeat(100000))").await;
    assert!(cell.output.contains(OUTPUT_MARKER), "truncation marker missing");
    assert!(!cell.output.contains('\u{FFFD}'), "replacement character introduced");
    let clipped = cell.output.split(&format!("\n{OUTPUT_MARKER}")).next().unwrap();
    assert!(clipped.len() <= WORKER_LIMIT, "clipped output {} > limit", clipped.len());
    assert!(
        clipped.chars().all(|c| c == 'x' || c == '🌄' || c == '\n'),
        "output contains a split surrogate pair"
    );
}
