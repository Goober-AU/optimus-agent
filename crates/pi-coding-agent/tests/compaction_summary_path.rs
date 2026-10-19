//! Summary-path compaction correctness (context-rot iteration 2, defect set
//! D5/D6/D9/D14).
//!
//! - D5: `prepare_compaction` must resolve to a no-op when the summarize
//!   window holds no new material, even when a previous summary exists, so a
//!   repeated threshold trigger does not pay a summary call to rewrite what
//!   the last compaction already persists.
//! - D6: an extension-supplied (`session_before_compact`) compaction summary
//!   must clear the same structural guard `validate_summary` enforces on
//!   model output before it may replace the prefix.
//! - D9: a deadline-fallback compaction entry stays a compaction boundary,
//!   but its boilerplate summary must not become the next compaction's
//!   `previous_summary`.
//! - D14: the read/write file inventory must reflect the tool calls actually
//!   present in the summarized window instead of always rendering an empty
//!   `<read-files>` block.

use pi_agent_core::types::AgentMessage;
use pi_ai::types::{Message, UserContent, UserMessage};
use pi_coding_agent::core::compaction::compaction::{
    default_compaction_settings, prepare_compaction, CompactionSessionEntry, CompactionSettings,
};
use serde_json::Value;

/// A handoff body that satisfies the conversation-format section rules.
const SUMMARY: &str = "## Goal\nShip the parser fix.\n## Constraints & Preferences\nKeep the public API.\n## Progress\nParser fixed.\n## Key Decisions\nIncremental rollout.\n## Next Steps\nPublish.\n## Critical Context\nSee src/parser.rs.";

fn compaction_entry(
    id: &str,
    summary: &str,
    first_kept_entry_id: &str,
    details: Option<Value>,
) -> CompactionSessionEntry {
    CompactionSessionEntry::Compaction {
        id: id.into(),
        parent_id: None,
        summary: summary.into(),
        first_kept_entry_id: first_kept_entry_id.into(),
        tokens_before: 2000.0,
        details,
        from_hook: None,
        custom_instructions: None,
        harness_digest: None,
        timestamp: "".into(),
    }
}

fn user_entry(id: &str, text: &str) -> CompactionSessionEntry {
    CompactionSessionEntry::Message {
        id: id.into(),
        parent_id: None,
        message: AgentMessage::Message(Message::User(UserMessage::new(
            UserContent::Text(text.into()),
            0,
        ))),
    }
}

fn settings_with(keep_recent_tokens: f64) -> CompactionSettings {
    let mut settings = default_compaction_settings();
    settings.keep_recent_tokens = keep_recent_tokens;
    settings
}

// ---------------------------------------------------------------------------
// D5: compaction can run with nothing new to summarize
// ---------------------------------------------------------------------------

/// D5 regression: after a compaction, a retained tail that already fits the
/// keep-recent budget leaves the summarize window empty. The preparation must
/// resolve to a no-op even though `previous_summary` exists, so a repeated
/// threshold trigger does not stop the turn to rewrite the old summary.
#[test]
fn repeated_trigger_with_no_new_material_resolves_to_no_op() {
    let entries = vec![
        compaction_entry("c1", SUMMARY, "kept-1", None),
        user_entry("kept-1", "A small retained turn."),
    ];
    // The tail is far below the budget, so the cut equals the boundary and
    // the window to summarize is empty.
    let preparation = prepare_compaction(&entries, &settings_with(20_000.0), &|_| 250_000.0);
    assert!(
        preparation.is_none(),
        "an empty window must skip even when a previous summary exists"
    );
}

/// D5 guard: new material beyond the boundary still prepares, with the
/// previous summary inherited as before.
#[test]
fn new_material_after_the_boundary_still_prepares() {
    let entries = vec![
        compaction_entry("c1", SUMMARY, "kept-1", None),
        user_entry("kept-1", "A small retained turn."),
        user_entry("new-1", &format!("Fresh history. {}", "detail: ".repeat(400))),
    ];
    let preparation = prepare_compaction(&entries, &settings_with(1.0), &|_| 250_000.0)
        .expect("new material beyond the boundary must still prepare");
    assert_eq!(preparation.first_kept_entry_id, "new-1");
    assert_eq!(preparation.previous_summary.as_deref(), Some(SUMMARY));
    assert!(!preparation.messages_to_summarize.is_empty());
}

/// D5 guard: a no-op skip changes no bookkeeping. The next real compaction
/// still walks back to the same boundary and inherits the same previous
/// summary and first-kept cut.
#[test]
fn a_no_op_skip_leaves_the_next_real_compaction_boundaries_intact() {
    let entries = vec![
        compaction_entry("c1", SUMMARY, "kept-1", None),
        user_entry("kept-1", "A small retained turn."),
    ];
    assert!(
        prepare_compaction(&entries, &settings_with(20_000.0), &|_| 250_000.0).is_none(),
        "the no-op skip must happen before any boundary is recorded"
    );

    let mut grown = entries.clone();
    grown.push(user_entry(
        "new-1",
        &format!("Fresh history. {}", "detail: ".repeat(400)),
    ));
    let preparation = prepare_compaction(&grown, &settings_with(1.0), &|_| 250_000.0)
        .expect("material beyond the boundary prepares after a no-op skip");
    assert_eq!(preparation.previous_summary.as_deref(), Some(SUMMARY));
    assert_eq!(preparation.first_kept_entry_id, "new-1");
    assert!(!preparation.messages_to_summarize.is_empty());
}

// ---------------------------------------------------------------------------
// D6: extension compaction bypasses summary validation
// ---------------------------------------------------------------------------

/// D6 regression: the `session_before_compact` hook path supplies a summary
/// that replaces the model's own handoff. It must clear the same structural
/// guard `validate_summary` enforces: an empty, file-inventory-only or
/// unsectioned summary must be refused so the prior context is kept instead
/// of being replaced by an empty prefix.
#[test]
fn extension_summary_guard_rejects_empty_files_only_and_unsectioned_summaries() {
    use pi_coding_agent::core::compaction::compaction::validate_extension_compaction_summary;

    for rejected in [
        "",
        "   ",
        "\n\n<read-files>\nsrc/a.rs\n</read-files>\n\n<modified-files>\nsrc/b.rs\n</modified-files>",
        "Plain prose without sections.",
        "## Goal\nOnly a goal.",
        "## Goal\nGoal.\n## Constraints & Preferences\nNone.\n## Progress\nDone.\n## Key Decisions\nWait.\n## Next Steps\nReview.\n## Critical Context\n",
        &format!("```\nUnfenced body only.\n```"),
    ] {
        let error = validate_extension_compaction_summary(rejected)
            .err()
            .unwrap_or_else(|| panic!("accepted an unusable extension summary: {rejected:?}"));
        assert!(
            error.contains("existing conversation preserved"),
            "the refusal must state the prior context is kept: {error}"
        );
        assert!(
            error.contains("Extension compaction returned an unusable handoff"),
            "{error}"
        );
    }

    // A sectioned handoff clears the guard, including fenced and
    // inventory-carrying variants.
    assert!(validate_extension_compaction_summary(SUMMARY).is_ok());
    assert!(validate_extension_compaction_summary(&format!("```markdown\n{SUMMARY}\n```")).is_ok());
    let with_files = format!("{SUMMARY}\n\n<read-files>\nsrc/a.rs\n</read-files>");
    assert!(validate_extension_compaction_summary(&with_files).is_ok());
}
