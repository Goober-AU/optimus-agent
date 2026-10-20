//! D21 regression tests: correction carriers are exempt from the render caps.
//!
//! The W24 offline re-measure (research/context_rot_r2/live/remeasure, commit
//! 2fbb14f33) found that with the W11 churn fix in place, a constraints section
//! that overflows CONSTRAINTS_CAP can cap-omit the correction carriers from the
//! rendered ACTIVE list while their stale targets stay visible as SUPERSEDED
//! replacement texts: the render's information priority is inverted exactly
//! where it matters most. The hard-corpus shape was 26 active constraints, 4
//! corrections, "14 lower-priority item(s) omitted" and a strict containment of
//! 11/20 constraint lines.
//!
//! The render now orders every capped section through `prioritize_corrections`:
//! correction carriers render first and the cap applies to the remaining slots,
//! and the dropped-count line states how many corrections were protected.

use pi_agent_core::types::AgentMessage;
use pi_ai::types::{AssistantMessage, ContentBlock, Message, TextContent, UserContent, UserMessage};
use pi_coding_agent::core::compaction::compaction::CompactionSessionEntry;
use pi_coding_agent::core::compaction::task_state::{
    CONSTRAINTS_CAP, TaskStateItem, TaskStateLedger, build_ledger, prioritize_corrections,
    render_ledger,
};

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

fn item(id: &str, text: &str, correction: bool) -> TaskStateItem {
    TaskStateItem {
        id: id.to_string(),
        text: text.to_string(),
        entry: "seed".to_string(),
        why: Vec::new(),
        correction,
    }
}

/// The `- ` lines of the rendered ACTIVE CONSTRAINTS section, in render order.
fn active_lines(rendered: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut in_section = false;
    for line in rendered.lines() {
        if line.starts_with("**ACTIVE CONSTRAINTS**") {
            in_section = true;
            continue;
        }
        if !in_section {
            continue;
        }
        if let Some(text) = line.strip_prefix("- ") {
            lines.push(text);
        } else {
            break;
        }
    }
    lines
}

#[test]
fn priority_order_puts_corrections_ahead_of_the_plain_items() {
    let items: Vec<TaskStateItem> = (0..16)
        .map(|index| {
            let correction = index % 4 == 3;
            item(&format!("C{index}"), &format!("text {index}"), correction)
        })
        .collect();
    let (rendered, omitted) = prioritize_corrections(&items, CONSTRAINTS_CAP);
    let ids: Vec<&str> = rendered.iter().map(|item| item.id.as_str()).collect();
    // The four corrections render first, in capture order, then the leading
    // plain items fill the remaining slots.
    assert_eq!(
        ids,
        vec!["C3", "C7", "C11", "C15", "C0", "C1", "C2", "C4", "C5", "C6", "C8", "C9"],
        "{ids:?}"
    );
    assert_eq!(omitted, 4);
}

#[test]
fn priority_order_without_corrections_is_a_plain_head_take() {
    let plain: Vec<TaskStateItem> = (0..16).map(|index| item(&format!("C{index}"), "text", false)).collect();
    let (rendered, omitted) = prioritize_corrections(&plain, CONSTRAINTS_CAP);
    assert_eq!(rendered.len(), CONSTRAINTS_CAP);
    assert_eq!(rendered.first().unwrap().id, "C0");
    assert_eq!(rendered.last().unwrap().id, "C11");
    assert_eq!(omitted, 4);

    let exact: Vec<TaskStateItem> = (0..CONSTRAINTS_CAP).map(|index| item(&format!("C{index}"), "text", false)).collect();
    let (rendered, omitted) = prioritize_corrections(&exact, CONSTRAINTS_CAP);
    assert_eq!(rendered.len(), CONSTRAINTS_CAP);
    assert_eq!(omitted, 0);

    let short: Vec<TaskStateItem> = (0..3).map(|index| item(&format!("C{index}"), "text", false)).collect();
    let (rendered, omitted) = prioritize_corrections(&short, CONSTRAINTS_CAP);
    assert_eq!(rendered.len(), 3);
    assert_eq!(omitted, 0);

    let (rendered, omitted) = prioritize_corrections(&[], CONSTRAINTS_CAP);
    assert!(rendered.is_empty());
    assert_eq!(omitted, 0);
}

#[test]
fn priority_order_exempts_corrections_beyond_the_cap() {
    let mut items: Vec<TaskStateItem> = (0..15)
        .map(|index| item(&format!("K{index}"), &format!("correction {index}"), true))
        .collect();
    items.extend((0..5).map(|index| item(&format!("P{index}"), &format!("plain {index}"), false)));
    let (rendered, omitted) = prioritize_corrections(&items, CONSTRAINTS_CAP);
    // All 15 corrections render even though they alone overflow the cap; the
    // plain items get the zero remaining slots.
    assert_eq!(rendered.len(), 15);
    assert!(rendered.iter().all(|item| item.correction));
    assert_eq!(omitted, 5);
}

#[test]
fn corrections_are_exempt_from_the_constraints_cap_in_the_render() {
    let mut ledger = TaskStateLedger::default();
    for index in 0..16 {
        let correction = index % 4 == 3;
        ledger.constraints.push(item(
            &format!("C{index}"),
            &format!("Requirement {index} must hold for module_{index}."),
            correction,
        ));
    }
    let rendered = render_ledger(&ledger);

    let lines = active_lines(&rendered);
    assert_eq!(lines.len(), CONSTRAINTS_CAP, "{rendered}");
    for index in (0..16).filter(|index| index % 4 == 3) {
        assert!(
            lines.iter().any(|line| line.contains(&format!("module_{index}"))),
            "correction carrier {index} must render in ACTIVE CONSTRAINTS: {rendered}"
        );
    }
    // Corrections render ahead of the non-correction items.
    let correction_positions: Vec<usize> = (0..4)
        .map(|slot| {
            lines
                .iter()
                .position(|line| line.contains(&format!("module_{}", slot * 4 + 3)))
                .expect("correction is rendered")
        })
        .collect();
    assert!(
        correction_positions.iter().all(|position| *position < 4),
        "corrections must render ahead of the plain items: {rendered}"
    );
    // The cap still applies to the remaining slots: 12 plain items minus the
    // 8 slots left after the 4 protected corrections are 4 omitted items.
    assert!(
        !lines.iter().any(|line| line.contains("module_10")),
        "the non-correction cap must stay enforced: {rendered}"
    );
    assert!(
        rendered.contains(
            "4 protected correction(s) rendered; 4 lower-priority item(s) omitted \
             from this block: 4 active constraint(s)"
        ),
        "{rendered}"
    );
}

#[test]
fn w24_hard_corpus_shape_renders_every_correction() {
    // 26 active constraints: 22 seed constraints plus 4 correction carriers,
    // with CONSTRAINTS_CAP = 12 (the W24 re-measure shape).
    let mut ledger = TaskStateLedger::default();
    for index in 0..22 {
        ledger.constraints.push(item(
            &format!("S{index}"),
            &format!("CONSTRAINT: seed rule {index} must hold for seed_{index}"),
            false,
        ));
    }
    for (index, module) in ["reconciler", "notifier", "payments", "reporting"].iter().enumerate() {
        ledger.constraints.push(item(
            &format!("X{index}"),
            &format!("CORRECTION: use architecture B for the {module} module instead"),
            true,
        ));
    }
    let rendered = render_ledger(&ledger);

    let lines = active_lines(&rendered);
    assert_eq!(lines.len(), CONSTRAINTS_CAP, "{rendered}");
    for module in ["reconciler", "notifier", "payments", "reporting"] {
        assert!(
            lines.iter().any(|line| line.contains(&format!("for the {module} module"))),
            "the {module} correction must render in ACTIVE CONSTRAINTS: {rendered}"
        );
    }
    assert_eq!(lines.iter().filter(|line| line.contains("seed rule")).count(), 8);
    assert!(
        rendered.contains(
            "4 protected correction(s) rendered; 14 lower-priority item(s) omitted \
             from this block: 14 active constraint(s)"
        ),
        "{rendered}"
    );
}

#[test]
fn a_protected_correction_keeps_its_superseded_replacement_link() {
    let entries = vec![
        user_entry(
            "u0",
            "CONSTRAINT: always run the payments suite before publishing R2-CONS-22222222",
        ),
        assistant_entry(
            "a1",
            "Decision: use architecture A for the reporting module R2-DECI-12345678",
        ),
        user_entry(
            "u2",
            "CORRECTION: forget that; use architecture B for the reporting module instead R2-CORR-abcdef01.",
        ),
        user_entry(
            "u3",
            "CONSTRAINT: keep the notifier protocol version at v2 R2-CONS-33333333",
        ),
    ];
    let ledger = build_ledger(&entries, None);
    assert_eq!(ledger.superseded.len(), 1, "{:#?}", ledger.superseded);

    let rendered = render_ledger(&ledger);
    let lines = active_lines(&rendered);
    // The correction renders ahead of the older plain constraint it did not
    // retire, and the retired decision keeps its replaced-by link.
    assert!(lines[0].contains("use architecture B for the reporting module instead"), "{rendered}");
    assert!(lines.iter().any(|line| line.contains("payments suite")), "{rendered}");
    assert!(lines.iter().any(|line| line.contains("notifier protocol")), "{rendered}");
    assert!(
        rendered.contains("replaced by") && rendered.contains("architecture B"),
        "the SUPERSEDED replacement link must stay: {rendered}"
    );
    assert!(
        !rendered.contains("**DECISIONS"),
        "the superseded decision must not render as current: {rendered}"
    );
}

#[test]
fn no_corrections_render_is_byte_equal_to_the_previous_output() {
    let mut entries = Vec::new();
    for index in 0..(CONSTRAINTS_CAP + 3) {
        entries.push(user_entry(
            &format!("cap-u{index:02}"),
            &format!("Requirement {index} must hold for module_{index}."),
        ));
    }
    let ledger = build_ledger(&entries, None);
    assert!(ledger.constraints.iter().all(|item| !item.correction));
    let rendered = render_ledger(&ledger);
    assert_eq!(rendered, EXPECTED_PLAIN_RENDER);
}

const EXPECTED_PLAIN_RENDER: &str = "\
## Protected Task State (deterministic, provenance-tagged)\n\
Derived from the transcript by fixed rules; no item is inferred or invented. Use it to keep constraints, current decisions and verified evidence exact. A SUPERSEDED item is history, not a current requirement.\n\
\n\
**ACTIVE CONSTRAINTS**\n\
- C-da0eaa4f [cap-u00]: Requirement 0 must hold for module_0.\n\
- C-83dc9b36 [cap-u01]: Requirement 1 must hold for module_1.\n\
- C-2badf599 [cap-u02]: Requirement 2 must hold for module_2.\n\
- C-3f9cfa68 [cap-u03]: Requirement 3 must hold for module_3.\n\
- C-57fdec74 [cap-u04]: Requirement 4 must hold for module_4.\n\
- C-f3fefda0 [cap-u05]: Requirement 5 must hold for module_5.\n\
- C-32d6c7bc [cap-u06]: Requirement 6 must hold for module_6.\n\
- C-2077521e [cap-u07]: Requirement 7 must hold for module_7.\n\
- C-224a06d6 [cap-u08]: Requirement 8 must hold for module_8.\n\
- C-869b7ed9 [cap-u09]: Requirement 9 must hold for module_9.\n\
- C-4e6b24de [cap-u10]: Requirement 10 must hold for module_10.\n\
- C-47e8ccde [cap-u11]: Requirement 11 must hold for module_11.\n\
\n\
_(3 lower-priority item(s) omitted from this block: 3 active constraint(s); the full ledger is persisted on the compaction entry.)_\n\
";
