//! ROT2 production-path multicycle benchmark adapter (context-rot iteration 2).
//!
//! Drives the REAL compaction pipeline over the REAL `SessionManager` JSONL
//! persistence for N repeated cycles, per arm, and records what survives:
//! the compacted context, the persisted ledger, the summarizer request
//! budget, and per-probe deterministic information survival over the
//! restored provider context.
//!
//! The adapter is environment-driven so the Python research harness
//! (`research/context_rot_r2/`) can orchestrate scenario matrices without
//! recompiling:
//!
//! * `ROT2_SCENARIO` - path to the scenario JSON (schema below).
//! * `ROT2_RESULT`   - path where the result JSON is written.
//!
//! Scenario JSON schema (see `research/context_rot_r2/scenario.py` for the
//! generator; the smoke test below carries an inline example):
//!
//! ```json
//! {
//!   "name": "scenario-id",
//!   "arms": ["default", "taskState"],
//!   "cycles": 5,
//!   "contextWindow": 20000.0,
//!   "reserveTokens": 4000.0,
//!   "keepRecentTokens": 20.0,
//!   "seedEntries": [{"kind": "user", "text": "..."}],
//!   "cycleEntries": [[{"kind": "user", "text": "..."}]],
//!   "probes": [{"id": "q1", "anchor": "ARCH-B", "gold": "B", "kind": "code"}]
//! }
//! ```
//!
//! Entry kinds: `user`, `assistant` (fields: `text`, `usageInput`),
//! `toolResult` (fields: `text`, `toolCallId`, `toolName`),
//! `agentMessage` (fields: `text`, `fromName`).
//!
//! The summarizer is the deterministic lossy fixture registered through
//! `register_api_provider_simple`: it obeys protected blocks verbatim and
//! applies a fixed head/tail loss to everything else, so retention
//! differences between arms measure what the PIPELINE protects, not model
//! skill. Live (real-model) summarization is out of scope for this adapter
//! and is layered by the research harness on the same restored contexts.
//!
//! No network. All state under a fresh temp dir per arm.

use std::sync::{Arc, Mutex};

use pi_agent_core::types::{AgentMessage, CustomMessageContent};
use pi_ai::api_registry::{register_api_provider_simple, ApiProviderSimple};
use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, ContentBlock, Message, Model, TextContent,
    ToolResultMessage, Usage, UserContent, UserMessage,
};
use pi_ai::utils::event_stream::AssistantMessageEventStream;
use pi_coding_agent::core::compaction::compaction::{
    compact, default_compaction_settings, default_summary_call_runner, estimate_context_tokens,
    prepare_compaction, CompactionSessionEntry, CompactionSettings,
};
use pi_coding_agent::core::session_manager::{
    CustomMessageEntryContent, SessionManager, SessionEntry,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Scenario model
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScenarioEntry {
    kind: String,
    #[serde(default)]
    text: String,
    #[serde(default)]
    usage_input: f64,
    #[serde(default)]
    tool_call_id: String,
    #[serde(default)]
    tool_name: String,
    #[serde(default)]
    from_name: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScenarioProbe {
    id: String,
    /// Unique token planted in the source entry; survival means the restored
    /// provider context still carries the information the probe needs.
    anchor: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Scenario {
    name: String,
    #[serde(default)]
    arms: Vec<String>,
    cycles: usize,
    #[serde(default = "default_reserve")]
    reserve_tokens: f64,
    #[serde(default = "default_keep_recent")]
    keep_recent_tokens: f64,
    #[serde(default)]
    seed_entries: Vec<ScenarioEntry>,
    #[serde(default)]
    cycle_entries: Vec<Vec<ScenarioEntry>>,
    #[serde(default)]
    probes: Vec<ScenarioProbe>,
}

fn default_reserve() -> f64 {
    4_000.0
}
fn default_keep_recent() -> f64 {
    20.0
}

// ---------------------------------------------------------------------------
// Result model
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CycleRecord {
    cycle: usize,
    ran: bool,
    skip_reason: Option<String>,
    first_kept_entry_id: Option<String>,
    summarized_entries: usize,
    retained_entries: usize,
    summary_chars: usize,
    summarizer_request_chars: usize,
    compacted_context_tokens: f64,
    ledger_present: bool,
    ledger_items: Value,
    anchors_present: Vec<String>,
    /// W15: the typed (v2) protected-state verification outcome persisted
    /// on the compaction details (`typedVerification`); `None` when the arm
    /// is not `typed`.
    #[serde(skip_serializing_if = "Option::is_none")]
    typed_verification: Option<Value>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ProbeRecord {
    id: String,
    anchor: String,
    retained: bool,
    found_in: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ArmResult {
    arm: String,
    cycles: Vec<CycleRecord>,
    probes: Vec<ProbeRecord>,
    final_context_messages: Value,
    final_summary: String,
}

// ---------------------------------------------------------------------------
// Session seeding helpers (mirror compaction_recovery.rs)
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

fn tool_result_message(id: &str, name: &str, text: &str) -> AgentMessage {
    AgentMessage::Message(Message::ToolResult(ToolResultMessage::new(
        id,
        name,
        vec![pi_ai::types::ImageOrTextContent::Text(TextContent::new(text))],
        false,
        0,
    )))
}

fn seed_entry(manager: &mut SessionManager, entry: &ScenarioEntry) {
    match entry.kind.as_str() {
        "user" => {
            manager.append_message(user_message(&entry.text)).unwrap();
        }
        "assistant" => {
            manager
                .append_message(assistant_message(&entry.text, entry.usage_input))
                .unwrap();
        }
        "toolResult" => {
            manager
                .append_message(tool_result_message(
                    &entry.tool_call_id,
                    &entry.tool_name,
                    &entry.text,
                ))
                .unwrap();
        }
        "agentMessage" => {
            manager
                .append_custom_message_entry(
                    "agent_message",
                    &CustomMessageEntryContent::Text(entry.text.clone()),
                    true,
                    Some(json!({
                        "id": format!("agentmsg-{}", entry.from_name),
                        "message": entry.text,
                        "fromRelationship": "child",
                        "fromName": entry.from_name,
                    })),
                )
                .unwrap();
        }
        other => panic!("unknown scenario entry kind: {other}"),
    }
}

/// Mirrors the production converter (`compaction_session_entry_from`,
/// core/agent_session.rs), same as the compaction_recovery suite: a session
/// file entry becomes the `CompactionSessionEntry` the kernel consumes.
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
            display: entry
                .get("display")
                .and_then(Value::as_bool)
                .unwrap_or(false),
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

/// The restored provider context as plain text per message, for anchor
/// survival checks. Mirrors how the live context renders: the compaction
/// summary first, then the retained entries.
fn context_text(messages: &[AgentMessage]) -> Vec<String> {
    messages
        .iter()
        .map(|message| match message {
            AgentMessage::Message(Message::User(user)) => user.content.text(),
            AgentMessage::Message(Message::Assistant(assistant)) => assistant
                .content
                .iter()
                .filter_map(|block| match block {
                    ContentBlock::Text(text) => Some(text.text.clone()),
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join(""),
            AgentMessage::Message(Message::ToolResult(result)) => result
                .content
                .iter()
                .map(|block| match block {
                    pi_ai::types::ImageOrTextContent::Text(text) => text.text.clone(),
                    _ => String::new(),
                })
                .collect::<Vec<_>>()
                .join(""),
            AgentMessage::Custom(custom) => match custom {
                pi_agent_core::types::CustomAgentMessage::CompactionSummary { summary, .. } => {
                    summary.clone()
                }
                pi_agent_core::types::CustomAgentMessage::Custom { content, .. } => match content {
                    CustomMessageContent::Text(text) => text.clone(),
                    CustomMessageContent::Blocks(blocks) => blocks
                        .iter()
                        .map(|block| match block {
                            pi_agent_core::types::ContentBlock::Text(text) => text.text.clone(),
                            _ => String::new(),
                        })
                        .collect::<Vec<_>>()
                        .join(""),
                },
                _ => String::new(),
            },
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Deterministic lossy summarizer fixture
// ---------------------------------------------------------------------------

/// Fixed loss applied to unprotected conversation material per chunk.
const LOSSY_HEAD_LINES: usize = 2;
const LOSSY_TAIL_LINES: usize = 2;
const LOSSY_LINE_CLIP: usize = 160;
/// Fixed loss applied to the carried previous summary (models
/// summary-of-summary drift for material the pipeline does not protect).
const PREVIOUS_SUMMARY_KEEP_CHARS: usize = 400;

fn clip_line(line: &str, limit: usize) -> String {
    if line.chars().count() <= limit {
        line.to_string()
    } else {
        let clipped: String = line.chars().take(limit).collect();
        format!("{clipped}...")
    }
}

/// Extract `<conversation>...</conversation>` chunks from the request text.
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

/// The deterministic lossy summarizer: an obedient model that copies
/// protected blocks verbatim and applies a fixed head/tail loss to
/// everything else. Arms differ in what the pipeline protects, so retention
/// differences measure pipeline behavior, not model skill.
pub fn lossy_summary(request: &str) -> String {
    let protected = request
        .lines()
        .any(|line| line.starts_with("## Protected Task State"))
        || request
            .lines()
            .any(|line| line.starts_with("## Typed Protected State"));
    let mut constraints = Vec::new();
    let mut decisions = Vec::new();
    let mut evidence = Vec::new();
    if protected {
        // Copy the ledger block verbatim, routed by its own section markers.
        // The typed block's intro orders the model to "Copy every item
        // below into the matching summary section", so every typed section
        // routes to its summary bucket (see `typed_section_bucket`); the v1
        // titles keep their original contains() routing, so the v1 arm is
        // byte-identical to the committed E1 run.
        let mut section = String::new();
        let mut in_block = false;
        for line in request.lines() {
            if line.starts_with("## Protected Task State")
                || line.starts_with("## Typed Protected State")
            {
                in_block = true;
                continue;
            }
            if in_block {
                if line.starts_with("## ") || line.starts_with("Copy every ACTIVE CONSTRAINT") {
                    // End of the protected block (instruction line follows).
                    if !line.starts_with("## Protected") && !line.starts_with("## Typed") {
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
                } else if let Some(bucket) = typed_section_bucket(line) {
                    section = bucket.into();
                }
            }
        }
    }

    // Unprotected loss: head/tail of each conversation chunk, clipped.
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

    // Previous summary decay: only its head survives, unprotected.
    let mut previous_kept = String::new();
    if let Some(start) = request.find("<previous-summary>") {
        if let Some(end_rel) = request[start..].find("</previous-summary>") {
            let previous = &request[start + "<previous-summary>".len()..start + end_rel];
            let previous = previous.trim();
            if previous.chars().count() > PREVIOUS_SUMMARY_KEEP_CHARS {
                previous_kept = format!(
                    "(decayed prior summary) {}...",
                    previous.chars().take(PREVIOUS_SUMMARY_KEEP_CHARS).collect::<String>()
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
        // Keep the section scaffold non-degenerate.
        summary.push_str("(no further context retained)\n");
    }
    summary
}

/// Typed protected-block section routing for the lossy fixture: the
/// summary bucket a typed section title maps to. The exact uppercase
/// titles are the typed render's own (`typed_state.rs section_title`); the
/// v1 titles (wrapped in `**` by `render_ledger`) fall through to their
/// historical contains() routing so v1-arm behavior is unchanged.
fn typed_section_bucket(line: &str) -> Option<&'static str> {
    match line {
        "ACTIVE CONSTRAINTS"
        | "SAFETY BOUNDARIES"
        | "ACCEPTANCE CRITERIA"
        | "USER CORRECTIONS"
        | "CURRENT TASK"
        | "PENDING ACTIONS" => Some("constraints"),
        "DECISIONS (CURRENT)" | "DECISION RATIONALE" => Some("decisions"),
        "VERIFIED EVIDENCE"
        | "SOURCE EVIDENCE"
        | "REPOSITORY STATE"
        | "SUBAGENT STATE"
        | "HISTORICAL FACTS"
        | "LOW-PRIORITY EPISODES"
        | "SUPERSEDED (HISTORY, NOT CURRENT)" => Some("evidence"),
        _ => {
            // v1 ledger titles (task_state.rs render_ledger): unchanged.
            if line.contains("ACTIVE CONSTRAINTS") {
                Some("constraints")
            } else if line.contains("DECISIONS (current)") {
                Some("decisions")
            } else if line.contains("VERIFIED EVIDENCE") {
                Some("evidence")
            } else {
                None
            }
        }
    }
}

/// Registers the lossy summarizer fixture; returns the model plus captured
/// request texts (for budget accounting).
fn lossy_summarizer_fixture(api: &str) -> (Model, Arc<Mutex<Vec<String>>>) {
    let captured: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let seen = Arc::clone(&captured);
    register_api_provider_simple(
        ApiProviderSimple {
            api: api.into(),
            stream: Arc::new(|_, _, _| panic!("unexpected base stream")),
            stream_simple: Arc::new(move |_, context, _| {
                let text = context
                    .messages
                    .iter()
                    .map(|message| match message {
                        Message::User(user) => user.content.text(),
                        _ => String::new(),
                    })
                    .collect::<Vec<_>>()
                    .join("\n");
                seen.lock().unwrap().push(text.clone());
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
    (model, captured)
}

// ---------------------------------------------------------------------------
// Arm runner
// ---------------------------------------------------------------------------

fn arm_settings(scenario: &Scenario, arm: &str) -> CompactionSettings {
    let mut settings = default_compaction_settings();
    settings.reserve_tokens = scenario.reserve_tokens;
    settings.keep_recent_tokens = scenario.keep_recent_tokens;
    // resolve_task_state_mode (compaction.rs): "prompt" keeps the v1
    // keyword ledger, "typed" (W15) selects the v2 typed protected-state
    // pipeline.
    settings.task_state = match arm {
        "taskState" => Some("prompt".to_string()),
        "typed" => Some("typed".to_string()),
        _ => Some("off".to_string()),
    };
    settings
}

fn ledger_items_value(details: Option<&Value>) -> (bool, Value) {
    let Some(details) = details else {
        return (false, Value::Null);
    };
    match details.get("taskState") {
        Some(ledger) => (true, ledger.clone()),
        None => match details.get("typedState") {
            // W15: the typed (v2) ledger persists under its own field
            // (details.typedState, never the v1 payload).
            Some(ledger) => (true, ledger.clone()),
            None => (false, Value::Null),
        },
    }
}

fn count_ledger_items(ledger: &Value) -> usize {
    if let Some(records) = ledger.get("records").and_then(Value::as_array) {
        // Typed ledger: one entry per retained record.
        return records.len();
    }
    let mut total = 0;
    for key in ["constraints", "decisions", "superseded", "evidence", "anchors", "openWork", "retractions"] {
        if let Some(items) = ledger.get(key).and_then(Value::as_array) {
            total += items.len();
        }
    }
    total
}

async fn run_arm(scenario: &Scenario, arm: &str) -> ArmResult {
    let root = tempfile::Builder::new()
        .prefix(&format!("rot2-{}-{}-", scenario.name, arm))
        .tempdir()
        .unwrap();
    let dir_string = root.path().to_string_lossy().to_string();
    let mut manager = SessionManager::create(&dir_string, Some(&dir_string)).unwrap();

    for entry in &scenario.seed_entries {
        seed_entry(&mut manager, entry);
    }

    let settings = arm_settings(scenario, arm);
    let api = format!("rot2-lossy-{}-{}", scenario.name, arm);
    let (model, captured) = lossy_summarizer_fixture(&api);

    let mut cycles = Vec::new();
    for cycle in 0..scenario.cycles {
        if let Some(entries) = scenario.cycle_entries.get(cycle) {
            for entry in entries {
                seed_entry(&mut manager, entry);
            }
        }
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

        let request_count_before = captured.lock().unwrap().len();
        let preparation = prepare_compaction(&entries, &settings, &|_| tokens);
        let Some(preparation) = preparation else {
            cycles.push(CycleRecord {
                cycle,
                ran: false,
                skip_reason: Some("prepare_compaction returned None".to_string()),
                first_kept_entry_id: None,
                summarized_entries: 0,
                retained_entries: entries.len(),
                summary_chars: 0,
                summarizer_request_chars: 0,
                compacted_context_tokens: tokens,
                ledger_present: false,
                ledger_items: Value::Null,
                anchors_present: Vec::new(),
                typed_verification: None,
            });
            continue;
        };
        let summarized_entries = preparation.messages_to_summarize.len();
        let first_kept_entry_id = preparation.first_kept_entry_id.clone();
        let result = compact(
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
        .expect("lossy summarizer fixture succeeds");
        let details = result
            .details
            .as_ref()
            .and_then(|details| serde_json::to_value(details).ok());
        let (ledger_present, ledger_items) = ledger_items_value(details.as_ref());
        let typed_verification = details
            .as_ref()
            .and_then(|details| details.get("typedVerification").cloned());
        manager
            .append_compaction(
                &result.summary,
                &result.first_kept_entry_id,
                result.tokens_before,
                details.clone(),
                None,
                None,
                None,
                None,
            )
            .unwrap();
        manager.flush_now().unwrap();

        let context = manager.build_session_context_with_entry_ids(None);
        let context_tokens = estimate_context_tokens(&context.messages).tokens;
        let summarizer_request_chars: usize = captured.lock().unwrap()[request_count_before..]
            .iter()
            .map(|text| text.len())
            .sum();
        let texts = context_text(&context.messages);
        let joined = texts.join("\n");
        let anchors_present = scenario
            .probes
            .iter()
            .filter(|probe| joined.contains(&probe.anchor))
            .map(|probe| probe.id.clone())
            .collect();
        cycles.push(CycleRecord {
            cycle,
            ran: true,
            skip_reason: None,
            first_kept_entry_id: Some(first_kept_entry_id),
            summarized_entries,
            retained_entries: context.entry_ids.len().saturating_sub(1),
            summary_chars: result.summary.chars().count(),
            summarizer_request_chars,
            compacted_context_tokens: context_tokens,
            ledger_present,
            ledger_items: Value::String(format!("{}", count_ledger_items(&ledger_items))),
            anchors_present,
            typed_verification,
        });
    }

    // Final probe evaluation over the restored provider context.
    let context = manager.build_session_context_with_entry_ids(None);
    let texts = context_text(&context.messages);
    let probes = scenario
        .probes
        .iter()
        .map(|probe| {
            let mut found_in = Vec::new();
            for (index, text) in texts.iter().enumerate() {
                if text.contains(&probe.anchor) {
                    found_in.push(if index == 0 {
                        "summary".to_string()
                    } else {
                        format!("message[{index}]")
                    });
                }
            }
            ProbeRecord {
                id: probe.id.clone(),
                anchor: probe.anchor.clone(),
                retained: !found_in.is_empty(),
                found_in,
            }
        })
        .collect();
    let final_summary = texts.first().cloned().unwrap_or_default();
    ArmResult {
        arm: arm.to_string(),
        cycles,
        probes,
        final_context_messages: Value::Array(
            context
                .messages
                .iter()
                .map(|message| Value::String(match message {
                    AgentMessage::Message(Message::User(user)) => user.content.text(),
                    AgentMessage::Message(Message::Assistant(assistant)) => assistant
                        .content
                        .iter()
                        .filter_map(|block| match block {
                            ContentBlock::Text(text) => Some(text.text.clone()),
                            _ => None,
                        })
                        .collect::<Vec<_>>()
                        .join(""),
                    AgentMessage::Message(Message::ToolResult(result)) => result
                        .content
                        .iter()
                        .map(|block| match block {
                            pi_ai::types::ImageOrTextContent::Text(text) => text.text.clone(),
                            _ => String::new(),
                        })
                        .collect::<Vec<_>>()
                        .join(""),
                    AgentMessage::Custom(custom) => match custom {
                        pi_agent_core::types::CustomAgentMessage::CompactionSummary {
                            summary,
                            ..
                        } => summary.clone(),
                        pi_agent_core::types::CustomAgentMessage::Custom { content, .. } => {
                            match content {
                                CustomMessageContent::Text(text) => text.clone(),
                                CustomMessageContent::Blocks(blocks) => blocks
                                    .iter()
                                    .map(|block| match block {
                                        pi_agent_core::types::ContentBlock::Text(text) => {
                                            text.text.clone()
                                        }
                                        _ => String::new(),
                                    })
                                    .collect::<Vec<_>>()
                                    .join("")
                            }
                        }
                        _ => String::new(),
                    },
                }))
                .collect(),
        ),
        final_summary,
    }
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RunResult {
    scenario: String,
    arms: Vec<ArmResult>,
}

async fn execute_scenario(scenario: &Scenario) -> RunResult {
    let mut arms = Vec::new();
    for arm in &scenario.arms {
        arms.push(run_arm(scenario, arm).await);
    }
    RunResult {
        scenario: scenario.name.clone(),
        arms,
    }
}

/// Environment-driven research entry point. Ignored by default so normal
/// `cargo test` runs are unaffected; the research harness invokes it with
/// `cargo test --test rot2_multicycle rot2_production_multicycle -- --ignored`
/// plus `ROT2_SCENARIO` / `ROT2_RESULT`.
#[tokio::test]
#[ignore = "research adapter: requires ROT2_SCENARIO/ROT2_RESULT env"]
async fn rot2_production_multicycle() {
    let scenario_path = std::env::var("ROT2_SCENARIO").expect("ROT2_SCENARIO");
    let result_path = std::env::var("ROT2_RESULT").expect("ROT2_RESULT");
    let raw = std::fs::read_to_string(&scenario_path).expect("read scenario");
    let scenario: Scenario = serde_json::from_str(&raw).expect("parse scenario");
    let result = execute_scenario(&scenario).await;
    let json = serde_json::to_string_pretty(&result).expect("serialize result");
    std::fs::write(&result_path, json).expect("write result");
}

// ---------------------------------------------------------------------------
// Always-on smoke tests: the adapter itself is regression-tested offline.
// ---------------------------------------------------------------------------

fn smoke_scenario() -> Scenario {
    let filler = |index: usize| {
        ScenarioEntry {
            kind: "user".into(),
            text: format!(
                "status update {index}: integration notes {index}. {}",
                "unrelated historical discussion. ".repeat(60)
            ),
            usage_input: 0.0,
            tool_call_id: String::new(),
            tool_name: String::new(),
            from_name: String::new(),
        }
    };
    Scenario {
        name: "smoke".into(),
        arms: vec!["default".into(), "taskState".into()],
        cycles: 3,
        reserve_tokens: 4_000.0,
        keep_recent_tokens: 20.0,
        seed_entries: vec![
            ScenarioEntry {
                kind: "user".into(),
                text: "TASK GOAL: migrate the billing service. CONSTRAINT: never drop the payments ledger ARCH-KEEP-LEDGER.".into(),
                usage_input: 0.0,
                tool_call_id: String::new(),
                tool_name: String::new(),
                from_name: String::new(),
            },
            ScenarioEntry {
                kind: "assistant".into(),
                // D25/D30: a decision stated by the ASSISTANT classifies as
                // VerifiedDecision (Task scope) and survives the step-boundary
                // merge; the same sentence in user text currently falls through
                // to LowPriorityEpisode (Step scope) and is retired at the first
                // merge (filed as D30).
                text: "Started the migration plan. Decision: use architecture B ARCH-DECISION-B for the parser (corrected from architecture A).".into(),
                usage_input: 12.0,
                tool_call_id: String::new(),
                tool_name: String::new(),
                from_name: String::new(),
            },
            ScenarioEntry {
                kind: "toolResult".into(),
                text: "cargo test: 4785 passed; 0 failed. Verified evidence EV-4785.".into(),
                usage_input: 0.0,
                tool_call_id: "call-1".into(),
                tool_name: "bash".into(),
                from_name: String::new(),
            },
            ScenarioEntry {
                kind: "user".into(),
                text: "Continue the migration. Keep going with the plan.".into(),
                usage_input: 0.0,
                tool_call_id: String::new(),
                tool_name: String::new(),
                from_name: String::new(),
            },
            ScenarioEntry {
                kind: "assistant".into(),
                text: "Section 3 verified; publishing next.".into(),
                usage_input: 17_000.0,
                tool_call_id: String::new(),
                tool_name: String::new(),
                from_name: String::new(),
            },
        ],
        cycle_entries: vec![
            vec![filler(1), filler(2)],
            vec![filler(3), filler(4)],
            vec![filler(5), filler(6)],
        ],
        probes: vec![
            ScenarioProbe {
                id: "q1".into(),
                anchor: "ARCH-KEEP-LEDGER".into(),
            },
            ScenarioProbe {
                id: "q2".into(),
                anchor: "ARCH-DECISION-B".into(),
            },
            ScenarioProbe {
                id: "q3".into(),
                anchor: "EV-4785".into(),
            },
        ],
    }
}

/// W15: the typed arm runs the same smoke scenario through the real
/// compaction pipeline with the typed protected-state mode selected, so the
/// always-on suite regression-tests the typed wiring offline.
#[tokio::test]
async fn rot2_smoke_typed_arm_persists_typed_ledger_and_verifies() {
    let mut scenario = smoke_scenario();
    scenario.arms = vec!["typed".into()];
    let result = execute_scenario(&scenario).await;
    assert_eq!(result.arms.len(), 1);
    let typed_arm = &result.arms[0];
    assert_eq!(typed_arm.arm, "typed");
    for cycle in &typed_arm.cycles {
        assert!(cycle.ran, "typed arm cycle {} must run", cycle.cycle);
        assert!(
            cycle.ledger_present,
            "typed arm cycle {} must persist the typed ledger",
            cycle.cycle
        );
        let verification = cycle
            .typed_verification
            .as_ref()
            .unwrap_or_else(|| panic!("typed arm cycle {} must record the verification", cycle.cycle));
        assert_eq!(
            verification.get("passed").and_then(Value::as_bool),
            Some(true),
            "typed arm cycle {} must pass the typed verifier",
            cycle.cycle
        );
    }
    // The deterministic lossy fixture copies the typed protected block, so
    // every planted anchor survives the five cycles.
    for probe in &typed_arm.probes {
        assert!(
            probe.retained,
            "typed arm probe {} must be retained (found in {:?})",
            probe.id, probe.found_in
        );
    }
}

#[tokio::test]
async fn rot2_smoke_multicycle_runs_all_cycles_and_reports_probes() {
    let scenario = smoke_scenario();
    let result = execute_scenario(&scenario).await;
    assert_eq!(result.arms.len(), 2);
    for arm in &result.arms {
        assert_eq!(arm.cycles.len(), 3, "arm {} must run every cycle", arm.arm);
        for cycle in &arm.cycles {
            assert!(cycle.ran, "cycle {} on {} must run", cycle.cycle, arm.arm);
            assert!(
                cycle.summarized_entries > 0,
                "cycle {} on {} must summarize material",
                cycle.cycle,
                arm.arm
            );
            assert!(
                cycle.summary_chars > 0,
                "cycle {} on {} must produce a summary",
                cycle.cycle,
                arm.arm
            );
        }
        assert_eq!(arm.probes.len(), 3);
    }
    // The ledger arm must persist a ledger on every cycle.
    let task_state_arm = &result.arms[1];
    assert_eq!(task_state_arm.arm, "taskState");
    for cycle in &task_state_arm.cycles {
        assert!(
            cycle.ledger_present,
            "taskState arm cycle {} must persist the ledger",
            cycle.cycle
        );
    }
}

#[test]
fn rot2_user_image_blocks_cost_tokens_d16() {
    use pi_ai::types::{ImageOrTextContent, TextContent, UserContent};
    let text_only = AgentMessage::Message(Message::User(UserMessage::new(
        UserContent::Blocks(vec![ImageOrTextContent::Text(TextContent::new("hello world"))]),
        0,
    )));
    let with_image = AgentMessage::Message(Message::User(UserMessage::new(
        UserContent::Blocks(vec![
            ImageOrTextContent::Text(TextContent::new("hello world")),
            ImageOrTextContent::Text(TextContent::new(TextContent::new("").text.clone())),
            ImageOrTextContent::Image(pi_ai::types::ImageContent::default()),
        ]),
        0,
    )));
    let base = estimate_tokens_message(&text_only);
    let with_img = estimate_tokens_message(&with_image);
    assert_eq!(
        with_img - base,
        pi_coding_agent::core::compaction::request_tokens::IMAGE_BLOCK_TOKENS,
        "D16: a user image block must cost IMAGE_BLOCK_TOKENS, not zero"
    );
}

/// Helper reaching the private estimator through the public context estimate:
/// with no assistant usage, tokens = sum(estimate_tokens), so the delta between
/// two message lists isolates the per-message estimate.
fn estimate_tokens_message(message: &AgentMessage) -> f64 {
    estimate_context_tokens(std::slice::from_ref(message)).tokens
}

#[test]
fn rot2_request_level_estimate_adds_system_prompt_and_tools_d17() {
    use pi_coding_agent::core::compaction::compaction::estimate_context_tokens_with_request;
    let messages = vec![user_message_ref("a short user message")];
    let prompt = "You are a coding agent. Follow the user's constraints exactly.";
    let tools = [tool_definition_fixture("read_file"), tool_definition_fixture("bash")];

    let message_only = estimate_context_tokens(&messages);
    assert_eq!(message_only.system_prompt_tokens, 0.0);
    let request = estimate_context_tokens_with_request(&messages, prompt, &tools);
    assert!(
        request.system_prompt_tokens > 0.0,
        "system prompt must be counted on the request-level path"
    );
    assert!(request.tool_schema_tokens > 0.0);
    assert!(
        (request.tokens - (message_only.tokens + request.system_prompt_tokens + request.tool_schema_tokens)).abs() < 1e-9,
        "request tokens must be the sum of its parts on the no-usage path"
    );
}

fn user_message_ref(text: &str) -> AgentMessage {
    AgentMessage::Message(Message::User(UserMessage::new(
        UserContent::Text(text.into()),
        0,
    )))
}

fn tool_definition_fixture(name: &str) -> pi_ai::types::Tool {
    serde_json::from_value(serde_json::json!({
        "name": name,
        "description": format!("Fixture tool {name} for request-level token estimates"),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    }))
    .expect("tool fixture")
}

#[tokio::test]
async fn rot2_lossy_summarizer_keeps_protected_block_verbatim_and_drops_middle() {
    let request = "instructions\n\n## Protected Task State (deterministic, provenance-tagged)\nUse it.\n\n**ACTIVE CONSTRAINTS**\n- c1 [t1]: never drop the payments ledger ARCH-KEEP-LEDGER\n\n**DECISIONS (current)**\n- d1 [t2]: use architecture B ARCH-DECISION-B\n\n**VERIFIED EVIDENCE**\n- e1 [t3]: cargo test 4785 passed EV-4785\n\nCopy every ACTIVE CONSTRAINT item.\n\n<conversation>\nline one\nline two\nmiddle line that must be dropped by the lossy rule\nline four\nline five\n</conversation>\n\n<previous-summary>\n## Goal\nOld goal text that is long enough to be decayed by the previous-summary loss rule so that only its head survives this cycle and the rest is lost to drift modelling. PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD PAD 
</previous-summary>\n";
    let summary = lossy_summary(request);
    assert!(summary.contains("ARCH-KEEP-LEDGER"), "protected constraint survives");
    assert!(summary.contains("ARCH-DECISION-B"), "protected decision survives");
    assert!(summary.contains("EV-4785"), "protected evidence survives");
    assert!(summary.contains("line one"), "head of chunk survives");
    assert!(summary.contains("line five"), "tail of chunk survives");
    assert!(
        !summary.contains("middle line that must be dropped"),
        "middle of chunk is dropped by the lossy rule"
    );
    assert!(
        summary.contains("(decayed prior summary)"),
        "previous summary decays when not protected"
    );
    assert!(summary.contains("## Goal"), "six-section scaffold present");
    assert!(summary.contains("## Critical Context"));
}
