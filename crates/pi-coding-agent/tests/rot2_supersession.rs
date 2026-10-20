//! ROT2 W16 supersession semantics regressions (context-rot iteration 2).
//!
//! W7's live-model corpus (research/context_rot_r2/live/) proved that the v1
//! ledger inverts a user correction: the correction ("use architecture B
//! instead; the earlier A decision is superseded") was rendered under
//! SUPERSEDED (history) while the pre-correction decision ("use architecture
//! A") stayed current, and the live model followed the wrong instruction from
//! cycle 3 on. W9's incremental corpus (research/context_rot_r2/incremental/)
//! proved two more misfires of the same rule: corrections retiring each other
//! (mutual retirement, W9-F4) and ordinary constraint lines churning into
//! spurious supersession pairs (15 of 20 constraints, W9-F3).
//!
//! These tests reconstruct the exact corpus scenarios on the production
//! compaction path (real SessionManager persistence, real `compact` cycle
//! loop, lossy summarizer fixture) and pin the required semantics:
//!
//! 1. W7 base scenario: a correction is always current, and the decision it
//!    replaces is the history.
//! 2. W9-F4 late variant: a correction planted at cycle 5 still retires the
//!    old decision captured at cycle 1.
//! 3. W9-F3 hard variant: ordinary constraint lines never create supersession
//!    pairs (no churn).
//! 4. Applying the same window to `build_ledger` twice changes nothing.

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
    prepare_compaction, CompactionSessionEntry,
};
use pi_coding_agent::core::compaction::task_state::{build_ledger, render_ledger, TaskStateLedger};
use pi_coding_agent::core::session_manager::{
    CustomMessageEntryContent, SessionManager, SessionEntry,
};
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Scenario entries and session seeding (mirrors rot2_multicycle.rs)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
enum TestEntry {
    User(String),
    Assistant { text: String, usage: f64 },
    Tool { text: String, call_id: String },
    Agent { text: String, from: String },
}

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

fn seed_entry(manager: &mut SessionManager, entry: &TestEntry) {
    match entry {
        TestEntry::User(text) => {
            manager.append_message(user_message(text)).unwrap();
        }
        TestEntry::Assistant { text, usage } => {
            manager.append_message(assistant_message(text, *usage)).unwrap();
        }
        TestEntry::Tool { text, call_id } => {
            manager
                .append_message(tool_result_message(call_id, "bash", text))
                .unwrap();
        }
        TestEntry::Agent { text, from } => {
            manager
                .append_custom_message_entry(
                    "agent_message",
                    &CustomMessageEntryContent::Text(text.clone()),
                    true,
                    Some(json!({
                        "id": format!("agentmsg-{from}"),
                        "message": text,
                        "fromRelationship": "child",
                        "fromName": from,
                    })),
                )
                .unwrap();
        }
    }
}

/// Mirrors the production converter (`compaction_session_entry_from`), same as
/// the rot2_multicycle suite.
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

/// The restored provider context as one text blob (summary carrier first,
/// then the retained entries), for information-survival checks.
fn context_text(messages: &[AgentMessage]) -> String {
    let mut out = String::new();
    for message in messages {
        match message {
            AgentMessage::Message(Message::User(user)) => out.push_str(&user.content.text()),
            AgentMessage::Message(Message::Assistant(assistant)) => {
                for block in &assistant.content {
                    if let ContentBlock::Text(text) = block {
                        out.push_str(&text.text);
                    }
                }
            }
            AgentMessage::Message(Message::ToolResult(result)) => {
                for block in &result.content {
                    if let pi_ai::types::ImageOrTextContent::Text(text) = block {
                        out.push_str(&text.text);
                    }
                }
            }
            AgentMessage::Custom(custom) => match custom {
                pi_agent_core::types::CustomAgentMessage::CompactionSummary { summary, .. } => {
                    out.push_str(summary);
                }
                pi_agent_core::types::CustomAgentMessage::Custom { content, .. } => match content {
                    CustomMessageContent::Text(text) => out.push_str(text),
                    CustomMessageContent::Blocks(blocks) => {
                        for block in blocks {
                            if let pi_agent_core::types::ContentBlock::Text(text) = block {
                                out.push_str(&text.text);
                            }
                        }
                    }
                },
                _ => {}
            },
        }
        out.push('\n');
    }
    out
}

// ---------------------------------------------------------------------------
// Deterministic lossy summarizer fixture (same rule as rot2_multicycle.rs)
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

/// The deterministic lossy summarizer: copies the protected ledger block
/// verbatim (routed by its own section markers) and applies a fixed head/tail
/// loss to everything else.
pub fn lossy_summary(request: &str) -> String {
    let protected = request
        .lines()
        .any(|line| line.starts_with("## Protected Task State"));
    let mut constraints = Vec::new();
    let mut decisions = Vec::new();
    let mut evidence = Vec::new();
    if protected {
        let mut section = String::new();
        let mut in_block = false;
        for line in request.lines() {
            if line.starts_with("## Protected Task State") {
                in_block = true;
                continue;
            }
            if in_block {
                if (line.starts_with("## ") || line.starts_with("Copy every ACTIVE CONSTRAINT"))
                    && !line.starts_with("## Protected")
                {
                    in_block = false;
                    continue;
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
                } else if line.contains("SUPERSEDED") {
                    // The ledger instruction is explicit: superseded items are
                    // history and stay out of the current state, so the
                    // fixture must stop routing lines into the current
                    // sections at this boundary.
                    section = "superseded".into();
                } else if line.contains("RETRACTIONS") || line.contains("OPEN WORK") {
                    section = "other".into();
                }
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
        summary.push_str("(no further context retained)\n");
    }
    summary
}

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
// Task-state arm runner
// ---------------------------------------------------------------------------

struct CycleOutcome {
    cycle: usize,
    ledger: TaskStateLedger,
    rendered: String,
    request: String,
    context: String,
}

/// One rendered ledger section between `header` and the next section marker.
fn section<'a>(rendered: &'a str, header: &str) -> &'a str {
    let Some(start) = rendered.find(header) else {
        return "";
    };
    let rest = &rendered[start..];
    match rest.find("\n**") {
        Some(end) => &rest[..end],
        None => rest,
    }
}

async fn run_task_state_cycles(
    tag: &str,
    seeds: &[TestEntry],
    cycles: &[Vec<TestEntry>],
) -> Vec<CycleOutcome> {
    let root = tempfile::Builder::new()
        .prefix(&format!("rot2-w16-{tag}-"))
        .tempdir()
        .unwrap();
    let dir_string = root.path().to_string_lossy().to_string();
    let mut manager = SessionManager::create(&dir_string, Some(&dir_string)).unwrap();

    for entry in seeds {
        seed_entry(&mut manager, entry);
    }

    let mut settings = default_compaction_settings();
    settings.reserve_tokens = 4_000.0;
    settings.keep_recent_tokens = 20.0;
    settings.task_state = Some("prompt".to_string());

    let api = format!("rot2-w16-lossy-{tag}");
    let (model, captured) = lossy_summarizer_fixture(&api);

    let mut outcomes = Vec::new();
    for (cycle, block) in cycles.iter().enumerate() {
        for entry in block {
            seed_entry(&mut manager, entry);
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
        let preparation = preparation.unwrap_or_else(|| {
            panic!("cycle {cycle} ({tag}) must compact; tokens were {tokens}")
        });
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
            .and_then(|details| serde_json::to_value(details).ok())
            .unwrap_or_else(|| json!({}));
        let ledger: TaskStateLedger = details
            .get("taskState")
            .cloned()
            .map(|value| serde_json::from_value(value).expect("deserialize taskState ledger"))
            .unwrap_or_else(|| panic!("cycle {cycle} ({tag}) must persist the taskState ledger"));
        let rendered = render_ledger(&ledger);
        let request = captured.lock().unwrap()[request_count_before..]
            .iter()
            .rev()
            .find(|text| text.contains("## Protected Task State"))
            .cloned()
            .unwrap_or_default();
        manager
            .append_compaction(
                &result.summary,
                &result.first_kept_entry_id,
                result.tokens_before,
                Some(details.clone()),
                None,
                None,
                None,
                None,
            )
            .unwrap();
        manager.flush_now().unwrap();

        let context = context_text(&manager.build_session_context_with_entry_ids(None).messages);
        outcomes.push(CycleOutcome {
            cycle,
            ledger,
            rendered,
            request,
            context,
        });
    }
    outcomes
}

// ---------------------------------------------------------------------------
// Scenario fixtures (verbatim from the W7/W9 corpus scenarios)
// ---------------------------------------------------------------------------

const W7_CONSTRAINTS: &str = "TASK GOAL: migrate the billing service to API v2 before Friday.\n\
     CONSTRAINT: never drop the scheduler ledger R2-CONS-bc725595\n\
     CONSTRAINT: always run the payments suite before publishing R2-CONS-3516c936\n\
     CONSTRAINT: do not modify files under auth/ without review R2-CONS-698afcda\n\
     CONSTRAINT: keep the reconciler protocol version at v2 R2-CONS-5d76e931\n\
     CONSTRAINT: the billing migration must be reversible R2-CONS-068b308a\n\
     CONSTRAINT: no new dependencies in gateway without approval R2-CONS-0b032270\n\
     CONSTRAINT: preserve the ledger audit trail verbatim R2-CONS-76b74091\n\
     CONSTRAINT: the notifier service stays single-writer R2-CONS-b8ef61c5";

const CORRECTION_REPORTING: &str = "CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-9d2f87f5. The earlier A decision is superseded.";
const CORRECTION_VALIDATOR: &str = "CORRECTION: forget that; use architecture B for the validator module instead R2-CORR-72a6f7e4. The earlier A decision is superseded.";

fn filler(cycle: usize, index: usize, repetitions: usize) -> TestEntry {
    TestEntry::User(format!(
        "status update {cycle}.{index}: integration notes. {}",
        "unrelated historical discussion. ".repeat(repetitions)
    ))
}

fn noted(cycle: usize) -> TestEntry {
    TestEntry::Assistant {
        text: format!("Noted; continuing ({cycle})."),
        usage: 30.0,
    }
}

fn w7_base_seeds() -> Vec<TestEntry> {
    vec![
        TestEntry::User(W7_CONSTRAINTS.to_string()),
        TestEntry::Assistant {
            text: "Started the migration plan.".into(),
            usage: 12.0,
        },
        TestEntry::Assistant {
            text: "Decision: use architecture reconciler for the parser R2-DECI-b42dbfea".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: keep the billing client in-process R2-DECI-2c55b71d".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: reject the gateway rewrite; extend instead R2-DECI-e382f68e".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: the ledger fix lands behind a feature flag R2-DECI-42a5f117".into(),
            usage: 40.0,
        },
        TestEntry::Tool {
            text: "cargo test: 4000 passed; 0 failed. Verified evidence R2-EVID-232a0357".into(),
            call_id: "call-evidence-0".into(),
        },
        TestEntry::Tool {
            text: "build succeeded with 4137 warnings. Verified evidence R2-EVID-15a18aac".into(),
            call_id: "call-evidence-1".into(),
        },
        TestEntry::Tool {
            text: "audit report indexer: 4274 findings, all closed. Verified evidence R2-EVID-ae341cdc".into(),
            call_id: "call-evidence-2".into(),
        },
        TestEntry::Tool {
            text: "benchmark reporting: p95 4411ms. Verified evidence R2-EVID-1432e5de".into(),
            call_id: "call-evidence-3".into(),
        },
        TestEntry::Assistant {
            text: "Decision: use architecture A for the reporting module R2-SUPE-48258b00".into(),
            usage: 20.0,
        },
        TestEntry::User(CORRECTION_REPORTING.into()),
        TestEntry::Assistant {
            text: "Decision: use architecture A for the validator module R2-SUPE-1762c030".into(),
            usage: 20.0,
        },
        TestEntry::User(CORRECTION_VALIDATOR.into()),
        TestEntry::Agent {
            text: "Subagent reviewer-1 finished section 3; artifact evidence/reviewer-1.json".into(),
            from: "reviewer-1".into(),
        },
        TestEntry::User("Continue the migration.".into()),
        TestEntry::Assistant {
            text: "Section 3 verified; publishing next.".into(),
            usage: 17_000.0,
        },
    ]
}

fn filler_cycles(count: usize, repetitions: usize) -> Vec<Vec<TestEntry>> {
    (0..count)
        .map(|cycle| {
            vec![
                filler(cycle, 0, repetitions),
                noted(cycle),
                filler(cycle, 1, repetitions),
                noted(cycle),
                filler(cycle, 2, repetitions),
                noted(cycle),
            ]
        })
        .collect()
}

const HARD_CONSTRAINTS: &str = "TASK GOAL: migrate the billing service to API v2 before Friday.\n\
     CONSTRAINT: never drop the reporting ledger R2-CONS-c03c64d5\n\
     CONSTRAINT: always run the indexer suite before publishing R2-CONS-614389ba\n\
     CONSTRAINT: do not modify files under gateway/ without review R2-CONS-08df154e\n\
     CONSTRAINT: keep the archiver protocol version at v2 R2-CONS-b5e2af97\n\
     CONSTRAINT: the validator migration must be reversible R2-CONS-611c40b5\n\
     CONSTRAINT: no new dependencies in scheduler without approval R2-CONS-3e350f73\n\
     CONSTRAINT: preserve the billing audit trail verbatim R2-CONS-ff67f34f\n\
     CONSTRAINT: the auth service stays single-writer R2-CONS-6f107ef4\n\
     CONSTRAINT: never drop the ledger ledger R2-CONS-a2ddeedf\n\
     CONSTRAINT: always run the reconciler suite before publishing R2-CONS-6e0de741\n\
     CONSTRAINT: do not modify files under notifier/ without review R2-CONS-525ffed6\n\
     CONSTRAINT: keep the payments protocol version at v2 R2-CONS-b0719117\n\
     CONSTRAINT: the reporting migration must be reversible R2-CONS-63df81b1\n\
     CONSTRAINT: no new dependencies in indexer without approval R2-CONS-7175c414\n\
     CONSTRAINT: preserve the gateway audit trail verbatim R2-CONS-6b04a002\n\
     CONSTRAINT: the archiver service stays single-writer R2-CONS-7c670c54\n\
     CONSTRAINT: never drop the validator ledger R2-CONS-be0c3750\n\
     CONSTRAINT: always run the scheduler suite before publishing R2-CONS-df2c908e\n\
     CONSTRAINT: do not modify files under billing/ without review R2-CONS-d9f6fc43\n\
     CONSTRAINT: keep the auth protocol version at v2 R2-CONS-647a02ae";

/// The 20 ordinary constraint lines of the hard scenario.
const HARD_CONSTRAINT_ANCHORS: [&str; 20] = [
    "R2-CONS-c03c64d5",
    "R2-CONS-614389ba",
    "R2-CONS-08df154e",
    "R2-CONS-b5e2af97",
    "R2-CONS-611c40b5",
    "R2-CONS-3e350f73",
    "R2-CONS-ff67f34f",
    "R2-CONS-6f107ef4",
    "R2-CONS-a2ddeedf",
    "R2-CONS-6e0de741",
    "R2-CONS-525ffed6",
    "R2-CONS-b0719117",
    "R2-CONS-63df81b1",
    "R2-CONS-7175c414",
    "R2-CONS-6b04a002",
    "R2-CONS-7c670c54",
    "R2-CONS-be0c3750",
    "R2-CONS-df2c908e",
    "R2-CONS-d9f6fc43",
    "R2-CONS-647a02ae",
];

fn hard_seeds() -> Vec<TestEntry> {
    vec![
        TestEntry::User(HARD_CONSTRAINTS.to_string()),
        TestEntry::Assistant {
            text: "Started the migration plan.".into(),
            usage: 12.0,
        },
        TestEntry::Assistant {
            text: "Decision: use architecture archiver for the parser R2-DECI-e59e919f".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: keep the validator client in-process R2-DECI-255629a6".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: reject the scheduler rewrite; extend instead R2-DECI-c760d973".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: the billing fix lands behind a feature flag R2-DECI-b472e29c".into(),
            usage: 40.0,
        },
        TestEntry::Tool {
            text: "cargo test: 4000 passed; 0 failed. Verified evidence R2-EVID-5215a325".into(),
            call_id: "call-evidence-0".into(),
        },
        TestEntry::Tool {
            text: "build succeeded with 4137 warnings. Verified evidence R2-EVID-6808dfe9".into(),
            call_id: "call-evidence-1".into(),
        },
        TestEntry::Tool {
            text: "audit report ledger: 4274 findings, all closed. Verified evidence R2-EVID-02ed891d".into(),
            call_id: "call-evidence-2".into(),
        },
        TestEntry::Tool {
            text: "benchmark reconciler: p95 4411ms. Verified evidence R2-EVID-12f5e50c".into(),
            call_id: "call-evidence-3".into(),
        },
        TestEntry::Tool {
            text: "cargo test: 4548 passed; 0 failed. Verified evidence R2-EVID-3e20935e".into(),
            call_id: "call-evidence-4".into(),
        },
        TestEntry::Tool {
            text: "build succeeded with 4685 warnings. Verified evidence R2-EVID-51a9719a".into(),
            call_id: "call-evidence-5".into(),
        },
        TestEntry::Tool {
            text: "audit report reporting: 4822 findings, all closed. Verified evidence R2-EVID-697cfd46".into(),
            call_id: "call-evidence-6".into(),
        },
        TestEntry::Tool {
            text: "benchmark indexer: p95 4059ms. Verified evidence R2-EVID-f91a4152".into(),
            call_id: "call-evidence-7".into(),
        },
        TestEntry::Assistant {
            text: "Decision: use architecture A for the reconciler module R2-SUPE-754adbca".into(),
            usage: 20.0,
        },
        TestEntry::User(
            "CORRECTION: forget that; use architecture B for the reconciler module instead R2-CORR-c5538937. The earlier A decision is superseded.".into(),
        ),
        TestEntry::Assistant {
            text: "Decision: use architecture A for the notifier module R2-SUPE-7953f328".into(),
            usage: 20.0,
        },
        TestEntry::User(
            "CORRECTION: forget that; use architecture B for the notifier module instead R2-CORR-976a0af8. The earlier A decision is superseded.".into(),
        ),
        TestEntry::Assistant {
            text: "Decision: use architecture A for the payments module R2-SUPE-4cba4bf3".into(),
            usage: 20.0,
        },
        TestEntry::User(
            "CORRECTION: forget that; use architecture B for the payments module instead R2-CORR-db6b6d66. The earlier A decision is superseded.".into(),
        ),
        TestEntry::Assistant {
            text: "Decision: use architecture A for the reporting module R2-SUPE-0679234e".into(),
            usage: 20.0,
        },
        TestEntry::User(
            "CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-0b6d89d3. The earlier A decision is superseded.".into(),
        ),
        TestEntry::Agent {
            text: "Subagent reviewer-1 finished section 3; artifact evidence/reviewer-1.json".into(),
            from: "reviewer-1".into(),
        },
        TestEntry::User("Continue the migration.".into()),
        TestEntry::Assistant {
            text: "Section 3 verified; publishing next.".into(),
            usage: 17_000.0,
        },
    ]
}

/// Assertion family shared by the correction regressions: the correction is
/// the newest user statement (current), the decision it replaces is history.
fn assert_correction_wins(outcome: &CycleOutcome, corrections: &[(&str, &str)]) {
    let CycleOutcome {
        cycle,
        ledger,
        rendered,
        request,
        ..
    } = outcome;
    for (correction, superseded_decision) in corrections {
        assert!(
            ledger
                .constraints
                .iter()
                .any(|item| item.text.contains(correction)),
            "cycle {cycle}: the correction ({correction}) must stay an active constraint"
        );
        assert!(
            ledger
                .decisions
                .iter()
                .all(|item| !item.text.contains(superseded_decision)),
            "cycle {cycle}: the superseded decision ({superseded_decision}) must leave the current decisions: {:?}",
            ledger.decisions
        );
        assert!(
            ledger
                .superseded
                .iter()
                .any(|item| item.new_text.contains(correction)),
            "cycle {cycle}: the correction ({correction}) must retire the old decision"
        );
        let superseded_section = section(rendered, "**SUPERSEDED");
        assert!(
            !superseded_section.contains(correction),
            "cycle {cycle}: the correction ({correction}) must never render as superseded history: {superseded_section}"
        );
        assert!(
            superseded_section.contains(superseded_decision),
            "cycle {cycle}: the retired decision ({superseded_decision}) must render as superseded history: {superseded_section}"
        );
        let active = section(rendered, "**ACTIVE CONSTRAINTS");
        assert!(
            active.contains(correction),
            "cycle {cycle}: the correction ({correction}) must render as an active constraint"
        );
        assert!(
            request.contains("## Protected Task State"),
            "cycle {cycle}: the ledger must ride the summarizer request"
        );
    }
}

// ---------------------------------------------------------------------------
// Regressions
// ---------------------------------------------------------------------------

/// W7 finding 1: the base scenario's user corrections must stay current and
/// retire the architecture A decisions, at every compaction cycle.
#[tokio::test]
async fn w7_base_correction_is_current_and_a_decision_is_history() {
    let outcomes = run_task_state_cycles(
        "w7base",
        &w7_base_seeds(),
        &filler_cycles(5, 120),
    )
    .await;
    assert_eq!(outcomes.len(), 5, "every cycle must compact");
    for outcome in &outcomes {
        assert_correction_wins(
            outcome,
            &[
                (
                    "use architecture B for the reporting module",
                    "use architecture A for the reporting module",
                ),
                (
                    "use architecture B for the validator module",
                    "use architecture A for the validator module",
                ),
            ],
        );
        // No collateral damage: the unrelated decisions stay current.
        assert!(outcome
            .ledger
            .decisions
            .iter()
            .any(|item| item.text.contains("use architecture reconciler for the parser")));
        // End to end: the restored provider context carries the correction,
        // not the superseded decision.
        assert!(
            outcome.context.contains("use architecture B for the reporting module"),
            "cycle {}: the restored context must carry the correction",
            outcome.cycle
        );
        assert!(
            !outcome.context.contains("use architecture A for the reporting module"),
            "cycle {}: the restored context must not carry the superseded decision; context:\n{}",
            outcome.cycle,
            outcome.context
        );
    }
}

/// W9 finding 4: a correction planted at cycle 5 still retires the decision
/// captured at cycle 1 (late supersession across compaction merges).
#[tokio::test]
async fn w9f4_late_correction_retires_the_old_decision() {
    let mut seeds = vec![
        TestEntry::User(W7_CONSTRAINTS.to_string()),
        TestEntry::Assistant {
            text: "Started the migration plan.".into(),
            usage: 12.0,
        },
        TestEntry::Assistant {
            text: "Decision: use architecture reconciler for the parser R2-DECI-b42dbfea".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: keep the billing client in-process R2-DECI-2c55b71d".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: reject the gateway rewrite; extend instead R2-DECI-e382f68e".into(),
            usage: 40.0,
        },
        TestEntry::Assistant {
            text: "Decision: the ledger fix lands behind a feature flag R2-DECI-42a5f117".into(),
            usage: 40.0,
        },
        TestEntry::Tool {
            text: "cargo test: 4000 passed; 0 failed. Verified evidence R2-EVID-232a0357".into(),
            call_id: "call-evidence-0".into(),
        },
        TestEntry::Tool {
            text: "build succeeded with 4137 warnings. Verified evidence R2-EVID-15a18aac".into(),
            call_id: "call-evidence-1".into(),
        },
        TestEntry::Tool {
            text: "audit report indexer: 4274 findings, all closed. Verified evidence R2-EVID-ae341cdc".into(),
            call_id: "call-evidence-2".into(),
        },
        TestEntry::Tool {
            text: "benchmark reporting: p95 4411ms. Verified evidence R2-EVID-1432e5de".into(),
            call_id: "call-evidence-3".into(),
        },
        TestEntry::Assistant {
            text: "Decision: use architecture A for the reporting module R2-SUPE-48258b00".into(),
            usage: 20.0,
        },
        TestEntry::Assistant {
            text: "Decision: use architecture A for the validator module R2-SUPE-1762c030".into(),
            usage: 20.0,
        },
        TestEntry::Agent {
            text: "Subagent reviewer-1 finished section 3; artifact evidence/reviewer-1.json".into(),
            from: "reviewer-1".into(),
        },
        TestEntry::User("Continue the migration.".into()),
        TestEntry::Assistant {
            text: "Section 3 verified; publishing next.".into(),
            usage: 17_000.0,
        },
    ];
    seeds.shrink_to_fit();

    let mut cycles = filler_cycles(4, 120);
    // The corrections land late: cycle 5, after four compaction merges.
    cycles.push(vec![
        TestEntry::User(CORRECTION_REPORTING.into()),
        TestEntry::Assistant {
            text: "Noted; applying the correction.".into(),
            usage: 30.0,
        },
        TestEntry::User(CORRECTION_VALIDATOR.into()),
        TestEntry::Assistant {
            text: "Noted; applying the correction.".into(),
            usage: 30.0,
        },
        filler(4, 0, 120),
        noted(4),
    ]);

    let outcomes = run_task_state_cycles("w9late", &seeds, &cycles).await;
    assert_eq!(outcomes.len(), 5, "every cycle must compact");
    for outcome in &outcomes[..4] {
        assert!(
            outcome.ledger.superseded.is_empty(),
            "cycle {}: no corrections seen yet, nothing may be superseded: {:?}",
            outcome.cycle,
            outcome.ledger.superseded
        );
    }
    let last = outcomes.last().unwrap();
    assert_correction_wins(
        last,
        &[
            (
                "use architecture B for the reporting module",
                "use architecture A for the reporting module",
            ),
            (
                "use architecture B for the validator module",
                "use architecture A for the validator module",
            ),
        ],
    );
    assert_eq!(
        last.ledger.superseded.len(),
        2,
        "exactly the two A decisions may be retired: {:?}",
        last.ledger.superseded
    );
}

/// W9 finding 3: ordinary constraint lines never create supersession pairs.
/// The hard scenario's 20 constraints all stay active; only the four
/// architecture A decisions are superseded, by their own corrections.
#[tokio::test]
async fn hard_scenario_ordinary_constraints_do_not_churn() {
    let outcomes = run_task_state_cycles("hardchurn", &hard_seeds(), &filler_cycles(5, 200)).await;
    assert_eq!(outcomes.len(), 5, "every cycle must compact");
    for outcome in &outcomes {
        for anchor in HARD_CONSTRAINT_ANCHORS {
            assert!(
                outcome
                    .ledger
                    .constraints
                    .iter()
                    .any(|item| item.text.contains(anchor)),
                "cycle {}: ordinary constraint {anchor} churned away: {:?}",
                outcome.cycle,
                outcome.ledger.constraints
            );
        }
        let superseded_section = section(&outcome.rendered, "**SUPERSEDED");
        assert!(
            !superseded_section.contains("R2-CONS-"),
            "cycle {}: no ordinary constraint may render as superseded: {superseded_section}",
            outcome.cycle
        );
        assert_eq!(
            outcome.ledger.superseded.len(),
            4,
            "cycle {}: exactly the four A decisions may be superseded: {:?}",
            outcome.cycle,
            outcome.ledger.superseded
        );
        assert_correction_wins(
            outcome,
            &[
                (
                    "use architecture B for the reconciler module",
                    "use architecture A for the reconciler module",
                ),
                (
                    "use architecture B for the notifier module",
                    "use architecture A for the notifier module",
                ),
                (
                    "use architecture B for the payments module",
                    "use architecture A for the payments module",
                ),
                (
                    "use architecture B for the reporting module",
                    "use architecture A for the reporting module",
                ),
            ],
        );
    }
}

/// Idempotence: applying the same window to `build_ledger` twice changes
/// nothing (the corrections stay captured, the supersession pairs are stable,
/// and nothing is re-derived).
#[test]
fn build_ledger_is_idempotent_for_the_same_window() {
    fn user(id: &str, text: &str) -> CompactionSessionEntry {
        CompactionSessionEntry::Message {
            id: id.to_string(),
            parent_id: None,
            message: AgentMessage::Message(Message::User(UserMessage::new(
                UserContent::Text(text.into()),
                0,
            ))),
        }
    }
    fn assistant(id: &str, text: &str) -> CompactionSessionEntry {
        CompactionSessionEntry::Message {
            id: id.to_string(),
            parent_id: None,
            message: AgentMessage::Message(Message::Assistant(AssistantMessage {
                content: vec![ContentBlock::Text(TextContent::new(text))],
                stop_reason: "stop".into(),
                ..Default::default()
            })),
        }
    }

    let window = vec![
        user("seed-0001", W7_CONSTRAINTS),
        assistant(
            "seed-0002",
            "Decision: use architecture A for the reporting module R2-SUPE-48258b00.",
        ),
        assistant(
            "seed-0003",
            "Decision: use architecture A for the validator module R2-SUPE-1762c030.",
        ),
        user("seed-0004", CORRECTION_REPORTING),
        user("seed-0005", CORRECTION_VALIDATOR),
    ];

    let once = build_ledger(&window, None);
    assert!(
        once.constraints
            .iter()
            .any(|item| item.text.contains("use architecture B for the reporting module")),
        "the reporting correction must be an active constraint: {:?}",
        once.constraints
    );
    assert!(
        once.constraints
            .iter()
            .any(|item| item.text.contains("use architecture B for the validator module")),
        "the validator correction must be an active constraint: {:?}",
        once.constraints
    );
    assert!(
        once.decisions
            .iter()
            .all(|item| !item.text.contains("use architecture A")),
        "both A decisions must be retired: {:?}",
        once.decisions
    );
    assert_eq!(
        once.superseded.len(),
        2,
        "exactly the two A decisions may be superseded: {:?}",
        once.superseded
    );

    let twice = build_ledger(&window, Some(&once));
    assert_eq!(
        once, twice,
        "re-applying the same window must not change the ledger"
    );
    assert_eq!(
        render_ledger(&once),
        render_ledger(&twice),
        "re-applying the same window must not change the render"
    );
}
