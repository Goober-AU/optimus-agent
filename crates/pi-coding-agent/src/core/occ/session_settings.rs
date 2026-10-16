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

/// Missing preferences are off. A child's nearest explicit preference wins.
/// Only bounded headers are read, never the chat transcript; no process cache
/// means existing workers see the next toggle without being restarted.
pub fn load(session_file: &str) -> Result<bool, String> {
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
                return Ok(value.occ2_enabled);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.to_string()),
        }
        let reader = match File::open(&file) {
            Ok(reader) => reader,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error.to_string()),
        };
        let mut header = Vec::new();
        BufReader::new(reader.take(65537)).read_until(b'\n', &mut header).map_err(|e| e.to_string())?;
        if header.len() > 65536 { return Err("OCC session header is too large".into()); }
        let header: serde_json::Value = serde_json::from_slice(&header).map_err(|e| e.to_string())?;
        // Forks can have parentSession while retaining depth zero. They are
        // independent chats, not subagents, so they never inherit this toggle.
        if header["rlmDepth"].as_u64().unwrap_or(0) == 0 { return Ok(false); }
        let Some(parent) = header["parentSession"].as_str().filter(|s| !s.is_empty()) else { return Ok(false); };
        let parent = Path::new(parent);
        file = if parent.is_absolute() { parent.to_path_buf() }
            else { file.parent().unwrap_or_else(|| Path::new(".")).join(parent) };
    }
    Err("OCC session ancestry exceeds the supported depth".into())
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
        let a = session(root.path(), "a", None, 0);
        let b = session(root.path(), "b", None, 0);
        let child = session(root.path(), "child", Some(&a), 1);
        let nested = session(root.path(), "nested", Some(&child), 2);
        let fork = session(root.path(), "fork", Some(&a), 0);
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
        let new_chat = session(root.path(), "new", None, 0);
        assert!(!load(&new_chat).unwrap());
    }

    #[test]
    fn occ_sessions_bound_reads_fail_closed_and_support_empty_new_chats() {
        let root = tempfile::tempdir().unwrap();
        let a = session(root.path(), "a", Some("b.jsonl"), 1);
        let b = session(root.path(), "b", Some("a.jsonl"), 2);
        assert!(load(&a).is_err());
        assert!(!enabled(Some(&a)));
        save(&b, true).unwrap();
        assert!(load(&a).unwrap());
        fs::write(preference_path(&b), "invalid").unwrap();
        assert!(!enabled(Some(&a)));
        assert!(load(&a).is_err());
        fs::write(preference_path(&b), "x".repeat(4097)).unwrap();
        assert!(load(&b).is_err());
        let empty = root.path().join("new.jsonl").to_str().unwrap().to_string();
        assert!(!load(&empty).unwrap());
        save(&empty, true).unwrap();
        assert!(load(&empty).unwrap());
        assert!(!enabled(None));
    }
}
