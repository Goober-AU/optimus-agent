//! `optimus.agent-comm.v1`: the typed inter-agent task/result contract.
//!
//! The contract is the shared, typed payload that all text-based OCC levels
//! encode. OCC-1 is one encoding of it (see [`crate::core::occ::occ1`]); a
//! learned symbolic dialect (OCC-2) or a latent channel (OCC-3) would be
//! others. Keeping one typed contract behind every encoding means task/result
//! semantics do not fork per representation.
//!
//! Security model:
//! - `sender_identity` is established by the daemon transport. Agent text is
//!   never trusted as sender authentication; a decoded value is at best a
//!   claim that the transport must overwrite or verify.
//! - `DeliveryReceipt` states are transport lifecycle facts. An `accepted`
//!   or `delivered` receipt is NOT proof that the receiving agent completed
//!   the task; only `processed`/`result_reported`/`verified` carry stronger
//!   meaning, and verification must stay separate from narrative claims.
//! - Parsers never invent missing required values: decoding an OCC-1 message
//!   without an explicit field leaves the corresponding `Option` as `None`.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::core::occ::occ1::{is_known_kind, is_known_status, Occ1Error, Occ1Message};

/// Protocol identity stamped on every typed message.
pub const AGENT_COMM_PROTOCOL: &str = "optimus.agent-comm.v1";

/// Message kinds.
pub mod kinds {
    /// Assign a task.
    pub const TASK: &str = "TASK";
    /// Progress update on an accepted task.
    pub const PROGRESS: &str = "PROGRESS";
    /// Result payload for a task.
    pub const RESULT: &str = "RESULT";
    /// Evidence-only payload.
    pub const EVIDENCE: &str = "EVIDENCE";
    /// Request additional detail from a peer.
    pub const REQUEST_DETAIL: &str = "REQUEST_DETAIL";
    /// Acknowledge a specific prior message.
    pub const ACK: &str = "ACK";
    /// Correct a previously reported claim.
    pub const CORRECTION: &str = "CORRECTION";
    /// Report that work cannot proceed.
    pub const BLOCKED: &str = "BLOCKED";
    /// Report cancellation.
    pub const CANCELLED: &str = "CANCELLED";
}

/// Task statuses a message can assert about its task.
pub mod statuses {
    /// Task accepted, not started.
    pub const PENDING: &str = "pending";
    /// Work started, not finished.
    pub const IN_PROGRESS: &str = "in_progress";
    /// Finished successfully.
    pub const DONE: &str = "done";
    /// Finished partially; scope remains.
    pub const PARTIAL: &str = "partial";
    /// Cannot proceed.
    pub const BLOCKED: &str = "blocked";
    /// Finished unsuccessfully.
    pub const FAILED: &str = "failed";
    /// Cancelled before completion.
    pub const CANCELLED: &str = "cancelled";
}

/// Delivery lifecycle states for receipts. Distinct from task status.
pub mod delivery {
    /// The transport accepted the message into its queue.
    pub const ACCEPTED: &str = "accepted";
    /// The message is queued for delivery.
    pub const QUEUED: &str = "queued";
    /// The message reached the recipient agent.
    pub const DELIVERED: &str = "delivered";
    /// The recipient agent processed the message.
    pub const PROCESSED: &str = "processed";
    /// The recipient reported a result for the referenced task.
    pub const RESULT_REPORTED: &str = "result_reported";
    /// An independent check verified the reported result.
    pub const VERIFIED: &str = "verified";
}

/// Evidence kinds. `locator` semantics are precise per kind.
pub mod evidence_kinds {
    /// A source file; `locator` is a repository-relative path.
    pub const SOURCE: &str = "src";
    /// A git commit; `locator` is the commit SHA.
    pub const COMMIT: &str = "commit";
    /// A test log; `locator` is a path or log id.
    pub const TEST_LOG: &str = "log";
    /// A build result; `locator` is a path or build id.
    pub const BUILD_RESULT: &str = "build";
    /// A tool execution receipt; `locator` is the receipt id or path.
    pub const TOOL_RECEIPT: &str = "tool";
    /// An artifact digest; `digest` carries the hash.
    pub const ARTIFACT_DIGEST: &str = "artifact";
    /// A source line range; use with `line_range` on a `SOURCE` ref.
    pub const LINE_RANGE: &str = "lines";
    /// A prior agent finding; `locator` is the prior finding/message id.
    pub const PRIOR_FINDING: &str = "finding";
}

/// Returns true for a known evidence kind.
pub fn is_known_evidence_kind(kind: &str) -> bool {
    matches!(
        kind,
        evidence_kinds::SOURCE
            | evidence_kinds::COMMIT
            | evidence_kinds::TEST_LOG
            | evidence_kinds::BUILD_RESULT
            | evidence_kinds::TOOL_RECEIPT
            | evidence_kinds::ARTIFACT_DIGEST
            | evidence_kinds::LINE_RANGE
            | evidence_kinds::PRIOR_FINDING
    )
}

/// Verification statuses.
pub mod verification {
    /// The claim was verified to hold.
    pub const PASS: &str = "pass";
    /// The claim was verified to fail.
    pub const FAIL: &str = "fail";
    /// Verification has not been performed or was inconclusive.
    pub const UNKNOWN: &str = "unknown";
}

/// Contract validation or conversion failure.
#[derive(Debug, Error)]
pub enum ContractError {
    /// A required field was absent.
    #[error("contract field {field:?} is required")]
    MissingRequired { field: &'static str },
    /// `task_id` was empty or contained control characters.
    #[error("task_id {0:?} is empty or malformed")]
    InvalidTaskId(String),
    /// `kind` was not a known message kind.
    #[error("unknown message kind {0:?}")]
    UnknownKind(String),
    /// `status` was not a known task status.
    #[error("unknown task status {0:?}")]
    UnknownStatus(String),
    /// `protocol` did not match [`AGENT_COMM_PROTOCOL`].
    #[error("protocol {0:?} does not match {AGENT_COMM_PROTOCOL:?}")]
    ProtocolMismatch(String),
    /// A confidence value was outside `[0, 1]`.
    #[error("confidence {0} is outside [0, 1]")]
    InvalidConfidence(f32),
    /// A finding referenced evidence ids that do not exist.
    #[error("finding {finding_id:?} references unknown evidence {evidence_id:?}")]
    UnknownEvidence { finding_id: String, evidence_id: String },
    /// An evidence kind was unknown.
    #[error("unknown evidence kind {0:?}")]
    UnknownEvidenceKind(String),
    /// A timestamp was not RFC 3339.
    #[error("timestamp {0:?} is not RFC 3339")]
    InvalidTimestamp(String),
    /// An OCC-1 conversion rule was violated (index gaps, dangling refs...).
    #[error("OCC-1 conversion error: {0}")]
    Occ1Conversion(String),
    /// The underlying OCC-1 codec failed.
    #[error(transparent)]
    Occ1(#[from] Occ1Error),
}

/// A reference to verifiable evidence.
///
/// Evidence is referenced, not copied: recipients retrieve the underlying
/// artifact on demand. Provenance is carried by `revision` (e.g. the git SHA
/// the locator was read at) and integrity by `digest` (e.g. sha256 hex).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvidenceRef {
    /// Stable id unique within the message (e.g. `E1`).
    pub id: String,
    /// One of the [`evidence_kinds`] constants.
    pub kind: String,
    /// Kind-specific locator: path, SHA, log id, receipt id, or prior finding id.
    pub locator: String,
    /// Inclusive line range when the kind admits one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub line_range: Option<(u32, u32)>,
    /// Integrity digest (hex) when available.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub digest: Option<String>,
    /// Revision identity (e.g. git SHA) the locator was read at.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision: Option<String>,
    /// Verification state of the evidence itself, when independently checked.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verified: Option<bool>,
}

/// One compact finding. `statement` must stand alone; evidence links by id.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Finding {
    /// Stable id unique within the message (e.g. `F1`).
    pub id: String,
    /// The claim, in one sentence of plain English.
    pub statement: String,
    /// Confidence in `[0, 1]`, or None when unestimated.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f32>,
    /// Ids of `EvidenceRef`s supporting this finding.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<String>,
}

/// An independent verification record for one claim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Verification {
    /// The claim being verified.
    pub claim: String,
    /// One of the [`verification`] constants.
    pub status: String,
    /// How it was verified (e.g. `cargo_test`, `manual_review`).
    pub method: String,
    /// Receipt pointer for the verification run, when one exists.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub receipt: Option<String>,
}

/// Transport lifecycle receipt. Established by the daemon; agents cannot
/// fabricate these through message text.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeliveryReceipt {
    /// The message the receipt is about.
    pub message_id: Option<String>,
    /// One of the [`delivery`] constants.
    pub state: String,
    /// RFC 3339 timestamp when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub at: Option<String>,
}

/// The typed agent-to-agent message.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentCommMessage {
    /// Unique message id. Set by the producer or the transport; may be absent
    /// in raw OCC-1 text and is never invented by parsers.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message_id: Option<String>,
    /// The task this message belongs to. Required.
    pub task_id: String,
    /// The task that spawned this task, when applicable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent_task_id: Option<String>,
    /// Shared trace/correlation id across a delegation subtree.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trace_id: Option<String>,
    /// Sender identity as established by the daemon transport. Never trusted
    /// from agent text.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sender_identity: Option<String>,
    /// Intended recipient identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recipient_identity: Option<String>,
    /// One of the [`kinds`] constants.
    pub kind: String,
    /// One of the [`statuses`] constants.
    pub status: String,
    /// Compact findings.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub findings: Vec<Finding>,
    /// Evidence referenced by the findings (not copied transcripts).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<EvidenceRef>,
    /// Artifact paths produced by this task.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub artifacts: Vec<String>,
    /// Independent verification record, when one exists.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verification: Option<Verification>,
    /// Task ids this task depends on.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub dependencies: Vec<String>,
    /// Required next actions for the recipient, in priority order.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub next_actions: Vec<String>,
    /// Sequence number within the task, for ordering and delta bases.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sequence: Option<u64>,
    /// Always [`AGENT_COMM_PROTOCOL`] on produced messages.
    pub protocol: String,
    /// RFC 3339 timestamp.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timestamp: Option<String>,
    /// RFC 3339 expiry after which the message should not be acted on.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expiry: Option<String>,
}

impl AgentCommMessage {
    /// Creates a minimal valid message for the given task.
    pub fn new(kind: impl Into<String>, task_id: impl Into<String>, status: impl Into<String>) -> Self {
        Self {
            message_id: None,
            task_id: task_id.into(),
            parent_task_id: None,
            trace_id: None,
            sender_identity: None,
            recipient_identity: None,
            kind: kind.into(),
            status: status.into(),
            findings: Vec::new(),
            evidence: Vec::new(),
            artifacts: Vec::new(),
            verification: None,
            dependencies: Vec::new(),
            next_actions: Vec::new(),
            sequence: None,
            protocol: AGENT_COMM_PROTOCOL.to_string(),
            timestamp: None,
            expiry: None,
        }
    }

    /// Validates the invariant rules: known kind and status, well-formed ids,
    /// confident findings, evidence links resolvable, protocol match, and
    /// RFC 3339 timestamps.
    pub fn validate(&self) -> Result<(), ContractError> {
        if self.task_id.is_empty() || self.task_id.chars().any(|c| c.is_control()) {
            return Err(ContractError::InvalidTaskId(self.task_id.clone()));
        }
        if !is_known_kind(&self.kind) {
            return Err(ContractError::UnknownKind(self.kind.clone()));
        }
        if !is_known_status(&self.status) {
            return Err(ContractError::UnknownStatus(self.status.clone()));
        }
        if self.kind == kinds::EVIDENCE && self.evidence.is_empty() {
            return Err(ContractError::Occ1Conversion(
                "an EVIDENCE message must carry at least one evidence reference".to_string(),
            ));
        }
        if self.protocol != AGENT_COMM_PROTOCOL {
            return Err(ContractError::ProtocolMismatch(self.protocol.clone()));
        }
        let evidence_ids: BTreeSet<&str> = self.evidence.iter().map(|e| e.id.as_str()).collect();
        for finding in &self.findings {
            if let Some(confidence) = finding.confidence {
                if !(0.0..=1.0).contains(&confidence) {
                    return Err(ContractError::InvalidConfidence(confidence));
                }
            }
            for evidence_id in &finding.evidence {
                if !evidence_ids.contains(evidence_id.as_str()) {
                    return Err(ContractError::UnknownEvidence {
                        finding_id: finding.id.clone(),
                        evidence_id: evidence_id.clone(),
                    });
                }
            }
        }
        for evidence in &self.evidence {
            if !is_known_evidence_kind(&evidence.kind) {
                return Err(ContractError::UnknownEvidenceKind(evidence.kind.clone()));
            }
        }
        if let Some(verification) = &self.verification {
            if !matches!(
                verification.status.as_str(),
                verification::PASS | verification::FAIL | verification::UNKNOWN
            ) {
                return Err(ContractError::Occ1Conversion(format!(
                    "unknown verification status {:?}",
                    verification.status
                )));
            }
        }
        for stamp in [&self.timestamp, &self.expiry].into_iter().flatten() {
            if chrono::DateTime::parse_from_rfc3339(stamp).is_err() {
                return Err(ContractError::InvalidTimestamp(stamp.clone()));
            }
        }
        Ok(())
    }

    /// Encodes to OCC-1.
    pub fn to_occ1(&self) -> Result<Occ1Message, ContractError> {
        self.try_into_occ1()
    }

    fn try_into_occ1(&self) -> Result<Occ1Message, ContractError> {
        self.validate()?;
        let mut out = Occ1Message::new();
        if let Some(id) = &self.message_id {
            out.push("MSGID", id);
        }
        out.push("TASK", &self.task_id);
        if let Some(parent) = &self.parent_task_id {
            out.push("PARENT", parent);
        }
        if let Some(trace) = &self.trace_id {
            out.push("TRACE", trace);
        }
        if let Some(sender) = &self.sender_identity {
            out.push("SENDER", sender);
        }
        if let Some(recipient) = &self.recipient_identity {
            out.push("RECIPIENT", recipient);
        }
        out.push("KIND", &self.kind);
        out.push("STATUS", &self.status);
        if let Some(sequence) = self.sequence {
            out.push("SEQ", &sequence.to_string());
        }
        for (index, finding) in self.findings.iter().enumerate() {
            let n = index + 1;
            out.push(&format!("F{n}"), &finding.statement);
            if let Some(confidence) = finding.confidence {
                out.push(&format!("FC{n}"), &format!("{confidence:.3}"));
            }
            if !finding.evidence.is_empty() {
                out.push(&format!("FE{n}"), &finding.evidence.join(","));
            }
        }
        for (index, evidence) in self.evidence.iter().enumerate() {
            let n = index + 1;
            out.push(&format!("E{n}"), &format!("{}:{}", evidence.kind, evidence.locator));
            if let Some((start, end)) = evidence.line_range {
                out.push(&format!("EL{n}"), &format!("{start}-{end}"));
            }
            if let Some(digest) = &evidence.digest {
                out.push(&format!("ED{n}"), digest);
            }
            if let Some(revision) = &evidence.revision {
                out.push(&format!("ER{n}"), revision);
            }
            if let Some(verified) = evidence.verified {
                out.push(&format!("EV{n}"), if verified { "pass" } else { "fail" });
            }
        }
        for (index, artifact) in self.artifacts.iter().enumerate() {
            out.push(&format!("ART{}", index + 1), artifact);
        }
        if let Some(verification) = &self.verification {
            out.push("V", &verification.claim);
            out.push("VS", &verification.status);
            out.push("VM", &verification.method);
            if let Some(receipt) = &verification.receipt {
                out.push("VR", receipt);
            }
        }
        for (index, dependency) in self.dependencies.iter().enumerate() {
            out.push(&format!("DEP{}", index + 1), dependency);
        }
        for action in &self.next_actions {
            out.push("NEXT", action);
        }
        if let Some(timestamp) = &self.timestamp {
            out.push("TS", timestamp);
        }
        if let Some(expiry) = &self.expiry {
            out.push("EXP", expiry);
        }
        Ok(out)
    }

    /// Decodes from OCC-1. Missing optional fields stay `None`; nothing is
    /// invented. `EVIDENCE`-indexed fields must be contiguous from 1.
    pub fn from_occ1(message: &Occ1Message) -> Result<Self, ContractError> {
        let Some(task_id) = message.get("TASK") else {
            return Err(ContractError::MissingRequired { field: "TASK" });
        };
        let Some(kind) = message.get("KIND") else {
            return Err(ContractError::MissingRequired { field: "KIND" });
        };
        let Some(status) = message.get("STATUS") else {
            return Err(ContractError::MissingRequired { field: "STATUS" });
        };
        let mut out = AgentCommMessage::new(kind, task_id, status);
        out.message_id = message.get("MSGID").map(str::to_string);
        out.parent_task_id = message.get("PARENT").map(str::to_string);
        out.trace_id = message.get("TRACE").map(str::to_string);
        out.sender_identity = message.get("SENDER").map(str::to_string);
        out.recipient_identity = message.get("RECIPIENT").map(str::to_string);
        if let Some(seq) = message.get("SEQ") {
            out.sequence = Some(
                seq.parse::<u64>()
                    .map_err(|_| ContractError::Occ1Conversion(format!("SEQ {seq:?} is not a u64")))?,
            );
        }
        let evidence_count = indexed_count(message, "E")?;
        let finding_count = indexed_count(message, "F")?;
        for index in 1..=finding_count {
            let Some(statement) = message.get(&format!("F{index}")) else {
                return Err(ContractError::Occ1Conversion(format!(
                    "finding index {index} missing (indices must be contiguous from 1)"
                )));
            };
            let mut finding = Finding {
                id: format!("F{index}"),
                statement: statement.to_string(),
                confidence: None,
                evidence: Vec::new(),
            };
            if let Some(confidence) = message.get(&format!("FC{index}")) {
                let value: f32 = confidence
                    .parse()
                    .map_err(|_| ContractError::Occ1Conversion(format!("FC{index} not a number")))?;
                finding.confidence = Some(value);
            }
            if let Some(evidence) = message.get(&format!("FE{index}")) {
                for reference in evidence.split(',') {
                    let reference = reference.trim();
                    if reference.is_empty() {
                        continue;
                    }
                    let number: usize = reference
                        .strip_prefix('E')
                        .and_then(|suffix| suffix.parse().ok())
                        .ok_or_else(|| {
                            ContractError::Occ1Conversion(format!(
                                "FE{index} references {reference:?}, expected E<n>"
                            ))
                        })?;
                    if number == 0 || number > evidence_count {
                        return Err(ContractError::Occ1Conversion(format!(
                            "FE{index} references E{number}, outside 1..={evidence_count}"
                        )));
                    }
                    finding.evidence.push(format!("E{number}"));
                }
            }
            out.findings.push(finding);
        }
        for index in 1..=evidence_count {
            let Some(encoded) = message.get(&format!("E{index}")) else {
                return Err(ContractError::Occ1Conversion(format!(
                    "evidence index {index} missing (indices must be contiguous from 1)"
                )));
            };
            let Some((kind, locator)) = encoded.split_once(':') else {
                return Err(ContractError::Occ1Conversion(format!(
                    "E{index} must be <kind>:<locator>"
                )));
            };
            let mut evidence = EvidenceRef {
                id: format!("E{index}"),
                kind: kind.to_string(),
                locator: locator.to_string(),
                line_range: None,
                digest: None,
                revision: None,
                verified: None,
            };
            if let Some(range) = message.get(&format!("EL{index}")) {
                let Some((start, end)) = range.split_once('-') else {
                    return Err(ContractError::Occ1Conversion(format!(
                        "EL{index} must be <start>-<end>"
                    )));
                };
                let start: u32 = start
                    .parse()
                    .map_err(|_| ContractError::Occ1Conversion(format!("EL{index} start invalid")))?;
                let end: u32 = end
                    .parse()
                    .map_err(|_| ContractError::Occ1Conversion(format!("EL{index} end invalid")))?;
                evidence.line_range = Some((start, end));
            }
            evidence.digest = message.get(&format!("ED{index}")).map(str::to_string);
            evidence.revision = message.get(&format!("ER{index}")).map(str::to_string);
            if let Some(verified) = message.get(&format!("EV{index}")) {
                match verified {
                    "pass" => evidence.verified = Some(true),
                    "fail" => evidence.verified = Some(false),
                    "unknown" => evidence.verified = None,
                    other => {
                        return Err(ContractError::Occ1Conversion(format!(
                            "EV{index} must be pass/fail/unknown, got {other:?}"
                        )))
                    }
                }
            }
            out.evidence.push(evidence);
        }
        let artifact_count = indexed_count(message, "ART")?;
        for index in 1..=artifact_count {
            let Some(artifact) = message.get(&format!("ART{index}")) else {
                return Err(ContractError::Occ1Conversion(format!(
                    "artifact index {index} missing"
                )));
            };
            out.artifacts.push(artifact.to_string());
        }
        if let Some(claim) = message.get("V") {
            let verification = Verification {
                claim: claim.to_string(),
                status: message
                    .get("VS")
                    .ok_or_else(|| ContractError::Occ1Conversion("V without VS".into()))?
                    .to_string(),
                method: message
                    .get("VM")
                    .ok_or_else(|| ContractError::Occ1Conversion("V without VM".into()))?
                    .to_string(),
                receipt: message.get("VR").map(str::to_string),
            };
            out.verification = Some(verification);
        } else if message.has("VS") || message.has("VM") {
            return Err(ContractError::Occ1Conversion("VS/VM without V".into()));
        }
        let dependency_count = indexed_count(message, "DEP")?;
        for index in 1..=dependency_count {
            let Some(dependency) = message.get(&format!("DEP{index}")) else {
                return Err(ContractError::Occ1Conversion(format!(
                    "dependency index {index} missing"
                )));
            };
            out.dependencies.push(dependency.to_string());
        }
        out.next_actions = message.get_all("NEXT").iter().map(|s| s.to_string()).collect();
        out.timestamp = message.get("TS").map(str::to_string);
        out.expiry = message.get("EXP").map(str::to_string);
        out.validate()?;
        Ok(out)
    }
}

/// Counts the indexed keys `PREFIX1..PREFIXn` and rejects gaps: when the
/// highest index is N, every index 1..=N must be present. A stream with a
/// hole (e.g. `F1` then `F3`) is corruption, not truncation, so it is
/// rejected instead of silently decoding a prefix.
fn indexed_count(message: &Occ1Message, prefix: &str) -> Result<usize, ContractError> {
    let mut highest = 0usize;
    for (key, _) in message.iter() {
        let Some(rest) = key.strip_prefix(prefix) else {
            continue;
        };
        if let Ok(index) = rest.parse::<usize>() {
            highest = highest.max(index);
        }
    }
    for index in 1..=highest {
        if !message.has(&format!("{prefix}{index}")) {
            return Err(ContractError::Occ1Conversion(format!(
                "{prefix} index {index} missing below the highest index {highest}"
            )));
        }
    }
    Ok(highest)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> AgentCommMessage {
        let mut message =
            AgentCommMessage::new(kinds::RESULT, "t42", statuses::DONE);
        message.message_id = Some("m-1".into());
        message.sequence = Some(3);
        message.findings.push(Finding {
            id: "F1".into(),
            statement: "race window on session lock".into(),
            confidence: Some(0.9),
            evidence: vec!["E1".into()],
        });
        message.evidence.push(EvidenceRef {
            id: "E1".into(),
            kind: evidence_kinds::SOURCE.into(),
            locator: "src/session.rs".into(),
            line_range: Some((218, 235)),
            digest: Some("a1b2".into()),
            revision: Some("159ce68cc".into()),
            verified: Some(true),
        });
        message.verification = Some(Verification {
            claim: "session_race reproduces".into(),
            status: verification::FAIL.into(),
            method: "cargo_test".into(),
            receipt: None,
        });
        message.artifacts.push("reports/race.txt".into());
        message.dependencies.push("t41".into());
        message.next_actions.push("inspect_lock".into());
        message.next_actions.push("regression_test".into());
        message
    }

    #[test]
    fn occ1_round_trip_preserves_semantics() {
        let message = sample();
        let encoded = message.to_occ1().expect("encodes").encode().expect("wire");
        assert!(encoded.starts_with("OCC/1\n"));
        let decoded = Occ1Message::decode(&encoded).expect("parses");
        let back = AgentCommMessage::from_occ1(&decoded).expect("decodes");
        assert_eq!(back, message);
    }

    #[test]
    fn every_contract_encoded_kind_passes_the_standard_dialect() {
        // BUG-002 regression: the dialect table once demanded GOAL/REF/WANT/
        // WHY, which to_occ1 never emits, so contract-encoded messages of
        // those kinds failed dialect validation. Every kind must now pass.
        let dialect = crate::core::occ::occ1::Occ1Dialect::standard();
        for (kind, status) in [
            (kinds::TASK, statuses::PENDING),
            (kinds::PROGRESS, statuses::IN_PROGRESS),
            (kinds::RESULT, statuses::DONE),
            (kinds::EVIDENCE, statuses::DONE),
            (kinds::REQUEST_DETAIL, statuses::PENDING),
            (kinds::ACK, statuses::DONE),
            (kinds::CORRECTION, statuses::IN_PROGRESS),
            (kinds::BLOCKED, statuses::BLOCKED),
            (kinds::CANCELLED, statuses::CANCELLED),
        ] {
            let mut message = AgentCommMessage::new(kind, "t1", status);
            if kind == kinds::EVIDENCE {
                message.evidence.push(EvidenceRef {
                    id: "E1".into(),
                    kind: evidence_kinds::SOURCE.into(),
                    locator: "src/a.rs".into(),
                    line_range: None,
                    digest: None,
                    revision: None,
                    verified: None,
                });
            }
            let encoded = message.to_occ1().expect("encodes").encode().expect("wire");
            let decoded = Occ1Message::decode(&encoded).expect("parses");
            dialect
                .validate(&decoded)
                .unwrap_or_else(|error| panic!("kind {kind} failed the standard dialect: {error}"));
        }
    }

    #[test]
    fn evidence_kind_without_evidence_is_rejected() {
        let message = AgentCommMessage::new(kinds::EVIDENCE, "t1", statuses::DONE);
        let err = message.validate().expect_err("EVIDENCE needs evidence");
        assert!(err.to_string().contains("at least one evidence reference"));
    }

    #[test]
    fn index_gaps_are_rejected_not_silently_truncated() {
        // F1 then F3 with F2 absent must be an error, not a decode that
        // quietly drops F3.
        let gapped = Occ1Message::new()
            .with("TASK", "t1")
            .with("KIND", "RESULT")
            .with("STATUS", "done")
            .with("F1", "first finding")
            .with("F3", "third finding");
        let err = AgentCommMessage::from_occ1(&gapped).expect_err("gap must be rejected");
        assert!(
            err.to_string().contains("index 2 missing below the highest index 3"),
            "unexpected error: {err}"
        );
        // Same rule for evidence indices.
        let gapped_evidence = Occ1Message::new()
            .with("TASK", "t1")
            .with("KIND", "EVIDENCE")
            .with("STATUS", "done")
            .with("E1", "src:a.rs")
            .with("E3", "src:b.rs");
        assert!(AgentCommMessage::from_occ1(&gapped_evidence).is_err());
    }

    #[test]
    fn decode_never_invents_required_fields() {
        let raw = Occ1Message::new().with("KIND", "RESULT").with("STATUS", "done");
        let err = AgentCommMessage::from_occ1(&raw).expect_err("TASK required");
        assert!(matches!(err, ContractError::MissingRequired { field: "TASK" }));
    }

    #[test]
    fn dangling_evidence_reference_is_rejected() {
        let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
        message.findings.push(Finding {
            id: "F1".into(),
            statement: "claim".into(),
            confidence: None,
            evidence: vec!["E9".into()],
        });
        let err = message.validate().expect_err("dangling reference");
        assert!(matches!(err, ContractError::UnknownEvidence { .. }));
    }

    #[test]
    fn json_round_trip_is_stable() {
        let message = sample();
        let json = serde_json::to_string(&message).expect("serializes");
        let back: AgentCommMessage = serde_json::from_str(&json).expect("deserializes");
        assert_eq!(back, message);
    }
}
