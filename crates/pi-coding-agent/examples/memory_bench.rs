//! H2 — memory benchmark driver, ORIGINAL CONTROL ARM (campaign: optimus-memory-bench).
//!
//! Pristine Original product pin: 4baed27e709d67f20a8edb5d0bd09e40bd3a7678
//! (v0.1.25). This example is the only addition to that clean worktree; every
//! product Rust/Python/settings file stays byte-identical to the pin. It is
//! harness-only code: no product bugfix is backported, and no product file is
//! touched. Historical base 136056ce is a later, fixed source and is NOT this
//! arm; label results from this adapter as Original (4baed27 + this adapter),
//! never as that baseline.
//!
//! Lineage: the d9f6627987cc3c5874d80be22a6227e287cbfa0a first harness-only
//! driver (1147 lines) proved the Original API surface. This adapter keeps its
//! product call shape and adds the harness-only contracts of the validated
//! 43b1993 driver where the Original product can express them:
//!   - explicit isolated config: MEMORY_BENCH_MODELS_JSON must name the
//!     provider configuration; the installed-profile fallback is removed;
//!   - process NoJev isolation: control profile under <output_dir>/_control,
//!     native Jev settings explicitly Off, credential env vars removed without
//!     reading them, verified before runtime/preflight/ingest/questions;
//!   - scratch settings.json with autoRefine/retry/compaction/telemetry off and
//!     a memory block derived from the manifest, checked against the real
//!     store before any provider work (settings silently ignored by the
//!     Original loader fail closed here);
//!   - camelCase and snake_case aliases for every exercised setting, plus a
//!     known-key guard: settings the Original product cannot express
//!     (importInstructions, recallQueryDistillation, recallRerank) or unknown
//!     settings keys are refused instead of silently dropped;
//!   - shared input contract: captureProtocol
//!     "optimus-memory-capture/temporal-authority/1.0.0" preserves
//!     source-authored roles (external locomo/locomo_plus speaker labels are
//!     user evidence) and writes only trusted source `ts` timestamps — the
//!     driver never invents dates and never appends row dates;
//!   - answerInstruction wrapper, answerMaxTokens (1..=32000) and
//!     answerThinkingLevel (native creation options, no clamping) match the
//!     validated driver;
//!   - skipIngest control for query-only runs;
//!   - gold-free all-or-none caller question declarations
//!     (question_protocol/scoring_protocol/question_sha256) echoed verbatim.
//!
//! Original product telemetry limits are declared, never fabricated: the
//! Original has no recall helpers, so their call evidence is
//! "unavailable_original_product_no_recall_helpers" and their observations are
//! null. Frozen-store hydration is NOT implemented: the Original arm runs fresh
//! capture + answer; manifests asking for frozenMemory fail closed.
//!
//! Usage:
//!   cargo run -p pi-coding-agent --example memory_bench -- <manifest.json>
//!
//! Outputs (append-only, resumable by (run_id, env_id[, qid])):
//!   <output_dir>/run.jsonl  — one record per question
//!   <output_dir>/env.jsonl  — one record per environment (store snapshot)

use std::collections::HashSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::{json, Map, Value};

use pi_agent_core::types::ThinkingLevel;
use pi_coding_agent::config::{env_agent_dir, get_agent_dir};
use pi_jev::config::{
    JevMode, JevSettings, JevSettingsStore, ENV_AGENT_DIR, ENV_JEV_API_KEY, ENV_TYPESAFE_API_KEY,
};

use pi_coding_agent::core::agent_session_services::{
    create_agent_session_from_services, create_agent_session_services,
    AgentSessionCreationOptions, CreateAgentSessionFromServicesOptions,
    CreateAgentSessionServicesOptions,
};
use pi_coding_agent::core::auth_storage::AuthStorage;
use pi_coding_agent::core::extensions::builtin::memory::{
    create_memory_extension, MEMORY_DIAGNOSTIC_CUSTOM_TYPE,
};
use pi_coding_agent::core::kernel::shared::HostRequestHandler;
use pi_coding_agent::core::memory::evidence::hash;
use pi_coding_agent::core::memory::service::{create_memory_host_handlers, MemoryService};
use pi_coding_agent::core::memory::store::MemorySettings;
use pi_coding_agent::core::model_registry::ModelRegistry;
use pi_coding_agent::core::resource_loader::DefaultResourceLoaderOptions;
use pi_coding_agent::core::session_manager::SessionManager;
use pi_coding_agent::core::settings_manager::SettingsManager;
use pi_coding_agent::core::side_question::read_assistant_text;

// ---------------------------------------------------------------------------
// Manifest (v1)
// ---------------------------------------------------------------------------

fn default_true() -> bool {
    true
}

fn default_variant() -> String {
    "base".to_string()
}

fn default_question_timeout_s() -> u64 {
    180
}

fn default_ingest_timeout_s() -> u64 {
    3600
}

fn default_import_run_timeout_s() -> u64 {
    900
}

fn default_import_run_retries() -> u32 {
    3
}

#[derive(Debug, Deserialize)]
struct ModelSpec {
    provider: String,
    id: String,
}

#[derive(Debug, Deserialize)]
struct ToolResultSpec {
    #[serde(rename = "toolCallId")]
    tool_call_id: String,
    #[serde(rename = "toolName")]
    tool_name: String,
    #[serde(default, rename = "isError")]
    is_error: bool,
}

#[derive(Debug, Deserialize)]
struct EventSpec {
    role: String,
    text: String,
    #[serde(default)]
    ext_id: Option<String>,
    // Legacy manifests ignored this field. Validate its type only in the new capture mode.
    #[serde(default)]
    ts: Option<Value>,
    #[serde(default)]
    authority: Option<String>,
    #[serde(default, alias = "toolResult")]
    tool_result: Option<ToolResultSpec>,
}

#[derive(Debug, Deserialize)]
struct SessionSpec {
    session_id: String,
    #[serde(default)]
    events: Vec<EventSpec>,
}

#[derive(Debug, Deserialize)]
struct EnvSpec {
    env_id: String,
    #[serde(default)]
    family: Option<String>,
    #[serde(default)]
    authority_revision: Option<u32>,
    #[serde(default)]
    capture_protocol: Option<String>,
    #[serde(default)]
    notes: Value,
    #[serde(default)]
    sessions: Vec<SessionSpec>,
    #[serde(default)]
    ground_truth_refs: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct QuestionSpec {
    qid: String,
    env_id: String,
    question: String,
    #[serde(default, deserialize_with = "declared_string")]
    question_protocol: Option<String>,
    #[serde(default, deserialize_with = "declared_string")]
    scoring_protocol: Option<String>,
    #[serde(default, deserialize_with = "declared_string")]
    question_sha256: Option<String>,
    #[serde(default)]
    answer: Option<Value>,
    #[serde(default)]
    evidence: Vec<String>,
    #[serde(default)]
    category: Option<String>,
    #[serde(default)]
    eval: Option<Value>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ManifestSettings {
    #[serde(default = "default_true", alias = "recall_on")]
    recall_on: bool,
    #[serde(default, alias = "memory_learning")]
    memory_learning: Option<bool>,
    #[serde(default, alias = "answer_max_tokens")]
    answer_max_tokens: Option<u32>,
    /// Explicit main-answer control only; None keeps the native SDK default path.
    #[serde(default, alias = "answer_thinking_level")]
    answer_thinking_level: Option<ThinkingLevel>,
    #[serde(default, alias = "max_recall_chars")]
    max_recall_chars: Option<i64>,
    #[serde(default, alias = "max_recall_entries")]
    max_recall_entries: Option<i64>,
    /// Override memory.maxExtractionTokens (None = production default 4096).
    #[serde(default, alias = "max_extraction_tokens")]
    max_extraction_tokens: Option<i64>,
    /// Override memory.maxImportChunkChars (None = production default 40000).
    #[serde(default, alias = "max_import_chunk_chars")]
    max_import_chunk_chars: Option<i64>,
    /// Extra driver-level retries for a failed import_run invocation (the job
    /// checkpoints per chunk, so a retry resumes where it stopped).
    #[serde(default = "default_import_run_retries", alias = "import_run_retries")]
    import_run_retries: u32,
    #[serde(default, alias = "auto_refine")]
    auto_refine: Option<Value>,
    #[serde(default = "default_question_timeout_s", alias = "question_timeout_s")]
    question_timeout_s: u64,
    #[serde(default = "default_ingest_timeout_s", alias = "ingest_timeout_s")]
    ingest_timeout_s: u64,
    #[serde(default = "default_import_run_timeout_s", alias = "import_run_timeout_s")]
    import_run_timeout_s: u64,
    /// Disable the session toolset for question answering (default true).
    #[serde(default = "default_true", alias = "disable_tools")]
    disable_tools: bool,
    /// Optional benchmark answering-protocol wrapper: the prompt becomes the
    /// instruction, a blank line, then "Question: <question>" so the model
    /// answers directly instead of narrating agent-style tool intent.
    #[serde(default, alias = "answer_instruction")]
    answer_instruction: Option<String>,
    /// New capture mode only; never a product memory setting.
    #[serde(default = "default_true", alias = "evidence_timestamps")]
    evidence_timestamps: bool,
    /// Skip environment ingestion entirely (query-only control: questions are
    /// answered without imported memory).
    #[serde(default, alias = "skip_ingest")]
    skip_ingest: bool,
}

impl Default for ManifestSettings {
    fn default() -> Self {
        ManifestSettings {
            recall_on: true,
            memory_learning: None,
            answer_max_tokens: None,
            answer_thinking_level: None,
            max_recall_chars: None,
            max_recall_entries: None,
            max_extraction_tokens: None,
            max_import_chunk_chars: None,
            import_run_retries: default_import_run_retries(),
            auto_refine: None,
            question_timeout_s: default_question_timeout_s(),
            ingest_timeout_s: default_ingest_timeout_s(),
            import_run_timeout_s: default_import_run_timeout_s(),
            disable_tools: true,
            answer_instruction: None,
            evidence_timestamps: true,
            skip_ingest: false,
        }
    }
}

#[derive(Debug, Deserialize)]
struct Manifest {
    #[serde(skip)]
    manifest_sha256: String,
    #[serde(default, rename = "captureProtocol", alias = "capture_protocol", deserialize_with = "declared_string")]
    capture_protocol: Option<String>,
    run_id: String,
    #[serde(default)]
    benchmark: String,
    #[serde(default = "default_variant")]
    variant: String,
    output_dir: String,
    #[serde(default)]
    envs: Vec<EnvSpec>,
    #[serde(default)]
    questions: Vec<QuestionSpec>,
    model: ModelSpec,
    #[serde(default)]
    settings: ManifestSettings,
    /// Informational only (subset-selection seeds are applied upstream).
    #[serde(default)]
    #[allow(dead_code)]
    seed: Option<Value>,
}

// ---------------------------------------------------------------------------
// Process isolation (install once, before starting threads or model services)
// ---------------------------------------------------------------------------

struct BenchmarkIsolation {
    output_dir: PathBuf,
    control_agent_dir: PathBuf,
}

fn canonical_inside(path: &Path, root: &Path) -> Result<PathBuf, String> {
    let canonical = std::fs::canonicalize(path)
        .map_err(|_| "noJev isolation path cannot be resolved".to_string())?;
    if !canonical.starts_with(root) {
        return Err("noJev isolation path escapes the requested scratch output".to_string());
    }
    Ok(canonical)
}

fn scratch_control_dir(parent: &Path, name: &str, root: &Path) -> Result<PathBuf, String> {
    let path = parent.join(name);
    match std::fs::symlink_metadata(&path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                return Err("noJev control path must be a real scratch directory".to_string());
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            std::fs::create_dir(&path)
                .map_err(|_| "cannot create noJev scratch control directory".to_string())?;
        }
        Err(_) => return Err("cannot inspect noJev scratch control directory".to_string()),
    }
    canonical_inside(&path, root)
}

fn validate_no_jev_settings(settings: &JevSettings) -> Result<(), String> {
    settings
        .validate()
        .map_err(|_| "invalid noJev control settings".to_string())?;
    if settings.schema_version != pi_jev::config::SETTINGS_SCHEMA_VERSION
        || settings.global_default != Some(JevMode::Off)
        || !settings.sessions.is_empty()
        || settings.full_jev.is_some()
        || settings.compaction_enabled
        || settings.credential_configured
        || settings.credential_source.is_some()
        || settings.transport.is_some()
        || settings.effective_mode("memory-bench-no-jev") != JevMode::Off
        || settings.effective_compaction_enabled("memory-bench-no-jev")
        || settings.wants_observer()
    {
        return Err("noJev requires explicit Off, compaction off, no session/Full overrides, and no credential or transport configuration; refusing unsafe control settings".to_string());
    }
    Ok(())
}

impl BenchmarkIsolation {
    fn prepare(output_dir: &Path) -> Result<Self, String> {
        std::fs::create_dir_all(output_dir)
            .map_err(|_| "cannot create benchmark scratch output".to_string())?;
        let output_dir = std::fs::canonicalize(output_dir)
            .map_err(|_| "cannot resolve benchmark scratch output".to_string())?;
        let control_dir = scratch_control_dir(&output_dir, "_control", &output_dir)?;
        let control_agent_dir = scratch_control_dir(&control_dir, "agent", &output_dir)?;
        scratch_control_dir(&control_agent_dir, "jev", &output_dir)?;
        Ok(Self {
            output_dir,
            control_agent_dir,
        })
    }

    fn checked_store(&self) -> Result<JevSettingsStore, String> {
        let agent_dir = canonical_inside(&self.control_agent_dir, &self.output_dir)?;
        if agent_dir != self.control_agent_dir {
            return Err("noJev control profile changed after initialization".to_string());
        }
        let jev_dir = canonical_inside(&agent_dir.join("jev"), &agent_dir)?;
        let store = JevSettingsStore::new(&agent_dir);
        let lock_name = format!("{}.lock", pi_jev::config::SETTINGS_FILE_NAME);
        // Inspect names/paths only. Never open a saved credential envelope.
        for entry in std::fs::read_dir(&jev_dir)
            .map_err(|_| "cannot inspect noJev control directory".to_string())?
        {
            let entry = entry.map_err(|_| "cannot inspect noJev control entry".to_string())?;
            let name = entry.file_name();
            if name.as_os_str() != std::ffi::OsStr::new(pi_jev::config::SETTINGS_FILE_NAME)
                && name.as_os_str() != std::ffi::OsStr::new(&lock_name)
            {
                return Err("unexpected state in noJev control directory; credentials and alternate settings are forbidden".to_string());
            }
            let metadata = std::fs::symlink_metadata(entry.path())
                .map_err(|_| "cannot inspect noJev control file".to_string())?;
            if metadata.file_type().is_symlink() || !metadata.is_file() {
                return Err("noJev control settings/lock must be regular scratch files".to_string());
            }
            canonical_inside(&entry.path(), &agent_dir)?;
        }
        Ok(store)
    }

    fn read_settings(&self) -> Result<JevSettings, String> {
        let store = self.checked_store()?;
        // Native load deliberately tolerates corruption. The benchmark must not.
        let bytes = std::fs::read(store.path())
            .map_err(|_| "noJev control settings are missing or unreadable".to_string())?;
        let parsed: JevSettings = serde_json::from_slice(&bytes)
            .map_err(|_| "noJev control settings are corrupt".to_string())?;
        validate_no_jev_settings(&parsed)?;
        let effective = store.load();
        validate_no_jev_settings(&effective)?;
        Ok(effective)
    }

    fn initialize_settings(&self) -> Result<(), String> {
        let store = self.checked_store()?;
        if !store.path().exists() {
            store
                .save(&JevSettings::with_global_default(JevMode::Off))
                .map_err(|_| "cannot initialize native noJev control settings".to_string())?;
        }
        self.read_settings()?;
        Ok(())
    }

    fn install_before_runtime(output_dir: &Path) -> Result<Self, String> {
        let isolation = Self::prepare(output_dir)?;
        // Do not inspect, retain, print, or restore inherited credential values.
        std::env::remove_var(ENV_TYPESAFE_API_KEY);
        std::env::remove_var(ENV_JEV_API_KEY);
        std::env::set_var(ENV_AGENT_DIR, &isolation.control_agent_dir);
        std::env::set_var(env_agent_dir(), &isolation.control_agent_dir);
        // Benchmark question sessions remain top-level, as before.
        std::env::remove_var("RLM_DEPTH");
        isolation.initialize_settings()?;
        isolation.verify("runtime startup")?;
        Ok(isolation)
    }

    fn verify_resolver(&self, resolved: &Path) -> Result<(), String> {
        if canonical_inside(resolved, &self.output_dir)? != self.control_agent_dir {
            return Err(
                "native profile resolver is not the benchmark noJev control profile".to_string(),
            );
        }
        Ok(())
    }

    fn verify(&self, phase: &str) -> Result<Value, String> {
        // Check resolver paths before reading settings; never inspect the host profile.
        self.verify_resolver(Path::new(&get_agent_dir()))?;
        self.verify_resolver(&pi_jev::config::default_agent_dir(None))?;
        let settings = self.read_settings()?;
        Ok(json!({
            "noJev": true,
            "verified_before": phase,
            "control_agent_dir": self.control_agent_dir,
            "native_resolvers_verified": ["config::get_agent_dir()", "pi_jev::config::default_agent_dir(None)"],
            "settings_source": "native JevSettingsStore at config::get_agent_dir()",
            "effective_mode": settings.effective_mode("memory-bench-no-jev").as_str(),
            "compaction_enabled": false,
            "session_override_count": 0,
            "full_jev_override": false,
            "credential_env_policy": "removed_without_reading_before_runtime",
            "installed_profile_fallback": false,
        }))
    }
}

fn explicit_models_source(value: Option<std::ffi::OsString>) -> Result<PathBuf, String> {
    value.filter(|value| !value.is_empty()).map(PathBuf::from).ok_or_else(|| {
        "MEMORY_BENCH_MODELS_JSON must explicitly name the provider configuration; installed-profile fallback is forbidden".to_string()
    })
}

// ---------------------------------------------------------------------------
// Scratch environment
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct EnvScratch {
    #[allow(dead_code)]
    env_id: String,
    #[allow(dead_code)]
    root: PathBuf,
    agent_dir: PathBuf,
    cwd: PathBuf,
    session_artifact_dir: PathBuf,
    sessions_dir: PathBuf,
}

impl EnvScratch {
    fn prepare(output_dir: &Path, env: &EnvSpec) -> Result<EnvScratch, String> {
        let root = output_dir.join("envs").join(&env.env_id);
        let agent_dir = root.join("agent");
        let cwd = root.join("workspace");
        let session_artifact_dir = root.join("session-artifacts");
        let sessions_dir = root.join("sessions");
        for dir in [&agent_dir, &cwd, &session_artifact_dir, &sessions_dir] {
            std::fs::create_dir_all(dir).map_err(|error| {
                format!("cannot create scratch dir {}: {error}", dir.display())
            })?;
        }
        Ok(EnvScratch {
            env_id: env.env_id.clone(),
            root,
            agent_dir,
            cwd,
            session_artifact_dir,
            sessions_dir,
        })
    }

    fn write_models_json(&self, models_source: &Path, provider: &str) -> Result<(), String> {
        let raw = std::fs::read_to_string(models_source).map_err(|error| {
            format!(
                "cannot read models.json source {}: {error}",
                models_source.display()
            )
        })?;
        let parsed: Value = serde_json::from_str(&raw)
            .map_err(|error| format!("models.json source is not valid JSON: {error}"))?;
        let entry = parsed
            .get("providers")
            .and_then(|providers| providers.get(provider))
            .cloned()
            .ok_or_else(|| {
                format!(
                    "provider \"{provider}\" not found in {}",
                    models_source.display()
                )
            })?;
        let mut providers = Map::new();
        providers.insert(provider.to_string(), entry);
        let document = json!({ "providers": Value::Object(providers) });
        let path = self.agent_dir.join("models.json");
        std::fs::write(&path, serde_json::to_string_pretty(&document).unwrap())
            .map_err(|error| format!("cannot write {}: {error}", path.display()))?;
        Ok(())
    }

    /// Scratch settings.json: the jev_memory_retrieval fixture keys plus the
    /// `memory` block derived from the manifest.
    fn write_settings_json(&self, settings: &ManifestSettings) -> Result<(), String> {
        let mut auto_refine = json!({"enabled": false});
        if let Some(override_value) = &settings.auto_refine {
            if let (Some(target), Some(source)) = (auto_refine.as_object_mut(), override_value.as_object()) {
                for (key, value) in source {
                    target.insert(key.clone(), value.clone());
                }
            }
        }
        let memory = manifest_memory_settings(settings);
        let document = json!({
            "autoRefine": auto_refine,
            "retry": {"enabled": false},
            "compaction": {"enabled": false},
            "telemetryEnabled": false,
            "agentTracesEnabled": false,
            "quietStartup": true,
            "memory": memory,
        });
        let path = self.agent_dir.join("settings.json");
        std::fs::write(&path, serde_json::to_string_pretty(&document).unwrap())
            .map_err(|error| format!("cannot write {}: {error}", path.display()))?;
        Ok(())
    }

    /// Write import rows without a synthetic clock. New source authority is opt-in.
    fn write_session_files(
        &self,
        env: &EnvSpec,
        mode: CaptureMode,
        evidence_timestamps: bool,
    ) -> Result<Vec<Value>, String> {
        validate_capture_env(env, mode)?;
        let mut index: Vec<Value> = Vec::new();
        for session in &env.sessions {
            let path = self.sessions_dir.join(format!("{}.jsonl", session.session_id));
            let mut lines: Vec<String> = Vec::new();
            let mut events: Vec<Value> = Vec::new();
            let mut previous_id: Option<String> = None;
            for (position, event) in session.events.iter().enumerate() {
                let row_id = format!("m_{:04}", position + 1);
                let (evidence_role, source_timestamp) = match mode {
                    CaptureMode::SourceAuthorityV1 => (
                        imported_event_role(env, event)?,
                        parse_event_timestamp(event.ts.as_ref())?,
                    ),
                    // Legacy role policy remains historical, but its invented dates never ground facts.
                    CaptureMode::LegacyUndated => ("user", None),
                };
                let timestamp = source_timestamp.filter(|_| evidence_timestamps);
                let mut message = json!({
                    "role": evidence_role,
                    "content": [{"type": "text", "text": event.text}],
                });
                if evidence_role == "toolResult" {
                    let tool = event.tool_result.as_ref().ok_or("missing tool_result descriptor")?;
                    message["toolCallId"] = json!(tool.tool_call_id);
                    message["toolName"] = json!(tool.tool_name);
                    message["isError"] = json!(tool.is_error);
                }
                let mut row = json!({
                    "type": "message",
                    "id": row_id,
                    "parentId": previous_id,
                    "message": message,
                });
                if let Some(timestamp) = timestamp {
                    row["timestamp"] = json!(timestamp);
                    row["message"]["timestamp"] = json!(timestamp);
                }
                lines.push(serde_json::to_string(&row).unwrap());
                events.push(json!({
                    "line": position + 1,
                    "row_id": row_id,
                    "ext_id": event.ext_id,
                    "role": event.role,
                    "evidence_role": evidence_role,
                    "source_authority": event.authority,
                    "source_ts": event.ts,
                    "source_timestamp_ms": source_timestamp,
                    "evidence_timestamp_ms": timestamp,
                    "evidence_timestamps_enabled": mode == CaptureMode::SourceAuthorityV1 && evidence_timestamps,
                }));
                previous_id = Some(row_id);
            }
            let body = lines.join("\n") + "\n";
            std::fs::write(&path, &body)
                .map_err(|error| format!("cannot write {}: {error}", path.display()))?;
            index.push(json!({
                "session_id": session.session_id,
                "file": path.to_string_lossy(),
                "source_rows_sha256": hash(&body),
                "capture_protocol": mode.protocol(),
                "events": events,
            }));
        }
        Ok(index)
    }
}


// Capture protocol is independent of question/scoring declarations and daemon capabilities.
const SOURCE_AUTHORITY_CAPTURE_PROTOCOL: &str = "optimus-memory-capture/temporal-authority/1.0.0";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CaptureMode {
    LegacyUndated,
    SourceAuthorityV1,
}

impl CaptureMode {
    fn protocol(self) -> &'static str {
        match self {
            Self::LegacyUndated => "legacy-unversioned-undated",
            Self::SourceAuthorityV1 => SOURCE_AUTHORITY_CAPTURE_PROTOCOL,
        }
    }
}

fn capture_mode(protocol: Option<&str>) -> Result<CaptureMode, String> {
    match protocol {
        None => Ok(CaptureMode::LegacyUndated),
        Some(SOURCE_AUTHORITY_CAPTURE_PROTOCOL) => Ok(CaptureMode::SourceAuthorityV1),
        Some(_) => Err("Unsupported explicit capture protocol".to_string()),
    }
}

fn parse_event_timestamp(value: Option<&Value>) -> Result<Option<i64>, String> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    let value = value.as_str().ok_or("Invalid event ts: expected a string or null")?;
    // Naive converter ISO fields retain their calendar values under a UTC convention.
    let datetime = chrono::DateTime::parse_from_rfc3339(value)
        .map(|datetime| datetime.with_timezone(&chrono::Utc))
        .or_else(|_| {
            chrono::NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f")
                .map(|datetime| datetime.and_utc())
        })
        .map_err(|_| "Invalid event ts: expected an ISO datetime with seconds".to_string())?;
    let timestamp = datetime.timestamp_millis();
    if timestamp == 0 || datetime.timestamp_subsec_nanos() >= 1_000_000_000 {
        return Err("Invalid event ts: epoch-zero sentinel or unsupported leap second".to_string());
    }
    Ok(Some(timestamp))
}

fn imported_event_role<'a>(env: &EnvSpec, event: &'a EventSpec) -> Result<&'a str, String> {
    if !matches!(event.role.as_str(), "user" | "assistant" | "toolResult") {
        return Err("Unsupported event role; refusing to promote it to user evidence".to_string());
    }
    if matches!(env.family.as_deref(), Some("locomo" | "locomo_plus"))
        && matches!(event.role.as_str(), "user" | "assistant")
    {
        Ok("user")
    } else {
        Ok(event.role.as_str())
    }
}

fn validate_capture_env(env: &EnvSpec, mode: CaptureMode) -> Result<(), String> {
    if mode == CaptureMode::LegacyUndated {
        if env.capture_protocol.is_some() || env.authority_revision.is_some() {
            return Err("Versioned capture input requires explicit captureProtocol opt-in".to_string());
        }
        return Ok(());
    }
    if env.capture_protocol.as_deref().is_some_and(|value| value != SOURCE_AUTHORITY_CAPTURE_PROTOCOL) {
        return Err("Environment capture protocol mismatch".to_string());
    }
    let lme_v2 = env.family.as_deref() == Some("lme_v2");
    if lme_v2 && (env.authority_revision != Some(1)
        || env.capture_protocol.as_deref() != Some(SOURCE_AUTHORITY_CAPTURE_PROTOCOL)
        || env.notes.get("axtree_cap_chars").and_then(Value::as_u64).is_none_or(|cap| cap == 0))
    {
        return Err("New LMEv2 capture requires authority_revision=1, the current capture protocol and an explicit positive axtree cap; regenerate inputs".to_string());
    }
    for session in &env.sessions {
        for (position, event) in session.events.iter().enumerate() {
            let context = |error: String| format!(
                "env {} session {} event {}: {error}", env.env_id, session.session_id, position + 1
            );
            imported_event_role(env, event).map_err(context)?;
            parse_event_timestamp(event.ts.as_ref()).map_err(context)?;
            match (&event.tool_result, event.role.as_str()) {
                (Some(tool), "toolResult") if !tool.tool_call_id.trim().is_empty()
                    && !tool.tool_name.trim().is_empty() => {},
                (None, "user" | "assistant") => {},
                _ => return Err(context("tool_result must contain nonempty native fields exactly on toolResult events".to_string())),
            }
            if lme_v2 {
                let valid_authority = match (event.role.as_str(), event.authority.as_deref()) {
                    ("assistant", Some("dataset_task_metadata" | "agent_decision")) => true,
                    ("toolResult", Some("browser_observation")) => event.tool_result.as_ref().is_some_and(|tool| {
                        tool.tool_name == "lme_v2_observation" && !tool.is_error
                            && event.ext_id.as_deref() == Some(tool.tool_call_id.as_str())
                    }),
                    _ => false,
                };
                if !valid_authority || event.ts.as_ref().is_some_and(|value| !value.is_null()) {
                    return Err(context("LMEv2 requires undated observation/decision/task-metadata authority; no user speaker or event time may be invented".to_string()));
                }
            }
        }
    }
    Ok(())
}

fn validate_capture_inputs(envs: &[EnvSpec], mode: CaptureMode) -> Result<(), String> {
    for env in envs {
        validate_capture_env(env, mode)?;
    }
    Ok(())
}

fn validate_capture_output(output: &Path, mode: CaptureMode) -> Result<(), String> {
    if mode == CaptureMode::SourceAuthorityV1 {
        for name in ["envs", "env.jsonl", "run.jsonl"] {
            match std::fs::symlink_metadata(output.join(name)) {
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
                _ => return Err("New capture protocol requires fresh output; do not resume or relabel old captures".to_string()),
            }
        }
    }
    Ok(())
}

fn capture_input_policy(manifest: &Manifest, env: &EnvSpec) -> Result<Value, String> {
    let mode = capture_mode(manifest.capture_protocol.as_deref())?;
    Ok(json!({
        "capture_protocol": mode.protocol(),
        "manifest_sha256": manifest.manifest_sha256,
        "family": env.family,
        "authority_revision": env.authority_revision,
        "axtree_cap_chars": env.notes.get("axtree_cap_chars"),
        "evidence_timestamps": mode == CaptureMode::SourceAuthorityV1 && manifest.settings.evidence_timestamps,
        "timestamp_source": if mode == CaptureMode::SourceAuthorityV1 { "event.ts_only" } else { "none_legacy_untrusted" },
        "naive_iso_policy": "preserve_calendar_fields_as_UTC",
        "synthetic_timestamp_policy": "never_written",
        "authority_policy": if mode == CaptureMode::SourceAuthorityV1 { "external_human_locomo_labels_only_other_roles_preserved" } else { "historical_blanket_user_not_repaired_authority" },
        "evidence_limit": "Capture metadata binds inputs and transport policy, not factual truth or real-world timezone authenticity.",
    }))
}


/// Every settings key this adapter can express. The Original product silently
/// ignores unknown `memory` keys, so an unrecognized manifest settings key is
/// refused instead of dropped.
const SUPPORTED_SETTINGS_KEYS: [(&str, &str); 17] = [
    ("recallOn", "recall_on"),
    ("memoryLearning", "memory_learning"),
    ("answerMaxTokens", "answer_max_tokens"),
    ("answerThinkingLevel", "answer_thinking_level"),
    ("maxRecallChars", "max_recall_chars"),
    ("maxRecallEntries", "max_recall_entries"),
    ("maxExtractionTokens", "max_extraction_tokens"),
    ("maxImportChunkChars", "max_import_chunk_chars"),
    ("importRunRetries", "import_run_retries"),
    ("autoRefine", "auto_refine"),
    ("questionTimeoutS", "question_timeout_s"),
    ("ingestTimeoutS", "ingest_timeout_s"),
    ("importRunTimeoutS", "import_run_timeout_s"),
    ("disableTools", "disable_tools"),
    ("answerInstruction", "answer_instruction"),
    ("evidenceTimestamps", "evidence_timestamps"),
    ("skipIngest", "skip_ingest"),
];

/// Newer-product memory controls the Original product cannot express.
const UNSUPPORTED_NEWER_MEMORY_KEYS: [(&str, &str); 3] = [
    ("importInstructions", "import_instructions"),
    ("recallQueryDistillation", "recall_query_distillation"),
    ("recallRerank", "recall_rerank"),
];

fn check_supported_settings(value: &Value) -> Result<(), String> {
    let Some(settings) = value.as_object() else {
        return Ok(());
    };
    for key in settings.keys() {
        let key = key.as_str();
        for (camel, snake) in UNSUPPORTED_NEWER_MEMORY_KEYS {
            if key == camel || key == snake {
                return Err(format!(
                    "manifest settings key \"{key}\" is a newer-product memory control the Original product cannot express; refusing to silently ignore it"
                ));
            }
        }
        if !SUPPORTED_SETTINGS_KEYS
            .iter()
            .any(|(camel, snake)| key == *camel || key == *snake)
        {
            return Err(format!(
                "unsupported manifest settings key \"{key}\"; the Original control adapter refuses silently ignored settings"
            ));
        }
    }
    Ok(())
}

fn check_no_frozen_memory(value: &Value) -> Result<(), String> {
    let Some(envs) = value.get("envs").and_then(Value::as_array) else {
        return Ok(());
    };
    for env in envs {
        for key in ["frozenMemory", "frozen_memory"] {
            if env.get(key).is_some() {
                return Err(format!(
                    "environment supplies \"{key}\"; frozen-store hydration is not implemented in the Original control adapter, fresh capture is required"
                ));
            }
        }
    }
    Ok(())
}

fn parse_manifest(raw: &str) -> Result<Manifest, String> {
    let mut manifest: Manifest = serde_json::from_str(raw).map_err(|error| error.to_string())?;
    manifest.manifest_sha256 = hash(raw);
    let value: Value = serde_json::from_str(raw).map_err(|error| error.to_string())?;
    check_supported_settings(&value["settings"])?;
    check_no_frozen_memory(&value)?;
    for question in &manifest.questions {
        question_declaration(question)?;
    }
    Ok(manifest)
}

fn declared_string<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<String>, D::Error> {
    // A missing field is legacy-compatible; an explicitly supplied null is not a declaration.
    String::deserialize(deserializer).map(Some)
}

fn question_declaration(question: &QuestionSpec) -> Result<Option<Value>, String> {
    match (
        question.question_protocol.as_deref(),
        question.scoring_protocol.as_deref(),
        question.question_sha256.as_deref(),
    ) {
        (None, None, None) => Ok(None),
        (Some(question_protocol), Some(scoring_protocol), Some(question_sha256)) => {
            if [question_protocol, scoring_protocol]
                .iter()
                .any(|value| value.trim().is_empty() || value.chars().count() > 256)
                || question_sha256.len() != 64
                || !question_sha256.bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("question declaration requires nonblank protocols of at most 256 Unicode scalar values and a 64-hex SHA256".to_string());
            }
            // Opaque caller metadata: never derive this hash from gold or claim presentation proof.
            Ok(Some(json!({
                "question_protocol": question_protocol,
                "scoring_protocol": scoring_protocol,
                "question_sha256": question_sha256,
                "question_binding_source": "caller_declared_scoring_question",
            })))
        }
        _ => Err(
            "question_protocol, scoring_protocol, and question_sha256 must be supplied together"
                .to_string(),
        ),
    }
}

// ---------------------------------------------------------------------------
// Feature realization (offline settings checks, not LLM call evidence)
// ---------------------------------------------------------------------------

fn manifest_memory_settings(settings: &ManifestSettings) -> Value {
    let mut memory = json!({"recall": settings.recall_on});
    if let Some(learning) = settings.memory_learning {
        memory["learning"] = json!(learning);
    }
    if let Some(max_chars) = settings.max_recall_chars {
        memory["maxRecallChars"] = json!(max_chars);
    }
    if let Some(max_entries) = settings.max_recall_entries {
        memory["maxRecallEntries"] = json!(max_entries);
    }
    if let Some(max_extraction_tokens) = settings.max_extraction_tokens {
        memory["maxExtractionTokens"] = json!(max_extraction_tokens);
    }
    if let Some(max_import_chunk_chars) = settings.max_import_chunk_chars {
        memory["maxImportChunkChars"] = json!(max_import_chunk_chars);
    }
    memory
}

fn memory_feature_realization(
    requested: &ManifestSettings,
    effective: &MemorySettings,
    phase: &str,
) -> Result<Value, String> {
    let requested_memory = manifest_memory_settings(requested);
    // Keep the snapshot limited to memory behavior; never serialize sharing credentials.
    let effective_memory = json!({
        "recall": effective.recall,
        "learning": effective.learning,
        "maxRecallChars": effective.max_recall_chars,
        "maxRecallEntries": effective.max_recall_entries,
        "maxExtractionTokens": effective.max_extraction_tokens,
        "maxImportBytes": effective.max_import_bytes,
        "maxImportChunkChars": effective.max_import_chunk_chars,
        "maxImportChunksPerRun": effective.max_import_chunks_per_run,
    });
    let mut mismatches = Vec::new();
    for (key, value) in requested_memory
        .as_object()
        .expect("memory settings object")
    {
        if effective_memory.get(key) != Some(value) {
            mismatches.push(format!(
                "memory.{key}: requested={value}, expected={value}, effective={}",
                effective_memory[key]
            ));
        }
    }
    if !mismatches.is_empty() {
        return Err(format!(
            "memory feature realization failed before {phase}: {}; refusing benchmark work with mismatched settings",
            mismatches.join("; ")
        ));
    }
    Ok(json!({
        "schema_version": 1,
        "driver_version": env!("CARGO_PKG_VERSION"),
        // The product fingerprint excludes example sources; pin this driver separately.
        "driver_source_sha256": hash(include_str!("memory_bench.rs")),
        "build_fingerprint": env!("OPTIMUS_BUILD_FINGERPRINT"),
        "settings_source": "MemoryService.store.settings()",
        "settings_observed_before": phase,
        "settings_check": "matched",
        "requested_memory": requested_memory,
        "requested_includes_manifest_defaults": true,
        "effective_memory": effective_memory,
        "recall_hook_observation": null,
        "llm_call_evidence": {
            "recallQueryDistillation": "unavailable_original_product_no_recall_helpers",
            "recallRerank": "unavailable_original_product_no_recall_helpers",
        },
        "llm_helper_observations": null,
        "llm_helper_usage_coverage": null,
        "evidence_limit": "Settings prove configuration only. The Original product has no recall helpers, so their call evidence is unavailable (not zero) and their observations are null. Recall diagnostics report ids/chars/latencyMs only. Missing fields are unobserved; null recall_hook_observation means not collected.",
    }))
}

fn check_memory_feature_realization(
    scratch: &EnvScratch,
    settings: &ManifestSettings,
    phase: &str,
) -> Result<Value, String> {
    let service = MemoryService::new(
        &scratch.cwd.to_string_lossy(),
        &scratch.agent_dir.to_string_lossy(),
        None,
    )?;
    memory_feature_realization(settings, &service.store.settings(), phase)
        .map_err(|error| format!("[env {}] {error}", scratch.env_id))
}

fn current_question_branch(
    branch: Vec<Map<String, Value>>,
    before_prompt: &[Map<String, Value>],
) -> Vec<Map<String, Value>> {
    let prior_ids: HashSet<&str> = before_prompt
        .iter()
        .filter_map(|entry| {
            entry
                .get("id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
        })
        .collect();
    branch
        .into_iter()
        .enumerate()
        .filter(|(index, entry)| {
            match entry
                .get("id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
            {
                Some(id) => !prior_ids.contains(id),
                None => *index >= before_prompt.len(),
            }
        })
        .map(|(_, entry)| entry)
        .collect()
}

fn recall_diagnostics(branch: &[Map<String, Value>]) -> Vec<&Map<String, Value>> {
    let mut seen_ids = HashSet::new();
    branch
        .iter()
        .filter(|entry| {
            entry.get("type").and_then(Value::as_str) == Some("custom")
                && entry.get("customType").and_then(Value::as_str)
                    == Some(MEMORY_DIAGNOSTIC_CUSTOM_TYPE)
        })
        .filter(|entry| {
            entry
                .get("id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
                .is_none_or(|id| seen_ids.insert(id))
        })
        .filter_map(|entry| entry.get("data").and_then(Value::as_object))
        .filter(|data| data.get("operation").and_then(Value::as_str) == Some("recall"))
        .collect()
}

fn recall_hook_observation(diagnostics: &[&Map<String, Value>]) -> Value {
    json!({
        "source": MEMORY_DIAGNOSTIC_CUSTOM_TYPE,
        "diagnostic_count": diagnostics.len(),
        "failed_diagnostic_count": diagnostics.iter().filter(|data| {
            data.get("status").and_then(Value::as_str) == Some("failed")
        }).count(),
        "last_reported_status": diagnostics.last().and_then(|data| {
            data.get("status").and_then(Value::as_str).filter(|status| {
                matches!(*status, "failed" | "success")
            })
        }),
        "cache_hit_by_diagnostic": diagnostics.iter().map(|data| {
            data.get("cacheHit").and_then(Value::as_bool)
        }).collect::<Vec<_>>(),
        "status_note": "Successful recall diagnostics currently omit status. An absent diagnostic provides no execution evidence.",
    })
}

fn record_recall_observations(realization: &mut Value, diagnostics: &[&Map<String, Value>]) {
    realization["recall_hook_observation"] = recall_hook_observation(diagnostics);
    // The Original product has no recall helpers: their telemetry stays the
    // declared unavailability, never a fabricated zero.
}

fn question_usage_coverage(record: &Value) -> Value {
    json!({
        "schema_version": 2,
        "answer": "last_assistant_message_only_when_reported",
        "answer_presence_aware_usage": false,
        "recall_helpers": null,
        "recall_helper_tokens": null,
        "recall_helper_latency_ms": null,
        "total_model_tokens": null,
        "cost_complete": false,
        "status": "incomplete_observations_only_no_cost_total",
        "aggregation": "none; current-question diagnostic entries are deduplicated by entry id when available; idless observations are not unique-call evidence",
        "limitations": "The Original product has no recall helpers, so recall helper telemetry is null (unavailable, not zero). Final-answer usage is normalized last-message-only and does not prove observed zero or full usage coverage. No ingest or preflight cost total is claimed.",
        "record_has_error": record.get("error").map(|error| !error.is_null()).unwrap_or(false),
    })
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn iso_now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

fn append_jsonl(path: &Path, record: &Value) -> Result<(), String> {
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| format!("cannot open {}: {error}", path.display()))?;
    let mut line = serde_json::to_string(record).unwrap();
    line.push('\n');
    file.write_all(line.as_bytes())
        .and_then(|_| file.flush())
        .map_err(|error| format!("cannot append to {}: {error}", path.display()))?;
    Ok(())
}

fn read_jsonl_keys(path: &Path, key_fields: &[&str]) -> Result<HashSet<String>, String> {
    let mut keys = HashSet::new();
    if !path.exists() {
        return Ok(keys);
    }
    let raw = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    for line in raw.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let key: Vec<String> = key_fields
            .iter()
            .map(|field| {
                value
                    .get(*field)
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string()
            })
            .collect();
        keys.insert(key.join("\u{1}"));
    }
    Ok(keys)
}

fn record_key(fields: &[&str]) -> String {
    fields.join("\u{1}")
}

/// Resolve (api_key, headers) for a model through a registry without blocking
/// a Tokio worker on the synchronous registry mutex.
async fn registry_auth(
    registry: Arc<Mutex<ModelRegistry>>,
    model: pi_ai::types::Model,
) -> Result<(String, Option<indexmap::IndexMap<String, String>>), String> {
    let handle = tokio::runtime::Handle::current();
    tokio::task::spawn_blocking(move || {
        let mut guard = registry
            .lock()
            .map_err(|_| "model registry poisoned".to_string())?;
        let auth = handle.block_on(guard.get_api_key_and_headers(&model));
        if !auth.ok {
            return Err(auth
                .error
                .unwrap_or_else(|| "no API key available for the model".to_string()));
        }
        let api_key = auth
            .api_key
            .filter(|key| !key.is_empty())
            .ok_or_else(|| "no API key available for the model".to_string())?;
        Ok((api_key, auth.headers))
    })
    .await
    .map_err(|error| error.to_string())?
}

// ---------------------------------------------------------------------------
// Preflight (fail fast when the real endpoint is unreachable)
// ---------------------------------------------------------------------------

async fn preflight(
    output_dir: &Path,
    models_source: &Path,
    model_spec: &ModelSpec,
    settings: &ManifestSettings,
) -> Result<(), String> {
    let scratch_root = output_dir.join("_preflight");
    std::fs::create_dir_all(&scratch_root)
        .map_err(|error| format!("cannot create {}: {error}", scratch_root.display()))?;
    let scratch = EnvScratch {
        env_id: "_preflight".to_string(),
        root: scratch_root.clone(),
        agent_dir: scratch_root.join("agent"),
        cwd: scratch_root.join("workspace"),
        session_artifact_dir: scratch_root.join("session-artifacts"),
        sessions_dir: scratch_root.join("sessions"),
    };
    for dir in [&scratch.agent_dir, &scratch.cwd] {
        std::fs::create_dir_all(dir)
            .map_err(|error| format!("cannot create {}: {error}", dir.display()))?;
    }
    scratch.write_settings_json(settings)?;
    let realization =
        check_memory_feature_realization(&scratch, settings, "connectivity preflight")?;
    eprintln!(
        "[preflight] memory settings matched: requested={} effective={} (configuration only, not LLM call evidence)",
        realization["requested_memory"], realization["effective_memory"]
    );
    scratch.write_models_json(models_source, &model_spec.provider)?;

    let registry = Arc::new(Mutex::new(ModelRegistry::create(
        AuthStorage::create(Some(scratch.agent_dir.join("auth.json").to_string_lossy().into_owned()), None),
        Some(scratch.agent_dir.join("models.json").to_string_lossy().into_owned()),
    )));
    let model = registry
        .lock()
        .map_err(|_| "model registry poisoned".to_string())?
        .find(&model_spec.provider, &model_spec.id)
        .ok_or_else(|| {
            format!(
                "model {}/{} not found in the scratch registry (provider entry copy failed?)",
                model_spec.provider, model_spec.id
            )
        })?;
    let (api_key, headers) = registry_auth(registry.clone(), model.clone()).await?;
    let context = pi_ai::types::Context {
        system_prompt: Some("You are a connectivity probe. Reply with the single word OK.".to_string()),
        messages: vec![pi_ai::types::Message::User(pi_ai::types::UserMessage::new(
            pi_ai::types::UserContent::Text("ping".to_string()),
            1_700_000_000_000,
        ))],
        tools: None,
    };
    let options = pi_ai::types::SimpleStreamOptions {
        stream: pi_ai::types::StreamOptions {
            max_tokens: Some(16.0),
            api_key: Some(api_key),
            headers,
            ..Default::default()
        },
        ..Default::default()
    };
    let reply = tokio::time::timeout(
        Duration::from_secs(120),
        pi_ai::stream::complete_simple(&model, &context, Some(&options)),
    )
    .await
    .map_err(|_| "preflight timed out after 120s".to_string())?;
    if reply.stop_reason.as_str() == "error" {
        return Err(format!(
            "preflight completion failed: {}",
            reply.error_message.unwrap_or_else(|| "unknown provider error".to_string())
        ));
    }
    eprintln!("[preflight] {}/{} reachable", model_spec.provider, model_spec.id);
    Ok(())
}

// ---------------------------------------------------------------------------
// Ingest (import mode)
// ---------------------------------------------------------------------------

struct IngestOutcome {
    record: Value,
}

async fn ingest_env(
    scratch: &EnvScratch,
    env: &EnvSpec,
    manifest: &Manifest,
    event_index: &[Value],
) -> Result<IngestOutcome, String> {
    let started = Instant::now();
    let realization = check_memory_feature_realization(scratch, &manifest.settings, "ingest")?;
    let cwd_str = scratch.cwd.to_string_lossy().into_owned();
    let agent_dir_str = scratch.agent_dir.to_string_lossy().into_owned();
    let session_artifact_str = scratch
        .session_artifact_dir
        .to_string_lossy()
        .into_owned();

    let get_model_info: HostRequestHandler = {
        let provider = manifest.model.provider.clone();
        let id = manifest.model.id.clone();
        Arc::new(move |_payload| {
            let provider = provider.clone();
            let id = id.clone();
            Box::pin(async move {
                Ok(json!({"provider": provider, "id": id}))
            })
        })
    };
    let handlers = create_memory_host_handlers(
        cwd_str.clone(),
        Some(agent_dir_str.clone()),
        Some(session_artifact_str.clone()),
        Some(get_model_info),
    );
    let memory_request = handlers
        .get("memory.request")
        .ok_or("memory.request handler missing")?
        .clone();

    let mut job_ids: Vec<String> = Vec::new();
    let mut total_usage_input = 0.0f64;
    let mut total_usage_output = 0.0f64;
    let mut chunk_count = 0usize;

    for session in &env.sessions {
        if session.events.is_empty() {
            eprintln!(
                "[env {}] session {} has no events; skipping import",
                env.env_id, session.session_id
            );
            continue;
        }
        let source = scratch
            .sessions_dir
            .join(format!("{}.jsonl", session.session_id))
            .to_string_lossy()
            .into_owned();

        // import_prepare
        let prepared = tokio::time::timeout(
            Duration::from_secs(60),
            call_memory(&memory_request, json!({"action": "import_prepare", "path": source})),
        )
        .await
        .map_err(|_| format!("import_prepare timed out for {}", session.session_id))?
        .map_err(|error| format!("import_prepare failed: {error}"))?;
        let job = prepared
            .get("result")
            .cloned()
            .ok_or("import_prepare returned no result")?;
        let job_id = job
            .get("id")
            .and_then(Value::as_str)
            .ok_or("import_prepare returned no job id")?
            .to_string();
        job_ids.push(job_id.clone());

        // import_run until preview (each call processes at most
        // maxImportChunksPerRun chunks; the job checkpoints after every chunk,
        // so a failed invocation resumes from the last completed chunk).
        let mut invocations = 0usize;
        let max_invocations = 512usize;
        let mut consecutive_failures = 0u32;
        loop {
            invocations += 1;
            if invocations > max_invocations {
                return Err(format!(
                    "import_run for job {job_id} exceeded {max_invocations} invocations"
                ));
            }
            let outcome = tokio::time::timeout(
                Duration::from_secs(manifest.settings.import_run_timeout_s),
                call_memory(&memory_request, json!({"action": "import_run", "id": job_id})),
            )
            .await;
            let run = match outcome {
                Ok(Ok(value)) => value,
                Ok(Err(error)) => {
                    let reason = error;
                    consecutive_failures += 1;
                    eprintln!(
                        "[env {}] import_run invocation {invocations} failed ({consecutive_failures} consecutive): {reason}",
                        env.env_id
                    );
                    if consecutive_failures > manifest.settings.import_run_retries {
                        return Err(format!(
                            "import_run for job {job_id} failed {consecutive_failures} times in a row: {reason}"
                        ));
                    }
                    tokio::time::sleep(Duration::from_secs(2)).await;
                    continue;
                }
                Err(_) => {
                    let reason = format!(
                        "import_run timed out for job {job_id} ({}s)",
                        manifest.settings.import_run_timeout_s
                    );
                    consecutive_failures += 1;
                    eprintln!(
                        "[env {}] import_run invocation {invocations} failed ({consecutive_failures} consecutive): {reason}",
                        env.env_id
                    );
                    if consecutive_failures > manifest.settings.import_run_retries {
                        return Err(format!(
                            "import_run for job {job_id} failed {consecutive_failures} times in a row: {reason}"
                        ));
                    }
                    tokio::time::sleep(Duration::from_secs(2)).await;
                    continue;
                }
            };
            consecutive_failures = 0;
            let result = run.get("result").cloned().unwrap_or(Value::Null);
            let status = result.get("status").and_then(Value::as_str).unwrap_or("");
            if status == "preview" || status == "applied" {
                if let Some(usage) = result.get("usage") {
                    total_usage_input += usage.get("input").and_then(Value::as_f64).unwrap_or(0.0);
                    total_usage_output += usage.get("output").and_then(Value::as_f64).unwrap_or(0.0);
                }
                if let Some(chunks) = result.get("chunks").and_then(Value::as_array) {
                    chunk_count += chunks.len();
                }
                break;
            }
        }

        // import_apply (revision read fresh right before applying)
        let service = MemoryService::new(&cwd_str, &agent_dir_str, Some(session_artifact_str.clone()))?;
        let revision = service.store.read()?.memory.revision;
        let applied = tokio::time::timeout(
            Duration::from_secs(120),
            call_memory(
                &memory_request,
                json!({"action": "import_apply", "id": job_id, "revision": revision}),
            ),
        )
        .await
        .map_err(|_| format!("import_apply timed out for job {job_id}"))?
        .map_err(|error| format!("import_apply failed for job {job_id}: {error}"))?;
        let status = applied
            .get("result")
            .and_then(|result| result.get("status"))
            .and_then(Value::as_str)
            .unwrap_or("");
        if status != "applied" {
            return Err(format!(
                "import_apply for job {job_id} returned status {status:?}"
            ));
        }
    }

    // Snapshot the store plus the harness state files.
    let service = MemoryService::new(&cwd_str, &agent_dir_str, Some(session_artifact_str.clone()))?;
    let document = service.store.read()?;
    let store_snapshot = serde_json::to_value(&document.entries)
        .map_err(|error| format!("cannot serialize store snapshot: {error}"))?;
    let global_harness = read_json_file(
        &scratch.agent_dir.join("harness").join("harness_state.json"),
    );
    let session_harness = read_json_file(
        &scratch
            .session_artifact_dir
            .join("harness")
            .join("harness_state.json"),
    );
    let entry_count: usize = document
        .entries
        .values()
        .map(|bucket| bucket.len())
        .sum();

    let record = json!({
        "run_id": manifest.run_id,
        "benchmark": manifest.benchmark,
        "variant": manifest.variant,
        "env_id": env.env_id,
        "ingest_mode": "import",
        "capture_input_policy": capture_input_policy(manifest, env)?,
        "answer_thinking_control": answer_thinking_control(&manifest.settings, None, None)?,
        "memory_feature_realization": realization,
        "project_id": service.store.project.id,
        "model": {"provider": manifest.model.provider, "id": manifest.model.id},
        "revision": document.memory.revision,
        "entry_count": entry_count,
        "store_snapshot": store_snapshot,
        "global_harness": global_harness,
        "session_harness": session_harness,
        "ingest_latency_ms": started.elapsed().as_millis() as u64,
        "ingest_llm_calls": chunk_count,
        "ingest_usage": {"input": total_usage_input, "output": total_usage_output},
        "job_ids": job_ids,
        "ground_truth_refs": env.ground_truth_refs,
        "sessions": event_index,
        "ts": iso_now(),
    });
    Ok(IngestOutcome { record })
}

async fn call_memory(
    request: &HostRequestHandler,
    payload: Value,
) -> Result<Value, String> {
    request(payload)
        .await
        .map_err(|error| error.to_string())
}

fn read_json_file(path: &Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or(Value::Null)
}

// ---------------------------------------------------------------------------
// Question runner
// ---------------------------------------------------------------------------

struct QuestionOutcome {
    record: Value,
}

/// Bounded output cap for the main answer, matched to the validated driver.
fn apply_answer_max_tokens(
    model: &mut pi_ai::types::Model,
    cap: Option<u32>,
) -> Result<(), String> {
    if let Some(cap) = cap {
        if cap == 0 || cap > 32000 {
            return Err("answerMaxTokens must be between 1 and 32000".to_string());
        }
        model.max_tokens = f64::from(cap);
    }
    Ok(())
}

/// Identical prompt bytes to the validated driver's answerInstruction wrapper.
fn effective_question_text(instruction: Option<&str>, question: &str) -> String {
    match instruction {
        Some(instruction) => format!("{instruction}\n\nQuestion: {question}"),
        None => question.to_string(),
    }
}

fn answer_session_creation(
    settings: &ManifestSettings,
    model: pi_ai::types::Model,
) -> Result<AgentSessionCreationOptions, String> {
    if let Some(requested) = settings.answer_thinking_level {
        let name = requested.as_str();
        if !pi_ai::models::get_supported_thinking_levels(&model)
            .iter()
            .any(|level| level == name)
            || pi_ai::models::clamp_thinking_level(&model, name) != name
        {
            return Err("answerThinkingLevel is unsupported by the resolved model; refusing native thinking-level clamping".to_string());
        }
    }
    Ok(AgentSessionCreationOptions {
        model: Some(model),
        thinking_level: settings.answer_thinking_level,
        no_tools: if settings.disable_tools {
            Some("all".to_string())
        } else {
            None
        },
        prewarm_ipython_kernel: Some(false),
        telemetry_disabled: Some(true),
        ..Default::default()
    })
}

fn answer_thinking_control(
    settings: &ManifestSettings,
    effective: Option<ThinkingLevel>,
    model_reasoning_capability: Option<bool>,
) -> Result<Value, String> {
    if let (Some(requested), Some(effective)) = (settings.answer_thinking_level, effective) {
        if requested != effective {
            return Err("answerThinkingLevel differs from the native session effective level; refusing question prompt".to_string());
        }
    }
    Ok(json!({
        "requested": settings.answer_thinking_level,
        "effective": effective,
        "effective_source": effective.map(|_| "AgentSession.thinking_level()"),
        "effective_observed_before": effective.map(|_| "question_prompt"),
        "model_reasoning_capability": model_reasoning_capability,
        "status": if effective.is_none() { "not_observed_before_question" }
            else if settings.answer_thinking_level.is_some() { "matched_explicit_request" }
            else { "native_default_path_observed" },
        "scope": "main_answer_only",
        "recall_helper_override": false,
        "provider_payload_observed": false,
        "provider_reasoning_disabled": null,
        "evidence_limit": "Native main-session control only; not proof of an emitted provider payload or backend reasoning behavior. The Original product has no recall helpers to override.",
    }))
}

#[allow(clippy::too_many_arguments)]
async fn run_question(
    scratch: &EnvScratch,
    manifest: &Manifest,
    question: &QuestionSpec,
) -> Result<QuestionOutcome, String> {
    let started = Instant::now();
    let declaration = question_declaration(question)?;
    let realization = check_memory_feature_realization(scratch, &manifest.settings, "question")?;
    let cwd_str = scratch.cwd.to_string_lossy().into_owned();
    let agent_dir_str = scratch.agent_dir.to_string_lossy().into_owned();

    let settings = Arc::new(Mutex::new(SettingsManager::create(
        &cwd_str,
        Some(&agent_dir_str),
    )));
    let registry = Arc::new(Mutex::new(ModelRegistry::create(
        AuthStorage::create(
            Some(scratch.agent_dir.join("auth.json").to_string_lossy().into_owned()),
            None,
        ),
        Some(scratch.agent_dir.join("models.json").to_string_lossy().into_owned()),
    )));
    let mut model = {
        let guard = registry
            .lock()
            .map_err(|_| "model registry poisoned".to_string())?;
        guard
            .find(&manifest.model.provider, &manifest.model.id)
            .ok_or_else(|| {
                format!(
                    "model {}/{} not found in the scratch registry",
                    manifest.model.provider, manifest.model.id
                )
            })?
    };

    apply_answer_max_tokens(&mut model, manifest.settings.answer_max_tokens)?;
    let creation = answer_session_creation(&manifest.settings, model)?;

    let services = create_agent_session_services(CreateAgentSessionServicesOptions {
        cwd: cwd_str.clone(),
        agent_dir: Some(agent_dir_str.clone()),
        auth_storage: None,
        settings_manager: Some(settings.clone()),
        model_registry: Some(registry),
        extension_flag_values: None,
        no_builtin_herdr_reporter: Some(true),
        telemetry_disabled: Some(true),
        resource_loader_options: Some(DefaultResourceLoaderOptions {
            cwd: cwd_str.clone(),
            agent_dir: agent_dir_str.clone(),
            no_extensions: true,
            extension_factories: vec![create_memory_extension(
                agent_dir_str.clone(),
                settings.clone(),
            )],
            no_prompt_templates: true,
            no_themes: true,
            no_context_files: true,
            bundled_skills_dir: Some(None),
            ..Default::default()
        }),
    })
    .await
    .map_err(|error| format!("create_agent_session_services failed: {error}"))?;

    let session_manager = Arc::new(Mutex::new(
        SessionManager::in_memory(Some(&cwd_str), Some(&agent_dir_str))
            .map_err(|error| format!("in-memory session manager failed: {error}"))?,
    ));
    let created = create_agent_session_from_services(CreateAgentSessionFromServicesOptions {
        services: Arc::new(services),
        session_manager,
        session_start_event: None,
        creation,
    })
    .await
    .map_err(|error| format!("create_agent_session_from_services failed: {error}"))?;
    let session = created.session;
    let thinking_control = match answer_thinking_control(
        &manifest.settings,
        Some(session.thinking_level()),
        session.model().map(|model| model.reasoning),
    ) {
        Ok(control) => control,
        Err(error) => {
            session.dispose();
            return Err(error);
        }
    };

    // Prompt + wait, bounded by the per-question timeout.
    // Optional debug: dump the exact system prompt the question session uses.
    if std::env::var("MEMORY_BENCH_DUMP_SYSTEM_PROMPT").is_ok() {
        let _ = std::fs::write(
            scratch.root.join("system_prompt_debug.txt"),
            session.system_prompt(),
        );
    }

    let effective_question = effective_question_text(
        manifest.settings.answer_instruction.as_deref(),
        &question.question,
    );
    let branch_before_prompt = session
        .session_manager
        .lock()
        .map_err(|_| "session manager poisoned".to_string())?
        .get_branch(None);
    let prompt_result = tokio::time::timeout(
        Duration::from_secs(manifest.settings.question_timeout_s),
        async {
            session
                .prompt_and_wait(&effective_question, None)
                .await
                .map_err(|error| format!("prompt failed: {error}"))?;
            session
                .wait_for_headless_idle()
                .await
                .map_err(|error| format!("wait_for_headless_idle failed: {error}"))?;
            Ok::<(), String>(())
        },
    )
    .await;

    let mut record = json!({
        "run_id": manifest.run_id,
        "benchmark": manifest.benchmark,
        "variant": manifest.variant,
        "env_id": question.env_id,
        "qid": question.qid,
        "question": question.question,
        "answer_thinking_control": thinking_control,
        "memory_feature_realization": realization,
        "model": {"provider": manifest.model.provider, "id": manifest.model.id},
        "answer": question.answer,
        "evidence": question.evidence,
        "category": question.category,
        "eval": question.eval,
        "ts": iso_now(),
    });

    if let Some(Value::Object(fields)) = declaration {
        record.as_object_mut().expect("question record").extend(fields);
    }

    // Snapshot completed diagnostics even if the answer failed or timed out.
    // The Original product has no recall helpers, so only the recall hook is
    // observable; an absent diagnostic is never zero usage.
    let branch = {
        let manager = session
            .session_manager
            .lock()
            .map_err(|_| "session manager poisoned".to_string())?;
        manager.get_branch(None)
    };
    let branch = current_question_branch(branch, &branch_before_prompt);
    let diagnostics = recall_diagnostics(&branch);
    record_recall_observations(&mut record["memory_feature_realization"], &diagnostics);
    let diagnostic = diagnostics.last().copied();
    match diagnostic {
        Some(data) => {
            record["recall_ids"] = data.get("ids").cloned().unwrap_or(Value::Null);
            record["recall_chars"] = data.get("chars").cloned().unwrap_or(Value::Null);
            record["recall_latency_ms"] = data.get("latencyMs").cloned().unwrap_or(Value::Null);
        }
        None => {
            record["recall_ids"] = json!([]);
            record["recall_chars"] = json!(0);
            record["recall_latency_ms"] = Value::Null;
        }
    }

    match prompt_result {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            record["error"] = json!(error);
            record["latency_ms"] = json!(started.elapsed().as_millis() as u64);
            session.dispose();
            return Ok(QuestionOutcome { record });
        }
        Err(_) => {
            record["error"] = json!(format!(
                "question timed out after {}s",
                manifest.settings.question_timeout_s
            ));
            record["latency_ms"] = json!(started.elapsed().as_millis() as u64);
            session.dispose();
            return Ok(QuestionOutcome { record });
        }
    }

    // Final assistant answer + usage.
    let assistant = session.agent.find_last_assistant_message();
    let answer_text = session
        .messages()
        .iter()
        .rev()
        .find_map(|message| {
            let text = read_assistant_text(message);
            if text.trim().is_empty() {
                None
            } else {
                Some(text)
            }
        })
        .unwrap_or_default();
    if let Some(assistant) = &assistant {
        record["stop_reason"] = json!(assistant.stop_reason.as_str());
        if let Some(error_message) = &assistant.error_message {
            record["error"] = json!(error_message);
        }
        record["usage"] = json!({
            "input_tokens": assistant.usage.input,
            "output_tokens": assistant.usage.output,
            "cache_read": assistant.usage.cache_read,
            "cache_write": assistant.usage.cache_write,
            "total_tokens": assistant.usage.total_tokens,
        });
    }
    record["answer_text"] = json!(answer_text);

    session.dispose();

    // Direct retrieval (no LLM): full ranked search + rendered recall.
    let service = MemoryService::new(&cwd_str, &agent_dir_str, None)?;
    let hits = service.search(&question.question, false);
    let ranked: Vec<Value> = hits
        .iter()
        .take(50)
        .map(|hit| {
            json!({
                "id": hit.id,
                "scope": hit.scope.as_str(),
                "title": hit.entry.title,
                "score": hit.score,
                "matched": hit.matched,
                "version": hit.entry.version,
            })
        })
        .collect();
    let direct = service.recall(&question.question);
    record["search_ranked"] = json!(ranked);
    record["direct_recall_ids"] = json!(direct.ids);
    record["direct_recall_chars"] = json!(direct.chars);
    record["latency_ms"] = json!(started.elapsed().as_millis() as u64);

    Ok(QuestionOutcome { record })
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 2 {
        eprintln!("usage: memory_bench <manifest.json>");
        std::process::exit(2);
    }
    let manifest_path = PathBuf::from(&args[1]);
    let raw = std::fs::read_to_string(&manifest_path).unwrap_or_else(|error| {
        eprintln!(
            "error: cannot read manifest {}: {error}",
            manifest_path.display()
        );
        std::process::exit(2);
    });
    let manifest = parse_manifest(&raw).unwrap_or_else(|error| {
        eprintln!("error: invalid manifest JSON: {error}");
        std::process::exit(2);
    });
    let isolation = BenchmarkIsolation::install_before_runtime(Path::new(&manifest.output_dir))
        .unwrap_or_else(|error| {
            eprintln!("error: {error}");
            std::process::exit(1);
        });
    let models_source = explicit_models_source(std::env::var_os("MEMORY_BENCH_MODELS_JSON"))
        .unwrap_or_else(|error| {
            eprintln!("error: {error}");
            std::process::exit(2);
        });

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .enable_all()
        .build()
        .expect("tokio runtime");

    if let Err(error) = runtime.block_on(run(manifest, &models_source, &isolation)) {
        eprintln!("error: {error}");
        std::process::exit(1);
    }
}

async fn run(
    manifest: Manifest,
    models_source: &Path,
    isolation: &BenchmarkIsolation,
) -> Result<(), String> {
    isolation.verify("benchmark startup")?;

    let output_dir = PathBuf::from(&manifest.output_dir);
    std::fs::create_dir_all(&output_dir)
        .map_err(|error| format!("cannot create output dir: {error}"))?;
    let run_path = output_dir.join("run.jsonl");
    let env_path = output_dir.join("env.jsonl");

    let done_questions = read_jsonl_keys(&run_path, &["run_id", "env_id", "qid"])?;
    let done_envs = read_jsonl_keys(&env_path, &["run_id", "env_id"])?;

    eprintln!(
        "[run {}] benchmark={} variant={} envs={} questions={} (resume: {} questions, {} envs done)",
        manifest.run_id,
        manifest.benchmark,
        manifest.variant,
        manifest.envs.len(),
        manifest.questions.len(),
        done_questions.len(),
        done_envs.len()
    );

    let capture_mode = capture_mode(manifest.capture_protocol.as_deref())?;
    if !manifest.settings.skip_ingest {
        validate_capture_inputs(&manifest.envs, capture_mode)?;
        validate_capture_output(&output_dir, capture_mode)?;
    }

    // Fail closed before any provider request, including the connectivity probe.
    let no_jev = isolation.verify("connectivity preflight")?;
    eprintln!("[preflight] noJev isolation verified: {no_jev}");
    preflight(&output_dir, models_source, &manifest.model, &manifest.settings).await?;

    // Environments: scratch + ingest + snapshot.
    let mut scratch_by_env: std::collections::HashMap<String, EnvScratch> =
        std::collections::HashMap::new();
    for (position, env) in manifest.envs.iter().enumerate() {
        let scratch = EnvScratch::prepare(&output_dir, env)?;
        scratch.write_models_json(models_source, &manifest.model.provider)?;
        scratch.write_settings_json(&manifest.settings)?;
        scratch_by_env.insert(env.env_id.clone(), scratch.clone());

        let key = record_key(&[&manifest.run_id, &env.env_id]);
        if manifest.settings.skip_ingest {
            eprintln!(
                "[env {}/{}] {} ingest skipped (skipIngest)",
                position + 1,
                manifest.envs.len(),
                env.env_id
            );
            continue;
        }
        if done_envs.contains(&key) {
            eprintln!(
                "[env {}/{}] {} already ingested (env.jsonl); skipping ingest",
                position + 1,
                manifest.envs.len(),
                env.env_id
            );
            continue;
        }
        eprintln!(
            "[env {}/{}] {} ingesting {} session(s)",
            position + 1,
            manifest.envs.len(),
            env.env_id,
            env.sessions.len()
        );
        let no_jev = isolation.verify("ingest")?;
        let event_index = scratch.write_session_files(env, capture_mode, manifest.settings.evidence_timestamps)?;
        let started = Instant::now();
        let mut outcome = tokio::time::timeout(
            Duration::from_secs(manifest.settings.ingest_timeout_s),
            ingest_env(&scratch, env, &manifest, &event_index),
        )
        .await
        .map_err(|_| {
            format!(
                "ingest for env {} timed out after {}s",
                env.env_id, manifest.settings.ingest_timeout_s
            )
        })??;
        outcome.record["noJev"] = json!(true);
        outcome.record["benchmark_isolation"] = no_jev;
        append_jsonl(&env_path, &outcome.record)?;
        eprintln!(
            "[env {}/{}] {} ingested in {}ms ({} entries)",
            position + 1,
            manifest.envs.len(),
            env.env_id,
            started.elapsed().as_millis(),
            outcome
                .record
                .get("entry_count")
                .and_then(Value::as_u64)
                .unwrap_or(0)
        );
    }

    // Questions.
    for (position, question) in manifest.questions.iter().enumerate() {
        let scratch = scratch_by_env.get(&question.env_id).ok_or_else(|| {
            format!(
                "question {} references unknown env {}",
                question.qid, question.env_id
            )
        })?;
        let key = record_key(&[&manifest.run_id, &question.env_id, &question.qid]);
        if done_questions.contains(&key) {
            eprintln!(
                "[q {}/{}] {} already recorded (run.jsonl); skipping",
                position + 1,
                manifest.questions.len(),
                question.qid
            );
            continue;
        }
        eprintln!(
            "[q {}/{}] {} asking ({} chars)",
            position + 1,
            manifest.questions.len(),
            question.qid,
            question.question.chars().count()
        );
        let no_jev = isolation.verify("question")?;
        let started = Instant::now();
        let mut outcome = run_question(scratch, &manifest, question).await?;
        outcome.record["usage_coverage"] = question_usage_coverage(&outcome.record);
        outcome.record["noJev"] = json!(true);
        outcome.record["benchmark_isolation"] = no_jev;
        append_jsonl(&run_path, &outcome.record)?;
        let error = outcome.record.get("error").and_then(Value::as_str);
        eprintln!(
            "[q {}/{}] {} done in {}ms{}",
            position + 1,
            manifest.questions.len(),
            question.qid,
            started.elapsed().as_millis(),
            error.map(|error| format!(" ERROR: {error}")).unwrap_or_default()
        );
    }

    eprintln!("[run {}] complete", manifest.run_id);
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests (offline, synthetic; no provider, network, or installed-profile access)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use pi_coding_agent::core::memory::store::default_memory_settings;

    fn manifest_fixture(settings: Option<Value>) -> Manifest {
        let mut value = json!({
            "run_id": "synthetic-run",
            "output_dir": "unused-synthetic-output",
            "model": {"provider": "synthetic-offline", "id": "unused"},
            "envs": [{"env_id": "synthetic-env"}],
            "questions": [{"env_id": "synthetic-env", "qid": "q1", "question": "Synthetic?"}],
        });
        if let Some(settings) = settings {
            value["settings"] = settings;
        }
        serde_json::from_value(value).unwrap()
    }

    fn scratch_fixture(temp: &tempfile::TempDir) -> EnvScratch {
        let manifest = manifest_fixture(None);
        EnvScratch::prepare(temp.path(), &manifest.envs[0]).unwrap()
    }

    fn diagnostic_branch(entries: Vec<Value>) -> Vec<Map<String, Value>> {
        entries
            .into_iter()
            .map(|entry| match entry {
                Value::Object(object) => object,
                _ => panic!("synthetic branch entry must be an object"),
            })
            .collect()
    }

    #[test]
    fn no_jev_control_profile_uses_native_off_and_never_overwrites_unsafe_settings() {
        let temp = tempfile::tempdir().unwrap();
        let isolation = BenchmarkIsolation::prepare(temp.path()).unwrap();
        isolation.initialize_settings().unwrap();
        let settings = isolation.read_settings().unwrap();
        assert_eq!(settings.global_default, Some(JevMode::Off));
        assert_eq!(settings.effective_mode("any-new-session"), JevMode::Off);
        assert!(!settings.effective_compaction_enabled("any-new-session"));
        assert!(settings.sessions.is_empty());
        assert!(settings.full_jev.is_none());

        for case in 0..7 {
            let mut unsafe_settings = JevSettings::with_global_default(JevMode::Off);
            match case {
                0 => unsafe_settings.global_default = Some(JevMode::Compare),
                1 => unsafe_settings.global_default = Some(JevMode::Active),
                2 => unsafe_settings.set_session_mode("synthetic", JevMode::Active),
                3 => {
                    unsafe_settings.full_jev_install();
                }
                4 => unsafe_settings.compaction_enabled = true,
                5 => unsafe_settings.credential_configured = true,
                _ => unsafe_settings.transport = Some("mock".to_string()),
            }
            let store = isolation.checked_store().unwrap();
            store.save(&unsafe_settings).unwrap();
            let before = std::fs::read(store.path()).unwrap();
            let error = isolation.initialize_settings().unwrap_err();
            assert!(
                error.contains("refusing unsafe control settings"),
                "{error}"
            );
            assert_eq!(std::fs::read(store.path()).unwrap(), before);
        }
    }

    #[test]
    fn no_jev_guard_rejects_corrupt_missing_and_unexpected_control_state() {
        let temp = tempfile::tempdir().unwrap();
        let isolation = BenchmarkIsolation::prepare(temp.path()).unwrap();
        let store = isolation.checked_store().unwrap();
        assert!(isolation.read_settings().is_err());
        std::fs::write(store.path(), "{corrupt synthetic settings").unwrap();
        assert!(isolation
            .initialize_settings()
            .unwrap_err()
            .contains("corrupt"));
        std::fs::remove_file(store.path()).unwrap();
        isolation.initialize_settings().unwrap();
        let envelope = store
            .path()
            .parent()
            .unwrap()
            .join("synthetic.jev-credential.json");
        std::fs::write(&envelope, "synthetic envelope; do not read").unwrap();
        assert!(isolation
            .read_settings()
            .unwrap_err()
            .contains("unexpected state"));
        assert!(envelope.exists());
    }

    #[test]
    fn no_jev_guard_rejects_foreign_resolvers_and_missing_explicit_model_source() {
        let temp = tempfile::tempdir().unwrap();
        let isolation = BenchmarkIsolation::prepare(temp.path()).unwrap();
        let foreign = tempfile::tempdir().unwrap();
        assert!(isolation.verify_resolver(foreign.path()).is_err());
        assert!(isolation.verify_resolver(temp.path()).is_err());
        isolation
            .verify_resolver(&isolation.control_agent_dir)
            .unwrap();
        assert!(explicit_models_source(None).is_err());
        assert!(explicit_models_source(Some(std::ffi::OsString::new())).is_err());
        assert_eq!(
            explicit_models_source(Some(std::ffi::OsString::from("synthetic-models.json")))
                .unwrap(),
            PathBuf::from("synthetic-models.json")
        );
    }

    #[test]
    fn no_jev_isolation_subprocess_entry() {
        let Some(output) = std::env::var_os("MEMORY_BENCH_TEST_ISOLATION_OUTPUT") else {
            return;
        };
        // Never mutate the environment in the ordinary parallel test process.
        let args: Vec<String> = std::env::args().collect();
        assert!(args.iter().any(|arg| arg == "--exact"));
        assert!(args
            .iter()
            .any(|arg| arg == "tests::no_jev_isolation_subprocess_entry"));
        assert!(args.iter().any(|arg| arg == "--test-threads=1"));
        let isolation = BenchmarkIsolation::install_before_runtime(Path::new(&output)).unwrap();
        assert!(std::env::var_os(ENV_TYPESAFE_API_KEY).is_none());
        assert!(std::env::var_os(ENV_JEV_API_KEY).is_none());
        let metadata = isolation.verify("offline isolated test").unwrap();
        assert_eq!(metadata["noJev"], true);
        assert_eq!(metadata["effective_mode"], "off");
        assert_eq!(metadata["compaction_enabled"], false);
        std::fs::write(
            Path::new(&output).join("isolation-proof.json"),
            serde_json::to_vec(&metadata).unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn no_jev_process_guard_ignores_inherited_profiles_without_mutating_parent_env() {
        let temp = tempfile::tempdir().unwrap();
        let inherited = temp.path().join("synthetic-installed-profile");
        let installed_store = JevSettingsStore::new(&inherited);
        let mut installed = JevSettings::with_global_default(JevMode::Active);
        installed.full_jev_install();
        installed_store.save(&installed).unwrap();
        let installed_before = std::fs::read(installed_store.path()).unwrap();
        let output = temp.path().join("scratch-output");
        let child = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "tests::no_jev_isolation_subprocess_entry",
                "--test-threads=1",
            ])
            .env_clear()
            .env("TEMP", std::env::temp_dir())
            .env("TMP", std::env::temp_dir())
            .env("TMPDIR", std::env::temp_dir())
            .env("HOME", &inherited)
            .env("USERPROFILE", &inherited)
            .env(ENV_AGENT_DIR, &inherited)
            .env(env_agent_dir(), &inherited)
            .env(ENV_TYPESAFE_API_KEY, "synthetic-unused-primary")
            .env(ENV_JEV_API_KEY, "synthetic-unused-alias")
            .env("MEMORY_BENCH_TEST_ISOLATION_OUTPUT", &output)
            .current_dir(temp.path())
            .output()
            .unwrap();
        assert!(
            child.status.success(),
            "isolated guard test failed: {}",
            String::from_utf8_lossy(&child.stderr)
        );
        let proof: Value =
            serde_json::from_slice(&std::fs::read(output.join("isolation-proof.json")).unwrap())
                .unwrap();
        assert_eq!(proof["noJev"], true);
        let resolved = PathBuf::from(proof["control_agent_dir"].as_str().unwrap());
        assert_eq!(
            resolved,
            std::fs::canonicalize(output.join("_control/agent")).unwrap()
        );
        assert_eq!(
            std::fs::read(installed_store.path()).unwrap(),
            installed_before
        );
        assert!(!output.join("run.jsonl").exists());
        assert!(!output.join("env.jsonl").exists());
        assert!(!resolved.join("models.json").exists());
    }

    #[test]
    fn original_snake_case_defaults_and_written_settings_are_unchanged() {
        let manifest = manifest_fixture(None);
        assert_eq!(manifest.variant, "base");
        assert!(manifest.settings.recall_on);
        assert_eq!(manifest.settings.question_timeout_s, 180);
        assert_eq!(manifest.settings.ingest_timeout_s, 3600);
        assert_eq!(manifest.settings.import_run_timeout_s, 900);
        assert_eq!(manifest.settings.import_run_retries, 3);
        assert!(manifest.settings.disable_tools);
        assert!(manifest.settings.evidence_timestamps);
        assert!(!manifest.settings.skip_ingest);
        assert_eq!(manifest.settings.max_recall_entries, None);
        assert_eq!(manifest.settings.max_recall_chars, None);
        assert_eq!(
            manifest_memory_settings(&manifest.settings),
            json!({"recall": true})
        );
        // The d9f6627-era driver used snake_case-only settings; those manifests
        // keep parsing with identical meaning.
        let snake = manifest_fixture(Some(json!({
            "recall_on": true,
            "max_recall_chars": 1200,
            "max_recall_entries": 3,
            "question_timeout_s": 90,
            "import_run_retries": 2,
            "disable_tools": true,
        })));
        assert_eq!(snake.settings.max_recall_chars, Some(1200));
        assert_eq!(snake.settings.max_recall_entries, Some(3));
        assert_eq!(snake.settings.question_timeout_s, 90);
        assert_eq!(snake.settings.import_run_retries, 2);
        assert_eq!(
            manifest_memory_settings(&snake.settings),
            json!({"recall": true, "maxRecallChars": 1200, "maxRecallEntries": 3})
        );

        let realization =
            memory_feature_realization(&manifest.settings, &default_memory_settings(), "test")
                .unwrap();
        assert_eq!(realization["settings_check"], "matched");
        assert_eq!(realization["effective_memory"]["maxRecallEntries"], 6);
        assert_eq!(realization["effective_memory"]["maxRecallChars"], 6000);
        assert_eq!(realization["effective_memory"]["maxExtractionTokens"], 4096);
        assert_eq!(realization["effective_memory"]["maxImportChunkChars"], 40000);
        assert!(realization["recall_hook_observation"].is_null());
        assert_eq!(
            realization["llm_call_evidence"]["recallQueryDistillation"],
            "unavailable_original_product_no_recall_helpers"
        );
        assert_eq!(
            realization["llm_call_evidence"]["recallRerank"],
            "unavailable_original_product_no_recall_helpers"
        );
        assert!(realization["llm_helper_observations"].is_null());
        assert!(realization["llm_helper_usage_coverage"].is_null());
        assert_eq!(
            realization["driver_source_sha256"],
            hash(include_str!("memory_bench.rs"))
        );
        assert!(!realization["build_fingerprint"]
            .as_str()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn existing_camel_case_and_snake_case_manifest_settings_match() {
        let camel = manifest_fixture(Some(json!({
            "recallOn": false,
            "memoryLearning": false,
            "maxRecallChars": 1200,
            "maxRecallEntries": 3,
            "maxExtractionTokens": 512,
            "maxImportChunkChars": 2000,
            "answerInstruction": "Answer directly.",
            "answerMaxTokens": 1024,
        })));
        let snake = manifest_fixture(Some(json!({
            "recall_on": false,
            "memory_learning": false,
            "max_recall_chars": 1200,
            "max_recall_entries": 3,
            "max_extraction_tokens": 512,
            "max_import_chunk_chars": 2000,
            "answer_instruction": "Answer directly.",
            "answer_max_tokens": 1024,
        })));
        assert_eq!(camel.settings.recall_on, snake.settings.recall_on);
        assert_eq!(camel.settings.memory_learning, snake.settings.memory_learning);
        assert_eq!(camel.settings.max_recall_chars, snake.settings.max_recall_chars);
        assert_eq!(
            camel.settings.max_import_chunk_chars,
            snake.settings.max_import_chunk_chars
        );
        assert_eq!(
            camel.settings.answer_instruction,
            snake.settings.answer_instruction
        );
        assert_eq!(camel.settings.answer_max_tokens, snake.settings.answer_max_tokens);
        assert_eq!(
            manifest_memory_settings(&camel.settings),
            manifest_memory_settings(&snake.settings),
        );
    }

    #[test]
    fn unsupported_newer_and_unknown_settings_keys_fail_closed() {
        for key in [
            "importInstructions",
            "import_instructions",
            "recallQueryDistillation",
            "recall_query_distillation",
            "recallRerank",
            "recall_rerank",
            "syntheticUnknownKey",
        ] {
            let raw = json!({
                "run_id": "synthetic-run", "output_dir": "unused-synthetic-output",
                "model": {"provider": "p", "id": "m"},
                "envs": [], "questions": [],
                "settings": {key: true},
            });
            let error = parse_manifest(&raw.to_string()).unwrap_err();
            if key.contains("mport") || key.to_lowercase().contains("distill") || key.to_lowercase().contains("rerank") {
                assert!(
                    error.contains("newer-product memory control"),
                    "{key}: {error}"
                );
            } else {
                assert!(
                    error.contains("unsupported manifest settings key"),
                    "{key}: {error}"
                );
            }
        }
        assert!(parse_manifest(
            &json!({
                "run_id": "synthetic-run", "output_dir": "unused-synthetic-output",
                "model": {"provider": "p", "id": "m"},
                "envs": [], "questions": [], "settings": {"skipIngest": true},
            })
            .to_string()
        )
        .is_ok());
    }

    #[test]
    fn frozen_memory_manifests_fail_closed_before_any_output() {
        let temp = tempfile::tempdir().unwrap();
        for key in ["frozenMemory", "frozen_memory"] {
            let raw = json!({
                "run_id": "synthetic-run",
                "output_dir": temp.path().join("out"),
                "model": {"provider": "p", "id": "m"},
                "envs": [{"env_id": "synthetic-env", key: {"fixture": "synthetic.json"}}],
                "questions": [{"qid": "q1", "env_id": "synthetic-env", "question": "Synthetic?"}],
            });
            let error = parse_manifest(&raw.to_string()).unwrap_err();
            assert!(error.contains("frozen-store hydration"), "{error}");
        }
        assert!(!temp.path().join("out").join("envs").exists());
        assert!(!temp.path().join("out").join("run.jsonl").exists());
    }

    #[test]
    fn only_supplied_limits_are_required_to_match() {
        let requested = ManifestSettings {
            max_recall_chars: Some(1200),
            max_recall_entries: Some(3),
            max_extraction_tokens: Some(512),
            max_import_chunk_chars: Some(2000),
            ..Default::default()
        };
        let mut effective = default_memory_settings();
        let error = memory_feature_realization(&requested, &effective, "test").unwrap_err();
        for key in [
            "maxRecallChars",
            "maxRecallEntries",
            "maxExtractionTokens",
            "maxImportChunkChars",
        ] {
            assert!(error.contains(&format!("memory.{key}:")), "{error}");
        }
        effective.max_recall_chars = 1200;
        effective.max_recall_entries = 3;
        effective.max_extraction_tokens = 512;
        effective.max_import_chunk_chars = 2000;
        memory_feature_realization(&requested, &effective, "test").unwrap();
        memory_feature_realization(&ManifestSettings::default(), &effective, "test").unwrap();
    }

    #[test]
    fn each_recall_toggle_is_checked_in_both_directions() {
        for enabled in [false, true] {
            let requested = ManifestSettings {
                recall_on: enabled,
                memory_learning: Some(enabled),
                ..Default::default()
            };
            let mut effective = default_memory_settings();
            effective.recall = enabled;
            effective.learning = enabled;
            memory_feature_realization(&requested, &effective, "test").unwrap();
            let mut mismatch = effective.clone();
            mismatch.recall = !enabled;
            let error = memory_feature_realization(&requested, &mismatch, "test").unwrap_err();
            assert!(error.contains("memory.recall:"), "{error}");
            let mut mismatch = effective.clone();
            mismatch.learning = !enabled;
            let error = memory_feature_realization(&requested, &mismatch, "test").unwrap_err();
            assert!(error.contains("memory.learning:"), "{error}");
        }
    }

    #[test]
    fn scratch_round_trip_checks_real_store_settings_without_a_provider() {
        let temp = tempfile::tempdir().unwrap();
        let scratch = scratch_fixture(&temp);
        let requested = ManifestSettings {
            recall_on: false,
            memory_learning: Some(false),
            max_recall_chars: Some(1200),
            max_recall_entries: Some(3),
            max_extraction_tokens: Some(512),
            max_import_chunk_chars: Some(2000),
            ..Default::default()
        };
        scratch.write_settings_json(&requested).unwrap();
        let realization = check_memory_feature_realization(&scratch, &requested, "test").unwrap();
        assert_eq!(realization["effective_memory"]["recall"], false);
        assert_eq!(realization["effective_memory"]["learning"], false);
        assert_eq!(realization["effective_memory"]["maxRecallChars"], 1200);
        assert_eq!(realization["effective_memory"]["maxRecallEntries"], 3);
        assert_eq!(realization["effective_memory"]["maxExtractionTokens"], 512);
        assert_eq!(realization["effective_memory"]["maxImportChunkChars"], 2000);
        assert!(!scratch.agent_dir.join("models.json").exists());
        assert!(!temp.path().join("env.jsonl").exists());
        assert!(!temp.path().join("run.jsonl").exists());
    }

    #[tokio::test]
    async fn preflight_rejects_ignored_invalid_limits_before_reading_models() {
        let temp = tempfile::tempdir().unwrap();
        let manifest = manifest_fixture(Some(json!({"maxRecallEntries": 51})));
        // A nonexistent models source also prevents provider access if the guard regresses.
        let error = preflight(
            temp.path(),
            &temp.path().join("nonexistent-models.json"),
            &manifest.model,
            &manifest.settings,
        )
        .await
        .unwrap_err();
        assert!(error.contains("before connectivity preflight"), "{error}");
        assert!(
            error.contains("memory.maxRecallEntries: requested=51, expected=51, effective=6"),
            "{error}"
        );
        assert!(!temp.path().join("_preflight/agent/models.json").exists());
        assert!(!temp.path().join("env.jsonl").exists());
        assert!(!temp.path().join("run.jsonl").exists());
    }

    #[tokio::test]
    async fn ingest_and_question_recheck_settings_before_provider_work() {
        let temp = tempfile::tempdir().unwrap();
        let scratch = scratch_fixture(&temp);
        let manifest = manifest_fixture(Some(json!({"maxRecallEntries": 51})));
        scratch.write_settings_json(&manifest.settings).unwrap();
        // Empty sessions and an unknown model keep this fixture offline even on regression.
        let ingest_error = ingest_env(&scratch, &manifest.envs[0], &manifest, &[])
            .await
            .err()
            .expect("ingest must reject mismatched settings");
        assert!(ingest_error.contains("before ingest"), "{ingest_error}");
        let question_error = run_question(&scratch, &manifest, &manifest.questions[0])
            .await
            .err()
            .expect("question must reject mismatched settings");
        assert!(
            question_error.contains("before question"),
            "{question_error}"
        );
        assert!(!scratch.agent_dir.join("models.json").exists());
    }

    #[test]
    fn project_overrides_are_checked_and_not_rewritten() {
        let temp = tempfile::tempdir().unwrap();
        let scratch = scratch_fixture(&temp);
        let requested = ManifestSettings {
            max_recall_entries: Some(3),
            ..Default::default()
        };
        scratch.write_settings_json(&requested).unwrap();
        let service = MemoryService::new(
            &scratch.cwd.to_string_lossy(),
            &scratch.agent_dir.to_string_lossy(),
            None,
        )
        .unwrap();
        std::fs::create_dir_all(&service.store.dir).unwrap();
        let local_path = Path::new(&service.store.dir).join("settings.json");
        let local_settings = r#"{"maxRecallEntries":2}"#;
        std::fs::write(&local_path, local_settings).unwrap();
        let error = check_memory_feature_realization(&scratch, &requested, "question").unwrap_err();
        assert!(
            error.contains("memory.maxRecallEntries: requested=3, expected=3, effective=2"),
            "{error}"
        );
        assert_eq!(std::fs::read_to_string(local_path).unwrap(), local_settings);
    }

    #[test]
    fn recall_diagnostics_never_claim_helper_calls_in_the_original() {
        let branch = diagnostic_branch(vec![
            json!({"type": "message", "customType": MEMORY_DIAGNOSTIC_CUSTOM_TYPE, "data": {"operation": "recall"}}),
            json!({"type": "custom", "customType": "other", "data": {"operation": "recall"}}),
            json!({"type": "custom", "customType": MEMORY_DIAGNOSTIC_CUSTOM_TYPE, "data": {"operation": "learn"}}),
            json!({"type": "custom", "customType": MEMORY_DIAGNOSTIC_CUSTOM_TYPE, "data": null}),
            json!({"type": "custom", "customType": MEMORY_DIAGNOSTIC_CUSTOM_TYPE, "data": {"operation": "recall", "ids": ["synthetic-id"], "chars": 42, "latencyMs": 1}}),
            json!({"type": "custom", "customType": MEMORY_DIAGNOSTIC_CUSTOM_TYPE, "data": {"operation": "recall", "status": "failed", "latencyMs": 2}}),
        ]);
        let diagnostics = recall_diagnostics(&branch);
        let observation = recall_hook_observation(&diagnostics);
        assert_eq!(observation["diagnostic_count"], 2);
        assert_eq!(observation["failed_diagnostic_count"], 1);
        assert_eq!(observation["last_reported_status"], "failed");
        let success = recall_hook_observation(&diagnostics[..1]);
        assert_eq!(success["diagnostic_count"], 1);
        assert!(success["last_reported_status"].is_null());
        let absent = recall_hook_observation(&[]);
        assert_eq!(absent["diagnostic_count"], 0);
        assert!(absent["last_reported_status"].is_null());

        let mut realization = memory_feature_realization(
            &ManifestSettings::default(),
            &default_memory_settings(),
            "question",
        )
        .unwrap();
        record_recall_observations(&mut realization, &diagnostics);
        // Helper telemetry exists only in newer products; the Original keeps
        // the declared unavailability instead of a fabricated zero.
        for key in ["recallQueryDistillation", "recallRerank"] {
            assert_eq!(
                realization["llm_call_evidence"][key],
                "unavailable_original_product_no_recall_helpers"
            );
        }
        assert!(realization["llm_helper_observations"].is_null());
        assert!(realization["llm_helper_usage_coverage"].is_null());
        assert_eq!(realization["recall_hook_observation"]["diagnostic_count"], 2);
        assert!(!realization.to_string().contains("synthetic-private"));
    }

    fn declared_manifest_fixture() -> Value {
        json!({
            "run_id": "synthetic-declared-run", "benchmark": "synthetic", "variant": "bound",
            "output_dir": "unused-synthetic-output",
            "model": {"provider": "synthetic-offline", "id": "unused"},
            "envs": [{"env_id": "synthetic-env"}],
            "questions": [{"qid": "q1", "env_id": "synthetic-env", "question": "  Exact visible café?\n",
                "question_protocol": "question-v2", "scoring_protocol": "score-v2", "question_sha256": "A".repeat(64)}],
        })
    }

    fn parse_manifest_value(value: &Value) -> Result<Manifest, String> {
        parse_manifest(&serde_json::to_string(value).unwrap())
    }

    #[test]
    fn question_binding_preserves_opaque_declarations_and_legacy_absence() {
        let value = declared_manifest_fixture();
        let manifest = parse_manifest_value(&value).unwrap();
        let question = &manifest.questions[0];
        assert_eq!(question.question, "  Exact visible café?\n");
        let declaration = question_declaration(question).unwrap().unwrap();
        assert_eq!(
            declaration,
            json!({
                "question_protocol": "question-v2", "scoring_protocol": "score-v2",
                "question_sha256": "A".repeat(64),
                "question_binding_source": "caller_declared_scoring_question",
            })
        );
        assert!(question.answer.is_none());
        assert!(question.eval.is_none());
        assert!(question.evidence.is_empty());
        let mut old = value;
        for field in ["question_protocol", "scoring_protocol", "question_sha256"] {
            old["questions"][0].as_object_mut().unwrap().remove(field);
        }
        let legacy = parse_manifest_value(&old).unwrap();
        assert!(question_declaration(&legacy.questions[0])
            .unwrap()
            .is_none());
    }

    #[test]
    fn question_binding_rejects_partial_null_wrong_type_and_unbounded_declarations() {
        for field in ["question_protocol", "scoring_protocol", "question_sha256"] {
            let mut value = declared_manifest_fixture();
            value["questions"][0].as_object_mut().unwrap().remove(field);
            assert!(parse_manifest_value(&value).is_err(), "missing {field}");
            for invalid in [Value::Null, json!(false), json!(123), json!([]), json!({})] {
                let mut value = declared_manifest_fixture();
                value["questions"][0][field] = invalid;
                assert!(parse_manifest_value(&value).is_err(), "typed {field}");
            }
        }
        for field in ["question_protocol", "scoring_protocol"] {
            for invalid in ["".to_string(), " \t\n".to_string(), "é".repeat(257)] {
                let mut value = declared_manifest_fixture();
                value["questions"][0][field] = json!(invalid);
                assert!(parse_manifest_value(&value).is_err(), "bounded {field}");
            }
            let mut value = declared_manifest_fixture();
            value["questions"][0][field] = json!("🧪".repeat(256));
            assert!(
                parse_manifest_value(&value).is_ok(),
                "Unicode scalar limit {field}"
            );
        }
        for invalid in [
            "0".repeat(63),
            "0".repeat(65),
            "g".repeat(64),
            "é".repeat(32),
        ] {
            let mut value = declared_manifest_fixture();
            value["questions"][0]["question_sha256"] = json!(invalid);
            assert!(parse_manifest_value(&value).is_err());
        }
        let mut value = declared_manifest_fixture();
        for field in ["question_protocol", "scoring_protocol", "question_sha256"] {
            value["questions"][0][field] = Value::Null;
        }
        assert!(parse_manifest_value(&value).is_err());
    }

    #[test]
    fn question_binding_new_rows_do_not_rewrite_old_unbound_records() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("run.jsonl");
        let old = json!({"run_id": "old", "env_id": "synthetic-env", "qid": "q1", "question": "Old question"});
        append_jsonl(&path, &old).unwrap();
        let old_bytes = std::fs::read(&path).unwrap();
        let manifest = parse_manifest_value(&declared_manifest_fixture()).unwrap();
        let question = &manifest.questions[0];
        let mut new = json!({"run_id": manifest.run_id, "benchmark": manifest.benchmark,
            "variant": manifest.variant, "env_id": question.env_id, "qid": question.qid, "question": question.question});
        if let Some(Value::Object(fields)) = question_declaration(question).unwrap() {
            new.as_object_mut().unwrap().extend(fields);
        }
        append_jsonl(&path, &new).unwrap();
        let after = std::fs::read(&path).unwrap();
        assert!(after.starts_with(&old_bytes));
        assert!(old.get("question_binding_source").is_none());
        assert_eq!(
            new["question_binding_source"],
            "caller_declared_scoring_question"
        );
        assert_eq!(new["question"], question.question);
        assert_eq!(
            read_jsonl_keys(&path, &["run_id", "env_id", "qid"])
                .unwrap()
                .len(),
            2
        );
    }

    fn thinking_model_fixture() -> pi_ai::types::Model {
        pi_ai::types::Model {
            id: "synthetic-thinking-model".to_string(),
            provider: "synthetic-offline".to_string(),
            reasoning: true,
            ..Default::default()
        }
    }

    #[test]
    fn answer_thinking_level_is_typed_optional_and_uses_native_creation_options() {
        assert!(ManifestSettings::default().answer_thinking_level.is_none());
        let default_creation =
            answer_session_creation(&ManifestSettings::default(), thinking_model_fixture())
                .unwrap();
        assert!(default_creation.thinking_level.is_none());
        for name in ["off", "minimal", "low", "medium", "high", "xhigh", "max"] {
            for key in ["answerThinkingLevel", "answer_thinking_level"] {
                let settings: ManifestSettings =
                    serde_json::from_value(json!({key: name})).unwrap();
                let level = settings.answer_thinking_level.unwrap();
                assert_eq!(level.as_str(), name);
                let mut model = thinking_model_fixture();
                model.thinking_level_map = Some(pi_ai::types::ThinkingLevelMap::from([
                    ("xhigh".to_string(), Some("xhigh".to_string())),
                    ("max".to_string(), Some("max".to_string())),
                ]));
                let before = serde_json::to_value(&model).unwrap();
                let creation = answer_session_creation(&settings, model).unwrap();
                assert_eq!(creation.thinking_level, Some(level));
                assert_eq!(
                    serde_json::to_value(creation.model.as_ref().unwrap()).unwrap(),
                    before
                );
                assert_eq!(creation.no_tools.as_deref(), Some("all"));
                assert_eq!(creation.prewarm_ipython_kernel, Some(false));
                assert_eq!(creation.telemetry_disabled, Some(true));
            }
        }
        for invalid in [
            json!("OFF"),
            json!("bogus"),
            json!(false),
            json!(1),
            json!([]),
        ] {
            assert!(serde_json::from_value::<ManifestSettings>(
                json!({"answerThinkingLevel": invalid})
            )
            .is_err());
        }
    }

    #[test]
    fn answer_thinking_level_rejects_native_upward_fallback_and_empty_support() {
        let settings = ManifestSettings {
            answer_thinking_level: Some(ThinkingLevel::Off),
            ..Default::default()
        };
        let mut model = thinking_model_fixture();
        model.thinking_level_map = Some(pi_ai::types::ThinkingLevelMap::from([(
            "off".to_string(),
            None,
        )]));
        assert_eq!(
            pi_ai::models::clamp_thinking_level(&model, "off"),
            "minimal"
        );
        assert!(answer_session_creation(&settings, model.clone()).is_err());
        let all_disabled = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
            .into_iter()
            .map(|name| (name.to_string(), None))
            .collect();
        model.thinking_level_map = Some(all_disabled);
        assert!(pi_ai::models::get_supported_thinking_levels(&model).is_empty());
        assert_eq!(pi_ai::models::clamp_thinking_level(&model, "off"), "off");
        assert!(answer_session_creation(&settings, model.clone()).is_err());
        // Omission retains the existing SDK path, even if a concrete request would be unsupported.
        assert!(answer_session_creation(&ManifestSettings::default(), model)
            .unwrap()
            .thinking_level
            .is_none());
        let mut model = thinking_model_fixture();
        model.reasoning = false;
        let unsupported = ManifestSettings {
            answer_thinking_level: Some(ThinkingLevel::High),
            ..Default::default()
        };
        assert!(answer_session_creation(&unsupported, model.clone()).is_err());
        let creation = answer_session_creation(&settings, model).unwrap();
        assert_eq!(creation.thinking_level, Some(ThinkingLevel::Off));
        assert!(!creation.model.as_ref().unwrap().reasoning);
    }

    #[test]
    fn answer_thinking_level_effective_guard_and_metadata_are_not_payload_evidence() {
        let settings = ManifestSettings {
            answer_thinking_level: Some(ThinkingLevel::Off),
            ..Default::default()
        };
        assert!(
            answer_thinking_control(&settings, Some(ThinkingLevel::Medium), Some(true)).is_err()
        );
        let env = answer_thinking_control(&settings, None, None).unwrap();
        assert_eq!(env["requested"], "off");
        assert!(env["effective"].is_null());
        assert!(env["effective_source"].is_null());
        assert_eq!(env["status"], "not_observed_before_question");
        let run =
            answer_thinking_control(&settings, Some(ThinkingLevel::Off), Some(false)).unwrap();
        assert_eq!(run["requested"], "off");
        assert_eq!(run["effective"], "off");
        assert_eq!(run["effective_source"], "AgentSession.thinking_level()");
        assert_eq!(run["status"], "matched_explicit_request");
        assert_eq!(run["model_reasoning_capability"], false);
        assert_eq!(run["scope"], "main_answer_only");
        assert_eq!(run["recall_helper_override"], false);
        assert_eq!(run["provider_payload_observed"], false);
        assert!(run["provider_reasoning_disabled"].is_null());
        let default = answer_thinking_control(
            &ManifestSettings::default(),
            Some(ThinkingLevel::Medium),
            Some(true),
        )
        .unwrap();
        assert!(default["requested"].is_null());
        assert_eq!(default["effective"], "medium");
        assert_eq!(default["status"], "native_default_path_observed");
    }

    #[test]
    fn answer_thinking_level_roundtrip_does_not_override_memory_or_global_defaults() {
        let temp = tempfile::tempdir().unwrap();
        let scratch = scratch_fixture(&temp);
        let default_settings = ManifestSettings::default();
        scratch.write_settings_json(&default_settings).unwrap();
        let before = std::fs::read(scratch.agent_dir.join("settings.json")).unwrap();
        let mut value = declared_manifest_fixture();
        value["settings"] = json!({"answerThinkingLevel": "off", "disableTools": false});
        let manifest = parse_manifest_value(&value).unwrap();
        assert_eq!(
            manifest.settings.answer_thinking_level,
            Some(ThinkingLevel::Off)
        );
        scratch.write_settings_json(&manifest.settings).unwrap();
        assert_eq!(
            std::fs::read(scratch.agent_dir.join("settings.json")).unwrap(),
            before
        );
        let settings = SettingsManager::create(
            &scratch.cwd.to_string_lossy(),
            Some(&scratch.agent_dir.to_string_lossy()),
        );
        assert!(settings.get_default_thinking_level().is_none());
        assert_eq!(
            manifest_memory_settings(&manifest.settings),
            manifest_memory_settings(&default_settings)
        );
        let creation =
            answer_session_creation(&manifest.settings, thinking_model_fixture()).unwrap();
        assert_eq!(creation.thinking_level, Some(ThinkingLevel::Off));
        assert!(creation.no_tools.is_none());
        assert!(!scratch.agent_dir.join("models.json").exists());
        assert!(!temp.path().join("run.jsonl").exists());
    }

    #[test]
    fn answer_instruction_wrapper_and_token_cap_match_the_current_driver() {
        // Identical wrapper bytes to the validated 43b1993 driver prompt path.
        assert_eq!(
            effective_question_text(None, "Where is the café?"),
            "Where is the café?"
        );
        assert_eq!(
            effective_question_text(Some("Answer directly."), "Where is the café?"),
            "Answer directly.\n\nQuestion: Where is the café?"
        );
        assert_eq!(
            effective_question_text(Some("  Multi\nline instruction  "), "Q?"),
            "  Multi\nline instruction  \n\nQuestion: Q?"
        );
        for cap in [1u32, 32000] {
            let mut model = thinking_model_fixture();
            apply_answer_max_tokens(&mut model, Some(cap)).unwrap();
            assert_eq!(model.max_tokens, f64::from(cap));
        }
        for invalid in [0u32, 32001, 100_000] {
            let mut model = thinking_model_fixture();
            let before = model.max_tokens;
            assert!(apply_answer_max_tokens(&mut model, Some(invalid)).is_err());
            assert_eq!(model.max_tokens, before);
        }
        let mut model = thinking_model_fixture();
        let before = model.max_tokens;
        apply_answer_max_tokens(&mut model, None).unwrap();
        assert_eq!(model.max_tokens, before);
    }

    #[test]
    fn usage_coverage_stays_cost_incomplete_and_helpers_null() {
        for error in [Value::Null, json!("synthetic timeout")] {
            let record = json!({"error": error, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}});
            let coverage = question_usage_coverage(&record);
            assert_eq!(
                coverage["status"],
                "incomplete_observations_only_no_cost_total"
            );
            assert_eq!(coverage["answer_presence_aware_usage"], false);
            assert_eq!(coverage["cost_complete"], false);
            assert!(coverage["recall_helpers"].is_null());
            for field in [
                "recall_helper_tokens",
                "recall_helper_latency_ms",
                "total_model_tokens",
            ] {
                assert!(coverage[field].is_null());
            }
        }
        assert!(question_usage_coverage(&json!({}))["recall_helpers"].is_null());
    }

    fn combined_env(family: Option<&str>, events: Value) -> EnvSpec {
        serde_json::from_value(json!({
            "env_id": "combined-env", "family": family,
            "sessions": [{"session_id": "source", "ts": "2099-01-01T00:00:00Z", "events": events}],
            "ground_truth_refs": ["MUST_NOT_ENTER_SOURCE"],
        })).unwrap()
    }

    fn combined_rows(scratch: &EnvScratch, session: &str) -> Vec<Value> {
        std::fs::read_to_string(scratch.sessions_dir.join(format!("{session}.jsonl")))
            .unwrap().lines().map(|line| serde_json::from_str(line).unwrap()).collect()
    }

    #[test]
    fn combined_source_dates_parse_without_host_clock_or_session_fallback() {
        for value in ["2024-03-01T12:00:00", "2024-03-01T12:00:00Z", "2024-03-01T14:00:00+02:00"] {
            assert_eq!(parse_event_timestamp(Some(&json!(value))).unwrap(), Some(1_709_294_400_000));
        }
        assert_eq!(parse_event_timestamp(Some(&json!("2024-03-01T12:00:00.125Z"))).unwrap(), Some(1_709_294_400_125));
        assert_eq!(parse_event_timestamp(Some(&json!("1969-12-31T23:59:59.999Z"))).unwrap(), Some(-1));
        assert_eq!(parse_event_timestamp(None).unwrap(), None);
        assert_eq!(parse_event_timestamp(Some(&Value::Null)).unwrap(), None);
        for invalid in [json!(""), json!("NaN"), json!("2024-02-30T12:00:00"),
            json!("2024-03-01"), json!("1970-01-01T00:00:00Z"), json!("2016-12-31T23:59:60Z"),
            json!(true), json!(1_700_000_000_000i64), json!({}), json!([])] {
            assert!(parse_event_timestamp(Some(&invalid)).is_err(), "accepted {invalid}");
        }
    }

    #[test]
    fn combined_legacy_manifest_remains_accepted_but_cannot_create_synthetic_anchors() {
        use pi_coding_agent::core::memory::evidence::{message_evidence, serialize_evidence, AgentMessage};
        let temp = tempfile::tempdir().unwrap();
        let scratch = scratch_fixture(&temp);
        let env = combined_env(None, json!([
            {"role": "assistant", "text": "Historical role policy", "ts": true},
            {"role": "user", "text": "Explicit source year 2021", "ts": "2024-03-01T12:00:00Z"}
        ]));
        assert_eq!(capture_mode(None).unwrap(), CaptureMode::LegacyUndated);
        for enabled in [false, true] {
            let index = scratch.write_session_files(&env, CaptureMode::LegacyUndated, enabled).unwrap();
            assert_eq!(index[0]["capture_protocol"], "legacy-unversioned-undated");
            for row in combined_rows(&scratch, "source") {
                assert_eq!(row["message"]["role"], "user");
                assert!(row.get("timestamp").is_none());
                assert!(row["message"].get("timestamp").is_none());
                let message: AgentMessage = serde_json::from_value(row["message"].clone()).unwrap();
                let evidence = message_evidence(&message, None, row["id"].as_str()).unwrap();
                assert_eq!(evidence.origin, pi_coding_agent::core::memory::evidence::MemoryOrigin::User);
                assert!(!serialize_evidence(&[evidence], 4096).contains("\"timestamp\""));
            }
        }
        assert!(!scratch.agent_dir.join("models.json").exists());
    }

    #[test]
    fn combined_roles_preserve_real_assistants_and_only_map_external_people() {
        let tool = json!({"toolCallId": "call", "toolName": "fixture", "isError": false});
        for family in [None, Some("longmemeval"), Some("unknown")] {
            let env = combined_env(family, json!([
                {"role": "user", "text": "User"}, {"role": "assistant", "text": "Agent"},
                {"role": "toolResult", "text": "Observed", "tool_result": tool}
            ]));
            validate_capture_env(&env, CaptureMode::SourceAuthorityV1).unwrap();
            let roles: Vec<_> = env.sessions[0].events.iter().map(|event| imported_event_role(&env, event).unwrap()).collect();
            assert_eq!(roles, ["user", "assistant", "toolResult"]);
        }
        for family in ["locomo", "locomo_plus"] {
            let env = combined_env(Some(family), json!([
                {"role": "user", "text": "Person A"}, {"role": "assistant", "text": "Person B"}
            ]));
            assert_eq!(imported_event_role(&env, &env.sessions[0].events[1]).unwrap(), "user");
        }
        let env = combined_env(Some("locomo"), json!([{"role": "system", "text": "Untrusted"}]));
        assert!(validate_capture_env(&env, CaptureMode::SourceAuthorityV1).is_err());
    }

    #[test]
    fn combined_native_tool_descriptor_accepts_converter_key_and_checks_aliases() {
        let tool = json!({"toolCallId": "call", "toolName": "fixture", "isError": false});
        for key in ["tool_result", "toolResult"] {
            let env = combined_env(None, json!([{"role": "toolResult", "text": "Observed", key: tool}]));
            validate_capture_env(&env, CaptureMode::SourceAuthorityV1).unwrap();
        }
        assert!(serde_json::from_value::<EventSpec>(json!({
            "role": "toolResult", "text": "Observed", "tool_result": tool, "toolResult": tool
        })).is_err());
        for event in [
            json!({"role": "toolResult", "text": "Missing"}),
            json!({"role": "assistant", "text": "Misplaced", "tool_result": tool}),
            json!({"role": "toolResult", "text": "Empty", "tool_result": {"toolCallId": "", "toolName": "fixture"}}),
            json!({"role": "toolResult", "text": "Blank", "tool_result": {"toolCallId": "call", "toolName": "  "}}),
        ] {
            let env = combined_env(None, json!([event]));
            assert!(validate_capture_env(&env, CaptureMode::SourceAuthorityV1).is_err());
        }
    }

    #[tokio::test]
    async fn combined_dated_transport_ablation_reaches_jobs_without_resolving_facts() {
        use pi_coding_agent::core::memory::evidence::{serialize_evidence, MemoryOrigin};
        use pi_coding_agent::core::memory::jobs::{ExtractionResult, ImportJobStatus, MemoryExtractor, MemoryJobs};
        use pi_coding_agent::core::refinement::refinement::normalize_refinement_proposal;
        let temp = tempfile::tempdir().unwrap();
        let scratch = scratch_fixture(&temp);
        let env = combined_env(None, json!([
            {"role": "user", "text": "I repaired the observatory clock yesterday.", "ts": "2024-03-01T12:00:00Z"},
            {"role": "assistant", "text": "Unconfirmed proposal", "ts": null}
        ]));
        let index = scratch.write_session_files(&env, CaptureMode::SourceAuthorityV1, true).unwrap();
        let mut anchored = combined_rows(&scratch, "source");
        assert_eq!(index[0]["events"][0]["source_timestamp_ms"], 1_709_294_400_000i64);
        assert_eq!(anchored[0]["message"]["timestamp"], 1_709_294_400_000i64);
        assert!(anchored[1]["message"].get("timestamp").is_none());
        let service = MemoryService::new(&scratch.cwd.to_string_lossy(), &scratch.agent_dir.to_string_lossy(), None).unwrap();
        let jobs = MemoryJobs::new(service.store);
        let source = scratch.sessions_dir.join("source.jsonl");
        let prepared = jobs.prepare(&source.to_string_lossy()).await.unwrap();
        let saved_before = std::fs::read(Path::new(&jobs.dir).join(format!("{}.json", prepared.id))).unwrap();
        let extract: MemoryExtractor = Arc::new(|records| Box::pin(async move {
            assert_eq!(records[0].origin, MemoryOrigin::User);
            assert_eq!(records[1].origin, MemoryOrigin::Assistant);
            assert_eq!(records[0].text, "I repaired the observatory clock yesterday.");
            let rendered = serialize_evidence(&records, 80_000);
            assert!(rendered.contains("I repaired the observatory clock yesterday."));
            assert!(rendered.contains("Unconfirmed proposal"));
            // Honest Original semantic: the Original product's Evidence carries
            // no event-time field, so extraction cannot see 2024-03-01; the
            // trusted timestamp stays only in the preserved source rows.
            assert!(!rendered.contains("2024-03-01T12:00:00"));
            assert!(!rendered.contains("2099-01-01"));
            assert!(!rendered.contains("2024-02-29"));
            Ok(ExtractionResult { proposal: normalize_refinement_proposal(&json!({
                "summary": "offline", "rationale": "transport", "expectedOutcome": "no edits", "edits": []
            })), input: 0.0, output: 0.0 })
        }));
        // Reading the saved job does not backfill or rewrite anything.
        assert_eq!(jobs.get(&prepared.id).unwrap(), prepared);
        assert_eq!(std::fs::read(Path::new(&jobs.dir).join(format!("{}.json", prepared.id))).unwrap(), saved_before);
        let result = jobs.run(&prepared.id, extract, None).await.unwrap();
        assert_eq!(result.status, ImportJobStatus::Preview);
        scratch.write_session_files(&env, CaptureMode::SourceAuthorityV1, false).unwrap();
        for row in &mut anchored {
            row.as_object_mut().unwrap().remove("timestamp");
            row["message"].as_object_mut().unwrap().remove("timestamp");
        }
        assert_eq!(anchored, combined_rows(&scratch, "source"));
        let undated_job = jobs.prepare(&source.to_string_lossy()).await.unwrap();
        assert_ne!(undated_job.id, prepared.id);
        assert!(!serde_json::to_string(&anchored).unwrap().contains("MUST_NOT_ENTER_SOURCE"));
        assert!(!scratch.agent_dir.join("models.json").exists());
    }

    #[test]
    fn combined_invalid_dates_fail_before_any_writer_output_even_when_ablation_off() {
        let temp = tempfile::tempdir().unwrap();
        let scratch = scratch_fixture(&temp);
        let env = combined_env(None, json!([
            {"role": "user", "text": "Valid first event", "ts": "2024-03-01T12:00:00Z"},
            {"role": "user", "text": "Invalid second event", "ts": "not-a-date"}
        ]));
        for enabled in [false, true] {
            let error = scratch.write_session_files(&env, CaptureMode::SourceAuthorityV1, enabled).unwrap_err();
            assert!(error.contains("event 2"), "{error}");
            assert!(!scratch.sessions_dir.join("source.jsonl").exists());
        }
    }

    #[test]
    fn combined_capture_opt_in_does_not_change_product_settings_or_thinking_control() {
        let mut value = declared_manifest_fixture();
        let legacy = parse_manifest_value(&value).unwrap();
        assert_eq!(capture_mode(legacy.capture_protocol.as_deref()).unwrap(), CaptureMode::LegacyUndated);
        value["captureProtocol"] = json!(SOURCE_AUTHORITY_CAPTURE_PROTOCOL);
        value["settings"] = json!({"evidenceTimestamps": false, "answerThinkingLevel": "off"});
        let manifest = parse_manifest_value(&value).unwrap();
        assert_eq!(capture_mode(manifest.capture_protocol.as_deref()).unwrap(), CaptureMode::SourceAuthorityV1);
        assert!(!manifest.settings.evidence_timestamps);
        assert_eq!(manifest_memory_settings(&manifest.settings), json!({"recall": true}));
        assert_eq!(manifest.settings.answer_thinking_level, Some(ThinkingLevel::Off));
        assert_eq!(question_declaration(&manifest.questions[0]).unwrap(), question_declaration(&legacy.questions[0]).unwrap());
        assert!(capture_mode(Some("unsupported-version")).is_err());
        value["captureProtocol"] = Value::Null;
        assert!(parse_manifest_value(&value).is_err());
        for key in ["evidenceTimestamps", "evidence_timestamps"] {
            let settings: ManifestSettings = serde_json::from_value(json!({key: false})).unwrap();
            assert!(!settings.evidence_timestamps);
            assert_eq!(manifest_memory_settings(&settings), json!({"recall": true}));
        }
        assert!(ManifestSettings::default().evidence_timestamps);
    }

    #[test]
    fn combined_fresh_output_gate_is_new_mode_only_and_does_not_rewrite_history() {
        let temp = tempfile::tempdir().unwrap();
        validate_capture_output(temp.path(), CaptureMode::SourceAuthorityV1).unwrap();
        let path = temp.path().join("env.jsonl");
        std::fs::write(&path, "historical bytes\n").unwrap();
        assert!(validate_capture_output(temp.path(), CaptureMode::SourceAuthorityV1).is_err());
        validate_capture_output(temp.path(), CaptureMode::LegacyUndated).unwrap();
        assert_eq!(std::fs::read_to_string(path).unwrap(), "historical bytes\n");
    }

    #[test]
    fn old_and_new_jsonl_records_keep_identical_resume_keys_and_bytes() {
        let temp = tempfile::tempdir().unwrap();
        let realization = memory_feature_realization(
            &ManifestSettings::default(),
            &default_memory_settings(),
            "test",
        )
        .unwrap();
        for (file, fields) in [
            ("env.jsonl", vec!["run_id", "env_id"]),
            ("run.jsonl", vec!["run_id", "env_id", "qid"]),
        ] {
            let path = temp.path().join(file);
            let old = json!({"run_id": "old-run", "env_id": "synthetic-env", "qid": "q1"});
            append_jsonl(&path, &old).unwrap();
            let old_bytes = std::fs::read(&path).unwrap();
            let new = json!({
                "run_id": "new-run", "env_id": "synthetic-env", "qid": "q1",
                "memory_feature_realization": realization,
            });
            append_jsonl(&path, &new).unwrap();
            let bytes_before = std::fs::read(&path).unwrap();
            assert!(bytes_before.starts_with(&old_bytes));
            let done = read_jsonl_keys(&path, &fields).unwrap();
            for run in ["old-run", "new-run"] {
                let key = if file == "env.jsonl" {
                    record_key(&[run, "synthetic-env"])
                } else {
                    record_key(&[run, "synthetic-env", "q1"])
                };
                assert!(done.contains(&key));
            }
            assert_eq!(done.len(), 2);
            assert_eq!(std::fs::read(&path).unwrap(), bytes_before);
        }
    }
}
