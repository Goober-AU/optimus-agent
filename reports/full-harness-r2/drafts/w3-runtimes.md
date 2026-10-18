# w3-runtimes — Node workspace defects (F1, F2)

Worker: w3-node. Scope: crates/pi-coding-agent/src/core/tools/node.rs,
node_worker.cjs, tests/node_workspace.rs. No cargo build was run by this worker
(the parent owns the build queue); the Rust files were parsed with
`rustfmt --check` (exit 0) and the worker was exercised with the real node
v24.16.0 binary through a protocol driver.

## F1 — a deferred async error killed the workspace and discarded every binding

Reproduced behaviour: a cell that schedules `setTimeout(() => { throw ... })`
leaves work running after it reports `done`. With no process-level handler,
Node's default `uncaughtException` handling terminated the worker, so the next
cell failed with `The pipe is being closed. (os error 232)` plus "Node
workspace reset" and lost all bindings.

Fix (worker, crates/pi-coding-agent/src/core/tools/node_worker.cjs:25-41):
- `process.on('uncaughtException')` and `process.on('unhandledRejection')`
  convert the error into one protocol notice frame
  `{"event":"workspace-notice","kind":...,"message":...}` written through the
  original `process.stdout.write`, so it is never attributed to a running cell.
- The worker keeps its bindings and keeps serving cells; the error text is
  clipped to 2000 chars in the worker.

Fix (Rust, crates/pi-coding-agent/src/core/tools/node.rs):
- :20 `NOTICE_EVENT` marker constant; :17-28 `FRAME_CAP`, `NOTICE_LIMIT`,
  `MAX_NOTICES`, `EXIT_WAIT`.
- :230 the response loop consumes a notice frame, buffers it and keeps waiting
  for the frame that answers the current request id (so the pairing is intact).
- :304 `push_notice` keeps a bounded, structured copy (2000 bytes per notice,
  at most 16 notices).
- :320 `append_notices` renders a visible block on the result ("this result is
  not a failure", "bindings are intact"); it also runs on the error/timeout
  path (:273) so buffered notices are never silently dropped.
- :354 `result` now also carries `details.backgroundNotices` (kind, message).
- The cell's `is_error` still comes only from the worker's own `error` flag, so
  a deferred error never marks the cell as failed.

## F2 — an ordinary exit was reported as a framing error

Reproduced behaviour: `process.exit(3)` hit the shared "exited or returned an
oversized response" branch because a short read without a terminator was treated
as a framing violation.

Fix (Rust, crates/pi-coding-agent/src/core/tools/node.rs):
- :62 `read_frame` isolates the bounded read.
- :73 `message` splits the three outcomes: a terminated line parses as JSON; a
  full `FRAME_CAP` buffer with no terminator is an oversized framing error
  (:91); a short read without a terminator asks for the exit explanation.
- :103 `gone_message` waits up to 3 s for the root exit status and reports
  `Node workspace exited with status N` instead of blaming the pipe.
- The existing reset path is unchanged: the dead process is stopped, the guard
  stays empty, and the next cell spawns a fresh workspace.

## Tests

- `deferred_async_error_from_one_cell_does_not_break_later_cells`
  (tests/node_workspace.rs:157) unchanged.
- `process_exit_reports_the_exit_and_the_next_cell_recovers`
  (tests/node_workspace.rs:235) unchanged.
- Added `deferred_async_error_is_reported_as_a_background_notice`
  (tests/node_workspace.rs:190): proves the notice is visible on the next
  result, that the cell stays successful, and that
  `details.backgroundNotices[0].kind == "uncaughtException"`.
- Added unit test `background_notices_are_bounded_and_never_fail_the_cell`
  (node.rs:443) covering rendering, bounding, the notice-count cap, and the
  unchanged details shape when there are no notices.
- Formatting only: two hunks of `concurrent_cells_are_serialized_with_their_own_output`
  were reformatted so the file passes `rustfmt --check`.

## Verification performed here (no cargo)

- `rustfmt --check --edition 2021` on node.rs and tests/node_workspace.rs: parse
  clean, exit 0.
- `node --check node_worker.cjs`: exit 0.
- Real node v24.16.0 protocol driver against the edited worker:
  - deferred `throw` in a timer -> one `workspace-notice` frame, next cell
    answered `surviving binding`, worker still alive.
  - floating `Promise.reject` -> one `workspace-notice` frame, bindings kept.
  - `throw new Error('deliberate')` inside a cell -> still `error:true` in the
    cell's own done frame (exceptionDetails path unchanged).
  - `process.exit(3)` -> stdout closes with no further frames, which is the exit
    path the Rust side now names.

## Residual risk

The Rust change has not been compiled here. The parent must run:
`cargo check --locked -p pi-coding-agent --all-targets`, then the two test
commands supplied in the reply.
