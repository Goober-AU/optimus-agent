# REGRESSION GATES — the r2 branch's test state (final tip)

Date: 2026-10-19 (UTC). Worker W25, restart pass: the first W25 run was
lost to a disk incident before commit, so every number below comes from a
clean re-run in this worktree. The integration tip moved three times
during the pass (W26/W27 live runs -> `f8ad670c8`; the W30/W31/W34/W35
defect and red-team wave -> `f7bc6306c`; the W29-E5 typed-arm and W40
F3-sweep wave -> `29f1f0c92`); after each move the branch was rebased and
every affected gate re-run. All numbers below are from the final tip
`29f1f0c92` (= `improve/context-rot-iteration-2`, 80 commits over base
`6000d6bc1`) unless a row states an earlier run SHA — those rows are
byte-identical sources: the last wave touched only
`crates/pi-coding-agent/tests/rot2_multicycle.rs` and research/report
files, no library or other test-binary source. This pass supersedes the
W14 gate report (written at `0ec43c8cb`, 23 commits over base).

## 1. Environment

| item | value |
| --- | --- |
| host | Windows, git-bash |
| worktree | `C:/Users/openclawuser/optimus-rot2-w25-gates`, branch `rot2/w25-gates` rebased onto `improve/context-rot-iteration-2` = `29f1f0c92`, plus this worker's reconciliation and report commits |
| dirty files | only the report files this worker edits under `reports/context-rot-r2/` (`git status --porcelain`) |
| toolchain | rustc 1.98.1 (48a229cea 2026-09-01), cargo 1.98.1 (797e8a9bc 2026-08-05); `RUSTUP_HOME`/`CARGO_HOME` under `C:/Users/openclawuser/optimus-rust-toolchain/` |
| cargo target | ISOLATED `C:/Users/openclawuser/rot2-w25-target` (fresh full build `cargo test -p pi-coding-agent --no-run` = 1 193.5 s at `30588707d`; rebuild after the `f7bc6306c` rebase = 900.1 s — the library and every integration-test binary except `rot2_multicycle` are byte-identical between `f7bc6306c` and `29f1f0c92`) |
| base tree for comparisons | `git archive 6000d6bc1` exported to `C:/Users/openclawuser/rot2-w25-base-export` (no worktree touched), built with ISOLATED `C:/Users/openclawuser/rot2-w25-target-base` |
| python (research tests) | system python 3.11.15 |
| python (adapter + ContextBench integration tests) | campaign cb-venv 3.11.15 (`C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv`) |

## 2. Rust lib-filter suites (`cargo test -p pi-coding-agent --lib <filter>`)

| filter | result | notes |
| --- | --- | --- |
| `core::compaction` | 146 passed, 0 failed, 0 ignored, 3 759 filtered out (0.29 s; run at `f7bc6306c`) | the campaign's compaction-module units: estimators (`fa4ccc8f2`/`0bb973e43`), typed_state v2 (`c948905a3`), ledger ids (`1754a3e1b`), the D5/D6/D9/D14 changes (`3b08e3f2e`/`289f3c2e1`/`720428967`/`439808412`), D10 branch input (`0e25a7db1`), typed wiring (`338b920c3`/`4be9623b0`) |
| `typed_state` | 60 passed, 0 failed, 3 845 filtered out (0.10 s; run at `f7bc6306c`) | the typed state v2 module (`c948905a3`; 60 unit tests) |
| `context_health_calibration` | 17 passed, 0 failed, 3 888 filtered out (0.02 s; run at `f7bc6306c`) | the W8 calibration corpus and metrics (`6f0d592c6`) |
| `compaction` (W14's wider continuity filter) | 263 passed, 2 failed, 3 640 filtered out (24.9-33.0 s over two runs; run at `f7bc6306c`) | the 2 failures are the documented environmental pair (section 5); 265 tests run vs W14's 199 — typed_state v2's 60 units and the new campaign fixtures account for the growth. Includes the W30 in-file guards `d18_failure_notice_is_model_visible_and_bounded` and `d19_live_disclosure_lists_are_bounded_with_a_dropped_marker` (module path `post_compaction_continuation_tests::compaction_retry_backoff_tests`; both green) |

## 3. Rust integration suites (`cargo test -p pi-coding-agent --test <name>`)

rot2_* (campaign suites; `rot2_multicycle` re-run at `29f1f0c92` after the
E5 wave changed its file — the other eight ran at `f7bc6306c`, sources
byte-identical at `29f1f0c92`):

| suite | result | introduced by |
| --- | --- | --- |
| `rot2_multicycle` | 5 passed, 1 ignored | `e249930d9`, `3eeed6f3f`, `0bb973e43`, `08f16adf0` (typed arm) |
| `rot2_ledger_fixes` | 7 passed | `1754a3e1b` |
| `rot2_subagent_constraints` | 3 passed | `0c920a95e` |
| `rot2_branch_summary` | 2 passed | `0e25a7db1` |
| `rot2_typed_integration` | 9 passed | `40bead288` |
| `rot2_harness_digest` | 2 passed | `d9ceea230` (fix `30588707d`) |
| `rot2_defect_wave2` | 3 passed | `8c558dd7b`/`e44faff08` (D18/D20 guards) |
| `rot2_render_cap` | 7 passed | `a9035ce8a` (D21 guards) |
| `rot2_typed_adversarial` | 8 passed, 6 ignored | `f7bc6306c` (W35 red-team) |

The ignored `rot2_multicycle` test is `rot2_production_multicycle`
(`#[ignore = "research adapter: requires ROT2_SCENARIO/ROT2_RESULT env"]`);
the five passing are the smoke multicycle, the D16 image-cost guard, the
D17 request-level estimate guard, the lossy-fixture protected-block test
and the new typed-arm smoke (`rot2_smoke_typed_arm_persists_typed_ledger_and_verifies`).
The six ignored `rot2_typed_adversarial` tests are the documented
defects D22-D27, each `#[ignore]`d with its D-number as the reason
(BUG-LEDGER); D28 has no test (native fixture outside the worker's file
map).

compaction_* (the brief's gate list; pre-existing suites unless noted; all
run at `f7bc6306c`, sources byte-identical at `29f1f0c92` — the E5 wave
touched no compaction_* file):

| suite | result | r2 commits touching the suite |
| --- | --- | --- |
| `compaction_turn_flow` | 3 passed | `b6487b9cd` (D1 reproduction + D2 guards) |
| `compaction_recovery` | 9 passed | none (adjacent gate) |
| `compaction_model_threshold` | 10 passed | none (adjacent gate) |
| `compaction_fidelity` | 1 passed | none (adjacent gate) |
| `compaction_summary_guard` | 2 passed | `338b920c3` (fixture mode plumbing) |
| `compaction_observability` | 7 passed | `338b920c3` (fixture mode plumbing) |
| `compaction_kernel_parity` | 23 passed | `4dea91d72` (D16 expectation alignment) |
| `compaction_parallel_fallback` | 2 passed | none (adjacent gate) |
| `compaction_length_recovery` | 7 passed | `338b920c3` (fixture mode plumbing) |
| `compaction_chat_length_recovery` | 4 passed | none (adjacent gate) |
| `compaction_anthropic_length_recovery` | 4 passed | none (adjacent gate) |
| `compaction_summary_path` | 8 passed | `3b08e3f2e` (D5), `289f3c2e1` (D6), `720428967` (D9), `439808412` (D14) — the W2 file, integrated |
| `compaction_parity_suite` (extra, outside the gate list — see section 6) | 13 passed, 1 failed | none (file untouched by r2) |

## 4. Python research suites (offline; all at `29f1f0c92`)

| suite | command | result |
| --- | --- | --- |
| context_rot_r2 offline unittest discovery | `cd research && python -m unittest discover -s context_rot_r2 -t . -p "test_*.py"` | 138 tests, OK (3 skipped) — the scenario/scoring tests (5), the W9 incremental variants (6), the ContextBench suite (124 run + 3 gated on the parquet refs) |
| ContextBench offline self-check, integration tests on | cb-venv python + `CB_REFS_ROOT=C:/Users/openclawuser/optimus-rot2-w5-research/refs/ContextBench`, `python -m unittest discover -s research/context_rot_r2/contextbench/tests -t .` | 127 passed, 0 skipped (the 3 gated tests verify the real parquet sha256, the row shape, and that the committed `data/dev_holdout_split.json` regenerates exactly) |
| W27 live-pipeline selftest | `python -m research.context_rot_r2.contextbench.live.selftest` | 24 tests OK — slice rule, transcripts, budgets, extraction, retained-tail bookkeeping, evaluation path |
| E3 offline selftest | `cd research && python -m unittest context_rot_r2.live.e3.selftest` | 14 tests OK — determinism, anchor planting, action matchers, TASK-F restart round-trip, and (new in the E5 wave) the typed arm's offline pipeline with the typed ledger present and verification passing at every cycle |
| E3ADV offline selftest | `cd research && python -m unittest context_rot_r2.live.e3adv.e3adv_selftest` | 15 tests OK — zero-pairing precondition against the real mirror ledger, cut invariant, action matchers |
| CompactBench adapter checks | `HARNESS=C:/Users/openclawuser/rot2-w25-target/debug/compaction-harness.exe` + cb-venv python, `research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py` | 13/13 checks passed — cut ladder, ledger mapping, both arms, starter-shaped transcript, determinism, delta round-trip, three-cycle partition, retry-after-failure, resync, small-transcript fallback, missing-binary degradation, controls |

## 5. Environmental exceptions (verified against the base `6000d6bc1` in this pass)

1. `core::agent_session::post_compaction_continuation_tests::enabled_skill_is_importable`
   fails with `uv pip install runs: Error { kind: NotFound, message:
   "program not found" }` — `uv` is not on the test child's `PATH` on this
   host. Verified at the base in this pass: the base `--lib compaction`
   run reports 186 passed, 3 failed with the same uv panic among the
   failures, and the serialized base rerun of this test fails
   identically. Same test, same cause is also documented at the
   iteration-1 baseline (reports/context-rot/REGRESSION-GATES.md
   section 2) and in the W14 r2 pass.
2. `modes::interactive::...::native_status::tests::compaction_count_stays_before_the_meter_with_goal_and_heartbeat`
   fails under default threading and passes serialized at both the tip and
   the base (verified in this pass on both trees): the documented
   painted-frame flake.
3. Scheduling-family flakes change composition run to run, exactly as
   iteration-1 documented (reports/context-rot/REGRESSION-GATES.md
   section 3): `b1_resync_catchup_republishes_independent_decision_and_compaction`
   (daemon parity) failed in the default-thread runs at `30588707d` and
   passed in every later run and serialized;
   `core002_refinement_plans_and_baselines_use_the_requested_scope` failed
   in the base default-thread run and passes serialized there. No r2
   commit touches `crates/pi-coding-agent/src/modes/`
   (`git log --oneline 6000d6bc1..HEAD -- crates/pi-coding-agent/src/modes/`
   is empty).

The default-thread base run of the wider `compaction` filter: 186
passed, 3 failed (the uv gap plus the two flakes above), 3 616 filtered
out; serialized reruns of the three failures leave only the uv gap. The
tip's corresponding run: 263 passed, 2 failed (section 2).

## 6. The one failing test: `compaction_parity_suite::post_compaction_usage_is_not_stale`

`compaction_parity_suite` is not in the campaign's gate list (W14 recorded
it as "outside the brief's gate list; not run"); this pass ran it anyway
because the brief says "every compaction_* integration test". Result at
the final tip: 13 passed, 1 failed; the failure is deterministic (an
isolated rerun fails identically).

`post_compaction_usage_is_not_stale` (T05.4) asserts that after a first
threshold compaction and a stale-usage turn, a fresh usage past the
threshold produces a second durable compaction entry. Observed at the tip:
the second compaction starts (`start_count` 1 -> 2, the
"genuinely new usage past the threshold must compact again" assert
passes) but no second durable entry lands ("the second compaction is
durable" panics).

- Base comparison (this pass, `git archive 6000d6bc1` tree, isolated
  target): the test PASSES at the base, and the full suite is 14/14 there.
- Mechanism (code-verified): the D5 fix (`3b08e3f2e`) removed
  `previous_summary.is_none()` from the empty-window guard in
  `prepare_compaction`. In this fixture (`keep_recent_tokens` = 1000) the
  two post-compaction turns fit inside the retained tail, so the second
  compaction's summarize window is empty and now resolves to a no-op —
  no model call, no appended entry — exactly the behavior D5's commit
  message and its guards describe
  (`repeated_trigger_with_no_new_material_resolves_to_no_op` and
  companions in `compaction_summary_path`, 8/8 here). At the base the old
  guard let the compaction run because a previous summary existed. W40's
  F3 sweep re-pinned the probe rows around the same no-op
  (`1d1b1fa8a`, adapter README verdicts `29f1f0c92`).
- Verdict: an expected behavior change from the integrated D5 fix
  breaking an un-gated parity test that pinned the pre-D5 behavior. Not a
  retention or correctness regression; the test needs a fixture update
  (larger post-compaction transcript or a smaller `keep_recent_tokens`)
  so the second window is non-empty. The file is outside this worker's
  file map, so it is recorded here for the integrator.
- Fixed in `04c6463c9` (`rot2/w43-parityfix`, W43): one 2000-token
  post-compaction turn before the fresh-usage turn keeps the second
  compaction's summarize window non-empty; the suite is 14/14 at the tip
  with that fixture update (same mechanism as W40's probe F3 re-pin,
  `1d1b1fa8a`).

## 7. `cargo check -p pi-coding-agent --all-targets`: warnings vs base

Count method: full stderr captured per side; lines starting `warning:`
minus the "generated N warnings"/"warnings emitted" summary lines.

| tree | warning diagnostics |
| --- | --- |
| base `6000d6bc1` | 542 |
| final tip (`f7bc6306c`, library byte-identical at `29f1f0c92`) | 546 (+4) |

The four new warnings, all in campaign-added files; zero base warnings
disappeared:

| warning | location |
| --- | --- |
| unused import: `std::path::PathBuf` | `crates/pi-coding-agent/tests/rot2_multicycle.rs` |
| fields `gold` and `kind` are never read | `crates/pi-coding-agent/tests/rot2_multicycle.rs` |
| field `context_window` is never read | `crates/pi-coding-agent/tests/rot2_multicycle.rs` |
| unused import: `CompactionSettings` | `crates/pi-coding-agent/src/bin/compaction_harness.rs` (left over from `6e1c3a139`'s settings drop; the W26/W30 waves added no further warnings — the count is identical at `30588707d`, `f8ad670c8`, `f7bc6306c`) |

## 8. Not run, and why

| item | reason |
| --- | --- |
| the `#[ignore]`d `rot2_production_multicycle` entry point | the research adapter that executes full experiment runs (`ROT2_SCENARIO`/`ROT2_RESULT`); the committed runs are the campaign's data |
| the six `#[ignore]`d `rot2_typed_adversarial` tests | they are the documented defects D22-D27 (BUG-LEDGER), ignored with the D-number as the reason by design |
| the live entry points (`run_r2e2.py`, `run_e3.py`, `run_e3adv.py`, `remeasure/replay.py`, `compactbench_adapter/live/run_live.py`, `contextbench/live/run_live.py`, the E5 typed-arm live run) | live-model runs are the campaign's committed data (R2-E2, R2-E3, R2-E3ADV, R2-E2-RM, W26, W27, R2-E5); no new live calls from a gate pass |
| the full `--lib` suite and other crates | campaign rules: focused tests only; the lib filters above cover every r2-touched module |
| CompactBench full live run, ContextBench free-exploration arms, the holdout run, RepoQA / SWE-bench / Terminal-Bench | see EXPERIMENT-LEDGER.md "NOT EXECUTED" and CODING-TASK-RESULTS.md section 4 (reconciled at the W34 refresh) |

## 9. Verdict

At the final integration tip `29f1f0c92` (80 commits over base
`6000d6bc1`), every suite in the campaign's gate list passes: the three
lib filters (146 + 60 + 17), all nine `rot2_*` suites — including the new
`rot2_typed_integration` (9), `rot2_harness_digest` (2),
`rot2_defect_wave2` (3), `rot2_render_cap` (7) and
`rot2_typed_adversarial` (8 passed, 6 ignored-as-documented-defects) —
all twelve `compaction_*` suites in the gate list, and every offline
Python research suite: the 138-test discovery set, the ContextBench
integration self-check (127), the W27 live-pipeline selftest (24), the
E3 (14) and E3ADV (15) selftests, and the 13 adapter checks against a
harness binary built from this branch. The known environmental pair
behaves exactly as documented and is verified at the base in this pass
(uv missing for the test child; the painted-frame tray test that passes
serialized), with the scheduling family's composition varying run to run
as iteration-1 documented. The single real failure is
`compaction_parity_suite::post_compaction_usage_is_not_stale`, in an
extra suite outside the gate list, broken by the integrated D5 no-op fix
in the way D5's own guards describe (section 6); it needs a fixture
update, not a code revert. The tip adds four warnings over base, all in
campaign test and harness files.
