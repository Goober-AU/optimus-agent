# Central validation gates (integrator run, 2026-10-17)

Agent: ipc-verify. All heavy steps through the shared gate
(C:/Users/openclawuser/.net-ipc/netbuild.ps1, -j 2, BelowNormal, 4-CPU shared affinity,
--test-threads=2). Every duration below is wall time including gate queue wait.
Measurement label for any number here: measured under throttled gate
(4 CPUs shared, -j 2, BelowNormal).

IMPORTANT gate defect found: PowerShell -File parameter binding silently drops
`-p <value>` from netbuild.ps1's ValueFromRemainingArguments CargoArgs. Probe:
C:/Users/openclawuser/.net-ipc/argprobe.ps1 with `-AgentName ipc-verify test -p pi-ai
--lib --offline --locked` yields CargoArgs = `test | --lib | --offline | --locked`
(pi-ai and -p are gone). `--package=<name>` passes through correctly and was used
for steps 4 and 5. Consequence: step 2 and step 3 invocations actually ran
`cargo test --lib` workspace-wide (fail-fast): pi-agent-core, pi-ai, pi-coding-agent
binaries ran; pi-jev and pi-tui were never reached because pi-coding-agent failed.

| # | Command (as intended) | Result | Detail |
|---|------------------------|--------|--------|
| 1 | check --workspace --all-targets --offline --locked | PASS | exit 0, 3m55s (235.4s). 0 errors. Warning census: pi-coding-agent lib 356 (= known baseline), pi-ai lib 9 / lib-test 13 (includes provider_transport_bench.rs:170 unused field `content_length`, teammate bench file), pi-agent-core 2, pi-jev 4, pi-tui 7. Log: step1_check_workspace.log |
| 2 | test -p pi-ai --lib (first attempt, before -p fix) | FAIL (compile) | exit 101, 897.2s (~14 min queue + ~1 min compile). error: `1 positional argument in format string, but no arguments were given` at crates/pi-ai/src/providers/sse_parse_bench.rs:1003:101 (`{:+.1}` with no positional arg). 0 tests ran. File mtime 04:27:38 postdates the failing compile: transient mid-edit state; the sse teammate landed the arg `(new_mean - old_mean) / old_mean * 100.0` and the same suite compiled and passed in step 3. Log: step2_test_pi_ai_lib.log |
| 3 | test -p pi-coding-agent --lib (actually workspace-wide, see gate defect) | FAIL | exit 101, 2143.2s. pi-agent-core: 83 passed/0 failed (0.25s). pi-ai: 881 passed/0 failed/4 ignored (70.40s) - full providers suite green including sse_parse_bench and provider_transport_bench_ttft. pi-coding-agent: 3623 passed/17 failed/10 ignored (487.76s). pi-jev, pi-tui: not reached (fail-fast). 17 failures: 15 x env `NotPresent` (KERNEL_CONTAINMENT_TEST_PYTHON unset; kernel_job.rs:264, retained_stop_tests.rs:151, repl_manager.rs:5175), 1 x `uv pip install runs: program not found` (agent_session.rs:22129; uv lives at C:/Users/openclawuser/.local/bin/uv.exe, not on PATH), 1 x assertion `a fresh settlement is installed` (agent_session.rs:19991, post_compaction_continuation_tests::settle_clears_the_ownership_slot_and_wakes_waiters - in-process, no env needed; flake-vs-real undetermined). Log: step3_test_pi_coding_agent_lib.log |
| 4 | test --package=pi-ai --release --lib --no-run | PASS | exit 0, 346.7s. Release test binary built (pi_ai-25ee42e8e2dcee38.exe), 0 errors. Log: step4_test_pi_ai_release_norun.log |
| 5 | test --package=pi-coding-agent --release --lib --no-run | PASS | exit 0, 1069.7s (17m50s). Release test binary built (pi_coding_agent-ed88d19714d755ce.exe), 0 errors, 0 x E0425, 0 mentions of debug_hint_fixture - the jev_bridge release test compile fix is validated. Log: step5_test_pi_coding_agent_release_norun.log |

Light steps (no gate):
- python -m unittest discover -s tests -p 'test_*.py' (python = 3.11.15; python3 not on
  git-bash PATH; PYTHONPATH unset): OK, 282 tests, 10 skipped, exit 0, 84.2s.
  No 0xC0000142 desktop-heap issue. Log: light_python_unittest.log
- python scripts/check-rust-layout.py: PASS, "Native resource layout verified; no
  TypeScript implementation or npm workspace." Log: light_check_rust_layout.log
- git diff --check: exit 0 (clean). Informational CRLF->LF notices only, on 4 teammate
  files (providers/mod.rs, openai_http_client_pool.rs, agent_message_transport.rs,
  daemon_mode.rs).

Reproduction recipe for the 17 step-3 failures (adjudication re-run, not executed -
out of time budget; would also rebuild debug pi-ai/pi-coding-agent since
sse_parse_bench.rs changed after step 3):

  export KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe
  export PATH=/c/Users/openclawuser/.local/bin:$PATH   # uv for agent_session.rs:22129

then re-run step 3's command (use `--package=pi-coding-agent`, not `-p`, see gate defect)
optionally with the 17 failing test names as filters.
