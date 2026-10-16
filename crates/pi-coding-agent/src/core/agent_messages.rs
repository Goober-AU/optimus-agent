//! Port of packages/coding-agent/src/core/agent-messages.ts
//!
//! Type mapping notes:
//! - `CustomMessage<T>` is carried by `pi_agent_core::types::CustomAgentMessage::Custom`;
//!   `AgentSessionMessage` is therefore a typed view over that variant plus the
//!   validation helpers that the TypeScript declaration provides.
//! - `Map`/`Set` iteration order is observable in the roster output, so ordered
//!   `Vec` collections are used where the TypeScript sorts explicitly.
//! - `randomUUID()` -> `uuid::Uuid::new_v4()`.
//! - `AbortSignal` -> `tokio_util::sync::CancellationToken`.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;

use pi_agent_core::types::{AgentMessage, CustomAgentMessage, CustomMessageContent};
use serde_json::{Map, Value};

use crate::core::messages::{ASYNC_BASH_COMPLETION_CUSTOM_TYPE, HEARTBEAT_PROMPT_CUSTOM_TYPE};
use crate::core::occ::contract::{AgentCommMessage, ContractError};
use crate::core::occ::dialect::{DialectRegistry, OccCapability};
use crate::core::occ::occ1::{Occ1Message, OCC1_HEADER};
use crate::core::occ::occ2::{self, OCC2_FALLBACK_SENTINEL, OCC2_HEADER};

/// Port of `agentFamilyRelationship`'s use of `canonicalSessionPath`: the pure
/// path canonicalisation from core/session-lease.ts, kept private here so this
/// module does not depend on another slice's file.
fn canonical_session_path(session_path: &str) -> String {
    let resolved = std::path::Path::new(session_path);
    let resolved = if resolved.is_absolute() {
        resolved.to_path_buf()
    } else {
        match std::env::current_dir() {
            Ok(cwd) => cwd.join(resolved),
            Err(_) => resolved.to_path_buf(),
        }
    };
    if let Ok(real) = std::fs::canonicalize(&resolved) {
        return real.to_string_lossy().to_string();
    }
    match resolved
        .parent()
        .and_then(|parent| std::fs::canonicalize(parent).ok())
    {
        Some(real_parent) => real_parent
            .join(resolved.file_name().unwrap_or_default())
            .to_string_lossy()
            .to_string(),
        None => resolved.to_string_lossy().to_string(),
    }
}

pub const AGENT_MESSAGE_CUSTOM_TYPE: &str = "agent_message";
pub const AGENT_MESSAGE_SKILL_NAME: &str = "agent-message";
pub const AGENT_MESSAGE_IMPORT_NAME: &str = "agent_message";
pub const AGENT_MESSAGE_SOURCE: &str = "agent_message";
pub const AGENT_MESSAGE_RECEIVED_PREVIEW_LABEL: &str = "Agent message received";
pub const DEFAULT_AGENT_MESSAGE_MAX_CHARS: usize = 16_384;
pub const DEFAULT_AGENT_MESSAGE_MAX_PENDING_PER_SESSION: usize = 20;
pub const DEFAULT_AGENT_MESSAGE_RATE_LIMIT_CAPACITY: f64 = 3.0;
pub const DEFAULT_AGENT_MESSAGE_RATE_LIMIT_REFILL_MS: f64 = 1000.0;

/// Legacy daemon wire input accepted and ignored for compatibility.
pub type AgentSessionMessageDeliveryMode = String;
pub type AgentSessionMessageDeliveryStatus = String;
pub type AgentSessionMessageRuntimeKind = String;
pub type AgentFamilyStatus = String;
pub type AgentFamilyRelationship = String;

pub const DELIVERY_MODE_AUTO: &str = "auto";
pub const DELIVERY_MODE_STEER: &str = "steer";
pub const DELIVERY_MODE_FOLLOW_UP: &str = "follow_up";

pub const DELIVERY_STATUS_DELIVERED: &str = "delivered";
pub const DELIVERY_STATUS_QUEUED: &str = "queued";

pub const RUNTIME_KIND_TOP_LEVEL: &str = "top-level";
pub const RUNTIME_KIND_SUBAGENT: &str = "subagent";

pub const FAMILY_STATUS_RUNNING: &str = "running";
pub const FAMILY_STATUS_IDLE: &str = "idle";
pub const FAMILY_STATUS_INACTIVE: &str = "inactive";

pub const FAMILY_RELATIONSHIP_PARENT: &str = "parent";
pub const FAMILY_RELATIONSHIP_SIBLING: &str = "sibling";
pub const FAMILY_RELATIONSHIP_CHILD: &str = "child";

pub const AGENT_FAMILY_REACH_ERROR: &str =
    "Agent reach is limited to parent, siblings, and children";

#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageEndpoint {
    pub active_session_id: String,
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime_kind: Option<AgentSessionMessageRuntimeKind>,
}

/// `AgentSessionMessageSender extends Partial<AgentSessionMessageEndpoint>`.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageSender {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub active_session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime_kind: Option<AgentSessionMessageRuntimeKind>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub client_id: Option<String>,
}

pub type AgentMessageDirection = String;

pub const AGENT_MESSAGE_DIRECTION_RECEIVED: &str = "received";
pub const AGENT_MESSAGE_DIRECTION_SENT: &str = "sent";

fn trimmed(value: Option<&String>) -> Option<&str> {
    let value = value?.trim();
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}

/// Format the directional role/name segment shared by received and sent agent-message UI.
pub fn format_agent_message_participant(
    direction: &AgentMessageDirection,
    role: Option<&AgentFamilyRelationship>,
    endpoint: Option<&AgentSessionMessageSender>,
) -> String {
    let empty = AgentSessionMessageSender::default();
    let normalized_endpoint = endpoint.unwrap_or(&empty);
    let name_or_id = trimmed(normalized_endpoint.session_name.as_ref())
        .or_else(|| trimmed(normalized_endpoint.active_session_id.as_ref()))
        .or_else(|| trimmed(normalized_endpoint.client_id.as_ref()))
        .or_else(|| trimmed(normalized_endpoint.session_id.as_ref()))
        .unwrap_or("unknown");
    let participant = match role {
        Some(role) => format!("{role} {name_or_id}"),
        None => name_or_id.to_string(),
    };
    let direction_word = if direction == AGENT_MESSAGE_DIRECTION_RECEIVED {
        "from"
    } else {
        "to"
    };
    format!("{direction_word} {participant}")
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageAgentSummary {
    pub active_session_id: String,
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime_kind: Option<AgentSessionMessageRuntimeKind>,
    pub cwd: String,
    pub is_streaming: bool,
    pub unfinished_action_count: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_active_session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rlm_child_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_dir: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rlm_depth: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<AgentFamilyStatus>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rlm_child_registry_status: Option<String>,
    /// OCC-2 dialect capability of this agent (OCC-2 integration design,
    /// section 4). Absent means the agent reported nothing: negotiation
    /// then yields no dialect and sends stay OCC-1.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub occ_capability: Option<OccCapability>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageListResult {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current: Option<AgentSessionMessageEndpoint>,
    pub agents: Vec<AgentSessionMessageAgentSummary>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentFamilyCatalogEntry {
    pub id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub depth: f64,
    pub status: AgentFamilyStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub replied_since_task: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_path: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentFamilyRosterEntry {
    pub relationship: AgentFamilyRelationship,
    pub name: String,
    pub id: String,
    pub depth: f64,
    pub status: AgentFamilyStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub replied_since_task: Option<bool>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentFamilyRosterCurrent {
    pub name: String,
    pub id: String,
    pub depth: f64,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentFamilyRosterResult {
    pub current: AgentFamilyRosterCurrent,
    pub entries: Vec<AgentFamilyRosterEntry>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionNameScope {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_path: Option<String>,
    pub depth: f64,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionNameAvailabilityInput {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_session_path: Option<String>,
    pub depth: f64,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ignore_session_id: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessagePayload {
    pub id: String,
    pub source: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<AgentSessionMessageSender>,
    /// Sender relationship from the receiver's point of view.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from_relationship: Option<AgentFamilyRelationship>,
    pub target: AgentSessionMessageEndpoint,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageDetails {
    pub id: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<AgentSessionMessageSender>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from_relationship: Option<AgentFamilyRelationship>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target: Option<AgentSessionMessageEndpoint>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageReceipt {
    pub id: String,
    pub source: String,
    pub target: AgentSessionMessageEndpoint,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<AgentSessionMessageSender>,
    pub message: String,
    // Not named "status": the kernel host bridge envelope reserves that key.
    pub delivery_status: AgentSessionMessageDeliveryStatus,
    /// Present only for delivered messages: when the target context received it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delivered_at: Option<String>,
    /// Present only for queued messages: when it was placed behind current work.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub queued_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delivery_mode: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageSendInput {
    pub target: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub receiver_role: Option<AgentFamilyRelationship>,
}

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSessionMessageSafetyStatus {
    pub paused: bool,
    pub max_message_chars: f64,
    pub max_pending_per_session: f64,
    pub rate_limit_capacity: f64,
    pub rate_limit_refill_ms: f64,
}

/// Structural reservation key for sibling-scoped session names. JSON encoding keeps
/// parent paths and names containing delimiter characters from colliding into one key;
/// the worker- and supervisor-side reservation maps must never diverge in encoding.
pub fn session_name_reservation_key(input: &AgentSessionNameScope, name: &str) -> String {
    let (parent_type, parent_value) = if input.depth == 0.0 {
        ("root", String::new())
    } else if let Some(parent_session_path) = &input.parent_session_path {
        ("path", canonical_session_path(parent_session_path))
    } else if let Some(parent_session_id) = &input.parent_session_id {
        ("id", parent_session_id.clone())
    } else {
        ("root", String::new())
    };
    // Reservation keys compare serialized bytes across hosts. JavaScript emits
    // integral depths without a decimal suffix (including negative zero).
    let depth = if input.depth.fract() == 0.0 && input.depth.abs() <= 9_007_199_254_740_991.0 {
        serde_json::json!(input.depth as i64)
    } else {
        serde_json::json!(input.depth)
    };
    serde_json::to_string(&serde_json::json!([
        depth,
        parent_type,
        parent_value,
        name
    ]))
    .unwrap_or_default()
}

pub fn format_agent_session_name_unavailable(name: &str, depth: f64) -> String {
    format!(
        "Agent name \"{name}\" is unavailable: an agent of that name already exists at depth {depth} under this parent"
    )
}

pub fn assert_agent_session_name_available(
    catalog: &[AgentFamilyCatalogEntry],
    input: &AgentSessionNameAvailabilityInput,
) -> Result<(), String> {
    let input_scope = AgentSessionNameScope {
        parent_session_id: input.parent_session_id.clone(),
        parent_session_path: input.parent_session_path.clone(),
        depth: input.depth,
    };
    let conflict = catalog.iter().any(|entry| {
        Some(entry.id.as_str()) != input.ignore_session_id.as_deref()
            && entry.name.as_deref() == Some(input.name.as_str())
            && entry.depth == input.depth
            && same_agent_session_name_parent(&catalog_entry_scope(entry), &input_scope, catalog)
    });
    if conflict {
        return Err(format_agent_session_name_unavailable(
            &input.name,
            input.depth,
        ));
    }
    Ok(())
}

fn catalog_entry_scope(entry: &AgentFamilyCatalogEntry) -> AgentSessionNameScope {
    AgentSessionNameScope {
        parent_session_id: entry.parent_session_id.clone(),
        parent_session_path: entry.parent_session_path.clone(),
        depth: entry.depth,
    }
}

pub fn build_agent_family_roster(
    current: &AgentFamilyCatalogEntry,
    catalog: &[AgentFamilyCatalogEntry],
) -> AgentFamilyRosterResult {
    let parent = catalog
        .iter()
        .find(|entry| is_agent_family_parent(entry, current));
    let mut siblings: Vec<&AgentFamilyCatalogEntry> = catalog
        .iter()
        .filter(|entry| {
            entry.id != current.id
                && entry.depth == current.depth
                && same_agent_family_parent(
                    &catalog_entry_scope(entry),
                    &catalog_entry_scope(current),
                    catalog,
                )
        })
        .collect();
    let mut children: Vec<&AgentFamilyCatalogEntry> = catalog
        .iter()
        .filter(|entry| {
            entry.depth == current.depth + 1.0 && is_agent_family_parent(current, entry)
        })
        .collect();

    let name_of =
        |entry: &AgentFamilyCatalogEntry| entry.name.clone().unwrap_or_else(|| entry.id.clone());
    // `localeCompare` on the default locale orders plain identifiers like Rust's
    // byte comparison for the ASCII session names this catalogue contains.
    siblings.sort_by_key(|entry| name_of(entry));
    children.sort_by_key(|entry| name_of(entry));

    let row = |relationship: &str, entry: &AgentFamilyCatalogEntry| AgentFamilyRosterEntry {
        relationship: relationship.to_string(),
        name: name_of(entry),
        id: entry.id.clone(),
        depth: entry.depth,
        status: entry.status.clone(),
        replied_since_task: if relationship == FAMILY_RELATIONSHIP_CHILD {
            entry.replied_since_task
        } else {
            None
        },
    };

    let mut entries: Vec<AgentFamilyRosterEntry> = Vec::new();
    if let Some(parent) = parent {
        entries.push(row(FAMILY_RELATIONSHIP_PARENT, parent));
    }
    for entry in siblings {
        entries.push(row(FAMILY_RELATIONSHIP_SIBLING, entry));
    }
    for entry in children {
        entries.push(row(FAMILY_RELATIONSHIP_CHILD, entry));
    }

    AgentFamilyRosterResult {
        current: AgentFamilyRosterCurrent {
            name: name_of(current),
            id: current.id.clone(),
            depth: current.depth,
        },
        entries,
    }
}

fn same_agent_session_name_parent(
    left: &AgentSessionNameScope,
    right: &AgentSessionNameScope,
    catalog: &[AgentFamilyCatalogEntry],
) -> bool {
    if left.depth == 0.0 && right.depth == 0.0 {
        return true;
    }
    same_agent_family_parent(left, right, catalog)
}

fn same_agent_family_parent(
    left: &AgentSessionNameScope,
    right: &AgentSessionNameScope,
    catalog: &[AgentFamilyCatalogEntry],
) -> bool {
    if left.parent_session_path.is_some() && left.parent_session_path == right.parent_session_path {
        return true;
    }
    if left.parent_session_id.is_some() && left.parent_session_id == right.parent_session_id {
        return true;
    }
    let has_catalog_parent_pair =
        |parent_session_id: Option<&String>, parent_session_path: Option<&String>| {
            let (Some(parent_session_id), Some(parent_session_path)) =
                (parent_session_id, parent_session_path)
            else {
                return false;
            };
            catalog.iter().any(|entry| {
                (Some(entry.id.as_str()) == Some(parent_session_id.as_str())
                    && entry.session_path.as_deref() == Some(parent_session_path.as_str()))
                    || (entry.parent_session_id.as_deref() == Some(parent_session_id.as_str())
                        && entry.parent_session_path.as_deref()
                            == Some(parent_session_path.as_str()))
            })
        };
    if has_catalog_parent_pair(
        left.parent_session_id.as_ref(),
        right.parent_session_path.as_ref(),
    ) || has_catalog_parent_pair(
        right.parent_session_id.as_ref(),
        left.parent_session_path.as_ref(),
    ) {
        return true;
    }
    if left.depth == 0.0
        && right.depth == 0.0
        && left.parent_session_path.is_none()
        && right.parent_session_path.is_none()
        && left.parent_session_id.is_none()
        && right.parent_session_id.is_none()
    {
        return true;
    }
    // Unresolved mixed identifiers stay unrelated to avoid false name conflicts across families.
    false
}

fn is_agent_family_parent(
    parent: &AgentFamilyCatalogEntry,
    child: &AgentFamilyCatalogEntry,
) -> bool {
    (child.parent_session_path.is_some() && child.parent_session_path == parent.session_path)
        || (child.parent_session_id.is_some() && child.parent_session_id == Some(parent.id.clone()))
}

/// Pure nuclear-family policy over persisted parent-edge snapshots.
pub fn agent_family_relationship(
    current: &AgentFamilyCatalogEntry,
    target: &AgentFamilyCatalogEntry,
) -> Option<AgentFamilyRelationship> {
    if current.id == target.id {
        return None;
    }
    if is_agent_family_parent(target, current) {
        return Some(FAMILY_RELATIONSHIP_PARENT.to_string());
    }
    if is_agent_family_parent(current, target) {
        return Some(FAMILY_RELATIONSHIP_CHILD.to_string());
    }
    if current.depth == target.depth {
        let pair = [current.clone(), target.clone()];
        if same_agent_family_parent(
            &catalog_entry_scope(current),
            &catalog_entry_scope(target),
            &pair,
        ) {
            return Some(FAMILY_RELATIONSHIP_SIBLING.to_string());
        }
    }
    None
}

pub fn assert_agent_family_reach(
    current: &AgentFamilyCatalogEntry,
    target: &AgentFamilyCatalogEntry,
) -> Result<AgentFamilyRelationship, String> {
    match agent_family_relationship(current, target) {
        Some(relationship) => Ok(relationship),
        None => Err(AGENT_FAMILY_REACH_ERROR.to_string()),
    }
}

pub fn create_agent_session_message_id() -> String {
    format!("agentmsg_{}", uuid::Uuid::new_v4())
}

pub fn normalize_agent_session_message(message: &str, max_chars: usize) -> Result<String, String> {
    let trimmed = message.trim();
    if trimmed.is_empty() {
        return Err("Agent session message cannot be empty".to_string());
    }
    let length = trimmed.chars().count();
    if length > max_chars {
        return Err(format!(
            "Agent session message is too long: {length} chars exceeds {max_chars}"
        ));
    }
    Ok(trimmed.to_string())
}

pub fn assert_direct_agent_message_target(target: &str) -> Result<String, String> {
    let normalized = target.trim();
    if normalized.is_empty() {
        return Err("Agent message target cannot be empty".to_string());
    }
    let lower = normalized.to_lowercase();
    if normalized == "*" || lower == "all" || lower == "broadcast" {
        return Err("Broadcast agent messaging is not supported".to_string());
    }
    Ok(normalized.to_string())
}

pub fn assert_agent_message_queue_capacity(
    unfinished_action_count: f64,
    max_pending: usize,
) -> Result<(), String> {
    if unfinished_action_count >= max_pending as f64 {
        return Err(format!(
            "Target session has too many pending messages: {unfinished_action_count} unfinished, limit is {max_pending}"
        ));
    }
    Ok(())
}

/// `parseAgentSessionMessagePromptId`: `\n`-split lines, with the optional
/// `[from ...]` relationship line shifting the fixed offsets by one.
pub fn parse_agent_session_message_prompt_id(text: &str) -> Option<String> {
    let lines: Vec<&str> = text.split('\n').collect();
    let offset = if lines
        .first()
        .map(|line| line.starts_with("[from "))
        .unwrap_or(false)
    {
        1
    } else {
        0
    };
    if lines.get(offset).copied() != Some("Agent-to-agent message received.") {
        return None;
    }
    if lines.get(offset + 1).copied() != Some(&format!("Source: {AGENT_MESSAGE_SOURCE}")) {
        return None;
    }
    let to_line_index = if lines
        .get(offset + 2)
        .map(|line| line.starts_with("From: "))
        .unwrap_or(false)
    {
        offset + 3
    } else {
        offset + 2
    };
    if !lines
        .get(to_line_index)
        .map(|line| line.starts_with("To: "))
        .unwrap_or(false)
    {
        return None;
    }
    let candidate = lines.get(to_line_index + 1).copied().unwrap_or("");
    let id = candidate.strip_prefix("Message id: ")?;
    if !id.starts_with("agentmsg_") || id.contains('\n') {
        return None;
    }
    Some(id.to_string())
}

pub fn is_agent_session_message_prompt(text: &str) -> bool {
    parse_agent_session_message_prompt_id(text).is_some()
}

// ---------------------------------------------------------------------------
// OCC-2 payload encoding seam (research/integration/
// OCC2-INTEGRATION-DESIGN.md, sections 1-4). OCC levels are interchangeable
// encodings of one typed payload: this seam substitutes only the single
// `AgentSessionMessagePayload.message` string, so sender identity,
// queueing, receipts, rate limits and family scoping are untouched. With
// the feature disabled (the default) every function here is the identity
// on the body and prompt output stays byte-identical.
// ---------------------------------------------------------------------------

/// Send policy for the OCC-2 wrap seam. `enabled: false` (the default)
/// makes [`wrap_outgoing_body`] the identity function.
#[derive(Debug, Clone, PartialEq)]
pub struct Occ2SendPolicy {
    /// Feature flag (`agentCommunication.occ2Enabled`), default OFF.
    pub enabled: bool,
    /// Negotiated (dialect id, version) for this target; `None` keeps OCC-1.
    pub dialect: Option<(String, u32)>,
    /// Dialect cards available for encoding.
    pub registry: DialectRegistry,
    /// Transport bound mirroring `normalize_agent_session_message`: a dual
    /// payload that would exceed it is never sent (R1: no truncation).
    pub max_message_chars: usize,
}

impl Occ2SendPolicy {
    /// The conservative default: OCC-1 only, byte-identical behavior.
    pub fn disabled() -> Self {
        Self {
            enabled: false,
            dialect: None,
            registry: DialectRegistry::new(),
            max_message_chars: DEFAULT_AGENT_MESSAGE_MAX_CHARS,
        }
    }

    /// An enabled policy for one negotiated dialect.
    pub fn for_dialect(
        registry: DialectRegistry,
        dialect: (String, u32),
        max_message_chars: usize,
    ) -> Self {
        Self {
            enabled: true,
            dialect: Some(dialect),
            registry,
            max_message_chars,
        }
    }
}

/// Negotiates the OCC-2 dialect for one target (design section 4). A
/// target that reports no capability, a peer with no shared dialect, or a
/// dialect that is not registered yields `None` and the send stays OCC-1.
/// Cards must be evaluated with round-trip fidelity 1.0 unless
/// `allow_unevaluated` is set (the dev flag): this is the tier-2 gate of
/// the fallback policy. Model-pair and task-family matching on evolved
/// cards is a daemon-side follow-up.
pub fn select_occ2_dialect(
    local: &OccCapability,
    peer: Option<&OccCapability>,
    registry: &DialectRegistry,
    allow_unevaluated: bool,
) -> Option<(String, u32)> {
    let peer = peer?;
    let (dialect_id, version) = local.negotiate(peer)?;
    let card = registry.get(&dialect_id, version)?;
    let evaluated = card.metrics.samples > 0 && card.metrics.round_trip_fidelity >= 1.0;
    if evaluated || allow_unevaluated {
        Some((dialect_id, version))
    } else {
        None
    }
}

/// Wraps one outgoing body (design section 2). Called at both send seams
/// after the target is known and before the payload is built:
///
/// 1. Disabled or no negotiated dialect: identity.
/// 2. The body must be a complete OCC-1 contract message; plain English,
///    deltas (excluded in v1, OQ4) and malformed OCC-1 pass through.
/// 3. `occ2::encode_dual` emits the symbolic section, the
///    [`OCC2_FALLBACK_SENTINEL`] line and the full OCC-1 text.
/// 4. Any encode error, or a dual payload that would exceed the transport
///    bound, returns the original body (R1: the message is never lost,
///    altered or truncated).
pub fn wrap_outgoing_body(body: &str, policy: &Occ2SendPolicy) -> String {
    if !policy.enabled {
        return body.to_string();
    }
    let Some((dialect_id, version)) = &policy.dialect else {
        return body.to_string();
    };
    if !body.starts_with(OCC1_HEADER) {
        return body.to_string();
    }
    let Ok(occ1) = Occ1Message::decode(body) else {
        return body.to_string();
    };
    let Ok(message) = AgentCommMessage::from_occ1(&occ1) else {
        return body.to_string();
    };
    let Some(card) = policy.registry.get(dialect_id, *version) else {
        return body.to_string();
    };
    let Ok(dual) = occ2::encode_dual(&message, card) else {
        return body.to_string();
    };
    if dual.trim().chars().count() > policy.max_message_chars {
        return body.to_string();
    }
    dual
}

/// Splits a dual OCC-2 payload at the fallback sentinel, mirroring the
/// codec's private splitter: (symbolic section, OCC-1 section). Returns
/// `None` when the body carries no sentinel line.
fn split_occ2_dual_body(body: &str) -> Option<(&str, &str)> {
    let mut offset = 0usize;
    for line in body.split('\n') {
        let line_start = offset;
        let line_end = line_start + line.len();
        offset = line_end + 1;
        let bare = line.strip_suffix('\r').unwrap_or(line);
        if bare == OCC2_FALLBACK_SENTINEL {
            let fallback_start = (line_end + 1).min(body.len());
            return Some((&body[..line_start], &body[fallback_start..]));
        }
    }
    None
}

/// The body a recipient model should see (design section 3): the symbolic
/// section of a dual payload, or the message verbatim when it is not a
/// dual payload (OCC-0/OCC-1, or the feature off). Only bodies that start
/// with the OCC-2 header are ever split, so every other body is
/// byte-identical to the previous behavior.
pub fn agent_session_message_prompt_body(message: &str) -> &str {
    if message.starts_with(OCC2_HEADER) {
        if let Some((symbolic, _)) = split_occ2_dual_body(message) {
            return symbolic.trim_end_matches('\n');
        }
    }
    message
}

/// Incidents per (sender, listener) pair before OCC-2 is disabled for
/// that pair for the session (design section 3, tier 3).
pub const OCC2_STRIKE_LIMIT: usize = 3;

/// Pure strike ledger for OCC-2 decode failures. Session state, no I/O;
/// the runtime recorder wiring is a daemon-side follow-up.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Occ2StrikeLedger {
    strikes: BTreeMap<String, usize>,
    disabled: BTreeSet<String>,
}

impl Occ2StrikeLedger {
    pub fn new() -> Self {
        Self::default()
    }

    /// Incidents recorded for one pair.
    pub fn incidents(&self, pair: &str) -> usize {
        self.strikes.get(pair).copied().unwrap_or(0)
    }

    /// True when the strike limit disabled OCC-2 for this pair.
    pub fn is_disabled(&self, pair: &str) -> bool {
        self.disabled.contains(pair)
    }

    /// Records one incident. Returns true when this strike disabled the
    /// pair.
    pub fn record_incident(&mut self, pair: &str) -> bool {
        let strikes = self.strikes.entry(pair.to_string()).or_insert(0);
        *strikes += 1;
        if *strikes >= OCC2_STRIKE_LIMIT {
            self.disabled.insert(pair.to_string());
            true
        } else {
            false
        }
    }

    /// Pairs disabled by the strike limit, ordered by id.
    pub fn disabled_pairs(&self) -> Vec<&str> {
        self.disabled.iter().map(String::as_str).collect()
    }
}

/// How one delivered body was consumed (design section 3, tier 3).
#[derive(Debug, Clone, PartialEq)]
pub enum Occ2BodyDecode {
    /// Strict `occ2::decode` succeeded on the symbolic section.
    Symbolic(AgentCommMessage),
    /// Consumed through the OCC-1 fallback section, or plain OCC-1.
    Occ1(AgentCommMessage),
}

impl Occ2BodyDecode {
    /// The decoded typed payload, regardless of the consumed section.
    pub fn message(&self) -> &AgentCommMessage {
        match self {
            Occ2BodyDecode::Symbolic(message) | Occ2BodyDecode::Occ1(message) => message,
        }
    }

    /// True when the symbolic path decoded the message.
    pub fn is_symbolic(&self) -> bool {
        matches!(self, Occ2BodyDecode::Symbolic(_))
    }
}

/// Decodes one delivered body: strict OCC-2 first, OCC-1 fallback second.
/// An OCC-2 body whose strict decode fails counts one incident for the
/// pair and is consumed through its embedded OCC-1 section; after
/// [`OCC2_STRIKE_LIMIT`] incidents the pair decodes straight through the
/// fallback. Nothing is invented: a body with no decodable OCC-1 form is
/// an error.
pub fn decode_agent_session_message_body(
    body: &str,
    registry: &DialectRegistry,
    pair: &str,
    ledger: &mut Occ2StrikeLedger,
) -> Result<Occ2BodyDecode, ContractError> {
    if body.starts_with(OCC2_HEADER) && !ledger.is_disabled(pair) {
        match occ2::decode(body, registry) {
            Ok(message) => return Ok(Occ2BodyDecode::Symbolic(message)),
            Err(_) => {
                ledger.record_incident(pair);
            }
        }
    }
    let message = occ2::occ1_fallback(body)?;
    Ok(Occ2BodyDecode::Occ1(message))
}

pub fn create_agent_session_message_prompt(payload: &AgentSessionMessagePayload) -> String {
    let relationship_label = payload.from_relationship.as_ref().map(|relationship| {
        if relationship == FAMILY_RELATIONSHIP_PARENT {
            format!("[from {relationship}]")
        } else {
            let sender = payload.from.as_ref();
            let name = sender
                .and_then(|sender| sender.session_name.clone())
                .or_else(|| sender.and_then(|sender| sender.session_id.clone()))
                .or_else(|| sender.and_then(|sender| sender.active_session_id.clone()))
                .unwrap_or_else(|| "unknown".to_string());
            format!(
                "[from {relationship}:{}]",
                format_agent_session_message_metadata(&name)
            )
        }
    });
    let mut lines: Vec<String> = Vec::new();
    if let Some(label) = relationship_label {
        lines.push(label);
    }
    lines.push("Agent-to-agent message received.".to_string());
    lines.push(format!("Source: {}", payload.source));
    if let Some(from) = &payload.from {
        lines.push(format!(
            "From: {}",
            format_agent_session_message_sender(from)
        ));
    }
    lines.push(format!(
        "To: {}",
        format_agent_session_message_endpoint(&payload.target)
    ));
    lines.push(format!("Message id: {}", payload.id));
    lines.push(String::new());
    // OCC-2 seam: a dual payload renders its symbolic section only; the
    // durable record keeps the full dual body (design section 3).
    lines.push(agent_session_message_prompt_body(&payload.message).to_string());
    lines.join("\n")
}

pub fn create_agent_session_message(
    payload: &AgentSessionMessagePayload,
    timestamp: i64,
) -> CustomAgentMessage {
    let details = AgentSessionMessageDetails {
        id: payload.id.clone(),
        message: payload.message.clone(),
        from: payload.from.clone(),
        from_relationship: payload.from_relationship.clone(),
        target: Some(payload.target.clone()),
    };
    CustomAgentMessage::Custom {
        custom_type: AGENT_MESSAGE_CUSTOM_TYPE.to_string(),
        content: CustomMessageContent::Text(create_agent_session_message_prompt(payload)),
        display: true,
        details: serde_json::to_value(&details).ok(),
        timestamp,
    }
}

pub fn is_agent_session_message(message: &AgentMessage) -> bool {
    let AgentMessage::Custom(CustomAgentMessage::Custom {
        custom_type,
        details,
        ..
    }) = message
    else {
        return false;
    };
    if custom_type != AGENT_MESSAGE_CUSTOM_TYPE {
        return false;
    }
    let Some(details) = details.as_ref().and_then(Value::as_object) else {
        return false;
    };
    details.get("id").map(Value::is_string).unwrap_or(false)
        && details
            .get("message")
            .map(Value::is_string)
            .unwrap_or(false)
}

// A message that starts a new agent run (prompt-turn boundary).
pub fn starts_agent_run(message: &AgentMessage) -> bool {
    if matches!(
        message,
        AgentMessage::Message(pi_ai::types::Message::User(_))
    ) {
        return true;
    }
    if is_agent_session_message(message) {
        return true;
    }
    matches!(
        message,
        AgentMessage::Custom(CustomAgentMessage::Custom { custom_type, .. })
            if custom_type == HEARTBEAT_PROMPT_CUSTOM_TYPE
                || custom_type == ASYNC_BASH_COMPLETION_CUSTOM_TYPE
    )
}

pub fn create_agent_session_message_receipt(
    payload: &AgentSessionMessagePayload,
    status: &AgentSessionMessageDeliveryStatus,
    at: &str,
) -> AgentSessionMessageReceipt {
    AgentSessionMessageReceipt {
        id: payload.id.clone(),
        source: payload.source.clone(),
        target: payload.target.clone(),
        from: payload.from.clone(),
        message: payload.message.clone(),
        delivery_status: status.clone(),
        delivered_at: if status == DELIVERY_STATUS_DELIVERED {
            Some(at.to_string())
        } else {
            None
        },
        queued_at: if status == DELIVERY_STATUS_DELIVERED {
            None
        } else {
            Some(at.to_string())
        },
        delivery_mode: Some(DELIVERY_MODE_STEER.to_string()),
    }
}

fn format_agent_session_message_metadata(value: &str) -> String {
    let mut out = String::new();
    let mut last_was_space = false;
    for ch in value.chars() {
        let is_separator = ch.is_whitespace() || ch == ',' || ch == '[' || ch == ']';
        if is_separator {
            if !last_was_space {
                out.push(' ');
                last_was_space = true;
            }
        } else {
            out.push(ch);
            last_was_space = false;
        }
    }
    out.trim().to_string()
}

fn format_agent_session_message_sender(sender: &AgentSessionMessageSender) -> String {
    let mut parts: Vec<String> = Vec::new();
    if let Some(session_name) = &sender.session_name {
        let formatted = format_agent_session_message_metadata(session_name);
        if !formatted.is_empty() {
            parts.push(formatted);
        }
    }
    if let Some(active_session_id) = &sender.active_session_id {
        parts.push(format!(
            "active {}",
            format_agent_session_message_metadata(active_session_id)
        ));
    }
    if let Some(session_id) = &sender.session_id {
        parts.push(format!(
            "session {}",
            format_agent_session_message_metadata(session_id)
        ));
    }
    if let Some(client_id) = &sender.client_id {
        parts.push(format!(
            "client {}",
            format_agent_session_message_metadata(client_id)
        ));
    }
    if parts.is_empty() {
        "unknown sender".to_string()
    } else {
        parts.join(", ")
    }
}

fn format_agent_session_message_endpoint(endpoint: &AgentSessionMessageEndpoint) -> String {
    let name = endpoint
        .session_name
        .as_ref()
        .map(|name| format!("{}, ", format_agent_session_message_metadata(name)))
        .unwrap_or_default();
    format!(
        "{name}active {}, session {}",
        format_agent_session_message_metadata(&endpoint.active_session_id),
        format_agent_session_message_metadata(&endpoint.session_id)
    )
}

/// Rate limiter state for one key (`{ tokens, updatedAt }`).
#[derive(Debug, Clone, Copy, PartialEq)]
struct RateLimitBucket {
    tokens: f64,
    updated_at: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum RateLimitResult {
    Ok,
    Exceeds { retry_after_ms: f64 },
}

/// Token-bucket limiter; `now` is injected so tests stay deterministic.
pub struct AgentSessionMessageRateLimiter {
    capacity: f64,
    refill_ms: f64,
    buckets: HashMap<String, RateLimitBucket>,
    now: Box<dyn Fn() -> f64 + Send + Sync>,
}

impl Default for AgentSessionMessageRateLimiter {
    fn default() -> Self {
        Self::new(
            DEFAULT_AGENT_MESSAGE_RATE_LIMIT_CAPACITY,
            DEFAULT_AGENT_MESSAGE_RATE_LIMIT_REFILL_MS,
            None,
        )
    }
}

impl AgentSessionMessageRateLimiter {
    pub fn new(
        capacity: f64,
        refill_ms: f64,
        now: Option<Box<dyn Fn() -> f64 + Send + Sync>>,
    ) -> Self {
        Self {
            capacity,
            refill_ms,
            buckets: HashMap::new(),
            now: now.unwrap_or_else(|| {
                Box::new(|| {
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|duration| duration.as_millis() as f64)
                        .unwrap_or(0.0)
                })
            }),
        }
    }

    pub fn try_consume(&mut self, key: &str) -> RateLimitResult {
        let now = (self.now)();
        let mut bucket = self.buckets.get(key).copied().unwrap_or(RateLimitBucket {
            tokens: self.capacity,
            updated_at: now,
        });
        let elapsed = f64::max(0.0, now - bucket.updated_at);
        let refilled_tokens = (elapsed / self.refill_ms).floor();
        if refilled_tokens > 0.0 {
            bucket.tokens = f64::min(self.capacity, bucket.tokens + refilled_tokens);
            bucket.updated_at += refilled_tokens * self.refill_ms;
        }
        if bucket.tokens <= 0.0 {
            self.buckets.insert(key.to_string(), bucket);
            return RateLimitResult::Exceeds {
                retry_after_ms: f64::max(1.0, bucket.updated_at + self.refill_ms - now),
            };
        }
        bucket.tokens -= 1.0;
        self.buckets.insert(key.to_string(), bucket);
        RateLimitResult::Ok
    }

    pub fn refund(&mut self, key: &str) {
        let Some(mut bucket) = self.buckets.get(key).copied() else {
            return;
        };
        bucket.tokens = f64::min(self.capacity, bucket.tokens + 1.0);
        self.buckets.insert(key.to_string(), bucket);
    }

    pub fn clear(&mut self, key: Option<&str>) {
        match key {
            Some(key) => {
                self.buckets.remove(key);
            }
            None => self.buckets.clear(),
        }
    }

    pub fn clear_matching(&mut self, predicate: impl Fn(&str) -> bool) {
        let keys: Vec<String> = self
            .buckets
            .keys()
            .filter(|key| predicate(key))
            .cloned()
            .collect();
        for key in keys {
            self.buckets.remove(&key);
        }
    }
}

/// Controller surface used by the host request handlers.
///
/// `AgentSessionMessageController` is a TypeScript interface with optional
/// members; the Rust trait keeps the same three members and lets each
/// implementation decide whether the optional ones exist.
pub trait AgentSessionMessageController: Send + Sync {
    /// Internal catalog, including passive children; older hosts may omit it.
    fn list_agents(&self) -> pi_ai::types::BoxFuture<Result<Option<AgentSessionMessageListResult>, String>> {
        Box::pin(async { Ok(None) })
    }
    fn roster(
        &self,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<AgentFamilyRosterResult, String>> + Send>,
    >;
    fn await_pending_child_publication(
        &self,
        selector: String,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<Option<String>, String>> + Send>>;
    fn send_agent_message(
        &self,
        input: AgentSessionMessageSendInput,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<AgentSessionMessageReceipt, String>> + Send>,
    >;
    /// OCC-2 send policy for one target id (OCC-2 integration design,
    /// section 4). The default is the conservative disabled policy, so
    /// existing implementors keep OCC-1 behavior unchanged.
    fn occ2_send_policy(&self, target: &str) -> pi_ai::types::BoxFuture<Occ2SendPolicy> {
        let _ = target;
        Box::pin(async { Occ2SendPolicy::disabled() })
    }
}

/// `createAgentMessageHostHandlers(controller)`.
pub fn create_agent_message_host_handlers(
    controller: std::sync::Arc<dyn AgentSessionMessageController>,
) -> crate::core::kernel::shared::HostRequestHandlers {
    use crate::core::kernel::shared::{HostRequestHandler, HostRequestHandlers, KernelError};
    use std::collections::HashMap;

    let mut handlers: crate::core::kernel::shared::HostRequestHandlers = HashMap::new();

    let roster_controller = controller.clone();
    let list_agents: HostRequestHandler = Arc::new(move |_payload: Value| {
        let controller = roster_controller.clone();
        Box::pin(async move {
            match controller.roster().await {
                Ok(roster) => Ok(serde_json::to_value(roster).unwrap_or(Value::Null)),
                Err(error) => Err(KernelError::new(error)),
            }
        })
    });
    handlers.insert("agent_message.list_agents".to_string(), list_agents);

    let send_controller = controller.clone();
    let send: HostRequestHandler = Arc::new(move |payload: Value| {
        let controller = send_controller.clone();
        Box::pin(async move { handle_agent_message_send(controller, payload).await })
    });
    handlers.insert("agent_message.send".to_string(), send);

    handlers
}

async fn handle_agent_message_send(
    controller: Arc<dyn AgentSessionMessageController>,
    payload: Value,
) -> Result<Value, crate::core::kernel::shared::KernelError> {
    use crate::core::kernel::shared::KernelError;

    let object = payload.as_object().cloned().unwrap_or_default();
    let message = match object.get("message") {
        Some(Value::String(message)) => message.clone(),
        _ => {
            return Err(KernelError::new(
                "agent_message.send message must be a string",
            ))
        }
    };
    let receiver_role = object
        .get("receiver_role")
        .and_then(Value::as_str)
        .map(str::to_string);
    let receiver_name = object
        .get("receiver_name")
        .and_then(Value::as_str)
        .map(str::to_string);

    let target: String;
    if let Some(Value::String(positional)) = object.get("target") {
        if positional != "all" {
            return Err(KernelError::new(
                "positional agent_message.send targets are not supported; use receiver_role and receiver_name",
            ));
        }
        if receiver_role.is_some() || receiver_name.is_some() {
            return Err(KernelError::new(
                "agent_message.send broadcast cannot be combined with receiver_role/receiver_name",
            ));
        }
        let roster = controller.roster().await.map_err(KernelError::new)?;
        let mut receipts: Vec<Value> = Vec::with_capacity(roster.entries.len());
        for entry in &roster.entries {
            // OCC-2 S1 seam, broadcast arm: each recipient encodes against
            // its own negotiated dialect (design R6); no receipt change.
            let policy = controller.occ2_send_policy(&entry.id).await;
            let message = wrap_outgoing_body(&message, &policy);
            let input = AgentSessionMessageSendInput {
                target: entry.id.clone(),
                message,
                receiver_role: Some(entry.relationship.clone()),
            };
            match controller.send_agent_message(input).await {
                Ok(receipt) => receipts.push(serde_json::to_value(receipt).unwrap_or(Value::Null)),
                Err(error) => {
                    let mut failed = Map::new();
                    failed.insert("target".to_string(), Value::String(entry.id.clone()));
                    failed.insert("error".to_string(), Value::String(error));
                    receipts.push(Value::Object(failed));
                }
            }
        }
        return Ok(serde_json::json!({ "receipts": receipts }));
    }

    let role =
        match receiver_role.as_deref() {
            Some("parent") => FAMILY_RELATIONSHIP_PARENT.to_string(),
            Some("sibling") => FAMILY_RELATIONSHIP_SIBLING.to_string(),
            Some("child") => FAMILY_RELATIONSHIP_CHILD.to_string(),
            _ => return Err(KernelError::new(
                "agent_message.send receiver_role must be \"parent\", \"sibling\", or \"child\"",
            )),
        };
    if role == FAMILY_RELATIONSHIP_PARENT && receiver_name.is_some() {
        return Err(KernelError::new(
            "agent_message.send receiver_name must be omitted for parent messages",
        ));
    }
    if role != FAMILY_RELATIONSHIP_PARENT
        && receiver_name
            .as_ref()
            .map(|name| name.trim().is_empty())
            .unwrap_or(true)
    {
        return Err(KernelError::new(
            "agent_message.send receiver_name is required for sibling and child messages",
        ));
    }

    let selector = receiver_name.as_ref().map(|name| name.trim().to_string());
    let published_id = if role == FAMILY_RELATIONSHIP_CHILD {
        match &selector {
            Some(selector) => controller
                .await_pending_child_publication(selector.clone())
                .await
                .map_err(KernelError::new)?,
            None => None,
        }
    } else {
        None
    };
    let roster = controller.roster().await.map_err(KernelError::new)?;
    let matches: Vec<&AgentFamilyRosterEntry> = roster
        .entries
        .iter()
        .filter(|entry| {
            entry.relationship == role
                && (role == FAMILY_RELATIONSHIP_PARENT
                    || Some(entry.name.as_str()) == selector.as_deref()
                    || Some(entry.id.as_str()) == selector.as_deref()
                    || published_id.as_deref() == Some(entry.id.as_str()))
        })
        .collect();
    if matches.len() != 1 {
        return Err(KernelError::new(if matches.is_empty() {
            if role == FAMILY_RELATIONSHIP_PARENT {
                "No parent matches the current agent".to_string()
            } else {
                format!(
                    "No {role} matches {}",
                    serde_json::to_string(&receiver_name).unwrap_or_default()
                )
            }
        } else {
            format!(
                "{role} selector {} is ambiguous",
                serde_json::to_string(&receiver_name).unwrap_or_default()
            )
        }));
    }
    target = matches[0].id.clone();

    // OCC-2 S1 seam, direct arm: the body is substituted only when the
    // feature is enabled and a dialect was negotiated for this target;
    // otherwise the send is byte-identical OCC-0/OCC-1.
    let policy = controller.occ2_send_policy(&target).await;
    let message = wrap_outgoing_body(&message, &policy);
    let receipt = controller
        .send_agent_message(AgentSessionMessageSendInput {
            target,
            message,
            receiver_role: Some(role),
        })
        .await
        .map_err(KernelError::new)?;
    Ok(serde_json::to_value(receipt).unwrap_or(Value::Null))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn endpoint(name: Option<&str>, active: &str, session: &str) -> AgentSessionMessageEndpoint {
        AgentSessionMessageEndpoint {
            active_session_id: active.to_string(),
            session_id: session.to_string(),
            session_name: name.map(str::to_string),
            runtime_kind: None,
        }
    }

    fn payload() -> AgentSessionMessagePayload {
        AgentSessionMessagePayload {
            id: "agentmsg_1".to_string(),
            source: AGENT_MESSAGE_SOURCE.to_string(),
            message: "hello".to_string(),
            from: Some(AgentSessionMessageSender {
                active_session_id: Some("active-1".to_string()),
                session_id: Some("session-1".to_string()),
                session_name: Some("parent".to_string()),
                ..Default::default()
            }),
            from_relationship: None,
            target: endpoint(Some("child"), "active-2", "session-2"),
        }
    }

    #[test]
    fn participant_format_falls_back_to_unknown() {
        assert_eq!(
            format_agent_message_participant(
                &AGENT_MESSAGE_DIRECTION_RECEIVED.to_string(),
                None,
                None
            ),
            "from unknown"
        );
        let sender = AgentSessionMessageSender {
            session_name: Some("  name  ".to_string()),
            ..Default::default()
        };
        assert_eq!(
            format_agent_message_participant(
                &AGENT_MESSAGE_DIRECTION_SENT.to_string(),
                Some(&FAMILY_RELATIONSHIP_CHILD.to_string()),
                Some(&sender)
            ),
            "to child name"
        );
    }

    #[test]
    fn prompt_round_trips_through_the_id_parser() {
        let prompt = create_agent_session_message_prompt(&payload());
        assert!(is_agent_session_message_prompt(&prompt));
        assert_eq!(
            parse_agent_session_message_prompt_id(&prompt).as_deref(),
            Some("agentmsg_1")
        );
    }

    #[test]
    fn prompt_with_relationship_line_keeps_the_id_offsets() {
        let mut sibling = payload();
        sibling.from_relationship = Some(FAMILY_RELATIONSHIP_SIBLING.to_string());
        let prompt = create_agent_session_message_prompt(&sibling);
        assert!(prompt.starts_with("[from sibling:parent]"));
        assert_eq!(
            parse_agent_session_message_prompt_id(&prompt).as_deref(),
            Some("agentmsg_1")
        );
        let parent = {
            let mut parent_payload = payload();
            parent_payload.from_relationship = Some(FAMILY_RELATIONSHIP_PARENT.to_string());
            create_agent_session_message_prompt(&parent_payload)
        };
        assert!(parent.starts_with("[from parent]"));
        assert_eq!(
            parse_agent_session_message_prompt_id(&parent).as_deref(),
            Some("agentmsg_1")
        );
    }

    #[test]
    fn prompt_parser_rejects_unrelated_text() {
        assert!(!is_agent_session_message_prompt("hello"));
        assert!(parse_agent_session_message_prompt_id(
            "Agent-to-agent message received.\nSource: other\nTo: x\nMessage id: agentmsg_1"
        )
        .is_none());
    }

    #[test]
    fn message_detection_uses_details_shape() {
        let message: AgentMessage = crate::core::messages::custom_message_to_agent_message(
            match create_agent_session_message(&payload(), 5) {
                CustomAgentMessage::Custom {
                    custom_type,
                    content,
                    display,
                    details,
                    timestamp,
                } => crate::core::messages::CustomMessage {
                    role: "custom".to_string(),
                    custom_type,
                    content,
                    display,
                    details,
                    timestamp,
                },
                _ => unreachable!("agent session messages are custom messages"),
            },
        );
        assert!(is_agent_session_message(&message));
        assert!(starts_agent_run(&message));
        let other = AgentMessage::Custom(CustomAgentMessage::Custom {
            custom_type: "other".to_string(),
            content: CustomMessageContent::Text("x".to_string()),
            display: true,
            details: None,
            timestamp: 1,
        });
        assert!(!is_agent_session_message(&other));
        assert!(!starts_agent_run(&other));
    }

    #[test]
    fn receipt_marks_delivered_and_queued_timestamps() {
        let delivered = create_agent_session_message_receipt(
            &payload(),
            &DELIVERY_STATUS_DELIVERED.to_string(),
            "2026-01-01T00:00:00.000Z",
        );
        assert_eq!(
            delivered.delivered_at.as_deref(),
            Some("2026-01-01T00:00:00.000Z")
        );
        assert!(delivered.queued_at.is_none());
        assert_eq!(delivered.delivery_mode.as_deref(), Some("steer"));
        let queued = create_agent_session_message_receipt(
            &payload(),
            &DELIVERY_STATUS_QUEUED.to_string(),
            "2026-01-01T00:00:00.000Z",
        );
        assert!(queued.delivered_at.is_none());
        assert!(queued.queued_at.is_some());
    }

    #[test]
    fn message_normalisation_enforces_limits() {
        assert_eq!(normalize_agent_session_message("  hi  ", 10).unwrap(), "hi");
        assert!(normalize_agent_session_message("   ", 10).is_err());
        assert_eq!(
            normalize_agent_session_message("abcd", 3).unwrap_err(),
            "Agent session message is too long: 4 chars exceeds 3"
        );
    }

    #[test]
    fn direct_target_rejects_broadcast() {
        assert_eq!(
            assert_direct_agent_message_target(" sub-1 ").unwrap(),
            "sub-1"
        );
        assert!(assert_direct_agent_message_target("").is_err());
        assert!(assert_direct_agent_message_target("*").is_err());
        assert!(assert_direct_agent_message_target("ALL").is_err());
        assert!(assert_direct_agent_message_target("Broadcast").is_err());
    }

    #[test]
    fn queue_capacity_matches_limit_message() {
        assert!(assert_agent_message_queue_capacity(19.0, 20).is_ok());
        assert_eq!(
            assert_agent_message_queue_capacity(20.0, 20).unwrap_err(),
            "Target session has too many pending messages: 20 unfinished, limit is 20"
        );
    }

    fn entry(id: &str, name: &str, depth: f64, parent_id: Option<&str>) -> AgentFamilyCatalogEntry {
        AgentFamilyCatalogEntry {
            id: id.to_string(),
            name: Some(name.to_string()),
            depth,
            status: FAMILY_STATUS_RUNNING.to_string(),
            replied_since_task: Some(false),
            parent_session_id: parent_id.map(str::to_string),
            parent_session_path: None,
            session_path: Some(format!("/sessions/{id}.jsonl")),
        }
    }

    #[test]
    fn relationship_covers_parent_child_sibling_and_none() {
        let current = entry("self", "self", 1.0, Some("parent"));
        let parent = entry("parent", "parent", 0.0, None);
        let sibling = entry("sib", "sib", 1.0, Some("parent"));
        let child = entry("kid", "kid", 2.0, Some("self"));
        assert_eq!(
            agent_family_relationship(&current, &parent).as_deref(),
            Some("parent")
        );
        assert_eq!(
            agent_family_relationship(&current, &child).as_deref(),
            Some("child")
        );
        assert_eq!(
            agent_family_relationship(&current, &sibling).as_deref(),
            Some("sibling")
        );
        assert!(agent_family_relationship(&current, &current).is_none());
        assert!(
            agent_family_relationship(&current, &entry("far", "far", 3.0, Some("other"))).is_none()
        );
        assert_eq!(
            assert_agent_family_reach(&current, &entry("far", "far", 3.0, Some("other")))
                .unwrap_err(),
            AGENT_FAMILY_REACH_ERROR
        );
    }

    #[test]
    fn roster_orders_parent_siblings_then_children() {
        let current = entry("self", "self", 1.0, Some("parent"));
        let catalog = vec![
            current.clone(),
            entry("parent", "parent", 0.0, None),
            entry("sib-b", "b", 1.0, Some("parent")),
            entry("sib-a", "a", 1.0, Some("parent")),
            entry("kid", "kid", 2.0, Some("self")),
        ];
        let roster = build_agent_family_roster(&current, &catalog);
        assert_eq!(roster.current.name, "self");
        let relationships: Vec<&str> = roster
            .entries
            .iter()
            .map(|entry| entry.relationship.as_str())
            .collect();
        assert_eq!(relationships, vec!["parent", "sibling", "sibling", "child"]);
        assert_eq!(roster.entries[1].name, "a");
        assert_eq!(roster.entries[3].replied_since_task, Some(false));
        assert!(roster.entries[0].replied_since_task.is_none());
    }

    #[test]
    fn name_availability_reports_the_conflict_message() {
        let catalog = vec![entry("self", "taken", 1.0, Some("parent"))];
        let input = AgentSessionNameAvailabilityInput {
            parent_session_id: Some("parent".to_string()),
            parent_session_path: None,
            depth: 1.0,
            name: "taken".to_string(),
            ignore_session_id: None,
        };
        assert_eq!(
            assert_agent_session_name_available(&catalog, &input).unwrap_err(),
            "Agent name \"taken\" is unavailable: an agent of that name already exists at depth 1 under this parent"
        );
        let ignoring = AgentSessionNameAvailabilityInput {
            ignore_session_id: Some("self".to_string()),
            ..input.clone()
        };
        assert!(assert_agent_session_name_available(&catalog, &ignoring).is_ok());
    }

    #[test]
    fn reservation_key_encodes_depth_parent_and_name() {
        let scope = AgentSessionNameScope {
            parent_session_id: Some("parent".to_string()),
            parent_session_path: None,
            depth: 1.0,
        };
        assert_eq!(
            session_name_reservation_key(&scope, "kid"),
            "[1,\"id\",\"parent\",\"kid\"]"
        );
        let root = AgentSessionNameScope {
            parent_session_id: None,
            parent_session_path: None,
            depth: 0.0,
        };
        assert_eq!(
            session_name_reservation_key(&root, "kid"),
            "[0,\"root\",\"\",\"kid\"]"
        );
    }

    #[test]
    fn rate_limiter_refills_and_reports_retry_after() {
        let now = std::sync::Arc::new(std::sync::Mutex::new(0.0_f64));
        let clock = now.clone();
        let mut limiter = AgentSessionMessageRateLimiter::new(
            3.0,
            1000.0,
            Some(Box::new(move || *clock.lock().unwrap())),
        );
        assert_eq!(limiter.try_consume("k"), RateLimitResult::Ok);
        assert_eq!(limiter.try_consume("k"), RateLimitResult::Ok);
        assert_eq!(limiter.try_consume("k"), RateLimitResult::Ok);
        assert_eq!(
            limiter.try_consume("k"),
            RateLimitResult::Exceeds {
                retry_after_ms: 1000.0
            }
        );
        limiter.refund("k");
        assert_eq!(limiter.try_consume("k"), RateLimitResult::Ok);
        *now.lock().unwrap() = 1000.0;
        assert_eq!(limiter.try_consume("k"), RateLimitResult::Ok);
        limiter.clear_matching(|key| key == "k");
        assert_eq!(limiter.try_consume("k"), RateLimitResult::Ok);
        limiter.clear(None);
        assert_eq!(limiter.try_consume("k"), RateLimitResult::Ok);
    }

    #[test]
    fn message_id_uses_the_agentmsg_prefix() {
        assert!(create_agent_session_message_id().starts_with("agentmsg_"));
        assert!(serde_json::to_value(AGENT_MESSAGE_CUSTOM_TYPE).is_ok());
        let _ = json!({});
    }

    // -- OCC-2 seam unit tests (u2/u3 of the integration design) --------

    fn occ1_contract_body() -> String {
        let message = crate::core::occ::contract::AgentCommMessage::new("TASK", "t1", "pending");
        message
            .to_occ1()
            .expect("to_occ1")
            .encode()
            .expect("encode")
    }

    fn enabled_policy() -> Occ2SendPolicy {
        Occ2SendPolicy::for_dialect(
            DialectRegistry::with_sym_v1(),
            ("sym-v1".to_string(), 1),
            DEFAULT_AGENT_MESSAGE_MAX_CHARS,
        )
    }

    #[test]
    fn occ2_disabled_policy_is_the_identity() {
        let body = occ1_contract_body();
        assert_eq!(wrap_outgoing_body(&body, &Occ2SendPolicy::disabled()), body);
        assert_eq!(wrap_outgoing_body("hello", &enabled_policy()), "hello");
        // Enabled but no negotiated dialect: identity.
        let mut policy = enabled_policy();
        policy.dialect = None;
        assert_eq!(wrap_outgoing_body(&body, &policy), body);
    }

    #[test]
    fn occ2_wrap_encodes_contract_bodies_as_dual() {
        let body = occ1_contract_body();
        let dual = wrap_outgoing_body(&body, &enabled_policy());
        assert!(dual.starts_with("OCC/2 D=sym-v1@1 C="), "dual header");
        assert!(dual.contains("\n#FALLBACK-OCC1\n"), "sentinel section");
        assert!(dual.ends_with(body.trim_end_matches('\n')) || dual.contains(&body));
        let registry = DialectRegistry::with_sym_v1();
        let decoded = crate::core::occ::occ2::decode(&dual, &registry).expect("strict decode");
        assert_eq!(
            decoded,
            crate::core::occ::contract::AgentCommMessage::from_occ1(
                &Occ1Message::decode(&body).expect("decode")
            )
            .expect("from_occ1")
        );
    }

    #[test]
    fn occ2_wrap_leaves_plain_english_and_deltas_unchanged() {
        // Plain English never parses as an OCC-1 contract message.
        assert_eq!(wrap_outgoing_body("plain english body", &enabled_policy()), "plain english body");
        // A delta body (no KIND) is excluded from wrapping in v1 (OQ4).
        let delta_body = "OCC/1\nTASK=t\nSEQ=2\nBASE=1\nSTATUS=done";
        assert_eq!(wrap_outgoing_body(delta_body, &enabled_policy()), delta_body);
        // Malformed OCC-1 passes through unchanged.
        let malformed = "OCC/1\nTASK=t\nKIND=RESULT\nSTATUS=weird";
        assert_eq!(wrap_outgoing_body(malformed, &enabled_policy()), malformed);
    }

    #[test]
    fn occ2_size_guard_sends_plain_occ1_instead_of_truncating() {
        // A body near the transport bound: its dual form would exceed the
        // bound, so the original OCC-1 body is sent, never truncated.
        let mut message =
            crate::core::occ::contract::AgentCommMessage::new("RESULT", "t-big", "done");
        for index in 0..6 {
            message.findings.push(crate::core::occ::contract::Finding {
                id: format!("F{}", index + 1),
                statement: "x".repeat(2_500),
                confidence: None,
                evidence: Vec::new(),
            });
        }
        let body = message.to_occ1().expect("to_occ1").encode().expect("encode");
        assert!(body.chars().count() <= DEFAULT_AGENT_MESSAGE_MAX_CHARS);
        let dual = crate::core::occ::occ2::encode_dual(
            &message,
            DialectRegistry::with_sym_v1().get("sym-v1", 1).expect("card"),
        )
        .expect("encode_dual");
        assert!(dual.chars().count() > DEFAULT_AGENT_MESSAGE_MAX_CHARS);
        assert_eq!(wrap_outgoing_body(&body, &enabled_policy()), body);
    }

    #[test]
    fn occ2_prompt_shows_symbolic_section_and_record_keeps_dual() {
        let body = occ1_contract_body();
        let dual = wrap_outgoing_body(&body, &enabled_policy());
        let mut with_dual = payload();
        with_dual.message = dual.clone();
        let prompt = create_agent_session_message_prompt(&with_dual);
        assert!(prompt.starts_with("Agent-to-agent message received.\nSource: agent_message\n"));
        assert!(prompt.contains("OCC/2 D=sym-v1@1 C="));
        assert!(!prompt.contains("#FALLBACK-OCC1"));
        assert!(!prompt.contains("OCC/1\n"));
        assert_eq!(
            parse_agent_session_message_prompt_id(&prompt).as_deref(),
            Some("agentmsg_1")
        );
        // The durable record keeps the dual payload verbatim.
        let record = create_agent_session_message(&with_dual, 5);
        let CustomAgentMessage::Custom { details: Some(details), .. } = &record else {
            panic!("agent session message is a custom message");
        };
        let stored: AgentSessionMessageDetails = serde_json::from_value(details.clone()).unwrap();
        assert_eq!(stored.message, dual);
        // A plain body renders byte-identically to the pre-OCC-2 format.
        let mut plain = payload();
        plain.message = body.clone();
        let expected = format!(
            "Agent-to-agent message received.\nSource: {}\nFrom: parent, active active-1, session session-1\nTo: child, active active-2, session session-2\nMessage id: agentmsg_1\n\n{}",
            AGENT_MESSAGE_SOURCE,
            body
        );
        assert_eq!(create_agent_session_message_prompt(&plain), expected);
    }

    #[test]
    fn occ2_decode_strict_then_fallback_with_three_strikes() {
        let body = occ1_contract_body();
        let dual = wrap_outgoing_body(&body, &enabled_policy());
        let empty_registry = DialectRegistry::new();
        let mut ledger = Occ2StrikeLedger::new();
        let pair = "sender->listener";
        for round in 1..=OCC2_STRIKE_LIMIT {
            let decoded =
                decode_agent_session_message_body(&dual, &empty_registry, pair, &mut ledger)
                    .expect("fallback decodes the embedded OCC-1");
            assert!(!decoded.is_symbolic(), "unknown dialect consumes via fallback");
            assert_eq!(ledger.incidents(pair), round);
        }
        assert!(ledger.is_disabled(pair));
        assert_eq!(ledger.disabled_pairs(), vec![pair]);
        // Disabled pairs decode straight through the fallback, no more strikes.
        let decoded =
            decode_agent_session_message_body(&dual, &empty_registry, pair, &mut ledger).unwrap();
        assert!(!decoded.is_symbolic());
        assert_eq!(ledger.incidents(pair), OCC2_STRIKE_LIMIT);
        // With the dialect registered the strict path wins and costs nothing.
        let registry = DialectRegistry::with_sym_v1();
        let mut fresh = Occ2StrikeLedger::new();
        let decoded = decode_agent_session_message_body(&dual, &registry, pair, &mut fresh).unwrap();
        assert!(decoded.is_symbolic());
        assert_eq!(fresh.incidents(pair), 0);
    }

    /// A minimal `AgentSessionMessageController` that records sends, for
    /// host-handler seam tests.
    struct RecordingController {
        sent: std::sync::Mutex<Vec<(String, String)>>,
        occ2: bool,
    }

    impl AgentSessionMessageController for RecordingController {
        fn roster(
            &self,
        ) -> std::pin::Pin<
            Box<dyn std::future::Future<Output = Result<AgentFamilyRosterResult, String>> + Send>,
        > {
            let entries = vec![
                AgentFamilyRosterEntry {
                    relationship: FAMILY_RELATIONSHIP_PARENT.to_string(),
                    name: "parent".to_string(),
                    id: "parent-id".to_string(),
                    depth: 0.0,
                    status: FAMILY_STATUS_RUNNING.to_string(),
                    replied_since_task: None,
                },
                AgentFamilyRosterEntry {
                    relationship: FAMILY_RELATIONSHIP_SIBLING.to_string(),
                    name: "sibling".to_string(),
                    id: "sibling-id".to_string(),
                    depth: 1.0,
                    status: FAMILY_STATUS_RUNNING.to_string(),
                    replied_since_task: None,
                },
            ];
            Box::pin(async move { Ok(AgentFamilyRosterResult { current: AgentFamilyRosterCurrent { name: "self".into(), id: "self-id".into(), depth: 1.0 }, entries }) })
        }

        fn await_pending_child_publication(
            &self,
            _selector: String,
        ) -> std::pin::Pin<
            Box<dyn std::future::Future<Output = Result<Option<String>, String>> + Send>,
        > {
            Box::pin(async move { Ok(None) })
        }

        fn send_agent_message(
            &self,
            input: AgentSessionMessageSendInput,
        ) -> std::pin::Pin<
            Box<dyn std::future::Future<Output = Result<AgentSessionMessageReceipt, String>> + Send>,
        > {
            self.sent
                .lock()
                .unwrap()
                .push((input.target.clone(), input.message.clone()));
            Box::pin(async move {
                Ok(AgentSessionMessageReceipt {
                    id: create_agent_session_message_id(),
                    source: AGENT_MESSAGE_SOURCE.to_string(),
                    target: AgentSessionMessageEndpoint {
                        active_session_id: input.target.clone(),
                        session_id: input.target,
                        ..Default::default()
                    },
                    from: None,
                    message: input.message,
                    delivery_status: DELIVERY_STATUS_DELIVERED.to_string(),
                    delivered_at: None,
                    queued_at: None,
                    delivery_mode: None,
                })
            })
        }

        fn occ2_send_policy(&self, _target: &str) -> pi_ai::types::BoxFuture<Occ2SendPolicy> {
            let enabled = self.occ2;
            Box::pin(async move {
                if enabled {
                    Occ2SendPolicy::for_dialect(
                        DialectRegistry::with_sym_v1(),
                        ("sym-v1".to_string(), 1),
                        DEFAULT_AGENT_MESSAGE_MAX_CHARS,
                    )
                } else {
                    Occ2SendPolicy::disabled()
                }
            })
        }
    }

    async fn run_send_handler(
        controller: Arc<RecordingController>,
        payload: Value,
    ) -> Result<Value, crate::core::kernel::shared::KernelError> {
        let handlers = create_agent_message_host_handlers(controller);
        let send = handlers.get("agent_message.send").expect("send handler");
        send(payload).await
    }

    #[tokio::test]
    async fn occ2_host_send_seam_wraps_only_when_enabled() {
        let body = occ1_contract_body();

        // Disabled: the direct arm sends the body byte-identically.
        let disabled = Arc::new(RecordingController {
            sent: std::sync::Mutex::new(Vec::new()),
            occ2: false,
        });
        run_send_handler(
            disabled.clone(),
            serde_json::json!({ "message": body, "receiver_role": "parent" }),
        )
        .await
        .expect("disabled send succeeds");
        assert_eq!(disabled.sent.lock().unwrap()[0].1, body, "no wrapping when disabled");

        // Enabled: the direct arm sends the dual payload.
        let enabled = Arc::new(RecordingController {
            sent: std::sync::Mutex::new(Vec::new()),
            occ2: true,
        });
        run_send_handler(
            enabled.clone(),
            serde_json::json!({ "message": body, "receiver_role": "parent" }),
        )
        .await
        .expect("enabled send succeeds");
        let sent = enabled.sent.lock().unwrap();
        assert_eq!(sent.len(), 1);
        assert!(sent[0].1.starts_with("OCC/2 D=sym-v1@1 C="), "dual payload sent");
        assert_eq!(
            sent[0].1.matches("#FALLBACK-OCC1").count(),
            1,
            "exactly one sentinel: the wrap never compounds"
        );

        // Broadcast: every roster entry receives its own single wrap of the
        // original body (R6), never a re-wrap of a wrapped body.
        let broadcast = Arc::new(RecordingController {
            sent: std::sync::Mutex::new(Vec::new()),
            occ2: true,
        });
        let result = run_send_handler(
            broadcast.clone(),
            serde_json::json!({ "target": "all", "message": body }),
        )
        .await
        .expect("broadcast succeeds");
        let receipts = result
            .get("receipts")
            .and_then(Value::as_array)
            .expect("broadcast receipts");
        assert_eq!(receipts.len(), 2);
        let sent = broadcast.sent.lock().unwrap();
        assert_eq!(sent.len(), 2, "one send per roster entry");
        for (target, message) in sent.iter() {
            assert!(message.starts_with("OCC/2 D=sym-v1@1 C="), "{target}: dual payload");
            assert_eq!(
                message.matches("#FALLBACK-OCC1").count(),
                1,
                "{target}: no compound wrapping across broadcast entries"
            );
            assert!(message.contains(body.trim_end_matches('\n')), "{target}: OCC-1 section intact");
        }
    }

    #[test]
    fn occ2_negotiation_absence_keeps_occ1() {
        let local = OccCapability::here();
        let registry = DialectRegistry::with_sym_v1();
        assert_eq!(
            select_occ2_dialect(&local, Some(&local), &registry, true),
            Some(("sym-v1".to_string(), 1))
        );
        // No reported capability: OCC-1.
        assert_eq!(select_occ2_dialect(&local, None, &registry, true), None);
        // A peer that speaks OCC-1 only: OCC-1.
        let occ1_only = OccCapability { occ1: true, dialects: BTreeMap::new() };
        assert_eq!(select_occ2_dialect(&local, Some(&occ1_only), &registry, true), None);
        // Unevaluated cards are refused without the dev override.
        assert_eq!(select_occ2_dialect(&local, Some(&local), &registry, false), None);
        // The summary field is absent for sessions that report nothing.
        let absent = AgentSessionMessageAgentSummary::default();
        assert!(absent.occ_capability.is_none());
        let serialized = serde_json::to_value(&absent).unwrap();
        assert!(serialized.get("occCapability").is_none());
        let present = AgentSessionMessageAgentSummary {
            occ_capability: Some(OccCapability::here()),
            ..absent
        };
        let serialized = serde_json::to_value(&present).unwrap();
        assert_eq!(serialized["occCapability"]["occ1"], json!(true));
        let round: AgentSessionMessageAgentSummary =
            serde_json::from_value(serialized).unwrap();
        assert_eq!(round.occ_capability, Some(OccCapability::here()));
    }
}
