//! W15 integration tests: the typed (v2) protected-state ledger wired into
//! the production compaction flow (`compaction.taskState = "typed"`).
//!
//! Covers the full wiring: mode resolution, `prepare_compaction` building
//! and merging the ledger over the real `SessionManager` JSONL persistence,
//! the budgeted allocator feeding the summarizer prompt, the unsafe-allocation
//! refusal, the deterministic verifier gating acceptance (with its bounded
//! missing-item repair), persistence under `details.typedState`, and v1
//! (`prompt` mode) interop.
//!
//! The summarizer is a deterministic fixture registered through
//! `register_api_provider_simple`: it obeys protected blocks (copying their
//! lines into the matching summary sections) with selectable failure modes,
//! so verifier outcomes measure pipeline behavior, not model skill.
//! No network. All state under a fresh temp dir per test.

use std::sync::{Arc, Mutex};

use pi_agent_core::types::{AgentMessage, CustomMessageContent};
use pi_ai::api_registry::{register_api_provider_simple, ApiProviderSimple};
use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, ContentBlock, Message, Model, TextContent,
    ToolResultMessage, UserContent, UserMessage,
};
use pi_ai::utils::event_stream::AssistantMessageEventStream;
use pi_coding_agent::core::compaction::compaction::{
    compact, default_compaction_settings, default_summary_call_runner, prepare_compaction,
    resolve_task_state_mode, typed_hot_state_budget_tokens, CompactionDetails,
    CompactionResult, CompactionSessionEntry, CompactionSettings, TypedVerificationRecord,
    TASK_STATE_MODE_OFF, TASK_STATE_MODE_PROMPT, TASK_STATE_MODE_TYPED,
};
use pi_coding_agent::core::compaction::typed_state::{build_typed_state, TypedInformationKind};
use pi_coding_agent::core::session_manager::{
    CustomMessageEntryContent, SessionManager, SessionEntry,
};
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Mode resolution and budget
// ---------------------------------------------------------------------------

#[test]
fn typed_mode_resolution_accepts_typed_and_fails_safe_on_unknown_values() {
    assert_eq!(
        resolve_task_state_mode(Some(TASK_STATE_MODE_TYPED), None),
        TASK_STATE_MODE_TYPED
    );
    assert_eq!(
        resolve_task_state_mode(None, Some(TASK_STATE_MODE_TYPED)),
        TASK_STATE_MODE_TYPED
    );
    // An unrecognized configured value fails safe to off without consulting
    // the environment.
    assert_eq!(
        resolve_task_state_mode(Some("on"), Some(TASK_STATE_MODE_TYPED)),
        TASK_STATE_MODE_OFF
    );
    assert_eq!(
        resolve_task_state_mode(Some(""), Some(TASK_STATE_MODE_TYPED)),
        TASK_STATE_MODE_OFF
    );
    assert_eq!(
        resolve_task_state_mode(None, Some("enabled")),
        TASK_STATE_MODE_OFF
    );
    // The v1 values keep their meaning.
    assert_eq!(
        resolve_task_state_mode(Some(TASK_STATE_MODE_PROMPT), Some(TASK_STATE_MODE_TYPED)),
        TASK_STATE_MODE_PROMPT
    );
    assert_eq!(
        resolve_task_state_mode(None, Some(TASK_STATE_MODE_PROMPT)),
        TASK_STATE_MODE_PROMPT
    );
    assert_eq!(resolve_task_state_mode(None, None), TASK_STATE_MODE_OFF);
}

#[test]
fn typed_hot_state_budget_is_a_capped_quarter_of_the_summary_budget() {
    // Legacy default summary budget (0.8 * 16384 = 13107): a quarter is 3276,
    // so the 2048 cap applies.
    assert_eq!(typed_hot_state_budget_tokens(13_107.0), 2048);
    assert_eq!(typed_hot_state_budget_tokens(6_000.0), 1_500);
    assert_eq!(typed_hot_state_budget_tokens(100.0), 25);
    assert_eq!(typed_hot_state_budget_tokens(0.0), 0);
    assert_eq!(typed_hot_state_budget_tokens(f64::NAN), 0);
    assert_eq!(typed_hot_state_budget_tokens(f64::NEG_INFINITY), 0);
}

// ---------------------------------------------------------------------------
// Session seeding helpers (mirror rot2_multicycle.rs / compaction_recovery.rs)
// ---------------------------------------------------------------------------

fn user_message(text: &str) -> AgentMessage {
    AgentMessage::Message(Message::User(UserMessage::new(
        UserContent::Text(text.into()),
        0,
    )))
}

fn assistant_message(text: &str) -> AgentMessage {
    AgentMessage::Message(Message::Assistant(AssistantMessage {
        content: vec![ContentBlock::Text(TextContent::new(text))],
        stop_reason: "stop".into(),
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

fn seed_user(manager: &mut SessionManager, text: &str) {
    manager.append_message(user_message(text)).unwrap();
}

fn seed_assistant(manager: &mut SessionManager, text: &str) {
    manager.append_message(assistant_message(text)).unwrap();
}

fn seed_tool(manager: &mut SessionManager, call_id: &str, name: &str, text: &str) {
    manager
        .append_message(tool_result_message(call_id, name, text))
        .unwrap();
}

/// A child-agent report as the session actually records it: a custom
/// `agent_message` entry from the daemon (fromRelationship: child).
fn seed_agent_message(manager: &mut SessionManager, from_name: &str, text: &str) {
    manager
        .append_custom_message_entry(
            "agent_message",
            &CustomMessageEntryContent::Text(text.to_string()),
            true,
            Some(json!({
                "id": format!("agentmsg-{from_name}"),
                "message": text,
                "fromRelationship": "child",
                "fromName": from_name,
            })),
        )
        .unwrap();
}

/// Mirrors the production converter (`compaction_session_entry_from`,
/// core/agent_session.rs), same as the rot2_multicycle suite: a session file
/// entry becomes the `CompactionSessionEntry` the kernel consumes.
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

fn typed_settings() -> CompactionSettings {
    let mut settings = default_compaction_settings();
    settings.keep_recent_tokens = 20.0;
    settings.task_state = Some(TASK_STATE_MODE_TYPED.to_string());
    settings
}

/// A session with the material the W4 classifier is supposed to retain: a
/// task statement, user constraints, a pending action, a cargo-test tool
/// result (the v1 evidence gap), a subagent tool report (SUBAGENT_STATE) and
/// a child-agent `agent_message` custom entry. Two trailing user fillers push
/// the cut point so everything above lands in the summarized window.
fn seed_window(manager: &mut SessionManager) {
    seed_user(
        manager,
        "TASK GOAL: migrate the billing service to the new ledger. \
CONSTRAINT: never delete the payments ledger ARCH-KEEP-LEDGER. \
CONSTRAINT: the API must stay frozen ARCH-FROZEN-API. \
Also don't forget to update the changelog after the migration ARCH-TODO-CHANGELOG.",
    );
    seed_assistant(manager, "Started the migration plan for the billing service.");
    // The child-agent report, as the session records it (custom entry).
    seed_agent_message(
        manager,
        "bench-runner",
        "Subagent bench-runner reported: baseline unchanged, 2 nits.",
    );
    seed_tool(
        manager,
        "call-bash-1",
        "bash",
        "running cargo test\ntest result: ok. 4785 passed; 0 failed; 0 ignored\nfinished in 13.2s",
    );
    seed_tool(
        manager,
        "call-agent-1",
        "agent",
        "Subagent api-reviewer finished: no blocking findings, 2 nits. RLM_CHILD_STATUS: complete",
    );
    seed_user(manager, "status update: continuing the integration work on the migration plan.");
    seed_user(manager, "status update: still working through the checklist for the migration.");
}

/// A session manager over a fresh temp dir. The returned guard keeps the
/// dir alive; drop both at the test's end.
fn new_manager(prefix: &str) -> (SessionManager, tempfile::TempDir) {
    let root = tempfile::Builder::new().prefix(prefix).tempdir().unwrap();
    let dir = root.path().to_string_lossy().to_string();
    let manager = SessionManager::create(&dir, Some(&dir)).unwrap();
    (manager, root)
}

// ---------------------------------------------------------------------------
// Deterministic summarizer fixture
// ---------------------------------------------------------------------------

#[derive(Clone)]
enum FixtureFlavor {
    /// Copies every protected line into the matching summary section.
    Obedient,
    /// Obedient, except protected lines containing the marker are dropped.
    DropsMarker(String),
    /// Presents the superseded section's text as current constraints and
    /// omits the corrections section: a model promoting retired material.
    LeaksSupersededAsCurrent,
}

/// Parse the protected block out of a summarizer request (typed or v1) and
/// route its item lines into the six summary sections `validate_summary`
/// requires.
fn fixture_summary(request: &str, flavor: &FixtureFlavor) -> String {
    let mut goal: Vec<String> = Vec::new();
    let mut constraints: Vec<String> = Vec::new();
    let mut next: Vec<String> = Vec::new();
    let mut context: Vec<String> = Vec::new();
    let mut in_block = false;
    let mut section = String::new();
    for line in request.lines() {
        if line.starts_with("## Typed Protected State")
            || line.starts_with("## Protected Task State")
        {
            in_block = true;
            continue;
        }
        if !in_block {
            continue;
        }
        if line.starts_with("Copy every") {
            // The instruction appended after the block; the block is complete.
            break;
        }
        if let Some(item) = line.strip_prefix("- ") {
            let section = section.as_str();
            let bullet = format!("- {item}");
            match section {
                "ACTIVE CONSTRAINTS" | "SAFETY BOUNDARIES" | "ACCEPTANCE CRITERIA" => {
                    constraints.push(bullet);
                }
                "USER CORRECTIONS" => {
                    if !matches!(flavor, FixtureFlavor::LeaksSupersededAsCurrent) {
                        constraints.push(bullet);
                    }
                }
                "CURRENT TASK" => goal.push(bullet),
                "PENDING ACTIONS" => next.push(bullet),
                "SUPERSEDED (HISTORY, NOT CURRENT)" => {
                    if matches!(flavor, FixtureFlavor::LeaksSupersededAsCurrent) {
                        // Present the retired text as a current constraint,
                        // without the retiring record.
                        let stripped = item
                            .split(" (superseded by")
                            .next()
                            .unwrap_or(item)
                            .to_string();
                        constraints.push(format!("- {stripped}"));
                    }
                }
                _ => context.push(bullet),
            }
        } else if !line.trim().is_empty() {
            section = line.trim().trim_matches('*').to_string();
        }
    }
    if let Some(marker) = match flavor {
        FixtureFlavor::DropsMarker(marker) => Some(marker.as_str()),
        _ => None,
    } {
        constraints.retain(|line| !line.contains(marker));
        goal.retain(|line| !line.contains(marker));
        next.retain(|line| !line.contains(marker));
        context.retain(|line| !line.contains(marker));
    }
    assemble_summary(&goal, &constraints, &next, &context)
}

fn assemble_summary(
    goal: &[String],
    constraints: &[String],
    next: &[String],
    context: &[String],
) -> String {
    let mut out = String::new();
    out.push_str("## Goal\n");
    if goal.is_empty() {
        out.push_str("- Continue the user's task from the retained state.\n");
    } else {
        for line in goal {
            out.push_str(line);
            out.push('\n');
        }
    }
    out.push_str("\n## Constraints & Preferences\n");
    if constraints.is_empty() {
        out.push_str("- None retained by the protected block.\n");
    } else {
        for line in constraints {
            out.push_str(line);
            out.push('\n');
        }
    }
    out.push_str("\n## Progress\n### Done\n- [x] Prior turns summarized by the fixture.\n\n### In Progress\n- [ ] Continuing the task.\n\n### Blocked\n- None.\n");
    out.push_str("\n## Key Decisions\n- None recorded by the protected block.\n");
    out.push_str("\n## Next Steps\n1. Continue the work from the retained state.\n");
    for line in next {
        out.push_str(line);
        out.push('\n');
    }
    out.push_str("\n## Critical Context\n");
    if context.is_empty() {
        out.push_str("- None retained by the protected block.\n");
    } else {
        for line in context {
            out.push_str(line);
            out.push('\n');
        }
    }
    out
}

/// Registers the deterministic summarizer fixture; returns the model plus the
/// captured request texts.
fn summarizer_fixture(api: &str, flavor: FixtureFlavor) -> (Model, Arc<Mutex<Vec<String>>>) {
    let captured: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let seen = Arc::clone(&captured);
    let flavor_for_call = flavor.clone();
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
                let summary = fixture_summary(&text, &flavor_for_call);
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

/// One compaction attempt over the current session state.
async fn run_compaction(
    manager: &mut SessionManager,
    settings: &CompactionSettings,
    model: &Model,
) -> Result<CompactionResult, String> {
    let branch = manager.get_branch(None);
    let entries = path_entries(&branch);
    let preparation =
        prepare_compaction(&entries, settings, &|_| 40_000.0).expect("prepare_compaction");
    compact(
        &preparation,
        model,
        "unused",
        None,
        None,
        None,
        default_summary_call_runner(None),
        None,
        None,
    )
    .await
}

fn details_value(result: &CompactionResult) -> Value {
    serde_json::to_value(result.details.as_ref().expect("details")).expect("serialize details")
}

// ---------------------------------------------------------------------------
// prepare_compaction: typed ledger from the seeded window
// ---------------------------------------------------------------------------

#[test]
fn prepare_compaction_builds_a_typed_ledger_from_tool_results_and_subagent_state() {
    let (mut manager, _root) = new_manager("rot2-typed-prepare-");
    seed_window(&mut manager);
    let branch = manager.get_branch(None);
    let entries = path_entries(&branch);
    let settings = typed_settings();
    let preparation = prepare_compaction(&entries, &settings, &|_| 40_000.0)
        .expect("prepare_compaction must run for a non-empty window");

    let typed = preparation
        .typed_state
        .as_ref()
        .expect("typed mode must produce a typed ledger");
    let has = |kind: TypedInformationKind, needle: &str| {
        typed
            .records
            .iter()
            .any(|record| record.kind == kind && record.text.contains(needle))
    };
    // The v1 evidence gap: cargo-test lines must be captured as
    // VERIFIED_TOOL_RESULT.
    assert!(
        has(TypedInformationKind::VerifiedToolResult, "4785 passed"),
        "cargo test lines must be retained as verified tool results"
    );
    // The subagent tool report must be captured as SUBAGENT_STATE.
    assert!(
        has(TypedInformationKind::SubagentState, "api-reviewer"),
        "the subagent report must be retained as subagent state"
    );
    // The user-authority material the classifier exists to protect.
    assert!(has(TypedInformationKind::UserConstraint, "payments ledger"));
    assert!(has(TypedInformationKind::CurrentTask, "billing service"));
    assert!(has(TypedInformationKind::PendingAction, "changelog"));
    // The v1 mode field stays untouched in typed mode.
    assert!(preparation.task_state.is_none());

    // W4 boundary, pinned deliberately: `typed_state::entry_role_text`
    // classifies only `Message` entries ("custom messages are machine
    // text"), so a child-agent `agent_message` custom entry is NOT retained
    // as SUBAGENT_STATE by the committed module. See the W15 report: closing
    // this gap needs a typed_state.rs change (read-only for this worker).
    let custom_entry_id = branch[2]
        .get("id")
        .and_then(Value::as_str)
        .expect("custom agent_message entry id");
    assert!(
        typed
            .records
            .iter()
            .all(|record| record.source_entry_id != custom_entry_id),
        "the custom agent_message entry must not be classified by W4 as committed"
    );
    assert!(
        !typed
            .records
            .iter()
            .any(|record| record.text.contains("bench-runner")),
        "the child-agent report text must not be retained under W4 as committed"
    );
}

// ---------------------------------------------------------------------------
// compact(): protected block, persistence, round-trip merge
// ---------------------------------------------------------------------------

#[tokio::test]
async fn compact_typed_mode_feeds_the_protected_block_and_round_trips_the_ledger() {
    let (mut manager, _root) = new_manager("rot2-typed-roundtrip-");
    seed_window(&mut manager);
    let settings = typed_settings();
    let (model, captured) =
        summarizer_fixture("rot2-typed-obedient", FixtureFlavor::Obedient);

    let result = run_compaction(&mut manager, &settings, &model)
        .await
        .expect("the obedient fixture must pass verification");

    // The protected block and its copy instruction reached the summarizer.
    let requests = captured.lock().unwrap().clone();
    assert!(!requests.is_empty(), "the summarizer must be called");
    let request = requests.join("\n");
    assert!(
        request.contains("## Typed Protected State"),
        "the typed hot-state block must be attached to the summarizer prompt"
    );
    assert!(
        request.contains("ARCH-KEEP-LEDGER"),
        "the protected constraint must be in the block"
    );
    assert!(
        request.contains("Copy every ACTIVE CONSTRAINT"),
        "the typed copy instruction must follow the block"
    );

    // The ledger and the verification outcome persist on the details.
    let details = result.details.as_ref().expect("details");
    let typed = details.typed_state.as_ref().expect("typedState persisted");
    let constraint = typed
        .records
        .iter()
        .find(|record| {
            record.kind == TypedInformationKind::UserConstraint
                && record.text.contains("payments ledger")
        })
        .expect("the payments-ledger constraint record");
    let verification = details
        .typed_verification
        .as_ref()
        .expect("typedVerification persisted");
    assert!(verification.passed);
    assert!(!verification.repaired);
    assert_eq!(verification.missing, 0);
    assert_eq!(verification.mis_superseded, 0);
    // The summary carries the constraint forward.
    assert!(result.summary.contains("ARCH-KEEP-LEDGER"));
    // The v1 field is not overloaded.
    let value = details_value(&result);
    assert!(value.get("typedState").is_some());
    assert!(value.get("taskState").is_none());
    assert!(value.get("typedVerification").is_some());
    assert!(details.task_state.is_none());

    // Round-trip: persist the compaction, then a second prepare_compaction
    // over new material must merge the carried ledger with the new window.
    manager
        .append_compaction(
            &result.summary,
            &result.first_kept_entry_id,
            result.tokens_before,
            Some(details_value(&result)),
            None,
            None,
            None,
            None,
        )
        .unwrap();
    manager.flush_now().unwrap();

    seed_user(
        &mut manager,
        "CONSTRAINT: always keep the changelog frozen ARCH-2-CHANGELOG.",
    );
    seed_user(&mut manager, "status update: second cycle filler content for the window.");
    seed_user(&mut manager, "status update: more second cycle filler content for the window.");
    let branch = manager.get_branch(None);
    let entries = path_entries(&branch);
    let preparation = prepare_compaction(&entries, &settings, &|_| 40_000.0)
        .expect("second prepare must run");
    let merged = preparation
        .typed_state
        .as_ref()
        .expect("second prepare must produce a merged typed ledger");
    assert!(
        merged
            .records
            .iter()
            .any(|record| record.id == constraint.id && record.text.contains("payments ledger")),
        "the cycle-1 constraint must be carried forward by id through details.typedState"
    );
    assert!(
        merged
            .records
            .iter()
            .any(|record| record.kind == TypedInformationKind::UserConstraint
                && record.text.contains("changelog frozen")),
        "the cycle-2 window constraint must be present in the merged ledger"
    );
}

// ---------------------------------------------------------------------------
// Allocator overflow
// ---------------------------------------------------------------------------

#[tokio::test]
async fn unsafe_typed_allocation_fails_the_episode_before_any_model_call() {
    let (mut manager, _root) = new_manager("rot2-typed-overflow-");
    seed_window(&mut manager);
    let mut settings = typed_settings();
    // Legacy summary budget 0.8 * 64 = 51 tokens, so the hot-state budget is
    // floor(51 / 4) = 12 tokens: far less than the mandatory tier needs.
    settings.reserve_tokens = 64.0;
    let (model, captured) =
        summarizer_fixture("rot2-typed-overflow", FixtureFlavor::Obedient);

    let error = run_compaction(&mut manager, &settings, &model)
        .await
        .expect_err("an unsafe allocation must fail the episode");
    assert!(
        error.contains("Typed protected state did not fit the hot-state budget"),
        "the failure must name the unsafe allocation: {error}"
    );
    assert!(
        error.contains("existing conversation preserved"),
        "the failure must surface like a summary-validation failure: {error}"
    );
    assert!(
        error.contains("mandatory material dropped"),
        "the failure must record the overflow: {error}"
    );
    assert!(
        error.contains("USER_CONSTRAINT"),
        "the overflow detail must name the dropped kinds: {error}"
    );
    assert!(
        captured.lock().unwrap().is_empty(),
        "no summarizer call may be made when the allocation is unsafe"
    );
}

// ---------------------------------------------------------------------------
// Verifier: bounded repair
// ---------------------------------------------------------------------------

#[tokio::test]
async fn missing_constraint_triggers_the_deterministic_repair() {
    let (mut manager, _root) = new_manager("rot2-typed-repair-");
    seed_window(&mut manager);
    let settings = typed_settings();
    let (model, captured) = summarizer_fixture(
        "rot2-typed-drops-marker",
        FixtureFlavor::DropsMarker("ARCH-FROZEN-API".to_string()),
    );

    let result = run_compaction(&mut manager, &settings, &model)
        .await
        .expect("the bounded repair must recover a missing constraint");

    // The model saw the constraint in the protected block and dropped it.
    let requests = captured.lock().unwrap().clone();
    assert!(
        requests.join("\n").contains("ARCH-FROZEN-API"),
        "the dropped constraint was in the protected block"
    );
    let details = result.details.as_ref().expect("details");
    let frozen = details
        .typed_state
        .as_ref()
        .expect("typedState persisted")
        .records
        .iter()
        .find(|record| {
            record.kind == TypedInformationKind::UserConstraint
                && record.text.contains("ARCH-FROZEN-API")
        })
        .expect("the frozen-API constraint record");
    let verification = details
        .typed_verification
        .as_ref()
        .expect("typedVerification persisted");
    assert!(verification.passed, "the repaired summary must verify");
    assert!(verification.repaired, "the repair must be recorded");
    assert_eq!(verification.missing, 0, "the re-verification must be clean");
    // The repaired summary carries the missing item verbatim with its id.
    assert!(
        result.summary.contains(&format!("[{}]", frozen.id)),
        "the repaired line must carry the record id: {}",
        result.summary
    );
    assert!(result.summary.contains("ARCH-FROZEN-API"));
}

// ---------------------------------------------------------------------------
// Verifier: unrecoverable failure
// ---------------------------------------------------------------------------

#[tokio::test]
async fn superseded_decision_presented_as_current_fails_verification() {
    let (mut manager, _root) = new_manager("rot2-typed-superseded-");
    seed_user(
        &mut manager,
        "TASK GOAL: build the parser for the migration. CONSTRAINT: the parser must use architecture A ARCH-PARSER-CHOICE.",
    );
    seed_user(
        &mut manager,
        "Correction: the parser must use architecture B ARCH-PARSER-CHOICE instead.",
    );
    seed_user(&mut manager, "status update: continuing the parser work on the migration plan.");
    seed_user(&mut manager, "status update: still working through the parser checklist items.");
    let settings = typed_settings();
    let (model, _captured) = summarizer_fixture(
        "rot2-typed-leaky",
        FixtureFlavor::LeaksSupersededAsCurrent,
    );

    let error = run_compaction(&mut manager, &settings, &model)
        .await
        .expect_err("a superseded decision presented as current must fail the episode");
    assert!(
        error.contains("typed protected state not represented"),
        "the failure must be reported as a typed verification failure: {error}"
    );
    assert!(
        error.contains("mis-superseded"),
        "the mis-superseded finding must be counted: {error}"
    );
    assert!(
        error.contains("existing conversation preserved"),
        "the failure must surface like a summary-validation failure: {error}"
    );
}

// ---------------------------------------------------------------------------
// v1 interop
// ---------------------------------------------------------------------------

#[test]
fn v1_task_state_details_still_deserialize_alongside_the_typed_fields() {
    // An old (v1) compaction entry's details: no typedState, no
    // typedVerification, and the v1 taskState payload.
    let old = json!({
        "readFiles": ["src/parser.rs"],
        "modifiedFiles": [],
        "taskState": {
            "constraints": [
                {"id": "C1", "text": "never drop the payments ledger", "entry": "t1"}
            ],
            "decisions": [],
            "superseded": [],
            "evidence": [],
            "anchors": [],
            "openWork": [],
            "retractions": []
        }
    });
    let details: CompactionDetails =
        serde_json::from_value(old).expect("v1 details must deserialize");
    assert!(details.task_state.is_some());
    assert_eq!(
        details.task_state.as_ref().unwrap().constraints[0].text,
        "never drop the payments ledger"
    );
    assert!(details.typed_state.is_none());
    assert!(details.typed_verification.is_none());

    // A typed entry round-trips through its own field names.
    let entry = CompactionSessionEntry::Message {
        id: "t1".to_string(),
        parent_id: None,
        message: user_message("CONSTRAINT: never drop the payments ledger ARCH-KEEP-LEDGER."),
    };
    let typed_details = CompactionDetails {
        read_files: Vec::new(),
        modified_files: Vec::new(),
        provider_checkpoint: None,
        deadline_fallback: None,
        task_state: None,
        typed_state: Some(build_typed_state(&[entry])),
        typed_verification: Some(TypedVerificationRecord {
            passed: true,
            ..Default::default()
        }),
    };
    let value = serde_json::to_value(&typed_details).expect("serialize typed details");
    assert!(value.get("typedState").is_some());
    assert!(value.get("typedVerification").is_some());
    assert!(value.get("taskState").is_none());
    let back: CompactionDetails =
        serde_json::from_value(value).expect("typed details must round-trip");
    assert_eq!(back, typed_details);
}

#[tokio::test]
async fn v1_prompt_mode_still_works_unchanged() {
    let (mut manager, _root) = new_manager("rot2-typed-v1prompt-");
    seed_window(&mut manager);
    let mut settings = default_compaction_settings();
    settings.keep_recent_tokens = 20.0;
    settings.task_state = Some(TASK_STATE_MODE_PROMPT.to_string());
    let (model, captured) = summarizer_fixture("rot2-typed-v1-obedient", FixtureFlavor::Obedient);

    let result = run_compaction(&mut manager, &settings, &model)
        .await
        .expect("the v1 prompt mode must keep working");

    // The v1 block is attached, not the typed one.
    let requests = captured.lock().unwrap().clone();
    let request = requests.join("\n");
    assert!(
        request.contains("## Protected Task State"),
        "the v1 ledger block must still be attached"
    );
    assert!(
        !request.contains("## Typed Protected State"),
        "the typed block must not appear in v1 mode"
    );
    let details = result.details.as_ref().expect("details");
    let v1_ledger = details.task_state.as_ref().expect("v1 taskState persisted");
    assert!(
        v1_ledger
            .constraints
            .iter()
            .any(|item| item.text.contains("payments ledger")),
        "the v1 ledger must still capture the constraint"
    );
    assert!(details.typed_state.is_none());
    assert!(details.typed_verification.is_none());
    let value = details_value(&result);
    assert!(value.get("taskState").is_some());
    assert!(value.get("typedState").is_none());

    // And a second prepare in v1 mode still merges through details.taskState.
    manager
        .append_compaction(
            &result.summary,
            &result.first_kept_entry_id,
            result.tokens_before,
            Some(value),
            None,
            None,
            None,
            None,
        )
        .unwrap();
    manager.flush_now().unwrap();
    seed_user(
        &mut manager,
        "CONSTRAINT: always keep the changelog frozen ARCH-2-CHANGELOG.",
    );
    seed_user(&mut manager, "status update: second cycle filler content for the window.");
    seed_user(&mut manager, "status update: more second cycle filler content for the window.");
    let branch = manager.get_branch(None);
    let entries = path_entries(&branch);
    let preparation = prepare_compaction(&entries, &settings, &|_| 40_000.0)
        .expect("second v1 prepare must run");
    let v1_merged = preparation
        .task_state
        .as_ref()
        .expect("v1 mode must still produce a v1 ledger");
    assert!(
        v1_merged
            .constraints
            .iter()
            .any(|item| item.text.contains("payments ledger")),
        "the cycle-1 v1 constraint must carry forward through details.taskState"
    );
    assert!(preparation.typed_state.is_none());
}
