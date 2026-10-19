# REGRESSION GATES — the r2 branch's test state

Date: 2026-10-20. Worker W14, report pass. Every count below was produced
in this worktree on this branch; commands and environment are recorded so
the run is repeatable.

## 1. Environment

| item | value |
| --- | --- |
| host | Windows, git-bash |
| worktree | `C:/Users/openclawuser/optimus-rot2-w14-reports`, branch `rot2/w14-reports`, HEAD `0ec43c8cb` (integration content; 23 commits over base `6000d6bc1`) |
| dirty files | the five report files this worker adds under `reports/context-rot-r2/`; nothing else (`git status --porcelain`) |
| toolchain | rustc 1.98.1 (48a229cea 2026-09-01), cargo 1.98.1, `RUSTUP_HOME`/`CARGO_HOME` = `C:/Users/openclawuser/optimus-rust-toolchain/...` |
| cargo target | ISOLATED `C:/Users/openclawuser/rot2-w14-target` (fresh full build; first suite ~20 min wall including the dependency build, later suites 1-2.5 min each) |
| python (research tests) | system python 3.11.15, pytest 9.1.1 (stdlib-only suites also verified with `python -m unittest`) |
| python (adapter + integration tests) | campaign cb-venv 3.11.15 (`C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv`, has compactbench + pydantic + pyarrow) |

## 2. Rust suites (`cargo test -p pi-coding-agent ...`)

| suite | touched by r2 | result | notes |
| --- | --- | --- | --- |
| `--lib compaction` (name filter) | `fa4ccc8f2`, `0bb973e43` (compaction estimators), `2200211fe` (context_health docs) | 197 passed, 2 failed, 3619 filtered out, 92.4 s | both failures are pre-existing/environmental, see section 4; not r2 regressions |
| `--test rot2_multicycle` | `e249930d9` (new file), `3eeed6f3f`, `0bb973e43` | 4 passed, 0 failed, 1 ignored | the ignored one is the research entry point `rot2_production_multicycle` (`#[ignore = "research adapter: requires ROT2_SCENARIO/ROT2_RESULT env"]`); passing: the smoke multicycle, D16, D17 and lossy-fixture tests |
| `--test compaction_turn_flow` | `b6487b9cd` (D1 reproduction + D2 guards, new file) | 3 passed, 0 failed | `d1_toggle_off_over_threshold_does_not_stop_the_turn`, both `d2_*` guards pass on this branch: the D1 fix holds |
| `--test compaction_recovery` | not touched (adjacent gate) | 9 passed, 0 failed | includes `agent_session_auto_compaction_end_to_end` |
| `--test compaction_model_threshold` | not touched (adjacent gate) | 10 passed, 0 failed | |
| `--test compaction_fidelity` | not touched (adjacent gate) | 1 passed, 0 failed | |
| `--test compaction_summary_guard` | not touched (adjacent gate) | 2 passed, 0 failed | |

The D-fix guard map (which test covers which defect) is in
COMPATION-CORRECTNESS.md section 5.

## 3. Python suites (research tree)

| suite | touched by | command | result |
| --- | --- | --- | --- |
| `research/context_rot_r2/tests/` (scenario/scoring harness) | `3eeed6f3f` | `python -m pytest research/context_rot_r2/tests/ -q` | 5 passed in 0.19 s (also 5/5 via stdlib `unittest discover`) |
| `research/context_rot_r2/incremental/tests/` (w9 variants) | `0229dee3a` | `python -m pytest research/context_rot_r2/incremental/tests/ -q` | 6 passed in 0.17 s |
| `research/context_rot_r2/contextbench/tests/` (w10) | `ab71234bc`, `29be98f76`, `3a1d5ee4d`, `86e8ad09b` | `python -m pytest research/context_rot_r2/contextbench/tests/ -q` (system python) | 124 passed, 3 skipped in 1.62 s |
| same, integration tests on | `29be98f76` | cb-venv python + `CB_REFS_ROOT=C:/Users/openclawuser/optimus-rot2-w5-research/refs/ContextBench`, `python -m unittest discover -s research/context_rot_r2/contextbench/tests -t .` | 127 passed, 0 skipped in 7.59 s (the 3 gated tests verify the real parquet sha256, the row shape, and that the committed `data/dev_holdout_split.json` regenerates exactly) |
| `research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py` (w6 adapter checks) | `00cbdd3ab` | cb-venv python + `HARNESS=C:/Users/openclawuser/rot2-w14-target/debug/compaction-harness.exe` (built from this branch) | 9/9 checks passed: task-state arm, plain arm, cut-point ladder, ledger mapping, starter-shaped transcript, determinism, small-transcript fallback, missing-binary degradation, controls |

## 4. The two `--lib compaction` failures, and why they are not r2 regressions

1. `core::agent_session::post_compaction_continuation_tests::enabled_skill_is_importable`
   - failure: `uv pip install runs: Error { kind: NotFound, message:
     "program not found" }` - `uv` is not on `PATH` for the test child
     process on this host.
   - This is the same environment gap iteration-1 documented at its
     baseline (`reports/context-rot/REGRESSION-GATES.md` section 2: the
     same test, same cause, "fails identically at the baseline commit").
     It matched this run only because the `compaction` name filter catches
     the whole `post_compaction_continuation_tests` module.
2. `modes::interactive::...::native_status::tests::compaction_count_stays_before_the_meter_with_goal_and_heartbeat`
   - failure under default threading: `Option::unwrap()` on `None`
     (`native_host_status_tests.rs:251`).
   - Serialized rerun in this gate pass:
     `cargo test -p pi-coding-agent --lib compaction_count_stays --
     --test-threads=1` -> 1 passed in 0.40 s. This is the painted-frame
     family iteration-1 documented as scheduling-sensitive (same file
     family, `reports/context-rot/REGRESSION-GATES.md` section 3).
   - The r2 campaign did not touch the file:
     `git log --oneline 6000d6bc1..HEAD --
     crates/pi-coding-agent/src/modes/` is empty.

## 5. Not run, and why

| item | reason |
| --- | --- |
| the `#[ignore]`d `rot2_production_multicycle` entry point | it is the research adapter that executes full experiment runs (`ROT2_SCENARIO`/`ROT2_RESULT`); this worker's brief is reports only, no new experiments. The committed runs (`research/context_rot_r2/runs/`, `incremental/runs/`) are the campaign's data; W9's byte-equal rerun already covers determinism |
| `--test compaction_summary_path.rs` (D5/D6/D9 guards) | the file does not exist on this branch; it lives on `rot2/w2-summarypath` with the three un integrated fixes (COMPATION-CORRECTNESS.md section 2) |
| other compaction suites (`compaction_kernel_parity`, `compaction_parallel_fallback`, `compaction_anthropic_length_recovery`, `compaction_chat_length_recovery`, `compaction_length_recovery`, `compaction_observability`, `compaction_parity_suite`) | outside the brief's gate list; not touched by r2 commits, not run in this pass |
| the full `--lib` suite (3 818 tests) and other crates | outside the brief's gate list; the campaign common rules mandate focused tests only (shared-host contention) |
| official CompactBench suites beyond the committed starter runs, ContextBench live trajectories | offline rule; see CODING-TASK-RESULTS.md section 4 |

## 6. Verdict

On this branch, every suite the campaign touched or named passes: the
r2 adapter suite (with its research entry properly ignored), the D1/D2
turn-flow guards, the recovery, model-threshold, fidelity and
summary-guard gates, and all four Python research suites including the
adapter checks against a harness binary built from this branch. The two
`--lib` filter-run failures are a documented host environment gap (`uv`
missing) and a scheduling-sensitive painted-frame test that passes
serialized, both outside r2's diff. The un integrated W2 fixes (D5/D6/D9)
and their new test file remain to be gated once integrated.
