//! Campaign section 35, scenarios E and F: memory ingest/recall across a
//! session boundary, and memory update where the current fact wins while the
//! historical evidence stays reachable.
//!
//! Both scenarios drive the real owners end to end: `MemoryService` (the same
//! service the `memory` host handlers build), the real `MemoryStore` on a
//! temporary agent directory, the real import pipeline with a scripted
//! extractor (offline, no provider), and the real indexed retrieval path
//! (`MemoryService::search`, which builds/uses the process-wide corpus index,
//! and `MemoryService::recall`, which renders the injected block).
//!
//! Session boundary: a "session" here is one `MemoryService` instance plus its
//! session artifact directory. Closing the session drops the instance; the new
//! session is a freshly constructed service over the same agent directory, so
//! every process-level cache must be revalidated against the on-disk store.
//! No real user data, store, profile or credential is touched: everything
//! lives under one `tempfile::TempDir`.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use pi_coding_agent::core::memory::evidence::hash;
use pi_coding_agent::core::memory::jobs::{ExtractionResult, MemoryExtractor};
use pi_coding_agent::core::memory::search::{MemoryFreshness, MemoryHit};
use pi_coding_agent::core::memory::service::MemoryService;
use pi_coding_agent::core::refinement::refinement::normalize_refinement_proposal;
use serde_json::{json, Value};
use tempfile::TempDir;

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/// One synthetic workspace: a project root plus the agent directory both
/// sessions share. Dropping it removes every byte the scenario wrote.
struct Workspace {
    _root: TempDir,
    project: PathBuf,
    agent: PathBuf,
}

impl Workspace {
    fn new() -> Self {
        let root = tempfile::tempdir().expect("temp dir");
        let project = root.path().join("project");
        std::fs::create_dir_all(&project).expect("project dir");
        let agent = root.path().join("agent");
        std::fs::create_dir_all(&agent).expect("agent dir");
        Self {
            _root: root,
            project,
            agent,
        }
    }

    /// Session start: a fresh service over the shared agent directory, exactly
    /// like a new CLI session resolving its project identity from the cwd.
    fn session(&self) -> MemoryService {
        MemoryService::new(
            &self.project.to_string_lossy(),
            &self.agent.to_string_lossy(),
            None,
        )
        .expect("memory service")
    }

    /// A different project root under the same agent directory: same host, a
    /// different project scope.
    fn other_project_session(&self) -> MemoryService {
        let other = self._root.path().join("other-project");
        std::fs::create_dir_all(&other).expect("other project dir");
        MemoryService::new(&other.to_string_lossy(), &self.agent.to_string_lossy(), None)
            .expect("other memory service")
    }

    fn store_bytes(&self, service: &MemoryService) -> Vec<u8> {
        std::fs::read(&service.store.path).expect("store file")
    }
}

fn source(id: &str) -> Value {
    json!({
        "id": id,
        "origin": "user",
        "sha256": hash(&format!("Synthetic evidence for {id}")),
        "uri": format!("synthetic://scenario/{id}"),
        "revision": "scenario_revision",
    })
}

/// One `memory` create edit for the apply payload.
fn create(id: &str, title: &str, content: &str, path: &str, sources: Vec<Value>) -> Value {
    let mut edit = json!({
        "action": "create",
        "kind": "memory",
        "id": id,
        "title": title,
        "content": content,
        "path": path,
    });
    if !sources.is_empty() {
        edit["metadata"] = json!({"sources": sources});
    }
    edit
}

fn update(id: &str, title: &str, content: &str, path: &str, sources: Vec<Value>) -> Value {
    let mut edit = create(id, title, content, path, sources);
    edit["action"] = json!("update");
    edit
}

fn payload(event: &str, revision: i64, edits: Vec<Value>) -> Value {
    json!({
        "eventId": event,
        "revision": revision,
        "proposal": {
            "summary": format!("Synthetic apply {event}"),
            "rationale": "Scenario fixture",
            "expectedOutcome": "Store the synthetic fact",
            "edits": edits,
        }
    })
}

async fn apply(service: &MemoryService, body: Value) -> Value {
    service
        .request("apply", body.as_object().expect("payload object"), None, None)
        .await
        .expect("apply")
}

fn ids(hits: &[MemoryHit]) -> Vec<String> {
    hits.iter().map(|hit| hit.id.clone()).collect()
}

fn write_session_file(path: &Path, rows: &[(&str, &str, i64)]) {
    let body = rows
        .iter()
        .map(|(id, content, timestamp)| {
            json!({
                "type": "message",
                "id": id,
                "message": {"role": "user", "content": content, "timestamp": timestamp}
            })
            .to_string()
        })
        .collect::<Vec<_>>()
        .join("\n");
    std::fs::write(path, body).expect("session fixture");
}

/// The real import pipeline with a scripted extractor: prepare, run one chunk
/// with a fixed proposal, then apply. No provider, no network, no tokens.
async fn import_with(service: &MemoryService, session: &Path, edits: Value) -> Value {
    let job = service
        .jobs
        .prepare(&session.to_string_lossy())
        .await
        .expect("import prepare");
    let extract: MemoryExtractor = Arc::new(move |_records| {
        let edits = edits.clone();
        Box::pin(async move {
            Ok(ExtractionResult {
                proposal: normalize_refinement_proposal(&edits),
                input: 10.0,
                output: 5.0,
            })
        })
    });
    service
        .request(
            "import_run",
            json!({"id": job.id}).as_object().unwrap(),
            Some(extract),
            None,
        )
        .await
        .expect("import run");
    let revision = service.store.read().expect("store").memory.revision;
    service
        .request(
            "import_apply",
            json!({"id": job.id, "revision": revision})
                .as_object()
                .unwrap(),
            None,
            None,
        )
        .await
        .expect("import apply")
}

fn enable(service: &MemoryService, settings: Value) {
    std::fs::create_dir_all(&service.store.dir).expect("store dir");
    std::fs::write(
        Path::new(&service.store.dir).join("settings.json"),
        settings.to_string(),
    )
    .expect("settings");
}

// ---------------------------------------------------------------------------
// Scenario E: ingest, close the session, recall in a new session
// ---------------------------------------------------------------------------

const DENTIST: &str = "Dr Ines Halloran, Wednesday 4 November 2026 at 09:15, Bridge Street clinic.";
const CAR: &str = "Toyota Corolla service booked at Kelvin Motors on 12 December 2026.";
const DENTIST_QUERY: &str = "when is my dentist appointment";

#[tokio::test]
async fn scenario_e_facts_ingested_in_one_session_recall_in_the_next() {
    let workspace = Workspace::new();

    // --- Session 1: ingest two synthetic facts through the real apply path.
    let first_session = workspace.session();
    let revision = first_session.store.read().expect("store").memory.revision;
    assert_eq!(revision, 0);
    let result = apply(
        &first_session,
        payload(
            "e_ingest",
            revision,
            vec![
                create(
                    "dentist_appt",
                    "Dentist appointment",
                    DENTIST,
                    "health",
                    vec![source("e1")],
                ),
                create("car_service", "Car service", CAR, "errands", vec![source("e2")]),
            ],
        ),
    )
    .await;
    assert_eq!(result["appliedEdits"].as_array().expect("edits").len(), 2);
    let stored = first_session.store.read().expect("store");
    assert_eq!(stored.memory.revision, 1);
    assert_eq!(stored.entries["memory"]["dentist_appt"].content, DENTIST);
    assert_eq!(stored.entries["memory"]["car_service"].content, CAR);
    // The authoritative bytes are on disk before the session closes.
    let first_bytes = workspace.store_bytes(&first_session);
    assert!(serde_json::from_slice::<Value>(&first_bytes).is_ok());
    // Session 1's own view of the query, recorded for the cold/warm comparison.
    let session_one_ids = ids(&first_session.search(DENTIST_QUERY, false));
    let session_one_recall = first_session.recall(DENTIST_QUERY);
    drop(first_session);

    // --- Session 2: a brand new service over the same agent directory.
    let second_session = workspace.session();
    let hits = second_session.search(DENTIST_QUERY, false);
    assert_eq!(
        hits.first().map(|hit| hit.id.as_str()),
        Some("project:memory:dentist_appt"),
        "the ingested fact must rank first in the new session: {:?}",
        ids(&hits)
    );
    let top = hits.first().expect("top hit");
    assert_eq!(top.entry.content, DENTIST);
    assert_eq!(top.scope.as_str(), "project");
    // Why it matched: the query terms are recorded per hit.
    let matched: Vec<&str> = top.matched.iter().map(String::as_str).collect();
    assert!(matched.contains(&"dentist"));
    assert!(matched.contains(&"appointment"));
    // The unrelated fact carries no query term, so it must not be returned.
    assert!(!ids(&hits).iter().any(|id| id == "project:memory:car_service"));
    // Sources survive the session boundary, so citations still resolve.
    assert_eq!(
        top.sources.iter().map(|source| source.id.as_str()).collect::<Vec<_>>(),
        vec!["e1"]
    );
    // The typed hit carries the search leg's default freshness; freshness is
    // resolved against the cited sources when the service renders a response.
    assert_eq!(top.freshness, MemoryFreshness::Unknown);

    // The host-facing `search` action agrees with the typed hit, so the agent
    // surface (not only the Rust API) sees the ingested fact.
    let listed = second_session
        .request(
            "search",
            json!({"query": DENTIST_QUERY}).as_object().unwrap(),
            None,
            None,
        )
        .await
        .expect("search request");
    let rows = listed.as_array().expect("search rows");
    assert_eq!(rows.len(), hits.len(), "the request lists the same hits");
    assert_eq!(rows[0]["id"], json!("project:memory:dentist_appt"));
    assert_eq!(rows[0]["scope"], json!("project"));
    assert_eq!(rows[0]["title"], json!("Dentist appointment"));
    assert_eq!(rows[0]["version"], json!(top.entry.version));
    assert_eq!(rows[0]["status"], json!("current"));
    // A `user`-origin citation has no file to stat, so freshness stays
    // unknown instead of blocking the rendered recall block below.
    assert_eq!(rows[0]["freshness"], json!("unknown"));
    assert_eq!(rows[0]["sources"][0]["id"], json!("e1"));
    assert_eq!(rows[0]["matched"], json!(top.matched));

    // Warm cache: the same query in the same session must be stable.
    assert_eq!(ids(&second_session.search(DENTIST_QUERY, false)), ids(&hits));
    // Cold-vs-warm parity: the first session (different service instance,
    // shared process cache) saw the same ranking.
    assert_eq!(session_one_ids, ids(&hits));

    // The rendered recall block carries the fact, labelled with its entry id.
    let recall = second_session.recall(DENTIST_QUERY);
    assert!(recall.text.contains("project:memory:dentist_appt"), "{}", recall.text);
    assert!(recall.text.contains("Halloran"), "{}", recall.text);
    assert!(!recall.text.contains("Corolla"), "{}", recall.text);
    assert_eq!(recall.ids, vec!["project:memory:dentist_appt".to_string()]);
    // Cold-vs-warm recall parity: the rendered block is identical to the one
    // the first session produced, so a new session does not change the text
    // the model is given for the same fact.
    assert_eq!(
        recall.text, session_one_recall.text,
        "the recall block must render identically across the session boundary"
    );
    assert_eq!(recall.ids, session_one_recall.ids);

    // A third session sees exactly the same ranking (restart, no session state).
    drop(second_session);
    let third_session = workspace.session();
    assert_eq!(ids(&third_session.search(DENTIST_QUERY, false)), ids(&hits));

    // Project isolation: the same host, a different project root, sees nothing.
    let other = workspace.other_project_session();
    assert!(
        other.search(DENTIST_QUERY, false).is_empty(),
        "project B must not recall project A's facts: {:?}",
        ids(&other.search(DENTIST_QUERY, false))
    );
    assert!(
        !Path::new(&other.store.path).exists(),
        "the other project must never write a store: {}",
        other.store.path
    );
}

// ---------------------------------------------------------------------------
// Scenario F: a later authoritative fact wins, history stays reachable
// ---------------------------------------------------------------------------

#[tokio::test]
async fn scenario_f_updated_fact_wins_and_history_stays_accessible() {
    let workspace = Workspace::new();

    // --- Session 1: store the fact, then revise it with authoritative data.
    let first_session = workspace.session();
    apply(
        &first_session,
        payload(
            "f_initial",
            0,
            vec![create(
                "airport",
                "Preferred airport",
                "Preferred airport: Sydney (SYD), terminal 2.",
                "travel",
                vec![source("f1")],
            )],
        ),
    )
    .await;
    apply(
        &first_session,
        payload(
            "f_revised",
            1,
            vec![update(
                "airport",
                "Preferred airport",
                "Preferred airport: Melbourne (MEL), terminal 4.",
                "travel",
                vec![source("f2")],
            )],
        ),
    )
    .await;
    let stored = first_session.store.read().expect("store");
    assert_eq!(stored.memory.revision, 2);
    assert_eq!(
        stored.entries["memory"]["airport"].content,
        "Preferred airport: Melbourne (MEL), terminal 4."
    );
    assert_eq!(
        stored.entries["memory"]["airport"].metadata["sources"][0]["id"],
        json!("f2")
    );
    // The superseded wording is still in the authoritative history row.
    let history = first_session
        .request("history", json!({}).as_object().unwrap(), None, None)
        .await
        .expect("history");
    let rows = history.as_array().expect("history rows");
    let revision_row = rows
        .iter()
        .find(|row| row["id"] == json!("f_revised"))
        .expect("revision row");
    assert_eq!(
        revision_row["appliedEdits"][0]["before"]["content"],
        json!("Preferred airport: Sydney (SYD), terminal 2.")
    );
    assert_eq!(
        revision_row["appliedEdits"][0]["after"]["content"],
        json!("Preferred airport: Melbourne (MEL), terminal 4.")
    );
    drop(first_session);

    // --- Session 2: the current fact is what recall returns.
    let second_session = workspace.session();
    let hits = second_session.search("preferred airport", false);
    assert_eq!(
        ids(&hits),
        vec!["project:memory:airport".to_string()],
        "one entry, no duplicate of the superseded wording"
    );
    assert!(hits[0].entry.content.contains("Melbourne"));
    assert!(!hits[0].entry.content.contains("Sydney"));
    let recall = second_session.recall("preferred airport");
    assert!(recall.text.contains("Melbourne"), "{}", recall.text);
    assert!(!recall.text.contains("Sydney (SYD)"), "{}", recall.text);

    // History stays reachable from the new session too: the superseded wording
    // is out of recall but still in the authoritative audit rows.
    let history = second_session
        .request("history", json!({}).as_object().unwrap(), None, None)
        .await
        .expect("history");
    let rows = history.as_array().expect("history rows");
    assert_eq!(rows.len(), 2, "both applies are still recorded: {:?}", rows);
    let revision_row = rows
        .iter()
        .find(|row| row["id"] == json!("f_revised"))
        .expect("the revision row survives the session boundary");
    assert_eq!(
        revision_row["appliedEdits"][0]["before"]["content"],
        json!("Preferred airport: Sydney (SYD), terminal 2.")
    );
    assert_eq!(
        revision_row["appliedEdits"][0]["after"]["content"],
        json!("Preferred airport: Melbourne (MEL), terminal 4.")
    );
    // The older claim keeps the evidence that justified it, so the history row
    // stays citable after the update replaced the current fact.
    assert_eq!(
        revision_row["appliedEdits"][0]["before"]["metadata"]["sources"][0]["id"],
        json!("f1")
    );
}

#[tokio::test]
async fn scenario_f_supersession_keeps_the_loser_queryable_as_history() {
    let workspace = Workspace::new();

    // --- Session 1: a cited older report, then a later authoritative report
    // of the same preference dimension, both through the real import pipeline.
    let first_session = workspace.session();
    enable(&first_session, json!({"personaSupersession": true}));
    let session_file = workspace.project.join("session.jsonl");
    write_session_file(
        &session_file,
        &[
            ("row_1", "I drink coffee every morning.", 1000),
            ("row_2", "I switched to green tea, that is what I drink now.", 5000),
        ],
    );
    let overview = import_with(
        &first_session,
        &session_file,
        json!({
            "summary": "import", "rationale": "scenario", "expectedOutcome": "scenario",
            "edits": [
                {"action": "create", "kind": "memory", "id": "coffee_claim",
                 "title": "Morning coffee",
                 "content": "The user drinks coffee every morning.",
                 "metadata": {"sourceIds": ["row_1"], "evidenceStatus": "cited",
                              "personaKey": "pref:morning-drink", "claimType": "stated"}},
                {"action": "create", "kind": "memory", "id": "tea_claim",
                 "title": "Morning green tea",
                 "content": "The user switched to green tea for their morning drink.",
                 "metadata": {"sourceIds": ["row_2"], "evidenceStatus": "cited",
                              "personaKey": "pref:morning-drink", "claimType": "stated"}}
            ]
        }),
    )
    .await;
    assert_eq!(overview["personaSupersession"]["superseded"], json!(1));
    drop(first_session);

    // --- Session 2: current fact first, history still reachable.
    let second_session = workspace.session();
    let active = second_session.search("morning drink", false);
    assert_eq!(
        ids(&active),
        vec!["project:memory:tea_claim".to_string()],
        "only the current claim is retrieved by default"
    );
    let recall = second_session.recall("morning drink");
    assert!(recall.text.contains("green tea"), "{}", recall.text);
    assert!(!recall.text.contains("coffee every morning"), "{}", recall.text);

    let all = second_session.search("morning drink", true);
    let all_ids = ids(&all);
    assert!(all_ids.contains(&"project:memory:coffee_claim".to_string()), "{:?}", all_ids);
    assert!(all_ids.contains(&"project:memory:tea_claim".to_string()), "{:?}", all_ids);
    let coffee = all
        .iter()
        .find(|hit| hit.entry.id == "coffee_claim")
        .expect("historical claim retained");
    assert_eq!(coffee.entry.metadata["supersededBy"], json!("tea_claim"));
    assert_eq!(coffee.entry.metadata["status"], json!("superseded"));
    // The historical claim keeps the evidence that justified it.
    assert_eq!(coffee.entry.metadata["sourceIds"], json!(["row_1"]));

    // The host-facing surface reports the same split: a default request returns
    // only the winner, and `includeInactive` returns the loser marked
    // superseded instead of dropping it.
    let current = second_session
        .request(
            "search",
            json!({"query": "morning drink"}).as_object().unwrap(),
            None,
            None,
        )
        .await
        .expect("current search");
    let current_rows = current.as_array().expect("rows");
    assert_eq!(current_rows.len(), 1, "current request: {:?}", current_rows);
    assert_eq!(current_rows[0]["id"], json!("project:memory:tea_claim"));
    assert_eq!(current_rows[0]["status"], json!("current"));
    let with_history = second_session
        .request(
            "search",
            json!({"query": "morning drink", "includeInactive": true})
                .as_object()
                .unwrap(),
            None,
            None,
        )
        .await
        .expect("search with inactive entries");
    let history_rows = with_history.as_array().expect("rows");
    let loser = history_rows
        .iter()
        .find(|row| row["id"] == json!("project:memory:coffee_claim"))
        .expect("the superseded claim stays queryable");
    assert_eq!(loser["status"], json!("superseded"));
    assert_eq!(
        loser["preview"],
        json!("The user drinks coffee every morning.")
    );
}
