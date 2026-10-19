//! ROT2 wave-2 defect tests: D18/D19 verification and regression coverage.
//!
//! Owner: `core/agent_session.rs` (the three deferred audit defects).
//! - D18 (LOW): a failed auto-compaction is invisible to the model; the
//!   conversation continues with no statement that compaction was attempted
//!   and failed, so the model cannot adapt (e.g. keep its output short).
//! - D19 (MED): live-list producers (`unpersisted_outcomes`,
//!   `pending_next_turn_messages`) had no drain or cap.
//!
//! All scenarios run offline on the faux provider through a real
//! `AgentSession` (same contract as `compaction_recovery.rs`): summary wire
//! calls are answered by the fixture factory, agent turns by the faux
//! provider, and every writable path stays under an isolated temp root.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use pi_ai::providers::faux::{
    faux_assistant_message, register_faux_provider, FauxAssistantContent,
    FauxAssistantMessageOptions, FauxModelDefinition, FauxResponseFactory, FauxResponseStep,
    RegisterFauxProviderOptions,
};
use pi_ai::types::{Context, Message, Model};
use pi_coding_agent::core::agent_session::AgentSession;
use pi_coding_agent::core::agent_session_services::{
    create_agent_session_from_services, create_agent_session_services,
    AgentSessionCreationOptions, CreateAgentSessionFromServicesOptions,
    CreateAgentSessionServicesOptions,
};
use pi_coding_agent::core::auth_storage::{AuthStorage, AuthStorageData};
use pi_coding_agent::core::model_registry::ModelRegistry;
use pi_coding_agent::core::resource_loader::DefaultResourceLoaderOptions;
use pi_coding_agent::core::settings_manager::SettingsManager;
use serde_json::Value;

const API_KEY: &str = "rot2-wave2-defect-fixture-key";

/// A summary body with none of the required sections: the summarizer fixture
/// for D18, where every compaction attempt must FAIL at `validate_summary`.
const INVALID_SUMMARY: &str = "this summary is deliberately unusable";

/// The body every summary wire call returns for the valid summarizer: every
/// `validate_summary` section, with the turn-prefix (split turn) shape using
/// its own three sections exactly like `compaction_recovery.rs`.
fn summary_text(request_text: &str) -> String {
    if request_text.contains("This is the PREFIX of a turn") {
        return format!(
            "## Original Request\nTASK GOAL: ship the migration report before Friday.\n## Early Progress\n- Section 1 audited.\n## Context for Suffix\n- reports/migration-audit.md stays available."
        );
    }
    format!(
        "## Goal\nTASK GOAL: ship the migration report before Friday.\n## Constraints & Preferences\nCONSTRAINT: never drop the payments ledger.\n## Progress\n### Done\n- [x] Section 1 audited.\n\n### In Progress\n- [ ] Section 2.\n\n### Blocked\n- None.\n\n## Key Decisions\n- Incremental rollout.\n## Next Steps\n1. Publish the report.\n## Critical Context\nEvidence reference: reports/migration-audit.md."
    )
}

fn unique_tag() -> String {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};
    static TAG_SEQ: AtomicUsize = AtomicUsize::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    format!("{}-{}", nanos, TAG_SEQ.fetch_add(1, Ordering::Relaxed))
}

/// One recorded wire request: the joined user-visible text plus whether the
/// call was a compaction summary call (the serialized `<conversation>` form).
#[derive(Debug, Clone)]
struct RecordedRequest {
    text: String,
    summary_call: bool,
}

struct Wave2Fixture {
    session: Arc<AgentSession>,
    _dir: tempfile::TempDir,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
}

/// A real `AgentSession` on the faux provider with a small context window and
/// compaction enabled. `summarizer` decides what every summary wire call
/// returns: `Summarizer::Valid` makes auto-compaction succeed,
/// `Summarizer::Invalid` makes every attempt fail at `validate_summary`.
enum Summarizer {
    Valid,
    Invalid,
}

async fn wave2_fixture(label: &str, summarizer: Summarizer) -> Wave2Fixture {
    let root = tempfile::Builder::new()
        .prefix(&format!("rot2-wave2-{label}-"))
        .tempdir()
        .unwrap();
    let cwd = root.path().join("workspace");
    let agent_dir = root.path().join("agent");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::create_dir_all(&agent_dir).unwrap();
    let cwd_string = cwd.to_string_lossy().to_string();
    let agent_dir_string = agent_dir.to_string_lossy().to_string();

    let provider = register_faux_provider(Some(RegisterFauxProviderOptions {
        provider: Some(format!("rot2-wave2-{label}-{}", unique_tag())),
        models: Some(vec![FauxModelDefinition {
            id: "rot2-wave2-model".into(),
            name: Some("ROT2 wave-2 fixture model".into()),
            reasoning: Some(false),
            input: Some(vec!["text".into()]),
            cost: None,
            context_window: Some(8_192.0),
            max_tokens: Some(8_192.0),
        }]),
        tokens_per_second: Some(0.0),
        ..Default::default()
    }));
    let model = provider.get_model();

    let requests: Arc<Mutex<Vec<RecordedRequest>>> = Arc::new(Mutex::new(Vec::new()));
    let seen = Arc::clone(&requests);
    let summarizer = std::sync::atomic::AtomicU8::new(match summarizer {
        Summarizer::Valid => 0,
        Summarizer::Invalid => 1,
    });
    let factory: FauxResponseFactory = Arc::new(
        move |context: &Context, _options, _state, _model: &Model| {
            let text = context
                .messages
                .iter()
                .map(|message| match message {
                    Message::User(user) => user.content.text(),
                    _ => String::new(),
                })
                .collect::<Vec<_>>()
                .join("\n");
            let summary_call = text.contains("<conversation>");
            seen.lock().unwrap().push(RecordedRequest {
                text: text.clone(),
                summary_call,
            });
            let text = if summary_call {
                match summarizer.load(std::sync::atomic::Ordering::Relaxed) {
                    0 => summary_text(&text),
                    _ => INVALID_SUMMARY.to_string(),
                }
            } else {
                "ack: work started.".to_string()
            };
            Box::pin(async move {
                faux_assistant_message(
                    FauxAssistantContent::Text(text),
                    Some(FauxAssistantMessageOptions {
                        stop_reason: Some("stop".into()),
                        ..Default::default()
                    }),
                )
            })
        },
    );
    let steps: Vec<FauxResponseStep> = (0..64)
        .map(|_| FauxResponseStep::Factory(factory.clone()))
        .collect();
    provider.set_responses(steps);

    let settings = Arc::new(Mutex::new(SettingsManager::in_memory(
        serde_json::json!({
            "autoRefine": {"enabled": false},
            "retry": {"enabled": false},
            "compaction": {
                "enabled": true,
                "reserveTokens": 512.0,
                "keepRecentTokens": 5.0
            },
            "telemetryEnabled": false,
            "agentTracesEnabled": false,
        })
        .as_object()
        .unwrap()
        .clone(),
    )));

    let session_manager =
        pi_coding_agent::core::session_manager::SessionManager::create(
            &cwd_string,
            Some(&agent_dir_string),
        )
        .unwrap();
    let session_manager = Arc::new(Mutex::new(session_manager));

    let auth_storage = Arc::new(tokio::sync::Mutex::new(AuthStorage::in_memory(
        AuthStorageData::new(),
        None,
    )));
    let model_registry = Arc::new(Mutex::new(ModelRegistry::in_memory(
        AuthStorage::in_memory(AuthStorageData::new(), None),
    )));
    auth_storage
        .lock()
        .await
        .set_runtime_api_key(&model.provider, API_KEY);
    model_registry
        .lock()
        .unwrap()
        .set_runtime_api_key(&model.provider, API_KEY);

    let services = create_agent_session_services(CreateAgentSessionServicesOptions {
        cwd: cwd_string.clone(),
        agent_dir: Some(agent_dir_string.clone()),
        auth_storage: Some(Arc::clone(&auth_storage)),
        settings_manager: Some(Arc::clone(&settings)),
        model_registry: Some(Arc::clone(&model_registry)),
        extension_flag_values: None,
        no_builtin_herdr_reporter: Some(true),
        telemetry_disabled: Some(true),
        resource_loader_options: Some(DefaultResourceLoaderOptions {
            cwd: cwd_string.clone(),
            agent_dir: agent_dir_string.clone(),
            no_extensions: true,
            no_skills: true,
            no_prompt_templates: true,
            no_themes: true,
            no_context_files: true,
            bundled_skills_dir: Some(None),
            ..Default::default()
        }),
    })
    .await
    .expect("services");

    let created = create_agent_session_from_services(CreateAgentSessionFromServicesOptions {
        services: Arc::new(services),
        session_manager: Arc::clone(&session_manager),
        session_start_event: None,
        creation: AgentSessionCreationOptions {
            model: Some(model),
            rlm_depth: Some(0),
            no_tools: Some("all".to_string()),
            prewarm_ipython_kernel: Some(false),
            telemetry_disabled: Some(true),
            ..Default::default()
        },
    })
    .await
    .expect("agent session");
    Wave2Fixture {
        session: created.session,
        _dir: root,
        requests,
    }
}

async fn wait_until(
    description: &str,
    timeout: Duration,
    mut predicate: impl FnMut() -> bool,
) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if predicate() {
            return true;
        }
        if Instant::now() >= deadline {
            eprintln!("timed out waiting for {description}");
            return false;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
}

/// The durable compaction-outcome entries on the session branch, as
/// `(outcome, reason)` pairs from the entry details.
fn compaction_outcomes(session: &AgentSession) -> Vec<(String, String)> {
    session
        .session_manager
        .lock()
        .unwrap()
        .get_branch(None)
        .iter()
        .filter(|entry| {
            entry.get("type").and_then(Value::as_str) == Some("custom_message")
                && entry.get("details").and_then(|d| d.get("outcome")).is_some()
        })
        .filter_map(|entry| {
            let details = entry.get("details")?;
            Some((
                details
                    .get("outcome")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                details
                    .get("reason")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
            ))
        })
        .collect()
}

/// The turn (non-summary) wire requests recorded so far.
fn turn_requests(fixture: &Wave2Fixture) -> Vec<String> {
    fixture
        .requests
        .lock()
        .unwrap()
        .iter()
        .filter(|request| !request.summary_call)
        .map(|request| request.text.clone())
        .collect()
}


/// The oversized prompt that pushes the faux usage estimate past the
/// compaction trigger boundary.
fn oversized_prompt() -> String {
    format!(
        "TASK GOAL: ship the migration report before Friday. CONSTRAINT: never drop the payments ledger. Evidence reference: reports/migration-audit.md. {}",
        "planning notes: the ledger export needs a dry run first. ".repeat(700)
    )
}

// ---------------------------------------------------------------------------
// D18: compaction failure visibility to the model
// ---------------------------------------------------------------------------

/// D18 contract, failure half: when an automatic threshold compaction fails,
/// the model's NEXT turn request must carry a bounded notice that compaction
/// failed and the conversation was not compacted.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn d18_failed_auto_compaction_surfaces_a_bounded_notice_to_the_model() {
    let fixture = wave2_fixture("d18-fail", Summarizer::Invalid).await;
    let session = &fixture.session;

    session
        .prompt_and_wait(&oversized_prompt(), None)
        .await
        .expect("prompt accepted");

    // The threshold compaction runs at turn end; every summary attempt fails
    // `validate_summary`, so a durable failure outcome must land.
    let failed = wait_until("failed compaction outcome", Duration::from_secs(90), || {
        compaction_outcomes(session)
            .iter()
            .any(|(outcome, _)| outcome == "failed")
    })
    .await;
    assert!(
        failed,
        "the auto-compaction never produced a durable failure outcome"
    );

    // The model's NEXT turn request must carry the bounded failure notice.
    session
        .prompt_and_wait("Continue with a one-word status.", None)
        .await
        .expect("second prompt accepted");
    let turns = turn_requests(&fixture);
    let last = turns
        .last()
        .expect("a second turn request")
        .clone();
    assert!(
        last.contains("Auto-compaction failed"),
        "the model's next turn must state that auto-compaction failed"
    );
    assert!(
        last.contains("not compacted"),
        "the notice must state the conversation was not compacted"
    );
    assert_eq!(
        last.matches("System notice: compaction failed").count(),
        1,
        "exactly one bounded failure notice may ride the conversation"
    );
}

/// D18 contract, success half: a successful auto-compaction must NOT surface
/// a failure notice (the summary itself is the model-visible handoff).
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn d18_successful_compaction_does_not_surface_a_failure_notice() {
    let fixture = wave2_fixture("d18-ok", Summarizer::Valid).await;
    let session = &fixture.session;

    session
        .prompt_and_wait(&oversized_prompt(), None)
        .await
        .expect("prompt accepted");
    let compacted = wait_until("compaction entry", Duration::from_secs(90), || {
        session
            .session_manager
            .lock()
            .unwrap()
            .get_branch(None)
            .iter()
            .any(|entry| entry.get("type").and_then(Value::as_str) == Some("compaction"))
    })
    .await;
    assert!(compacted, "auto-compaction never produced a durable entry");

    session
        .prompt_and_wait("Continue with a one-word status.", None)
        .await
        .expect("second prompt accepted");
    let turns = turn_requests(&fixture);
    let last = turns.last().expect("a second turn request").clone();
    assert!(
        !last.contains("Auto-compaction failed"),
        "a successful compaction must not surface a failure notice"
    );
}
