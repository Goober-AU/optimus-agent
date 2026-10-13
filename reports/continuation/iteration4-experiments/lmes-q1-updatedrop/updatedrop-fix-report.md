# Fix: silent import_apply update-drop (LME-S 00ca467f write-time root cause)

Branch `improve/mem4-update-drop` (worktree imp4-updatedrop, base = imp4 HEAD
67eeee38c on improve/memory-iteration-4).

## Root cause (mechanics)

Job `import_8319fcc58d0185e551f0b987` (session answer_39900a0a_1) chunk 0
proposed UPDATING two existing entries (Dr. Smith PCP visit March 3 bronchitis
-> memory_lingering_cough_guaifenesin; numbness facts -> imported_a8d217...).
The post-normalization filter in `extraction::extract()`
(crates/pi-coding-agent/src/core/memory/extraction.rs) kept only
`action == "create" && kind == "memory" && evidenceStatus == "cited"` edits,
so the chunk's update edits were REMOVED there - the proposal persisted with
`edits: []`. `jobs::apply()` then had nothing to apply: no error, no log, no
entry (apply-time guards like "Imports only create memories" never fired
because there were no edits left). The target ids were NOT stale and there was
no schema mismatch: the generic /refine system prompt invites consolidation
updates, so the model legitimately proposed them; the import-only-creates
policy silently discarded them. Result: the store had 110 entries, none with
the March 3 Dr. Smith visit; the question "How many doctor's appointments did I
go to in March?" was unanswerable (answered 1, gold 2).

## Fix (minimal, general - option b: convert instead of lose)

`select_import_edits()` (extraction.rs) now:
- keeps cited `create` edits of kind `memory` unchanged;
- converts cited `update` edits of kind `memory` with non-empty content into
  `create` edits under a fresh derived id (`imported_<sha24>` of content -
  deliberately NOT the update target id, which would be dropped again by the
  apply-time idempotent filter), preserving title/content/metadata and adding
  `metadata.updateTargetId` for traceability;
- counts every policy drop (uncited evidence, delete, foreign kind, contentless
  update) and appends an `[import-normalization: converted N update edit(s) to
  create; dropped M edit(s) ...]` marker to the persisted chunk rationale, so
  no drop is silent (audit trail in the job document).

6 new unit tests in extraction.rs, including the exact 8319fcc5 chunk-0
reproduction (update of memory_lingering_cough_guaifenesin with the
Dr. Smith March 3 fact) asserting conversion, derived id != target id,
content/title/metadata preservation, determinism, and the counted-drop paths.

## Validation (controller, DGX GLM-5.3 only)

- typeB (write-time, this fix), 00ca467f, official-lme-v1 judge: 1.0 pass.
  Mini env = the 3 evidence sessions of qid 00ca467f from the smoke5 manifest
  (env_00ca467f_mini.json). Store now contains
  `mem-health-bronchitis-diagnosis-smith` ("...on March 3rd they saw their
  primary care physician, Dr. Smith, who diagnosed them with bronchitis") and
  the fix's conversion path fired on the same job that used to drop facts:
  chunk 0 rationale ends with "[import-normalization: converted 1 update
  edit(s) to create; dropped 0 ...]" and the converted entry
  `imported_9ee11965dbc42a6534a8991e` carries
  `metadata.updateTargetId: mem-health-lefthand-numbness-emg-johnson`.
  Answer enumerated March 3 (Dr. Smith) + March 20 (Dr. Thompson) = 2.
- Regressions (typeA read-time on frozen stores): 01493427 1.0 ("25 new
  postcards", KU store lme_s_ku_smoke2a_v2), 031748ae 1.0 ("4 then 5", KU
  store lme_s_ku_smoke2b_v2), 001be529 1.0 ("over a year", smoke5 store,
  base-settings = sealed stack incl. recallQueryExpansion=true). Note: the
  001be529 attempt must run with attempts-root OUTSIDE any git worktree
  (a scratch inside imp4-updatedrop resolves the driver's project identity to
  the repo alias and sees 0 of the copied store's 121 entries; reran from
  campaign-root scratch -> pass).
- memory:: test suite: 128 passed, 0 failed (extraction 6/6 included).
  `cargo test -p pi-coding-agent --example memory_bench`: 69 passed,
  1 failed - combined_actual_converter_serialization_reaches_native_jobs
  fails identically on the base commit (worktrees lack bench/.venv); pre-existing.

## Artifacts

- driver: CARGO_TARGET_DIR imp4-targetC, release/examples/memory_bench.exe
  sha256 3e43cdae6f7497e208188e98c5b7295f7122c90d9cfeb4c1150ecc44ddc3b9f1
- typeb-00ca467f/: scores.json (1.0), run.jsonl (answer record),
  manifest.json, env_00ca467f_mini.json, store-check.json (Dr. Smith entry,
  converted entry, the session _1 chunk-0 proposal with the conversion marker)
- regressions/{01493427,031748ae,001be529}/: scores.json + run.jsonl each
