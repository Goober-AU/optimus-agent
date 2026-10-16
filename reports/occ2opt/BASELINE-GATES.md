# OCC-2 Optimization Campaign — Offline Baseline Gates

**Worktree:** `C:/Users/openclawuser/optimus-occ2opt-20261016`
**Tree measured:** clean HEAD `1863b5dea98e62c60b3757e80190e0ebf66c533b` (branch `improve/occ2-efficiency`)
**Date:** 2026-10-16, 12:42-13:15 local
**Mode:** offline (no model calls, no network, no git commits, no source modifications)

## Headline summary

| # | Gate (command) | Result | Pass | Fail | Exit | Wall time |
|---|----------------|--------|-----:|-----:|-----:|----------:|
| 1 | `cargo test -p pi-coding-agent --test occ_comm_fidelity` | PASS | 7 | 0 | 0 | 662.0 s (cold build 11m00s + 0.20s tests) |
| 2 | `cargo test -p pi-coding-agent --test occ2_comm_fidelity` | PASS | 12 | 0 | 0 | 14.4 s (12.03s link + 1.50s tests) |
| 3 | `cargo test -p pi-coding-agent --lib occ::` | PASS | 206 | 0 | 0 | 487.1 s (lib-test compile; 0.17s tests) |
| 4 | `cargo test -p pi-coding-agent --lib communication_` | PASS* | 16 | 0 | 0 | 1.0 s (state-A binary; see Methodology) |
| 5 | `cargo test -p pi-coding-agent --lib occ` | PASS* | 219 | 0 | 0 | 1.3 s (state-A binary) |
| 6 | `cargo test -p pi-coding-agent --lib session_settings` | PASS* | 2 | 0 | 0 | 0.7 s (state-A binary) |
| 7 | `python -m unittest discover -s prime-agent-runtime/test -p "test_occ1_codec.py"` | PASS** | 44 | 0 | 0 | 1.3 s (0.031s tests) |
| 8 | `cargo test -p pi-coding-agent --lib agent_messages` | PASS* | 27 | 0 | 0 | 1.1 s (state-A binary) |

\* Gates 4/5/6/8 were executed by invoking the already-compiled clean-HEAD lib test binary directly
(`pi_coding_agent-14d7f26ad312a125.exe`) with the same filter argument `cargo test --lib <filter>` passes.
Reason: sibling agents began editing the worktree mid-run (see "Concurrent modification"), so the
literal cargo command recompiled a broken mid-edit tree (gate 4 literal attempt: exit 101, verbatim
below). The binary was verified to be clean-HEAD content (symbol-level check, below).

\** Gate 7 required `PYTHONPATH=<worktree>/prime-agent-runtime/src` for the repo `rlm` package to be
importable. The literal command with fully cleared `PYTHONPATH` fails on import (verbatim below);
the prior campaign ran this gate through `uv run --project` (venv supplies `rlm`).

**Verdict: 100% green on clean HEAD. Zero failures across all 8 gates (334 assertions total:
7+12+206+16+219+2+44+27, where gate 5 re-runs the gate-3 set plus 13 more `occ`-named tests).**

## Environment

```
RUSTUP_HOME=C:/Users/openclawuser/optimus-rust-toolchain/rustup
CARGO_HOME=C:/Users/openclawuser/optimus-rust-toolchain/cargo
CARGO_BUILD_JOBS=3
CARGO_TARGET_DIR=C:/Users/openclawuser/optimus-occ2opt-target-w2   (fresh, this run only)
KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe
cargo 1.98.1 (797e8a9bc 2026-08-05)
rustc 1.98.1 (48a229cea 2026-09-01)
```

- **Cold build** (empty target dir): gate 1 = `Finished test profile in 11m 00s` (full dep graph +
  pi-coding-agent lib + occ_comm_fidelity integration crate).
- **Second cold artifact**: gate 3 compiled the lib *unit-test* binary (487.1 s) — integration tests
  (gates 1-2) link the lib without `#[cfg(test)]` code, so this is a separate large compile.
- **Warm per-test-run times**: 0.7-2.5 s per filtered run (process spawn dominates; libtest itself
  reports 0.10-1.50 s).
- Pre-existing warnings: 351 in the lib (matches the prior campaign's count — no new warnings).

## Per-gate detail

### Gate 1 — `cargo test -p pi-coding-agent --test occ_comm_fidelity`
- Exit 0. `7 passed; 0 failed; 0 ignored` in 0.20s; wall 662.0 s (cold build 11m00s).
- Tests: t01_fixture_shape_and_holdout_rule, t02_round_trip_fidelity_all_scenarios,
  t03_encoding_size_comparison, t04_fault_injection_never_silently_passes,
  t05_semantic_faults_are_rejected, t06_latency_within_budget, t07_aggregate_headline_report.
- Log: `logs/gate1_occ_comm_fidelity.log`

### Gate 2 — `cargo test -p pi-coding-agent --test occ2_comm_fidelity`
- Exit 0. `12 passed; 0 failed; 0 ignored` in 1.50s; wall 14.4 s (12.03s compile+link).
- Tests: t01..t12 (corpus shape, dual round trip, symbolic size, fault injection, wrap policy,
  prompt sentinel split, negotiation absence, classify strict, durable record, three-strike,
  latency budget, aggregate headline).
- Log: `logs/gate2_occ2_comm_fidelity.log`

### Gate 3 — `cargo test -p pi-coding-agent --lib occ::`
- Exit 0. `206 passed; 0 failed; 0 ignored; 0 measured; 3379 filtered out` in 0.17s; wall 487.1 s
  (lib test binary compile).
- Delta vs occ23 campaign (204): +2 = `core::occ::session_settings::tests::
  occ_sessions_bound_reads_fail_closed_and_support_empty_new_chats` and `occ_sessions_isolate_
  chats_resume_and_dynamically_inherit_for_existing_children` — present in HEAD (upstream since
  that campaign), not campaign work.
- Log: `logs/gate3_lib_occ_colon.log`

### Gate 4 — `cargo test -p pi-coding-agent --lib communication_`
- State-A (clean HEAD) result: Exit 0. `16 passed; 0 failed; 0 ignored; 3569 filtered out` in 0.18s;
  wall 1.0 s. Log: `logs/gate4_stateA_lib_communication.log`
- Literal cargo attempt (13:05, mid-edit tree): **exit 101, compile failure**, 158.9 s. Verbatim:
```
error[E0106]: missing lifetime specifier
   --> crates/pi-coding-agent/src/core/agent_messages.rs:965:23
    |
962 |     message: &str,
963 |     registry: &DialectRegistry,
964 |     codebooks: &Occ2CodebooksPresent,
965 | ) -> std::borrow::Cow<'_, str> {
    |                       ^^ expected named lifetime parameter
[... 2 more E0106 at agent_messages.rs:1011 and :1025 ...]
error: could not compile `pi-coding-agent` (lib test) due to 3 previous errors; 240 warnings emitted
```
- Cause: sibling edit saved at 13:01:12 (see "Concurrent modification"), not a HEAD defect.
- Delta vs occ23 campaign (13): +3, from upstream merges since that campaign's tree.

### Gate 5 — `cargo test -p pi-coding-agent --lib occ` (wider filter)
- Exit 0. `219 passed; 0 failed; 0 ignored; 0 measured; 3366 filtered out` in 0.70s; wall 1.3 s.
- Superset of gate 3 (206) + 13 additional `occ`-named tests outside `core::occ::` (e.g.
  communication-activity, ipython-cell rendering tests). Not a re-run of 3+4 only — recorded in full.
- Log: `logs/gate5_stateA_lib_occ.log`

### Gate 6 — `cargo test -p pi-coding-agent --lib session_settings` (scoped)
- Discovery: `--lib session -- --list` → 534 tests (too wide; session_manager, agent_session, etc.).
  Scoped filter `session_settings` → exactly 2 tests, both under `core::occ::session_settings::tests`.
- Exit 0. `2 passed; 0 failed; 0 ignored; 3583 filtered out` in 0.10s; wall 0.7 s.
- Log: `logs/gate6_stateA_session_list.log`, `logs/gate6_stateA_lib_session_settings.log`

### Gate 7 — Python codec gate
- Attempt A (literal task spec; `PYTHONPATH` fully cleared): **exit 1, import failure**. Verbatim:
```
ImportError: Failed to import test module: test_occ1_codec
  File ".../optimus-occ2opt-20261016/prime-agent-runtime/test/test_occ1_codec.py", line 14, in <module>
    from rlm.occ1 import (
ModuleNotFoundError: No module named 'rlm'
----------------------------------------------------------------------
Ran 1 test in 0.000s
FAILED (errors=1)
```
  Cause: `rlm` lives at `prime-agent-runtime/src/rlm` (src layout, not installed into the base
  interpreter). The prior campaign's 44/44 ran through `uv run --project prime-agent-runtime`
  (project venv supplies `rlm`).
- Attempt B (repo src on PYTHONPATH, same controlled interpreter, harness runtime still cleared):
  **exit 0. `Ran 44 tests in 0.031s — OK` (44/44)**; wall 1.3 s. This matches the expected result.
- Command (attempt B):
```
export PYTHONPATH="C:/Users/openclawuser/optimus-occ2opt-20261016/prime-agent-runtime/src"
export KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe
"$KERNEL_CONTAINMENT_TEST_PYTHON" -m unittest discover -s prime-agent-runtime/test -p "test_occ1_codec.py"
```
- Logs: `logs/gate7_attemptA_as_specified.log`, `logs/gate7_attemptB_repo_src.log`

### Gate 8 — `cargo test -p pi-coding-agent --lib agent_messages`
- Exit 0. `27 passed; 0 failed; 0 ignored; 0 measured; 3558 filtered out` in 0.60s; wall 1.1 s.
- Log: `logs/gate8_stateA_lib_agent_messages.log`

## Methodology — why gates 4/5/6/8 used the pre-built binary

1. The lib unit-test binary compiled by gate 3 (`pi_coding_agent-14d7f26ad312a125.exe`, linked
   13:02:15) was built from clean-HEAD sources (rustc read all modules at compile start
   ~12:54:30; the first tracked-file sibling edit landed 13:01:12, during codegen).
2. Symbol-level verification: the binary contains NO sibling-added OCC-2 symbols
   (`Occ2CodebooksPresent`, `render_agent_session_message_prompt`,
   `agent_session_message_prompt_body_with_codebooks` all absent) and DOES contain the HEAD-only
   additions (`core::occ::session_settings` tests present).
3. Direct invocation with a filter is exactly what `cargo test --lib <filter>` does after compile;
   equivalence was verified by re-running `occ::` through the binary (206/0, 3379 filtered —
   identical to gate 3).
4. This avoids recompiling a tree that sibling agents are actively editing.

## Concurrent modification record (IMPORTANT for baseline validity)

Sibling agents `occ2-arch`, `occ2-codebook`, `occ2-daemon` (spawned by the same parent, all
"running" during this baseline) began editing THIS worktree mid-run:

| Time | File | Event |
|------|------|-------|
| 12:42:48 | — | gate 1 starts (tree = clean HEAD) |
| 12:53:40 | `.../deps/occ_comm_fidelity-*.exe` | gate 1 binary linked (state A) |
| 12:54:05 | `.../deps/occ2_comm_fidelity-*.exe` | gate 2 binary linked (state A) |
| 12:54:30 | — | gate 3 starts; rustc reads all lib sources (state A) |
| 12:55:25 | `tests/occ2opt_scratch.rs` (untracked) | first sibling artifact |
| 13:01:12 | `crates/pi-coding-agent/src/core/agent_messages.rs` | first tracked-file sibling edit (79,740 -> 89,659 B) |
| 13:02:15 | `.../deps/pi_coding_agent-14d7f26ad312a125.exe` | gate 3 binary linked (clean HEAD) |
| 13:02:52 | `tests/occ2_comm_fidelity.rs` | sibling edit |
| 13:04:39-13:05:24 | `occ/dialect_load.rs` (new), `occ/session_settings.rs`, `occ/mod.rs`, `daemon_protocol.rs`, `daemon_mode.rs` | sibling edits ongoing |
| 13:05:30 | — | gate 4 literal cargo attempt -> E0106 compile failure (mid-edit tree) |

**Consequence:** every number in this report is clean-HEAD `1863b5dea` (verified), i.e. a valid
known-good baseline. Any later gate run in this worktree must either (a) wait for the sibling
work to be committed/stashed and re-verify the tree, or (b) use a separate worktree. Baseline
numbers must NOT be compared against runs on the sibling-modified tree without re-establishing
which tree state they measured.

## Raw logs

All captured under `reports/occ2opt/logs/`:
`gate1_occ_comm_fidelity.log`, `gate2_occ2_comm_fidelity.log`, `gate3_lib_occ_colon.log`,
`gate4_lib_communication.log` (literal, failed), `gate4_stateA_lib_communication.log`,
`gate5_stateA_lib_occ.log`, `gate6_stateA_session_list.log`, `gate6_stateA_lib_session_settings.log`,
`gate7_attemptA_as_specified.log`, `gate7_attemptB_repo_src.log`, `gate8_stateA_lib_agent_messages.log`.
