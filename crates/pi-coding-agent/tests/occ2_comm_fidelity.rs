//! Offline, deterministic OCC-2 communication-fidelity suite (Phase 6
//! native integration, worker occ2-native).
//!
//! Exercises the public OCC-2 seam end to end against the message corpus
//! `research/tasks/msgs_corpus.json` (69 typed
//! `optimus.agent-comm.v1` messages, 18 of them holdout):
//!
//! - `t01` fixture shape: corpus count, declared holdout split, schema
//!   fit and coverage anchors.
//! - `t02` dual round-trip fidelity: `AgentCommMessage` ->
//!   `occ2::encode_dual` -> strict `occ2::decode` == source, and
//!   `occ2::occ1_fallback` on the same text == source.
//! - `t03` size comparison: symbolic vs OCC-1 vs JSON vs plain English,
//!   per split and aggregate.
//! - `t04` fault injection: unknown dialect, hash mismatch and wire
//!   corruption never strict-decode to the original and never panic;
//!   the embedded OCC-1 section still recovers the payload when intact.
//! - `t05` wrap policy: identity when disabled, dual encoding when
//!   enabled, delta exclusion (OQ4), and the R1 size guard (plain OCC-1
//!   instead of truncation).
//! - `t06` prompt seam: a dual body renders header lines plus the
//!   codebook and symbolic section only; the durable record keeps the dual verbatim;
//!   plain and OCC-1 bodies render byte-identically; the prompt-id parser
//!   still round-trips.
//! - `t07` negotiation: capability absence keeps OCC-1; `OccCapability`
//!   round-trips through the optional summary field.
//! - `t08` durability and compaction survival: encode -> serialize the
//!   durable record -> deserialize -> decode, including with an empty
//!   registry (daemon restart with no loaded cards).
//! - `t09` telemetry: the classify strict arm reports Symbolic only for
//!   bodies that decoded through the symbolic section.
//! - `t10` three-strike policy: forced strict-decode failures disable
//!   OCC-2 for the pair while delivery continues through OCC-1.
//! - `t11` latency: mean dual encode/decode under the 5 ms budget.
//! - `t12` aggregate headline report (final test by name order).
//!
//! Holdout rule: the corpus declares its holdout flag (`c23`-`c25`,
//! `c32`-`c35`, `c39`-`c41`, `c53`-`c55`, `c58`-`c61`, `c69`, the
//! discovery-harness stories). Holdout messages are never used to tune
//! thresholds: every assertion applies identically to train and holdout
//! rows, and the comparison tables report both splits separately.
//!
//! Everything here is offline and deterministic: no network, no model
//! APIs, no credentials, no randomness, no filesystem writes. The only
//! filesystem access is reading the corpus; the only clock is `Instant`,
//! used solely for the latency budget.

use std::collections::{BTreeMap, BTreeSet};
use std::time::{Duration, Instant};

use pi_coding_agent::core::agent_messages::{
    agent_session_message_prompt_body, create_agent_session_message,
    create_agent_session_message_prompt, decode_agent_session_message_body,
    parse_agent_session_message_prompt_id, select_occ2_dialect, wrap_outgoing_body,
    AgentSessionMessageAgentSummary, AgentSessionMessageEndpoint, AgentSessionMessagePayload,
    AgentSessionMessageSender, Occ2SendPolicy, Occ2StrikeLedger, AGENT_MESSAGE_SOURCE,
    DEFAULT_AGENT_MESSAGE_MAX_CHARS,
};
use pi_coding_agent::core::occ::activity::{classify, classify_with_registry, CommunicationKind};
use pi_coding_agent::core::occ::contract::AgentCommMessage;
use pi_coding_agent::core::occ::dialect::{
    codebook_sha256, DialectCard, DialectRegistry, OccCapability,
};
use pi_coding_agent::core::occ::occ2;
use serde::Deserialize;

const CORPUS_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../research/tasks/msgs_corpus.json");

/// Latency budget: mean per-message dual encode or decode time.
const LATENCY_BUDGET: Duration = Duration::from_millis(5);
/// Iterations per message for the latency measurement.
const LATENCY_ITERATIONS: u32 = 100;

// ---------------------------------------------------------------------------
// Corpus model (schema-compatible with the occ_comm_fidelity fixture)
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct CorpusEntry {
    id: String,
    holdout: bool,
    plain_english: String,
    payload: CorpusPayload,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CorpusPayload {
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
    findings: Vec<CorpusFinding>,
    #[serde(default)]
    evidence: Vec<pi_coding_agent::core::occ::contract::EvidenceRef>,
    #[serde(default)]
    artifacts: Vec<String>,
    #[serde(default)]
    verification: Option<pi_coding_agent::core::occ::contract::Verification>,
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CorpusFinding {
    statement: String,
    #[serde(default)]
    confidence: Option<f32>,
    #[serde(default)]
    evidence: Vec<String>,
}

fn load_corpus() -> Vec<CorpusEntry> {
    let text = std::fs::read_to_string(CORPUS_PATH)
        .unwrap_or_else(|error| panic!("read {CORPUS_PATH}: {error}"));
    serde_json::from_str(&text)
        .unwrap_or_else(|error| panic!("parse {CORPUS_PATH}: {error}"))
}

fn build_message(payload: &CorpusPayload) -> AgentCommMessage {
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
        message
            .findings
            .push(pi_coding_agent::core::occ::contract::Finding {
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

/// The OCC-1 wire form of one corpus message.
fn occ1_wire(message: &AgentCommMessage) -> String {
    message
        .to_occ1()
        .unwrap_or_else(|error| panic!("to_occ1: {error}"))
        .encode()
        .unwrap_or_else(|error| panic!("encode: {error}"))
}

/// The dual wire form (symbolic + sentinel + OCC-1) of one corpus message.
fn dual_wire(message: &AgentCommMessage, card: &DialectCard) -> String {
    occ2::encode_dual(message, card).unwrap_or_else(|error| panic!("encode_dual: {error}"))
}

fn sym_v1_card() -> DialectCard {
    DialectCard::sym_v1()
}

/// An enabled send policy pinned to sym-v1 at the default transport bound.
fn enabled_policy() -> Occ2SendPolicy {
    Occ2SendPolicy::for_dialect(
        DialectRegistry::with_sym_v1(),
        ("sym-v1".to_string(), 1),
        DEFAULT_AGENT_MESSAGE_MAX_CHARS,
    )
}

// ---------------------------------------------------------------------------
// t01 - corpus shape and declared holdout split
// ---------------------------------------------------------------------------

#[test]
fn t01_corpus_shape_and_declared_holdout() {
    let corpus = load_corpus();
    assert_eq!(corpus.len(), 69, "the corpus carries 69 messages");
    let holdout = corpus.iter().filter(|entry| entry.holdout).count();
    assert_eq!(holdout, 18, "the corpus declares 18 holdout messages");

    let mut ids = BTreeSet::new();
    for entry in &corpus {
        assert!(
            ids.insert(entry.id.clone()),
            "duplicate corpus id {:?}",
            entry.id
        );
        assert!(
            entry.id.starts_with('c') && entry.id[1..].chars().all(|c| c.is_ascii_digit()),
            "corpus id {:?} must be cNN",
            entry.id
        );
        for (index, evidence) in entry.payload.evidence.iter().enumerate() {
            assert_eq!(
                evidence.id,
                format!("E{}", index + 1),
                "{}: evidence ids must be E1..En in order",
                entry.id
            );
        }
        build_message(&entry.payload)
            .validate()
            .unwrap_or_else(|error| panic!("{} validate: {error}", entry.id));
    }

    // Coverage anchors across the full kind/status surface.
    let messages: Vec<AgentCommMessage> = corpus.iter().map(|e| build_message(&e.payload)).collect();
    let kinds: BTreeSet<&str> = messages.iter().map(|m| m.kind.as_str()).collect();
    for required in [
        "TASK",
        "PROGRESS",
        "RESULT",
        "EVIDENCE",
        "REQUEST_DETAIL",
        "ACK",
        "CORRECTION",
        "BLOCKED",
        "CANCELLED",
    ] {
        assert!(kinds.contains(required), "no corpus message with kind {required}");
    }
    let statuses: BTreeSet<&str> = messages.iter().map(|m| m.status.as_str()).collect();
    for required in ["pending", "in_progress", "done", "failed", "blocked", "cancelled", "partial"] {
        assert!(statuses.contains(required), "no corpus message with status {required}");
    }
    let verification: BTreeSet<&str> = messages
        .iter()
        .filter_map(|m| m.verification.as_ref())
        .map(|v| v.status.as_str())
        .collect();
    for required in ["pass", "fail", "unknown"] {
        assert!(verification.contains(required), "no verification-{required} message");
    }
    println!("corpus: {} messages, {} holdout, coverage anchors OK", corpus.len(), holdout);
}

// ---------------------------------------------------------------------------
// t02 - dual round-trip fidelity (train + holdout)
// ---------------------------------------------------------------------------

#[test]
fn t02_dual_round_trip_all_corpus() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let registry = DialectRegistry::with_sym_v1();
    for entry in &corpus {
        let message = build_message(&entry.payload);
        let dual = dual_wire(&message, &card);

        // Wire shape: dialect header, sentinel, OCC-1 tail.
        assert!(dual.starts_with("OCC/2 D=sym-v1@1 C="), "{} header", entry.id);
        let sentinel_line = format!("\n{}\n", occ2::OCC2_FALLBACK_SENTINEL);
        let sentinel = dual
            .find(&sentinel_line)
            .unwrap_or_else(|| panic!("{}: no sentinel section", entry.id));
        assert!(
            dual[0..sentinel].starts_with("OCC/2"),
            "{}: symbolic section precedes the sentinel",
            entry.id
        );
        assert!(
            dual[sentinel + sentinel_line.len()..].starts_with("OCC/1\n"),
            "{}: OCC-1 fallback section follows the sentinel",
            entry.id
        );

        // Strict decode (verifies both sections agree).
        let decoded = occ2::decode(&dual, &registry)
            .unwrap_or_else(|error| panic!("{} strict decode: {error}", entry.id));
        assert_eq!(decoded, message, "{} strict round trip", entry.id);

        // Conservative fallback decode from the embedded OCC-1 section.
        let via_fallback = occ2::occ1_fallback(&dual)
            .unwrap_or_else(|error| panic!("{} occ1_fallback: {error}", entry.id));
        assert_eq!(via_fallback, message, "{} fallback round trip", entry.id);

        // Evidence, next-action and uncertainty preservation.
        assert_eq!(decoded.evidence, message.evidence, "{} evidence", entry.id);
        assert_eq!(decoded.next_actions, message.next_actions, "{} next actions", entry.id);
        assert_eq!(decoded.verification, message.verification, "{} verification", entry.id);
        for (left, right) in decoded.findings.iter().zip(message.findings.iter()) {
            assert_eq!(left.confidence, right.confidence, "{} confidence", entry.id);
            assert_eq!(left.evidence, right.evidence, "{} finding evidence", entry.id);
        }
    }
    let holdout = corpus.iter().filter(|e| e.holdout).count();
    println!(
        "dual round trip: {}/{} exact (train {}, holdout {})",
        corpus.len(),
        corpus.len(),
        corpus.len() - holdout,
        holdout
    );
}

// ---------------------------------------------------------------------------
// t03 - size comparison
// ---------------------------------------------------------------------------

fn token_proxy(text: &str) -> usize {
    let mut count = 0usize;
    let mut in_token = false;
    for character in text.chars() {
        if character.is_whitespace() {
            in_token = false;
        } else if matches!(character, '.' | ',' | ':' | ';' | '=') {
            count += 1;
            in_token = false;
        } else if !in_token {
            count += 1;
            in_token = true;
        }
    }
    count
}

#[derive(Debug, Clone, Copy, Default)]
struct Encoded {
    bytes: usize,
    tokens: usize,
}

impl Encoded {
    fn measure(text: &str) -> Self {
        Encoded { bytes: text.len(), tokens: token_proxy(text) }
    }

    fn add(&mut self, other: &Encoded) {
        self.bytes += other.bytes;
        self.tokens += other.tokens;
    }
}

#[test]
fn t03_symbolic_size_comparison() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let mut totals: BTreeMap<&str, Encoded> = BTreeMap::new();
    let mut split_totals: BTreeMap<(bool, &str), Encoded> = BTreeMap::new();
    let mut symbolic_le_occ1 = 0usize;
    let mut dual_le_bound = 0usize;
    for entry in &corpus {
        let message = build_message(&entry.payload);
        let symbolic = occ2::encode(&message, &card).expect("encode");
        let dual = dual_wire(&message, &card);
        let occ1 = occ1_wire(&message);
        let json = serde_json::to_string(&message).expect("serialize");
        for (label, text) in [
            ("plain", entry.plain_english.as_str()),
            ("json", json.as_str()),
            ("occ1", occ1.as_str()),
            ("symbolic", symbolic.as_str()),
            ("dual", dual.as_str()),
        ] {
            let encoded = Encoded::measure(text);
            totals.entry(label).or_default().add(&encoded);
            split_totals
                .entry((entry.holdout, label))
                .or_default()
                .add(&encoded);
        }
        if symbolic.len() <= occ1.len() {
            symbolic_le_occ1 += 1;
        }
        if dual.chars().count() <= DEFAULT_AGENT_MESSAGE_MAX_CHARS {
            dual_le_bound += 1;
        }
    }
    for (label, encoded) in &totals {
        println!("{:<9} {:>10} bytes {:>10} proxy tokens", label, encoded.bytes, encoded.tokens);
    }
    for holdout in [false, true] {
        let label = if holdout { "holdout" } else { "train" };
        println!(
            "{:<8} plain {} B, json {} B, occ1 {} B, symbolic {} B, dual {} B",
            label,
            split_totals.get(&(holdout, "plain")).map(|e| e.bytes).unwrap_or(0),
            split_totals.get(&(holdout, "json")).map(|e| e.bytes).unwrap_or(0),
            split_totals.get(&(holdout, "occ1")).map(|e| e.bytes).unwrap_or(0),
            split_totals.get(&(holdout, "symbolic")).map(|e| e.bytes).unwrap_or(0),
            split_totals.get(&(holdout, "dual")).map(|e| e.bytes).unwrap_or(0),
        );
    }
    let symbolic = totals["symbolic"];
    let occ1 = totals["occ1"];
    let plain = totals["plain"];
    let dual = totals["dual"];
    // The symbolic section must save bytes against OCC-1 on the aggregate,
    // on both splits (no threshold tuning: the direction is structural).
    for holdout in [false, true] {
        let symbolic = split_totals.get(&(holdout, "symbolic")).unwrap().bytes;
        let occ1 = split_totals.get(&(holdout, "occ1")).unwrap().bytes;
        assert!(
            symbolic < occ1,
            "{split} symbolic ({symbolic} B) must undercut occ1 ({occ1} B)",
            split = if holdout { "holdout" } else { "train" }
        );
    }
    assert!(symbolic.bytes < occ1.bytes, "aggregate symbolic must undercut occ1");
    assert!(symbolic.bytes < plain.bytes, "aggregate symbolic must undercut plain English");
    // A dual payload is structurally symbolic + sentinel + OCC-1.
    assert!(dual.bytes > symbolic.bytes);
    assert!(dual.bytes > occ1.bytes);
    println!(
        "symbolic <= occ1 per message: {}/{}, dual within the 16,384-char transport bound: {}/{}",
        symbolic_le_occ1,
        corpus.len(),
        dual_le_bound,
        corpus.len()
    );
}

// ---------------------------------------------------------------------------
// t04 - fault injection (unknown dialect, hash mismatch, corruption)
// ---------------------------------------------------------------------------

fn replace_first(text: &str, from: &str, to: &str) -> String {
    text.replacen(from, to, 1)
}

/// Replaces the 12-hex codebook prefix of the dialect header with zeros.
fn corrupt_hash_prefix(dual: &str) -> String {
    let marker = dual.find(" C=").expect("codebook token in the header");
    let prefix_start = marker + 3;
    let prefix_len = dual[prefix_start..]
        .char_indices()
        .find(|(_, c)| *c == ' ' || *c == '\n')
        .map(|(i, _)| i)
        .unwrap_or(dual.len() - prefix_start);
    format!(
        "{}{}{}",
        &dual[..prefix_start],
        "0".repeat(prefix_len),
        &dual[prefix_start + prefix_len..]
    )
}

/// Builds a registry holding a tampered sym-v1 card under the same
/// (dialect, version) key: resolving the real header then reports
/// HashMismatch.
fn tampered_registry() -> DialectRegistry {
    let mut tampered = DialectCard::sym_v1();
    tampered.symbol_table.slots.insert("task_id".to_string(), "Z".to_string());
    tampered.sha256 = codebook_sha256(&tampered.grammar, &tampered.symbol_table);
    let mut registry = DialectRegistry::new();
    registry.insert(tampered).expect("tampered card registers");
    registry
}

#[test]
fn t04_fault_injection_never_silently_passes() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let empty = DialectRegistry::new();
    let mismatch = tampered_registry();
    let mut unknown_rejected = 0usize;
    let mut hash_rejected = 0usize;
    let mut corrupted_rejected = 0usize;
    let mut recovered_via_fallback = 0usize;
    for entry in &corpus {
        let message = build_message(&entry.payload);
        let dual = dual_wire(&message, &card);

        // Unknown dialect: an empty registry must refuse the strict path
        // and recover through the embedded OCC-1 section.
        match occ2::decode(&dual, &empty) {
            Err(occ2::Occ2Error::UnknownDialect { .. }) => {
                unknown_rejected += 1;
            }
            other => panic!("{}: unknown dialect must reject, got {other:?}", entry.id),
        }
        assert_eq!(
            occ2::occ1_fallback(&dual).expect("fallback recovers"),
            message,
            "{}: fallback after unknown dialect",
            entry.id
        );
        recovered_via_fallback += 1;

        // Hash mismatch: a registered-but-different codebook is corruption,
        // not a different dialect.
        match occ2::decode(&dual, &mismatch) {
            Err(occ2::Occ2Error::HashMismatch { .. }) => {
                hash_rejected += 1;
            }
            other => panic!("{}: hash mismatch must reject, got {other:?}", entry.id),
        }
        assert_eq!(
            occ2::occ1_fallback(&dual).expect("fallback recovers"),
            message,
            "{}: fallback after hash mismatch",
            entry.id
        );

        // Wire corruption, on the symbolic half: the strict path must fail
        // or differ, never silently decode to the original, and never
        // panic. The intact OCC-1 section still recovers the payload.
        let newline = dual.find('\n').expect("header terminator");
        let corruptions = [
            ("header", replace_first(&dual, "OCC/2 D=sym-v1@1", "OCC/2 D=sym-v9@1")),
            ("version", replace_first(&dual, "D=sym-v1@1", "D=sym-v1@2")),
            ("hash", corrupt_hash_prefix(&dual)),
            ("symbol line", {
                let second = dual[newline + 1..].find('\n').map(|i| newline + 1 + i).unwrap_or(dual.len());
                let flipped = replace_first(&dual[second + 1..].to_string(), "=", "#");
                format!("{}{}", &dual[..second + 1], flipped)
            }),
            ("truncated symbolic", {
                let sentinel = dual.find(&format!("\n{}", occ2::OCC2_FALLBACK_SENTINEL)).expect("sentinel");
                dual[..sentinel / 2].to_string()
            }),
            ("sentinel removed", replace_first(&dual, &format!("\n{}", occ2::OCC2_FALLBACK_SENTINEL), "")),
        ];
        for (label, corrupted) in corruptions {
            assert_ne!(corrupted, dual, "{}/{label}: corruption is a no-op", entry.id);
            match occ2::decode(&corrupted, &DialectRegistry::with_sym_v1()) {
                Err(_) => corrupted_rejected += 1,
                Ok(decoded) => {
                    assert_ne!(
                        decoded, message,
                        "{}/{label}: corruption decoded to the original",
                        entry.id
                    );
                }
            }
            // The OCC-1 fallback either recovers the intact section or fails
            // loudly; it never returns a different payload.
            if let Ok(recovered) = occ2::occ1_fallback(&corrupted) {
                assert_eq!(recovered, message, "{}/{label}: fallback changed the payload", entry.id);
            }
        }

        // Corruption on the fallback half: recovery must fail, never
        // return a different message.
        let occ1_start = dual
            .find(&format!("\n{}\n", occ2::OCC2_FALLBACK_SENTINEL))
            .map(|i| i + occ2::OCC2_FALLBACK_SENTINEL.len() + 2)
            .expect("fallback section");
        let mut broken_fallback = dual.clone();
        broken_fallback.replace_range(occ1_start + 2..occ1_start + 3, "#");
        assert!(
            occ2::decode(&broken_fallback, &DialectRegistry::with_sym_v1()).is_err()
                || occ2::decode(&broken_fallback, &DialectRegistry::with_sym_v1()).unwrap() != message,
            "{}: broken fallback must not strict-decode to the original",
            entry.id
        );
        assert!(
            occ2::occ1_fallback(&broken_fallback).is_err(),
            "{}: broken OCC-1 section must not decode",
            entry.id
        );
    }
    println!(
        "fault injection: {} unknown-dialect rejections, {} hash-mismatch rejections, {} corruption rejections, {} fallback recoveries",
        unknown_rejected, hash_rejected, corrupted_rejected, recovered_via_fallback
    );
}

// ---------------------------------------------------------------------------
// t05 - wrap policy identity, delta exclusion and the size guard
// ---------------------------------------------------------------------------

#[test]
fn t05_wrap_policy_identity_and_size_guard() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let policy = enabled_policy();
    let mut wrapped = 0usize;
    for entry in &corpus {
        let message = build_message(&entry.payload);
        let occ1 = occ1_wire(&message);

        // Disabled policy: byte-identical identity for every body.
        assert_eq!(wrap_outgoing_body(&occ1, &Occ2SendPolicy::disabled()), occ1);

        // Enabled policy: the corpus OCC-1 bodies become dual payloads.
        let dual = wrap_outgoing_body(&occ1, &policy);
        assert_eq!(dual, dual_wire(&message, &card), "{} wrap", entry.id);
        assert_ne!(dual, occ1, "{}: wrap must change the body", entry.id);
        wrapped += 1;

        // Plain English passes through unchanged.
        assert_eq!(
            wrap_outgoing_body(&entry.plain_english, &policy),
            entry.plain_english,
            "{}: plain English unchanged",
            entry.id
        );
    }
    assert_eq!(wrapped, corpus.len());

    // A delta body (no KIND) is excluded from wrapping in v1 (OQ4).
    let delta_body = "OCC/1\nTASK=t\nSEQ=2\nBASE=1\nSTATUS=done";
    assert_eq!(wrap_outgoing_body(delta_body, &policy), delta_body);

    // An enabled policy whose registry lacks the dialect never wraps.
    let missing = Occ2SendPolicy::for_dialect(
        DialectRegistry::new(),
        ("sym-v1".to_string(), 1),
        DEFAULT_AGENT_MESSAGE_MAX_CHARS,
    );
    let sample = occ1_wire(&build_message(&corpus[0].payload));
    assert_eq!(wrap_outgoing_body(&sample, &missing), sample);

    // R1 size guard: a body near the bound whose dual form would exceed it
    // is sent as plain OCC-1, never truncated.
    let mut big = AgentCommMessage::new("RESULT", "t-big", "done");
    for index in 0..6 {
        big.findings.push(pi_coding_agent::core::occ::contract::Finding {
            id: format!("F{}", index + 1),
            statement: "x".repeat(2_500),
            confidence: None,
            evidence: Vec::new(),
        });
    }
    let big_occ1 = occ1_wire(&big);
    assert!(big_occ1.chars().count() <= DEFAULT_AGENT_MESSAGE_MAX_CHARS);
    assert!(
        dual_wire(&big, &card).chars().count() > DEFAULT_AGENT_MESSAGE_MAX_CHARS,
        "the dual form must exceed the bound for the guard to fire"
    );
    let guarded = wrap_outgoing_body(&big_occ1, &policy);
    assert_eq!(guarded, big_occ1, "the size guard sends the plain OCC-1 body");
}

// ---------------------------------------------------------------------------
// t06 - prompt seam: sentinel split and prompt-id round trip
// ---------------------------------------------------------------------------

fn sample_payload(message: &str) -> AgentSessionMessagePayload {
    AgentSessionMessagePayload {
        id: "agentmsg_1".to_string(),
        source: AGENT_MESSAGE_SOURCE.to_string(),
        message: message.to_string(),
        from: Some(AgentSessionMessageSender {
            active_session_id: Some("active-1".to_string()),
            session_id: Some("session-1".to_string()),
            session_name: Some("parent".to_string()),
            ..Default::default()
        }),
        from_relationship: None,
        target: AgentSessionMessageEndpoint {
            active_session_id: "active-2".to_string(),
            session_id: "session-2".to_string(),
            session_name: Some("child".to_string()),
            runtime_kind: None,
        },
    }
}

#[test]
fn t06_prompt_sentinel_split_and_record_verbatim() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    for entry in corpus.iter().step_by(7) {
        let message = build_message(&entry.payload);
        let occ1 = occ1_wire(&message);
        let dual = dual_wire(&message, &card);
        let rendered = agent_session_message_prompt_body(&dual);
        let (legend, symbolic) = rendered.split_once("\n\n").expect("codebook precedes symbolic body");
        assert!(legend.contains("T:task_id") && legend.contains("F:finding.statement"));
        assert_eq!(symbolic.to_string() + "\n#FALLBACK-OCC1\n" + &occ1, dual);
        let unknown = dual.replace("D=sym-v1@1", "D=unknown@1");
        assert_eq!(agent_session_message_prompt_body(&unknown), occ1);

        // Dual body: the prompt shows the codebook and symbolic section only.
        let prompt = create_agent_session_message_prompt(&sample_payload(&dual));
        assert!(prompt.contains("OCC/2 D=sym-v1@1 C="), "{}: symbolic header", entry.id);
        assert!(!prompt.contains(occ2::OCC2_FALLBACK_SENTINEL), "{}: no sentinel", entry.id);
        assert!(!prompt.contains("\nOCC/1\n"), "{}: no OCC-1 section", entry.id);
        assert_eq!(
            parse_agent_session_message_prompt_id(&prompt).as_deref(),
            Some("agentmsg_1"),
            "{}: prompt-id framing survives",
            entry.id
        );

        // The durable record stores the dual payload verbatim.
        let record = create_agent_session_message(&sample_payload(&dual), 5);
        let details = match &record {
            pi_agent_core::types::CustomAgentMessage::Custom { details: Some(details), .. } => details,
            _ => panic!("agent session message is a custom message"),
        };
        assert_eq!(details["message"].as_str(), Some(dual.as_str()), "{}: dual record", entry.id);

        // Plain and OCC-1 bodies render byte-identically to the framed form.
        for body in [entry.plain_english.clone(), occ1.clone()] {
            let prompt = create_agent_session_message_prompt(&sample_payload(&body));
            let expected = format!(
                "Agent-to-agent message received.\nSource: {AGENT_MESSAGE_SOURCE}\nFrom: parent, active active-1, session session-1\nTo: child, active active-2, session session-2\nMessage id: agentmsg_1\n\n{body}"
            );
            assert_eq!(prompt, expected, "{}: plain framing", entry.id);
        }
    }
}

// ---------------------------------------------------------------------------
// t07 - negotiation and capability absence
// ---------------------------------------------------------------------------

#[test]
fn t07_negotiation_absence_keeps_occ1() {
    let local = OccCapability::here();
    let registry = DialectRegistry::with_sym_v1();
    assert_eq!(
        select_occ2_dialect(&local, Some(&local), &registry, true),
        Some(("sym-v1".to_string(), 1))
    );
    // A target that reports nothing: OCC-1.
    assert_eq!(select_occ2_dialect(&local, None, &registry, true), None);
    // A target that speaks OCC-1 only: OCC-1.
    let occ1_only = OccCapability { occ1: true, dialects: BTreeMap::new() };
    assert_eq!(select_occ2_dialect(&local, Some(&occ1_only), &registry, true), None);
    // A target that knows a different dialect: OCC-1.
    let other = OccCapability {
        occ1: true,
        dialects: BTreeMap::from([("sym-x1".to_string(), BTreeSet::from([1]))]),
    };
    assert_eq!(select_occ2_dialect(&local, Some(&other), &registry, true), None);
    // Unevaluated cards are refused without the dev override (tier 2).
    assert_eq!(select_occ2_dialect(&local, Some(&local), &registry, false), None);

    // The optional summary field: absent serializes without the key and
    // means OCC-1; present round-trips through serde.
    let absent = AgentSessionMessageAgentSummary::default();
    assert!(absent.occ_capability.is_none());
    let serialized = serde_json::to_value(&absent).unwrap();
    assert!(serialized.get("occCapability").is_none());
    let advertised = AgentSessionMessageAgentSummary {
        occ_capability: Some(local.clone()),
        ..absent.clone()
    };
    let serialized = serde_json::to_value(&advertised).unwrap();
    assert_eq!(serialized["occCapability"]["occ1"], serde_json::json!(true));
    let round: AgentSessionMessageAgentSummary = serde_json::from_value(serialized).unwrap();
    assert_eq!(round.occ_capability, Some(local));
}

// ---------------------------------------------------------------------------
// t08 - durability and compaction survival
// ---------------------------------------------------------------------------

#[test]
fn t08_durable_record_and_compaction_survival() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let registry = DialectRegistry::with_sym_v1();
    let empty = DialectRegistry::new();
    let mut ledger = Occ2StrikeLedger::new();
    let pair = "sender->listener";
    for entry in &corpus {
        let message = build_message(&entry.payload);
        let dual = dual_wire(&message, &card);

        // Encode -> build the durable record -> serialize the custom
        // message -> deserialize -> decode: the typed payload survives the
        // whole storage round trip (OQ1: the summarizer consumes content,
        // the transcript entry keeps the dual body in details).
        let record = create_agent_session_message(&sample_payload(&dual), 5);
        let serialized = serde_json::to_value(&record).unwrap();
        let deserialized: pi_agent_core::types::CustomAgentMessage =
            serde_json::from_value(serialized).unwrap();
        let details = match &deserialized {
            pi_agent_core::types::CustomAgentMessage::Custom { details: Some(details), .. } => {
                details.clone()
            }
            _ => panic!("agent session message is a custom message"),
        };
        let stored = details["message"].as_str().expect("message detail");
        assert_eq!(stored, dual, "{}: dual body stored verbatim", entry.id);
        let decoded = decode_agent_session_message_body(stored, &registry, pair, &mut ledger)
            .unwrap_or_else(|error| panic!("{} decode: {error}", entry.id));
        assert_eq!(decoded.message(), &message, "{}: durable decode", entry.id);

        // Daemon restart with an empty registry: the OCC-1 fallback section
        // still reconstructs the typed payload. A separate ledger models
        // the restarted session's state.
        let mut restart_ledger = Occ2StrikeLedger::new();
        let recovered = decode_agent_session_message_body(stored, &empty, pair, &mut restart_ledger)
            .unwrap_or_else(|error| panic!("{} fallback decode: {error}", entry.id));
        assert_eq!(recovered.message(), &message, "{}: restart recovery", entry.id);
    }
    assert_eq!(ledger.incidents(pair), 0, "registered dialects decode strictly");
}

// ---------------------------------------------------------------------------
// t09 - telemetry: the classify strict arm
// ---------------------------------------------------------------------------

#[test]
fn t09_classify_strict_symbolic_arm() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let empty = DialectRegistry::new();
    let mismatch = tampered_registry();
    let mut symbolic = 0usize;
    for entry in corpus.iter().step_by(5) {
        let message = build_message(&entry.payload);
        let dual = dual_wire(&message, &card);
        let symbolic_only = occ2::encode(&message, &card).expect("encode");

        // Dual and symbolic-only bodies report Symbolic: they decoded
        // through the symbolic path.
        assert_eq!(classify(&dual), CommunicationKind::Symbolic, "{}", entry.id);
        assert_eq!(classify(&symbolic_only), CommunicationKind::Symbolic, "{}", entry.id);
        symbolic += 1;

        // Unknown dialect or hash mismatch consumed via the fallback: Occ1.
        assert_eq!(classify_with_registry(&dual, &empty), CommunicationKind::Occ1, "{}", entry.id);
        assert_eq!(classify_with_registry(&dual, &mismatch), CommunicationKind::Occ1, "{}", entry.id);
    }
    // Malformed OCC/2, plain text and OCC-1 keep their pinned classes.
    assert_eq!(classify("OCC/2\nSYMBOL=x"), CommunicationKind::Text);
    assert_eq!(classify("OCC/2"), CommunicationKind::Text);
    assert_eq!(classify("latent vector"), CommunicationKind::Text);
    assert_eq!(classify("OCC/1\nTASK=t\nKIND=RESULT\nSTATUS=done"), CommunicationKind::Occ1);
    println!("classify strict arm: {} sampled bodies report Symbolic", symbolic);
}

// ---------------------------------------------------------------------------
// t10 - three-strike session disable
// ---------------------------------------------------------------------------

#[test]
fn t10_three_strike_session_disable() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let empty = DialectRegistry::new();
    let pair = "speaker->listener";
    let mut ledger = Occ2StrikeLedger::new();
    let sample = &corpus[3];
    let message = build_message(&sample.payload);
    let dual = dual_wire(&message, &card);

    // Two failures: incidents recorded, delivery continues through OCC-1.
    for expected in 1..=2 {
        let decoded = decode_agent_session_message_body(&dual, &empty, pair, &mut ledger)
            .expect("fallback delivery");
        assert!(!decoded.is_symbolic());
        assert_eq!(decoded.message(), &message);
        assert_eq!(ledger.incidents(pair), expected);
        assert!(!ledger.is_disabled(pair));
    }
    // Third failure: the pair disables for the session.
    assert!(ledger.record_incident(pair) || {
        decode_agent_session_message_body(&dual, &empty, pair, &mut ledger).is_ok()
    });
    assert!(ledger.is_disabled(pair), "three strikes disable the pair");
    assert_eq!(ledger.disabled_pairs(), vec![pair]);
    // Disabled pairs still deliver, through the OCC-1 fallback, with no
    // further strikes recorded.
    let decoded = decode_agent_session_message_body(&dual, &empty, pair, &mut ledger)
        .expect("delivery continues after disable");
    assert!(!decoded.is_symbolic());
    assert_eq!(decoded.message(), &message);
    assert_eq!(ledger.incidents(pair), 3);
    // Other pairs are unaffected.
    let other = decode_agent_session_message_body(&dual, &empty, "a->b", &mut ledger).unwrap();
    assert!(!other.is_symbolic());
    assert_eq!(ledger.incidents("a->b"), 1);
    assert!(!ledger.is_disabled("a->b"));
}

// ---------------------------------------------------------------------------
// t11 - latency budget
// ---------------------------------------------------------------------------

#[test]
fn t11_latency_within_budget() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let registry = DialectRegistry::with_sym_v1();
    let mut worst_encode = Duration::ZERO;
    let mut worst_decode = Duration::ZERO;
    let mut encode_sum = Duration::ZERO;
    let mut decode_sum = Duration::ZERO;
    for entry in &corpus {
        let message = build_message(&entry.payload);
        let dual = dual_wire(&message, &card);

        let start = Instant::now();
        for _ in 0..LATENCY_ITERATIONS {
            let encoded = dual_wire(&message, &card);
            std::hint::black_box(&encoded);
        }
        let encode_mean = start.elapsed() / LATENCY_ITERATIONS;

        let start = Instant::now();
        for _ in 0..LATENCY_ITERATIONS {
            let decoded = occ2::decode(&dual, &registry).expect("decode");
            std::hint::black_box(&decoded);
        }
        let decode_mean = start.elapsed() / LATENCY_ITERATIONS;

        encode_sum += encode_mean;
        decode_sum += decode_mean;
        worst_encode = worst_encode.max(encode_mean);
        worst_decode = worst_decode.max(decode_mean);
        assert!(encode_mean < LATENCY_BUDGET, "{}: encode mean {encode_mean:?}", entry.id);
        assert!(decode_mean < LATENCY_BUDGET, "{}: decode mean {decode_mean:?}", entry.id);
    }
    let count = corpus.len() as u32;
    println!(
        "latency: dual encode mean-of-means {:>7.1} us (worst {:>7.1} us), decode mean-of-means {:>7.1} us (worst {:>7.1} us), budget {} us",
        (encode_sum / count).as_secs_f64() * 1e6,
        worst_encode.as_secs_f64() * 1e6,
        (decode_sum / count).as_secs_f64() * 1e6,
        worst_decode.as_secs_f64() * 1e6,
        LATENCY_BUDGET.as_secs_f64() * 1e6
    );
}

// ---------------------------------------------------------------------------
// t12 - aggregate headline report
// ---------------------------------------------------------------------------

#[test]
fn t12_aggregate_headline_report() {
    let corpus = load_corpus();
    let card = sym_v1_card();
    let mut plain = Encoded::default();
    let mut json = Encoded::default();
    let mut occ1 = Encoded::default();
    let mut symbolic = Encoded::default();
    let mut dual = Encoded::default();
    for entry in &corpus {
        let message = build_message(&entry.payload);
        plain.add(&Encoded::measure(&entry.plain_english));
        json.add(&Encoded::measure(&serde_json::to_string(&message).unwrap()));
        occ1.add(&Encoded::measure(&occ1_wire(&message)));
        symbolic.add(&Encoded::measure(&occ2::encode(&message, &card).unwrap()));
        dual.add(&Encoded::measure(&dual_wire(&message, &card)));
    }
    println!();
    println!(
        "==== OCC-2 communication-fidelity aggregate: {} messages, {} holdout ====",
        corpus.len(),
        corpus.iter().filter(|e| e.holdout).count()
    );
    println!("{:<10} {:>12} {:>14}", "encoding", "total bytes", "proxy tokens");
    for (label, encoded) in [
        ("plain", &plain),
        ("json", &json),
        ("occ1", &occ1),
        ("symbolic", &symbolic),
        ("dual", &dual),
    ] {
        println!("{:<10} {:>12} {:>14}", label, encoded.bytes, encoded.tokens);
    }
    let pct = |a: &Encoded, b: &Encoded| 100.0 * (b.bytes as f64 - a.bytes as f64) / b.bytes as f64;
    println!(
        "symbolic vs occ1:  {:+.1}% bytes, {:+.1}% proxy tokens",
        pct(&symbolic, &occ1),
        100.0 * (occ1.tokens as f64 - symbolic.tokens as f64) / occ1.tokens as f64
    );
    println!(
        "symbolic vs plain: {:+.1}% bytes, {:+.1}% proxy tokens",
        pct(&symbolic, &plain),
        100.0 * (plain.tokens as f64 - symbolic.tokens as f64) / plain.tokens as f64
    );
    println!(
        "listener context (symbolic) vs sender wire (dual): {:+.1}% bytes",
        pct(&symbolic, &dual)
    );
    assert!(symbolic.bytes < occ1.bytes);
    assert!(symbolic.bytes < plain.bytes);
    assert!(symbolic.bytes < dual.bytes);
}
