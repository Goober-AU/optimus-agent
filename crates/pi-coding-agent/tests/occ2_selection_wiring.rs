//! Offline, deterministic OCC-2 selection-wiring suite (efficiency
//! campaign, selection integration).
//!
//! Exercises the production send-seam decision assembly
//! (`occ2_send_policy_from_selection`) over real registries, real
//! evaluation-store files and real strike-ledger files:
//!
//! - `w01` production default with an empty evaluation store stays
//!   OCC-1 (both shipped dialects are honestly unevaluated).
//! - `w02` the experimental (`/occ 2`) path selects OCC-2 for a big
//!   message through the development policy, with and without the
//!   codebook already amortized in the listener's context.
//! - `w03` a strike-disabled pair stays OCC-1 even on the experimental
//!   path, read back from a real persisted strike ledger.
//! - `w04` a validated evaluation record flips the production policy to
//!   OCC-2, read back from a real persisted evaluation store.
//! - `w05` the evaluation store round-trips and fails open.
//! - `w06` a validated but losing record (measured ratio above 1.0)
//!   keeps OCC-1 on both policies.
//! - `w07` the small-message floor holds on the experimental path.
//!
//! Everything here is offline and deterministic: no network, no model
//! APIs, no credentials, no randomness; the only filesystem access is
//! tempdirs.

use std::collections::BTreeMap;

use pi_coding_agent::core::agent_messages::{
    load_occ2_evaluations, occ2_evaluations_path, occ2_pair_key, occ2_send_policy_from_selection,
    occ2_strike_pair_incidents, save_occ2_evaluations, save_occ2_strike_ledger, Occ2SendPolicy,
    Occ2StrikeLedger,
};
use pi_coding_agent::core::occ::dialect::{DialectRegistry, OccCapability};
use pi_coding_agent::core::occ::selection::{
    DialectEvaluationRecord, SelectionContext, SelectionDecision, SelectionReason,
    EXPECTED_REMAINING_MESSAGES,
};

/// A fake session file path inside a tempdir; only the path shape
/// matters (the artifact directory is derived from it).
fn session_file(root: &std::path::Path, id: &str) -> String {
    root.join("sessions")
        .join(format!("{id}.jsonl"))
        .to_string_lossy()
        .into_owned()
}

fn empty_evaluations() -> BTreeMap<(String, u32), DialectEvaluationRecord> {
    BTreeMap::new()
}

fn validated_evaluations(
    dialect: &str,
    version: u32,
    ratio: f64,
) -> BTreeMap<(String, u32), DialectEvaluationRecord> {
    let mut records = BTreeMap::new();
    records.insert(
        (dialect.to_string(), version),
        DialectEvaluationRecord {
            samples: 30,
            listener_fidelity_mean: 1.0,
            wire_tokens_vs_occ1: ratio,
            last_evaluated: Some(1_800_000_000),
        },
    );
    records
}

#[test]
fn w01_production_default_is_occ1_with_an_empty_evaluation_store() {
    let local = OccCapability::here();
    let registry = DialectRegistry::with_sym_v1();
    let evaluations = empty_evaluations();
    let context = SelectionContext {
        local: &local,
        listener: Some(&local),
        speaker_model: None,
        listener_model: None,
        registry: &registry,
        evaluations: &evaluations,
        message_chars: 2_000,
        kind: "RESULT",
        codebook_in_context: true,
        strikes: 0,
        messages_remaining: EXPECTED_REMAINING_MESSAGES,
    };

    // Production: the unevaluated dialect is refused outright.
    let outcome = occ2_send_policy_from_selection(&context, false);
    assert_eq!(
        outcome.production,
        SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect)
    );
    assert!(outcome.development.is_none(), "no experimental fallback");
    assert_eq!(outcome.policy, Occ2SendPolicy::disabled());
}

#[test]
fn w02_experimental_path_selects_occ2_for_big_messages() {
    let local = OccCapability::here();
    let registry = DialectRegistry::with_sym_v1();
    let evaluations = empty_evaluations();

    // Codebook already amortized in the listener's context.
    let context = SelectionContext {
        local: &local,
        listener: Some(&local),
        speaker_model: None,
        listener_model: None,
        registry: &registry,
        evaluations: &evaluations,
        message_chars: 2_000,
        kind: "RESULT",
        codebook_in_context: true,
        strikes: 0,
        messages_remaining: EXPECTED_REMAINING_MESSAGES,
    };
    let outcome = occ2_send_policy_from_selection(&context, true);
    assert_eq!(
        outcome.production,
        SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect)
    );
    let development = outcome.development.clone().expect("experimental fallback ran");
    assert!(
        matches!(development, SelectionDecision::Occ2 { .. }),
        "development selects OCC-2: {development:?}"
    );
    assert!(outcome.policy.enabled);
    assert_eq!(outcome.policy.dialect, Some(("sym-v1".to_string(), 1)));

    // Codebook not yet amortized: the default expected-remaining window
    // (8) still clears the SILO-profile break-even for a big message.
    let context = SelectionContext {
        codebook_in_context: false,
        ..context_owned(&local, &registry, &evaluations)
    };
    let outcome = occ2_send_policy_from_selection(&context, true);
    assert!(outcome.policy.enabled, "amortization over 8 messages");
}

/// Clones the shared context with a fresh borrow of the same inputs
/// (the struct borrows; this helper keeps the test bodies readable).
fn context_owned<'a>(
    local: &'a OccCapability,
    registry: &'a DialectRegistry,
    evaluations: &'a BTreeMap<(String, u32), DialectEvaluationRecord>,
) -> SelectionContext<'a> {
    SelectionContext {
        local,
        listener: Some(local),
        speaker_model: None,
        listener_model: None,
        registry,
        evaluations,
        message_chars: 2_000,
        kind: "RESULT",
        codebook_in_context: true,
        strikes: 0,
        messages_remaining: EXPECTED_REMAINING_MESSAGES,
    }
}

#[test]
fn w03_strike_disabled_pair_stays_occ1_even_on_the_experimental_path() {
    // A real persisted strike ledger with one incident for the pair.
    let root = tempfile::tempdir().unwrap();
    let listener_file = session_file(root.path(), "listener-1");
    let pair = occ2_pair_key("speaker-1", "listener-1", "sym-v1", 1);
    let mut ledger = Occ2StrikeLedger::new();
    ledger.record_incident_at(&pair, 1_000);
    save_occ2_strike_ledger(&listener_file, &ledger).unwrap();

    // The selection input reads the count back from the file.
    let strikes = occ2_strike_pair_incidents(
        &listener_file,
        "speaker-1",
        "listener-1",
        ("sym-v1", 1),
        2_000,
    );
    assert_eq!(strikes, 1, "the persisted incident is the selection input");

    let local = OccCapability::here();
    let registry = DialectRegistry::with_sym_v1();
    let evaluations = empty_evaluations();
    let mut context = context_owned(&local, &registry, &evaluations);
    context.strikes = strikes;
    let outcome = occ2_send_policy_from_selection(&context, true);
    // Gate order: the unevaluated check fires before strikes, so the
    // production refusal here is UnevaluatedDialect; the experimental
    // fallback then refuses on the strike (its own gate 6).
    assert_eq!(
        outcome.production,
        SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect)
    );
    let development = outcome.development.clone().expect("the fallback ran");
    assert_eq!(
        development,
        SelectionDecision::Occ1(SelectionReason::RecentDecodeFailure)
    );
    assert_eq!(outcome.policy, Occ2SendPolicy::disabled());

    // Another pair's strikes never leak into this one.
    assert_eq!(
        occ2_strike_pair_incidents(
            &listener_file,
            "speaker-2",
            "listener-1",
            ("sym-v1", 1),
            2_000
        ),
        0
    );
    assert_eq!(
        occ2_strike_pair_incidents(
            &listener_file,
            "speaker-1",
            "listener-1",
            ("sym-b", 1),
            2_000
        ),
        0
    );
}

#[test]
fn w04_validated_record_flips_production_to_occ2() {
    let local = OccCapability::here();
    let registry = DialectRegistry::with_sym_v1();

    // A validated record through the real store: save, load, select.
    let root = tempfile::tempdir().unwrap();
    let sender_file = session_file(root.path(), "sender-1");
    let records = validated_evaluations("sym-v1", 1, 0.60);
    save_occ2_evaluations(&sender_file, &records).unwrap();
    let loaded = load_occ2_evaluations(&sender_file);
    assert_eq!(loaded, records, "the store round-trips");

    let context = context_owned(&local, &registry, &loaded);
    let outcome = occ2_send_policy_from_selection(&context, false);
    assert!(
        matches!(outcome.production, SelectionDecision::Occ2 { .. }),
        "validated economics select OCC-2 in production: {:?}",
        outcome.production
    );
    assert!(outcome.development.is_none());
    assert!(outcome.policy.enabled);
    assert_eq!(outcome.policy.dialect, Some(("sym-v1".to_string(), 1)));
}

#[test]
fn w05_evaluation_store_fails_open() {
    let root = tempfile::tempdir().unwrap();
    let sender_file = session_file(root.path(), "sender-1");

    // Missing file: an empty store.
    assert!(load_occ2_evaluations(&sender_file).is_empty());

    // Corrupt or wrong-schema content: still an empty store, never an
    // error path for the sender.
    let path = occ2_evaluations_path(&sender_file);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(&path, b"not json at all").unwrap();
    assert!(load_occ2_evaluations(&sender_file).is_empty());
    std::fs::write(&path, b"{\"schema\":\"something.else\",\"records\":[]}").unwrap();
    assert!(load_occ2_evaluations(&sender_file).is_empty());
}

#[test]
fn w06_validated_but_losing_record_keeps_occ1_on_both_policies() {
    let local = OccCapability::here();
    let registry = DialectRegistry::with_sym_v1();
    let records = validated_evaluations("sym-v1", 1, 1.20);
    let context = context_owned(&local, &registry, &records);

    // Production: measured savings are negative.
    let outcome = occ2_send_policy_from_selection(&context, false);
    assert_eq!(
        outcome.production,
        SelectionDecision::Occ1(SelectionReason::NoExpectedSavings)
    );

    // Experimental: a measured refusal is final - the assembly never
    // routes a validated-but-losing dialect through the dev fallback,
    // and the dev policy itself would refuse it too (the measured
    // record governs once samples > 0).
    let outcome = occ2_send_policy_from_selection(&context, true);
    assert_eq!(
        outcome.production,
        SelectionDecision::Occ1(SelectionReason::NoExpectedSavings)
    );
    assert!(outcome.development.is_none(), "no dev fallback for a measured refusal");
    assert_eq!(outcome.policy, Occ2SendPolicy::disabled());
    let development = pi_coding_agent::core::occ::selection::select_with_policy(
        &context,
        &pi_coding_agent::core::occ::selection::SelectionPolicy::development(),
    );
    assert_eq!(
        development,
        SelectionDecision::Occ1(SelectionReason::NoExpectedSavings)
    );
}

#[test]
fn w07_small_message_floor_holds_on_the_experimental_path() {
    let local = OccCapability::here();
    let registry = DialectRegistry::with_sym_v1();
    let evaluations = empty_evaluations();
    let mut context = context_owned(&local, &registry, &evaluations);
    context.message_chars = 200; // below the 220-char floor
    let outcome = occ2_send_policy_from_selection(&context, true);
    let development = outcome.development.expect("the fallback ran");
    assert_eq!(
        development,
        SelectionDecision::Occ1(SelectionReason::MessageBelowSizeFloor)
    );
    assert_eq!(outcome.policy, Occ2SendPolicy::disabled());
}
