//! Process-local source enrollment for the actual native provider request.
//!
//! A completed source receipt is not evidence of receiver use or task semantics.
//! Raw tokens/tensors stay on the runtime. A transcript cannot restore this lease.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio_util::sync::CancellationToken;

use crate::inference_attachment::InferenceStateTarget;
use crate::types::{AssistantMessage, ContentBlock};

pub const SOURCE_CAPTURE_SCHEMA: &str = "occ3.capture.v1";
pub const SOURCE_RECEIPT_SCHEMA: &str = "occ3.source.v1";
pub const SOURCE_RECEIPT_HEADER: &str = "x-optimus-occ3-source";
pub const MAX_SOURCE_RECEIPT_BYTES: usize = 2048;
pub const SOURCE_SPAN_POLICY: &str = "whole_completed_response_tail_v1";
pub const SOURCE_REPRESENTATION: &str = "final_layer_prenorm_predictor";

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceCaptureIntent {
    pub schema: String,
    pub capture_id: String,
    pub source_session_id: String,
    pub source_request_id: String,
    pub turn_nonce: String,
    pub model_fingerprint: String,
    pub runtime_id: String,
    pub runtime_generation: u64,
    pub method: String,
    pub retained_count_limit: u32,
    /// Aggregate all-TP live staged and retained bytes, NOT logical receipt bytes.
    pub max_capture_bytes: u64,
    pub expires_at_epoch: u64,
    pub span_policy: String,
}

impl std::fmt::Debug for SourceCaptureIntent {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SourceCaptureIntent")
            .finish_non_exhaustive()
    }
}

impl SourceCaptureIntent {
    pub fn validate(&self, now: u64) -> Result<(), SourceCaptureError> {
        if self.schema != SOURCE_CAPTURE_SCHEMA || self.span_policy != SOURCE_SPAN_POLICY {
            return Err(SourceCaptureError::InvalidField("schema_or_scope"));
        }
        for (field, value) in [
            ("capture_id", &self.capture_id),
            ("source_session_id", &self.source_session_id),
            ("source_request_id", &self.source_request_id),
            ("turn_nonce", &self.turn_nonce),
            ("model_fingerprint", &self.model_fingerprint),
            ("runtime_id", &self.runtime_id),
            ("method", &self.method),
        ] {
            identifier(field, value, 256)?;
        }
        if !(1..=128).contains(&self.retained_count_limit) || self.max_capture_bytes == 0 {
            return Err(SourceCaptureError::InvalidField("capture_limits"));
        }
        if self.expires_at_epoch <= now {
            return Err(SourceCaptureError::Expired);
        }
        Ok(())
    }

    /// Sorted-key compact ASCII JSON. All string fields are restricted to ASCII.
    pub fn canonical_bytes(&self) -> Result<Vec<u8>, SourceCaptureError> {
        let value =
            serde_json::to_value(self).map_err(|_| SourceCaptureError::InvalidField("intent"))?;
        canonical_json_bytes(&value)
    }

    pub fn digest(&self) -> Result<String, SourceCaptureError> {
        Ok(sha256(&self.canonical_bytes()?))
    }
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceCaptureReceipt {
    pub schema: String,
    pub intent_digest: String,
    pub source_handle: String,
    pub native_rid: String,
    pub response_id: String,
    pub capture_commit_id: String,
    pub accepted_span_start: u64,
    pub accepted_span_end: u64,
    pub retained_count: u32,
    pub predictor_position_offset: i32,
    pub representation: String,
    pub layer_index: u32,
    pub hidden_dim: u32,
    pub dtype: String,
    pub layout_digest: String,
    pub ready_ranks: u32,
    pub expected_ranks: u32,
    pub accepted_sequence_digest: String,
    pub output_sha256: String,
    pub expires_at_epoch: u64,
    /// One logical aligned [retained_count, hidden_dim] prefix, NOT GPU total.
    pub byte_count: u64,
}

impl std::fmt::Debug for SourceCaptureReceipt {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SourceCaptureReceipt")
            .finish_non_exhaustive()
    }
}

/// Fresh connector-verified expected shape. It is not public request authority.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceCaptureProfile {
    pub layer_index: u32,
    pub hidden_dim: u32,
    pub dtype: String,
    pub layout_digest: String,
    pub expected_ranks: u32,
}

/// Local full-operation limits; never serialized into ordinary provider options.
#[derive(Clone, Copy, Debug)]
pub struct SourceCaptureLimits {
    pub deadline: Instant,
    pub max_response_bytes: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SourceCaptureState {
    Prepared,
    Submitted,
    Captured,
    Cancelled,
    Uncertain,
    Retired,
}

#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum SourceCaptureError {
    #[error("Source capture has an invalid {0} field.")]
    InvalidField(&'static str),
    #[error("Source capture target does not match this request.")]
    TargetMismatch,
    #[error("Source capture endpoint is unsafe.")]
    InvalidEndpoint,
    #[error("Source capture has expired or exceeded its deadline.")]
    Expired,
    #[error("Source capture clock is unavailable.")]
    ClockUnavailable,
    #[error("Source capture is no longer prepared; replay is forbidden.")]
    NotPrepared,
    #[error("Source capture submission is no longer active.")]
    NotSubmitted,
    #[error("Source capture receipt is missing, invalid, or mismatched.")]
    InvalidReceipt,
    #[error("Source capture body or terminal provenance is invalid.")]
    InvalidCompletion,
    #[error("Source capture output exceeds its reserved bound.")]
    ResponseTooLarge,
    #[error(
        "Source capture payload fields are reserved; capture and consumption cannot be combined."
    )]
    InvalidPayload,
    #[error("Source capture tool provenance does not match the completed response.")]
    InvalidToolBinding,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceToolBinding {
    pub tool_call_id: String,
    pub tool_name: String,
    pub arguments_sha256: String,
}

struct SourceInner {
    target: InferenceStateTarget,
    intent: SourceCaptureIntent,
    profile: SourceCaptureProfile,
    limits: SourceCaptureLimits,
    state: Mutex<SourceRecord>,
    cancellation: CancellationToken,
}
struct SourceRecord {
    state: SourceCaptureState,
    receipt: Option<SourceCaptureReceipt>,
    tools: Vec<SourceToolBinding>,
}

#[derive(Clone)]
pub struct SourceCaptureLease(Arc<SourceInner>);
impl std::fmt::Debug for SourceCaptureLease {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SourceCaptureLease")
            .field("state", &self.state())
            .finish_non_exhaustive()
    }
}

impl SourceCaptureLease {
    pub fn new(
        target: InferenceStateTarget,
        intent: SourceCaptureIntent,
        profile: SourceCaptureProfile,
        limits: SourceCaptureLimits,
    ) -> Result<Self, SourceCaptureError> {
        intent.validate(epoch_now()?)?;
        identifier("provider", &target.provider, 256)?;
        identifier("model_id", &target.model_id, 256)?;
        endpoint(&target.base_url)?;
        digest_field(&profile.layout_digest)?;
        if profile.hidden_dim == 0
            || profile.expected_ranks == 0
            || element_bytes(&profile.dtype).is_none()
        {
            return Err(SourceCaptureError::InvalidField("profile"));
        }
        if limits.deadline <= Instant::now()
            || limits.max_response_bytes == 0
            || limits.max_response_bytes > 64 * 1024 * 1024
        {
            return Err(SourceCaptureError::InvalidField("response_limits"));
        }
        Ok(Self(Arc::new(SourceInner {
            target,
            intent,
            profile,
            limits,
            state: Mutex::new(SourceRecord {
                state: SourceCaptureState::Prepared,
                receipt: None,
                tools: Vec::new(),
            }),
            cancellation: CancellationToken::new(),
        })))
    }
    pub fn target(&self) -> &InferenceStateTarget {
        &self.0.target
    }
    pub fn intent(&self) -> &SourceCaptureIntent {
        &self.0.intent
    }
    pub fn profile(&self) -> &SourceCaptureProfile {
        &self.0.profile
    }
    pub fn limits(&self) -> SourceCaptureLimits {
        self.0.limits
    }
    pub fn state(&self) -> SourceCaptureState {
        self.0.state.lock().state
    }
    /// Available only after the exact native response and parser completed.
    pub fn receipt(&self) -> Option<SourceCaptureReceipt> {
        let record = self.0.state.lock();
        (record.state == SourceCaptureState::Captured)
            .then(|| record.receipt.clone())
            .flatten()
    }
    /// Cleanup-only metadata remains readable after local retirement. This does
    /// not restore publication authority; validate_tool still requires Captured.
    pub fn retirement_receipt(&self) -> Option<SourceCaptureReceipt> {
        self.0.state.lock().receipt.clone()
    }
    pub fn tool_bindings(&self) -> Vec<SourceToolBinding> {
        let record = self.0.state.lock();
        if record.state == SourceCaptureState::Captured {
            record.tools.clone()
        } else {
            Vec::new()
        }
    }
    pub fn validate_tool(
        &self,
        tool_id: &str,
        name: &str,
        arguments: &serde_json::Value,
    ) -> Result<SourceToolBinding, SourceCaptureError> {
        let expected = SourceToolBinding {
            tool_call_id: tool_id.into(),
            tool_name: name.into(),
            arguments_sha256: value_digest(arguments)?,
        };
        let record = self.0.state.lock();
        if record.state != SourceCaptureState::Captured || !record.tools.contains(&expected) {
            return Err(SourceCaptureError::InvalidToolBinding);
        }
        Ok(expected)
    }
    /// Revokes local authority; callers separately retire the runtime resource.
    pub fn cancel(&self) -> SourceCaptureState {
        let mut record = self.0.state.lock();
        record.state = match record.state {
            SourceCaptureState::Prepared => SourceCaptureState::Cancelled,
            SourceCaptureState::Submitted => SourceCaptureState::Uncertain,
            SourceCaptureState::Captured => SourceCaptureState::Retired,
            other => other,
        };
        self.0.cancellation.cancel();
        record.state
    }
    pub(crate) async fn cancelled(&self) {
        self.0.cancellation.cancelled().await;
    }
    pub(crate) fn validate_target(
        &self,
        provider: &str,
        model: &str,
        base_url: &str,
    ) -> Result<(), SourceCaptureError> {
        if self.state() != SourceCaptureState::Prepared {
            return Err(SourceCaptureError::NotPrepared);
        }
        if provider != self.target().provider
            || model != self.target().model_id
            || base_url != self.target().base_url
        {
            return Err(SourceCaptureError::TargetMismatch);
        }
        self.intent().validate(epoch_now()?)?;
        if self.limits().deadline <= Instant::now() {
            return Err(SourceCaptureError::Expired);
        }
        Ok(())
    }
    pub(crate) fn submit(&self) -> Result<SourceSubmission, SourceCaptureError> {
        let mut record = self.0.state.lock();
        if record.state != SourceCaptureState::Prepared {
            return Err(SourceCaptureError::NotPrepared);
        }
        self.intent().validate(epoch_now()?)?;
        if self.limits().deadline <= Instant::now() {
            return Err(SourceCaptureError::Expired);
        }
        record.state = SourceCaptureState::Submitted;
        Ok(SourceSubmission {
            lease: self.clone(),
            receipt: None,
            body_verified: false,
        })
    }
}

#[derive(Debug)]
pub(crate) struct SourceSubmission {
    lease: SourceCaptureLease,
    receipt: Option<SourceCaptureReceipt>,
    body_verified: bool,
}
impl SourceSubmission {
    pub(crate) fn ensure_active(&self) -> Result<(), SourceCaptureError> {
        if self.lease.state() != SourceCaptureState::Submitted {
            return Err(SourceCaptureError::NotSubmitted);
        }
        if self.lease.limits().deadline <= Instant::now()
            || self.lease.intent().expires_at_epoch <= epoch_now()?
        {
            return Err(SourceCaptureError::Expired);
        }
        Ok(())
    }
    pub(crate) fn verify_header(
        &mut self,
        headers: &reqwest::header::HeaderMap,
    ) -> Result<(), SourceCaptureError> {
        self.ensure_active()?;
        let mut values = headers.get_all(SOURCE_RECEIPT_HEADER).iter();
        let header = values.next().ok_or(SourceCaptureError::InvalidReceipt)?;
        if values.next().is_some() || header.as_bytes().len() > MAX_SOURCE_RECEIPT_BYTES {
            return Err(SourceCaptureError::InvalidReceipt);
        }
        if headers
            .get_all(reqwest::header::CONTENT_ENCODING)
            .iter()
            .any(|v| v.as_bytes() != b"identity")
        {
            return Err(SourceCaptureError::InvalidReceipt);
        }
        let receipt: SourceCaptureReceipt = serde_json::from_slice(header.as_bytes())
            .map_err(|_| SourceCaptureError::InvalidReceipt)?;
        validate_receipt(
            &receipt,
            self.lease.intent(),
            self.lease.profile(),
            epoch_now()?,
        )?;
        self.receipt = Some(receipt);
        Ok(())
    }
    pub(crate) fn expected_response_id(&self) -> Result<&str, SourceCaptureError> {
        self.receipt
            .as_ref()
            .map(|r| r.response_id.as_str())
            .ok_or(SourceCaptureError::InvalidReceipt)
    }
    pub(crate) fn verify_body(&mut self, bytes: &[u8]) -> Result<(), SourceCaptureError> {
        self.ensure_active()?;
        if bytes.len() > self.lease.limits().max_response_bytes {
            return Err(SourceCaptureError::ResponseTooLarge);
        }
        let receipt = self
            .receipt
            .as_ref()
            .ok_or(SourceCaptureError::InvalidReceipt)?;
        if sha256(bytes) != receipt.output_sha256 {
            return Err(SourceCaptureError::InvalidCompletion);
        }
        self.body_verified = true;
        Ok(())
    }
    pub(crate) fn complete(self, output: &AssistantMessage) -> Result<(), SourceCaptureError> {
        self.ensure_active()?;
        let receipt = self
            .receipt
            .as_ref()
            .ok_or(SourceCaptureError::InvalidReceipt)?;
        if !self.body_verified
            || output.response_id.as_deref() != Some(receipt.response_id.as_str())
            || !matches!(output.stop_reason.as_str(), "stop" | "length" | "toolUse")
        {
            return Err(SourceCaptureError::InvalidCompletion);
        }
        let mut tools = Vec::new();
        for block in &output.content {
            if let ContentBlock::ToolCall(tool) = block {
                identifier("tool_call_id", &tool.id, 256)?;
                identifier("tool_name", &tool.name, 256)?;
                if tools
                    .iter()
                    .any(|prior: &SourceToolBinding| prior.tool_call_id == tool.id)
                {
                    return Err(SourceCaptureError::InvalidToolBinding);
                }
                tools.push(SourceToolBinding {
                    tool_call_id: tool.id.clone(),
                    tool_name: tool.name.clone(),
                    arguments_sha256: value_digest(&serde_json::Value::Object(
                        tool.arguments.clone(),
                    ))?,
                });
            }
        }
        let mut record = self.lease.0.state.lock();
        if record.state != SourceCaptureState::Submitted {
            return Err(SourceCaptureError::NotSubmitted);
        }
        record.receipt = Some(receipt.clone());
        record.tools = tools;
        record.state = SourceCaptureState::Captured;
        Ok(())
    }
}
impl Drop for SourceSubmission {
    fn drop(&mut self) {
        let mut record = self.lease.0.state.lock();
        if record.state == SourceCaptureState::Submitted {
            record.state = SourceCaptureState::Uncertain;
        }
    }
}

pub fn validate_receipt(
    receipt: &SourceCaptureReceipt,
    intent: &SourceCaptureIntent,
    profile: &SourceCaptureProfile,
    now: u64,
) -> Result<(), SourceCaptureError> {
    intent.validate(now)?;
    if receipt.schema != SOURCE_RECEIPT_SCHEMA
        || receipt.representation != SOURCE_REPRESENTATION
        || receipt.predictor_position_offset != -1
        || receipt.intent_digest != intent.digest()?
    {
        return Err(SourceCaptureError::InvalidReceipt);
    }
    identifier("source_handle", &receipt.source_handle, 128)?;
    for (field, value) in [
        ("native_rid", &receipt.native_rid),
        ("response_id", &receipt.response_id),
        ("capture_commit_id", &receipt.capture_commit_id),
    ] {
        identifier(field, value, 96)?;
    }
    for digest in [
        &receipt.intent_digest,
        &receipt.layout_digest,
        &receipt.accepted_sequence_digest,
        &receipt.output_sha256,
    ] {
        digest_field(digest)?;
    }
    let width = receipt
        .accepted_span_end
        .checked_sub(receipt.accepted_span_start)
        .ok_or(SourceCaptureError::InvalidReceipt)?;
    let element = element_bytes(&receipt.dtype).ok_or(SourceCaptureError::InvalidReceipt)?;
    let logical_bytes = u64::from(receipt.retained_count)
        .checked_mul(u64::from(receipt.hidden_dim))
        .and_then(|v| v.checked_mul(element))
        .ok_or(SourceCaptureError::InvalidReceipt)?;
    if width == 0
        || receipt.retained_count == 0
        || receipt.retained_count > intent.retained_count_limit
        || u64::from(receipt.retained_count) > width
        || logical_bytes == 0
        || receipt.byte_count != logical_bytes
        || receipt.byte_count > intent.max_capture_bytes
        || receipt.hidden_dim != profile.hidden_dim
        || receipt.layer_index != profile.layer_index
        || receipt.dtype != profile.dtype
        || receipt.layout_digest != profile.layout_digest
        || receipt.expected_ranks != profile.expected_ranks
        || receipt.expected_ranks == 0
        || receipt.ready_ranks != receipt.expected_ranks
        || receipt.expires_at_epoch > intent.expires_at_epoch
        || receipt.expires_at_epoch <= now
    {
        return Err(SourceCaptureError::InvalidReceipt);
    }
    Ok(())
}

/// Shared protocol canonical JSON: sorted object keys, compact ASCII, exact integers.
/// Typed protocol deserialization rejects duplicate/unknown keys before this call.
pub fn canonical_json_bytes(value: &serde_json::Value) -> Result<Vec<u8>, SourceCaptureError> {
    fn string(value: &str, out: &mut String) -> Result<(), SourceCaptureError> {
        let escaped = serde_json::to_string(value)
            .map_err(|_| SourceCaptureError::InvalidField("canonical_json"))?;
        for ch in escaped.chars() {
            if ch.is_ascii() {
                out.push(ch);
            } else {
                let mut units = [0u16; 2];
                for unit in ch.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
        Ok(())
    }
    fn encode(value: &serde_json::Value, out: &mut String) -> Result<(), SourceCaptureError> {
        match value {
            serde_json::Value::Null => out.push_str("null"),
            serde_json::Value::Bool(v) => out.push_str(if *v { "true" } else { "false" }),
            serde_json::Value::Number(v) => {
                if v.as_u64().is_none() && v.as_i64().is_none() {
                    return Err(SourceCaptureError::InvalidField("canonical_integer"));
                }
                out.push_str(&v.to_string());
            }
            serde_json::Value::String(v) => string(v, out)?,
            serde_json::Value::Array(values) => {
                out.push('[');
                for (i, v) in values.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    encode(v, out)?;
                }
                out.push(']');
            }
            serde_json::Value::Object(values) => {
                out.push('{');
                let sorted: BTreeMap<_, _> = values.iter().collect();
                for (i, (key, value)) in sorted.into_iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    string(key, out)?;
                    out.push(':');
                    encode(value, out)?;
                }
                out.push('}');
            }
        }
        Ok(())
    }
    let mut out = String::new();
    encode(value, &mut out)?;
    Ok(out.into_bytes())
}

pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn value_digest(value: &serde_json::Value) -> Result<String, SourceCaptureError> {
    fn sorted(value: &serde_json::Value) -> serde_json::Value {
        match value {
            serde_json::Value::Object(map) => {
                let keys: std::collections::BTreeSet<_> = map.keys().collect();
                serde_json::Value::Object(
                    keys.into_iter()
                        .map(|key| (key.clone(), sorted(&map[key])))
                        .collect(),
                )
            }
            serde_json::Value::Array(items) => {
                serde_json::Value::Array(items.iter().map(sorted).collect())
            }
            other => other.clone(),
        }
    }
    serde_json::to_vec(&sorted(value))
        .map(|bytes| sha256(&bytes))
        .map_err(|_| SourceCaptureError::InvalidToolBinding)
}
fn identifier(field: &'static str, value: &str, maximum: usize) -> Result<(), SourceCaptureError> {
    if value.is_empty()
        || value.len() > maximum
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-/".contains(&b))
    {
        return Err(SourceCaptureError::InvalidField(field));
    }
    Ok(())
}
fn digest_field(value: &str) -> Result<(), SourceCaptureError> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(SourceCaptureError::InvalidField("digest"));
    }
    Ok(())
}
fn element_bytes(dtype: &str) -> Option<u64> {
    match dtype {
        "bfloat16" | "float16" => Some(2),
        "float32" => Some(4),
        _ => None,
    }
}
fn epoch_now() -> Result<u64, SourceCaptureError> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_secs())
        .map_err(|_| SourceCaptureError::ClockUnavailable)
}
fn endpoint(value: &str) -> Result<(), SourceCaptureError> {
    if value.len() > 2048
        || value
            .bytes()
            .any(|b| b.is_ascii_control() || b.is_ascii_whitespace())
    {
        return Err(SourceCaptureError::InvalidEndpoint);
    }
    let url = url::Url::parse(value).map_err(|_| SourceCaptureError::InvalidEndpoint)?;
    let loopback = match url.host() {
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        Some(url::Host::Domain(h)) => h == "localhost",
        None => false,
    };
    if !(url.scheme() == "https" || (url.scheme() == "http" && loopback))
        || url.host().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(SourceCaptureError::InvalidEndpoint);
    }
    Ok(())
}
