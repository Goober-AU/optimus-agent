//! MOC-inspired evidence consolidation over `optimus.agent-comm.v1` messages.
//!
//! FROZEN PUBLIC API SPEC: see `crate::core::occ::mod.rs`. The notes below
//! document how this implementation resolves the spec's edge cases; none of
//! them drop findings, evidence, contradictions or uncertainty.
//!
//! - Statement matching uses [`normalized_statement`]: trimmed, internal
//!   whitespace runs collapsed to single spaces, ASCII-lowercased.
//! - A finding's verification status is the enclosing message's
//!   `verification.status` when the verification claim normalizes equal to
//!   the finding statement, otherwise absent. A `Verification` is a record
//!   about ONE claim, so it is only attributed to the matching finding.
//! - With `merge_same_statement` on, findings whose normalized statements are
//!   equal merge when their verification statuses agree (all absent or all
//!   equal) and no two contributing findings' evidence sets disagree.
//!   Disagreement: same evidence `(kind, locator)` with a different digest
//!   or `verified` flag. The merged finding keeps the first-appearance
//!   finding verbatim; `sources` are deduplicated message labels in
//!   first-appearance order and `agreement` is the number of contributing
//!   messages. With `merge_same_statement` off, findings pass through
//!   ungrouped (agreement 1) and no conflicts are extracted.
//! - A same-statement group that does not agree becomes one `Conflict` with
//!   one position per asserting finding occurrence (every statement, source
//!   label and evidence id stays visible) instead of a consolidated finding.
//!   Findings and evidence are ordered by first appearance, conflicts by
//!   first detection.
//! - Evidence dedup identity is the [`EvidenceKeyMode`] fields plus
//!   `line_range`. `line_range` is not part of any key mode, but two refs
//!   whose keys are equal while their line ranges differ are both kept so no
//!   pointer is silently dropped. Evidence-level `verified` differences are
//!   not part of any key mode; they surface through finding conflicts.
//! - Source labels: `message_id` when present, else `<task>#<sequence>` when
//!   the message carries one, else `<task>#<pos>` with `pos` the 1-based
//!   position in the input slice.
//! - Uncertainties are emitted per input finding occurrence (merged or not)
//!   so merging never hides a low-confidence report.

use std::collections::{BTreeMap, BTreeSet};

use crate::core::occ::contract::{verification, AgentCommMessage, EvidenceRef, Finding};

/// Options for [`consolidate`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConsolidationOptions {
    /// Merge findings whose normalized statements are equal. Default true.
    pub merge_same_statement: bool,
    /// Evidence dedup identity mode.
    /// Default [`EvidenceKeyMode::LocatorAndDigest`].
    pub evidence_key: EvidenceKeyMode,
}

impl Default for ConsolidationOptions {
    fn default() -> Self {
        Self {
            merge_same_statement: true,
            evidence_key: EvidenceKeyMode::LocatorAndDigest,
        }
    }
}

/// Which [`EvidenceRef`] fields form the dedup identity.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EvidenceKeyMode {
    /// `(kind, locator)`.
    Locator,
    /// `(kind, locator, digest)`.
    LocatorAndDigest,
    /// `(kind, locator, digest, revision)`.
    LocatorDigestRevision,
}

/// Exact input/output counts of one [`consolidate`] run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConsolidationStats {
    pub messages_in: usize,
    pub findings_in: usize,
    pub findings_out: usize,
    pub evidence_in: usize,
    pub evidence_out: usize,
    pub conflicts: usize,
    pub uncertainties: usize,
}

/// One consolidated finding: the first-appearance finding plus provenance.
#[derive(Debug, Clone, PartialEq)]
pub struct ConsolidatedFinding {
    pub finding: Finding,
    /// Contributing message labels, ordered, deduplicated.
    pub sources: Vec<String>,
    /// Number of messages asserting the statement.
    pub agreement: usize,
}

/// One side of a conflict: a single finding occurrence, verbatim.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConflictPosition {
    pub statement: String,
    pub source_message_id: String,
    pub evidence_ids: Vec<String>,
}

/// One normalized statement with disagreeing verification status or evidence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Conflict {
    /// The normalized statement the positions disagree about.
    pub topic: String,
    /// One position per asserting finding occurrence, in appearance order.
    pub positions: Vec<ConflictPosition>,
}

/// Result of consolidating a batch of messages. Inputs are never mutated.
#[derive(Debug, Clone, PartialEq)]
pub struct ConsolidationReport {
    pub findings: Vec<ConsolidatedFinding>,
    pub evidence: Vec<EvidenceRef>,
    pub conflicts: Vec<Conflict>,
    pub uncertainties: Vec<String>,
    pub dependencies: Vec<String>,
    pub stats: ConsolidationStats,
}

/// Normalizes a statement for merge matching: trimmed, internal whitespace
/// runs collapsed to single spaces, ASCII-lowercased. Shared with `prune`.
pub fn normalized_statement(statement: &str) -> String {
    statement
        .split_whitespace()
        .collect::<Vec<&str>>()
        .join(" ")
        .to_ascii_lowercase()
}

/// A finding's verification status: the enclosing message's verification
/// status when its claim normalizes equal to the finding statement, else
/// `None`. Shared with `prune`.
pub fn finding_verification_status<'a>(
    message: &'a AgentCommMessage,
    finding: &Finding,
) -> Option<&'a str> {
    let record = message.verification.as_ref()?;
    if normalized_statement(&record.claim) == normalized_statement(&finding.statement) {
        Some(record.status.as_str())
    } else {
        None
    }
}

/// Consolidates findings, evidence, conflicts, uncertainties and task
/// dependencies across a batch of messages. Deterministic for identical
/// input order.
pub fn consolidate(
    messages: &[AgentCommMessage],
    options: &ConsolidationOptions,
) -> ConsolidationReport {
    let mut flat: Vec<Occurrence> = Vec::new();
    let mut groups: Vec<Group> = Vec::new();
    let mut group_at: BTreeMap<String, usize> = BTreeMap::new();
    let mut evidence: Vec<EvidenceRef> = Vec::new();
    let mut evidence_seen: BTreeSet<EvidenceIdentity> = BTreeSet::new();
    let mut dependencies: Vec<String> = Vec::new();
    let mut seen_dependencies: BTreeSet<String> = BTreeSet::new();
    let mut uncertainties: Vec<String> = Vec::new();
    let mut findings_in = 0usize;
    let mut evidence_in = 0usize;

    for (message_index, message) in messages.iter().enumerate() {
        let label = message_label(message, message_index);
        evidence_in += message.evidence.len();
        for item in &message.evidence {
            if evidence_seen.insert(EvidenceIdentity::new(options.evidence_key, item)) {
                evidence.push(item.clone());
            }
        }
        for dependency in &message.dependencies {
            if seen_dependencies.insert(dependency.clone()) {
                dependencies.push(dependency.clone());
            }
        }
        for finding in &message.findings {
            findings_in += 1;
            let status = finding_verification_status(message, finding).map(str::to_string);
            if is_uncertain(finding, status.as_deref()) {
                uncertainties.push(format!(
                    "task={} finding={} confidence={} statement={}",
                    message.task_id,
                    finding.id,
                    confidence_field(finding),
                    finding.statement
                ));
            }
            let occurrence = Occurrence {
                finding: finding.clone(),
                source: label.clone(),
                status,
                evidence: resolve_finding_evidence(message, finding),
            };
            if !options.merge_same_statement {
                flat.push(occurrence);
                continue;
            }
            let normalized = normalized_statement(&finding.statement);
            let index = findings_in - 1;
            let at = match group_at.get(&normalized) {
                Some(&at) => at,
                None => {
                    groups.push(Group {
                        normalized: normalized.clone(),
                        occurrences: Vec::new(),
                        union: BTreeMap::new(),
                        status_state: None,
                        first_detection: None,
                    });
                    let at = groups.len() - 1;
                    group_at.insert(normalized.clone(), at);
                    at
                }
            };
            let group = &mut groups[at];
            // Compare against the accumulated state of PRIOR occurrences
            // only; a single finding's own evidence set is never compared
            // with itself.
            let status_mismatch = match &group.status_state {
                None => false,
                Some(prior) => *prior != occurrence.status,
            };
            let evidence_disagreement = occurrence.evidence.iter().any(|item| {
                group
                    .union
                    .get(&(item.kind.clone(), item.locator.clone()))
                    .map_or(false, |(digest, verified)| {
                        &item.digest != digest || &item.verified != verified
                    })
            });
            if (status_mismatch || evidence_disagreement) && group.first_detection.is_none() {
                group.first_detection = Some(index);
            }
            if group.status_state.is_none() {
                group.status_state = Some(occurrence.status.clone());
            }
            for item in &occurrence.evidence {
                group
                    .union
                    .entry((item.kind.clone(), item.locator.clone()))
                    .or_insert((item.digest.clone(), item.verified));
            }
            group.occurrences.push(occurrence);
        }
    }

    let mut findings: Vec<ConsolidatedFinding> = Vec::new();
    let mut detected: Vec<(usize, Conflict)> = Vec::new();
    if options.merge_same_statement {
        for group in &groups {
            if let Some(detection) = group.first_detection {
                detected.push((
                    detection,
                    Conflict {
                        topic: group.normalized.clone(),
                        positions: group
                            .occurrences
                            .iter()
                            .map(|occurrence| ConflictPosition {
                                statement: occurrence.finding.statement.clone(),
                                source_message_id: occurrence.source.clone(),
                                evidence_ids: occurrence.finding.evidence.clone(),
                            })
                            .collect(),
                    },
                ));
                continue;
            }
            let mut sources: Vec<String> = Vec::new();
            for occurrence in &group.occurrences {
                if !sources.contains(&occurrence.source) {
                    sources.push(occurrence.source.clone());
                }
            }
            findings.push(ConsolidatedFinding {
                finding: group.occurrences[0].finding.clone(),
                agreement: sources.len(),
                sources,
            });
        }
    } else {
        for occurrence in &flat {
            findings.push(ConsolidatedFinding {
                finding: occurrence.finding.clone(),
                sources: vec![occurrence.source.clone()],
                agreement: 1,
            });
        }
    }
    detected.sort_by_key(|(index, _)| *index);
    let conflicts = detected
        .into_iter()
        .map(|(_, conflict)| conflict)
        .collect::<Vec<_>>();

    ConsolidationReport {
        stats: ConsolidationStats {
            messages_in: messages.len(),
            findings_in,
            findings_out: findings.len(),
            evidence_in,
            evidence_out: evidence.len(),
            conflicts: conflicts.len(),
            uncertainties: uncertainties.len(),
        },
        findings,
        evidence,
        conflicts,
        uncertainties,
        dependencies,
    }
}

/// One finding occurrence with the context needed for grouping.
struct Occurrence {
    finding: Finding,
    source: String,
    status: Option<String>,
    evidence: Vec<EvidenceRef>,
}

/// All findings sharing one normalized statement, plus the accumulated
/// agreement state used to detect conflicts.
struct Group {
    normalized: String,
    occurrences: Vec<Occurrence>,
    /// `(kind, locator)` to `(digest, verified)` across prior occurrences.
    union: BTreeMap<(String, String), (Option<String>, Option<bool>)>,
    /// Verification status of the first occurrence.
    status_state: Option<Option<String>>,
    /// Global finding index at which the group first disagreed.
    first_detection: Option<usize>,
}

/// Dedup identity of one evidence ref under the chosen key mode.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct EvidenceIdentity {
    kind: String,
    locator: String,
    digest: Option<String>,
    revision: Option<String>,
    line_range: Option<(u32, u32)>,
}

impl EvidenceIdentity {
    fn new(mode: EvidenceKeyMode, evidence: &EvidenceRef) -> Self {
        Self {
            kind: evidence.kind.clone(),
            locator: evidence.locator.clone(),
            digest: if matches!(mode, EvidenceKeyMode::Locator) {
                None
            } else {
                evidence.digest.clone()
            },
            revision: if matches!(mode, EvidenceKeyMode::LocatorDigestRevision) {
                evidence.revision.clone()
            } else {
                None
            },
            line_range: evidence.line_range,
        }
    }
}

fn message_label(message: &AgentCommMessage, index: usize) -> String {
    if let Some(id) = &message.message_id {
        return id.clone();
    }
    match message.sequence {
        Some(sequence) => format!("{}#{}", message.task_id, sequence),
        None => format!("{}#{}", message.task_id, index + 1),
    }
}

/// The evidence refs a finding links to, resolved inside its own message.
/// Dangling ids are skipped: they carry no evidence content to consolidate.
fn resolve_finding_evidence(message: &AgentCommMessage, finding: &Finding) -> Vec<EvidenceRef> {
    let mut resolved = Vec::new();
    let mut seen = BTreeSet::new();
    for id in &finding.evidence {
        if !seen.insert(id.as_str()) {
            continue;
        }
        if let Some(item) = message.evidence.iter().find(|item| &item.id == id) {
            resolved.push(item.clone());
        }
    }
    resolved
}

fn is_uncertain(finding: &Finding, status: Option<&str>) -> bool {
    finding
        .confidence
        .map_or(false, |confidence| confidence < 0.5)
        || status == Some(verification::UNKNOWN)
}

fn confidence_field(finding: &Finding) -> String {
    match finding.confidence {
        Some(confidence) => format!("{confidence}"),
        None => "unknown".to_string(),
    }
}
