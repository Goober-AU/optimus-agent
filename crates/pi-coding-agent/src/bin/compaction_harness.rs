//! CompactBench harness: one-shot real-compaction driver (context-rot r2).
//!
//! Reads one compact-request JSON on stdin, appends the new turns to the
//! per-case session directory, runs the REAL production pipeline
//! (`prepare_compaction` -> `compact` -> `append_compaction` ->
//! `SessionManager` restore), and writes one compact-response JSON on stdout.
//! Exit code 0 on `ok`/`unsafe`, 1 on transport/schema failure.
//!
//! The summarizer is the deterministic lossy fixture registered through
//! `register_api_provider_simple` (same fixture as
//! `tests/rot2_multicycle.rs`): it copies protected ledger blocks verbatim
//! and applies a fixed head/tail loss elsewhere. A live endpoint mode is
//! reserved for the online phase and refuses to run until wired.
//!
//! Offline by construction: no network, all state under `session_dir`.

use std::io::{Read, Write};
use std::sync::Arc;

use pi_agent_core::types::{AgentMessage, CustomMessageContent};
use pi_ai::api_registry::{register_api_provider_simple, ApiProviderSimple};
use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, ContentBlock, Message, Model, TextContent, Usage,
    UserContent, UserMessage,
};
use pi_ai::utils::event_stream::AssistantMessageEventStream;
use pi_coding_agent::core::compaction::compaction::{
    compact, default_compaction_settings, default_summary_call_runner, estimate_context_tokens,
    prepare_compaction, CompactionSessionEntry, CompactionSettings,
};
use pi_coding_agent::core::session_manager::{SessionEntry, SessionManager};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Request / response schema (see COMPACTBENCH-ADAPTER-DESIGN.md 2.2)
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HarnessTurn {
    role: String,
    content: String,
    turn_id: i64,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct HarnessSettings {
    #[serde(default = "default_reserve")]
    reserve_tokens: f64,
    #[serde(default = "default_keep_recent")]
    keep_recent_tokens: f64,
    #[serde(default = "default_task_state")]
    task_state: Option<String>,
}

fn default_reserve() -> f64 {
    4_000.0
}
fn default_keep_recent() -> f64 {
    20.0
}
fn default_task_state() -> Option<String> {
    Some("off".to_string())
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HarnessRequest {
    session_dir: String,
    #[serde(default)]
    new_turns: Vec<HarnessTurn>,
    #[serde(default)]
    settings: HarnessSettings,
    /// `null` or `"fixture"` selects the deterministic lossy summarizer.
    /// Any other value is refused: the live endpoint mode is not wired yet.
    summarizer: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct HarnessResponse {
    status: String,
    summary: String,
    first_kept_entry_id: String,
    tokens_before: f64,
    compacted_context_tokens: f64,
    ledger: Value,
    summarized_turn_ids: Vec<i64>,
    kept_turn_ids: Vec<i64>,
    warnings: Vec<String>,
    error: Option<String>,
}

// ---------------------------------------------------------------------------
// Session seeding (same shapes as tests/rot2_multicycle.rs)
// ---------------------------------------------------------------------------

fn user_message(text: &str) -> AgentMessage {
    AgentMessage::Message(Message::User(UserMessage::new(
        UserContent::Text(text.into()),
        0,
    )))
}

fn assistant_message(text: &str, usage_input: f64) -> AgentMessage {
    AgentMessage::Message(Message::Assistant(AssistantMessage {
        content: vec![ContentBlock::Text(TextContent::new(text))],
        stop_reason: "stop".into(),
        usage: Usage {
            input: usage_input,
            ..Default::default()
        },
        ..Default::default()
    }))
}

fn compaction_entry(entry: &SessionEntry) -> Option<CompactionSessionEntry> {
    let entry_type = entry.get("type").and_then(Value::as_str)?;
    let id = entry.get("id").and_then(Value::as_str)?.to_string();
    let parent_id = entry
        .get("parentId")
        .and_then(Value::as_str)
        .map(str::to_string);
    match entry_type {
        "message" => Some(CompactionSessionEntry::Message {
            id,
            parent_id,
            message: serde_json::from_value::<AgentMessage>(
                entry.get("message").cloned().unwrap_or(Value::Null),
            )
            .ok()?,
        }),
        "custom_message" => Some(CompactionSessionEntry::CustomMessage {
            id,
            parent_id,
            custom_type: entry
                .get("customType")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            content: serde_json::from_value(entry.get("content").cloned().unwrap_or(Value::Null))
                .unwrap_or(CustomMessageContent::Text(String::new())),
            details: entry.get("details").cloned(),
            display: entry.get("display").and_then(Value::as_bool).unwrap_or(false),
            timestamp: entry
                .get("timestamp")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_default(),
        }),
        "compaction" => Some(CompactionSessionEntry::Compaction {
            id,
            parent_id,
            summary: entry
                .get("summary")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            first_kept_entry_id: entry
                .get("firstKeptEntryId")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            tokens_before: entry
                .get("tokensBefore")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
            details: entry.get("details").cloned(),
            from_hook: entry.get("fromHook").and_then(Value::as_bool),
            custom_instructions: entry
                .get("customInstructions")
                .and_then(Value::as_str)
                .map(str::to_string),
            harness_digest: entry
                .get("harnessDigest")
                .and_then(Value::as_str)
                .map(str::to_string),
            timestamp: entry
                .get("timestamp")
                .map(|value| value.to_string())
                .unwrap_or_default(),
        }),
        _ => Some(CompactionSessionEntry::Other {
            id,
            parent_id,
            entry_type: entry_type.to_string(),
        }),
    }
}

fn path_entries(branch: &[SessionEntry]) -> Vec<CompactionSessionEntry> {
    branch.iter().filter_map(compaction_entry).collect()
}

/// Entry id -> compactbench turn id. The adapter appends turns in order and
/// records the id map in the session file's sidecar; simpler and robust:
/// turn ids are stamped into the message text is NOT acceptable (it would
/// corrupt the transcript), so the adapter passes the id map through the
/// request and the harness echoes kept/summarized ranges by entry order.
/// To keep the mapping exact without sidecars, the harness writes a
/// `.rot2-turn-map.json` next to the session file.
fn turn_map_path(session_dir: &str) -> std::path::PathBuf {
    std::path::Path::new(session_dir).join(".rot2-turn-map.json")
}

fn load_turn_map(session_dir: &str) -> Vec<i64> {
    let path = turn_map_path(session_dir);
    std::fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn store_turn_map(session_dir: &str, map: &[i64]) {
    let path = turn_map_path(session_dir);
    let _ = std::fs::write(path, serde_json::to_string(map).unwrap_or_default());
}

// ---------------------------------------------------------------------------
// Deterministic lossy summarizer (shared policy with rot2_multicycle.rs)
// ---------------------------------------------------------------------------

const LOSSY_HEAD_LINES: usize = 2;
const LOSSY_TAIL_LINES: usize = 2;
const LOSSY_LINE_CLIP: usize = 160;
const PREVIOUS_SUMMARY_KEEP_CHARS: usize = 400;

fn clip_line(line: &str, limit: usize) -> String {
    if line.chars().count() <= limit {
        line.to_string()
    } else {
        let clipped: String = line.chars().take(limit).collect();
        format!("{clipped}...")
    }
}

fn conversation_chunks(request: &str) -> Vec<String> {
    let mut chunks = Vec::new();
    let mut rest = request;
    while let Some(start) = rest.find("<conversation>") {
        let after = &rest[start + "<conversation>".len()..];
        if let Some(end_rel) = after.find("</conversation>") {
            chunks.push(after[..end_rel].to_string());
            rest = &after[end_rel + "</conversation>".len()..];
        } else {
            break;
        }
    }
    chunks
}

pub fn lossy_summary(request: &str) -> String {
    let mut constraints = Vec::new();
    let mut decisions = Vec::new();
    let mut evidence = Vec::new();
    let mut section = String::new();
    let mut in_block = false;
    for line in request.lines() {
        if line.starts_with("## Protected Task State") {
            in_block = true;
            continue;
        }
        if in_block {
            if line.starts_with("## ") || line.starts_with("Copy every ACTIVE CONSTRAINT") {
                if !line.starts_with("## Protected") {
                    in_block = false;
                    continue;
                }
            }
            if line.starts_with("- ") {
                match section.as_str() {
                    "constraints" => constraints.push(line.to_string()),
                    "decisions" => decisions.push(line.to_string()),
                    "evidence" => evidence.push(line.to_string()),
                    _ => {}
                }
            } else if line.contains("ACTIVE CONSTRAINTS") {
                section = "constraints".into();
            } else if line.contains("DECISIONS (current)") {
                section = "decisions".into();
            } else if line.contains("VERIFIED EVIDENCE") {
                section = "evidence".into();
            }
        }
    }

    let mut excerpts = Vec::new();
    for chunk in conversation_chunks(request) {
        let lines: Vec<&str> = chunk.lines().filter(|line| !line.trim().is_empty()).collect();
        if lines.is_empty() {
            continue;
        }
        let head = lines.len().min(LOSSY_HEAD_LINES);
        let tail = lines.len().saturating_sub(LOSSY_TAIL_LINES).max(head);
        let mut kept: Vec<String> = lines[..head]
            .iter()
            .map(|line| clip_line(line, LOSSY_LINE_CLIP))
            .collect();
        if tail > head {
            kept.extend(
                lines[tail..]
                    .iter()
                    .map(|line| clip_line(line, LOSSY_LINE_CLIP)),
            );
        }
        excerpts.push(format!("(lossy excerpt) {}", kept.join(" / ")));
    }

    let mut previous_kept = String::new();
    if let Some(start) = request.find("<previous-summary>") {
        if let Some(end_rel) = request[start..].find("</previous-summary>") {
            let previous = &request[start + "<previous-summary>".len()..start + end_rel];
            let previous = previous.trim();
            if previous.chars().count() > PREVIOUS_SUMMARY_KEEP_CHARS {
                previous_kept = format!(
                    "(decayed prior summary) {}...",
                    previous
                        .chars()
                        .take(PREVIOUS_SUMMARY_KEEP_CHARS)
                        .collect::<String>()
                );
            } else {
                previous_kept = previous.to_string();
            }
        }
    }

    let mut summary = String::new();
    summary.push_str("## Goal\nContinue the user's task per the retained state.\n");
    summary.push_str("## Constraints & Preferences\n");
    if constraints.is_empty() {
        summary.push_str("- None retained by the protected block.\n");
    } else {
        for line in &constraints {
            summary.push_str(line);
            summary.push('\n');
        }
    }
    summary.push_str("## Progress\n### Done\n- [x] Prior turns summarized (lossy).\n\n### In Progress\n- [ ] Continuing the task.\n\n### Blocked\n- None.\n");
    summary.push_str("## Key Decisions\n");
    if decisions.is_empty() {
        summary.push_str("- None recorded by the protected block.\n");
    } else {
        for line in &decisions {
            summary.push_str(line);
            summary.push('\n');
        }
    }
    summary.push_str("## Next Steps\n1. Continue the task from the retained state.\n");
    summary.push_str("## Critical Context\n");
    if !evidence.is_empty() {
        for line in &evidence {
            summary.push_str(line);
            summary.push('\n');
        }
    }
    if !previous_kept.is_empty() {
        summary.push_str(&previous_kept);
        summary.push('\n');
    }
    if !excerpts.is_empty() {
        summary.push_str(&excerpts.join("\n"));
        summary.push('\n');
    }
    if summary.lines().count() < 8 {
        summary.push_str("(no further context retained)\n");
    }
    summary
}

fn lossy_summarizer_fixture(api: &str) -> Model {
    register_api_provider_simple(
        ApiProviderSimple {
            api: api.into(),
            stream: Arc::new(|_, _, _| panic!("unexpected base stream")),
            stream_simple: Arc::new(|_, context, _| {
                let text = context
                    .messages
                    .iter()
                    .map(|message| match message {
                        Message::User(user) => user.content.text(),
                        _ => String::new(),
                    })
                    .collect::<Vec<_>>()
                    .join("\n");
                let summary = lossy_summary(&text);
                let stream = AssistantMessageEventStream::new();
                stream.push(AssistantMessageEvent::Done {
                    reason: "stop".into(),
                    message: AssistantMessage {
                        content: vec![ContentBlock::Text(TextContent::new(summary))],
                        stop_reason: "stop".into(),
                        ..Default::default()
                    },
                });
                stream
            }),
            compact: None,
            supports_compaction: None,
        },
        None,
    );
    let mut model = Model::new(api, api, api, "fixture", "https://fixture.invalid");
    model.context_window = 1_000_000.0;
    model.max_tokens = 64_000.0;
    model
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

fn fail(error: String) -> ! {
    let response = HarnessResponse {
        status: "error".into(),
        summary: String::new(),
        first_kept_entry_id: String::new(),
        tokens_before: 0.0,
        compacted_context_tokens: 0.0,
        ledger: Value::Null,
        summarized_turn_ids: Vec::new(),
        kept_turn_ids: Vec::new(),
        warnings: Vec::new(),
        error: Some(error),
    };
    let json = serde_json::to_string(&response).unwrap();
    let mut stdout = std::io::stdout();
    let _ = writeln!(stdout, "{json}");
    let _ = stdout.flush();
    std::process::exit(1);
}

fn main() {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        fail("could not read request from stdin".into());
    }
    let request: HarnessRequest = match serde_json::from_str(&raw) {
        Ok(request) => request,
        Err(error) => fail(format!("invalid request json: {error}")),
    };
    match request.summarizer.as_deref() {
        None | Some("fixture") => {}
        Some(other) => fail(format!("unsupported summarizer mode: {other}")),
    }

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(1)
        .enable_all()
        .build()
        .expect("tokio runtime");
    let response = runtime.block_on(run(request));
    let status = response.status.clone();
    let json = serde_json::to_string(&response).expect("serialize response");
    let mut stdout = std::io::stdout();
    let _ = writeln!(stdout, "{json}");
    let _ = stdout.flush();
    if status == "error" {
        std::process::exit(1);
    }
}

async fn run(request: HarnessRequest) -> HarnessResponse {
    std::fs::create_dir_all(&request.session_dir).expect("session dir");
    let dir_string = request.session_dir.clone();
    // F1: `create` always starts a NEW session file, so a per-case dir
    // accumulated across invocations would silently fork a fresh session
    // every call. `continue_recent` reopens the most recent session in the
    // dir (falling back to a new one when the dir is empty), which is the
    // design-doc 2.3 delta-append mode: the previous compaction entry and its
    // taskState ledger carry forward across compact() calls.
    let mut manager = match SessionManager::continue_recent(&dir_string, Some(&dir_string)) {
        Ok(manager) => manager,
        Err(error) => fail(format!("session manager: {error}")),
    };

    let mut turn_map = load_turn_map(&request.session_dir);
    for turn in &request.new_turns {
        match turn.role.as_str() {
            "user" => {
                manager.append_message(user_message(&turn.content)).unwrap();
            }
            "assistant" => {
                // F4: a zero usage anchor makes the usage-anchored estimator
                // read 0 tokens. Give each assistant turn a conservative
                // self-estimate (its own content at chars/4) so the anchor is
                // non-zero and monotone with transcript growth.
                let usage_input = (turn.content.chars().count() as f64 / 4.0).ceil().max(1.0);
                manager
                    .append_message(assistant_message(&turn.content, usage_input))
                    .unwrap();
            }
            other => fail(format!("unsupported turn role: {other}")),
        }
        turn_map.push(turn.turn_id);
    }
    // F5: flush appended turns immediately so an error-path exit cannot lose
    // them (the sidecar would otherwise outlive a partial session file).
    if let Err(error) = manager.flush_now() {
        fail(format!("session flush after append: {error}"));
    }
    store_turn_map(&request.session_dir, &turn_map);

    let branch = manager.get_branch(None);
    let entries = path_entries(&branch);
    let live: Vec<AgentMessage> = entries
        .iter()
        .filter_map(|entry| match entry {
            CompactionSessionEntry::Compaction { .. } => None,
            CompactionSessionEntry::Message { message, .. } => Some(message.clone()),
            CompactionSessionEntry::CustomMessage {
                custom_type,
                content,
                details,
                display,
                ..
            } => Some(AgentMessage::Custom(
                pi_agent_core::types::CustomAgentMessage::Custom {
                    custom_type: custom_type.clone(),
                    content: content.clone(),
                    display: *display,
                    details: details.clone(),
                    timestamp: 0,
                },
            )),
            _ => None,
        })
        .collect();
    let tokens = estimate_context_tokens(&live).tokens;

    let mut settings = default_compaction_settings();
    settings.reserve_tokens = request.settings.reserve_tokens;
    settings.keep_recent_tokens = request.settings.keep_recent_tokens;
    settings.task_state = request.settings.task_state.clone();

    let api = format!("rot2-harness-lossy-{}", request.session_dir.len());
    let model = lossy_summarizer_fixture(&api);

    let Some(preparation) = prepare_compaction(&entries, &settings, &|_| tokens) else {
        return HarnessResponse {
            status: "ok".into(),
            summary: String::new(),
            first_kept_entry_id: String::new(),
            tokens_before: tokens,
            compacted_context_tokens: tokens,
            ledger: Value::Null,
            summarized_turn_ids: Vec::new(),
            kept_turn_ids: turn_map.clone(),
            warnings: vec!["no compaction prepared (nothing new to summarize)".into()],
            error: None,
        };
    };

    let first_kept_entry_id = preparation.first_kept_entry_id.clone();
    let summarized_entries = preparation.messages_to_summarize.len();
    let result = match compact(
        &preparation,
        &model,
        "unused",
        None,
        None,
        None,
        default_summary_call_runner(None),
        None,
        None,
    )
    .await
    {
        Ok(result) => result,
        Err(error) => fail(format!("compaction failed: {error}")),
    };
    let details = result
        .details
        .as_ref()
        .and_then(|details| serde_json::to_value(details).ok());
    let ledger = details
        .as_ref()
        .and_then(|details| details.get("taskState").cloned())
        .unwrap_or(Value::Null);
    manager
        .append_compaction(
            &result.summary,
            &result.first_kept_entry_id,
            result.tokens_before,
            details,
            None,
            None,
            None,
            None,
        )
        .unwrap();
    manager.flush_now().unwrap();

    let context = manager.build_session_context_with_entry_ids(None);
    let compacted_tokens = estimate_context_tokens(&context.messages).tokens;

    // Turn-id bookkeeping: entry position of the first kept entry maps to the
    // turn map; entries before it were summarized.
    let branch = manager.get_branch(None);
    let kept_index = branch
        .iter()
        .position(|entry| entry.get("id").and_then(Value::as_str) == Some(&first_kept_entry_id))
        .unwrap_or(branch.len());
    // The turn map indexes non-compaction appends only; walk the branch and
    // count non-compaction entries up to the boundary.
    let mut non_compaction = 0usize;
    let mut summarized_count = 0usize;
    for entry in &branch {
        if entry.get("type").and_then(Value::as_str) == Some("compaction") {
            continue;
        }
        if non_compaction < kept_index.saturating_sub(
            branch
                .iter()
                .take(kept_index)
                .filter(|entry| entry.get("type").and_then(Value::as_str) == Some("compaction"))
                .count(),
        ) {
            summarized_count += 1;
        }
        non_compaction += 1;
    }
    let summarized_turn_ids = turn_map[..summarized_count.min(turn_map.len())].to_vec();
    let kept_turn_ids = turn_map[summarized_count.min(turn_map.len())..].to_vec();

    let mut warnings = Vec::new();
    if summarized_entries == 0 {
        warnings.push("compaction ran with an empty summarize window".into());
    }

    HarnessResponse {
        status: "ok".into(),
        summary: result.summary,
        first_kept_entry_id,
        tokens_before: result.tokens_before,
        compacted_context_tokens: compacted_tokens,
        ledger,
        summarized_turn_ids,
        kept_turn_ids,
        warnings,
        error: None,
    }
}

#[allow(dead_code)]
fn unused_json() -> Value {
    json!({})
}
