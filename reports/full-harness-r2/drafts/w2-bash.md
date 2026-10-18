# w2-bash — Bash and process execution (round 2 working notes)

Status: IN PROGRESS (nothing verified by a green run yet).

## Setup

- Repo/worktree: C:/Users/openclawuser/optimus-fh2-20261018 (branch improve/harness-validation-r2)
- Private target dir: C:/Users/openclawuser/optimus-fh2-target-w2
- Every cargo/python job goes through C:/Users/openclawuser/fh2-build-queue/fh2q.py
  (--owner w2-bash, -j 2, --test-threads=1). No direct cargo runs.
- Host: Windows, Git Bash shell (PRIME_AGENT_BASH_SHELL=C:/Users/openclawuser/.prime/agent/git-bash/bin/bash.exe).

## Plan

1. Baseline: `cargo test --locked -p pi-coding-agent --lib -- bash` (queue job).
2. Real-path acceptance tests (new, added to crates/pi-coding-agent/src/core/tools/bash.rs
   `#[cfg(test)] mod tests::real_paths`): cwd with spaces/unicode, quoted paths, stdout+stderr,
   exit 0/nonzero/127, empty output, unicode through the pipe (incl. 8 KiB boundary), binary
   output, env overrides, cwd-that-is-a-file fault injection, timeout (partial output + process
   gone), cancellation (partial output + process gone), 3000-line truncation + complete spill,
   120000-byte single-line byte-limit truncation, concurrent calls, pipeline.
3. Integration test `bash_executor_unicode_and_cancel` (Windows containment) re-run.
4. Python runtime: test_bash.py, test_bash_guard.py, test_winjob.py with the repo src on
   PYTHONPATH; plus a real-path probe (artifacts/w2-bash-python-probe.py) for await/handle API,
   kill, concurrent handles, immediate-await cancellation, descendant holding the stdout pipe,
   no orphan processes.
5. Benchmark spawn/collect overhead only if a change is kept.

## Log

- Queue policy changed by the parent mid-run: children must not hold fh2q jobs (a child's job
  dies with its turn). My queued job was killed before it acquired the lock; no job of mine is
  running and no lock file was left behind. The parent runs the jobs below.

## Defect candidates under investigation

1. `LocalBashOperations::exec` timeout path: `Duration::from_secs_f64(seconds)` PANICS on an
   overflowing timeout (e.g. a JSON `timeout: 1e300`) instead of treating it as "no timeout".
   Reproducer test added: `core::tools::bash::tests::real_paths::real_absurd_timeout_values_do_not_panic`.
   Status: waiting for the queue to confirm red, then fix with `Duration::try_from_secs_f64`.
2. Observation only (not a defect, parity): `core::exec::exec_command` awaits its stdout/stderr
   reader tasks without a bound after the child exits, so a descendant that left the process
   group and holds the pipe can block the call past its timeout. It has no production callers
   (`core::extensions::loader` has its own local `exec_command`), and TS `exec.ts` behaves the
   same, so no change is planned unless the owner asks.
3. Observation: on Windows the Python helper terminates the job (killing background
   descendants) when the shell settles, while POSIX lets them outlive the call. Documented
   per-platform containment divergence; not changed.

## Change kept (fix for defect 1)

File: crates/pi-coding-agent/src/core/tools/bash.rs

- New `pub const MAX_TIMEOUT_SECONDS: f64 = 7 * 24 * 60 * 60` (one week) and
  `fn effective_timeout_seconds(seconds: f64) -> Option<f64>`: non-finite (inf/NaN) or
  <= 0 -> None ("no timeout", the existing TS truthiness behaviour); a finite value is
  clamped to the ceiling.
- `LocalBashOperations::exec` no longer calls `Duration::from_secs_f64` on raw input. It arms
  `options.timeout.and_then(effective_timeout_seconds).and_then(Duration::try_from_secs_f64)`,
  so an overflow is impossible and a huge request means "no timeout" instead of a panic.
- The timeout error reports the ARMED (clamped) value: `timeout:{armed}` -> "Command timed out
  after N seconds".
- `format_bash_call` renders the clamped value, so the call line shows the timeout that will
  actually fire; non-finite values render no suffix.

Root cause: `std::time::Duration::from_secs_f64` PANICS when the value overflows `Duration`
(> ~5.8e11 years) or is NaN. `execute_bash`'s input timeout comes from
`params["timeout"].as_f64()`, so a model call with a huge/overflowing number (serde_json maps
an out-of-range float such as 1e400 to +inf) reached the panic inside the select.

Tests added (all in `crates/pi-coding-agent/src/core/tools/bash.rs`):
- `tests::real_paths::real_absurd_timeout_values_do_not_panic` (entry level: 1e300, +inf, NaN,
  -5, 0 all run the command; the call never panics)
- `tests::real_paths::absurd_timeouts_render_the_effective_value` (rendered suffix shows
  604800s for 1e300/f64::MAX, nothing for inf/NaN/negative/zero, 2s for a normal value)
- `tests::real_paths::a_json_timeout_overflow_never_becomes_finite` (serde_json entry)
- `tests::real_paths::real_timeout_keeps_partial_output_and_kills_the_shell` now uses a normal
  2 s timeout, asserts it really waits and still fires.

Not proven red by a run: a child may not hold queue jobs, so the pre-fix failing run was not
executed. The panic is Rust's documented `from_secs_f64` contract; the new tests pin the fixed
behaviour.
