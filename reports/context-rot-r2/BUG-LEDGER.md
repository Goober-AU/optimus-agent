# BUG LEDGER — context rot iteration 2

Status vocabulary: FIXED (behaviour changed and a test covers it), VERIFIED-OPEN
(still present on this base, citation re-checked against current source),
NEEDS-REVERIFICATION (structure evolved since the 8ae54da audit; the cited
lines no longer map cleanly), DEFERRED (verified, fix scoped for a later wave).

Base for this campaign: 6000d6bc1 (v0.1.36 + #181/#182).

## 1. Defect status on this base

| # | severity | status r2 | evidence |
| --- | --- | --- | --- |
| D1 | HIGH | in flight (W1) | hook `threshold_compaction_needed` (agent_session.rs:4807+) gates on `settings.enabled` only; executor `check_compaction` (16498) also requires the runtime `auto_compaction_enabled` toggle. Verified present on base. |
| D2 | HIGH | in flight (W1) | continuation flags only set when RLM/goal/autonomous continuation queued (4800-4865); ordinary interactive turn can stop unresumed. Verified present. |
| D4 | HIGH | CONFIRMED on the production path | R2-E1: default arm loses all 19 anchors over 5 cycles under a deterministic lossy summarizer (see EXPERIMENT-LEDGER.md). taskState mitigates for constraints/decisions/corrections (14/19). |
| D5 | HIGH | in flight (W2) | prepare_compaction still returns Some with an empty summarize window. |
| D6 | HIGH | in flight (W2, reported green) | extension path bypasses validate_summary. |
| D7 | MED-HIGH | VERIFIED-OPEN, fix DEFERRED | session_manager.rs:1146-1164: incompatible/null provider checkpoint => the checkpointed compaction is skipped as boundary and pre-compaction messages replay (heavy fallback, not lossy). Recommended fix: honour the skipped entry's firstKeptEntryId as a truncation boundary while using the older summary for text. Deferred: risk of subtle restore changes needs dedicated test coverage. |
| D8 | MED-HIGH | FIXED (this PR) | migrate_v1_to_v2 dropped firstKeptEntryIndex without writing firstKeptEntryId when the index was out of bounds or pointed at the session header. Now falls back to the entry after the compaction. 2 regression tests. |
| D9 | MED-HIGH | in flight (W2) | deadline-fallback boilerplate still becomes the next previous_summary (prepare_compaction walk-back 1620-1639 does not check details.deadline_fallback). |
| D10 | MED | VERIFIED-OPEN, DEFERRED to wave 3 | branch_summarization.rs:153-156: toolResult entries are skipped entirely ("context remains attached to its assistant tool call"), so branch summaries lose tool output; silent truncation unchanged. |
| D11 | MED | NEEDS-REVERIFICATION | stale harness digests beside the fresh one (agent_session.rs 16176-16207 at audit time; W1 owns the file this wave). |
| D12 | MED | NEEDS-REVERIFICATION | memory/harness text as unfenced instruction-shaped turns; the memory module has evolved (service.rs recall dispatch). Re-verify against current recall path. |
| D13 | MED | NEEDS-REVERIFICATION | memory recall fails closed on a foreign projectId; re-verify in memory/search.rs. |
| D14 | MED | in flight (W2) | read-files inventory always empty; extraction mismatch to be diagnosed. |
| D16 | MED | in flight (W3) | images zero tokens in estimate_tokens while find_cut_point charges 4096 chars. |
| D17 | LOW-MED | in flight (W3) | estimate excludes system prompt + tool schemas; request-level estimator being built. |
| D18 | LOW | VERIFIED-OPEN, DEFERRED | compaction failure invisible to the model (agent_session.rs handle_auto_compaction_failure path); wave 3. |
| D19 | MED | VERIFIED-OPEN, DEFERRED | live-list producers without drain/cap (agent_session.rs 16736-16739 at audit time); W1 owns the file, not in its brief. |
| D20 | MED | VERIFIED-OPEN, DEFERRED | summarizer request vs model-visible request divergence (agent_session.rs 17910-17924 at audit time); needs the request-token work (W3) first. |

## 2. New findings this campaign (not in the iteration-1 ledger)

| # | finding | evidence |
| --- | --- | --- |
| N1 | v1 ledger evidence extraction misses the most common evidence shapes: `cargo test: N passed`, `build succeeded`, benchmark lines. EVIDENCE_RE (task_state.rs:45-47) matches only `test result:|error[E\d+]|N files changed|panicked at|FAILED`. | R2-E1: evidence 0/4 in the taskState arm. |
| N2 | v1 ledger is blind to custom entries: `entry_text` (task_state.rs:146-149) handles only `Message`, so subagent reports (agent_message custom entries) never reach the ledger. | R2-E1: subagent artifact 0/1 in the taskState arm. |
| N3 | both N1 and N2 are design constraints for typed_state v2 (W4): classify by role/provenance, not content regex. | brief requirement; R2-E1 measured the gap. |
