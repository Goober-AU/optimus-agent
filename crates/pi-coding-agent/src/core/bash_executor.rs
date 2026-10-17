//! Port of packages/coding-agent/src/core/bash-executor.ts
//!
//! Bash command execution with streaming support and cancellation.
//!
//! This module provides a unified bash execution implementation used by:
//! - AgentSession.executeBash() for interactive and RPC modes
//! - Direct calls from modes that need bash execution

use std::sync::{Arc, Mutex};

use tokio_util::sync::CancellationToken;

use crate::core::tools::bash::{BashExecOptions, BashExecResult, BashOperations};
use crate::core::tools::output_accumulator::OutputSpill;
use crate::core::tools::truncate::{truncate_tail, TruncationOptions, DEFAULT_MAX_BYTES};
use crate::utils::shell::sanitize_binary_output;

/// Streaming UTF-8 decoder mirroring `new TextDecoder()` with `{ stream: true }`:
/// a multibyte character split across pipe chunks is buffered until its
/// continuation arrives. Same pattern as the private
/// `output_accumulator::Utf8StreamDecoder`; a lossy per-chunk decode cannot
/// do this and replaces both halves of a split character.
struct Utf8StreamDecoder {
    pending: Vec<u8>,
}

impl Utf8StreamDecoder {
    fn new() -> Self {
        Self {
            pending: Vec::new(),
        }
    }

    fn decode(&mut self, chunk: &[u8], stream: bool) -> String {
        self.pending.extend_from_slice(chunk);
        let mut out = String::new();
        let mut index = 0usize;
        let bytes = self.pending.clone();
        while index < bytes.len() {
            match std::str::from_utf8(&bytes[index..]) {
                Ok(valid) => {
                    out.push_str(valid);
                    index = bytes.len();
                    break;
                }
                Err(error) => {
                    let valid_up_to = error.valid_up_to();
                    if valid_up_to > 0 {
                        out.push_str(
                            std::str::from_utf8(&bytes[index..index + valid_up_to])
                                .expect("valid prefix"),
                        );
                        index += valid_up_to;
                    }
                    let incomplete = error.error_len().is_none();
                    if incomplete && stream {
                        // Keep the incomplete trailing sequence for the next chunk.
                        break;
                    }
                    if error.error_len().is_none() {
                        // Flush: an unterminated sequence at the end is replaced.
                        out.push('\u{FFFD}');
                        index = bytes.len();
                        break;
                    }
                    let error_len = error.error_len().expect("error length");
                    out.push('\u{FFFD}');
                    index += error_len;
                }
            }
        }
        self.pending = bytes[index.min(bytes.len())..].to_vec();
        out
    }
}

/// Push one sanitized text segment into the spill, the bounded chunk list and
/// the on_chunk stream. Shared by the per-chunk handler and the terminal
/// decoder flush so both keep identical bookkeeping.
fn emit_sanitized_text(
    text: &str,
    chunks: &Arc<Mutex<Vec<String>>>,
    bytes: &Arc<Mutex<usize>>,
    spill: &Arc<Mutex<OutputSpill>>,
    max_output_bytes: usize,
    on_chunk: Option<&Arc<dyn Fn(&str) + Send + Sync>>,
) {
    spill.lock().expect("spill").write(text.as_bytes());
    {
        let mut chunk_list = chunks.lock().expect("chunks");
        chunk_list.push(text.to_string());
        let mut byte_count = bytes.lock().expect("output bytes");
        *byte_count += text.len();
        while *byte_count > max_output_bytes && chunk_list.len() > 1 {
            let removed = chunk_list.remove(0);
            *byte_count -= removed.len();
        }
    }
    if let Some(on_chunk) = on_chunk {
        on_chunk(text);
    }
}

/// Options for executing shell commands.
pub struct BashExecutorOptions {
    /// Callback for streaming output chunks (already sanitized)
    pub on_chunk: Option<Arc<dyn Fn(&str) + Send + Sync>>,
    /// AbortSignal for cancellation
    pub signal: Option<CancellationToken>,
}

impl Default for BashExecutorOptions {
    fn default() -> Self {
        Self {
            on_chunk: None,
            signal: None,
        }
    }
}

/// Result of executing a shell command.
#[derive(Debug, Clone, PartialEq)]
pub struct BashResult {
    /// Combined stdout + stderr output (sanitized, possibly truncated)
    pub output: String,
    /// Process exit code (None if killed/cancelled)
    pub exit_code: Option<i64>,
    /// Whether the command was cancelled via signal
    pub cancelled: bool,
    /// Whether the output was truncated
    pub truncated: bool,
    /// Path to temp file containing full output (if output exceeded truncation threshold)
    pub full_output_path: Option<String>,
}

/// Execute a bash command using custom [`BashOperations`].
/// Used for remote execution (SSH, containers, etc.).
pub async fn execute_bash_with_operations(
    command: &str,
    cwd: &str,
    operations: Arc<dyn BashOperations>,
    options: Option<BashExecutorOptions>,
) -> Result<BashResult, String> {
    let options = options.unwrap_or_default();
    let output_chunks: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let output_bytes = Arc::new(Mutex::new(0usize));
    let max_output_bytes = DEFAULT_MAX_BYTES * 2;

    let spill = Arc::new(Mutex::new(OutputSpill::new("pi-bash")));
    let total_bytes = Arc::new(Mutex::new(0usize));
    let decoder = Arc::new(Mutex::new(Utf8StreamDecoder::new()));

    let on_chunk = options.on_chunk.clone();
    let chunks_for_data = Arc::clone(&output_chunks);
    let bytes_for_data = Arc::clone(&output_bytes);
    let spill_for_data = Arc::clone(&spill);
    let total_for_data = Arc::clone(&total_bytes);
    let decoder_for_data = Arc::clone(&decoder);
    let on_data: Arc<dyn Fn(&[u8]) + Send + Sync> = Arc::new(move |data: &[u8]| {
        {
            let mut total = total_for_data.lock().expect("total bytes");
            *total += data.len();
        }
        // `sanitizeBinaryOutput(stripAnsi(decoder.decode(data, { stream: true })))`
        // plus the CR removal. The decoder buffers a partial UTF-8 sequence
        // until its continuation arrives, so a character split across pipe
        // chunks reassembles instead of double-replacing.
        let decoded = {
            let mut decoder = decoder_for_data.lock().expect("decoder");
            decoder.decode(data, true)
        };
        let text = sanitize_binary_output(&pi_tui::utils::strip_ansi(&decoded)).replace('\r', "");
        if *total_for_data.lock().expect("total bytes") > DEFAULT_MAX_BYTES {
            let replay: Vec<String> = chunks_for_data.lock().expect("chunks").clone();
            spill_for_data
                .lock()
                .expect("spill")
                .open(replay.iter().map(|chunk| chunk.as_bytes()));
        }

        emit_sanitized_text(
            &text,
            &chunks_for_data,
            &bytes_for_data,
            &spill_for_data,
            max_output_bytes,
            on_chunk.as_ref(),
        );
    });

    let exec_result = operations
        .exec(
            command,
            cwd,
            BashExecOptions {
                on_data,
                signal: options.signal.clone(),
                timeout: None,
                env: None,
            },
        )
        .await;

    // The stream ended: a partial sequence the decoder still holds resolves to
    // U+FFFD instead of being dropped silently (same contract as
    // `OutputAccumulator::finish`).
    {
        let trailing = {
            let mut decoder = decoder.lock().expect("decoder");
            decoder.decode(&[], false)
        };
        if !trailing.is_empty() {
            let text =
                sanitize_binary_output(&pi_tui::utils::strip_ansi(&trailing)).replace('\r', "");
            emit_sanitized_text(
                &text,
                &output_chunks,
                &output_bytes,
                &spill,
                max_output_bytes,
                options.on_chunk.as_ref(),
            );
        }
    }

    let full_output = || output_chunks.lock().expect("chunks").join("");

    match exec_result {
        Ok(BashExecResult { exit_code }) => {
            let full = full_output();
            let truncation_result = truncate_tail(&full, TruncationOptions::default());
            if truncation_result.truncated {
                let replay: Vec<String> = output_chunks.lock().expect("chunks").clone();
                spill
                    .lock()
                    .expect("spill")
                    .open(replay.iter().map(|chunk| chunk.as_bytes()));
            }
            // Settled before advertising: the path refers to the COMPLETE file or is undefined.
            let full_output_path = spill.lock().expect("spill").finalize();
            // Completion state comes from the resolved exit code, captured
            // before the signal is sampled: a token firing between the
            // operation resolving and this check must not retroactively cancel
            // a command whose side effects already landed. Only an unresolved
            // kill (`None`) may still be attributed to the token.
            let cancelled = exit_code.is_none()
                && options
                    .signal
                    .as_ref()
                    .map(CancellationToken::is_cancelled)
                    .unwrap_or(false);

            Ok(BashResult {
                output: if truncation_result.truncated {
                    truncation_result.content
                } else {
                    full
                },
                exit_code: exit_code.map(i64::from),
                cancelled,
                truncated: truncation_result.truncated,
                full_output_path,
            })
        }
        Err(error) => {
            if options
                .signal
                .as_ref()
                .map(CancellationToken::is_cancelled)
                .unwrap_or(false)
            {
                let full = full_output();
                let truncation_result = truncate_tail(&full, TruncationOptions::default());
                if truncation_result.truncated {
                    let replay: Vec<String> = output_chunks.lock().expect("chunks").clone();
                    spill
                        .lock()
                        .expect("spill")
                        .open(replay.iter().map(|chunk| chunk.as_bytes()));
                }
                let full_output_path = spill.lock().expect("spill").finalize();
                return Ok(BashResult {
                    output: if truncation_result.truncated {
                        truncation_result.content
                    } else {
                        full
                    },
                    exit_code: None,
                    cancelled: true,
                    truncated: truncation_result.truncated,
                    full_output_path,
                });
            }

            let _ = spill.lock().expect("spill").finalize();

            Err(error)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::future::BoxFuture;

    struct FakeOperations {
        chunks: Vec<Vec<u8>>,
        exit_code: Option<i32>,
        fail: bool,
    }

    impl BashOperations for FakeOperations {
        fn exec(
            &self,
            _command: &str,
            _cwd: &str,
            options: BashExecOptions,
        ) -> BoxFuture<'static, Result<BashExecResult, String>> {
            let chunks = self.chunks.clone();
            let exit_code = self.exit_code;
            let fail = self.fail;
            Box::pin(async move {
                for chunk in chunks {
                    (options.on_data)(&chunk);
                }
                if fail {
                    return Err("boom".to_string());
                }
                Ok(BashExecResult { exit_code })
            })
        }
    }

    #[tokio::test]
    async fn streams_and_returns_the_exit_code() {
        let operations = Arc::new(FakeOperations {
            chunks: vec![b"hello\n".to_vec()],
            exit_code: Some(0),
            fail: false,
        });
        let seen: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let seen_clone = Arc::clone(&seen);
        let result = execute_bash_with_operations(
            "cmd",
            "/tmp",
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
        assert_eq!(result.output, "hello\n");
        assert_eq!(result.exit_code, Some(0));
        assert!(!result.cancelled);
        assert!(!result.truncated);
        assert!(result.full_output_path.is_none());
        assert_eq!(seen.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn strips_carriage_returns() {
        let operations = Arc::new(FakeOperations {
            chunks: vec![b"a\r\nb".to_vec()],
            exit_code: Some(0),
            fail: false,
        });
        let result = execute_bash_with_operations("cmd", "/tmp", operations, None)
            .await
            .unwrap();
        assert_eq!(result.output, "a\nb");
    }

    #[tokio::test]
    async fn cancellation_reports_no_exit_code() {
        let token = CancellationToken::new();
        token.cancel();
        let operations = Arc::new(FakeOperations {
            chunks: vec![b"partial".to_vec()],
            exit_code: None,
            fail: true,
        });
        let result = execute_bash_with_operations(
            "cmd",
            "/tmp",
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

    #[tokio::test]
    async fn errors_propagate_without_cancellation() {
        let operations = Arc::new(FakeOperations {
            chunks: Vec::new(),
            exit_code: None,
            fail: true,
        });
        let error = execute_bash_with_operations("cmd", "/tmp", operations, None)
            .await
            .unwrap_err();
        assert_eq!(error, "boom");
    }

    #[tokio::test]
    async fn large_output_is_truncated_and_spilled() {
        let big = "x".repeat(DEFAULT_MAX_BYTES + 1024);
        let operations = Arc::new(FakeOperations {
            chunks: vec![big.into_bytes()],
            exit_code: Some(0),
            fail: false,
        });
        let result = execute_bash_with_operations("cmd", "/tmp", operations, None)
            .await
            .unwrap();
        assert!(result.truncated);
        assert!(result.output.len() <= DEFAULT_MAX_BYTES);
        let path = result.full_output_path.expect("full output path");
        assert!(std::path::Path::new(&path).exists());
        let _ = std::fs::remove_file(path);
    }

    /// Fake backend that can fire the cancellation token at the resolution
    /// boundary: the work (and the exit code) exists, the token fires only
    /// after it, exactly when a late cancellation races the report.
    struct ResolvingOperations {
        chunks: Vec<Vec<u8>>,
        exit_code: Option<i32>,
        cancel_before_resolve: Option<CancellationToken>,
    }

    impl BashOperations for ResolvingOperations {
        fn exec(
            &self,
            _command: &str,
            _cwd: &str,
            options: BashExecOptions,
        ) -> BoxFuture<'static, Result<BashExecResult, String>> {
            let chunks = self.chunks.clone();
            let exit_code = self.exit_code;
            let cancel = self.cancel_before_resolve.clone();
            Box::pin(async move {
                for chunk in chunks {
                    (options.on_data)(&chunk);
                }
                if let Some(token) = cancel {
                    // The command finished; the token fires after the work
                    // completed but before the executor samples the signal.
                    token.cancel();
                }
                Ok(BashExecResult { exit_code })
            })
        }
    }

    // S-1: a multibyte character split across two pipe chunks must reassemble,
    // not become two U+FFFD replacements, and must stream without partial
    // replacement characters.
    #[tokio::test]
    async fn split_utf8_chunks_reassemble_in_executor() {
        let operations = Arc::new(FakeOperations {
            chunks: vec![b"h\xc3".to_vec(), b"\xa9llo".to_vec()],
            exit_code: Some(0),
            fail: false,
        });
        let seen: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let seen_clone = Arc::clone(&seen);
        let result = execute_bash_with_operations(
            "cmd",
            "/tmp",
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
        let seen = seen.lock().unwrap().clone();
        assert_eq!(
            seen,
            vec!["h".to_string(), "\u{e9}llo".to_string()],
            "the split character must ride with its continuation chunk"
        );
        assert!(
            !seen.iter().any(|chunk| chunk.contains('\u{FFFD}')),
            "{seen:?}"
        );
    }

    // S-1: the spill file is written from the sanitized stream, so the same
    // split character must land there byte-identical, not double-replaced.
    #[tokio::test]
    async fn split_utf8_reaches_the_spill_file_intact() {
        let filler = "x".repeat(DEFAULT_MAX_BYTES + 1);
        let operations = Arc::new(FakeOperations {
            chunks: vec![
                filler.clone().into_bytes(),
                b"h\xc3".to_vec(),
                b"\xa9llo".to_vec(),
            ],
            exit_code: Some(0),
            fail: false,
        });
        let result = execute_bash_with_operations("cmd", "/tmp", operations, None)
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

    // A stream that ends mid-sequence flushes to a single replacement, the
    // same contract as `OutputAccumulator::finish`.
    #[tokio::test]
    async fn a_trailing_partial_sequence_flushes_to_a_replacement() {
        let operations = Arc::new(FakeOperations {
            chunks: vec![b"ok".to_vec(), b" \xc3".to_vec()],
            exit_code: Some(0),
            fail: false,
        });
        let result = execute_bash_with_operations("cmd", "/tmp", operations, None)
            .await
            .unwrap();
        assert_eq!(result.output, "ok \u{FFFD}");
    }

    // S-4: a token firing after the operation resolved must not retroactively
    // cancel a completed command or discard its real exit code.
    #[tokio::test]
    async fn a_token_firing_after_resolution_does_not_cancel_a_completed_command() {
        let token = CancellationToken::new();
        let operations = Arc::new(ResolvingOperations {
            chunks: vec![b"done\n".to_vec()],
            exit_code: Some(0),
            cancel_before_resolve: Some(token.clone()),
        });
        let result = execute_bash_with_operations(
            "cmd",
            "/tmp",
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

    // An unresolved kill (`exit_code: None`) still attributes a cancelled
    // token: the kill cannot be distinguished from the cancellation itself.
    #[tokio::test]
    async fn an_unresolved_kill_still_reports_a_cancelled_token() {
        let token = CancellationToken::new();
        token.cancel();
        let operations = Arc::new(ResolvingOperations {
            chunks: vec![b"partial".to_vec()],
            exit_code: None,
            cancel_before_resolve: Some(token.clone()),
        });
        let result = execute_bash_with_operations(
            "cmd",
            "/tmp",
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
}
