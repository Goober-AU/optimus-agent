# Regression Gates

Date: 2026-10-18
Baseline: `8ae54da78d94920b543e22567403b7e939cb5bd2` (`origin/main`)
Branch: `improve/context-rot-resilience`
Host: Windows, `pi-coding-agent` crate only. No cross-platform matrix was run.

## 1. Method

Two worktrees were built separately, each with its own `CARGO_TARGET_DIR`:

| Tree | Path | Target dir |
|---|---|---|
| Baseline | `C:/Users/openclawuser/optimus-rot-baseline` (detached `origin/main`) | `optimus-rot-target-base` |
| Branch | `C:/Users/openclawuser/optimus-rot-20261018` | `optimus-rot-target` |

Both sides used the same environment:

```
env -u PYTHONPATH \
  KERNEL_CONTAINMENT_TEST_PYTHON="C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe" \
  cargo test -p pi-coding-agent --lib
```

`PYTHONPATH` is removed because an inherited value makes the kernel-containment tests
spawn the wrong interpreter; the variable is the documented input for those tests.

## 2. Result

| Run | Baseline | Branch | Delta |
|---|---|---|---|
| `--lib`, default threads | 3745 passed, 4 failed, 10 ignored | 3770 passed, 9 failed, 10 ignored | +25 passed, +5 failed |
| `--lib`, `--test-threads=1` | 3748 passed, **1 failed**, 10 ignored, 643.9 s | 3779 passed, **1 failed**, 10 ignored, 609.6 s | **+31 passed, same single failure** |
| `--test compaction_kernel_parity` | 23 passed, 0 failed | 23 passed, 0 failed | none |
| `--test compaction_parallel_fallback` | 2 passed, 0 failed | 2 passed, 0 failed | none |

The serialized run is the verdict. It removes the cross-test interference that makes the
default-thread run unstable, and the two sides then agree exactly: one failure each, and it
is the same test.

Failing in both trees, serialized:

```
core::agent_session::post_compaction_continuation_tests::enabled_skill_is_importable
```

Cause: `uv pip install runs: Error { kind: NotFound, message: "program not found" }`.
`uv` is not on `PATH` for the test child process on this host. This is an environment gap,
not a code defect, and it fails identically at the baseline commit.

## 3. Default-thread run: why the extra failures are not regressions

The same baseline commit produced three different failure sets in three consecutive runs
of the same suite (`--lib`, `--tests`, `--tests --no-fail-fast`). Their intersection was
`enabled_skill_is_importable` only. Failure sets seen at baseline:

- run A (`--lib`): `enabled_skill_is_importable`, `get_api_key_for_provider_prefers_auth_storage`,
  `native_neon::tests::neon_frame_fixture_keeps_editor_jev_history_and_theme_switching`,
  `tools_manager::tests::offline_mode_skips_the_download`
- run B (`--tests`): `enabled_skill_is_importable`, `custom_editor::tests::neon_frame_preserves_wrapping_cursor_and_scroll_indicators`,
  `native_neon::cache_tests::long_transcript_editor_repaints_do_not_reformat_unchanged_neon_lines`,
  `native_neon::tests::neon_frame_fixture_keeps_editor_jev_history_and_theme_switching`,
  `native_status::goal_tests::goal_status_and_heartbeat_share_the_painted_context_row`,
  `native_host::tests::ui_speed_typed_direct_and_wrapped_replay_match_frozen_json_adapter_at_every_event`
- run C (`--tests --no-fail-fast`): `enabled_skill_is_importable`,
  `dispatch_delivery_tests::{child_backlog_batches_once, family_batch_does_not_skip, provider_retry_reuses}`,
  `native_neon::cache_tests::long_transcript_editor_repaints_do_not_reformat_unchanged_neon_lines`,
  `native_host::tests::ui_speed_typed_direct_and_wrapped_replay_match_frozen_json_adapter_at_every_event`

The unstable families are painted-frame comparisons that read global theme state and
wall-clock timings (`editing`/`theme` singletons, elapsed-time assertions). They change
composition with scheduling. Every branch failure in the default-thread and nextest-style runs
belongs to a family that already fails at baseline in at least one run:

| Branch failure | Baseline evidence |
|---|---|
| `enabled_skill_is_importable` | fails in all three baseline runs |
| `custom_editor::tests::neon_frame_preserves_wrapping_cursor_and_scroll_indicators` | run B |
| `custom_editor::tests::placeholder_replaces_the_empty_second_line` | same painted-frame family as run B |
| `native_neon::cache_tests::{long_transcript_editor_repaints..., timeline_cache_refreshes..., timeline_cache_updates...}` | runs B and C |
| `native_neon::tests::neon_frame_fixture_keeps_editor_jev_history_and_theme_switching` | runs A and B |
| `native_status::goal_tests::goal_status_and_heartbeat_share_the_painted_context_row` | run B |
| `native_host::tests::ui_speed_typed_direct_and_wrapped_replay_...` | runs B and C |

Serialized, the whole family passes in both trees, which confirms scheduling sensitivity
rather than a behavioural difference.

## 4. Integration targets touched by this PR

| Target | Baseline | Branch | Note |
|---|---|---|---|
| `compaction_kernel_parity` | 23/0 | 23/0 | Compaction constants and truncation pins unchanged |
| `compaction_parallel_fallback` | 2/0 | 2/0 | Unchanged |
| `compaction_length_recovery` | not run at baseline | 7/0 | Fixed a struct literal that this PR had to extend |
| `compaction_observability` | not run at baseline | 7/0 | Same |
| `compaction_summary_guard` | not run at baseline | 2/0 | Same |
| `compaction_recovery` | not run at baseline | 9/0 | End-to-end compaction, restart, subagent, cut-point |
| `jev_ui_tests` | 63/1 | 63/1 | Same single failure `the_daemon_surface_is_capability_gated_and_read_only_where_it_gets` in both trees |

`compaction_length_recovery`, `compaction_observability`, `compaction_summary_guard` and
`compaction_recovery` were not run at the baseline commit before this branch. They are
listed with branch results only so the numbers are not presented as a comparison. The
struct literals in the first three had to gain the new `task_state` field to compile, which
is why they were run here.

`jev_ui_tests` asserts source text. Two assertions referenced
`get_tray_context_usage_text`; this PR renames that accessor, so the assertions were updated
to `get_tray_context_health_text` and `get_tray_context_health`. Its unrelated daemon
capability-gate failure is pre-existing and identical in both trees.

## 5. New tests added by this PR

| Suite | Tests | Command |
|---|---|---|
| Task-state ledger | 12 | `cargo test -p pi-coding-agent --lib task_state` (14 with the mode test) |
| Ledger mode precedence | 1 | same filter |
| Evidence-recovery truncation | 6 | `cargo test -p pi-coding-agent --lib truncate_for_summary` |
| Context health scoring | 13 | `cargo test -p pi-coding-agent --lib context_health` |
| Tray composition | 15 | `cargo test -p pi-coding-agent --lib tray` |
| Tray painted status | 1 updated | `--lib native_host_status_tests` |
| Offline research harness | 125 | `python3 -m unittest discover -s research/context_rot/tests -t . -p 'test_*.py'` |

All green on the branch.

## 6. NOT EXECUTED

- Full workspace suite (`cargo test --workspace`). Only `pi-coding-agent` was gated.
- Node-dependent targets (`node_workspace`, `native_cli`, `execution_mode`): Node.js is
  not installed on this host. They fail at baseline for the same reason.
- Parity targets needing fixtures (`memory_scope_parity`, `telegram_parity`,
  `bootstrap_t01/t02`, `oauth_auth_storage`, `jev_compare_tests`): the `PARITY_*` state
  roots and bootstrap fixtures are absent on this host. They fail at baseline for the same
  reason.
- macOS and Linux runners.
- Any real-model gate that depends on the installed application. The installed app was not
  modified.
