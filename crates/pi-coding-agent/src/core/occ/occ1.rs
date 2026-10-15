//! OCC-1: Optimus Compact Communication level 1 - compact structured English.
//!
//! OCC-1 is a deterministic, line-oriented `KEY=VALUE` grammar for
//! agent-to-agent messages. It is an encoding of the typed
//! [`crate::core::occ::contract`] payload, not a separate agent lifecycle: the
//! daemon transport stays authoritative, and sender identity is established by
//! the daemon, never trusted from message text.
//!
//! Grammar (deterministic; malformed input is rejected, never guessed):
//! - Line 1 is exactly `OCC/1`.
//! - Every following line is `KEY=VALUE` with `KEY = [A-Z][A-Z0-9_]*`.
//! - Repeated keys are allowed and order-preserving (lists, e.g. `NEXT=...`).
//! - Values are UTF-8 and contain neither newlines nor C0/C1 control
//!   characters.
//! - Decode tolerates CRLF line endings and at most one trailing newline;
//!   interior blank lines are errors.
//! - Bounds are enforced: [`OCC1_MAX_FIELDS`] fields, [`OCC1_MAX_KEY_BYTES`]
//!   per key, [`OCC1_MAX_VALUE_BYTES`] per value, [`OCC1_MAX_MESSAGE_BYTES`]
//!   overall.
//!
//! The codec is deliberately domain-free: it knows the grammar, not the field
//! semantics. Required-field rules per message kind live in [`Occ1Dialect`],
//! and the typed mapping lives in [`crate::core::occ::contract`].

use thiserror::Error;

/// Exact required first line of every OCC-1 message.
pub const OCC1_HEADER: &str = "OCC/1";
/// Maximum number of `KEY=VALUE` fields in one message.
pub const OCC1_MAX_FIELDS: usize = 128;
/// Maximum length of a field key in bytes.
pub const OCC1_MAX_KEY_BYTES: usize = 32;
/// Maximum length of a field value in bytes.
pub const OCC1_MAX_VALUE_BYTES: usize = 4096;
/// Maximum encoded size of one message in bytes.
pub const OCC1_MAX_MESSAGE_BYTES: usize = 64 * 1024;

/// Message kinds shared with the typed contract.
pub const KIND_TASK: &str = "TASK";
/// Message kind: progress update on an accepted task.
pub const KIND_PROGRESS: &str = "PROGRESS";
/// Message kind: final or intermediate result.
pub const KIND_RESULT: &str = "RESULT";
/// Message kind: evidence-only payload.
pub const KIND_EVIDENCE: &str = "EVIDENCE";
/// Message kind: request for additional detail.
pub const KIND_REQUEST_DETAIL: &str = "REQUEST_DETAIL";
/// Message kind: acknowledgement of receipt.
pub const KIND_ACK: &str = "ACK";
/// Message kind: correction of a previously reported claim.
pub const KIND_CORRECTION: &str = "CORRECTION";
/// Message kind: blocked report.
pub const KIND_BLOCKED: &str = "BLOCKED";
/// Message kind: cancellation notice.
pub const KIND_CANCELLED: &str = "CANCELLED";

/// Task statuses shared with the typed contract.
pub const STATUS_PENDING: &str = "pending";
/// Task status: work started, not finished.
pub const STATUS_IN_PROGRESS: &str = "in_progress";
/// Task status: finished successfully.
pub const STATUS_DONE: &str = "done";
/// Task status: finished partially; scope remains.
pub const STATUS_PARTIAL: &str = "partial";
/// Task status: cannot proceed (with reason elsewhere in the message).
pub const STATUS_BLOCKED: &str = "blocked";
/// Task status: finished unsuccessfully.
pub const STATUS_FAILED: &str = "failed";
/// Task status: cancelled before completion.
pub const STATUS_CANCELLED: &str = "cancelled";

/// Returns true when `kind` is one of the known message kinds.
pub fn is_known_kind(kind: &str) -> bool {
    matches!(
        kind,
        KIND_TASK
            | KIND_PROGRESS
            | KIND_RESULT
            | KIND_EVIDENCE
            | KIND_REQUEST_DETAIL
            | KIND_ACK
            | KIND_CORRECTION
            | KIND_BLOCKED
            | KIND_CANCELLED
    )
}

/// Returns true when `status` is one of the known task statuses.
pub fn is_known_status(status: &str) -> bool {
    matches!(
        status,
        STATUS_PENDING
            | STATUS_IN_PROGRESS
            | STATUS_DONE
            | STATUS_PARTIAL
            | STATUS_BLOCKED
            | STATUS_FAILED
            | STATUS_CANCELLED
    )
}

/// Codec-level parse/encode failure. Line numbers are 1-based; line 1 is the
/// header.
#[derive(Debug, Error)]
pub enum Occ1Error {
    /// The input was empty or contained only whitespace.
    #[error("OCC-1 input is empty")]
    Empty,
    /// The first line was not the exact `OCC/1` header.
    #[error("first line {0:?} is not the exact header {1:?}")]
    BadHeader(String, &'static str),
    /// A non-header line lacked the `=` separator.
    #[error("line {line}: expected KEY=VALUE, no '=' found")]
    MissingSeparator { line: usize },
    /// A key failed the `[A-Z][A-Z0-9_]*` grammar.
    #[error("line {line}: key {key:?} is not [A-Z][A-Z0-9_]*")]
    InvalidKey { line: usize, key: String },
    /// A value contained a forbidden control character.
    #[error("line {line}: value of {key:?} contains a control character")]
    ControlChar { line: usize, key: String },
    /// An interior line was empty or whitespace-only.
    #[error("line {line}: blank lines are not allowed")]
    BlankLine { line: usize },
    /// The message exceeded the byte bound.
    #[error("message exceeds the {limit} byte bound")]
    TooLarge { limit: usize },
    /// The message exceeded the field-count bound.
    #[error("message exceeds the {limit} field bound")]
    TooManyFields { limit: usize },
    /// A value exceeded its byte bound.
    #[error("value of {key:?} exceeds {limit} bytes")]
    ValueTooLarge { key: String, limit: usize },
    /// A key exceeded its byte bound.
    #[error("key {key:?} exceeds {limit} bytes")]
    KeyTooLarge { key: String, limit: usize },
}

/// An OCC-1 message: an ordered multiset of `KEY=VALUE` fields.
///
/// Ordering is preserved exactly as inserted; repeated keys are legal and
/// `get`/`get_all` return first/all occurrences respectively.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Occ1Message {
    fields: Vec<(String, String)>,
}

impl Occ1Message {
    /// Creates an empty message.
    pub fn new() -> Self {
        Self { fields: Vec::new() }
    }

    /// Builder-style append of a field.
    pub fn with(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.push(key, value);
        self
    }

    /// Appends a field. Duplicate keys are allowed; order is preserved.
    pub fn push(&mut self, key: impl Into<String>, value: impl Into<String>) {
        self.fields.push((key.into(), value.into()));
    }

    /// Replaces every existing occurrence of `key` with a single field.
    pub fn set(&mut self, key: impl Into<String>, value: impl Into<String>) {
        let key = key.into();
        self.fields.retain(|(k, _)| *k != key);
        self.fields.push((key, value.into()));
    }

    /// Returns the first value stored under `key`.
    pub fn get(&self, key: &str) -> Option<&str> {
        self.fields
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }

    /// Returns every value stored under `key`, in insertion order.
    pub fn get_all(&self, key: &str) -> Vec<&str> {
        self.fields
            .iter()
            .filter(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
            .collect()
    }

    /// Returns true when at least one field with `key` exists.
    pub fn has(&self, key: &str) -> bool {
        self.fields.iter().any(|(k, _)| k == key)
    }

    /// Removes every field with `key` and returns how many were removed.
    pub fn remove(&mut self, key: &str) -> usize {
        let before = self.fields.len();
        self.fields.retain(|(k, _)| k != key);
        before - self.fields.len()
    }

    /// Iterates the fields in insertion order.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &str)> {
        self.fields.iter().map(|(k, v)| (k.as_str(), v.as_str()))
    }

    /// Number of fields (counting repeated keys separately).
    pub fn len(&self) -> usize {
        self.fields.len()
    }

    /// True when the message has no fields (header only).
    pub fn is_empty(&self) -> bool {
        self.fields.is_empty()
    }

    /// Encodes to the wire grammar. Returns an error when any field violates
    /// the key grammar, the value rules, or the size bounds; the output of
    /// this function always parses back to an equal message.
    pub fn encode(&self) -> Result<String, Occ1Error> {
        if self.fields.len() > OCC1_MAX_FIELDS {
            return Err(Occ1Error::TooManyFields {
                limit: OCC1_MAX_FIELDS,
            });
        }
        let mut out = String::with_capacity(OCC1_HEADER.len() + 1);
        out.push_str(OCC1_HEADER);
        for (key, value) in &self.fields {
            validate_key(key).map_err(|e| match e {
                Occ1Error::InvalidKey { key, .. } => Occ1Error::InvalidKey { line: 0, key },
                Occ1Error::KeyTooLarge { key, limit } => Occ1Error::KeyTooLarge { key, limit },
                other => other,
            })?;
            if value.len() > OCC1_MAX_VALUE_BYTES {
                return Err(Occ1Error::ValueTooLarge {
                    key: key.clone(),
                    limit: OCC1_MAX_VALUE_BYTES,
                });
            }
            if value.chars().any(|c| c.is_control()) {
                return Err(Occ1Error::ControlChar {
                    line: 0,
                    key: key.clone(),
                });
            }
            out.push('\n');
            out.push_str(key);
            out.push('=');
            out.push_str(value);
        }
        if out.len() > OCC1_MAX_MESSAGE_BYTES {
            return Err(Occ1Error::TooLarge {
                limit: OCC1_MAX_MESSAGE_BYTES,
            });
        }
        Ok(out)
    }

    /// Parses the wire grammar. Malformed input is rejected with a precise
    /// error; nothing is guessed or repaired.
    pub fn decode(text: &str) -> Result<Self, Occ1Error> {
        if text.is_empty() {
            return Err(Occ1Error::Empty);
        }
        if text.len() > OCC1_MAX_MESSAGE_BYTES {
            return Err(Occ1Error::TooLarge {
                limit: OCC1_MAX_MESSAGE_BYTES,
            });
        }
        let trimmed = text.strip_suffix('\n').unwrap_or(text);
        let mut lines = trimmed.split('\n');
        let header = lines.next().ok_or(Occ1Error::Empty)?;
        let header = header.strip_suffix('\r').unwrap_or(header);
        if header != OCC1_HEADER {
            return Err(Occ1Error::BadHeader(header.to_string(), OCC1_HEADER));
        }
        let mut message = Occ1Message::new();
        for (offset, raw_line) in lines.enumerate() {
            let line_no = offset + 2; // header is line 1
            let line = raw_line.strip_suffix('\r').unwrap_or(raw_line);
            if line.is_empty() {
                return Err(Occ1Error::BlankLine { line: line_no });
            }
            let Some((key, value)) = line.split_once('=') else {
                return Err(Occ1Error::MissingSeparator { line: line_no });
            };
            if key.is_empty() {
                return Err(Occ1Error::InvalidKey {
                    line: line_no,
                    key: key.to_string(),
                });
            }
            validate_key(key).map_err(|e| match e {
                Occ1Error::InvalidKey { key, .. } => Occ1Error::InvalidKey {
                    line: line_no,
                    key,
                },
                other => other,
            })?;
            if value.chars().any(|c| c.is_control()) {
                return Err(Occ1Error::ControlChar {
                    line: line_no,
                    key: key.to_string(),
                });
            }
            if value.len() > OCC1_MAX_VALUE_BYTES {
                return Err(Occ1Error::ValueTooLarge {
                    key: key.to_string(),
                    limit: OCC1_MAX_VALUE_BYTES,
                });
            }
            if message.fields.len() >= OCC1_MAX_FIELDS {
                return Err(Occ1Error::TooManyFields {
                    limit: OCC1_MAX_FIELDS,
                });
            }
            message.fields.push((key.to_string(), value.to_string()));
        }
        Ok(message)
    }

    /// Returns the subset of `required` keys that are absent from the message.
    pub fn required_missing(&self, required: &[&str]) -> Vec<String> {
        required
            .iter()
            .filter(|key| !self.has(key))
            .map(|key| (*key).to_string())
            .collect()
    }

    /// Renders a readable English fallback for consumers that do not
    /// understand OCC-1. Deterministic; never fails.
    pub fn render_readable(&self) -> String {
        let kind = self.get("KIND").unwrap_or("UNKNOWN");
        let task = self.get("TASK").unwrap_or("-");
        let status = self.get("STATUS").unwrap_or("-");
        let mut out = format!("OCC-1 message kind={kind} task={task} status={status}");
        for (key, value) in &self.fields {
            if key == "KIND" || key == "TASK" || key == "STATUS" {
                continue;
            }
            out.push_str("\n  ");
            out.push_str(key);
            out.push_str(": ");
            out.push_str(value);
        }
        out
    }
}

fn validate_key(key: &str) -> Result<(), Occ1Error> {
    if key.is_empty() {
        return Err(Occ1Error::InvalidKey {
            line: 0,
            key: key.to_string(),
        });
    }
    if key.len() > OCC1_MAX_KEY_BYTES {
        return Err(Occ1Error::KeyTooLarge {
            key: key.to_string(),
            limit: OCC1_MAX_KEY_BYTES,
        });
    }
    let mut bytes = key.bytes();
    let first = bytes.next().expect("non-empty");
    if !first.is_ascii_uppercase() {
        return Err(Occ1Error::InvalidKey {
            line: 0,
            key: key.to_string(),
        });
    }
    if !bytes.all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_') {
        return Err(Occ1Error::InvalidKey {
            line: 0,
            key: key.to_string(),
        });
    }
    Ok(())
}

/// Per-kind required-field rules for OCC-1 messages.
///
/// `standard()` encodes the shared minimum: every message must identify its
/// task and kind; kinds that assert an outcome must carry the fields a
/// consumer needs to interpret that outcome.
#[derive(Debug, Clone)]
pub struct Occ1Dialect {
    required_by_kind: Vec<(&'static str, Vec<&'static str>)>,
}

impl Default for Occ1Dialect {
    fn default() -> Self {
        Self::standard()
    }
}

impl Occ1Dialect {
    /// The standard required-field table.
    pub fn standard() -> Self {
        let table: Vec<(&'static str, Vec<&'static str>)> = vec![
            (KIND_TASK, vec!["TASK", "KIND", "GOAL"]),
            (KIND_PROGRESS, vec!["TASK", "KIND", "STATUS"]),
            (KIND_RESULT, vec!["TASK", "KIND", "STATUS"]),
            (KIND_EVIDENCE, vec!["TASK", "KIND", "E1"]),
            (KIND_REQUEST_DETAIL, vec!["TASK", "KIND", "WANT"]),
            (KIND_ACK, vec!["TASK", "KIND", "REF"]),
            (KIND_CORRECTION, vec!["TASK", "KIND", "REF"]),
            (KIND_BLOCKED, vec!["TASK", "KIND", "WHY"]),
            (KIND_CANCELLED, vec!["TASK", "KIND"]),
        ];
        Self {
            required_by_kind: table,
        }
    }

    /// Required keys for a kind, or None when the kind is unknown.
    pub fn required_for(&self, kind: &str) -> Option<&[&'static str]> {
        self.required_by_kind
            .iter()
            .find(|(k, _)| *k == kind)
            .map(|(_, keys)| keys.as_slice())
    }

    /// Validates a message against the dialect: known kind, required fields
    /// present, `SEQ` (when present) a non-negative integer, and `STATUS`
    /// (when present) a known task status.
    pub fn validate(&self, message: &Occ1Message) -> Result<(), Occ1ValidationError> {
        let Some(kind) = message.get("KIND") else {
            return Err(Occ1ValidationError::MissingKind);
        };
        let Some(required) = self.required_for(kind) else {
            return Err(Occ1ValidationError::UnknownKind(kind.to_string()));
        };
        for field in required {
            if !message.has(field) {
                return Err(Occ1ValidationError::MissingRequired {
                    kind: kind.to_string(),
                    field: (*field).to_string(),
                });
            }
        }
        if let Some(seq) = message.get("SEQ") {
            if seq.parse::<u64>().is_err() {
                return Err(Occ1ValidationError::InvalidSequence(seq.to_string()));
            }
        }
        if let Some(status) = message.get("STATUS") {
            if !is_known_status(status) {
                return Err(Occ1ValidationError::UnknownStatus(status.to_string()));
            }
        }
        Ok(())
    }
}

/// Dialect-level validation failure.
#[derive(Debug, Error)]
pub enum Occ1ValidationError {
    /// The message carried no `KIND` field.
    #[error("OCC-1 message is missing the KIND field")]
    MissingKind,
    /// The message carried an unknown `KIND`.
    #[error("unknown OCC-1 KIND {0:?}")]
    UnknownKind(String),
    /// A field required for the kind was absent.
    #[error("KIND {kind:?} requires field {field:?}")]
    MissingRequired { kind: String, field: String },
    /// `SEQ` was present but not an unsigned integer.
    #[error("SEQ {0:?} is not an unsigned integer")]
    InvalidSequence(String),
    /// `STATUS` was present but unknown.
    #[error("unknown STATUS {0:?}")]
    UnknownStatus(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_preserves_fields_and_order() {
        let message = Occ1Message::new()
            .with("TASK", "t42")
            .with("KIND", "RESULT")
            .with("STATUS", "done")
            .with("F1", "race:src/session.rs:218-235")
            .with("E1", "src:src/session.rs")
            .with("VERIFY", "session_race:fail")
            .with("NEXT", "inspect_lock")
            .with("NEXT", "regression_test");
        let encoded = message.encode().expect("valid message");
        let decoded = Occ1Message::decode(&encoded).expect("parses");
        assert_eq!(decoded, message);
        assert_eq!(decoded.get_all("NEXT"), vec!["inspect_lock", "regression_test"]);
    }

    #[test]
    fn rejects_missing_header() {
        let err = Occ1Message::decode("TASK=t42\nKIND=RESULT").expect_err("header required");
        assert!(matches!(err, Occ1Error::BadHeader(_, _)));
    }

    #[test]
    fn rejects_blank_interior_line() {
        let err = Occ1Message::decode("OCC/1\nTASK=t42\n\nKIND=RESULT").expect_err("blank line");
        assert!(matches!(err, Occ1Error::BlankLine { line: 3 }));
    }

    #[test]
    fn tolerates_crlf_and_single_trailing_newline() {
        let text = "OCC/1\r\nTASK=t42\r\nKIND=RESULT\r\n";
        let decoded = Occ1Message::decode(text).expect("CRLF tolerated");
        assert_eq!(decoded.get("TASK"), Some("t42"));
    }

    #[test]
    fn rejects_lowercase_key() {
        let err = Occ1Message::decode("OCC/1\ntask=t42").expect_err("uppercase keys only");
        assert!(matches!(err, Occ1Error::InvalidKey { line: 2, .. }));
    }

    #[test]
    fn dialect_requires_kind_specific_fields() {
        let message = Occ1Message::new().with("TASK", "t42").with("KIND", "RESULT");
        let err = Occ1Dialect::standard()
            .validate(&message)
            .expect_err("RESULT requires STATUS");
        assert!(matches!(
            err,
            Occ1ValidationError::MissingRequired { field, .. } if field == "STATUS"
        ));
    }
}
