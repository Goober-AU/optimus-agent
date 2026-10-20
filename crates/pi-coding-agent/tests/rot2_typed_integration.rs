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
//! W79 adds the two hops PRODUCTION-PATH-TRACE.md flagged as unpinned: the
//! session-adapter hop (a real `AgentSession` whose settings manager carries
//! `compaction.taskState = "typed"` driving the public manual compaction) and
//! the `PRIME_AGENT_COMPACTION_TASK_STATE` process-environment route.
//!
//! The summarizer is a deterministic fixture registered through
//! `register_api_provider_simple`: it obeys protected blocks (copying their
//! lines into the matching summary sections) with selectable failure modes,
//! so verifier outcomes measure pipeline behavior, not model skill.
//! No network. All state under a fresh temp dir per test.

use std::sync::{Arc, Mutex};

use pi_agent_core::agent::{default_stream_fn, AgentContinueError};
use pi_agent_core::performance_metrics::AgentLoopPerformanceMetrics;
use pi_agent_core::types::{
    AgentEvent, AgentMessage, AgentState, CustomMessageContent, ShouldStopAfterTurnContext,
    StreamFn,
};
use pi_ai::api_registry::{register_api_provider_simple, ApiProviderSimple};
use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, BoxFuture, ContentBlock, Message, Model, OnPayload,
    OnResponse, TextContent, ToolResultMessage, UserContent, UserMessage,
};
use pi_ai::utils::event_stream::AssistantMessageEventStream;
use pi_coding_agent::core::agent_session::{
    AfterToolCallHook, AgentHandle, AgentSession, AgentSessionConfig, BeforeRequestHook,
    BeforeToolCallHook, GetContinuationMessagesHook,
};
use pi_coding_agent::core::auth_storage::AuthStorage;
use pi_coding_agent::core::compaction::compaction::{
    compact, default_compaction_settings, default_summary_call_runner, prepare_compaction,
    resolve_task_state_mode, typed_hot_state_budget_tokens, CompactionDetails, CompactionResult,
    CompactionSessionEntry, CompactionSettings, TypedVerificationRecord, TASK_STATE_ENV,
    TASK_STATE_MODE_OFF, TASK_STATE_MODE_PROMPT, TASK_STATE_MODE_TYPED,
};
use pi_coding_agent::core::compaction::typed_state::{build_typed_state, TypedInformationKind};
use pi_coding_agent::core::model_registry::ModelRegistry;
use pi_coding_agent::core::resource_loader::{DefaultResourceLoader, DefaultResourceLoaderOptions};
use pi_coding_agent::core::session_manager::{
    CustomMessageEntryContent, SessionEntry, SessionManager,
};
use pi_coding_agent::core::settings_manager::SettingsManager;
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;

// ---------------------------------------------------------------------------
// W79: PRIME_AGENT_COMPACTION_TASK_STATE process-environment serialization
// ---------------------------------------------------------------------------

/// Serializes the tests that read or write `PRIME_AGENT_COMPACTION_TASK_STATE`
/// through the real process environment. The Rust test harness runs the tests
/// of one binary on parallel threads, and three tests in this file resolve
/// the variable through the process environment: the resolver matrix below
/// (its `None`-environment calls fall back to `std::env::var` inside
/// `resolve_task_state_mode`, compaction.rs), the process-env test at the
/// bottom of this file, and the default-`off` control arm of the
/// session-adapter test (the settings manager reads the environment whenever
/// the setting is absent, settings_manager.rs). The lock keeps those reads
/// deterministic; no dependency is added.
static TASK_STATE_ENV_MUTEX: Mutex<()> = Mutex::new(());

/// Owns `PRIME_AGENT_COMPACTION_TASK_STATE` for a scope: exports or removes
/// it, then restores the value the test found on drop, so a failed assertion
/// still leaves the process environment as it was.
struct TaskStateEnvScope {
    original: Option<String>,
}

impl TaskStateEnvScope {
    /// Exports the variable as `typed` for the scope's lifetime.
    fn set_typed() -> Self {
        let original = std::env::var(TASK_STATE_ENV).ok();
        std::env::set_var(TASK_STATE_ENV, TASK_STATE_MODE_TYPED);
        Self { original }
    }

    /// Pins the variable absent for the scope's lifetime.
    fn removed() -> Self {
        let original = std::env::var(TASK_STATE_ENV).ok();
        std::env::remove_var(TASK_STATE_ENV);
        Self { original }
    }
}

impl Drop for TaskStateEnvScope {
    fn drop(&mut self) {
        match self.original.take() {
            Some(value) => std::env::set_var(TASK_STATE_ENV, value),
            None => std::env::remove_var(TASK_STATE_ENV),
        }
    }
}

// ---------------------------------------------------------------------------
// Mode resolution and budget
// ---------------------------------------------------------------------------

#[test]
fn typed_mode_resolution_accepts_typed_and_fails_safe_on_unknown_values() {
    // The `None`-environment calls below fall back to the real process
    // variable (`resolve_task_state_mode`, compaction.rs), so this test
    // serializes against the process-env tests in this file and pins the
    // variable absent for its duration.
    let _serial = TASK_STATE_ENV_MUTEX.lock().unwrap();
    let _env = TaskStateEnvScope::removed();
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

// ---------------------------------------------------------------------------
// W79: hop 6 - the session adapter (compaction.taskState -> CompactionSettings)
// ---------------------------------------------------------------------------

/// A minimal `AgentHandle` double for driving a real `AgentSession`
/// compaction without the agent loop: a trimmed mirror of the in-crate
/// `ScriptedAgent` (agent_session.rs). The session reads the state snapshot
/// (model + live messages) and installs hooks that never fire during a
/// manually triggered compaction.
struct StaticAgent {
    state: Mutex<AgentState>,
    stream_fn: Mutex<StreamFn>,
}

impl StaticAgent {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(AgentState::default()),
            stream_fn: Mutex::new(default_stream_fn()),
        })
    }
}

impl AgentHandle for StaticAgent {
    fn state(&self) -> AgentState {
        self.state.lock().unwrap().clone()
    }

    fn set_state(&self, state: AgentState) {
        *self.state.lock().unwrap() = state;
    }

    fn subscribe(
        &self,
        _listener: Arc<
            dyn Fn(AgentEvent, Option<CancellationToken>) -> BoxFuture<()> + Send + Sync,
        >,
    ) -> Box<dyn Fn() + Send + Sync> {
        Box::new(|| {})
    }

    fn set_before_tool_call(&self, _hook: BeforeToolCallHook) {}
    fn set_after_tool_call(&self, _hook: AfterToolCallHook) {}
    fn set_get_continuation_messages(&self, _hook: GetContinuationMessagesHook) {}
    fn set_before_request(&self, _hook: BeforeRequestHook) {}
    fn set_should_stop_before_turn(&self, _hook: Arc<dyn Fn() -> bool + Send + Sync>) {}
    fn set_should_stop_after_turn(
        &self,
        _hook: Arc<dyn Fn(ShouldStopAfterTurnContext) -> BoxFuture<bool> + Send + Sync>,
    ) {
    }

    fn set_stream_fn(&self, stream_fn: StreamFn) {
        *self.stream_fn.lock().unwrap() = stream_fn;
    }

    fn stream_fn(&self) -> StreamFn {
        self.stream_fn.lock().unwrap().clone()
    }

    fn abort(&self) {}

    fn wait_for_idle(&self) -> BoxFuture<()> {
        Box::pin(async {})
    }

    fn prompt(&self, _messages: Vec<AgentMessage>) -> BoxFuture<Result<(), String>> {
        Box::pin(async { Ok(()) })
    }

    fn continue_(&self) -> BoxFuture<Result<(), AgentContinueError>> {
        Box::pin(async { Ok(()) })
    }

    fn is_streaming(&self) -> bool {
        false
    }

    fn has_queued_messages(&self) -> bool {
        false
    }

    fn clear_all_queues(&self) {}

    fn remove_queued_messages(
        &self,
        _predicate: Arc<dyn Fn(&AgentMessage) -> bool + Send + Sync>,
    ) -> Vec<AgentMessage> {
        Vec::new()
    }

    fn follow_up(&self, _message: AgentMessage) {}
    fn set_follow_up_mode(&self, _mode: String) {}
    fn set_steering_mode(&self, _mode: String) {}

    fn set_convert_to_llm(
        &self,
        _convert: Arc<dyn Fn(Vec<AgentMessage>) -> BoxFuture<Vec<Message>> + Send + Sync>,
    ) {
    }

    fn set_transform_context(
        &self,
        _transform: Arc<
            dyn Fn(Vec<AgentMessage>, Option<CancellationToken>) -> BoxFuture<Vec<AgentMessage>>
                + Send
                + Sync,
        >,
    ) {
    }

    fn set_get_api_key(
        &self,
        _get_api_key: Arc<dyn Fn(String) -> BoxFuture<Option<String>> + Send + Sync>,
    ) {
    }

    fn set_on_payload(&self, _hook: OnPayload) {}
    fn set_on_response(&self, _hook: OnResponse) {}
    fn set_tool_execution(&self, _mode: String) {}
    fn performance_metrics(&self) -> Option<AgentLoopPerformanceMetrics> {
        None
    }
    fn set_performance_metrics(&self, _metrics: Option<AgentLoopPerformanceMetrics>) {}
    fn signal(&self) -> Option<CancellationToken> {
        None
    }
}

/// A full `AgentSession` over a fresh temp root, mirroring the in-crate
/// `test_session_inner` fixture (agent_session.rs) with the compaction-relevant
/// parts kept real: an in-memory settings manager carrying `settings`, a
/// file-backed session manager, a no-op resource loader, and an in-memory
/// model registry holding a runtime API key for `model.provider` so the
/// compaction body's auth pre-check passes. The agent double's live state is
/// set by the caller.
fn session_fixture(
    prefix: &str,
    settings: Value,
    model: &Model,
) -> (
    Arc<AgentSession>,
    Arc<Mutex<SessionManager>>,
    tempfile::TempDir,
) {
    let root = tempfile::Builder::new().prefix(prefix).tempdir().unwrap();
    let dir = root.path().to_string_lossy().to_string();
    let settings = Arc::new(Mutex::new(SettingsManager::in_memory(
        settings.as_object().cloned().unwrap_or_default(),
    )));
    let session_manager = Arc::new(Mutex::new(
        SessionManager::create(&dir, Some(&dir)).unwrap(),
    ));
    let model_registry = Arc::new(Mutex::new(ModelRegistry::in_memory(
        AuthStorage::in_memory(Default::default(), None),
    )));
    model_registry
        .lock()
        .unwrap()
        .set_runtime_api_key(&model.provider, "rot2-integration-key");
    let loader = Arc::new(DefaultResourceLoader::new(DefaultResourceLoaderOptions {
        cwd: dir.clone(),
        agent_dir: dir.clone(),
        settings_manager: Some(Arc::clone(&settings)),
        no_extensions: true,
        no_skills: true,
        no_prompt_templates: true,
        no_themes: true,
        no_context_files: true,
        bundled_skills_dir: Some(None),
        ..Default::default()
    }));
    let session = AgentSession::new(AgentSessionConfig {
        agent: StaticAgent::new(),
        session_manager: Arc::clone(&session_manager),
        settings_manager: Arc::clone(&settings),
        service_tier_preference: None,
        cwd: dir.clone(),
        agent_dir: Some(dir.clone()),
        scoped_models: None,
        resource_loader: loader,
        custom_tools: None,
        model_registry,
        initial_active_tool_names: None,
        allowed_tool_names: None,
        include_goals: Some(false),
        agent_message_controller: None,
        agent_observe_controller: None,
        include_compact_skill: Some(false),
        rlm_heartbeat_controller: None,
        mcp_manager: None,
        base_tools_override: None,
        extension_runner_ref: None,
        session_start_event: None,
        rlm_depth: Some(0),
        rlm_max_depth: Some(2),
        rlm_session_dir: None,
        rlm_parent_node_id: None,
        rlm_parent_agent: None,
        semantic_parent_session_id: None,
        semantic_spawned_by_request_id: None,
        subagent_runtime_host: None,
        autonomous: None,
        prewarm_ipython_kernel: Some(false),
        auto_refine_reviewer: None,
        serialized_refine: None,
        initial_goal: None,
    })
    .expect("session fixture");
    (session, session_manager, root)
}

/// The live-conversation mirror of `seed_window`'s file entries (the custom
/// `agent_message` entry is file-only). The compaction body reads this list
/// for `tokensBefore` and the provider context.
fn window_messages() -> Vec<AgentMessage> {
    vec![
        user_message(
            "TASK GOAL: migrate the billing service to the new ledger. \
CONSTRAINT: never delete the payments ledger ARCH-KEEP-LEDGER. \
CONSTRAINT: the API must stay frozen ARCH-FROZEN-API. \
Also don't forget to update the changelog after the migration ARCH-TODO-CHANGELOG.",
        ),
        assistant_message("Started the migration plan for the billing service."),
        tool_result_message(
            "call-bash-1",
            "bash",
            "running cargo test\ntest result: ok. 4785 passed; 0 failed; 0 ignored\nfinished in 13.2s",
        ),
        tool_result_message(
            "call-agent-1",
            "agent",
            "Subagent api-reviewer finished: no blocking findings, 2 nits. RLM_CHILD_STATUS: complete",
        ),
        user_message("status update: continuing the integration work on the migration plan."),
        user_message("status update: still working through the checklist for the migration."),
    ]
}

/// The `details` object of the newest compaction entry on the branch.
fn latest_compaction_details(manager: &Arc<Mutex<SessionManager>>) -> Value {
    let branch = manager.lock().unwrap().get_branch(None);
    let entry = branch
        .iter()
        .rev()
        .find(|entry| entry.get("type").and_then(Value::as_str) == Some("compaction"))
        .expect("a persisted compaction entry");
    entry.get("details").cloned().unwrap_or(Value::Null)
}

/// W79 gap 1 (hop 6 of PRODUCTION-PATH-TRACE.md): no test pinned
/// `compaction.taskState` through `AgentSession::compaction_settings`
/// (agent_session.rs:19514-19531), the single adapter every production
/// compaction entry point reads. The adapter is private (`fn`, not `pub fn`),
/// so an external test cannot call it directly; the production-observable
/// seam is the compaction it drives. This test builds a real session whose
/// settings manager carries `compaction.taskState = "typed"`, runs the
/// public manual-compaction entry point (`compact_with_options`), and asserts
/// on the persisted compaction entry: `details.typedState` and a passed typed
/// verification exist only when the adapter delivered
/// `task_state = Some("typed")` to the kernel (agent_session.rs:19529).
/// Documented limit: the pin is outcome-level (the persisted entry and the
/// summarizer request), not a direct read of the private accessor; the
/// settings side is asserted first so a failure localizes to one side of the
/// hop.
#[tokio::test]
async fn session_adapter_maps_typed_task_state_into_the_compaction_kernel() {
    // The default-`off` control arm resolves the process environment
    // whenever the setting is absent, so hold the serializer and pin the
    // variable absent for the whole test.
    let _serial = TASK_STATE_ENV_MUTEX.lock().unwrap();
    let _env = TaskStateEnvScope::removed();
    let (model, captured) =
        summarizer_fixture("rot2-typed-session-adapter", FixtureFlavor::Obedient);
    let (session, session_manager, _root) = session_fixture(
        "rot2-typed-session-",
        json!({
            "autoRefine": {"enabled": false},
            "retry": {"enabled": false},
            "telemetry": {"enabled": false},
            "compaction": {"enabled": true, "taskState": "typed", "keepRecentTokens": 20},
        }),
        &model,
    );

    // The settings side of the hop: the session's own manager resolves typed.
    assert_eq!(
        session
            .settings_manager
            .lock()
            .unwrap()
            .get_compaction_task_state(),
        "typed"
    );

    // The live agent holds the conversation the session file records.
    {
        let mut state = AgentState::default();
        state.model = model.clone();
        state.messages = window_messages();
        session.agent.set_state(state);
    }
    seed_window(&mut session_manager.lock().unwrap());

    session
        .compact_with_options(None, false)
        .await
        .expect("the manual compaction over the fixture must succeed");

    // The kernel ran in typed mode: the typed protected block reached the
    // summarizer request through the session adapter.
    let requests = captured.lock().unwrap().join("\n");
    assert!(
        requests.contains("## Typed Protected State"),
        "the typed block must reach the summarizer through the session adapter"
    );

    // The persisted entry carries the typed ledger and its verification.
    let details = latest_compaction_details(&session_manager);
    let typed = details.get("typedState").expect("details.typedState");
    assert!(
        serde_json::to_string(typed)
            .unwrap()
            .contains("payments ledger"),
        "the typed ledger must retain the seeded constraint: {typed}"
    );
    let verification = details
        .get("typedVerification")
        .expect("details.typedVerification");
    assert_eq!(verification.get("passed"), Some(&json!(true)));
    assert!(
        details.get("taskState").is_none(),
        "the v1 field stays unset in typed mode"
    );

    session.dispose_async(Some(false)).await;

    // Control: the same session shape without the setting resolves "off" end
    // to end - compaction still succeeds, but no typed ledger is built or
    // persisted.
    let (plain_model, plain_captured) =
        summarizer_fixture("rot2-typed-session-default", FixtureFlavor::Obedient);
    let (plain_session, plain_manager, _plain_root) = session_fixture(
        "rot2-typed-default-",
        json!({
            "autoRefine": {"enabled": false},
            "retry": {"enabled": false},
            "telemetry": {"enabled": false},
            "compaction": {"enabled": true, "keepRecentTokens": 20},
        }),
        &plain_model,
    );
    assert_eq!(
        plain_session
            .settings_manager
            .lock()
            .unwrap()
            .get_compaction_task_state(),
        "off"
    );
    {
        let mut state = AgentState::default();
        state.model = plain_model.clone();
        state.messages = window_messages();
        plain_session.agent.set_state(state);
    }
    seed_window(&mut plain_manager.lock().unwrap());
    plain_session
        .compact_with_options(None, false)
        .await
        .expect("the default-mode compaction must still succeed");
    assert!(
        !plain_captured
            .lock()
            .unwrap()
            .join("\n")
            .contains("## Typed Protected State"),
        "no typed block may be attached in the default off mode"
    );
    let plain_details = latest_compaction_details(&plain_manager);
    assert!(
        plain_details.get("typedState").is_none(),
        "no typed ledger may persist in the default off mode"
    );
    assert!(
        plain_details.get("taskState").is_none(),
        "no v1 ledger may persist in the default off mode"
    );
    plain_session.dispose_async(Some(false)).await;
}

// ---------------------------------------------------------------------------
// W79: the PRIME_AGENT_COMPACTION_TASK_STATE process-environment route
// ---------------------------------------------------------------------------

/// W79 gap 2 (the environment hop of PRODUCTION-PATH-TRACE.md): the resolver
/// matrix pins `resolve_task_state_mode`'s environment argument, but no test
/// exported the real `PRIME_AGENT_COMPACTION_TASK_STATE` variable and asserted
/// the settings chain resolves it. This test does, over the production read
/// path: `SettingsManager::get_compaction_task_state` passes
/// `env_flag(PRIME_AGENT_COMPACTION_TASK_STATE)` to the resolver
/// (settings_manager.rs), and the packed `get_compaction_settings` record
/// carries the resolved mode into the session adapter (hop 5/6). The library
/// fallback route - `resolve_task_state_mode(None, None)` reads the process
/// variable when no environment argument is given (compaction.rs) - is pinned
/// the same way. The variable is exported only under `TASK_STATE_ENV_MUTEX`
/// and restored by the drop guard even on failure.
#[test]
fn process_env_task_state_resolves_typed_through_the_settings_chain() {
    let _serial = TASK_STATE_ENV_MUTEX.lock().unwrap();
    let manager = SettingsManager::in_memory(serde_json::Map::new());

    {
        let _exported = TaskStateEnvScope::set_typed();
        assert_eq!(
            manager.get_compaction_task_state(),
            TASK_STATE_MODE_TYPED,
            "the settings chain must resolve the exported variable to typed"
        );
        assert_eq!(
            manager.get_compaction_settings().task_state,
            TASK_STATE_MODE_TYPED,
            "the packed record the session adapter copies must carry the resolved mode"
        );
        assert_eq!(
            resolve_task_state_mode(None, None),
            TASK_STATE_MODE_TYPED,
            "the library fallback must read the same process variable"
        );
    }

    // Control: with the variable restored and pinned absent, the same chain
    // resolves the default "off".
    let _absent = TaskStateEnvScope::removed();
    assert_eq!(
        manager.get_compaction_task_state(),
        TASK_STATE_MODE_OFF,
        "the settings chain must fall back to off without the variable"
    );
    assert_eq!(
        manager.get_compaction_settings().task_state,
        TASK_STATE_MODE_OFF
    );
    assert_eq!(resolve_task_state_mode(None, None), TASK_STATE_MODE_OFF);
}
