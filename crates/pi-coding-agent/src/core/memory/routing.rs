//! Deterministic question-type routing for recall.
//!
//! AgentOS gates reader prompts and retrieval budgets on a six-way
//! question-type classifier (one LLM call per question; AgentOS report §3).
//! This port is deterministic-first: the classification below is pure lexical
//! pattern matching over the recall query, so it runs before any LLM helper
//! (distillation, expansion, rerank) and costs nothing. The categories mirror
//! the AgentOS reader-prompt gates that transfer to a single-model system:
//! temporal scratchpad for TR, latest-wins supersession for KU, bridge
//! collection for MS, keep/drop discipline for counting, and explicit
//! abstention wording for absence questions.
//!
//! The router is opt-in (`recallTypeRouting`, default false). When enabled,
//! [`recall_type_guidance`] appends one type-specific line after the recall
//! header and [`recall_type_entry_budget`] gates the entry cap.

/// The six recall question types, checked in this priority order:
/// counting, temporal, abstention-prone, knowledge-update, multi-session,
/// default. A query matching several categories resolves to the first hit;
/// each category's patterns are chosen so the priority order keeps the most
/// failure-mode-specific guidance.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecallQuestionType {
    /// Dates, durations, ordering: "when", "how long", "since", "before".
    TemporalReasoning,
    /// Supersession: "changed", "updated", "moved", "new", "used to".
    KnowledgeUpdate,
    /// Facts spanning several entries: "across sessions", "also", "both".
    MultiSession,
    /// Cardinality: "how many", "count", "number of".
    Counting,
    /// Asking about absence: "did I ever", "anything about", "do you remember".
    AbstentionProne,
    /// No category matched; plain lookup behavior.
    Default,
}

/// Lowercase alphanumeric word tokens; punctuation and casing are noise for
/// the markers below, and adjacency is preserved for phrase matching.
fn word_tokens(query: &str) -> Vec<(String, String)> {
    query
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|token| !token.is_empty())
        .map(|token| (token.to_lowercase(), token.to_string()))
        .collect()
}

fn has_word(tokens: &[(String, String)], word: &str) -> bool {
    tokens.iter().any(|(lower, _)| lower == word)
}

fn has_phrase(tokens: &[(String, String)], phrase: &[&str]) -> bool {
    let lowers: Vec<&str> = tokens.iter().map(|(lower, _)| lower.as_str()).collect();
    if phrase.is_empty() || lowers.len() < phrase.len() {
        return false;
    }
    lowers.windows(phrase.len()).any(|window| window == phrase)
}

/// `new` is a knowledge-update marker only when it is an adjective, not the
/// start of a proper noun ("new address" vs "New York"): require the next
/// token to be lowercase in the original query.
fn has_new_value(tokens: &[(String, String)]) -> bool {
    tokens.windows(2).any(|window| {
        window[0].0 == "new" && window[1].1.chars().next().is_none_or(char::is_lowercase)
    })
}

/// Bare four-digit years (1999, 2026) are strong temporal anchors.
fn has_year(tokens: &[(String, String)]) -> bool {
    tokens.iter().any(|(lower, _)| {
        lower.len() == 4
            && lower.starts_with(['1', '2'])
            && lower.chars().all(|c| c.is_ascii_digit())
            && lower.parse::<i64>().is_ok_and(|year| (1900..=2099).contains(&year))
    })
}

const COUNTING_WORDS: &[&str] = &["count", "counts", "counted", "counting", "tally", "tallies"];
const COUNTING_PHRASES: &[&[&str]] = &[
    &["how", "many"],
    &["count", "of"],
    &["number", "of"],
    &["numbers", "of"],
    &["total", "number"],
];

const TEMPORAL_WORDS: &[&str] = &[
    "when", "before", "after", "since", "until", "till", "ago", "yesterday", "tomorrow", "earlier",
    "later", "recently", "anniversary", "birthday", "date", "dates", "day", "days", "week",
    "weeks", "month", "months", "year", "years", "hour", "hours", "minute", "minutes",
    "january", "february", "april", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
];
const TEMPORAL_PHRASES: &[&[&str]] = &[
    &["how", "long"],
    &["how", "often"],
    &["how", "far", "apart"],
    &["what", "time"],
    &["what", "date"],
    &["which", "date"],
    &["what", "day"],
    &["which", "day"],
    &["what", "month"],
    &["which", "month"],
    &["what", "year"],
    &["which", "year"],
    &["time", "span"],
    &["time", "period"],
    &["date", "range"],
    &["last", "week"],
    &["last", "month"],
    &["last", "year"],
    &["next", "week"],
    &["next", "month"],
    &["next", "year"],
    &["this", "week"],
    &["this", "month"],
    &["this", "year"],
];

const ABSTENTION_WORDS: &[&str] = &["anything"];
const ABSTENTION_PHRASES: &[&[&str]] = &[
    &["did", "i", "ever"],
    &["have", "i", "ever"],
    &["did", "we", "ever"],
    &["have", "we", "ever"],
    &["did", "you", "ever"],
    &["have", "you", "ever"],
    &["do", "you", "remember"],
    &["do", "you", "recall"],
    &["can", "you", "remember"],
    &["can", "you", "recall"],
    &["did", "i", "mention"],
    &["have", "i", "mentioned"],
    &["did", "i", "tell"],
    &["have", "i", "told"],
    &["did", "i", "say"],
    &["have", "i", "said"],
    &["did", "we", "discuss"],
    &["have", "we", "discussed"],
    &["did", "we", "talk"],
    &["have", "we", "talked"],
    &["did", "we", "mention"],
    &["is", "there", "anything"],
    &["anything", "about"],
    &["any", "mention"],
    &["any", "idea"],
    &["any", "memory"],
    &["ever", "mention"],
    &["ever", "mentioned"],
    &["ever", "told"],
    &["ever", "discussed"],
];

const KNOWLEDGE_UPDATE_WORDS: &[&str] = &[
    "changed", "change", "changes", "update", "updates", "updated", "moved", "moving", "relocated",
    "relocation", "switched", "switching", "replaced", "replacing", "latest", "newest", "current",
    "currently", "now", "originally", "previously", "anymore",
];
const KNOWLEDGE_UPDATE_PHRASES: &[&[&str]] = &[
    &["used", "to"],
    &["no", "longer"],
    &["not", "anymore"],
    &["these", "days"],
    &["at", "the", "moment"],
    &["right", "now"],
    &["at", "present"],
];

const MULTI_SESSION_WORDS: &[&str] = &[
    "across", "both", "also", "together", "altogether", "sessions",
];
const MULTI_SESSION_PHRASES: &[&[&str]] = &[
    &["across", "sessions"],
    &["both", "sessions"],
    &["both", "conversations"],
    &["all", "sessions"],
    &["every", "session"],
    &["each", "session"],
    &["another", "session"],
    &["another", "conversation"],
    &["other", "sessions"],
    &["earlier", "conversation"],
    &["earlier", "sessions"],
    &["previous", "session"],
    &["previous", "sessions"],
    &["previous", "conversation"],
    &["previous", "conversations"],
    &["first", "session"],
    &["second", "session"],
    &["last", "session"],
    &["first", "meeting"],
    &["last", "meeting"],
    &["next", "meeting"],
    &["also", "mentioned"],
    &["also", "told"],
    &["also", "said"],
    &["also", "noted"],
    &["and", "also"],
    &["in", "total"],
];

/// Classify a recall query into one of the six types with pure pattern
/// matching. Deterministic and LLM-free: the same query always yields the
/// same type, including for the empty query (Default).
pub fn classify_recall_question(query: &str) -> RecallQuestionType {
    let tokens = word_tokens(query);
    if tokens.is_empty() {
        return RecallQuestionType::Default;
    }
    if COUNTING_PHRASES.iter().any(|phrase| has_phrase(&tokens, phrase))
        || COUNTING_WORDS.iter().any(|word| has_word(&tokens, word))
    {
        return RecallQuestionType::Counting;
    }
    if TEMPORAL_PHRASES.iter().any(|phrase| has_phrase(&tokens, phrase))
        || TEMPORAL_WORDS.iter().any(|word| has_word(&tokens, word))
        || has_year(&tokens)
    {
        return RecallQuestionType::TemporalReasoning;
    }
    if ABSTENTION_PHRASES.iter().any(|phrase| has_phrase(&tokens, phrase))
        || ABSTENTION_WORDS.iter().any(|word| has_word(&tokens, word))
    {
        return RecallQuestionType::AbstentionProne;
    }
    if KNOWLEDGE_UPDATE_PHRASES.iter().any(|phrase| has_phrase(&tokens, phrase))
        || KNOWLEDGE_UPDATE_WORDS.iter().any(|word| has_word(&tokens, word))
        || has_new_value(&tokens)
    {
        return RecallQuestionType::KnowledgeUpdate;
    }
    if MULTI_SESSION_PHRASES.iter().any(|phrase| has_phrase(&tokens, phrase))
        || MULTI_SESSION_WORDS.iter().any(|word| has_word(&tokens, word))
    {
        return RecallQuestionType::MultiSession;
    }
    RecallQuestionType::Default
}

impl RecallQuestionType {
    /// Stable identifier used in diagnostics and tests.
    pub fn as_str(&self) -> &'static str {
        match self {
            RecallQuestionType::TemporalReasoning => "temporal",
            RecallQuestionType::KnowledgeUpdate => "knowledge-update",
            RecallQuestionType::MultiSession => "multi-session",
            RecallQuestionType::Counting => "counting",
            RecallQuestionType::AbstentionProne => "abstention-prone",
            RecallQuestionType::Default => "default",
        }
    }
}

/// One-line recall-header guidance per question type, appended after the
/// base header when routing is enabled. `None` for the default type keeps
/// the render unchanged.
pub fn recall_type_guidance(kind: RecallQuestionType) -> Option<&'static str> {
    match kind {
        RecallQuestionType::TemporalReasoning => Some(
            "Temporal question: list the dates seen per fact before answering; latest wins.",
        ),
        RecallQuestionType::KnowledgeUpdate => Some(
            "Update question: when entries disagree, answer with the newest entry's value.",
        ),
        RecallQuestionType::MultiSession => Some(
            "Multi-session question: gather matching facts from every entry before answering.",
        ),
        RecallQuestionType::Counting => Some(
            "Counting question: keep/drop check every candidate entry before the final count.",
        ),
        RecallQuestionType::AbstentionProne => Some(
            "Absence question: if no entry mentions the topic, say you don't know instead of guessing.",
        ),
        RecallQuestionType::Default => None,
    }
}

/// Entry budget for a question type. Temporal questions may recall up to two
/// extra entries (older versions of the same fact must be visible for the
/// latest-wins check), bounded so the budget never exceeds 1.5x the cap.
/// Every other type, including abstention-prone, keeps the default budget.
pub fn recall_type_entry_budget(kind: RecallQuestionType, max_entries: i64) -> i64 {
    match kind {
        RecallQuestionType::TemporalReasoning => {
            let bounded = max_entries + max_entries.div_euclid(2);
            (max_entries + 2).min(bounded).max(max_entries)
        }
        _ => max_entries,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kind(query: &str) -> &'static str {
        classify_recall_question(query).as_str()
    }

    #[test]
    fn classifies_temporal_queries() {
        assert_eq!(kind("When did I move to Canada?"), "temporal");
        assert_eq!(kind("How long did I work at Acme?"), "temporal");
        assert_eq!(kind("What was my address before I updated the lease?"), "temporal");
        assert_eq!(kind("Have we spoken since March?"), "temporal");
        assert_eq!(kind("What happened after the 2024 audit?"), "temporal");
    }

    #[test]
    fn classifies_knowledge_update_queries() {
        assert_eq!(kind("Did my phone number change?"), "knowledge-update");
        assert_eq!(kind("Where do I live now?"), "knowledge-update");
        assert_eq!(kind("I used to run every morning; what is my current routine?"), "knowledge-update");
        assert_eq!(kind("What is my new address?"), "knowledge-update");
    }

    #[test]
    fn proper_noun_new_is_not_a_knowledge_update() {
        assert_eq!(kind("What do you know about New York?"), "default");
        assert_eq!(kind("Tell me about New Zealand trips"), "default");
    }

    #[test]
    fn classifies_multi_session_queries() {
        assert_eq!(kind("Combine my notes about the launch across sessions."), "multi-session");
        assert_eq!(kind("What did I say in the first meeting and also the second?"), "multi-session");
        assert_eq!(kind("Summarize both conversations about the budget."), "multi-session");
    }

    #[test]
    fn classifies_counting_queries() {
        assert_eq!(kind("How many jobs have I had?"), "counting");
        assert_eq!(kind("What is the number of projects I started?"), "counting");
        assert_eq!(kind("Count the cities I mentioned."), "counting");
    }

    #[test]
    fn classifies_abstention_prone_queries() {
        assert_eq!(kind("Did I ever mention a dog?"), "abstention-prone");
        assert_eq!(kind("Is there anything about my diet?"), "abstention-prone");
        assert_eq!(kind("Do you remember my cousin's name?"), "abstention-prone");
        assert_eq!(kind("Have you ever told me about the backup plan?"), "abstention-prone");
    }

    #[test]
    fn classifies_default_queries() {
        assert_eq!(kind("What is my favorite coffee?"), "default");
        assert_eq!(kind("Tell me about the Sydney trip."), "default");
        assert_eq!(kind(""), "default");
        assert_eq!(kind("   "), "default");
    }

    #[test]
    fn counting_beats_other_categories() {
        assert_eq!(kind("How many times did I change my phone number?"), "counting");
        assert_eq!(kind("How many sessions did we have before July?"), "counting");
    }

    #[test]
    fn temporal_beats_knowledge_update_and_abstention() {
        assert_eq!(kind("When did I change my address?"), "temporal");
        assert_eq!(kind("Do you remember when we met?"), "temporal");
    }

    #[test]
    fn abstention_beats_knowledge_update() {
        assert_eq!(kind("Did I ever change my plan?"), "abstention-prone");
        assert_eq!(kind("Did I mention my updated laptop?"), "abstention-prone");
    }

    #[test]
    fn marker_words_do_not_match_inside_larger_words() {
        // "whenever" is not "when", "afternoon" is not "after".
        assert_eq!(kind("Whenever you have time, review the checklist."), "default");
        assert_eq!(kind("The afternoon standup notes."), "default");
        assert_eq!(kind("I sing in a chorus about tilling soil."), "default");
    }

    #[test]
    fn classification_is_deterministic_and_case_insensitive() {
        assert_eq!(
            classify_recall_question("WHEN did I move?"),
            classify_recall_question("when did i move?")
        );
        assert_eq!(kind("HOW MANY books?"), "counting");
    }

    #[test]
    fn guidance_covers_every_type_except_default() {
        for value in [
            RecallQuestionType::TemporalReasoning,
            RecallQuestionType::KnowledgeUpdate,
            RecallQuestionType::MultiSession,
            RecallQuestionType::Counting,
            RecallQuestionType::AbstentionProne,
        ] {
            let guidance = recall_type_guidance(value).expect(value.as_str());
            assert!(!guidance.contains('\n'), "one line only: {guidance}");
        }
        assert_eq!(recall_type_guidance(RecallQuestionType::Default), None);
    }

    #[test]
    fn temporal_budget_adds_two_entries_bounded_to_one_and_a_half() {
        assert_eq!(recall_type_entry_budget(RecallQuestionType::TemporalReasoning, 6), 8);
        assert_eq!(recall_type_entry_budget(RecallQuestionType::TemporalReasoning, 3), 4);
        assert_eq!(recall_type_entry_budget(RecallQuestionType::TemporalReasoning, 2), 3);
        assert_eq!(recall_type_entry_budget(RecallQuestionType::TemporalReasoning, 1), 1);
        assert_eq!(recall_type_entry_budget(RecallQuestionType::TemporalReasoning, 0), 0);
    }

    #[test]
    fn non_temporal_types_keep_the_default_budget() {
        for value in [
            RecallQuestionType::KnowledgeUpdate,
            RecallQuestionType::MultiSession,
            RecallQuestionType::Counting,
            RecallQuestionType::AbstentionProne,
            RecallQuestionType::Default,
        ] {
            assert_eq!(recall_type_entry_budget(value, 6), 6);
            assert_eq!(recall_type_entry_budget(value, 0), 0);
        }
    }
}
