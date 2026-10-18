# Full-Harness Validation & Optimisation - Round 2 - Final Report

Scope: verify the whole Optimus Agent harness against the merged state of `main` (v0.1.35 plus
PR #174), fix what the verification proves broken, and ship the evidence with the fixes.
Round 1 shipped as PR #172; this round starts from the merged result and does not repeat it.

## Source

- Worktree: `C:/Users/openclawuser/optimus-fh2-20261018` (`improve/harness-validation-r2`)
- Base: `5feb475968c42757e30ffbc6b7518b331c8ab284` (v0.1.35 + PR #174)
- Pristine baseline worktree: `C:/Users/openclawuser/optimus-fh2-baseline`, detached at the same
  commit, so campaign edits cannot contaminate provenance and atomicity tests.

## Method

- Every heavy job (cargo, tests, python suites) runs through one cross-process queue
  (`fh2q.py`): one job at a time, `CARGO_BUILD_JOBS=2`, `-j 2`, `--test-threads=1`, BelowNormal
  priority, CPU affinity 0-3, hard timeout, and an immutable owner/command/exit-code record.
- Baseline numbers come from the pristine worktree under the same containment, so before/after
  comparisons are like for like.
- Wall times are labelled throttled: the host also ran the agent and Defender.
- Offline only: no paid API, no network provider. Fixtures are the faux provider, the local CLI
  fixture, and real local `bash`, `node` and Python kernels.

## Headline results

| Area | Evidence | Result |
|---|---|---|
| Core loop | gate A + scenario A/B | terminal error/abort now answers its tool calls; cancel wording fixed |
| Memory | gate F, 19 tests | recover damaged store, keep unparseable settings, refuse unserializable apply |
| Bash tool | gate D, 8 tests | an absurd JSON timeout clamps instead of panicking |
| Node tool | gate D, 7 tests | deferred errors no longer destroy the workspace; exits are named |
| OCC-2 daemon | lib tests | name-addressed receivers negotiate |
| Kernel | gate D, 2 tests | snapshot isolation between clients, latency bound |
| Providers | gate H, 899 tests | `pi-ai` lib green |
| Streams and persistence | gate P, 3 tests | interrupted stream answered with an error result, partial transcript durable |
| Workspace lib | gate E2 | `pi-coding-agent` 3747 passed / 0 failed |

## Defects fixed

See BUG-LEDGER.md for repro, file, fix and tests.

- CORE-R2-1 (P2) terminal provider error or abort left assistant tool calls unanswered, so the
  next request was protocol-invalid and `continue()` refused the context.
- CORE-R2-2 (P3) cancel before a tool started recorded the provider abort text.
- MEM-R2-1 (P2) `memory restore` could not recover a damaged authoritative store.
- MEM-R2-2 (P3) `memory configure` destroyed an unparseable settings file.
- MEM-R2-3 (P4) memory apply could write a `null` document.
- OCC-R2-1 (P2) OCC-2 peer lookup ignored name-addressed receivers.
- BASH-R2-1 (P2) an absurd JSON timeout panicked the tool call.
- NODE-R2-1 (P2) a deferred error in a Node cell destroyed the workspace and every binding.
- NODE-R2-2 (P3) `process.exit` was reported as an oversized response.

## Worked, documented, not changed

- EXEC-R2-1: `core::exec::exec_command` drains pipes without a deadline after the child exits.
  Bounding it would change documented behaviour for callers that legitimately read a pipe written
  by a longer-lived child.
- BASH-R2-2: Windows terminates the job, so background descendants do not outlive the call; on
  POSIX they can. Deliberate containment choice, recorded so it is not read as a cross-platform
  guarantee.

## Not verified (blocked, not skipped silently)

- clang-repl kernels: no clang-repl on this host.
- Linux and macOS: not executed; this host is Windows only.
- Official LongMemEval and LoCoMo scoring: neither the dataset nor the official scorer is here.
- Real HTTP providers: the campaign is offline by design.
- Multi-agent scenarios C/D/I/J: written and compiling, blocked by a fixture limitation; the
  scripted provider serves one script, so a child session never gets a usable turn and the
  supervisor reports `protocol_exhausted_after_3_continuations`. Not shipped red; see
  REGRESSION-GATES.md for the evidence and the required fixture work.
- Python runtime suite: 19 failures / 91 errors identically in the pristine and campaign trees.
  Individual classes pass (10/10) and a manually driven `python -m rlm.repl` child answers
  correctly, so the full `discover` run is order- or resource-sensitive on this host. Recorded,
  not claimed green.

## Harness recommendations

- R1: interpreter-gated tests should skip with a printed reason instead of panicking `NotPresent`.
  Sixteen tests look like product failures on any machine without the fixture interpreter.
- R2: the Python runtime suite must run under `uv run --locked --project prime-agent-runtime`.
  A bare interpreter produces 91 errors that are not product defects.

## Reports

- `FEATURE-COVERAGE-MATRIX.md`: every harness feature, its evidence, and its status.
- `BUG-LEDGER.md`: one entry per defect with repro, fix and tests; open and documented items.
- `PERFORMANCE-SCORECARD.md`: measured wall times and test-count delta, labelled throttled.
- `EXPERIMENT-LEDGER.md`: each probing experiment, including refuted hypotheses.
- `REGRESSION-GATES.md`: every gate command with its real result and the triage of each failure.
