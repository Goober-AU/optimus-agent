//! Exhaustive, adversarial unit tests for the OCC-1 codec (`occ1`) and the
//! `optimus.agent-comm.v1` typed contract (`contract`).
//!
//! Everything here is offline and deterministic. The property-style loops
//! use a fixed-seed xorshift64 generator so failures reproduce exactly.

use super::contract::*;
use super::occ1::*;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/// Fixed-seed xorshift64; deterministic and reproducible.
struct TestRng(u64);

impl TestRng {
    fn new(seed: u64) -> Self {
        assert_ne!(seed, 0);
        Self(seed)
    }

    fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    fn below(&mut self, n: u64) -> u64 {
        self.next_u64() % n
    }

    fn flip(&mut self) -> bool {
        self.below(2) == 0
    }
}

/// Characters that are legal in OCC-1 values: no newlines, no C0/C1 controls.
const SAFE_ALPHABET: &[&str] = &[
    "a", "B", "7", "z", " ", "=", ":", "\"", "'", "_", "-", ".", ",", "!", "?", "(", ")", "配",
    "転", "述", "锁", "順", "😀", "🚀",
];

fn safe_value(rng: &mut TestRng, max_units: usize) -> String {
    let units = rng.below(max_units as u64 + 1) as usize;
    let mut out = String::new();
    for _ in 0..units {
        out.push_str(SAFE_ALPHABET[rng.below(SAFE_ALPHABET.len() as u64) as usize]);
    }
    out
}

/// A minimal contract-valid RESULT message.
fn result_msg() -> Occ1Message {
    Occ1Message::new()
        .with("TASK", "t1")
        .with("KIND", "RESULT")
        .with("STATUS", "done")
}

/// Full contract round trip: to_occ1 -> encode -> decode -> from_occ1.
fn round_trip(message: &AgentCommMessage) -> AgentCommMessage {
    let wire = message
        .to_occ1()
        .expect("encodes to OCC-1")
        .encode()
        .expect("encodes to wire");
    AgentCommMessage::from_occ1(&Occ1Message::decode(&wire).expect("decodes wire"))
        .expect("decodes contract")
}

/// Deterministic generator for contract-valid messages (property tests).
fn gen_agent_comm_message(rng: &mut TestRng, index: usize) -> AgentCommMessage {
    const KINDS: &[&str] = &[
        kinds::TASK,
        kinds::PROGRESS,
        kinds::RESULT,
        kinds::EVIDENCE,
        kinds::REQUEST_DETAIL,
        kinds::ACK,
        kinds::CORRECTION,
        kinds::BLOCKED,
        kinds::CANCELLED,
    ];
    const STATUSES: &[&str] = &[
        statuses::PENDING,
        statuses::IN_PROGRESS,
        statuses::DONE,
        statuses::PARTIAL,
        statuses::BLOCKED,
        statuses::FAILED,
        statuses::CANCELLED,
    ];
    const EVIDENCE_KINDS: &[&str] = &[
        evidence_kinds::SOURCE,
        evidence_kinds::COMMIT,
        evidence_kinds::TEST_LOG,
        evidence_kinds::BUILD_RESULT,
        evidence_kinds::TOOL_RECEIPT,
        evidence_kinds::ARTIFACT_DIGEST,
        evidence_kinds::LINE_RANGE,
        evidence_kinds::PRIOR_FINDING,
    ];
    // Confidences chosen so format!("{:.3}") parses back to the same f32.
    const CONFIDENCES: &[Option<f32>] = &[
        None,
        Some(0.0),
        Some(0.125),
        Some(0.25),
        Some(0.5),
        Some(0.75),
        Some(0.9),
        Some(1.0),
    ];
    const TIMESTAMPS: &[&str] = &[
        "2026-10-15T12:34:56Z",
        "2025-01-02T03:04:05.123+02:00",
        "2030-12-31T23:59:59Z",
        "2000-06-01T00:00:00-05:00",
    ];

    let kind = KINDS[rng.below(KINDS.len() as u64) as usize];
    let status = STATUSES[rng.below(STATUSES.len() as u64) as usize];
    let mut message = AgentCommMessage::new(kind, format!("t{}", rng.below(1000)), status);
    if rng.flip() {
        message.message_id = Some(format!("m{index}"));
    }
    if rng.flip() {
        message.parent_task_id = Some(format!("p{index}"));
    }
    if rng.flip() {
        message.trace_id = Some(format!("tr{index}"));
    }
    if rng.flip() {
        message.sender_identity = Some(safe_value(rng, 8));
    }
    if rng.flip() {
        message.recipient_identity = Some(safe_value(rng, 8));
    }
    if rng.flip() {
        message.sequence = Some(rng.below(1000));
    }

    // The contract requires EVIDENCE messages to carry at least one
    // evidence reference; other kinds may carry none.
    let evidence_count = if kind == kinds::EVIDENCE {
        1 + rng.below(3) as usize
    } else {
        rng.below(4) as usize
    };
    for e in 1..=evidence_count {
        let start = rng.below(1000) as u32;
        let mut evidence = EvidenceRef {
            id: format!("E{e}"),
            kind: EVIDENCE_KINDS[rng.below(EVIDENCE_KINDS.len() as u64) as usize].to_string(),
            locator: safe_value(rng, 12),
            line_range: None,
            digest: None,
            revision: None,
            verified: None,
        };
        if rng.flip() {
            evidence.line_range = Some((start, start + rng.below(50) as u32));
        }
        if rng.flip() {
            evidence.digest = Some(format!("{:08x}", rng.below(0xffff_ffff)));
        }
        if rng.flip() {
            evidence.revision = Some(format!("r{}", rng.below(1000)));
        }
        match rng.below(3) {
            0 => evidence.verified = Some(true),
            1 => evidence.verified = Some(false),
            _ => {}
        }
        message.evidence.push(evidence);
    }

    let finding_count = rng.below(4) as usize;
    for f in 1..=finding_count {
        let mut evidence_ids = Vec::new();
        for e in 1..=evidence_count {
            if rng.flip() {
                evidence_ids.push(format!("E{e}"));
            }
        }
        message.findings.push(Finding {
            id: format!("F{f}"),
            statement: safe_value(rng, 20),
            confidence: CONFIDENCES[rng.below(CONFIDENCES.len() as u64) as usize],
            evidence: evidence_ids,
        });
    }

    for _ in 0..rng.below(4) {
        message.artifacts.push(safe_value(rng, 10));
    }
    for _ in 0..rng.below(4) {
        message.dependencies.push(format!("t{}", rng.below(1000)));
    }
    for _ in 0..rng.below(4) {
        message.next_actions.push(safe_value(rng, 12));
    }

    if rng.below(3) == 0 {
        message.verification = Some(Verification {
            claim: safe_value(rng, 12),
            status: ["pass", "fail", "unknown"][rng.below(3) as usize].to_string(),
            method: safe_value(rng, 8),
            receipt: if rng.flip() {
                Some(safe_value(rng, 6))
            } else {
                None
            },
        });
    }
    if rng.flip() {
        message.timestamp = Some(TIMESTAMPS[rng.below(TIMESTAMPS.len() as u64) as usize].to_string());
    }
    if rng.flip() {
        message.expiry = Some(TIMESTAMPS[rng.below(TIMESTAMPS.len() as u64) as usize].to_string());
    }
    message
}

// ---------------------------------------------------------------------------
// pinned constants and kind/status tables
// ---------------------------------------------------------------------------

#[test]
fn codec_constants_are_pinned() {
    assert_eq!(OCC1_HEADER, "OCC/1");
    assert_eq!(OCC1_MAX_FIELDS, 128);
    assert_eq!(OCC1_MAX_KEY_BYTES, 32);
    assert_eq!(OCC1_MAX_VALUE_BYTES, 4096);
    assert_eq!(OCC1_MAX_MESSAGE_BYTES, 64 * 1024);
    assert_eq!(AGENT_COMM_PROTOCOL, "optimus.agent-comm.v1");
}

#[test]
fn kind_and_status_constants_match_between_occ1_and_contract() {
    assert_eq!(KIND_TASK, kinds::TASK);
    assert_eq!(KIND_PROGRESS, kinds::PROGRESS);
    assert_eq!(KIND_RESULT, kinds::RESULT);
    assert_eq!(KIND_EVIDENCE, kinds::EVIDENCE);
    assert_eq!(KIND_REQUEST_DETAIL, kinds::REQUEST_DETAIL);
    assert_eq!(KIND_ACK, kinds::ACK);
    assert_eq!(KIND_CORRECTION, kinds::CORRECTION);
    assert_eq!(KIND_BLOCKED, kinds::BLOCKED);
    assert_eq!(KIND_CANCELLED, kinds::CANCELLED);
    assert_eq!(STATUS_PENDING, statuses::PENDING);
    assert_eq!(STATUS_IN_PROGRESS, statuses::IN_PROGRESS);
    assert_eq!(STATUS_DONE, statuses::DONE);
    assert_eq!(STATUS_PARTIAL, statuses::PARTIAL);
    assert_eq!(STATUS_BLOCKED, statuses::BLOCKED);
    assert_eq!(STATUS_FAILED, statuses::FAILED);
    assert_eq!(STATUS_CANCELLED, statuses::CANCELLED);
}

#[test]
fn is_known_kind_and_status_tables() {
    for kind in [
        KIND_TASK,
        KIND_PROGRESS,
        KIND_RESULT,
        KIND_EVIDENCE,
        KIND_REQUEST_DETAIL,
        KIND_ACK,
        KIND_CORRECTION,
        KIND_BLOCKED,
        KIND_CANCELLED,
    ] {
        assert!(is_known_kind(kind), "kind {kind}");
    }
    assert!(!is_known_kind("NOPE"));
    assert!(!is_known_kind("Result"));
    assert!(!is_known_kind(""));
    for status in [
        STATUS_PENDING,
        STATUS_IN_PROGRESS,
        STATUS_DONE,
        STATUS_PARTIAL,
        STATUS_BLOCKED,
        STATUS_FAILED,
        STATUS_CANCELLED,
    ] {
        assert!(is_known_status(status), "status {status}");
    }
    assert!(!is_known_status("Done"));
    assert!(!is_known_status(""));
}

#[test]
fn is_known_evidence_kind_table() {
    for kind in [
        evidence_kinds::SOURCE,
        evidence_kinds::COMMIT,
        evidence_kinds::TEST_LOG,
        evidence_kinds::BUILD_RESULT,
        evidence_kinds::TOOL_RECEIPT,
        evidence_kinds::ARTIFACT_DIGEST,
        evidence_kinds::LINE_RANGE,
        evidence_kinds::PRIOR_FINDING,
    ] {
        assert!(is_known_evidence_kind(kind), "kind {kind}");
    }
    assert!(!is_known_evidence_kind("src "));
    assert!(!is_known_evidence_kind(""));
    assert!(!is_known_evidence_kind("SRC"));
    assert!(!is_known_evidence_kind("screenshot"));
}

// ---------------------------------------------------------------------------
// codec round trips
// ---------------------------------------------------------------------------

#[test]
fn codec_round_trip_header_only_message() {
    let empty = Occ1Message::new();
    assert!(empty.is_empty());
    assert_eq!(empty.len(), 0);
    assert_eq!(empty.encode().unwrap(), "OCC/1");
    for text in ["OCC/1", "OCC/1\n", "OCC/1\r\n"] {
        let decoded = Occ1Message::decode(text).unwrap();
        assert!(decoded.is_empty(), "text {text:?}");
        assert_eq!(decoded, empty);
    }
    assert_eq!(Occ1Message::default(), empty);
}

#[test]
fn codec_round_trip_single_field() {
    let message = Occ1Message::new().with("TASK", "t42");
    let encoded = message.encode().unwrap();
    assert_eq!(encoded, "OCC/1\nTASK=t42");
    assert_eq!(Occ1Message::decode(&encoded).unwrap(), message);
    assert_eq!(message.get("TASK"), Some("t42"));
    assert_eq!(message.len(), 1);
    assert!(!message.is_empty());
}

#[test]
fn codec_round_trip_many_fields_preserves_order() {
    let mut message = Occ1Message::new();
    for i in 0..50 {
        message.push(format!("K{i}"), format!("value {i}"));
    }
    let encoded = message.encode().unwrap();
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
    let pairs: Vec<(&str, &str)> = decoded.iter().collect();
    for (i, (key, value)) in pairs.iter().enumerate() {
        assert_eq!(*key, format!("K{i}"));
        assert_eq!(*value, format!("value {i}"));
    }
}

#[test]
fn codec_round_trip_repeated_keys_preserve_order() {
    let message = Occ1Message::new()
        .with("NEXT", "first")
        .with("TASK", "t1")
        .with("NEXT", "second")
        .with("NEXT", "third");
    let encoded = message.encode().unwrap();
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
    assert_eq!(decoded.get_all("NEXT"), vec!["first", "second", "third"]);
    assert_eq!(decoded.get("NEXT"), Some("first"));
    assert_eq!(decoded.len(), 4);
}

#[test]
fn codec_round_trip_values_with_special_characters() {
    let message = Occ1Message::new()
        .with("EQ", "=x")
        .with("COLON", "a=b:c")
        .with("QUOTED", "\"double\" 'single'")
        .with("SPACED", "  padded  value  ");
    let encoded = message.encode().unwrap();
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
    assert_eq!(decoded.get("EQ"), Some("=x"));
    assert_eq!(decoded.get("COLON"), Some("a=b:c"));
    assert_eq!(decoded.get("QUOTED"), Some("\"double\" 'single'"));
    assert_eq!(decoded.get("SPACED"), Some("  padded  value  "));
}

#[test]
fn codec_round_trip_unicode_values() {
    let values = [
        "配転述 順序を保つ",
        "emoji 😀🚀 in values is legal",
        "mixed 配 😀 ascii",
        "🔒🔒🔒",
    ];
    for value in values {
        let message = Occ1Message::new().with("U", value);
        let encoded = message.encode().unwrap();
        let decoded = Occ1Message::decode(&encoded).unwrap();
        assert_eq!(decoded, message, "value {value:?}");
        assert_eq!(decoded.get("U"), Some(value));
    }
}

#[test]
fn codec_round_trip_empty_and_whitespace_values() {
    let message = Occ1Message::new()
        .with("EMPTY", "")
        .with("SPACES", "   ")
        .with("TRAILING", "x  ");
    let encoded = message.encode().unwrap();
    assert_eq!(encoded, "OCC/1\nEMPTY=\nSPACES=   \nTRAILING=x  ");
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
    assert_eq!(decoded.get("EMPTY"), Some(""));
    assert_eq!(decoded.get("SPACES"), Some("   "));
    assert_eq!(decoded.get("TRAILING"), Some("x  "));
}

#[test]
fn codec_value_length_exactly_max_round_trips() {
    // 4096 ASCII bytes, and 4096 bytes of mixed CJK + ASCII.
    let ascii = "x".repeat(OCC1_MAX_VALUE_BYTES);
    let mut message = Occ1Message::new().with("A", ascii.clone());
    let cjk = format!("{}x", "配".repeat(1365)); // 1365 * 3 + 1 = 4096 bytes
    assert_eq!(cjk.len(), OCC1_MAX_VALUE_BYTES);
    message.push("B", cjk.clone());
    let encoded = message.encode().unwrap();
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
    assert_eq!(decoded.get("A"), Some(ascii.as_str()));
    assert_eq!(decoded.get("B"), Some(cjk.as_str()));
}

#[test]
fn codec_value_over_max_rejected() {
    let oversized = "x".repeat(OCC1_MAX_VALUE_BYTES + 1);
    let message = Occ1Message::new().with("A", oversized.clone());
    match message.encode().unwrap_err() {
        Occ1Error::ValueTooLarge { key, limit } => {
            assert_eq!(key, "A");
            assert_eq!(limit, OCC1_MAX_VALUE_BYTES);
        }
        other => panic!("expected ValueTooLarge, got {other:?}"),
    }
    let text = format!("OCC/1\nA={oversized}");
    match Occ1Message::decode(&text).unwrap_err() {
        Occ1Error::ValueTooLarge { key, limit } => {
            assert_eq!(key, "A");
            assert_eq!(limit, OCC1_MAX_VALUE_BYTES);
        }
        other => panic!("expected ValueTooLarge, got {other:?}"),
    }
}

#[test]
fn codec_field_count_exactly_max_round_trips() {
    let mut message = Occ1Message::new();
    for i in 0..OCC1_MAX_FIELDS {
        message.push("A", format!("v{i}"));
    }
    assert_eq!(message.len(), OCC1_MAX_FIELDS);
    let encoded = message.encode().unwrap();
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
}

#[test]
fn codec_field_count_over_max_rejected() {
    let mut message = Occ1Message::new();
    for _ in 0..(OCC1_MAX_FIELDS + 1) {
        message.push("A", "v");
    }
    assert!(matches!(
        message.encode().unwrap_err(),
        Occ1Error::TooManyFields {
            limit: OCC1_MAX_FIELDS
        }
    ));
    // On decode the 129th field line (wire line 130) trips the bound.
    let mut text = String::from("OCC/1");
    for _ in 0..(OCC1_MAX_FIELDS + 1) {
        text.push_str("\nA=1");
    }
    match Occ1Message::decode(&text).unwrap_err() {
        Occ1Error::TooManyFields { limit } => assert_eq!(limit, OCC1_MAX_FIELDS),
        other => panic!("expected TooManyFields, got {other:?}"),
    }
}

#[test]
fn codec_message_size_exactly_max_round_trips() {
    // 5 header bytes + 15 fields of 4100 bytes + one field of 4031 bytes
    // = 65536 bytes exactly.
    let mut message = Occ1Message::new();
    for _ in 0..15 {
        message.push("AA", "x".repeat(OCC1_MAX_VALUE_BYTES));
    }
    message.push("AA", "x".repeat(4027));
    let encoded = message.encode().unwrap();
    assert_eq!(encoded.len(), OCC1_MAX_MESSAGE_BYTES);
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
}

#[test]
fn codec_message_size_over_max_rejected() {
    let mut message = Occ1Message::new();
    for _ in 0..15 {
        message.push("AA", "x".repeat(OCC1_MAX_VALUE_BYTES));
    }
    message.push("AA", "x".repeat(4028)); // 65537 bytes when encoded
    match message.encode().unwrap_err() {
        Occ1Error::TooLarge { limit } => assert_eq!(limit, OCC1_MAX_MESSAGE_BYTES),
        other => panic!("expected TooLarge, got {other:?}"),
    }
    let mut text = String::from("OCC/1");
    for _ in 0..15 {
        text.push_str("\nAA=");
        text.push_str(&"x".repeat(OCC1_MAX_VALUE_BYTES));
    }
    text.push_str("\nAA=");
    text.push_str(&"x".repeat(4028));
    assert_eq!(text.len(), OCC1_MAX_MESSAGE_BYTES + 1);
    match Occ1Message::decode(&text).unwrap_err() {
        Occ1Error::TooLarge { limit } => assert_eq!(limit, OCC1_MAX_MESSAGE_BYTES),
        other => panic!("expected TooLarge, got {other:?}"),
    }
}

#[test]
fn codec_key_length_boundaries() {
    let max_key = "K".repeat(OCC1_MAX_KEY_BYTES);
    let message = Occ1Message::new().with(max_key.clone(), "v");
    let encoded = message.encode().unwrap();
    let decoded = Occ1Message::decode(&encoded).unwrap();
    assert_eq!(decoded, message);
    assert_eq!(decoded.get(max_key.as_str()), Some("v"));

    let oversized_key = "K".repeat(OCC1_MAX_KEY_BYTES + 1);
    let message = Occ1Message::new().with(oversized_key.clone(), "v");
    match message.encode().unwrap_err() {
        Occ1Error::KeyTooLarge { key, limit } => {
            assert_eq!(key, oversized_key);
            assert_eq!(limit, OCC1_MAX_KEY_BYTES);
        }
        other => panic!("expected KeyTooLarge, got {other:?}"),
    }
    let text = format!("OCC/1\n{oversized_key}=v");
    match Occ1Message::decode(&text).unwrap_err() {
        Occ1Error::KeyTooLarge { key, limit } => {
            assert_eq!(key, oversized_key);
            assert_eq!(limit, OCC1_MAX_KEY_BYTES);
        }
        other => panic!("expected KeyTooLarge, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// malformed rejection
// ---------------------------------------------------------------------------

#[test]
fn codec_rejects_empty_input() {
    match Occ1Message::decode("").unwrap_err() {
        Occ1Error::Empty => {}
        other => panic!("expected Empty, got {other:?}"),
    }
    // A lone newline is not "empty": its only (stripped) line is a bad header.
    match Occ1Message::decode("\n").unwrap_err() {
        Occ1Error::BadHeader(got, expected) => {
            assert_eq!(got, "");
            assert_eq!(expected, OCC1_HEADER);
        }
        other => panic!("expected BadHeader, got {other:?}"),
    }
}

#[test]
fn codec_rejects_missing_header() {
    match Occ1Message::decode("TASK=t42\nKIND=RESULT").unwrap_err() {
        Occ1Error::BadHeader(got, expected) => {
            assert_eq!(got, "TASK=t42");
            assert_eq!(expected, OCC1_HEADER);
        }
        other => panic!("expected BadHeader, got {other:?}"),
    }
}

#[test]
fn codec_rejects_wrong_header_variants() {
    for wrong in ["OCC/2", "occ/1", "OCC/1 ", " OCC/1", "OCC/1.0", "OCC1", "XOCC/1"] {
        let text = format!("{wrong}\nA=1");
        match Occ1Message::decode(&text).unwrap_err() {
            Occ1Error::BadHeader(got, expected) => {
                assert_eq!(got, wrong, "input {text:?}");
                assert_eq!(expected, OCC1_HEADER);
            }
            other => panic!("input {text:?}: expected BadHeader, got {other:?}"),
        }
    }
}

#[test]
fn codec_rejects_lowercase_key() {
    match Occ1Message::decode("OCC/1\ntask=t42").unwrap_err() {
        Occ1Error::InvalidKey { line, key } => {
            assert_eq!(line, 2);
            assert_eq!(key, "task");
        }
        other => panic!("expected InvalidKey, got {other:?}"),
    }
}

#[test]
fn codec_rejects_keys_violating_key_grammar() {
    for (text, bad_key) in [
        ("OCC/1\n1AB=x", "1AB"), // digit first
        ("OCC/1\nA-B=x", "A-B"), // hyphen
        ("OCC/1\nA B=x", "A B"), // space inside key
        ("OCC/1\nÄ=x", "Ä"),     // non-ASCII
        ("OCC/1\nA.B=x", "A.B"), // dot
        ("OCC/1\n_x=x", "_x"),   // underscore first
    ] {
        match Occ1Message::decode(text).unwrap_err() {
            Occ1Error::InvalidKey { line, key } => {
                assert_eq!(line, 2, "input {text:?}");
                assert_eq!(key, bad_key, "input {text:?}");
            }
            other => panic!("input {text:?}: expected InvalidKey, got {other:?}"),
        }
    }
}

#[test]
fn codec_rejects_empty_key() {
    match Occ1Message::decode("OCC/1\n=orphan").unwrap_err() {
        Occ1Error::InvalidKey { line, key } => {
            assert_eq!(line, 2);
            assert_eq!(key, "");
        }
        other => panic!("expected InvalidKey, got {other:?}"),
    }
}

#[test]
fn codec_rejects_missing_separator() {
    match Occ1Message::decode("OCC/1\nTASK t42").unwrap_err() {
        Occ1Error::MissingSeparator { line } => assert_eq!(line, 2),
        other => panic!("expected MissingSeparator, got {other:?}"),
    }
    match Occ1Message::decode("OCC/1\nA=1\nJUSTKEY").unwrap_err() {
        Occ1Error::MissingSeparator { line } => assert_eq!(line, 3),
        other => panic!("expected MissingSeparator, got {other:?}"),
    }
}

#[test]
fn codec_rejects_blank_interior_line() {
    match Occ1Message::decode("OCC/1\nTASK=t42\n\nKIND=RESULT").unwrap_err() {
        Occ1Error::BlankLine { line } => assert_eq!(line, 3),
        other => panic!("expected BlankLine, got {other:?}"),
    }
    // A "\r"-only interior line is blank after CRLF stripping.
    match Occ1Message::decode("OCC/1\nA=1\r\n\r\nB=2").unwrap_err() {
        Occ1Error::BlankLine { line } => assert_eq!(line, 3),
        other => panic!("expected BlankLine, got {other:?}"),
    }
}

#[test]
fn codec_whitespace_only_interior_line_is_missing_separator() {
    // The BlankLine error documents "empty or whitespace-only", but the
    // implementation only flags truly empty lines; whitespace-only lines fail
    // the KEY=VALUE grammar instead. Reported as a doc/behavior mismatch.
    match Occ1Message::decode("OCC/1\nA=1\n   \nB=2").unwrap_err() {
        Occ1Error::MissingSeparator { line } => assert_eq!(line, 3),
        other => panic!("expected MissingSeparator, got {other:?}"),
    }
}

#[test]
fn codec_rejects_two_trailing_newlines() {
    // At most one trailing newline is tolerated; a second becomes an
    // interior blank line.
    match Occ1Message::decode("OCC/1\nTASK=t42\n\n").unwrap_err() {
        Occ1Error::BlankLine { line } => assert_eq!(line, 3),
        other => panic!("expected BlankLine, got {other:?}"),
    }
    match Occ1Message::decode("OCC/1\n\n").unwrap_err() {
        Occ1Error::BlankLine { line } => assert_eq!(line, 2),
        other => panic!("expected BlankLine, got {other:?}"),
    }
}

#[test]
fn codec_tolerates_crlf_line_endings() {
    let text = "OCC/1\r\nTASK=t42\r\nKIND=RESULT\r\n";
    let decoded = Occ1Message::decode(text).unwrap();
    assert_eq!(decoded.get("TASK"), Some("t42"));
    assert_eq!(decoded.get("KIND"), Some("RESULT"));
    // Re-encoding normalizes to LF.
    assert_eq!(decoded.encode().unwrap(), "OCC/1\nTASK=t42\nKIND=RESULT");
    let decoded = Occ1Message::decode("OCC/1\r\nA=1\r\n").unwrap();
    assert_eq!(decoded.get("A"), Some("1"));
    // A trailing "\r" alone after the final line is stripped as a line ending.
    let decoded = Occ1Message::decode("OCC/1\nA=1\r").unwrap();
    assert_eq!(decoded.get("A"), Some("1"));
}

#[test]
fn codec_rejects_lone_cr_line_endings() {
    // CR without LF is not a line ending: the whole text is one line and the
    // header comparison fails.
    for text in ["OCC/1\rTASK=t42", "OCC/1\rKIND=RESULT\rSTATUS=done"] {
        match Occ1Message::decode(text).unwrap_err() {
            Occ1Error::BadHeader(got, expected) => {
                assert_eq!(got, text);
                assert_eq!(expected, OCC1_HEADER);
            }
            other => panic!("expected BadHeader, got {other:?}"),
        }
    }
}

#[test]
fn codec_rejects_control_characters_in_values() {
    // \t is a C0 control char (char::is_control), so tabs are rejected in
    // values on both decode and encode.
    for (text, line) in [
        ("OCC/1\nA=x\ty", 2),
        ("OCC/1\nA=x\u{0}y", 2),
        ("OCC/1\nA=x\ry", 2),
        ("OCC/1\nA=x\u{1}y", 2),
        ("OCC/1\nA=x\u{1f}y", 2),
        ("OCC/1\nA=x\u{7f}y", 2),
        ("OCC/1\nA=x\u{85}y", 2),
        ("OCC/1\nA=x\u{9b}y", 2),
        ("OCC/1\nB=1\nA=x\ty", 3),
    ] {
        match Occ1Message::decode(text).unwrap_err() {
            Occ1Error::ControlChar { line: got_line, key } => {
                assert_eq!(got_line, line, "input {text:?}");
                assert_eq!(key, "A", "input {text:?}");
            }
            other => panic!("input {text:?}: expected ControlChar, got {other:?}"),
        }
    }
    // encode() has no line context, so it reports line 0.
    for value in ["x\ty", "x\u{0}y", "x\ry"] {
        let message = Occ1Message::new().with("A", value);
        match message.encode().unwrap_err() {
            Occ1Error::ControlChar { line, key } => {
                assert_eq!(line, 0);
                assert_eq!(key, "A");
            }
            other => panic!("expected ControlChar, got {other:?}"),
        }
    }
}

#[test]
fn codec_encode_reports_invalid_keys_at_line_zero() {
    let message = Occ1Message::new().with("bad", "v");
    match message.encode().unwrap_err() {
        Occ1Error::InvalidKey { line, key } => {
            assert_eq!(line, 0);
            assert_eq!(key, "bad");
        }
        other => panic!("expected InvalidKey, got {other:?}"),
    }
    let message = Occ1Message::new().with("A", "ok").with("2B", "v");
    match message.encode().unwrap_err() {
        Occ1Error::InvalidKey { line, key } => {
            assert_eq!(line, 0);
            assert_eq!(key, "2B");
        }
        other => panic!("expected InvalidKey, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// Occ1Message API
// ---------------------------------------------------------------------------

#[test]
fn api_set_replaces_all_duplicates_and_appends() {
    let mut message = Occ1Message::new()
        .with("A", "1")
        .with("B", "2")
        .with("A", "3");
    message.set("A", "9");
    let pairs: Vec<(String, String)> = message
        .iter()
        .map(|(key, value)| (key.to_string(), value.to_string()))
        .collect();
    assert_eq!(
        pairs,
        vec![
            ("B".to_string(), "2".to_string()),
            ("A".to_string(), "9".to_string())
        ]
    );
    assert_eq!(message.get_all("A"), vec!["9"]);
    // set on a fresh key appends.
    message.set("C", "7");
    assert_eq!(message.get_all("C"), vec!["7"]);
    assert_eq!(message.len(), 3);
}

#[test]
fn api_remove_removes_every_occurrence_and_counts() {
    let mut message = Occ1Message::new()
        .with("NEXT", "a")
        .with("KEEP", "k")
        .with("NEXT", "b")
        .with("NEXT", "c");
    assert_eq!(message.remove("NEXT"), 3);
    assert_eq!(message.get_all("NEXT"), Vec::<&str>::new());
    assert!(!message.has("NEXT"));
    assert_eq!(message.remove("NEXT"), 0);
    assert_eq!(message.remove("KEEP"), 1);
    assert!(message.is_empty());
}

#[test]
fn api_get_returns_first_and_get_all_returns_every_occurrence() {
    let message = Occ1Message::new()
        .with("K", "first")
        .with("OTHER", "x")
        .with("K", "second")
        .with("K", "third");
    assert_eq!(message.get("K"), Some("first"));
    assert_eq!(message.get_all("K"), vec!["first", "second", "third"]);
    assert_eq!(message.get("MISSING"), None);
    assert!(message.get_all("MISSING").is_empty());
    assert!(message.has("K"));
    assert!(!message.has("MISSING"));
}

#[test]
fn api_required_missing_preserves_required_order() {
    let message = Occ1Message::new().with("TASK", "t1");
    let missing = message.required_missing(&["TASK", "KIND", "STATUS", "TASK"]);
    assert_eq!(missing, vec!["KIND".to_string(), "STATUS".to_string()]);
    let complete = Occ1Message::new().with("A", "1");
    assert!(complete.required_missing(&["A"]).is_empty());
}

#[test]
fn api_render_readable_lists_all_fields_and_is_stable() {
    let message = Occ1Message::new()
        .with("KIND", "RESULT")
        .with("TASK", "t42")
        .with("STATUS", "done")
        .with("F1", "race window")
        .with("NEXT", "a")
        .with("NEXT", "b");
    let rendered = message.render_readable();
    assert_eq!(rendered, message.render_readable());
    assert!(rendered.starts_with("OCC-1 message kind=RESULT task=t42 status=done"));
    // KIND/TASK/STATUS only appear in the summary line; every other field
    // appears once per occurrence.
    assert_eq!(
        rendered,
        "OCC-1 message kind=RESULT task=t42 status=done\n  F1: race window\n  NEXT: a\n  NEXT: b"
    );
}

#[test]
fn api_render_readable_defaults_missing_headers() {
    let message = Occ1Message::new().with("X", "1");
    assert_eq!(
        message.render_readable(),
        "OCC-1 message kind=UNKNOWN task=- status=-\n  X: 1"
    );
}

#[test]
fn api_len_is_empty_and_iter() {
    let mut message = Occ1Message::new();
    assert!(message.is_empty());
    assert_eq!(message.len(), 0);
    assert_eq!(message.iter().count(), 0);
    message.push("A", "1");
    message.push("A", "2");
    assert_eq!(message.len(), 2);
    assert!(!message.is_empty());
    let pairs: Vec<(&str, &str)> = message.iter().collect();
    assert_eq!(pairs, vec![("A", "1"), ("A", "2")]);
}

// ---------------------------------------------------------------------------
// Occ1Dialect
// ---------------------------------------------------------------------------

#[test]
fn dialect_required_for_matches_standard_table() {
    let dialect = Occ1Dialect::standard();
    // BUG-002 regression: the table mirrors exactly what the
    // optimus.agent-comm.v1 encoder emits for every kind, so a
    // contract-encoded message always passes dialect validation.
    let expected: &[(&str, &[&str])] = &[
        ("TASK", &["TASK", "KIND", "STATUS"]),
        ("PROGRESS", &["TASK", "KIND", "STATUS"]),
        ("RESULT", &["TASK", "KIND", "STATUS"]),
        ("EVIDENCE", &["TASK", "KIND", "STATUS", "E1"]),
        ("REQUEST_DETAIL", &["TASK", "KIND", "STATUS"]),
        ("ACK", &["TASK", "KIND", "STATUS"]),
        ("CORRECTION", &["TASK", "KIND", "STATUS"]),
        ("BLOCKED", &["TASK", "KIND", "STATUS"]),
        ("CANCELLED", &["TASK", "KIND", "STATUS"]),
    ];
    for &(kind, keys) in expected {
        assert_eq!(dialect.required_for(kind), Some(keys), "kind {kind}");
    }
    assert_eq!(dialect.required_for("NOPE"), None);
    assert_eq!(dialect.required_for(""), None);
}

#[test]
fn dialect_default_matches_standard() {
    let default = Occ1Dialect::default();
    let standard = Occ1Dialect::standard();
    for kind in [
        KIND_TASK,
        KIND_PROGRESS,
        KIND_RESULT,
        KIND_EVIDENCE,
        KIND_REQUEST_DETAIL,
        KIND_ACK,
        KIND_CORRECTION,
        KIND_BLOCKED,
        KIND_CANCELLED,
    ] {
        assert_eq!(default.required_for(kind), standard.required_for(kind));
    }
}

#[test]
fn dialect_validates_each_kind_when_complete() {
    let dialect = Occ1Dialect::standard();
    // Required fields per the standard table, plus the optional
    // GOAL/WANT/REF/WHY extras to prove they stay legal.
    let cases: Vec<Occ1Message> = vec![
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "TASK")
            .with("STATUS", "pending")
            .with("GOAL", "fix the race"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "PROGRESS")
            .with("STATUS", "in_progress"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "RESULT")
            .with("STATUS", "done"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "EVIDENCE")
            .with("STATUS", "done")
            .with("E1", "src:x.rs"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "REQUEST_DETAIL")
            .with("STATUS", "pending")
            .with("WANT", "lock order"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "ACK")
            .with("STATUS", "done")
            .with("REF", "m-1"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "CORRECTION")
            .with("STATUS", "in_progress")
            .with("REF", "m-1"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "BLOCKED")
            .with("STATUS", "blocked")
            .with("WHY", "missing credentials"),
        Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "CANCELLED")
            .with("STATUS", "cancelled"),
    ];
    for message in &cases {
        dialect.validate(message).unwrap();
    }
}

#[test]
fn dialect_task_missing_status_or_task_is_rejected() {
    let dialect = Occ1Dialect::standard();
    let full = Occ1Message::new()
        .with("TASK", "t")
        .with("KIND", "TASK")
        .with("STATUS", "pending");
    dialect.validate(&full).unwrap();
    for missing in ["STATUS", "TASK"] {
        let mut message = full.clone();
        message.remove(missing);
        match dialect.validate(&message).unwrap_err() {
            Occ1ValidationError::MissingRequired { kind, field } => {
                assert_eq!(kind, "TASK");
                assert_eq!(field, missing);
            }
            other => panic!("expected MissingRequired, got {other:?}"),
        }
    }
    // Removing KIND is reported as MissingKind, which is checked first.
    let mut message = full.clone();
    message.remove("KIND");
    assert!(matches!(
        dialect.validate(&message).unwrap_err(),
        Occ1ValidationError::MissingKind
    ));
}

#[test]
fn dialect_progress_and_result_require_status() {
    let dialect = Occ1Dialect::standard();
    for kind in [KIND_PROGRESS, KIND_RESULT] {
        let full = Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", kind)
            .with("STATUS", "in_progress");
        dialect.validate(&full).unwrap();
        for missing in ["STATUS", "TASK"] {
            let mut message = full.clone();
            message.remove(missing);
            match dialect.validate(&message).unwrap_err() {
                Occ1ValidationError::MissingRequired { kind: got_kind, field } => {
                    assert_eq!(got_kind, kind);
                    assert_eq!(field, missing);
                }
                other => panic!("expected MissingRequired, got {other:?}"),
            }
        }
    }
}

#[test]
fn dialect_evidence_requires_e1() {
    let dialect = Occ1Dialect::standard();
    let full = Occ1Message::new()
        .with("TASK", "t")
        .with("KIND", KIND_EVIDENCE)
        .with("STATUS", "done")
        .with("E1", "src:src/a.rs");
    dialect.validate(&full).unwrap();
    let mut message = full.clone();
    message.remove("E1");
    match dialect.validate(&message).unwrap_err() {
        Occ1ValidationError::MissingRequired { kind, field } => {
            assert_eq!(kind, KIND_EVIDENCE);
            assert_eq!(field, "E1");
        }
        other => panic!("expected MissingRequired, got {other:?}"),
    }
}

#[test]
fn dialect_request_detail_requires_status_and_tolerates_want() {
    let dialect = Occ1Dialect::standard();
    let full = Occ1Message::new()
        .with("TASK", "t")
        .with("KIND", KIND_REQUEST_DETAIL)
        .with("STATUS", "pending")
        .with("WANT", "lock ordering");
    dialect.validate(&full).unwrap();
    // STATUS is required for every kind the encoder can produce.
    let mut message = full.clone();
    message.remove("STATUS");
    match dialect.validate(&message).unwrap_err() {
        Occ1ValidationError::MissingRequired { kind, field } => {
            assert_eq!(kind, KIND_REQUEST_DETAIL);
            assert_eq!(field, "STATUS");
        }
        other => panic!("expected MissingRequired, got {other:?}"),
    }
    // WANT is an optional extra: removing it still validates.
    let mut without_want = full.clone();
    without_want.remove("WANT");
    dialect.validate(&without_want).unwrap();
}

#[test]
fn dialect_ack_and_correction_require_status_and_tolerate_ref() {
    let dialect = Occ1Dialect::standard();
    for kind in [KIND_ACK, KIND_CORRECTION] {
        let full = Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", kind)
            .with("STATUS", "done")
            .with("REF", "m-1");
        dialect.validate(&full).unwrap();
        let mut message = full.clone();
        message.remove("STATUS");
        match dialect.validate(&message).unwrap_err() {
            Occ1ValidationError::MissingRequired { kind: got, field } => {
                assert_eq!(got, kind);
                assert_eq!(field, "STATUS");
            }
            other => panic!("expected MissingRequired, got {other:?}"),
        }
        // REF is an optional extra: removing it still validates.
        let mut without_ref = full.clone();
        without_ref.remove("REF");
        dialect.validate(&without_ref).unwrap();
    }
}

#[test]
fn dialect_blocked_requires_status_and_tolerates_why() {
    let dialect = Occ1Dialect::standard();
    let full = Occ1Message::new()
        .with("TASK", "t")
        .with("KIND", KIND_BLOCKED)
        .with("STATUS", "blocked")
        .with("WHY", "no credentials");
    dialect.validate(&full).unwrap();
    let mut message = full.clone();
    message.remove("STATUS");
    match dialect.validate(&message).unwrap_err() {
        Occ1ValidationError::MissingRequired { kind, field } => {
            assert_eq!(kind, KIND_BLOCKED);
            assert_eq!(field, "STATUS");
        }
        other => panic!("expected MissingRequired, got {other:?}"),
    }
    // WHY is an optional extra: removing it still validates.
    let mut without_why = full.clone();
    without_why.remove("WHY");
    dialect.validate(&without_why).unwrap();
}

#[test]
fn dialect_cancelled_requires_task_kind_and_status() {
    let dialect = Occ1Dialect::standard();
    let full = Occ1Message::new()
        .with("TASK", "t")
        .with("KIND", "CANCELLED")
        .with("STATUS", "cancelled");
    dialect.validate(&full).unwrap();
    for missing in ["STATUS", "TASK"] {
        let mut message = full.clone();
        message.remove(missing);
        match dialect.validate(&message).unwrap_err() {
            Occ1ValidationError::MissingRequired { kind, field } => {
                assert_eq!(kind, "CANCELLED");
                assert_eq!(field, missing);
            }
            other => panic!("expected MissingRequired, got {other:?}"),
        }
    }
    // Optional extras stay legal for CANCELLED.
    let mut extra = full.clone();
    extra.push("SEQ", "7");
    dialect.validate(&extra).unwrap();
}

#[test]
fn dialect_rejects_unknown_and_missing_kind() {
    let dialect = Occ1Dialect::standard();
    let message = Occ1Message::new().with("TASK", "t").with("KIND", "NOPE");
    match dialect.validate(&message).unwrap_err() {
        Occ1ValidationError::UnknownKind(kind) => assert_eq!(kind, "NOPE"),
        other => panic!("expected UnknownKind, got {other:?}"),
    }
    let message = Occ1Message::new().with("TASK", "t").with("STATUS", "done");
    assert!(matches!(
        dialect.validate(&message).unwrap_err(),
        Occ1ValidationError::MissingKind
    ));
}

#[test]
fn dialect_rejects_invalid_sequence_values() {
    let dialect = Occ1Dialect::standard();
    for bad in ["-1", "x", "1.5", "", " 1", "1 ", "0x1", "18446744073709551616"] {
        let mut message = Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "RESULT")
            .with("STATUS", "done");
        message.push("SEQ", bad);
        match dialect.validate(&message).unwrap_err() {
            Occ1ValidationError::InvalidSequence(seq) => assert_eq!(seq, bad, "SEQ={bad:?}"),
            other => panic!("SEQ={bad:?}: expected InvalidSequence, got {other:?}"),
        }
    }
}

#[test]
fn dialect_accepts_valid_sequence_values() {
    let dialect = Occ1Dialect::standard();
    for good in ["0", "1", "7", "007", "18446744073709551615"] {
        let mut message = Occ1Message::new()
            .with("TASK", "t")
            .with("KIND", "RESULT")
            .with("STATUS", "done");
        message.push("SEQ", good);
        dialect.validate(&message).unwrap();
    }
}

#[test]
fn dialect_rejects_unknown_status() {
    let dialect = Occ1Dialect::standard();
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("KIND", "RESULT")
        .with("STATUS", "wednesday");
    match dialect.validate(&message).unwrap_err() {
        Occ1ValidationError::UnknownStatus(status) => assert_eq!(status, "wednesday"),
        other => panic!("expected UnknownStatus, got {other:?}"),
    }
    // The status check also applies to kinds that do not require STATUS,
    // and it is case-sensitive.
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("KIND", "CANCELLED")
        .with("STATUS", "Done");
    match dialect.validate(&message).unwrap_err() {
        Occ1ValidationError::UnknownStatus(status) => assert_eq!(status, "Done"),
        other => panic!("expected UnknownStatus, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// contract: to_occ1 / from_occ1 round trips for all nine kinds
// ---------------------------------------------------------------------------

#[test]
fn contract_round_trip_task_kind() {
    let mut message = AgentCommMessage::new(kinds::TASK, "t1", statuses::PENDING);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "scope agreed: fix the OCC-1 codec tests".into(),
        confidence: Some(0.5),
        evidence: vec![],
    });
    message.next_actions.push("draft the test plan".into());
    message.sequence = Some(0);
    message.timestamp = Some("2026-01-01T00:00:00Z".into());
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_progress_kind() {
    let mut message = AgentCommMessage::new(kinds::PROGRESS, "t2", statuses::IN_PROGRESS);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "codec handles 40 fields".into(),
        confidence: None,
        evidence: vec!["E1".into()],
    });
    message.evidence.push(EvidenceRef {
        id: "E1".into(),
        kind: evidence_kinds::TEST_LOG.into(),
        locator: "tests/codec.log".into(),
        line_range: None,
        digest: None,
        revision: None,
        verified: Some(false),
    });
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_result_kind_full() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t9", statuses::DONE);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "field order is deterministic".into(),
        confidence: Some(0.75),
        evidence: vec!["E1".into(), "E2".into()],
    });
    message.findings.push(Finding {
        id: "F2".into(),
        statement: "repeated keys stay ordered".into(),
        confidence: None,
        evidence: vec![],
    });
    message.evidence.push(EvidenceRef {
        id: "E1".into(),
        kind: evidence_kinds::SOURCE.into(),
        locator: "src/occ/codec_tests.rs".into(),
        line_range: Some((1, 40)),
        digest: None,
        revision: Some("159ce68cc".into()),
        verified: Some(false),
    });
    message.evidence.push(EvidenceRef {
        id: "E2".into(),
        kind: evidence_kinds::COMMIT.into(),
        locator: "159ce68cc".into(),
        line_range: None,
        digest: Some("deadbeef".into()),
        revision: None,
        verified: None,
    });
    message.artifacts.push("reports/occ.txt".into());
    message.artifacts.push("".into());
    message.verification = Some(Verification {
        claim: "all codec tests pass".into(),
        status: verification::UNKNOWN.into(),
        method: "cargo_test".into(),
        receipt: None,
    });
    message.dependencies.push("t8".into());
    message.next_actions.push("review".into());
    message.sequence = Some(42);
    message.timestamp = Some("2026-10-15T09:00:00Z".into());
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_evidence_kind() {
    let mut message = AgentCommMessage::new(kinds::EVIDENCE, "t3", statuses::IN_PROGRESS);
    message.evidence.push(EvidenceRef {
        id: "E1".into(),
        kind: evidence_kinds::COMMIT.into(),
        locator: "159ce68cc".into(),
        line_range: None,
        digest: Some("deadbeef".into()),
        revision: None,
        verified: None,
    });
    message.evidence.push(EvidenceRef {
        id: "E2".into(),
        kind: evidence_kinds::ARTIFACT_DIGEST.into(),
        locator: "reports/race.txt".into(),
        line_range: None,
        digest: Some("a1b2c3d4".into()),
        revision: Some("159ce68cc".into()),
        verified: Some(true),
    });
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_request_detail_kind() {
    let mut message = AgentCommMessage::new(kinds::REQUEST_DETAIL, "t4", statuses::PENDING);
    message.next_actions.push("send the lock ordering for session.rs".into());
    message.next_actions.push("include the failing test name".into());
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_ack_kind() {
    let message = AgentCommMessage::new(kinds::ACK, "t5", statuses::DONE);
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_correction_kind() {
    let mut message = AgentCommMessage::new(kinds::CORRECTION, "t6", statuses::PARTIAL);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "earlier claim about field order was wrong".into(),
        confidence: Some(0.75),
        evidence: vec!["E1".into()],
    });
    message.evidence.push(EvidenceRef {
        id: "E1".into(),
        kind: evidence_kinds::PRIOR_FINDING.into(),
        locator: "msg-42".into(),
        line_range: None,
        digest: None,
        revision: None,
        verified: None,
    });
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_blocked_kind() {
    let mut message = AgentCommMessage::new(kinds::BLOCKED, "t7", statuses::BLOCKED);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "cannot proceed without staging credentials".into(),
        confidence: Some(0.9),
        evidence: vec![],
    });
    message.next_actions.push("provide the staging token".into());
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_round_trip_cancelled_kind() {
    let mut message = AgentCommMessage::new(kinds::CANCELLED, "t8", statuses::CANCELLED);
    message.sequence = Some(12);
    assert_eq!(round_trip(&message), message);
}

#[test]
fn contract_to_occ1_wire_format_is_exact() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t42", statuses::DONE);
    message.message_id = Some("m-1".into());
    message.parent_task_id = Some("p-1".into());
    message.trace_id = Some("tr-1".into());
    message.sender_identity = Some("agent-a".into());
    message.recipient_identity = Some("agent-b".into());
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
    message.artifacts.push("reports/race.txt".into());
    message.verification = Some(Verification {
        claim: "session_race reproduces".into(),
        status: verification::FAIL.into(),
        method: "cargo_test".into(),
        receipt: Some("r-9".into()),
    });
    message.dependencies.push("t41".into());
    message.next_actions.push("inspect_lock".into());
    message.next_actions.push("regression_test".into());
    message.timestamp = Some("2026-10-15T12:34:56Z".into());
    message.expiry = Some("2026-10-16T00:00:00Z".into());
    let wire = message.to_occ1().unwrap().encode().unwrap();
    let expected = "OCC/1\n\
MSGID=m-1\n\
TASK=t42\n\
PARENT=p-1\n\
TRACE=tr-1\n\
SENDER=agent-a\n\
RECIPIENT=agent-b\n\
KIND=RESULT\n\
STATUS=done\n\
SEQ=3\n\
F1=race window on session lock\n\
FC1=0.900\n\
FE1=E1\n\
E1=src:src/session.rs\n\
EL1=218-235\n\
ED1=a1b2\n\
ER1=159ce68cc\n\
EV1=pass\n\
ART1=reports/race.txt\n\
V=session_race reproduces\n\
VS=fail\n\
VM=cargo_test\n\
VR=r-9\n\
DEP1=t41\n\
NEXT=inspect_lock\n\
NEXT=regression_test\n\
TS=2026-10-15T12:34:56Z\n\
EXP=2026-10-16T00:00:00Z";
    assert_eq!(wire, expected);
    let back = AgentCommMessage::from_occ1(&Occ1Message::decode(&wire).unwrap()).unwrap();
    assert_eq!(back, message);
}

#[test]
fn contract_to_occ1_rejects_newline_in_statement_via_encode() {
    // validate() does not inspect statement content; the codec rejects the
    // control character when the OCC-1 form is encoded.
    let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "line one\nline two".into(),
        confidence: None,
        evidence: vec![],
    });
    let occ = message.to_occ1().unwrap();
    match occ.encode().unwrap_err() {
        Occ1Error::ControlChar { line, key } => {
            assert_eq!(line, 0);
            assert_eq!(key, "F1");
        }
        other => panic!("expected ControlChar, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// contract: from_occ1 rejections
// ---------------------------------------------------------------------------

#[test]
fn from_occ1_requires_task_kind_status() {
    for (raw, field) in [
        (
            Occ1Message::new()
                .with("KIND", "RESULT")
                .with("STATUS", "done"),
            "TASK",
        ),
        (
            Occ1Message::new()
                .with("TASK", "t1")
                .with("STATUS", "done"),
            "KIND",
        ),
        (
            Occ1Message::new()
                .with("TASK", "t1")
                .with("KIND", "RESULT"),
            "STATUS",
        ),
    ] {
        match AgentCommMessage::from_occ1(&raw).unwrap_err() {
            ContractError::MissingRequired { field: got } => assert_eq!(got, field),
            other => panic!("expected MissingRequired, got {other:?}"),
        }
    }
}

#[test]
fn from_occ1_rejects_fe_referencing_e0() {
    let raw = result_msg().with("F1", "claim").with("FE1", "E0");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "FE1 references E0, outside 1..=0");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_fe_referencing_beyond_last_evidence() {
    let raw = result_msg()
        .with("F1", "claim")
        .with("E1", "src:src/a.rs")
        .with("FE1", "E2");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "FE1 references E2, outside 1..=1");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_fe_non_e_token() {
    for token in ["Q7", "E", "e1", "E-1", "E 2"] {
        let raw = result_msg()
            .with("F1", "claim")
            .with("E1", "src:src/a.rs")
            .with("FE1", token);
        match AgentCommMessage::from_occ1(&raw).unwrap_err() {
            ContractError::Occ1Conversion(msg) => {
                assert_eq!(msg, format!("FE1 references \"{token}\", expected E<n>"));
            }
            other => panic!("FE1={token}: expected Occ1Conversion, got {other:?}"),
        }
    }
}

#[test]
fn from_occ1_rejects_e_without_colon() {
    let raw = result_msg().with("E1", "src-without-colon");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "E1 must be <kind>:<locator>"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_el_without_dash() {
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("EL1", "218");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "EL1 must be <start>-<end>"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_el_non_numeric_bounds() {
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("EL1", "a-b");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "EL1 start invalid"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("EL1", "1-b");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "EL1 end invalid"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_ev_invalid_value() {
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("EV1", "maybe");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "EV1 must be pass/fail/unknown, got \"maybe\"");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_maps_ev_unknown_to_none() {
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("EV1", "unknown");
    let message = AgentCommMessage::from_occ1(&raw).unwrap();
    assert_eq!(message.evidence.len(), 1);
    assert_eq!(message.evidence[0].verified, None);
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("EV1", "pass");
    assert_eq!(
        AgentCommMessage::from_occ1(&raw).unwrap().evidence[0].verified,
        Some(true)
    );
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("EV1", "fail");
    assert_eq!(
        AgentCommMessage::from_occ1(&raw).unwrap().evidence[0].verified,
        Some(false)
    );
}

#[test]
fn from_occ1_rejects_verification_fields_without_claim() {
    for extra in ["VS", "VM"] {
        let mut raw = result_msg();
        raw.push(extra, "x");
        match AgentCommMessage::from_occ1(&raw).unwrap_err() {
            ContractError::Occ1Conversion(msg) => assert_eq!(msg, "VS/VM without V"),
            other => panic!("expected Occ1Conversion, got {other:?}"),
        }
    }
    let mut raw = result_msg();
    raw.push("VS", "fail");
    raw.push("VM", "cargo_test");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "VS/VM without V"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_v_without_vs() {
    let raw = result_msg().with("V", "claim").with("VM", "cargo_test");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "V without VS"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_v_without_vm() {
    let raw = result_msg().with("V", "claim").with("VS", "fail");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "V without VM"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_unknown_evidence_kind() {
    let raw = result_msg().with("E1", "bogus:somewhere");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::UnknownEvidenceKind(kind) => assert_eq!(kind, "bogus"),
        other => panic!("expected UnknownEvidenceKind, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_unknown_kind() {
    let raw = Occ1Message::new()
        .with("TASK", "t1")
        .with("KIND", "NOPE")
        .with("STATUS", "done");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::UnknownKind(kind) => assert_eq!(kind, "NOPE"),
        other => panic!("expected UnknownKind, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_unknown_status() {
    let raw = Occ1Message::new()
        .with("TASK", "t1")
        .with("KIND", "RESULT")
        .with("STATUS", "wednesday");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::UnknownStatus(status) => assert_eq!(status, "wednesday"),
        other => panic!("expected UnknownStatus, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_non_numeric_seq() {
    let raw = result_msg().with("SEQ", "x");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "SEQ \"x\" is not a u64"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
    // A numeric SEQ is accepted, including 0 and u64::MAX.
    let raw = result_msg().with("SEQ", "0");
    assert_eq!(
        AgentCommMessage::from_occ1(&raw).unwrap().sequence,
        Some(0)
    );
    let raw = result_msg().with("SEQ", "18446744073709551615");
    assert_eq!(
        AgentCommMessage::from_occ1(&raw).unwrap().sequence,
        Some(u64::MAX)
    );
}

#[test]
fn from_occ1_rejects_confidence_out_of_range() {
    for fc in ["1.5", "-0.25", "2"] {
        let raw = result_msg().with("F1", "claim").with("FC1", fc);
        match AgentCommMessage::from_occ1(&raw).unwrap_err() {
            ContractError::InvalidConfidence(value) => {
                assert_eq!(value, fc.parse::<f32>().unwrap(), "FC1={fc}");
            }
            other => panic!("FC1={fc}: expected InvalidConfidence, got {other:?}"),
        }
    }
    for fc in ["0.0", "1.0"] {
        let raw = result_msg().with("F1", "claim").with("FC1", fc);
        let message = AgentCommMessage::from_occ1(&raw).unwrap();
        assert_eq!(
            message.findings[0].confidence,
            Some(fc.parse::<f32>().unwrap())
        );
    }
}

#[test]
fn from_occ1_rejects_fc_not_a_number() {
    let raw = result_msg().with("F1", "claim").with("FC1", "high");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => assert_eq!(msg, "FC1 not a number"),
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

#[test]
fn from_occ1_rejects_invalid_timestamps() {
    for (field, bad) in [
        ("TS", "2026-10-15"),
        ("TS", "yesterday"),
        ("TS", "2026-13-45T99:99:99Z"),
        ("EXP", "not-a-time"),
    ] {
        let mut raw = result_msg();
        raw.push(field, bad);
        match AgentCommMessage::from_occ1(&raw).unwrap_err() {
            ContractError::InvalidTimestamp(stamp) => assert_eq!(stamp, bad),
            other => panic!("{field}={bad}: expected InvalidTimestamp, got {other:?}"),
        }
    }
    let raw = result_msg()
        .with("TS", "2026-10-15T12:34:56Z")
        .with("EXP", "2026-10-16T00:00:00+02:00");
    let message = AgentCommMessage::from_occ1(&raw).unwrap();
    assert_eq!(message.timestamp.as_deref(), Some("2026-10-15T12:34:56Z"));
    assert_eq!(
        message.expiry.as_deref(),
        Some("2026-10-16T00:00:00+02:00")
    );
}

#[test]
fn from_occ1_rejects_empty_task_id() {
    let raw = Occ1Message::new()
        .with("TASK", "")
        .with("KIND", "RESULT")
        .with("STATUS", "done");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::InvalidTaskId(id) => assert_eq!(id, ""),
        other => panic!("expected InvalidTaskId, got {other:?}"),
    }
}

#[test]
fn from_occ1_tolerates_unknown_extra_fields() {
    let mut raw = result_msg();
    raw.push("ZZZ", "future extension");
    raw.push("WANT", "ignored by the contract layer");
    let message = AgentCommMessage::from_occ1(&raw).unwrap();
    assert_eq!(message.task_id, "t1");
    assert_eq!(message.kind, kinds::RESULT);
    assert_eq!(message.status, statuses::DONE);
}

#[test]
fn from_occ1_uses_first_occurrence_for_scalar_fields() {
    let raw = Occ1Message::new()
        .with("TASK", "first")
        .with("KIND", "RESULT")
        .with("STATUS", "done")
        .with("TASK", "second");
    let message = AgentCommMessage::from_occ1(&raw).unwrap();
    assert_eq!(message.task_id, "first");
}

// BUG-001 regression: F2 missing between F1 and F3 must be rejected, not
// silently truncated. Confirmed fixed in contract.rs (indexed_count now
// errors on gaps below the highest index).
#[test]
fn from_occ1_rejects_finding_index_gap() {
    let raw = result_msg()
        .with("F1", "first finding")
        .with("F3", "third finding");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "F index 2 missing below the highest index 3");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
    // No F1 either: a lone F3 is still a gap at index 1.
    let raw = result_msg().with("F3", "third finding");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "F index 1 missing below the highest index 3");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

// BUG-001 regression on the E<n> index family: E2 missing between E1 and
// E3 must be rejected, not silently truncated.
#[test]
fn from_occ1_rejects_evidence_index_gap() {
    let raw = result_msg()
        .with("E1", "src:src/a.rs")
        .with("E3", "src:src/c.rs");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "E index 2 missing below the highest index 3");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
    // Gap detection also fires for the ART and DEP families.
    let raw = result_msg().with("ART1", "a.txt").with("ART3", "c.txt");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "ART index 2 missing below the highest index 3");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
    let raw = result_msg().with("DEP1", "t1").with("DEP3", "t3");
    match AgentCommMessage::from_occ1(&raw).unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "DEP index 2 missing below the highest index 3");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// contract: struct-level validate rejections
// ---------------------------------------------------------------------------

#[test]
fn validate_rejects_malformed_task_ids() {
    let mut message = AgentCommMessage::new(kinds::TASK, "", statuses::PENDING);
    match message.validate().unwrap_err() {
        ContractError::InvalidTaskId(id) => assert_eq!(id, ""),
        other => panic!("expected InvalidTaskId, got {other:?}"),
    }
    message.task_id = "t\u{1}x".into();
    match message.validate().unwrap_err() {
        ContractError::InvalidTaskId(id) => assert_eq!(id, "t\u{1}x"),
        other => panic!("expected InvalidTaskId, got {other:?}"),
    }
    // to_occ1 validates before encoding, so the same error surfaces there.
    match message.to_occ1() {
        Err(ContractError::InvalidTaskId(id)) => assert_eq!(id, "t\u{1}x"),
        other => panic!("expected InvalidTaskId, got {other:?}"),
    }
}

#[test]
fn validate_rejects_dangling_evidence_reference() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "claim".into(),
        confidence: None,
        evidence: vec!["E9".into()],
    });
    match message.validate().unwrap_err() {
        ContractError::UnknownEvidence {
            finding_id,
            evidence_id,
        } => {
            assert_eq!(finding_id, "F1");
            assert_eq!(evidence_id, "E9");
        }
        other => panic!("expected UnknownEvidence, got {other:?}"),
    }
}

#[test]
fn validate_rejects_unknown_evidence_kind() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
    message.evidence.push(EvidenceRef {
        id: "E1".into(),
        kind: "screenshot".into(),
        locator: "img.png".into(),
        line_range: None,
        digest: None,
        revision: None,
        verified: None,
    });
    match message.validate().unwrap_err() {
        ContractError::UnknownEvidenceKind(kind) => assert_eq!(kind, "screenshot"),
        other => panic!("expected UnknownEvidenceKind, got {other:?}"),
    }
}

#[test]
fn validate_confidence_bounds() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "claim".into(),
        confidence: Some(0.0),
        evidence: vec![],
    });
    message.validate().unwrap();
    message.findings[0].confidence = Some(1.0);
    message.validate().unwrap();
    message.findings[0].confidence = Some(-0.01);
    match message.validate().unwrap_err() {
        ContractError::InvalidConfidence(value) => assert_eq!(value, -0.01),
        other => panic!("expected InvalidConfidence, got {other:?}"),
    }
    message.findings[0].confidence = Some(1.001);
    match message.validate().unwrap_err() {
        ContractError::InvalidConfidence(value) => assert_eq!(value, 1.001),
        other => panic!("expected InvalidConfidence, got {other:?}"),
    }
    message.findings[0].confidence = None;
    message.validate().unwrap();
}

#[test]
fn validate_rejects_protocol_mismatch() {
    let mut message = AgentCommMessage::new(kinds::TASK, "t1", statuses::PENDING);
    message.protocol = "optimus.agent-comm.v2".into();
    match message.validate().unwrap_err() {
        ContractError::ProtocolMismatch(protocol) => {
            assert_eq!(protocol, "optimus.agent-comm.v2");
        }
        other => panic!("expected ProtocolMismatch, got {other:?}"),
    }
}

#[test]
fn validate_rejects_unknown_verification_status() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
    message.verification = Some(Verification {
        claim: "claim".into(),
        status: "maybe".into(),
        method: "cargo_test".into(),
        receipt: None,
    });
    match message.validate().unwrap_err() {
        ContractError::Occ1Conversion(msg) => {
            assert_eq!(msg, "unknown verification status \"maybe\"");
        }
        other => panic!("expected Occ1Conversion, got {other:?}"),
    }
    for status in [verification::PASS, verification::FAIL, verification::UNKNOWN] {
        message.verification = Some(Verification {
            claim: "claim".into(),
            status: status.into(),
            method: "cargo_test".into(),
            receipt: None,
        });
        message.validate().unwrap();
    }
}

#[test]
fn validate_rejects_malformed_timestamps() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t1", statuses::DONE);
    message.timestamp = Some("yesterday".into());
    match message.validate().unwrap_err() {
        ContractError::InvalidTimestamp(stamp) => assert_eq!(stamp, "yesterday"),
        other => panic!("expected InvalidTimestamp, got {other:?}"),
    }
    message.timestamp = None;
    message.expiry = Some("2026-02-30T00:00:00Z".into());
    match message.validate().unwrap_err() {
        ContractError::InvalidTimestamp(stamp) => assert_eq!(stamp, "2026-02-30T00:00:00Z"),
        other => panic!("expected InvalidTimestamp, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// JSON serde
// ---------------------------------------------------------------------------

#[test]
fn json_round_trip_full_message() {
    let mut message = AgentCommMessage::new(kinds::RESULT, "t42", statuses::DONE);
    message.message_id = Some("m-1".into());
    message.parent_task_id = Some("p-1".into());
    message.trace_id = Some("tr-1".into());
    message.findings.push(Finding {
        id: "F1".into(),
        statement: "unicode 配 😀 round trips through JSON".into(),
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
        receipt: Some("r-9".into()),
    });
    message.artifacts.push("reports/race.txt".into());
    message.dependencies.push("t41".into());
    message.next_actions.push("inspect_lock".into());
    message.sequence = Some(3);
    message.timestamp = Some("2026-10-15T12:34:56Z".into());
    message.expiry = Some("2026-10-16T00:00:00Z".into());
    let json = serde_json::to_string(&message).unwrap();
    let back: AgentCommMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(back, message);
}

#[test]
fn json_tolerates_unknown_fields() {
    let json = format!(
        r#"{{"task_id":"t1","kind":"TASK","status":"pending","protocol":"{AGENT_COMM_PROTOCOL}","bogus":42,"findings":[{{"id":"F1","statement":"s","bogus_nested":true}}]}}"#
    );
    let message: AgentCommMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(message.findings.len(), 1);
    assert_eq!(message.findings[0].id, "F1");
    assert_eq!(message.findings[0].statement, "s");
    assert_eq!(message.findings[0].confidence, None);
    assert_eq!(message.findings[0].evidence, Vec::<String>::new());
}

#[test]
fn json_omits_empty_collections_and_none_options() {
    let message = AgentCommMessage::new(kinds::TASK, "t1", statuses::PENDING);
    let json = serde_json::to_string(&message).unwrap();
    let value: serde_json::Value = serde_json::from_str(&json).unwrap();
    let mut keys: Vec<&str> = value
        .as_object()
        .unwrap()
        .keys()
        .map(|key| key.as_str())
        .collect();
    keys.sort_unstable();
    assert_eq!(keys, vec!["kind", "protocol", "status", "task_id"]);
    for absent in [
        "message_id",
        "parent_task_id",
        "trace_id",
        "sender_identity",
        "recipient_identity",
        "findings",
        "evidence",
        "artifacts",
        "verification",
        "dependencies",
        "next_actions",
        "sequence",
        "timestamp",
        "expiry",
    ] {
        assert!(
            !value.as_object().unwrap().contains_key(absent),
            "key {absent}"
        );
    }
}

#[test]
fn json_absent_optionals_default_to_none_and_empty() {
    let json = format!(
        r#"{{"task_id":"t1","kind":"TASK","status":"pending","protocol":"{AGENT_COMM_PROTOCOL}"}}"#
    );
    let message: AgentCommMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(
        message,
        AgentCommMessage::new(kinds::TASK, "t1", statuses::PENDING)
    );
}

#[test]
fn json_requires_protocol_task_kind_status() {
    for json in [
        r#"{"kind":"TASK","status":"pending","protocol":"optimus.agent-comm.v1"}"#,
        r#"{"task_id":"t1","status":"pending","protocol":"optimus.agent-comm.v1"}"#,
        r#"{"task_id":"t1","kind":"TASK","protocol":"optimus.agent-comm.v1"}"#,
        r#"{"task_id":"t1","kind":"TASK","status":"pending"}"#,
    ] {
        assert!(
            serde_json::from_str::<AgentCommMessage>(json).is_err(),
            "json {json}"
        );
    }
}

#[test]
fn json_protocol_mismatch_detected_via_validate() {
    let json = r#"{"task_id":"t1","kind":"TASK","status":"pending","protocol":"optimus.agent-comm.v2"}"#;
    let message: AgentCommMessage = serde_json::from_str(json).unwrap();
    match message.validate().unwrap_err() {
        ContractError::ProtocolMismatch(protocol) => {
            assert_eq!(protocol, "optimus.agent-comm.v2");
        }
        other => panic!("expected ProtocolMismatch, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// property-style loops (fixed seed)
// ---------------------------------------------------------------------------

#[test]
fn property_codec_round_trip_generated_messages() {
    let mut rng = TestRng::new(0x243F_6A88_85A3_08D3);
    const KEYS: &[&str] = &[
        "TASK", "KIND", "STATUS", "SEQ", "NEXT", "F1", "F2", "E1", "EL1", "ED1", "A", "B_C", "Z9",
        "MSGID", "X",
    ];
    for iteration in 0..30 {
        let field_count = rng.below(25) as usize;
        let mut message = Occ1Message::new();
        for _ in 0..field_count {
            let key = KEYS[rng.below(KEYS.len() as u64) as usize];
            message.push(key, safe_value(&mut rng, 24));
        }
        let encoded = message
            .encode()
            .unwrap_or_else(|err| panic!("iteration {iteration}: {err:?}"));
        assert!(encoded.starts_with(OCC1_HEADER), "iteration {iteration}");
        assert!(!encoded.contains('\r'), "iteration {iteration}");
        let decoded = Occ1Message::decode(&encoded)
            .unwrap_or_else(|err| panic!("iteration {iteration}: {err:?}"));
        assert_eq!(decoded, message, "iteration {iteration}");
    }
}

#[test]
fn property_contract_round_trip_generated_messages() {
    let mut rng = TestRng::new(0x1B15_6A7A_3C4E_DDCC);
    for iteration in 0..30 {
        let message = gen_agent_comm_message(&mut rng, iteration);
        message
            .validate()
            .unwrap_or_else(|err| panic!("iteration {iteration}: {err:?}"));
        let wire = message
            .to_occ1()
            .unwrap_or_else(|err| panic!("iteration {iteration}: {err:?}"))
            .encode()
            .unwrap_or_else(|err| panic!("iteration {iteration}: {err:?}"));
        let decoded = Occ1Message::decode(&wire)
            .unwrap_or_else(|err| panic!("iteration {iteration}: {err:?}"));
        let back = AgentCommMessage::from_occ1(&decoded)
            .unwrap_or_else(|err| panic!("iteration {iteration}: {err:?}"));
        assert_eq!(back, message, "iteration {iteration}");
    }
}
