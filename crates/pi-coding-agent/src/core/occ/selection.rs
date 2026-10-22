//! Adaptive OCC-1 vs OCC-2 selection: deterministic policy v1.
//!
//! Today the send seams hardcode `allow_unevaluated = true`
//! (`subagent_runs.rs` S1/S2) and the shipped `sym-v1` card carries
//! `metrics.samples = 0`, so the tier-2 "evaluated" gate in
//! `select_occ2_dialect` (`agent_messages.rs`) can never fire and every
//! negotiated send becomes OCC-2 regardless of cost. This module is the
//! self-contained replacement decision core: a deterministic selector
//! that picks OCC-1 or `OCC-2(dialect)` from measurable inputs, plus a
//! validated-registry constructor that only exposes evaluated dialects.
//!
//! It is deliberately free of I/O, clocks and daemons: every input is
//! passed in, every threshold is a named constant measured offline
//! (see `reports/occ2opt/CODEBOOK-COST.md` and
//! `reports/occ2opt/IMPL-DIALECT-PROMOTION.md`), and every branch is
//! unit-tested. The integration into the send seams is a small,
//! documented patch for the owning lane (see
//! `reports/occ2opt/SELECTION-INTEGRATION.md`); nothing in this module
//! edits those files.
//!
//! # Measured fixed overheads (Qwen2.5-0.5B tokenizer, offline)
//!
//! | Overhead | Value | Where it applies |
//! |---|---|---|
//! | sym-v1 codebook line | 602 B / 161 tok | injected into the listener prompt on every OCC-2 message today |
//! | OCC-2 wire header | 22 tok (vs `OCC/1` = 4 tok) | per symbolic message |
//! | symbolic payload vs OCC-1 | 337.9 B vs 353.1 B (-4.3%), 167.1 tok vs 153.1 tok (+9.2%) | corpus profile (`msgs_corpus.json`) |
//! | dual-payload fallback | 2.09x wire chars | wire/storage only; stripped from prompts |
//! | SILO benchmark profile | 153.8 B vs 289.8 B (-47%), ~76 tok vs ~126 tok (-40%) | key-dense traffic the evolution harness tunes for |
//!
//! # Policy v1 decision order
//!
//! 1. Unknown listener (no `OccCapability`) -> OCC-1.
//! 2. No shared dialect version -> OCC-1.
//! 3. Dialect not registered locally -> OCC-1.
//! 4. Unevaluated dialect and the policy does not allow unevaluated
//!    -> OCC-1 (production default: both shipped dialects start with
//!    `samples = 0`, so production stays OCC-1 until evaluation lands).
//! 5. Card model restrictions exclude the speaker/listener pair -> OCC-1.
//! 6. Any recent decode failure (strike ledger non-empty for the pair)
//!    -> OCC-1.
//! 7. Message below the small-message floor -> OCC-1.
//! 8. Codebook economics: expected per-message token savings must be
//!    positive, and the 161-token codebook must amortize over the
//!    expected remaining messages unless the listener already holds the
//!    codebook in context.
//! 9. Otherwise -> OCC-2 with the negotiated dialect.
//!
//! The per-message savings model is linear and derived from the two
//! measured profiles: `savings = (1 - token_ratio) * tokens_per_char *
//! message_chars`, where `token_ratio` is the dialect's measured
//! mean wire tokens as a fraction of the OCC-1 encoding when evaluated,
//! or the policy's reference ratio otherwise (corpus-conservative for
//! production, SILO-profile for the development policy so the wire can
//! actually be exercised end to end before evaluation data exists).

use std::collections::BTreeMap;

use crate::core::occ::dialect::{
    DialectCard, DialectRegistry, OccCapability,
};
use crate::core::occ::dialect_load::{
    promoted_registry, DialectLoadError,
};

// ---------------------------------------------------------------------------
// Measured constants (offline, deterministic; do not tune by hand)
// ---------------------------------------------------------------------------

/// The sym-v1 codebook injected into listener prompts: 602 chars.
pub const CODEBOOK_CHARS: usize = 602;
/// The sym-v1 codebook measured in tokens (Qwen2.5-0.5B tokenizer).
pub const CODEBOOK_TOKENS: f64 = 161.0;
/// The OCC-2 wire header in tokens (`OCC/2 D=<id>@<v> C=<12 hex>`).
pub const OCC2_HEADER_TOKENS: f64 = 22.0;
/// The OCC-1 wire header in tokens (`OCC/1`).
pub const OCC1_HEADER_TOKENS: f64 = 4.0;
/// Mean OCC-1 wire tokens per char, measured on the 69-message corpus
/// (153.1 tok / 353.1 B).
pub const OCC1_TOKENS_PER_CHAR: f64 = 0.4336;
/// Corpus-profile wire token ratio (symbolic / OCC-1): 167.1 / 153.1.
/// Symbolic LOSES tokens on natural-language values: savings are
/// negative, so this ratio never passes the economics gate.
pub const CORPUS_REFERENCE_TOKEN_RATIO: f64 = 1.0914435;
/// SILO-profile wire token ratio (symbolic / OCC-1): ~76 / ~126 tok on
/// key-dense benchmark traffic. This is the profile the offline
/// evolution harness tunes for; the development policy uses it so the
/// unevaluated wire can be exercised before evaluation data exists.
pub const SILO_REFERENCE_TOKEN_RATIO: f64 = 0.6032;
/// Messages below this OCC-1 wire size never pay off: fixed overheads
/// (header delta, prompt scaffolding) dominate and key-shortening has
/// too few lines to work on. The corpus ACK/REQUEST_DETAIL floor sits
/// around 220 B.
pub const SMALL_MESSAGE_CHAR_FLOOR: usize = 220;
/// Expected remaining messages to one listener in a task exchange; the
/// SILO-profile amortization break-even is ~4, so 8 leaves headroom.
pub const EXPECTED_REMAINING_MESSAGES: usize = 8;
/// Minimum evaluation samples before a dialect counts as validated.
pub const MIN_VALIDATION_SAMPLES: usize = 30;
/// Minimum mean listener fidelity before a dialect counts as validated
/// (the contract requires exact round-trip fidelity to ship).
pub const MIN_VALIDATION_FIDELITY: f64 = 1.0;

// ---------------------------------------------------------------------------
// Evaluation record
// ---------------------------------------------------------------------------

/// Offline evaluation record for one (dialect, version). The daemon owns
/// where these live; this module only reads them. Mirrors the shipped
/// `DialectMetrics` plus listener-side fidelity and an evaluation
/// timestamp stamped by the harness (never by this module: no clocks
/// here).
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DialectEvaluationRecord {
    /// Messages in the evaluation corpus (0 = unevaluated).
    pub samples: usize,
    /// Mean listener fidelity: fraction of messages the listener
    /// reconstructed exactly.
    pub listener_fidelity_mean: f64,
    /// Mean wire tokens as a fraction of the OCC-1 encoding
    /// (1.0 = parity, below 1.0 = symbolic wins).
    pub wire_tokens_vs_occ1: f64,
    /// Unix seconds of the last evaluation, when one exists.
    pub last_evaluated: Option<u64>,
}

impl DialectEvaluationRecord {
    /// The honest default: no samples, no fidelity, parity ratio.
    pub fn unevaluated() -> Self {
        Self {
            samples: 0,
            listener_fidelity_mean: 0.0,
            wire_tokens_vs_occ1: 1.0,
            last_evaluated: None,
        }
    }

    /// Projects the shipped card metrics into a record. `last_evaluated`
    /// stays `None`: only the harness can stamp a time.
    pub fn from_metrics(metrics: &crate::core::occ::dialect::DialectMetrics) -> Self {
        Self {
            samples: metrics.samples,
            listener_fidelity_mean: metrics.round_trip_fidelity,
            wire_tokens_vs_occ1: metrics.bytes_vs_occ1,
            last_evaluated: None,
        }
    }

    /// True when the record clears the validation gate.
    pub fn is_validated(&self) -> bool {
        self.samples >= MIN_VALIDATION_SAMPLES
            && self.listener_fidelity_mean >= MIN_VALIDATION_FIDELITY
    }
}

impl Default for DialectEvaluationRecord {
    fn default() -> Self {
        Self::unevaluated()
    }
}

// ---------------------------------------------------------------------------
// Policy and decision types
// ---------------------------------------------------------------------------

/// The deterministic selection policy. All fields are data; there is no
/// hidden state, no clock and no I/O.
#[derive(Debug, Clone, PartialEq)]
pub struct SelectionPolicy {
    /// The dev flag: allow unevaluated dialects through the validation
    /// gate. Production keeps this `false`; both shipped dialects start
    /// unevaluated (`samples = 0`), so production stays OCC-1 until
    /// evaluation data lands.
    pub allow_unevaluated: bool,
    /// Reference wire-token ratio used when the dialect is unevaluated.
    /// Production uses the corpus profile (symbolic loses tokens);
    /// development uses the SILO profile so the wire is exercised on
    /// benchmark-shaped traffic.
    pub unevaluated_reference_token_ratio: f64,
    /// OCC-1 wire size below which OCC-2 is never selected.
    pub small_message_char_floor: usize,
    /// Expected further messages to this listener, used for codebook
    /// amortization when the listener does not already hold the codebook.
    pub expected_remaining_messages: usize,
    /// Minimum evaluation samples for the validated registry.
    pub min_validation_samples: usize,
    /// Minimum mean listener fidelity for the validated registry.
    pub min_validation_fidelity: f64,
}

impl Default for SelectionPolicy {
    fn default() -> Self {
        Self {
            allow_unevaluated: false,
            unevaluated_reference_token_ratio: CORPUS_REFERENCE_TOKEN_RATIO,
            small_message_char_floor: SMALL_MESSAGE_CHAR_FLOOR,
            expected_remaining_messages: EXPECTED_REMAINING_MESSAGES,
            min_validation_samples: MIN_VALIDATION_SAMPLES,
            min_validation_fidelity: MIN_VALIDATION_FIDELITY,
        }
    }
}

impl SelectionPolicy {
    /// The development policy: mirrors the current hardcoded
    /// `allow_unevaluated = true` seams but with the SILO-profile
    /// reference ratio, the small-message floor, the strike guard and
    /// codebook amortization still enforced.
    pub fn development() -> Self {
        Self {
            allow_unevaluated: true,
            unevaluated_reference_token_ratio: SILO_REFERENCE_TOKEN_RATIO,
            ..Self::default()
        }
    }

    /// The validation gate for one record.
    fn admits(&self, record: &DialectEvaluationRecord) -> bool {
        record.is_validated_with(self.min_validation_samples, self.min_validation_fidelity)
    }
}

impl DialectEvaluationRecord {
    fn is_validated_with(&self, min_samples: usize, min_fidelity: f64) -> bool {
        self.samples >= min_samples && self.listener_fidelity_mean >= min_fidelity
    }
}

/// Why a decision was reached. Every branch maps to exactly one reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SelectionReason {
    /// The listener advertised no capability (or is unknown).
    UnknownListener,
    /// No shared dialect version between speaker and listener.
    NoSharedDialect,
    /// The negotiated dialect is not registered on this side.
    UnregisteredDialect,
    /// The dialect has no passing evaluation record and the policy
    /// refuses unevaluated dialects.
    UnevaluatedDialect,
    /// The card's model restrictions exclude this speaker/listener pair.
    ModelPairMismatch,
    /// The strike ledger shows a recent decode failure for this pair.
    RecentDecodeFailure,
    /// The message is below the small-message floor.
    MessageBelowSizeFloor,
    /// Expected per-message savings are not positive for this size.
    NoExpectedSavings,
    /// The codebook cannot amortize over the expected remaining
    /// messages.
    CodebookNotAmortized,
    /// OCC-2 selected with the codebook already in the listener's
    /// context (amortization landed).
    CodebookAmortized,
    /// OCC-2 selected on positive expected savings.
    Selected,
}

impl std::fmt::Display for SelectionReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let text = match self {
            SelectionReason::UnknownListener => "unknown listener",
            SelectionReason::NoSharedDialect => "no shared dialect",
            SelectionReason::UnregisteredDialect => "dialect not registered",
            SelectionReason::UnevaluatedDialect => "dialect unevaluated",
            SelectionReason::ModelPairMismatch => "model pair mismatch",
            SelectionReason::RecentDecodeFailure => "recent decode failure",
            SelectionReason::MessageBelowSizeFloor => "message below size floor",
            SelectionReason::NoExpectedSavings => "no expected savings",
            SelectionReason::CodebookNotAmortized => "codebook not amortized",
            SelectionReason::CodebookAmortized => "codebook amortized in context",
            SelectionReason::Selected => "selected on expected savings",
        };
        f.write_str(text)
    }
}

/// The selection outcome: OCC-1, or OCC-2 with the negotiated dialect.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectionDecision {
    /// Send plain OCC-1.
    Occ1(SelectionReason),
    /// Send OCC-2 in the given (dialect id, version).
    Occ2 {
        /// The negotiated dialect.
        dialect: (String, u32),
        /// Why.
        reason: SelectionReason,
    },
}

// ---------------------------------------------------------------------------
// Selection context
// ---------------------------------------------------------------------------

/// One selection input bundle. Everything the policy needs, nothing it
/// does not: the caller (send seam or daemon) supplies capability,
/// registry, evaluation records, ledger state and size estimates.
#[derive(Debug, Clone)]
pub struct SelectionContext<'a> {
    /// This side's capability.
    pub local: &'a OccCapability,
    /// The listener's advertised capability; `None` for an unknown
    /// listener.
    pub listener: Option<&'a OccCapability>,
    /// The speaker's model id, when known.
    pub speaker_model: Option<&'a str>,
    /// The listener's model id, when known.
    pub listener_model: Option<&'a str>,
    /// The dialect cards available for encoding.
    pub registry: &'a DialectRegistry,
    /// Offline evaluation records, keyed by (dialect id, version). Cards
    /// without a record fall back to their own metrics.
    pub evaluations: &'a BTreeMap<(String, u32), DialectEvaluationRecord>,
    /// Estimated OCC-1 wire size of the outgoing message, in chars.
    pub message_chars: usize,
    /// The message kind (informational in v1; compression behavior is
    /// owned by the dialect card, and per-kind savings calibration is a
    /// v2 input).
    pub kind: &'a str,
    /// True when the listener already holds the codebook in context
    /// (the amortization seam): the per-message codebook cost is then 0.
    pub codebook_in_context: bool,
    /// Recent decode failures recorded for this (speaker, listener)
    /// pair. Any strike selects OCC-1.
    pub strikes: usize,
    /// Expected further messages to this listener. When the caller has
    /// no estimate it passes the policy default.
    pub messages_remaining: usize,
}

impl<'a> SelectionContext<'a> {
    /// Convenience builder around a registry-only setup (no evaluation
    /// records, no model ids, no amortization).
    pub fn simple(
        local: &'a OccCapability,
        listener: Option<&'a OccCapability>,
        registry: &'a DialectRegistry,
        message_chars: usize,
        kind: &'a str,
    ) -> Self {
        Self {
            local,
            listener,
            speaker_model: None,
            listener_model: None,
            registry,
            evaluations: &EMPTY_EVALUATIONS,
            message_chars,
            kind,
            codebook_in_context: false,
            strikes: 0,
            messages_remaining: EXPECTED_REMAINING_MESSAGES,
        }
    }
}

static EMPTY_EVALUATIONS: BTreeMap<(String, u32), DialectEvaluationRecord> = BTreeMap::new();

// ---------------------------------------------------------------------------
// The selector
// ---------------------------------------------------------------------------

/// Expected per-message token savings of OCC-2 over OCC-1 for a message
/// of `message_chars` OCC-1 wire chars, under the given wire-token
/// ratio. Linear v1 model: `savings = (1 - ratio) * tokens_per_char *
/// chars`. The ratio is end-to-end (it includes the wire-header delta),
/// measured on whole corpora.
pub fn expected_per_message_savings(message_chars: usize, token_ratio: f64) -> f64 {
    (1.0 - token_ratio) * OCC1_TOKENS_PER_CHAR * message_chars as f64
}

/// How many further messages amortize the codebook at the given
/// per-message savings. `usize::MAX` when savings are not positive.
pub fn codebook_break_even_messages(per_message_savings: f64) -> usize {
    if per_message_savings <= 0.0 {
        usize::MAX
    } else {
        (CODEBOOK_TOKENS / per_message_savings).ceil() as usize
    }
}

/// The record for one card: the daemon's evaluation record when present,
/// otherwise the card's own shipped metrics.
fn evaluation_for<'a>(
    card: &'a DialectCard,
    evaluations: &'a BTreeMap<(String, u32), DialectEvaluationRecord>,
) -> DialectEvaluationRecord {
    evaluations
        .get(&(card.dialect_id.clone(), card.version))
        .cloned()
        .unwrap_or_else(|| DialectEvaluationRecord::from_metrics(&card.metrics))
}

/// Deterministic OCC-1 vs OCC-2 selection (policy v1). Pure: no I/O, no
/// clock, no mutation. See the module docs for the decision order and
/// the measured constants behind every threshold.
pub fn select_with_policy(
    context: &SelectionContext<'_>,
    policy: &SelectionPolicy,
) -> SelectionDecision {
    // 1. Unknown listener.
    let Some(listener) = context.listener else {
        return SelectionDecision::Occ1(SelectionReason::UnknownListener);
    };
    // 2. No shared dialect version.
    let Some((dialect_id, version)) = context.local.negotiate(listener) else {
        return SelectionDecision::Occ1(SelectionReason::NoSharedDialect);
    };
    // 3. Not registered locally.
    let Some(card) = context.registry.get(&dialect_id, version) else {
        return SelectionDecision::Occ1(SelectionReason::UnregisteredDialect);
    };
    // 4. Validation gate.
    let record = evaluation_for(card, context.evaluations);
    if !record.is_validated_with(policy.min_validation_samples, policy.min_validation_fidelity)
        && !policy.allow_unevaluated
    {
        return SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect);
    }
    // 5. Model-pair restrictions (empty lists are unrestricted).
    if !card.speaker_models.is_empty() {
        let admitted = context
            .speaker_model
            .is_some_and(|model| card.speaker_models.iter().any(|m| m == model));
        if !admitted {
            return SelectionDecision::Occ1(SelectionReason::ModelPairMismatch);
        }
    }
    if !card.listener_models.is_empty() {
        let admitted = context
            .listener_model
            .is_some_and(|model| card.listener_models.iter().any(|m| m == model));
        if !admitted {
            return SelectionDecision::Occ1(SelectionReason::ModelPairMismatch);
        }
    }
    // 6. Recent decode failure for this pair.
    if context.strikes > 0 {
        return SelectionDecision::Occ1(SelectionReason::RecentDecodeFailure);
    }
    // 7. Small message.
    if context.message_chars < policy.small_message_char_floor {
        return SelectionDecision::Occ1(SelectionReason::MessageBelowSizeFloor);
    }
    // 8. Codebook economics.
    let token_ratio = if record.is_validated_with(
        policy.min_validation_samples,
        policy.min_validation_fidelity,
    ) || record.samples > 0
    {
        // A measured record exists (even an unevaluated-but-sampled one):
        // trust its measured ratio over the reference profile.
        if record.wire_tokens_vs_occ1 > 0.0 {
            record.wire_tokens_vs_occ1
        } else {
            policy.unevaluated_reference_token_ratio
        }
    } else {
        policy.unevaluated_reference_token_ratio
    };
    let savings = expected_per_message_savings(context.message_chars, token_ratio);
    if savings <= 0.0 {
        return SelectionDecision::Occ1(SelectionReason::NoExpectedSavings);
    }
    if !context.codebook_in_context {
        let needed = codebook_break_even_messages(savings);
        if context.messages_remaining < needed {
            return SelectionDecision::Occ1(SelectionReason::CodebookNotAmortized);
        }
    }
    // 9. OCC-2.
    SelectionDecision::Occ2 {
        reason: if context.codebook_in_context {
            SelectionReason::CodebookAmortized
        } else {
            SelectionReason::Selected
        },
        dialect: (dialect_id, version),
    }
}

// ---------------------------------------------------------------------------
// Validated registry
// ---------------------------------------------------------------------------

/// A registry containing only dialects with a passing evaluation record:
/// `samples >= MIN_VALIDATION_SAMPLES` and listener fidelity
/// `>= MIN_VALIDATION_FIDELITY`. Built on top of
/// [`promoted_registry`] (sym-v1 plus the promoted research card).
///
/// Today this returns an **empty** registry: both `sym-v1` and
/// `sym-v1-pt-pt-r1` ship `samples = 0` and are honestly unevaluated.
/// An empty registry makes every send OCC-1, which is the intended
/// conservative production default until offline evaluation data lands.
pub fn validated_registry() -> Result<DialectRegistry, DialectLoadError> {
    let records = BTreeMap::new();
    validated_registry_with(&records)
}

/// [`validated_registry`] with explicit evaluation records (the daemon's
/// evaluation store). Cards without a record fall back to their own
/// shipped metrics, which for both shipped dialects is unevaluated.
pub fn validated_registry_with(
    records: &BTreeMap<(String, u32), DialectEvaluationRecord>,
) -> Result<DialectRegistry, DialectLoadError> {
    let source = promoted_registry()?;
    let mut registry = DialectRegistry::new();
    for card in source.iter() {
        let record = evaluation_for(card, records);
        if record.is_validated() {
            registry
                .insert(card.clone())
                .map_err(|error| DialectLoadError::Translation(error.to_string()))?;
        }
    }
    Ok(registry)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::occ::dialect::{DialectCard, DialectMetrics};

    fn local() -> OccCapability {
        OccCapability::here()
    }

    fn peer_sym_v1() -> OccCapability {
        OccCapability::here()
    }

    fn eval_validated(samples: usize, ratio: f64) -> BTreeMap<(String, u32), DialectEvaluationRecord> {
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
    fn constants_match_the_measured_reports() {
        // CODEBOOK-COST.md: 602 B / 161 tok codebook, 22 vs 4 tok headers.
        assert_eq!(CODEBOOK_CHARS, 602);
        assert_eq!(CODEBOOK_TOKENS, 161.0);
        assert_eq!(OCC2_HEADER_TOKENS - OCC1_HEADER_TOKENS, 18.0);
        // Corpus profile: symbolic loses tokens (167.1 / 153.1).
        assert!((CORPUS_REFERENCE_TOKEN_RATIO - 167.1 / 153.1).abs() < 1e-9);
        // SILO profile: symbolic wins (~76 / ~126).
        assert!((SILO_REFERENCE_TOKEN_RATIO - 76.0 / 126.0).abs() < 1e-2);
    }

    #[test]
    fn unknown_listener_selects_occ1() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let context =
            SelectionContext::simple(&speaker, None, &registry, 400, "TASK");
        let decision = select_with_policy(&context, &SelectionPolicy::default());
        assert_eq!(decision, SelectionDecision::Occ1(SelectionReason::UnknownListener));
    }

    #[test]
    fn no_shared_dialect_selects_occ1() {
        let registry = DialectRegistry::with_sym_v1();
        let occ1_only = OccCapability {
            occ1: true,
            dialects: BTreeMap::new(),
            occ3: None,
};
        let speaker = local();
        let context =
            SelectionContext::simple(&speaker, Some(&occ1_only), &registry, 400, "TASK");
        let decision = select_with_policy(&context, &SelectionPolicy::default());
        assert_eq!(decision, SelectionDecision::Occ1(SelectionReason::NoSharedDialect));
    }

    #[test]
    fn unregistered_dialect_selects_occ1() {
        let registry = DialectRegistry::new();
        let speaker = local();
        let listener = peer_sym_v1();
        let context =
            SelectionContext::simple(&speaker, Some(&listener), &registry, 400, "TASK");
        let decision = select_with_policy(&context, &SelectionPolicy::development());
        assert_eq!(decision, SelectionDecision::Occ1(SelectionReason::UnregisteredDialect));
    }

    #[test]
    fn unevaluated_dialect_is_refused_in_production() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        let context =
            SelectionContext::simple(&speaker, Some(&listener), &registry, 400, "TASK");
        let decision = select_with_policy(&context, &SelectionPolicy::default());
        assert_eq!(
            decision,
            SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect)
        );
    }

    #[test]
    fn unevaluated_dialect_can_pass_in_development() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        let context = SelectionContext::simple(
            &speaker,
            Some(&listener),
            &registry,
            400,
            "TASK",
        );
        let decision = select_with_policy(&context, &SelectionPolicy::development());
        assert!(matches!(decision, SelectionDecision::Occ2 { .. }));
    }

    #[test]
    fn model_pair_mismatch_selects_occ1() {
        let mut card = DialectCard::sym_v1();
        card.speaker_models = vec!["some-other-model".to_string()];
        let mut registry = DialectRegistry::new();
        registry.insert(card).expect("card inserts");
        let speaker = local();
        let listener = peer_sym_v1();
        let context = SelectionContext::simple(
            &speaker,
            Some(&listener),
            &registry,
            400,
            "TASK",
        );
        let decision = select_with_policy(&context, &SelectionPolicy::development());
        assert_eq!(decision, SelectionDecision::Occ1(SelectionReason::ModelPairMismatch));
    }

    #[test]
    fn model_pair_match_selects_occ2() {
        let mut card = DialectCard::sym_v1();
        card.speaker_models = vec!["qwen2.5-0.5b".to_string()];
        card.listener_models = vec!["qwen2.5-0.5b".to_string()];
        let mut registry = DialectRegistry::new();
        registry.insert(card).expect("card inserts");
        let speaker = local();
        let listener = peer_sym_v1();
        let context = SelectionContext {
            speaker_model: Some("qwen2.5-0.5b"),
            listener_model: Some("qwen2.5-0.5b"),
            ..SelectionContext::simple(
                &speaker,
                Some(&listener),
                &registry,
                400,
                "TASK",
            )
        };
        let decision = select_with_policy(&context, &SelectionPolicy::development());
        assert!(matches!(decision, SelectionDecision::Occ2 { .. }));
    }

    #[test]
    fn strike_selects_occ1() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        let context = SelectionContext {
            strikes: 1,
            ..SelectionContext::simple(
                &speaker,
                Some(&listener),
                &registry,
                400,
                "TASK",
            )
        };
        let decision = select_with_policy(&context, &SelectionPolicy::development());
        assert_eq!(decision, SelectionDecision::Occ1(SelectionReason::RecentDecodeFailure));
    }

    #[test]
    fn small_message_selects_occ1() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        for chars in [0, 1, SMALL_MESSAGE_CHAR_FLOOR - 1] {
            let context = SelectionContext::simple(
                &speaker,
                Some(&listener),
                &registry,
                chars,
                "ACK",
            );
            let decision = select_with_policy(&context, &SelectionPolicy::development());
            assert_eq!(
                decision,
                SelectionDecision::Occ1(SelectionReason::MessageBelowSizeFloor)
            );
        }
    }

    #[test]
    fn corpus_reference_ratio_has_no_savings() {
        // Production reference: symbolic loses tokens on prose-heavy
        // traffic, so even a large message is refused.
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        let context = SelectionContext::simple(
            &speaker,
            Some(&listener),
            &registry,
            4_000,
            "RESULT",
        );
        let mut policy = SelectionPolicy::development();
        policy.unevaluated_reference_token_ratio = CORPUS_REFERENCE_TOKEN_RATIO;
        let decision = select_with_policy(&context, &policy);
        assert_eq!(decision, SelectionDecision::Occ1(SelectionReason::NoExpectedSavings));
    }

    #[test]
    fn codebook_not_amortized_selects_occ1() {
        let registry = DialectRegistry::with_sym_v1();
        // SILO-profile savings at 400 chars: (1 - 0.6032) * 0.4336 * 400
        // ~= 68.8 tok; break-even = ceil(161 / 68.8) = 3 messages.
        let speaker = local();
        let listener = peer_sym_v1();
        let context = SelectionContext::simple(
            &speaker,
            Some(&listener),
            &registry,
            400,
            "TASK",
        );
        assert_eq!(codebook_break_even_messages(68.8), 3);
        let short = SelectionContext {
            messages_remaining: 2,
            ..SelectionContext::simple(
                &speaker,
                Some(&listener),
                &registry,
                400,
                "TASK",
            )
        };
        let decision = select_with_policy(&short, &SelectionPolicy::development());
        assert_eq!(
            decision,
            SelectionDecision::Occ1(SelectionReason::CodebookNotAmortized)
        );
    }

    #[test]
    fn codebook_in_context_selects_occ2_without_amortization() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        let context = SelectionContext {
            codebook_in_context: true,
            messages_remaining: 1,
            ..SelectionContext::simple(
                &speaker,
                Some(&listener),
                &registry,
                400,
                "TASK",
            )
        };
        let decision = select_with_policy(&context, &SelectionPolicy::development());
        match decision {
            SelectionDecision::Occ2 { reason, .. } => {
                assert_eq!(reason, SelectionReason::CodebookAmortized)
            }
            other => panic!("expected Occ2, got {other:?}"),
        }
    }

    #[test]
    fn validated_dialect_is_selected_in_production() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        // Evaluated record with SILO-profile token ratio.
        let evaluations = eval_validated(64, SILO_REFERENCE_TOKEN_RATIO);
        let context = SelectionContext {
            evaluations: &evaluations,
            ..SelectionContext::simple(
                &speaker,
                Some(&listener),
                &registry,
                400,
                "TASK",
            )
        };
        let decision = select_with_policy(&context, &SelectionPolicy::default());
        match decision {
            SelectionDecision::Occ2 { dialect, .. } => {
                assert_eq!(dialect, ("sym-v1".to_string(), 1))
            }
            other => panic!("expected Occ2, got {other:?}"),
        }
    }

    #[test]
    fn evaluated_but_low_fidelity_is_refused() {
        let registry = DialectRegistry::with_sym_v1();
        let speaker = local();
        let listener = peer_sym_v1();
        let mut evaluations = eval_validated(64, SILO_REFERENCE_TOKEN_RATIO);
        evaluations.insert(
            ("sym-v1".to_string(), 1),
            DialectEvaluationRecord {
                samples: 64,
                listener_fidelity_mean: 0.98,
                wire_tokens_vs_occ1: SILO_REFERENCE_TOKEN_RATIO,
                last_evaluated: Some(1_800_000_000),
            },
        );
        let context = SelectionContext {
            evaluations: &evaluations,
            ..SelectionContext::simple(
                &speaker,
                Some(&listener),
                &registry,
                400,
                "TASK",
            )
        };
        let decision = select_with_policy(&context, &SelectionPolicy::default());
        assert_eq!(
            decision,
            SelectionDecision::Occ1(SelectionReason::UnevaluatedDialect)
        );
    }

    #[test]
    fn validated_registry_is_empty_until_evaluation_lands() {
        // Both shipped dialects are unevaluated: the validated registry
        // must be empty, not silently permissive.
        let registry = validated_registry().expect("builds");
        assert!(registry.is_empty());
        assert!(registry.get("sym-v1", 1).is_none());
    }

    #[test]
    fn validated_registry_admits_only_passing_records() {
        let mut records = BTreeMap::new();
        records.insert(
            ("sym-v1".to_string(), 1),
            DialectEvaluationRecord {
                samples: 69,
                listener_fidelity_mean: 1.0,
                wire_tokens_vs_occ1: 0.61,
                last_evaluated: Some(1_800_000_000),
            },
        );
        let registry = validated_registry_with(&records).expect("builds");
        assert_eq!(registry.len(), 1);
        assert!(registry.get("sym-v1", 1).is_some());
        // The promoted card has no record and stays out.
        assert!(registry.get("sym-v1-pt-pt-r1", 1).is_none());
    }

    #[test]
    fn savings_and_break_even_arithmetic() {
        // SILO profile at the corpus-mean message: 353 chars.
        let savings = expected_per_message_savings(353, SILO_REFERENCE_TOKEN_RATIO);
        assert!((savings - 60.7).abs() < 1.0, "savings {savings}");
        // Break-even: ceil(161 / 60.7) = 3.
        assert_eq!(codebook_break_even_messages(savings), 3);
        // Corpus profile never pays.
        let corpus = expected_per_message_savings(353, CORPUS_REFERENCE_TOKEN_RATIO);
        assert!(corpus < 0.0);
        assert_eq!(codebook_break_even_messages(corpus), usize::MAX);
        // Codebook amortized: 161 tokens spread over break-even messages.
        assert!((savings * 3.0 - CODEBOOK_TOKENS).abs() < savings);
    }

    #[test]
    fn reason_display_is_stable() {
        assert_eq!(SelectionReason::UnknownListener.to_string(), "unknown listener");
        assert_eq!(
            SelectionReason::CodebookAmortized.to_string(),
            "codebook amortized in context"
        );
    }

    #[test]
    fn evaluation_record_falls_back_to_card_metrics() {
        let card = DialectCard::sym_v1();
        let record = evaluation_for(&card, &BTreeMap::new());
        assert_eq!(record.samples, 0);
        assert!(!record.is_validated());
        // from_metrics projects the shipped metrics.
        let metrics = DialectMetrics {
            samples: 69,
            bytes_vs_occ1: 0.53,
            round_trip_fidelity: 1.0,
            mean_latency_us: 12.0,
        };
        let projected = DialectEvaluationRecord::from_metrics(&metrics);
        assert_eq!(projected.samples, 69);
        assert_eq!(projected.wire_tokens_vs_occ1, 0.53);
        assert_eq!(projected.last_evaluated, None);
        assert!(projected.is_validated());
    }
}
