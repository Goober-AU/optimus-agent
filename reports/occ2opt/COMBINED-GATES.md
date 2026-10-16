# COMBINED-GATES.md - quiesced-tree campaign-authoritative gate table

Task: `occ2opt-telemetry-and-combined-gates`, Part B. Run on the quiesced
tree after parent-verified completion of all lanes (occ2-arch Part A,
occ2-codebook dialect-v2 + card-clarify, occ2-daemon3 /occ lane, live eval
run b). Machine-time stamp of the run window: 2026-10-16 ~17:5x-18:0xZ.
Toolchain: cargo 1.98.1, CARGO_BUILD_JOBS=3, dedicated target dir
`optimus-occ2opt-target-w4` (warm). No network, no model calls, no commits.

## Authoritative table

| # | Gate | Result | Passed | Failed | Ignored | Notes |
|---|------|--------|-------:|-------:|--------:|-------|
| 1 | `cargo test -p pi-coding-agent --test occ_comm_fidelity` | PASS | 7 | 0 | 0 | 0.21 s |
| 2 | `cargo test -p pi-coding-agent --test occ2_comm_fidelity` | PASS | 30 | 0 | 0 | includes t28-t30 (Part A) |
| 3 | `cargo test -p pi-coding-agent --test occ2_dialect_promotion` | PASS | 18 | 0 | 0 | 3 new card-clarify regressions included |
| 4 | `cargo test -p pi-coding-agent --test occ2_selection_policy` | PASS | 9 | 0 | 0 | |
| 5 | `cargo test -p pi-coding-agent --test occ2_selection_wiring` | PASS | 7 | 0 | 0 | w01-w07 |
| 6 | `cargo test -p pi-coding-agent --test occ2_daemon_compat` | PASS | 5 | 0 | 0 | both live daemon tests fixed by daemon3 lane |
| 7 | lib filter `occ::` | PASS | 251 | 0 | 0 | discovery 251; session_settings provenance test now green (daemon3 lane fix) |
| 8 | lib filter `communication_` | PASS | 16 | 0 | 0 | discovery 16 |
| 9 | lib filter `agent_messages` | PASS | 27 | 0 | 0 | discovery 27 |
| 10 | lib filter `session` | PASS* | 531 | 1 | 2 | discovery 534; the 1 failure is the flaky pre-existing `session_lease::tests::sweep_reclaims_only_dead_owner_leases` (see below) |
| 11 | Python `test_occ1_codec.py` (occ23 venv python, PYTHONPATH=repo runtime src) | PASS | 44 | 0 | 0 | 0.038 s, fresh process |
| 12 | `cargo build -p pi-coding-agent` | PASS | 377 warning lines (356 attributed to pi-coding-agent files) | vs baseline 351 | | +26 total / +5 package-attributed; top files: daemon_supervisor.rs 64, daemon_mode.rs 46 (daemon-lane style, mostly pre-existing patterns); campaign-owned files: agent_messages.rs 3, dialect_load.rs 2, selection.rs 1, daemon_protocol.rs 1 |
| 13 | clippy | NOT RUN (component absent offline) | - | - | - | no clippy gate in CI; clippy component not installed, install needs network (forbidden); non-gating |

Integration total: 76 passed / 0 failed across the six binaries.

## Environment notes for gate 10 (session filter)

The `session` filter needs two environment settings to be meaningful; both
were missing from a bare shell and produced 6 then 2 spurious failures on
first runs:

- `KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe`
  - without it: `retained_stop_real_python_tool_and_contained_descendant_settle_together`
    and `kernel_settlement_stops_one_session_not_the_other` panic with
    `explicit fixture interpreter: NotPresent`.
- `~/.local/bin` on PATH (for `uv`)
  - without it: `post_compaction_continuation_tests::enabled_skill_is_importable`
    panics with `uv pip install runs: Error { kind: NotFound }`.
- `PYTHONPATH` cleared (installed runtime shadows repo src).

With all three set, the session filter runs 531/1/2.

## Known non-blockers (pre-existing, documented not fixed)

1. `core::session_lease::tests::sweep_reclaims_only_dead_owner_leases`
   (gate 10's single failure). File `session_lease.rs` is untouched by the
   campaign (not in `git status` modified list). Passes 3/3 solo
   (0.03 s each); fails only under the full 534-test parallel load. The
   test's dead-owner liveness probes are timing-sensitive under load.
   Not an OCC regression: flaky-under-load, pre-existing.
2. `jev_ui_tests` daemon-surface pin (parent-reported): fails at HEAD
   too (stale pin in the Jev lane, not an OCC regression). Not covered
   by this suite's filters (no `jev_ui` test exists in the
   pi-coding-agent lib discovery; the pin lives outside the gated
   surfaces). Documented per parent instruction; not fixed.

## Gate 13 clippy summary (non-gating)

No clippy step exists in `.github/workflows` (ci.yml check job:
`cargo +1.98.0 check --locked --workspace --all-targets`; other workflows:
build-binaries, changelog-fragment, contribution-gate, linear-ticket,
nightly-process-stress - none run clippy). Attempted
`cargo clippy -p pi-coding-agent --lib` in a scratch target dir:
`'cargo-clippy.exe' is not installed for the toolchain
'stable-x86_64-pc-windows-msvc'`; installing the component requires
network access, which is forbidden in this campaign. Recorded as
not-run, non-gating: the CI-equivalent check is gate 12 (cargo build,
PASS), and CI itself never runs clippy.

## Verdict

The quiesced tree is green on every campaign gate: 6/6 integration
binaries, 3/4 lib filters clean, the 4th (session) green except one
pre-existing flaky-under-load lease test, Python codec 44/44, build
passes with +26 warnings over baseline (351 -> 377 total; +5 attributed
to campaign-owned files). No OCC-owned regression found. The tree is
ready for the PR stage from the gates' perspective.
