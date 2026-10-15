//! Private offline gate: count native Original import chunks; never extract or apply.
use std::{fs, io::Write, path::{Path, PathBuf}};
use pi_coding_agent::core::memory::{
    evidence::hash, jobs::{ImportJobStatus, MemoryJobs},
    project::ProjectIdentity, store::MemoryStore,
};
use serde_json::{json, Value};

type Result<T> = std::result::Result<T, &'static str>;
const CHUNK_CHARS: i64 = 15000;
const PADDING: usize = 128;

fn fresh_path(path: &Path) -> Result<PathBuf> {
    if !path.is_absolute() || !matches!(fs::symlink_metadata(path), Err(e) if e.kind() == std::io::ErrorKind::NotFound) {
        return Err("scratch/report must be absolute and absent");
    }
    let parent = fs::canonicalize(path.parent().ok_or("missing output parent")?)
        .map_err(|_| "output parent must exist")?;
    Ok(parent.join(path.file_name().ok_or("missing output filename")?))
}

fn digest(path: &Path) -> Result<String> {
    fs::read_to_string(path).map(|text| hash(&text)).map_err(|_| "unreadable UTF-8 session")
}

fn prepare(sessions: &Path, scratch: &Path, report: &Path) -> Result<bool> {
    if !sessions.is_absolute() { return Err("sessions directory must be absolute"); }
    let sessions = fs::canonicalize(sessions).map_err(|_| "sessions directory unavailable")?;
    let scratch = fresh_path(scratch)?;
    let report = fresh_path(report)?;
    if scratch.starts_with(&sessions) || report.starts_with(&sessions) || scratch == report {
        return Err("outputs must be separate from input sessions and each other");
    }
    let mut files = Vec::new();
    for entry in fs::read_dir(&sessions).map_err(|_| "cannot list sessions")? {
        let path = entry.map_err(|_| "cannot inspect session entry")?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("jsonl") { continue; }
        let metadata = fs::symlink_metadata(&path).map_err(|_| "cannot inspect session file")?;
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return Err("session JSONL must be a regular non-symlink file");
        }
        files.push(path);
    }
    files.sort();
    if files.is_empty() { return Err("no session JSONL files supplied"); }
    fs::create_dir(&scratch).map_err(|_| "cannot exclusively create fresh scratch")?;
    let mut output = fs::OpenOptions::new().write(true).create_new(true).open(&report)
        .map_err(|_| "cannot exclusively create report")?;
    let agent = scratch.join(format!("agent-conservative-{}", "p".repeat(PADDING)));
    let cwd = scratch.join("dummy-workspace");
    fs::create_dir(&agent).map_err(|_| "cannot create dummy agent")?;
    fs::create_dir(&cwd).map_err(|_| "cannot create dummy workspace")?;
    // Explicit dummy identity avoids project_identity's Git/config discovery.
    let project = ProjectIdentity {
        id: format!("project_{}", "p".repeat(80)),
        root: cwd.to_string_lossy().into_owned(), aliases: Vec::new(),
    };
    std::env::set_var("PRIME_AGENT_CODING_AGENT_DIR", &agent);
    let settings = json!({"memory": {"learning": false, "recall": false,
        "maxImportChunkChars": CHUNK_CHARS}, "autoRefine": {"enabled": false}});
    fs::write(agent.join("settings.json"), settings.to_string())
        .map_err(|_| "cannot write dummy settings")?;
    let store = MemoryStore::new(&agent.to_string_lossy(), project)
        .map_err(|_| "cannot create native dummy store")?;
    let effective = store.settings();
    if effective.learning || effective.recall || effective.max_import_chunk_chars != CHUNK_CHARS {
        return Err("native settings mismatch");
    }
    let jobs = MemoryJobs::new(store);
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build()
        .map_err(|_| "cannot create prepare-only runtime")?;
    let mut rows = Vec::<Value>::new();
    let mut total = 0usize;
    let (mut uri_min, mut uri_max) = (usize::MAX, 0usize);
    for file in files {
        let name = file.file_name().and_then(|name| name.to_str()).ok_or("invalid filename")?;
        let source_hash = digest(&file)?;
        let job = runtime.block_on(jobs.prepare(file.to_str().ok_or("invalid session path")?))
            .map_err(|_| "native prepare failed; no extraction was attempted")?;
        if job.source_hash != source_hash || digest(&file)? != source_hash {
            return Err("session changed during native prepare");
        }
        if job.status != ImportJobStatus::Pending || job.next_chunk != 0
            || job.chunks.iter().any(|chunk| chunk.proposal.is_some())
            || job.coverage.evidence_records == 0 || !job.coverage.excluded_lines.is_empty() {
            return Err("unexpected prepared state or missing/excluded source evidence");
        }
        for record in job.chunks.iter().flat_map(|chunk| &chunk.records) {
            let length = record.uri.as_ref().ok_or("native evidence URI missing")?.encode_utf16().count();
            uri_min = uri_min.min(length);
            uri_max = uri_max.max(length);
        }
        total += job.chunks.len();
        rows.push(json!({"filename": name, "sourceSha256": source_hash,
            "chunkCount": job.chunks.len(), "evidenceRecordCount": job.coverage.evidence_records}));
    }
    let body = json!({"sessions": rows, "totalChunks": total, "withinTwentyChunks": total <= 20,
        "settings": {"memoryLearning": false, "recallOn": false, "maxImportChunkChars": CHUNK_CHARS,
            "agentPaddingChars": PADDING, "projectIdChars": 88,
            "nativeEvidenceUriUtf16Min": uri_min, "nativeEvidenceUriUtf16Max": uri_max}});
    let encoded = serde_json::to_vec_pretty(&body).map_err(|_| "cannot encode report")?;
    output.write_all(&encoded).and_then(|_| output.write_all(b"\n")).and_then(|_| output.sync_all())
        .map_err(|_| "cannot persist content-free report")?;
    Ok(total <= 20)
}

fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).map(PathBuf::from).collect();
    if args.len() != 3 {
        eprintln!("usage: memory_original_prepare <absolute-sessions-dir> <new-absolute-scratch> <new-absolute-report>");
        std::process::exit(2);
    }
    match prepare(&args[0], &args[1], &args[2]) {
        Ok(true) => {}, Ok(false) => std::process::exit(3),
        Err(error) => { eprintln!("{error}"); std::process::exit(2); }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn synthetic_native_prepare_only_and_fresh_output_guard() {
        let temp = tempfile::tempdir().unwrap();
        let input = temp.path().join("sessions");
        let scratch = temp.path().join("scratch");
        let report = temp.path().join("counts.json");
        fs::create_dir(&input).unwrap();
        let raw = json!({"type": "message", "id": "one", "message": {
            "role": "user", "content": "INVENTED_ONLY ".repeat(2500), "timestamp": 1709294400000i64}}).to_string();
        fs::write(input.join("synthetic.jsonl"), &raw).unwrap();
        assert!(prepare(&input, &scratch, &report).unwrap());
        let text = fs::read_to_string(&report).unwrap();
        let value: Value = serde_json::from_str(&text).unwrap();
        assert!(value["totalChunks"].as_u64().unwrap() > 1);
        assert_eq!(value["sessions"][0]["sourceSha256"], hash(&raw));
        assert_eq!(value["sessions"][0]["evidenceRecordCount"], 1);
        assert!(!text.contains("INVENTED_ONLY") && !text.contains("file:"));
        assert!(prepare(&input, &scratch, &temp.path().join("new.json")).is_err());
        assert!(prepare(&input, &temp.path().join("new"), &report).is_err());
        assert!(!temp.path().join("new").exists());
    }
}
