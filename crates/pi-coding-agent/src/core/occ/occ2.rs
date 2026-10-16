//! OCC-2: symbolic wire codec for the `optimus.agent-comm.v1` contract.
//!
//! OCC-2 encodes the same typed contract ([`AgentCommMessage`]) that OCC-1
//! encodes, as compact symbolic field lines: a dialect (see
//! [`crate::core::occ::dialect`]) assigns each canonical contract field a
//! short wire symbol, and the codec emits `<SYMBOL>[<index>][<marker>]
//! <value>` lines behind a dialect header. Dialects are data; the default
//! hand-designed `sym-v1` dialect is a starting point that the offline
//! discovery harness will evolve later. No information is lost relative
//! to the OCC-1 encoding: every field the OCC-1 contract encoder emits has
//! a slot in every dialect.
//!
//! Wire format (strict; malformed input is rejected, never guessed):
//! - Line 1 is the dialect header, exactly three space-separated tokens:
//!   `OCC/2 D=<dialect_id>@<version> C=<codebook sha256 prefix>`, where
//!   the hash prefix is 12 lowercase hex characters.
//! - Every following line is `<KEY><operator><VALUE>`; the operator comes
//!   from the dialect grammar (`=` in sym-v1) and the value is everything
//!   after its first occurrence.
//! - Keys are `<base symbol>` for scalar and repeatable slots,
//!   `<base symbol><index>` for indexed slots, plus an optional
//!   single-character subfield marker for grouped fields (e.g. `F1c` is
//!   the confidence of finding 1). Indices are 1-based and must be
//!   contiguous from 1; a gap is corruption, not truncation.
//! - Duplicate non-repeatable fields, dangling evidence references, a
//!   verification record without its status and method, or any value that
//!   does not parse for its slot are errors.
//! - Values are UTF-8 without newlines or C0/C1 control characters, with
//!   the same per-value and per-message bounds as OCC-1.
//! - CRLF line endings and one trailing newline are tolerated; interior
//!   blank lines are errors.
//!
//! Fallback: a message may carry both encodings. After the symbolic lines,
//! a line exactly [`OCC2_FALLBACK_SENTINEL`] introduces a complete OCC-1
//! wire text for listeners that do not know the dialect.
//! [`decode`] verifies that both sections decode to the same contract
//! (disagreement is an error, never a silent choice).
//! [`occ1_fallback`] re-parses via OCC-1: it uses the embedded section
//! when the payload carries both, and otherwise attempts a plain OCC-1
//! parse, so callers can decode conservatively after an OCC-2 failure.
//!
//! Security model: unchanged from OCC-1. Sender identity is established
//! by the daemon transport and never trusted from message text; parsers
//! never invent missing required values. The codebook hash prefix binds
//! the wire bytes to a registered dialect version, so an unknown or
//! tampered dialect cannot be silently reinterpreted.
//!
//! Campaign rules: pure, deterministic, offline-testable logic. No
//! network, no model APIs, no filesystem access. Never silently drop
//! contradictory evidence or uncertainty; reject instead of inventing.

use std::collections::BTreeMap;

use thiserror::Error;

use crate::core::occ::contract::{
    AgentCommMessage, ContractError, EvidenceRef, Finding, Verification,
};
use crate::core::occ::dialect::{
    slots, subfields, DialectCard, DialectError, DialectRegistry, Resolved, CODEBOOK_PREFIX_LEN,
    MAX_DIALECT_ID_LEN,
};
use crate::core::occ::occ1::Occ1Message;

/// Exact required first token of every OCC-2 header line.
pub const OCC2_HEADER: &str = "OCC/2";
/// Sentinel line introducing the embedded OCC-1 fallback section of a
/// dual payload. It starts with `#`, which no symbolic key or OCC-1 key
/// can start with, so it cannot collide with either encoding.
pub const OCC2_FALLBACK_SENTINEL: &str = "#FALLBACK-OCC1";
/// Maximum number of symbolic field lines in one message.
pub const OCC2_MAX_FIELDS: usize = 128;
/// Maximum length of a field value in bytes.
pub const OCC2_MAX_VALUE_BYTES: usize = 4096;
/// Maximum size of the symbolic section (header plus field lines).
pub const OCC2_MAX_SYMBOLIC_BYTES: usize = 64 * 1024;
/// Maximum size of a whole message, including an embedded OCC-1 fallback
/// section.
pub const OCC2_MAX_MESSAGE_BYTES: usize = 128 * 1024;

/// OCC-2 encode/decode failure. Line numbers are 1-based; line 1 is the
/// dialect header. Line 0 marks errors detected after the line loop or
/// on the encode side.
#[derive(Debug, Error)]
pub enum Occ2Error {
    /// The input was empty.
    #[error("OCC-2 input is empty")]
    Empty,
    /// The header line was not the exact three-token dialect header.
    #[error("OCC-2 header {0:?} is malformed; expected \"OCC/2 D=<dialect>@<version> C=<12 lowercase hex>\"")]
    BadHeader(String),
    /// The advertised dialect id or version is not registered.
    #[error("unknown dialect {dialect:?}@{version}")]
    UnknownDialect {
        /// Advertised dialect id.
        dialect: String,
        /// Advertised version.
        version: u32,
    },
    /// The header hash prefix does not match the registered dialect card.
    #[error("codebook hash mismatch for {dialect:?}@{version}: header says {advertised:?}, registered card says {registered:?}")]
    HashMismatch {
        /// Advertised dialect id.
        dialect: String,
        /// Advertised version.
        version: u32,
        /// Hash prefix carried on the wire header.
        advertised: String,
        /// Hash prefix of the registered card.
        registered: String,
    },
    /// A non-header line lacked the dialect operator.
    #[error("line {line}: expected SYMBOL<op>VALUE, no operator {op:?} found")]
    MissingSeparator {
        /// 1-based line number.
        line: usize,
        /// The dialect operator.
        op: char,
    },
    /// A key matched a base symbol but its remainder is not a valid
    /// index or subfield marker.
    #[error("line {line}: key {key:?} is not valid for dialect {dialect:?}")]
    InvalidSymbol {
        /// 1-based line number.
        line: usize,
        /// The offending key.
        key: String,
        /// The dialect id.
        dialect: String,
    },
    /// A key matched no base symbol of the dialect.
    #[error("line {line}: key {key:?} does not match any symbol in dialect {dialect:?}")]
    UnknownSymbol {
        /// 1-based line number.
        line: usize,
        /// The offending key.
        key: String,
        /// The dialect id.
        dialect: String,
    },
    /// An interior line was empty.
    #[error("line {line}: blank lines are not allowed")]
    BlankLine {
        /// 1-based line number.
        line: usize,
    },
    /// A value contained a forbidden control character.
    #[error("line {line}: value of {key:?} contains a control character")]
    ControlChar {
        /// 1-based line number.
        line: usize,
        /// The field key.
        key: String,
    },
    /// A value exceeded its byte bound.
    #[error("value of {key:?} exceeds {limit} bytes")]
    ValueTooLarge {
        /// The field key.
        key: String,
        /// The byte bound.
        limit: usize,
    },
    /// The message exceeded the field-count bound.
    #[error("message exceeds the {limit} field bound")]
    TooManyFields {
        /// The field bound.
        limit: usize,
    },
    /// The message exceeded its byte bound.
    #[error("message exceeds the {limit} byte bound")]
    TooLarge {
        /// The byte bound.
        limit: usize,
    },
    /// A value did not parse for its slot.
    #[error("line {line}: value of {key:?} is invalid: {reason}")]
    BadValue {
        /// 1-based line number (0 when detected after the line loop).
        line: usize,
        /// The field key.
        key: String,
        /// Why the value is invalid.
        reason: String,
    },
    /// A non-repeatable field appeared more than once.
    #[error("field {key:?} appears more than once")]
    DuplicateField {
        /// The duplicated field key.
        key: String,
    },
    /// An indexed family had a hole below its highest index.
    #[error("{prefix} index {index} missing below the highest index {highest}")]
    IndexGap {
        /// The family prefix (e.g. `F`).
        prefix: String,
        /// The first missing index.
        index: usize,
        /// The highest index present.
        highest: usize,
    },
    /// Verification lines did not form the complete all-or-nothing group.
    #[error("verification fields must appear as a complete group: {0}")]
    VerificationGroup(String),
    /// The embedded OCC-1 fallback section failed to parse.
    #[error("embedded OCC-1 fallback section is invalid: {0}")]
    FallbackSection(String),
    /// A dual payload's symbolic and OCC-1 sections disagree.
    #[error("dual payload sections disagree: the symbolic decode differs from the OCC-1 fallback decode")]
    DualMismatch,
    /// The dialect card is invalid.
    #[error("dialect card is invalid: {0}")]
    Dialect(#[from] DialectError),
    /// The typed contract rejected the message.
    #[error(transparent)]
    Contract(#[from] ContractError),
}

/// Encodes the contract with the given dialect: strict header plus
/// canonical symbolic field lines, in the same field order the OCC-1
/// contract encoder emits. The message must pass
/// [`AgentCommMessage::validate`]; values that the dialect grammar cannot
/// represent losslessly (non-positional evidence references) are errors,
/// never silent loss.
pub fn encode(message: &AgentCommMessage, card: &DialectCard) -> Result<String, Occ2Error> {
    encode_symbolic(message, card)
}

/// Encodes the contract twice: the symbolic OCC-2 section, then the
/// [`OCC2_FALLBACK_SENTINEL`] line and a complete OCC-1 wire text.
/// Listeners without the dialect re-parse via OCC-1; listeners with it
/// verify both sections agree.
pub fn encode_dual(message: &AgentCommMessage, card: &DialectCard) -> Result<String, Occ2Error> {
    let mut out = encode_symbolic(message, card)?;
    out.push('\n');
    out.push_str(OCC2_FALLBACK_SENTINEL);
    out.push('\n');
    let occ1 = message.to_occ1()?;
    let occ1_text = occ1
        .encode()
        .map_err(|error| Occ2Error::FallbackSection(error.to_string()))?;
    if out.len() + occ1_text.len() > OCC2_MAX_MESSAGE_BYTES {
        return Err(Occ2Error::TooLarge {
            limit: OCC2_MAX_MESSAGE_BYTES,
        });
    }
    out.push_str(&occ1_text);
    Ok(out)
}

/// Strict decode. Unknown dialects, version misses, hash mismatches,
/// malformed lines, duplicate fields, index gaps and any contract-level
/// violation are errors; nothing is guessed or repaired. Dual payloads
/// must decode to the same contract in both sections.
pub fn decode(text: &str, registry: &DialectRegistry) -> Result<AgentCommMessage, Occ2Error> {
    if text.is_empty() {
        return Err(Occ2Error::Empty);
    }
    if text.len() > OCC2_MAX_MESSAGE_BYTES {
        return Err(Occ2Error::TooLarge {
            limit: OCC2_MAX_MESSAGE_BYTES,
        });
    }
    let (symbolic, fallback) = match split_fallback(text) {
        Some((symbolic, fallback)) => (symbolic, Some(fallback)),
        None => (text, None),
    };
    let message = decode_symbolic(symbolic, registry)?;
    if let Some(fallback) = fallback {
        let decoded = Occ1Message::decode(fallback)
            .map_err(|error| Occ2Error::FallbackSection(error.to_string()))?;
        let via_occ1 = AgentCommMessage::from_occ1(&decoded)
            .map_err(|error| Occ2Error::FallbackSection(error.to_string()))?;
        if via_occ1 != message {
            return Err(Occ2Error::DualMismatch);
        }
    }
    Ok(message)
}

/// Conservative decode via OCC-1 only. When the payload carries both
/// encodings, the embedded OCC-1 section is re-parsed; otherwise the
/// whole text is parsed as plain OCC-1. This never decodes the symbolic
/// section, so it works without a registry, for unknown dialects and for
/// hash mismatches. It fails when there is no OCC-1 form to fall back on;
/// use [`decode`] for the strict path.
pub fn occ1_fallback(text: &str) -> Result<AgentCommMessage, ContractError> {
    if let Some((_, fallback)) = split_fallback(text) {
        let decoded = Occ1Message::decode(fallback)?;
        return AgentCommMessage::from_occ1(&decoded);
    }
    let decoded = Occ1Message::decode(text)?;
    AgentCommMessage::from_occ1(&decoded)
}

// ---------------------------------------------------------------------------
// Encode
// ---------------------------------------------------------------------------

/// All symbols of a validated card, resolved once. Constructed only
/// after [`DialectCard::validate`], which guarantees every lookup
/// succeeds; the error path is kept anyway so a bug cannot panic.
struct Symbols {
    message_id: String,
    task_id: String,
    parent_task_id: String,
    trace_id: String,
    sender: String,
    recipient: String,
    kind: String,
    status: String,
    sequence: String,
    timestamp: String,
    expiry: String,
    finding_statement: String,
    evidence_ref: String,
    artifact: String,
    verification_claim: String,
    dependency: String,
    next_action: String,
    finding_confidence: char,
    finding_evidence: char,
    evidence_line_range: char,
    evidence_digest: char,
    evidence_revision: char,
    evidence_verified: char,
    verification_status: char,
    verification_method: char,
    verification_receipt: char,
}

impl Symbols {
    fn resolve(card: &DialectCard) -> Result<Self, Occ2Error> {
        let slot = |name: &str| -> Result<String, Occ2Error> {
            card.symbol_table
                .slots
                .get(name)
                .cloned()
                .ok_or_else(|| Occ2Error::Dialect(DialectError::MissingSlot(name.to_string())))
        };
        let marker = |name: &str| -> Result<char, Occ2Error> {
            card.symbol_table
                .marker(name)
                .ok_or_else(|| Occ2Error::Dialect(DialectError::MissingSubfield(name.to_string())))
        };
        Ok(Self {
            message_id: slot(slots::MESSAGE_ID)?,
            task_id: slot(slots::TASK_ID)?,
            parent_task_id: slot(slots::PARENT_TASK_ID)?,
            trace_id: slot(slots::TRACE_ID)?,
            sender: slot(slots::SENDER)?,
            recipient: slot(slots::RECIPIENT)?,
            kind: slot(slots::KIND)?,
            status: slot(slots::STATUS)?,
            sequence: slot(slots::SEQUENCE)?,
            timestamp: slot(slots::TIMESTAMP)?,
            expiry: slot(slots::EXPIRY)?,
            finding_statement: slot(slots::FINDING_STATEMENT)?,
            evidence_ref: slot(slots::EVIDENCE_REF)?,
            artifact: slot(slots::ARTIFACT)?,
            verification_claim: slot(slots::VERIFICATION_CLAIM)?,
            dependency: slot(slots::DEPENDENCY)?,
            next_action: slot(slots::NEXT_ACTION)?,
            finding_confidence: marker(subfields::FINDING_CONFIDENCE)?,
            finding_evidence: marker(subfields::FINDING_EVIDENCE)?,
            evidence_line_range: marker(subfields::EVIDENCE_LINE_RANGE)?,
            evidence_digest: marker(subfields::EVIDENCE_DIGEST)?,
            evidence_revision: marker(subfields::EVIDENCE_REVISION)?,
            evidence_verified: marker(subfields::EVIDENCE_VERIFIED)?,
            verification_status: marker(subfields::VERIFICATION_STATUS)?,
            verification_method: marker(subfields::VERIFICATION_METHOD)?,
            verification_receipt: marker(subfields::VERIFICATION_RECEIPT)?,
        })
    }
}

fn encode_symbolic(message: &AgentCommMessage, card: &DialectCard) -> Result<String, Occ2Error> {
    card.validate()?;
    message.validate()?;
    let symbols = Symbols::resolve(card)?;
    let operator = card.grammar.operator;
    let separator = card.grammar.list_separator;
    let mut out = String::with_capacity(256);
    out.push_str(OCC2_HEADER);
    out.push_str(" D=");
    out.push_str(&card.dialect_id);
    out.push('@');
    out.push_str(&card.version.to_string());
    out.push_str(" C=");
    out.push_str(&card.sha256[..CODEBOOK_PREFIX_LEN]);
    let mut fields = 0usize;

    // Field order mirrors the OCC-1 contract encoder exactly.
    if let Some(id) = &message.message_id {
        push_field(&mut out, &mut fields, operator, &symbols.message_id, id)?;
    }
    push_field(
        &mut out,
        &mut fields,
        operator,
        &symbols.task_id,
        &message.task_id,
    )?;
    if let Some(parent) = &message.parent_task_id {
        push_field(
            &mut out,
            &mut fields,
            operator,
            &symbols.parent_task_id,
            parent,
        )?;
    }
    if let Some(trace) = &message.trace_id {
        push_field(&mut out, &mut fields, operator, &symbols.trace_id, trace)?;
    }
    if let Some(sender) = &message.sender_identity {
        push_field(&mut out, &mut fields, operator, &symbols.sender, sender)?;
    }
    if let Some(recipient) = &message.recipient_identity {
        push_field(
            &mut out,
            &mut fields,
            operator,
            &symbols.recipient,
            recipient,
        )?;
    }
    push_field(
        &mut out,
        &mut fields,
        operator,
        &symbols.kind,
        &message.kind,
    )?;
    push_field(
        &mut out,
        &mut fields,
        operator,
        &symbols.status,
        &message.status,
    )?;
    if let Some(sequence) = message.sequence {
        push_field(
            &mut out,
            &mut fields,
            operator,
            &symbols.sequence,
            &sequence.to_string(),
        )?;
    }
    for (index, finding) in message.findings.iter().enumerate() {
        let n = index + 1;
        let statement_key = format!("{}{n}", symbols.finding_statement);
        push_field(
            &mut out,
            &mut fields,
            operator,
            &statement_key,
            &finding.statement,
        )?;
        if let Some(confidence) = finding.confidence {
            let key = format!(
                "{}{n}{}",
                symbols.finding_statement, symbols.finding_confidence
            );
            push_field(
                &mut out,
                &mut fields,
                operator,
                &key,
                &format!("{confidence:.3}"),
            )?;
        }
        if !finding.evidence.is_empty() {
            // Evidence links are 1-based positional indices into the
            // message's evidence list, joined by the list separator.
            let mut references = Vec::with_capacity(finding.evidence.len());
            for reference in &finding.evidence {
                let number = reference
                    .strip_prefix('E')
                    .and_then(|suffix| suffix.parse::<usize>().ok())
                    .filter(|number| *number >= 1 && *number <= message.evidence.len())
                    .ok_or_else(|| Occ2Error::BadValue {
                        line: 0,
                        key: format!(
                            "{}{n}{}",
                            symbols.finding_statement, symbols.finding_evidence
                        ),
                        reason: format!(
                            "finding {n} references evidence id {reference:?}, which is not \
                                 a positional E<n> reference into 1..={}",
                            message.evidence.len()
                        ),
                    })?;
                references.push(number.to_string());
            }
            let key = format!(
                "{}{n}{}",
                symbols.finding_statement, symbols.finding_evidence
            );
            let value = references.join(&separator.to_string());
            push_field(&mut out, &mut fields, operator, &key, &value)?;
        }
    }
    for (index, evidence) in message.evidence.iter().enumerate() {
        let n = index + 1;
        let key = format!("{}{n}", symbols.evidence_ref);
        let value = format!("{}:{}", evidence.kind, evidence.locator);
        push_field(&mut out, &mut fields, operator, &key, &value)?;
        if let Some((start, end)) = evidence.line_range {
            let key = format!("{}{n}{}", symbols.evidence_ref, symbols.evidence_line_range);
            push_field(
                &mut out,
                &mut fields,
                operator,
                &key,
                &format!("{start}-{end}"),
            )?;
        }
        if let Some(digest) = &evidence.digest {
            let key = format!("{}{n}{}", symbols.evidence_ref, symbols.evidence_digest);
            push_field(&mut out, &mut fields, operator, &key, digest)?;
        }
        if let Some(revision) = &evidence.revision {
            let key = format!("{}{n}{}", symbols.evidence_ref, symbols.evidence_revision);
            push_field(&mut out, &mut fields, operator, &key, revision)?;
        }
        if let Some(verified) = evidence.verified {
            let key = format!("{}{n}{}", symbols.evidence_ref, symbols.evidence_verified);
            let value = if verified { "1" } else { "0" };
            push_field(&mut out, &mut fields, operator, &key, value)?;
        }
    }
    for (index, artifact) in message.artifacts.iter().enumerate() {
        let key = format!("{}{}", symbols.artifact, index + 1);
        push_field(&mut out, &mut fields, operator, &key, artifact)?;
    }
    if let Some(verification) = &message.verification {
        push_field(
            &mut out,
            &mut fields,
            operator,
            &symbols.verification_claim,
            &verification.claim,
        )?;
        let key = format!(
            "{}{}",
            symbols.verification_claim, symbols.verification_status
        );
        push_field(&mut out, &mut fields, operator, &key, &verification.status)?;
        let key = format!(
            "{}{}",
            symbols.verification_claim, symbols.verification_method
        );
        push_field(&mut out, &mut fields, operator, &key, &verification.method)?;
        if let Some(receipt) = &verification.receipt {
            let key = format!(
                "{}{}",
                symbols.verification_claim, symbols.verification_receipt
            );
            push_field(&mut out, &mut fields, operator, &key, receipt)?;
        }
    }
    for (index, dependency) in message.dependencies.iter().enumerate() {
        let key = format!("{}{}", symbols.dependency, index + 1);
        push_field(&mut out, &mut fields, operator, &key, dependency)?;
    }
    for action in &message.next_actions {
        push_field(
            &mut out,
            &mut fields,
            operator,
            &symbols.next_action,
            action,
        )?;
    }
    if let Some(timestamp) = &message.timestamp {
        push_field(
            &mut out,
            &mut fields,
            operator,
            &symbols.timestamp,
            timestamp,
        )?;
    }
    if let Some(expiry) = &message.expiry {
        push_field(&mut out, &mut fields, operator, &symbols.expiry, expiry)?;
    }
    Ok(out)
}

/// Appends one field line, enforcing the value and message bounds.
fn push_field(
    out: &mut String,
    fields: &mut usize,
    operator: char,
    key: &str,
    value: &str,
) -> Result<(), Occ2Error> {
    if *fields >= OCC2_MAX_FIELDS {
        return Err(Occ2Error::TooManyFields {
            limit: OCC2_MAX_FIELDS,
        });
    }
    if value.len() > OCC2_MAX_VALUE_BYTES {
        return Err(Occ2Error::ValueTooLarge {
            key: key.to_string(),
            limit: OCC2_MAX_VALUE_BYTES,
        });
    }
    if value.chars().any(char::is_control) {
        return Err(Occ2Error::ControlChar {
            line: 0,
            key: key.to_string(),
        });
    }
    out.push('\n');
    out.push_str(key);
    out.push(operator);
    out.push_str(value);
    *fields += 1;
    if out.len() > OCC2_MAX_SYMBOLIC_BYTES {
        return Err(Occ2Error::TooLarge {
            limit: OCC2_MAX_SYMBOLIC_BYTES,
        });
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Decode
// ---------------------------------------------------------------------------

/// One parsed wire field, dispatching on the canonical slot of the
/// dialect's symbol table.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WireSlot {
    MessageId,
    TaskId,
    ParentTaskId,
    TraceId,
    Sender,
    Recipient,
    Kind,
    Status,
    Sequence,
    Timestamp,
    Expiry,
    FindingStatement,
    FindingConfidence,
    FindingEvidence,
    EvidenceRef,
    EvidenceLineRange,
    EvidenceDigest,
    EvidenceRevision,
    EvidenceVerified,
    Artifact,
    VerificationClaim,
    VerificationStatus,
    VerificationMethod,
    VerificationReceipt,
    Dependency,
    NextAction,
}

#[derive(Default)]
struct WireFinding {
    statement: Option<String>,
    confidence: Option<f32>,
    evidence: Option<Vec<usize>>,
}

#[derive(Default)]
struct WireEvidence {
    kind_locator: Option<String>,
    line_range: Option<(u32, u32)>,
    digest: Option<String>,
    revision: Option<String>,
    verified: Option<bool>,
}

/// Accumulated symbolic fields of one message.
#[derive(Default)]
struct WireMessage {
    message_id: Option<String>,
    task_id: Option<String>,
    parent_task_id: Option<String>,
    trace_id: Option<String>,
    sender: Option<String>,
    recipient: Option<String>,
    kind: Option<String>,
    status: Option<String>,
    sequence: Option<u64>,
    timestamp: Option<String>,
    expiry: Option<String>,
    findings: BTreeMap<usize, WireFinding>,
    evidence: BTreeMap<usize, WireEvidence>,
    artifacts: BTreeMap<usize, String>,
    dependencies: BTreeMap<usize, String>,
    next_actions: Vec<String>,
    verification_claim: Option<String>,
    verification_status: Option<String>,
    verification_method: Option<String>,
    verification_receipt: Option<String>,
}

fn decode_symbolic(text: &str, registry: &DialectRegistry) -> Result<AgentCommMessage, Occ2Error> {
    if text.is_empty() {
        return Err(Occ2Error::Empty);
    }
    if text.len() > OCC2_MAX_SYMBOLIC_BYTES {
        return Err(Occ2Error::TooLarge {
            limit: OCC2_MAX_SYMBOLIC_BYTES,
        });
    }
    let trimmed = text.strip_suffix('\n').unwrap_or(text);
    let mut lines = trimmed.split('\n');
    let header = lines.next().ok_or(Occ2Error::Empty)?;
    let header = header.strip_suffix('\r').unwrap_or(header);
    let (dialect, version, codebook_prefix) = parse_header(header)?;
    let card = match registry.resolve(&dialect, version, &codebook_prefix) {
        Resolved::Dialect(card) => card,
        Resolved::Unknown { .. } => return Err(Occ2Error::UnknownDialect { dialect, version }),
        Resolved::HashMismatch {
            advertised,
            registered,
            ..
        } => {
            return Err(Occ2Error::HashMismatch {
                dialect,
                version,
                advertised,
                registered,
            })
        }
    };
    let separator = card.grammar.list_separator;
    let mut wire = WireMessage::default();
    let mut fields = 0usize;
    for (offset, raw_line) in lines.enumerate() {
        let line_no = offset + 2; // header is line 1
        let line = raw_line.strip_suffix('\r').unwrap_or(raw_line);
        if line.is_empty() {
            return Err(Occ2Error::BlankLine { line: line_no });
        }
        if fields >= OCC2_MAX_FIELDS {
            return Err(Occ2Error::TooManyFields {
                limit: OCC2_MAX_FIELDS,
            });
        }
        let Some((key, value)) = line.split_once(card.grammar.operator) else {
            return Err(Occ2Error::MissingSeparator {
                line: line_no,
                op: card.grammar.operator,
            });
        };
        if value.len() > OCC2_MAX_VALUE_BYTES {
            return Err(Occ2Error::ValueTooLarge {
                key: key.to_string(),
                limit: OCC2_MAX_VALUE_BYTES,
            });
        }
        if value.chars().any(char::is_control) {
            return Err(Occ2Error::ControlChar {
                line: line_no,
                key: key.to_string(),
            });
        }
        let (slot, index) = parse_key(key, card)?;
        apply_field(&mut wire, slot, index, value, key, line_no, separator)?;
        fields += 1;
    }
    finish(wire)
}

/// Parses the three-token dialect header.
fn parse_header(header: &str) -> Result<(String, u32, String), Occ2Error> {
    let bad = || Occ2Error::BadHeader(header.to_string());
    let tokens: Vec<&str> = header.split(' ').collect();
    if tokens.len() != 3 || tokens[0] != OCC2_HEADER {
        return Err(bad());
    }
    let Some(dialect_token) = tokens[1].strip_prefix("D=") else {
        return Err(bad());
    };
    let Some((dialect, version_text)) = dialect_token.split_once('@') else {
        return Err(bad());
    };
    let valid_dialect = !dialect.is_empty()
        && dialect.len() <= MAX_DIALECT_ID_LEN
        && dialect
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_lowercase())
        && dialect
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    if !valid_dialect
        || version_text.is_empty()
        || version_text.len() > 10
        || !version_text.chars().all(|c| c.is_ascii_digit())
    {
        return Err(bad());
    }
    let version: u32 = version_text.parse().map_err(|_| bad())?;
    if version == 0 {
        return Err(bad());
    }
    let Some(hash_token) = tokens[2].strip_prefix("C=") else {
        return Err(bad());
    };
    if hash_token.len() != CODEBOOK_PREFIX_LEN
        || !hash_token
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(bad());
    }
    Ok((dialect.to_string(), version, hash_token.to_string()))
}

/// Splits a dual payload at the fallback sentinel line. Returns
/// (symbolic section, OCC-1 section); the symbolic section keeps its
/// trailing newline, which [`decode_symbolic`] strips like OCC-1 does.
fn split_fallback(text: &str) -> Option<(&str, &str)> {
    let mut offset = 0usize;
    for line in text.split('\n') {
        let line_start = offset;
        let line_end = line_start + line.len();
        offset = line_end + 1;
        let bare = line.strip_suffix('\r').unwrap_or(line);
        if bare == OCC2_FALLBACK_SENTINEL {
            let fallback_start = (line_end + 1).min(text.len());
            return Some((&text[..line_start], &text[fallback_start..]));
        }
    }
    None
}

/// Parses one field key against the dialect's symbol table. The table is
/// prefix-free (validated on insert), so at most one base symbol matches.
fn parse_key(key: &str, card: &DialectCard) -> Result<(WireSlot, Option<usize>), Occ2Error> {
    let table = &card.symbol_table;
    for (slot, symbol) in table.slots.iter() {
        let Some(rest) = key.strip_prefix(symbol.as_str()) else {
            continue;
        };
        let invalid = Occ2Error::InvalidSymbol {
            line: 0,
            key: key.to_string(),
            dialect: card.dialect_id.clone(),
        };
        return match slot.as_str() {
            slots::MESSAGE_ID => scalar_key(rest, WireSlot::MessageId).ok_or(invalid),
            slots::TASK_ID => scalar_key(rest, WireSlot::TaskId).ok_or(invalid),
            slots::PARENT_TASK_ID => scalar_key(rest, WireSlot::ParentTaskId).ok_or(invalid),
            slots::TRACE_ID => scalar_key(rest, WireSlot::TraceId).ok_or(invalid),
            slots::SENDER => scalar_key(rest, WireSlot::Sender).ok_or(invalid),
            slots::RECIPIENT => scalar_key(rest, WireSlot::Recipient).ok_or(invalid),
            slots::KIND => scalar_key(rest, WireSlot::Kind).ok_or(invalid),
            slots::STATUS => scalar_key(rest, WireSlot::Status).ok_or(invalid),
            slots::SEQUENCE => scalar_key(rest, WireSlot::Sequence).ok_or(invalid),
            slots::TIMESTAMP => scalar_key(rest, WireSlot::Timestamp).ok_or(invalid),
            slots::EXPIRY => scalar_key(rest, WireSlot::Expiry).ok_or(invalid),
            slots::NEXT_ACTION => scalar_key(rest, WireSlot::NextAction).ok_or(invalid),
            slots::VERIFICATION_CLAIM => verification_key(rest, table).ok_or(invalid),
            slots::FINDING_STATEMENT => finding_key(rest, table).ok_or(invalid),
            slots::EVIDENCE_REF => evidence_key(rest, table).ok_or(invalid),
            slots::ARTIFACT => indexed_key(rest)
                .map(|index| (WireSlot::Artifact, Some(index)))
                .ok_or(invalid),
            slots::DEPENDENCY => indexed_key(rest)
                .map(|index| (WireSlot::Dependency, Some(index)))
                .ok_or(invalid),
            other => {
                let _ = other;
                Err(Occ2Error::UnknownSymbol {
                    line: 0,
                    key: key.to_string(),
                    dialect: card.dialect_id.clone(),
                })
            }
        };
    }
    Err(Occ2Error::UnknownSymbol {
        line: 0,
        key: key.to_string(),
        dialect: card.dialect_id.clone(),
    })
}

fn scalar_key(rest: &str, slot: WireSlot) -> Option<(WireSlot, Option<usize>)> {
    rest.is_empty().then_some((slot, None))
}

/// Parses a 1-based decimal index with no trailing characters.
fn indexed_key(rest: &str) -> Option<usize> {
    if rest.is_empty() || !rest.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let index: usize = rest.parse().ok()?;
    (index >= 1).then_some(index)
}

/// Splits `<digits>[<one lowercase marker>]` into (index, marker).
fn split_index_marker(rest: &str) -> Option<(usize, Option<char>)> {
    let digit_count = rest.chars().take_while(|c| c.is_ascii_digit()).count();
    let (digits, tail) = rest.split_at(digit_count);
    if digits.is_empty() {
        return None;
    }
    let index: usize = digits.parse().ok()?;
    if index == 0 {
        return None;
    }
    let mut chars = tail.chars();
    match chars.next() {
        None => Some((index, None)),
        Some(marker) if marker.is_ascii_lowercase() && chars.next().is_none() => {
            Some((index, Some(marker)))
        }
        _ => None,
    }
}

fn finding_key(
    rest: &str,
    table: &crate::core::occ::dialect::SymbolTable,
) -> Option<(WireSlot, Option<usize>)> {
    let (index, marker) = split_index_marker(rest)?;
    let slot = match marker {
        None => WireSlot::FindingStatement,
        Some(marker) if table.marker(subfields::FINDING_CONFIDENCE) == Some(marker) => {
            WireSlot::FindingConfidence
        }
        Some(marker) if table.marker(subfields::FINDING_EVIDENCE) == Some(marker) => {
            WireSlot::FindingEvidence
        }
        _ => return None,
    };
    Some((slot, Some(index)))
}

fn evidence_key(
    rest: &str,
    table: &crate::core::occ::dialect::SymbolTable,
) -> Option<(WireSlot, Option<usize>)> {
    let (index, marker) = split_index_marker(rest)?;
    let slot = match marker {
        None => WireSlot::EvidenceRef,
        Some(marker) if table.marker(subfields::EVIDENCE_LINE_RANGE) == Some(marker) => {
            WireSlot::EvidenceLineRange
        }
        Some(marker) if table.marker(subfields::EVIDENCE_DIGEST) == Some(marker) => {
            WireSlot::EvidenceDigest
        }
        Some(marker) if table.marker(subfields::EVIDENCE_REVISION) == Some(marker) => {
            WireSlot::EvidenceRevision
        }
        Some(marker) if table.marker(subfields::EVIDENCE_VERIFIED) == Some(marker) => {
            WireSlot::EvidenceVerified
        }
        _ => return None,
    };
    Some((slot, Some(index)))
}

fn verification_key(
    rest: &str,
    table: &crate::core::occ::dialect::SymbolTable,
) -> Option<(WireSlot, Option<usize>)> {
    if rest.is_empty() {
        return Some((WireSlot::VerificationClaim, None));
    }
    let mut chars = rest.chars();
    let marker = chars.next()?;
    if chars.next().is_some() || !marker.is_ascii_lowercase() {
        return None;
    }
    let slot = if table.marker(subfields::VERIFICATION_STATUS) == Some(marker) {
        WireSlot::VerificationStatus
    } else if table.marker(subfields::VERIFICATION_METHOD) == Some(marker) {
        WireSlot::VerificationMethod
    } else if table.marker(subfields::VERIFICATION_RECEIPT) == Some(marker) {
        WireSlot::VerificationReceipt
    } else {
        return None;
    };
    Some((slot, None))
}

fn set_scalar<T>(slot: &mut Option<T>, value: T, key: &str) -> Result<(), Occ2Error> {
    if slot.is_some() {
        return Err(Occ2Error::DuplicateField {
            key: key.to_string(),
        });
    }
    *slot = Some(value);
    Ok(())
}

fn bad_value(key: &str, line: usize, reason: &str) -> Occ2Error {
    Occ2Error::BadValue {
        line,
        key: key.to_string(),
        reason: reason.to_string(),
    }
}

/// Applies one parsed field to the accumulator. Typed values are parsed
/// strictly here; references are range-checked in [`finish`], when the
/// evidence count is known.
fn apply_field(
    wire: &mut WireMessage,
    slot: WireSlot,
    index: Option<usize>,
    value: &str,
    key: &str,
    line: usize,
    separator: char,
) -> Result<(), Occ2Error> {
    match slot {
        WireSlot::MessageId => set_scalar(&mut wire.message_id, value.to_string(), key),
        WireSlot::TaskId => set_scalar(&mut wire.task_id, value.to_string(), key),
        WireSlot::ParentTaskId => set_scalar(&mut wire.parent_task_id, value.to_string(), key),
        WireSlot::TraceId => set_scalar(&mut wire.trace_id, value.to_string(), key),
        WireSlot::Sender => set_scalar(&mut wire.sender, value.to_string(), key),
        WireSlot::Recipient => set_scalar(&mut wire.recipient, value.to_string(), key),
        WireSlot::Kind => set_scalar(&mut wire.kind, value.to_string(), key),
        WireSlot::Status => set_scalar(&mut wire.status, value.to_string(), key),
        WireSlot::Sequence => {
            let sequence: u64 = value
                .parse()
                .map_err(|_| bad_value(key, line, "not a u64"))?;
            set_scalar(&mut wire.sequence, sequence, key)
        }
        WireSlot::Timestamp => set_scalar(&mut wire.timestamp, value.to_string(), key),
        WireSlot::Expiry => set_scalar(&mut wire.expiry, value.to_string(), key),
        WireSlot::FindingStatement => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let finding = wire.findings.entry(index).or_default();
            set_scalar(&mut finding.statement, value.to_string(), key)
        }
        WireSlot::FindingConfidence => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let confidence: f32 = value
                .parse()
                .map_err(|_| bad_value(key, line, "not a number"))?;
            let finding = wire.findings.entry(index).or_default();
            set_scalar(&mut finding.confidence, confidence, key)
        }
        WireSlot::FindingEvidence => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let mut references = Vec::new();
            for part in value.split(separator) {
                let number: usize = part.parse().map_err(|_| {
                    bad_value(key, line, "evidence references must be decimal indices")
                })?;
                if number == 0 {
                    return Err(bad_value(key, line, "evidence indices are 1-based"));
                }
                references.push(number);
            }
            let finding = wire.findings.entry(index).or_default();
            set_scalar(&mut finding.evidence, references, key)
        }
        WireSlot::EvidenceRef => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let evidence = wire.evidence.entry(index).or_default();
            set_scalar(&mut evidence.kind_locator, value.to_string(), key)
        }
        WireSlot::EvidenceLineRange => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let Some((start, end)) = value.split_once('-') else {
                return Err(bad_value(key, line, "must be <start>-<end>"));
            };
            let start: u32 = start
                .parse()
                .map_err(|_| bad_value(key, line, "start is not a u32"))?;
            let end: u32 = end
                .parse()
                .map_err(|_| bad_value(key, line, "end is not a u32"))?;
            let evidence = wire.evidence.entry(index).or_default();
            set_scalar(&mut evidence.line_range, (start, end), key)
        }
        WireSlot::EvidenceDigest => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let evidence = wire.evidence.entry(index).or_default();
            set_scalar(&mut evidence.digest, value.to_string(), key)
        }
        WireSlot::EvidenceRevision => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let evidence = wire.evidence.entry(index).or_default();
            set_scalar(&mut evidence.revision, value.to_string(), key)
        }
        WireSlot::EvidenceVerified => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            let verified = match value {
                "1" => true,
                "0" => false,
                _ => return Err(bad_value(key, line, "must be 1 (verified) or 0 (failed)")),
            };
            let evidence = wire.evidence.entry(index).or_default();
            set_scalar(&mut evidence.verified, verified, key)
        }
        WireSlot::Artifact => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            if wire.artifacts.insert(index, value.to_string()).is_some() {
                return Err(Occ2Error::DuplicateField {
                    key: key.to_string(),
                });
            }
            Ok(())
        }
        WireSlot::Dependency => {
            let index = index.ok_or_else(|| bad_value(key, line, "missing index"))?;
            if wire.dependencies.insert(index, value.to_string()).is_some() {
                return Err(Occ2Error::DuplicateField {
                    key: key.to_string(),
                });
            }
            Ok(())
        }
        WireSlot::VerificationClaim => {
            set_scalar(&mut wire.verification_claim, value.to_string(), key)
        }
        WireSlot::VerificationStatus => {
            set_scalar(&mut wire.verification_status, value.to_string(), key)
        }
        WireSlot::VerificationMethod => {
            set_scalar(&mut wire.verification_method, value.to_string(), key)
        }
        WireSlot::VerificationReceipt => {
            set_scalar(&mut wire.verification_receipt, value.to_string(), key)
        }
        WireSlot::NextAction => {
            wire.next_actions.push(value.to_string());
            Ok(())
        }
    }
}

/// Checks that an indexed family is exactly `1..=n`. Efficient even for a
/// huge forged index: the map size is compared against the highest key
/// before any scan, and the scan itself walks only present keys.
fn contiguous<T>(map: &BTreeMap<usize, T>, prefix: &str) -> Result<(), Occ2Error> {
    if map.is_empty() {
        return Ok(());
    }
    let highest = *map
        .keys()
        .next_back()
        .expect("non-empty map has a last key");
    if map.len() != highest {
        let mut expected = 1usize;
        let mut missing = expected;
        for key in map.keys() {
            if *key != expected {
                missing = expected;
                break;
            }
            expected += 1;
        }
        return Err(Occ2Error::IndexGap {
            prefix: prefix.to_string(),
            index: missing,
            highest,
        });
    }
    Ok(())
}

/// Builds the typed contract from the accumulated fields, enforcing the
/// same grouping and reference rules as the OCC-1 contract decoder.
fn finish(mut wire: WireMessage) -> Result<AgentCommMessage, Occ2Error> {
    let task_id =
        wire.task_id
            .clone()
            .ok_or(Occ2Error::Contract(ContractError::MissingRequired {
                field: "task_id",
            }))?;
    let kind = wire
        .kind
        .clone()
        .ok_or(Occ2Error::Contract(ContractError::MissingRequired {
            field: "kind",
        }))?;
    let status =
        wire.status
            .clone()
            .ok_or(Occ2Error::Contract(ContractError::MissingRequired {
                field: "status",
            }))?;
    contiguous(&wire.findings, "F")?;
    contiguous(&wire.evidence, "E")?;
    contiguous(&wire.artifacts, "G")?;
    contiguous(&wire.dependencies, "D")?;

    let mut out = AgentCommMessage::new(kind, task_id, status);
    out.message_id = wire.message_id.take();
    out.parent_task_id = wire.parent_task_id.take();
    out.trace_id = wire.trace_id.take();
    out.sender_identity = wire.sender.take();
    out.recipient_identity = wire.recipient.take();
    out.sequence = wire.sequence;
    let evidence_count = wire.evidence.len();
    for (index, finding) in &wire.findings {
        let statement = finding
            .statement
            .clone()
            .ok_or_else(|| bad_value(&format!("F{index}"), 0, "finding statement missing"))?;
        let evidence = match finding.evidence.as_ref() {
            Some(references) => references
                .iter()
                .map(|number| {
                    if *number > evidence_count {
                        Err(bad_value(
                            &format!("F{index}"),
                            0,
                            &format!("references E{number}, outside 1..={evidence_count}"),
                        ))
                    } else {
                        Ok(format!("E{number}"))
                    }
                })
                .collect::<Result<Vec<_>, _>>()?,
            None => Vec::new(),
        };
        out.findings.push(Finding {
            id: format!("F{index}"),
            statement,
            confidence: finding.confidence,
            evidence,
        });
    }
    for (index, evidence) in &wire.evidence {
        let encoded = evidence
            .kind_locator
            .clone()
            .ok_or_else(|| bad_value(&format!("E{index}"), 0, "evidence kind:locator missing"))?;
        let Some((kind, locator)) = encoded.split_once(':') else {
            return Err(bad_value(
                &format!("E{index}"),
                0,
                "must be <kind>:<locator>",
            ));
        };
        out.evidence.push(EvidenceRef {
            id: format!("E{index}"),
            kind: kind.to_string(),
            locator: locator.to_string(),
            line_range: evidence.line_range,
            digest: evidence.digest.clone(),
            revision: evidence.revision.clone(),
            verified: evidence.verified,
        });
    }
    for (_, artifact) in wire.artifacts.iter() {
        out.artifacts.push(artifact.clone());
    }
    let has_verification = wire.verification_claim.is_some()
        || wire.verification_status.is_some()
        || wire.verification_method.is_some()
        || wire.verification_receipt.is_some();
    if has_verification {
        let claim = wire.verification_claim.take().ok_or_else(|| {
            Occ2Error::VerificationGroup(
                "verification status, method or receipt requires the claim".to_string(),
            )
        })?;
        let status = wire.verification_status.take().ok_or_else(|| {
            Occ2Error::VerificationGroup("a verification claim requires its status".to_string())
        })?;
        let method = wire.verification_method.take().ok_or_else(|| {
            Occ2Error::VerificationGroup("a verification claim requires its method".to_string())
        })?;
        out.verification = Some(Verification {
            claim,
            status,
            method,
            receipt: wire.verification_receipt.take(),
        });
    }
    for (_, dependency) in wire.dependencies.iter() {
        out.dependencies.push(dependency.clone());
    }
    out.next_actions = wire.next_actions;
    out.timestamp = wire.timestamp.take();
    out.expiry = wire.expiry.take();
    out.validate()?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::occ::contract::{
        evidence_kinds, kinds, statuses, verification, EvidenceRef as ContractEvidence,
    };
    use crate::core::occ::dialect::{
        codebook_sha256, subfields, Resolved, DEFAULT_DIALECT_ID, DEFAULT_DIALECT_VERSION,
    };
    use serde::Deserialize;
    use std::collections::BTreeSet;

    /// The communication-fidelity fixture, embedded at compile time so the
    /// unit tests stay offline, deterministic and free of runtime
    /// filesystem access.
    const SCENARIOS_JSON: &str = include_str!("../../../tests/fixtures/occ/scenarios.json");

    /// Pinned sha256 of the sym-v1 codebook. The canonical serialization
    /// is deterministic (struct field order, sorted maps), so any change
    /// to the symbol table or separators must change this hash; the pin
    /// makes silent drift a test failure.
    const SYM_V1_CODEBOOK_SHA256: &str =
        "1efce9992ee24ff779a0430d998b37704d484f9cbb90c2fb78feec063add1547";

    #[derive(Debug, Deserialize)]
    struct Scenario {
        id: String,
        #[allow(dead_code)]
        holdout: bool,
        payload: ScenarioPayload,
    }

    /// Mirrors the integration harness model: the typed payload minus
    /// `protocol`, which `AgentCommMessage::new` stamps.
    #[derive(Debug, Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ScenarioPayload {
        task_id: String,
        kind: String,
        status: String,
        #[serde(default)]
        message_id: Option<String>,
        #[serde(default)]
        parent_task_id: Option<String>,
        #[serde(default)]
        trace_id: Option<String>,
        #[serde(default)]
        sender_identity: Option<String>,
        #[serde(default)]
        recipient_identity: Option<String>,
        #[serde(default)]
        findings: Vec<ScenarioFinding>,
        #[serde(default)]
        evidence: Vec<ContractEvidence>,
        #[serde(default)]
        artifacts: Vec<String>,
        #[serde(default)]
        verification: Option<Verification>,
        #[serde(default)]
        dependencies: Vec<String>,
        #[serde(default)]
        next_actions: Vec<String>,
        #[serde(default)]
        sequence: Option<u64>,
        #[serde(default)]
        timestamp: Option<String>,
        #[serde(default)]
        expiry: Option<String>,
    }

    /// Fixture findings carry no id: the codec assigns positional ids.
    #[derive(Debug, Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ScenarioFinding {
        statement: String,
        #[serde(default)]
        confidence: Option<f32>,
        #[serde(default)]
        evidence: Vec<String>,
    }

    fn scenarios() -> Vec<Scenario> {
        serde_json::from_str(SCENARIOS_JSON).expect("parse scenarios.json")
    }

    fn build_message(payload: &ScenarioPayload) -> AgentCommMessage {
        let mut message = AgentCommMessage::new(
            payload.kind.clone(),
            payload.task_id.clone(),
            payload.status.clone(),
        );
        message.message_id = payload.message_id.clone();
        message.parent_task_id = payload.parent_task_id.clone();
        message.trace_id = payload.trace_id.clone();
        message.sender_identity = payload.sender_identity.clone();
        message.recipient_identity = payload.recipient_identity.clone();
        for (index, finding) in payload.findings.iter().enumerate() {
            message.findings.push(Finding {
                id: format!("F{}", index + 1),
                statement: finding.statement.clone(),
                confidence: finding.confidence,
                evidence: finding.evidence.clone(),
            });
        }
        message.evidence = payload.evidence.clone();
        message.artifacts = payload.artifacts.clone();
        message.verification = payload.verification.clone();
        message.dependencies = payload.dependencies.clone();
        message.next_actions = payload.next_actions.clone();
        message.sequence = payload.sequence;
        message.timestamp = payload.timestamp.clone();
        message.expiry = payload.expiry.clone();
        message
    }

    fn registry_with_sym_v1() -> DialectRegistry {
        DialectRegistry::with_sym_v1()
    }

    /// Builds a symbolic wire text with sym-v1 for hand-written fields.
    fn sym_wire(fields: &[(&str, &str)]) -> String {
        let card = DialectCard::sym_v1();
        let mut out = format!(
            "OCC/2 D={}@{} C={}",
            card.dialect_id,
            card.version,
            &card.sha256[..CODEBOOK_PREFIX_LEN]
        );
        for (key, value) in fields {
            out.push('\n');
            out.push_str(key);
            out.push('=');
            out.push_str(value);
        }
        out
    }

    fn floor_boundary(text: &str, byte_pos: usize) -> usize {
        let mut end = byte_pos.min(text.len());
        while end > 0 && !text.is_char_boundary(end) {
            end -= 1;
        }
        end
    }

    fn truncate_at(text: &str, byte_pos: usize) -> String {
        text[..floor_boundary(text, byte_pos)].to_string()
    }

    fn insert_at(text: &str, byte_idx: usize, insert: &str) -> String {
        let at = floor_boundary(text, byte_idx);
        format!("{}{}{}", &text[..at], insert, &text[at..])
    }

    // -------------------------------------------------------------------
    // Round-trip fidelity over the fixture
    // -------------------------------------------------------------------

    #[test]
    fn sym_v1_round_trips_every_scenario_shape() {
        let registry = registry_with_sym_v1();
        let card = DialectCard::sym_v1();
        let scenarios = scenarios();
        assert!(scenarios.len() >= 24);
        let mut kinds = BTreeSet::new();
        let mut holdout_ok = 0usize;
        let mut train_ok = 0usize;
        for scenario in &scenarios {
            let message = build_message(&scenario.payload);
            let wire = encode(&message, &card)
                .unwrap_or_else(|error| panic!("{} encode: {error}", scenario.id));
            let header_prefix =
                format!("OCC/2 D={DEFAULT_DIALECT_ID}@{DEFAULT_DIALECT_VERSION} C=");
            assert!(
                wire.starts_with(&header_prefix),
                "{} wire header",
                scenario.id
            );
            let header = &wire[..wire.find('\n').expect("header terminator")];
            assert_eq!(
                header.split(' ').count(),
                3,
                "{} header tokens",
                scenario.id
            );
            let decoded = decode(&wire, &registry)
                .unwrap_or_else(|error| panic!("{} decode: {error}", scenario.id));
            assert_eq!(decoded, message, "{} round trip", scenario.id);
            kinds.insert(message.kind.clone());
            if scenario.holdout {
                holdout_ok += 1;
            } else {
                train_ok += 1;
            }
        }
        // Every contract shape the fixture covers is round-tripped.
        for required in [
            kinds::TASK,
            kinds::PROGRESS,
            kinds::RESULT,
            kinds::EVIDENCE,
            kinds::REQUEST_DETAIL,
            kinds::ACK,
            kinds::CORRECTION,
            kinds::BLOCKED,
            kinds::CANCELLED,
        ] {
            assert!(
                kinds.contains(required),
                "no round trip for kind {required}"
            );
        }
        println!(
            "round trip: {} scenarios exact (train {}, holdout {})",
            scenarios.len(),
            train_ok,
            holdout_ok
        );
    }

    #[test]
    fn sym_v1_wire_never_exceeds_occ1_on_the_fixture() {
        let card = DialectCard::sym_v1();
        let mut occ1_total = 0usize;
        let mut occ2_total = 0usize;
        for scenario in scenarios() {
            let message = build_message(&scenario.payload);
            let occ2 = encode(&message, &card).expect("encodes");
            let occ1 = message
                .to_occ1()
                .expect("occ1")
                .encode()
                .expect("occ1 wire");
            assert!(
                occ2.len() <= occ1.len(),
                "{}: occ2 {} bytes > occ1 {} bytes",
                scenario.id,
                occ2.len(),
                occ1.len()
            );
            occ1_total += occ1.len();
            occ2_total += occ2.len();
        }
        assert!(occ2_total < occ1_total);
        println!(
            "sizes: occ1 {occ1_total} B, occ2 {occ2_total} B ({:.1}% smaller in aggregate)",
            100.0 * (occ1_total - occ2_total) as f64 / occ1_total as f64
        );
    }

    #[test]
    fn sym_v1_codebook_hash_is_pinned_and_stable() {
        let card = DialectCard::sym_v1();
        assert_eq!(
            card.sha256, SYM_V1_CODEBOOK_SHA256,
            "the sym-v1 codebook hash drifted; a changed codebook is a new dialect version"
        );
        assert_eq!(
            codebook_sha256(&card.grammar, &card.symbol_table),
            SYM_V1_CODEBOOK_SHA256
        );
        assert_eq!(DialectCard::sym_v1().sha256, card.sha256);
    }

    // -------------------------------------------------------------------
    // Dual payloads
    // -------------------------------------------------------------------

    #[test]
    fn dual_payload_round_trips_and_sections_must_agree() {
        let registry = registry_with_sym_v1();
        let card = DialectCard::sym_v1();
        for scenario in scenarios().into_iter().take(6) {
            let message = build_message(&scenario.payload);
            let dual = encode_dual(&message, &card)
                .unwrap_or_else(|error| panic!("{} encode_dual: {error}", scenario.id));
            assert!(dual.contains(&format!("\n{OCC2_FALLBACK_SENTINEL}\nOCC/1\n")));
            assert_eq!(
                decode(&dual, &registry).expect("dual decodes"),
                message,
                "{} dual round trip",
                scenario.id
            );

            // Tampering the OCC-1 section breaks the agreement check.
            let task_at = dual.rfind("\nTASK=").expect("occ1 TASK") + 6;
            let line_end = dual[task_at..]
                .find('\n')
                .map(|offset| task_at + offset)
                .unwrap_or(dual.len());
            let tampered = format!("{}zz-tampered{}", &dual[..task_at], &dual[line_end..]);
            let error = decode(&tampered, &registry).expect_err("sections must disagree");
            assert!(matches!(error, Occ2Error::DualMismatch));

            // Tampering the symbolic section breaks it too.
            let sym_task = dual.find("\nT=").expect("symbolic task") + 3;
            let line_end = dual[sym_task..]
                .find('\n')
                .map(|offset| sym_task + offset)
                .unwrap_or(dual.len());
            let tampered = format!("{}zz-tampered{}", &dual[..sym_task], &dual[line_end..]);
            let error = decode(&tampered, &registry).expect_err("sections must disagree");
            assert!(
                matches!(error, Occ2Error::DualMismatch),
                "unexpected error: {error}"
            );
        }
    }

    // -------------------------------------------------------------------
    // Unknown dialects, version mismatch, hash mismatch, fallback
    // -------------------------------------------------------------------

    #[test]
    fn unknown_dialect_and_version_mismatch_reject_and_fall_back() {
        let registry = registry_with_sym_v1();
        let card = DialectCard::sym_v1();
        let scenario = scenarios().into_iter().next().expect("a scenario");
        let message = build_message(&scenario.payload);
        let wire = encode(&message, &card).unwrap();
        let dual = encode_dual(&message, &card).unwrap();

        // Unknown dialect id: strict decode rejects.
        let unknown = wire.replacen("D=sym-v1@", "D=zzz@", 1);
        let error = decode(&unknown, &registry).expect_err("unknown dialect");
        assert!(matches!(error, Occ2Error::UnknownDialect { .. }));

        // Unknown version of a known dialect: strict decode rejects.
        let bumped = wire.replacen("D=sym-v1@1 C=", "D=sym-v1@9 C=", 1);
        let error = decode(&bumped, &registry).expect_err("unknown version");
        assert!(
            matches!(error, Occ2Error::UnknownDialect { dialect, version } if dialect == "sym-v1" && version == 9)
        );

        // The registry reports both as unknown, directing conservative
        // OCC-1 fallback.
        let prefix = &card.sha256[..CODEBOOK_PREFIX_LEN];
        assert_eq!(
            registry.resolve("zzz", 1, prefix),
            Resolved::Unknown {
                dialect: "zzz".to_string(),
                version: 1
            }
        );
        assert_eq!(
            registry.resolve(DEFAULT_DIALECT_ID, 9, prefix),
            Resolved::Unknown {
                dialect: DEFAULT_DIALECT_ID.to_string(),
                version: 9
            }
        );

        // Dual payloads still decode via the embedded OCC-1 section.
        assert_eq!(
            occ1_fallback(&dual.replacen("D=sym-v1@", "D=zzz@", 1))
                .expect("unknown dialect falls back"),
            message
        );
        assert_eq!(
            occ1_fallback(&dual.replacen("D=sym-v1@1 C=", "D=sym-v1@9 C=", 1))
                .expect("version mismatch falls back"),
            message
        );

        // Non-dual payloads have nothing to fall back to: rejected.
        assert!(occ1_fallback(&unknown).is_err());
        assert!(occ1_fallback(&bumped).is_err());
    }

    #[test]
    fn codebook_hash_mismatch_is_rejected() {
        let registry = registry_with_sym_v1();
        let card = DialectCard::sym_v1();
        let scenario = scenarios().into_iter().next().expect("a scenario");
        let message = build_message(&scenario.payload);
        let wire = encode(&message, &card).unwrap();
        let dual = encode_dual(&message, &card).unwrap();

        // Flip the first hex character of the advertised codebook hash.
        let c_pos = wire.find(" C=").expect("codebook token") + 3;
        let first = wire.as_bytes()[c_pos];
        let replacement = if first == b'f' { '0' } else { 'f' };
        let tampered = format!("{}{}{}", &wire[..c_pos], replacement, &wire[c_pos + 1..]);
        let error = decode(&tampered, &registry).expect_err("hash mismatch");
        assert!(
            matches!(error, Occ2Error::HashMismatch { ref dialect, .. } if dialect.as_str() == DEFAULT_DIALECT_ID),
            "unexpected error: {error}"
        );

        // A registered dialect with different symbols under the same
        // id@version produces exactly this failure.
        let mut impostor = DialectCard::sym_v1();
        impostor
            .symbol_table
            .slots
            .insert(slots::TRACE_ID.to_string(), "Y".to_string());
        impostor.sha256 = codebook_sha256(&impostor.grammar, &impostor.symbol_table);
        assert_ne!(impostor.sha256, card.sha256);
        let impostor_wire = encode(&message, &impostor).unwrap();
        let error = decode(&impostor_wire, &registry).expect_err("impostor hash");
        assert!(matches!(error, Occ2Error::HashMismatch { .. }));

        // The dual form still falls back via its OCC-1 section.
        let tampered_dual = format!("{}{}{}", &dual[..c_pos], replacement, &dual[c_pos + 1..]);
        assert_eq!(
            occ1_fallback(&tampered_dual).expect("hash mismatch falls back"),
            message
        );
    }

    // -------------------------------------------------------------------
    // Fault injection
    // -------------------------------------------------------------------

    #[test]
    fn corrupted_payloads_never_decode_to_the_original() {
        let registry = registry_with_sym_v1();
        let card = DialectCard::sym_v1();
        let sample: Vec<Scenario> = scenarios()
            .into_iter()
            .filter(|scenario| scenario.id[1..].parse::<u32>().is_ok_and(|n| n % 2 == 1))
            .collect();
        assert!(sample.len() >= 12, "fault sample too small");

        let mut rejected = 0usize;
        let mut survived_but_changed = 0usize;
        for scenario in &sample {
            let message = build_message(&scenario.payload);
            let wire = encode(&message, &card).unwrap();

            // Truncation at every 1/8 boundary: either rejected, or a
            // grammatical shorter message that differs from the original.
            for k in 1..8usize {
                let truncated = truncate_at(&wire, wire.len() * k / 8);
                assert!(truncated.len() < wire.len());
                match decode(&truncated, &registry) {
                    Err(_) => rejected += 1,
                    Ok(decoded) => {
                        assert_ne!(
                            decoded, message,
                            "{}/trunc{k}: corruption decoded to the original",
                            scenario.id
                        );
                        survived_but_changed += 1;
                    }
                }
            }

            // Flipped header byte: rejected as a bad header.
            let mut flipped_header = wire.clone();
            flipped_header.replace_range(2..3, "X");
            assert!(matches!(
                decode(&flipped_header, &registry),
                Err(Occ2Error::BadHeader(_))
            ));
            rejected += 1;

            // NUL anywhere: control characters are never legal values.
            let nul_inserted = insert_at(&wire, wire.len() / 2, "\0");
            assert!(decode(&nul_inserted, &registry).is_err());
            rejected += 1;

            // Interior blank line: when the insert lands on a line
            // boundary the blank line is reported; mid-line it splits a
            // value, which fails the key grammar instead. Either way the
            // corruption is rejected.
            let blank_inserted = insert_at(&wire, wire.len() / 3, "\n\n");
            let error = decode(&blank_inserted, &registry).expect_err("blank insert");
            assert!(
                matches!(
                    error,
                    Occ2Error::BlankLine { .. }
                        | Occ2Error::MissingSeparator { .. }
                        | Occ2Error::UnknownSymbol { .. }
                        | Occ2Error::InvalidSymbol { .. }
                ),
                "unexpected error: {error}"
            );
            rejected += 1;

            // Lowercase first key: symbols are uppercase.
            let newline = wire.find('\n').expect("header terminator");
            let mut lowered = wire.clone();
            let first_key_char = lowered[newline + 1..].chars().next().unwrap();
            let lowered_char = first_key_char.to_ascii_lowercase();
            let char_len = first_key_char.len_utf8();
            lowered.replace_range(
                newline + 1..newline + 1 + char_len,
                &lowered_char.to_string(),
            );
            assert_ne!(lowered, wire);
            let error = decode(&lowered, &registry).expect_err("lowercase symbol");
            assert!(
                matches!(
                    error,
                    Occ2Error::UnknownSymbol { .. } | Occ2Error::InvalidSymbol { .. }
                ),
                "unexpected error: {error}"
            );
            rejected += 1;

            // Removed separator on the first field line.
            let first_eq = wire[newline + 1..]
                .find('=')
                .expect("first field separator")
                + newline
                + 1;
            let mut no_separator = wire.clone();
            no_separator.replace_range(first_eq..first_eq + 1, "#");
            let error = decode(&no_separator, &registry).expect_err("separator removed");
            assert!(matches!(error, Occ2Error::MissingSeparator { .. }));
            rejected += 1;

            // Sentinel without a fallback section: rejected.
            let sentinel_only = format!("{wire}\n{OCC2_FALLBACK_SENTINEL}\n");
            let error = decode(&sentinel_only, &registry).expect_err("empty fallback section");
            assert!(matches!(error, Occ2Error::FallbackSection(_)));
            rejected += 1;

            // Empty and whitespace input.
            assert!(matches!(decode("", &registry), Err(Occ2Error::Empty)));
            assert!(decode("   ", &registry).is_err());
            rejected += 2;
        }
        println!(
            "fault injection: {} scenarios, {} corruptions rejected, {} grammatical but changed",
            sample.len(),
            rejected,
            survived_but_changed
        );
    }

    // -------------------------------------------------------------------
    // Semantic faults (header-valid, semantically broken)
    // -------------------------------------------------------------------

    #[test]
    fn indexed_gaps_duplicates_and_range_faults_are_rejected() {
        let registry = registry_with_sym_v1();
        let base = [("T", "t1"), ("K", "RESULT"), ("S", "done")];
        let cases: Vec<(&str, Vec<(&str, &str)>, fn(&Occ2Error) -> bool)> = vec![
            (
                "finding gap F2 without F1",
                vec![("F2", "claim")],
                |error| matches!(error, Occ2Error::IndexGap { prefix, index, highest } if prefix == "F" && *index == 1 && *highest == 2),
            ),
            (
                "finding gap F3 after F1",
                vec![("F1", "one"), ("F3", "three")],
                |error| {
                    matches!(
                        error,
                        Occ2Error::IndexGap {
                            index: 2,
                            highest: 3,
                            ..
                        }
                    )
                },
            ),
            (
                "evidence gap E2 without E1",
                vec![("E2", "src:a.rs")],
                |error| matches!(error, Occ2Error::IndexGap { prefix, .. } if prefix == "E"),
            ),
            (
                "artifact gap G2 without G1",
                vec![("G2", "a.txt")],
                |error| matches!(error, Occ2Error::IndexGap { prefix, .. } if prefix == "G"),
            ),
            (
                "dependency gap D2 without D1",
                vec![("D2", "t9")],
                |error| matches!(error, Occ2Error::IndexGap { prefix, .. } if prefix == "D"),
            ),
            (
                "evidence reference out of range",
                vec![("E1", "src:a.rs"), ("F1", "claim"), ("F1e", "9")],
                |error| matches!(error, Occ2Error::BadValue { .. }),
            ),
            ("finding index zero", vec![("F0", "claim")], |error| {
                matches!(error, Occ2Error::InvalidSymbol { .. })
            }),
            (
                "duplicate task field",
                vec![("T", "t2")],
                |error| matches!(error, Occ2Error::DuplicateField { key } if key == "T"),
            ),
            (
                "duplicate finding field",
                vec![("F1", "one"), ("F1", "two")],
                |error| matches!(error, Occ2Error::DuplicateField { key } if key == "F1"),
            ),
            (
                "duplicate finding confidence",
                vec![("F1", "one"), ("F1c", "0.5"), ("F1c", "0.9")],
                |error| matches!(error, Occ2Error::DuplicateField { key } if key == "F1c"),
            ),
            (
                "verification status without claim",
                vec![("Vs", "pass")],
                |error| matches!(error, Occ2Error::VerificationGroup(_)),
            ),
            ("claim without status", vec![("V", "claim")], |error| {
                matches!(error, Occ2Error::VerificationGroup(_))
            }),
            (
                "claim without method",
                vec![("V", "claim"), ("Vs", "pass")],
                |error| matches!(error, Occ2Error::VerificationGroup(_)),
            ),
            ("bad sequence", vec![("Q", "abc")], |error| {
                matches!(error, Occ2Error::BadValue { .. })
            }),
            (
                "evidence without kind:locator",
                vec![("E1", "nocolon")],
                |error| matches!(error, Occ2Error::BadValue { .. }),
            ),
            (
                "bad line range",
                vec![("E1", "src:a.rs"), ("E1l", "x-y")],
                |error| matches!(error, Occ2Error::BadValue { .. }),
            ),
            (
                "bad evidence verdict",
                vec![("E1", "src:a.rs"), ("E1v", "pass")],
                |error| matches!(error, Occ2Error::BadValue { .. }),
            ),
            (
                "empty evidence list value",
                vec![("E1", "src:a.rs"), ("F1", "claim"), ("F1e", "")],
                |error| matches!(error, Occ2Error::BadValue { .. }),
            ),
            (
                "unknown symbol",
                vec![("Z", "1")],
                |error| matches!(error, Occ2Error::UnknownSymbol { key, .. } if key == "Z"),
            ),
            (
                "unknown subfield marker",
                vec![("F1", "claim"), ("F1x", "1")],
                |error| matches!(error, Occ2Error::InvalidSymbol { key, .. } if key == "F1x"),
            ),
            (
                "indexed repeatable slot",
                vec![("N1", "action")],
                |error| matches!(error, Occ2Error::InvalidSymbol { key, .. } if key == "N1"),
            ),
            (
                "indexed scalar slot",
                vec![("T1", "t2")],
                |error| matches!(error, Occ2Error::InvalidSymbol { key, .. } if key == "T1"),
            ),
        ];
        for (label, extra, predicate) in cases {
            let mut fields: Vec<(&str, &str)> = base.to_vec();
            fields.extend(extra);
            let wire = sym_wire(&fields);
            let error = decode(&wire, &registry)
                .err()
                .unwrap_or_else(|| panic!("{label}: expected rejection, got a message"));
            assert!(predicate(&error), "{label}: unexpected error {error}");
        }

        // Missing required core fields.
        for (label, fields) in [
            ("missing task", vec![("K", "RESULT"), ("S", "done")]),
            ("missing kind", vec![("T", "t1"), ("S", "done")]),
            ("missing status", vec![("T", "t1"), ("K", "RESULT")]),
        ] {
            let wire = sym_wire(&fields);
            let error = decode(&wire, &registry)
                .err()
                .unwrap_or_else(|| panic!("{label}: expected rejection"));
            assert!(
                matches!(
                    error,
                    Occ2Error::Contract(ContractError::MissingRequired { .. })
                ),
                "{label}: {error}"
            );
        }

        // An EVIDENCE message without evidence refs, and an unknown
        // evidence kind, are caught by the contract validation.
        let wire = sym_wire(&[("T", "t1"), ("K", "EVIDENCE"), ("S", "done")]);
        assert!(decode(&wire, &registry).is_err());
        let wire = sym_wire(&[
            ("T", "t1"),
            ("K", "RESULT"),
            ("S", "done"),
            ("E1", "weird:a.rs"),
        ]);
        assert!(decode(&wire, &registry).is_err());
    }

    #[test]
    fn decode_rejects_more_than_the_field_bound() {
        let registry = registry_with_sym_v1();
        let mut fields: Vec<(&str, &str)> = vec![("T", "t1"), ("K", "RESULT"), ("S", "done")];
        for i in 0..200 {
            let _ = i;
            fields.push(("N", "action"));
        }
        let wire = sym_wire(&fields);
        assert!(matches!(
            decode(&wire, &registry),
            Err(Occ2Error::TooManyFields { .. })
        ));
    }

    // -------------------------------------------------------------------
    // OCC-1 no-regression and fallback behavior
    // -------------------------------------------------------------------

    #[test]
    fn occ1_outputs_still_decode_via_occ1() {
        for scenario in scenarios() {
            let message = build_message(&scenario.payload);
            let occ1 = message
                .to_occ1()
                .unwrap_or_else(|error| panic!("{} to_occ1: {error}", scenario.id));
            let wire = occ1
                .encode()
                .unwrap_or_else(|error| panic!("{} encode: {error}", scenario.id));
            assert!(wire.starts_with("OCC/1\n"), "{} header", scenario.id);
            let decoded = Occ1Message::decode(&wire)
                .unwrap_or_else(|error| panic!("{} decode: {error}", scenario.id));
            assert_eq!(decoded, occ1, "{} codec round trip", scenario.id);
            let back = AgentCommMessage::from_occ1(&decoded)
                .unwrap_or_else(|error| panic!("{} from_occ1: {error}", scenario.id));
            assert_eq!(back, message, "{} contract round trip", scenario.id);
        }
    }

    #[test]
    fn occ1_fallback_decodes_plain_occ1_and_dual_payloads() {
        let card = DialectCard::sym_v1();
        let scenarios = scenarios();
        let scenario = scenarios.first().expect("a scenario");
        let message = build_message(&scenario.payload);

        // Plain OCC-1 text: the whole-text parse path.
        let occ1_text = message.to_occ1().unwrap().encode().unwrap();
        assert_eq!(occ1_fallback(&occ1_text).unwrap(), message);

        // Dual payload: the embedded-section path.
        let dual = encode_dual(&message, &card).unwrap();
        assert_eq!(occ1_fallback(&dual).unwrap(), message);

        // A corrupted OCC-2 payload without a fallback section has no
        // OCC-1 form to fall back on: rejected, never guessed.
        let wire = encode(&message, &card).unwrap();
        let mut flipped = wire.clone();
        flipped.replace_range(2..3, "X");
        assert!(occ1_fallback(&flipped).is_err());
        assert!(occ1_fallback("").is_err());
        assert!(occ1_fallback(
            "OCC/2 D=sym-v1@1 C=1efce9992ee2\nT=t\nK=RESULT\nS=done\n#FALLBACK-OCC1\n"
        )
        .is_err());
    }

    // -------------------------------------------------------------------
    // Bounds and edge values
    // -------------------------------------------------------------------

    #[test]
    fn bounds_are_enforced_on_encode() {
        let card = DialectCard::sym_v1();
        let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
        message.findings.push(Finding {
            id: "F1".into(),
            statement: "a".repeat(5000),
            confidence: None,
            evidence: Vec::new(),
        });
        let error = encode(&message, &card).expect_err("oversized value");
        assert!(matches!(error, Occ2Error::ValueTooLarge { .. }));

        let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
        for _ in 0..200 {
            message.next_actions.push("act".into());
        }
        let error = encode(&message, &card).expect_err("too many fields");
        assert!(matches!(error, Occ2Error::TooManyFields { .. }));

        // Control characters in values are rejected.
        let message = AgentCommMessage::new(kinds::RESULT, "t\u{0001}", statuses::DONE);
        let error = encode(&message, &card).expect_err("control character");
        assert!(matches!(
            error,
            Occ2Error::Contract(ContractError::InvalidTaskId(_))
        ));
    }

    #[test]
    fn edge_values_round_trip() {
        let registry = registry_with_sym_v1();
        let card = DialectCard::sym_v1();

        // Minimal message.
        let minimal = AgentCommMessage::new(kinds::TASK, "t1", statuses::PENDING);
        assert_eq!(
            decode(&encode(&minimal, &card).unwrap(), &registry).unwrap(),
            minimal
        );

        // Evidence-only message.
        let mut evidence_only = AgentCommMessage::new(kinds::EVIDENCE, "t1", statuses::DONE);
        evidence_only.evidence.push(EvidenceRef {
            id: "E1".into(),
            kind: evidence_kinds::COMMIT.into(),
            locator: "159ce68cc".into(),
            line_range: None,
            digest: None,
            revision: None,
            verified: None,
        });
        assert_eq!(
            decode(&encode(&evidence_only, &card).unwrap(), &registry).unwrap(),
            evidence_only
        );

        // Operator characters, separators and unicode inside values;
        // duplicate evidence references are preserved verbatim.
        let mut message = AgentCommMessage::new(kinds::RESULT, "t=e=ual", statuses::DONE);
        message.findings.push(Finding {
            id: "F1".into(),
            statement: "a = b, c; 配転述锁順 😀".into(),
            confidence: Some(0.5),
            evidence: vec!["E1".into(), "E1".into()],
        });
        message.evidence.push(EvidenceRef {
            id: "E1".into(),
            kind: evidence_kinds::SOURCE.into(),
            locator: "src/ä=ö.rs".into(),
            line_range: Some((1, 2)),
            digest: Some("d=i,g".into()),
            revision: Some("r=1".into()),
            verified: Some(false),
        });
        message.artifacts.push(String::new());
        message.next_actions.push("act with = and ,".into());
        message.verification = Some(Verification {
            claim: "claim = with equals".into(),
            status: verification::UNKNOWN.into(),
            method: "manual, review".into(),
            receipt: None,
        });
        let wire = encode(&message, &card).unwrap();
        assert_eq!(decode(&wire, &registry).unwrap(), message);

        // All three verification statuses round trip.
        for status in [
            verification::PASS,
            verification::FAIL,
            verification::UNKNOWN,
        ] {
            let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
            message.verification = Some(Verification {
                claim: "claim".into(),
                status: status.into(),
                method: "cargo_test".into(),
                receipt: Some("reports/r.txt".into()),
            });
            assert_eq!(
                decode(&encode(&message, &card).unwrap(), &registry).unwrap(),
                message,
                "verification status {status}"
            );
        }

        // A finding referencing a non-positional evidence id is rejected
        // at encode time, matching the OCC-1 wire's own decode limits.
        let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
        message.evidence.push(EvidenceRef {
            id: "ev-alpha".into(),
            kind: evidence_kinds::SOURCE.into(),
            locator: "src/a.rs".into(),
            line_range: None,
            digest: None,
            revision: None,
            verified: None,
        });
        message.findings.push(Finding {
            id: "F1".into(),
            statement: "claim".into(),
            confidence: None,
            evidence: vec!["ev-alpha".into()],
        });
        assert!(matches!(
            encode(&message, &card),
            Err(Occ2Error::BadValue { .. })
        ));
    }

    // -------------------------------------------------------------------
    // Dialect-generic encoding
    // -------------------------------------------------------------------

    #[test]
    fn custom_dialect_round_trips_through_a_fresh_registry() {
        let mut card = DialectCard::sym_v1();
        card.dialect_id = "alt-9".to_string();
        card.version = 3;
        card.grammar.operator = ' ';
        card.grammar.list_separator = '+';
        card.symbol_table
            .slots
            .insert(slots::TASK_ID.to_string(), "Z".to_string());
        card.symbol_table
            .subfields
            .insert(subfields::FINDING_CONFIDENCE.to_string(), "q".to_string());
        card.sha256 = codebook_sha256(&card.grammar, &card.symbol_table);
        card.validate().unwrap();

        let mut registry = DialectRegistry::new();
        registry.insert(card.clone()).unwrap();

        let scenario = scenarios().into_iter().next().expect("a scenario");
        let message = build_message(&scenario.payload);
        let wire = encode(&message, &card).expect("encodes with the custom dialect");
        assert!(wire.starts_with("OCC/2 D=alt-9@3 C="));
        // Space operator and the remapped task symbol are on the wire.
        assert!(wire.contains("\nZ occ-7"), "task line: {wire:?}");
        let decoded = decode(&wire, &registry).expect("decodes");
        assert_eq!(decoded, message);

        // The default registry does not know alt-9.
        assert!(matches!(
            decode(&wire, &registry_with_sym_v1()),
            Err(Occ2Error::UnknownDialect { .. })
        ));

        // The standard dialect decodes nothing of it: different codebook
        // hash, different symbols.
        let sym_wire = encode(&message, &DialectCard::sym_v1()).unwrap();
        assert!(matches!(
            decode(&sym_wire, &registry),
            Err(Occ2Error::UnknownDialect { .. })
        ));
    }
}
