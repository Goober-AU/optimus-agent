//! Torn-head session recovery (audit S1/G1) and fork write-failure surfacing
//! (audit S4/G6), exercised through the real `SessionManager` production
//! entry points (open/append_message/flush_now/fork_from). No provider or
//! network. All writable state under OPTIMUS_PARITY_STATE_ROOT (V00
//! isolation).
//!
//! Oracles:
//! - A session file whose FIRST line is torn (a crash mid-header-write during
//!   `fork_from`, a partial disk write) parses to zero entries because the
//!   loader skips malformed lines and `finalize_loaded_entries` drops
//!   everything when entries[0] is not a valid header. The old contract reset
//!   the path in place with no backup; the fixed contract must quarantine the
//!   damaged bytes beside the fresh session (`.corrupt-<ts>`) before the
//!   rewrite, and the resulting session must be usable.
//! - `fork_from` writes its target line by line; every write must be checked
//!   so a full disk or permission error fails the fork instead of leaving a
//!   truncated file that a later open silently "repairs".

use std::path::{Path, PathBuf};

use pi_agent_core::types::AgentMessage;
use pi_ai::types::{Message, UserContent, UserMessage};
use pi_coding_agent::core::session_manager::{load_entries_from_file, SessionManager};
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// V00 harness (transcript_t06 pattern)
// ---------------------------------------------------------------------------

fn state_root() -> PathBuf {
    static ROOT: std::sync::OnceLock<tempfile::TempDir> = std::sync::OnceLock::new();
    let root = std::env::var_os("OPTIMUS_PARITY_STATE_ROOT")
        .map(|raw| {
            assert!(!raw.is_empty(), "OPTIMUS_PARITY_STATE_ROOT must not be empty");
            std::path::absolute(raw).expect("absolute state root")
        })
        .unwrap_or_else(|| {
            ROOT.get_or_init(|| tempfile::Builder::new().prefix("optimus-torn-head-").tempdir().expect("private state root"))
                .path().to_path_buf()
        });
    let lowered = root.to_string_lossy().to_lowercase();
    assert!(!lowered.contains(".prime"), "state root must not touch .prime");
    std::fs::create_dir_all(&root).expect("create state root");
    assert!(!std::fs::canonicalize(&root).expect("canonical state root").to_string_lossy().to_lowercase().contains(".prime"), "state root must not resolve into .prime");
    root
}

fn case_dir(tag: &str) -> PathBuf {
    let dir = state_root()
        .join(tag)
        .join(format!("{}-{}", std::process::id(), unique_tag()));
    std::fs::create_dir_all(&dir).expect("create case dir");
    dir
}

fn unique_tag() -> String {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};
    static TAG_SEQ: AtomicUsize = AtomicUsize::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    format!("{}-{}", nanos, TAG_SEQ.fetch_add(1, Ordering::Relaxed))
}

fn session_header(id: &str, timestamp: &str, cwd: &str) -> Value {
    json!({
        "type": "session",
        "version": 3,
        "id": id,
        "timestamp": timestamp,
        "cwd": cwd,
        "rlmDepth": 0,
    })
}

fn message_entry(id: &str, parent: Value, timestamp: &str, text: &str) -> Value {
    json!({
        "type": "message",
        "id": id,
        "parentId": parent,
        "timestamp": timestamp,
        "message": {"role": "user", "content": text, "timestamp": 0},
    })
}

fn user_message(text: &str) -> AgentMessage {
    AgentMessage::Message(Message::User(UserMessage::new(
        UserContent::Text(text.to_string()),
        1,
    )))
}

/// Quarantine sidecar files produced for `path` (`.corrupt-<ts>[-<n>]`).
fn quarantine_files(path: &Path) -> Vec<PathBuf> {
    let prefix = format!("{}.corrupt-", path.file_name().expect("file name").to_string_lossy());
    let mut found: Vec<PathBuf> = std::fs::read_dir(path.parent().expect("parent dir"))
        .expect("read case dir")
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|candidate| {
            candidate.file_name().expect("file name").to_string_lossy().starts_with(&prefix)
        })
        .collect();
    found.sort();
    found
}

// ---------------------------------------------------------------------------
// S1: torn head -> quarantine, then a usable fresh session
// ---------------------------------------------------------------------------

#[test]
fn torn_first_line_quarantines_the_damaged_bytes_and_opens_a_usable_session() {
    let dir = case_dir("torn-head");
    let file = dir.join("torn.jsonl");
    let original = format!(
        "{{\"type\":\"session\",\"version\":3,\"id\":\"torn-\n{}\n{}\n",
        serde_json::to_string(&message_entry(
            "m1",
            Value::String("torn-session".into()),
            "2026-10-17T00:00:00.000Z",
            "first user turn"
        )).unwrap(),
        serde_json::to_string(&message_entry(
            "m2",
            Value::String("m1".into()),
            "2026-10-17T00:00:01.000Z",
            "second user turn"
        )).unwrap(),
    );
    std::fs::write(&file, original.as_bytes()).expect("write torn fixture");

    // Reproduce the trigger: a torn FIRST line leaves zero loadable entries
    // because the loader skips the malformed line and the remaining entries
    // have no session header.
    assert!(
        load_entries_from_file(&file.to_string_lossy()).is_empty(),
        "the torn-head fixture must fail to load before the fix is exercised"
    );
    assert!(quarantine_files(&file).is_empty());

    let mut manager = SessionManager::open(&file.to_string_lossy(), None, None)
        .expect("open with a torn head must succeed via reset");

    // The damaged bytes survive verbatim in exactly one quarantine sidecar.
    let quarantines = quarantine_files(&file);
    assert_eq!(quarantines.len(), 1, "one quarantine sidecar beside the reset session");
    let quarantined = std::fs::read(&quarantines[0]).expect("read quarantine");
    assert_eq!(quarantined, original.into_bytes(), "the quarantine preserves the damaged bytes exactly");

    // The original path now holds a fresh, valid, single-header session.
    let entries = load_entries_from_file(&file.to_string_lossy());
    assert_eq!(entries.len(), 1, "the reset file holds only the fresh header");
    assert_eq!(entries[0].get("type").and_then(Value::as_str), Some("session"));
    assert_ne!(
        entries[0].get("id").and_then(Value::as_str),
        Some("torn-session"),
        "the fresh header must not reuse the damaged session id"
    );

    // The session is usable: appends persist and survive a reopen.
    manager
        .append_message(user_message("after the recovery"))
        .expect("append after recovery");
    manager.flush_now().expect("flush after recovery");
    let reopened = SessionManager::open(&file.to_string_lossy(), None, None)
        .expect("reopen the recovered session");
    let reloaded = load_entries_from_file(&file.to_string_lossy());
    assert_eq!(reloaded.len(), 2, "header plus the appended message");
    assert_eq!(
        reopened.get_entries().len(),
        2,
        "the reopened manager sees the recovered transcript"
    );

    // A second open of the now-valid file must not quarantine again.
    drop(reopened);
    assert_eq!(quarantine_files(&file).len(), 1, "no quarantine on a valid reopen");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn empty_and_whitespace_files_reset_without_a_false_quarantine() {
    let dir = case_dir("empty-head");

    // A zero-byte file has nothing to preserve: reset in place, no sidecar.
    let empty = dir.join("empty.jsonl");
    std::fs::write(&empty, b"").expect("write empty fixture");
    let manager = SessionManager::open(&empty.to_string_lossy(), None, None)
        .expect("open an empty session file");
    assert!(quarantine_files(&empty).is_empty(), "an empty file is not quarantined");
    let entries = load_entries_from_file(&empty.to_string_lossy());
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].get("type").and_then(Value::as_str), Some("session"));
    drop(manager);

    // A whitespace-only file is non-empty damage: its bytes are preserved.
    let blank = dir.join("blank.jsonl");
    std::fs::write(&blank, b"\n\n").expect("write blank fixture");
    let manager = SessionManager::open(&blank.to_string_lossy(), None, None)
        .expect("open a whitespace-only session file");
    let quarantines = quarantine_files(&blank);
    assert_eq!(quarantines.len(), 1, "non-empty unloadable bytes are quarantined");
    assert_eq!(std::fs::read(&quarantines[0]).unwrap(), b"\n\n".to_vec());
    let entries = load_entries_from_file(&blank.to_string_lossy());
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].get("type").and_then(Value::as_str), Some("session"));
    drop(manager);
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// S4: fork_from write failures surface; forked content is complete
// ---------------------------------------------------------------------------

fn write_valid_session(path: &Path) {
    let body = [
        serde_json::to_string(&session_header("source-session", "2026-10-17T00:00:00.000Z", "/work")).unwrap(),
        serde_json::to_string(&message_entry("m1", Value::String("source-session".into()), "2026-10-17T00:00:01.000Z", "one")).unwrap(),
        serde_json::to_string(&message_entry("m2", Value::String("m1".into()), "2026-10-17T00:00:02.000Z", "two")).unwrap(),
        serde_json::to_string(&message_entry("m3", Value::String("m2".into()), "2026-10-17T00:00:03.000Z", "three")).unwrap(),
    ]
    .join("\n");
    std::fs::write(path, format!("{body}\n")).expect("write source fixture");
}

#[test]
fn fork_from_copies_every_source_entry_under_a_fresh_header() {
    let dir = case_dir("fork-copy");
    let source = dir.join("source.jsonl");
    write_valid_session(&source);

    let fork_dir = dir.join("forks");
    std::fs::create_dir_all(&fork_dir).expect("create fork dir");
    let fork = SessionManager::fork_from(
        &source.to_string_lossy(),
        "/work",
        Some(&fork_dir.to_string_lossy()),
    )
    .expect("fork_from succeeds");
    let fork_file = PathBuf::from(fork.get_session_file().expect("fork target file"));
    assert!(fork_file.starts_with(&fork_dir), "the fork lands in the requested dir: {fork_file:?}");

    let source_entries = load_entries_from_file(&source.to_string_lossy());
    let fork_entries = load_entries_from_file(&fork_file.to_string_lossy());
    assert_eq!(fork_entries.len(), source_entries.len(), "header plus every source entry");
    assert_eq!(fork_entries[0].get("type").and_then(Value::as_str), Some("session"));
    assert_ne!(
        fork_entries[0].get("id").and_then(Value::as_str),
        Some("source-session"),
        "the fork header is a fresh session"
    );
    assert_eq!(
        fork_entries[0].get("parentSession").and_then(Value::as_str),
        Some(source.to_string_lossy().as_ref()),
        "the fork header links back to the source"
    );
    // Every non-header source entry is copied byte-for-byte.
    for (index, entry) in source_entries.iter().skip(1).enumerate() {
        assert_eq!(
            fork_entries[index + 1],
            *entry,
            "forked entry {} must equal its source entry",
            index + 1
        );
    }
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn fork_from_surfaces_target_creation_failures_and_leaves_the_source_intact() {
    let dir = case_dir("fork-fail");
    let source = dir.join("source.jsonl");
    write_valid_session(&source);
    let before = std::fs::read(&source).expect("read source before");

    // A regular file where the fork directory should be: the fork target
    // cannot be created and the failure must reach the caller.
    let not_a_dir = dir.join("not-a-dir");
    std::fs::write(&not_a_dir, b"file, not a directory").expect("write blocker");
    let error = match SessionManager::fork_from(
        &source.to_string_lossy(),
        "/work",
        Some(&not_a_dir.to_string_lossy()),
    ) {
        Err(error) => error,
        Ok(_) => panic!("fork_from must fail when the target cannot be created"),
    };
    assert!(!error.is_empty(), "the failure carries a message");
    assert_eq!(std::fs::read(&source).unwrap(), before, "the source is untouched");

    // An unloadable source is refused before any target is touched.
    let torn = dir.join("torn.jsonl");
    std::fs::write(&torn, b"{\"type\":\"sess\n").expect("write torn source");
    let error = match SessionManager::fork_from(
        &torn.to_string_lossy(),
        "/work",
        Some(&dir.join("forks").to_string_lossy()),
    ) {
        Err(error) => error,
        Ok(_) => panic!("fork_from must refuse an unloadable source"),
    };
    assert!(error.contains("Cannot fork"), "the refusal explains itself: {error}");
    let _ = std::fs::remove_dir_all(&dir);
}
