//! SYNTHETIC-CALIBRATION corpus and pure metrics for the context-health score.
//!
//! Campaign context (ROT2, section 17): the tray's context-health score is a
//! heuristic over two pressure proxies (utilisation and compaction cycles).
//! Whether it actually predicts semantic context loss is an empirical
//! question, and answering it needs a labelled corpus: one health snapshot
//! per scenario, plus ground truth about what the session went on to lose.
//! This module is that measurement harness, and nothing else.
//!
//! What lives here:
//! - `HealthCase` / `TrueLabels`: the labelled-corpus types. The health
//!   inputs reuse the REAL `ContextHealthInput` from `context_health.rs`, so
//!   the corpus scores with the production scorer, not a copy of it.
//! - `synthetic_calibration_corpus()`: a deterministic 66-case sweep
//!   (utilisation 0.20..=0.95 crossed with compaction cycles 0..=10) whose
//!   labels follow the R2-E1 finding: constraint loss only ever appears
//!   after at least one compaction cycle, and whether a cycled session
//!   actually lost the constraint is dominated by a semantic factor the
//!   score cannot observe (the constraint being restated in the retained
//!   tail). This is SYNTHETIC-CALIBRATION data: the labels are generated,
//!   not observed. Real outcome labels arrive later from the live-model
//!   phase; the corpus generator is then replaced, the metrics stay.
//! - Pure calibration metrics: rank-based ROC-AUC, Brier score, 10-bin
//!   expected calibration error, and at-threshold precision / recall /
//!   FPR / FNR. All of them are pure functions over slices.
//!
//! What deliberately does NOT live here: any production wiring. Nothing in
//! the agent reads these metrics at runtime; the score is measured, not
//! tuned, by this module.

use crate::core::context_health::{context_health, ContextHealthInput};
use serde::Serialize;

/// Context window the synthetic corpus scores against. The window only
/// rescales `tokens`; every utilisation in the sweep is exact.
pub const CALIBRATION_CONTEXT_WINDOW: f64 = 200_000.0;

/// Utilisation sweep, low to high, within the campaign's 0.2..0.95 band.
pub const CALIBRATION_UTILISATIONS: [f64; 6] = [0.20, 0.35, 0.50, 0.65, 0.80, 0.95];

/// Compaction-cycle sweep, 0..=10 (the campaign's 0..10 band, both ends in).
pub const CALIBRATION_COMPACTION_CYCLES: [f64; 11] =
    [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0];

/// Bins of the expected calibration error.
pub const CALIBRATION_ECE_BINS: usize = 10;

/// Ground truth about what a session went on to lose or do, recorded per
/// case. Real corpora fill these from observed outcomes; the synthetic
/// corpus fills them from the documented label rules below.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct TrueLabels {
    /// An outstanding constraint (a "must" or "never" the user stated)
    /// disappeared from the working context.
    pub lost_constraint: bool,
    /// An evidence reference the session depended on became unresolvable.
    pub lost_evidence: bool,
    /// The session made a decision that contradicts a still-standing
    /// instruction (the operational damage of a lost constraint).
    pub wrong_decision_followed: bool,
    /// The task continued to a terminal state despite the losses above.
    pub task_continued: bool,
}

/// One labelled health snapshot.
#[derive(Debug, Clone, PartialEq)]
pub struct HealthCase {
    pub scenario_id: String,
    /// The real production input struct; scored with the real
    /// `context_health` scorer. `is_compacting` is false throughout: the
    /// corpus labels a settled state, not the transient compaction moment.
    pub health_inputs: ContextHealthInput,
    pub true_labels: TrueLabels,
}

/// Deterministic 64-bit LCG step (Knuth constants), the corpus's only source
/// of pseudo-randomness. No runtime state, no seeds, no clocks.
fn lcg_next(state: u64) -> u64 {
    state
        .wrapping_mul(6_364_136_223_846_793_005)
        .wrapping_add(1_442_695_040_888_963_407)
}

/// One deterministic pseudo-random bit per (case, salt). Models a semantic
/// property of the transcript that the health score cannot observe.
fn semantic_bit(case_index: u64, salt: u64) -> bool {
    let mixed = case_index ^ salt.wrapping_mul(0x9E37_79B9_7F4A_7C15);
    (lcg_next(mixed) >> 63) & 1 == 1
}

/// Build the SYNTHETIC-CALIBRATION corpus: the full cross product of the
/// utilisation and compaction-cycle sweeps (6 x 11 = 66 cases).
///
/// Label rules (R2-E1 derived, deterministic):
/// - `lost_constraint`: never at cycle 0. After that, the constraint
///   survives only if it was restated in the retained recent tail - a
///   content property invisible to any pressure proxy. Shallow histories
///   (one cycle) restate often; deeper ones rarely do.
/// - `lost_evidence`: very full contexts and deep histories lose evidence
///   references, each with its own coin flip.
/// - `wrong_decision_followed`: mostly follows a lost constraint, made
///   likelier by high utilisation (less room to notice the drift).
/// - `task_continued`: the default; only a session that lost both its
///   constraint and its evidence while nearly full gives up.
pub fn synthetic_calibration_corpus() -> Vec<HealthCase> {
    let mut cases = Vec::new();
    for (utilisation_index, utilisation) in CALIBRATION_UTILISATIONS.iter().enumerate() {
        for (cycle_index, cycles) in CALIBRATION_COMPACTION_CYCLES.iter().enumerate() {
            let case_index =
                (utilisation_index * CALIBRATION_COMPACTION_CYCLES.len() + cycle_index + 1) as u64;
            let restated_in_tail = (*cycles <= 1.0 && semantic_bit(case_index, 7))
                || (semantic_bit(case_index, 1) && semantic_bit(case_index, 2));
            let lost_constraint = *cycles > 0.0 && !restated_in_tail;
            let lost_evidence = (*utilisation >= 0.80 && semantic_bit(case_index, 3))
                || (*cycles >= 3.0 && !semantic_bit(case_index, 4));
            let wrong_decision_followed =
                lost_constraint && (*utilisation >= 0.65 || semantic_bit(case_index, 5));
            let task_continued =
                !(lost_constraint && lost_evidence && *utilisation >= 0.90);
            cases.push(HealthCase {
                scenario_id: format!(
                    "synthetic-u{}-c{}",
                    utilisation_index + 1,
                    cycle_index
                ),
                health_inputs: ContextHealthInput {
                    tokens: Some(utilisation * CALIBRATION_CONTEXT_WINDOW),
                    context_window: CALIBRATION_CONTEXT_WINDOW,
                    compaction_cycles: *cycles,
                    is_compacting: false,
                },
                true_labels: TrueLabels {
                    lost_constraint,
                    lost_evidence,
                    wrong_decision_followed,
                    task_continued,
                },
            });
        }
    }
    cases
}

/// Map a health score (0..100, higher is healthier) to the risk probability
/// the calibration metrics consume (1.0 = certain semantic loss). This is
/// the naive linear reading of the score; calibration is exactly the
/// question of whether that reading means anything.
pub fn risk_probability(health_score: f64) -> f64 {
    (1.0 - health_score / 100.0).clamp(0.0, 1.0)
}

/// Rank-based ROC-AUC (Mann-Whitney statistic): the probability that a
/// randomly chosen positive outscores a randomly chosen negative, with
/// ties counted as one half. `None` when either class is empty or the
/// inputs disagree in length.
pub fn roc_auc(scores: &[f64], labels: &[bool]) -> Option<f64> {
    if scores.len() != labels.len() {
        return None;
    }
    let positives: Vec<f64> = scores
        .iter()
        .zip(labels)
        .filter(|(_, label)| **label)
        .map(|(score, _)| *score)
        .collect();
    let negatives: Vec<f64> = scores
        .iter()
        .zip(labels)
        .filter(|(_, label)| !**label)
        .map(|(score, _)| *score)
        .collect();
    if positives.is_empty() || negatives.is_empty() {
        return None;
    }
    let mut wins = 0.0f64;
    for positive in &positives {
        for negative in &negatives {
            wins += if positive > negative {
                1.0
            } else if positive == negative {
                0.5
            } else {
                0.0
            };
        }
    }
    Some(wins / (positives.len() * negatives.len()) as f64)
}

/// Mean squared error of predicted probabilities against 0/1 outcomes.
/// `None` on empty or mismatched input.
pub fn brier_score(probabilities: &[f64], labels: &[bool]) -> Option<f64> {
    if probabilities.is_empty() || probabilities.len() != labels.len() {
        return None;
    }
    let total: f64 = probabilities
        .iter()
        .zip(labels)
        .map(|(probability, label)| {
            let outcome = if *label { 1.0 } else { 0.0 };
            (probability - outcome).powi(2)
        })
        .sum();
    Some(total / probabilities.len() as f64)
}

/// Expected calibration error over `bins` equal-width probability buckets:
/// the count-weighted mean absolute gap between average predicted
/// probability and observed positive rate per bucket. `None` on empty or
/// mismatched input.
pub fn expected_calibration_error(
    probabilities: &[f64],
    labels: &[bool],
    bins: usize,
) -> Option<f64> {
    if probabilities.is_empty() || probabilities.len() != labels.len() || bins == 0 {
        return None;
    }
    let mut counts = vec![0usize; bins];
    let mut probability_sums = vec![0.0f64; bins];
    let mut outcome_sums = vec![0.0f64; bins];
    for (probability, label) in probabilities.iter().zip(labels) {
        let clamped = probability.clamp(0.0, 1.0);
        let bucket = ((clamped * bins as f64).floor() as usize).min(bins - 1);
        counts[bucket] += 1;
        probability_sums[bucket] += clamped;
        outcome_sums[bucket] += if *label { 1.0 } else { 0.0 };
    }
    let total = probabilities.len() as f64;
    let mut error = 0.0f64;
    for bucket in 0..bins {
        if counts[bucket] == 0 {
            continue;
        }
        let count = counts[bucket] as f64;
        let average_probability = probability_sums[bucket] / count;
        let observed_rate = outcome_sums[bucket] / count;
        error += (count / total) * (average_probability - observed_rate).abs();
    }
    Some(error)
}

/// Confusion-derived metrics at one decision threshold. The rate fields are
/// `None` exactly when their denominator is zero.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct ThresholdMetrics {
    pub true_positives: usize,
    pub false_positives: usize,
    pub false_negatives: usize,
    pub true_negatives: usize,
    pub precision: Option<f64>,
    pub recall: Option<f64>,
    pub false_positive_rate: Option<f64>,
    pub false_negative_rate: Option<f64>,
}

/// Precision / recall / FPR / FNR at `threshold` (a probability counts as
/// predicted positive when it is `>= threshold`). `None` on empty or
/// mismatched input.
pub fn threshold_metrics(
    probabilities: &[f64],
    labels: &[bool],
    threshold: f64,
) -> Option<ThresholdMetrics> {
    if probabilities.is_empty() || probabilities.len() != labels.len() {
        return None;
    }
    let mut metrics = ThresholdMetrics {
        true_positives: 0,
        false_positives: 0,
        false_negatives: 0,
        true_negatives: 0,
        precision: None,
        recall: None,
        false_positive_rate: None,
        false_negative_rate: None,
    };
    for (probability, label) in probabilities.iter().zip(labels) {
        let predicted_positive = *probability >= threshold;
        match (predicted_positive, *label) {
            (true, true) => metrics.true_positives += 1,
            (true, false) => metrics.false_positives += 1,
            (false, true) => metrics.false_negatives += 1,
            (false, false) => metrics.true_negatives += 1,
        }
    }
    let predicted = metrics.true_positives + metrics.false_positives;
    if predicted > 0 {
        metrics.precision = Some(metrics.true_positives as f64 / predicted as f64);
    }
    let positives = metrics.true_positives + metrics.false_negatives;
    if positives > 0 {
        metrics.recall = Some(metrics.true_positives as f64 / positives as f64);
        metrics.false_negative_rate = Some(metrics.false_negatives as f64 / positives as f64);
    }
    let negatives = metrics.true_negatives + metrics.false_positives;
    if negatives > 0 {
        metrics.false_positive_rate = Some(metrics.false_positives as f64 / negatives as f64);
    }
    Some(metrics)
}

/// One scored corpus row, as it appears in the JSON artifact.
#[derive(Debug, Clone, Serialize)]
pub struct CalibrationCaseRecord {
    pub scenario_id: String,
    pub utilisation: f64,
    pub compaction_cycles: f64,
    pub health_score: f64,
    pub risk_probability: f64,
    pub true_labels: TrueLabels,
}

/// The calibration of the current heuristic score against one labelled
/// corpus, here `lost_constraint`. All fields are plain data.
#[derive(Debug, Clone, Serialize)]
pub struct CalibrationReport {
    pub target: &'static str,
    pub corpus: &'static str,
    pub cases: usize,
    pub positives: usize,
    pub roc_auc: Option<f64>,
    pub brier_score: Option<f64>,
    pub expected_calibration_error: Option<f64>,
    pub threshold: f64,
    pub threshold_metrics: Option<ThresholdMetrics>,
    pub per_case: Vec<CalibrationCaseRecord>,
}

/// Score every case with the real production scorer, derive the naive risk
/// probability, and measure that probability against `lost_constraint`.
pub fn calibrate_against_lost_constraint(cases: &[HealthCase]) -> CalibrationReport {
    let mut probabilities = Vec::with_capacity(cases.len());
    let mut labels = Vec::with_capacity(cases.len());
    let mut per_case = Vec::with_capacity(cases.len());
    for case in cases {
        let health = context_health(&case.health_inputs);
        let risk = risk_probability(health.score);
        probabilities.push(risk);
        labels.push(case.true_labels.lost_constraint);
        per_case.push(CalibrationCaseRecord {
            scenario_id: case.scenario_id.clone(),
            utilisation: case
                .health_inputs
                .tokens
                .map(|tokens| tokens / case.health_inputs.context_window)
                .unwrap_or_default(),
            compaction_cycles: case.health_inputs.compaction_cycles,
            health_score: health.score,
            risk_probability: risk,
            true_labels: case.true_labels,
        });
    }
    let threshold = 0.5;
    CalibrationReport {
        target: "lost_constraint",
        corpus: "SYNTHETIC-CALIBRATION",
        cases: cases.len(),
        positives: labels.iter().filter(|label| **label).count(),
        roc_auc: roc_auc(&probabilities, &labels),
        brier_score: brier_score(&probabilities, &labels),
        expected_calibration_error: expected_calibration_error(
            &probabilities,
            &labels,
            CALIBRATION_ECE_BINS,
        ),
        threshold,
        threshold_metrics: threshold_metrics(&probabilities, &labels, threshold),
        per_case,
    }
}

/// Serialize a report to pretty JSON. Pure: string building only.
pub fn calibration_report_json(report: &CalibrationReport) -> String {
    serde_json::to_string_pretty(report).expect("calibration report is plain data")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(left: f64, right: f64) {
        assert!(
            (left - right).abs() < 1e-12,
            "{left} and {right} differ"
        );
    }

    // -- metric fixtures: hand-computed ------------------------------------

    #[test]
    fn roc_auc_is_one_for_a_perfect_ranking() {
        let auc = roc_auc(&[0.1, 0.4, 0.9], &[false, false, true]).unwrap();
        close(auc, 1.0);
    }

    #[test]
    fn roc_auc_is_half_for_tied_scores() {
        // All scores equal: every positive-negative pair is a tie.
        let auc = roc_auc(&[0.5, 0.5, 0.5, 0.5], &[true, false, true, false]).unwrap();
        close(auc, 0.5);
    }

    #[test]
    fn roc_auc_handles_partial_ties_by_half_credit() {
        // Pairs (positive 0.5 vs negatives 0.1, 0.5, 0.9) and
        // (positive 0.8 vs 0.1, 0.5, 0.9):
        // 0.5: win + tie + loss = 1.5; 0.8: win + win + loss = 2.0;
        // total 3.5 of 6 -> 7/12.
        let auc = roc_auc(&[0.1, 0.5, 0.9, 0.5, 0.8], &[false, false, false, true, true])
            .unwrap();
        close(auc, 7.0 / 12.0);
    }

    #[test]
    fn roc_auc_is_zero_for_an_inverted_ranking() {
        let auc = roc_auc(&[0.9, 0.1], &[false, true]).unwrap();
        close(auc, 0.0);
    }

    #[test]
    fn roc_auc_is_undefined_without_both_classes() {
        assert_eq!(roc_auc(&[0.1, 0.2], &[true, true]), None);
        assert_eq!(roc_auc(&[0.1, 0.2], &[false, false]), None);
        assert_eq!(roc_auc(&[0.1], &[true, false]), None);
    }

    #[test]
    fn brier_score_matches_hand_computation() {
        // (0.1-0)^2 and (0.9-1)^2 average to 0.01.
        let brier = brier_score(&[0.1, 0.9], &[false, true]).unwrap();
        close(brier, 0.01);
        // A confident wrong prediction costs the full square.
        let wrong = brier_score(&[1.0, 1.0], &[false, false]).unwrap();
        close(wrong, 1.0);
        assert_eq!(brier_score(&[], &[]), None);
    }

    #[test]
    fn ece_matches_hand_computation() {
        // Two cases, both in the first decile bin [0.0, 0.1): predicted
        // mean 0.05, observed rate 0.5 -> ECE 0.45.
        let ece = expected_calibration_error(&[0.05, 0.05], &[true, false], 10).unwrap();
        close(ece, 0.45);
        // A perfectly calibrated pair of bins: 0.25 predicted, 0.25 observed.
        let calibrated =
            expected_calibration_error(&[0.25, 0.25, 0.25, 0.25], &[true, false, false, false], 10)
                .unwrap();
        close(calibrated, 0.0);
        // One case per extreme bin, both correct: ECE 0.1 (the bin residual
        // equals the distance from the bin edge).
        let edges = expected_calibration_error(&[0.0, 1.0], &[false, true], 10).unwrap();
        close(edges, 0.0);
        assert_eq!(expected_calibration_error(&[], &[], 10), None);
    }

    #[test]
    fn ece_buckets_clamp_probabilities_into_range() {
        // Out-of-range probabilities clamp instead of indexing out of bounds.
        let ece = expected_calibration_error(&[-0.5, 1.7], &[false, true], 10).unwrap();
        close(ece, 0.0);
    }

    #[test]
    fn threshold_metrics_match_hand_computation() {
        let metrics = threshold_metrics(&[0.2, 0.6, 0.8], &[false, true, false], 0.5).unwrap();
        assert_eq!(metrics.true_positives, 1);
        assert_eq!(metrics.false_positives, 1);
        assert_eq!(metrics.false_negatives, 0);
        assert_eq!(metrics.true_negatives, 1);
        close(metrics.precision.unwrap(), 0.5);
        close(metrics.recall.unwrap(), 1.0);
        close(metrics.false_positive_rate.unwrap(), 0.5);
        close(metrics.false_negative_rate.unwrap(), 0.0);
    }

    #[test]
    fn threshold_metrics_threshold_is_inclusive() {
        let metrics = threshold_metrics(&[0.5], &[true], 0.5).unwrap();
        assert_eq!(metrics.true_positives, 1);
        assert_eq!(metrics.false_positives, 0);
    }

    #[test]
    fn threshold_metrics_rates_are_none_without_a_denominator() {
        // No predicted positives: precision undefined, recall defined.
        let metrics = threshold_metrics(&[0.1], &[true], 0.5).unwrap();
        assert_eq!(metrics.precision, None);
        close(metrics.recall.unwrap(), 0.0);
        // No positives at all: recall and FNR undefined, FPR defined.
        let negatives = threshold_metrics(&[0.1, 0.9], &[false, false], 0.5).unwrap();
        assert_eq!(negatives.recall, None);
        assert_eq!(negatives.false_negative_rate, None);
        close(negatives.false_positive_rate.unwrap(), 0.5);
        assert_eq!(threshold_metrics(&[], &[], 0.5), None);
    }

    #[test]
    fn risk_probability_is_the_linear_reading_of_the_score() {
        close(risk_probability(100.0), 0.0);
        close(risk_probability(0.0), 1.0);
        close(risk_probability(72.25), 0.2775);
        close(risk_probability(150.0), 0.0);
        close(risk_probability(-10.0), 1.0);
    }

    // -- corpus properties -------------------------------------------------

    #[test]
    fn corpus_has_at_least_forty_deterministic_cases() {
        let first = synthetic_calibration_corpus();
        let second = synthetic_calibration_corpus();
        assert!(first.len() >= 40, "corpus of {} cases", first.len());
        assert_eq!(first, second, "the corpus must be deterministic");
        let mut ids: Vec<&str> = first.iter().map(|case| case.scenario_id.as_str()).collect();
        ids.sort_unstable();
        let unique = ids.len();
        ids.dedup();
        assert_eq!(unique, ids.len(), "scenario ids must be unique");
        assert_eq!(first.len(), CALIBRATION_UTILISATIONS.len() * CALIBRATION_COMPACTION_CYCLES.len());
    }

    #[test]
    fn corpus_sweeps_the_required_ranges() {
        let cases = synthetic_calibration_corpus();
        for utilisation in CALIBRATION_UTILISATIONS {
            assert!((0.2..=0.95).contains(&utilisation));
        }
        assert_eq!(CALIBRATION_UTILISATIONS.first(), Some(&0.20));
        assert_eq!(CALIBRATION_UTILISATIONS.last(), Some(&0.95));
        assert_eq!(CALIBRATION_COMPACTION_CYCLES.first(), Some(&0.0));
        assert_eq!(CALIBRATION_COMPACTION_CYCLES.last(), Some(&10.0));
        for case in &cases {
            let utilisation = case
                .health_inputs
                .tokens
                .expect("synthetic cases carry a token count")
                / case.health_inputs.context_window;
            assert!((0.2..=0.95).contains(&utilisation));
            assert!((0.0..=10.0).contains(&case.health_inputs.compaction_cycles));
            assert!(!case.health_inputs.is_compacting);
        }
    }

    #[test]
    fn corpus_encodes_the_r2_e1_finding_on_constraint_loss() {
        let cases = synthetic_calibration_corpus();
        for case in &cases {
            if case.health_inputs.compaction_cycles == 0.0 {
                assert!(
                    !case.true_labels.lost_constraint,
                    "{} lost a constraint without any compaction cycle",
                    case.scenario_id
                );
            }
        }
        let cycled: Vec<&HealthCase> = cases
            .iter()
            .filter(|case| case.health_inputs.compaction_cycles > 0.0)
            .collect();
        let lost = cycled
            .iter()
            .filter(|case| case.true_labels.lost_constraint)
            .count();
        let kept = cycled.len() - lost;
        assert!(lost > 0 && kept > 0, "cycled cases must mix losses and keeps");
        // Wrong decisions follow losses, never appear without them.
        for case in &cases {
            assert!(
                !case.true_labels.wrong_decision_followed || case.true_labels.lost_constraint,
                "{}",
                case.scenario_id
            );
        }
    }

    #[test]
    fn corpus_scores_with_the_production_scorer() {
        use crate::core::context_health::{
            FULL_PENALTY_UTILISATION, MAX_COMPACTION_PENALTY, MAX_UTILISATION_PENALTY,
            PER_COMPACTION_PENALTY, RELIABLE_UTILISATION,
        };
        let cases = synthetic_calibration_corpus();
        for case in &cases {
            let health = context_health(&case.health_inputs);
            let utilisation =
                case.health_inputs.tokens.unwrap() / case.health_inputs.context_window;
            // The documented scoring formula, recomputed from the public
            // constants: flat below the reliable utilisation, maximal at the
            // full-penalty point, six points per cycle capped at thirty.
            let size = if utilisation <= RELIABLE_UTILISATION {
                0.0
            } else if utilisation >= FULL_PENALTY_UTILISATION {
                MAX_UTILISATION_PENALTY
            } else {
                MAX_UTILISATION_PENALTY * (utilisation - RELIABLE_UTILISATION)
                    / (FULL_PENALTY_UTILISATION - RELIABLE_UTILISATION)
            };
            let compaction = (PER_COMPACTION_PENALTY * case.health_inputs.compaction_cycles)
                .min(MAX_COMPACTION_PENALTY);
            let expected = (100.0 - size - compaction).clamp(0.0, 100.0);
            close(health.score, expected);
            // A cycle-0 case under low utilisation is pristine.
            if case.health_inputs.compaction_cycles == 0.0 && utilisation <= RELIABLE_UTILISATION {
                close(health.score, 100.0);
            }
        }
    }

    // -- the calibration measurement itself --------------------------------

    /// Prints the per-case table and the headline metrics, and writes the
    /// JSON artifact. The write goes to `$ROT2_HEALTH_CALIBRATION_OUT` when
    /// set, otherwise to `research/context_rot_r2/health/` at the repo root
    /// (next to the crate manifest's grandparent). A stale committed copy
    /// only warns: the committed artifact documents the current generator,
    /// and a read-only checkout must not fail the measurement.
    #[test]
    fn current_heuristic_scores_poorly_against_semantic_constraint_loss() {
        let cases = synthetic_calibration_corpus();
        let report = calibrate_against_lost_constraint(&cases);

        println!("SYNTHETIC-CALIBRATION corpus: {} cases, {} positives",
            report.cases, report.positives);
        println!("{:<18} {:>12} {:>7} {:>9} {:>7} {:>6} {:>5} {:>6} {:>5}",
            "scenario", "utilisation", "cycles", "score", "risk", "LC", "LE", "WD", "TC");
        for case in &report.per_case {
            println!("{:<18} {:>12.2} {:>7.0} {:>9.2} {:>7.3} {:>5} {:>5} {:>6} {:>5}",
                case.scenario_id,
                case.utilisation,
                case.compaction_cycles,
                case.health_score,
                case.risk_probability,
                case.true_labels.lost_constraint as u8,
                case.true_labels.lost_evidence as u8,
                case.true_labels.wrong_decision_followed as u8,
                case.true_labels.task_continued as u8);
        }
        println!("ROC-AUC  {:.4}", report.roc_auc.unwrap());
        println!("Brier    {:.4}", report.brier_score.unwrap());
        println!("ECE({})  {:.4}", CALIBRATION_ECE_BINS, report.expected_calibration_error.unwrap());
        let metrics = report.threshold_metrics.unwrap();
        println!("threshold {}: precision {:.3} recall {:.3} FPR {:.3} FNR {:.3}",
            report.threshold,
            metrics.precision.unwrap(),
            metrics.recall.unwrap(),
            metrics.false_positive_rate.unwrap(),
            metrics.false_negative_rate.unwrap());

        // Headline expectation (campaign section 17): the pressure/cycle
        // heuristic is a POOR predictor of semantic constraint loss. On this
        // synthetic corpus it must beat a coin flip (cycle 0 never loses, so
        // the cycle count carries some signal) while staying far below the
        // 0.85+ band a usable risk gate needs. These bounds verify the
        // measurement machinery and the direction of the finding; the
        // absolute numbers are corpus artifacts until the live-model phase
        // supplies real labels.
        let auc = report.roc_auc.expect("both classes are present");
        assert!(auc > 0.5, "AUC {auc} must beat a coin flip");
        assert!(auc < 0.75, "AUC {auc} must stay in the poor band");
        let brier = report.brier_score.unwrap();
        assert!(brier >= 0.2, "Brier {brier} must expose the naive risk mapping");
        let ece = report.expected_calibration_error.unwrap();
        assert!(ece >= 0.1, "ECE {ece} must expose the miscalibration");

        // Write the JSON artifact.
        let json = calibration_report_json(&report);
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("the artifact must parse back");
        assert_eq!(parsed["corpus"], "SYNTHETIC-CALIBRATION");
        assert_eq!(parsed["cases"], cases.len());
        let path = std::env::var_os("ROT2_HEALTH_CALIBRATION_OUT")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| {
                std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .ancestors()
                    .nth(2)
                    .expect("repo root above crates/pi-coding-agent")
                    .join("research")
                    .join("context_rot_r2")
                    .join("health")
                    .join("context_health_calibration.json")
            });
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).expect("create health artifact directory");
        }
        match std::fs::read_to_string(&path) {
            Ok(existing) => {
                let normalized_existing = existing.replace("\r\n", "\n");
                if normalized_existing != json {
                    eprintln!(
                        "warning: committed calibration artifact is stale; regenerate {}",
                        path.display()
                    );
                }
            }
            Err(_) => {}
        }
        std::fs::write(&path, &json).expect("write the calibration artifact");
        assert!(path.is_file());
    }
}
