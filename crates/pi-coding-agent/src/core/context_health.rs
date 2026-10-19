//! Health of the working context, scored for the tray indicator.
//!
//! The tray already shows the raw counter (`146k (14%)`). Size alone is not
//! risk: a context is unreliable once it is nearly full, and every compaction
//! cycle spends fidelity. The score is deterministic and dependency-free so the
//! tray can render it on every frame without I/O or RPC.

/// Below this utilisation, size alone costs nothing.
pub const RELIABLE_UTILISATION: f64 = 0.35;
/// At or above this utilisation the size penalty is maximal.
pub const FULL_PENALTY_UTILISATION: f64 = 0.95;
/// Largest score loss attributable to utilisation.
pub const MAX_UTILISATION_PENALTY: f64 = 45.0;
/// Score loss per completed compaction cycle.
pub const PER_COMPACTION_PENALTY: f64 = 6.0;
/// Largest score loss attributable to compaction cycles.
pub const MAX_COMPACTION_PENALTY: f64 = 30.0;

/// Health bands, best first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ContextHealthBand {
    Healthy,
    Straining,
    Degraded,
    Critical,
}

impl ContextHealthBand {
    fn for_score(score: f64) -> Self {
        if score >= 80.0 {
            Self::Healthy
        } else if score >= 60.0 {
            Self::Straining
        } else if score >= 40.0 {
            Self::Degraded
        } else {
            Self::Critical
        }
    }
}

/// Inputs of one health evaluation. Unknown fields stay unknown: a missing
/// token count is never read as a zero-sized context.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ContextHealthInput {
    pub tokens: Option<f64>,
    pub context_window: f64,
    pub compaction_cycles: f64,
    pub is_compacting: bool,
}

/// One health evaluation. `score` keeps full precision; only labels round it.
#[derive(Debug, Clone, PartialEq)]
pub struct ContextHealth {
    pub score: f64,
    pub band: ContextHealthBand,
    pub utilisation: Option<f64>,
    pub reasons: Vec<String>,
}

/// Score the working context in `[0, 100]`.
pub fn context_health(input: &ContextHealthInput) -> ContextHealth {
    let utilisation = utilisation(input);
    let size = utilisation.map(size_penalty).unwrap_or(0.0);
    // `f64::max` maps NaN cycles to zero; infinite cycles saturate the cap.
    let cycles = input.compaction_cycles.max(0.0);
    let compaction = (PER_COMPACTION_PENALTY * cycles).min(MAX_COMPACTION_PENALTY);
    let score = (100.0 - size - compaction).clamp(0.0, 100.0);
    let mut reasons = Vec::new();
    match utilisation {
        Some(utilisation) => reasons.push(format!(
            "utilisation {}% of the window",
            percent_text(utilisation)
        )),
        None => reasons.push("usage unknown".to_string()),
    }
    if cycles > 0.0 {
        reasons.push(format!(
            "{} compaction cycle{}",
            count_text(cycles),
            if cycles == 1.0 { "" } else { "s" }
        ));
    }
    if input.is_compacting {
        reasons.push("compacting now".to_string());
    }
    // Unknown usage scores from the compaction penalty alone, so the band can
    // only reach Healthy when that score still clears the healthy threshold.
    ContextHealth {
        score,
        band: ContextHealthBand::for_score(score),
        utilisation,
        reasons,
    }
}

/// Full tray label, e.g. `CONTEXT HEALTH 92`.
pub fn label(health: &ContextHealth) -> String {
    format!("CONTEXT HEALTH {}", score_text(health))
}

/// Compact tray label, e.g. `CTX HEALTH 92`.
pub fn compact_label(health: &ContextHealth) -> String {
    format!("CTX HEALTH {}", score_text(health))
}

/// Minimal tray label, e.g. `CH 92`.
pub fn minimal_label(health: &ContextHealth) -> String {
    format!("CH {}", score_text(health))
}

/// Theme key for the band: healthy stays quiet, critical is an error.
pub fn band_colour(band: ContextHealthBand) -> &'static str {
    match band {
        ContextHealthBand::Healthy => "dim",
        ContextHealthBand::Straining => "warning",
        ContextHealthBand::Degraded => "warning",
        ContextHealthBand::Critical => "error",
    }
}

/// `tokens / context_window` clamped to `[0, 1.5]`, or `None` when either side
/// is unknown or unusable.
fn utilisation(input: &ContextHealthInput) -> Option<f64> {
    let tokens = input.tokens.filter(|tokens| tokens.is_finite())?;
    let window = input.context_window;
    if !window.is_finite() || window <= 0.0 {
        return None;
    }
    Some((tokens / window).clamp(0.0, 1.5))
}

fn size_penalty(utilisation: f64) -> f64 {
    if utilisation <= RELIABLE_UTILISATION {
        0.0
    } else if utilisation >= FULL_PENALTY_UTILISATION {
        MAX_UTILISATION_PENALTY
    } else {
        MAX_UTILISATION_PENALTY * (utilisation - RELIABLE_UTILISATION)
            / (FULL_PENALTY_UTILISATION - RELIABLE_UTILISATION)
    }
}

fn score_text(health: &ContextHealth) -> i64 {
    if health.score.is_finite() {
        health.score.round().clamp(0.0, 100.0) as i64
    } else {
        0
    }
}

fn percent_text(utilisation: f64) -> i64 {
    (utilisation * 100.0).round() as i64
}

fn count_text(count: f64) -> String {
    if count.fract() == 0.0 {
        format!("{}", count as i64)
    } else {
        format!("{count}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn health(tokens: f64, window: f64, cycles: f64) -> ContextHealth {
        context_health(&ContextHealthInput {
            tokens: Some(tokens),
            context_window: window,
            compaction_cycles: cycles,
            is_compacting: false,
        })
    }

    /// Score for `tokens` of a 1000-token window, so utilisation is exact.
    fn score_tokens(tokens: f64) -> f64 {
        health(tokens, 1000.0, 0.0).score
    }

    #[test]
    fn band_boundaries_are_exact_on_both_sides() {
        assert_eq!(
            ContextHealthBand::for_score(100.0),
            ContextHealthBand::Healthy
        );
        assert_eq!(
            ContextHealthBand::for_score(80.0),
            ContextHealthBand::Healthy
        );
        assert_eq!(
            ContextHealthBand::for_score(79.999),
            ContextHealthBand::Straining
        );
        assert_eq!(
            ContextHealthBand::for_score(60.0),
            ContextHealthBand::Straining
        );
        assert_eq!(
            ContextHealthBand::for_score(59.999),
            ContextHealthBand::Degraded
        );
        assert_eq!(
            ContextHealthBand::for_score(40.0),
            ContextHealthBand::Degraded
        );
        assert_eq!(
            ContextHealthBand::for_score(39.999),
            ContextHealthBand::Critical
        );
        assert_eq!(
            ContextHealthBand::for_score(0.0),
            ContextHealthBand::Critical
        );
    }

    #[test]
    fn size_penalty_is_flat_below_the_reliable_utilisation() {
        assert_eq!(score_tokens(0.0), 100.0);
        assert_eq!(score_tokens(350.0), 100.0);
        assert!(score_tokens(351.0) < 100.0);
        assert_eq!(score_tokens(950.0), 100.0 - MAX_UTILISATION_PENALTY);
        // Clamped utilisation: above the full-penalty point the size cost is flat.
        assert_eq!(score_tokens(1200.0), 100.0 - MAX_UTILISATION_PENALTY);
        assert_eq!(score_tokens(6000.0), score_tokens(1200.0));
    }

    #[test]
    fn score_is_monotonic_in_utilisation() {
        let points = [
            0.0, 100.0, 350.0, 351.0, 500.0, 650.0, 800.0, 940.0, 950.0, 1000.0, 1500.0,
        ];
        let scores: Vec<f64> = points.iter().map(|point| score_tokens(*point)).collect();
        for pair in scores.windows(2) {
            assert!(pair[0] >= pair[1], "{scores:?}");
        }
    }

    #[test]
    fn score_is_monotonic_in_compaction_cycles() {
        let scores: Vec<f64> = (0..12)
            .map(|cycles| health(500.0, 1000.0, cycles as f64).score)
            .collect();
        for pair in scores.windows(2) {
            assert!(pair[0] >= pair[1], "{scores:?}");
        }
        // 6 points per cycle, then flat at the 30-point cap.
        for (cycle, pair) in scores.windows(2).enumerate().take(5) {
            assert!(
                (pair[0] - pair[1] - PER_COMPACTION_PENALTY).abs() < 1e-9,
                "cycle {cycle}"
            );
        }
        assert_eq!(scores[5], scores[11]);
    }

    #[test]
    fn a_compaction_heavy_session_scores_lower_at_equal_utilisation() {
        let fresh = health(720.0, 1000.0, 0.0);
        let compacted = health(720.0, 1000.0, 3.0);
        assert_eq!(fresh.utilisation, Some(0.72));
        assert_eq!(compacted.utilisation, Some(0.72));
        assert!(compacted.score < fresh.score);
        assert!((fresh.score - compacted.score - MAX_COMPACTION_PENALTY / 5.0 * 3.0).abs() < 1e-9);
        assert_eq!(
            compacted.reasons,
            vec!["utilisation 72% of the window", "3 compaction cycles"]
        );
    }

    #[test]
    fn compaction_penalty_is_capped() {
        let capped = health(0.0, 1000.0, 5.0);
        let deeper = health(0.0, 1000.0, 50.0);
        assert_eq!(capped.score, 100.0 - MAX_COMPACTION_PENALTY);
        assert_eq!(deeper.score, capped.score);
        assert_eq!(
            deeper.reasons,
            vec!["utilisation 0% of the window", "50 compaction cycles"]
        );
    }

    #[test]
    fn unknown_usage_is_not_treated_as_zero_risk() {
        let unknown = context_health(&ContextHealthInput {
            tokens: None,
            context_window: 1000.0,
            compaction_cycles: 4.0,
            is_compacting: false,
        });
        assert_eq!(unknown.utilisation, None);
        assert_eq!(unknown.score, 100.0 - 4.0 * PER_COMPACTION_PENALTY);
        assert!(unknown.reasons.contains(&"usage unknown".to_string()));
        assert_eq!(unknown.band, ContextHealthBand::Straining);

        let pristine = context_health(&ContextHealthInput::default());
        assert_eq!(pristine.score, 100.0);
        assert_eq!(pristine.band, ContextHealthBand::Healthy);
        assert_eq!(pristine.reasons, vec!["usage unknown"]);
    }

    #[test]
    fn compacting_now_adds_a_reason_without_changing_the_score() {
        let idle = health(500.0, 1000.0, 1.0);
        let mut input = ContextHealthInput {
            tokens: Some(500.0),
            context_window: 1000.0,
            compaction_cycles: 1.0,
            is_compacting: true,
        };
        let busy = context_health(&input);
        assert_eq!(busy.score, idle.score);
        assert_eq!(busy.band, idle.band);
        assert_eq!(
            busy.reasons.last().map(String::as_str),
            Some("compacting now")
        );
        input.is_compacting = false;
        assert!(!context_health(&input)
            .reasons
            .contains(&"compacting now".to_string()));
    }

    #[test]
    fn nan_zero_window_and_negative_inputs_are_safe() {
        for tokens in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            for window in [0.0, f64::NAN, -10.0] {
                let health = context_health(&ContextHealthInput {
                    tokens: Some(tokens),
                    context_window: window,
                    compaction_cycles: f64::NAN,
                    is_compacting: true,
                });
                assert!(health.score.is_finite(), "{tokens} {window}");
                assert!((0.0..=100.0).contains(&health.score));
                assert_eq!(health.utilisation, None);
                assert_eq!(health.score, 100.0);
                assert_eq!(health.band, ContextHealthBand::Healthy);
            }
        }
        let negative = health(-500.0, 1000.0, -3.0);
        assert_eq!(negative.utilisation, Some(0.0));
        assert_eq!(negative.score, 100.0);
        assert_eq!(negative.reasons, vec!["utilisation 0% of the window"]);
        let infinite_cycles = context_health(&ContextHealthInput {
            tokens: Some(0.0),
            context_window: 1000.0,
            compaction_cycles: f64::INFINITY,
            is_compacting: false,
        });
        assert!(infinite_cycles.score.is_finite());
        assert_eq!(infinite_cycles.score, 100.0 - MAX_COMPACTION_PENALTY);
    }

    #[test]
    fn labels_have_exact_formats() {
        let health = ContextHealth {
            score: 92.4,
            band: ContextHealthBand::Healthy,
            utilisation: Some(0.14),
            reasons: Vec::new(),
        };
        assert_eq!(label(&health), "CONTEXT HEALTH 92");
        assert_eq!(compact_label(&health), "CTX HEALTH 92");
        assert_eq!(minimal_label(&health), "CH 92");
        assert_eq!(
            label(&ContextHealth {
                score: 92.6,
                ..health.clone()
            }),
            "CONTEXT HEALTH 93"
        );
        assert_eq!(
            minimal_label(&ContextHealth {
                score: 0.4,
                ..health.clone()
            }),
            "CH 0"
        );
        assert_eq!(
            label(&ContextHealth {
                score: f64::NAN,
                ..health.clone()
            }),
            "CONTEXT HEALTH 0"
        );
    }

    #[test]
    fn band_colours_use_theme_keys() {
        assert_eq!(band_colour(ContextHealthBand::Healthy), "dim");
        assert_eq!(band_colour(ContextHealthBand::Straining), "warning");
        assert_eq!(band_colour(ContextHealthBand::Degraded), "warning");
        assert_eq!(band_colour(ContextHealthBand::Critical), "error");
    }

    #[test]
    fn reasons_are_short_lowercase_and_factual() {
        let health = health(910.0, 1000.0, 2.0);
        assert_eq!(
            health.reasons,
            vec!["utilisation 91% of the window", "2 compaction cycles"]
        );
        for reason in &health.reasons {
            assert_eq!(*reason, reason.to_lowercase(), "{reason}");
        }
    }
}
