//! Delta-based messaging on top of `optimus.agent-comm.v1`.
//!
//! A [`Delta`] is a change-only update for one task: it is rooted at a
//! snapshot (`base_sequence`) and numbered by `sequence`, contiguous from
//! `base_sequence + 1`. [`DeltaTracker`] turns a stream of deltas into
//! ordered, deduplicated application decisions.
//!
//! The tracker is pure bookkeeping and never holds the payload snapshot:
//! callers keep the snapshot and apply exactly what the receipts report.
//! The tracker validates stream state only - task identity, base identity,
//! ordering, duplicate replays and finding-id existence. Payload semantics
//! (for example evidence existence inside the snapshot) stay with the
//! caller, because the tracker never sees the snapshot.
//!
//! Receipt delivery: every applied or buffered delta produces a receipt,
//! delivered exactly once. A delta observed in order returns its receipt
//! from [`DeltaTracker::observe`]. A delta observed out of order is
//! buffered and reported as [`DeltaError::Gap`]; its buffering receipt
//! (`applied = false, buffered = true`) is queued, and when the gap fills
//! and it drains, its application receipt (`applied = true`,
//! `buffered = true`) is queued as well. Queued receipts are collected with
//! [`DeltaTracker::drain_ready`]. When `observe` fails because a drained
//! buffered delta is invalid, the observed delta itself has already been
//! applied; its receipt is queued instead of returned.
//!
//! Base handling: the first delta observed with a different
//! `base_sequence` returns [`DeltaError::StaleBase`] and permanently marks
//! the tracker stale; every later `observe` returns
//! [`DeltaError::BaseUnavailable`] until the caller rebuilds the tracker
//! from a fresh snapshot. Overflowing the buffer (more than
//! [`DELTA_BUFFER_CAP`] buffered deltas) is also reported as
//! `BaseUnavailable`, but it does not mark the tracker stale: filling the
//! gap still recovers.
//!
//! Finding ids: `observe` validates `UpdateFinding` references against the
//! ids seeded with [`DeltaTracker::seed_findings`] plus the ids introduced
//! by `new_findings` of applied deltas (a delta may patch a finding it
//! introduces itself). Snapshot ids that predate the stream must be seeded
//! or they are rejected as [`DeltaError::UnknownFinding`]. The tracker does
//! not validate evidence existence (the snapshot's evidence ids are unknown
//! to it); [`DeltaError::UnknownEvidence`] is produced only by the OCC-1
//! codec for dangling `NEWE` references.
//!
//! Restart recovery: [`DeltaTracker::state`] captures everything the
//! tracker needs - task, base, cursor, buffered payloads, known finding
//! ids, the stale flag and undelivered receipts. [`DeltaTracker::restore`]
//! rebuilds an equivalent tracker; re-observing the same stream yields
//! identical decisions.
//!
//! OCC-1 wire encoding (frozen in `mod.rs`): `TASK`, `SEQ` and `BASE` are
//! required; `STATUS` encodes `SetStatus`; `UPDATE=<finding-id>:<field>=<value>`
//! lines encode finding patches (`stmt`, `conf` - where the value `none`
//! clears the confidence - and `ev+<id>`/`ev-<id>`, where the value repeats
//! the evidence id as a self-check); `NEW<n>`/`NEWC<n>`/`NEWE<n>` encode
//! new findings; `E<n>`/`EL<n>`/`ED<n>`/`ER<n>`/`EV<n>` encode new evidence
//! like the contract encoding; `V`/`VS`/`VM` with optional `VR` encode
//! `SetVerification`; `NEXT` is repeatable and `WHY` optional.
//!
//! Wire limits enforced by the codec - the frozen grammar cannot express
//! these, so they are rejected, never silently dropped:
//! - new findings and new evidence carry positional ids (`N1..`, `E1..`);
//! - `FieldUpdate::AddEvidence` is representable only when it repeats,
//!   field-for-field, one of this delta's own new-evidence entries; the
//!   wire form is `EADD=E<n>` after the `E<n>` entries, and references to
//!   evidence the delta does not introduce are rejected;
//! - `updates` must be canonical (optional `SetStatus` first, then
//!   `UpdateFinding` entries, then `AddEvidence` entries, then optional
//!   `SetVerification`) with unique finding ids;
//! - reference ids must be non-empty and free of `:`, `=` and `,`.
//!
//! Decoding is strict on every field it understands and ignores unrelated
//! extra fields.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::core::occ::contract::{
    is_known_evidence_kind, verification, ContractError, EvidenceRef, Finding, Verification,
};
use crate::core::occ::occ1::{is_known_status, Occ1Message};

/// Maximum number of deltas held buffered while a gap is unfilled.
/// Observing a delta that would push the buffer past this cap returns
/// [`DeltaError::BaseUnavailable`] without changing any state.
pub const DELTA_BUFFER_CAP: usize = 1024;

/// Upper bound on how many entries [`DeltaTracker::missing_sequences`]
/// reports. Longer gaps are truncated to this many entries;
/// `needs_full_snapshot()` stays true for them.
pub const DELTA_MISSING_REPORT_CAP: usize = 1024;

/// One change-only update for a task, rooted at the snapshot identified by
/// `base_sequence` and numbered by `sequence`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Delta {
    /// The task this delta belongs to.
    pub task_id: String,
    /// The snapshot the delta stream is rooted at.
    pub base_sequence: u64,
    /// Position in the stream, contiguous from `base_sequence + 1`.
    pub sequence: u64,
    /// Field updates applied to the existing snapshot state.
    pub updates: Vec<FieldUpdate>,
    /// Findings introduced by this delta.
    pub new_findings: Vec<Finding>,
    /// Evidence introduced by this delta.
    pub new_evidence: Vec<EvidenceRef>,
    /// Required next actions for the recipient, in priority order.
    pub next_actions: Vec<String>,
    /// Optional human-readable reason for the delta.
    pub reason: Option<String>,
}

/// One field update inside a [`Delta`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum FieldUpdate {
    /// Replace the task status.
    SetStatus(String),
    /// Patch one existing finding.
    UpdateFinding {
        /// Id of the finding being patched.
        id: String,
        /// The patch to apply.
        patch: FindingPatch,
    },
    /// Add one evidence reference to the snapshot. Not representable on the
    /// OCC-1 wire (see module docs); rejected by [`Delta::to_occ1`].
    AddEvidence(EvidenceRef),
    /// Replace the independent verification record.
    SetVerification(Verification),
}

/// A partial patch of one finding. `None` fields are left unchanged.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FindingPatch {
    /// New statement, when changed.
    pub statement: Option<String>,
    /// `Some(Some(v))` sets the confidence, `Some(None)` clears it.
    #[serde(with = "confidence_serde")]
    pub confidence: Option<Option<f32>>,
    /// Evidence ids to attach to the finding.
    pub add_evidence: Vec<String>,
    /// Evidence ids to detach from the finding.
    pub remove_evidence: Vec<String>,
}

/// Unambiguous JSON for `Option<Option<f32>>`: absent/`null` is `None`, the
/// string `"none"` is `Some(None)`, a number is `Some(Some(v))`.
mod confidence_serde {
    use serde::{Deserializer, Serializer};

    pub fn serialize<S: Serializer>(
        value: &Option<Option<f32>>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        match value {
            None => serializer.serialize_none(),
            Some(None) => serializer.serialize_str("none"),
            Some(Some(value)) => serializer.serialize_f32(*value),
        }
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<Option<Option<f32>>, D::Error> {
        struct Visitor;
        impl<'de> serde::de::Visitor<'de> for Visitor {
            type Value = Option<Option<f32>>;

            fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
                formatter.write_str("null, \"none\" or a number")
            }

            fn visit_none<E>(self) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(None)
            }

            fn visit_unit<E>(self) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(None)
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                if value == "none" {
                    Ok(Some(None))
                } else {
                    Err(E::invalid_value(
                        serde::de::Unexpected::Str(value),
                        &"\"none\"",
                    ))
                }
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Some(Some(value as f32)))
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Some(Some(value as f32)))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(Some(Some(value as f32)))
            }
        }
        // Not `deserialize_option`: this is a tri-state at the JSON level
        // (null | "none" | number), so every variant must reach the
        // visitor instead of serde's default `visit_some` error.
        deserializer.deserialize_any(Visitor)
    }
}

/// Failure of a delta to fit a tracker's stream state.
#[derive(Debug, Clone, PartialEq, Error)]
pub enum DeltaError {
    /// The delta belongs to a different task.
    #[error("delta task_id {got:?} does not match tracker task {expected:?}")]
    WrongTask { expected: String, got: String },
    /// The sequence was already applied, already buffered, or predates the
    /// snapshot base. Idempotent no-op; nothing changed.
    #[error("delta sequence {sequence} was already applied or buffered")]
    Duplicate { sequence: u64 },
    /// The delta jumped ahead; it was buffered until the gap fills.
    #[error("delta sequence {got} jumped ahead; expected {expected}")]
    Gap { expected: u64, got: u64 },
    /// The delta is rooted at a different snapshot base.
    #[error("delta base {got_base} does not match tracker base {base}")]
    StaleBase { base: u64, got_base: u64 },
    /// A patched finding id is neither seeded nor introduced by this stream.
    #[error("finding {id:?} is neither seeded nor introduced by this stream")]
    UnknownFinding { id: String },
    /// An evidence id is unknown. Produced by the codec for dangling
    /// `NEWE` references; the tracker does not check evidence existence.
    #[error("evidence {id:?} is unknown")]
    UnknownEvidence { id: String },
    /// The tracker cannot proceed; a full snapshot is required.
    #[error("the tracker cannot proceed; a full snapshot is required")]
    BaseUnavailable,
}

/// What happened to one observed delta.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeltaReceipt {
    /// False when the delta was buffered for a gap.
    pub applied: bool,
    /// True when the delta was (or is) buffered for a gap.
    pub buffered: bool,
    /// The delta's sequence number.
    pub sequence: u64,
}

impl Delta {
    /// OCC-1 wire encoding of a delta (see module docs for the grammar and
    /// its limits). Rejects deltas the frozen grammar cannot express.
    pub fn to_occ1(&self) -> Result<Occ1Message, ContractError> {
        if self.task_id.is_empty() || self.task_id.chars().any(|c| c.is_control()) {
            return Err(ContractError::InvalidTaskId(self.task_id.clone()));
        }
        // The wire grammar fixes field positions (STATUS first, UPDATE
        // lines, V block last), so updates must already be in canonical
        // order; anything else is rejected, not silently reordered.
        let mut status: Option<&str> = None;
        let mut finding_updates: Vec<(&str, &FindingPatch)> = Vec::new();
        let mut verification_update: Option<&Verification> = None;
        let mut add_evidence_positions: Vec<usize> = Vec::new();
        for update in &self.updates {
            match update {
                FieldUpdate::SetStatus(value) => {
                    if status.is_some() {
                        return Err(ContractError::Occ1Conversion(
                            "at most one SetStatus update is representable".into(),
                        ));
                    }
                    if !finding_updates.is_empty() || verification_update.is_some() {
                        return Err(ContractError::Occ1Conversion(
                            "SetStatus must precede UpdateFinding and SetVerification".into(),
                        ));
                    }
                    if !is_known_status(value) {
                        return Err(ContractError::UnknownStatus(value.clone()));
                    }
                    status = Some(value.as_str());
                }
                FieldUpdate::UpdateFinding { id, patch } => {
                    if verification_update.is_some() {
                        return Err(ContractError::Occ1Conversion(
                            "SetVerification must be the last update".into(),
                        ));
                    }
                    if finding_updates.iter().any(|(known, _)| *known == id.as_str()) {
                        return Err(ContractError::Occ1Conversion(format!(
                            "duplicate UpdateFinding id {id:?}; merge into one patch"
                        )));
                    }
                    if patch.statement.is_none()
                        && patch.confidence.is_none()
                        && patch.add_evidence.is_empty()
                        && patch.remove_evidence.is_empty()
                    {
                        return Err(ContractError::Occ1Conversion(format!(
                            "UpdateFinding {id:?} carries an empty patch"
                        )));
                    }
                    if let Some(Some(value)) = patch.confidence {
                        if !(0.0..=1.0).contains(&value) {
                            return Err(ContractError::InvalidConfidence(value));
                        }
                    }
                    validate_reference_id(id)?;
                    for evidence_id in patch.add_evidence.iter().chain(&patch.remove_evidence) {
                        validate_reference_id(evidence_id)?;
                    }
                    finding_updates.push((id.as_str(), patch));
                }
                FieldUpdate::AddEvidence(evidence) => {
                    if verification_update.is_some() {
                        return Err(ContractError::Occ1Conversion(
                            "SetVerification must be the last update".into(),
                        ));
                    }
                    let Some(position) = self
                        .new_evidence
                        .iter()
                        .position(|candidate| candidate == evidence)
                    else {
                        return Err(ContractError::Occ1Conversion(format!(
                            "AddEvidence references evidence {:?} that this delta does not introduce; the grammar only carries references to new evidence",
                            evidence.id
                        )));
                    };
                    if add_evidence_positions.contains(&position) {
                        return Err(ContractError::Occ1Conversion(format!(
                            "duplicate AddEvidence for evidence E{}",
                            position + 1
                        )));
                    }
                    add_evidence_positions.push(position);
                }
                FieldUpdate::SetVerification(value) => {
                    if verification_update.is_some() {
                        return Err(ContractError::Occ1Conversion(
                            "at most one SetVerification update is representable".into(),
                        ));
                    }
                    verification_update = Some(value);
                }
            }
        }
        // The grammar carries no explicit ids for new findings/evidence;
        // ids are positional by construction.
        for (index, finding) in self.new_findings.iter().enumerate() {
            let position = index + 1;
            if finding.id != format!("N{position}") {
                return Err(ContractError::Occ1Conversion(format!(
                    "new finding at position {position} has id {:?}; the grammar only carries positional ids (\"N{position}\")",
                    finding.id
                )));
            }
            if let Some(confidence) = finding.confidence {
                if !(0.0..=1.0).contains(&confidence) {
                    return Err(ContractError::InvalidConfidence(confidence));
                }
            }
            for evidence_id in &finding.evidence {
                if !self.new_evidence.iter().any(|e| e.id == *evidence_id) {
                    return Err(ContractError::UnknownEvidence {
                        finding_id: finding.id.clone(),
                        evidence_id: evidence_id.clone(),
                    });
                }
            }
        }
        for (index, evidence) in self.new_evidence.iter().enumerate() {
            let position = index + 1;
            if evidence.id != format!("E{position}") {
                return Err(ContractError::Occ1Conversion(format!(
                    "new evidence at position {position} has id {:?}; the grammar only carries positional ids (\"E{position}\")",
                    evidence.id
                )));
            }
            if !is_known_evidence_kind(&evidence.kind) {
                return Err(ContractError::UnknownEvidenceKind(evidence.kind.clone()));
            }
        }
        if let Some(value) = verification_update {
            if !matches!(
                value.status.as_str(),
                verification::PASS | verification::FAIL | verification::UNKNOWN
            ) {
                return Err(ContractError::Occ1Conversion(format!(
                    "unknown verification status {:?}",
                    value.status
                )));
            }
        }

        let mut out = Occ1Message::new();
        out.push("TASK", self.task_id.as_str());
        out.push("SEQ", self.sequence.to_string());
        out.push("BASE", self.base_sequence.to_string());
        if let Some(status) = status {
            out.push("STATUS", status);
        }
        for (id, patch) in &finding_updates {
            if let Some(statement) = &patch.statement {
                out.push("UPDATE", format!("{id}:stmt={statement}"));
            }
            match patch.confidence {
                Some(Some(value)) => out.push("UPDATE", format!("{id}:conf={value:.3}")),
                Some(None) => out.push("UPDATE", format!("{id}:conf=none")),
                None => {}
            }
            for evidence_id in &patch.add_evidence {
                out.push("UPDATE", format!("{id}:ev+{evidence_id}={evidence_id}"));
            }
            for evidence_id in &patch.remove_evidence {
                out.push("UPDATE", format!("{id}:ev-{evidence_id}={evidence_id}"));
            }
        }
        for (index, finding) in self.new_findings.iter().enumerate() {
            let position = index + 1;
            out.push(format!("NEW{position}"), finding.statement.as_str());
            if let Some(confidence) = finding.confidence {
                out.push(format!("NEWC{position}"), format!("{confidence:.3}"));
            }
            if !finding.evidence.is_empty() {
                out.push(format!("NEWE{position}"), finding.evidence.join(","));
            }
        }
        for (index, evidence) in self.new_evidence.iter().enumerate() {
            let position = index + 1;
            out.push(
                format!("E{position}"),
                format!("{}:{}", evidence.kind, evidence.locator),
            );
            if let Some((start, end)) = evidence.line_range {
                out.push(format!("EL{position}"), format!("{start}-{end}"));
            }
            if let Some(digest) = &evidence.digest {
                out.push(format!("ED{position}"), digest.as_str());
            }
            if let Some(revision) = &evidence.revision {
                out.push(format!("ER{position}"), revision.as_str());
            }
            if let Some(verified) = evidence.verified {
                out.push(
                    format!("EV{position}"),
                    if verified { "pass" } else { "fail" },
                );
            }
        }
        for position in &add_evidence_positions {
            out.push("EADD", format!("E{}", position + 1));
        }
        if let Some(value) = verification_update {
            out.push("V", value.claim.as_str());
            out.push("VS", value.status.as_str());
            out.push("VM", value.method.as_str());
            if let Some(receipt) = &value.receipt {
                out.push("VR", receipt.as_str());
            }
        }
        for action in &self.next_actions {
            out.push("NEXT", action.as_str());
        }
        if let Some(reason) = &self.reason {
            out.push("WHY", reason.as_str());
        }
        Ok(out)
    }

    /// Strict OCC-1 decode. Syntax only; stream semantics (ordering, task
    /// identity, base, finding existence) are checked by
    /// [`DeltaTracker::observe`]. Unrelated extra fields are ignored.
    pub fn from_occ1(message: &Occ1Message) -> Result<Self, ContractError> {
        let task_id = single(message, "TASK")?
            .ok_or(ContractError::MissingRequired { field: "TASK" })?
            .to_string();
        if task_id.is_empty() || task_id.chars().any(|c| c.is_control()) {
            return Err(ContractError::InvalidTaskId(task_id));
        }
        let sequence = required_u64(message, "SEQ")?;
        let base_sequence = required_u64(message, "BASE")?;

        // Classify indexed keys once; repeated indexed keys are ambiguous.
        let mut seen: BTreeSet<(&'static str, usize)> = BTreeSet::new();
        for (key, _) in message.iter() {
            if let Some((family, index)) = classify_indexed_key(key) {
                if !seen.insert((family, index)) {
                    return Err(ContractError::Occ1Conversion(format!(
                        "repeated field {key:?}"
                    )));
                }
            }
        }
        let new_indices = family_indices(&seen, "NEW");
        let evidence_indices = family_indices(&seen, "E");
        require_contiguous(&new_indices, "NEW")?;
        require_contiguous(&evidence_indices, "E")?;
        for (family, index) in &seen {
            match *family {
                "NEWC" | "NEWE" => {
                    if !new_indices.contains(index) {
                        return Err(ContractError::Occ1Conversion(format!(
                            "{family}{index} without NEW{index}"
                        )));
                    }
                }
                "EL" | "ED" | "ER" | "EV" => {
                    if !evidence_indices.contains(index) {
                        return Err(ContractError::Occ1Conversion(format!(
                            "{family}{index} without E{index}"
                        )));
                    }
                }
                _ => {}
            }
        }

        let mut delta = Delta {
            task_id,
            base_sequence,
            sequence,
            updates: Vec::new(),
            new_findings: Vec::new(),
            new_evidence: Vec::new(),
            next_actions: Vec::new(),
            reason: None,
        };

        if let Some(status) = single(message, "STATUS")? {
            if !is_known_status(status) {
                return Err(ContractError::UnknownStatus(status.to_string()));
            }
            delta.updates.push(FieldUpdate::SetStatus(status.to_string()));
        }

        // UPDATE lines merge per finding id, in first-appearance order.
        let mut order: Vec<String> = Vec::new();
        let mut patches: BTreeMap<String, FindingPatch> = BTreeMap::new();
        for line in message.get_all("UPDATE") {
            let Some((finding_id, rest)) = line.split_once(':') else {
                return Err(ContractError::Occ1Conversion(format!(
                    "UPDATE {line:?} must be <finding-id>:<field>=<value>"
                )));
            };
            let Some((field, value)) = rest.split_once('=') else {
                return Err(ContractError::Occ1Conversion(format!(
                    "UPDATE {line:?} must be <finding-id>:<field>=<value>"
                )));
            };
            if finding_id.is_empty() {
                return Err(ContractError::Occ1Conversion(
                    "UPDATE finding id is empty".into(),
                ));
            }
            let patch = patches.entry(finding_id.to_string()).or_insert_with(|| {
                order.push(finding_id.to_string());
                FindingPatch {
                    statement: None,
                    confidence: None,
                    add_evidence: Vec::new(),
                    remove_evidence: Vec::new(),
                }
            });
            if field == "stmt" {
                patch.statement = Some(value.to_string());
            } else if field == "conf" {
                patch.confidence = Some(if value == "none" {
                    None
                } else {
                    let parsed: f32 = value.parse().map_err(|_| {
                        ContractError::Occ1Conversion(format!(
                            "UPDATE conf {value:?} is not a number"
                        ))
                    })?;
                    if !(0.0..=1.0).contains(&parsed) {
                        return Err(ContractError::InvalidConfidence(parsed));
                    }
                    Some(parsed)
                });
            } else if let Some(evidence_id) = field.strip_prefix("ev+") {
                if evidence_id.is_empty() {
                    return Err(ContractError::Occ1Conversion(
                        "UPDATE ev+ evidence id is empty".into(),
                    ));
                }
                if evidence_id != value {
                    return Err(ContractError::Occ1Conversion(format!(
                        "UPDATE ev+{evidence_id} value must repeat the evidence id, got {value:?}"
                    )));
                }
                patch.add_evidence.push(evidence_id.to_string());
            } else if let Some(evidence_id) = field.strip_prefix("ev-") {
                if evidence_id.is_empty() {
                    return Err(ContractError::Occ1Conversion(
                        "UPDATE ev- evidence id is empty".into(),
                    ));
                }
                if evidence_id != value {
                    return Err(ContractError::Occ1Conversion(format!(
                        "UPDATE ev-{evidence_id} value must repeat the evidence id, got {value:?}"
                    )));
                }
                patch.remove_evidence.push(evidence_id.to_string());
            } else {
                return Err(ContractError::Occ1Conversion(format!(
                    "UPDATE field {field:?} must be stmt, conf, ev+<id> or ev-<id>"
                )));
            }
        }
        for id in order {
            if let Some(patch) = patches.remove(&id) {
                delta
                    .updates
                    .push(FieldUpdate::UpdateFinding { id, patch });
            }
        }

        let evidence_ids: BTreeSet<String> = evidence_indices
            .iter()
            .map(|index| format!("E{index}"))
            .collect();
        for index in &new_indices {
            let position = *index;
            let Some(statement) = message.get(&format!("NEW{position}")) else {
                return Err(ContractError::Occ1Conversion(format!(
                    "NEW{position} missing"
                )));
            };
            let mut finding = Finding {
                id: format!("N{position}"),
                statement: statement.to_string(),
                confidence: None,
                evidence: Vec::new(),
            };
            if let Some(confidence) = message.get(&format!("NEWC{position}")) {
                let parsed: f32 = confidence.parse().map_err(|_| {
                    ContractError::Occ1Conversion(format!(
                        "NEWC{position} is not a number"
                    ))
                })?;
                if !(0.0..=1.0).contains(&parsed) {
                    return Err(ContractError::InvalidConfidence(parsed));
                }
                finding.confidence = Some(parsed);
            }
            if let Some(links) = message.get(&format!("NEWE{position}")) {
                for reference in links.split(',') {
                    let reference = reference.trim();
                    if reference.is_empty() {
                        continue;
                    }
                    if !evidence_ids.contains(reference) {
                        return Err(ContractError::UnknownEvidence {
                            finding_id: finding.id.clone(),
                            evidence_id: reference.to_string(),
                        });
                    }
                    finding.evidence.push(reference.to_string());
                }
            }
            delta.new_findings.push(finding);
        }

        for index in &evidence_indices {
            let position = *index;
            let Some(encoded) = message.get(&format!("E{position}")) else {
                return Err(ContractError::Occ1Conversion(format!(
                    "E{position} missing"
                )));
            };
            let Some((kind, locator)) = encoded.split_once(':') else {
                return Err(ContractError::Occ1Conversion(format!(
                    "E{position} must be <kind>:<locator>"
                )));
            };
            if !is_known_evidence_kind(kind) {
                return Err(ContractError::UnknownEvidenceKind(kind.to_string()));
            }
            let mut evidence = EvidenceRef {
                id: format!("E{position}"),
                kind: kind.to_string(),
                locator: locator.to_string(),
                line_range: None,
                digest: None,
                revision: None,
                verified: None,
            };
            if let Some(range) = message.get(&format!("EL{position}")) {
                let Some((start, end)) = range.split_once('-') else {
                    return Err(ContractError::Occ1Conversion(format!(
                        "EL{position} must be <start>-<end>"
                    )));
                };
                let start: u32 = start.parse().map_err(|_| {
                    ContractError::Occ1Conversion(format!("EL{position} start is not a u32"))
                })?;
                let end: u32 = end.parse().map_err(|_| {
                    ContractError::Occ1Conversion(format!("EL{position} end is not a u32"))
                })?;
                evidence.line_range = Some((start, end));
            }
            evidence.digest = message
                .get(&format!("ED{position}"))
                .map(str::to_string);
            evidence.revision = message
                .get(&format!("ER{position}"))
                .map(str::to_string);
            if let Some(verified) = message.get(&format!("EV{position}")) {
                match verified {
                    "pass" => evidence.verified = Some(true),
                    "fail" => evidence.verified = Some(false),
                    "unknown" => evidence.verified = None,
                    other => {
                        return Err(ContractError::Occ1Conversion(format!(
                            "EV{position} must be pass/fail/unknown, got {other:?}"
                        )))
                    }
                }
            }
            delta.new_evidence.push(evidence);
        }

        for reference in message.get_all("EADD") {
            let Some(suffix) = reference.strip_prefix('E') else {
                return Err(ContractError::Occ1Conversion(format!(
                    "EADD {reference:?} must be E<n>"
                )));
            };
            let number: usize = suffix.parse().map_err(|_| {
                ContractError::Occ1Conversion(format!("EADD {reference:?} is not E<n>"))
            })?;
            if number == 0 || number > delta.new_evidence.len() {
                return Err(ContractError::Occ1Conversion(format!(
                    "EADD {reference:?} is outside 1..={}",
                    delta.new_evidence.len()
                )));
            }
            delta
                .updates
                .push(FieldUpdate::AddEvidence(delta.new_evidence[number - 1].clone()));
        }

        if let Some(claim) = single(message, "V")? {
            let status = single(message, "VS")?
                .ok_or_else(|| ContractError::Occ1Conversion("V without VS".into()))?
                .to_string();
            if !matches!(
                status.as_str(),
                verification::PASS | verification::FAIL | verification::UNKNOWN
            ) {
                return Err(ContractError::Occ1Conversion(format!(
                    "VS must be pass/fail/unknown, got {status:?}"
                )));
            }
            let method = single(message, "VM")?
                .ok_or_else(|| ContractError::Occ1Conversion("V without VM".into()))?
                .to_string();
            let receipt = single(message, "VR")?.map(str::to_string);
            delta.updates.push(FieldUpdate::SetVerification(Verification {
                claim: claim.to_string(),
                status,
                method,
                receipt,
            }));
        } else if message.has("VS") || message.has("VM") || message.has("VR") {
            return Err(ContractError::Occ1Conversion(
                "VS/VM/VR without V".into(),
            ));
        }

        delta.next_actions = message
            .get_all("NEXT")
            .iter()
            .map(|action| action.to_string())
            .collect();
        delta.reason = single(message, "WHY")?.map(str::to_string);
        Ok(delta)
    }
}

/// Serializable snapshot of a [`DeltaTracker`], sufficient for restart
/// recovery. Applied sequences are exactly `base+1 ..= next_sequence-1` by
/// construction, so they are represented by `next_sequence` alone.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeltaTrackerState {
    /// The task the stream belongs to.
    pub task_id: String,
    /// The snapshot base the stream is rooted at.
    pub base_sequence: u64,
    /// The next sequence the tracker expects.
    pub next_sequence: u64,
    /// Buffered deltas waiting for their gap to fill, in sequence order.
    pub buffered: Vec<Delta>,
    /// Seeded and stream-introduced finding ids.
    pub known_findings: Vec<String>,
    /// True once a wrong base was observed; the tracker is unusable.
    pub stale: bool,
    /// Receipts produced but not yet collected with `drain_ready`.
    pub pending_receipts: Vec<DeltaReceipt>,
}

/// Tracks one task's delta stream: ordering, deduplication, task identity,
/// base identity and finding-id existence. See the module docs for the
/// receipt-delivery rules; the caller owns the payload snapshot.
#[derive(Debug)]
pub struct DeltaTracker {
    task_id: String,
    base_sequence: u64,
    next_sequence: u64,
    buffered: BTreeMap<u64, Delta>,
    known_findings: BTreeSet<String>,
    stale: bool,
    pending: Vec<DeltaReceipt>,
}

impl DeltaTracker {
    /// Builds a tracker for `task_id` rooted at the snapshot with
    /// `base_sequence`. The first expected delta has sequence
    /// `base_sequence + 1`.
    pub fn new(task_id: impl Into<String>, base_sequence: u64) -> Self {
        Self {
            task_id: task_id.into(),
            base_sequence,
            next_sequence: base_sequence.saturating_add(1),
            buffered: BTreeMap::new(),
            known_findings: BTreeSet::new(),
            stale: false,
            pending: Vec::new(),
        }
    }

    /// The task this tracker accepts.
    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    /// The next sequence the tracker expects to apply.
    pub fn next_sequence(&self) -> u64 {
        self.next_sequence
    }

    /// Records one delta. Rejects wrong tasks, duplicate replays, gaps and
    /// stale bases; returns the receipt of what was applied so callers can
    /// update their own snapshot state. Out-of-order deltas are buffered
    /// and reported as [`DeltaError::Gap`]; their receipts are queued for
    /// [`DeltaTracker::drain_ready`].
    pub fn observe(&mut self, delta: &Delta) -> Result<DeltaReceipt, DeltaError> {
        if delta.task_id != self.task_id {
            return Err(DeltaError::WrongTask {
                expected: self.task_id.clone(),
                got: delta.task_id.clone(),
            });
        }
        if self.stale {
            return Err(DeltaError::BaseUnavailable);
        }
        if delta.base_sequence != self.base_sequence {
            // First mismatch permanently marks the tracker stale; only a
            // tracker rebuilt from a fresh snapshot recovers.
            self.stale = true;
            return Err(DeltaError::StaleBase {
                base: self.base_sequence,
                got_base: delta.base_sequence,
            });
        }
        let sequence = delta.sequence;
        if sequence < self.next_sequence || self.buffered.contains_key(&sequence) {
            // Already applied, already buffered, or contained in the
            // snapshot (sequence <= base): idempotent no-op.
            return Err(DeltaError::Duplicate { sequence });
        }
        if sequence > self.next_sequence {
            if self.buffered.len() >= DELTA_BUFFER_CAP {
                // Overflow is an error, not a crash; nothing is mutated.
                return Err(DeltaError::BaseUnavailable);
            }
            self.buffered.insert(sequence, delta.clone());
            self.pending.push(DeltaReceipt {
                applied: false,
                buffered: true,
                sequence,
            });
            return Err(DeltaError::Gap {
                expected: self.next_sequence,
                got: sequence,
            });
        }
        self.apply(delta)?;
        let receipt = DeltaReceipt {
            applied: true,
            buffered: false,
            sequence,
        };
        if let Err(error) = self.drain() {
            // The observed delta applied; its receipt is queued so it is
            // not lost, and the drain failure is reported instead.
            self.pending.push(receipt);
            return Err(error);
        }
        Ok(receipt)
    }

    /// Sequences in `[next_sequence, highest_buffered)` that are neither
    /// applied nor buffered: what the tracker is missing to become
    /// contiguous. At most [`DELTA_MISSING_REPORT_CAP`] entries are
    /// reported; longer gaps are truncated.
    pub fn missing_sequences(&self) -> Vec<u64> {
        let mut missing = Vec::new();
        let Some(highest) = self.buffered.keys().next_back() else {
            return missing;
        };
        let mut sequence = self.next_sequence;
        while sequence < *highest && missing.len() < DELTA_MISSING_REPORT_CAP {
            if !self.buffered.contains_key(&sequence) {
                missing.push(sequence);
            }
            sequence += 1;
        }
        missing
    }

    /// True while any gap remains unfilled or the tracker is stale.
    pub fn needs_full_snapshot(&self) -> bool {
        self.stale || !self.missing_sequences().is_empty()
    }

    /// Optional snapshot priming: finding ids that predate the stream (from
    /// the initial snapshot) that later deltas may patch. Without seeding,
    /// only ids introduced by `new_findings` of applied deltas are known.
    pub fn seed_findings(&mut self, ids: &[String]) {
        for id in ids {
            self.known_findings.insert(id.clone());
        }
    }

    /// Returns and clears the receipts queued since the last call: one
    /// buffering receipt per out-of-order observation, one application
    /// receipt per drained buffered delta, and the observed delta's receipt
    /// when `observe` returned an error caused by a drained delta.
    pub fn drain_ready(&mut self) -> Vec<DeltaReceipt> {
        std::mem::take(&mut self.pending)
    }

    /// Captures the tracker state for persistence.
    pub fn state(&self) -> DeltaTrackerState {
        DeltaTrackerState {
            task_id: self.task_id.clone(),
            base_sequence: self.base_sequence,
            next_sequence: self.next_sequence,
            buffered: self.buffered.values().cloned().collect(),
            known_findings: self.known_findings.iter().cloned().collect(),
            stale: self.stale,
            pending_receipts: self.pending.clone(),
        }
    }

    /// Rebuilds a tracker from a persisted state. States produced by
    /// [`DeltaTracker::state`] restore exactly; impossible entries are
    /// normalized away (buffered deltas for another task or base, at or
    /// below the cursor; duplicate sequences keep the last entry).
    pub fn restore(state: DeltaTrackerState) -> Self {
        let next_sequence = state
            .next_sequence
            .max(state.base_sequence.saturating_add(1));
        let mut buffered = BTreeMap::new();
        for delta in state.buffered {
            if delta.task_id == state.task_id
                && delta.base_sequence == state.base_sequence
                && delta.sequence > next_sequence
            {
                buffered.insert(delta.sequence, delta);
            }
        }
        Self {
            task_id: state.task_id,
            base_sequence: state.base_sequence,
            next_sequence,
            buffered,
            known_findings: state.known_findings.into_iter().collect(),
            stale: state.stale,
            pending: state.pending_receipts,
        }
    }

    /// Validates and commits one delta at the cursor. On validation failure
    /// no state changes.
    fn apply(&mut self, delta: &Delta) -> Result<(), DeltaError> {
        let introduced: BTreeSet<&str> = delta
            .new_findings
            .iter()
            .map(|finding| finding.id.as_str())
            .collect();
        for update in &delta.updates {
            if let FieldUpdate::UpdateFinding { id, .. } = update {
                if !self.known_findings.contains(id) && !introduced.contains(id.as_str()) {
                    return Err(DeltaError::UnknownFinding { id: id.clone() });
                }
            }
        }
        self.next_sequence = self.next_sequence.saturating_add(1);
        for finding in &delta.new_findings {
            self.known_findings.insert(finding.id.clone());
        }
        Ok(())
    }

    /// Applies buffered deltas that became contiguous. A buffered delta
    /// that fails finding validation is dropped from the buffer (never
    /// applied); the error surfaces from the observe call that triggered
    /// the drain, and the failed sequence can be retried.
    fn drain(&mut self) -> Result<(), DeltaError> {
        while let Some(delta) = self.buffered.remove(&self.next_sequence) {
            let sequence = delta.sequence;
            match self.apply(&delta) {
                Ok(()) => self.pending.push(DeltaReceipt {
                    applied: true,
                    buffered: true,
                    sequence,
                }),
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }
}

/// Returns the value of a key that must appear at most once.
fn single<'a>(message: &'a Occ1Message, key: &str) -> Result<Option<&'a str>, ContractError> {
    let all = message.get_all(key);
    match all.as_slice() {
        [] => Ok(None),
        [value] => Ok(Some(*value)),
        _ => Err(ContractError::Occ1Conversion(format!(
            "field {key:?} must appear at most once"
        ))),
    }
}

/// Parses a required singular u64 field.
fn required_u64(message: &Occ1Message, key: &'static str) -> Result<u64, ContractError> {
    let value = single(message, key)?.ok_or(ContractError::MissingRequired { field: key })?;
    value.parse::<u64>().map_err(|_| {
        ContractError::Occ1Conversion(format!("{key} {value:?} is not a u64"))
    })
}

/// Maps an indexed key to its family and 1-based index, or None for
/// unrelated keys. Longest prefixes are matched first.
fn classify_indexed_key(key: &str) -> Option<(&'static str, usize)> {
    const FAMILIES: [(&str, &str); 8] = [
        ("NEWC", "NEWC"),
        ("NEWE", "NEWE"),
        ("NEW", "NEW"),
        ("EL", "EL"),
        ("ED", "ED"),
        ("ER", "ER"),
        ("EV", "EV"),
        ("E", "E"),
    ];
    for (prefix, family) in FAMILIES {
        if let Some(rest) = key.strip_prefix(prefix) {
            if !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_digit()) {
                if let Ok(index) = rest.parse::<usize>() {
                    return Some((family, index));
                }
            }
        }
    }
    None
}

/// Collects the indices present for one family.
fn family_indices(seen: &BTreeSet<(&'static str, usize)>, family: &str) -> BTreeSet<usize> {
    seen.iter()
        .filter(|(known, _)| *known == family)
        .map(|(_, index)| *index)
        .collect()
}

/// Requires the family's indices to be exactly `1..=max` with no repeats
/// (repeats are rejected earlier).
fn require_contiguous(indices: &BTreeSet<usize>, family: &str) -> Result<(), ContractError> {
    if let Some(max) = indices.iter().next_back() {
        if indices.contains(&0) || indices.len() != *max {
            return Err(ContractError::Occ1Conversion(format!(
                "{family} indices must be contiguous from 1"
            )));
        }
    }
    Ok(())
}

/// Reference ids must survive the UPDATE grammar and NEWE lists.
fn validate_reference_id(id: &str) -> Result<(), ContractError> {
    if id.is_empty() || [':', '=', ','].iter().any(|c| id.contains(*c)) {
        return Err(ContractError::Occ1Conversion(format!(
            "reference id {id:?} must be non-empty and free of ':', '=' and ','"
        )));
    }
    Ok(())
}
