# Performance Scorecard - Full-Harness Round 2

Every number below was measured on this host under the campaign containment: one job at a
time through `fh2q.py`, `CARGO_BUILD_JOBS=2`, `-j 2`, `--test-threads=1`, BelowNormal
priority, CPU affinity 0-3, while the host also ran the agent and Defender. Treat the wall
times as **throttled**; they compare like with like (same box, same containment) but they are
not absolute throughput figures for an idle machine.

## Test-suite wall times (throttled)

| Suite | Tests | Wall | Per test |
|---|---|---|---|
| `pi-agent-core` lib | 103 passed, 1 ignored | 1.98 s | 19 ms |
| `pi-ai` lib | 899 passed, 6 ignored | 112.1 s | 125 ms |
| `pi-coding-agent` lib (campaign) | 3745 passed, 10 ignored | 663.0 s | 177 ms |
| `pi-coding-agent` lib (pristine baseline) | 3708 passed, 10 ignored | 940.0 s | 253 ms |
| `bash_executor_unicode_and_cancel` (real bash) | 8 | 8.0 s | 1.0 s |
| `compaction_recovery` | 9 | 2.5 s | 281 ms |
| `node_workspace` (real node v24.16.0) | 7 | 2.4 s | 337 ms |
| `ipython_output_bounds` (real kernel) | 2 | 1.1 s | 529 ms |
| `full_harness_scenarios` (real session + kernel) | 4 | 39.2 s | 9.8 s |
| `scenario_memory_e2e` (real store) | 16 | 8.0 s | 498 ms |
| `memory_source_integrity` | 3 | 1.3 s | 437 ms |
| `scenario_stream_persist` (real session) | 3 | 0.9 s | 311 ms |
| `prime-agent-runtime` bash/guard/winjob (python) | 128 (37 skipped) | 11.8 s | 92 ms |

The pristine column is the round-2 baseline crate run (3708 passed / 16 failed). The 16
failures are all interpreter-gated tests that need `KERNEL_CONTAINMENT_TEST_PYTHON`; with that
variable set the campaign tree passes every one of them, so the two columns are comparable.

## Work added by the campaign

| Crate area | Tests before | Tests after | Delta |
|---|---|---|---|
| `pi-coding-agent` lib | 3724 (pristine) | 3757 (campaign tree, final) | +33 |
| `pi-coding-agent` integration targets | 5 tracked targets | +4 new targets (`full_harness_scenarios`, `scenario_memory_e2e`, `scenario_mult_agent`, `scenario_stream_persist`) | +4 targets |
| `node_workspace` | 4 | 7 | +3 |
| `memory_source_integrity` | 0 new | +2 (Windows-gated + portable twin) | +2 |

## What the campaign did not measure

- No HTTP provider latency: the campaign is offline by design (faux provider and local CLI
  fixtures only), so no real network round trip is included.
- No official LongMemEval or LoCoMo scoring: neither the dataset nor the official scorer is
  present on this host.
- No clang-repl kernels, no Linux or macOS run. Those rows stay BLOCKED in
  FEATURE-COVERAGE-MATRIX.md rather than being estimated.
