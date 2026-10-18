//! Source intent, schema-1 delivery receipts, and retry integrity without a model.
use std::path::Path;
use std::sync::{Arc, Mutex};

use pi_coding_agent::core::memory::evidence::{hash, Evidence};
use pi_coding_agent::core::memory::jobs::{ExtractionResult, ImportJobStatus, MemoryExtractor};
use pi_coding_agent::core::memory::project::ProjectIdentity;
use pi_coding_agent::core::memory::search::{recall_memory, MemoryFreshness, MemoryHit, MemoryScope};
use pi_coding_agent::core::memory::service::MemoryService;
use pi_coding_agent::core::memory::store::{
    default_memory_settings, empty_document, ApplyOptions, MemoryStore,
};
use pi_coding_agent::core::refinement::refinement::{normalize_refinement_proposal, HarnessEntry};
use serde_json::{json, Value};
use tempfile::TempDir;

const CONFLICT: &str = "Event ID reused with different content";
const RESTORED: &str =
    "Event already committed before restore; inspect current memory before resubmitting";
const LEGACY_TIME: &str = "2026-10-01T00:00:00.000Z";
// Pre-fix schema-1 receipt: sha256 of the original ordered fingerprint payload.
const LEGACY_DIGEST: &str = "abc9fd1c16daa9b5b02aa300f3d98c4f47c3a097a0aa65c4b269583a84eeb270";
const LEGACY_REPLACEMENT_DIGEST: &str =
    "5b81e19cff6e093cc1e6f998a4d88a40b05bc2aa52ce4827546fb7fcad130e7f";
const LEGACY_PROPOSAL: &str = r#"{
    "summary": "Legacy source update", "rationale": "Synthetic fixture",
    "edits": [{"action": "update", "kind": "memory", "id": "note",
        "title": "Synthetic note", "content": "Legacy updated content"}],
    "expectedOutcome": "Keep prior evidence"
}"#;

struct Fixture {
    _root: TempDir,
    service: MemoryService,
}

impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let project = root.path().join("project");
        std::fs::create_dir_all(&project).unwrap();
        let service = MemoryService::new(
            &project.to_string_lossy(),
            &root.path().join("agent").to_string_lossy(),
            None,
        )
        .unwrap();
        Self {
            _root: root,
            service,
        }
    }

    async fn apply(&self, payload: &Value) -> Result<Value, String> {
        self.service
            .request("apply", payload.as_object().unwrap(), None, None)
            .await
    }

    async fn seed(&self) -> Value {
        self.apply(&apply_payload(
            "seed",
            0,
            "create",
            "Original content",
            Some(json!([source("original")])),
        ))
        .await
        .unwrap()
    }

    fn bytes(&self) -> Vec<u8> {
        std::fs::read(&self.service.store.path).unwrap()
    }

    fn sources(&self) -> Value {
        self.service.store.read().unwrap().entries["memory"]["note"].metadata["sources"].clone()
    }
}

fn source(id: &str) -> Value {
    json!({
        "id": id, "origin": "user", "sha256": hash(&format!("Synthetic evidence: {id}")),
        "uri": format!("synthetic://evidence/{id}"), "revision": "synthetic_revision"
    })
}

fn apply_payload(
    event: &str,
    revision: i64,
    action: &str,
    content: &str,
    sources: Option<Value>,
) -> Value {
    let mut payload = json!({
        "eventId": event, "revision": revision,
        "proposal": {
            "summary": "Update synthetic evidence", "rationale": "Synthetic fixture",
            "edits": [{"action": action, "kind": "memory", "id": "note",
                "title": "Synthetic note", "content": content}],
            "expectedOutcome": "Keep source intent"
        }
    });
    if let Some(sources) = sources {
        payload["sources"] = sources;
    }
    payload
}

fn legacy_fingerprint(payload: &Value) -> String {
    let proposal = normalize_refinement_proposal(&payload["proposal"]);
    hash(
        &serde_json::to_string(&json!({
            "proposal": proposal,
            "sources": payload.get("sources").cloned().unwrap_or_else(|| json!([])),
            "host": payload.get("host") == Some(&Value::Bool(true)),
            "replaceMetadata": false
        }))
        .unwrap(),
    )
}

fn write_snapshot(fixture: &Fixture, snapshot: &Value) {
    std::fs::create_dir_all(&fixture.service.store.dir).unwrap();
    std::fs::write(
        &fixture.service.store.path,
        serde_json::to_vec_pretty(snapshot).unwrap(),
    )
    .unwrap();
}

fn legacy_payload(sources: Option<Value>) -> Value {
    let mut payload = json!({
        "eventId": "legacy_update", "revision": 1,
        "proposal": serde_json::from_str::<Value>(LEGACY_PROPOSAL).unwrap()
    });
    if let Some(sources) = sources {
        payload["sources"] = sources;
    }
    payload
}

// Reconstruct historical entry/history JSON rather than generating it by apply().
// The old store could retain sources with None; the old service supplied Some([]).
fn install_legacy_fixture(fixture: &Fixture, sources: Option<Value>) -> Value {
    let digest = if sources
        .as_ref()
        .is_some_and(|refs| !refs.as_array().unwrap().is_empty())
    {
        LEGACY_REPLACEMENT_DIGEST
    } else {
        LEGACY_DIGEST
    };
    let store = &fixture.service.store;
    let before = json!({
        "id": "note", "kind": "memory", "title": "Synthetic note",
        "content": "Original content", "path": "general", "scope": "local",
        "reference": {}, "arguments": {},
        "metadata": {"projectId": store.project.id, "sources": [source("original")], "status": "current"},
        "source": "refine", "created_at": LEGACY_TIME, "updated_at": LEGACY_TIME, "version": 1
    });
    let mut after = before.clone();
    after["content"] = json!("Legacy updated content");
    after["version"] = json!(2);
    after["metadata"]["sources"] = sources.unwrap_or_else(|| json!([source("original")]));
    let proposal: Value = serde_json::from_str(LEGACY_PROPOSAL).unwrap();
    let mut edit = proposal["edits"][0].clone();
    edit["metadata"] = after["metadata"].clone();
    edit["before"] = before;
    edit["after"] = after.clone();
    edit["applied"] = json!(true);
    let result = json!({
        "id": "legacy_update", "summary": proposal["summary"], "rationale": proposal["rationale"],
        "expectedOutcome": proposal["expectedOutcome"], "appliedEdits": [edit],
        "harnessStatePath": store.path, "scope": "local"
    });
    let snapshot = json!({
        "schema": 1.0, "entries": {"memory": {"note": after}, "prompt": {}, "skill": {}, "subagent": {}},
        "refinements": [{
            "id": "legacy_update", "trigger": proposal["summary"], "changes": ["update memory:note"],
            "evidence": proposal["rationale"], "outcome": proposal["expectedOutcome"], "created_at": LEGACY_TIME
        }],
        "memory": {
            "schema": 1.0, "projectId": store.project.id, "revision": 2,
            "history": [result.clone()], "events": {"legacy_update": digest}
        }
    });
    write_snapshot(fixture, &snapshot);
    result
}

#[tokio::test]
async fn source_modes_preserve_clear_or_replace_and_retry_the_original_result() {
    for refs in [None, Some(json!([])), Some(json!([source("replacement")]))] {
        let fixture = Fixture::new();
        fixture.seed().await;
        let payload = apply_payload("update", 1, "update", "Updated content", refs.clone());
        let applied = fixture.apply(&payload).await.unwrap();
        let expected = refs.unwrap_or_else(|| json!([source("original")]));
        assert_eq!(fixture.sources(), expected);
        assert_eq!(
            applied["appliedEdits"][0]["before"]["metadata"]["sources"],
            json!([source("original")])
        );
        assert_eq!(
            applied["appliedEdits"][0]["after"]["metadata"]["sources"],
            expected
        );

        fixture
            .apply(&apply_payload(
                "later",
                2,
                "update",
                "Later content",
                Some(json!([source("later")])),
            ))
            .await
            .unwrap();
        let committed = fixture.bytes();
        let reopened = MemoryService::new(
            &fixture.service.store.project.root,
            &fixture.service.store.agent_dir,
            None,
        )
        .unwrap();
        let retry = reopened
            .request("apply", payload.as_object().unwrap(), None, None)
            .await
            .unwrap();
        assert_eq!(
            retry, applied,
            "retry returns history, not current entry state"
        );
        assert_eq!(fixture.bytes(), committed);
        assert_eq!(fixture.sources(), json!([source("later")]));
        assert_eq!(reopened.store.read().unwrap().memory.revision, 3);
    }
}

#[tokio::test]
async fn new_receipts_bind_all_three_source_modes() {
    let modes = [None, Some(json!([])), Some(json!([source("replacement")]))];
    for (committed_index, sources) in modes.iter().enumerate() {
        let fixture = Fixture::new();
        fixture.seed().await;
        let payload = apply_payload("update", 1, "update", "Updated content", sources.clone());
        let applied = fixture.apply(&payload).await.unwrap();
        let doc = fixture.service.store.read().unwrap();
        if committed_index < 2 {
            assert_ne!(doc.memory.events["update"], legacy_fingerprint(&payload));
        } else {
            assert_eq!(doc.memory.events["update"], legacy_fingerprint(&payload));
        }
        assert_eq!(
            doc.memory.events["update"].len(),
            64,
            "schema-1 SHA-256 receipt shape"
        );
        let before = fixture.bytes();
        for (retry_index, sources) in modes.iter().enumerate() {
            let retry = apply_payload("update", 1, "update", "Updated content", sources.clone());
            if retry_index == committed_index {
                assert_eq!(fixture.apply(&retry).await.unwrap(), applied);
            } else {
                assert_eq!(fixture.apply(&retry).await.unwrap_err(), CONFLICT);
            }
            assert_eq!(fixture.bytes(), before);
        }
    }
}

#[tokio::test]
async fn nonempty_source_fingerprints_stay_legacy_compatible_and_bind_all_source_fields() {
    let fixture = Fixture::new();
    let payload = apply_payload(
        "create",
        0,
        "create",
        "Original content",
        Some(json!([source("original")])),
    );
    let applied = fixture.apply(&payload).await.unwrap();
    assert_eq!(
        fixture.service.store.read().unwrap().memory.events["create"],
        legacy_fingerprint(&payload)
    );
    let before = fixture.bytes();
    for (key, value) in [
        ("id", json!("different")),
        ("origin", json!("assistant")),
        ("sha256", json!(hash("different evidence"))),
        ("uri", json!("synthetic://different")),
        ("revision", json!("different_revision")),
        ("projectPath", json!("different.txt")),
    ] {
        let mut changed = payload.clone();
        changed["sources"][0][key] = value;
        assert_eq!(
            fixture.apply(&changed).await.unwrap_err(),
            CONFLICT,
            "{key}"
        );
    }
    assert_eq!(fixture.apply(&payload).await.unwrap(), applied);
    assert_eq!(fixture.bytes(), before);
}

#[tokio::test]
async fn legacy_receipts_return_history_without_rewriting_or_reapplying() {
    let modes = [None, Some(json!([])), Some(json!([source("replacement")]))];
    for (committed_index, sources) in modes.iter().enumerate() {
        let fixture = Fixture::new();
        let result = install_legacy_fixture(&fixture, sources.clone());
        let digest = if committed_index < 2 {
            LEGACY_DIGEST
        } else {
            LEGACY_REPLACEMENT_DIGEST
        };
        assert_eq!(legacy_fingerprint(&legacy_payload(sources.clone())), digest);
        let before = fixture.bytes();
        for (retry_index, sources) in modes.iter().enumerate() {
            let retry = legacy_payload(sources.clone());
            if retry_index == committed_index || (retry_index < 2 && committed_index < 2) {
                // Historical receipts irrecoverably collapsed None and Some([]).
                assert_eq!(fixture.apply(&retry).await.unwrap(), result);
            } else {
                assert_eq!(fixture.apply(&retry).await.unwrap_err(), CONFLICT);
            }
            assert_eq!(fixture.bytes(), before);
        }
        let mut changed_proposal = legacy_payload(sources.clone());
        changed_proposal["proposal"]["edits"][0]["content"] = json!("Different content");
        assert_eq!(
            fixture.apply(&changed_proposal).await.unwrap_err(),
            CONFLICT
        );
        let mut changed_host = legacy_payload(sources.clone());
        changed_host["host"] = json!(true);
        assert_eq!(fixture.apply(&changed_host).await.unwrap_err(), CONFLICT);
        assert_eq!(fixture.bytes(), before);
        assert_eq!(fixture.service.store.read().unwrap().memory.revision, 2);
    }
}

#[tokio::test]
async fn restore_keeps_new_source_receipts_when_update_history_is_absent() {
    let modes = [None, Some(json!([])), Some(json!([source("replacement")]))];
    for (committed_index, sources) in modes.iter().enumerate() {
        let fixture = Fixture::new();
        let seed = fixture.seed().await;
        let backup = fixture.service.store.backup(None).unwrap();
        let payload = apply_payload("update", 1, "update", "Updated content", sources.clone());
        fixture.apply(&payload).await.unwrap();
        let receipts = fixture.service.store.read().unwrap().memory.events;
        assert_eq!(fixture.service.store.restore(&backup).await.unwrap(), 3);
        let restored = fixture.service.store.read().unwrap();
        assert_eq!(restored.memory.events, receipts);
        assert_eq!(restored.memory.history.len(), 1);
        assert_eq!(fixture.sources(), json!([source("original")]));
        let before = fixture.bytes();
        for (retry_index, sources) in modes.iter().enumerate() {
            let retry = apply_payload("update", 1, "update", "Updated content", sources.clone());
            let expected = if retry_index == committed_index {
                RESTORED
            } else {
                CONFLICT
            };
            assert_eq!(fixture.apply(&retry).await.unwrap_err(), expected);
            assert_eq!(fixture.bytes(), before);
        }
        let seed_payload = apply_payload(
            "seed",
            0,
            "create",
            "Original content",
            Some(json!([source("original")])),
        );
        assert_eq!(fixture.apply(&seed_payload).await.unwrap(), seed);
        assert_eq!(fixture.bytes(), before);
    }
}

#[tokio::test]
async fn restored_legacy_receipts_cannot_replay_even_without_saved_history() {
    let modes = [None, Some(json!([])), Some(json!([source("replacement")]))];
    for (committed_index, sources) in modes.iter().enumerate() {
        let fixture = Fixture::new();
        let empty =
            serde_json::to_value(empty_document(&fixture.service.store.project.id)).unwrap();
        write_snapshot(&fixture, &empty);
        let backup = fixture.service.store.backup(None).unwrap();
        install_legacy_fixture(&fixture, sources.clone());
        let receipts = fixture.service.store.read().unwrap().memory.events;
        assert_eq!(fixture.service.store.restore(&backup).await.unwrap(), 3);
        let before = fixture.bytes();
        for (retry_index, sources) in modes.iter().enumerate() {
            let expected =
                if retry_index == committed_index || (retry_index < 2 && committed_index < 2) {
                    RESTORED
                } else {
                    CONFLICT
                };
            assert_eq!(
                fixture
                    .apply(&legacy_payload(sources.clone()))
                    .await
                    .unwrap_err(),
                expected
            );
            assert_eq!(fixture.bytes(), before);
        }
        let restored = fixture.service.store.read().unwrap();
        assert!(restored.entries["memory"].is_empty());
        assert!(restored.memory.history.is_empty());
        assert_eq!(restored.memory.events, receipts);
    }
}

#[tokio::test]
async fn malformed_sources_and_proposals_fail_without_erasing_provenance_or_receipts() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let before = fixture.bytes();
    let mut missing_id = source("invalid");
    missing_id.as_object_mut().unwrap().remove("id");
    let mut bad_hash = source("invalid");
    bad_hash["sha256"] = json!("not-a-digest");
    let mut bad_origin = source("invalid");
    bad_origin["origin"] = json!("memory");
    for sources in [
        Value::Null,
        json!(false),
        json!({}),
        json!("not-an-array"),
        json!([null]),
        json!([missing_id]),
        json!([bad_hash]),
        json!([bad_origin]),
        Value::Array(vec![source("too_many"); 201]),
    ] {
        let payload = apply_payload("invalid", 1, "update", "Invalid update", Some(sources));
        assert!(fixture.apply(&payload).await.is_err());
        assert_eq!(fixture.bytes(), before);
    }
    for proposal in [
        Value::Null,
        json!({"edits": [null]}),
        json!({"edits": "bad"}),
    ] {
        let mut payload = apply_payload("invalid", 1, "update", "Invalid update", None);
        payload["proposal"] = proposal;
        assert!(fixture.apply(&payload).await.is_err());
        assert_eq!(fixture.bytes(), before);
    }
    assert_eq!(fixture.sources(), json!([source("original")]));
    assert_eq!(fixture.service.store.read().unwrap().memory.events.len(), 1);
}

#[tokio::test]
async fn rollback_restores_before_sources_instead_of_the_current_source_policy() {
    for refs in [None, Some(json!([])), Some(json!([source("replacement")]))] {
        let fixture = Fixture::new();
        fixture.seed().await;
        let original = fixture.service.store.read().unwrap().entries["memory"]["note"].clone();
        let mut payload = apply_payload("update", 1, "update", "Updated content", refs);
        payload["proposal"]["edits"][0]["metadata"] = json!({"reviewed": true});
        fixture.apply(&payload).await.unwrap();
        let rolled = fixture.service.store.rollback("update", 2).await.unwrap();
        let doc = fixture.service.store.read().unwrap();
        let entry = &doc.entries["memory"]["note"];
        assert_eq!(entry.metadata, original.metadata);
        assert_eq!(entry.content, original.content);
        assert_eq!(entry.created_at, original.created_at);
        assert_eq!(entry.version, 3);
        assert_eq!(doc.memory.revision, 3);
        assert_eq!(rolled.applied_edits[0].after.as_ref(), Some(entry));
    }
}

#[tokio::test]
async fn handoff_retains_its_existing_complete_checkpoint_source_semantics() {
    let fixture = Fixture::new();
    let base = json!({
        "task": "Synthetic task", "state": "Started", "decisions": "Keep fixture local",
        "unresolved": "Nothing", "eventId": "handoff_create", "revision": 0,
        "sources": [source("original")]
    });
    fixture
        .service
        .request("handoff", base.as_object().unwrap(), None, None)
        .await
        .unwrap();
    let mut update = base.clone();
    update["eventId"] = json!("handoff_update");
    update["revision"] = json!(1);
    update["state"] = json!("Checkpoint complete");
    update.as_object_mut().unwrap().remove("sources");
    let applied = fixture
        .service
        .request("handoff", update.as_object().unwrap(), None, None)
        .await
        .unwrap();
    assert_eq!(
        applied["appliedEdits"][0]["before"]["metadata"]["sources"],
        json!([source("original")])
    );
    assert_eq!(
        applied["appliedEdits"][0]["after"]["metadata"]["sources"],
        json!([])
    );
    let before = fixture.bytes();
    assert_eq!(
        fixture
            .service
            .request("handoff", update.as_object().unwrap(), None, None)
            .await
            .unwrap(),
        applied
    );
    assert_eq!(fixture.bytes(), before);
}

#[tokio::test]
async fn omitted_store_sources_keep_import_style_per_edit_evidence_and_legacy_retries() {
    let fixture = Fixture::new();
    let raw = json!({
        "summary": "Import synthetic evidence", "rationale": "Offline fixture",
        "expectedOutcome": "Keep separate citations",
        "edits": [
            {"action": "create", "kind": "memory", "id": "one", "title": "One", "content": "First fact",
                "metadata": {"sources": [source("one")], "sourceIds": ["one"]}},
            {"action": "create", "kind": "memory", "id": "two", "title": "Two", "content": "Second fact",
                "metadata": {"sources": [source("two")], "sourceIds": ["two"]}}
        ]
    });
    let proposal = normalize_refinement_proposal(&raw);
    let options = ApplyOptions {
        event_id: "synthetic_import".to_string(),
        expected_revision: 0,
        ..Default::default()
    };
    let applied = serde_json::to_value(
        fixture
            .service
            .store
            .apply(&proposal, options.clone())
            .await
            .unwrap(),
    )
    .unwrap();
    let document = fixture.service.store.read().unwrap();
    for id in ["one", "two"] {
        assert_eq!(
            document.entries["memory"][id].metadata["sources"],
            json!([source(id)])
        );
        assert_eq!(
            document.entries["memory"][id].metadata["sourceIds"],
            json!([id])
        );
    }
    let before = fixture.bytes();
    assert_eq!(
        serde_json::to_value(
            fixture
                .service
                .store
                .apply(&proposal, options.clone())
                .await
                .unwrap(),
        )
        .unwrap(),
        applied
    );
    assert_eq!(fixture.bytes(), before);

    // Emulate a pre-fix accepted import receipt after a crash before job status was saved.
    let mut legacy = serde_json::to_value(document).unwrap();
    legacy["memory"]["events"]["synthetic_import"] =
        json!(legacy_fingerprint(&json!({"proposal": raw})));
    write_snapshot(&fixture, &legacy);
    let before = fixture.bytes();
    assert_eq!(
        serde_json::to_value(
            fixture
                .service
                .store
                .apply(&proposal, options)
                .await
                .unwrap(),
        )
        .unwrap(),
        applied
    );
    assert_eq!(fixture.bytes(), before);
    assert_eq!(fixture.service.store.read().unwrap().memory.revision, 1);
}

// ---------------------------------------------------------------------------
// Wave-2 fix coverage: import-apply recovery (SB-02), corrupt host identity
// (SB-03), settings diagnostics (SB-08), and recall span truncation (SB-04).
// ---------------------------------------------------------------------------

fn write_session(root: &Path, name: &str, content: &str) -> String {
    std::fs::create_dir_all(root).unwrap();
    let path = root.join(name);
    let line = json!({
        "type": "message", "id": "row_1",
        "message": {"role": "user", "content": content, "timestamp": 1}
    });
    std::fs::write(&path, line.to_string()).unwrap();
    path.to_string_lossy().to_string()
}

fn create_extractor(id: &str, content: &str) -> MemoryExtractor {
    let id = id.to_string();
    let content = content.to_string();
    Arc::new(move |_records: Vec<Evidence>| {
        let id = id.clone();
        let content = content.clone();
        Box::pin(async move {
            Ok(ExtractionResult {
                proposal: normalize_refinement_proposal(&json!({
                    "summary": "Import", "rationale": "Synthetic fixture",
                    "expectedOutcome": "Recover selected knowledge",
                    "edits": [{"action": "create", "kind": "memory", "id": id,
                        "title": "Shared topic", "content": content}]
                })),
                input: 1.0,
                output: 1.0,
            })
        })
    })
}

fn apply_options(event_id: &str, expected_revision: i64) -> ApplyOptions {
    ApplyOptions {
        event_id: event_id.to_string(),
        expected_revision,
        sources: None,
        host: false,
        automatic: false,
        replace_metadata: false,
    }
}

#[tokio::test]
async fn import_apply_recovers_after_a_revision_bump_and_a_competing_import() {
    let root = tempfile::tempdir().unwrap();
    let project = root.path().join("project");
    std::fs::create_dir_all(&project).unwrap();
    let service = MemoryService::new(
        &project.to_string_lossy(),
        &root.path().join("agent").to_string_lossy(),
        None,
    )
    .unwrap();
    let store = service.store.clone();
    let jobs = service.jobs.clone();

    // An unrelated memory seeds the store at revision 1.
    let seed = normalize_refinement_proposal(&json!({
        "summary": "Seed", "rationale": "Synthetic fixture",
        "expectedOutcome": "o",
        "edits": [{"action": "create", "kind": "memory", "id": "seed_note",
            "title": "Seed", "content": "Unrelated seed content"}]
    }));
    store
        .apply(&seed, apply_options("seed_event_1", 0))
        .await
        .unwrap();
    assert_eq!(store.read().unwrap().memory.revision, 1);

    // Two imports whose extractors emit the same content under different ids.
    const SHARED: &str = "Shared deployment checklist for the release";
    let source_a = write_session(&root.path().join("sources-a"), "session.jsonl", "alpha session");
    let source_b = write_session(&root.path().join("sources-b"), "session.jsonl", "beta session");
    let job_a = jobs.prepare(&source_a).await.unwrap();
    let job_b = jobs.prepare(&source_b).await.unwrap();
    assert_ne!(job_a.id, job_b.id);
    jobs.run(&job_a.id, create_extractor("alpha_note", SHARED), None)
        .await
        .unwrap();
    jobs.run(&job_b.id, create_extractor("beta_note", SHARED), None)
        .await
        .unwrap();
    assert_eq!(jobs.get(&job_b.id).unwrap().status, ImportJobStatus::Preview);

    // The first apply of the second import fails on a stale revision after its
    // accepted proposal was built and persisted.
    let stale = jobs.apply(&job_b.id, 0).await.unwrap_err();
    assert_eq!(
        stale,
        "Memory changed; review current revision before applying"
    );
    // The failure must not freeze the persisted proposal: retrying has to be
    // able to re-run the build-time skip filters against current state.
    let persisted = jobs.get(&job_b.id).unwrap();
    assert_eq!(persisted.status, ImportJobStatus::Preview);
    assert!(persisted.accepted_proposal.is_none());

    // The competing import lands the shared content under its own id.
    jobs.apply(&job_a.id, 1).await.unwrap();
    let doc = store.read().unwrap();
    assert_eq!(doc.entries["memory"]["alpha_note"].content, SHARED);
    assert_eq!(doc.memory.revision, 2);

    // Retrying the poisoned import re-reconciles instead of failing forever
    // with "Exact duplicate": the build-time filter skips the landed content.
    let retried = jobs.apply(&job_b.id, 2).await.unwrap();
    assert_eq!(retried.status, ImportJobStatus::Applied);
    let doc = store.read().unwrap();
    assert!(doc.entries["memory"].get("beta_note").is_none());
    assert_eq!(doc.entries["memory"]["alpha_note"].content, SHARED);
    assert_eq!(doc.memory.revision, 3);

    // The store-level duplicate guard still rejects a genuinely duplicate
    // create that reaches it under a different id.
    let duplicate = normalize_refinement_proposal(&json!({
        "summary": "Duplicate", "rationale": "Synthetic fixture",
        "expectedOutcome": "o",
        "edits": [{"action": "create", "kind": "memory", "id": "gamma_note",
            "title": "Gamma", "content": SHARED}]
    }));
    let error = store
        .apply(&duplicate, apply_options("duplicate_event_1", 3))
        .await
        .unwrap_err();
    assert_eq!(error, "Exact duplicate: alpha_note");
}

#[tokio::test]
async fn corrupt_host_id_is_quarantined_and_reminted_without_losing_memories() {
    let root = tempfile::tempdir().unwrap();
    let agent_dir = root.path().join("agent");
    let project_dir = root.path().join("project");
    std::fs::create_dir_all(&project_dir).unwrap();
    let identity = |id: &str| ProjectIdentity {
        id: id.to_string(),
        root: project_dir.to_string_lossy().to_string(),
        aliases: Vec::new(),
    };
    let store = MemoryStore::new(&agent_dir.to_string_lossy(), identity("project_recovery"))
        .expect("initial store");
    let original_host_id = store.host_id.clone();
    let survivor = normalize_refinement_proposal(&json!({
        "summary": "Survivor", "rationale": "Synthetic fixture",
        "expectedOutcome": "o",
        "edits": [{"action": "create", "kind": "memory", "id": "survivor",
            "title": "Survivor", "content": "Memory that must survive host-id recovery"}]
    }));
    store
        .apply(&survivor, apply_options("survivor_event_1", 0))
        .await
        .unwrap();
    drop(store);

    let memory_dir = agent_dir.join("memory");
    let host_path = memory_dir.join("host-id.json");
    let quarantined = || -> Vec<std::fs::DirEntry> {
        std::fs::read_dir(&memory_dir)
            .unwrap()
            .flatten()
            .filter(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with("host-id.corrupt-")
            })
            .collect()
    };

    // A corrupt (unparseable) host identity used to brick every construction.
    let garbage = b"not a valid host identity".to_vec();
    std::fs::write(&host_path, &garbage).unwrap();
    let recovered = MemoryStore::new(&agent_dir.to_string_lossy(), identity("project_recovery"))
        .expect("recovers from garbage bytes");
    assert_ne!(recovered.host_id, original_host_id);
    let backups = quarantined();
    assert_eq!(backups.len(), 1, "the corrupt bytes are preserved aside");
    assert_eq!(std::fs::read(backups[0].path()).unwrap(), garbage);
    let active: Value =
        serde_json::from_slice(&std::fs::read(&host_path).unwrap()).unwrap();
    assert_eq!(active["id"], json!(recovered.host_id));
    assert_eq!(
        recovered.read().unwrap().entries["memory"]["survivor"].content,
        "Memory that must survive host-id recovery"
    );

    // A parseable file with a non-UUID id is corrupt too, and a second
    // corruption quarantines under its own content address.
    let bad_uuid = br#"{"id": "definitely-not-a-uuid"}"#.to_vec();
    std::fs::write(&host_path, &bad_uuid).unwrap();
    let reminted = MemoryStore::new(&agent_dir.to_string_lossy(), identity("project_recovery"))
        .expect("recovers from an invalid identity");
    assert_ne!(reminted.host_id, recovered.host_id);
    let backups = quarantined();
    assert_eq!(backups.len(), 2, "each corruption is quarantined once");
    let contents: Vec<Vec<u8>> = backups
        .iter()
        .map(|entry| std::fs::read(entry.path()).unwrap())
        .collect();
    assert!(contents.contains(&garbage));
    assert!(contents.contains(&bad_uuid));

    // A clean reconstruction reuses the re-minted identity and stays stable.
    let stable = MemoryStore::new(&agent_dir.to_string_lossy(), identity("project_recovery"))
        .unwrap();
    assert_eq!(stable.host_id, reminted.host_id);
    assert_eq!(quarantined().len(), 2);
    assert_eq!(
        stable.read().unwrap().entries["memory"]["survivor"].content,
        "Memory that must survive host-id recovery"
    );
}

#[test]
fn invalid_memory_settings_fall_back_to_defaults_and_are_logged() {
    let captured: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
    let sink_target = captured.clone();
    pi_ai::log::set_log_sink(Some(Box::new(move |entry| {
        sink_target.lock().unwrap().push(entry.clone());
    })));

    let root = tempfile::tempdir().unwrap();
    let agent_dir = root.path().join("agent");
    let project_dir = root.path().join("project");
    std::fs::create_dir_all(&project_dir).unwrap();
    let store = MemoryStore::new(
        &agent_dir.to_string_lossy(),
        ProjectIdentity {
            id: "project_settings".to_string(),
            root: project_dir.to_string_lossy().to_string(),
            aliases: Vec::new(),
        },
    )
    .unwrap();

    // Global file: valid JSON, out-of-range memory value.
    std::fs::write(
        &agent_dir.join("settings.json"),
        r#"{"memory": {"maxRecallEntries": 9999}}"#,
    )
    .unwrap();
    // Local file: unparseable JSON.
    std::fs::create_dir_all(&store.dir).unwrap();
    std::fs::write(Path::new(&store.dir).join("settings.json"), "{oops").unwrap();

    // The load still succeeds with the affected keys at their defaults.
    let settings = store.settings();
    let defaults = default_memory_settings();
    assert_eq!(settings.max_recall_entries, defaults.max_recall_entries);
    assert_eq!(settings.max_recall_chars, defaults.max_recall_chars);
    assert_eq!(settings.recall, defaults.recall);

    // Both silent fallbacks are recorded as diagnostics.
    let entries = captured.lock().unwrap().clone();
    let messages: Vec<String> = entries
        .iter()
        .filter(|entry| entry["component"] == json!("coding-agent.memory-store"))
        .map(|entry| entry["msg"].as_str().unwrap_or_default().to_string())
        .collect();
    assert!(
        messages
            .iter()
            .any(|msg| msg.contains("invalid content of the global memory settings")),
        "global invalid-value fallback is logged: {messages:?}"
    );
    assert!(
        messages
            .iter()
            .any(|msg| msg.contains("unreadable file of the project memory settings")),
        "local unparseable fallback is logged: {messages:?}"
    );
    let details: Vec<&Value> = entries
        .iter()
        .filter(|entry| entry["component"] == json!("coding-agent.memory-store"))
        .collect();
    assert!(details
        .iter()
        .any(|entry| entry["error"].as_str().unwrap_or_default().contains("Invalid maxRecallEntries")));

    pi_ai::log::set_log_sink(None);
}

#[test]
fn recall_source_span_truncation_is_marked() {
    let settings = default_memory_settings();
    let hit = |span: &str| {
        let entry: HarnessEntry = serde_json::from_value(json!({
            "id": "move", "kind": "memory", "title": "Relocation",
            "content": "We relocated to a new place.", "path": "general",
            "reference": {}, "arguments": {},
            "metadata": {"sourceSpan": span},
            "source": "refine", "created_at": "2026-01-01T00:00:00.000Z",
            "updated_at": "2026-01-01T00:00:00.000Z", "version": 1
        }))
        .unwrap();
        MemoryHit {
            id: "move".to_string(),
            scope: MemoryScope::Global,
            entry,
            score: 1.0,
            matched: vec!["relocated".to_string()],
            freshness: MemoryFreshness::Current,
            sources: Vec::new(),
        }
    };

    // A 400-char stored span renders 200 chars plus an explicit marker.
    let long = "k".repeat(400);
    let recall = recall_memory(std::slice::from_ref(&hit(&long)), &settings, None);
    let start = recall.text.find("Source: ").expect("rendered source span");
    let rendered = &recall.text[start..];
    assert_eq!(
        rendered,
        &format!(
            "Source: {} [truncated; inspect source]",
            "k".repeat(200)
        ),
        "the cut is exactly 200 chars and carries the marker"
    );

    // A span that fits is rendered verbatim without any marker.
    let short = "short verbatim turn";
    let recall = recall_memory(std::slice::from_ref(&hit(short)), &settings, None);
    let start = recall.text.find("Source: ").expect("rendered source span");
    let rendered = &recall.text[start..];
    assert_eq!(rendered, "Source: short verbatim turn");
    assert!(!recall.text.contains("[truncated; inspect source]"));
}

// ---------------------------------------------------------------------------
// Storage integrity: a failed authoritative write must never destroy data
// ---------------------------------------------------------------------------

/// Windows only: the authoritative write replaces the destination with a
/// rename, and NTFS refuses to replace a destination that carries the
/// read-only attribute. On Unix the rename only needs a writable parent
/// directory, so this injection cannot fail the write there; the portable
/// twin below covers the rejection paths instead.
#[cfg(windows)]
fn make_readonly(path: &str, readonly: bool) {
    let mut permissions = std::fs::metadata(path).expect("store metadata").permissions();
    permissions.set_readonly(readonly);
    std::fs::set_permissions(path, permissions).expect("store permissions");
}

#[cfg(windows)]
#[tokio::test]
async fn failed_authoritative_write_keeps_the_previous_store_intact() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let before = fixture.bytes();
    let revision = fixture.service.store.read().unwrap().memory.revision;
    assert_eq!(revision, 1);

    // Deny the atomic rename its destination: the write must fail closed.
    let store_path = fixture.service.store.path.clone();
    make_readonly(&store_path, true);
    let error = fixture
        .apply(&apply_payload(
            "blocked_write",
            revision,
            "update",
            "Changed content",
            Some(json!([source("updated")])),
        ))
        .await
        .expect_err("a read-only store cannot be replaced");
    assert!(!error.is_empty(), "the failure is reported, not swallowed");
    make_readonly(&store_path, false);

    // The previous valid state is still the authoritative one, byte for byte.
    assert_eq!(fixture.bytes(), before);
    let document = fixture.service.store.read().expect("store still validates");
    assert_eq!(document.memory.revision, revision);
    assert_eq!(document.entries["memory"]["note"].content, "Original content");
    assert!(!document.memory.events.contains_key("blocked_write"));

    // The failed operation left nothing wedged: the same edit applies after
    // the destination is writable again.
    let applied = fixture
        .apply(&apply_payload(
            "blocked_write",
            revision,
            "update",
            "Changed content",
            Some(json!([source("updated")])),
        ))
        .await
        .expect("retry applies");
    assert_eq!(applied["appliedEdits"][0]["applied"], json!(true));
    let document = fixture.service.store.read().expect("store validates");
    assert_eq!(document.memory.revision, revision + 1);
    assert_eq!(document.entries["memory"]["note"].content, "Changed content");
}

/// Portable twin of the read-only injection: an apply that is rejected before
/// the write must leave the authoritative bytes untouched, and the next valid
/// apply must still succeed at the same revision.
#[tokio::test]
async fn rejected_apply_leaves_the_authoritative_bytes_untouched() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let before = fixture.bytes();
    let revision = fixture.service.store.read().unwrap().memory.revision;
    assert_eq!(revision, 1, "the seed apply is the only committed revision");

    // A stale revision is rejected before anything is written.
    let stale = fixture
        .apply(&apply_payload(
            "stale_write",
            revision - 1,
            "update",
            "Changed content",
            Some(json!([source("updated")])),
        ))
        .await
        .expect_err("a stale revision is rejected");
    assert_eq!(stale, "Memory changed; review current revision before applying");
    assert_eq!(fixture.bytes(), before, "a rejected apply must not touch the store");

    // An invalid memory id is rejected before anything is written.
    let mut invalid = apply_payload("invalid_id", revision, "update", "Other content", None);
    invalid["proposal"]["edits"][0]["id"] = json!("bad id with spaces");
    let error = fixture.apply(&invalid).await.expect_err("an invalid id is rejected");
    assert_eq!(error, "Invalid memory ID");
    assert_eq!(fixture.bytes(), before, "a rejected edit must not touch the store");

    // Nothing was wedged: the store still applies a valid edit at the same
    // revision, and the rejected event ids left no receipts behind.
    let document = fixture.service.store.read().expect("store still validates");
    assert_eq!(document.memory.revision, revision);
    assert!(!document.memory.events.contains_key("stale_write"));
    assert!(!document.memory.events.contains_key("invalid_id"));
    let applied = fixture
        .apply(&apply_payload(
            "valid_write",
            revision,
            "update",
            "Changed content",
            Some(json!([source("updated")])),
        ))
        .await
        .expect("a valid apply still works");
    assert_eq!(applied["appliedEdits"][0]["applied"], json!(true));
    assert_eq!(
        fixture.service.store.read().unwrap().memory.revision,
        revision + 1
    );
}
