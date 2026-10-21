//! Selection-policy integration tests: the deterministic OCC-1 vs OCC-2
//! selector over real registries (sym-v1 and the promoted research card),
//! plus the validated-registry constructor and the fixed-overhead table.

use std::collections::BTreeMap;

use pi_coding_agent::core::occ::dialect::{DialectCard, DialectRegistry, OccCapability};
use pi_coding_agent::core::occ::dialect_load::{PromotedDialect, PROMOTED_DIALECT_ID};
use pi_coding_agent::core::occ::selection::{
    codebook_break_even_messages, expected_per_message_savings, select_with_policy,
    validated_registry, validated_registry_with, DialectEvaluationRecord, SelectionContext,
    SelectionDecision, SelectionPolicy, SelectionReason, CODEBOOK_TOKENS,
    CORPUS_REFERENCE_TOKEN_RATIO, EXPECTED_REMAINING_MESSAGES, MIN_VALIDATION_FIDELITY,
    MIN_VALIDATION_SAMPLES, SILO_REFERENCE_TOKEN_RATIO, SMALL_MESSAGE_CHAR_FLOOR,
};

fn sym_v1_registry() -> DialectRegistry {
    DialectRegistry::with_sym_v1()
}

fn local() -> OccCapability {
    OccCapability::here()
    occ3: None,
}

/// Owns speaker and listener so `SelectionContext` borrows outlive the
/// statement (Rust temporaries drop at the semicolon).
struct Pair {
    speaker: OccCapability,
    listener: OccCapability,
}

impl Pair {
    fn sym_v1() -> Self {
        Self {
            speaker: local(),
            listener: local(),
        }
    }

    fn occ1_only_listener() -> Self {
        Self {
            speaker: local(),
            listener: occ1_only_peer(),
        }
    }

    fn context<'a>(
        &'a self,
        registry: &'a DialectRegistry,
        chars: usize,
        kind: &'a str,
    ) -> SelectionContext<'a> {
        SelectionContext::simple(&self.speaker, Some(&self.listener), registry, chars, kind)
    }
}

fn occ1_only_peer() -> OccCapability {
    OccCapability {
        occ1: true,
        dialects: BTreeMap::new(),
    }
    occ3: None,
}

fn evaluated_sym_v1(samples: usize, ratio: f64) -> BTreeMap<(String, u32), DialectEvaluationRecord> {
    let mut map = BTreeMap::new();
    map.insert(
        ("sym-v1".to_string(), 1),
        DialectEvaluationRecord {
            samples,
            listener_fidelity_mean: 1.0,
            wire_tokens_vs_occ1: ratio,
            last_evaluated: Some(1_800_000_000),
        },
    );
    map
}

#[test]
fn fixed_overhead_table_matches_the_measured_reports() {
    // CODEBOOK-COST.md (offline, Qwen2.5-0.5B tokenizer):
    // codebook 602 B / 161 tok; headers 22 vs 4 tok; corpus sym vs OCC-1
    // 337.9 B / 167.1 tok vs 353.1 B / 153.1 tok; SILO 153.8 vs 289.8 B
    // (~76 vs ~126 tok).
    assert_eq!(CODEBOOK_TOKENS, 161.0);
    assert_eq!(SMALL_MESSAGE_CHAR_FLOOR, 220);
    assert_eq!(MIN_VALIDATION_SAMPLES, 30);
    assert_eq!(MIN_VALIDATION_FIDELITY, 1.0);
    assert_eq!(EXPECTED_REMAINING_MESSAGES, 8);
    let corpus = expected_per_message_savings(353, CORPUS_REFERENCE_TOKEN_RATIO);
    assert!(corpus < 0.0, "corpus profile must not pay: {corpus}");
    let silo = expected_per_message_savings(290, SILO_REFERENCE_TOKEN_RATIO);
    assert!((silo - 50.0).abs() < 3.0, "SILO profile ~50 tok: {silo}");
}

#[test]
fn production_stays_occ1_while_nothing_is_evaluated() {
    let registry = sym_v1_registry();
    for (chars, kind) in [
        (120usize, "ACK"),
        (300, "PROGRESS"),
        (900, "RESULT"),
        (4_000, "EVIDENCE"),
    ] {
        let pair = Pair::sym_v1();
        let context = pair.context(&registry, chars, kind);
        let decision = select_with_policy(&context, &SelectionPolicy::default());
        assert_eq!(
            decision,
            SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect),
            "chars={chars}"
        );
    }
}

#[test]
fn production_selects_occ2_once_evaluated() {
    let registry = sym_v1_registry();
    let evaluations = evaluated_sym_v1(69, SILO_REFERENCE_TOKEN_RATIO);
    let pair = Pair::sym_v1();
    let context = SelectionContext {
        evaluations: &evaluations,
        ..pair.context(&registry, 400, "TASK")
    };
    match select_with_policy(&context, &SelectionPolicy::default()) {
        SelectionDecision::Occ2 { dialect, reason } => {
            assert_eq!(dialect, ("sym-v1".to_string(), 1));
            assert_eq!(reason, SelectionReason::Selected);
        }
        other => panic!("expected Occ2, got {other:?}"),
    }
}

#[test]
fn development_policy_exercises_the_wire_but_not_for_small_messages() {
    let registry = sym_v1_registry();
    // Above the floor with SILO-profile economics: OCC-2.
    let pair = Pair::sym_v1();
    let big = pair.context(&registry, 400, "TASK");
    assert!(matches!(
        select_with_policy(&big, &SelectionPolicy::development()),
        SelectionDecision::Occ2 { .. }
    ));
    // Below the floor: OCC-1 even in development.
    let pair = Pair::sym_v1();
    let small = pair.context(&registry, 219, "ACK");
    assert_eq!(
        select_with_policy(&small, &SelectionPolicy::development()),
        SelectionDecision::Occ1(SelectionReason::MessageBelowSizeFloor)
    );
}

#[test]
fn unknown_listener_and_occ1_only_peer_select_occ1() {
    let registry = sym_v1_registry();
    let pair = Pair::sym_v1();
    let unknown =
        SelectionContext::simple(&pair.speaker, None, &registry, 400, "TASK");
    assert_eq!(
        select_with_policy(&unknown, &SelectionPolicy::development()),
        SelectionDecision::Occ1(SelectionReason::UnknownListener)
    );
    let pair = Pair::occ1_only_listener();
    let occ1_only = pair.context(&registry, 400, "TASK");
    assert_eq!(
        select_with_policy(&occ1_only, &SelectionPolicy::development()),
        SelectionDecision::Occ1(SelectionReason::NoSharedDialect)
    );
}

#[test]
fn strikes_and_amortization_guard_the_dev_path() {
    let registry = sym_v1_registry();
    let pair = Pair::sym_v1();
    let struck = SelectionContext {
        strikes: 1,
        ..pair.context(&registry, 400, "TASK")
    };
    assert_eq!(
        select_with_policy(&struck, &SelectionPolicy::development()),
        SelectionDecision::Occ1(SelectionReason::RecentDecodeFailure)
    );
    let pair = Pair::sym_v1();
    let unamortized = SelectionContext {
        messages_remaining: 1,
        ..pair.context(&registry, 400, "TASK")
    };
    // 400 chars at SILO ratio: ~69 tok/msg; break-even ceil(161/69) = 3.
    assert_eq!(codebook_break_even_messages(68.8), 3);
    assert_eq!(
        select_with_policy(&unamortized, &SelectionPolicy::development()),
        SelectionDecision::Occ1(SelectionReason::CodebookNotAmortized)
    );
    let pair = Pair::sym_v1();
    let amortized = SelectionContext {
        codebook_in_context: true,
        messages_remaining: 1,
        ..pair.context(&registry, 400, "TASK")
    };
    match select_with_policy(&amortized, &SelectionPolicy::development()) {
        SelectionDecision::Occ2 { reason, .. } => {
            assert_eq!(reason, SelectionReason::CodebookAmortized)
        }
        other => panic!("expected Occ2, got {other:?}"),
    }
}

#[test]
fn validated_registry_is_honestly_empty_until_evaluation_lands() {
    let registry = validated_registry().expect("builds");
    assert!(registry.is_empty());
    assert!(registry.get("sym-v1", 1).is_none());
    // sym-v1 and the promoted card both ship samples = 0.
    assert!(sym_v1_registry().get("sym-v1", 1).is_some());
    let promoted = PromotedDialect::load_embedded().expect("loads");
    assert_eq!(promoted.production.metrics.samples, 0);
    assert_eq!(promoted.production.metrics.round_trip_fidelity, 0.0);
    // With an evaluation record for sym-v1 only that dialect is admitted.
    let records = evaluated_sym_v1(69, 0.61);
    let filtered = validated_registry_with(&records).expect("builds");
    assert_eq!(filtered.len(), 1);
    assert!(filtered.get("sym-v1", 1).is_some());
    assert!(filtered.get(PROMOTED_DIALECT_ID, 1).is_none());
}

#[test]
fn selection_works_over_the_promoted_card_too() {
    let promoted = PromotedDialect::load_embedded().expect("loads");
    let mut registry = DialectRegistry::new();
    registry.insert(promoted.production.clone()).expect("inserts");
    // A capability that speaks the promoted dialect.
    let peer = OccCapability {
        occ1: true,
        dialects: BTreeMap::from([(
            PROMOTED_DIALECT_ID.to_string(),
            std::collections::BTreeSet::from([1]),
        )]),
        occ3: None,
};
    let mut local = local();
    local
        .dialects
        .insert(PROMOTED_DIALECT_ID.to_string(), std::collections::BTreeSet::from([1]));
    // Unevaluated + production: OCC-1.
    let context = SelectionContext::simple(&local, Some(&peer), &registry, 400, "TASK");
    assert_eq!(
        select_with_policy(&context, &SelectionPolicy::default()),
        SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect)
    );
    // Evaluated + production: OCC-2 in the promoted dialect.
    let mut records = BTreeMap::new();
    records.insert(
        (PROMOTED_DIALECT_ID.to_string(), 1),
        DialectEvaluationRecord {
            samples: 69,
            listener_fidelity_mean: 1.0,
            wire_tokens_vs_occ1: 0.71, // 252.2 B wire vs 353.1 B OCC-1, ~0.71 token ratio
            last_evaluated: Some(1_800_000_000),
        },
    );
    let context = SelectionContext {
        evaluations: &records,
        ..SelectionContext::simple(&local, Some(&peer), &registry, 400, "TASK")
    };
    match select_with_policy(&context, &SelectionPolicy::default()) {
        SelectionDecision::Occ2 { dialect, .. } => {
            assert_eq!(dialect, (PROMOTED_DIALECT_ID.to_string(), 1))
        }
        other => panic!("expected Occ2, got {other:?}"),
    }
}

#[test]
fn card_model_restrictions_are_respected() {
    let mut card = DialectCard::sym_v1();
    card.listener_models = vec!["qwen2.5-0.5b".to_string()];
    let mut registry = DialectRegistry::new();
    registry.insert(card).expect("inserts");
    let pair = Pair::sym_v1();
    let wrong = pair.context(&registry, 400, "TASK");
    assert_eq!(
        select_with_policy(&wrong, &SelectionPolicy::development()),
        SelectionDecision::Occ1(SelectionReason::ModelPairMismatch)
    );
    let pair = Pair::sym_v1();
    let right = SelectionContext {
        listener_model: Some("qwen2.5-0.5b"),
        ..pair.context(&registry, 400, "TASK")
    };
    assert!(matches!(
        select_with_policy(&right, &SelectionPolicy::development()),
        SelectionDecision::Occ2 { .. }
    ));
}
