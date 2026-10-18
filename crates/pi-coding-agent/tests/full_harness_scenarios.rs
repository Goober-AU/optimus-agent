//! Mandated end-to-end scenarios (campaign section 35) driven against the real
//! session, the real tool implementations and the reference faux provider.
//!
//! No network, no real provider, no real user profile. Every tool effect is
//! asserted on disk, and every assertion names the layer it proves.
//!
//! Coverage in this file:
//! - Scenario A: read a synthetic file, run Bash, edit the file, validate.
//! - Scenario B: IPython keeps state across cells, survives a failing cell.
//! - Scenario G: long session with tool calls, session close and reopen.
//! - Scenario H: a nonzero Bash exit is reported as failure, never as success.
//!
//! Subagent, memory and provider-interruption scenarios live in their own files
//! because they need the daemon, the memory store and stream fixture owners.

#![allow(clippy::all)]

use pi_ai::providers::faux::*;
use pi_ai::types::{ContentBlock, Context, Message, Model, ToolCall};
use pi_coding_agent::core::agent_session::{AgentSession, PromptOptions};
use pi_coding_agent::core::agent_session_services::*;
use pi_coding_agent::core::auth_storage::AuthStorage;
use pi_coding_agent::core::model_registry::ModelRegistry;
use pi_coding_agent::core::resource_loader::DefaultResourceLoaderOptions;
use pi_coding_agent::core::session_manager::SessionManager;
use pi_coding_agent::core::settings_manager::SettingsManager;
use serde_json::json;
use std::sync::{Arc, Mutex};
use std::time::Duration;

static ENV: Mutex<()> = Mutex::new(());

struct Fixture {
    root: tempfile::TempDir,
    old_profile: Option<std::ffi::OsString>,
    provider: FauxProviderRegistration,
    captured: Arc<Mutex<Vec<Context>>>,
}

impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(root.path().join("workspace")).unwrap();
        std::fs::create_dir_all(root.path().join("profile")).unwrap();
        let old_profile = std::env::var_os("PRIME_AGENT_CODING_AGENT_DIR");
        std::env::set_var("PRIME_AGENT_CODING_AGENT_DIR", root.path().join("profile"));
        let provider = register_faux_provider(Some(RegisterFauxProviderOptions {
            provider: Some(format!("scenario-{}", uuid::Uuid::new_v4())),
            tokens_per_second: Some(0.0),
            ..Default::default()
        }));
        Self { root, old_profile, provider, captured: Default::default() }
    }

    fn workspace(&self) -> std::path::PathBuf {
        self.root.path().join("workspace")
    }

    async fn session(&self, saved: Option<&str>) -> Arc<AgentSession> {
        let cwd = self.workspace().to_string_lossy().into_owned();
        let profile = self.root.path().join("profile").to_string_lossy().into_owned();
        let settings = Arc::new(Mutex::new(SettingsManager::in_memory(json!({
            "autoRefine":{"enabled":false}, "retry":{"enabled":false}, "compaction":{"enabled":false},
            "telemetryEnabled":false, "agentTracesEnabled":false
        }).as_object().unwrap().clone())));
        let mut model: Model = self.provider.get_model();
        model.input.push(pi_ai::types::InputModality::Image);
        let auth = Arc::new(tokio::sync::Mutex::new(AuthStorage::in_memory(Default::default(), None)));
        auth.lock().await.set_runtime_api_key(&model.provider, "synthetic-scenario-key");
        let registry = Arc::new(Mutex::new(ModelRegistry::in_memory(AuthStorage::in_memory(Default::default(), None))));
        registry.lock().unwrap().set_runtime_api_key(&model.provider, "synthetic-scenario-key");
        let services = create_agent_session_services(CreateAgentSessionServicesOptions {
            cwd: cwd.clone(),
            agent_dir: Some(profile.clone()),
            auth_storage: Some(auth),
            settings_manager: Some(settings.clone()),
            model_registry: Some(registry),
            extension_flag_values: None,
            no_builtin_herdr_reporter: Some(true),
            telemetry_disabled: Some(true),
            resource_loader_options: Some(DefaultResourceLoaderOptions {
                cwd: cwd.clone(),
                agent_dir: profile.clone(),
                settings_manager: Some(settings),
                no_extensions: true,
                no_skills: true,
                no_prompt_templates: true,
                no_themes: true,
                bundled_skills_dir: Some(None),
                ..Default::default()
            }),
        })
        .await
        .unwrap();
        let manager = match saved {
            Some(path) => SessionManager::open(path, Some(&profile), None).unwrap(),
            None => SessionManager::create(&cwd, Some(&profile)).unwrap(),
        };
        create_agent_session_from_services(CreateAgentSessionFromServicesOptions {
            services: Arc::new(services),
            session_manager: Arc::new(Mutex::new(manager)),
            session_start_event: None,
            creation: AgentSessionCreationOptions {
                model: Some(model),
                prewarm_ipython_kernel: Some(false),
                telemetry_disabled: Some(true),
                ..Default::default()
            },
        })
        .await
        .unwrap()
        .session
    }

    /// Queue one scripted model reply. `tool` makes it a tool-use turn.
    fn reply(&self, tool: Option<(&str, serde_json::Value)>, text: &str) {
        let captured = self.captured.clone();
        let content = tool.map(|(name, args)| {
            ContentBlock::ToolCall(ToolCall::new(
                format!("call-{}", uuid::Uuid::new_v4()),
                name,
                args.as_object().unwrap().clone(),
            ))
        });
        let text = text.to_string();
        self.provider.append_responses(vec![FauxResponseStep::Factory(Arc::new(
            move |context, _, _, _| {
                captured.lock().unwrap().push(context.clone());
                let message = faux_assistant_message(
                    content
                        .clone()
                        .map(FauxAssistantContent::Block)
                        .unwrap_or_else(|| text.clone().into()),
                    Some(FauxAssistantMessageOptions {
                        stop_reason: Some(if content.is_some() { "toolUse" } else { "stop" }.into()),
                        ..Default::default()
                    }),
                );
                Box::pin(async move { message })
            },
        ))]);
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.provider.unregister();
        match self.old_profile.take() {
            Some(value) => std::env::set_var("PRIME_AGENT_CODING_AGENT_DIR", value),
            None => std::env::remove_var("PRIME_AGENT_CODING_AGENT_DIR"),
        }
    }
}

async fn idle(session: &Arc<AgentSession>) {
    tokio::time::timeout(Duration::from_secs(60), session.wait_for_headless_idle())
        .await
        .expect("session became idle")
        .unwrap();
}

async fn turn(session: &Arc<AgentSession>, text: &str) {
    tokio::time::timeout(Duration::from_secs(60), session.prompt(text, None::<PromptOptions>))
        .await
        .expect("prompt returned")
        .unwrap();
    idle(session).await;
}

/// Every (tool_call_id, tool_name, text, is_error) in transcript order.
fn tool_results(session: &Arc<AgentSession>) -> Vec<(String, String, String, bool)> {
    session
        .messages()
        .into_iter()
        .filter_map(|message| match message {
            pi_agent_core::types::AgentMessage::Message(Message::ToolResult(result)) => Some((
                result.tool_call_id.clone(),
                result.tool_name.clone(),
                result
                    .content
                    .iter()
                    .map(|block| match block {
                        pi_ai::types::ImageOrTextContent::Text(text) => text.text.clone(),
                        pi_ai::types::ImageOrTextContent::Image(_) => "[image]".to_string(),
                    })
                    .collect::<Vec<_>>()
                    .join(""),
                result.is_error,
            )),
            _ => None,
        })
        .collect()
}

fn tool_call_ids(session: &Arc<AgentSession>) -> Vec<String> {
    session
        .messages()
        .into_iter()
        .filter_map(|message| match message {
            pi_agent_core::types::AgentMessage::Message(Message::Assistant(assistant)) => Some(
                assistant
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        ContentBlock::ToolCall(call) => Some(call.id.clone()),
                        _ => None,
                    })
                    .collect::<Vec<_>>(),
            ),
            _ => None,
        })
        .flatten()
        .collect()
}

fn assistant_texts(session: &Arc<AgentSession>) -> Vec<String> {
    session
        .messages()
        .into_iter()
        .filter_map(|message| match message {
            pi_agent_core::types::AgentMessage::Message(Message::Assistant(assistant)) => Some(
                assistant
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        ContentBlock::Text(text) => Some(text.text.clone()),
                        _ => None,
                    })
                    .collect::<Vec<_>>()
                    .join(""),
            ),
            _ => None,
        })
        .collect()
}

/// SCENARIO A: bash reads a file, edit changes it, bash validates the change.
#[tokio::test]
async fn scenario_a_agent_tool_use_reads_runs_and_edits() {
    let _env = ENV.lock().unwrap_or_else(|p| p.into_inner());
    let f = Fixture::new();
    std::fs::write(f.workspace().join("note.txt"), "alpha\nbravo\n").unwrap();
    let session = f.session(None).await;
    turn(&session, "/mode direct").await;
    assert_eq!(
        session.get_active_tool_names(),
        ["bash", "edit", "subagent", "attach_image"],
        "scenario A needs the real direct tools, not a placeholder"
    );

    f.reply(Some(("bash", json!({"command": "cat note.txt"}))), "");
    turn(&session, "Read the note").await;
    f.reply(
        Some(("edit", json!({"path": "note.txt", "edits": [{"oldText": "bravo", "newText": "charlie"}]}))),
        "",
    );
    turn(&session, "Change bravo to charlie").await;
    f.reply(Some(("bash", json!({"command": "cat note.txt"}))), "");
    turn(&session, "Validate the change").await;
    f.reply(None, "SCENARIO_A_DONE");
    turn(&session, "Summarise").await;

    let results = tool_results(&session);
    assert_eq!(results.len(), 3, "three tool calls must produce three results: {results:?}");
    assert!(results.iter().all(|(_, _, _, is_error)| !is_error), "no tool may report an error: {results:?}");
    assert!(results[0].2.contains("bravo"), "the first read must see the original line: {:?}", results[0].2);
    assert!(results[2].2.contains("charlie"), "the validation read must see the edit: {:?}", results[2].2);
    assert_eq!(
        std::fs::read_to_string(f.workspace().join("note.txt")).unwrap(),
        "alpha\ncharlie\n",
        "the on-disk effect is the assertion, not the tool text"
    );
    // Node identity: every tool call id has exactly one result, in order.
    let calls = tool_call_ids(&session);
    let result_ids: Vec<String> = results.iter().map(|(id, _, _, _)| id.clone()).collect();
    assert_eq!(calls, result_ids, "tool call and result ids must pair exactly");
    assert!(assistant_texts(&session).iter().any(|text| text.contains("SCENARIO_A_DONE")));
    session.dispose_async(Some(false)).await;
}

/// SCENARIO B: IPython state survives cells, survives an error, and reports it.
#[tokio::test]
async fn scenario_b_python_state_survives_cells_and_errors() {
    let _env = ENV.lock().unwrap_or_else(|p| p.into_inner());
    if std::env::var("PRIME_AGENT_KERNEL_PYTHON").is_err() {
        eprintln!("BLOCKED: PRIME_AGENT_KERNEL_PYTHON is unset; scenario B needs a real kernel");
        return;
    }
    let f = Fixture::new();
    let session = f.session(None).await;
    assert_eq!(session.get_active_tool_names(), ["ipython"], "default mode is the Python REPL");

    f.reply(Some(("ipython", json!({"code": "counter = 41"}))), "");
    turn(&session, "Set the counter").await;
    f.reply(Some(("ipython", json!({"code": "counter + 1"}))), "");
    turn(&session, "Use it").await;
    f.reply(Some(("ipython", json!({"code": "raise RuntimeError('SCENARIO_B_BOOM')"}))), "");
    turn(&session, "Fail on purpose").await;
    f.reply(Some(("ipython", json!({"code": "counter * 2"}))), "");
    turn(&session, "Keep going after the error").await;
    f.reply(None, "SCENARIO_B_DONE");
    turn(&session, "Summarise").await;

    let results = tool_results(&session);
    assert_eq!(results.len(), 4, "four cells must produce four results: {results:?}");
    assert!(!results[0].3, "assignment must not error");
    assert!(results[1].2.contains("42"), "a later cell must see the earlier binding: {:?}", results[1].2);
    assert!(results[2].3, "a raising cell must be reported as an error: {:?}", results[2]);
    assert!(results[2].2.contains("SCENARIO_B_BOOM"), "the error text must reach the tool result");
    assert!(!results[3].3, "an error cell must not poison the kernel");
    assert!(results[3].2.contains("82"), "state after the error must still be the real state: {:?}", results[3].2);
    session.dispose_async(Some(false)).await;
}

/// SCENARIO H: a nonzero Bash exit is failure, and the transcript keeps it.
#[tokio::test]
async fn scenario_h_tool_failure_is_never_reported_as_success() {
    let _env = ENV.lock().unwrap_or_else(|p| p.into_inner());
    let f = Fixture::new();
    let session = f.session(None).await;
    turn(&session, "/mode direct").await;
    f.reply(Some(("bash", json!({"command": "printf 'boom\\n' >&2; exit 7"}))), "");
    turn(&session, "Fail").await;
    f.reply(None, "SCENARIO_H_RECOGNIZED_FAILURE");
    turn(&session, "Report").await;

    let results = tool_results(&session);
    assert_eq!(results.len(), 1);
    assert!(results[0].3, "a nonzero exit must be an error result: {:?}", results[0]);
    assert!(results[0].2.contains("boom"), "stderr must survive: {:?}", results[0].2);
    assert!(
        !results[0].2.contains("exit code 0"),
        "the failure must not be dressed up as success: {:?}",
        results[0].2
    );
    assert!(assistant_texts(&session).iter().any(|text| text.contains("SCENARIO_H_RECOGNIZED_FAILURE")));
    session.dispose_async(Some(false)).await;
}

/// SCENARIO G: a long session survives close and reopen with its tool history.
#[tokio::test]
async fn scenario_g_long_session_survives_reopen() {
    let _env = ENV.lock().unwrap_or_else(|p| p.into_inner());
    let f = Fixture::new();
    let session = f.session(None).await;
    turn(&session, "/mode direct").await;
    for index in 0..12 {
        f.reply(Some(("bash", json!({"command": format!("printf 'G_{index:02}'")}))), "");
        turn(&session, &format!("Step {index}")).await;
    }
    f.reply(None, "SCENARIO_G_DONE");
    turn(&session, "Finish").await;
    let before = tool_results(&session);
    assert_eq!(before.len(), 12);
    let file = session.session_file().expect("a session file exists");
    session.dispose_async(Some(false)).await;

    let reopened = f.session(Some(&file)).await;
    let after = tool_results(&reopened);
    assert_eq!(after, before, "every tool result must survive the reopen byte for byte");
    assert!(assistant_texts(&reopened).iter().any(|text| text.contains("SCENARIO_G_DONE")));
    reopened.dispose_async(Some(false)).await;
}
