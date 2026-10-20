//! W20 D11: superseded harness digests must not accumulate in the provider
//! context.
//!
//! `append_harness_digest_if_stale` compares only the LAST digest custom in
//! the live context, so k harness changes used to leave k harness-digest
//! user turns in the provider context (k-1 stale). The fix prunes the
//! superseded digest customs from the live `state().messages` view when the
//! newest digest is committed; the durable session transcript keeps the full
//! digest history. Before the fix only compaction stripped them.
//!
//! No network, no real provider: agent turns (and compaction summary calls)
//! go to the faux provider, and all writable state stays under an isolated
//! temp root.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use pi_agent_core::types::{AgentMessage, AgentState};
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
use pi_coding_agent::core::messages::{
    create_harness_digest_message, HARNESS_DIGEST_CUSTOM_TYPE, HARNESS_DIGEST_PREFIX,
    HARNESS_DIGEST_SUFFIX,
};
use pi_coding_agent::core::model_registry::ModelRegistry;
use pi_coding_agent::core::resource_loader::DefaultResourceLoaderOptions;
use pi_coding_agent::core::session_manager::{CustomMessageEntryContent, SessionEntry, SessionManager};
use pi_coding_agent::core::settings_manager::SettingsManager;
use serde_json::{json, Value};

const API_KEY: &str = "rot2-harness-digest-fixture-key";

/// Summary text with every required section so `validate_summary` accepts
/// the faux summarizer handoff (same contract as the compaction-recovery
/// suite).
fn summary_text(request_text: &str) -> String {
    if request_text.contains("This is the PREFIX of a turn") {
        return "## Original Request\nContinue the migration work.\n## Early Progress\n- Section 1 audited.\n## Context for Suffix\n- reports/migration-audit.md stays available."
            .to_string();
    }
    "## Goal\nContinue the migration work.\n## Constraints & Preferences\nNone.\n## Progress\n### Done\n- [x] Section 1 audited.\n\n### In Progress\n- [ ] Section 2.\n\n### Blocked\n- None.\n\n## Key Decisions\n- Incremental rollout.\n## Next Steps\n1. Publish the report.\n## Critical Context\nreports/migration-audit.md stays available."
        .to_string()
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

// ---------------------------------------------------------------------------
// End-to-end fixture on the faux provider
// ---------------------------------------------------------------------------

struct E2eFixture {
    session: Arc<AgentSession>,
    _dir: tempfile::TempDir,
}

/// One real `AgentSession` on the faux provider with isolated roots. The
/// factory answers agent turns with a plain ack and compaction summary calls
/// (distinguished by the `<conversation>` wrapper) with a valid summary.
async fn fixture(label: &str, compaction_enabled: bool, context_window: f64) -> E2eFixture {
    let root = tempfile::Builder::new()
        .prefix(&format!("rot2-harness-digest-{label}-"))
        .tempdir()
        .unwrap();
    let cwd = root.path().join("workspace");
    let agent_dir = root.path().join("agent");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::create_dir_all(&agent_dir).unwrap();
    let cwd_string = cwd.to_string_lossy().to_string();
    let agent_dir_string = agent_dir.to_string_lossy().to_string();

    let provider = register_faux_provider(Some(RegisterFauxProviderOptions {
        provider: Some(format!("rot2-digest-{label}-{}", unique_tag())),
        models: Some(vec![FauxModelDefinition {
            id: "rot2-digest-model".into(),
            name: Some("Rot2 digest fixture model".into()),
            reasoning: Some(false),
            input: Some(vec!["text".into()]),
            cost: None,
            context_window: Some(context_window),
            max_tokens: Some(context_window),
        }]),
        tokens_per_second: Some(0.0),
        ..Default::default()
    }));
    let model = provider.get_model();

    let factory: FauxResponseFactory =
        Arc::new(move |context: &Context, _options, _state, _model: &Model| {
            let request_text = context
                .messages
                .iter()
                .map(|message| match message {
                    Message::User(user) => user.content.text(),
                    _ => String::new(),
                })
                .collect::<Vec<_>>()
                .join("\n");
            let text = if request_text.contains("<conversation>") {
                summary_text(&request_text)
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
        });
    let steps: Vec<FauxResponseStep> = (0..64)
        .map(|_| FauxResponseStep::Factory(factory.clone()))
        .collect();
    provider.set_responses(steps);

    let settings = Arc::new(Mutex::new(SettingsManager::in_memory(
        serde_json::json!({
            "autoRefine": {"enabled": false},
            "retry": {"enabled": false},
            "compaction": {
                "enabled": compaction_enabled,
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

    let session_manager = Arc::new(Mutex::new(
        SessionManager::create(&cwd_string, Some(&agent_dir_string)).unwrap(),
    ));

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
    E2eFixture {
        session: created.session,
        _dir: root,
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

// ---------------------------------------------------------------------------
// Digest fixtures and probes
// ---------------------------------------------------------------------------

/// The digest values of every harness-digest custom in a message list, in
/// order.
fn digest_values(messages: &[AgentMessage]) -> Vec<String> {
    messages
        .iter()
        .filter_map(|message| match message {
            AgentMessage::Custom(pi_agent_core::types::CustomAgentMessage::Custom {
                custom_type,
                details,
                ..
            }) if custom_type == HARNESS_DIGEST_CUSTOM_TYPE => details
                .as_ref()
                .and_then(|details| details.get("digest"))
                .and_then(Value::as_str)
                .map(str::to_string),
            _ => None,
        })
        .collect()
}

/// One harness-digest custom exactly as `create_harness_digest_message`
/// shapes it, with a chosen digest.
fn crafted_digest(value: &str, timestamp: i64) -> AgentMessage {
    AgentMessage::Custom(create_harness_digest_message(value.to_string(), timestamp))
}

/// The digest values of every durable harness-digest entry in a session
/// branch, in order.
fn journal_digest_values(branch: &[SessionEntry]) -> Vec<String> {
    branch
        .iter()
        .filter(|entry| {
            entry.get("customType").and_then(Value::as_str) == Some(HARNESS_DIGEST_CUSTOM_TYPE)
        })
        .filter_map(|entry| {
            entry
                .get("details")
                .and_then(|details| details.get("digest"))
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect()
}

/// Seed one stale harness change into the live provider context and the
/// durable journal, exactly as a delivered digest would look.
fn seed_stale_digest(session: &Arc<AgentSession>, digest: &str, timestamp: i64) {
    session
        .session_manager
        .lock()
        .unwrap()
        .append_custom_message_entry(
            HARNESS_DIGEST_CUSTOM_TYPE,
            &CustomMessageEntryContent::Text(format!(
                "{HARNESS_DIGEST_PREFIX}{digest}{HARNESS_DIGEST_SUFFIX}"
            )),
            false,
            Some(json!({ "digest": digest })),
        )
        .expect("seed durable digest entry");
    let message = crafted_digest(digest, timestamp);
    session.agent.update_state(Box::new(move |state: &mut AgentState| {
        state.messages.push(message);
    }));
}

// ---------------------------------------------------------------------------
// D11: k harness changes leave exactly one digest in the provider context
// ---------------------------------------------------------------------------

/// Session creation armed the pending digest (empty live context). Seeding
/// the digest history that k-1 prior harness changes would have produced
/// and delivering the k-th change must leave exactly ONE digest custom in
/// the live provider context, while the durable transcript keeps them all.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn k_harness_changes_leave_exactly_one_digest_in_the_provider_context() {
    let fixture = fixture("single-digest", false, 1_000_000.0).await;
    let session = &fixture.session;

    // Four prior harness changes: four stale digest customs in the live
    // provider context, each mirrored into the durable journal.
    let stale = ["digest-one", "digest-two", "digest-three", "digest-four"];
    for (index, digest) in stale.iter().enumerate() {
        seed_stale_digest(session, digest, index as i64 + 1);
    }
    assert_eq!(
        digest_values(&session.agent.messages()),
        stale,
        "fixture: four stale digest customs in the live context"
    );

    // The k-th harness change: the pending digest rides the turn commit.
    session
        .prompt_and_wait("Continue the migration work.", None)
        .await
        .expect("prompt accepted");

    // Exactly ONE digest custom remains in the provider context, and it is
    // the newest one (not any of the stale digests).
    let live = digest_values(&session.agent.messages());
    assert_eq!(
        live.len(),
        1,
        "k harness changes must leave exactly one digest custom in the provider context, got {}",
        live.len()
    );
    assert!(
        !stale.contains(&live[0].as_str()),
        "the surviving digest must be the newest one, not a stale value"
    );

    // The durable transcript is not rewritten: every stale digest entry
    // survives in the session file.
    let durable = {
        let mut manager = session.session_manager.lock().unwrap();
        manager.flush_now().unwrap();
        manager.get_branch(None)
    };
    let durable_values = journal_digest_values(&durable);
    for digest in stale {
        assert!(
            durable_values.contains(&digest.to_string()),
            "the session file must keep the full digest history; {digest} missing from {durable_values:?}"
        );
    }

    // SessionManager read-back after reopening the file keeps the history.
    let session_file = session
        .session_manager
        .lock()
        .unwrap()
        .get_session_file()
        .expect("durable session file");
    let reopened = SessionManager::open(&session_file, None, None).unwrap();
    let reopened_values = journal_digest_values(&reopened.get_branch(None));
    for digest in stale {
        assert!(
            reopened_values.contains(&digest.to_string()),
            "the reopened session file must keep the full digest history; {digest} missing from {reopened_values:?}"
        );
    }
}

// ---------------------------------------------------------------------------
// D11 + compaction: the compacted context stays digest-clean
// ---------------------------------------------------------------------------

/// After the prune the live context holds one digest; a threshold
/// compaction must still strip digest customs from the rebuilt provider
/// context (the existing strip path), and the durable history survives.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn compaction_keeps_the_pruned_provider_context_digest_clean() {
    let fixture = fixture("compaction", true, 8_192.0).await;
    let session = &fixture.session;

    let stale = ["digest-one", "digest-two", "digest-three", "digest-four"];
    for (index, digest) in stale.iter().enumerate() {
        seed_stale_digest(session, digest, index as i64 + 1);
    }

    // One oversized turn: the commit prunes the stale digests, delivers the
    // newest digest, and the faux usage estimate crosses the compaction
    // boundary so the threshold compaction runs at turn end.
    let prompt = format!(
        "TASK GOAL: ship the migration report before Friday. {}",
        "planning notes: the ledger export needs a dry run first. ".repeat(700)
    );
    session
        .prompt_and_wait(&prompt, None)
        .await
        .expect("prompt accepted");

    let compacted = wait_until(
        "compaction entry",
        Duration::from_secs(60),
        || {
            session
                .session_manager
                .lock()
                .unwrap()
                .get_branch(None)
                .iter()
                .any(|entry| entry.get("type").and_then(Value::as_str) == Some("compaction"))
        },
    )
    .await;
    assert!(compacted, "auto-compaction never produced a durable entry");

    // The rebuilt provider context is digest-clean.
    let live = digest_values(&session.agent.messages());
    assert_eq!(
        live,
        Vec::<String>::new(),
        "the compacted provider context must stay digest-clean"
    );
    match session.agent.messages().first() {
        Some(AgentMessage::Custom(
            pi_agent_core::types::CustomAgentMessage::CompactionSummary { .. },
        )) => {}
        other => panic!(
            "live context must be summary-first after auto-compaction, got {:?}",
            other.map(|message| message.role())
        ),
    }

    // The durable transcript keeps the full digest history.
    let durable = {
        let mut manager = session.session_manager.lock().unwrap();
        manager.flush_now().unwrap();
        manager.get_branch(None)
    };
    let durable_values = journal_digest_values(&durable);
    for digest in stale {
        assert!(
            durable_values.contains(&digest.to_string()),
            "the session file must keep the full digest history; {digest} missing from {durable_values:?}"
        );
    }
}
