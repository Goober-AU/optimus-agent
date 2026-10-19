# REVIEW AUDIT — pre-PR reviewer simulation (W42)

Hostile-reviewer pass over the campaign PR (base `origin/main` `6000d6bc1` -> integration tip `29f1f0c92`, 80 commits, 328 files, +149,503/-182). Audit worktree `optimus-rot2-w42-audit`, branch `rot2/w42-audit`. Method: changelog fragment vs landed commits vs tree (git grep against HEAD, bounded paths), full `cargo check -p pi-coding-agent --all-targets` in an isolated target dir, focused test runs, and bounded greps inside `research/context_rot_r2/` + `reports/context-rot-r2/` only. No repo-wide sweeps; no history rewrites.

Verdict up front: one blocker (a changelog bullet claims a behavior change that is not wired into any production call site), seven should-fix findings (three documentation-staleness gaps, missing changelog bullets, undisclosed open defects in the new typed mode, machine-specific paths committed in research code, an 8.1 MB JSONL, seven non-conventional commit subjects, one env-mutating test), and seven nits. The four campaign-introduced warnings are fixed by the cleanup commit at the tip of this branch. The other 17 fragment bullets verified true in the tree.

## 1. Cleanup commit (surface 2)

`fa3634b7a` `chore(compaction): drop the four campaign-introduced warnings`

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
| 17 | L17 compaction-harness binary | `b04d1f040`, live mode `963ca577a` | `[[bin]]` in `Cargo.toml`, 1148 lines, JSON on stdin | TRUE |
| 18 | L18 research harness | `3eeed6f3f` + the research commits | `research/context_rot_r2/` tree (this audit's surface 7) | TRUE |

Missing from the fragment: D18 (`8c558dd7b`), D19 (`4769f42f4`), D21 (`a9035ce8a`) — see S2.

## 4. Test-hygiene sweep (surface 6, new risks only)

- Tempdir discipline: all campaign test files create state under `tempfile::TempDir` with unique prefixes (`rot2_w8-<label>-<tag>`, `rot2-<scenario>-<arm>-`), provider registrations use per-test unique api names (`rot2-lossy-<scenario>-<arm>`, `rot2-wave2-<label>-<tag>`), and captured requests are `Arc<Mutex<...>>`-guarded. No fixed shared paths, no writes outside the tempdir, no sleeps, no stdout/stderr-capture assertions.
- The one new risk is S7 (process-global env mutation in `rot2_typed_adversarial.rs`).
- The known parallel flakes (two environment-bound failures documented in `reports/context-rot-r2/REGRESSION-GATES.md` — `native_host_status_tests.rs:251` unwrap under default threading, plus the name-filter catch) are pre-existing campaign-documented and were not re-litigated here.

## 5. Commit index (this audit)

- `fa3634b7a` `chore(compaction): drop the four campaign-introduced warnings` — the surface-2 cleanup, verified behavior-preserving (see section 1).
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

## 7. AUDIT RESOLUTION — final disposition (W59)

Recorded by the W59 final-resolution pass (worktree `optimus-rot2-w59-audit2`,
branch `rot2/w59-audit2`) at integration tip `0a35fbfa6` (130 commits over
base `6000d6bc1`). Method: full read of the three reviewer-facing files
(`PR-BODY.md`, `BUG-LEDGER.md`, `.changes/context-rot-r2.md`); `git log`/
`git show` on specific paths, including bounded reads of exactly the files
the audit items and the cross-check name (the two adversarial test files,
`rot2_multicycle.rs`, the live drivers, `run-config.json`, `.gitattributes`,
report headers); and two bounded `git grep` sweeps against HEAD (conflict
markers; embedded `git log --oneline` lines) over `reports/context-rot-r2/`
and the fragment only, with bounded context reads around each hit. No test
was run (docs-only pass; W59 file map). This section supersedes sections 1-6
where they conflict. One citation correction was applied to this file: the
cleanup commit cited at lines 9 and 106 used the W42 worktree sha
`7b570d082`; the ancestor on this branch is `fa3634b7a` (same subject), and
both occurrences now cite it — the same class of fix the W50 sweep applied to
the `963ca575a` typo.

### B1 — RESOLVED (claim rescoped to what the tree does)

`b65e2cd2e` rewrote the D17 fragment bullet to a request-aware estimator
"verified against the kernel-parity mirror and exercised by the multicycle
adapter; production call-site wiring remains follow-up work, the no-usage
fallback path is unchanged", and the ledger D17 row to
`FIXED (W3 estimator; call-site wiring deferred)` with the post-audit note
naming the six unchanged production estimator call sites. Both verified at
HEAD; the false "pressure readings" claim is gone from every
reviewer-facing file. N1 rides the same disclosed follow-up: the
request-aware variant stays a pub, adapter-exercised API until the wiring
lands.

### S1 — substance resolved by W45/W50; the landing itself regressed (R1/R2)

The D18-D21 "pending / in flight" language S1 flagged is gone from PR-BODY
and FINAL-REPORT (the W45 refresh `68ca1cc81` + `c72df0c31`, then the W50
sweep `2ed67a8b3`, whose commit message records it as extending S1 to the
whole report set), and the ledger rows are current. Two regressions came
with the landing: the W50 sweep was committed with PR-BODY.md's conflicts
unresolved, and both PR-BODY.md and FINAL-REPORT.md now carry paste debris
plus in-flight claims that went stale when W46/W47 landed after them (R1/R2
below).

### S2 — RESOLVED, residual fragment debris (integrator-owned)

The D18/D19/D21 and D22-D29 bullets are present. Residuals at HEAD, all in
the integrator-owned fragment (outside the W59 file map): twelve bullet
texts appear twice (the W45 `9cf87b266` and W50 `5e2491cff` fragment
commits both landed and unioned); a stray `D` line after the D11 bullet
(fragment line 12); two embedded `git log` lines citing worker-tree shas
`78ccdf254`/`557ca0c0c` (lines 30/45) that are not ancestors of this
branch; and no D30 bullet — the W46 fix (`10e2b36e6`) postdates both
fragment waves, so both typed-mode bullets still list the D30 classifier
gap as the "known remaining gap" although the ledger row and the pinned
smoke seed (user entry `ARCH-DECISION-C`, probe q4,
`tests/rot2_multicycle.rs:970-978`) mark it FIXED.

### S3 — RESOLVED

PR-BODY carries the W35 red-team table (D22-D29 with severities and fix
shas) plus the "Disclosed, not claimed away" paragraph; the fragment's
typed-mode bullets carry the same disclosure. Residual: the disclosed-open
list is stale on D30 (see S2), and PR-BODY's own conflict markers sit above
it (see R1).

### S4 — RESOLVED

`8e7796702`: `contextbench/live/harness_client.py` resolves both
credential scripts from `ROT2_GLM_RESOLVER_SCRIPT` /
`ROT2_POMERIUM_RESOLVER_SCRIPT` with no machine default — it fails closed
with a clear message (verified at HEAD); `compactbench_adapter/live/run_live.py`
takes `BENCHMARKS` from `ROT2_COMPACTBENCH_BENCHMARKS` only and requires
`HARNESS`. `run-config.json` keeps its machine paths by recorded ruling
(artifact provenance, not reusable input), documented in the two live
READMEs and the commit message.

### S5 — RESOLVED

`.gitattributes` carries LFS rules for exactly the five raw call-log
files; all five are LFS pointers in the HEAD tree; the working-tree file
is still 8,131,489 bytes. History was not rewritten. The S5 appendix
integrator notes stand: (a) `git lfs push` must accompany the first push;
(b) no repo-wide `git add --renormalize .`; (c) the drop-the-telemetry
alternative remains open to the maintainers.

### S6 — CLOSED: leave as committed (option 1)

No history rewrite happened: all seven cited subjects exist with unchanged
shas (`59832805f`, `fe6b6d966`, `0b824f358`, `4b8f2165f`, `2fbb14f33`,
`c5107fc31`, `fb089ee6e`). The count grew from seven to twelve — the W47
E5-RM wave added `0f60ee761`, `83ae4fb32`, `8ebc8155b`, `621dc462f`,
`f932b746e` — which only strengthens the appendix's citation-churn
argument against a rebase at 130 commits over base.

### S7 — RESOLVED

`4144028c1`: `explicit_off_setting_wins_over_the_typed_environment_variable`
now exercises the precedence through the explicit `environment` parameter
of `resolve_task_state_mode`; the single env readback asserts only when
the host has not exported the variable; no `set_var`/`remove_var` remains
in `tests/rot2_typed_adversarial.rs` (verified at HEAD; the module doc
records the rationale).

### Nits

N1 rides B1's disclosed follow-up. N3 was not acted on (nit; no action was
requested): `lossy_summary` (`src/bin/compaction_harness.rs:257`) and
`is_empty` (`src/core/compaction/typed_state.rs:441`) still lack doc
comments. N2 and N4-N7 were observations with no action requested and
stand as recorded; the W50 sweep refreshed the drifted citations it found
in the report set.

### Post-audit defect waves (context for the residual-risk list)

- D30 FIXED (W46, `10e2b36e6`; the W41 cycle-5 budget caveat pinned by
  `d00ff2067`; ledger row current). The files that still disclose the gap
  as open are the S2/S3 residuals above.
- D31 OPEN at this tip: no fix commit is an ancestor of `0a35fbfa6`; the
  fix is in flight on `rot2/w52` at the time of this writing. The red test
  `retired_ids_do_not_vouch_for_verified_claims` stays ignored in
  `tests/rot2_wave_adversarial.rs` (runnable red with `--ignored`). Risk as
  recorded in the W48 row: the fabricated-claim check accepts retired
  (tombstoned) record and entry ids as vouching mappings, so a "verified"
  claim citing a retired id passes. If the w52 fix lands before the PR
  opens, replace this bullet with its sha.
- D32 FIXED: the on-branch fix is `53ab2e3d9` (compaction.rs, plus the red
  test un-ignored in `tests/rot2_wave_adversarial.rs`); the ledger-marking
  commit is `b12560575`. The D32 row's cited `aab8e4c58` is the worker-tree
  original, not an ancestor of this branch (see R3).
- D33 OPEN (filed by `2e727e0b6`; fix direction in the row: retire absorbed
  ids into `retiredRefs` at absorption time).
- Post-audit docs work: the typed mode is now user-documented (`a5362aa63`
  README, `0a35fbfa6` settings reference and settings-manager doc comments).

### PR-BODY reviewer-checklist cross-check (paths, shas, sections)

- Paths: every file and directory the body cites exists at HEAD. The
  crate-relative `src/...` and `tests/...` mentions resolve under
  `crates/pi-coding-agent/`; the two glob references
  (`runs/r2-e1-multicycle.*`, `repro/typecompact_repro*.json`) match
  committed files.
- Shas: all 39 commit citations resolve to ancestors of HEAD (`48a229cea`
  is the rustc build hash, not a commit). The only non-resolving sha in
  the file is `78ccdf254`, and it occurs only inside the six conflict
  blocks (R1).
- Sections: "FINAL-REPORT section 4.4" exists and is the right section (the
  typed-state red team and the D21-D29 fix wave); "BUG-LEDGER.md section 4"
  exists (the W35 findings). One drifted pointer: the Testing section's
  "documented at `reports/context-rot/REGRESSION-GATES.md` and r2's section
  4" — the r2 gates file carries the environmental exceptions in section 5
  (its section 4 is the Python research suites; iteration-1's equivalent
  material is section 3).
- Count drift: "23 tests green, zero ignored" for
  `rot2_typed_adversarial.rs` was exact at the W50 tip (`2ed67a8b3`) and
  is 24 at HEAD (the W46 D30 tests added one); zero ignored still holds.
  The W48 suite `rot2_wave_adversarial.rs` (8 tests, one ignored — the D31
  red) is not yet in the body's gate table.

### Residual risks at this tip (integrator checklist before the PR opens)

R1. PR-BODY.md carries six unresolved merge-conflict blocks (lines 5-22,
    61-74, 97-140, 472-490, 495-506, 598-605), each naming worker sha
    `78ccdf254`; the W50 landing `2ed67a8b3` committed them. Both sides
    describe the D18-D21 fixes as landed, so no information is lost —
    resolve each block by picking a side. The W50-side text also still
    says "E5-RM, W47, in flight" (three places) and "the W46 fix is in
    flight": both landed since (`8ebc8155b`/`83ae4fb32`/`0f60ee761`,
    `10e2b36e6`). A bounded marker sweep shows PR-BODY.md is the only
    file under `reports/context-rot-r2/` still carrying markers.
R2. FINAL-REPORT.md (outside the W59 file map; found by the bounded
    embedded-log sweep) carries 29 pasted `git log --oneline` lines citing
    worker shas `f638de7b9`/`78ccdf254` (not ancestors) at eleven
    locations, several interrupting tables and lists (the hypotheses
    table at lines 53-55; the reviewer-check numbered list at 611-613),
    and its intro, section 5 and section 8 still say the D30/W46 recovery
    is "in flight / NOT in this tree" — false at HEAD. EXPERIMENT-LEDGER.md
    carries two of the same pasted lines (65, 472).
R3. BUG-LEDGER.md debris: a stray `D` line (112) between the D31/D32 rows;
    three embedded log lines (81, 115, 116) citing non-ancestor worker
    shas; and the D32 row's `aab8e4c58` citation — the on-branch fix to
    cite is `53ab2e3d9`.
R4. Fragment debris and the missing D30 bullet (the S2 residuals).
R5. D31 open (in flight on `rot2/w52`): the typed verifier's
    fabricated-claim check accepts retired-id citations.
R6. D33 open: absorbed-record reference stranding.
R7. Disclosed open/deferred, unchanged: D7, D12 partial (soft fence), D20
    residue, D10 "never retry" sub-item, W9-F1 v1 capture gap, D17
    production wiring follow-up.
R8. `git lfs push` must accompany the first push (S5 note a).

Reproduce the debris sweeps (bounded, against HEAD):
`git grep -n "^<<<<<<< " HEAD -- reports/context-rot-r2/` for R1, and
`git grep -n -E "^[0-9a-f]{9} \((docs|research)" HEAD -- reports/context-rot-r2/ .changes/context-rot-r2.md`
for R2-R4. R1-R4 are outside the W59 file map (this pass edits
REVIEW-AUDIT.md only); they are recorded here so the integrator can sweep
them in one pass before the PR opens.
