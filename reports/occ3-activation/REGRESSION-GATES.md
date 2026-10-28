# Regression gates

Every number below is from a real run in this worktree on 2026-10-28. Nothing is reported as
passing that was not run.

## 1. Workspace

| Gate | Command | Result |
|---|---|---|
| whitespace | `git diff --check` | clean, exit 0 |
| native layout | `python scripts/check-rust-layout.py` | `Native resource layout verified; no TypeScript implementation or npm workspace.` |
| workspace check | `cargo check --locked --workspace --all-targets` | no errors |

## 2. Rust, this change

| Suite | Command | Result |
|---|---|---|
| OCC-3 and native events | `cargo test -p pi-coding-agent occ3` | 72 passed, 0 failed, 2 ignored |
| communication indicator | `cargo test -p pi-coding-agent --lib communication` | 26 passed, 0 failed |
| indicator module | `cargo test -p pi-coding-agent --lib communication_activity` | 17 passed, 0 failed |
| latent activity end to end | `cargo test -p pi-coding-agent --test occ3_latent_activity` | 7 passed, 0 failed |
| activation gates | `cargo test -p pi-coding-agent --lib occ3_connectors::activation` | 7 passed, 0 failed |
| OCC-3 settings | `cargo test -p pi-coding-agent --lib settings_manager::tests::occ3` | 3 passed, 0 failed |

The 2 ignored tests are pre-existing: `native_send_admits` and `native_send_duplicates_are_refused_without_replay`
carry `#[ignore]` for the same runtime-less-fixture shutdown hang recorded at baseline.

## 3. Rust, interactive isolation

`cargo test -p pi-coding-agent --lib modes::interactive`:

| Threads | Result |
|---|---|
| parallel | 737 passed, 11 failed, 1 ignored |
| `--test-threads=1` | **748 passed, 0 failed**, 1 ignored, exit 0 |

All 11 parallel failures are pre-existing test-isolation defects in this crate: the first panic is in
`custom_editor::tests::neon_frame_preserves_wrapping_cursor_and_scroll_indicators`
(`theme.rs:793`), which poisons the global theme slot and makes later render and cache snapshot
tests mismatch. The two tests nearest this change pass in isolation:
`agent_graph::tests::graph_is_centered_between_the_header_top_and_horizontal_transcript_border` and
`context_health_bar_tests::context_health_bar_paints_beside_tps_in_both_styles_and_narrow_terminals`.

## 4. Rust, one intentional contract change

`native_send_acknowledges_a_queued_admission_without_requiring_consumption` asserted that the daemon
never verifies the `Consumed` outcome. That is the behaviour the campaign forbids, so the assertion
was replaced rather than the behaviour: the test now also asserts that a queued admission projects
no latent completion. This is a reviewed contract update, stated here so it is not mistaken for a
weakened test.

## 5. Extension Python suite

`cd extensions/sglang-occ3 && python -m pytest tests/ -q`

| Tree | Result |
|---|---|
| this branch | 115 failed, 1808 passed, 351 skipped, 2 xfailed, 0 errors |
| pristine `git archive HEAD` | 116 failed, 1719 passed, 351 skipped, 2 xfailed, 0 errors |

Failure attribution by file, unchanged before and after:

| File | This branch | Baseline |
|---|---|---|
| `test_v3_native_package.py` | 72 | 72 |
| `test_v3_native_image_build.py` | 42 | 43 |
| `test_native_install.py` | 1 | 1 |

The one-test difference is `test_missing_old_or_broken_build_tools_fail_before_pip`, which flaps in
the pristine baseline too. Cause of the two families: Windows filesystem identity races in
`deploy/v3` (`staged_input_identity_drift`, `input_identity_drift`) raised when a freshly written
temp file's metadata changes between two `fstat` calls, plus `test_native_install`'s fastapi router
internals. Pre-existing, unrelated to this change.

Related suites: `test_calibration_artifact` (19), `test_calibration_corpus` (16),
`test_native_method_registry`, `test_alignment_dispatch` (13), `test_v3_persistence` (20),
`test_controller` + `test_v3_lifecycle` + `test_v3_supervisor` + `test_v3_supervisor_admission` +
`test_v3_service_checks` + `test_v3_docker_config` + `test_v3_persistence` (360) all pass.

## 6. Repository Python suite

`python -m unittest discover -s tests -p 'test_*.py'`

| Tree | Result |
|---|---|
| this branch | 293 run, 76 failures, 11 skipped |
| pristine `git archive HEAD` | 293 run, 70 failures, 11 skipped |

Every failure is in `test_memory_bench_snapshot_export`. The failing test functions are the same set
in both runs; the differing ids are parametrised cases whose ids embed a random temporary directory
name, so the counts and ids vary run to run. No OCC-3 file is touched by that suite. Pre-existing,
not attributable to this change.

## 7. Not run

- no live DGX window, so no live startup, readiness, capture, consumption, semantic or benchmark gate;
- no Docker build or image attestation in this session;
- no `scripts/check.sh` end-to-end: `cargo check` and the layout check were run individually and both
  pass; the packaged Python suites are the two suites in sections 5 and 6.
