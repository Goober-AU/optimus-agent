//! Tests for `crate::core::occ::prune`.

use crate::core::occ::contract::{
    evidence_kinds, kinds, statuses, verification, AgentCommMessage, EvidenceRef, Finding,
    Verification,
};
use crate::core::occ::prune::{
    evidence_digest, finding_digest, DeliveryRecord, PruneDecision, PrunePolicy, PruneReason,
    Pruner,
};

fn finding(id: &str, statement: &str) -> Finding {
    Finding {
        id: id.to_string(),
        statement: statement.to_string(),
        confidence: Some(0.9),
        evidence: Vec::new(),
    }
}

fn src_evidence(id: &str, locator: &str, digest: &str) -> EvidenceRef {
    EvidenceRef {
        id: id.to_string(),
        kind: evidence_kinds::SOURCE.to_string(),
        locator: locator.to_string(),
        line_range: None,
        digest: Some(digest.to_string()),
        revision: None,
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

/// RESULT with one finding "The build passes" and one evidence ref.
fn base_message() -> AgentCommMessage {
    let mut base = message(kinds::RESULT, "t1", statuses::DONE);
    base.message_id = Some("m1".to_string());
    base.findings.push(finding("F1", "The build passes"));
    base.evidence.push(src_evidence("E1", "src/a.rs", "d1"));
    base.verification = Some(verification_record("The build passes", verification::PASS));
    base
}

#[test]
fn first_message_for_a_task_is_delivered() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let heartbeat = message(kinds::PROGRESS, "t1", statuses::IN_PROGRESS);
    assert_eq!(pruner.classify(&heartbeat), PruneDecision::Deliver);
}

#[test]
fn must_deliver_classes_are_never_suppressed() {
    let blocked = {
        let mut m = base_message();
        m.kind = kinds::BLOCKED.to_string();
        m.status = statuses::BLOCKED.to_string();
        m
    };
    let cancelled = {
        let mut m = base_message();
        m.kind = kinds::CANCELLED.to_string();
        m.status = statuses::CANCELLED.to_string();
        m
    };
    let correction = {
        let mut m = base_message();
        m.kind = kinds::CORRECTION.to_string();
        m
    };
    let ack = {
        let mut m = base_message();
        m.kind = kinds::ACK.to_string();
        m
    };
    let failed_status = {
        let mut m = base_message();
        m.status = statuses::FAILED.to_string();
        m
    };
    let verification_failed = {
        let mut m = base_message();
        m.verification = Some(verification_record("The build passes", verification::FAIL));
        m
    };
    let contradictory = {
        let mut m = base_message();
        m.verification = Some(verification_record("The build passes", verification::UNKNOWN));
        m
    };
    let new_dependency = {
        let mut m = base_message();
        m.dependencies.push("t9".to_string());
        m
    };

    for case in [
        blocked,
        cancelled,
        correction,
        ack,
        failed_status,
        verification_failed,
        contradictory,
        new_dependency,
    ] {
        let mut pruner = Pruner::new(PrunePolicy::default());
        assert_eq!(pruner.classify(&base_message()), PruneDecision::Deliver);
        assert_eq!(pruner.classify(&case), PruneDecision::Deliver);
    }
}

#[test]
fn a_genuine_duplicate_is_suppressed_as_control() {
    // The same must-deliver scenario without any safety class: the base is
    // re-sent verbatim (same verification status) and is suppressed.
    let mut pruner = Pruner::new(PrunePolicy::default());
    assert_eq!(pruner.classify(&base_message()), PruneDecision::Deliver);
    assert_eq!(
        pruner.classify(&base_message()),
        PruneDecision::Suppress {
            reason: PruneReason::DuplicateFindings
        }
    );
}

#[test]
fn unchanged_progress_is_suppressed() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::PROGRESS, "t1", statuses::IN_PROGRESS);
    first.findings.push(finding("F1", "Halfway done"));
    first.evidence.push(src_evidence("E1", "src/a.rs", "d1"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let repeat = {
        let mut m = first.clone();
        m.message_id = Some("m2".to_string());
        m
    };
    assert_eq!(
        pruner.classify(&repeat),
        PruneDecision::Suppress {
            reason: PruneReason::UnchangedProgress
        }
    );
}

#[test]
fn empty_progress_heartbeat_is_suppressed_when_status_is_unchanged() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let heartbeat = message(kinds::PROGRESS, "t1", statuses::IN_PROGRESS);
    assert_eq!(pruner.classify(&heartbeat), PruneDecision::Deliver);
    assert_eq!(
        pruner.classify(&heartbeat),
        PruneDecision::Suppress {
            reason: PruneReason::UnchangedProgress
        }
    );
}

#[test]
fn progress_with_changed_status_is_delivered() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::PROGRESS, "t1", statuses::IN_PROGRESS);
    first.findings.push(finding("F1", "Halfway done"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let mut done = first.clone();
    done.status = statuses::DONE.to_string();
    assert_eq!(pruner.classify(&done), PruneDecision::Deliver);

    // Now the status matches the last delivered record again.
    let mut done_repeat = done.clone();
    done_repeat.message_id = Some("m3".to_string());
    assert_eq!(
        pruner.classify(&done_repeat),
        PruneDecision::Suppress {
            reason: PruneReason::UnchangedProgress
        }
    );
}

#[test]
fn progress_with_new_finding_or_evidence_is_delivered() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::PROGRESS, "t1", statuses::IN_PROGRESS);
    first.findings.push(finding("F1", "Halfway done"));
    first.evidence.push(src_evidence("E1", "src/a.rs", "d1"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let mut with_new_finding = first.clone();
    with_new_finding.findings.push(finding("F2", "New finding"));
    assert_eq!(pruner.classify(&with_new_finding), PruneDecision::Deliver);

    let mut with_new_evidence = with_new_finding.clone();
    with_new_evidence.evidence.push(src_evidence("E2", "src/b.rs", "d2"));
    assert_eq!(pruner.classify(&with_new_evidence), PruneDecision::Deliver);
}

#[test]
fn duplicate_findings_result_is_suppressed() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::RESULT, "t1", statuses::DONE);
    first.findings.push(finding("F1", "The build passes"));
    first.evidence.push(src_evidence("E1", "src/a.rs", "d1"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let repeat = {
        let mut m = first.clone();
        m.message_id = Some("m2".to_string());
        m
    };
    assert_eq!(
        pruner.classify(&repeat),
        PruneDecision::Suppress {
            reason: PruneReason::DuplicateFindings
        }
    );
}

#[test]
fn duplicate_findings_rule_does_not_check_status_per_spec() {
    // The frozen DuplicateFindings rule has no status condition, unlike the
    // PROGRESS rule.
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::RESULT, "t1", statuses::DONE);
    first.findings.push(finding("F1", "The build passes"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let mut partial = first.clone();
    partial.status = statuses::PARTIAL.to_string();
    assert_eq!(
        pruner.classify(&partial),
        PruneDecision::Suppress {
            reason: PruneReason::DuplicateFindings
        }
    );
}

#[test]
fn repeated_evidence_is_suppressed() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::EVIDENCE, "t1", statuses::IN_PROGRESS);
    first.evidence.push(src_evidence("E1", "src/a.rs", "d1"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let repeat = {
        let mut m = first.clone();
        m.message_id = Some("m2".to_string());
        m
    };
    assert_eq!(
        pruner.classify(&repeat),
        PruneDecision::Suppress {
            reason: PruneReason::RepeatedEvidence
        }
    );

    let mut with_new_evidence = first.clone();
    with_new_evidence.evidence.push(src_evidence("E2", "src/b.rs", "d2"));
    assert_eq!(pruner.classify(&with_new_evidence), PruneDecision::Deliver);
}

#[test]
fn task_and_request_detail_kinds_are_always_delivered() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::RESULT, "t1", statuses::DONE);
    first.findings.push(finding("F1", "The build passes"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let mut task = first.clone();
    task.kind = kinds::TASK.to_string();
    task.status = statuses::PENDING.to_string();
    assert_eq!(pruner.classify(&task), PruneDecision::Deliver);

    let mut request = first.clone();
    request.kind = kinds::REQUEST_DETAIL.to_string();
    assert_eq!(pruner.classify(&request), PruneDecision::Deliver);
}

#[test]
fn policy_flags_disable_each_suppression() {
    let progress = {
        let mut m = message(kinds::PROGRESS, "t1", statuses::IN_PROGRESS);
        m.findings.push(finding("F1", "Halfway done"));
        m
    };
    let mut pruner = Pruner::new(PrunePolicy {
        suppress_unchanged_progress: false,
        ..Default::default()
    });
    pruner.classify(&progress);
    assert_eq!(pruner.classify(&progress), PruneDecision::Deliver);

    let result = {
        let mut m = message(kinds::RESULT, "t1", statuses::DONE);
        m.findings.push(finding("F1", "The build passes"));
        m
    };
    let mut pruner = Pruner::new(PrunePolicy {
        suppress_duplicate_findings: false,
        ..Default::default()
    });
    pruner.classify(&result);
    assert_eq!(pruner.classify(&result), PruneDecision::Deliver);

    let evidence = {
        let mut m = message(kinds::EVIDENCE, "t1", statuses::IN_PROGRESS);
        m.evidence.push(src_evidence("E1", "src/a.rs", "d1"));
        m
    };
    let mut pruner = Pruner::new(PrunePolicy {
        suppress_repeated_evidence: false,
        ..Default::default()
    });
    pruner.classify(&evidence);
    assert_eq!(pruner.classify(&evidence), PruneDecision::Deliver);
}

#[test]
fn contradictory_findings_against_history_are_delivered() {
    // Same statement already delivered as "pass", now reported "unknown":
    // duplicate-looking but contradictory, so it must be delivered.
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::RESULT, "t1", statuses::DONE);
    first.findings.push(finding("F1", "The build passes"));
    first.verification = Some(verification_record("The build passes", verification::PASS));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let mut contradiction = {
        let mut m = first.clone();
        m.message_id = Some("m2".to_string());
        m.findings[0].statement = "the build PASSES".to_string();
        m.verification = Some(verification_record("the build PASSES", verification::UNKNOWN));
        m
    };
    assert_eq!(pruner.classify(&contradiction), PruneDecision::Deliver);

    // The contradiction is now recorded, so any further position on the same
    // statement disagrees with one of the delivered records and is delivered.
    let mut repeat = {
        let mut m = first.clone();
        m.message_id = Some("m3".to_string());
        m.findings[0].statement = "the build PASSES".to_string();
        m.verification = Some(verification_record("the build PASSES", verification::PASS));
        m
    };
    assert_eq!(pruner.classify(&repeat), PruneDecision::Deliver);
}

#[test]
fn matching_status_repeat_is_a_plain_duplicate() {
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::RESULT, "t1", statuses::DONE);
    first.findings.push(finding("F1", "The build passes"));
    first.verification = Some(verification_record("The build passes", verification::PASS));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let mut repeat = {
        let mut m = first.clone();
        m.message_id = Some("m2".to_string());
        m.findings[0].statement = "the build PASSES".to_string();
        m.verification = Some(verification_record("the build PASSES", verification::PASS));
        m
    };
    assert_eq!(
        pruner.classify(&repeat),
        PruneDecision::Suppress {
            reason: PruneReason::DuplicateFindings
        }
    );
}

#[test]
fn unseen_dependencies_deliver_until_the_task_is_seen() {
    // A dependency counts as seen only once a delivered record covers that
    // task; mentions inside other messages' dependency lists do not count.
    let mut pruner = Pruner::new(PrunePolicy::default());
    let mut first = message(kinds::RESULT, "t1", statuses::DONE);
    first.findings.push(finding("F1", "The build passes"));
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);

    let mut with_dep = first.clone();
    with_dep.dependencies.push("t2".to_string());
    assert_eq!(pruner.classify(&with_dep), PruneDecision::Deliver);
    assert_eq!(pruner.classify(&with_dep), PruneDecision::Deliver);

    let task = message(kinds::TASK, "t2", statuses::PENDING);
    assert_eq!(pruner.classify(&task), PruneDecision::Deliver);

    assert_eq!(
        pruner.classify(&with_dep),
        PruneDecision::Suppress {
            reason: PruneReason::DuplicateFindings
        }
    );
}

#[test]
fn history_eviction_delivers_repeats_again() {
    // With max_history = 1 the first record is evicted, so its finding digest
    // is forgotten and the repeat is delivered again instead of suppressed.
    let mut pruner = Pruner::new(PrunePolicy {
        max_history: 1,
        ..Default::default()
    });
    let first = {
        let mut m = message(kinds::RESULT, "t1", statuses::DONE);
        m.findings.push(finding("F1", "First finding"));
        m
    };
    let second = {
        let mut m = message(kinds::RESULT, "t1", statuses::DONE);
        m.findings.push(finding("F1", "Second finding"));
        m
    };
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);
    assert_eq!(pruner.classify(&second), PruneDecision::Deliver);
    assert_eq!(pruner.history().len(), 1);
    // "First finding" was evicted with its record.
    assert_eq!(pruner.classify(&first), PruneDecision::Deliver);
}

#[test]
fn suppressed_messages_are_not_recorded() {
    // max_history = 2. If the suppressed third message were recorded, the
    // fifth would still see the "First finding" digest and be suppressed.
    let mut pruner = Pruner::new(PrunePolicy {
        max_history: 2,
        ..Default::default()
    });
    let messages = ["First finding", "Second finding", "First finding", "Third finding"]
        .iter()
        .map(|statement| {
            let mut m = message(kinds::RESULT, "t1", statuses::DONE);
            m.findings.push(finding("F1", statement));
            m
        })
        .collect::<Vec<_>>();
    assert_eq!(pruner.classify(&messages[0]), PruneDecision::Deliver);
    assert_eq!(pruner.classify(&messages[1]), PruneDecision::Deliver);
    assert_eq!(
        pruner.classify(&messages[2]),
        PruneDecision::Suppress {
            reason: PruneReason::DuplicateFindings
        }
    );
    assert_eq!(pruner.classify(&messages[3]), PruneDecision::Deliver);
    let repeat_of_evicted = {
        let mut m = messages[0].clone();
        m.message_id = Some("m5".to_string());
        m
    };
    assert_eq!(pruner.classify(&repeat_of_evicted), PruneDecision::Deliver);
}

#[test]
fn delivery_records_are_capped_and_shaped() {
    let mut pruner = Pruner::new(PrunePolicy {
        max_history: 2,
        ..Default::default()
    });
    let statements = ["First finding", "Second finding", "Third finding"];
    for (index, statement) in statements.iter().enumerate() {
        let mut m = message(kinds::RESULT, "t1", statuses::DONE);
        m.message_id = Some(format!("m{}", index + 1));
        m.sequence = Some(index as u64 + 1);
        m.findings.push(finding("F1", statement));
        m.evidence.push(src_evidence("E1", "src/a.rs", "d1"));
        assert_eq!(pruner.classify(&m), PruneDecision::Deliver);
    }

    let history = pruner.history();
    assert_eq!(history.len(), 2);
    let latest = &history[1];
    assert_eq!(latest.message_id.as_deref(), Some("m3"));
    assert_eq!(latest.task_id, "t1");
    assert_eq!(latest.kind, kinds::RESULT);
    assert_eq!(latest.status, statuses::DONE);
    assert_eq!(latest.sequence, Some(3));
    assert_eq!(
        latest.finding_digests,
        vec![finding_digest("Third finding")]
    );
    let expected_evidence = src_evidence("E1", "src/a.rs", "d1");
    assert_eq!(latest.evidence_digests, vec![evidence_digest(&expected_evidence)]);
    assert_eq!(
        history[0],
        DeliveryRecord {
            message_id: Some("m2".to_string()),
            task_id: "t1".to_string(),
            kind: kinds::RESULT.to_string(),
            status: statuses::DONE.to_string(),
            sequence: Some(2),
            finding_digests: vec![finding_digest("Second finding")],
            evidence_digests: vec![evidence_digest(&expected_evidence)],
        }
    );
}

#[test]
fn identical_inputs_produce_identical_decisions() {
    let sequence = ["First finding", "First finding", "Second finding", "Second finding"]
        .iter()
        .map(|statement| {
            let mut m = message(kinds::PROGRESS, "t1", statuses::IN_PROGRESS);
            m.findings.push(finding("F1", statement));
            m
        })
        .collect::<Vec<_>>();

    let decide = || -> Vec<PruneDecision> {
        let mut pruner = Pruner::new(PrunePolicy::default());
        sequence.iter().map(|m| pruner.classify(m)).collect()
    };

    assert_eq!(decide(), decide());
    assert_eq!(
        decide(),
        vec![
            PruneDecision::Deliver,
            PruneDecision::Suppress {
                reason: PruneReason::UnchangedProgress
            },
            PruneDecision::Deliver,
            PruneDecision::Suppress {
                reason: PruneReason::UnchangedProgress
            },
        ]
    );
}

#[test]
fn default_policy_matches_the_frozen_spec() {
    let policy = PrunePolicy::default();
    assert!(policy.suppress_duplicate_findings);
    assert!(policy.suppress_unchanged_progress);
    assert!(policy.suppress_repeated_evidence);
    assert_eq!(policy.max_history, 64);
    assert_eq!(Pruner::new(PrunePolicy::default()).policy(), &policy);
}

#[test]
fn digests_are_stable_sha256_hex_over_canonical_content() {
    assert_eq!(
        finding_digest("  The   BUILD passes "),
        finding_digest("the build passes")
    );
    assert_eq!(finding_digest("x").len(), 64);
    assert_eq!(
        evidence_digest(&src_evidence("E1", "src/a.rs", "d1")),
        evidence_digest(&src_evidence("E9", "src/a.rs", "d1"))
    );
    // The evidence id and line range are not part of the digest input.
    let mut ranged = src_evidence("E1", "src/a.rs", "d1");
    ranged.line_range = Some((1, 9));
    assert_eq!(
        evidence_digest(&ranged),
        evidence_digest(&src_evidence("E1", "src/a.rs", "d1"))
    );
}
