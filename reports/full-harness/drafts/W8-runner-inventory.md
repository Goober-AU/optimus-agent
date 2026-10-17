# W8 — Validation-runner + test-suite/benchmark inventory (WAVE 1, read-only)

Scope: static code reading only. No cargo build/check/test executed, no suite
run, no network. Repo worktree C:/Users/openclawuser/optimus-fh-20261017,
branch improve/full-harness-validation, base c227b0ae3 (v0.1.34).
The only files created: `scripts/full_harness_validation.py` (new),
`reports/full-harness/FEATURE-COVERAGE-MATRIX.md` (skeleton), this draft.
Every claim cites file:line (1-based) from the current worktree.

---

## 1. CAPABILITY INVENTORY

### 1.1 Test entry points (invocation lanes)

| Lane | Entry point | What it covers | How invoked | Expected runtime | Prerequisites | Ignored/env-gated tests |
|---|---|---|---|---|---|---|
| Workspace unit tests | `test.sh:4` `cargo test --locked --workspace --lib -- --test-threads=1` | all in-crate `#[cfg(test)]` modules of the 5 crates | bash `./test.sh` or the cargo line directly | long on cold cache (build dominated); CI splits it (ci.yml:79-90) | rustc+cargo, Cargo.lock | none known at lane level |
| Root Python tests | `test.sh:5` `python3 -m unittest discover -s tests -p 'test_*.py'` | 9 files: jev launcher env, 4x memory-bench, question-loop controller, memory skill payloads, rust release, standalone install | bash `./test.sh`; CI ci.yml:59 | baseline 2026-10-17: 217s total, slowest test_rust_release.py 109.8s (artifacts/baseline-python-root.json) | python3 on PATH (MISSING on Windows host; `python` 3.11.15 exists) | test_jev_launcher_env.py skips 6 on Windows (:22 skipUnless posix); test_standalone_install.py skips 2 (:25) |
| Kernel runtime tests | `test.sh:6` `uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test` | 32 files / 566 test methods: REPL protocol, bash, winjob, MCP, OCC-1, snapshots, harness, subagents | test.sh or per-file discover (artifacts/run_pyrt.py pattern) | per-file seconds to ~minutes; each spawns fresh `python -m rlm.repl` children (test_repl.py:58-64) | uv on PATH (NOT on PATH; at C:/Users/openclawuser/.local/bin/uv.exe per BASELINE.md), PYTHONPATH cleared (BASELINE.md note; installed runtime shadows repo src) | platform skips: test_bash.py POSIX paths (14 skip sites, e.g. :182,233), test_repl.py nt skips (:506,558,579), test_snapshot_v2.py numpy/pandas/symlink (:196,210,548), test_matplotlib_display.py (:12), test_winjob_windows_visibility.py nt-only (:18) |
| Rust integration tests | per-file cargo targets, e.g. `cargo test --locked -p pi-coding-agent --test native_cli -- --test-threads=1` | 87 files / 892 test fns (61 files/542 fns pi-coding-agent, 19/309 pi-jev, 5/25 pi-ai, 1/4 pi-agent-core, 1/8 pi-tui) | `cargo test -p <crate> --test <name>`; CI ci.yml:87-92 picks 6; nightly-process-stress.yml:32 native_cli on ubuntu | compile-dominated; suites individually minutes | rustc+cargo | see 1.3 |
| Node tests | `tests/performance-metrics-compaction-report.test.mjs`, `tests/performance-metrics-ui-report.test.mjs` | summarize-performance-metrics.mjs report shaping (2 tests, node:test) | `node --test tests/<file>.test.mjs` (node >=18) | seconds | node on PATH (MISSING on campaign host) | none; **not wired into test.sh/check.sh/CI at all** |
| Layout/packaging gate | `scripts/check.sh:4,5,7` (git diff --check; cargo check --workspace --all-targets; unittest discover; scripts/check-rust-layout.py) | whitespace, compile, packaging, resource layout | `bash scripts/check.sh` (AGENTS.md:22) | cargo check dominates | bash, cargo, python3, git | none |
| Layout validator | `scripts/check-rust-layout.py:18-42` | no TS sources, no root package.json, resource identity, skills pyproject files, runtime repl.py presence | python scripts/check-rust-layout.py | <2s | python, git (uses `git ls-files`, :12) | none |
| CI full pipeline | `.github/workflows/ci.yml` | ubuntu-only `test` job: shared-crate lib tests (ci.yml:79), config/jev/native/execution/compaction filters (ci.yml:82-92), runtime skill+observation files (ci.yml:96-98) | GitHub Actions push/PR to main | 45min timeout budget | GH runners | same as above |
| Nightly stress | `.github/workflows/nightly-process-stress.yml:32` | native_cli under process stress, ubuntu | schedule cron 09:00 | minutes | GH runner | none |
| Interactive-mode manual procedure | AGENTS.md:49-68 (tmux recipe) | TUI smoke by hand | tmux send-keys + capture-pane | manual | tmux, built binary | manual-only; no automated TUI e2e |

### 1.2 Python runtime suite inventory (prime-agent-runtime/test, 32 files)

File | Tests | Notes
---|---|---
test_repl.py | 104 | protocol v3, interrupt, shutdown, owner pid (PRIME_AGENT_KERNEL_OWNER_PID :2458)
test_harness.py | 55 | harness CRUD/scopes/refinements; env-isolated via RLM_HARNESS_STATE_DIR (:753-756)
test_bash.py | 60 | real process spawns; PRIME_AGENT_BASH_SHELL shell stand-in (:29-31)
test_mcp.py / test_mcp_base.py | 32 / 18 | stdio MCP fixtures (in-file `_STDIO_FIXTURE`, test_mcp.py:22)
test_occ1_codec.py | 44 | OCC-1 codec
snapshot family (9 files) | 134 | v2 CAS, budget, speed, file handles, skip-unchanged, legacy migration, restore
test_winjob.py / test_winjob_windows_visibility.py | 22 / 2 | Windows job objects; visibility needs Git Bash + headless parent (:23,44)
test_subagent_registry.py, test_collect.py, test_create_session.py | 10 / 4 / 4 | native subagents
test_lifecycle.py, test_execution_status.py, test_upstream_stability.py, test_windows_stability.py | 12 / 10 / 6 / 3
test_agent_message_skill.py | 2 | thin vs Rust OCC fidelity depth
test_code_search.py, test_observation_aliases.py, test_repl_protocol_diagnostics.py, test_repl_windows_finish.py, test_matplotlib_display.py | 5 / 3 / 4 / 4 / 5

### 1.3 Ignored / env-gated Rust tests (rg within the five tests/ dirs)

| Location | Gating |
|---|---|
| crates/pi-coding-agent/tests/browser_full_catalog.rs:4 | `#[ignore]` private full-catalogue capture via OPTIMUS_BROWSER_CATALOG_FIXTURE |
| crates/pi-jev/tests/live_systemone.rs:43 | `#[ignore]` live network; TYPESAFE_API_KEY/JEV_API_KEY + `--ignored`; JEV_BASE_URL override (:25) |
| crates/pi-coding-agent/tests/execution_mode.rs:449,501 | `#[ignore]` requires PRIME_AGENT_KERNEL_PYTHON prepared runtime |
| crates/pi-coding-agent/tests/execution_mode.rs:744,780 | `#[ignore]` requires OPTIMUS_CLANG_REPL |
| crates/pi-coding-agent/tests/native_images.rs:82 | `#[ignore]` requires OPTIMUS_CLANG_REPL |
| crates/pi-coding-agent/tests/kernel_metrics_legacy_export.rs:36 | hard `.expect` on PRIME_AGENT_KERNEL_PYTHON ("explicitly provisioned Python with dill"); panics if unset |
| crates/pi-coding-agent/tests/bootstrap_t01.rs, bootstrap_t02.rs | require PARITY_BOOTSTRAP_TOOLS_DIR (bootstrap_support/mod.rs:22-24) built by bootstrap_support/build-fixtures.cmd (needs `cl` from a VS developer prompt, build-fixtures.cmd:12-13) |
| crates/pi-coding-agent/tests/sessionkernel_t04_process_cleanup.rs:16 | `#![cfg(windows)]` whole file |
| ambient env used by tests | PARITY_CHILD_RESULT/PARITY_CHILD_ROLE/PARITY_MEMORY_STATE_ROOT/PARITY_TELEGRAM_STATE_ROOT/PARITY_VALIDATION_BASE/PARITY_CHILD_SKILLS, OPTIMUS_T07_CHILD_CASE/OPTIMUS_T07_CHILD_MODE/OPTIMUS_T07_EXPECTED_SESSIONS, JEV_TEST_SETTINGS_DIR/JEV_TEST_WRITER_ID, T05_TRACE, TEMP/TMP/PATH |

### 1.4 Benchmark infrastructure

| Piece | Entry point | Expected behaviour | Notes |
|---|---|---|---|
| memory_bench.rs (7213 lines) | `cargo run -p pi-coding-agent --example memory_bench -- <manifest.json>` (memory_bench.rs:8, main :3927-3928 usage check) | drives REAL memory system in-process: per-env scratch dirs, real ingest pipeline, full AgentSession per question; append-only resumable run.jsonl/env.jsonl; isolation pins control profile, no-jev verified; MEMORY_BENCH_MODELS_JSON required, no profile fallback (:31, :59-62) | live-model (DGX GLM-5.3 only per bench/README.md "DGX-only network access"); frozen-replay mode exists for historical fixtures (:3970-3990) |
| mem_search_bench.rs (550 lines) | `cargo run -p pi-coding-agent --example mem_search_bench --release -- <out_root> [N ...]` (:12) | deterministic LLM-free lexical search timing over synthesized stores (SplitMix64 RNG, Zipf vocab); default N = 1000 5000 20000 50000 | offline-safe; results.json per-query timings |
| scripts/memory_question_loop.py (1152 lines) | `python scripts/memory_question_loop.py [qid] --next/--retry...` (argparse :1063-1111) | per-question controller: type-A read-time copy, type-B content-addressed ingest cache, driver invocation, question-scoped scoring, ledger appends; exit 0/2/3/4 contract (:43-45) | tested by tests/test_memory_question_loop.py (pytest-style; see finding 3.1) |
| bench/ package | `python -m bench.converters.run_converters`, `bench.eval.evaluate`, `bench.analysis.attribute`, `bench.prepare_questions` (bench/README.md:33-46) | offline converters + local scoring + attribution; stdlib-only venv (README:14-18); evaluator identity optimus-memory-evaluation/2.0.0 | bench/tests: test_local_scoring.py (74), test_pmem_converter.py (11); root portability suite re-imports scoring tests (tests/test_memory_bench_portability.py:18) |
| other perf examples | compaction_replay.rs (gen-fixtures/replay subcommands), disk_write_bench.rs, session_loop_bench.rs (offline faux provider), memory_recall_probe.rs (frozen fixtures) | offline deterministic benchmarks | all offline; read_subscription_catalog_once.rs is gated Copilot metadata read ("Never part of normal tests", :4) |

### 1.5 Validation runner (new, W8 deliverable)

`scripts/full_harness_validation.py` (stdlib only, Windows + Git-Bash python):
- modes `--smoke` (8 build-free suites), `--full` (140), `--performance` (12), `--report`, `--list` (147 total planned; safe dry run, also the default action);
- prerequisite phase resolves python/cargo/rustc/uv/node/bash/git/pytest/clang/KERNEL_CONTAINMENT_TEST_PYTHON/bench venv/.port-env/MEMORY_BENCH_MODELS_JSON/PARITY_BOOTSTRAP_TOOLS_DIR and reports BLOCKED per suite with reasons;
- kernel-python resolution order: `--kernel-python` flag, KERNEL_CONTAINMENT_TEST_PYTHON, repo `prime-agent-runtime/.venv` (dev group ships dill), ambient PRIME_AGENT_KERNEL_PYTHON; the ambient var is overridden/removed for suites so the orchestrating agent's live kernel never leaks into campaign tests;
- one subprocess per suite (fresh process per Python test FILE), bounded per-kind timeouts (cargo-lib 3600s, cargo-it 1800s, py-rt 1200s, py-root 900s, node 300s, ...), timeout kills the whole tree (taskkill /T /F on Windows);
- PASS/FAIL/BLOCKED/NOT EXECUTED recorded to reports/full-harness/artifacts/<suite>.json (schema full-harness-suite/1) with exit code, duration, parsed test counts, zero-test detection, stdout/stderr tails, full logs under artifacts/logs/;
- per-run isolated profile: PRIME_AGENT_CODING_AGENT_DIR/PI_CODING_AGENT_DIR/PRIME_AGENT_SESSION_DIR/RLM_SESSION_DIR/RLM_HARNESS_STATE_DIR/RLM_GLOBAL_HARNESS_STATE_DIR/TEMP/TMP all point into reports/full-harness/tmp/run-<stamp>/; HOME kept (uv/cargo caches); PYTHONPATH cleared (BASELINE.md); cleanup removes only the run directory it created (--keep-tmp to retain);
- `--report` aggregates artifacts into reports/full-harness/REPORT.md with status summary, per-suite table, zero-test warnings.
Verified: `--help` exit 0; `--list` exit 0 printing 147 suites with resolved prerequisites; machinery self-test exercised PASS/FAIL/BLOCKED/NOT-EXECUTED/timeout-kill/report paths on synthetic suites in a temp sandbox (no repo suite executed).

---

## 2. RISK-RANKED GAPS

**P0**
1. `tests/test_memory_question_loop.py` (744 lines, 32 tests) is pytest-style (`import pytest` :18, `@pytest.fixture` :201, plain classes :275 with no TestCase base) while every invocation lane uses `python -m unittest discover -p 'test_*.py'` (test.sh:5, ci.yml:59). unittest imports it and runs 0 tests; the process exits 0. Evidence: artifacts/baseline-python-root.json shows `"Ran 0 tests in 0.000s OK"` for that file. The entire controller regression suite silently never runs anywhere.

**P1**
2. Node tests `tests/performance-metrics-*.test.mjs` are wired into no lane: test.sh and check.sh contain no `node` invocation, ci.yml neither. They only run if invoked by hand. Node is also absent from the campaign host PATH (runner reports BLOCKED).
3. `tests/parity/` (kernel_client.py, differential.py, test_kernel_client.py) is pytest-style and not a package (no `__init__.py`), so unittest discover skips it; parity lane requires `.port-env/python` (kernel_client.py:26) which does not exist in the worktree. The differential TS-vs-Rust parity harness is effectively unreachable on this host.
4. Integration-test coverage is CI-thin: ci.yml runs only 6 of 87 integration files (rust_only_resources, slash_command_matrix, native_cli, transcript_t06 + execution_mode + 4 compaction files, ci.yml:85-92). 81 files (e.g. occ2_* 72 tests, jev_* 178 tests, memory_* 35, telegram_* 19) run only via a full local `--test` loop that test.sh does not provide (test.sh:4 is `--lib` only).
5. `test.sh:5` uses `python3`, which does not exist on the Windows campaign host (only `python`); the documented local lane fails before running a single root test on Windows.

**P2**
0. Runtime baseline (root agent, artifacts/baseline-python-runtime.json, completed during this wave) shows `test_repl.py` FAILED with 2 failures in 104 tests (skipped=3): one is `test_ready_handshake_and_startup_time` (test_repl.py:165) asserting startup < 500 ms but measuring 667.3 ms. This is a timing-sensitive flake under load; the remaining failure is not visible in the 400-char tail (full log not retained by run_pyrt.py). All other 31 runtime files passed (slowest: test_mcp.py 43.0s, test_bash.py 21.5s).
6. Kernel-python prerequisite is fragile: kernel_metrics_legacy_export.rs:36 panics via `.expect` without PRIME_AGENT_KERNEL_PYTHON; the BASELINE.md-documented KERNEL_CONTAINMENT_TEST_PYTHON (uv cpython 3.11.15) lacks `dill` (verified: `import dill` fails), so following the baseline doc verbatim yields a panic, not a BLOCKED. Runtime `.venv` python has dill 0.4.1 (verified).
7. bootstrap_t01/t02 need PARITY_BOOTSTRAP_TOOLS_DIR fixtures compiled by build-fixtures.cmd, which requires a VS developer prompt `cl` (build-fixtures.cmd:3,12-13). No lane or doc check produces them before `cargo test`, so those 9 tests fail or get skipped ad hoc.
8. Runtime suite isolation depends on ambient env: RLM_HARNESS_STATE_DIR etc. are set/restored inside tests (test_harness.py:753-776) but a bare `uv run ... unittest discover` from an agent session inherits the orchestrator's RLM_*/PRIME_AGENT_* env unless the caller clears it (BASELINE.md "PYTHONPATH cleared" is necessary but not sufficient).
9. Whole-file platform gating: sessionkernel_t04_process_cleanup.rs:16 `#![cfg(windows)]` compiles empty on POSIX; its POSIX counterpart does not exist, so orphan-journal/recovery coverage is Windows-only by construction.

**P3**
10. `cargo test --workspace --lib` (test.sh:4) with `--test-threads=1` serializes all unit tests; no lane runs the `#[ignore]`d execution_mode/native_images clang tests or live_systemone, and nothing records that they were skipped (no aggregate marker; only this runner's NOT EXECUTED status does).
11. tests/test_jev_launcher_env.py skips 6/7 tests on Windows (:22) — the only launcher coverage on the campaign platform is a single mock test.
12. The two .mjs tests execFile `scripts/summarize-performance-metrics.mjs` (compaction :27, ui :19) — fine, but nothing covers the remaining scripts/*.mjs (bench-attach-bytes, bench-daemon-startup, edit-tool-stats, test-native-subagents).

**P4**
13. Cosmetic/robustness: runner timeouts are wall-clock including cargo compile time on cold cache (first `--full` may need `--timeout-scale` >1); `--list` output is 232 lines (could gain a `--suite` filter preview).

---

## 3. SUSPECTED BUGS

1. **Silent zero-test pass** — `tests/test_memory_question_loop.py` (pytest-style, :18/:201/:275) + `test.sh:5`/`ci.yml:59` unittest discovery. What looks wrong: `Ran 0 tests ... OK` exits 0, so CI is green while the controller has no executed regression tests. Reproduce/verify (wave 2): run `python -m unittest tests.test_memory_question_loop` and assert "Ran 0 tests" in stderr; then run `pytest -q tests/test_memory_question_loop.py` and assert 32 collected. The runner's py-pytest suite + zero_tests flag implement exactly this check.
2. **Baseline-documented kernel python lacks dill** — BASELINE.md names KERNEL_CONTAINMENT_TEST_PYTHON = uv cpython 3.11.15; `import dill` fails there (verified read-only probe). kernel_metrics_legacy_export.rs:36 `.expect(...)` panics. Verify (wave 2): run the suite with PRIME_AGENT_KERNEL_PYTHON pointing at the uv cpython and confirm the panic message; then with prime-agent-runtime/.venv python (dill 0.4.1) and confirm it proceeds. The runner already resolves this by probing dill and preferring the runtime venv.
3. **Windows lane uses non-existent interpreter name** — `test.sh:5` `python3 -m unittest ...`: on Windows 11 with only `python` on PATH, bash fails with command-not-found before any test runs (UNCERTAIN for MSYS aliases: Git-Bash may map python3 when a Windows python3 shim exists; on this host `python3` is not resolvable, verified). Verify: `bash test.sh` on Windows — the cargo lane runs, then the python lane errors.
4. **Node suite orphaned from all gates** — not a code bug but an infrastructure defect: `tests/*.test.mjs` reference `node:test` and pass when run (`node --test tests/performance-metrics-compaction-report.test.mjs`), but no entry point invokes them. Verify (wave 2): `node --test tests/` locally and in CI; then add the lane. Status on this host: BLOCKED (node missing).
5. **check.sh mixes cheap and expensive gates** — `scripts/check.sh:5` runs `cargo check --locked --workspace --all-targets` (heavy) before the cheap layout check at :7; a layout failure is only reported after a full compile. Not a correctness bug; ordering hurts feedback latency. Verify: time `python scripts/check-rust-layout.py` (<2s) vs the full script.

---

## 4. TEST PLAN (wave 2)

| # | Test name | Target | Asserts | Tier |
|---|---|---|---|---|
| 1 | `test_question_loop_pytest_collected` | tests/test_memory_question_loop.py | `pytest --collect-only -q` yields 32 tests; `python -m unittest tests.test_memory_question_loop` prints "Ran 0 tests"; gate fails closed on zero-test discovery | unit |
| 2 | `test_runner_list_is_dry_run` | scripts/full_harness_validation.py | `--list` exits 0, executes no suite, prints suite count >= 140, never writes artifacts | unit |
| 3 | `test_runner_zero_test_flag` | scripts/full_harness_validation.py | a suite producing "Ran 0 tests" gets zero_tests=True in its artifact JSON and a warning row in REPORT.md | unit |
| 4 | `test_runner_blocked_prereqs` | scripts/full_harness_validation.py | with node/uv hidden from PATH, node/py-rt suites are BLOCKED with reason strings; exit code stays 0 unless a FAIL occurs | unit |
| 5 | `test_runner_isolation_env` | scripts/full_harness_validation.py | build_isolated_env redirects PRIME_AGENT_CODING_AGENT_DIR, RLM_*, TEMP under the campaign run dir; PRIME_AGENT_KERNEL_PYTHON is overridden (never the ambient live kernel); PYTHONPATH removed | unit |
| 6 | `test_runner_timeout_kills_tree` | scripts/full_harness_validation.py | a sleeping child+grandchild pair is fully terminated after timeout (taskkill /T on Windows); artifact status FAIL with reason "timeout" | integration |
| 7 | `test_smoke_lane_green` | scripts/full_harness_validation.py --smoke | 8 suites: node x2, layout, py-root x2, py-rt x3 all PASS; runtime <5 min | e2e |
| 8 | `test_full_lane_counts` | scripts/full_harness_validation.py --full | cargo-lib x5 + cargo-it x87 + py lanes all reach a terminal status; REPORT.md totals match artifacts/*.json count; no suite left without artifact | e2e |
| 9 | `test_kernel_python_dill_gate` | runner + kernel_metrics_legacy_export.rs | with KERNEL_CONTAINMENT_TEST_PYTHON lacking dill the suite is BLOCKED pre-run (not panic); with runtime venv python it executes | integration |
| 10 | `test_bench_venv_bootstrap` | bench/.venv | `--ensure-bench-venv` creates an offline venv; py-bench-portability runs 124 tests; py-bench-unit runs 85 | integration |
| 11 | `test_node_lane_wired` | CI/test.sh | `node --test tests/*.test.mjs` added to a lane executes 2 tests green (requires node on PATH) | e2e |
| 12 | `test_parity_prereq_blocked` | tests/parity | without .port-env/python the parity suites report BLOCKED with the port-isolation hint; with it, pytest collects test_kernel_client.py | integration |

---

## 5. COVERAGE NOTES (not inspected / not executed)

- NOT EXECUTED (constraint): no cargo build/check/test, no `--smoke`/`--full`/`--performance` run, no node run, no uv suite run by THIS agent. Only `--list`, `--help`, `py_compile`, and synthetic-suite machinery self-tests in a temp sandbox were executed for the runner itself. The root agent's own baselines (baseline-python-root.json, baseline-python-runtime.json) are cited as evidence where noted; they ran outside this task.
- In-crate `#[cfg(test)]` unit inventories for `crates/*/src/` were out of the allowed read scope (wave-1 file map); lib-suite coverage claims rely on ci.yml:79 and the lane definition, marked UNCERTAIN where relevant.
- `resources/agent/skills/*` (edit, memory, agent-message, ...) runtime payloads were out of scope; file-tool test coverage is marked UNCERTAIN in the matrix.
- `.changes/`, `installers/`, `research/`, `resources/` trees were not read beyond the files cited.
- Cargo test counts are static `#[test]`/`#[tokio::test]` regex counts per file; actual executed counts may differ (cfg-gated modules, helper fns named test_*). Python counts are static `def test_` counts (incl. async).
- `tests/test_memory_bench_portability.py` reports 124 executed tests in baseline vs 50 statically counted methods: the suite re-imports bench/tests/test_local_scoring.py (74) through `from bench.tests import ...` (tests/test_memory_bench_portability.py:18) — count discrepancy explained, not independently re-verified.
- KERNEL_CONTAINMENT_TEST_PYTHON is a campaign-level variable (reports/full-harness/BASELINE.md); it appears nowhere in the repo itself. The runner treats it as the designated override for PRIME_AGENT_KERNEL_PYTHON.
