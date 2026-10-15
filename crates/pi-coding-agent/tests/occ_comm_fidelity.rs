//! Offline, deterministic communication-fidelity suite for OCC (worker E).
//!
//! Exercises the public OCC API (`pi_coding_agent::core::occ::{occ1,
//! contract}`) end to end against the scenario fixture
//! `tests/fixtures/occ/scenarios.json`:
//!
//! - `t01` fixture shape: scenario count, holdout rule, coverage anchors.
//! - `t02` round-trip fidelity: validate -> to_occ1 -> encode -> decode ->
//!   from_occ1 -> exact `PartialEq` equality for every scenario, plus
//!   explicit evidence / next-action / uncertainty preservation checks.
//! - `t03` encoding-size comparison: plain English vs concise English vs
//!   JSON vs OCC-1, per scenario and aggregate.
//! - `t04` fault injection: systematic wire corruption must never decode to
//!   the original message and never panic.
//! - `t05` semantic faults: header-valid but semantically broken OCC-1 must
//!   be rejected with `ContractError`, never panic.
//! - `t06` latency: per-scenario mean encode/decode time over 100 runs.
//! - `t07` aggregate headline table (libtest runs tests in name order under
//!   `--test-threads=1`, so the `tNN` prefixes make this the final test).
//!
//! Holdout rule (deterministic, no inspection of results): the numeric
//! suffix of the scenario id decides. Ids whose number is divisible by 4
//! (s04, s08, s12, s16, s20, s24, s28) carry `"holdout": true` in the
//! fixture, exactly one quarter of the suite. Holdout scenarios are never
//! used to tune any threshold in this file: every assertion applies
//! identically to train and holdout rows, and the comparison tables report
//! both splits separately so overfitting would be visible.
//!
//! Everything here is offline and deterministic: no network, no model APIs,
//! no credentials, no randomness, no filesystem writes. The only filesystem
//! access is reading the fixture; the only clock is `Instant`, used solely
//! for the latency budget.

use std::collections::{BTreeMap, BTreeSet};
use std::time::{Duration, Instant};

use pi_coding_agent::core::occ::contract::{
    AgentCommMessage, ContractError, EvidenceRef, Finding, Verification,
};
use pi_coding_agent::core::occ::occ1::{Occ1Error, Occ1Message, Occ1Dialect};
use serde::Deserialize;

const SCENARIOS_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/occ/scenarios.json");

/// Latency budget: mean per-scenario encode or decode time must stay below
/// this. Generous bound chosen for a loaded shared CI runner.
const LATENCY_BUDGET: Duration = Duration::from_millis(5);
/// Iterations per scenario for the latency measurement.
const LATENCY_ITERATIONS: u32 = 100;

// ---------------------------------------------------------------------------
// Fixture model
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct Scenario {
    id: String,
    holdout: bool,
    plain_english: String,
    payload: ScenarioPayload,
}

/// The typed payload mirrors `AgentCommMessage` minus the `protocol` field,
/// which the harness stamps via `AgentCommMessage::new` (the fixture never
/// carries protocol identity; parsers must not accept it from text).
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
    evidence: Vec<EvidenceRef>,
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

/// Fixture findings carry no id: the contract assigns positional ids `F1..`
/// and `from_occ1` regenerates them positionally, so ids are derived here.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ScenarioFinding {
    statement: String,
    #[serde(default)]
    confidence: Option<f32>,
    #[serde(default)]
    evidence: Vec<String>,
}

fn load_scenarios() -> Vec<Scenario> {
    let text = std::fs::read_to_string(SCENARIOS_PATH)
        .unwrap_or_else(|error| panic!("read {SCENARIOS_PATH}: {error}"));
    serde_json::from_str(&text)
        .unwrap_or_else(|error| panic!("parse {SCENARIOS_PATH}: {error}"))
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

/// Encoded wire form of a scenario payload, validated and unwrapped.
fn occ1_wire(message: &AgentCommMessage) -> String {
    message
        .to_occ1()
        .unwrap_or_else(|error| panic!("to_occ1: {error}"))
        .encode()
        .unwrap_or_else(|error| panic!("encode: {error}"))
}

fn scenario_number(id: &str) -> u32 {
    id[1..]
        .parse()
        .unwrap_or_else(|error| panic!("scenario id {id:?} is not sNN: {error}"))
}

// ---------------------------------------------------------------------------
// Concise-English renderer and the token proxy
// ---------------------------------------------------------------------------

/// Deterministic concise-English renderer for one typed payload, in the
/// style `Task t42 done. F1: ... (conf 0.900, E1). Verify: claim=pass.`.
/// Digests are abbreviated to their first 12 hex chars; the full digest
/// stays recoverable from the typed payload and the OCC-1 wire form.
fn render_concise(message: &AgentCommMessage) -> String {
    let mut lines: Vec<String> = Vec::new();
    let mut header = format!("Task {} {} ({}", message.task_id, message.status, message.kind);
    if let Some(sequence) = message.sequence {
        header.push_str(&format!(", seq {sequence}"));
    }
    header.push_str(").");
    lines.push(header);
    if message.sender_identity.is_some() || message.recipient_identity.is_some() {
        lines.push(format!(
            "From {} to {}.",
            message.sender_identity.as_deref().unwrap_or("-"),
            message.recipient_identity.as_deref().unwrap_or("-")
        ));
    }
    let mut routing = Vec::new();
    if let Some(parent) = &message.parent_task_id {
        routing.push(format!("parent {parent}"));
    }
    if let Some(trace) = &message.trace_id {
        routing.push(format!("trace {trace}"));
    }
    if !routing.is_empty() {
        lines.push(format!("{}.", routing.join(" ")));
    }
    for finding in &message.findings {
        let mut line = format!("{}: {}", finding.id, finding.statement);
        let mut parts: Vec<String> = Vec::new();
        if let Some(confidence) = finding.confidence {
            parts.push(format!("conf {confidence:.3}"));
        }
        if !finding.evidence.is_empty() {
            parts.push(format!("ev {}", finding.evidence.join(" ")));
        }
        if !parts.is_empty() {
            line.push_str(&format!(" ({})", parts.join(", ")));
        }
        line.push('.');
        lines.push(line);
    }
    for evidence in &message.evidence {
        let mut line = format!("{}: {} {}", evidence.id, evidence.kind, evidence.locator);
        if let Some((start, end)) = evidence.line_range {
            line.push_str(&format!(":{start}-{end}"));
        }
        if let Some(revision) = &evidence.revision {
            line.push_str(&format!(" rev {revision}"));
        }
        if let Some(digest) = &evidence.digest {
            line.push_str(&format!(" digest {}", digest.get(..12).unwrap_or(digest)));
        }
        match evidence.verified {
            Some(true) => line.push_str(" verified"),
            Some(false) => line.push_str(" unverified"),
            None => {}
        }
        line.push('.');
        lines.push(line);
    }
    if let Some(verification) = &message.verification {
        let mut line = format!(
            "Verify: {}={} ({})",
            verification.claim, verification.status, verification.method
        );
        if let Some(receipt) = &verification.receipt {
            line.push_str(&format!(", receipt {receipt}"));
        }
        line.push_str(").");
        lines.push(line);
    }
    if !message.dependencies.is_empty() {
        lines.push(format!("Deps: {}.", message.dependencies.join(", ")));
    }
    if !message.artifacts.is_empty() {
        lines.push(format!("Artifacts: {}.", message.artifacts.join(", ")));
    }
    if !message.next_actions.is_empty() {
        lines.push(format!("Next: {}.", message.next_actions.join("; ")));
    }
    if let Some(timestamp) = &message.timestamp {
        lines.push(format!("At {timestamp}."));
    }
    if let Some(expiry) = &message.expiry {
        lines.push(format!("Expires {expiry}."));
    }
    lines.join("\n")
}

/// Token PROXY, not a tokenizer measurement: a token is a maximal run of
/// characters that are neither whitespace nor one of `.,:;=`, and each such
/// punctuation character counts as one token of its own. This approximates
/// how sub-word tokenizers split on punctuation. Real tokenizer numbers
/// require an online run against the target model and are out of scope for
/// this offline suite; the proxy is only used for relative comparison.
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

// ---------------------------------------------------------------------------
// Size comparison model
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Default)]
struct Encoded {
    bytes: usize,
    chars: usize,
    tokens: usize,
}

impl Encoded {
    fn measure(text: &str) -> Self {
        Encoded {
            bytes: text.len(),
            chars: text.chars().count(),
            tokens: token_proxy(text),
        }
    }

    fn add(&mut self, other: &Encoded) {
        self.bytes += other.bytes;
        self.chars += other.chars;
        self.tokens += other.tokens;
    }

    fn reduction_vs(&self, other: &Encoded) -> f64 {
        if other.bytes == 0 {
            return 0.0;
        }
        100.0 * (other.bytes as f64 - self.bytes as f64) / other.bytes as f64
    }
}

struct Row {
    id: String,
    holdout: bool,
    plain: Encoded,
    concise: Encoded,
    json: Encoded,
    occ1: Encoded,
}

struct Summary {
    scenarios: usize,
    holdout: usize,
    plain: Encoded,
    concise: Encoded,
    json: Encoded,
    occ1: Encoded,
    occ1_le_plain: usize,
    occ1_le_json: usize,
}

fn compute_comparison() -> (Vec<Row>, Summary) {
    let scenarios = load_scenarios();
    let mut rows = Vec::with_capacity(scenarios.len());
    for scenario in &scenarios {
        let message = build_message(&scenario.payload);
        let wire = occ1_wire(&message);
        let json = serde_json::to_string(&message)
            .unwrap_or_else(|error| panic!("serialize {}: {error}", scenario.id));
        let concise = render_concise(&message);
        rows.push(Row {
            id: scenario.id.clone(),
            holdout: scenario.holdout,
            plain: Encoded::measure(&scenario.plain_english),
            concise: Encoded::measure(&concise),
            json: Encoded::measure(&json),
            occ1: Encoded::measure(&wire),
        });
    }
    let mut summary = Summary {
        scenarios: rows.len(),
        holdout: rows.iter().filter(|row| row.holdout).count(),
        plain: Encoded::default(),
        concise: Encoded::default(),
        json: Encoded::default(),
        occ1: Encoded::default(),
        occ1_le_plain: 0,
        occ1_le_json: 0,
    };
    for row in &rows {
        summary.plain.add(&row.plain);
        summary.concise.add(&row.concise);
        summary.json.add(&row.json);
        summary.occ1.add(&row.occ1);
        if row.occ1.bytes <= row.plain.bytes {
            summary.occ1_le_plain += 1;
        }
        if row.occ1.bytes <= row.json.bytes {
            summary.occ1_le_json += 1;
        }
    }
    (rows, summary)
}

fn print_row_table(rows: &[Row]) {
    println!(
        "{:<5} {:<8} {:>14} {:>14} {:>14} {:>14} {:>9}",
        "id", "split", "plain b/t", "concise b/t", "json b/t", "occ1 b/t", "vs plain"
    );
    for row in rows {
        println!(
            "{:<5} {:<8} {:>7}/{:<6} {:>7}/{:<6} {:>7}/{:<6} {:>7}/{:<6} {:>8.1}%",
            row.id,
            if row.holdout { "holdout" } else { "train" },
            row.plain.bytes,
            row.plain.tokens,
            row.concise.bytes,
            row.concise.tokens,
            row.json.bytes,
            row.json.tokens,
            row.occ1.bytes,
            row.occ1.tokens,
            row.occ1.reduction_vs(&row.plain)
        );
    }
}

// ---------------------------------------------------------------------------
// t01 - fixture shape and holdout rule
// ---------------------------------------------------------------------------

#[test]
fn t01_fixture_shape_and_holdout_rule() {
    let scenarios = load_scenarios();
    assert!(
        scenarios.len() >= 24,
        "need at least 24 scenarios, got {}",
        scenarios.len()
    );

    let mut ids = BTreeSet::new();
    for scenario in &scenarios {
        assert!(
            ids.insert(scenario.id.clone()),
            "duplicate scenario id {:?}",
            scenario.id
        );
        assert!(
            scenario.id.starts_with('s') && scenario.id[1..].chars().all(|c| c.is_ascii_digit()),
            "scenario id {:?} must be sNN",
            scenario.id
        );
        // Holdout rule: numeric suffix divisible by 4.
        assert_eq!(
            scenario.holdout,
            scenario_number(&scenario.id) % 4 == 0,
            "scenario {} violates the holdout rule",
            scenario.id
        );
        for (index, evidence) in scenario.payload.evidence.iter().enumerate() {
            assert_eq!(
                evidence.id,
                format!("E{}", index + 1),
                "scenario {}: evidence ids must be E1..En in order",
                scenario.id
            );
        }
    }

    let holdout = scenarios.iter().filter(|s| s.holdout).count();
    assert_eq!(
        holdout * 4,
        scenarios.len(),
        "holdout must be exactly one quarter of the suite"
    );

    // Coverage anchors (each required scenario shape exists somewhere).
    let messages: Vec<AgentCommMessage> = scenarios
        .iter()
        .map(|s| build_message(&s.payload))
        .collect();
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
        assert!(kinds.contains(required), "no scenario with kind {required}");
    }
    let statuses: BTreeSet<&str> = messages.iter().map(|m| m.status.as_str()).collect();
    assert!(statuses.contains("partial"), "no partial-completion scenario");
    assert!(statuses.contains("in_progress"), "no interrupted-task scenario");
    assert!(statuses.contains("failed"), "no failed-task scenario");
    let verification_statuses: BTreeSet<&str> = messages
        .iter()
        .filter_map(|m| m.verification.as_ref())
        .map(|v| v.status.as_str())
        .collect();
    for required in ["pass", "fail", "unknown"] {
        assert!(
            verification_statuses.contains(required),
            "no verification-{required} scenario"
        );
    }
    assert!(
        messages
            .iter()
            .any(|m| m.findings.iter().any(|f| f.confidence.is_none())),
        "no explicit-unknown (confidence absent) finding"
    );
    assert!(
        messages.iter().any(|m| m.dependencies.len() >= 3),
        "no scenario with multiple (>=3) dependencies"
    );
    assert!(
        messages
            .iter()
            .any(|m| m.findings.iter().any(|f| f.evidence.len() >= 3)),
        "no finding supported by >=3 evidence refs"
    );
    assert!(
        messages
            .iter()
            .any(|m| m.findings.iter().any(|f| f.statement.starts_with("do not"))),
        "no negative-constraint finding"
    );
    assert!(
        messages
            .iter()
            .any(|m| m.findings.iter().any(|f| f.statement.contains(" ms "))),
        "no numeric-value statement"
    );
    assert!(
        messages
            .iter()
            .any(|m| m.findings.iter().any(|f| f.statement.contains("2026-10-"))),
        "no date-bearing statement"
    );
    assert!(
        messages.iter().all(|m| m.sender_identity.is_none() == m.recipient_identity.is_none()),
        "identity fields should appear as a pair in this fixture"
    );
    assert!(
        messages
            .iter()
            .any(|m| m.sender_identity.is_some() && m.recipient_identity.is_some()),
        "no security-relevant scenario with sender/recipient identity"
    );
    assert!(
        messages.iter().any(|m| {
            m.evidence
                .iter()
                .any(|e| e.kind == "src" && e.digest.is_some() && e.line_range.is_some())
        }),
        "no source evidence with digest and line range"
    );

    // Conflicting agent reports and changed verification status: one task
    // asserted by multiple scenarios with different verification statuses.
    let mut verdicts_by_task: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for message in &messages {
        if let Some(verification) = &message.verification {
            verdicts_by_task
                .entry(message.task_id.clone())
                .or_default()
                .insert(verification.status.clone());
        }
    }
    assert!(
        verdicts_by_task
            .values()
            .any(|statuses| statuses.len() >= 2),
        "no task with two different verification statuses across scenarios"
    );

    // Interrupted task: one task with a sequence gap across two scenarios.
    let mut saw_gap = false;
    for (index, left) in messages.iter().enumerate() {
        for right in messages.iter().skip(index + 1) {
            if left.task_id == right.task_id
                && left.status == "in_progress"
                && right.status == "in_progress"
                && left.sequence.is_some()
                && right.sequence.is_some()
                && left.sequence.unwrap().abs_diff(right.sequence.unwrap()) > 1
            {
                saw_gap = true;
            }
        }
    }
    assert!(saw_gap, "no in_progress sequence-gap pair on one task");

    println!(
        "fixture: {} scenarios, {} holdout ({}%), coverage anchors OK",
        scenarios.len(),
        holdout,
        100 * holdout / scenarios.len()
    );
}

// ---------------------------------------------------------------------------
// t02 - round-trip fidelity (every scenario, train + holdout)
// ---------------------------------------------------------------------------

#[test]
fn t02_round_trip_fidelity_all_scenarios() {
    let scenarios = load_scenarios();
    assert!(!scenarios.is_empty());
    for scenario in &scenarios {
        let message = build_message(&scenario.payload);
        message
            .validate()
            .unwrap_or_else(|error| panic!("{} validate: {error}", scenario.id));
        let occ1 = message
            .to_occ1()
            .unwrap_or_else(|error| panic!("{} to_occ1: {error}", scenario.id));
        let wire = occ1
            .encode()
            .unwrap_or_else(|error| panic!("{} encode: {error}", scenario.id));
        assert!(wire.starts_with("OCC/1\n"), "{} wire header", scenario.id);
        let decoded = Occ1Message::decode(&wire)
            .unwrap_or_else(|error| panic!("{} decode: {error}", scenario.id));
        assert_eq!(decoded, occ1, "{} codec round trip", scenario.id);
        let back = AgentCommMessage::from_occ1(&decoded)
            .unwrap_or_else(|error| panic!("{} from_occ1: {error}", scenario.id));

        // Exact semantic equality with the original payload.
        assert_eq!(back, message, "{} round trip", scenario.id);

        // Evidence preservation: every id, digest, range and verdict survives.
        assert_eq!(
            back.evidence.len(),
            message.evidence.len(),
            "{} evidence count",
            scenario.id
        );
        for (original, decoded) in message.evidence.iter().zip(back.evidence.iter()) {
            assert_eq!(original.id, decoded.id, "{} evidence id", scenario.id);
            assert_eq!(original.digest, decoded.digest, "{} digest", scenario.id);
            assert_eq!(
                original.line_range, decoded.line_range,
                "{} line range",
                scenario.id
            );
            assert_eq!(
                original.revision, decoded.revision,
                "{} revision",
                scenario.id
            );
            assert_eq!(
                original.verified, decoded.verified,
                "{} evidence verdict",
                scenario.id
            );
        }

        // Next actions preserved, in order.
        assert_eq!(
            back.next_actions, message.next_actions,
            "{} next actions",
            scenario.id
        );

        // Uncertainty preserved: absent confidence stays absent, present
        // confidence stays exact, verification status is carried verbatim.
        for (original, decoded) in message.findings.iter().zip(back.findings.iter()) {
            assert_eq!(
                original.confidence, decoded.confidence,
                "{} finding confidence",
                scenario.id
            );
        }
        assert_eq!(
            back.verification.as_ref().map(|v| v.status.clone()),
            message.verification.as_ref().map(|v| v.status.clone()),
            "{} verification status",
            scenario.id
        );
    }
    let holdout = scenarios.iter().filter(|s| s.holdout).count();
    println!(
        "round trip: {}/{} scenarios exact (train {}, holdout {})",
        scenarios.len(),
        scenarios.len(),
        scenarios.len() - holdout,
        holdout
    );
}

// ---------------------------------------------------------------------------
// t03 - encoding size comparison
// ---------------------------------------------------------------------------

#[test]
fn t03_encoding_size_comparison() {
    let (rows, summary) = compute_comparison();
    println!("per-scenario encoding sizes (bytes/proxy-tokens):");
    print_row_table(&rows);
    println!(
        "aggregate over {} scenarios: plain {} B / {} t, concise {} B / {} t, json {} B / {} t, occ1 {} B / {} t",
        summary.scenarios,
        summary.plain.bytes,
        summary.plain.tokens,
        summary.concise.bytes,
        summary.concise.tokens,
        summary.json.bytes,
        summary.json.tokens,
        summary.occ1.bytes,
        summary.occ1.tokens
    );

    // OCC-1 must never exceed the plain-English ground truth.
    assert_eq!(
        summary.occ1_le_plain,
        summary.scenarios,
        "OCC-1 exceeded plain English in {} scenarios: {:?}",
        summary.scenarios - summary.occ1_le_plain,
        rows.iter()
            .filter(|row| row.occ1.bytes > row.plain.bytes)
            .map(|row| row.id.clone())
            .collect::<Vec<_>>()
    );

    // OCC-1 must not exceed JSON for at least 80% of scenarios.
    let json_ok_percent = 100 * summary.occ1_le_json / summary.scenarios.max(1);
    assert!(
        100 * summary.occ1_le_json >= 80 * summary.scenarios,
        "OCC-1 <= JSON held for only {}/{} scenarios ({}%), failing: {:?}",
        summary.occ1_le_json,
        summary.scenarios,
        json_ok_percent,
        rows.iter()
            .filter(|row| row.occ1.bytes > row.json.bytes)
            .map(|row| row.id.clone())
            .collect::<Vec<_>>()
    );
    println!(
        "occ1 <= plain: {}/{}, occ1 <= json: {}/{} ({}%)",
        summary.occ1_le_plain,
        summary.scenarios,
        summary.occ1_le_json,
        summary.scenarios,
        json_ok_percent
    );
}

// ---------------------------------------------------------------------------
// t04 - fault injection on the wire text
// ---------------------------------------------------------------------------

/// Largest char boundary at or below `byte_pos`.
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

fn replace_char_at(text: &str, byte_idx: usize, replacement: char) -> String {
    let start = floor_boundary(text, byte_idx);
    let mut end = start + 1;
    while end < text.len() && !text.is_char_boundary(end) {
        end += 1;
    }
    let mut out = String::with_capacity(text.len());
    out.push_str(&text[..start]);
    out.push(replacement);
    out.push_str(&text[end..]);
    out
}

fn insert_at(text: &str, byte_idx: usize, insert: &str) -> String {
    let at = floor_boundary(text, byte_idx);
    format!("{}{}{}", &text[..at], insert, &text[at..])
}

/// Lowercases the first character of the first field key (line 2).
fn lowercase_first_key(text: &str) -> String {
    let newline = text.find('\n').expect("field separator after header");
    let rest = &text[newline + 1..];
    let equals = rest.find('=').expect("key/value separator");
    let key = &rest[..equals];
    let mut lowered = key.to_string();
    let first = lowered.remove(0).to_ascii_lowercase();
    lowered.insert(0, first);
    format!("{}{}{}", &text[..newline + 1], lowered, &rest[equals..])
}

fn without_header(text: &str) -> String {
    let newline = text.find('\n').expect("header terminator");
    text[newline + 1..].to_string()
}

fn duplicate_first_field_line(text: &str) -> String {
    let newline = text.find('\n').expect("header terminator");
    let header = &text[..newline];
    let rest = &text[newline + 1..];
    let line_end = rest.find('\n').unwrap_or(rest.len());
    let line = &rest[..line_end];
    format!("{header}\n{line}\n{rest}")
}

/// Decodes a corrupted wire text: it must be rejected or decode to
/// something visibly different from the original. Returns true when the
/// corruption was rejected. Never panics on the corruption itself.
fn assert_corruption_visible(
    scenario_id: &str,
    label: &str,
    corrupted: &str,
    original: &Occ1Message,
) -> bool {
    match Occ1Message::decode(corrupted) {
        Err(_) => true,
        Ok(decoded) => {
            assert_ne!(
                decoded,
                *original,
                "{scenario_id}/{label}: corruption decoded to the original message"
            );
            false
        }
    }
}

#[test]
fn t04_fault_injection_never_silently_passes() {
    // Fixed, deterministic sample: every scenario with an odd id number.
    let scenarios: Vec<Scenario> = load_scenarios()
        .into_iter()
        .filter(|scenario| scenario_number(&scenario.id) % 2 == 1)
        .collect();
    assert!(scenarios.len() >= 12, "fault sample too small");

    let mut rejected = 0usize;
    let mut survived_but_changed = 0usize;
    for scenario in &scenarios {
        let message = build_message(&scenario.payload);
        let occ1 = message.to_occ1().unwrap();
        let wire = occ1.encode().unwrap();

        // A decoded corruption must never equal the original wire message:
        // it is either rejected or visibly different. Nothing may panic.
        // Truncation at every 1/8 boundary: a cut on a line boundary leaves
        // a shorter but grammatical message, which is allowed but must never
        // decode to the original.
        for k in 1..8usize {
            let truncated = truncate_at(&wire, wire.len() * k / 8);
            assert!(truncated.len() < wire.len(), "{}/trunc{k}", scenario.id);
            if assert_corruption_visible(&scenario.id, &format!("trunc{k}"), &truncated, &occ1) {
                rejected += 1;
            } else {
                survived_but_changed += 1;
            }
        }
        if assert_corruption_visible(&scenario.id, "empty", "", &occ1) {
            rejected += 1;
        } else {
            survived_but_changed += 1;
        }

        // Three fixed-position byte flips, each engineered to break the
        // grammar deterministically: inside the header, the first field
        // separator, and the last field boundary.
        let flipped_header = replace_char_at(&wire, 2, 'X');
        let err = Occ1Message::decode(&flipped_header)
            .expect_err("flipped header byte must be rejected");
        assert!(matches!(err, Occ1Error::BadHeader(_, _)));
        rejected += 1;

        let first_equals = wire.find('=').expect("first field separator");
        let flipped_equals = replace_char_at(&wire, first_equals, '#');
        let err = Occ1Message::decode(&flipped_equals)
            .expect_err("flipped separator byte must be rejected");
        assert!(
            matches!(&err, Occ1Error::MissingSeparator { .. } | Occ1Error::InvalidKey { .. }),
            "unexpected error for flipped separator: {err}"
        );
        rejected += 1;

        let last_newline = wire.rfind('\n').expect("last field boundary");
        let flipped_newline = replace_char_at(&wire, last_newline, '\u{0001}');
        let err = Occ1Message::decode(&flipped_newline)
            .expect_err("flipped field boundary must be rejected");
        assert!(
            matches!(&err, Occ1Error::ControlChar { .. } | Occ1Error::BadHeader(_, _)),
            "unexpected error for flipped boundary: {err}"
        );
        rejected += 1;

        // Insert a NUL anywhere: control characters are never legal values.
        let nul_inserted = insert_at(&wire, wire.len() / 2, "\0");
        let err = Occ1Message::decode(&nul_inserted).expect_err("NUL must be rejected");
        assert!(
            matches!(
                &err,
                Occ1Error::ControlChar { .. }
                    | Occ1Error::InvalidKey { .. }
                    | Occ1Error::MissingSeparator { .. }
                    | Occ1Error::BadHeader(_, _)
            ),
            "unexpected error for NUL insert: {err}"
        );
        rejected += 1;

        // Insert a blank line: interior blank lines are errors.
        let blank_inserted = insert_at(&wire, wire.len() / 3, "\n\n");
        let err = Occ1Message::decode(&blank_inserted).expect_err("blank line must be rejected");
        assert!(
            matches!(&err, Occ1Error::BlankLine { .. } | Occ1Error::MissingSeparator { .. }),
            "unexpected error for blank line: {err}"
        );
        rejected += 1;

        // Lowercase the first key: keys must be [A-Z][A-Z0-9_]*.
        let lowered = lowercase_first_key(&wire);
        assert_ne!(lowered, wire, "{}/lowercase: no-op", scenario.id);
        let err = Occ1Message::decode(&lowered).expect_err("lowercase key must be rejected");
        assert!(matches!(err, Occ1Error::InvalidKey { .. }));
        rejected += 1;

        // Remove the header: the first line must be exactly OCC/1.
        let headerless = without_header(&wire);
        assert!(!headerless.is_empty());
        let err = Occ1Message::decode(&headerless).expect_err("header removal must be rejected");
        assert!(matches!(err, Occ1Error::BadHeader(_, _)));
        rejected += 1;

        // Duplicate a valid line: grammatical (repeated keys are legal),
        // so decode succeeds; the message differs and reads first-wins.
        let duplicated = duplicate_first_field_line(&wire);
        let decoded = Occ1Message::decode(&duplicated)
            .unwrap_or_else(|error| panic!("{}/duplicate: {error}", scenario.id));
        assert_ne!(
            decoded,
            occ1,
            "{}/duplicate: decoded equals the original",
            scenario.id
        );
        let back = AgentCommMessage::from_occ1(&decoded)
            .unwrap_or_else(|error| panic!("{}/duplicate from_occ1: {error}", scenario.id));
        // The duplicate is verbatim and `get` reads the first occurrence,
        // so semantic equality with the original is correct here, not a
        // silent corruption: the wire form differs (asserted above).
        assert_eq!(back, message, "{}/duplicate first-wins", scenario.id);
        survived_but_changed += 1;

        // Duplicates stay valid Occ1Messages under the standard dialect
        // for kinds whose required fields the contract encoder emits.
        // Frozen-foundation note: Occ1Dialect::standard() requires GOAL, REF,
        // WANT or WHY for TASK, ACK, CORRECTION, REQUEST_DETAIL and BLOCKED,
        // none of which AgentCommMessage::to_occ1 ever emits, so dialect
        // validation of contract-encoded messages of those kinds fails.
        // Reported to the parent; not worked around here.
        if matches!(message.kind.as_str(), "PROGRESS" | "RESULT" | "CANCELLED")
            || (message.kind == "EVIDENCE" && !message.evidence.is_empty())
        {
            Occ1Dialect::standard()
                .validate(&decoded)
                .unwrap_or_else(|error| panic!("{}/duplicate dialect: {error}", scenario.id));
        }
    }
    println!(
        "fault injection: {} scenarios, {} corruptions rejected, {} grammatical but changed",
        scenarios.len(),
        rejected,
        survived_but_changed
    );
}

// ---------------------------------------------------------------------------
// t05 - semantic faults (header-valid, semantically broken)
// ---------------------------------------------------------------------------

/// Header-valid but semantically broken OCC-1 must be rejected with a
/// `ContractError`, never panic, and never silently invent or drop
/// content. Index gaps (`F3` without `F2`, `E2` without `E1`, `ART2`
/// without `ART1`, `DEP2` without `DEP1`) are included: an earlier
/// revision of the frozen foundation silently dropped everything below
/// the gap; the current revision rejects with `Occ1Conversion`, which is
/// the behavior this suite requires.
fn occ1_of(fields: &[(&str, &str)]) -> Occ1Message {
    let mut message = Occ1Message::new();
    for (key, value) in fields {
        message.push(*key, *value);
    }
    message
}

#[test]
fn t05_semantic_faults_are_rejected() {
    let base = [("TASK", "t1"), ("KIND", "RESULT"), ("STATUS", "done")];
    let cases: Vec<(&str, Vec<(&str, &str)>)> = vec![
        (
            "dangling evidence reference",
            vec![
                ("F1", "claim"),
                ("FE1", "E9"),
            ],
        ),
        (
            "non-E evidence reference",
            vec![("F1", "claim"), ("FE1", "Q7")],
        ),
        (
            "evidence reference out of range",
            vec![("E1", "src:a.rs"), ("F1", "claim"), ("FE1", "E2")],
        ),
        ("finding gap F2 without F1", vec![("F2", "claim")]),
        ("finding gap F3 after F1", vec![("F1", "one"), ("F3", "three")]),
        ("evidence gap E2 without E1", vec![("E2", "src:a.rs")]),
        ("artifact gap ART2 without ART1", vec![("ART2", "a.txt")]),
        ("dependency gap DEP2 without DEP1", vec![("DEP2", "t9")]),
        ("bad sequence", vec![("SEQ", "abc")]),
        ("bad line range", vec![("E1", "src:a.rs"), ("EL1", "x-y")]),
        ("evidence without kind:locator", vec![("E1", "nocolon")]),
        ("bad evidence verdict", vec![("E1", "src:a.rs"), ("EV1", "maybe")]),
        ("verification claim without status", vec![("V", "claim")]),
        ("verification status without claim", vec![("VS", "pass")]),
        ("confidence out of range", vec![("F1", "claim"), ("FC1", "1.5")]),
        ("bad timestamp", vec![("TS", "yesterday")]),
        ("bad expiry", vec![("EXP", "soon")]),
    ];

    for (label, extra) in cases {
        let mut fields: Vec<(&str, &str)> = base.to_vec();
        fields.extend(extra);
        let message = occ1_of(&fields);
        let error = AgentCommMessage::from_occ1(&message)
            .err()
            .unwrap_or_else(|| panic!("{label}: expected rejection, got a message"));
        assert!(
            matches!(
                &error,
                ContractError::Occ1Conversion(_)
                    | ContractError::InvalidConfidence(_)
                    | ContractError::InvalidTimestamp(_)
            ),
            "{label}: unexpected rejection {error}"
        );
    }

    // Missing required core fields.
    for (label, fields) in [
        ("missing TASK", vec![("KIND", "RESULT"), ("STATUS", "done")]),
        ("missing KIND", vec![("TASK", "t1"), ("STATUS", "done")]),
        ("missing STATUS", vec![("TASK", "t1"), ("KIND", "RESULT")]),
    ] {
        let message = occ1_of(&fields);
        let error = AgentCommMessage::from_occ1(&message)
            .err()
            .unwrap_or_else(|| panic!("{label}: expected rejection"));
        assert!(
            matches!(&error, ContractError::MissingRequired { .. }),
            "{label}: {error}"
        );
    }

    // Invalid typed values (caught by validate() inside from_occ1).
    let bad_values: Vec<(&str, Vec<(&str, &str)>, fn(&ContractError) -> bool)> = vec![
        (
            "unknown kind",
            vec![("TASK", "t1"), ("KIND", "NOPE"), ("STATUS", "done")],
            |error| matches!(error, ContractError::UnknownKind(_)),
        ),
        (
            "unknown status",
            vec![("TASK", "t1"), ("KIND", "RESULT"), ("STATUS", "weird")],
            |error| matches!(error, ContractError::UnknownStatus(_)),
        ),
        (
            "unknown evidence kind",
            vec![("TASK", "t1"), ("KIND", "RESULT"), ("STATUS", "done"), ("E1", "weird:a.rs")],
            |error| matches!(error, ContractError::UnknownEvidenceKind(_)),
        ),
        (
            "empty task id",
            vec![("TASK", ""), ("KIND", "RESULT"), ("STATUS", "done")],
            |error| matches!(error, ContractError::InvalidTaskId(_)),
        ),
        (
            "control character in task id",
            vec![("TASK", "t\u{0001}"), ("KIND", "RESULT"), ("STATUS", "done")],
            |error| matches!(error, ContractError::InvalidTaskId(_)),
        ),
    ];
    for (label, fields, predicate) in bad_values {
        let message = occ1_of(&fields);
        let error = AgentCommMessage::from_occ1(&message)
            .err()
            .unwrap_or_else(|| panic!("{label}: expected rejection"));
        assert!(predicate(&error), "{label}: unexpected error {error}");
    }
    println!("semantic faults: all rejected or pinned, no panics");
}

// ---------------------------------------------------------------------------
// t06 - latency budget
// ---------------------------------------------------------------------------

#[test]
fn t06_latency_within_budget() {
    let scenarios = load_scenarios();
    let mut worst_encode = Duration::ZERO;
    let mut worst_decode = Duration::ZERO;
    let mut encode_sum = Duration::ZERO;
    let mut decode_sum = Duration::ZERO;
    for scenario in &scenarios {
        let message = build_message(&scenario.payload);
        let wire = occ1_wire(&message);

        let start = Instant::now();
        for _ in 0..LATENCY_ITERATIONS {
            let encoded = occ1_wire(&message);
            std::hint::black_box(&encoded);
        }
        let encode_total = start.elapsed();

        let start = Instant::now();
        for _ in 0..LATENCY_ITERATIONS {
            let decoded =
                AgentCommMessage::from_occ1(&Occ1Message::decode(&wire).expect("decode"))
                    .expect("from_occ1");
            std::hint::black_box(&decoded);
        }
        let decode_total = start.elapsed();

        let encode_mean = encode_total / LATENCY_ITERATIONS;
        let decode_mean = decode_total / LATENCY_ITERATIONS;
        encode_sum += encode_mean;
        decode_sum += decode_mean;
        worst_encode = worst_encode.max(encode_mean);
        worst_decode = worst_decode.max(decode_mean);
        assert!(
            encode_mean < LATENCY_BUDGET,
            "{}: encode mean {encode_mean:?} exceeds {LATENCY_BUDGET:?}",
            scenario.id
        );
        assert!(
            decode_mean < LATENCY_BUDGET,
            "{}: decode mean {decode_mean:?} exceeds {LATENCY_BUDGET:?}",
            scenario.id
        );
        println!(
            "{:<5} encode mean {:>8.1} us   decode mean {:>8.1} us",
            scenario.id,
            encode_mean.as_secs_f64() * 1e6,
            decode_mean.as_secs_f64() * 1e6
        );
    }
    let count = scenarios.len() as u32;
    println!(
        "latency: encode mean-of-means {:>8.1} us (worst {:>8.1} us), decode mean-of-means {:>8.1} us (worst {:>8.1} us), budget {} us",
        (encode_sum / count).as_secs_f64() * 1e6,
        worst_encode.as_secs_f64() * 1e6,
        (decode_sum / count).as_secs_f64() * 1e6,
        worst_decode.as_secs_f64() * 1e6,
        LATENCY_BUDGET.as_secs_f64() * 1e6
    );
}

// ---------------------------------------------------------------------------
// t07 - aggregate headline report
// ---------------------------------------------------------------------------

#[test]
fn t07_aggregate_headline_report() {
    let (rows, summary) = compute_comparison();
    println!();
    println!(
        "==== OCC communication-fidelity aggregate: {} scenarios, {} holdout ====",
        summary.scenarios, summary.holdout
    );
    println!(
        "{:<18} {:>12} {:>12} {:>14}",
        "encoding", "total bytes", "total chars", "proxy tokens"
    );
    for (label, encoded) in [
        ("plain English", &summary.plain),
        ("concise English", &summary.concise),
        ("JSON", &summary.json),
        ("OCC-1", &summary.occ1),
    ] {
        println!(
            "{:<18} {:>12} {:>12} {:>14}",
            label, encoded.bytes, encoded.chars, encoded.tokens
        );
    }
    println!(
        "OCC-1 vs plain English: {:+.1}% bytes, {:+.1}% proxy tokens",
        -summary.occ1.reduction_vs(&summary.plain),
        -(100.0 * (summary.plain.tokens as f64 - summary.occ1.tokens as f64)
            / summary.plain.tokens as f64)
    );
    println!(
        "OCC-1 vs JSON:           {:+.1}% bytes, {:+.1}% proxy tokens",
        -summary.occ1.reduction_vs(&summary.json),
        -(100.0 * (summary.json.tokens as f64 - summary.occ1.tokens as f64)
            / summary.json.tokens as f64)
    );
    println!(
        "per-scenario occ1 <= plain: {}/{}; occ1 <= json: {}/{} ({:.1}%, threshold 80%)",
        summary.occ1_le_plain,
        summary.scenarios,
        summary.occ1_le_json,
        summary.scenarios,
        100.0 * summary.occ1_le_json as f64 / summary.scenarios as f64
    );

    // Train/holdout split: both splits must show the same direction, which
    // is the point of keeping the holdout protected from threshold tuning.
    for (label, holdout) in [("train", false), ("holdout", true)] {
        let mut plain = Encoded::default();
        let mut concise = Encoded::default();
        let mut json = Encoded::default();
        let mut occ1 = Encoded::default();
        let mut count = 0usize;
        let mut le_json = 0usize;
        for row in rows.iter().filter(|row| row.holdout == holdout) {
            count += 1;
            plain.add(&row.plain);
            concise.add(&row.concise);
            json.add(&row.json);
            occ1.add(&row.occ1);
            if row.occ1.bytes <= row.json.bytes {
                le_json += 1;
            }
        }
        println!(
            "{:<8} {:>2} scenarios: plain {} B, concise {} B, json {} B, occ1 {} B ({:+.1}% vs plain, occ1<=json {}/{})",
            label,
            count,
            plain.bytes,
            concise.bytes,
            json.bytes,
            occ1.bytes,
            -occ1.reduction_vs(&plain),
            le_json,
            count
        );
    }

    // Sanity: the aggregate direction must match the per-scenario rule.
    assert!(summary.occ1.bytes < summary.plain.bytes);
    assert!(summary.occ1.bytes < summary.json.bytes);
    assert_eq!(summary.occ1_le_plain, summary.scenarios);
    assert!(100 * summary.occ1_le_json >= 80 * summary.scenarios);
}
