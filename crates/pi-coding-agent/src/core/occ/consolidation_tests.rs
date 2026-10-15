//! Tests for `crate::core::occ::consolidation`.

use crate::core::occ::consolidation::{
    consolidate, normalized_statement, ConsolidationOptions, EvidenceKeyMode,
};
use crate::core::occ::contract::{
    evidence_kinds, kinds, statuses, verification, AgentCommMessage, EvidenceRef, Finding,
    Verification,
};

fn finding(id: &str, statement: &str, confidence: Option<f32>) -> Finding {
    Finding {
        id: id.to_string(),
        statement: statement.to_string(),
        confidence,
        evidence: Vec::new(),
    }
}

fn finding_with_evidence(id: &str, statement: &str, evidence: &[&str]) -> Finding {
    Finding {
        id: id.to_string(),
        statement: statement.to_string(),
        confidence: None,
        evidence: evidence.iter().map(|id| id.to_string()).collect(),
    }
}

fn src_evidence(id: &str, locator: &str, digest: Option<&str>) -> EvidenceRef {
    evidence_ref(id, locator, digest, None, None)
}

fn evidence_ref(
    id: &str,
    locator: &str,
    digest: Option<&str>,
    revision: Option<&str>,
    line_range: Option<(u32, u32)>,
) -> EvidenceRef {
    EvidenceRef {
        id: id.to_string(),
        kind: evidence_kinds::SOURCE.to_string(),
        locator: locator.to_string(),
        line_range,
        digest: digest.map(str::to_string),
        revision: revision.map(str::to_string),
        verified: None,
    }
}

fn message(kind: &str, task: &str, status: &str) -> AgentCommMessage {
    AgentCommMessage::new(kind, task, status)
}

fn verification_record(claim: &str, status: &str) -> Verification {
    Verification {
        claim: claim.to_string(),
        status: status.to_string(),
        method: "cargo_test".to_string(),
        receipt: None,
    }
}

#[test]
fn normalized_statement_trims_collapses_and_lowercases() {
    assert_eq!(normalized_statement("  The   BUILD\npasses "), "the build passes");
    assert_eq!(normalized_statement("Already clean"), "already clean");
    assert_eq!(normalized_statement(""), "");
}

#[test]
fn merges_identical_statements_across_three_messages() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.message_id = Some("m1".to_string());
    m1.findings.push(finding("F1", "The build passes", Some(0.9)));
    m1.evidence.push(src_evidence("E1", "src/a.rs", Some("d1")));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.sequence = Some(7);
    m2.findings.push(finding("F1", "the build   passes", Some(0.8)));
    m2.evidence.push(src_evidence("E1", "src/a.rs", Some("d1")));
    let mut m3 = message(kinds::RESULT, "t3", statuses::DONE);
    m3.findings.push(finding("F9", "THE BUILD PASSES", None));
    m3.evidence.push(src_evidence("E1", "src/a.rs", Some("d1")));

    let report = consolidate(&[m1, m2, m3], &ConsolidationOptions::default());

    assert_eq!(report.findings.len(), 1);
    let merged = &report.findings[0];
    // First-appearance finding content is kept verbatim.
    assert_eq!(merged.finding.statement, "The build passes");
    assert_eq!(merged.finding.id, "F1");
    assert_eq!(merged.finding.confidence, Some(0.9));
    assert_eq!(merged.sources, vec!["m1", "t2#7", "t3#3"]);
    assert_eq!(merged.agreement, 3);
    assert!(report.conflicts.is_empty());
    assert_eq!(report.stats.messages_in, 3);
    assert_eq!(report.stats.findings_in, 3);
    assert_eq!(report.stats.findings_out, 1);
    assert_eq!(report.stats.evidence_in, 3);
    assert_eq!(report.stats.evidence_out, 1);
    assert_eq!(report.stats.conflicts, 0);
    // Unestimated confidence without an unknown verification is not uncertainty.
    assert_eq!(report.stats.uncertainties, 0);
}

#[test]
fn merged_finding_keeps_first_content_and_per_occurrence_uncertainty() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.message_id = Some("m1".to_string());
    m1.findings.push(finding("F1", "Same claim", Some(0.9)));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.message_id = Some("m2".to_string());
    m2.findings.push(finding("F2", "same   CLAIM", Some(0.1)));

    let report = consolidate(&[m1, m2], &ConsolidationOptions::default());

    assert_eq!(report.findings.len(), 1);
    assert_eq!(report.findings[0].finding.confidence, Some(0.9));
    assert_eq!(report.findings[0].agreement, 2);
    // The low-confidence occurrence is still reported, not hidden by the merge.
    assert_eq!(
        report.uncertainties,
        vec!["task=t2 finding=F2 confidence=0.1 statement=same   CLAIM".to_string()]
    );
}

#[test]
fn conflict_on_different_verification_status_preserves_both() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.message_id = Some("m1".to_string());
    m1.findings.push(finding("F1", "The build passes", Some(0.9)));
    m1.verification = Some(verification_record("The build passes", verification::PASS));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.message_id = Some("m2".to_string());
    m2.findings.push(finding("F1", "the build passes", Some(0.8)));
    m2.verification = Some(verification_record("the build passes", verification::FAIL));

    let report = consolidate(&[m1, m2], &ConsolidationOptions::default());

    assert!(report.findings.is_empty());
    assert_eq!(report.conflicts.len(), 1);
    let conflict = &report.conflicts[0];
    assert_eq!(conflict.topic, "the build passes");
    assert_eq!(conflict.positions.len(), 2);
    assert_eq!(conflict.positions[0].statement, "The build passes");
    assert_eq!(conflict.positions[0].source_message_id, "m1");
    assert_eq!(conflict.positions[1].statement, "the build passes");
    assert_eq!(conflict.positions[1].source_message_id, "m2");
    assert_eq!(report.stats.conflicts, 1);
    assert_eq!(report.stats.findings_out, 0);
}

#[test]
fn conflict_on_same_locator_different_digest_preserves_evidence_ids() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.message_id = Some("m1".to_string());
    m1.findings.push(finding_with_evidence("F1", "Lock is racy", &["E1"]));
    m1.evidence.push(src_evidence("E1", "src/lock.rs", Some("d1")));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.message_id = Some("m2".to_string());
    m2.findings.push(finding_with_evidence("F1", "lock is RACY", &["E1"]));
    m2.evidence.push(src_evidence("E1", "src/lock.rs", Some("d2")));

    let report = consolidate(&[m1, m2], &ConsolidationOptions::default());

    assert!(report.findings.is_empty());
    assert_eq!(report.conflicts.len(), 1);
    let conflict = &report.conflicts[0];
    assert_eq!(conflict.topic, "lock is racy");
    assert_eq!(conflict.positions[0].evidence_ids, vec!["E1"]);
    assert_eq!(conflict.positions[1].evidence_ids, vec!["E1"]);
    // Both digests survive in the consolidated evidence list.
    assert_eq!(report.evidence.len(), 2);
    assert_eq!(report.stats.evidence_out, 2);
}

#[test]
fn verification_claim_matching_limits_status_attribution() {
    // The verification claim does not match the finding statement, so the
    // verification status is not attributed to the finding and both merge.
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.findings.push(finding("F1", "Same claim", Some(0.9)));
    m1.verification = Some(verification_record("A different claim", verification::PASS));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.findings.push(finding("F1", "same claim", None));

    let report = consolidate(&[m1, m2], &ConsolidationOptions::default());

    assert!(report.conflicts.is_empty());
    assert_eq!(report.findings.len(), 1);
    assert_eq!(report.findings[0].agreement, 2);
}

#[test]
fn uncertainty_extraction_covers_low_confidence_and_unknown_verification() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.findings.push(finding("F1", "Low confidence claim", Some(0.3)));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.findings.push(finding("F1", "High confidence claim", Some(0.9)));
    let mut m3 = message(kinds::RESULT, "t3", statuses::DONE);
    m3.findings.push(finding("F1", "Unestimated claim", None));
    m3.verification = Some(verification_record("Unestimated claim", verification::UNKNOWN));
    let mut m4 = message(kinds::RESULT, "t4", statuses::DONE);
    m4.findings.push(finding("F1", "Verified unknown claim", Some(0.9)));
    m4.verification = Some(verification_record("Verified unknown claim", verification::UNKNOWN));
    let mut m5 = message(kinds::RESULT, "t5", statuses::DONE);
    m5.findings.push(finding("F1", "Unestimated clean claim", None));

    let report = consolidate(&[m1, m2, m3, m4, m5], &ConsolidationOptions::default());

    assert_eq!(
        report.uncertainties,
        vec![
            "task=t1 finding=F1 confidence=0.3 statement=Low confidence claim".to_string(),
            "task=t3 finding=F1 confidence=unknown statement=Unestimated claim".to_string(),
            "task=t4 finding=F1 confidence=0.9 statement=Verified unknown claim".to_string(),
        ]
    );
    assert_eq!(report.stats.uncertainties, 3);
    assert_eq!(report.stats.findings_out, 5);
}

#[test]
fn evidence_dedup_locator_mode_ignores_digest_and_revision() {
    let mut m1 = message(kinds::EVIDENCE, "t1", statuses::IN_PROGRESS);
    m1.evidence
        .push(evidence_ref("E1", "src/a.rs", Some("d1"), Some("r1"), None));
    let mut m2 = message(kinds::EVIDENCE, "t2", statuses::IN_PROGRESS);
    m2.evidence
        .push(evidence_ref("E1", "src/a.rs", Some("d2"), Some("r2"), None));

    let options = ConsolidationOptions {
        evidence_key: EvidenceKeyMode::Locator,
        ..Default::default()
    };
    let report = consolidate(&[m1, m2], &options);

    assert_eq!(report.evidence.len(), 1);
    assert_eq!(report.evidence[0].digest.as_deref(), Some("d1"));
    assert_eq!(report.stats.evidence_in, 2);
    assert_eq!(report.stats.evidence_out, 1);
}

#[test]
fn evidence_dedup_locator_and_digest_mode() {
    let mut m1 = message(kinds::EVIDENCE, "t1", statuses::IN_PROGRESS);
    m1.evidence.push(src_evidence("E1", "src/a.rs", Some("d1")));
    let mut m2 = message(kinds::EVIDENCE, "t2", statuses::IN_PROGRESS);
    m2.evidence.push(src_evidence("E1", "src/a.rs", Some("d2")));
    let mut m3 = message(kinds::EVIDENCE, "t3", statuses::IN_PROGRESS);
    m3.evidence.push(src_evidence("E1", "src/a.rs", Some("d1")));

    let report = consolidate(
        &[m1, m2, m3],
        &ConsolidationOptions {
            evidence_key: EvidenceKeyMode::LocatorAndDigest,
            ..Default::default()
        },
    );

    assert_eq!(report.evidence.len(), 2);
    assert_eq!(report.evidence[0].digest.as_deref(), Some("d1"));
    assert_eq!(report.evidence[1].digest.as_deref(), Some("d2"));
    assert_eq!(report.stats.evidence_out, 2);
}

#[test]
fn evidence_dedup_locator_digest_revision_mode() {
    let mut m1 = message(kinds::EVIDENCE, "t1", statuses::IN_PROGRESS);
    m1.evidence
        .push(evidence_ref("E1", "src/a.rs", Some("d1"), Some("r1"), None));
    let mut m2 = message(kinds::EVIDENCE, "t2", statuses::IN_PROGRESS);
    m2.evidence
        .push(evidence_ref("E1", "src/a.rs", Some("d1"), Some("r2"), None));
    let mut m3 = message(kinds::EVIDENCE, "t3", statuses::IN_PROGRESS);
    m3.evidence
        .push(evidence_ref("E1", "src/a.rs", Some("d1"), Some("r2"), None));

    let report = consolidate(
        &[m1, m2, m3],
        &ConsolidationOptions {
            evidence_key: EvidenceKeyMode::LocatorDigestRevision,
            ..Default::default()
        },
    );

    assert_eq!(report.evidence.len(), 2);
    assert_eq!(report.evidence[0].revision.as_deref(), Some("r1"));
    assert_eq!(report.evidence[1].revision.as_deref(), Some("r2"));
    assert_eq!(report.stats.evidence_out, 2);
}

#[test]
fn evidence_with_equal_keys_but_different_line_ranges_keeps_both() {
    // line_range is not part of any key mode, but differing line ranges
    // never merge, so no pointer is silently dropped.
    let mut m1 = message(kinds::EVIDENCE, "t1", statuses::IN_PROGRESS);
    m1.evidence
        .push(evidence_ref("E1", "src/a.rs", Some("d1"), None, None));
    let mut m2 = message(kinds::EVIDENCE, "t2", statuses::IN_PROGRESS);
    m2.evidence
        .push(evidence_ref("E1", "src/a.rs", Some("d1"), None, Some((10, 20))));

    let report = consolidate(&[m1, m2], &ConsolidationOptions::default());

    assert_eq!(report.evidence.len(), 2);
    assert_eq!(report.evidence[0].line_range, None);
    assert_eq!(report.evidence[1].line_range, Some((10, 20)));
    assert_eq!(report.stats.evidence_out, 2);
}

#[test]
fn dependency_union_preserves_first_appearance_order() {
    let mut m1 = message(kinds::TASK, "t1", statuses::PENDING);
    m1.dependencies = vec!["t9".to_string(), "t8".to_string()];
    let mut m2 = message(kinds::TASK, "t2", statuses::PENDING);
    m2.dependencies = vec!["t8".to_string(), "t7".to_string()];
    let m3 = message(kinds::TASK, "t3", statuses::PENDING);

    let report = consolidate(&[m1, m2, m3], &ConsolidationOptions::default());

    assert_eq!(report.dependencies, vec!["t9", "t8", "t7"]);
}

#[test]
fn stats_are_exact() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.findings.push(finding("F1", "Alpha", Some(0.9)));
    m1.evidence.push(src_evidence("E1", "src/a.rs", Some("d1")));
    m1.dependencies.push("t9".to_string());
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.findings.push(finding("F1", "alpha", Some(0.9)));
    m2.evidence.push(src_evidence("E1", "src/a.rs", Some("d1")));
    m2.dependencies = vec!["t9".to_string(), "t8".to_string()];
    let mut m3 = message(kinds::RESULT, "t3", statuses::DONE);
    m3.findings.push(Finding {
        id: "F2".to_string(),
        statement: "beta".to_string(),
        confidence: Some(0.3),
        evidence: vec!["E2".to_string()],
    });
    m3.evidence.push(src_evidence("E2", "src/b.rs", Some("d2")));
    let mut m4 = message(kinds::RESULT, "t4", statuses::DONE);
    m4.findings.push(finding_with_evidence("F3", "BETA", &["E3"]));
    m4.evidence.push(src_evidence("E3", "src/b.rs", Some("d3")));

    let report = consolidate(&[m1, m2, m3, m4], &ConsolidationOptions::default());

    assert_eq!(report.stats.messages_in, 4);
    assert_eq!(report.stats.findings_in, 4);
    // alpha merges; the beta group conflicts and emits no finding.
    assert_eq!(report.stats.findings_out, 1);
    assert_eq!(report.stats.evidence_in, 4);
    assert_eq!(report.stats.evidence_out, 3);
    assert_eq!(report.stats.conflicts, 1);
    assert_eq!(report.stats.uncertainties, 1);
    assert_eq!(report.dependencies, vec!["t9", "t8"]);
}

#[test]
fn conflicts_are_ordered_by_first_detection() {
    // Group P appears first but is only detected at message 4; group Q is
    // detected earlier at message 3, so Q comes first in the output.
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.findings.push(finding("F1", "Papa", Some(0.9)));
    m1.verification = Some(verification_record("Papa", verification::PASS));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.findings.push(finding_with_evidence("F1", "Quebec", &["E1"]));
    m2.evidence.push(src_evidence("E1", "src/q.rs", Some("d1")));
    let mut m3 = message(kinds::RESULT, "t3", statuses::DONE);
    m3.findings.push(finding_with_evidence("F1", "quebec", &["E1"]));
    m3.evidence.push(src_evidence("E1", "src/q.rs", Some("d2")));
    let mut m4 = message(kinds::RESULT, "t4", statuses::DONE);
    m4.findings.push(finding("F1", "papa", Some(0.9)));
    m4.verification = Some(verification_record("papa", verification::FAIL));

    let report = consolidate(&[m1, m2, m3, m4], &ConsolidationOptions::default());

    assert_eq!(report.conflicts.len(), 2);
    assert_eq!(report.conflicts[0].topic, "quebec");
    assert_eq!(report.conflicts[0].positions.len(), 2);
    assert_eq!(report.conflicts[1].topic, "papa");
    assert_eq!(report.conflicts[1].positions.len(), 2);
    assert!(report.findings.is_empty());
}

#[test]
fn merge_disabled_passes_findings_through() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.findings.push(finding("F1", "Same claim", Some(0.9)));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.findings.push(finding("F1", "same claim", Some(0.1)));

    let report = consolidate(
        &[m1.clone(), m2.clone()],
        &ConsolidationOptions {
            merge_same_statement: false,
            ..Default::default()
        },
    );

    assert_eq!(report.findings.len(), 2);
    assert!(report.conflicts.is_empty());
    for finding in &report.findings {
        assert_eq!(finding.agreement, 1);
        assert_eq!(finding.sources.len(), 1);
    }
    assert_eq!(report.stats.findings_in, 2);
    assert_eq!(report.stats.findings_out, 2);
}

#[test]
fn determinism_same_input_same_output() {
    let mut m1 = message(kinds::RESULT, "t1", statuses::DONE);
    m1.findings.push(finding("F1", "Alpha", Some(0.9)));
    let mut m2 = message(kinds::RESULT, "t2", statuses::DONE);
    m2.findings.push(finding("F1", "beta", Some(0.2)));
    let mut m3 = message(kinds::RESULT, "t3", statuses::DONE);
    m3.findings.push(finding("F1", "BETA", None));
    m3.verification = Some(verification_record("BETA", verification::UNKNOWN));

    let first =
        consolidate(&[m1.clone(), m2.clone(), m3.clone()], &ConsolidationOptions::default());
    let second = consolidate(&[m1, m2, m3], &ConsolidationOptions::default());

    assert_eq!(first, second);
}

#[test]
fn empty_input_produces_empty_report() {
    let report = consolidate(&[], &ConsolidationOptions::default());

    assert!(report.findings.is_empty());
    assert!(report.evidence.is_empty());
    assert!(report.conflicts.is_empty());
    assert!(report.uncertainties.is_empty());
    assert!(report.dependencies.is_empty());
    assert_eq!(
        report.stats,
        crate::core::occ::consolidation::ConsolidationStats {
            messages_in: 0,
            findings_in: 0,
            findings_out: 0,
            evidence_in: 0,
            evidence_out: 0,
            conflicts: 0,
            uncertainties: 0,
        }
    );
}

#[test]
fn default_options_match_the_frozen_spec() {
    let options = ConsolidationOptions::default();
    assert!(options.merge_same_statement);
    assert_eq!(options.evidence_key, EvidenceKeyMode::LocatorAndDigest);
}
