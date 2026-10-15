//! One fixed synthetic direct-provider call; no sessions, extensions, or retrieval.
//! --config ABS --scratch-dir NEW_ABS --output NEW_ABS --max-tokens 1..1024
//! [--thinking off|unspecified] [--timeout-seconds 1..90] --allow-network

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use pi_ai::types::{
    AssistantMessage, AssistantMessageEvent, Context, Message, Model, ProviderUsageObservation,
    SimpleStreamOptions, StreamOptions, UserContent, UserMessage, API_OPENAI_COMPLETIONS,
};
use pi_ai::utils::event_stream::StreamTaskReceipt;
use pi_coding_agent::core::auth_storage::{
    AuthStorage, AuthStorageOptions, FileAuthStorageBackend,
};
use pi_coding_agent::core::model_registry::{
    strip_json_comments, ModelRegistry, ResolvedRequestAuth,
};
use serde::Serialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use tokio_util::sync::CancellationToken;

const PROVIDER: &str = "dgx-glm53";
const MODEL: &str = "glm-5.3";
const PROMPT: &str = "Reply with exactly READY.";
const SCHEMA: &str = "memory-provider-probe/v1";
type Checked<T> = Result<T, &'static str>;

#[derive(Debug)]
struct Args {
    config: PathBuf,
    scratch_dir: PathBuf,
    output: PathBuf,
    max_tokens: u16,
    thinking_off: bool,
    timeout_seconds: u16,
}

impl Args {
    fn parse(raw: &[String]) -> Checked<Self> {
        // This gate precedes every filesystem, profile, auth, and runtime operation.
        if !raw.iter().any(|arg| arg == "--allow-network") {
            return Err("network_not_allowed");
        }
        let mut values = std::collections::HashMap::new();
        let mut index = 0;
        let mut allowed = false;
        while index < raw.len() {
            let flag = raw[index].as_str();
            index += 1;
            if flag == "--allow-network" && !allowed {
                allowed = true;
                continue;
            }
            if !matches!(
                flag,
                "--config"
                    | "--scratch-dir"
                    | "--output"
                    | "--max-tokens"
                    | "--thinking"
                    | "--timeout-seconds"
            ) {
                return Err("invalid_arguments");
            }
            let value = raw.get(index).ok_or("invalid_arguments")?;
            index += 1;
            if values.insert(flag, value.as_str()).is_some() {
                return Err("invalid_arguments");
            }
        }
        if !allowed {
            return Err("network_not_allowed");
        }
        let path = |flag: &str| -> Checked<PathBuf> {
            let path = PathBuf::from(*values.get(flag).ok_or("missing_arguments")?);
            if !path.is_absolute() {
                return Err("absolute_paths_required");
            }
            Ok(path)
        };
        let max_tokens = values
            .get("--max-tokens")
            .ok_or("missing_arguments")?
            .parse::<u16>()
            .map_err(|_| "invalid_token_cap")?;
        if !(1..=1024).contains(&max_tokens) {
            return Err("invalid_token_cap");
        }
        let thinking_off = match values.get("--thinking").copied().unwrap_or("off") {
            "off" => true,
            "unspecified" => false,
            _ => return Err("invalid_thinking_control"),
        };
        let timeout_seconds = values
            .get("--timeout-seconds")
            .copied()
            .unwrap_or("90")
            .parse::<u16>()
            .map_err(|_| "invalid_timeout")?;
        if !(1..=90).contains(&timeout_seconds) {
            return Err("invalid_timeout");
        }
        Ok(Self {
            config: path("--config")?,
            scratch_dir: path("--scratch-dir")?,
            output: path("--output")?,
            max_tokens,
            thinking_off,
            timeout_seconds,
        })
    }
}

fn create_new(path: &Path) -> Checked<File> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path).map_err(|error| {
        if error.kind() == std::io::ErrorKind::AlreadyExists {
            "path_exists"
        } else {
            "file_create_failed"
        }
    })
}

fn reserve_output(args: &Args) -> Checked<File> {
    match std::fs::symlink_metadata(&args.output) {
        Ok(_) => return Err("output_exists"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err("output_unavailable"),
    }
    let mut builder = std::fs::DirBuilder::new();
    builder.recursive(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder
        .create(&args.scratch_dir)
        .map_err(|_| "scratch_not_new")?;
    create_new(&args.output)
}

struct TransientConfig(PathBuf);
impl Drop for TransientConfig {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn load_registry(
    args: &Args,
    config_sha256: &mut Option<String>,
) -> Checked<(ModelRegistry, Model)> {
    let bytes = std::fs::read(&args.config).map_err(|_| "config_read_failed")?;
    *config_sha256 = Some(format!("{:x}", Sha256::digest(&bytes)));
    let text = std::str::from_utf8(&bytes).map_err(|_| "config_invalid")?;
    let parsed: Value =
        serde_json::from_str(&strip_json_comments(text)).map_err(|_| "config_invalid")?;
    let provider = parsed
        .get("providers")
        .and_then(|v| v.get(PROVIDER))
        .ok_or("provider_not_explicit")?;
    if provider
        .get("apiKey")
        .and_then(Value::as_str)
        .is_none_or(|key| key.trim().is_empty())
    {
        return Err("auth_not_explicit");
    }
    let models = provider
        .get("models")
        .and_then(Value::as_array)
        .ok_or("model_not_explicit")?;
    if models
        .iter()
        .filter(|model| model.get("id").and_then(Value::as_str) == Some(MODEL))
        .count()
        != 1
    {
        return Err("model_not_explicit");
    }
    // Native path-based loading, but never beside installed profile/cache files.
    let config_path = args.scratch_dir.join("models.json");
    let mut selected = Map::new();
    selected.insert(PROVIDER.to_owned(), provider.clone());
    let selected =
        serde_json::to_vec(&json!({"providers": selected})).map_err(|_| "config_invalid")?;
    let mut config_file = create_new(&config_path)?;
    let transient = TransientConfig(config_path);
    config_file
        .write_all(&selected)
        .map_err(|_| "scratch_write_failed")?;
    drop(config_file);
    let auth_path = args.scratch_dir.join("auth.json");
    create_new(&auth_path)?
        .write_all(b"{}")
        .map_err(|_| "scratch_write_failed")?;
    // from_storage avoids create()'s default-profile-path comparison entirely.
    let mut auth = AuthStorage::from_storage(
        Box::new(FileAuthStorageBackend::new(Some(
            auth_path.to_string_lossy().into_owned(),
        ))),
        Some(AuthStorageOptions {
            prime_cli_config_path: None,
            use_prime_cli_config: false,
        }),
    );
    if !auth.drain_errors().is_empty() {
        return Err("scratch_auth_failed");
    }
    let registry = ModelRegistry::create(auth, Some(transient.0.to_string_lossy().into_owned()));
    // create() eagerly stores model definitions and request auth/header references.
    std::fs::remove_file(&transient.0).map_err(|_| "config_cleanup_failed")?;
    if registry.get_error().is_some() {
        return Err("config_invalid");
    }
    let model = registry.find(PROVIDER, MODEL).ok_or("model_not_explicit")?;
    if model.api != API_OPENAI_COMPLETIONS {
        return Err("unsupported_api");
    }
    validate_endpoint(&model.base_url)?;
    validate_thinking(&model, args.thinking_off)?;
    Ok((registry, model))
}

fn validate_endpoint(endpoint: &str) -> Checked<()> {
    let parsed = url::Url::parse(endpoint).map_err(|_| "endpoint_invalid")?;
    let authority = endpoint
        .split_once("://")
        .map(|(_, rest)| rest.split(['/', '?', '#']).next().unwrap_or(""));
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || authority.is_some_and(|authority| authority.contains('@'))
    {
        return Err("endpoint_invalid");
    }
    Ok(())
}

fn validate_thinking(model: &Model, thinking_off: bool) -> Checked<()> {
    if thinking_off
        && (!model.reasoning
            || !pi_ai::models::get_supported_thinking_levels(model)
                .iter()
                .any(|level| level == "off")
            || pi_ai::models::clamp_thinking_level(model, "off") != "off")
    {
        return Err("thinking_off_unsupported");
    }
    Ok(())
}

#[derive(Default, Serialize)]
struct UsageSnapshot {
    input: Option<f64>,
    output: Option<f64>,
    #[serde(rename = "reasoningTokens")]
    reasoning: Option<f64>,
    #[serde(rename = "totalTokens")]
    total: Option<f64>,
}
impl UsageSnapshot {
    fn observe(&mut self, observation: ProviderUsageObservation) {
        fn update(target: &mut Option<f64>, value: Option<Option<f64>>) {
            if let Some(value) = value {
                *target = value.filter(|n| n.is_finite() && *n >= 0.0);
            }
        }
        update(&mut self.input, observation.input_tokens);
        update(&mut self.output, observation.output_tokens);
        update(&mut self.reasoning, observation.reasoning_tokens);
        update(&mut self.total, observation.total_tokens);
    }
}

fn payload_summary(payload: &Value) -> Value {
    let mut safe = Map::new();
    for key in ["max_tokens", "max_completion_tokens"] {
        if let Some(value) = payload
            .get(key)
            .and_then(Value::as_f64)
            .filter(|n| n.is_finite() && (1.0..=1024.0).contains(n) && n.fract() == 0.0)
        {
            safe.insert(key.into(), json!(value as u16));
        }
    }
    for (key, allowed) in [(
        "reasoning_effort",
        &[
            "none", "off", "minimal", "low", "medium", "high", "xhigh", "max",
        ][..],
    )] {
        if let Some(value) = payload
            .get(key)
            .and_then(Value::as_str)
            .filter(|value| allowed.contains(value))
        {
            safe.insert(key.into(), json!(value));
        }
    }
    if let Some(value) = payload.get("enable_thinking").and_then(Value::as_bool) {
        safe.insert("enable_thinking".into(), json!(value));
    }
    if let Some(value) = payload
        .pointer("/thinking/type")
        .and_then(Value::as_str)
        .filter(|value| ["enabled", "disabled", "adaptive"].contains(value))
    {
        safe.insert("thinking".into(), json!({"type": value}));
    }
    let mut kwargs = Map::new();
    for key in ["enable_thinking", "preserve_thinking"] {
        if let Some(value) = payload
            .get("chat_template_kwargs")
            .and_then(|v| v.get(key))
            .and_then(Value::as_bool)
        {
            kwargs.insert(key.into(), json!(value));
        }
    }
    if !kwargs.is_empty() {
        safe.insert("chat_template_kwargs".into(), Value::Object(kwargs));
    }
    if let Some(messages) = payload.get("messages").and_then(Value::as_array) {
        let roles: Vec<_> = messages
            .iter()
            .map(
                |message| match message.get("role").and_then(Value::as_str) {
                    Some(
                        role
                        @ ("system" | "developer" | "user" | "assistant" | "tool" | "function"),
                    ) => role,
                    _ => "unknown",
                },
            )
            .collect();
        safe.insert("message_roles".into(), json!(roles));
    }
    Value::Object(safe)
}

struct CompletionSummary {
    stop_reason: &'static str,
    text_scalar_count: usize,
    text_is_exact_ready: bool,
    reasoning_lengths: Vec<usize>,
}
impl CompletionSummary {
    fn from_message(message: &AssistantMessage) -> Self {
        let text: String = message
            .content
            .iter()
            .filter_map(|block| block.as_text())
            .map(|block| block.text.as_str())
            .collect();
        Self {
            stop_reason: match message.stop_reason.as_str() {
                "stop" => "stop",
                "length" => "length",
                "toolUse" => "toolUse",
                "error" => "error",
                "aborted" => "aborted",
                _ => "unknown",
            },
            text_scalar_count: text.chars().count(),
            text_is_exact_ready: text.trim() == "READY",
            reasoning_lengths: message
                .content
                .iter()
                .filter_map(|block| block.as_thinking())
                .map(|block| block.thinking.chars().count())
                .collect(),
        }
    }
}

#[derive(Default)]
struct Observation {
    payload: Option<Value>,
    payload_count: u64,
    usage: UsageSnapshot,
    usage_count: u64,
    completion: Option<CompletionSummary>,
    receipt: Option<StreamTaskReceipt>,
}
type Shared = Arc<Mutex<Observation>>;
fn lock(shared: &Shared) -> std::sync::MutexGuard<'_, Observation> {
    shared
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn options(
    args: &Args,
    auth: ResolvedRequestAuth,
    signal: CancellationToken,
    state: &Shared,
) -> SimpleStreamOptions {
    let payload_state = state.clone();
    let usage_state = state.clone();
    SimpleStreamOptions {
        // Native simple adapter maps supported off to enabled=false, effort=None.
        reasoning: args.thinking_off.then(|| "off".to_owned()),
        thinking_budgets: None,
        stream: StreamOptions {
            max_tokens: Some(f64::from(args.max_tokens)),
            api_key: auth.api_key,
            headers: auth.headers,
            signal: Some(signal),
            timeout_ms: Some(f64::from(args.timeout_seconds) * 1000.0),
            on_payload: Some(Arc::new(move |payload, _| {
                let mut state = lock(&payload_state);
                state.payload = Some(payload_summary(&payload));
                state.payload_count += 1;
                Box::pin(async { None }) // Observe only; do not rewrite the request.
            })),
            on_usage_observation: Some(Arc::new(move |observation, _| {
                let mut state = lock(&usage_state);
                state.usage.observe(observation);
                state.usage_count += 1;
                Box::pin(async {})
            })),
            ..Default::default()
        },
    }
}

async fn invoke(model: &Model, options: &SimpleStreamOptions, state: &Shared) -> &'static str {
    let context = Context::new(
        None,
        vec![Message::User(UserMessage::new(
            UserContent::Text(PROMPT.into()),
            0,
        ))],
        None,
    );
    let stream = pi_ai::stream::stream_simple(model, &context, Some(options));
    lock(state).receipt = Some(stream.task_receipt());
    while let Some(event) = stream.next().await {
        let message = match event {
            AssistantMessageEvent::Done { message, .. } => message,
            AssistantMessageEvent::Error { error, .. } => error,
            _ => continue,
        };
        let summary = CompletionSummary::from_message(&message);
        let status = match summary.stop_reason {
            "aborted" => "cancelled",
            "error" => "provider_error",
            "unknown" => "unexpected_stop_reason",
            _ if message.error_message.is_some() => "provider_error",
            _ => "completed",
        };
        lock(state).completion = Some(summary);
        return status;
    }
    "stream_ended_without_terminal"
}

async fn run(registry: ModelRegistry, model: Model, args: &Args, state: &Shared) -> &'static str {
    let signal = CancellationToken::new();
    let deadline =
        tokio::time::Instant::now() + Duration::from_secs(u64::from(args.timeout_seconds));
    let work = async {
        // Config helpers can block; keep the main deadline/cancellation runnable.
        let auth_model = model.clone();
        let runtime = tokio::runtime::Handle::current();
        let auth = tokio::task::spawn_blocking(move || {
            let mut registry = registry;
            runtime.block_on(registry.get_api_key_and_headers(&auth_model))
        })
        .await;
        let auth = match auth {
            Ok(auth) if auth.ok && auth.api_key.as_ref().is_some_and(|key| !key.is_empty()) => auth,
            _ => return "auth_resolution_failed",
        };
        invoke(&model, &options(args, auth, signal.clone(), state), state).await
    };
    let status = tokio::select! {
        result = tokio::time::timeout_at(deadline, work) => result.unwrap_or("timeout"),
        _ = tokio::signal::ctrl_c() => "cancelled",
    };
    settle(
        state,
        &signal,
        status,
        deadline.saturating_duration_since(tokio::time::Instant::now()),
    )
    .await;
    status
}

async fn settle(state: &Shared, signal: &CancellationToken, status: &str, remaining: Duration) {
    let receipt = lock(state).receipt.clone();
    if matches!(status, "timeout" | "cancelled") {
        signal.cancel();
        if let Some(receipt) = &receipt {
            receipt.request_cancel();
        }
    }
    if let Some(receipt) = receipt {
        let tasks = receipt
            .settle(remaining.min(Duration::from_millis(250)))
            .await;
        if tasks.pending_tasks > 0 {
            signal.cancel();
            receipt.request_cancel();
        }
    }
}

fn report(
    args: &Args,
    sha: Option<String>,
    state: &Shared,
    status: &str,
    elapsed: Duration,
) -> Value {
    let state = lock(state);
    let completion = state.completion.as_ref();
    json!({
        "schema": SCHEMA, "provider": PROVIDER, "model": MODEL, "config_sha256": sha,
        "requested": {"thinking": if args.thinking_off {"off"} else {"unspecified"},
            "reasoning_enabled": args.thinking_off.then_some(false), "reasoning_effort": Value::Null,
            "max_tokens": args.max_tokens, "timeout_seconds": args.timeout_seconds},
        "payload": state.payload, "payload_observation_count": state.payload_count,
        "status": status, "stop_reason": completion.map(|c| c.stop_reason),
        "text_scalar_count": completion.map(|c| c.text_scalar_count),
        "text_is_exact_ready": completion.map(|c| c.text_is_exact_ready),
        "reasoning_block_count": completion.map(|c| c.reasoning_lengths.len()),
        "reasoning_scalar_count": completion.map(|c| c.reasoning_lengths.iter().sum::<usize>()),
        "reasoning_block_scalar_counts": completion.map(|c| &c.reasoning_lengths),
        "usage": state.usage, "usage_source": if state.usage_count > 0 {Some("provider_observation")} else {None},
        "usage_observation_count": state.usage_count, "elapsed_ms": elapsed.as_millis(),
    })
}

fn execute(raw: &[String]) -> Checked<bool> {
    let args = Args::parse(raw)?;
    let started = Instant::now();
    let mut output = reserve_output(&args)?;
    let state = Shared::default();
    let mut sha = None;
    let status = match load_registry(&args, &mut sha) {
        Err(category) => category,
        Ok((registry, model)) => {
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
                .map_err(|_| "runtime_failed")?;
            let status = runtime.block_on(run(registry, model, &args, &state));
            // A native blocking credential helper must not extend the process deadline.
            runtime.shutdown_timeout(Duration::ZERO);
            status
        }
    };
    let encoded = serde_json::to_vec(&report(&args, sha, &state, status, started.elapsed()))
        .map_err(|_| "report_failed")?;
    output
        .write_all(&encoded)
        .and_then(|_| output.write_all(b"\n"))
        .and_then(|_| output.sync_all())
        .map_err(|_| "report_write_failed")?;
    Ok(status == "completed")
}

fn main() {
    // Do not let provider/helper panic payloads reveal configuration or content.
    std::panic::set_hook(Box::new(|_| {}));
    let result =
        std::panic::catch_unwind(|| execute(&std::env::args().skip(1).collect::<Vec<_>>()));
    let exit = match result {
        Ok(Ok(true)) => 0,
        Ok(Ok(false)) => 2,
        other => {
            let category = match other {
                Ok(Err(category)) => category,
                _ => "panic",
            };
            eprintln!("{}", json!({"schema": SCHEMA, "status": category}));
            2
        }
    };
    std::process::exit(exit);
}

#[cfg(test)]
mod tests {
    use super::*;
    use pi_ai::api_registry::{register_api_provider_simple, ApiProviderSimple};
    use pi_ai::types::{
        Compat, ContentBlock, OpenAICompletionsCompat, TextContent, ThinkingContent,
    };
    use pi_ai::utils::event_stream::AssistantMessageEventStream;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn args(root: &Path) -> Args {
        Args {
            config: root.join("input.json"),
            scratch_dir: root.join("scratch"),
            output: root.join("result.json"),
            max_tokens: 64,
            thinking_off: true,
            timeout_seconds: 1,
        }
    }
    fn raw(args: &Args) -> Vec<String> {
        vec![
            "--config".into(),
            args.config.to_string_lossy().into_owned(),
            "--scratch-dir".into(),
            args.scratch_dir.to_string_lossy().into_owned(),
            "--output".into(),
            args.output.to_string_lossy().into_owned(),
            "--max-tokens".into(),
            args.max_tokens.to_string(),
            "--allow-network".into(),
        ]
    }
    fn synthetic_auth() -> ResolvedRequestAuth {
        ResolvedRequestAuth {
            ok: true,
            api_key: Some("synthetic-not-a-key".into()),
            ..Default::default()
        }
    }
    fn synthetic_model(api: &str) -> Model {
        let mut model = Model::new(MODEL, MODEL, api, PROVIDER, "https://example.invalid/v1");
        model.reasoning = true;
        model
    }

    #[test]
    fn gate_and_bounds_precede_io() {
        let temp = tempfile::tempdir().unwrap();
        let args = args(temp.path());
        let mut raw = raw(&args);
        raw.pop();
        assert_eq!(execute(&raw), Err("network_not_allowed"));
        assert!(!args.output.exists() && !args.scratch_dir.exists());
        raw.push("--allow-network".into());
        for bad in ["0", "1025", "-1", "2.5"] {
            raw[7] = bad.into();
            assert_eq!(Args::parse(&raw).unwrap_err(), "invalid_token_cap");
        }
        raw[7] = "64".into();
        raw.extend(["--timeout-seconds".into(), "91".into()]);
        assert_eq!(Args::parse(&raw).unwrap_err(), "invalid_timeout");
        assert!(!args.output.exists() && !args.scratch_dir.exists());
    }

    #[test]
    fn new_outputs_preserve_existing_attempts() {
        let temp = tempfile::tempdir().unwrap();
        let args = args(temp.path());
        reserve_output(&args)
            .unwrap()
            .write_all(b"synthetic-attempt")
            .unwrap();
        assert_eq!(reserve_output(&args).unwrap_err(), "output_exists");
        assert_eq!(std::fs::read(&args.output).unwrap(), b"synthetic-attempt");
    }

    #[test]
    fn explicit_registry_preserves_configuration_without_retaining_copy() {
        let temp = tempfile::tempdir().unwrap();
        let args = args(temp.path());
        let bytes = serde_json::to_vec(&json!({"providers": {
            "unrelated": {"invalidUnrelatedSettings": true},
            "dgx-glm53": {"api": "openai-completions", "baseUrl": "https://example.invalid/v1",
                "apiKey": "synthetic-not-a-key", "compat": {"supportsDeveloperRole": false, "thinkingFormat": "qwen-chat-template"},
                "models": [{"id": "glm-5.3", "reasoning": true, "thinkingLevelMap": {"off": "none", "minimal": null},
                    "compat": {"maxTokensField": "max_tokens", "requiresToolResultName": true}}]}
        }})).unwrap();
        std::fs::write(&args.config, &bytes).unwrap();
        let _output = reserve_output(&args).unwrap();
        let mut sha = None;
        let (registry, model) = load_registry(&args, &mut sha).unwrap();
        assert_eq!(sha, Some(format!("{:x}", Sha256::digest(&bytes))));
        assert!(!args.scratch_dir.join("models.json").exists());
        assert!(registry.auth_storage().get_all().is_empty());
        assert!(args.scratch_dir.join("auth.json").exists());
        assert!(model.reasoning);
        assert_eq!(model.thinking_level_map_get("minimal"), Some(None));
        let compat = model.compat_completions().unwrap();
        assert_eq!(compat.supports_developer_role, Some(false));
        assert_eq!(compat.requires_tool_result_name, Some(true));
        assert_eq!(
            compat.thinking_format.as_deref(),
            Some("qwen-chat-template")
        );
        assert_eq!(compat.max_tokens_field.as_deref(), Some("max_tokens"));
        let mut unsupported = model.clone();
        unsupported
            .thinking_level_map
            .get_or_insert_with(Default::default)
            .insert("off".into(), None);
        assert_eq!(
            validate_thinking(&unsupported, true),
            Err("thinking_off_unsupported")
        );
        unsupported.reasoning = false;
        assert_eq!(
            validate_thinking(&unsupported, true),
            Err("thinking_off_unsupported")
        );
        assert!(validate_thinking(&unsupported, false).is_ok());
    }

    #[test]
    fn all_null_thinking_map_and_unsafe_endpoints_are_rejected() {
        let mut model = synthetic_model(API_OPENAI_COMPLETIONS);
        model.thinking_level_map = Some(
            pi_ai::models::EXTENDED_THINKING_LEVELS
                .iter()
                .map(|level| (level.to_string(), None))
                .collect(),
        );
        assert!(pi_ai::models::get_supported_thinking_levels(&model).is_empty());
        assert_eq!(pi_ai::models::clamp_thinking_level(&model, "off"), "off");
        assert_eq!(
            validate_thinking(&model, true),
            Err("thinking_off_unsupported")
        );
        for endpoint in [
            "http://example.invalid/v1",
            "https://user:secret@example.invalid/v1",
            "https://user@example.invalid/v1",
            "https://:secret@example.invalid/v1",
            "https://@example.invalid/v1",
            "example.invalid/v1",
            "not a URL",
        ] {
            assert_eq!(validate_endpoint(endpoint), Err("endpoint_invalid"));
        }
        assert!(validate_endpoint("https://example.invalid/v1").is_ok());
    }

    #[test]
    fn missing_explicit_auth_is_rejected_before_native_registry() {
        let temp = tempfile::tempdir().unwrap();
        let args = args(temp.path());
        std::fs::write(
            &args.config,
            serde_json::to_vec(&json!({"providers": {
                "dgx-glm53": {"api": "openai-completions", "baseUrl": "https://example.invalid/v1",
                    "models": [{"id": "glm-5.3", "reasoning": true}]}
            }}))
            .unwrap(),
        )
        .unwrap();
        let _output = reserve_output(&args).unwrap();
        assert_eq!(
            load_registry(&args, &mut None).err(),
            Some("auth_not_explicit")
        );
        assert!(!args.scratch_dir.join("auth.json").exists());
        assert!(!args.scratch_dir.join("models.json").exists());
    }

    #[test]
    fn usage_latest_present_unknown_and_zero_are_not_inferred_or_summed() {
        let mut usage = UsageSnapshot::default();
        usage.observe(ProviderUsageObservation {
            input_tokens: Some(Some(5.0)),
            output_tokens: Some(Some(2.0)),
            ..Default::default()
        });
        usage.observe(ProviderUsageObservation {
            output_tokens: Some(Some(3.0)),
            ..Default::default()
        });
        assert_eq!(usage.input, Some(5.0));
        assert_eq!(usage.output, Some(3.0));
        assert!(usage.total.is_none());
        usage.observe(ProviderUsageObservation {
            input_tokens: Some(None),
            output_tokens: Some(Some(0.0)),
            reasoning_tokens: Some(Some(f64::NAN)),
            total_tokens: Some(Some(-1.0)),
            ..Default::default()
        });
        assert!(usage.input.is_none() && usage.reasoning.is_none() && usage.total.is_none());
        assert_eq!(usage.output, Some(0.0));
    }

    #[test]
    fn whitelist_and_error_report_never_contain_content() {
        let temp = tempfile::tempdir().unwrap();
        let args = args(temp.path());
        let state = Shared::default();
        let payload = json!({"max_tokens": 64, "reasoning_effort": "SECRET", "thinking": {"type": "SECRET"},
            "headers": {"Authorization": "SECRET"}, "apiKey": "SECRET", "other": "SECRET",
            "chat_template_kwargs": {"enable_thinking": false, "preserve_thinking": true, "body": "SECRET"},
            "messages": [{"role": "user", "content": "SECRET"}, {"role": "SECRET"}]});
        lock(&state).payload = Some(payload_summary(&payload));
        let message = AssistantMessage {
            stop_reason: "SECRET".into(),
            error_message: Some("SECRET".into()),
            content: vec![
                ContentBlock::Text(TextContent::new("é🙂")),
                ContentBlock::Thinking(ThinkingContent::new("SECRET")),
            ],
            ..Default::default()
        };
        lock(&state).completion = Some(CompletionSummary::from_message(&message));
        let row = report(&args, None, &state, "provider_error", Duration::ZERO);
        assert_eq!(row["text_scalar_count"], 2);
        assert_eq!(row["reasoning_scalar_count"], 6);
        assert_eq!(row["stop_reason"], "unknown");
        assert_eq!(row["payload"]["message_roles"], json!(["user", "unknown"]));
        assert!(row["usage"]["input"].is_null() && row["usage"]["output"].is_null());
        assert!(!row.to_string().contains("SECRET"));
        assert!(!row.to_string().contains("é🙂"));
    }

    #[tokio::test]
    async fn registered_fake_provider_calls_once_and_keeps_missing_usage_unknown() {
        let temp = tempfile::tempdir().unwrap();
        let args = args(temp.path());
        for mode in ["success", "error", "aborted", "timeout"] {
            let api = format!("memory-provider-probe-fixture-{mode}");
            let calls = Arc::new(AtomicUsize::new(0));
            let called = calls.clone();
            register_api_provider_simple(
                ApiProviderSimple {
                    api: api.clone(),
                    stream: Arc::new(|_, _, _| panic!("unexpected base dispatch")),
                    stream_simple: Arc::new(move |model, context, options| {
                        called.fetch_add(1, Ordering::SeqCst);
                        assert_eq!(model.provider, PROVIDER);
                        assert_eq!(model.id, MODEL);
                        assert_eq!(context.messages.len(), 1);
                        assert!(context.system_prompt.is_none() && context.tools.is_none());
                        match &context.messages[0] {
                            Message::User(user) => {
                                assert_eq!(user.content, UserContent::Text(PROMPT.into()))
                            }
                            _ => panic!("unexpected role"),
                        }
                        let options = options.unwrap().clone();
                        assert_eq!(options.reasoning.as_deref(), Some("off"));
                        assert!(
                            options.thinking_budgets.is_none() && options.stream.extra.is_empty()
                        );
                        let model = model.clone();
                        let stream = AssistantMessageEventStream::new_owned();
                        let out = stream.producer_handle();
                        stream.spawn(async move {
                            let callback = options.stream.on_payload.as_ref().unwrap();
                            assert!(callback(
                                json!({"max_tokens": 64, "enable_thinking": false,
                            "messages": [{"role": "user", "content": "SECRET"}]}),
                                &model
                            )
                            .await
                            .is_none());
                            if mode == "timeout" {
                                std::future::pending::<()>().await;
                            }
                            let mut message = AssistantMessage::default();
                            if mode == "success" {
                                for output in [2.0, 3.0] {
                                    options.stream.on_usage_observation.as_ref().unwrap()(
                                        ProviderUsageObservation {
                                            input_tokens: Some(Some(5.0)),
                                            output_tokens: Some(Some(output)),
                                            ..Default::default()
                                        },
                                        &model,
                                    )
                                    .await;
                                }
                                message.content = vec![
                                    ContentBlock::Text(TextContent::new(" READY \n")),
                                    ContentBlock::Thinking(ThinkingContent::new("SECRET")),
                                ];
                                out.push(AssistantMessageEvent::Done {
                                    reason: "stop".into(),
                                    message,
                                });
                            } else {
                                message.stop_reason = mode.into();
                                message.error_message = Some("SECRET".into());
                                out.push(AssistantMessageEvent::Error {
                                    reason: mode.into(),
                                    error: message,
                                });
                            }
                        });
                        stream
                    }),
                    compact: None,
                    supports_compaction: None,
                },
                Some(api.clone()),
            );
            let state = Shared::default();
            let signal = CancellationToken::new();
            let options = options(&args, synthetic_auth(), signal.clone(), &state);
            let model = synthetic_model(&api);
            let status =
                tokio::time::timeout(Duration::from_millis(100), invoke(&model, &options, &state))
                    .await
                    .unwrap_or("timeout");
            settle(&state, &signal, status, Duration::from_secs(1)).await;
            assert_eq!(calls.load(Ordering::SeqCst), 1);
            let row = report(&args, None, &state, status, Duration::ZERO);
            assert_eq!(row["payload_observation_count"], 1);
            if mode == "success" {
                assert_eq!(row["status"], "completed");
                assert_eq!(row["text_is_exact_ready"], true);
                assert_eq!(row["text_scalar_count"], 8);
                assert_eq!(row["usage"]["output"], 3.0);
                assert_eq!(row["usage_observation_count"], 2);
            } else {
                assert_eq!(
                    row["status"],
                    match mode {
                        "aborted" => "cancelled",
                        "error" => "provider_error",
                        _ => "timeout",
                    }
                );
                assert!(row["usage_source"].is_null());
                assert!(row["usage"]["input"].is_null() && row["usage"]["output"].is_null());
                if mode == "timeout" {
                    assert!(row["stop_reason"].is_null() && row["text_scalar_count"].is_null());
                }
            }
            assert!(
                row["usage"]["totalTokens"].is_null() && row["usage"]["reasoningTokens"].is_null()
            );
            assert!(!row.to_string().contains("SECRET"));
            assert_eq!(
                lock(&state)
                    .receipt
                    .as_ref()
                    .unwrap()
                    .status()
                    .pending_tasks,
                0
            );
            pi_ai::api_registry::unregister_api_providers(&api);
        }
    }

    #[tokio::test]
    async fn native_payload_observer_stops_before_transport_and_preserves_dialects() {
        pi_ai::stream::ensure_builtin_api_providers_registered();
        let temp = tempfile::tempdir().unwrap();
        for (dialect, off, field, expected) in [
            ("openai", true, "/reasoning_effort", json!("none")),
            ("openai", false, "/reasoning_effort", Value::Null),
            ("deepseek", true, "/thinking/type", json!("disabled")),
            ("zai", true, "/enable_thinking", json!(false)),
            ("qwen", true, "/enable_thinking", json!(false)),
            (
                "qwen-chat-template",
                true,
                "/chat_template_kwargs/enable_thinking",
                json!(false),
            ),
        ] {
            let mut args = args(temp.path());
            args.thinking_off = off;
            let mut model = synthetic_model(API_OPENAI_COMPLETIONS);
            model.compat = Some(Compat::Completions(OpenAICompletionsCompat {
                thinking_format: Some(dialect.into()),
                supports_reasoning_effort: Some(true),
                supports_developer_role: Some(false),
                max_tokens_field: Some("max_tokens".into()),
                ..Default::default()
            }));
            let original_model = model.clone();
            let state = Shared::default();
            let signal = CancellationToken::new();
            let mut options = options(&args, synthetic_auth(), signal.clone(), &state);
            let observer = options.stream.on_payload.take().unwrap();
            let observed = Arc::new(tokio::sync::Notify::new());
            let wake = observed.clone();
            options.stream.on_payload = Some(Arc::new(move |payload, model| {
                let result = observer(payload, model);
                wake.notify_one();
                Box::pin(async move {
                    assert!(result.await.is_none());
                    // The native provider awaits this hook BEFORE sending. Never release it.
                    std::future::pending::<Option<Value>>().await
                })
            }));
            let context = Context::new(
                None,
                vec![Message::User(UserMessage::new(
                    UserContent::Text(PROMPT.into()),
                    0,
                ))],
                None,
            );
            let stream = pi_ai::stream::stream_simple(&model, &context, Some(&options));
            tokio::time::timeout(Duration::from_secs(2), observed.notified())
                .await
                .unwrap();
            signal.cancel();
            stream.request_cancel();
            assert_eq!(
                stream
                    .task_receipt()
                    .settle(Duration::from_secs(1))
                    .await
                    .pending_tasks,
                0
            );
            let row = report(&args, None, &state, "cancelled", Duration::ZERO);
            assert_eq!(row["payload_observation_count"], 1);
            assert_eq!(
                row["payload"]
                    .pointer(field)
                    .cloned()
                    .unwrap_or(Value::Null),
                expected
            );
            assert_eq!(row["payload"]["max_tokens"], 64);
            assert_eq!(row["payload"]["message_roles"], json!(["user"]));
            assert!(row["usage_source"].is_null());
            assert_eq!(model, original_model);
        }
    }
}
