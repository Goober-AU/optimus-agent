//! W13 D10 regression: branch summaries must carry tool results.
//!
//! `get_message_from_entry` used to return `None` for `toolResult` entries,
//! so a branch summary was built from a conversation with every tool output
//! removed: the exact evidence needed to continue a task on the branch (a
//! failing test line, a compiler error) never reached the summarizer. These
//! tests drive `generate_branch_summary` over a fixture API whose
//! `stream_simple` captures the full summarizer request (the same capture
//! pattern as `tests/compaction_recovery.rs`) and assert the tool result is
//! in the input, paired with its assistant tool call, and excerpted with the
//! evidence-aware policy instead of dropped.
//!
//! No network, no real provider: the summary wire call goes to a fixture API
//! registered through `register_api_provider_simple`.

use std::sync::{Arc, Mutex};

use pi_agent_core::types::AgentMessage;
use pi_ai::api_registry::{register_api_provider_simple, ApiProviderSimple};
use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, ContentBlock, Message, Model, TextContent, ToolCall,
    ToolResultMessage, UserContent, UserMessage,
};
use pi_ai::utils::event_stream::AssistantMessageEventStream;
use pi_coding_agent::core::compaction::branch_summarization::{
    generate_branch_summary, GenerateBranchSummaryOptions, SessionEntry,
};

fn fixture_model(api: &str) -> Model {
    let mut model = Model::new(api, api, api, "fixture", "https://fixture.invalid");
    model.context_window = 1_000_000.0;
    model.max_tokens = 64_000.0;
    model
}

/// Registers a fixture API whose `stream_simple` captures the full text of
/// every request's first user message and answers with a fixed summary body.
fn summarizer_fixture(api: &str) -> (Model, Arc<Mutex<Vec<String>>>) {
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
                seen.lock().unwrap().push(text);
                let stream = AssistantMessageEventStream::new();
                stream.push(AssistantMessageEvent::Done {
                    reason: "stop".into(),
                    message: AssistantMessage {
                        content: vec![ContentBlock::Text(TextContent::new(
                            "## Goal\nFix the failing test.\n## Progress\n### Blocked\n- rot2_w13_regress_target fails.",
                        ))],
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
    (fixture_model(api), captured)
}

fn user_entry(id: &str, parent_id: Option<&str>, text: &str, timestamp: i64) -> SessionEntry {
    SessionEntry::Message {
        id: id.to_string(),
        parent_id: parent_id.map(str::to_string),
        message: AgentMessage::Message(Message::User(UserMessage::new(
            UserContent::Text(text.to_string()),
            timestamp,
        ))),
    }
}

fn assistant_entry(id: &str, parent_id: Option<&str>, timestamp: i64) -> SessionEntry {
    SessionEntry::Message {
        id: id.to_string(),
        parent_id: parent_id.map(str::to_string),
        message: AgentMessage::Message(Message::Assistant(AssistantMessage {
            content: vec![
                ContentBlock::Text(TextContent::new("I will run the test suite.")),
                ContentBlock::ToolCall(ToolCall::new("call-1", "bash", {
                    let mut arguments = serde_json::Map::new();
                    arguments.insert(
                        "command".to_string(),
                        serde_json::json!("cargo test rot2_w13_regress"),
                    );
                    arguments
                })),
            ],
            timestamp,
            ..Default::default()
        })),
    }
}

/// A tool result whose failing-test evidence sits in the middle of a long
/// log, beyond both the head and the tail window of the excerpt policy.
fn failing_test_log() -> String {
    let mut log = String::new();
    for index in 0..40 {
        log.push_str(&format!("INFO [worker-1] entry={index} op=append ok\n"));
    }
    log.push_str("test result: FAILED. 4781 passed; 1 failed; 0 ignored\n");
    log.push_str("failures:\n");
    log.push_str("    rot2_w13_regress_target\n");
    for index in 0..40 {
        log.push_str(&format!("INFO [worker-2] entry={index} op=flush ok\n"));
    }
    log
}

fn tool_result_entry(id: &str, parent_id: Option<&str>, log: &str, timestamp: i64) -> SessionEntry {
    SessionEntry::Message {
        id: id.to_string(),
        parent_id: parent_id.map(str::to_string),
        message: AgentMessage::Message(Message::ToolResult(ToolResultMessage::new(
            "call-1",
            "bash",
            vec![pi_ai::types::ImageOrTextContent::Text(TextContent::new(
                log.to_string(),
            ))],
            true,
            timestamp,
        ))),
    }
}

/// The abandoned branch: a user request, an assistant tool call, the failing
/// test result in the middle, then a follow-up exchange.
fn abandoned_branch() -> Vec<SessionEntry> {
    vec![
        user_entry("u1", None, "Fix the failing rot2 test.", 1),
        assistant_entry("a1", Some("u1"), 2),
        tool_result_entry("t1", Some("a1"), &failing_test_log(), 3),
        user_entry("u2", Some("t1"), "What failed?", 4),
        SessionEntry::Message {
            id: "a2".to_string(),
            parent_id: Some("u2".to_string()),
            message: AgentMessage::Message(Message::Assistant(AssistantMessage {
                content: vec![ContentBlock::Text(TextContent::new(
                    "See the test output for the failure.",
                ))],
                timestamp: 5,
                ..Default::default()
            })),
        },
    ]
}

#[tokio::test]
async fn branch_summary_input_carries_the_failing_tool_result() {
    let (model, captured) = summarizer_fixture("rot2-w13-branch-summary-fixture");
    let result = generate_branch_summary(
        &abandoned_branch(),
        GenerateBranchSummaryOptions {
            model,
            api_key: "unused".to_string(),
            headers: None,
            signal: None,
            custom_instructions: None,
            replace_instructions: None,
            retry: None,
            reserve_tokens: None,
        },
    )
    .await;
    assert_eq!(result.error, None, "fixture summarizer must succeed");
    assert_eq!(result.aborted, None);

    let requests = captured.lock().unwrap();
    assert_eq!(requests.len(), 1, "exactly one summarizer request");
    let request = &requests[0];
    assert!(
        request.contains("<conversation>"),
        "the request must frame the serialized conversation"
    );
    // The tool result is summarizer input (D10), not silently dropped.
    assert!(
        request.contains("[User]: Fix the failing rot2 test."),
        "user turn missing from summary input"
    );
    // Pairing: the result renders against its assistant tool call number.
    assert!(
        request.contains("[Assistant tool calls]: #1 bash("),
        "assistant tool call missing from summary input: {request}"
    );
    assert!(
        request.contains("[Tool result #1 bash ERROR]:"),
        "tool result must render paired with its call and error flag: {request}"
    );
    // The evidence-aware excerpt policy kept the failing line from the
    // middle of the log - beyond both the head and the tail window.
    assert!(
        request.contains("test result: FAILED. 4781 passed; 1 failed; 0 ignored"),
        "failing test line lost from summary input: {request}"
    );
    assert!(
        request.contains("rot2_w13_regress_target"),
        "failing test name lost from summary input: {request}"
    );
    assert!(
        request.contains("characters truncated"),
        "the excerpt policy must mark the elided middle, not include it silently"
    );

    // The generated branch summary itself still comes from the fixture body.
    let summary = result.summary.expect("summary text");
    assert!(summary.contains("Fix the failing test."));
    assert!(summary.contains("rot2_w13_regress_target fails."));
}

#[tokio::test]
async fn branch_summary_input_skips_tool_results_with_no_text() {
    // A tool result with only image content renders nothing (the historical
    // `serialize_conversation` rule); the branch summary must still generate
    // over the remaining conversation instead of failing.
    let (model, captured) = summarizer_fixture("rot2-w13-branch-summary-imageonly");
    let mut entries = abandoned_branch();
    entries[2] = SessionEntry::Message {
        id: "t1".to_string(),
        parent_id: Some("a1".to_string()),
        message: AgentMessage::Message(Message::ToolResult(ToolResultMessage::new(
            "call-1",
            "view",
            vec![pi_ai::types::ImageOrTextContent::Image(Default::default())],
            false,
            3,
        ))),
    };
    let result = generate_branch_summary(
        &entries,
        GenerateBranchSummaryOptions {
            model,
            api_key: "unused".to_string(),
            headers: None,
            signal: None,
            custom_instructions: None,
            replace_instructions: None,
            retry: None,
            reserve_tokens: None,
        },
    )
    .await;
    assert_eq!(result.error, None);
    let requests = captured.lock().unwrap();
    let request = &requests[0];
    assert!(!request.contains("[Tool result"));
    assert!(request.contains("[User]: Fix the failing rot2 test."));
}
