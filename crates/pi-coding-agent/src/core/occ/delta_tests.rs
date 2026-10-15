//! Tests for `crate::core::occ::delta` (worker B). All offline.

use crate::core::occ::contract::{ContractError, EvidenceRef, Finding, Verification};
use crate::core::occ::delta::{
    Delta, DeltaError, DeltaReceipt, DeltaTracker, DeltaTrackerState, FieldUpdate, FindingPatch,
    DELTA_BUFFER_CAP,
};
use crate::core::occ::occ1::{Occ1Error, Occ1Message};

fn plain(sequence: u64, base: u64, task: &str) -> Delta {
    Delta {
        task_id: task.to_string(),
        base_sequence: base,
        sequence,
        updates: Vec::new(),
        new_findings: Vec::new(),
        new_evidence: Vec::new(),
        next_actions: Vec::new(),
        reason: None,
    }
}

fn evidence(id: &str) -> EvidenceRef {
    EvidenceRef {
        id: id.to_string(),
        kind: "src".to_string(),
        locator: "src/lib.rs".to_string(),
        line_range: None,
        digest: None,
        revision: None,
        verified: None,
    }
}

fn patch_finding(id: &str) -> FieldUpdate {
    FieldUpdate::UpdateFinding {
        id: id.to_string(),
        patch: FindingPatch {
            statement: Some("changed".to_string()),
            confidence: None,
            add_evidence: Vec::new(),
            remove_evidence: Vec::new(),
        },
    }
}

#[test]
fn happy_path_applies_in_order() {
    let mut tracker = DeltaTracker::new("t1", 10);
    assert_eq!(tracker.task_id(), "t1");
    assert_eq!(tracker.next_sequence(), 11);
    assert!(tracker.missing_sequences().is_empty());
    assert!(!tracker.needs_full_snapshot());

    let receipt = tracker.observe(&plain(11, 10, "t1")).expect("applies");
    assert_eq!(
        receipt,
        DeltaReceipt {
            applied: true,
            buffered: false,
            sequence: 11
        }
    );
    assert_eq!(tracker.next_sequence(), 12);
    assert!(tracker.missing_sequences().is_empty());
    assert!(!tracker.needs_full_snapshot());
    assert!(tracker.drain_ready().is_empty());

    assert_eq!(
        tracker.observe(&plain(12, 10, "t1")).expect("applies"),
        DeltaReceipt {
            applied: true,
            buffered: false,
            sequence: 12
        }
    );
    assert_eq!(tracker.next_sequence(), 13);
}

#[test]
fn out_of_order_buffers_then_applies_on_gap_fill() {
    let mut tracker = DeltaTracker::new("t1", 5);
    assert_eq!(
        tracker.observe(&plain(8, 5, "t1")).unwrap_err(),
        DeltaError::Gap { expected: 6, got: 8 }
    );
    assert_eq!(tracker.next_sequence(), 6);
    assert_eq!(tracker.missing_sequences(), vec![6, 7]);
    assert!(tracker.needs_full_snapshot());
    // the buffering receipt is queued, not returned
    assert_eq!(
        tracker.drain_ready(),
        vec![DeltaReceipt {
            applied: false,
            buffered: true,
            sequence: 8
        }]
    );

    assert_eq!(
        tracker.observe(&plain(7, 5, "t1")).unwrap_err(),
        DeltaError::Gap { expected: 6, got: 7 }
    );
    assert_eq!(tracker.missing_sequences(), vec![6]);

    let receipt = tracker.observe(&plain(6, 5, "t1")).expect("fills the gap");
    assert_eq!(
        receipt,
        DeltaReceipt {
            applied: true,
            buffered: false,
            sequence: 6
        }
    );
    // 7 and 8 became applicable and drained in sequence order
    assert_eq!(
        tracker.drain_ready(),
        vec![
            DeltaReceipt {
                applied: false,
                buffered: true,
                sequence: 7
            },
            DeltaReceipt {
                applied: true,
                buffered: true,
                sequence: 7
            },
            DeltaReceipt {
                applied: true,
                buffered: true,
                sequence: 8
            },
        ]
    );
    assert_eq!(tracker.next_sequence(), 9);
    assert!(tracker.missing_sequences().is_empty());
    assert!(!tracker.needs_full_snapshot());
}

#[test]
fn duplicate_replay_is_idempotent() {
    let mut tracker = DeltaTracker::new("t1", 0);
    let first = plain(1, 0, "t1");
    tracker.observe(&first).expect("applies");
    assert_eq!(
        tracker.observe(&first).unwrap_err(),
        DeltaError::Duplicate { sequence: 1 }
    );
    assert_eq!(tracker.next_sequence(), 2);
    assert!(tracker.drain_ready().is_empty());

    // replay of a buffered delta is also a duplicate no-op
    assert_eq!(
        tracker.observe(&plain(3, 0, "t1")).unwrap_err(),
        DeltaError::Gap { expected: 2, got: 3 }
    );
    assert_eq!(
        tracker.observe(&plain(3, 0, "t1")).unwrap_err(),
        DeltaError::Duplicate { sequence: 3 }
    );
    assert_eq!(tracker.next_sequence(), 2);

    // a sequence at or below the base is already contained in the snapshot
    assert_eq!(
        tracker.observe(&plain(0, 0, "t1")).unwrap_err(),
        DeltaError::Duplicate { sequence: 0 }
    );
    assert_eq!(tracker.next_sequence(), 2);
    tracker.drain_ready();
}

#[test]
fn wrong_task_is_rejected_without_state_change() {
    let mut tracker = DeltaTracker::new("t1", 10);
    assert_eq!(
        tracker.observe(&plain(11, 10, "t2")).unwrap_err(),
        DeltaError::WrongTask {
            expected: "t1".to_string(),
            got: "t2".to_string()
        }
    );
    assert_eq!(tracker.next_sequence(), 11);
    assert!(tracker.missing_sequences().is_empty());
    assert!(!tracker.needs_full_snapshot());
    // the correct stream is unaffected
    tracker.observe(&plain(11, 10, "t1")).expect("applies");
}

#[test]
fn stale_base_then_base_unavailable() {
    let mut tracker = DeltaTracker::new("t1", 10);
    assert_eq!(
        tracker.observe(&plain(11, 11, "t1")).unwrap_err(),
        DeltaError::StaleBase {
            base: 10,
            got_base: 11
        }
    );
    assert!(tracker.needs_full_snapshot());
    // even a matching-base delta is rejected until the tracker is rebuilt
    assert_eq!(
        tracker.observe(&plain(11, 10, "t1")).unwrap_err(),
        DeltaError::BaseUnavailable
    );
    assert_eq!(
        tracker.observe(&plain(12, 11, "t1")).unwrap_err(),
        DeltaError::BaseUnavailable
    );
    // task identity is still classified first
    assert_eq!(
        tracker.observe(&plain(11, 10, "t2")).unwrap_err(),
        DeltaError::WrongTask {
            expected: "t1".to_string(),
            got: "t2".to_string()
        }
    );
    // a rebuilt tracker works again
    let mut rebuilt = DeltaTracker::new("t1", 11);
    rebuilt.observe(&plain(12, 11, "t1")).expect("applies after rebuild");
}

#[test]
fn buffer_cap_reports_base_unavailable() {
    let mut tracker = DeltaTracker::new("t1", 0);
    for sequence in 2..=(DELTA_BUFFER_CAP as u64 + 1) {
        assert_eq!(
            tracker.observe(&plain(sequence, 0, "t1")).unwrap_err(),
            DeltaError::Gap { expected: 1, got: sequence }
        );
    }
    // the buffer is full: one more buffered delta is refused, state unchanged
    assert_eq!(
        tracker
            .observe(&plain(DELTA_BUFFER_CAP as u64 + 2, 0, "t1"))
            .unwrap_err(),
        DeltaError::BaseUnavailable
    );
    assert_eq!(tracker.next_sequence(), 1);
    assert!(tracker.needs_full_snapshot());

    // filling the gap still recovers and drains the whole buffer
    tracker.observe(&plain(1, 0, "t1")).expect("applies");
    assert_eq!(tracker.next_sequence(), DELTA_BUFFER_CAP as u64 + 2);
    assert!(tracker.missing_sequences().is_empty());
    assert!(!tracker.needs_full_snapshot());
    let receipts = tracker.drain_ready();
    assert_eq!(receipts.len(), 2 * DELTA_BUFFER_CAP);
    assert_eq!(
        receipts[0],
        DeltaReceipt {
            applied: false,
            buffered: true,
            sequence: 2
        }
    );
    assert_eq!(
        receipts[DELTA_BUFFER_CAP],
        DeltaReceipt {
            applied: true,
            buffered: true,
            sequence: 2
        }
    );
    assert_eq!(
        receipts[2 * DELTA_BUFFER_CAP - 1],
        DeltaReceipt {
            applied: true,
            buffered: true,
            sequence: DELTA_BUFFER_CAP as u64 + 1
        }
    );
    tracker
        .observe(&plain(DELTA_BUFFER_CAP as u64 + 2, 0, "t1"))
        .expect("cap freed after drain");
}

#[test]
fn seed_findings_and_unknown_finding() {
    let mut tracker = DeltaTracker::new("t1", 0);
    let mut patching = plain(1, 0, "t1");
    patching.updates.push(patch_finding("F9"));
    assert_eq!(
        tracker.observe(&patching).unwrap_err(),
        DeltaError::UnknownFinding { id: "F9".to_string() }
    );
    assert_eq!(tracker.next_sequence(), 1); // state unchanged
    tracker.seed_findings(&["F9".to_string()]);
    tracker.observe(&patching).expect("applies after seeding");
    assert_eq!(tracker.next_sequence(), 2);

    // a delta may patch a finding it introduces itself
    let mut introducing = plain(2, 0, "t1");
    introducing
        .new_findings
        .push(Finding {
            id: "N1".to_string(),
            statement: "fresh finding".to_string(),
            confidence: None,
            evidence: Vec::new(),
        });
    introducing.updates.push(FieldUpdate::UpdateFinding {
        id: "N1".to_string(),
        patch: FindingPatch {
            statement: None,
            confidence: Some(Some(0.5)),
            add_evidence: Vec::new(),
            remove_evidence: Vec::new(),
        },
    });
    tracker.observe(&introducing).expect("same-delta introduction is known");

    // introduced ids stay known for later deltas
    let mut later = plain(3, 0, "t1");
    later.updates.push(patch_finding("N1"));
    tracker.observe(&later).expect("introduced ids stay known");

    let mut unknown = plain(4, 0, "t1");
    unknown.updates.push(patch_finding("N2"));
    assert_eq!(
        tracker.observe(&unknown).unwrap_err(),
        DeltaError::UnknownFinding { id: "N2".to_string() }
    );
}

#[test]
fn sequence_queries_track_stream_state() {
    let mut tracker = DeltaTracker::new("t1", 100);
    assert_eq!(tracker.next_sequence(), 101);
    assert_eq!(tracker.missing_sequences(), Vec::<u64>::new());
    assert!(!tracker.needs_full_snapshot());

    tracker.observe(&plain(103, 100, "t1")).unwrap_err();
    assert_eq!(tracker.next_sequence(), 101);
    assert_eq!(tracker.missing_sequences(), vec![101, 102]);
    assert!(tracker.needs_full_snapshot());

    tracker.observe(&plain(102, 100, "t1")).unwrap_err();
    assert_eq!(tracker.missing_sequences(), vec![101]);

    tracker.observe(&plain(101, 100, "t1")).expect("fills the gap");
    assert_eq!(tracker.missing_sequences(), Vec::<u64>::new());
    assert!(!tracker.needs_full_snapshot());
    assert_eq!(tracker.next_sequence(), 104);

    // waiting for the next delta alone is not a known gap
    assert_eq!(tracker.missing_sequences(), Vec::<u64>::new());
}

#[test]
fn drain_failure_surfaces_and_allows_retry() {
    let mut tracker = DeltaTracker::new("t1", 0);
    let mut broken = plain(2, 0, "t1");
    broken.updates.push(patch_finding("F7"));
    assert_eq!(
        tracker.observe(&broken).unwrap_err(),
        DeltaError::Gap { expected: 1, got: 2 }
    );
    // collect the buffering receipt before the gap fills
    assert_eq!(
        tracker.drain_ready(),
        vec![DeltaReceipt {
            applied: false,
            buffered: true,
            sequence: 2
        }]
    );

    // the gap fills; the buffered delta fails finding validation
    let error = tracker.observe(&plain(1, 0, "t1")).expect_err("drain failure");
    assert_eq!(error, DeltaError::UnknownFinding { id: "F7".to_string() });
    // the observed delta applied: its receipt was queued, not returned
    assert_eq!(
        tracker.drain_ready(),
        vec![DeltaReceipt {
            applied: true,
            buffered: false,
            sequence: 1
        }]
    );
    assert_eq!(tracker.next_sequence(), 2);
    // the failed sequence is no longer buffered, so a fixed retry applies
    tracker.seed_findings(&["F7".to_string()]);
    tracker.observe(&broken).expect("retry applies after seeding");
    assert_eq!(tracker.next_sequence(), 3);
}

#[test]
fn state_restore_mid_stream_yields_identical_decisions() {
    type Log = Vec<(Result<DeltaReceipt, DeltaError>, u64, Vec<u64>, bool)>;

    fn record(tracker: &mut DeltaTracker, deltas: &[Delta], log: &mut Log) {
        for delta in deltas {
            let result = tracker.observe(delta);
            log.push((
                result,
                tracker.next_sequence(),
                tracker.missing_sequences(),
                tracker.needs_full_snapshot(),
            ));
        }
    }

    let stream = vec![
        plain(11, 10, "t1"),
        plain(13, 10, "t1"),
        plain(14, 10, "t1"),
        plain(12, 10, "t1"),
        plain(15, 10, "t1"),
    ];

    let mut continuous = DeltaTracker::new("t1", 10);
    let mut continuous_log: Log = Vec::new();
    record(&mut continuous, &stream, &mut continuous_log);

    let mut staged = DeltaTracker::new("t1", 10);
    let mut head_log: Log = Vec::new();
    record(&mut staged, &stream[..3], &mut head_log);
    let snapshot = staged.state();
    let json = serde_json::to_string(&snapshot).expect("serializes");
    let mut restored =
        DeltaTracker::restore(serde_json::from_str::<DeltaTrackerState>(&json).expect("parses"));
    let mut tail_log: Log = Vec::new();
    record(&mut restored, &stream[3..], &mut tail_log);

    assert_eq!(head_log.len(), 3);
    assert_eq!(tail_log.len(), 2);
    let mut combined = head_log.clone();
    combined.extend(tail_log);
    assert_eq!(combined, continuous_log);

    assert_eq!(restored.next_sequence(), continuous.next_sequence());
    assert_eq!(restored.missing_sequences(), continuous.missing_sequences());
    assert_eq!(
        restored.needs_full_snapshot(),
        continuous.needs_full_snapshot()
    );
    assert_eq!(restored.state(), continuous.state());

    // duplicates replay identically after restore
    assert_eq!(
        restored.observe(&stream[0]).unwrap_err(),
        DeltaError::Duplicate { sequence: 11 }
    );
    assert_eq!(
        continuous.observe(&stream[0]).unwrap_err(),
        DeltaError::Duplicate { sequence: 11 }
    );
    assert_eq!(restored.state(), continuous.state());
}

#[test]
fn restore_normalizes_impossible_buffered_entries() {
    let state = DeltaTrackerState {
        task_id: "t1".to_string(),
        base_sequence: 10,
        next_sequence: 11,
        buffered: vec![plain(13, 10, "t1"), plain(12, 10, "other")],
        known_findings: Vec::new(),
        stale: false,
        pending_receipts: Vec::new(),
    };
    let mut tracker = DeltaTracker::restore(state);
    assert_eq!(tracker.missing_sequences(), vec![11, 12]);
    assert!(tracker.needs_full_snapshot());
    tracker.observe(&plain(11, 10, "t1")).expect("applies");
    // if the foreign entry at 12 were still buffered this would be a
    // Duplicate; the normalization dropped it, so a t1 delta applies
    tracker
        .observe(&plain(12, 10, "t1"))
        .expect("foreign entry dropped, not buffered");
    assert_eq!(tracker.next_sequence(), 14); // 13 drains after 12
}

#[test]
fn occ1_round_trip_every_field_type() {
    let mut delta = plain(11, 10, "t-42");
    delta.updates.push(FieldUpdate::SetStatus("in_progress".to_string()));
    delta.updates.push(FieldUpdate::UpdateFinding {
        id: "F2".to_string(),
        patch: FindingPatch {
            statement: Some("revised statement".to_string()),
            confidence: Some(Some(0.75)),
            add_evidence: vec!["E5".to_string()],
            remove_evidence: vec!["E6".to_string()],
        },
    });
    delta.updates.push(FieldUpdate::UpdateFinding {
        id: "F3".to_string(),
        patch: FindingPatch {
            statement: None,
            confidence: Some(None),
            add_evidence: Vec::new(),
            remove_evidence: Vec::new(),
        },
    });
    delta.updates.push(FieldUpdate::SetVerification(Verification {
        claim: "tests pass".to_string(),
        status: "pass".to_string(),
        method: "cargo_test".to_string(),
        receipt: Some("log/r1".to_string()),
    }));
    delta.new_findings.push(Finding {
        id: "N1".to_string(),
        statement: "fresh finding".to_string(),
        confidence: Some(0.9),
        evidence: vec!["E1".to_string()],
    });
    delta.new_findings.push(Finding {
        id: "N2".to_string(),
        statement: "another finding".to_string(),
        confidence: None,
        evidence: Vec::new(),
    });
    delta.new_evidence.push(EvidenceRef {
        id: "E1".to_string(),
        kind: "src".to_string(),
        locator: "src/a.rs".to_string(),
        line_range: Some((12, 30)),
        digest: Some("deadbeef".to_string()),
        revision: Some("159ce68cc".to_string()),
        verified: Some(true),
    });
    delta
        .new_evidence
        .push(EvidenceRef {
            id: "E2".to_string(),
            kind: "commit".to_string(),
            locator: "0f1e2d3c".to_string(),
            line_range: None,
            digest: None,
            revision: None,
            verified: Some(false),
        });
    delta.next_actions = vec!["review patch".to_string(), "run tests".to_string()];
    delta.reason = Some("wrap up".to_string());

    let message = delta.to_occ1().expect("encodes");
    let wire = message.encode().expect("wire");
    let decoded = Occ1Message::decode(&wire).expect("parses");
    let back = Delta::from_occ1(&decoded).expect("decodes");
    assert_eq!(back, delta);

    // the frozen field layout
    assert_eq!(message.get("TASK"), Some("t-42"));
    assert_eq!(message.get("SEQ"), Some("11"));
    assert_eq!(message.get("BASE"), Some("10"));
    assert_eq!(message.get("STATUS"), Some("in_progress"));
    assert_eq!(
        message.get_all("UPDATE"),
        vec![
            "F2:stmt=revised statement",
            "F2:conf=0.750",
            "F2:ev+E5=E5",
            "F2:ev-E6=E6",
            "F3:conf=none",
        ]
    );
    assert_eq!(message.get("NEW1"), Some("fresh finding"));
    assert_eq!(message.get("NEWC1"), Some("0.900"));
    assert_eq!(message.get("NEWE1"), Some("E1"));
    assert_eq!(message.get("NEW2"), Some("another finding"));
    assert_eq!(message.get("E1"), Some("src:src/a.rs"));
    assert_eq!(message.get("EL1"), Some("12-30"));
    assert_eq!(message.get("ED1"), Some("deadbeef"));
    assert_eq!(message.get("ER1"), Some("159ce68cc"));
    assert_eq!(message.get("EV1"), Some("pass"));
    assert_eq!(message.get("E2"), Some("commit:0f1e2d3c"));
    assert_eq!(message.get("EV2"), Some("fail"));
    assert_eq!(message.get("V"), Some("tests pass"));
    assert_eq!(message.get("VS"), Some("pass"));
    assert_eq!(message.get("VM"), Some("cargo_test"));
    assert_eq!(message.get("VR"), Some("log/r1"));
    assert_eq!(message.get_all("NEXT"), vec!["review patch", "run tests"]);
    assert_eq!(message.get("WHY"), Some("wrap up"));

    // a minimal delta round-trips too
    let minimal = plain(7, 3, "t-min");
    assert_eq!(
        Delta::from_occ1(&minimal.to_occ1().expect("encodes")).expect("decodes"),
        minimal
    );
}

#[test]
fn repeated_update_lines_merge_in_first_appearance_order() {
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "1")
        .with("BASE", "0")
        .with("UPDATE", "F1:stmt=a")
        .with("UPDATE", "F1:ev+E1=E1")
        .with("UPDATE", "F2:stmt=b")
        .with("UPDATE", "F1:stmt=c")
        .with("UPDATE", "F1:ev-E2=E2");
    let delta = Delta::from_occ1(&message).expect("decodes");
    assert_eq!(delta.updates.len(), 2);
    match &delta.updates[0] {
        FieldUpdate::UpdateFinding { id, patch } => {
            assert_eq!(id, "F1");
            assert_eq!(patch.statement.as_deref(), Some("c")); // last wins
            assert_eq!(patch.add_evidence, vec!["E1"]);
            assert_eq!(patch.remove_evidence, vec!["E2"]);
            assert_eq!(patch.confidence, None);
        }
        other => panic!("unexpected update {other:?}"),
    }
    match &delta.updates[1] {
        FieldUpdate::UpdateFinding { id, patch } => {
            assert_eq!(id, "F2");
            assert_eq!(patch.statement.as_deref(), Some("b"));
        }
        other => panic!("unexpected update {other:?}"),
    }
}

#[test]
fn from_occ1_ignores_unrelated_extra_fields() {
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "1")
        .with("BASE", "0")
        .with("KIND", "PROGRESS")
        .with("MSGID", "m-1");
    let delta = Delta::from_occ1(&message).expect("decodes");
    assert_eq!(delta.task_id, "t");
    assert_eq!(delta.sequence, 1);
    assert_eq!(delta.base_sequence, 0);
    assert!(delta.updates.is_empty());
    assert!(delta.next_actions.is_empty());
    assert_eq!(delta.reason, None);
}

#[test]
fn wire_missing_or_malformed_scalars_are_rejected() {
    // missing TASK
    let message = Occ1Message::new().with("SEQ", "2").with("BASE", "1");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::MissingRequired { field: "TASK" })
    ));
    // missing SEQ
    let message = Occ1Message::new().with("TASK", "t").with("BASE", "1");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::MissingRequired { field: "SEQ" })
    ));
    // missing BASE
    let message = Occ1Message::new().with("TASK", "t").with("SEQ", "2");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::MissingRequired { field: "BASE" })
    ));
    // non-numeric SEQ and BASE
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2x")
        .with("BASE", "1");
    match Delta::from_occ1(&message) {
        Err(ContractError::Occ1Conversion(text)) => assert!(text.contains("SEQ")),
        other => panic!("expected SEQ conversion error, got {other:?}"),
    }
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "one");
    match Delta::from_occ1(&message) {
        Err(ContractError::Occ1Conversion(text)) => assert!(text.contains("BASE")),
        other => panic!("expected BASE conversion error, got {other:?}"),
    }
    // repeated TASK is ambiguous
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("TASK", "t2")
        .with("SEQ", "2")
        .with("BASE", "1");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));
    // unknown STATUS
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("STATUS", "weird");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::UnknownStatus(status)) if status == "weird"
    ));
    // control characters never reach the tracker: rejected by the codec
    assert!(matches!(
        Occ1Message::decode("OCC/1\nTASK=t\u{1}"),
        Err(Occ1Error::ControlChar { .. })
    ));
    // a programmatically built message is validated by from_occ1 itself
    let message = Occ1Message::new()
        .with("TASK", "t\u{1}")
        .with("SEQ", "2")
        .with("BASE", "1");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::InvalidTaskId(_))
    ));
}

#[test]
fn wire_update_faults_are_rejected() {
    fn with_update(line: &str) -> Occ1Message {
        Occ1Message::new()
            .with("TASK", "t")
            .with("SEQ", "2")
            .with("BASE", "1")
            .with("UPDATE", line)
    }
    // no '=' after the field
    for line in [
        "F1:stmt",
        "F1",
        ":stmt=x",
        "F1:bogus=x",
        "F1:ev+E2=E3",
        "F1:ev-=E2",
        "F1:conf=abc",
        "F1:ev+=E2",
    ] {
        let message = with_update(line);
        match Delta::from_occ1(&message) {
            Err(ContractError::Occ1Conversion(_)) => {}
            other => panic!("UPDATE {line:?} should be rejected, got {other:?}"),
        }
    }
    // out-of-range confidence is a value error, not a grammar error
    let message = with_update("F1:conf=2.5");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::InvalidConfidence(value)) if value == 2.5
    ));
    // valid lines still decode
    let message = with_update("F1:stmt=ok");
    assert!(Delta::from_occ1(&message).is_ok());
}

#[test]
fn wire_index_faults_are_rejected() {
    // NEW index gap
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("NEW1", "a")
        .with("NEW3", "c");
    match Delta::from_occ1(&message) {
        Err(ContractError::Occ1Conversion(text)) => {
            assert!(text.contains("NEW"), "unexpected error {text:?}")
        }
        other => panic!("NEW gap should be rejected, got {other:?}"),
    }
    // NEWC without its NEW
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("NEW1", "a")
        .with("NEWC2", "0.5");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));
    // NEWC not a number / out of range
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("NEW1", "a")
        .with("NEWC1", "abc");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("NEW1", "a")
        .with("NEWC1", "1.5");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::InvalidConfidence(_))
    ));
    // E index gap
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("E1", "src:a")
        .with("E3", "src:c");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));
    // repeated NEW1 is ambiguous
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("NEW1", "a")
        .with("NEW1", "b");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));
    // dangling NEWE reference
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("NEW1", "a")
        .with("NEWE1", "E9");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::UnknownEvidence { evidence_id, .. }) if evidence_id == "E9"
    ));
}

#[test]
fn wire_evidence_and_verification_faults_are_rejected() {
    fn with_evidence(extra: &[(&str, &str)]) -> Occ1Message {
        let mut message = Occ1Message::new()
            .with("TASK", "t")
            .with("SEQ", "2")
            .with("BASE", "1")
            .with("E1", "src:a.rs");
        for (key, value) in extra {
            message = message.with(*key, *value);
        }
        message
    }
    // EV bad value
    let message = with_evidence(&[("EV1", "maybe")]);
    match Delta::from_occ1(&message) {
        Err(ContractError::Occ1Conversion(text)) => {
            assert!(text.contains("EV1"), "unexpected error {text:?}")
        }
        other => panic!("EV fault should be rejected, got {other:?}"),
    }
    // EL without dash / non-numeric / missing E
    for extra in [
        vec![("EL1", "abc")],
        vec![("EL1", "10")],
        vec![("EL1", "x-9")],
        vec![("EL1", "1-x")],
        vec![("EL2", "1-2")],
    ] {
        let message = with_evidence(&extra);
        match Delta::from_occ1(&message) {
            Err(ContractError::Occ1Conversion(_)) => {}
            other => panic!("EL fault {extra:?} should be rejected, got {other:?}"),
        }
    }
    // unknown evidence kind
    let message = with_evidence(&[("E2", "weird:x")]);
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::UnknownEvidenceKind(kind)) if kind == "weird"
    ));
    // E value without ':'
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("E1", "no-colon");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));

    // V without VS
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("V", "claim")
        .with("VM", "cargo_test");
    match Delta::from_occ1(&message) {
        Err(ContractError::Occ1Conversion(text)) => {
            assert!(text.contains("V without VS"), "unexpected error {text:?}")
        }
        other => panic!("V without VS should be rejected, got {other:?}"),
    }
    // V without VM
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("V", "claim")
        .with("VS", "pass");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));
    // VS without V
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("VS", "pass");
    match Delta::from_occ1(&message) {
        Err(ContractError::Occ1Conversion(text)) => {
            assert!(text.contains("without V"), "unexpected error {text:?}")
        }
        other => panic!("VS without V should be rejected, got {other:?}"),
    }
    // unknown verification status
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("V", "claim")
        .with("VS", "bogus")
        .with("VM", "cargo_test");
    assert!(matches!(
        Delta::from_occ1(&message),
        Err(ContractError::Occ1Conversion(_))
    ));
    // valid verification still decodes
    let message = Occ1Message::new()
        .with("TASK", "t")
        .with("SEQ", "2")
        .with("BASE", "1")
        .with("V", "claim")
        .with("VS", "fail")
        .with("VM", "cargo_test");
    assert!(Delta::from_occ1(&message).is_ok());
}

#[test]
fn to_occ1_rejects_unrepresentable_deltas() {
    // AddEvidence has no wire form
    let mut delta = plain(2, 1, "t");
    delta.updates.push(FieldUpdate::AddEvidence(evidence("E1")));
    match delta.to_occ1() {
        Err(ContractError::Occ1Conversion(text)) => {
            assert!(text.contains("AddEvidence"), "unexpected error {text:?}")
        }
        other => panic!("AddEvidence should be rejected, got {other:?}"),
    }

    // non-canonical update order
    let mut delta = plain(2, 1, "t");
    delta.updates.push(patch_finding("F1"));
    delta
        .updates
        .push(FieldUpdate::SetStatus("done".to_string()));
    match delta.to_occ1() {
        Err(ContractError::Occ1Conversion(text)) => {
            assert!(text.contains("SetStatus"), "unexpected error {text:?}")
        }
        other => panic!("late SetStatus should be rejected, got {other:?}"),
    }

    // duplicate UpdateFinding ids
    let mut delta = plain(2, 1, "t");
    delta.updates.push(patch_finding("F1"));
    delta.updates.push(patch_finding("F1"));
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::Occ1Conversion(_))
    ));

    // empty patch
    let mut delta = plain(2, 1, "t");
    delta.updates.push(FieldUpdate::UpdateFinding {
        id: "F1".to_string(),
        patch: FindingPatch {
            statement: None,
            confidence: None,
            add_evidence: Vec::new(),
            remove_evidence: Vec::new(),
        },
    });
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::Occ1Conversion(_))
    ));

    // non-positional new finding id
    let mut delta = plain(2, 1, "t");
    delta
        .new_findings
        .push(Finding {
            id: "F17".to_string(),
            statement: "task-scoped".to_string(),
            confidence: None,
            evidence: Vec::new(),
        });
    match delta.to_occ1() {
        Err(ContractError::Occ1Conversion(text)) => {
            assert!(text.contains("N1"), "unexpected error {text:?}")
        }
        other => panic!("non-positional id should be rejected, got {other:?}"),
    }

    // non-positional evidence id
    let mut delta = plain(2, 1, "t");
    delta.new_evidence.push(evidence("E9"));
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::Occ1Conversion(_))
    ));

    // dangling NEWE reference at encode time
    let mut delta = plain(2, 1, "t");
    delta
        .new_findings
        .push(Finding {
            id: "N1".to_string(),
            statement: "cites nothing".to_string(),
            confidence: None,
            evidence: vec!["E4".to_string()],
        });
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::UnknownEvidence { .. })
    ));

    // unknown status
    let mut delta = plain(2, 1, "t");
    delta
        .updates
        .push(FieldUpdate::SetStatus("weird".to_string()));
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::UnknownStatus(_))
    ));

    // unknown verification status
    let mut delta = plain(2, 1, "t");
    delta
        .updates
        .push(FieldUpdate::SetVerification(Verification {
            claim: "c".to_string(),
            status: "bogus".to_string(),
            method: "cargo_test".to_string(),
            receipt: None,
        }));
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::Occ1Conversion(_))
    ));

    // unknown evidence kind
    let mut delta = plain(2, 1, "t");
    delta.new_evidence.push(EvidenceRef {
        kind: "weird".to_string(),
        ..evidence("E1")
    });
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::UnknownEvidenceKind(_))
    ));

    // out-of-range confidence values
    let mut delta = plain(2, 1, "t");
    delta.updates.push(FieldUpdate::UpdateFinding {
        id: "F1".to_string(),
        patch: FindingPatch {
            statement: None,
            confidence: Some(Some(1.5)),
            add_evidence: Vec::new(),
            remove_evidence: Vec::new(),
        },
    });
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::InvalidConfidence(_))
    ));
    let mut delta = plain(2, 1, "t");
    delta
        .new_findings
        .push(Finding {
            id: "N1".to_string(),
            statement: "s".to_string(),
            confidence: Some(-0.1),
            evidence: Vec::new(),
        });
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::InvalidConfidence(_))
    ));

    // reference ids must survive the grammar
    let mut delta = plain(2, 1, "t");
    delta.updates.push(patch_finding("F:1"));
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::Occ1Conversion(_))
    ));
    let mut delta = plain(2, 1, "t");
    delta.updates.push(FieldUpdate::UpdateFinding {
        id: "F1".to_string(),
        patch: FindingPatch {
            statement: None,
            confidence: None,
            add_evidence: vec!["E,2".to_string()],
            remove_evidence: Vec::new(),
        },
    });
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::Occ1Conversion(_))
    ));

    // malformed task id
    let mut delta = plain(2, 1, "");
    assert!(matches!(
        delta.to_occ1(),
        Err(ContractError::InvalidTaskId(_))
    ));
}

#[test]
fn serde_round_trips_confidence_triple() {
    let variants = [
        None,
        Some(None),
        Some(Some(0.5)),
        Some(Some(1.0)),
    ];
    for confidence in variants {
        let patch = FindingPatch {
            statement: None,
            confidence,
            add_evidence: Vec::new(),
            remove_evidence: Vec::new(),
        };
        let json = serde_json::to_string(&patch).expect("serializes");
        let back: FindingPatch = serde_json::from_str(&json).expect("deserializes");
        assert_eq!(back, patch);
    }
    // a full state round-trips through JSON
    let mut tracker = DeltaTracker::new("t1", 3);
    let mut delta = plain(5, 3, "t1");
    delta.updates.push(FieldUpdate::UpdateFinding {
        id: "F1".to_string(),
        patch: FindingPatch {
            statement: None,
            confidence: Some(None),
            add_evidence: vec!["E1".to_string()],
            remove_evidence: Vec::new(),
        },
    });
    delta
        .new_findings
        .push(Finding {
            id: "N1".to_string(),
            statement: "s".to_string(),
            confidence: Some(0.5),
            evidence: Vec::new(),
        });
    assert_eq!(
        tracker.observe(&delta).unwrap_err(),
        DeltaError::Gap { expected: 4, got: 5 }
    );
    let state = tracker.state();
    let json = serde_json::to_string(&state).expect("serializes");
    let back: DeltaTrackerState = serde_json::from_str(&json).expect("deserializes");
    assert_eq!(back, state);
    assert_eq!(DeltaTracker::restore(back).state(), state);
}
