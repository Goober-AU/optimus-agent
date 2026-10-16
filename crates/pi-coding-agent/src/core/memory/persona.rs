//! Persona snapshot layer: persona-level memory with dynamic profile updates.
//!
//! The LLM writes typed claim entries; deterministic Rust composes the
//! persona snapshot they describe.
//!
//! # Field contract (shared with the extraction layer)
//!
//! Persona claims are ordinary `memory`-bucket `HarnessEntry` records whose
//! metadata carries additive, all-optional fields. Emission and sanitization
//! belong to the extraction layer (`sanitize_persona_typing`,
//! extraction.rs); this module only reads them:
//!
//! - `personaKey: string` — normalized topic+attribute key that groups
//!   competing claims for supersession (`pref:...` preference dimensions,
//!   `fact:...` stable facts). A non-empty key IS the claim marker; the
//!   supersession verifier groups on the same field.
//! - `claimType: "stated" | "inferred"` — stated claims are always eligible
//!   for the snapshot; inferred claims additionally require evidence ids
//!   (`metadata.evidenceIds`, `metadata.sources[].id` or
//!   `metadata.sourceIds`). `summarized` is reserved for a future overflow
//!   path and parses as stated-like when present.
//! - `reason: string` — quoted user reason when the claim replaces a
//!   same-key predecessor (feeds evolution lines).
//! - `firstObservedAt: string` — evidence timestamp (ISO); ordering falls
//!   back to the entry `created_at`.
//!
//! # Storage shape
//!
//! The snapshot is a sidecar JSON file next to the store document
//! (`persona_snapshot.json`), never a store entry: lexical search cannot
//! return it and the supersession machinery cannot walk it as a claim. The
//! sidecar only advances on a successful composition (technique D
//! advance-on-success semantics); a failed regeneration leaves the previous
//! snapshot in place so the floor never regresses to nothing.

use crate::core::memory::store::{read_json, write_json, MemoryDocument, MemoryStore};
use crate::core::refinement::refinement::{HarnessEntry, RefinementEdit, RefinementProposal};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Fixed id recorded inside the snapshot sidecar (never a store entry id).
pub const SNAPSHOT_ID: &str = "persona_snapshot";
/// Sidecar file name, relative to the store directory.
const SNAPSHOT_FILE: &str = "persona_snapshot.json";
/// Snapshot sidecar schema version.
const SNAPSHOT_SCHEMA: i64 = 1;
/// Header line of the composed snapshot; mirrors the recall-block framing
/// ("[memory data; not new evidence]" in search.rs) so the floor reads as
/// derived context, never as new user evidence.
const SNAPSHOT_HEADER: &str = "[persona snapshot; derived from cited memory, not new evidence]";
/// Section titles, in render order.
const SECTION_STATED: &str = "Stated preferences:";
const SECTION_INFERRED: &str = "Inferred tendencies:";
const SECTION_FACTS: &str = "Stable facts:";
const SECTION_EVOLUTION: &str = "Evolution (most recent change per topic):";
/// Hard cap on evolution lines regardless of budget leftovers.
const MAX_EVOLUTION_LINES: usize = 12;
/// Per-item truncation lengths (characters).
const ITEM_CHARS: usize = 160;
const EVOLUTION_SIDE_CHARS: usize = 90;
const REASON_CHARS: usize = 100;
/// Mirrors the extraction-layer key sanitizer bound (extraction.rs).
const PERSONA_KEY_MAX_CHARS: usize = 120;

#[derive(Clone, Copy, PartialEq, Eq)]
enum ClaimType {
    Stated,
    Inferred,
    Summarized,
}

#[derive(Clone)]
struct PersonaClaim {
    id: String,
    key: String,
    claim_type: ClaimType,
    content: String,
    reason: Option<String>,
    observed_at: String,
    superseded: bool,
    has_evidence: bool,
}

impl PersonaClaim {
    /// Eligibility for the snapshot floor. Inferred claims must carry
    /// evidence ids (the design doc's inferenceBasis rule); stated and
    /// summarized claims are always eligible.
    fn eligible(&self) -> bool {
        match self.claim_type {
            ClaimType::Inferred => self.has_evidence,
            _ => true,
        }
    }
}

/// Parses one store entry into a claim. Malformed or missing persona fields
/// fail safe: the claim is skipped, never a panic.
fn parse_claim(entry: &HarnessEntry) -> Option<PersonaClaim> {
    // A valid personaKey IS the claim marker (same field the supersession
    // verifier groups on). The extraction-layer sanitizer already trims and
    // bounds it; the parser re-validates so hand-written or promoted stores
    // cannot smuggle malformed keys into the floor.
    let key = entry
        .metadata
        .get("personaKey")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|key| {
            !key.is_empty()
                && key.chars().count() <= PERSONA_KEY_MAX_CHARS
                && key.chars().all(|c| !c.is_control())
        })?;
    let claim_type = match entry
        .metadata
        .get("claimType")
        .and_then(Value::as_str)
        .map(str::trim)
    {
        Some("stated") => ClaimType::Stated,
        Some("inferred") => ClaimType::Inferred,
        Some("summarized") => ClaimType::Summarized,
        _ => return None,
    };
    let content = collapse(&entry.content);
    if content.is_empty() {
        return None;
    }
    let reason = entry
        .metadata
        .get("reason")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|reason| !reason.is_empty())
        .map(str::to_string);
    let observed_at = entry
        .metadata
        .get("firstObservedAt")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(entry.created_at.trim())
        .to_string();
    Some(PersonaClaim {
        id: entry.id.clone(),
        key: key.to_string(),
        claim_type,
        content,
        reason,
        observed_at,
        superseded: entry.metadata.contains_key("supersededBy"),
        has_evidence: evidence_id_count(&entry.metadata) > 0,
    })
}

/// Counts evidence ids across the three provenance field shapes: the
/// extraction layer's explicit `evidenceIds`, the store's `sources` array
/// and the citation `sourceIds` array.
fn evidence_id_count(metadata: &serde_json::Map<String, Value>) -> usize {
    let mut count = 0;
    if let Some(list) = metadata.get("evidenceIds").and_then(Value::as_array) {
        count += list
            .iter()
            .filter(|value| {
                value
                    .as_str()
                    .map(|id| !id.trim().is_empty())
                    .unwrap_or(false)
            })
            .count();
    }
    if let Some(list) = metadata.get("sources").and_then(Value::as_array) {
        count += list
            .iter()
            .filter(|value| {
                value
                    .get("id")
                    .and_then(Value::as_str)
                    .map(|id| !id.trim().is_empty())
                    .unwrap_or(false)
            })
            .count();
    }
    if let Some(list) = metadata.get("sourceIds").and_then(Value::as_array) {
        count += list
            .iter()
            .filter(|value| {
                value
                    .as_str()
                    .map(|id| !id.trim().is_empty())
                    .unwrap_or(false)
            })
            .count();
    }
    count
}

fn claims_from_document(doc: &MemoryDocument) -> Vec<PersonaClaim> {
    doc.entries
        .get("memory")
        .map(|bucket| bucket.values().filter_map(parse_claim).collect())
        .unwrap_or_default()
}

/// True when the edit creates a persona claim (trigger detection for both
/// the import path and the local-to-project promotion path). Detection is
/// the same non-empty `personaKey` the composer and the supersession
/// verifier group on.
pub fn edit_marks_persona_claim(edit: &RefinementEdit) -> bool {
    edit.kind == "memory"
        && edit
            .metadata
            .as_ref()
            .and_then(|metadata| metadata.get("personaKey"))
            .and_then(Value::as_str)
            .map(|key| {
                let key = key.trim();
                !key.is_empty()
                    && key.chars().count() <= PERSONA_KEY_MAX_CHARS
                    && key.chars().all(|c| !c.is_control())
            })
            .unwrap_or(false)
}

pub(crate) struct ComposeResult {
    pub content: String,
    pub claim_count: usize,
    pub current_count: usize,
    pub dropped: usize,
    pub derived_from: Vec<String>,
}

/// Deterministic composition of the snapshot text from the parsed claims.
/// Returns `None` when nothing eligible can be rendered (no floor).
///
/// Per `personaKey` only the newest current claim renders in the
/// current-state sections, so the floor stays coherent even on stores where
/// the supersession verifier has not (yet) marked the older twin; the
/// displaced claims feed evolution lines instead. Priority order under
/// budget pressure: stated preferences first, then inferred tendencies,
/// then stable facts, then evolution lines; within a section items are
/// newest-first. Every ordering key is a total order, so the same claim set
/// always composes to the same bytes.
pub(crate) fn compose(claims: &[PersonaClaim], budget_chars: i64) -> Option<ComposeResult> {
    let budget = budget_chars.clamp(512, 4000) as usize;
    let eligible: Vec<&PersonaClaim> = claims.iter().filter(|claim| claim.eligible()).collect();
    let mut derived_from: Vec<String> = claims.iter().map(|claim| claim.id.clone()).collect();
    derived_from.sort();
    derived_from.dedup();

    // Per-key walk, ordered by (observed_at, id).
    let mut by_key: BTreeMap<&str, Vec<&PersonaClaim>> = BTreeMap::new();
    for claim in &eligible {
        by_key.entry(claim.key.as_str()).or_default().push(claim);
    }
    for walk in by_key.values_mut() {
        walk.sort_by(|left, right| {
            left.observed_at
                .cmp(&right.observed_at)
                .then_with(|| left.id.cmp(&right.id))
        });
    }

    // Section item: the newest current claim of the key. Evolution line:
    // the immediate predecessor of that claim in the walk.
    let mut stated: Vec<(String, String)> = Vec::new();
    let mut inferred: Vec<(String, String)> = Vec::new();
    let mut facts: Vec<(String, String)> = Vec::new();
    let mut evolution: Vec<(String, String, String)> = Vec::new();
    let mut current_count = 0usize;
    for walk in by_key.values() {
        let Some(target_index) = walk
            .iter()
            .rposition(|claim| !claim.superseded)
        else {
            continue;
        };
        let target = walk[target_index];
        current_count += 1;
        if target_index > 0 {
            let old = walk[target_index - 1];
            let reason = match &target.reason {
                Some(reason) => format!(" ({})", truncate(reason, REASON_CHARS)),
                None => String::new(),
            };
            evolution.push((
                target.observed_at.clone(),
                target.key.clone(),
                format!(
                    "- {}: {} -> {}{}",
                    target.key,
                    truncate(&old.content, EVOLUTION_SIDE_CHARS),
                    truncate(&target.content, EVOLUTION_SIDE_CHARS),
                    reason
                ),
            ));
        }
        let item = format!(
            "- {}: {}",
            target.key,
            truncate(&target.content, ITEM_CHARS)
        );
        if target.claim_type == ClaimType::Inferred {
            inferred.push((target.observed_at.clone(), item));
        } else if target.key.starts_with("fact:") {
            facts.push((target.observed_at.clone(), item));
        } else {
            stated.push((target.observed_at.clone(), item));
        }
    }
    evolution.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| left.1.cmp(&right.1))
    });
    let evolution: Vec<String> = evolution
        .into_iter()
        .take(MAX_EVOLUTION_LINES)
        .map(|(_, _, line)| line)
        .collect();

    let sort_items = |mut items: Vec<(String, String)>| -> Vec<String> {
        items.sort_by(|left, right| {
            right
                .0
                .cmp(&left.0)
                .then_with(|| left.1.cmp(&right.1))
        });
        items.into_iter().map(|(_, item)| item).collect()
    };

    let sections: [(&str, Vec<String>); 4] = [
        (SECTION_STATED, sort_items(stated)),
        (SECTION_INFERRED, sort_items(inferred)),
        (SECTION_FACTS, sort_items(facts)),
        (SECTION_EVOLUTION, evolution),
    ];
    let mut lines: Vec<String> = Vec::new();
    let mut used = 0usize;
    let mut dropped = 0usize;
    push_line(&mut lines, &mut used, SNAPSHOT_HEADER.to_string(), budget);
    for (title, items) in sections {
        if items.is_empty() {
            continue;
        }
        if !push_line(&mut lines, &mut used, title.to_string(), budget) {
            dropped += items.len();
            continue;
        }
        for item in items {
            if !push_line(&mut lines, &mut used, item, budget) {
                dropped += 1;
            }
        }
    }
    if lines.len() <= 1 {
        // Nothing eligible rendered: no floor.
        return None;
    }
    if dropped > 0 {
        // Best-effort marker; the exact count always lives in the sidecar
        // metadata and the regeneration report.
        let marker = format!("(+{dropped} more claims not shown)");
        push_line(&mut lines, &mut used, marker, budget);
    }
    Some(ComposeResult {
        content: lines.join("\n"),
        claim_count: claims.len(),
        current_count,
        dropped,
        derived_from,
    })
}

/// Appends one line under the character budget. Returns false (and leaves
/// the line out) when it would not fit.
fn push_line(lines: &mut Vec<String>, used: &mut usize, line: String, budget: usize) -> bool {
    let extra = line.chars().count() + usize::from(!lines.is_empty());
    if *used + extra > budget {
        return false;
    }
    *used += extra;
    lines.push(line);
    true
}

fn collapse(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn truncate(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let mut truncated: String = text.chars().take(max.saturating_sub(3)).collect();
    truncated.push_str("...");
    truncated
}

fn snapshot_path(store: &MemoryStore) -> PathBuf {
    Path::new(&store.dir).join(SNAPSHOT_FILE)
}

fn now_iso() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string()
}

/// Reads the composed snapshot content from the sidecar, if present and
/// valid. A corrupt or empty file reads as missing (recovery path).
pub fn load(store: &MemoryStore) -> Option<String> {
    let value = read_json(&snapshot_path(store).to_string_lossy()).ok()?;
    let content = value
        .get("content")
        .and_then(Value::as_str)?
        .trim()
        .to_string();
    if content.is_empty() {
        None
    } else {
        Some(content)
    }
}

/// Regenerates the snapshot sidecar from the current store document.
///
/// Technique D semantics: the sidecar only advances on success. A failed
/// regeneration (lock contention, IO error) leaves the previous snapshot in
/// place and returns `Err`; a composition with no eligible claims is a
/// successful regeneration that removes the sidecar so the floor never
/// keeps injecting stale text.
pub fn regenerate(store: &MemoryStore, budget_chars: i64) -> Result<Value, String> {
    // A fresh store may not have its project directory yet (only apply()
    // creates it); the floor must still work on cold start.
    std::fs::create_dir_all(&store.dir).map_err(|error| error.to_string())?;
    let _lock = super::acquire_lock_sync(
        &store.dir,
        super::MemoryLockRetries {
            stale_ms: 30_000,
            retries: 0,
            min_timeout_ms: 0,
            max_timeout_ms: 0,
        },
    )?;
    let doc = store.read()?;
    let claims = claims_from_document(&doc);
    let report = match compose(&claims, budget_chars) {
        None => {
            let removed = std::fs::remove_file(snapshot_path(store)).is_ok();
            json!({
                "regenerated": true,
                "claims": claims.len(),
                "chars": 0,
                "dropped": 0,
                "removed": removed,
                "reason": "no_eligible_claims",
            })
        }
        Some(result) => {
            let value = json!({
                "schema": SNAPSHOT_SCHEMA,
                "id": SNAPSHOT_ID,
                "derivedFrom": result.derived_from,
                "regeneratedAt": now_iso(),
                "storeRevision": doc.memory.revision,
                "claimCount": result.claim_count,
                "chars": result.content.chars().count(),
                "dropped": result.dropped,
                "content": result.content,
            });
            write_json(&snapshot_path(store).to_string_lossy(), &value)?;
            json!({
                "regenerated": true,
                "claims": result.claim_count,
                "current": result.current_count,
                "chars": result.content.chars().count(),
                "dropped": result.dropped,
            })
        }
    };
    Ok(report)
}

/// Import-apply trigger (technique D): regenerates only when the applied job
/// carried persona-claim edits — exact change detection, not a count
/// threshold. Non-fatal by construction: failures are reported, never thrown.
pub fn regenerate_after_import(
    store: &MemoryStore,
    job: &crate::core::memory::jobs::ImportJob,
    budget_chars: i64,
) -> Value {
    let mut proposals: Vec<&RefinementProposal> = Vec::new();
    if let Some(proposal) = &job.accepted_proposal {
        proposals.push(proposal);
    }
    for chunk in &job.chunks {
        if let Some(proposal) = &chunk.proposal {
            proposals.push(proposal);
        }
    }
    let touched = proposals
        .iter()
        .any(|proposal| proposal.edits.iter().any(edit_marks_persona_claim));
    if !touched {
        return json!({"regenerated": false, "reason": "no_persona_claims"});
    }
    match regenerate(store, budget_chars) {
        Ok(report) => report,
        Err(error) => json!({"regenerated": false, "error": error}),
    }
}

pub struct FloorOutcome {
    /// The composed snapshot block, empty when the floor is disabled or
    /// unavailable.
    pub prefix: String,
    /// One of: disabled, loaded, regenerated, no_eligible_claims, failed.
    pub status: &'static str,
}

/// Injection-side entry point (technique C floor).
///
/// `disabled` never touches the filesystem, so a flag-off turn is
/// byte-identical to the pre-feature recall output. A missing or corrupt
/// sidecar triggers one cold-start/recovery regeneration under the store
/// lock (technique D recovery path); lock contention fails soft with an
/// empty prefix and the next turn retries.
pub fn persona_floor(store: &MemoryStore, enabled: bool, budget_chars: i64) -> FloorOutcome {
    if !enabled {
        return FloorOutcome {
            prefix: String::new(),
            status: "disabled",
        };
    }
    if let Some(content) = load(store) {
        return FloorOutcome {
            prefix: content,
            status: "loaded",
        };
    }
    match regenerate(store, budget_chars) {
        Ok(report) => match load(store) {
            Some(content) => FloorOutcome {
                prefix: content,
                status: "regenerated",
            },
            None => FloorOutcome {
                prefix: String::new(),
                status: "no_eligible_claims",
            },
        },
        Err(_) => FloorOutcome {
            prefix: String::new(),
            status: "failed",
        },
    }
}

/// Pure assembly of the injected note text: the snapshot block rides first as
/// the stable header, the lexical recall block after. An empty prefix is the
/// identity function, which is what keeps flag-off output byte-identical.
pub fn attach_floor(prefix: &str, recall_text: &str) -> String {
    match (prefix.is_empty(), recall_text.is_empty()) {
        (true, _) => recall_text.to_string(),
        (_, true) => prefix.to_string(),
        _ => format!("{prefix}\n{recall_text}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::memory::project::ProjectIdentity;
    use crate::core::refinement::refinement::normalize_refinement_proposal;
    use serde_json::json;

    fn claim(
        id: &str,
        key: &str,
        claim_type: &str,
        content: &str,
        observed_at: &str,
    ) -> PersonaClaim {
        PersonaClaim {
            id: id.to_string(),
            key: key.to_string(),
            claim_type: match claim_type {
                "inferred" => ClaimType::Inferred,
                "summarized" => ClaimType::Summarized,
                _ => ClaimType::Stated,
            },
            content: collapse(content),
            reason: None,
            observed_at: observed_at.to_string(),
            superseded: false,
            has_evidence: false,
        }
    }

    fn store_fixture() -> (MemoryStore, std::path::PathBuf) {
        let root = std::env::temp_dir().join(format!("prime-persona-{}", uuid::Uuid::new_v4()));
        let cwd = root.join("repo");
        let agent_dir = root.join("agent");
        std::fs::create_dir_all(&cwd).unwrap();
        std::fs::create_dir_all(&agent_dir).unwrap();
        let store = MemoryStore::new(
            &agent_dir.to_string_lossy(),
            ProjectIdentity {
                id: "project_test".to_string(),
                root: cwd.to_string_lossy().to_string(),
                aliases: Vec::new(),
            },
        )
        .unwrap();
        (store, root)
    }

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }

    fn apply_options(event_id: &str, revision: i64) -> crate::core::memory::store::ApplyOptions {
        crate::core::memory::store::ApplyOptions {
            event_id: event_id.to_string(),
            expected_revision: revision,
            ..Default::default()
        }
    }

    /// A persona claim proposal in the real field contract: personaKey +
    /// claimType + evidence, no marker boolean.
    fn persona_proposal(
        id: &str,
        key: &str,
        claim_type: &str,
        content: &str,
    ) -> crate::core::refinement::refinement::RefinementProposal {
        normalize_refinement_proposal(&json!({
            "summary": id,
            "rationale": "test evidence",
            "expectedOutcome": "persona claim",
            "edits": [{
                "action": "create",
                "kind": "memory",
                "id": id,
                "title": id,
                "content": content,
                "metadata": {"personaKey": key, "claimType": claim_type,
                             "sourceIds": ["m_0001:0"], "evidenceStatus": "cited"}
            }]
        }))
    }

    #[test]
    fn compose_is_deterministic_and_within_budget() {
        let mut claims: Vec<PersonaClaim> = Vec::new();
        for index in 0..30 {
            let claim_type = if index % 3 == 0 { "inferred" } else { "stated" };
            let mut item = claim(
                &format!("c{index:02}"),
                &format!("pref:topic-{index:02}"),
                claim_type,
                &format!("Preference number {index} with some descriptive detail"),
                &format!("2026-01-{:02}T00:00:00.000Z", index + 1),
            );
            if claim_type == "inferred" {
                item.has_evidence = true;
            }
            claims.push(item);
        }
        let first = compose(&claims, 2400).unwrap();
        let second = compose(&claims, 2400).unwrap();
        assert_eq!(first.content, second.content);
        assert!(first.content.chars().count() <= 2400);
        assert!(first.content.starts_with(SNAPSHOT_HEADER));
        assert!(first.content.contains(SECTION_STATED));
        assert!(first.content.contains(SECTION_INFERRED));
        assert_eq!(first.claim_count, 30);
        assert_eq!(first.current_count, 30);
    }

    #[test]
    fn compose_respects_budget_priority_and_marks_overflow() {
        let mut claims: Vec<PersonaClaim> = Vec::new();
        for index in 0..40 {
            claims.push(claim(
                &format!("s{index:02}"),
                &format!("pref:stated-{index:02}"),
                "stated",
                &format!("Stated preference {index} that goes on and on with padding words"),
                &format!("2026-02-{:02}T00:00:00.000Z", index + 1),
            ));
        }
        let result = compose(&claims, 512).unwrap();
        assert!(result.content.chars().count() <= 512);
        assert!(result.dropped > 0);
        assert!(result.content.contains("more claims not shown"));
        // Newest-first within the section: the newest claim must be present.
        assert!(result.content.contains("pref:stated-39"));
    }

    #[test]
    fn inferred_claims_require_evidence() {
        let no_evidence = claim(
            "i1",
            "pref:quiet",
            "inferred",
            "prefers quiet spaces",
            "2026-01-01T00:00:00.000Z",
        );
        let mut with_evidence = no_evidence.clone();
        with_evidence.id = "i2".to_string();
        with_evidence.has_evidence = true;
        let result = compose(&[no_evidence, with_evidence], 2400).unwrap();
        assert_eq!(result.current_count, 1);
        // Exactly one pref:quiet line: the evidence-less twin is excluded.
        assert_eq!(result.content.matches("pref:quiet").count(), 1);
        // The eligible inferred claim renders under its section.
        assert!(result.content.contains(SECTION_INFERRED));
    }

    #[test]
    fn superseded_claims_leave_current_sections_but_feed_evolution() {
        let mut old = claim(
            "p1",
            "pref:podcast",
            "stated",
            "enjoys podcasting about music trends",
            "2026-01-10T00:00:00.000Z",
        );
        old.superseded = true;
        let mut new = claim(
            "p2",
            "pref:podcast",
            "stated",
            "no longer enjoys podcasting",
            "2026-03-05T00:00:00.000Z",
        );
        new.reason = Some("bandmate conflicts stifled the collaboration".to_string());
        let result = compose(&[old, new], 2400).unwrap();
        assert!(result.content.contains(SECTION_EVOLUTION));
        assert!(result.content.contains("->"));
        assert!(result.content.contains("bandmate conflicts"));
        // Only the current claim renders in the stated section.
        assert_eq!(result.current_count, 1);
        assert!(result.content.contains("no longer enjoys podcasting"));
    }

    #[test]
    fn newest_current_claim_wins_when_supersession_has_not_run() {
        // Two current claims of one key (the E verifier has not marked the
        // older one yet): the floor must still state only the newest.
        let older = claim(
            "a1",
            "pref:hot-drink",
            "stated",
            "prefers coffee",
            "2026-01-01T00:00:00.000Z",
        );
        let newer = claim(
            "a2",
            "pref:hot-drink",
            "stated",
            "prefers tea now",
            "2026-06-01T00:00:00.000Z",
        );
        let result = compose(&[older.clone(), newer], 2400).unwrap();
        // Exactly one current-state item for the key: the newest claim.
        let section_items = result
            .content
            .lines()
            .filter(|line| line.starts_with("- pref:hot-drink:") && !line.contains("->"))
            .count();
        assert_eq!(section_items, 1);
        assert!(result.content.contains("- pref:hot-drink: prefers tea now"));
        // The displaced claim still surfaces as the evolution predecessor.
        assert!(result.content.contains("prefers coffee -> prefers tea now"));
        assert_eq!(result.current_count, 1);
    }

    #[test]
    fn attach_floor_is_identity_when_disabled() {
        assert_eq!(attach_floor("", "recall text"), "recall text");
        assert_eq!(attach_floor("", ""), "");
        let joined = attach_floor("persona block", "recall text");
        assert_eq!(joined, "persona block\nrecall text");
        assert_eq!(attach_floor("persona block", ""), "persona block");
    }

    #[test]
    fn persona_floor_disabled_never_touches_the_filesystem() {
        let (store, root) = store_fixture();
        runtime()
            .block_on(store.apply(
                &persona_proposal("c1", "pref:coffee", "stated", "drinks black coffee"),
                apply_options("op_c1", 0),
            ))
            .unwrap();
        let outcome = persona_floor(&store, false, 2400);
        assert_eq!(outcome.status, "disabled");
        assert!(outcome.prefix.is_empty());
        assert!(!snapshot_path(&store).exists());
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn regeneration_writes_sidecar_and_composes_deterministically() {
        let (store, root) = store_fixture();
        runtime()
            .block_on(store.apply(
                &persona_proposal("c1", "pref:coffee", "stated", "drinks black coffee"),
                apply_options("op_c1", 0),
            ))
            .unwrap();
        let report = regenerate(&store, 2400).unwrap();
        assert_eq!(report["regenerated"], true);
        let first = load(&store).unwrap();
        let second = regenerate(&store, 2400).unwrap();
        assert_eq!(second["regenerated"], true);
        // Content is deterministic across regenerations.
        assert_eq!(first, load(&store).unwrap());
        assert!(first.contains("pref:coffee"));
        // The snapshot is a sidecar, never a store entry.
        let doc = store.read().unwrap();
        assert!(doc
            .entries
            .get("memory")
            .map(|bucket| !bucket.contains_key(SNAPSHOT_ID))
            .unwrap_or(true));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn trigger_regenerates_only_on_persona_claim_events() {
        let (store, root) = store_fixture();
        // A plain memory apply with no persona fields: no trigger.
        let plain = normalize_refinement_proposal(&json!({
            "summary": "plain",
            "rationale": "test evidence",
            "expectedOutcome": "recall",
            "edits": [{"action": "create", "kind": "memory", "id": "m1",
                        "title": "m1", "content": "ordinary fact"}]
        }));
        runtime()
            .block_on(store.apply(&plain, apply_options("op_m1", 0)))
            .unwrap();
        let job = crate::core::memory::jobs::ImportJob {
            schema: 1.0,
            id: "job1".to_string(),
            source: "session.jsonl".to_string(),
            source_hash: "deadbeef".to_string(),
            status: crate::core::memory::jobs::ImportJobStatus::Applied,
            chunks: Vec::new(),
            next_chunk: 0,
            expected_revision: 1,
            error: None,
            coverage: crate::core::memory::jobs::ImportCoverage {
                lines: 1,
                evidence_records: 1,
                excluded_lines: Vec::new(),
            },
            usage: crate::core::memory::jobs::ImportUsage { input: 0.0, output: 0.0 },
            accepted_proposal: Some(plain),
        };
        let report = regenerate_after_import(&store, &job, 2400);
        assert_eq!(report["regenerated"], false);
        assert_eq!(report["reason"], "no_persona_claims");
        assert!(!snapshot_path(&store).exists());

        // A persona-claim apply event: trigger fires and the sidecar lands.
        let persona = persona_proposal("c1", "pref:tea", "stated", "prefers green tea");
        runtime()
            .block_on(store.apply(&persona, apply_options("op_c1", 1)))
            .unwrap();
        let job2 = crate::core::memory::jobs::ImportJob {
            schema: 1.0,
            id: "job2".to_string(),
            source: "session.jsonl".to_string(),
            source_hash: "deadbeef".to_string(),
            status: crate::core::memory::jobs::ImportJobStatus::Applied,
            chunks: Vec::new(),
            next_chunk: 0,
            expected_revision: 2,
            error: None,
            coverage: crate::core::memory::jobs::ImportCoverage {
                lines: 1,
                evidence_records: 1,
                excluded_lines: Vec::new(),
            },
            usage: crate::core::memory::jobs::ImportUsage { input: 0.0, output: 0.0 },
            accepted_proposal: Some(persona),
        };
        let report = regenerate_after_import(&store, &job2, 2400);
        assert_eq!(report["regenerated"], true);
        assert!(load(&store).unwrap().contains("pref:tea"));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn cold_start_and_recovery_regenerate_the_floor() {
        let (store, root) = store_fixture();
        runtime()
            .block_on(store.apply(
                &persona_proposal("c1", "pref:coffee", "stated", "drinks black coffee"),
                apply_options("op_c1", 0),
            ))
            .unwrap();
        // Cold start: no sidecar exists.
        let outcome = persona_floor(&store, true, 2400);
        assert_eq!(outcome.status, "regenerated");
        assert!(outcome.prefix.contains("pref:coffee"));
        // Loaded on the next turn.
        let outcome = persona_floor(&store, true, 2400);
        assert_eq!(outcome.status, "loaded");
        // Recovery: a corrupt sidecar is regenerated.
        std::fs::write(snapshot_path(&store), "not json at all").unwrap();
        let outcome = persona_floor(&store, true, 2400);
        assert_eq!(outcome.status, "regenerated");
        assert!(outcome.prefix.contains("pref:coffee"));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn regeneration_skips_malformed_entries_and_removes_stale_sidecars() {
        let (store, root) = store_fixture();
        // Valid claim plus malformed ones (missing personaKey, unknown
        // claimType, empty content) and one ordinary memory.
        runtime()
            .block_on(store.apply(
                &normalize_refinement_proposal(&json!({
                    "summary": "mixed",
                    "rationale": "test evidence",
                    "expectedOutcome": "persona claims",
                    "edits": [
                        {"action": "create", "kind": "memory", "id": "good1", "title": "good1",
                         "content": "prefers early mornings",
                         "metadata": {"personaKey": "pref:mornings",
                                     "claimType": "stated", "sourceIds": ["m_0001:0"]}},
                        {"action": "create", "kind": "memory", "id": "nokey", "title": "nokey",
                         "content": "missing personaKey",
                         "metadata": {"claimType": "stated"}},
                        {"action": "create", "kind": "memory", "id": "badtype", "title": "badtype",
                         "content": "unknown claim type",
                         "metadata": {"personaKey": "pref:x", "claimType": "vibes"}},
                        {"action": "create", "kind": "memory", "id": "empty", "title": "empty",
                         "content": "   ",
                         "metadata": {"personaKey": "pref:y", "claimType": "stated"}},
                        {"action": "create", "kind": "memory", "id": "notpersona", "title": "notpersona",
                         "content": "ordinary memory without persona fields"}
                    ]
                })),
                apply_options("op_mixed", 0),
            ))
            .unwrap();
        let report = regenerate(&store, 2400).unwrap();
        assert_eq!(report["regenerated"], true);
        let content = load(&store).unwrap();
        assert!(content.contains("pref:mornings"));
        assert!(!content.contains("missing personaKey"));
        assert!(!content.contains("vibes"));
        assert!(!content.contains("ordinary memory"));

        // A store with no eligible claims removes a stale sidecar so the
        // floor never injects outdated text.
        let empty_root = std::env::temp_dir().join(format!("prime-persona-{}", uuid::Uuid::new_v4()));
        let cwd = empty_root.join("repo");
        let agent_dir = empty_root.join("agent");
        std::fs::create_dir_all(&cwd).unwrap();
        std::fs::create_dir_all(&agent_dir).unwrap();
        let empty_store = MemoryStore::new(
            &agent_dir.to_string_lossy(),
            ProjectIdentity {
                id: "project_test".to_string(),
                root: cwd.to_string_lossy().to_string(),
                aliases: Vec::new(),
            },
        )
        .unwrap();
        std::fs::create_dir_all(&empty_store.dir).unwrap();
        std::fs::write(snapshot_path(&empty_store), "{\"content\":\"stale\"}").unwrap();
        let report = regenerate(&empty_store, 2400).unwrap();
        assert_eq!(report["reason"], "no_eligible_claims");
        assert_eq!(report["removed"], true);
        assert!(load(&empty_store).is_none());
        let outcome = persona_floor(&empty_store, true, 2400);
        assert_eq!(outcome.status, "no_eligible_claims");
        assert!(outcome.prefix.is_empty());
        std::fs::remove_dir_all(&empty_root).ok();
        std::fs::remove_dir_all(&root).ok();
    }
}
