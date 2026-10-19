//! Regression tests for the W9 ledger defects (context-rot iteration 2).
//!
//! W9-F2/W9-F5: `build_ledger` numbered item ids by current section length, so a
//! merge after retirements re-issued a retired id to newly captured content and
//! the merge path's blanket `!retired.contains(id)` retain silently deleted it.
//! W9-F4: the supersession pass scored corrections against every item,
//! including other corrections, so two user corrections could retire each other
//! while the decision they corrected stayed current.
//!
//! The scenario shapes come from the W9 incremental runs
//! (`research/context_rot_r2/incremental/runs/w9-*-late*.scenario.json`).

use pi_agent_core::types::AgentMessage;
use pi_ai::types::{
    AssistantMessage, ContentBlock, Message, TextContent, UserContent, UserMessage,
};
use pi_coding_agent::core::compaction::compaction::CompactionSessionEntry;
use pi_coding_agent::core::compaction::task_state::{TaskStateLedger, build_ledger, render_ledger};
use serde_json::json;

const NOTIFIER_DECISION: &str =
    "Decision: use architecture A for the notifier module R2-SUPE-da1ea13c";
const NOTIFIER_CORRECTION: &str = "CORRECTION: forget that; use architecture B for the notifier module instead R2-CORR-b80518c9. The earlier A decision is superseded.";
const SCHEDULER_DECISION: &str =
    "Decision: use architecture A for the scheduler module R2-SUPE-bceddd90";
const SCHEDULER_CORRECTION: &str = "CORRECTION: forget that; use architecture B for the scheduler module instead R2-CORR-f2a8ddf5. The earlier A decision is superseded.";

fn user_entry(id: &str, text: &str) -> CompactionSessionEntry {
    CompactionSessionEntry::Message {
        id: id.to_string(),
        parent_id: None,
        message: AgentMessage::Message(Message::User(UserMessage::new(
            UserContent::Text(text.into()),
            0,
        ))),
    }
}

fn assistant_entry(id: &str, text: &str) -> CompactionSessionEntry {
    CompactionSessionEntry::Message {
        id: id.to_string(),
        parent_id: None,
        message: AgentMessage::Message(Message::Assistant(AssistantMessage {
            content: vec![ContentBlock::Text(TextContent::new(text))],
            stop_reason: "stop".into(),
            ..Default::default()
        })),
    }
}

/// The W9 late-corrections window: two decisions, each followed by a user
/// correction that supersedes it.
fn correction_window() -> Vec<CompactionSessionEntry> {
    vec![
        assistant_entry("a1", NOTIFIER_DECISION),
        user_entry("u2", NOTIFIER_CORRECTION),
        assistant_entry("a3", SCHEDULER_DECISION),
        user_entry("u4", SCHEDULER_CORRECTION),
    ]
}

#[test]
fn w9_f4_corrections_supersede_the_older_decisions_not_each_other() {
    let ledger = build_ledger(&correction_window(), None);

    assert!(
        ledger
            .decisions
            .iter()
            .all(|item| !item.text.contains("architecture A")),
        "superseded decisions must leave the current list: {:?}",
        ledger.decisions
    );
    assert!(
        ledger.constraints.iter().any(|item| item
            .text
            .contains("use architecture B for the notifier module")),
        "the notifier correction is an authoritative user statement and must stay active: {:?}",
        ledger.constraints
    );
    assert!(
        ledger.constraints.iter().any(|item| item
            .text
            .contains("use architecture B for the scheduler module")),
        "the scheduler correction is an authoritative user statement and must stay active: {:?}",
        ledger.constraints
    );

    assert_eq!(ledger.superseded.len(), 2, "{:#?}", ledger.superseded);
    let mut old_entries: Vec<&str> = ledger
        .superseded
        .iter()
        .map(|item| item.old_entry.as_str())
        .collect();
    old_entries.sort();
    assert_eq!(
        old_entries,
        vec!["a1", "a3"],
        "the retired items must be the older decisions, not other corrections"
    );
    for supersession in &ledger.superseded {
        assert!(
            supersession.new_text.contains("architecture B"),
            "the correction must carry the requirement: {supersession:#?}"
        );
    }

    let rendered = render_ledger(&ledger);
    assert!(
        !rendered.contains("architecture A for the notifier"),
        "{rendered}"
    );
    assert!(
        !rendered.contains("architecture A for the scheduler"),
        "{rendered}"
    );
    assert!(rendered.contains("use architecture B for the notifier module instead"));
    assert!(rendered.contains("use architecture B for the scheduler module instead"));
}

#[test]
fn w9_f2_a_late_decision_reusing_a_retired_id_is_not_deleted() {
    let first = build_ledger(
        &[
            assistant_entry(
                "a1",
                "Decision: use architecture billing for the parser R2-DECI-72b9f4b9",
            ),
            assistant_entry(
                "a2",
                "Decision: keep the archiver client in-process R2-DECI-9ad8613a",
            ),
            user_entry(
                "u3",
                "Drop the decision to keep the archiver client in-process; run the archiver as a sidecar service instead R2-CORR-91bb4c07.",
            ),
        ],
        None,
    );
    assert!(
        first
            .decisions
            .iter()
            .all(|item| !item.text.contains("archiver client in-process")),
        "the corrected decision must be retired: {:?}",
        first.decisions
    );

    // Next cycle: the source entries are gone, new unrelated work arrives.
    let merged = build_ledger(
        &[assistant_entry(
            "a9",
            "Decision: the auth fix lands behind a feature flag R2-DECI-4c906815",
        )],
        Some(&first),
    );
    assert!(
        merged
            .decisions
            .iter()
            .any(|item| item.text.contains("feature flag")),
        "a newly captured decision must not be deleted by a retired id collision: {:?}",
        merged.decisions
    );
    assert!(
        merged
            .decisions
            .iter()
            .any(|item| item.text.contains("architecture billing")),
        "the older surviving decision must be carried forward: {:?}",
        merged.decisions
    );
}

#[test]
fn w9_f2_and_f5_late_constraints_reusing_retired_ids_survive_with_anchors() {
    let first = build_ledger(&correction_window(), None);

    // Late-planted constraints, as in the w9-a-late3c scenario variant.
    let merged = build_ledger(
        &[
            user_entry(
                "u5",
                "CONSTRAINT: never drop the ledger ledger R2-CONS-81651ee3",
            ),
            user_entry(
                "u6",
                "CONSTRAINT: always run the reporting suite before publishing R2-CONS-f949cbec",
            ),
        ],
        Some(&first),
    );
    for expected in [
        "never drop the ledger ledger",
        "always run the reporting suite",
    ] {
        assert!(
            merged
                .constraints
                .iter()
                .any(|item| item.text.contains(expected)),
            "late constraint must survive the merge: {expected:?} missing from {:#?}",
            merged.constraints
        );
    }
    let mut ids: Vec<&str> = merged
        .constraints
        .iter()
        .map(|item| item.id.as_str())
        .collect();
    ids.sort();
    let before = ids.len();
    ids.dedup();
    assert_eq!(before, ids.len(), "constraint ids must be unique: {ids:?}");

    // W9-F5: the anchor of the late constraint must reach the summarizer.
    let rendered = render_ledger(&merged);
    assert!(rendered.contains("R2-CONS-81651ee3"), "{rendered}");
    assert!(rendered.contains("R2-CONS-f949cbec"), "{rendered}");
}

#[test]
fn a_later_correction_wins_the_same_target_and_keeps_the_earlier_correction() {
    let ledger = build_ledger(
        &[
            assistant_entry("a1", NOTIFIER_DECISION),
            user_entry(
                "u2",
                "CORRECTION: forget that; use architecture B for the notifier module instead R2-CORR-b80518c9.",
            ),
            user_entry(
                "u3",
                "CORRECTION: forget that; use architecture C for the notifier module instead R2-CORR-7d31e5aa.",
            ),
        ],
        None,
    );

    assert_eq!(ledger.superseded.len(), 1, "{:#?}", ledger.superseded);
    assert!(
        ledger.superseded[0].new_text.contains("architecture C"),
        "the newest correction must carry the requirement: {:#?}",
        ledger.superseded[0]
    );
    assert_eq!(ledger.superseded[0].old_entry, "a1");
    assert!(
        ledger
            .constraints
            .iter()
            .any(|item| item.text.contains("architecture B")),
        "the earlier correction is still an authoritative user statement: {:?}",
        ledger.constraints
    );
    assert!(
        ledger
            .constraints
            .iter()
            .any(|item| item.text.contains("architecture C")),
        "the newest correction must stay active: {:?}",
        ledger.constraints
    );
}

#[test]
fn a_retired_id_suppresses_only_the_exact_same_content() {
    let first = build_ledger(
        &[
            assistant_entry("a1", NOTIFIER_DECISION),
            assistant_entry(
                "a2",
                "Decision: keep the archiver client in-process R2-DECI-9ad8613a",
            ),
            user_entry("u3", NOTIFIER_CORRECTION),
        ],
        None,
    );
    assert!(
        first
            .decisions
            .iter()
            .all(|item| !item.text.contains("architecture A for the notifier")),
        "the corrected decision must be retired: {:?}",
        first.decisions
    );

    // The superseded decision is restated in a later window, next to new work.
    let merged = build_ledger(
        &[
            assistant_entry("a4", NOTIFIER_DECISION),
            assistant_entry(
                "a5",
                "Decision: the auth fix lands behind a feature flag R2-DECI-4c906815",
            ),
        ],
        Some(&first),
    );
    assert!(
        merged
            .decisions
            .iter()
            .all(|item| !item.text.contains("architecture A for the notifier")),
        "re-captured superseded content must stay retired: {:?}",
        merged.decisions
    );
    assert!(
        merged
            .decisions
            .iter()
            .any(|item| item.text.contains("feature flag")),
        "unrelated new decisions must be kept: {:?}",
        merged.decisions
    );
    assert!(
        merged
            .decisions
            .iter()
            .any(|item| item.text.contains("archiver client in-process")),
        "the untouched decision must be carried forward: {:?}",
        merged.decisions
    );
}

#[test]
fn ids_are_content_derived_and_stable_across_merges() {
    let first = build_ledger(
        &[user_entry(
            "u1",
            "CONSTRAINT: never drop the ledger ledger R2-CONS-81651ee3",
        )],
        None,
    );
    let id = first.constraints[0].id.clone();
    assert!(id.starts_with("C-") && id.len() == 10, "id: {id}");
    assert!(
        id[2..]
            .chars()
            .all(|ch| ch.is_ascii_hexdigit() && !ch.is_ascii_uppercase()),
        "id must be 8 lowercase hex chars: {id}"
    );

    let later = build_ledger(
        &[assistant_entry("a1", "Unrelated work continues.")],
        Some(&first),
    );
    assert_eq!(later.constraints[0].id, id);

    let decisions = build_ledger(&[assistant_entry("a1", NOTIFIER_DECISION)], None);
    assert!(
        decisions.decisions[0].id.starts_with("D-"),
        "id: {}",
        decisions.decisions[0].id
    );
}

#[test]
fn old_positional_ids_round_trip_and_merge_without_losing_new_items() {
    let old: TaskStateLedger = serde_json::from_value(json!({
        "constraints": [
            {"id": "C1", "text": "CONSTRAINT: never drop the ledger ledger R2-CONS-81651ee3", "entry": "u1"},
            {"id": "C3", "text": "CONSTRAINT: the archiver migration must be reversible R2-CONS-425a6ead", "entry": "u3"}
        ],
        "decisions": [
            {"id": "D1", "text": "Decision: use architecture billing for the parser R2-DECI-72b9f4b9", "entry": "a1"}
        ],
        "superseded": [
            {
                "oldId": "D2",
                "oldEntry": "a2",
                "newId": "C2",
                "newText": "Drop the in-process archiver client; run it as a sidecar instead",
                "newEntry": "u2"
            }
        ],
        "evidence": [
            {"id": "E1", "text": "test result: ok. 4785 passed; 0 failed", "entry": "t1", "repeats": 2, "lastEntry": "t2"}
        ],
        "anchors": ["R2-CONS-81651ee3"],
        "retired": ["D2", "C2"]
    }))
    .unwrap();
    assert_eq!(old.constraints.len(), 2);

    let merged = build_ledger(
        &[
            assistant_entry(
                "a9",
                "Decision: the auth fix lands behind a feature flag R2-DECI-4c906815",
            ),
            user_entry(
                "u9",
                "CONSTRAINT: always run the reporting suite before publishing R2-CONS-f949cbec",
            ),
        ],
        Some(&old),
    );
    assert!(merged.constraints.iter().any(|item| item.id == "C1"));
    assert!(merged.constraints.iter().any(|item| item.id == "C3"));
    assert!(
        merged
            .constraints
            .iter()
            .any(|item| item.text.contains("reporting suite")),
        "old positional retired ids must not delete newly captured content: {:#?}",
        merged.constraints
    );
    assert!(merged.decisions.iter().any(|item| item.id == "D1"));
    assert!(
        merged
            .decisions
            .iter()
            .any(|item| item.text.contains("feature flag")),
        "{:#?}",
        merged.decisions
    );

    let rendered = render_ledger(&merged);
    assert!(rendered.contains("R2-CONS-f949cbec"), "{rendered}");
    assert!(rendered.contains("R2-CONS-425a6ead"), "{rendered}");

    let value = serde_json::to_value(&merged).unwrap();
    let back: TaskStateLedger = serde_json::from_value(value).unwrap();
    assert_eq!(merged, back);
}
