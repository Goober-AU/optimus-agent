//! DIALECT/1 promotion round-trip tests.
//!
//! Proves the promoted card against the frozen research artifacts:
//! - the on-disk card and the compile-time embedded copy are identical
//!   and both pass checksum verification;
//! - the full 69-message corpus round-trips: wire text produced by the
//!   research encoder (Python dialect.py, fixture
//!   `research/occ2opt_analysis/dialect1_corpus_wire.json`) decodes to
//!   the exact typed contract, and the Rust encoder reproduces that
//!   wire byte-for-byte;
//! - the promoted production card validates, stays non-default, and
//!   interoperates with the existing OCC-2 codec;
//! - strict decode rejects tampering.

use std::path::PathBuf;

use pi_coding_agent::core::occ::contract::AgentCommMessage;
use pi_coding_agent::core::occ::dialect_load::{
    load_research_card, promoted_registry, promoted_registry_v2, PromotedDialect,
    Dialect1Error, DIALECT1_FORMAT, FROZEN2_CARD_CHECKSUM, FROZEN2_CARD_JSON,
    FROZEN_CARD_CHECKSUM, FROZEN_CARD_JSON, PROMOTED_DIALECT_ID,
};
use pi_coding_agent::core::occ::dialect::DialectRegistry;
use pi_coding_agent::core::occ::occ2;
use serde_json::Value;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("repo root")
        .to_path_buf()
}

fn fixture() -> Value {
    let path = repo_root()
        .join("research/occ2opt_analysis/dialect1_corpus_wire.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    serde_json::from_str(&text).expect("fixture parses")
}

fn corpus() -> Vec<(String, Value)> {
    let path = repo_root().join("research/tasks/msgs_corpus.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    let raw: Vec<Value> = serde_json::from_str(&text).expect("corpus parses");
    raw.into_iter()
        .map(|entry| {
            let id = entry["id"].as_str().expect("id").to_string();
            (id, entry["payload"].clone())
        })
        .collect()
}

/// Rebuilds the typed contract from a corpus payload, the same shape
/// the research `contract.AgentCommMessage.from_payload` produces:
/// positional finding ids `F1..`, evidence ids from the payload.
fn payload_to_message(payload: &Value) -> AgentCommMessage {
    let mut message = AgentCommMessage::new(
        payload["kind"].as_str().expect("kind"),
        payload["task_id"].as_str().expect("task_id"),
        payload["status"].as_str().expect("status"),
    );
    message.message_id = str_field(payload, "message_id");
    message.parent_task_id = str_field(payload, "parent_task_id");
    message.trace_id = str_field(payload, "trace_id");
    message.sender_identity = str_field(payload, "sender_identity");
    message.recipient_identity = str_field(payload, "recipient_identity");
    message.sequence = payload["sequence"].as_u64();
    for (index, finding) in payload["findings"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .enumerate()
    {
        message.findings.push(
            pi_coding_agent::core::occ::contract::Finding {
                id: format!("F{}", index + 1),
                statement: finding["statement"].as_str().expect("statement").to_string(),
                confidence: finding
                    .get("confidence")
                    .filter(|value| !value.is_null())
                    .and_then(|value| value.as_f64())
                    .map(|value| value as f32),
                evidence: finding["evidence"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                    .iter()
                    .filter_map(|value| value.as_str().map(str::to_string))
                    .collect(),
            },
        );
    }
    for (index, evidence) in payload["evidence"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .enumerate()
    {
        message.evidence.push(
            pi_coding_agent::core::occ::contract::EvidenceRef {
                id: evidence["id"]
                    .as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| format!("E{}", index + 1)),
                kind: evidence["kind"].as_str().expect("evidence kind").to_string(),
                locator: evidence["locator"].as_str().expect("locator").to_string(),
                line_range: evidence["line_range"]
                    .as_array()
                    .and_then(|range| {
                        let start = range.first()?.as_u64()? as u32;
                        let end = range.get(1)?.as_u64()? as u32;
                        Some((start, end))
                    }),
                digest: str_field(evidence, "digest"),
                revision: str_field(evidence, "revision"),
                verified: evidence
                    .get("verified")
                    .filter(|value| !value.is_null())
                    .and_then(|value| value.as_bool()),
            },
        );
    }
    message.artifacts = payload["artifacts"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect();
    if let Some(verification) = payload.get("verification").filter(|value| !value.is_null()) {
        message.verification = Some(pi_coding_agent::core::occ::contract::Verification {
            claim: verification["claim"].as_str().expect("claim").to_string(),
            status: verification["status"].as_str().expect("status").to_string(),
            method: verification["method"].as_str().expect("method").to_string(),
            receipt: str_field(&verification, "receipt"),
        });
    }
    message.dependencies = payload["dependencies"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect();
    message.next_actions = payload["next_actions"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect();
    message.timestamp = str_field(payload, "timestamp");
    message.expiry = str_field(payload, "expiry");
    message
}

fn str_field(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .filter(|field| !field.is_null())
        .and_then(|field| field.as_str())
        .map(str::to_string)
}

#[test]
fn on_disk_card_matches_embedded_and_verifies() {
    let path = repo_root()
        .join("research/symbolic_comm/dialect-card.json");
    let on_disk = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    assert_eq!(on_disk.trim_end(), FROZEN_CARD_JSON.trim_end());

    let card = load_research_card(&on_disk).expect("on-disk card loads");
    assert_eq!(card.checksum(), FROZEN_CARD_CHECKSUM);
    let embedded = load_research_card(FROZEN_CARD_JSON).expect("embedded card loads");
    assert_eq!(embedded.checksum(), FROZEN_CARD_CHECKSUM);
    assert_eq!(card.id(), "sym-v1-pt-pt");
    assert_eq!(card.generation(), 4);
}

#[test]
fn fixture_matches_corpus_and_card_checksum() {
    let fixture = fixture();
    assert_eq!(fixture["format"].as_str().unwrap(), DIALECT1_FORMAT);
    assert_eq!(
        fixture["card_checksum"].as_str().unwrap(),
        FROZEN_CARD_CHECKSUM
    );
    let fixture_messages = fixture["messages"].as_array().unwrap().len();
    assert_eq!(fixture_messages, corpus().len(), "fixture/corpus size mismatch");
}

#[test]
fn corpus_research_encode_to_promoted_decode_round_trip() {
    let promoted = PromotedDialect::load_embedded().expect("promoted dialect loads");
    let fixture = fixture();
    let corpus = corpus();
    let payloads: std::collections::HashMap<String, &Value> = corpus
        .iter()
        .map(|(id, payload)| (id.clone(), payload))
        .collect();

    let mut decoded_count = 0usize;
    let mut encode_byte_equal = 0usize;
    for entry in fixture["messages"].as_array().unwrap() {
        let id = entry["id"].as_str().expect("fixture id");
        let wire = entry["wire"].as_str().expect("fixture wire");

        // research-encode -> promoted-card decode -> exact typed match
        let decoded = promoted
            .decode(wire)
            .unwrap_or_else(|error| panic!("decode {id} failed: {error}"));
        let expected = payload_to_message(
            payloads
                .get(id)
                .unwrap_or_else(|| panic!("corpus payload {id} missing")),
        );
        assert!(
            decoded == expected,
            "typed mismatch for {id}\nexpected: {expected:?}\ndecoded:  {decoded:?}"
        );
        decoded_count += 1;

        // promoted-card encode reproduces the research wire byte-for-byte
        let re_encoded = promoted
            .encode(&expected)
            .unwrap_or_else(|error| panic!("encode {id} failed: {error}"));
        assert!(
            re_encoded == wire,
            "wire mismatch for {id}\nresearch: {wire:?}\nrust:    {re_encoded:?}"
        );
        encode_byte_equal += 1;
    }
    assert_eq!(decoded_count, 69);
    assert_eq!(encode_byte_equal, 69);
}

#[test]
fn corpus_rust_encode_decode_round_trip_all_kinds() {
    let promoted = PromotedDialect::load_embedded().expect("promoted dialect loads");
    let mut kinds = std::collections::BTreeSet::new();
    for (id, payload) in corpus() {
        let message = payload_to_message(&payload);
        kinds.insert(message.kind.clone());
        let wire = promoted
            .encode(&message)
            .unwrap_or_else(|error| panic!("encode {id} failed: {error}"));
        let decoded = promoted
            .decode(&wire)
            .unwrap_or_else(|error| panic!("decode {id} failed: {error}"));
        assert_eq!(decoded, message, "round-trip mismatch for {id}");
    }
    // All nine corpus kinds exercised.
    assert_eq!(kinds.len(), 9, "kinds: {kinds:?}");
}

#[test]
fn production_translation_validates_and_stays_non_default() {
    let promoted = PromotedDialect::load_embedded().expect("promoted dialect loads");
    promoted
        .production
        .validate()
        .expect("production card validates");
    assert_eq!(promoted.production.dialect_id, PROMOTED_DIALECT_ID);

    let default = DialectRegistry::with_sym_v1();
    assert!(default.get(PROMOTED_DIALECT_ID, 1).is_none());
    let registry = promoted_registry().expect("promoted registry builds");
    assert!(registry.get(PROMOTED_DIALECT_ID, 1).is_some());
    assert!(registry.get("sym-v1", 1).is_some());
}

#[test]
fn promoted_card_interoperates_with_the_occ2_codec() {
    // The translated symbol table must be usable by the existing OCC-2
    // codec: encode with the promoted production card, decode through a
    // registry that contains it.
    let promoted = PromotedDialect::load_embedded().expect("promoted dialect loads");
    let registry = promoted_registry().expect("promoted registry builds");
    let mut checked = 0;
    for (id, payload) in corpus().into_iter().take(12) {
        let message = payload_to_message(&payload);
        let wire = occ2::encode(&message, &promoted.production)
            .unwrap_or_else(|error| panic!("occ2 encode {id} failed: {error}"));
        let decoded = occ2::decode(&wire, &registry)
            .unwrap_or_else(|error| panic!("occ2 decode {id} failed: {error}"));
        assert_eq!(decoded, message, "occ2 round-trip mismatch for {id}");
        checked += 1;
    }
    assert_eq!(checked, 12);
}

#[test]
fn strict_decode_rejects_tampered_wire() {
    let promoted = PromotedDialect::load_embedded().expect("promoted dialect loads");
    let fixture = fixture();
    let entry = &fixture["messages"][0];
    let wire = entry["wire"].as_str().expect("wire");
    let id = entry["id"].as_str().expect("id");

    // An unknown enum symbol is rejected by the card's symbol table.
    let tampered = wire.replacen("k=", "k=Z", 1);
    assert!(
        promoted.decode(&tampered).is_err(),
        "tampered {id} wire must not decode"
    );
    // A line reorder breaks the encode fixed point: the canonical field
    // order is part of the strict contract.
    let mut lines: Vec<&str> = wire.split('\n').collect();
    assert!(lines.len() >= 4, "fixture message has enough lines");
    lines.swap(1, 2);
    let reordered = lines.join("\n");
    assert!(
        promoted.decode(&reordered).is_err(),
        "reordered {id} wire must not decode"
    );
    // Unknown alias.
    assert!(promoted.decode("D/1\nzz=1").is_err());
    // Wrong header.
    assert!(promoted.decode("D/2\nk=T").is_err());
    // Missing operator.
    assert!(promoted.decode("D/1\nkT").is_err());
}

#[test]
fn usage_rules_never_enter_the_wire() {
    let promoted = PromotedDialect::load_embedded().expect("promoted dialect loads");
    let fixture = fixture();
    let entry = &fixture["messages"][0];
    let wire = entry["wire"].as_str().expect("wire");
    assert!(!wire.contains("usage_rules"));
    assert!(!wire.contains("Findings and evidence stay positional"));
    // The prose exists on the card (checksummed) but is inert.
    assert!(!promoted.research.usage_rules().is_empty());
}


// ---------------------------------------------------------------------------
// DIALECT/2 promotion tests
// ---------------------------------------------------------------------------

mod v2 {
    use super::*;

    fn fixture2() -> Value {
        let path = repo_root().join("research/occ2opt_analysis/dialect2_corpus_wire.json");
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
        serde_json::from_str(&text).expect("v2 fixture parses")
    }

    /// corpus1 (list shape) + corpus2 splits ({meta, messages} shape),
    /// flattened into id -> payload.
    fn all_corpora() -> Vec<(String, Value)> {
        let mut out: Vec<(String, Value)> = Vec::new();
        let fixture = fixture2();
        for corpus in fixture["corpora"].as_array().expect("corpora list") {
            let path = repo_root().join(corpus["path"].as_str().expect("corpus path"));
            let text = std::fs::read_to_string(&path)
                .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
            let value: Value = serde_json::from_str(&text).expect("corpus parses");
            let entries = value
                .get("messages")
                .and_then(|messages| messages.as_array().cloned())
                .unwrap_or_else(|| {
                    value
                        .as_array()
                        .expect("corpus is a list or {meta, messages}")
                        .clone()
                });
            for entry in entries {
                let id = entry["id"].as_str().expect("id").to_string();
                out.push((id, entry["payload"].clone()));
            }
        }
        out
    }

    #[test]
    fn v2_on_disk_card_matches_embedded_and_verifies() {
        let path = repo_root().join("research/symbolic_comm2/dialect2-card.json");
        let on_disk = std::fs::read_to_string(&path)
            .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
        assert_eq!(on_disk.trim_end(), FROZEN2_CARD_JSON.trim_end());
        let card = load_research_card(&on_disk).expect("on-disk v2 card loads");
        assert_eq!(card.checksum(), FROZEN2_CARD_CHECKSUM);
        let embedded = load_research_card(FROZEN2_CARD_JSON).expect("embedded v2 card loads");
        assert_eq!(embedded.checksum(), FROZEN2_CARD_CHECKSUM);
        assert!(card.has_v2_features());
        assert_eq!(card.wire_header(), "d");
    }

    #[test]
    fn v2_fixture_shape_matches_corpora_and_card() {
        let fixture = fixture2();
        assert_eq!(fixture["format"].as_str().unwrap(), DIALECT1_FORMAT);
        assert_eq!(
            fixture["card_checksum"].as_str().unwrap(),
            FROZEN2_CARD_CHECKSUM
        );
        let messages = fixture["messages"].as_array().unwrap().len();
        let corpora: usize = fixture["corpora"]
            .as_array()
            .unwrap()
            .iter()
            .map(|corpus| corpus["count"].as_u64().expect("count") as usize)
            .sum();
        assert_eq!(messages, corpora, "fixture/corpora size mismatch");
        assert_eq!(messages, 273, "69 corpus1 + 90 evolve + 60 dev + 54 holdout");
        assert_eq!(all_corpora().len(), 273);
    }

    #[test]
    fn v2_corpus_research_encode_to_promoted_decode_round_trip() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let fixture = fixture2();
        let corpus = all_corpora();
        let payloads: std::collections::HashMap<String, &Value> = corpus
            .iter()
            .map(|(id, payload)| (id.clone(), payload))
            .collect();

        let mut decoded_count = 0usize;
        let mut encode_byte_equal = 0usize;
        for entry in fixture["messages"].as_array().unwrap() {
            let id = entry["id"].as_str().expect("fixture id");
            let wire = entry["wire"].as_str().expect("fixture wire");

            let decoded = promoted
                .decode(wire)
                .unwrap_or_else(|error| panic!("decode {id} failed: {error}"));
            let expected = payload_to_message(
                payloads
                    .get(id)
                    .unwrap_or_else(|| panic!("corpus payload {id} missing")),
            );
            assert!(
                decoded == expected,
                "typed mismatch for {id}\nexpected: {expected:?}\ndecoded:  {decoded:?}"
            );
            decoded_count += 1;

            let re_encoded = promoted
                .encode(&expected)
                .unwrap_or_else(|error| panic!("encode {id} failed: {error}"));
            assert!(
                re_encoded == wire,
                "wire mismatch for {id}\nresearch: {wire:?}\nrust:    {re_encoded:?}"
            );
            encode_byte_equal += 1;
        }
        assert_eq!(decoded_count, 273);
        assert_eq!(encode_byte_equal, 273);
    }

    /// Live-eval finding A (live-dev-sym-v2): a fully-consistent child
    /// emitted the $= scalar-pack line before the family lines. The parse
    /// stage is order-insensitive, but strict decode is a fixed-point
    /// check, so any non-canonical line order is rejected.
    #[test]
    fn v2_reordered_lines_fail_the_fixed_point() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let fixture = fixture2();
        let entry = fixture["messages"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["wire"].as_str().unwrap().contains("\n$="))
            .expect("fixture wire with a $= line");
        let wire = entry["wire"].as_str().unwrap();
        let mut lines: Vec<&str> = wire.split('\n').collect();
        let pack_index = lines
            .iter()
            .position(|line| line.starts_with("$="))
            .expect("$= line present");
        let pack_line = lines.remove(pack_index);
        lines.insert(1, pack_line);
        let reordered = lines.join("\n");
        let error = promoted
            .decode(&reordered)
            .expect_err("reordered wire must fail strict decode");
        assert!(
            matches!(error, Dialect1Error::NotFixedPoint(_)),
            "expected NotFixedPoint, got {error}"
        );
    }

    /// Live-eval finding B: the symbol alphabet applies even to
    /// never-compress kinds; spelled-out enum values are rejected, and a
    /// raw ISO timestamp is not a fixed point (TS/EXP travel epoch36 even
    /// for never-compress messages).
    #[test]
    fn v2_never_compress_requires_symbols_and_epoch36() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let fixture = fixture2();
        let entry = fixture["messages"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| {
                let wire = entry["wire"].as_str().unwrap();
                wire.contains("\nk=A\n") && wire.contains("\n$=")
            })
            .expect("fixture ACK wire with a $= line");
        let wire = entry["wire"].as_str().unwrap();

        let spelled = wire.replace("\nk=A\n", "\nk=ACK\n");
        let error = promoted
            .decode(&spelled)
            .expect_err("spelled-out KIND must fail");
        assert!(
            matches!(
                error,
                Dialect1Error::UnknownSymbol { .. }
            ),
            "expected UnknownSymbol, got {error}"
        );

        // Replace the epoch36 TS inside the $= line with the raw ISO form.
        let mut lines: Vec<String> = wire.split('\n').map(str::to_string).collect();
        let pack_index = lines
            .iter()
            .position(|line| line.starts_with("$="))
            .expect("$= line present");
        let parts: Vec<&str> = lines[pack_index][2..].split(' ').collect();
        let message = payload_to_message(
            &all_corpora()
                .into_iter()
                .find(|(id, _)| id == entry["id"].as_str().unwrap())
                .map(|(_, payload)| payload)
                .expect("corpus payload"),
        );
        let iso = message.timestamp.expect("ACK fixture has a timestamp");
        lines[pack_index] = format!("$={} {} {} {}", parts[0], parts[1], parts[2], iso);
        let iso_wire = lines.join("\n");
        let error = promoted
            .decode(&iso_wire)
            .expect_err("raw ISO timestamp must not be a fixed point");
        assert!(
            matches!(error, Dialect1Error::NotFixedPoint(_)),
            "expected NotFixedPoint, got {error}"
        );
    }

    /// Live-eval finding C: packed evidence links are bare indices. A
    /// non-index reference like `v1=e1` is rejected by the typed contract
    /// (UnknownEvidence), matching the Python contract error
    /// `FE1 references 'e1', expected E<n>`. The card text now states
    /// the bare-index format explicitly.
    #[test]
    fn v2_non_index_evidence_links_are_rejected() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let fixture = fixture2();
        let entry = fixture["messages"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["wire"].as_str().unwrap().contains("\nv1=1\n"))
            .expect("fixture wire with a packed evidence link");
        let wire = entry["wire"].as_str().unwrap();
        let linked = wire.replace("\nv1=1\n", "\nv1=e1\n");
        let error = promoted
            .decode(&linked)
            .expect_err("non-index evidence link must be rejected");
        assert!(
            matches!(
                error,
                Dialect1Error::Contract { .. }
            ) || matches!(&error, Dialect1Error::Contract(_)),
            "expected a contract error, got {error}"
        );
        let text = format!("{error}");
        assert!(
            text.contains("e1"),
            "error should name the bad reference: {text}"
        );
    }

    #[test]
    fn v2_rust_encode_decode_round_trip_all_kinds() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let mut kinds = std::collections::BTreeSet::new();
        let mut checked = 0usize;
        for (id, payload) in all_corpora() {
            let message = payload_to_message(&payload);
            kinds.insert(message.kind.clone());
            let wire = promoted
                .encode(&message)
                .unwrap_or_else(|error| panic!("encode {id} failed: {error}"));
            let decoded = promoted
                .decode(&wire)
                .unwrap_or_else(|error| panic!("decode {id} failed: {error}"));
            assert_eq!(decoded, message, "round-trip mismatch for {id}");
            checked += 1;
        }
        assert_eq!(checked, 273);
        assert_eq!(kinds.len(), 9, "kinds: {kinds:?}");
    }

    #[test]
    fn v2_promoted_card_interoperates_with_the_occ2_codec() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let registry = promoted_registry_v2().expect("v2 registry builds");
        let mut checked = 0usize;
        for (id, payload) in all_corpora().into_iter().take(24) {
            let message = payload_to_message(&payload);
            let wire = occ2::encode(&message, &promoted.production)
                .unwrap_or_else(|error| panic!("occ2 encode {id} failed: {error}"));
            let decoded = occ2::decode(&wire, &registry)
                .unwrap_or_else(|error| panic!("occ2 decode {id} failed: {error}"));
            assert_eq!(decoded, message, "occ2 round-trip mismatch for {id}");
            checked += 1;
        }
        assert_eq!(checked, 24);
    }

    #[test]
    fn v2_strict_decode_rejects_tampered_wire() {
        let promoted = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let fixture = fixture2();
        // Find an entry with a digest-bearing evidence line and one with
        // a scalar-packed line.
        let mut sp_entry: Option<&Value> = None;
        let mut digest_entry: Option<&Value> = None;
        for entry in fixture["messages"].as_array().unwrap() {
            let wire = entry["wire"].as_str().expect("wire");
            if sp_entry.is_none() && wire.contains("$=") {
                sp_entry = Some(entry);
            }
            if digest_entry.is_none() && wire.contains('#') {
                digest_entry = Some(entry);
            }
        }
        let sp = sp_entry.expect("a scalar-packed fixture entry");
        let id = sp["id"].as_str().expect("id");
        let wire = sp["wire"].as_str().expect("wire");

        // Wrong header: the v1 header is not the v2 header.
        let v1_header = wire.replacen("d\n", "D/1\n", 1);
        assert_ne!(v1_header, wire, "header must differ");
        assert!(
            promoted.decode(&v1_header).is_err(),
            "v1 header must not decode as v2 ({id})"
        );
        // Dropping a part from the scalar-packed line breaks the strict
        // fixed point.
        if let Some(pos) = wire.find("$=") {
            let mut lines: Vec<&str> = wire.split('\n').collect();
            let index = lines.iter().position(|l| l.starts_with("$=")).expect("sp line");
            let parts: Vec<&str> = lines[index][2..].split(' ').collect();
            assert!(parts.len() == 4, "four parts");
            let truncated = format!("$={}", parts[..3].join(" "));
            lines[index] = truncated.as_str();
            let tampered = lines.join("\n");
            let _ = pos;
            assert!(
                promoted.decode(&tampered).is_err(),
                "truncated scalar pack must not decode ({id})"
            );
        }
        // Unknown alias.
        assert!(promoted.decode("d\nzz=1").is_err());

        // A value swap inside a digest or prose line is a *valid* wire
        // for a different message (no codec rejects mutations without a
        // MAC), so the strictness tests stick to structural violations.
        // A base36 epoch that cannot round-trip (digits decode to an
        // epoch whose canonical base36 differs) breaks the fixed point.
        if let Some(stamp_line) = wire.split('\n').find(|l| l.starts_with("w=")) {
            let tampered = wire.replacen(stamp_line, "w=123", 1);
            assert_ne!(&tampered, wire, "stamp line must be recoded");
            assert!(
                promoted.decode(&tampered).is_err(),
                "non-canonical base36 epoch must not decode ({id})"
            );
        }
        let _ = digest_entry; // digest-bearing entries exist; value swaps are out of scope
    }

    #[test]
    fn v1_and_v2_wires_are_mutually_rejected() {
        let v1 = PromotedDialect::load_embedded().expect("v1 promoted loads");
        let v2 = PromotedDialect::load_embedded_v2().expect("v2 promoted loads");
        let fixture2 = fixture2();
        let v2_wire = fixture2["messages"][0]["wire"].as_str().expect("wire");
        let fixture1 = fixture();
        let v1_wire = fixture1["messages"][0]["wire"].as_str().expect("wire");
        assert!(
            v1.decode(v2_wire).is_err(),
            "v1 decoder must reject a v2 wire (header)"
        );
        assert!(
            v2.decode(v1_wire).is_err(),
            "v2 decoder must reject a v1 wire (header)"
        );
    }
}
