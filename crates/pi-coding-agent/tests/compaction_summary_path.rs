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
use serde_json::{json, Value};

/// A handoff body that satisfies the conversation-format section rules.
const SUMMARY: &str = "## Goal\nShip the parser fix.\n## Constraints & Preferences\nKeep the public API.\n## Progress\nParser fixed.\n## Key Decisions\nIncremental rollout.\n## Next Steps\nPublish.\n## Critical Context\nSee src/parser.rs.";

/// The A7 deadline-fallback marker text `deadline_truncation_result` writes.
const FALLBACK_SUMMARY: &str = "## Goal\nCompaction deadline fallback: truncated to the retained tail.\n## Constraints & Preferences\n- See the retained recent tail.\n## Progress\n### Done\n- [x] History dropped after the deadline.\n\n### In Progress\n- [ ] Continue from the tail.\n\n### Blocked\n- Summary generation exceeded the deadline.\n\n## Key Decisions\n- **Keep-recent-tail truncation**: applied.\n## Next Steps\n1. Continue the work.\n## Critical Context\n- The transcript keeps the full record.";

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

// ---------------------------------------------------------------------------
// D9: deadline-fallback boilerplate becomes the next previous_summary
// ---------------------------------------------------------------------------

/// D9 regression: a deadline-fallback compaction is still a boundary (its
/// firstKeptEntryId defines the retained tail, its cumulative file inventory
/// is inherited), but its boilerplate summary must not be fed back as the
/// next compaction's previous_summary. The walk for previous prose must skip
/// the fallback entry and continue to the last real summary.
#[test]
fn deadline_fallback_boilerplate_is_not_the_next_previous_summary() {
    let entries = vec![
        compaction_entry("c0", SUMMARY, "kept-2", None),
        user_entry(
            "kept-2",
            &format!("Real summarized history. {}", "fact: ".repeat(300)),
        ),
        compaction_entry(
            "c1",
            FALLBACK_SUMMARY,
            "kept-3",
            Some(json!({
                "deadlineFallback": true,
                "readFiles": ["fallback-read.txt"],
                "modifiedFiles": ["fallback-modified.txt"]
            })),
        ),
        user_entry("kept-3", "A small retained turn."),
        user_entry("new-1", "Another small turn."),
    ];
    let preparation = prepare_compaction(&entries, &settings_with(1.0), &|_| 250_000.0)
        .expect("new material after the fallback boundary prepares");
    assert_eq!(
        preparation.previous_summary.as_deref(),
        Some(SUMMARY),
        "the fallback boilerplate must not be fed back as previous prose"
    );
    // The fallback entry still owns the boundary: the cut lands inside the
    // material after its firstKeptEntryId, not after the older real summary.
    assert_eq!(preparation.first_kept_entry_id, "new-1");
    assert!(!preparation.messages_to_summarize.is_empty());
    // Its cumulative file inventory is still inherited.
    use pi_coding_agent::core::compaction::utils::compute_file_lists;
    let (read_files, modified_files) = compute_file_lists(&preparation.file_ops);
    assert!(read_files.contains(&"fallback-read.txt".to_string()));
    assert!(modified_files.contains(&"fallback-modified.txt".to_string()));
}

/// D9 regression: when only a deadline-fallback compaction exists, the next
/// compaction has no real previous summary and must run as a first
/// compaction instead of treating the boilerplate as authoritative prose.
#[test]
fn fallback_only_history_yields_no_previous_summary() {
    let entries = vec![
        compaction_entry(
            "c0",
            FALLBACK_SUMMARY,
            "kept-1",
            Some(json!({"deadlineFallback": true})),
        ),
        user_entry("kept-1", "A small retained turn."),
        user_entry("new-1", "Another small turn."),
    ];
    let preparation = prepare_compaction(&entries, &settings_with(1.0), &|_| 250_000.0)
        .expect("new material after the fallback boundary prepares");
    assert!(
        preparation.previous_summary.is_none(),
        "no real summary exists before the fallback, so previous prose stays empty"
    );
    assert_eq!(preparation.first_kept_entry_id, "new-1");
    assert!(!preparation.messages_to_summarize.is_empty());
}
