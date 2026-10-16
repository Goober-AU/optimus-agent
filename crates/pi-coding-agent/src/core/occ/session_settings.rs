//! Session-local OCC preferences. Subagents inherit dynamically; independent chats do not.

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use serde::{Deserialize, Serialize};

const SCHEMA: &str = "optimus.occ-session.v1";

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Preference {
    schema: String,
    occ2_enabled: bool,
}

pub fn preference_path(session_file: &str) -> PathBuf {
    PathBuf::from(crate::core::session_manager::get_session_artifact_path_for_file(session_file, None)).join("occ.json")
}

/// A chat's resolved preference with provenance: which chat's `occ.json`
/// decided (`None` for the default), so daemon-owned reads can report whether
/// the value was inherited from a subagent ancestry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Resolution {
    pub occ2_enabled: bool,
    pub source_session_id: Option<String>,
}

impl Default for Resolution {
    fn default() -> Self {
        Resolution { occ2_enabled: false, source_session_id: None }
    }
}

fn session_id_of(session_file: &str) -> String {
    // Mirrors the id derivation in `get_session_artifact_path_for_file`
    // (session_manager.rs): basename minus the `.jsonl` suffix.
    let name = Path::new(session_file)
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_default();
    name.strip_suffix(".jsonl").unwrap_or(&name).to_string()
}

/// Missing preferences are off. A child's nearest explicit preference wins.
/// Only bounded headers are read, never the chat transcript; no process cache
/// means existing workers see the next toggle without being restarted.
pub fn load(session_file: &str) -> Result<bool, String> {
    resolve(session_file).map(|resolution| resolution.occ2_enabled)
}

/// `load` with provenance for the daemon-owned `occ_get_preferences`
/// response: the effective value plus the chat whose preference decided.
pub fn resolve(session_file: &str) -> Result<Resolution, String> {
    let mut file = PathBuf::from(session_file);
    let mut visited = BTreeSet::new();
    for _ in 0..64 {
        if !visited.insert(file.clone()) { return Err("OCC session ancestry contains a cycle".into()); }
        let path = preference_path(&file.to_string_lossy());
        match File::open(path) {
            Ok(reader) => {
                let mut bytes = Vec::new();
                reader.take(4097).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
                if bytes.len() > 4096 { return Err("OCC session preference is too large".into()); }
                let value: Preference = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                if value.schema != SCHEMA { return Err("Unsupported OCC session preference schema".into()); }
                return Ok(Resolution {
                    occ2_enabled: value.occ2_enabled,
                    source_session_id: Some(session_id_of(&file.to_string_lossy())),
                });
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.to_string()),
        }
        let reader = match File::open(&file) {
            Ok(reader) => reader,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Resolution::default()),
            Err(error) => return Err(error.to_string()),
        };
        let mut header = Vec::new();
        BufReader::new(reader.take(65537)).read_until(b'\n', &mut header).map_err(|e| e.to_string())?;
        if header.len() > 65536 { return Err("OCC session header is too large".into()); }
        let header: serde_json::Value = serde_json::from_slice(&header).map_err(|e| e.to_string())?;
        // Forks can have parentSession while retaining depth zero. They are
        // independent chats, not subagents, so they never inherit this toggle.
        if header["rlmDepth"].as_u64().unwrap_or(0) == 0 { return Ok(Resolution::default()); }
        let Some(parent) = header["parentSession"].as_str().filter(|s| !s.is_empty()) else { return Ok(Resolution::default()); };
        let parent = Path::new(parent);
        file = if parent.is_absolute() { parent.to_path_buf() }
            else { file.parent().unwrap_or_else(|| Path::new(".")).join(parent) };
    }
    Err("OCC session ancestry exceeds the supported depth".into())
}

/// The `occ_get_preferences` response payload, built where the preference
/// lives so the daemon and an in-process host report identical shapes:
/// `occ2Enabled`, `inherited` (decided by an ancestor chat) and
/// `sourceSessionId` (the deciding chat, or null for the default).
pub fn preferences(session_file: &str) -> Result<serde_json::Value, String> {
    let resolution = resolve(session_file)?;
    let inherited = resolution
        .source_session_id
        .as_deref()
        .is_some_and(|source| source != session_id_of(session_file));
    Ok(serde_json::json!({
        "occ2Enabled": resolution.occ2_enabled,
        "inherited": inherited,
        "sourceSessionId": resolution.source_session_id,
    }))
}

/// The `occ_set_preferences` response payload after persisting `enabled`.
/// The daemon-authoritative echo comes from a fresh resolve, so a client can
/// never report a value the storage did not accept.
pub fn set_preferences(session_file: &str, enabled: bool) -> Result<serde_json::Value, String> {
    save(session_file, enabled)?;
    let resolution = resolve(session_file)?;
    Ok(serde_json::json!({
        "occ2Enabled": resolution.occ2_enabled,
        "applied": resolution.occ2_enabled == enabled,
        "scope": "this chat",
        "effectiveForNewMessages": true,
        "sourceSessionId": resolution.source_session_id,
    }))
}
/// Runtime sends fail closed; interactive commands surface load errors.
pub fn enabled(session_file: Option<&str>) -> bool {
    session_file.and_then(|file| load(file).ok()).unwrap_or(false)
}

pub fn save(session_file: &str, enabled: bool) -> Result<(), String> {
    if session_file.is_empty() { return Err("This chat has no saved session identity".into()); }
    let path = preference_path(session_file);
    let parent = path.parent().ok_or("Invalid OCC session preference path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let value = Preference { schema: SCHEMA.into(), occ2_enabled: enabled };
    let mut pending = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    serde_json::to_writer(pending.as_file_mut(), &value).map_err(|e| e.to_string())?;
    pending.write_all(b"\n").map_err(|e| e.to_string())?;
    pending.as_file().sync_all().map_err(|e| e.to_string())?;
    pending.persist(path).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn session(root: &Path, id: &str, parent: Option<&str>, depth: usize) -> String {
        let path = root.join(format!("{id}.jsonl"));
        fs::write(&path, format!("{}\n", json!({"type":"session","id":id,"rlmDepth":depth,"parentSession":parent}))).unwrap();
        path.to_str().unwrap().to_string()
    }

    #[test]
    fn occ_sessions_isolate_chats_resume_and_dynamically_inherit_for_existing_children() {
        let root = tempfile::tempdir().unwrap();
        let sessions = root.path().join("sessions");
        fs::create_dir(&sessions).unwrap();
        let a = session(&sessions, "a", None, 0);
        let b = session(&sessions, "b", None, 0);
        let child = session(&sessions, "child", Some(&a), 1);
        let nested = session(&sessions, "nested", Some(&child), 2);
        let fork = session(&sessions, "fork", Some(&a), 0);
        for file in [&a, &b, &child, &nested, &fork] { assert!(!load(file).unwrap()); }
        save(&a, true).unwrap();
        assert!(load(&a).unwrap()); // reopening reads the saved preference
        assert!(!load(&b).unwrap());
        assert!(!load(&fork).unwrap());
        assert!(load(&child).unwrap());
        assert!(load(&nested).unwrap());
        save(&child, false).unwrap();
        assert!(load(&a).unwrap());
        assert!(!load(&child).unwrap());
        assert!(!load(&nested).unwrap());
        save(&b, true).unwrap();
        save(&a, false).unwrap();
        assert!(load(&b).unwrap());
        assert!(!load(&a).unwrap());
        let new_chat = session(&sessions, "new", None, 0);
        assert!(!load(&new_chat).unwrap());
        // Removing a child override re-inherits the parent, dynamically.
        assert!(!load(&child).unwrap());
        fs::remove_file(preference_path(&child)).unwrap();
        assert!(!load(&child).unwrap());
        save(&a, true).unwrap();
        assert!(load(&child).unwrap());
        assert!(load(&nested).unwrap());
    }

    #[test]
    fn occ_session_resolution_reports_provenance_and_round_trips() {
        let root = tempfile::tempdir().unwrap();
        let sessions = root.path().join("sessions");
        fs::create_dir(&sessions).unwrap();
        let a = session(&sessions, "a", None, 0);
        let child = session(&sessions, "child", Some("a.jsonl"), 1);
        // Default: off, no source.
        let resolution = resolve(&a).unwrap();
        assert_eq!(resolution, Resolution::default());
        assert_eq!(preferences(&a).unwrap(), json!({"occ2Enabled": false, "inherited": false, "sourceSessionId": null}));
        // The chat that owns the toggle is not inherited.
        save(&a, true).unwrap();
        let resolution = resolve(&a).unwrap();
        assert_eq!(resolution.occ2_enabled, true);
        assert_eq!(resolution.source_session_id.as_deref(), Some("a"));
        assert_eq!(preferences(&a).unwrap(), json!({"occ2Enabled": true, "inherited": false, "sourceSessionId": "a"}));
        // A subagent reports the ancestor as its source.
        assert_eq!(preferences(&child).unwrap(), json!({"occ2Enabled": true, "inherited": true, "sourceSessionId": "a"}));
        // The set response echoes the daemon-authoritative value.
        let set = set_preferences(&a, false).unwrap();
        assert_eq!(set["occ2Enabled"], json!(false));
        assert_eq!(set["applied"], json!(true));
        assert_eq!(set["scope"], json!("this chat"));
        assert_eq!(set["effectiveForNewMessages"], json!(true));
        assert_eq!(set["sourceSessionId"], json!("a"));
        assert!(!load(&a).unwrap());
        // A child override becomes the child's own source for its subtree.
        set_preferences(&child, true).unwrap();
        assert_eq!(preferences(&child).unwrap()["sourceSessionId"], json!("child"));
        // Read failures propagate instead of degrading to a silent value.
        fs::write(preference_path(&a), "not json").unwrap();
        assert!(preferences(&a).is_err());
        assert!(load(&a).is_err());
        // A set heals corrupt storage: it overwrites the bad file atomically
        // and echoes the stored value, never a silent default (the live
        // daemon test pins the same heal-on-set semantics).
        let healed = set_preferences(&a, true).unwrap();
        assert_eq!(healed["occ2Enabled"], json!(true));
        assert_eq!(healed["applied"], json!(true));
        assert!(load(&a).unwrap());
        // A write that cannot land fails loudly: the preference path is a
        // directory, so the atomic rename in `save` cannot complete.
        fs::remove_file(preference_path(&a)).unwrap();
        fs::create_dir(preference_path(&a)).unwrap();
        assert!(set_preferences(&a, false).is_err());
        fs::remove_dir(preference_path(&a)).unwrap();
        fs::write(preference_path(&a), "x".repeat(4097)).unwrap();
        assert!(preferences(&a).is_err());
    }

    #[test]
    fn occ_sessions_bound_reads_fail_closed_and_support_empty_new_chats() {
        let root = tempfile::tempdir().unwrap();
        let sessions = root.path().join("sessions");
        fs::create_dir(&sessions).unwrap();
        let a = session(&sessions, "a", Some("b.jsonl"), 1);
        let b = session(&sessions, "b", Some("a.jsonl"), 2);
        assert!(load(&a).is_err());
        assert!(!enabled(Some(&a)));
        save(&b, true).unwrap();
        assert!(load(&a).unwrap());
        fs::write(preference_path(&b), "invalid").unwrap();
        assert!(!enabled(Some(&a)));
        assert!(load(&a).is_err());
        fs::write(preference_path(&b), "x".repeat(4097)).unwrap();
        assert!(load(&b).is_err());
        let empty = sessions.join("new.jsonl").to_str().unwrap().to_string();
        assert!(!load(&empty).unwrap());
        save(&empty, true).unwrap();
        assert!(load(&empty).unwrap());
        assert!(!enabled(None));
    }
}
