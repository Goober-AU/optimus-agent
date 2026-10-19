# REVIEW AUDIT — pre-PR reviewer simulation (W42)

Hostile-reviewer pass over the campaign PR (base `origin/main` `6000d6bc1` -> integration tip `29f1f0c92`, 80 commits, 328 files, +149,503/-182). Audit worktree `optimus-rot2-w42-audit`, branch `rot2/w42-audit`. Method: changelog fragment vs landed commits vs tree (git grep against HEAD, bounded paths), full `cargo check -p pi-coding-agent --all-targets` in an isolated target dir, focused test runs, and bounded greps inside `research/context_rot_r2/` + `reports/context-rot-r2/` only. No repo-wide sweeps; no history rewrites.

Verdict up front: one blocker (a changelog bullet claims a behavior change that is not wired into any production call site), seven should-fix findings (three documentation-staleness gaps, missing changelog bullets, undisclosed open defects in the new typed mode, machine-specific paths committed in research code, an 8.1 MB JSONL, seven non-conventional commit subjects, one env-mutating test), and seven nits. The four campaign-introduced warnings are fixed by the cleanup commit at the tip of this branch. The other 17 fragment bullets verified true in the tree.

## 1. Cleanup commit (surface 2)

`7b570d082` `chore(compaction): drop the four campaign-introduced warnings`

- `src/bin/compaction_harness.rs:32` — removed the unused `CompactionSettings` import. Verified truly unused: the only occurrence in the file was the import; the bin builds settings through `default_compaction_settings()` and the stdin JSON contract lives in the local `HarnessSettings` struct, so no CLI/serde contract changes.
- `tests/rot2_multicycle.rs:46` — removed the unused `use std::path::PathBuf;`.
- `tests/rot2_multicycle.rs` `ScenarioProbe.gold`/`.kind` and `Scenario.context_window` — removed the never-read fields (plus `default_window()` and the corresponding smoke-scenario literal entries). Safe for the JSON contract: the committed scenario files carry `gold`/`kind`/`contextWindow`/`authority`/`scoringVersion`, the struct already did not model the last two, and there is no `deny_unknown_fields`, so serde ignores the unmodeled keys. Verified by rerunning the committed `research/context_rot_r2/runs/r2-e1-multicycle.scenario.json` through the ignored research adapter (`rot2_production_multicycle`): 2 arms x 5 cycles, probe retention identical to the committed result (default 0/19, taskState 14/19), 0.79 s.

Verification: `cargo check -p pi-coding-agent --all-targets` reports zero warnings for `compaction_harness.rs` and `rot2_multicycle.rs`; `cargo test -p pi-coding-agent --test rot2_multicycle` passes 5/5 (D16/D17 estimator pins included) + 1 research adapter ignored, as before.

## 2. Findings

### Blocker

**B1. The D17 changelog bullet claims a user-visible change the tree does not perform.**
`.changes/context-rot-r2.md:12` says the no-usage context estimate now includes the system prompt and tool schemas "so pressure readings no longer understate the request the provider receives". The function that does this, `estimate_context_tokens_with_request` (`crates/pi-coding-agent/src/core/compaction/compaction.rs:1461`), has zero production callers: the only references are `tests/rot2_multicycle.rs:1140,1147`. Every production estimator call site still calls `estimate_context_tokens(&messages)` without the prompt or tool schemas: `agent_session.rs:281` (`threshold_context_tokens_from_state`), `:391` (`model_and_context_tokens`), `:18222` (the `prepare_compaction` estimator closure), `:19594` (`threshold_context_tokens`), `agent_session/agent_handle.rs:127`, `agent_session/runtime_members.rs:4481`, and the harness bin at `src/bin/compaction_harness.rs:1021,1095`. The struct fields and the no-usage decomposition are real, but no pressure reading in the shipped binary changes. `reports/context-rot-r2/BUG-LEDGER.md:28` (D17 "FIXED") overstates the same way.
Recommended action: either wire `estimate_context_tokens_with_request` into the production pressure paths (the system prompt and tool definitions must be plumbed to those six call sites) before the PR merges, or rewrite the bullet to an API addition ("added a request-level estimator variant...; production wiring pending") and mark D17 partially fixed. A maintainer diffing the estimator call sites will find the bullet false as written.

### Should-fix

**S1. PR-BODY and FINAL-REPORT describe merged fixes as open.**
The D18/D19/D21 fixes (`8c558dd7b`, `4769f42f4`, `a9035ce8a`) and the D20 contract test (`e44faff08`) are ancestors of HEAD, but:
- `reports/context-rot-r2/PR-BODY.md:74-78` says D21 is "PENDING - ... in flight on `rot2/w28-d21`" and D18/D19/D20 are open with "a targeted fix wave is in flight"; `PR-BODY.md:401` repeats it.
- `reports/context-rot-r2/FINAL-REPORT.md:530-541` (section 9) says "`rot2/w18-defects` has no commits over this head" and the D21 fix is "drafted on `rot2/w28-d21`"; `FINAL-REPORT.md:614-618` (section 8) says the D18/D19/D20 recovery and the D21 fix are "In flight at this writing, not in this tree". Both false at HEAD: the fixes are in this tree (`git merge-base --is-ancestor` confirmed for all four).
- `reports/context-rot-r2/BUG-LEDGER.md:23` (D11 "fix in flight (W20)" — fixed by `30588707d`), `:29-30` (D18/D19 "VERIFIED-OPEN, DEFERRED" — both fixed on this branch) are stale, and D21 has no ledger row at all.
Cause: the docs waves landed before/simultaneously with the fix wave (PR-BODY refreshed 17:36:02, fixes 17:36:03-17:47:54; the FINAL-REPORT refresh at 17:47:54 predates the W35 red-team at 17:52:28).
Recommended action: refresh PR-BODY, FINAL-REPORT sections 7-9, and the BUG-LEDGER rows to the branch tip before opening the PR.

**S2. The changelog fragment is missing the D18/D19/D21 bullets.**
`.changes/context-rot-r2.md` was last touched at `1502eb9a7` (17:36:02); the three user-visible fixes that landed afterwards (bounded model-visible notice when auto-compaction fails; capped live disclosure lists with a dropped-count marker; correction carriers exempt from the ledger render caps) have no bullets. The integrator owns fragments, so no worker added them; the PR as it stands will ship three behavior changes absent from the release notes.
Recommended action: integrator adds the three bullets (and, per B1, corrects the D17 bullet).

**S3. The PR body and final report are silent on the eight open typed-mode defects (D22-D29).**
`reports/context-rot-r2/BUG-LEDGER.md` section 4 documents the W35 red-team findings — all VERIFIED-OPEN with committed failing-first tests in `tests/rot2_typed_adversarial.rs` (runnable red with `cargo test -- --ignored`) — including D25 (step-scoped pending actions stay CURRENT forever; ledger and summaries grow without bound until mandatory material exceeds the 2048-token hot-state budget and compaction fails permanently, MED-HIGH), D26 (a malformed `details.typedState` payload silently drops every durable record below the boundary), and D29 (supersession pairing is token-overlap-based; the adversarial no-anchor correction case the campaign set out to fix structurally is NOT achieved). Neither `PR-BODY.md` nor `FINAL-REPORT.md` mentions D22-D29 anywhere (0 matches), and the typed-mode bullet `.changes/context-rot-r2.md:14` carries no caveat. A maintainer reading the PR body sees only the nine green integration guards.
Recommended action: add a "known open defects" block to PR-BODY (D22-D29 with severities, pointer to the red tests) and a matching caveat sentence to the typed-mode changelog bullet; keep the default `off` recommendation (FINAL-REPORT section 6 already says OPT-IN).

**S4. Machine-specific absolute paths are committed in runnable research code and configs.**
Bounded grep for `C:/Users/openclawuser` (forward, backslash and git-bash forms) inside `research/context_rot_r2/` + `reports/context-rot-r2/`: 90 files. Breakdown: 70 JSONL data files (provenance fields `sessionDir`/`logFile` in raw call telemetry — data, low risk), 9 Markdown reports (environment documentation — acceptable as records), 10 Python files, 1 committed config:
- `research/context_rot_r2/contextbench/live/harness_client.py:20-27` — `CRED_BEARER_SCRIPT`/`CRED_POMERIUM_SCRIPT` are hard constants pointing at a specific user's credential-store scripts (`.../profile/agent/skill-state/dgx-k3-login/glm53-resolvers/...ps1`, `.../profile/agent/credentials/Get-K3PomeriumJwt.ps1`) with no environment override (used verbatim at line 55). Breaks on any other checkout and discloses private credential infrastructure in a public repo.
- `research/context_rot_r2/live/run_r2e2.py:489-495` — env-overridable (`R2E2_GLM_RESOLVER`/`R2E2_POMERIUM`) but defaults into the same credential store.
- `research/context_rot_r2/compactbench_adapter/live/run_live.py:55` — `BENCHMARKS` hard-coded to a local checkout; no override.
- `research/context_rot_r2/compactbench_adapter/live/run-config.json:13,15,57` — a committed config referencing `C:/Users/openclawuser/rot2-w26-target/debug/compaction-harness.exe`.
- The remaining six (`optimus_compactor.py:69`, `probe_harness_behavior.py:60`, `test_optimus_compactor.py:8`, `contextbench/live/run_live.py:37`, `incremental/driver.py:12` docstring, `orchestrate.py:20,41-43`, `repro/typecompact_repro.py:46`) are env-overridable defaults or candidate lists; they still fail on a fresh checkout unless the env is set.
Recommended action: make the credential scripts fail-closed without an explicit env var (no machine default), parameterize `BENCHMARKS`/`REFS_ROOT`, and replace the committed run-config paths with placeholders or a template. The `run_r2e2.py`/`harness_client.py` references to a private LaunchPad endpoint and credential scripts are the ones a reviewer will flag first.

**S5. One research artifact exceeds the 5 MB bar with no LFS policy.**
`research/context_rot_r2/live/raw-calls.jsonl` is 8,131,489 bytes (the only >5 MB blob in the tree). `.gitattributes` has no LFS rules (git-lfs 3.7.1 is installed locally, so adoption is a policy decision). The aggregated results (`results.json`, 19k lines) already carry the summary.
Recommended action: LFS-track `research/context_rot_r2/live/raw-calls.jsonl` (and the sibling `compactbench_adapter/live/raw-calls.jsonl`, 71 absolute-path-bearing lines, if it grows) or drop the raw call telemetry from the repo and keep the aggregates.

**S6. Seven commits break the conventional-commit pattern.**
`research(context-rot):` subjects — `59832805f`, `fe6b6d966`, `0b824f358`, `4b8f2165f`, `2fbb14f33`, `c5107fc31`; plus `fb089ee6e` (`research: ...`, no scope). `research` is not a conventional type; these should have been `docs(context-rot):`/`test(context-rot):`/`chore(research):`. All 73 other subjects conform (feat/fix/test/docs with scope, no "fixes:" forms). Per the brief, listed only; the integrator decides whether to rewrite (a rebase would also invalidate the report citations pinned to these SHAs).

**S7. One test mutates process-global environment state.**
`tests/rot2_typed_adversarial.rs:997-1024` (`explicit_off_setting_wins_over_the_typed_environment_variable`, a default-on `#[test]`) calls `std::env::set_var`/`remove_var` on `PRIME_AGENT_COMPACTION_TASK_STATE`. Today no other test in that binary reads the variable, so it cannot flake yet, but: (a) any future test in the same binary that resolves the task-state mode through the env fallback (e.g. a settings-manager path) races it under default parallel threads, and (b) an assertion failure between the `set_var` and `remove_var` leaves the variable set for the rest of the process, converting one failure into cascading ones.
Recommended action: exercise the precedence through the explicit `environment` parameter of `resolve_task_state_mode` (the function already accepts it) and keep at most a one-line env-var readback check, or guard the mutation with guaranteed cleanup.

### Nits

**N1.** `estimate_context_tokens_with_request` is `pub` and test-only (see B1); if it stays unwired, do not leave it as a production-looking API.
**N2.** `crates/pi-coding-agent/src/core/context_health_calibration.rs` (702 lines, registered `pub mod` at `core/mod.rs:14`) is referenced by no production code and no integration test — only its own 17 unit tests. The module doc and the changelog bullet (`.changes/context-rot-r2.md:15`) disclose "measurement only", which is accurate; consider a one-line note at the `pub mod` declaration or relocating the module so its in-`src/` placement does not read as production code.
**N3.** Two campaign pub items lack doc comments: `src/bin/compaction_harness.rs:257` `lossy_summary` (`pub` in a bin is also unnecessary) and `compaction/typed_state.rs:324` `is_empty`. All other 62 new pub items are documented, and the defect-tied ones cite their D-numbers (D16 at `request_tokens.rs:43-47`, D17 at `compaction.rs:1365-1368`, D21 in `task_state.rs:87-102`).
**N4.** BUG-LEDGER citation drift: `task_state.rs:146-149` (N2, `entry_text` is now at 162) and `:45-47` (N1, `EVIDENCE_RE` now at 49); D1's "~4846"/"~16499" are now 4876/16580 (the tilde keeps them approximate). The D19/D20 `agent_session.rs` citations are marked "at audit time", so their drift is disclosed. 10 of the 13 `file:line` citations in `reports/context-rot-r2/` still map exactly (all eight `typed_state.rs`/`compaction.rs` citations verified current).
**N5.** `FINAL-REPORT.md` and `PR-BODY.md` contain no `file.rs:NNN` code citations at all; the line-citation surface lives in `BUG-LEDGER.md` (see N4). The brief's compaction.rs-citation concern therefore resolves to "no such citations to drift".
**N6.** Pre-existing warnings, do not fix in this PR: the pi-ai lib reports 9 and the pi-coding-agent lib 355 warnings, but none of the 53 `agent_session.rs` warning lines fall inside campaign diff hunks, and `branch_summarization.rs:10-11`, `memory/search.rs:10` (`HarnessScope`), `messages.rs:468/981` dead items are byte-identical at `origin/main`. W33's "4 campaign-introduced warnings" count is corroborated; their line numbers had drifted (`compaction_harness.rs:29` -> `:32`).
**N7.** Hygiene checks that passed: no emojis in the fragment or any `reports/context-rot-r2/*.md`; the fragment's bullet format matches the house style (plain `Added/Changed/Fixed` bullets, cf. `.changes/compaction-task-state-and-context-health.md`); AGENTS.md "Rust-only" is not violated by the Python research drivers (research tooling, iteration-1 precedent; the rule targets the TypeScript implementation and npm workspaces).

## 3. Surface-1 verification matrix (fragment bullet -> commits -> tree)

| # | fragment line | commits | tree evidence | verdict |
| --- | --- | --- | --- | --- |
| 1 | L1 turn stall (D1) | `0a896e639`, tests `b6487b9cd` | shared predicate `threshold_auto_compaction_ready` def `agent_session.rs:4839`, hook `:4876`, executor `:16580` | TRUE |
| 2 | L2 migration boundary (D8) | `a56631631` | ledger row D8 FIXED; fallback in `session_manager.rs` | TRUE |
| 3 | L3 no-op compaction (D5) | `3b08e3f2e` | ledger D5 FIXED + `compaction_summary_path` cases | TRUE |
| 4 | L4 deadline fallback (D9) | `720428967` | ledger D9 FIXED | TRUE |
| 5 | L5 branch summaries (D10) | `0e25a7db1` | ledger D10 FIXED + `rot2_branch_summary` | TRUE |
| 6 | L6 digest supersession (D11) | `30588707d`, test `d9ceea230` | `prune_superseded_harness_digests` invoked `agent_session.rs:11944,11948` | TRUE (ledger row stale, see S1) |
| 7 | L7 memory-recall fence (D12) | `021678796` | fence constants `messages.rs:1327-1328`, applied `:574-578` | TRUE |
| 8 | L8 fail-closed recall (D13) | `157316268` | ledger D13 FIXED | TRUE |
| 9 | L9 read-files inventory (D14) | `439808412` | ledger D14 FIXED | TRUE |
| 10 | L10 image token cost (D16) | `0bb973e43` | `IMAGE_BLOCK_CHARS` shared at `compaction.rs:1550,1574,1605` + `request_tokens.rs:45`; D16 test green | TRUE |
| 11 | L11 ledger ids (supersession) | `1754a3e1b` | `rot2_ledger_fixes.rs` pins; content-derived `stable_id` `typed_state.rs:977` | TRUE |
| 12 | L12 no-usage estimate (D17) | `0bb973e43`/`fa4ccc8f2` | function exists, zero production callers | **FALSE (B1)** |
| 13 | L13 extension guard (D6) | `289f3c2e1`, wiring `6e1c3a139`, test `e44faff08` | wired at `agent_session.rs:18338` | TRUE |
| 14 | L14 typed mode | `c948905a3`,`338b920c3`,`40bead288`,`4be9623b0` | `resolve_task_state_mode` `compaction.rs:722-736` accepts `typed`, fails-safe to `off`; default `off` (`settings_manager.rs:3902-3906`); typed_state.rs 3033 lines | TRUE (open defects undisclosed, S3) |
| 15 | L15 calibration harness | `6f0d592c6` | module self-contained, 17 unit tests, no production readers | TRUE |
| 16 | L16 subagent constraints | `0c920a95e` | three named scenarios exist in `rot2_subagent_constraints.rs:740,901,1031` | TRUE |
| 17 | L17 compaction-harness binary | `b04d1f040`, live mode `963ca575a` | `[[bin]]` in `Cargo.toml`, 1148 lines, JSON on stdin | TRUE |
| 18 | L18 research harness | `3eeed6f3f` + the research commits | `research/context_rot_r2/` tree (this audit's surface 7) | TRUE |

Missing from the fragment: D18 (`8c558dd7b`), D19 (`4769f42f4`), D21 (`a9035ce8a`) — see S2.

## 4. Test-hygiene sweep (surface 6, new risks only)

- Tempdir discipline: all campaign test files create state under `tempfile::TempDir` with unique prefixes (`rot2_w8-<label>-<tag>`, `rot2-<scenario>-<arm>-`), provider registrations use per-test unique api names (`rot2-lossy-<scenario>-<arm>`, `rot2-wave2-<label>-<tag>`), and captured requests are `Arc<Mutex<...>>`-guarded. No fixed shared paths, no writes outside the tempdir, no sleeps, no stdout/stderr-capture assertions.
- The one new risk is S7 (process-global env mutation in `rot2_typed_adversarial.rs`).
- The known parallel flakes (two environment-bound failures documented in `reports/context-rot-r2/REGRESSION-GATES.md` — `native_host_status_tests.rs:251` unwrap under default threading, plus the name-filter catch) are pre-existing campaign-documented and were not re-litigated here.

## 5. Commit index (this audit)

- `7b570d082` `chore(compaction): drop the four campaign-introduced warnings` — the surface-2 cleanup, verified behavior-preserving (see section 1).
- `reports/context-rot-r2/REVIEW-AUDIT.md` — this file.

## 6. W45 appendix: post-audit status of the should-fix items

Recorded by the W45 audit-fix wave (`rot2/w45-auditfix`). B1 (the D17
changelog-bullet / production-wiring mismatch) is W44's item and is not
touched here. The D22-D29 defect wave the audit could not yet see: after the
audit, the integrator landed the red-team fixes and re-verified them - all of
D22, D23, D24, D25, D26, D27, D28 and D29 are FIXED on the current tip
(commits `a3997b5d6` D25, `891702c32` D23+D24, `51c9a6a59` D22+D26+D27,
`1e6e5ff9d` D28, `4315f9154` D29, all ancestors of the W45 head), each with
its committed red-first test un-ignored in
`crates/pi-coding-agent/tests/rot2_typed_adversarial.rs` (23 tests green,
zero ignored) and D28 pinned in `tests/rot2_native_typed_chain.rs`. The
remaining open items are the D30 classifier gap (an unprefixed decision in
plain user text classifies as step-scoped low-priority material and retires
at the first merge) and the W41 cycle-5 budget caveat (E3ADV tier-8 history
line overflows the fixed 800-token hot budget; the pairing line still
renders, verification passes). S1 (stale in-flight language in PR-BODY and
FINAL-REPORT), S2 (missing D18/D19/D21 and D22-D29 fragment bullets), S3
(the undisclosed red-team wave), S4 (machine paths in research code), S5
(the 8.1 MB `raw-calls.jsonl`) and S7 (the env-mutating test) are addressed
by this wave's commits; S6 is the appendix below. A cosmetic defect this
wave found and left untouched (file map): `BUG-LEDGER.md` carries unresolved
merge-conflict markers from the D22-D29 status merges - the rows are
readable and current, but the integrator should resolve the markers when
next touching that file.

### S6: non-conventional commit subjects, integrator options (no history rewrite performed)

Seven commits on the branch use `research:` / `research(context-rot):`
subjects, which is not a conventional-commit type:

| commit | subject |
| --- | --- |
| `59832805f` | `research(context-rot): r2-e5 typed-arm benchmark results` |
| `fe6b6d966` | `research(context-rot): typed-arm mirror wiring for e3/e3adv (W15 port)` |
| `0b824f358` | `research(context-rot): d21 render replay - hard corpus strict containment 20/20` |
| `4b8f2165f` | `research(context-rot): W26 live-model CompactBench practice run (GLM 5.3)` |
| `2fbb14f33` | `research(context-rot): r2-e2-rm offline re-measure of the w11 ledger fix` |
| `c5107fc31` | `research(context-rot): e3 live results (GLM 5.3, 5 tasks x 2 arms x 5 cycles)` |
| `fb089ee6e` | `research: R2-E2 live-model multicycle experiment (GLM 5.3)` |

The integrator's options:

1. Leave the subjects as committed. Zero risk; the seven are research-only
   commits (data, drivers, READMEs), no code paths. A maintainer reading
   `git log --oneline` sees a non-standard type on seven of 80 commits.
2. Interactive rebase renaming them to conventional subjects
   (`docs(context-rot):` for report/data landings, `test(context-rot):` where
   the commit adds test harness code, `chore(research):` otherwise). Cost: a
   rewritten history invalidates every SHA citation pinned to these commits
   in `reports/context-rot-r2/` (BUG-LEDGER, FINAL-REPORT, EXPERIMENT-LEDGER
   and this file cite at least `59832805f`, `fe6b6d966`, `0b824f358`,
   `4b8f2165f`, `2fbb14f33`, `c5107fc31`), so the rebase would require a
   coordinated citation-refresh pass afterwards. Do this only before the PR
   opens, not after review starts.
3. Add `research` to the project's allowed commit types (a `commitlint`
   config or CONTRIBUTING note), keeping the history intact. Zero risk, but
   it legitimizes the type for future contributors.

This wave's recommendation: option 1 (leave as committed). The citation
churn of option 2 outweighs the cosmetic gain; option 3 is a policy call
that belongs to the maintainers, not this PR.

### S5: large research call logs, git-lfs adoption (implemented)

`research/context_rot_r2/live/raw-calls.jsonl` was the only blob over 5 MB
(8,131,489 bytes). Implemented: `.gitattributes` now carries LFS rules for
the five raw call-log files (`live/raw-calls.jsonl`, `live/e3/raw-calls.jsonl`,
`live/e3adv/raw-calls.jsonl`, `live/e5/live/e3-typed-raw-calls.jsonl`,
`compactbench_adapter/live/raw-calls.jsonl`), and this wave re-staged them
(`git add --renormalize` scoped to exactly those five paths) so their index
blobs are LFS pointers as of this commit. Minimal disruption chosen over
moving the files: no path changes, no citation churn, working-tree contents
byte-identical (verified: 8,131,489 bytes after the operation). History is
not rewritten - the pre-pointer blobs remain in earlier commits, so the
8.1 MB blob exists in history once; the PR diff and all future writes use
pointers. Integrator notes: (a) `git lfs push` must accompany the first
push of these commits or the pointers will not resolve on the remote;
(b) nobody should run a repo-wide `git add --renormalize .`, which would
convert unrelated text files through the lfs filter; (c) the alternative
(drop the telemetry, keep `results.json` aggregates) stays available if
maintainers prefer no LFS dependency - in that case delete the five files
and the attribute block in one commit.
