# Context-rot PR — commit plan

Status: PLAN ONLY. No git command that changes the index, the worktree or the branch was run
by this worker (no add, commit, push, stash, reset, checkout, restore). The parent stages and
commits.

Branch: `improve/context-rot-resilience`, base `8ae54da78d94920b543e22567403b7e939cb5bd2`.
All change is uncommitted in the worktree at plan time.

Toolchain caveat: this host has no `cargo` or `rustc` (`where cargo` fails), so "compiles"
below means "checked by inspection against the call sites that exist in the tree", not
"compiled here". The Python harness was executed: `python -m unittest discover -s
research/context_rot/tests -t .` -> 125 tests, OK, 6.4 s.

## 1. Change inventory (as found in the worktree)

Tracked modifications (9 files, +430/-29):

| file | + | - | content |
| --- | --- | --- | --- |
| `crates/pi-coding-agent/src/core/agent_session.rs` | 1 | 0 | pass `resolved.task_state` into `CompactionSettings` |
| `crates/pi-coding-agent/src/core/compaction/compaction.rs` | 125 | 7 | ledger mode + wiring, cut-point cost, persistence, deadline fallback, test fixture field |
| `crates/pi-coding-agent/src/core/compaction/mod.rs` | 1 | 0 | `pub mod task_state;` |
| `crates/pi-coding-agent/src/core/compaction/utils.rs` | 165 | 3 | evidence-recovery excerpt + 5 unit tests |
| `crates/pi-coding-agent/src/core/mod.rs` | 1 | 0 | `pub mod context_health;` |
| `crates/pi-coding-agent/src/core/settings_manager.rs` | 21 | 0 | `compaction.taskState` setting + env fallback + resolved field |
| `crates/pi-coding-agent/src/modes/interactive/interactive_mode.rs` | 70 | 10 | `get_tray_context_health` / `get_tray_context_health_text` + test |
| `crates/pi-coding-agent/src/modes/interactive/native_host.rs` | 41 | 7 | label ladder, band colour, tray wiring |
| `crates/pi-coding-agent/src/modes/interactive/native_host_status_tests.rs` | 5 | 2 | width-dependent expectation |

Untracked (40 files): `crates/pi-coding-agent/src/core/compaction/task_state.rs` (924 lines),
`crates/pi-coding-agent/src/core/context_health.rs` (409 lines),
`.changes/compaction-task-state-and-context-health.md`,
`research/context_rot/` (28 files), `reports/context-rot/` (9 files).

## 2. Blocking preconditions — apply before the adds

The branch does not compile its test targets and fails one integration test as it stands.
Both defects are outside the diff. Neither is fixable by staging choices.

### 2.1 Five struct literals miss the new fields (compile error, 4 files)

`CompactionSettings` gained `task_state: Option<String>` and `CompactionPreparation` gained
`task_state: Option<TaskStateLedger>`. Struct literals without `..Default::default()` must
set both.

| file:line | literal | add |
| --- | --- | --- |
| `crates/pi-coding-agent/tests/compaction_kernel_parity.rs:877` | `CompactionSettings` (ends `deadline_ms: 0.0,`) | `task_state: Some("off".to_string()),` |
| `crates/pi-coding-agent/tests/compaction_kernel_parity.rs:975` | `CompactionSettings` (ends `deadline_ms: 0.0,`) | `task_state: Some("off".to_string()),` |
| `crates/pi-coding-agent/tests/compaction_length_recovery.rs:162-164` | one-line `CompactionPreparation` | `task_state: None,` |
| `crates/pi-coding-agent/tests/compaction_observability.rs:87-100` | `CompactionPreparation` | `task_state: None,` |
| `crates/pi-coding-agent/tests/compaction_summary_guard.rs:31-40` | `CompactionPreparation` | `task_state: None,` |

Use the explicit `"off"` string in the two settings literals so those parity tests stay
hermetic: `None` makes the resolver read `PRIME_AGENT_COMPACTION_TASK_STATE`.

### 2.2 Two source-text assertions name the removed accessor (test failure, 1 file)

`crates/pi-coding-agent/tests/jev_ui_tests.rs` asserts on source strings:

- line 2234: `tray.contains("get_tray_context_usage_text()")` -> `get_tray_context_health_text()`
- line 2240: `mode.contains("fn get_tray_context_usage_text(")` -> `fn get_tray_context_health_text(`

Neither string exists after the rename, so `the_tray_row_...` fails by construction. That
file is not part of the diff.

## 3. Commit plan (7 commits, in this order)

Ordering rule: non-product first, then the default-path correctness fix, then the opt-in
feature, then the excerpt fix, then the display-only tray change, then the fragment. Every
commit is independently revertable; the only cross-commit coupling is the shared file
`compaction.rs` between commits 3 and 4 (see section 4).

### Commit 1 — `test(context-rot): add the offline context-rot benchmark harness`

```bash
git add   research/context_rot/ARCHITECTURE-AUDIT.md
  research/context_rot/README.md
  research/context_rot/REFERENCES.md
  research/context_rot/__init__.py
  research/context_rot/adapters.py
  research/context_rot/cli.py
  research/context_rot/corpus.py
  research/context_rot/distractors.py
  research/context_rot/harness.py
  research/context_rot/multicycle.py
  research/context_rot/needles.py
  research/context_rot/report.py
  research/context_rot/runs/.gitignore
  research/context_rot/scoring.py
  research/context_rot/stress.py
  research/context_rot/tasks.py
  research/context_rot/tests/__init__.py
  research/context_rot/tests/test_adapters.py
  research/context_rot/tests/test_cli.py
  research/context_rot/tests/test_corpus.py
  research/context_rot/tests/test_distractors.py
  research/context_rot/tests/test_harness.py
  research/context_rot/tests/test_multicycle.py
  research/context_rot/tests/test_needles.py
  research/context_rot/tests/test_report.py
  research/context_rot/tests/test_scoring.py
  research/context_rot/tests/test_stress.py
  research/context_rot/tests/test_tasks.py
```

- 28 files, no product code, no Rust. `requests` is imported lazily in `adapters.py`; the
  suite runs with no network.
- `research/context_rot/runs/.gitignore` (`*` and `!.gitignore`) keeps run output out;
  `__pycache__` and `*.pyc` are already ignored by the root `.gitignore`.
- What leaves it buildable: nothing depends on it. Gate: the unittest command above (125
  tests, OK here).

### Commit 2 — `docs(context-rot): add the campaign evidence pack`

```bash
git add   reports/context-rot/BASELINE.md
  reports/context-rot/BUG-LEDGER.md
  reports/context-rot/CODING-TASK-RESULTS.md
  reports/context-rot/COMPACTION-FIDELITY.md
  reports/context-rot/CONTEXT-ROT-CURVES.md
  reports/context-rot/EXPERIMENT-LEDGER.md
  reports/context-rot/FINAL-REPORT.md
  reports/context-rot/MULTICYCLE-DRIFT.md
  reports/context-rot/RETRIEVAL-SCORECARD.md
git add reports/context-rot/PR-BODY.md reports/context-rot/COMMIT-PLAN.md   # optional, see note
```

- 9 evidence files. Drop the last line if the repository should not carry the PR description;
  `reports/full-harness-r2/PR-BODY.md` is tracked, so carrying it matches precedent.
- Docs only. No gate beyond spelling and the report index in `FINAL-REPORT.md` section 8.

### Commit 3 — `fix(compaction): charge injected blocks against keepRecentTokens`

Hunk-level stage; `compaction.rs` is shared with commit 4.

```bash
git add -p crates/pi-coding-agent/src/core/compaction/compaction.rs
# stage ONLY these two hunks:
#   @@ -1402,6 +1444,26 @@  (fn entry_token_cost)
#   @@ -1432,10 +1494,14 @@ (find_cut_point accumulation)
```

- Self-contained: `entry_token_cost` uses only `estimate_tokens`, `count_chars` and
  `ceil_div4`, which already exist in the file.
- Behaviour change on the default path: custom messages and branch summaries now cost
  tokens; `Compaction` and `Other` entries still cost zero.
- Alternative if the parent refuses hunk staging: fold this hunk into commit 4 and say so in
  the commit body. The two cannot be split by path.
- Gate: `cargo test -p pi-coding-agent --lib` and `--test compaction_recovery`.

### Commit 4 — `feat(compaction): add an opt-in protected task-state ledger`

```bash
git add crates/pi-coding-agent/src/core/compaction/task_state.rs
git add crates/pi-coding-agent/src/core/compaction/mod.rs
git add crates/pi-coding-agent/src/core/compaction/compaction.rs
git add crates/pi-coding-agent/src/core/settings_manager.rs
git add crates/pi-coding-agent/src/core/agent_session.rs
git add crates/pi-coding-agent/tests/compaction_kernel_parity.rs
git add crates/pi-coding-agent/tests/compaction_length_recovery.rs
git add crates/pi-coding-agent/tests/compaction_observability.rs
git add crates/pi-coding-agent/tests/compaction_summary_guard.rs
```

- `git add` on `compaction.rs` stages the remaining hunks only because commit 3 already
  staged the two cut-point hunks. Verify with `git diff --cached --stat` before committing.
- Includes the four test files from section 2.1: without them this commit cannot compile.
- Default off: `default_compaction_settings()` sets `task_state: Some("off")`; with off the
  summarizer prompt is unchanged (`(!ledger.is_empty()).then_some(ledger)` plus the resolver).
- Gate: `cargo test -p pi-coding-agent --lib` and the four test targets above.

### Commit 5 — `fix(compaction): recover evidence lines from the elided tool-result middle`

```bash
git add crates/pi-coding-agent/src/core/compaction/utils.rs
```

- Independent of commits 3 and 4; could be reordered before them without conflict.
- Plain output is unchanged when the elided middle holds no evidence line (pinned by
  `truncate_for_summary_is_unchanged_without_middle_evidence`).
- Gate: `--lib` (5 new tests) and `--test compaction_kernel_parity` (it pins the plain
  2001-character excerpt).

### Commit 6 — `feat(tui): show context health in the tray row`

```bash
git add crates/pi-coding-agent/src/core/context_health.rs
git add crates/pi-coding-agent/src/core/mod.rs
git add crates/pi-coding-agent/src/modes/interactive/interactive_mode.rs
git add crates/pi-coding-agent/src/modes/interactive/native_host.rs
git add crates/pi-coding-agent/src/modes/interactive/native_host_status_tests.rs
git add crates/pi-coding-agent/tests/jev_ui_tests.rs
```

- Includes the section 2.2 assertion fix; without it the commit is red.
- Display only: no compaction decision reads the score, and a session with no token snapshot
  and no compaction cycle renders nothing.
- Gate: `--lib` (12 module tests + 1 mode test) and `--test jev_ui_tests`.

### Commit 7 — `docs(changes): add the compaction task-state and context-health fragment`

```bash
git add .changes/compaction-task-state-and-context-health.md
```

- Last so the fragment's bullets match the subjects above.
- Open issue: AGENTS.md requires every bullet to begin with Added/Changed/Fixed/Removed. This
  fragment uses `Added`/`Changed`/`Fixed` as section headings and the bullets begin with
  backticked names or "The tray indicator"; 115 of the 133 existing fragments use the flat
  `- Added ...` form and this is the only heading-style fragment. Rewrite to flat bullets
  before committing, or accept the deviation knowingly.
- Gate: none (docs), plus `bash scripts/check.sh` if it validates fragments.

## 4. Staging mechanics and safety

1. `git -C C:/Users/openclawuser/optimus-rot-20261018 status --short` before every commit;
   it must show only the paths listed for that commit. Never `git add -A` or `git add .`.
2. Do not stage `research/context_rot/runs/20261019T001229Z-selftest/` (ignored: `runs/.gitignore`
   is `*` with `!.gitignore`) or any `__pycache__` (ignored by the root `.gitignore`).
3. Do not stage the lab artefacts: every real-model JSONL quoted in the reports lives outside
   the tree in `C:/Users/openclawuser/optimus-rot-lab/runs/`. The reports say so; the commits
   carry no run data.
4. `compaction.rs` staging order is one-way. Commit 3 must be committed before the `git add`
   of commit 4, otherwise the file lands whole in one commit.
5. Suggested check after each Rust commit: `bash scripts/check.sh` and
   `cargo test -p pi-coding-agent --lib`. After commit 6 also run the integration targets
   named above. None of these were run here: no Rust toolchain on this host.

## 5. Rollback map

| feature | revert unit | runtime kill switch |
| --- | --- | --- |
| ledger | commit 4 (+ its bullet in the fragment) | `compaction.taskState = "off"` (default) or unset `PRIME_AGENT_COMPACTION_TASK_STATE` |
| cut-point accounting | commit 3 | none; it is unconditional. `git revert` of commit 3 after commit 4 conflicts in `compaction.rs`; revert commit 4 first, then commit 3 |
| evidence excerpt | commit 5 | none; plain text is unchanged when no evidence line sits in the elided middle |
| context health | commit 6 | none; display only, no score consumer |
| harness and reports | commits 1 and 2 | none; not loaded by the product |

## 6. Known gaps left for the parent (not in the diff)

1. `resources/agent/docs/settings.md` documents `compaction.enabled`, `reserveTokens`,
   `keepRecentTokens`, `summaryUpdatePolicy` and does not list `compaction.taskState`.
2. No unit test covers `resolve_task_state_mode` / `get_compaction_task_state` precedence
   (explicit setting wins over a bad value, then env, then off). The code says so; nothing
   pins it.
3. `research/context_rot/tests` is not wired into `test.sh`, which discovers only `tests/`.
   The 125 tests run only through the explicit command above.
