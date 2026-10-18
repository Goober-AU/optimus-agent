# w4-memory — full-harness validation round 2 evidence

Area: memory storage, retrieval, refinement/persona, benchmark harness.
Worktree: C:/Users/openclawuser/optimus-fh2-20261018 (branch improve/harness-validation-r2,
base 5feb47596).
Private target dir: C:/Users/openclawuser/optimus-fh2-target-w4 (cargo -j 2).
Status: in progress — this file is updated as the runs complete.

## Scope actually touched

Source (edit):
- crates/pi-coding-agent/src/core/memory/store.rs

Tests (new/changed):
- crates/pi-coding-agent/tests/scenario_memory_e2e.rs (new; campaign section 35, scenarios E and F)
- crates/pi-coding-agent/tests/memory_source_integrity.rs (one added test)

## Defects

### D1 (P2) restore could not recover a corrupt authoritative store

- File: crates/pi-coding-agent/src/core/memory/store.rs, `MemoryStore::restore`.
- Root cause: `restore` read the current store with `self.read()?` before writing
  the backup back. `read()` fails closed on a corrupt store, so the documented
  recovery command (`memory restore <backup-id>`) returned the parse/validation
  error and never applied the backup. A user whose `harness_state.json` was
  damaged had backups that could never be restored.
- Reproducer (pre-fix): apply one edit, `backup(None)`, overwrite the store with
  `{"schema": 2}` or with `{"schema": 1, "memory": {"revision": `, then
  `restore(&backup)` -> `Err("Invalid project memory snapshot")` /
  `Err("expected value at line 1 column 27")`.
- Fix: on a read failure, `salvage_damaged_state()` quarantines the damaged bytes
  to `harness_state.corrupt-<sha256[..16]>.json` (content-addressed, fail-closed:
  a failed quarantine write aborts the restore before the file is replaced) and
  salvages only what is still trustworthy: `memory.revision` and the
  `memory.events` delivery receipts, when the bytes still parse. A truncated file
  salvages nothing and the restore advances from revision 0. The healthy path is
  unchanged (pre-restore backup still written).
- Tests: `store::tests::restore_recovers_a_damaged_authoritative_store_and_keeps_its_bytes`,
  `store::tests::restore_recovers_a_truncated_store_without_salvaging_garbage`.

### D2 (P3) `memory configure` silently destroyed an unparseable project settings file

- File: crates/pi-coding-agent/src/core/memory/store.rs, `MemoryStore::configure`.
- Root cause: the merge base was built with
  `read_json(..).ok().and_then(record).unwrap_or_default()`. A settings file that
  no longer parses contributed `{}` as the merge base, so the next `/memory
  configure` write replaced the whole file with only the patched keys: every
  other setting the file carried (recall/learning flags, limits) was dropped with
  no warning and no copy.
- Reproducer (pre-fix): write `{"recall": false, "recallRerank": tru` to
  `<store.dir>/settings.json`, call `configure({"recallRerankCandidates": 42})`,
  then list `<store.dir>`: no copy of the previous bytes exists and they are gone.
- Fix: `quarantine_settings_file()` keeps the previous bytes in
  `settings.corrupt-<sha256[..16]>.json` (content-addressed) and logs a warning
  with both paths before the patch writes a fresh file. Quarantine failure aborts
  the configure instead of destroying the file.
- Test: `store::tests::configure_preserves_an_invalid_settings_file_before_replacing_it`.

### D3 (P4, hardening, not reproducible from the public API) apply could write `null`

- File: crates/pi-coding-agent/src/core/memory/store.rs, `MemoryStore::apply`.
- Root cause: the authoritative write used
  `serde_json::to_value(&candidate).unwrap_or(Value::Null)`. Every reachable
  `MemoryDocument` serializes, so this could not be triggered from the public API,
  but any future serialization failure would have replaced the whole store with
  `null` (an empty, unvalidatable document) instead of failing the apply.
- Fix: the conversion error now aborts the apply with
  `Could not serialize the memory state: ..`; the previous state stays
  authoritative.
- Test: none (unreachable by construction); covered as hardening, not as a bug fix.

## Verified (feature -> test/command -> result)

Nothing is verified by a green run yet. Round 2 hands the exact job commands to the
parent (see "Job commands for the parent" below); this section is filled in from the
parent's queue logs, not from a worker run.

| Feature | Test | Command | Result |
|---|---|---|---|
| F/E scenario file compiles and passes | scenario_memory_e2e (2 tests) | job 1 | PENDING (parent) |
| source integrity + failed-write tests | memory_source_integrity | job 2 | PENDING (parent) |
| store unit tests (restore/configure) | `core::memory::` lib filter | job 3 | PENDING (parent) |

## Review of the fixes already on disk (what the tests missed)

Static review of `store.rs` (no cargo run: the parent owns the build queue).

### R1 (test defect, would have failed on first run) truncated-restore test asserted the wrong receipt state

- File: `crates/pi-coding-agent/src/core/memory/store.rs` (`mod tests`,
  `restore_recovers_a_truncated_store_without_salvaging_garbage`).
- The draft asserted `document.memory.events.is_empty()` "nothing was salvageable, so no
  receipt is invented". That is false: `restore` copies `current.memory.events` into the
  document it takes from the *backup*, and the backup in that test is taken after the
  apply, so it carries `op_one`. The restored document therefore has
  `events = {"op_one": <fingerprint>}` and the assertion fails.
- Fix: the test now asserts exactly `["op_one"]` (the backup's own receipt, equal to the
  fingerprint captured before the damage) and that a receipt the damaged bytes *claim*
  (`fabricated`) is absent. The truncated bytes now also claim `revision: 7` so a
  recovery that trusted them would land on 8 instead of the asserted 1.

### R2 (test weakness) the damaged-store test proved nothing about the salvage branch

- File: same module, `restore_recovers_a_damaged_authoritative_store_and_keeps_its_bytes`.
- The draft's receipt assertion compared a value with itself
  (`Some(document.memory.events["op_one"].as_str())`), which is a tautology, and both the
  backup and the damaged file carried revision 1, so `revision == 2` could be produced by
  a restore that never looked at the damaged bytes at all.
- Fix: the damaged file now claims `revision: 5` and carries an extra receipt
  (`salvaged_only`), so `revision == 6` and the salvaged receipt can only come from the
  salvage branch. The test also applies a second edit at the recovered revision, which
  proves the recovery leaves a usable store instead of a wedged one.

### R3 (gap) nothing exercised the byte-preserving quarantine path

- `quarantine_beside` deliberately uses `write_bytes_atomic_sync` (the byte-capable twin)
  because a damaged store may not be valid UTF-8; no test wrote non-UTF-8 bytes.
- Added `restore_recovers_a_non_utf8_store_and_keeps_its_bytes_verbatim`.

### R4 (gap) only the "does not parse" branch of the settings quarantine was covered

- `configure` quarantines on a `read_json` error *and* on a `record` error (valid JSON
  that is not an object). Only the first branch had a test.
- Added `configure_quarantines_settings_that_are_valid_json_but_not_an_object`
  (`[1, 2, 3]`) and factored the copy listing into `settings_corrupt_copies`.

### R5 (portability of a new test) the read-only write injection is Windows-only

- `crates/pi-coding-agent/tests/memory_source_integrity.rs`,
  `failed_authoritative_write_keeps_the_previous_store_intact` (draft) sets the store file
  read-only and expects the atomic write to fail. The store write is a temp file plus a
  rename; on Unix `rename()` over a read-only *file* succeeds (only the parent directory
  permissions matter), so the apply would succeed and `expect_err` would fail the test on
  Linux/macOS.
- Fix: `#[cfg(windows)]` on the helper and the test, with the reason in the doc comment,
  plus a portable twin `rejected_apply_leaves_the_authoritative_bytes_untouched` that
  covers the same invariant through the pre-write rejections (stale revision, invalid id)
  and then applies a valid edit at the same revision.

### Observations, no change made

- `server.rs` still builds its in-memory state with
  `serde_json::to_value(document).unwrap_or(Value::Null)`; a serialization failure there
  would degrade a shared-memory request to a `null` state. Unreachable for
  `MemoryDocument`, same class as D3, and outside the authoritative store path. Left
  alone to keep the diff minimal; the parent may want a follow-up.
- `restore` does not write a pre-restore backup when the current store is damaged (there
  is no valid document to back up); the quarantined bytes are the record. Intentional.

## Tests added or changed by this worker (file:line)

- `crates/pi-coding-agent/src/core/memory/store.rs`
  - `restore_recovers_a_damaged_authoritative_store_and_keeps_its_bytes` (strengthened:
    revision/receipt salvage + follow-up apply).
  - `restore_recovers_a_truncated_store_without_salvaging_garbage` (corrected receipt
    assertion, garbage revision claim).
  - `restore_recovers_a_non_utf8_store_and_keeps_its_bytes_verbatim` (new).
  - `configure_quarantines_settings_that_are_valid_json_but_not_an_object` (new),
    `settings_corrupt_copies` helper (new).
- `crates/pi-coding-agent/tests/memory_source_integrity.rs`
  - `failed_authoritative_write_keeps_the_previous_store_intact` (Windows-gated),
    `rejected_apply_leaves_the_authoritative_bytes_untouched` (new, portable).
- `crates/pi-coding-agent/tests/scenario_memory_e2e.rs` (scenarios E and F)
  - E: the host-facing `search` action is now asserted (id/scope/title/version/status/
    freshness/sources/matched), so the agent surface, not only the Rust API, is covered.
  - E: cold-vs-warm recall parity (the rendered block and ids are compared across the
    session boundary, not just the hit ids).
  - F: the history rows are re-read from the *new* session (durability of the audit trail
    across the boundary, including the superseded `before` content and its `f1` source).
  - F: the host-facing surface for supersession (`status: "current"` for the winner,
    `status: "superseded"` plus the retained preview for the loser under
    `includeInactive`).

## Job commands for the parent

All three go through `C:/Users/openclawuser/fh2-build-queue/fh2q.py` (one job at a time,
`CARGO_BUILD_JOBS=2`, `-j 2`, `--test-threads=1`) and use the warm private target dir
`C:/Users/openclawuser/optimus-fh2-target-w4`.

Use the absolute Git timeout binary as the wrapper: a bare `timeout` resolves to
`C:\Windows\System32\TIMEOUT.EXE` and the job dies at once with
`ERROR: Invalid syntax. Default option is not allowed more than '1' time(s)` (this is
exactly what happened to `gate-b-coding-lib.log`, exit 1 after 0.5 s, no tests ran).

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w4-memory2 \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w4" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/artifacts/logs/w4-scenario-memory-e2e.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 1800 \
  cargo test --locked -p pi-coding-agent --test scenario_memory_e2e -j 2 -- --test-threads=1
```

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w4-memory2 \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w4" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/artifacts/logs/w4-memory-source-integrity.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 1800 \
  cargo test --locked -p pi-coding-agent --test memory_source_integrity -j 2 -- --test-threads=1
```

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w4-memory2 \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w4" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/artifacts/logs/w4-coding-agent-memory-lib.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 2400 \
  cargo test --locked -p pi-coding-agent --lib -j 2 -- --test-threads=1 core::memory::
```

Notes for the parent:
- Job 1 is the gate for this task; jobs 2 and 3 cover the files this task touched. The
  three targets are independent, so the broken `tests/ipython_output_bounds.rs` target
  (which currently makes `cargo check --workspace --all-targets` fail with 4 errors) does
  not block them.
- If job 3 is used as gate B, re-run it under the absolute timeout path: the previous
  `gate-b-coding-lib` record (exit 1, 0.5 s) is a wrapper failure, not a test result.

## Still unverified (must not be claimed)

- No compile or run has been done for any of the three files by this worker (hard rule:
  the parent owns the build queue). Syntax was checked with `rustfmt --check` (parses, no
  errors) and the two tricky expressions (`&&Value` indexing, the `concat!` byte fixture)
  were type-checked with a standalone `rustc` snippet. Type errors against the crate APIs
  remain possible.
- Every scenario assertion about retrieval ranking, supersession and the host-facing JSON
  shapes is derived from reading `search.rs`/`service.rs`, not from a run.
- The Windows-only read-only write-injection test is unrun; on Unix it is compiled out by
  design.
- `salvage_damaged_state` when the quarantine write itself fails (read-only memory dir) is
  argued from the code (fail-closed before the store is replaced) but untested.

## Changed (file:line, current working tree)

Source (only test code; the fixes themselves are unchanged from the draft):
- `crates/pi-coding-agent/src/core/memory/store.rs:838` `salvage_damaged_state` (unchanged,
  reviewed), `:880` `quarantine_beside`, `:911` `quarantine_settings_file`, `:927`
  `restore`, `:780` `configure`.
- Tests in the same file: `:1702` `corrupted_copies`, `:1728`
  `restore_recovers_a_damaged_authoritative_store_and_keeps_its_bytes` (strengthened),
  `:1797` `restore_recovers_a_truncated_store_without_salvaging_garbage` (corrected),
  `:1850` `restore_recovers_a_non_utf8_store_and_keeps_its_bytes_verbatim` (new), `:2386`
  `settings_corrupt_copies` (new helper), `:2404`
  `configure_quarantines_settings_that_are_valid_json_but_not_an_object` (new), `:2426`
  `configure_preserves_an_invalid_settings_file_before_replacing_it` (helper reuse).

- `crates/pi-coding-agent/tests/memory_source_integrity.rs:944` `make_readonly`
  (`#[cfg(windows)]`), `:952` `failed_authoritative_write_keeps_the_previous_store_intact`
  (`#[cfg(windows)]`), `:1004` `rejected_apply_leaves_the_authoritative_bytes_untouched`
  (new, portable).

- `crates/pi-coding-agent/tests/scenario_memory_e2e.rs:212` scenario E, `:277` host-facing
  search assertions (new), `:313` cold/warm recall parity (new), `:346` scenario F,
  `:425` history from the new session (new), `:454` scenario F supersession, `:517`
  host-facing supersession surface (new).

## Cross-worker observations (outside my file map, no edits made)

- `crates/pi-coding-agent/tests/ipython_output_bounds.rs` does not compile: `OwnedKernel`
  has no `execute`/`snapshot_state` (E0599) and `stop(client)` gets `OwnedKernel` where
  `&ReplKernelManager` is expected (E0308). Seen in
  `artifacts/logs/integrate-check.log` (exit 101) and still present in the working tree,
  so `cargo check --locked --workspace --all-targets` is RED until its owner fixes it.
  It does not block the three jobs above (separate targets).
- `artifacts/logs/gate-b-coding-lib.log` (owner parent-b-coding, exit 1, 0.5 s) is a
  wrapper failure, not a test result: the log starts with
  `ERROR: Invalid syntax. Default option is not allowed more than '1' time(s)` because
  `timeout` resolved to `C:\Windows\System32\TIMEOUT.EXE`. Gate B needs a re-run with
  `"C:/Program Files/Git/usr/bin/timeout"` (the form gate A used successfully).

## Portability of the new tests (requested by the parent)

Asked for: which assertions depend on Windows-only behaviour.

- `store.rs` T1–T5 (`:1728`, `:1797`, `:1850`, `:2404`, `:2426`): **no Windows-only
  assertion, all portable.** They assert revision numbers, entry content, the
  `memory.events` map, one content-addressed `harness_state.corrupt-<sha16>.json` /
  `settings.corrupt-<sha16>.json`, and byte equality against bytes read back from disk
  (`std::fs::write` + `std::fs::read`, so no text-mode translation on any OS). The copy
  list is sorted, so no directory-order or path-separator assumption remains.
- `memory_source_integrity.rs:952` `failed_authoritative_write_keeps_the_previous_store_intact`
  (helper `:944`): **Windows only by design**, `#[cfg(windows)]`, compiled out elsewhere,
  because Unix `rename()` over a read-only file succeeds. The portable twin
  `:1004` `rejected_apply_leaves_the_authoritative_bytes_untouched` carries the same
  invariant through the pre-write rejections.
- `scenario_memory_e2e.rs`: portable, with one environment assumption, `:327`-`:337`
  project isolation. `project_identity` resolves the root with
  `git rev-parse --show-toplevel` before falling back to the real path, so a machine whose
  `TMPDIR`/`%TEMP%` sits inside a git repository would collapse both test projects onto one
  identity and fail the isolation assertions. On this host the temp dir is
  `C:/Users/openclawuser/Optimus-Assistant/profile/tmp` and no ancestor is a repository
  (checked); CI `RUNNER_TEMP` is outside the checkout.

Environment-sensitive, not OS-gated:
- T1–T5 need a writable temp dir; no shared or user state is touched.
- Store writes use `fsync_dir: true`; Unix opens and syncs the directory (Windows skips
  it). A filesystem that rejects directory fsync (network/FUSE mounts) would fail the
  quarantine write and therefore T1–T3. Local NTFS/ext4/APFS are unaffected.
- Scenario E/F do not depend on filesystem timestamp granularity: every search runs after
  the last store write, and rewrites also change the file length, so the
  `(mtime_ns, len)` corpus cache cannot serve a stale index even with 2 s granularity.
