//! Turn-flow compaction correctness (campaign defects D1 and D2).
//!
//! D1: the turn-end threshold hook (`should_stop_for_threshold_compaction` ->
//! `threshold_compaction_needed`) and the executor (`check_compaction`) must
//! agree on when the automatic threshold compaction will run. When the runtime
//! `auto_compaction_enabled` toggle is off, a turn past the trigger used to be
//! stopped with nothing compacting, so the user's in-flight work stalled
//! silently.
//!
//! D2: when the threshold path stops an interactive turn, the queued user work
//! must complete after the compaction: the turn continues (a mid-task tool turn
//! is resumed) and a prompt queued while the turn streamed is delivered, with
//! no duplicate turns.
//!
//! The fixtures are offline: `register_api_provider_simple` / the faux provider
//! answer every request, the summarizer returns a handoff that passes
//! `validate_summary`, and every session runs on isolated temp roots.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use pi_agent_core::types::AgentMessage;
use pi_ai::api_registry::{register_api_provider_simple, ApiProviderSimple};
use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, ContentBlock, Message, Model, TextContent, ToolCall,
    Usage,
};
use pi_ai::utils::event_stream::AssistantMessageEventStream;
use pi_coding_agent::core::agent_session::PromptOptions;
use pi_coding_agent::core::agent_session_services::{
    create_agent_session_from_services, create_agent_session_services,
    AgentSessionCreationOptions, CreateAgentSessionFromServicesOptions,
    CreateAgentSessionServicesOptions,
};
use pi_coding_agent::core::auth_storage::{AuthStorage, AuthStorageData};
use pi_coding_agent::core::model_registry::ModelRegistry;
use pi_coding_agent::core::resource_loader::DefaultResourceLoaderOptions;
use pi_coding_agent::core::session_manager::{NewSessionOptions, SessionManager};
use pi_coding_agent::core::settings_manager::SettingsManager;
use serde_json::Value;

const API_KEY: &str = "turn-flow-fixture-key";
const FINAL_ANSWER: &str = "FINAL ANSWER: migration complete.";

/// A handoff that passes `validate_summary` for both summary formats: the
/// conversation format and the turn-prefix format a split turn uses.
fn valid_summary(request_text: &str) -> String {
    if request_text.contains("This is the PREFIX of a turn") {
        return "## Original Request\nShip the migration report.\n## Early Progress\n- Section 1 audited.\n## Context for Suffix\n- reports/migration-audit.md stays available."
            .to_string();
    }
    "## Goal\nFinish the migration.\n## Constraints & Preferences\nKeep user data.\n## Progress\n### Done\n- [x] Audit read.\n\n### In Progress\n- [ ] Section 3.\n\n### Blocked\n- None.\n\n## Key Decisions\n- Incremental rollout.\n## Next Steps\n1. Publish the report.\n## Critical Context\nSee reports/audit-2026.md#findings."
        .to_string()
}

/// A prompt large enough that the char/4 context estimate crosses the fixture
/// threshold (window 8_192, reserve 512).
fn big_prompt() -> String {
    format!(
        "TASK GOAL: ship the migration report. {}",
        "planning notes: the ledger export needs a dry run first. ".repeat(700)
    )
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

fn branch_has_compaction(session: &pi_coding_agent::core::agent_session::AgentSession) -> bool {
    session
        .session_manager
        .lock()
        .unwrap()
        .get_branch(None)
        .iter()
        .any(|entry| entry.get("type").and_then(Value::as_str) == Some("compaction"))
}

fn branch_compaction_count(session: &pi_coding_agent::core::agent_session::AgentSession) -> usize {
    session
        .session_manager
        .lock()
        .unwrap()
        .get_branch(None)
        .iter()
        .filter(|entry| entry.get("type").and_then(Value::as_str) == Some("compaction"))
        .count()
}

fn final_answer_count(messages: &[AgentMessage]) -> usize {
    messages
        .iter()
        .filter(|message| match message {
            AgentMessage::Message(Message::Assistant(assistant)) => assistant.content.iter().any(
                |block| {
                    block
                        .as_text()
                        .map(|text| text.text.contains(FINAL_ANSWER))
                        .unwrap_or(false)
                },
            ),
            _ => false,
        })
        .count()
}

/// A session over the `register_api_provider_simple` fixture whose model answer
/// schedule is fixed: turn 1 asks for one `bash echo` tool call (when
/// `use_tool_call`), every later turn answers with the final text. The
/// summarizer answers every `<conversation>` request with a valid handoff.
struct TurnFlowFixture {
    session: Arc<pi_coding_agent::core::agent_session::AgentSession>,
    _root: tempfile::TempDir,
    turn_requests: Arc<Mutex<Vec<String>>>,
    summary_requests: Arc<Mutex<Vec<String>>>,
}

async fn turn_flow_fixture(label: &str, auto_compaction: bool, use_tool_call: bool) -> TurnFlowFixture {
    let root = tempfile::Builder::new()
        .prefix(&format!("turn-flow-{label}-"))
        .tempdir()
        .unwrap();
    let cwd = root.path().join("workspace");
    let agent_dir = root.path().join("agent");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::create_dir_all(&agent_dir).unwrap();
    let cwd_string = cwd.to_string_lossy().to_string();
    let agent_dir_string = agent_dir.to_string_lossy().to_string();

    let turn_requests: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let summary_requests: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let seen_turns = Arc::clone(&turn_requests);
    let seen_summaries = Arc::clone(&summary_requests);
    let use_tool_call_flag = Arc::new(AtomicBool::new(use_tool_call));

    let api = format!("turn-flow-{label}-{}", std::process::id());
    let mut model = Model::new(&api, &api, &api, "fixture", "https://fixture.invalid");
    model.context_window = 8_192.0;
    model.max_tokens = 8_192.0;

    register_api_provider_simple(
        ApiProviderSimple {
            api: api.clone(),
            stream: Arc::new(|_, _, _| panic!("unexpected base stream")),
            stream_simple: Arc::new(move |_, context, _| {
                let request_text = context
                    .messages
                    .iter()
                    .map(|message| match message {
                        Message::User(user) => user.content.text(),
                        _ => String::new(),
                    })
                    .collect::<Vec<_>>()
                    .join("\n");
                let message = if request_text.contains("<conversation>") {
                    seen_summaries.lock().unwrap().push(request_text.clone());
                    AssistantMessage {
                        content: vec![ContentBlock::Text(TextContent::new(valid_summary(
                            &request_text,
                        )))],
                        stop_reason: "stop".into(),
                        usage: Usage::default(),
                        ..Default::default()
                    }
                } else {
                    let turn_index = {
                        let mut seen = seen_turns.lock().unwrap();
                        seen.push(request_text);
                        seen.len()
                    };
                    if turn_index == 1 && use_tool_call_flag.load(Ordering::SeqCst) {
                        let mut arguments = serde_json::Map::new();
                        arguments.insert(
                            "command".to_string(),
                            Value::String("echo turn-flow-probe".to_string()),
                        );
                        AssistantMessage {
                            content: vec![
                                ContentBlock::Text(TextContent::new(
                                    "I will run the probe tool first.",
                                )),
                                ContentBlock::ToolCall(ToolCall::new(
                                    "probe-call-1",
                                    "bash",
                                    arguments,
                                )),
                            ],
                            stop_reason: "tool_use".into(),
                            usage: Usage::default(),
                            ..Default::default()
                        }
                    } else {
                        AssistantMessage {
                            content: vec![ContentBlock::Text(TextContent::new(FINAL_ANSWER))],
                            stop_reason: "stop".into(),
                            usage: Usage::default(),
                            ..Default::default()
                        }
                    }
                };
                let stream = AssistantMessageEventStream::new();
                stream.push(AssistantMessageEvent::Done {
                    reason: message.stop_reason.clone(),
                    message,
                });
                stream
            }),
            compact: None,
            supports_compaction: None,
        },
        None,
    );

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

    let mut manager = SessionManager::create(&cwd_string, Some(&agent_dir_string)).unwrap();
    manager
        .new_session(Some(&NewSessionOptions {
            rlm_depth: Some(0),
            ..Default::default()
        }))
        .unwrap();
    let session_manager = Arc::new(Mutex::new(manager));

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
            prewarm_ipython_kernel: Some(false),
            telemetry_disabled: Some(true),
            ..Default::default()
        },
    })
    .await
    .expect("agent session");

    if !auto_compaction {
        created.session.set_auto_compaction_enabled(false);
    }

    TurnFlowFixture {
        session: created.session,
        _root: root,
        turn_requests,
        summary_requests,
    }
}

/// D1: with the runtime auto-compaction toggle off and the context over the
/// threshold, the turn must not be stopped-without-compaction. The tool turn
/// completes (the model answers the tool result) and no compaction runs,
/// because the executor gate declines it.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn d1_toggle_off_over_threshold_does_not_stop_the_turn() {
    let fixture = turn_flow_fixture("d1-off", false, true).await;
    let session = &fixture.session;
    assert!(!session.auto_compaction_enabled());

    session
        .prompt_and_wait(&big_prompt(), None)
        .await
        .expect("prompt accepted");

    let completed = wait_until(
        "the mid-task turn completes after its tool call",
        Duration::from_secs(60),
        || final_answer_count(&session.messages()) > 0,
    )
    .await;
    assert!(
        completed,
        "the turn was stopped past the threshold while the runtime auto-compaction toggle is off, and nothing compacted or resumed it; the tool result is never answered (defect D1)"
    );

    // The model answered the tool result: two provider requests in total.
    assert_eq!(
        fixture.turn_requests.lock().unwrap().len(),
        2,
        "the stopped turn must have continued to a second provider request"
    );
    // The toggle keeps auto compaction disabled, so nothing compacted.
    assert!(
        !branch_has_compaction(session),
        "a runtime-disabled auto compaction must not run"
    );
    assert_eq!(
        fixture.summary_requests.lock().unwrap().len(),
        0,
        "no summarizer call may happen while the toggle is off"
    );
}

/// D2: an interactive turn that crosses the threshold mid-task is stopped for
/// the compaction, the compaction runs, and the turn continues to completion
/// with no duplicate turns.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn d2_interactive_turn_crossing_threshold_compacts_and_continues() {
    let fixture = turn_flow_fixture("d2-midtask", true, true).await;
    let session = &fixture.session;
    assert!(session.auto_compaction_enabled());

    session
        .prompt_and_wait(&big_prompt(), None)
        .await
        .expect("prompt accepted");

    let completed = wait_until(
        "the turn continues after the threshold compaction",
        Duration::from_secs(60),
        || {
            branch_has_compaction(session) && final_answer_count(&session.messages()) > 0
        },
    )
    .await;
    assert!(
        completed,
        "the threshold compaction did not run or the interactive turn did not continue after it (defect D2)"
    );

    // No duplicate turns: exactly two provider requests (the tool turn and the
    // continuation) and exactly one final answer.
    assert_eq!(
        fixture.turn_requests.lock().unwrap().len(),
        2,
        "the continuation must run exactly one further provider request"
    );
    assert_eq!(
        final_answer_count(&session.messages()),
        1,
        "the final answer must be produced exactly once"
    );
    assert_eq!(
        branch_compaction_count(session),
        1,
        "exactly one threshold compaction may run"
    );
    assert!(
        !fixture.summary_requests.lock().unwrap().is_empty(),
        "the compaction must have made its summarizer call"
    );
}

/// D2, queued-work form: a second prompt submitted while the first turn
/// streams is queued user work. The first turn crosses the threshold, the
/// compaction runs, and the queued prompt is delivered and answered afterwards.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn d2_queued_user_prompt_completes_after_threshold_compaction() {
    use pi_ai::providers::faux::{
        faux_assistant_message, register_faux_provider, FauxAssistantContent,
        FauxAssistantMessageOptions, FauxModelDefinition, FauxResponseFactory, FauxResponseStep,
        RegisterFauxProviderOptions,
    };
    use pi_ai::types::Context;

    let root = tempfile::Builder::new()
        .prefix("turn-flow-d2-queued-")
        .tempdir()
        .unwrap();
    let cwd = root.path().join("workspace");
    let agent_dir = root.path().join("agent");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::create_dir_all(&agent_dir).unwrap();
    let cwd_string = cwd.to_string_lossy().to_string();
    let agent_dir_string = agent_dir.to_string_lossy().to_string();

    let provider = register_faux_provider(Some(RegisterFauxProviderOptions {
        provider: Some(format!("turn-flow-d2-queued-{}", std::process::id())),
        models: Some(vec![FauxModelDefinition {
            id: "queued-model".into(),
            name: Some("Queued fixture model".into()),
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

    // The first model call parks until the test has queued the second prompt,
    // so the queued work demonstrably exists while the first turn streams.
    let first_call_entered = Arc::new(tokio::sync::Notify::new());
    let release_first_call = Arc::new(tokio::sync::Notify::new());
    let turn_calls = Arc::new(AtomicUsize::new(0));
    let summary_requests: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));

    let entered = Arc::clone(&first_call_entered);
    let release = Arc::clone(&release_first_call);
    let seen_turns = Arc::clone(&turn_calls);
    let seen_summaries = Arc::clone(&summary_requests);
    let factory: FauxResponseFactory = Arc::new(
        move |context: &Context, _options, _state, _model| {
            let request_text = context
                .messages
                .iter()
                .map(|message| match message {
                    Message::User(user) => user.content.text(),
                    _ => String::new(),
                })
                .collect::<Vec<_>>()
                .join("\n");
            let is_summary = request_text.contains("<conversation>");
            let turn_index = if is_summary {
                0
            } else {
                seen_turns.fetch_add(1, Ordering::SeqCst) + 1
            };
            if is_summary {
                seen_summaries.lock().unwrap().push(request_text.clone());
            }
            let text = if is_summary {
                valid_summary(&request_text)
            } else {
                "answer to the current prompt.".to_string()
            };
            let entered = Arc::clone(&entered);
            let release = Arc::clone(&release);
            Box::pin(async move {
                if turn_index == 1 {
                    entered.notify_one();
                    release.notified().await;
                }
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

    let mut manager = SessionManager::create(&cwd_string, Some(&agent_dir_string)).unwrap();
    manager
        .new_session(Some(&NewSessionOptions {
            rlm_depth: Some(0),
            ..Default::default()
        }))
        .unwrap();
    let session_manager = Arc::new(Mutex::new(manager));

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
    let session = created.session;

    let first = tokio::spawn({
        let session = session.clone();
        let text = big_prompt();
        async move { session.prompt_and_wait(&text, None).await }
    });
    tokio::time::timeout(Duration::from_secs(30), first_call_entered.notified())
        .await
        .expect("the first model call must start");

    // The first turn is streaming: the second prompt becomes queued user work.
    session
        .prompt(
            "SECOND TASK: summarize the state.",
            Some(PromptOptions {
                streaming_behavior: Some("steer".to_string()),
                ..Default::default()
            }),
        )
        .await
        .expect("second prompt queued");
    release_first_call.notify_one();

    let first_result = tokio::time::timeout(Duration::from_secs(30), first)
        .await
        .expect("the first prompt settles");
    assert!(
        first_result.is_ok_and(|result| result.is_ok()),
        "the first prompt must not reject"
    );

    let completed = wait_until(
        "the queued prompt is delivered and answered after the compaction",
        Duration::from_secs(60),
        || {
            let messages = session.messages();
            let second_prompt = messages.iter().any(|message| {
                matches!(message, AgentMessage::Message(Message::User(user)) if user
                    .content
                    .text()
                    .contains("SECOND TASK"))
            });
            let second_answer = messages.iter().any(|message| {
                matches!(message, AgentMessage::Message(Message::Assistant(assistant)) if assistant
                    .content
                    .iter()
                    .any(|block| {
                        block
                            .as_text()
                            .map(|text| text.text.contains("answer to the current prompt"))
                            .unwrap_or(false)
                    }))
            });
            second_prompt && second_answer
        },
    )
    .await;
    assert!(
        completed,
        "the queued user prompt never completed after the threshold compaction (defect D2)"
    );
    assert!(
        branch_has_compaction(&session),
        "the threshold compaction must run for the crossing turn"
    );
    assert_eq!(
        branch_compaction_count(&session),
        1,
        "exactly one threshold compaction may run"
    );
    assert_eq!(
        turn_calls.load(Ordering::SeqCst),
        2,
        "each prompt is answered by exactly one model turn"
    );
}
