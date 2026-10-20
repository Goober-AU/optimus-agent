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

W50 consistency note (2026-10-20): the recorded pass stays pinned to
`29f1f0c92`; the integration tip has since advanced to `34c27c07d`
(104 commits over base) with the W36-W41 typed-defect fixes. Those waves
un-ignored and greened the six `rot2_typed_adversarial` rows below (the
former D22-D27 red tests) and added the D25/D29 pins named in BUG-LEDGER,
so the "8 passed, 6 ignored" row and the D22-D27 mentions in sections 3
and 8 describe the state at `29f1f0c92`, not the current suite. No
full-gate re-run was performed by the W50 docs-only sweep; re-run the
focused suites at the final tip before merging.

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

## 10. Wave-2 gate pass (W49): the D21-D29 fix-wave battery at `71e770c5e`

The W25 record above stands at its recorded SHAs. The D21-D29 fix wave then
moved the integration tip to `71e770c5e` (103 commits over base
`6000d6bc1`; source deltas over `29f1f0c92`: `core/compaction/compaction.rs` and
`core/compaction/typed_state.rs` (the wave's fixes), the harness bin's
warning cleanup, the `rot2_multicycle` typed-arm smoke re-pointing
(`7bec70f09`), the new `rot2_native_typed_chain` suite, the
`rot2_typed_adversarial` wave tests, and the W43 `compaction_parity_suite`
fixture re-pin `31ce39b84`; the four warnings of
section 7 were dropped by `fa3634b7a`, W42). This worker re-ran the wave
battery in `C:/Users/openclawuser/optimus-rot2-w49-wavereport` (branch
`rot2/w49-wavereport`, clean tree at run time; this pass's report edits were
made after the runs), 2026-10-19, rustc 1.98.1 (48a229cea 2026-09-01) / cargo 1.98.1
(797e8a9bc 2026-08-05), ISOLATED target `C:/Users/openclawuser/rot2-w49-target`
(fresh full `cargo test -p pi-coding-agent --no-run` build):

| suite | result | notes |
| --- | --- | --- |
| `rot2_typed_adversarial` | 23 passed, 0 failed, 0 ignored (0.30 s) | 8 passed + 6 ignored at the W25 pass -> 23/0/0: the six defect tests are un-ignored and green, plus the wave's new pins |
| `rot2_typed_integration` | 9 passed, 0 failed, 0 ignored (0.29 s) | unchanged count, re-run after the wave's `compaction.rs` deltas |
| `rot2_native_typed_chain` | 2 passed, 0 failed, 0 ignored (0.25 s) | the new D28 suite (`1e6e5ff9d`) |
| `rot2_multicycle` | 5 passed, 0 failed, 1 ignored (0.76 s) | same shape as the W25 run; the ignored test is the research adapter |
| `rot2_render_cap` | 7 passed, 0 failed, 0 ignored (0.05 s) | the D21 guards, unchanged count |
| `rot2_ledger_fixes` | 7 passed, 0 failed, 0 ignored (0.03 s) | the W11 guards, unchanged count |
| `--lib typed_state` | 74 passed, 0 failed, 3 845 filtered out (2.88 s) | 60 at `f7bc6306c` -> 74 after the wave |
| `--lib core::compaction` | 160 passed, 0 failed, 3 759 filtered out (2.34 s) | 146 at `f7bc6306c` -> 160 after the wave |

Spot checks (the wave touched `compaction.rs`): `compaction_summary_path`
8 passed, 0 failed (0.01 s); `compaction_parity_suite` 14 passed, 0 failed
(8.32 s) — the W43 T05.4 fixture re-pin holds at this tip, so section 6's
one real failure is resolved in-tree.

Not re-run in this pass: the other `compaction_*` suites and the wider
`--lib compaction` filter (W25's sections 2-3 record stands at `f7bc6306c`;
the wave's library deltas are confined to `compaction.rs`/`typed_state.rs`
and are covered by the battery plus the two spot checks), the offline
Python research suites (W25's section 4 record), and the live entry points
(committed data; no new live calls from a gate pass).

Verdict: the wave-2 battery is green at `71e770c5e` — zero failures, and no
defect-`#[ignore]` is left in the adversarial suite. The typed mode's known
open items after the wave are D30 and the cycle-5 budget-overflow caveat,
both assigned to W46 (`rot2/w46-d30`, no commits over this head at this
writing).

## 11. LANDED-TIP BATTERY (90fcd17db) — RED (W63)

W63 ran the campaign's full gate list at the landed integration tip
`90fcd17db` (= `improve/context-rot-iteration-2` head at run time, 143
commits over base `6000d6bc1`; D31 and D32 FIXED, D33 VERIFIED-OPEN in
BUG-LEDGER at that tip - the D33 fix landed ten commits above it:
W54 `cea3a4496` + pin `6d85cc48f` + ledger row `510fd168d`), so the
reports carry a concrete landed-tip battery record ahead of the post-D33
final matrix. The battery is RED on exactly one
deterministic failure — the `rot2_multicycle` typed-arm smoke, filed as
D34 with fix ownership W71 — and green on everything else that ran: the
other eleven `rot2_*` suites, all thirteen `compaction_*` suites, all six
lib filters (32 of the 33 recorded steps). The run was stopped on the real
failure per the gate protocol, so the six Python research suites were not
run and the workspace check was interrupted (partial numbers below).
Runner: W55's `run_matrix.sh` (its prep survived the stalled wait loop),
adapted for worker paths plus one latent fix — the CompactBench
adapter's `HARNESS` value uses the Windows-style path because the
adapter passes it verbatim to Windows python; W55's unix-style value
would not resolve (W55 never executed its script). Run window
2026-10-19T23:23Z-2026-10-20T00:00Z; every step logged under
`C:/Users/openclawuser/rot2-w63-gates/` (`logs/<key>.log`, `results.tsv`).

### 11.1 Environment

| item | value |
| --- | --- |
| host | Windows, git-bash |
| worktree | `C:/Users/openclawuser/optimus-rot2-w63-gates`, branch `rot2/w63-gates` at `90fcd17db`, clean at run time (the report edit is this worker's only change) |
| toolchain | rustc 1.98.1 (48a229cea 2026-09-01), cargo 1.98.1 (797e8a9bc 2026-08-05); `RUSTUP_HOME`/`CARGO_HOME` under `C:/Users/openclawuser/optimus-rust-toolchain/` |
| cargo target | ISOLATED `C:/Users/openclawuser/rot2-w63-target` (fresh full `cargo test -p pi-coding-agent --no-run` = 1 765 s, harness bin build 28 s) |
| base tree for comparisons | W55's `git archive 6000d6bc1` export at `C:/Users/openclawuser/rot2-w55-gates/base-export`, built with ISOLATED `C:/Users/openclawuser/rot2-w55-target-base` (W55's completed base check and base test binaries; this pass reused them for the environmental-pair verification) |
| python (research tests) | system python 3.11.15 (not reached: the battery stopped first) |
| python (adapter + ContextBench integration tests) | campaign cb-venv 3.11.15, `C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv` (not reached) |

### 11.2 rot2_* suites (`cargo test -p pi-coding-agent --test <name>`)

| suite | result | notes |
| --- | --- | --- |
| `rot2_typed_adversarial` | 24 passed, 0 failed, 0 ignored (0.75 s) | grew from 23 at the W49 pass (`71e770c5e`); the D30 wave added the user-stated-decision pin |
| `rot2_wave_adversarial` | 9 passed, 0 failed, 0 ignored (21.0 s) | the D33 absorption pin `6d85cc48f` landed after this run; the suite re-verifies 10/0 at the landed tip |
| `rot2_typed_integration` | 9 passed, 0 failed, 0 ignored (0.38 s) | |
| `rot2_native_typed_chain` | 2 passed, 0 failed, 0 ignored (0.34 s) | |
| `rot2_multicycle` | **4 passed, 1 FAILED**, 1 ignored (0.41 s) | the red: the typed-arm smoke, section 11.7 (D34); the ignored test is the research adapter entry point |
| `rot2_render_cap` | 7 passed, 0 failed, 0 ignored (0.06 s) | |
| `rot2_ledger_fixes` | 7 passed, 0 failed, 0 ignored (0.07 s) | |
| `rot2_defect_wave2` | 3 passed, 0 failed, 0 ignored (2.27 s) | |
| `rot2_harness_digest` | 2 passed, 0 failed, 0 ignored (2.75 s) | |
| `rot2_branch_summary` | 2 passed, 0 failed, 0 ignored (0.06 s) | |
| `rot2_subagent_constraints` | 3 passed, 0 failed, 0 ignored (2.84 s) | |
| `rot2_supersession` | 4 passed, 0 failed, 0 ignored (0.52 s) | |

### 11.3 compaction_* suites (`cargo test -p pi-coding-agent --test <name>`)

| suite | result | notes |
| --- | --- | --- |
| `compaction_turn_flow` | 3 passed, 0 failed (3.12 s) | |
| `compaction_recovery` | 9 passed, 0 failed (3.00 s) | |
| `compaction_model_threshold` | 10 passed, 0 failed | |
| `compaction_fidelity` | 1 passed, 0 failed (0.39 s) | |
| `compaction_summary_guard` | 2 passed, 0 failed (0.11 s) | |
| `compaction_observability` | 7 passed, 0 failed (0.24 s) | |
| `compaction_kernel_parity` | 23 passed, 0 failed (0.12 s) | |
| `compaction_parallel_fallback` | 2 passed, 0 failed (3.62 s) | |
| `compaction_length_recovery` | 7 passed, 0 failed (0.22 s) | |
| `compaction_chat_length_recovery` | 4 passed, 0 failed (0.38 s) | |
| `compaction_anthropic_length_recovery` | 4 passed, 0 failed (0.82 s) | |
| `compaction_summary_path` | 8 passed, 0 failed (0.13 s) | |
| `compaction_parity_suite` | 14 passed, 0 failed (8.97 s) | the W43 T05.4 fixture re-pin holds at this tip |

### 11.4 lib filters (`cargo test -p pi-coding-agent --lib <filter>`)

| filter | result | notes |
| --- | --- | --- |
| `core::compaction` | 168 passed, 0 failed, 3 759 filtered out (3.05 s) | grew from 160 at the W49 pass; the D30/D31/D32 waves added units |
| `typed_state` | 78 passed, 0 failed, 3 849 filtered out (2.76 s) | grew from 74 at the W49 pass |
| `compaction::task_state` | 17 passed, 0 failed, 3 910 filtered out (0.07 s) | |
| `core::context_health_calibration` | 17 passed, 0 failed, 3 910 filtered out (0.02 s) | |
| `core::settings_manager` | 62 passed, 0 failed, 3 865 filtered out (0.60 s) | |
| `settings` | 100 passed, 0 failed, 3 827 filtered out (1.28 s) | |

### 11.5 `cargo check --workspace --all-targets`: interrupted, partial numbers only

The battery was stopped on the D34 failure while this step ran; cargo was
killed with the runner and the log truncates mid-diagnostic at
2026-10-19T23:58:09Z with no `Finished` line. What the partial log holds:
289 `Checking` lines (275 distinct crates — every crate the base check
covered, plus the r2 branch's additions), 0 `error:` lines, and 198
warning diagnostics as a lower bound (210 `warning:` lines minus 12
summary lines, count method as before). Base comparison from W55's
completed base run of the same command (same toolchain, same count
method): 0 errors, 576 warning diagnostics. No complete tip-vs-base
warning comparison exists from this battery; the successor's final matrix
must re-run this step.

### 11.6 Python research suites: not run

The battery stopped on the D34 failure before the Python steps (offline
unittest discovery, ContextBench integration self-check, the live-pipeline
selftest, the E3 and E3ADV selftests, the CompactBench adapter checks).
No W63 numbers exist for them at this tip; the landed record for their
content is the W25 pass (section 4, at `29f1f0c92`). The successor's
final matrix must run them.

### 11.7 The red: `rot2_multicycle::rot2_smoke_typed_arm_persists_typed_ledger_and_verifies` (D34)

- Suite result: 4 passed, 1 FAILED, 1 ignored (the research adapter);
  exit 101. Panic at `crates/pi-coding-agent/tests/rot2_multicycle.rs:751`
  (`lossy summarizer fixture succeeds`):
  `Summarization returned an unusable handoff (typed protected state not
  represented: 0 missing, 0 mis-superseded, 0 unresolved references, 1
  unbacked verified claims); existing conversation preserved. Retry
  compaction or select another summarization model.`
- Determinism: an isolated direct test-binary rerun (`--exact`, no
  cargo, the binary this battery built) fails identically — not a
  threading flake, not environment-dependent (the fixture is in-process).
- Not one of the known environmental pair: neither appeared anywhere in
  this battery (the lib filters do not match them), and both were
  re-verified at base in this pass (section 11.8).
- First-failure range: green in the W49 battery at `71e770c5e` (5 passed,
  1 ignored, typed-arm smoke among the five); red at `90fcd17db`; 40
  commits between. The last commit touching the suite file is `10e2b36e6`
  (D30), which added a user-stated decision seed (ARCH-DECISION-C,
  `verified` provenance) to the smoke scenario; its commit message's test
  list does not include the typed-arm smoke. Co-suspect `6041f71c9` (D31,
  retired ids no longer vouch for verified claims) matches the failure
  signature "1 unbacked verified claims".
- D34 mechanism (integrator ruling): the user-stated
  VERIFIED_DECISION seed retires at a step boundary; the lossy
  summarizer's handoff then cites it as an unbacked verified claim; D31's
  strictness correctly refuses to vouch via the retired record, and the
  verification hard-fails the compaction with the existing conversation
  preserved. Fix ownership: W71. No diagnosis beyond this was attempted
  by W63 per its brief.
- Artifacts: `logs/test_rot2_multicycle.log`, `results.tsv`, and the
  isolated rerun transcript `isolated_rerun_typed_arm_smoke.log`, all
  under `C:/Users/openclawuser/rot2-w63-gates/`.
- 2026-10-20 update (W82, the D34 ripple): D34 is FIXED on the integration
  branch - W71's bounded repair `6894f6a6d` ("fix(compaction): bounded
  repair downgrades unbacked verified claims (D34)", ledger row
  `08f34308b`): typed_state gains `annotate_unbacked_verified_claims`
  and the `UNVERIFIED_CLAIM_MARKER` suffix, `verify` exempts a line
  already carrying the marker, and the deterministic repair gate widens
  to fabricated-only findings, so an unbacked verified claim is
  downgraded in place (content kept, re-verified, recorded as repaired)
  instead of failing the handoff; a claim citing an id nothing knows
  stays unrecoverable and still fails the episode. Mechanism correction
  from the fix (dump-confirmed): the retiree was the assistant pending
  action "Section 3 verified; publishing next." (Step-scoped, retired at
  the cycle-1 merge by the D25 boundary), not the D30 user-stated seed
  the ruling above suspected - D31's strict vouching behaved correctly
  throughout. Guards: `unbacked_verified_claim_from_a_retired_record_repairs_to_unverified`
  and `annotate_unbacked_verified_claims_leaves_backed_lines_alone`
  (lib), the smoke's repaired-cycle assertions, and the fail-closed pin
  moved to an unresolvable-reference shape. The battery record above
  stands as the landed-tip record (the W63 battery ran at `90fcd17db`,
  below the fix); the final matrix (W81) re-runs the gate list at the
  final tip - pending at this writing (section 12 of this file when it
  lands).

### 11.8 Environmental-pair verification at base (this pass)

Both known environmental failures were re-verified at the base
`6000d6bc1` (W55's base-export tree and base test binaries, serialized
`--test-threads=1`), and both behaved exactly as documented:

1. `core::agent_session::post_compaction_continuation_tests::enabled_skill_is_importable`
   FAILS at base serialized with the identical panic
   (`uv pip install runs: Error { kind: NotFound, message: "program not
   found" }` — uv not on the test child's PATH; 251.89 s). Environmental.
2. `modes::interactive::...::native_status::tests::compaction_count_stays_before_the_meter_with_goal_and_heartbeat`
   PASSES at base serialized (1 passed, 3 804 filtered out, 2.92 s) — the
   documented painted-frame/threshold-meter flake under default
   threading. Environmental.

Neither appeared in this battery: no failure besides the D34 typed-arm
smoke was recorded in any of the 33 steps that ran.

### 11.9 Verdict

RED, on the typed-arm smoke only. At the landed tip `90fcd17db` the full
gate list that ran is green — eleven of twelve `rot2_*` suites, all
thirteen `compaction_*` suites, all six lib filters — and the single
failure is the deterministic D34 (`rot2_smoke_typed_arm_persists_typed_ledger_and_verifies`, section 11.7), filed for W71 with its artifacts
preserved. The two known environmental failures were verified at base and
did not appear. Not run because of the stop: the six Python research
suites and the complete workspace check (partial: 0 errors, >=198
warning diagnostics). The successor's final matrix (after the
D34 fix lands; D33 landed after this battery, W54 `cea3a4496`) supersedes
this record.

### 11.10 PYTHON SUITES AT THE LANDED TIP (W72)

W72 closed the gap section 11.6 records: the six Python research suites
W63's stopped battery never reached, run at the landed tip on 2026-10-20.
All six are green, with exactly the shapes the W25 record (section 4, at
`29f1f0c92`) carries — the Python research path is proven green at the
tip ahead of W67's final matrix, and this pass is its comparison point.

Environment: worktree `C:/Users/openclawuser/optimus-rot2-w72-python`,
branch `rot2/w72-python` at `6c6015ce8` (= integration head at run time,
148 commits over base `6000d6bc1`), clean at run time (`git status
--porcelain` empty; the report edit below is this worker's only change).
`6c6015ce8` differs from W63's battery tip `90fcd17db` in report files
only (`git diff --name-only 90fcd17db..HEAD -- crates/ research/` is
empty), so this pass exercised byte-identical suite sources and the same
harness source W63's battery built. Commands: the six `py_*` steps of
W63's runner (`C:/Users/openclawuser/rot2-w63-gates/run_matrix.sh`),
verbatim and in W63's order, from that worktree. Interpreters: system
python 3.11.15 (discovery, live selftest, E3, E3ADV) and the campaign
cb-venv 3.11.15 with pyarrow 26.0.0 + compactbench (ContextBench,
adapter). Artifacts: `C:/Users/openclawuser/rot2-w72-gates/`
(`run_python_suites.sh`, `logs/<key>.log`, `results.tsv`); run window
2026-10-20T00:31:45Z-00:32:26Z.

| suite | command | exit | result |
| --- | --- | --- | --- |
| offline unittest discovery | `cd research && python -m unittest discover -s context_rot_r2 -t . -p "test_*.py"` | 0 | 138 tests, OK (3 skipped; 0.13 s) — scenario/scoring (5), W9 incremental variants (6), ContextBench suite (124 run, the 3 parquet-gated skipped) |
| ContextBench integration self-check | cb-venv python + `CB_REFS_ROOT=C:/Users/openclawuser/optimus-rot2-w5-research/refs/ContextBench`, `-m unittest discover -s research/context_rot_r2/contextbench/tests -t .` | 0 | 127 tests, OK, 0 skipped (9.32 s) — the 3 gated tests ran against the pinned parquet |
| W27 live-pipeline selftest | `python -m research.context_rot_r2.contextbench.live.selftest` | 0 | 24 tests, OK (0.01 s) |
| E3 offline selftest | `cd research && python -m unittest context_rot_r2.live.e3.selftest` | 0 | 14 tests, OK (1.46 s) |
| E3ADV offline selftest | `cd research && python -m unittest context_rot_r2.live.e3adv.e3adv_selftest` | 0 | 15 tests, OK (0.69 s) |
| CompactBench adapter checks | cb-venv python + `HARNESS=C:/Users/openclawuser/rot2-w63-target/debug/compaction-harness.exe`, `research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py` | 0 | 13/13 checks passed (18 s) — cut ladder, ledger mapping, both arms, starter-shaped transcript, determinism, delta round-trip, three-cycle partition, retry-after-failure, resync, small-transcript fallback, missing-binary degradation, controls |

Two invocation notes, both verified on this host before the runs:

1. `CB_REFS_ROOT` was set only for the ContextBench step, Windows-style,
   as the W25 record shows. W63's runner exported it script-wide in
   unix style; git-bash hands such values to the Windows interpreters
   converted (`C:/...`, verified), so the script-wide export would also
   have run the three parquet-gated tests inside the discovery step,
   under the system python, which has no pyarrow (`import pyarrow` fails
   there) — an environmental red the recorded invocation avoids; the
   gated trio is the cb-venv step's job (hence W25's "3 skipped"
   discovery shape, reproduced exactly here).
2. `HARNESS` keeps W63's Windows-style fix and points at W63's
   `compaction-harness.exe` built at `90fcd17db` (the adapter's default
   would be the shared-target build of unknown provenance). With
   `crates/` byte-identical to this tip, that binary is this tip's
   harness; the adapter checks ran it through both arms, the delta
   round-trip and the missing-binary degradation.

Verdict: green across all six suites — zero failures, zero unexpected
skips (the only skips are the 3 documented parquet-gated tests in the
system-python discovery run, which run green through the cb-venv step
immediately after). The W63 gap (section 11.6) is closed at the landed
tip; W67's final matrix re-runs these suites at the final tip.


## 12. FINAL TIP MATRIX (ac5edde2b) — GREEN (W81)

W81 ran the campaign's definitive final gate matrix at the final
integration tip `ac5edde2b924e86188722b20d8e8770a669fb234`
(`improve/context-rot-iteration-2` head at run time, 177 commits over
base `6000d6bc1`; D34 FIXED in BUG-LEDGER — W71's bounded repair
`6894f6a6d` integrated, ledger rows `08f34308b`/`b0fdcde69`/
`ac5edde2b`). Every step is green: all twelve `rot2_*` suites, all
thirteen `compaction_*` suites, all six lib filters, the complete
workspace check, and all six Python research suites — zero failed tests
anywhere, and the workspace warning count equals the base. This record
supersedes W63's RED battery (section 11) as the landed-tip battery of
record, and it closes the two gaps that battery left open (sections 11.5
and 11.6).

Execution note: the matrix ran detached (WMI `Win32_Process.Create`, so
the runner sits outside the agent session's process tree) after an
agent-session restart killed the first launch mid-build; the relaunch
re-ran every step from a fresh preflight. Run window
2026-10-20T02:25:23Z-02:35:47Z; 40 steps, all exit 0. Runner: W63's
`run_matrix.sh` adapted for this worker — W81 paths, `HARNESS` as the
Windows-style path to this worker's isolated target's
`compaction-harness.exe`, and W72's `CB_REFS_ROOT` split (set only for
the ContextBench step, Windows-style; see section 11.10 note 1). The
harness binary was rebuilt at the final tip by the matrix's own
`build_harness` step (24 281 088 bytes, 02:30:42Z, before every test
step). Artifacts under `C:/Users/openclawuser/rot2-w81-gates/`
(`run_matrix.sh`, `logs/<key>.log`, `results.tsv`, `matrix_run.log`).
Warm build: 1 174 s at the pre-D34 tip `83b3719c3` into the ISOLATED
target `C:/Users/openclawuser/rot2-w81-target`; the final-tip
incremental test build added 473 s plus the 4.7 s harness build.

### 12.1 Environment

| item | value |
| --- | --- |
| host | Windows, git-bash; runner detached via WMI (cmd -> git-bash) |
| worktree | `C:/Users/openclawuser/optimus-rot2-w81-matrix`, branch `rot2/w81-matrix` at `ac5edde2b`, clean at run time (the report edit below is this worker's only change) |
| toolchain | rustc 1.98.1 (48a229cea 2026-09-01), cargo 1.98.1 (797e8a9bc 2026-08-05); `RUSTUP_HOME`/`CARGO_HOME` under `C:/Users/openclawuser/optimus-rust-toolchain/` |
| cargo target | ISOLATED `C:/Users/openclawuser/rot2-w81-target` |
| python (ContextBench + adapter) | campaign cb-venv 3.11.15, `C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv` (pyarrow 26.0.0, compactbench import ok) |
| python (other four suites) | 3.14.3 as resolved in the detached runner's PATH; re-verified under the 3.11.15 interpreter W72 recorded — identical shapes, `logs/py_*_311.log` |

### 12.2 rot2_* suites (`cargo test -p pi-coding-agent --test <name>`)

| suite | result | notes |
| --- | --- | --- |
| `rot2_typed_adversarial` | 24 passed, 0 failed, 0 ignored (0.23 s) | unchanged from W63's red battery; the D34 fix moved the adversarial repair-bound pin to the still-unrecoverable `[VD-0badca]` shape inside the existing count |
| `rot2_wave_adversarial` | 10 passed, 0 failed, 0 ignored (13.80 s) | grew from W63's 9: the D33 absorption pin `6d85cc48f` landed after that battery, as its table note predicted |
| `rot2_typed_integration` | 9 passed, 0 failed, 0 ignored (0.30 s) | |
| `rot2_native_typed_chain` | 2 passed, 0 failed, 0 ignored (0.24 s) | |
| `rot2_multicycle` | **5 passed, 0 failed, 1 ignored** (0.34 s) | the D34 gate: green (section 12.7); the ignored test is the research adapter entry point |
| `rot2_render_cap` | 7 passed, 0 failed, 0 ignored (0.06 s) | |
| `rot2_ledger_fixes` | 7 passed, 0 failed, 0 ignored (0.06 s) | |
| `rot2_defect_wave2` | 3 passed, 0 failed, 0 ignored (0.91 s) | |
| `rot2_harness_digest` | 2 passed, 0 failed, 0 ignored (0.56 s) | |
| `rot2_branch_summary` | 2 passed, 0 failed, 0 ignored (0.01 s) | |
| `rot2_subagent_constraints` | 3 passed, 0 failed, 0 ignored (1.45 s) | |
| `rot2_supersession` | 4 passed, 0 failed, 0 ignored (0.27 s) | |

Totals: 78 passed, 0 failed, 1 ignored (the documented research adapter).

### 12.3 compaction_* suites (`cargo test -p pi-coding-agent --test <name>`)

| suite | result | notes |
| --- | --- | --- |
| `compaction_turn_flow` | 3 passed, 0 failed (0.87 s) | |
| `compaction_recovery` | 9 passed, 0 failed (0.83 s) | |
| `compaction_model_threshold` | 10 passed, 0 failed (0.00 s) | |
| `compaction_fidelity` | 1 passed, 0 failed (0.01 s) | |
| `compaction_summary_guard` | 2 passed, 0 failed (0.01 s) | |
| `compaction_observability` | 7 passed, 0 failed (0.01 s) | |
| `compaction_kernel_parity` | 23 passed, 0 failed (0.03 s) | |
| `compaction_parallel_fallback` | 2 passed, 0 failed (2.96 s) | |
| `compaction_length_recovery` | 7 passed, 0 failed (0.01 s) | |
| `compaction_chat_length_recovery` | 4 passed, 0 failed (0.03 s) | |
| `compaction_anthropic_length_recovery` | 4 passed, 0 failed (0.03 s) | |
| `compaction_summary_path` | 8 passed, 0 failed (0.01 s) | |
| `compaction_parity_suite` | 14 passed, 0 failed (4.26 s) | the W43 T05.4 fixture re-pin holds |

Totals: 94 passed, 0 failed — identical counts to W63's battery; the D34
repair path added no `compaction_*` integration case (its guards are the
lib units and the multicycle smoke, below).

### 12.4 lib filters (`cargo test -p pi-coding-agent --lib <filter>`)

| filter | result | notes |
| --- | --- | --- |
| `core::compaction` | 170 passed, 0 failed, 3 759 filtered out (2.28 s) | grew from W63's 168: the D34 fix's two repair units |
| `typed_state` | 80 passed, 0 failed, 3 849 filtered out (2.18 s) | grew from W63's 78: `annotate_unbacked_verified_claims` units |
| `compaction::task_state` | 17 passed, 0 failed, 3 912 filtered out (0.06 s) | |
| `core::context_health_calibration` | 17 passed, 0 failed, 3 912 filtered out (0.02 s) | |
| `core::settings_manager` | 62 passed, 0 failed, 3 867 filtered out (0.25 s) | |
| `settings` | 100 passed, 0 failed, 3 829 filtered out (3.56 s) | |

Totals: 446 passed, 0 failed. The two known environmental failures
(section 11.8) are matched by none of these six filters and appeared in
no step of this matrix.

### 12.5 `cargo check --workspace --all-targets`

Complete this time (W63's was interrupted, section 11.5): exit 0,
`Finished dev profile in 52.16 s`, 0 `error:` lines, 576 warning
diagnostics (604 `warning:` lines minus 28 crate-summary lines, W55's
count method). W55's base run of the same command at `6000d6bc1` with
the same toolchain and method: 0 errors, 576 warning diagnostics. The
campaign's net warning delta at the final tip is therefore 0 — every
warning any wave introduced was cleaned up before landing.

### 12.6 Python research suites

All six green, shapes identical to W72's section 11.10 record:

| suite | exit | result |
| --- | --- | --- |
| offline unittest discovery | 0 | 138 tests, OK (3 skipped; 0.08 s) — also OK (3 skipped) under 3.11.15 |
| ContextBench integration self-check (cb-venv + `CB_REFS_ROOT`) | 0 | 127 tests, OK, 0 skipped (1.99 s) — the 3 gated tests ran against the pinned parquet |
| W27 live-pipeline selftest | 0 | 24 tests, OK (0.006 s) — also OK under 3.11.15 |
| E3 offline selftest | 0 | 14 tests, OK (1.96 s) — also OK under 3.11.15 |
| E3ADV offline selftest | 0 | 15 tests, OK (0.31 s) — also OK under 3.11.15 |
| CompactBench adapter checks (cb-venv + `HARNESS`) | 0 | 13/13 checks passed (4 s) — ran the tip-built harness through both arms |

Interpreter note: the detached runner's PATH resolves `python` to
3.14.3, not the 3.11.15 W72 recorded; the four system-python suites
were re-run under 3.11.15 from this worktree with identical shapes and
exit 0 (`logs/py_discovery_311.log`, `py_live_selftest_311.log`,
`py_e3_selftest_311.log`, `py_e3adv_selftest_311.log`), and the two
cb-venv suites pin their interpreter explicitly, so the recorded
invocation matches W72's either way.

### 12.7 The D34 gate: `rot2_multicycle` at the final tip

Six tests ran; five passed and one was ignored:

```
test rot2_production_multicycle ... ignored, research adapter: requires ROT2_SCENARIO/ROT2_RESULT env
test rot2_lossy_summarizer_keeps_protected_block_verbatim_and_drops_middle ... ok
test rot2_user_image_blocks_cost_tokens_d16 ... ok
test rot2_request_level_estimate_adds_system_prompt_and_tools_d17 ... ok
test rot2_smoke_typed_arm_persists_typed_ledger_and_verifies ... ok
test rot2_smoke_multicycle_runs_all_cycles_and_reports_probes ... ok
```

`rot2_smoke_typed_arm_persists_typed_ledger_and_verifies` — the
deterministic D34 red of W63's battery (section 11.7: 4 passed, 1
FAILED, 1 ignored, exit 101, "1 unbacked verified claims") — is green
at the final tip, and the suite result is exactly the post-fix shape
the D34 ledger row records (5+1 ignored, the smoke asserting the
repaired cycle exists and re-verifies clean). The fail-closed bound the
fix must not weaken is still pinned in the green
`rot2_typed_adversarial` (the unresolvable `[VD-0badca]` reference
blocks the repair) and in the two lib units sections 12.2/12.4 count.

### 12.8 Verdict

GREEN. At the final tip `ac5edde2b` the campaign's full gate list
passes with zero failed tests: 78 `rot2_*` + 94 `compaction_*`
integration tests, 446 lib-filter tests, the workspace check at base
warning parity (0 errors, 576 warnings), and all six Python research
suites. The D34 fix is verified green by the battery that caught it,
on the same hardware and toolchain, with the runner's every step logged
(`C:/Users/openclawuser/rot2-w81-gates/logs/`, `results.tsv`). The
branch's final gate evidence is this section.
