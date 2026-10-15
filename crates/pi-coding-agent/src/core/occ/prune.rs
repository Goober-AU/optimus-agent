//! AgentPrune-inspired redundant-message suppression.
//!
//! FROZEN PUBLIC API SPEC: see `crate::core::occ::mod.rs`. Safety rules run
//! BEFORE any suppression heuristic and the pruner biases toward delivery.
//!
//! Documented interpretations of the frozen spec:
//! - All duplicate detection is scoped per `task_id`.
//! - `must_deliver` never suppresses: BLOCKED/CANCELLED/CORRECTION/ACK kinds,
//!   FAILED status, verification failures (`verification.status == "fail"`),
//!   contradictory findings (a normalized statement already delivered for the
//!   same task with a different verification status), messages carrying a
//!   dependency on a task no delivered record covers, and the first message
//!   for a task. Two findings inside one message cannot contradict: a
//!   message carries at most one verification record, and equal normalized
//!   statements match that record identically.
//! - "Every finding digest was already delivered" is vacuously true for a
//!   message with no findings: an empty PROGRESS with unchanged status and
//!   no new evidence is a suppressed heartbeat, and an empty RESULT with no
//!   new evidence is suppressed as `DuplicateFindings`.
//! - Suppression only ever applies to content the digests cover: any
//!   message carrying required next actions, artifact paths or a
//!   verification record is delivered regardless of duplicate findings,
//!   because no suppression rule accounts for that content.
//! - "Dependencies the recipient has not seen" counts a task as seen only
//!   when a delivered record exists for it; mentions inside other
//!   messages' dependency lists do not count, which biases toward delivery.
//! - Only `Deliver` decisions are recorded. History keeps the most recent
//!   `max_history` records; eviction makes the pruner forget, so a repeat of
//!   an evicted message is delivered again rather than silently dropped.

use std::collections::{BTreeMap, BTreeSet};

use sha2::{Digest, Sha256};

use crate::core::occ::consolidation::{finding_verification_status, normalized_statement};
use crate::core::occ::contract::{kinds, statuses, verification, AgentCommMessage, EvidenceRef};

/// Suppression switches and history size.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PrunePolicy {
    pub suppress_duplicate_findings: bool,
    pub suppress_unchanged_progress: bool,
    pub suppress_repeated_evidence: bool,
    /// Most recent delivered records kept. Default 64.
    pub max_history: usize,
}

impl Default for PrunePolicy {
    fn default() -> Self {
        Self {
            suppress_duplicate_findings: true,
            suppress_unchanged_progress: true,
            suppress_repeated_evidence: true,
            max_history: 64,
        }
    }
}

/// Why a message was suppressed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PruneReason {
    DuplicateFindings,
    UnchangedProgress,
    RepeatedEvidence,
}

/// One classification outcome.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PruneDecision {
    Deliver,
    Suppress { reason: PruneReason },
}

/// Summary of one delivered message, as kept in history.
#[derive(Debug, Clone, PartialEq)]
pub struct DeliveryRecord {
    pub message_id: Option<String>,
    pub task_id: String,
    pub kind: String,
    pub status: String,
    pub sequence: Option<u64>,
    pub finding_digests: Vec<String>,
    pub evidence_digests: Vec<String>,
    /// The message's verification record when it carried one; used to tell
    /// a verbatim verification repeat (suppressible) from a new claim or a
    /// flipped status (must deliver). Compared by value; the record is not
    /// serialized as part of pruning history.
    pub verification: Option<crate::core::occ::contract::Verification>,
}

/// sha256 hex over the normalized statement.
pub fn finding_digest(statement: &str) -> String {
    let normalized = normalized_statement(statement);
    format!("{:x}", Sha256::digest(normalized.as_bytes()))
}

/// sha256 hex over `"<kind>\0<locator>\0<digest-or-empty>"`.
pub fn evidence_digest(evidence: &EvidenceRef) -> String {
    let input = format!(
        "{}\0{}\0{}",
        evidence.kind,
        evidence.locator,
        evidence.digest.as_deref().unwrap_or("")
    );
    format!("{:x}", Sha256::digest(input.as_bytes()))
}

/// One delivered message plus the per-finding statement/status pairs needed
/// by the contradictory-findings safety check.
struct HistoryEntry {
    record: DeliveryRecord,
    finding_statuses: Vec<(String, Option<String>)>,
}

/// Redundant-message suppressor with private delivery history.
pub struct Pruner {
    policy: PrunePolicy,
    history: Vec<HistoryEntry>,
}

impl Pruner {
    pub fn new(policy: PrunePolicy) -> Self {
        Self {
            policy,
            history: Vec::new(),
        }
    }

    /// Classifies one outgoing message. Only `Deliver` decisions are recorded
    /// into history.
    pub fn classify(&mut self, message: &AgentCommMessage) -> PruneDecision {
        if self.must_deliver(message) {
            self.record(message);
            return PruneDecision::Deliver;
        }
        let finding_digests = message
            .findings
            .iter()
            .map(|finding| finding_digest(&finding.statement))
            .collect::<Vec<_>>();
        let evidence_digests = message
            .evidence
            .iter()
            .map(evidence_digest)
            .collect::<Vec<_>>();
        let seen_findings = self.seen_finding_digests(&message.task_id);
        let seen_evidence = self.seen_evidence_digests(&message.task_id);
        let findings_all_seen = finding_digests
            .iter()
            .all(|digest| seen_findings.contains(digest.as_str()));
        let no_new_evidence = evidence_digests
            .iter()
            .all(|digest| seen_evidence.contains(digest.as_str()));
        if message.kind == kinds::PROGRESS && self.policy.suppress_unchanged_progress {
            let status_unchanged = self
                .last_delivered(&message.task_id)
                .map_or(false, |record| record.status == message.status);
            if findings_all_seen && no_new_evidence && status_unchanged {
                return PruneDecision::Suppress {
                    reason: PruneReason::UnchangedProgress,
                };
            }
        }
        if message.kind == kinds::RESULT && self.policy.suppress_duplicate_findings {
            if findings_all_seen && no_new_evidence {
                return PruneDecision::Suppress {
                    reason: PruneReason::DuplicateFindings,
                };
            }
        }
        if message.kind == kinds::EVIDENCE && self.policy.suppress_repeated_evidence {
            if no_new_evidence {
                return PruneDecision::Suppress {
                    reason: PruneReason::RepeatedEvidence,
                };
            }
        }
        self.record(message);
        PruneDecision::Deliver
    }

    /// The retained delivery records, oldest first.
    pub fn history(&self) -> Vec<DeliveryRecord> {
        self.history
            .iter()
            .map(|entry| entry.record.clone())
            .collect()
    }

    /// The active policy.
    pub fn policy(&self) -> &PrunePolicy {
        &self.policy
    }

    fn must_deliver(&self, message: &AgentCommMessage) -> bool {
        if matches!(
            message.kind.as_str(),
            kinds::BLOCKED | kinds::CANCELLED | kinds::CORRECTION | kinds::ACK
        ) {
            return true;
        }
        if message.status == statuses::FAILED {
            return true;
        }
        // Content the digest heuristics do not cover must never be
        // suppressed: required next actions, artifact paths, and any
        // verification record whose claim or status differs from the last
        // delivered verification for the task. A verbatim repeat of an
        // already-delivered verification adds nothing, but a new claim or
        // a flipped status does. Finding digests only cover statements;
        // evidence digests only cover kind/locator/digest. Without this
        // guard a RESULT repeating known findings while carrying new next
        // actions would be silently dropped.
        if !message.next_actions.is_empty() || !message.artifacts.is_empty() {
            return true;
        }
        if let Some(record) = &message.verification {
            let changed = self
                .last_delivered(&message.task_id)
                .and_then(|last| last.verification.as_ref())
                .map_or(true, |previous| {
                    normalized_statement(&previous.claim) != normalized_statement(&record.claim)
                        || previous.status != record.status
                        || previous.method != record.method
                        || previous.receipt != record.receipt
                });
            if changed {
                return true;
            }
        }
        if message
            .verification
            .as_ref()
            .map_or(false, |record| record.status == verification::FAIL)
        {
            return true;
        }
        let recorded = self.statement_statuses(&message.task_id);
        for finding in &message.findings {
            let normalized = normalized_statement(&finding.statement);
            let status = finding_verification_status(message, finding);
            if let Some(recorded_statuses) = recorded.get(normalized.as_str()) {
                if recorded_statuses
                    .iter()
                    .any(|recorded| recorded.as_deref() != status)
                {
                    return true;
                }
            }
        }
        let seen_tasks = self.seen_task_ids();
        if message
            .dependencies
            .iter()
            .any(|dependency| !seen_tasks.contains(dependency.as_str()))
        {
            return true;
        }
        !seen_tasks.contains(message.task_id.as_str())
    }

    fn record(&mut self, message: &AgentCommMessage) {
        let finding_digests =
            dedup_in_order(message.findings.iter().map(|f| finding_digest(&f.statement)));
        let evidence_digests =
            dedup_in_order(message.evidence.iter().map(evidence_digest));
        let finding_statuses = message
            .findings
            .iter()
            .map(|finding| {
                (
                    normalized_statement(&finding.statement),
                    finding_verification_status(message, finding).map(str::to_string),
                )
            })
            .collect();
        self.history.push(HistoryEntry {
            record: DeliveryRecord {
                message_id: message.message_id.clone(),
                task_id: message.task_id.clone(),
                kind: message.kind.clone(),
                status: message.status.clone(),
                sequence: message.sequence,
                finding_digests,
                evidence_digests,
                verification: message.verification.clone(),
            },
            finding_statuses,
        });
        let cap = self.policy.max_history;
        if self.history.len() > cap {
            let excess = self.history.len() - cap;
            self.history.drain(0..excess);
        }
    }

    fn seen_finding_digests(&self, task_id: &str) -> BTreeSet<&str> {
        let mut seen = BTreeSet::new();
        for entry in &self.history {
            if entry.record.task_id == task_id {
                seen.extend(entry.record.finding_digests.iter().map(String::as_str));
            }
        }
        seen
    }

    fn seen_evidence_digests(&self, task_id: &str) -> BTreeSet<&str> {
        let mut seen = BTreeSet::new();
        for entry in &self.history {
            if entry.record.task_id == task_id {
                seen.extend(entry.record.evidence_digests.iter().map(String::as_str));
            }
        }
        seen
    }

    fn seen_task_ids(&self) -> BTreeSet<&str> {
        self.history
            .iter()
            .map(|entry| entry.record.task_id.as_str())
            .collect()
    }

    fn last_delivered(&self, task_id: &str) -> Option<&DeliveryRecord> {
        self.history
            .iter()
            .rev()
            .find(|entry| entry.record.task_id == task_id)
            .map(|entry| &entry.record)
    }

    fn statement_statuses(&self, task_id: &str) -> BTreeMap<&str, BTreeSet<&Option<String>>> {
        let mut map: BTreeMap<&str, BTreeSet<&Option<String>>> = BTreeMap::new();
        for entry in &self.history {
            if entry.record.task_id != task_id {
                continue;
            }
            for (statement, status) in &entry.finding_statuses {
                map.entry(statement.as_str()).or_default().insert(status);
            }
        }
        map
    }
}

fn dedup_in_order<I>(items: I) -> Vec<String>
where
    I: IntoIterator<Item = String>,
{
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for item in items {
        if seen.insert(item.clone()) {
            out.push(item);
        }
    }
    out
}
