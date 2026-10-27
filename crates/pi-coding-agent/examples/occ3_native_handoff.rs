//! Opt-in development-native proof harness. Default: local synthetic fixture.
//! No installed profile mutation, server lifecycle commands, or credential argv.
#[path = "../tests/occ3_source_handoff.rs"]
#[allow(dead_code, unused_imports)]
mod offline;
use pi_agent_core::agent::{Agent, AgentOptions, PromptInput};
use pi_agent_core::types::{AgentState, ThinkingLevel};
use pi_ai::source_capture::{sha256, SourceCaptureProfile};
use pi_ai::types::Model;
use pi_coding_agent::core::agent_messages::*;
use pi_coding_agent::core::agent_session::occ3_state::{NativeSourceBudget, Occ3NativeSession};
use pi_coding_agent::core::occ::occ3::connector::Occ3Connector;
use pi_coding_agent::core::occ::occ3::types::Occ3ModelIdentity;
use pi_coding_agent::core::occ3_connectors::sglang::{
    control_header_name_is_allowed, NativeExperimentalPermit, SglangOcc3Config,
    SglangOcc3Connector,
};
use pi_coding_agent::modes::daemon::daemon_mode::occ3_native::{
    IsolatedNativeFamily, IsolatedNativeMember,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LiveDescriptor {
    schema: String,
    root_authorized: bool,
    isolated_profile: String,
    attempt_id: String,
    /// Controller checkpoint identity (runbook B4 / integration plan 3.1):
    /// echoed verbatim into the report and bound into validation_case_passed.
    candidate_id: String,
    process_epoch: String,
    phase: String,
    checkpoint_challenge: String,
    model: Model,
    identity: Occ3ModelIdentity,
    expected_fingerprint: String,
    endpoint: String,
    credential_reference: String,
    /// Existing credential references for gateway control headers
    /// (name -> `!helper` or set env var). Literal resolved tokens are
    /// forbidden; values are resolved in-process and never persisted.
    control_header_references: Option<indexmap::IndexMap<String, String>>,
    runtime_id: String,
    runtime_generation: u64,
    access_domain: String,
    expires_at_epoch: u64,
    layer_index: u32,
    hidden_dim: u32,
    dtype: String,
    layout_digest: String,
    expected_ranks: u32,
    profile_digest: String,
    max_capture_bytes: u64,
    max_response_bytes: usize,
    deadline_seconds: u64,
    budget_profile_verified: bool,
    budget_review_digest: String,
    case: String,
}
fn fail() -> String {
    "Live harness refused or operation uncertain; see private reviewed descriptor and offline gates"
        .into()
}
fn resolve_existing_key(reference: &str) -> Result<String, String> {
    // The existing resolver may invoke a previously configured credential helper.
    // Unset environment references must never fall back to literal key material.
    if !reference.starts_with('!')
        && (reference.is_empty()
            || !reference
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_')
            || std::env::var(reference).is_err())
    {
        return Err(fail());
    }
    pi_coding_agent::core::resolve_config_value::resolve_config_value(reference)
        .filter(|k| !k.is_empty())
        .ok_or_else(fail)
}
/// Existing credential reference grammar only: a configured `!` helper
/// reference, or a valid environment-variable name. Anything else — including
/// a literal resolved token embedded in the descriptor JSON — is refused.
fn control_reference_is_valid(reference: &str) -> bool {
    if let Some(command) = reference.strip_prefix('!') {
        !command.trim().is_empty()
    } else {
        !reference.is_empty()
            && reference
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_')
    }
}

/// Descriptor-side grammar/allowlist check, pure so it stays testable offline.
fn control_header_references_valid(
    references: &Option<indexmap::IndexMap<String, String>>,
) -> bool {
    let Some(references) = references else {
        return true;
    };
    let mut seen: Vec<String> = Vec::new();
    for (name, reference) in references {
        let lower = name.to_ascii_lowercase();
        if !control_header_name_is_allowed(name)
            || seen.contains(&lower)
            || !control_reference_is_valid(reference)
        {
            return false;
        }
        seen.push(lower);
    }
    true
}

/// Descriptor `model.headers` must not smuggle gateway/auth credential names;
/// the in-process resolved control context is the only source for those.
fn model_headers_have_no_reserved_credential_names(model: &Model) -> bool {
    model
        .headers
        .as_ref()
        .is_none_or(|headers| {
            headers.keys().all(|k| {
                !matches!(
                    k.to_ascii_lowercase().as_str(),
                    "authorization" | "host" | "proxy-authorization" | "x-pomerium-authorization"
                )
            })
        })
}

/// Resolve the descriptor's control header references once per attempt,
/// through the existing resolver, before any enroll/inference/HTTP. Unset,
/// empty, unknown or malformed references fail closed here.
fn resolve_control_headers(
    references: &Option<indexmap::IndexMap<String, String>>,
) -> Result<Option<indexmap::IndexMap<String, String>>, String> {
    let Some(references) = references else {
        return Ok(None);
    };
    if references.is_empty() || !control_header_references_valid(&Some(references.clone())) {
        return Err(fail());
    }
    let mut resolved = indexmap::IndexMap::new();
    for (name, reference) in references {
        let value = resolve_existing_key(reference)?;
        resolved.insert(name.clone(), value);
    }
    Ok(Some(resolved))
}

// A successful Agent::prompt future is not a successful attached inference.
// Require the saved phase receipts and semantic/control result before exit 0.
fn validation_case_passed(report: &Value) -> bool {
    let truth = |value: &Value| value.as_bool() == Some(true);
    let nonempty = |value: &Value| value.as_str().is_some_and(|v| !v.is_empty());
    let Some(requests) = report["receiver_request_audit"].as_array() else {
        return false;
    };
    let Some(messages) = report["messages"].as_array() else {
        return false;
    };
    let Some(received) = report["receiver_phase"]["receiver"].as_array() else {
        return false;
    };
    // Controller checkpoint identity (integration plan 3.1): the report must
    // carry the phase and the exact candidate/epoch/challenge echo the
    // DGX-side receipt assembles from the controller checkpoint.
    let checkpoint_bound = matches!(report["phase"].as_str(), Some("initial" | "final"))
        && report["candidate_id"].as_str().is_some_and(|v| !v.is_empty())
        && report["process_epoch"].as_str().is_some_and(|v| !v.is_empty())
        && report["checkpoint_challenge"].as_str().is_some_and(|v| !v.is_empty());
    if !checkpoint_bound
        || !truth(&report["action_completed"])
        || !truth(&report["logical_cleanup_confirmed"])
        || report["native_error"].as_bool() != Some(false)
        || report["source_native_error"].as_bool() != Some(false)
        || requests.is_empty()
        || !requests
            .iter()
            .all(|r| truth(&r["source_facts_absent"]) && r["max_tokens"].as_u64() == Some(131072))
    {
        return false;
    }
    if report["case"] == "no-state" {
        return report["semantic_nonce_match"].as_bool() == Some(false)
            && messages.is_empty()
            && received.is_empty()
            && report["source_phase"]["source"].is_null();
    }
    if report["case"] != "positive"
        || !truth(&report["semantic_nonce_match"])
        || messages.len() != 1
        || received.len() != 1
    {
        return false;
    }
    let source = &report["source_phase"]["source"];
    let receipt = &source["receipt"];
    let receiver = &received[0];
    let id = &messages[0]["message_id"];
    source["state"] == "Captured"
        && truth(&source["tool_claimed"])
        && truth(&source["handoff_admitted"])
        && messages[0]["status"] == "delivered"
        && nonempty(id)
        && source["published_message"][0] == *id
        && receiver["message_id"] == *id
        && receiver["lease_state"] == "Consumed"
        && nonempty(&receiver["request_id"])
        && nonempty(&receipt["native_rid"])
        && receipt["native_rid"] == receipt["response_id"]
        && nonempty(&receipt["capture_commit_id"])
        && receipt["retained_count"].as_u64().is_some_and(|v| v > 0)
        && receipt["expected_ranks"].as_u64().is_some_and(|v| v > 0)
        && receipt["ready_ranks"] == receipt["expected_ranks"]
        && nonempty(&receipt["handle_sha256"])
        && receiver["handle_sha256"] == receipt["handle_sha256"]
        && receiver["runtime_id"] == report["source_phase"]["runtime_id"]
        && receiver["runtime_generation"] == report["source_phase"]["runtime_generation"]
        && receiver["model_fingerprint"] == report["source_phase"]["model_fingerprint"]
        && receiver["model_fingerprint"] == report["model_fingerprint"]
}
async fn live(path: &str) -> Result<Value, String> {
    let bytes = std::fs::read(path).map_err(|_| fail())?;
    if bytes.len() > 65536 {
        return Err(fail());
    }
    let d: LiveDescriptor = serde_json::from_slice(&bytes).map_err(|_| fail())?;
    if d.schema != "optimus.occ3.isolated-native-validation.v1"
        || !d.root_authorized
        || !d.budget_profile_verified
        || d.budget_review_digest.len() != 64
        || d.identity.fingerprint() != d.expected_fingerprint
        || d.model.id != d.identity.model_id
        || d.model.api != pi_ai::types::API_OPENAI_COMPLETIONS
        || d.model.max_tokens != 262144.
        || d.model.default_max_tokens != Some(131072.)
        || d.model.context_window != 1048576.
        || !d.model.reasoning
        || d.deadline_seconds == 0
        || d.deadline_seconds > 3600
        || !matches!(d.case.as_str(), "positive" | "no-state")
        || !matches!(d.phase.as_str(), "initial" | "final")
        || d.candidate_id.is_empty()
        || d.candidate_id.len() > 256
        || d.process_epoch.is_empty()
        || d.process_epoch.len() > 256
        || d.checkpoint_challenge.is_empty()
        || d.checkpoint_challenge.len() > 4096
        || !model_headers_have_no_reserved_credential_names(&d.model)
        || !control_header_references_valid(&d.control_header_references)
    {
        return Err(fail());
    }
    let profile = std::path::PathBuf::from(&d.isolated_profile);
    if !profile.is_absolute() || profile.exists() {
        return Err(fail());
    }
    std::fs::create_dir(&profile).map_err(|_| fail())?;
    // The armed source turn buffers the whole response server-side (the
    // capture span settles at completion), so zero client events for the
    // full server-bounded turn are healthy, not a stall. Raise the stream
    // watchdog floors to this descriptor's own absolute deadline; the
    // server deadline still bounds the turn and the watchdog maxima still
    // clamp. Explicit longer operator settings are preserved.
    let _ = pi_agent_core::stream_watchdog::raise_env_for_armed_turn(d.deadline_seconds);
    // No settings/models/auth file is written. This new folder is evidence only.
    // Control header references resolve first: an unset, empty, unknown or
    // malformed reference fails before any enroll, inference or HTTP request.
    let control_headers = resolve_control_headers(&d.control_header_references)?;
    let key = Arc::new(resolve_existing_key(&d.credential_reference)?);
    // The same resolved control context rides the inference model headers;
    // legitimate non-credential descriptor headers are preserved, reserved
    // credential names were already rejected above and are never overridden.
    let mut model = d.model.clone();
    if let Some(headers) = &control_headers {
        let mut merged = model.headers.clone().unwrap_or_default();
        for (name, value) in headers {
            merged.insert(name.clone(), value.clone());
        }
        model.headers = Some(merged);
    }
    let connector = Arc::new(
        SglangOcc3Connector::new_experimental(
            SglangOcc3Config {
                endpoint: url::Url::parse(&d.endpoint).map_err(|_| fail())?,
                api_key: Some((*key).clone()),
                control_headers: control_headers.clone(),
            },
            NativeExperimentalPermit {
                attempt_id: d.attempt_id.clone(),
                identity: d.identity.clone(),
                target: pi_ai::inference_attachment::InferenceStateTarget {
                    provider: model.provider.clone(),
                    model_id: model.id.clone(),
                    base_url: model.base_url.clone(),
                },
                runtime_id: d.runtime_id,
                runtime_generation: d.runtime_generation,
                access_domain: d.access_domain,
                expires_at_epoch: d.expires_at_epoch,
                source_profile: SourceCaptureProfile {
                    layer_index: d.layer_index,
                    hidden_dim: d.hidden_dim,
                    dtype: d.dtype,
                    layout_digest: d.layout_digest,
                    expected_ranks: d.expected_ranks,
                },
                profile_digest: d.profile_digest,
                max_capture_bytes: d.max_capture_bytes,
            },
        )
        .map_err(|_| fail())?,
    );
    let source_id = format!("source-{}", uuid::Uuid::new_v4());
    let receiver_id = format!("receiver-{}", uuid::Uuid::new_v4());
    let source = Occ3NativeSession::enroll(&source_id, &model, connector.clone()).await?;
    let receiver = Occ3NativeSession::enroll(&receiver_id, &model, connector.clone()).await?;
    let nonce = format!("vault-{}", uuid::Uuid::new_v4());
    let context_audit = Arc::new(Mutex::new(Vec::<Value>::new()));
    let options = |receiver: bool| {
        let key = key.clone();
        AgentOptions {initial_state:Some(AgentState {model:model.clone(),thinking_level:ThinkingLevel::Max,
            system_prompt:if receiver {"Read the private vault code from the attached native state. Reply with that code. If unavailable say UNKNOWN. Do not call tools."}else{"Preserve the private vault code in the native source turn. Call occ3_handoff exactly once to child receiver; include only receiver_role and receiver_name arguments. After the handoff stop."}.into(),..Default::default()}),
            get_api_key:Some(Arc::new(move |_|{let key=key.clone();Box::pin(async move {Some((*key).clone())})})),
            ..Default::default()}
    };
    let source_agent = Agent::new(options(false));
    let mut receiver_options = options(false);
    receiver_options.initial_state.as_mut().unwrap().system_prompt="Read the private vault code from attached state. Reply only with the code, or UNKNOWN if unavailable. Do not call tools.".into();
    let audit = context_audit.clone();
    let forbidden = nonce.clone();
    receiver_options.on_payload = Some(Arc::new(move |payload: Value, _model: &Model| {
        let serialized = payload.to_string();
        let absent = !serialized.contains(&forbidden);
        audit.lock().unwrap().push(json!({"source_facts_absent":absent,"body_sha256_before_attachment":sha256(serialized.as_bytes()),"max_tokens":payload.get("max_tokens").or_else(||payload.get("max_completion_tokens")),"reasoning_effort":payload.get("reasoning_effort")}));
        Box::pin(async move {
            if absent {
                None
            } else {
                Some(json!({"model":"refused","stream":false}))
            }
        })
    }));
    let receiver_agent = Agent::new(receiver_options);
    let family = IsolatedNativeFamily::new(vec![
        IsolatedNativeMember {
            catalog: AgentFamilyCatalogEntry {
                id: source_id.clone(),
                name: Some("source".into()),
                depth: 0.,
                ..Default::default()
            },
            endpoint: AgentSessionMessageEndpoint {
                active_session_id: source_id.clone(),
                session_id: source_id.clone(),
                session_name: Some("source".into()),
                ..Default::default()
            },
            agent: source_agent.clone(),
            native: source.clone(),
        },
        IsolatedNativeMember {
            catalog: AgentFamilyCatalogEntry {
                id: receiver_id.clone(),
                name: Some("receiver".into()),
                depth: 1.,
                parent_session_id: Some(source_id.clone()),
                ..Default::default()
            },
            endpoint: AgentSessionMessageEndpoint {
                active_session_id: receiver_id.clone(),
                session_id: receiver_id.clone(),
                session_name: Some("receiver".into()),
                ..Default::default()
            },
            agent: receiver_agent.clone(),
            native: receiver.clone(),
        },
    ])?;
    family.install()?;
    let action_future = async {
        if d.case == "positive" {
            source.arm_source(NativeSourceBudget {
                retained_count_limit: 128,
                max_capture_bytes: d.max_capture_bytes,
                deadline: Instant::now() + Duration::from_secs(d.deadline_seconds),
                max_response_bytes: d.max_response_bytes,
            })?;
            source_agent.prompt(PromptInput::Text {input:format!("The private vault code is {nonce}. The controller checkpoint challenge for this attempt is {}. Communicate the code only through native state to child receiver, using occ3_handoff with no code in arguments.", d.checkpoint_challenge),images:Vec::new()}).await.map_err(|_|fail())?;
        } else {
            receiver_agent.prompt(PromptInput::Text {input:"Continue the authorized task. No state attachment is available for this negative control.".into(),images:Vec::new()}).await.map_err(|_|fail())?;
        }
        Ok::<(), String>(())
    };
    let action = tokio::time::timeout(Duration::from_secs(d.deadline_seconds), action_future)
        .await
        .unwrap_or_else(|_| Err(fail()));
    if action.is_err() {
        source_agent.abort();
        receiver_agent.abort();
    }
    let receiver_state = receiver_agent.state();
    let answer = receiver_state
        .messages
        .iter()
        .rev()
        .find_map(|m| match m {
            pi_agent_core::types::AgentMessage::Message(pi_ai::types::Message::Assistant(a)) => {
                Some(
                    a.content
                        .iter()
                        .filter_map(|c| match c {
                            pi_ai::types::ContentBlock::Text(t) => Some(t.text.as_str()),
                            _ => None,
                        })
                        .collect::<String>(),
                )
            }
            _ => None,
        })
        .unwrap_or_default();
    let mut report = json!({"schema":"optimus.occ3.native-attempt.v1","case":d.case,"attempt_id":d.attempt_id,
        "candidate_id":d.candidate_id,"process_epoch":d.process_epoch,"phase":d.phase,"checkpoint_challenge":d.checkpoint_challenge,
        "model_fingerprint":d.expected_fingerprint,
        "control_header_names":d.control_header_references.as_ref().map(|refs| refs.keys().collect::<Vec<_>>()),
        "source_fact_sha256":sha256(nonce.as_bytes()),"answer_sha256":sha256(answer.as_bytes()),"semantic_nonce_match":answer.contains(&nonce),
        "messages":family.receipts().iter().map(|r|json!({"message_id":r.id,"status":r.delivery_status})).collect::<Vec<_>>(),
        "source_phase":source.audit(),"receiver_phase":receiver.audit(),
        "receiver_request_audit":context_audit.lock().unwrap().clone(),"native_error":receiver_state.error_message.is_some(),
        "source_native_error":source_agent.state().error_message.is_some(),"action_completed":action.is_ok(),
        "ordinary_kernel_source_route":"unsupported","production_enabled":false});
    let cleanup = family.close().await;
    let shutdown = connector.shutdown().await;
    report["logical_cleanup_confirmed"] = json!(cleanup.is_ok() && shutdown.is_ok());
    report["physical_gpu_free_proven"] = json!(false);
    let passed = validation_case_passed(&report);
    report["case_passed"] = json!(passed);
    std::fs::write(
        profile.join("native-receipt.json"),
        serde_json::to_vec_pretty(&report).map_err(|_| fail())?,
    )
    .map_err(|_| fail())?;
    action?;
    cleanup?;
    shutdown.map_err(|_| fail())?;
    if !passed {
        return Err(fail());
    }
    Ok(report)
}
#[tokio::main]
async fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let result = match args.as_slice() {
        [] => offline::run_offline_fixture(Default::default())
            .await
            .map(|v| v.report),
        [flag] if flag == "--offline" => offline::run_offline_fixture(Default::default())
            .await
            .map(|v| v.report),
        [flag, path] if flag == "--live" => live(path).await,
        _ => Err("Usage: occ3_native_handoff [--offline | --live PRIVATE_DESCRIPTOR_PATH]".into()),
    };
    match result {
        Ok(report) => println!("{report}"),
        Err(_) => {
            eprintln!("Native validation failed or refused. No success/replay is authorized.");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod gate_tests {
    use super::*;
    fn positive() -> Value {
        json!({"case":"positive","action_completed":true,"logical_cleanup_confirmed":true,
            "native_error":false,"source_native_error":false,"semantic_nonce_match":true,
            "model_fingerprint":"fingerprint",
            "candidate_id":"candidate-1","process_epoch":"epoch-1","phase":"initial",
            "checkpoint_challenge":"challenge-1",
            "receiver_request_audit":[{"source_facts_absent":true,"max_tokens":131072}],
            "messages":[{"message_id":"message-1","status":"delivered"}],
            "source_phase":{"runtime_id":"epoch","runtime_generation":1,"model_fingerprint":"fingerprint",
                "source":{"state":"Captured","tool_claimed":true,"handoff_admitted":true,"published_message":["message-1","digest"],
                    "receipt":{"native_rid":"rid","response_id":"rid","capture_commit_id":"commit",
                        "retained_count":8,"expected_ranks":8,"ready_ranks":8,"handle_sha256":"digest"}}},
            "receiver_phase":{"receiver":[{"message_id":"message-1","lease_state":"Consumed",
                "request_id":"request-1","handle_sha256":"digest","runtime_id":"epoch",
                "runtime_generation":1,"model_fingerprint":"fingerprint"}]}})
    }
    #[test]
    fn live_exit_requires_all_bound_phases_semantics_budget_and_cleanup() {
        assert!(validation_case_passed(&positive()));
        for pointer in [
            "/action_completed",
            "/logical_cleanup_confirmed",
            "/semantic_nonce_match",
            "/source_phase/source/tool_claimed",
            "/receiver_request_audit/0/source_facts_absent",
        ] {
            let mut report = positive();
            *report.pointer_mut(pointer).unwrap() = json!(false);
            assert!(!validation_case_passed(&report), "{pointer}");
        }
        for pointer in ["/native_error", "/source_native_error"] {
            let mut report = positive();
            *report.pointer_mut(pointer).unwrap() = json!(true);
            assert!(!validation_case_passed(&report), "{pointer}");
        }
        for pointer in [
            "/receiver_phase/receiver/0/message_id",
            "/receiver_phase/receiver/0/lease_state",
            "/source_phase/source/receipt/response_id",
            "/source_phase/source/receipt/handle_sha256",
            "/receiver_phase/receiver/0/runtime_id",
            "/receiver_phase/receiver/0/model_fingerprint",
            "/phase",
        ] {
            let mut report = positive();
            *report.pointer_mut(pointer).unwrap() = json!("mismatch");
            assert!(!validation_case_passed(&report), "{pointer}");
        }
        // The checkpoint echo must be exactly the controller grammar:
        // phase initial/final only, no empty candidate/epoch/challenge.
        let mut report = positive();
        report["phase"] = json!("other");
        assert!(!validation_case_passed(&report));
        for pointer in ["/candidate_id", "/process_epoch", "/checkpoint_challenge"] {
            let mut report = positive();
            *report.pointer_mut(pointer).unwrap() = json!("");
            assert!(!validation_case_passed(&report), "{pointer}");
        }
        let mut report = positive();
        report["receiver_request_audit"][0]["max_tokens"] = json!(4096);
        assert!(!validation_case_passed(&report));
        report = positive();
        report["messages"] = json!([]);
        assert!(!validation_case_passed(&report));
        report = positive();
        report["receiver_request_audit"] = json!([]);
        assert!(!validation_case_passed(&report));
    }
    #[test]
    fn control_header_references_accept_only_existing_reference_grammar() {
        let var = "OCC3_CONTROL_HEADER_TEST_REFERENCE_VAR";
        std::env::remove_var(var);
        let mut references = indexmap::IndexMap::new();
        references.insert("x-pomerium-authorization".to_string(), var.to_string());
        // Unset env reference refuses before any enroll/inference/HTTP.
        assert!(resolve_control_headers(&Some(references.clone())).is_err());
        // Set env reference resolves to the in-memory value.
        std::env::set_var(var, "synthetic-resolved-gateway-value");
        let resolved = resolve_control_headers(&Some(references.clone()))
            .unwrap()
            .unwrap();
        assert_eq!(
            resolved
                .get("x-pomerium-authorization")
                .map(String::as_str),
            Some("synthetic-resolved-gateway-value")
        );
        // Set-but-empty env reference is a missing credential, never a literal.
        std::env::set_var(var, "");
        assert!(resolve_control_headers(&Some(references.clone())).is_err());
        std::env::remove_var(var);
        // Literal resolved tokens embedded in descriptor JSON are forbidden.
        for literal in ["synthetic-literal-gateway-token", "Bearer x", "with space", ""] {
            let mut literal_refs = indexmap::IndexMap::new();
            literal_refs.insert(
                "x-pomerium-authorization".to_string(),
                literal.to_string(),
            );
            assert!(resolve_control_headers(&Some(literal_refs)).is_err(), "{literal}");
        }
        // Configured helper references match the existing grammar (checked
        // without executing any helper).
        assert!(control_reference_is_valid("!configured-credential-helper"));
        assert!(!control_reference_is_valid("!"));
        assert!(!control_reference_is_valid("!   "));
        // Unapproved names and duplicate case variants refuse.
        let mut bad = indexmap::IndexMap::new();
        bad.insert("authorization".to_string(), var.to_string());
        assert!(!control_header_references_valid(&Some(bad)));
        let mut duplicated = indexmap::IndexMap::new();
        duplicated.insert("X-Pomerium-Authorization".to_string(), var.to_string());
        duplicated.insert("x-pomerium-authorization".to_string(), var.to_string());
        assert!(!control_header_references_valid(&Some(duplicated)));
        // An absent map stays absent.
        assert!(resolve_control_headers(&None).unwrap().is_none());
    }

    #[test]
    fn descriptor_model_headers_cannot_smuggle_reserved_credential_names() {
        let mut model = Model::new(
            "fixture-model",
            "fixture",
            pi_ai::types::API_OPENAI_COMPLETIONS,
            "fixture-provider",
            "https://fixture.invalid/v1",
        );
        assert!(model_headers_have_no_reserved_credential_names(&model));
        model.headers = Some(indexmap::IndexMap::from([(
            "x-legit-custom-header".to_string(),
            "value".to_string(),
        )]));
        assert!(model_headers_have_no_reserved_credential_names(&model));
        for reserved in [
            "authorization",
            "Authorization",
            "host",
            "proxy-authorization",
            "X-Pomerium-Authorization",
            "x-pomerium-authorization",
        ] {
            model.headers = Some(indexmap::IndexMap::from([(
                reserved.to_string(),
                "value".to_string(),
            )]));
            assert!(
                !model_headers_have_no_reserved_credential_names(&model),
                "{reserved}"
            );
        }
    }

    #[test]
    fn live_no_state_control_requires_absent_state_and_absent_nonce() {
        let mut report = positive();
        report["case"] = json!("no-state");
        report["semantic_nonce_match"] = json!(false);
        report["messages"] = json!([]);
        report["receiver_phase"]["receiver"] = json!([]);
        report["source_phase"]["source"] = Value::Null;
        assert!(validation_case_passed(&report));
        report["semantic_nonce_match"] = json!(true);
        assert!(!validation_case_passed(&report));
    }
}
