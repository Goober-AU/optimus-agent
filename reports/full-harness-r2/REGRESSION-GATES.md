# Regression Gates - Full-Harness Round 2

All jobs ran through the single build queue (`fh2q.py`): one at a time, `CARGO_BUILD_JOBS=2`,
`-j 2`, `--test-threads=1`, BelowNormal priority, CPU affinity 0-3, hard timeout per job.
Raw logs stay outside the tree (they are `*.log`, which this repository ignores);
the result lines and failure names of every run are committed under `artifacts/summary/*.txt`. Every result below is a real run on this host, not an estimate.

| Gate | Command | Result |
|---|---|---|
| Build (pristine 5feb47596) | `cargo check --locked --workspace --all-targets` | PASS, exit 0, 386 s, warnings only |
| Build (campaign tree) | same | PASS after one compile break inherited from an interrupted worker was fixed (`ipython_output_bounds`) |
| A: core loop | `cargo test -p pi-agent-core --lib` | PASS 103 passed, 0 failed, 1 ignored (2.0 s) |
| B: coding agent lib, pristine | `cargo test -p pi-coding-agent --lib` in optimus-fh2-baseline | 3708 passed, 16 failed, 10 ignored (940 s) |
| B2: coding agent lib, campaign | same in the campaign tree, `KERNEL_CONTAINMENT_TEST_PYTHON` set | 3747 passed, 0 failed, 10 ignored (627 s) |
| C: python bash/guard/winjob | `python -m unittest test_bash test_bash_guard test_winjob` | PASS, 128 tests OK, 37 skipped (11.8 s) |
| D: integration batch | `bash_executor_unicode_and_cancel`, `compaction_recovery`, `direct_bash_lifecycle`, `full_harness_scenarios`, `ipython_output_bounds`, `node_workspace` | PASS 8/8, 9/9, compiles (0 tests on Windows), 4/4, 2/2, 7/7 |
| E2: workspace lib | `cargo test --locked --workspace --lib` | `pi-agent-core` 103 PASS, `pi-ai` 899 PASS, `pi-coding-agent` 3747 PASS / 0 failed, `pi-jev` 97 passed / 1 failed |
| F: memory | `scenario_memory_e2e`, `memory_source_integrity` | PASS 16 + 3 |
| H: providers | `cargo test -p pi-ai --lib` | PASS 899 passed, 0 failed, 6 ignored (112 s) |
| P: stream and persistence | `scenario_stream_persist` | PASS 3/3 |
| T: narrow-terminal throughput | `-- throughput_red_indicator` | PASS 1/1 |
| G2: multi-agent scenarios | `scenario_mult_agent` | FAIL 0/4 - fixture limitation, not shipped (see below) |
| RT: python runtime suite, campaign | `uv run --locked --project prime-agent-runtime python -m unittest discover` | 603 tests: 19 failures, 91 errors, 52 skipped |
| RT: python runtime suite, pristine | same in optimus-fh2-baseline | 603 tests: 19 failures, 91 errors, 52 skipped - identical |

## The one workspace failure is pre-existing

`pi-jev scheduler::settlement_tests::retain_waits_for_actual_sink_and_leaves_other_session_running`
fails with `Elapsed(())` in the campaign tree. The same test fails in isolation in the **pristine**
baseline worktree (`gate-j2-jev-bw.log`: 0 passed, 1 failed). It is a timing test that the
campaign never touched, so it is recorded as a pre-existing flake, not a regression.

## The 16 pristine failures are interpreter-gated, not defects

All 16 need a real Python kernel started from an explicit fixture interpreter. With
`KERNEL_CONTAINMENT_TEST_PYTHON` set they all pass (gate B2: 0 failed). Without it they panic
`NotPresent`. Recommendation R1 in BUG-LEDGER.md: skip with a printed reason instead of failing.

## The Python runtime suite fails identically before and after

603 tests produce 19 failures / 91 errors / 52 skipped in both the pristine and the campaign
tree. Individual classes pass in isolation (`test_execution_status`: 10/10 OK), and a single
`python -m rlm.repl` child starts and answers correctly when driven manually, so the full
`discover` run is order- or resource-sensitive on this host rather than broken by the campaign.
Recorded as-is; not presented as green.

## Why `scenario_mult_agent` is not in this PR

The suite compiles and runs, but every child session in it ends with
`status=error, error=protocol_exhausted_after_3_continuations, tool_use_count=0` and
`answer_preview=UNKNOWN_CHILD`. The scripted local CLI fixture serves one response script; a
child session started by the subagent tool inherits the provider but not a fresh script, so the
child never gets a usable turn and the supervisor correctly reports an exhausted protocol after
three continuation attempts. Making scenarios C/D/I/J deterministic needs a per-session script in
the fixture. That is fixture work, not a product fix, so the file stays out of the PR and the
finding is recorded here instead of shipping a red test.

## Environment

Windows 11 host, 12 logical cores, `cargo` 1.x from the pinned toolchain, Node v24.16.0,
CPython 3.11.15. The host also ran the agent and Defender throughout, so all wall times are
throttled numbers measured under the same containment; see PERFORMANCE-SCORECARD.md.
