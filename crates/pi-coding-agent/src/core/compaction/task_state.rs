//! Deterministic protected task-state ledger.
//!
//! Compaction replaces a window of history with a model-written summary. A summary can
//! drop an enduring user constraint, promote a superseded decision, or lose an exact
//! anchor (path, identifier, error code, test count). This module derives a bounded,
//! provenance-tagged ledger from the session records themselves with fixed rules: no
//! model call, no inference beyond pattern matching, and every item copied verbatim from
//! the entry it came from.
//!
//! The ledger is carried forward across compactions (merged, not re-derived from a
//! summary) so repeated cycles cannot erode it. It is fed to the summarizer as protected
//! material and persisted on the compaction entry under `details.taskState`.

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};

use super::compaction::CompactionSessionEntry;
use pi_agent_core::types::AgentMessage;

/// Per-section caps. A section that overflows states the count and the newest source
/// entry, so an omission is visible instead of silent.
pub const CONSTRAINTS_CAP: usize = 12;
pub const DECISIONS_CAP: usize = 10;
pub const SUPERSEDED_CAP: usize = 8;
pub const EVIDENCE_CAP: usize = 8;
pub const ANCHORS_CAP: usize = 12;
pub const OPEN_WORK_CAP: usize = 6;
pub const RETRACTIONS_CAP: usize = 4;
/// A single copied item never exceeds this many characters.
pub const ITEM_TEXT_MAX: usize = 320;
/// Upper bound on the rendered ledger. The renderer drops whole trailing items from the
/// lowest-priority sections until it fits, and states what it dropped.
pub const RENDER_MAX_CHARS: usize = 6_000;

static CONSTRAINT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(must|must not|never|do not|don't|only|always|keep|frozen|unchanged|offline|not exceed|no emojis|should be)\b").unwrap()
});
static DECISION_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\bdecision\b|\bchosen\b|\bchose\b|\bwe will use\b").unwrap()
});
static SUPERSEDE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(drop|instead|change of plan|no longer|rather than|replace[sd]?|revert|withdraw|supersed\w*)\b").unwrap()
});
static EVIDENCE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"test result:|error\[E\d+\]|\d+ files changed|panicked at|FAILED").unwrap()
});
static ANCHOR_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"`([^`]{2,60})`",
        r"|\b([A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+)\b",
        r"|\b([A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b",
        r"|\b([A-Za-z0-9_.\-/]+\.(?:rs|toml|json|jsonl|md|py|sh))\b",
        r"|\b(E\d{4})\b",
        r"|\b(\d{4,})\b",
    )).unwrap()
});
static CLAUSE_SPLIT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s\(\d\)\s").unwrap());
static OPEN_WORK_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\b(remaining|left to do|next step|todo)\b").unwrap());
static RATIONALE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)^rationale\b").unwrap());
static RETRACTION_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)^(superseded|withdrawn|reverted)\b").unwrap());
static LOG_LEVEL_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^(INFO|DEBUG|WARN|ERROR|TRACE)$").unwrap());
static TOKEN_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[A-Za-z_][A-Za-z0-9_]{2,}").unwrap());
static DIGITS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\d+").unwrap());

/// One copied statement with provenance.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct TaskStateItem {
    /// Stable within the ledger, e.g. `C3`.
    pub id: String,
    /// Verbatim (clipped) statement.
    pub text: String,
    /// Session entry id the statement came from. `"summary"` for carried summary text.
    pub entry: String,
    /// Reason attached by the user in the same message (`Rationale: ...`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub why: Vec<String>,
}

/// An earlier item replaced by a later user correction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct Supersession {
    /// Id of the retired item.
    pub old_id: String,
    pub old_entry: String,
    /// Id of the item that now carries the requirement.
    pub new_id: String,
    /// Verbatim correction text.
    pub new_text: String,
    pub new_entry: String,
}

/// A tool result that stated a checkable outcome.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct EvidenceItem {
    pub id: String,
    pub text: String,
    pub entry: String,
    #[serde(default, skip_serializing_if = "is_one")]
    pub repeats: u32,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub last_entry: String,
}

fn is_one(value: &u32) -> bool {
    *value <= 1
}

/// The ledger.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct TaskStateLedger {
    #[serde(default)]
    pub constraints: Vec<TaskStateItem>,
    #[serde(default)]
    pub decisions: Vec<TaskStateItem>,
    #[serde(default)]
    pub superseded: Vec<Supersession>,
    #[serde(default)]
    pub evidence: Vec<EvidenceItem>,
    #[serde(default)]
    pub anchors: Vec<String>,
    #[serde(default)]
    pub open_work: Vec<TaskStateItem>,
    #[serde(default)]
    pub retractions: Vec<TaskStateItem>,
    /// Items retired by a supersession but still active in the source window.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub retired: Vec<String>,
}

impl TaskStateLedger {
    pub fn is_empty(&self) -> bool {
        self.constraints.is_empty()
            && self.decisions.is_empty()
            && self.superseded.is_empty()
            && self.evidence.is_empty()
            && self.open_work.is_empty()
    }
}

/// `(role, text, is_error)` for the entry shapes the ledger understands.
fn entry_text(entry: &CompactionSessionEntry) -> Option<(Role, String, bool)> {
    let CompactionSessionEntry::Message { message, .. } = entry else {
        return None;
    };
    let AgentMessage::Message(message) = message else {
        return None;
    };
    match message {
        pi_ai::types::Message::User(user) => Some((Role::User, user.content.text(), false)),
        pi_ai::types::Message::Assistant(assistant) => {
            let text = assistant
                .content
                .iter()
                .filter_map(|block| match block {
                    pi_ai::types::ContentBlock::Text(text) => Some(text.text.clone()),
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join("\n");
            if text.trim().is_empty() {
                None
            } else {
                Some((Role::Assistant, text, false))
            }
        }
        pi_ai::types::Message::ToolResult(result) => {
            let text = result
                .content
                .iter()
                .filter_map(|block| match block {
                    pi_ai::types::ImageOrTextContent::Text(text) => Some(text.text.clone()),
                    pi_ai::types::ImageOrTextContent::Image(_) => None,
                })
                .collect::<Vec<_>>()
                .join("");
            Some((Role::Tool, text, result.is_error))
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum Role {
    User,
    Assistant,
    Tool,
}

/// Split into sentences without lookaround: a sentence ends after `.`, `!` or `?`
/// followed by whitespace, and every newline also ends one.
fn sentences(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '\n' {
            out.push(std::mem::take(&mut current));
            continue;
        }
        current.push(ch);
        if matches!(ch, '.' | '!' | '?') && chars.peek().is_some_and(|next| next.is_whitespace()) {
            out.push(std::mem::take(&mut current));
        }
    }
    out.push(current);
    // A run of whitespace leaves empty parts, and `mem::take` on the final
    // iteration leaves one more. Trim and drop them.
    out.into_iter()
        .map(|part| part.trim().to_string())
        .filter(|part| !part.is_empty())
        .collect()
}

fn tokens(text: &str) -> std::collections::BTreeSet<String> {
    TOKEN_RE
        .find_iter(text)
        .map(|m| m.as_str().to_ascii_lowercase())
        .filter(|token| !STOPWORDS.contains(&token.as_str()))
        .collect()
}

const STOPWORDS: &[&str] = &[
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "is", "are", "was", "were", "be", "been", "to", "of", "in", "on", "for", "with", "without",
    "by", "from", "as", "at", "it", "its", "their", "our", "your", "we", "you", "i", "they", "he",
    "she",
];

fn clip(text: &str, limit: usize) -> String {
    let squeezed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if squeezed.chars().count() <= limit {
        return squeezed;
    }
    let mut out: String = squeezed.chars().take(limit.saturating_sub(1)).collect();
    out.push('\u{2026}');
    out
}

fn dedupe_key(text: &str) -> String {
    tokens(text).into_iter().collect::<Vec<_>>().join(" ")
}

fn short_id(entry_id: &str) -> String {
    if entry_id.is_empty() {
        "summary".to_string()
    } else {
        entry_id.chars().take(8).collect()
    }
}

fn split_clauses(sentence: &str) -> Vec<String> {
    if !CLAUSE_SPLIT_RE.is_match(&format!(" {sentence} ")) {
        return vec![sentence.to_string()];
    }
    let prefixed = format!(" {sentence}");
    let parts: Vec<&str> = CLAUSE_SPLIT_RE.split(&prefixed).collect();
    let mut out: Vec<String> = Vec::new();
    if let Some(head) = parts.first() {
        let head = head.trim();
        if head.chars().count() > 8 {
            out.push(head.to_string());
        }
    }
    for part in parts.iter().skip(1) {
        let part = part.trim();
        if !part.is_empty() {
            out.push(part.to_string());
        }
    }
    out
}

/// Extract a ledger from the entries that are about to leave the model's window.
///
/// `previous` is the ledger persisted by the last compaction. It is merged, not
/// re-derived, so anything older than the window survives every later cycle.
pub fn build_ledger(
    entries: &[CompactionSessionEntry],
    previous: Option<&TaskStateLedger>,
) -> TaskStateLedger {
    let mut ledger = previous.cloned().unwrap_or_default();
    let mut seen: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    for item in ledger
        .constraints
        .iter()
        .chain(ledger.decisions.iter())
        .chain(ledger.open_work.iter())
    {
        seen.insert(dedupe_key(&item.text));
    }

    let mut corrections: Vec<TaskStateItem> = Vec::new();
    let mut evidence_seen: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for (index, item) in ledger.evidence.iter().enumerate() {
        evidence_seen.insert(evidence_key(&item.text), index);
    }
    let mut anchor_counts: std::collections::BTreeMap<String, std::collections::BTreeSet<String>> =
        std::collections::BTreeMap::new();
    let mut anchor_first: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();

    for (position, entry) in entries.iter().enumerate() {
        let Some((role, text, _is_error)) = entry_text(entry) else {
            continue;
        };
        let entry_id = entry.id().to_string();

        if role == Role::User {
            let is_correction = SUPERSEDE_RE.is_match(&text);
            for sentence in sentences(&text) {
                for clause in split_clauses(&sentence) {
                    if RATIONALE_RE.is_match(&clause) {
                        let target = corrections
                            .last_mut()
                            .or_else(|| ledger.constraints.last_mut());
                        if let Some(target) = target {
                            let why = clause
                                .split_once(':')
                                .map(|(_, rest)| rest.trim())
                                .unwrap_or(clause.trim());
                            target.why.push(clip(why, 200));
                        }
                        continue;
                    }
                    if !(is_correction || CONSTRAINT_RE.is_match(&clause)) {
                        continue;
                    }
                    if clause.chars().count() < 12 {
                        continue;
                    }
                    let key = dedupe_key(&clause);
                    if key.is_empty() || !seen.insert(key) {
                        continue;
                    }
                    let id = format!("C{}", ledger.constraints.len() + 1);
                    let item = TaskStateItem {
                        id,
                        text: clip(&clause, ITEM_TEXT_MAX),
                        entry: short_id(&entry_id),
                        why: Vec::new(),
                    };
                    if is_correction {
                        corrections.push(item.clone());
                    }
                    ledger.constraints.push(item);
                }
            }
        }

        if matches!(role, Role::User | Role::Assistant) {
            for sentence in sentences(&text) {
                if role == Role::Assistant && RETRACTION_RE.is_match(&sentence) {
                    ledger.retractions.push(TaskStateItem {
                        id: format!("R{}", ledger.retractions.len() + 1),
                        text: clip(&sentence, 240),
                        entry: short_id(&entry_id),
                        why: Vec::new(),
                    });
                    continue;
                }
                if !DECISION_RE.is_match(&sentence) {
                    continue;
                }
                let key = dedupe_key(&sentence);
                if key.is_empty() || !seen.insert(key) {
                    continue;
                }
                let id = format!("D{}", ledger.decisions.len() + 1);
                ledger.decisions.push(TaskStateItem {
                    id,
                    text: clip(&sentence, ITEM_TEXT_MAX),
                    entry: short_id(&entry_id),
                    why: Vec::new(),
                });
            }
            for capture in ANCHOR_RE.captures_iter(&text) {
                let Some(token) = capture.iter().skip(1).flatten().next().map(|m| m.as_str().to_string())
                else {
                    continue;
                };
                if LOG_LEVEL_RE.is_match(&token) {
                    continue;
                }
                anchor_counts
                    .entry(token.clone())
                    .or_default()
                    .insert(entry_id.clone());
                anchor_first.entry(token).or_insert(position);
            }
        }

        if role == Role::Tool && EVIDENCE_RE.is_match(&text) {
            let line = text
                .lines()
                .find(|line| EVIDENCE_RE.is_match(line))
                .unwrap_or(&text);
            let key = evidence_key(line);
            if let Some(index) = evidence_seen.get(&key).copied() {
                if let Some(existing) = ledger.evidence.get_mut(index) {
                    existing.repeats += 1;
                    existing.last_entry = short_id(&entry_id);
                }
            } else {
                let item = EvidenceItem {
                    id: format!("E{}", ledger.evidence.len() + 1),
                    text: clip(line, 200),
                    entry: short_id(&entry_id),
                    repeats: 1,
                    last_entry: short_id(&entry_id),
                };
                evidence_seen.insert(key, ledger.evidence.len());
                ledger.evidence.push(item);
            }
        }
    }

    // Supersession: a later user correction retires the best earlier item that shares a
    // specific anchor (identifier, path, digit-bearing token). Shared generic words alone
    // never retire a constraint.
    for correction in &corrections {
        let correction_tokens = tokens(&correction.text);
        let mut best: Option<((bool, usize), i32)> = None;
        let candidates = ledger
            .decisions
            .iter()
            .enumerate()
            .map(|(index, item)| (true, index, item))
            .chain(
                ledger
                    .constraints
                    .iter()
                    .enumerate()
                    .map(|(index, item)| (false, index, item)),
            );
        for (is_decision, index, item) in candidates {
            if item.id == correction.id {
                continue;
            }
            let item_tokens = tokens(&item.text);
            let shared: Vec<&String> = correction_tokens.intersection(&item_tokens).collect();
            let specific = shared
                .iter()
                .filter(|token| {
                    token.contains('_')
                        || token.contains('/')
                        || token.chars().any(|c| c.is_ascii_digit())
                        || token.chars().count() > 8
                })
                .count();
            let score = (specific as i32) * 3 + shared.len() as i32;
            if score > best.map(|(_, current)| current).unwrap_or(0) {
                best = Some(((is_decision, index), score));
            }
        }
        let Some(((is_decision, index), score)) = best else {
            continue;
        };
        if score < 3 {
            continue;
        }
        let old = if is_decision {
            &ledger.decisions[index]
        } else {
            &ledger.constraints[index]
        };
        if ledger.superseded.iter().any(|existing| existing.old_id == old.id) {
            continue;
        }
        ledger.superseded.push(Supersession {
            old_id: old.id.clone(),
            old_entry: old.entry.clone(),
            new_id: correction.id.clone(),
            new_text: correction.text.clone(),
            new_entry: correction.entry.clone(),
        });
    }

    let retired: std::collections::BTreeSet<String> =
        ledger.superseded.iter().map(|item| item.old_id.clone()).collect();
    for id in &retired {
        if !ledger.retired.contains(id) {
            ledger.retired.push(id.clone());
        }
    }
    ledger.constraints.retain(|item| !retired.contains(&item.id));
    ledger.decisions.retain(|item| !retired.contains(&item.id));

    // Open work: the newest first-person statement of what remains.
    for entry in entries.iter().rev() {
        let Some((Role::Assistant, text, _)) = entry_text(entry) else {
            continue;
        };
        if !OPEN_WORK_RE.is_match(&text) {
            continue;
        }
        let candidate = TaskStateItem {
            id: "O1".to_string(),
            text: clip(&text, 240),
            entry: short_id(entry.id()),
            why: Vec::new(),
        };
        let key = dedupe_key(&candidate.text);
        if !seen.contains(&key) {
            ledger.open_work.retain(|item| item.id != "O1");
            seen.insert(key);
        }
        ledger.open_work.retain(|item| item.id != "O1");
        ledger.open_work.insert(0, candidate);
        break;
    }

    // Anchors: identifiers that recur, weighted to exact, specific shapes.
    let mut ranked: Vec<(String, usize, usize)> = anchor_counts
        .iter()
        .filter(|(_, entries)| entries.len() >= 2)
        .map(|(token, entries)| {
            (
                token.clone(),
                entries.len(),
                anchor_first.get(token).copied().unwrap_or(usize::MAX),
            )
        })
        .collect();
    ranked.sort_by(|a, b| {
        b.1.cmp(&a.1)
            .then_with(|| anchor_weight(&b.0).cmp(&anchor_weight(&a.0)))
            .then_with(|| a.2.cmp(&b.2))
    });
    for (token, _, _) in ranked {
        if ledger.anchors.contains(&token) {
            continue;
        }
        if ledger.anchors.len() >= ANCHORS_CAP {
            break;
        }
        ledger.anchors.push(token);
    }

    ledger
}

fn anchor_weight(token: &str) -> i32 {
    let mut weight = 0;
    if token.contains('_') || token.contains('/') || token.contains('.') {
        weight += 3;
    }
    if token.chars().any(|c| c.is_ascii_digit()) {
        weight += 2;
    }
    if token.chars().count() >= 8 {
        weight += 1;
    }
    weight
}

fn evidence_key(line: &str) -> String {
    let squeezed = line.split_whitespace().collect::<Vec<_>>().join(" ");
    let replaced = DIGITS_RE.replace_all(&squeezed, "#");
    replaced.chars().take(160).collect()
}

/// Extract a named section body from a structured summary (`## Next Steps`).
pub fn summary_section<'a>(summary: &'a str, name: &str) -> Option<&'a str> {
    let mut start: Option<usize> = None;
    let mut offset = 0usize;
    for line in summary.split_inclusive('\n') {
        let trimmed = line.trim_start();
        let is_heading = trimmed.starts_with('#');
        if is_heading {
            if let Some(begin) = start {
                let body = summary[begin..offset].trim();
                return (!body.is_empty()).then_some(body);
            }
            let heading = trimmed.trim_start_matches('#').trim().trim_end_matches(':');
            if heading.eq_ignore_ascii_case(name) {
                start = Some(offset + line.len());
            }
        }
        offset += line.len();
    }
    start.and_then(|begin| {
        let body = summary[begin.min(summary.len())..].trim();
        (!body.is_empty()).then_some(body)
    })
}

/// Render the ledger for the summarizer prompt.
///
/// Bounded: lower-priority sections lose their tail first, and a dropped item is counted
/// in the same block. Every line names the entry it came from.
pub fn render_ledger(ledger: &TaskStateLedger) -> String {
    if ledger.is_empty() {
        return String::new();
    }
    let mut out = String::new();
    out.push_str("## Protected Task State (deterministic, provenance-tagged)\n");
    out.push_str(
        "Derived from the transcript by fixed rules; no item is inferred or invented. \
         Use it to keep constraints, current decisions and verified evidence exact. \
         A SUPERSEDED item is history, not a current requirement.\n",
    );

    let mut dropped: Vec<(usize, &'static str)> = Vec::new();
    push_items(&mut out, "ACTIVE CONSTRAINTS", &ledger.constraints, CONSTRAINTS_CAP, &mut dropped, |item| {
        let mut line = format!("{} [{}]: {}", item.id, item.entry, item.text);
        if !item.why.is_empty() {
            line.push_str(&format!(" (why: {})", item.why.join("; ")));
        }
        line
    });
    push_items(&mut out, "DECISIONS (current)", &ledger.decisions, DECISIONS_CAP, &mut dropped, |item| {
        format!("{} [{}]: {}", item.id, item.entry, item.text)
    });
    if !ledger.superseded.is_empty() {
        out.push_str("\n**SUPERSEDED (history, not current)**\n");
        for item in ledger.superseded.iter().take(SUPERSEDED_CAP) {
            out.push_str(&format!(
                "- {} [{}] replaced by {} [{}]: {}\n",
                item.old_id, item.old_entry, item.new_id, item.new_entry, item.new_text
            ));
        }
        if ledger.superseded.len() > SUPERSEDED_CAP {
            dropped.push((ledger.superseded.len() - SUPERSEDED_CAP, "supersession"));
        }
    }
    if !ledger.evidence.is_empty() {
        out.push_str("\n**VERIFIED EVIDENCE**\n");
        for item in ledger.evidence.iter().take(EVIDENCE_CAP) {
            let repeats = if item.repeats > 1 {
                format!(" (x{}, latest [{}])", item.repeats, item.last_entry)
            } else {
                String::new()
            };
            out.push_str(&format!("- {} [{}]: {}{}\n", item.id, item.entry, item.text, repeats));
        }
        if ledger.evidence.len() > EVIDENCE_CAP {
            dropped.push((ledger.evidence.len() - EVIDENCE_CAP, "evidence item"));
        }
    }
    if !ledger.anchors.is_empty() {
        out.push_str("\n**EXACT ANCHORS**: ");
        out.push_str(&ledger.anchors.join(", "));
        out.push('\n');
    }
    if !ledger.retractions.is_empty() {
        out.push_str("\n**RETRACTIONS (assistant-recorded history)**\n");
        for item in ledger.retractions.iter().take(RETRACTIONS_CAP) {
            out.push_str(&format!("- [{}] {}\n", item.entry, item.text));
        }
    }
    push_items(&mut out, "OPEN WORK", &ledger.open_work, OPEN_WORK_CAP, &mut dropped, |item| {
        format!("[{}] {}", item.entry, item.text)
    });
    if !dropped.is_empty() {
        let total: usize = dropped.iter().map(|(count, _)| *count).sum();
        let detail = dropped
            .iter()
            .map(|(count, label)| format!("{count} {label}(s)"))
            .collect::<Vec<_>>()
            .join(", ");
        out.push_str(&format!(
            "\n_({total} lower-priority item(s) omitted from this block: {detail}; the full ledger is persisted on the compaction entry.)_\n"
        ));
    }
    if out.len() > RENDER_MAX_CHARS {
        out = truncate_ledger(out, RENDER_MAX_CHARS);
    }
    out
}

fn truncate_ledger(mut out: String, max: usize) -> String {
    let mut end = max;
    while end > 0 && !out.is_char_boundary(end) {
        end -= 1;
    }
    out.truncate(end);
    out.push_str("\n_(ledger block truncated to its character budget; the full ledger is persisted on the compaction entry.)_\n");
    out
}

fn push_items<F>(
    out: &mut String,
    title: &str,
    items: &[TaskStateItem],
    cap: usize,
    dropped: &mut Vec<(usize, &'static str)>,
    render: F,
) where
    F: Fn(&TaskStateItem) -> String,
{
    if items.is_empty() {
        return;
    }
    out.push_str(&format!("\n**{title}**\n"));
    for item in items.iter().take(cap) {
        out.push_str("- ");
        out.push_str(&render(item));
        out.push('\n');
    }
    if items.len() > cap {
        let label = match title {
            "ACTIVE CONSTRAINTS" => "active constraint",
            "DECISIONS (current)" => "current decision",
            "OPEN WORK" => "open-work item",
            _ => "item",
        };
        dropped.push((items.len() - cap, label));
    }
}

/// Instruction appended after the ledger block when it is supplied to the summarizer.
pub const LEDGER_INSTRUCTION: &str = "Copy every ACTIVE CONSTRAINT, current DECISION and VERIFIED EVIDENCE item into the \
matching section of the summary without paraphrasing identifiers, paths, numbers or error codes. Keep SUPERSEDED \
items out of the current state; mention a superseded item only as history.";

#[cfg(test)]
mod tests {
    use super::*;
    use pi_ai::types::Message;

    fn user_entry(id: &str, text: &str) -> CompactionSessionEntry {
        CompactionSessionEntry::Message {
            id: id.to_string(),
            parent_id: None,
            message: AgentMessage::Message(Message::User(pi_ai::types::UserMessage::new(
                pi_ai::types::UserContent::Text(text.into()),
                0,
            ))),
        }
    }

    fn assistant_entry(id: &str, text: &str) -> CompactionSessionEntry {
        CompactionSessionEntry::Message {
            id: id.to_string(),
            parent_id: None,
            message: AgentMessage::Message(Message::Assistant(pi_ai::types::AssistantMessage {
                content: vec![pi_ai::types::ContentBlock::Text(pi_ai::types::TextContent::new(text))],
                stop_reason: "stop".into(),
                ..Default::default()
            })),
        }
    }

    fn tool_entry(id: &str, text: &str, is_error: bool) -> CompactionSessionEntry {
        CompactionSessionEntry::Message {
            id: id.to_string(),
            parent_id: None,
            message: AgentMessage::Message(Message::ToolResult(
                pi_ai::types::ToolResultMessage::new(
                    "call-1",
                    "bash",
                    vec![pi_ai::types::ImageOrTextContent::Text(pi_ai::types::TextContent::new(text))],
                    is_error,
                    0,
                ),
            )),
        }
    }

    #[test]
    fn enumerated_constraints_are_split_and_kept_verbatim() {
        let entries = vec![user_entry(
            "entry-aaaa",
            "Constraints: (1) Never modify crates/pi-ai/src/providers/anthropic/. \
             (2) All tests must run offline. (3) Keep the public API of SessionManager unchanged.",
        )];
        let ledger = build_ledger(&entries, None);
        assert_eq!(ledger.constraints.len(), 3, "{:?}", ledger.constraints);
        assert!(ledger.constraints[0].text.contains("anthropic"));
        assert_eq!(ledger.constraints[0].entry, "entry-aa");
        let rendered = render_ledger(&ledger);
        assert!(rendered.contains("Never modify crates/pi-ai/src/providers/anthropic/"));
        assert!(rendered.contains("must run offline"));
    }

    #[test]
    fn a_later_correction_retires_the_earlier_decision() {
        let entries = vec![
            assistant_entry(
                "a1",
                "Decision: store checkpoints in a sidecar file under checkpoints/<session>.jsonl.",
            ),
            user_entry(
                "u2",
                "Drop the sidecar checkpoint idea. Persist checkpoints inline in the transcript instead.",
            ),
        ];
        let ledger = build_ledger(&entries, None);
        assert!(
            ledger.decisions.iter().all(|item| !item.text.contains("sidecar file")),
            "superseded decision must leave the current list: {:?}",
            ledger.decisions
        );
        assert_eq!(ledger.superseded.len(), 1, "{:?}", ledger.superseded);
        assert!(ledger.superseded[0].new_text.contains("inline in the transcript"));
        let rendered = render_ledger(&ledger);
        assert!(rendered.contains("SUPERSEDED (history, not current)"));
        assert!(rendered.contains("inline in the transcript instead"));
    }

    #[test]
    fn a_correction_without_a_predecessor_is_still_recorded() {
        let entries = vec![user_entry(
            "u1",
            "Change of plan: keep_recent_tokens should be 32000, not 20000.",
        )];
        let ledger = build_ledger(&entries, None);
        assert!(ledger.constraints.iter().any(|item| item.text.contains("32000")));
        assert!(ledger.superseded.is_empty());
    }

    #[test]
    fn evidence_is_deduplicated_and_counts_repeats() {
        let entries = vec![
            tool_entry("t1", "test result: ok. 4785 passed; 0 failed; 7 ignored", false),
            tool_entry("t2", "noise\ntest result: ok. 4790 passed; 0 failed; 7 ignored", false),
        ];
        let ledger = build_ledger(&entries, None);
        assert_eq!(ledger.evidence.len(), 1, "{:?}", ledger.evidence);
        assert_eq!(ledger.evidence[0].repeats, 2);
        assert_eq!(ledger.evidence[0].last_entry, "t2");
    }

    #[test]
    fn anchors_skip_log_levels_and_need_repetition() {
        let entries = vec![
            user_entry("u1", "The file crates/pi-coding-agent/src/core/session_manager.rs holds prepare_compaction."),
            assistant_entry("a2", "I will open crates/pi-coding-agent/src/core/session_manager.rs and read prepare_compaction."),
            tool_entry("t3", "INFO DEBUG WARN INFO WARN", false),
        ];
        let ledger = build_ledger(&entries, None);
        assert!(ledger.anchors.iter().any(|a| a.contains("session_manager.rs")));
        assert!(ledger.anchors.iter().any(|a| a == "prepare_compaction"));
        for noise in ["INFO", "DEBUG", "WARN"] {
            assert!(!ledger.anchors.iter().any(|a| a == noise), "{noise} leaked into anchors");
        }
    }

    #[test]
    fn a_ledger_survives_three_compaction_cycles_unchanged() {
        let first = vec![user_entry("u1", "Never modify the migration in crates/pi-ai/src/model.rs.")];
        let mut ledger = build_ledger(&first, None);
        for cycle in 0..3 {
            // The source entries are gone; each cycle only sees new, unrelated work.
            let fresh = vec![assistant_entry(&format!("a{cycle}"), "Ran the suite again; still green.")];
            ledger = build_ledger(&fresh, Some(&ledger));
            assert_eq!(ledger.constraints.len(), 1, "cycle {cycle} lost a constraint");
            assert!(ledger.constraints[0].text.contains("crates/pi-ai/src/model.rs"));
        }
    }

    #[test]
    fn previous_ledger_items_are_not_duplicated() {
        let entries = vec![user_entry("u1", "All tests must run offline.")];
        let once = build_ledger(&entries, None);
        let again = build_ledger(&entries, Some(&once));
        assert_eq!(again.constraints.len(), 1);
    }

    #[test]
    fn caps_are_enforced_with_a_visible_dropped_count() {
        let mut entries = Vec::new();
        for index in 0..(CONSTRAINTS_CAP + 3) {
            entries.push(user_entry(
                &format!("u{index}"),
                &format!("Requirement {index} must hold for module_{index}."),
            ));
        }
        let ledger = build_ledger(&entries, None);
        assert_eq!(ledger.constraints.len(), CONSTRAINTS_CAP + 3);
        let rendered = render_ledger(&ledger);
        assert!(rendered.contains("3 lower-priority item(s) omitted"), "{rendered}");
        assert!(rendered.contains("3 active constraint(s)"), "{rendered}");
    }

    #[test]
    fn rendering_is_bounded_and_never_splits_a_character() {
        let mut ledger = TaskStateLedger::default();
        for index in 0..40 {
            ledger.constraints.push(TaskStateItem {
                id: format!("C{index}"),
                text: "\u{0436}".repeat(200),
                entry: "e".to_string(),
                why: Vec::new(),
            });
            ledger.decisions.push(TaskStateItem {
                id: format!("D{index}"),
                text: "\u{0436}".repeat(200),
                entry: "e".to_string(),
                why: Vec::new(),
            });
        }
        let rendered = render_ledger(&ledger);
        assert!(rendered.chars().count() <= RENDER_MAX_CHARS + 200);
        assert!(rendered.contains("truncated to its character budget"));
    }

    #[test]
    fn summary_sections_are_read_for_carry_forward() {
        let summary = "## Goal\nShip it.\n\n## Next Steps\n1. Wire the ledger.\n2. Run the suite.\n";
        assert_eq!(summary_section(summary, "Next Steps"), Some("1. Wire the ledger.\n2. Run the suite."));
        assert_eq!(summary_section(summary, "Blocked"), None);
    }

    #[test]
    fn ledger_round_trips_through_json() {
        let entries = vec![user_entry("u1", "Never touch the frozen provider subtree.")];
        let ledger = build_ledger(&entries, None);
        let value = serde_json::to_value(&ledger).unwrap();
        let back: TaskStateLedger = serde_json::from_value(value).unwrap();
        assert_eq!(ledger, back);
    }

    #[test]
    fn user_text_that_is_only_a_greeting_produces_no_items() {
        let entries = vec![user_entry("u1", "hi"), user_entry("u2", "thanks")];
        let ledger = build_ledger(&entries, None);
        assert!(ledger.is_empty(), "{ledger:?}");
        assert_eq!(render_ledger(&ledger), "");
    }
}
