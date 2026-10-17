# W2 — Tool-execution audit (Bash, IPython, Node, Clang, dispatch)

Wave 1 (read-only). Scope: the 12 mapped files plus the tool-registry file the
fd listing revealed (`tools/mod.rs`) and its direct output-pipeline dependencies
(`output_accumulator.rs`, `truncate.rs`, `node_worker.cjs`,
`ipython_lifecycle.rs`, `ipython_cell_code.rs`, in-tree `ipython_*_tests.rs`) — all
inside `crates/pi-coding-agent/src/core/tools/`. Integration tests read:
`tests/direct_bash_lifecycle.rs`, `tests/node_workspace.rs`,
`tests/sessionkernel_t04_process_cleanup.rs`,
`tests/configured_output_limits.rs` (rejected as unrelated: model token
limits), `tests/bootstrap_t01.rs` + `tests/long_session_soak.rs` (headers only,
to classify). No builds, no code execution, no network. Every claim cites
file:line from static reading; anything unverified is marked UNCERTAIN.

---

## 1. CAPABILITY INVENTORY

| Function/module | Entry point | Expected behaviour | Failure modes | Existing tests (file+name) | Missing tests |
|---|---|---|---|---|---|
| Rust bash tool (session-facing) | `create_bash_tool_definition` bash.rs:849; `execute_bash` bash.rs:675; `LocalBashOperations::exec` bash.rs:125 | Run command in resolved shell, stream combined stdout+stderr, throttle UI updates (100 ms), truncate to last 2000 lines/50 KB, spill full output to temp file, report exit code/abort/timeout status | Shell resolution failure (teaching error); missing cwd; non-zero exit becomes Err text; timeout/abort kill the tree; background child holds pipe past shell exit | bash.rs: `execute_bash_streams_output_and_returns_details` (1070), `execute_bash_reports_non_zero_exit_code` (1085), `execute_bash_reports_abort_and_timeout_statuses` (1098), `execute_bash_applies_command_prefix` (1120), `execute_bash_rejects_missing_working_directory` (1133), `local_operations_reject_a_missing_custom_shell_path` (1160), `local_operations_run_the_resolved_shell` (1197), `local_operations_hide_windows_console_and_preserve_stdio` (1235, win), `spawn_context_env_uses_the_shell_env_owner` (1280), throttle tests (1007, 1048) | Real-process timeout kill and RAII kill on Windows; drain-grace notice path (bash.rs:264-266); env_clear+env passthrough beyond the console probe; kill of escaped descendants |
| Rust bash executor (RPC/AgentSession path) | `execute_bash_with_operations` bash_executor.rs:52 | Same contract via pluggable `BashOperations`; sanitize (strip ANSI + binary sanitize + CR removal), bounded retained chunks (2x cap), spill, truncate | Sanitization corrupts split UTF-8 (see S-1); cancelled-after-success race (S-6) | bash_executor.rs: `streams_and_returns_the_exit_code` (226), `strips_carriage_returns` (256), `cancellation_reports_no_exit_code` (269), `errors_propagate_without_cancellation` (294), `large_output_is_truncated_and_spilled` (307) | Split-UTF-8 chunk test; spill-file content equality test; on_chunk sanitization assertions |
| Output accumulator / spill | `OutputAccumulator::append/finish/snapshot` output_accumulator.rs:187/205/217; `OutputSpill` output_accumulator.rs:37-126 | Streaming UTF-8 decode with pending-sequence carry, bounded tail (2x max bytes), spill opens at threshold with replay of raw chunks, degraded spill never advertises a path | Unwritable temp dir degrades spill (no path advertised); append after finish rejected | output_accumulator.rs: 7 tests (393-488) incl. `split_multibyte_chunk_decodes_across_appends` (448), `invalid_utf8_is_replaced` (459) | Degraded-spill path (unwritable tmpdir); tail-trim char-boundary at exactly the trim point; line-boundary partial-tail snapshot |
| Truncation utilities | `truncate_head/tail/line` truncate.rs:83/169/271 | Line/byte caps, never partial lines (tail may keep a partial first line), UTF-8-safe byte truncation | Exact-cap edge cases; oversized first line (head) | truncate.rs: 8 tests (295-396) | Byte-cap + line-cap interaction fuzzing; `truncate_head` byte budget with multibyte |
| IPython tool + kernel provisioner | `create_ipython_tool_definition` ipython.rs:1411; `execute_ipython` ipython.rs:1213; `IpythonKernelProvisioner::ensure` ipython.rs:736; `start_kernel` ipython.rs:824 | Lazy kernel start with boot permit, restore-before-bootstrap, 30 s bootstrap timeout, busy-kernel wait/kill dialog, sequential execution mode, late agent-message handler bound to tool-call id, restart notice | Bootstrap stall; startup abort vs timeout distinction; superseded/fenced startups; BusyAfterInterrupt loop | ipython.rs: ~30 tests (1588-2636); ipython_bootstrap_tests.rs: `ready_kernel_with_stuck_bootstrap_is_stopped_once_and_can_retry_fresh` (59), `cancelling_bootstrap_is_not_reported_as_a_timeout` (89); ipython_lifecycle_tests.rs: 3 tests (82, 141, 176) | BusyAfterInterrupt -> kill -> retry loop with KERNEL_RESTART_NOTICE; `model_output_artifact` persistence path; stream_update -> on_update wiring; total result-size cap (see S-5) |
| Node tool | `create_node_tool_definition` node.rs:233; `NodeRuntime::execute` node.rs:113 | Persistent inspector-eval workspace, newline JSON protocol, readiness+version handshake (>=22), 64 KB output cap, timeout/cancel/dispose resets workspace, RAII kill on dropped future | Protocol mismatch; oversized message (512 KB); silent Rust-side cap without notice (S-4) | tests/node_workspace.rs: `persistent_javascript_await_files_modules_errors_and_reset` (10), `cancellation_and_large_output_do_not_jam_the_next_cell` (89), `dropped_execution_future_discards_busy_workspace` (123) | In-module unit tests (none exist); result-text truncation notice; `fatal` frame handling; version<22 rejection; dispose/execute lock race |
| Node worker | `node_worker.cjs` (embedded, node.rs:13) | Wrap stdout/stderr writes per cell via AsyncLocalStorage, emit capped output frames + truncation notice, inspector eval with top-level await | Code-unit vs byte budget drift (S-3); output after cell done dropped | (covered indirectly via node_workspace.rs) | Direct worker protocol tests; surrogate-pair split test |
| Clang tool | `create_clang_tool` clang.rs:294; `ClangRuntime::execute` clang.rs:246; `ClangProcess::cell` clang.rs:130 | Persistent clang-repl, per-cell #include of temp file, dual-stream marker fence (`OPTIMUS_CLANG_DONE_{id}`), 64 KB per-stream cap with notice, timeout/cancel resets, 1 MB code limit | clang-repl missing (teaching error); marker split across chunks; false truncation notice at exactly 64 KiB (S-2) | NONE — no in-module tests, no integration test file in crates/pi-coding-agent/tests | Everything: marker round-trip, caps, timeout reset, error heuristic (`error:`/`Parsing failed`), RAII, dispose race |
| Tool registry/dispatch | `create_all_tool_definitions` mod.rs:294; `ToolDefinition` mod.rs:175-199 | Register built-ins (only `ipython` in the map; bash/node/clang registered by extensions elsewhere — UNCERTAIN where), convert to extension definitions, replay names `["bash","edit"]` | Default execute returns "tool has no execute handler" (mod.rs:222-229) | mod.rs: `builtin_conversion_preserves_metadata_and_live_extension_context` (313) | Dispatch/registration of bash/node/clang (out of file map) |
| Python `bash()` | `bash()` bash.py:885; `BashHandle` bash.py:217 | Spawn immediately, return live handle; one-shot await owns kill-on-cancel; head 512 KB + tail 1.5 MB buffer with drop marker; POSIX status-fd + completion-fence protocol; Windows kill-on-close job object + fail-closed journal; escaped-descendant reaping; atexit kill of live handles | Journal enrollment failure aborts spawn; shell death without status; background child holds pipe | NOT INSPECTED — Python-side test files not in file map | All of it at unit level in-map (see COVERAGE NOTES) |
| Python bash guard | `guard_command` bash_guard.py:297 | Bounded literal scan refusing destructive git (dirty-tree probe) / recursive-force rm outside cwd, with explicit bypass flags; token/command-size limits | Refusal on uncertain syntax (by design); probe timeouts refuse | NOT INSPECTED | Bypass flag matrix; probe deadline; Windows untranslatable paths |
| Python REPL protocol | `main` repl.py:1633; `_serve` repl.py:1428; `_handle_execute` repl.py:588; `_handle_state` repl.py:1254 | Newline JSON over stdio, protocol v3, persistent `__main__` namespace, top-level await, tagged stream attribution (cell id vs null for raw fd), interrupt machinery (parked/handoff/targeted), snapshot/restore as interruptible requests, host bridge, owner watchdog hard-exit | Interrupt windows (mitigated by extensive comments); SIGINT rebind by cells; payload cap 16 MB raises | NOT INSPECTED for the real module — ipython_bootstrap_tests.rs drives a FAKE rlm/repl.py fixture (fixture at ipython_bootstrap_tests.rs:11-34) | Real-protocol tests: interrupt parking, duplicate id rejection (repl.py:1501-1510), host_request shutdown failure, drain ordering |
| Script-result reporting | `report_script_result` execution.py:51 | Opt-in per-cell process outcome reports (max 32), strict receipt validation, verification designation | Raises outside an active cell; oversized receipt | NOT INSPECTED | Receipt validation matrix; report cap |
| Python snapshot store (CAS v2) | `snapshot_cas_v2` snapshot.py:1265; `restore_cas_v2` snapshot.py:1576; `migrate_legacy_to_cas` snapshot.py:1511; `open_cas_generation` snapshot.py:516 | Content-addressed blobs, hash-verify on read and after write, atomic temp+rename+fsync, two-generation pointer, bounded GC under registration lock, immutable-blob reuse with identity check, budget-aware partial saves | Corrupt pointer blocks new saves (fail-closed, S-7); GC ambiguity no-ops | NOT INSPECTED (a `test_snapshot_v2.py` is referenced at snapshot.py:588 but lives outside the file map) | In-map: none; verify external suite covers hash mismatch, GC race, migration flip |
| Snapshot restore prep | `prepare_restored_values` snapshot_restore.py:85; `_revive` snapshot_restore.py:14 | Rebind `__main__` functions to live namespace, revive closures/defaults, backfill globals, per-name failure isolation | Cycles (memoized); non-function values pass through | NOT INSPECTED | Function/closure revival round-trip |
| Snapshot serializer | `dump_snapshot_value` snapshot_serializer.rs:300 (file snapshot_serializer.py) | Native fast path for builtin subgraphs, dill fallback preserving custom reducers, file-handle rejection, FRAME-aware fragment splicing | Probe rejection storms (single-shot fallback per value) | NOT INSPECTED | Native/dill byte-parity for mixed graphs |

---

## 2. RISK-RANKED GAPS

### P0

- **P0-1. No visible total-size cap on the ipython tool result.** The tool
  assembles `text = stdout + stderr + result + traceback + background +
  reports` with no truncation anywhere in ipython.rs (assembly at
  ipython.rs:1300-1341; verified: no `truncate`/cap call in the file — only the
  launcher-guard regex matches "cap"). The Python side caps only per-frame
  stream text at 64 KB (repl.py:56, repl.py:332-334 — a split, not a cap) and
  repr/error text at 1 MB (repl.py:57, repl.py:620, repl.py:513-514). Total
  `print()` output is unbounded at every layer I could inspect. The Rust
  `repl_manager.rs` that assembles `ExecuteResult.stdout` was OUT OF FILE MAP
  (NOT INSPECTED) — if it also lacks a cap, one runaway `print` floods the
  model context. Contrast: the bash tool truncates at 2000 lines/50 KB
  (bash.rs:544-551) and spills to disk; node/clang cap at 64 KB
  (node.rs:14, clang.rs:12). This is the single most important wave-2
  verification item.

### P1

- **P1-1. Rust `LocalBashOperations` on Windows has no job-object containment.**
  Spawn uses only `CREATE_NO_WINDOW` (bash.rs:164-165); kill relies on
  `kill_process_tree` (bash.py-equivalent in utils/shell.rs — NOT INSPECTED)
  at timeout/abort (bash.rs:241-246) plus a detached-pid journal
  (bash.rs:176-178, untracked at settle bash.rs:270-272). Descendants that
  reparent or detach between spawn and kill can survive `taskkill /T`; this
  matches the recorded Windows tree-kill lesson. Node and clang tools DO use
  `spawn_kernel` with a `containment` field (node.rs:31-34, clang.rs:95-97),
  and Python `bash()` enters a kill-on-close job while the child is still
  suspended (bash.py:294-297, bash.py:311-313). Tool-bash is the only local
  process tool without containment.

- **P1-2. No Windows execution-path tests for the Rust bash tool.**
  `tests/direct_bash_lifecycle.rs` is entirely `#[cfg(unix)]` (L1-2): the
  background-child-holds-pipe case, the stdin-EOF settle case, and the
  dropped-future RAII kill are untested on the campaign's host OS. The
  Windows-specific pieces that DO have tests are the console/env probe
  (bash.rs:1235) and journal reaping (sessionkernel_t04:132) — the kill/drain
  semantics themselves are not covered on Windows.

- **P1-3. The real `rlm.repl` protocol has no in-scope tests.** The interrupt
  machinery (repl.py:365-476: `_sigint_handler`, `_request_interrupt`,
  handoff/parked interrupts, `_finish_request` windows), duplicate-id rejection
  (repl.py:1501-1510), host bridge failure (repl.py:170-177), and the
  snapshot-commit SIGINT parking (repl.py:986-1051, snapshot.py:1420-1451) are
  only exercised against a hand-written fake protocol fixture
  (ipython_bootstrap_tests.rs:11-34). Python-side test files were outside the
  file map (NOT INSPECTED — they may exist under prime-agent-runtime; a
  `test_snapshot_v2.py` is referenced at snapshot.py:588).

- **P1-4. Clang tool has zero tests anywhere in the inspected set.** No
  `#[cfg(test)]` module in clang.rs and no integration test file touches it.
  Untested: the dual-stream marker fence (clang.rs:147-192), the per-stream
  64 KB cap and its notice (clang.rs:178-181, clang.rs:210-212), the
  `error:`/`Parsing failed` error heuristic (clang.rs:220), the 1 MB code
  limit (clang.rs:259-261), timeout/cancel reset (clang.rs:275-290), and RAII
  drop (clang.rs:197-203).

### P2

- **P2-1. Sanitization divergence between the two Rust bash paths.**
  `bash_executor.rs` strips ANSI, sanitizes binary output and removes CR
  (bash_executor.rs:79-80); the tool path (`execute_bash` ->
  `OutputAccumulator`) applies none of these (verified by search: no
  sanitize/strip in bash.rs or output_accumulator.rs). The comment at
  bash_executor.rs:76-78 claims the lossy per-chunk decode has "the same
  effect" as TS's streaming TextDecoder — that is wrong for split sequences
  (see S-1), and the tool path keeps CR/ANSI bytes. Whether the tool path
  should sanitize is a TS-parity question (UNCERTAIN: TS `bash.ts` handleData
  also only appends), but the two Rust paths behave differently for identical
  commands.

- **P2-2. Python `_BoundedBuffer` can split multibyte characters at both
  caps.** The head cap slices `chunk[:take]` with no char-boundary check
  (bash.py:181-184) and the tail trim slices `oldest[excess:]` likewise
  (bash.py:197-200). Rust's `trim_tail` does check the boundary
  (output_accumulator.rs:299-302). Result: U+FFFD pairs at exactly the 512 KB
  head boundary or the 1.5 MB tail boundary for non-ASCII output.

- **P2-3. Corrupt `CURRENT.json` bricks new CAS snapshots (fail-closed).**
  `_read_pointer` raises on any validation failure (snapshot.py:348-361);
  `snapshot_cas_v2` reads it before writing (snapshot.py:1316) and the outer
  handler converts every error into a failed save (snapshot.py:1453-1456).
  There is no in-scope self-heal (no auto-rollback to `previous`, no legacy
  fallback when format=auto has a CAS root). Restore also fails
  (snapshot.py:1632-1633). Only an explicit legacy export/restore (if a legacy
  pair exists) or manual deletion recovers. Deliberate fail-closed design, but
  the recovery runbook is untested.

- **P2-4. Cancelled-after-success race in `bash_executor`.** `cancelled` is
  decided by checking the token AFTER exec resolves (bash_executor.rs:133-137):
  a token firing between process exit and the check discards a real exit code
  and reports `cancelled: true` for a command whose side effects fully landed.

- **P2-5. Rust tool-bash background descendants outlive the call unowned.**
  On normal shell exit with a background child holding the pipe, readers are
  aborted after the grace window and a notice is emitted (bash.rs:254-266),
  the journal record is deactivated (bash.rs:270-272), and nothing reaps the
  descendant later (no job object per P1-1; the pid-only journal record is
  explicitly not a reaper target — sessionkernel_t04:187-191). This mirrors
  the TS contract (comments at bash.rs:174, 268-269), so it is parity, not a
  port bug — but combined with P1-1 it is a leak channel on Windows.

### P3

- **P3-1.** clang.rs:210 — truncation notice triggers on exact equality
  (`s.len() == LIMIT`), so an output of exactly 64 KiB that was NOT truncated
  gets a false "[Clang-Repl output truncated at 64 KiB per stream]" notice.

- **P3-2.** node.rs:212-219 — `append_bounded` silently caps with no notice;
  the worker-side notice (node_worker.cjs:27) only covers `output` frames, so
  a long `result` expression truncated by the worker (node_worker.cjs:67) or by
  the Rust side is invisible to the model.

- **P3-3.** node_worker.cjs:24-25 — `text.slice(0, limit - cell.bytes)` slices
  by UTF-16 code units while `cell.bytes` counts `Buffer.byteLength`: a
  non-ASCII clip can exceed the byte budget or split a surrogate pair.

- **P3-4.** bash.rs:286 — `None => Ok(BashExecResult { exit_code: None })` is
  dead code: `result.is_none()` implies `aborted` or `timed_out`, both of which
  return earlier (bash.rs:274-279).

- **P3-5.** ipython.rs:1561-1571 — the kernel-client factory override is a
  process-global static with no panic guard; a test that panics between
  set/reset leaks the override into other tests.

- **P3-6.** repl.py:62-65 — `emit()`/`host_request()` payloads above 16 MB
  raise `ValueError` inside the user cell. Correct fail-closed, but surprising
  for a legitimate large display payload.

### P4

- **P4-1.** Python `bash()` has no timeout parameter at all (bash.py:885-913)
  while the Rust bash tool accepts one (bash.rs:27) — a documented semantic
  difference between kernel-bash and tool-bash that wave-2 tests should
  characterize, not fix.

- **P4-2.** Output retention differs: Rust tool keeps last 50 KB + spill file
  (truncate.rs:13-14); Python kernel keeps first 512 KB + last 1.5 MB with a
  drop marker (bash.py:35-36) and NO spill file. Models moving between the two
  see different truncation shapes for the same command.

---

## 3. SUSPECTED BUGS

Each item: what looks wrong, exact location, and how to verify as a test.

**S-1. Split UTF-8 sequences are corrupted in `execute_bash_with_operations`.**
- Where: bash_executor.rs:79-80 — `String::from_utf8_lossy(data)` runs per
  chunk; a multibyte character split across two pipe chunks becomes two
  U+FFFD replacements. The in-code comment (bash_executor.rs:76-78) claims
  parity with TS's streaming TextDecoder, but a streaming decoder buffers the
  partial sequence — `from_utf8_lossy` cannot. The corruption also lands in
  the spill file, because the spill is written from the sanitized text
  (bash_executor.rs:89), not raw bytes.
- Contrast: the tool path handles this correctly (`Utf8StreamDecoder`,
  output_accumulator.rs:339-386, tested at output_accumulator.rs:448-456), and
  so does Python (`codecs.getincrementaldecoder`, repl.py:200, bash.py uses
  buffer-level decode).
- Verify (unit, bash_executor.rs): feed `FakeOperations` two chunks
  `[b"h\xc3", b"\xa9llo"]`; assert `result.output == "héllo"`. Today it
  yields `h\u{FFFD}\u{FFFD}llo`. Also assert the spill file equals `héllo`.

**S-2. False truncation notice at exactly 64 KiB in clang.**
- Where: clang.rs:210 — `output.iter().any(|s| s.len() == LIMIT)` uses exact
  equality; a stdout of exactly 65536 bytes that fits without truncation gets
  the notice. (Also: the cap applies per stream but the notice does not say
  which stream.)
- Verify (integration, gated on clang-repl presence): a cell printing exactly
  65536 bytes; assert no "[Clang-Repl output truncated...]" in the result;
  then 65537 bytes; assert the notice appears.

**S-3. Node worker byte-budget drift and silent result truncation.**
- Where: node_worker.cjs:24-27 (code-unit slice vs `Buffer.byteLength`
  accounting) and node_worker.cjs:67 (`result: text.slice(0, limit)` —
  truncated result with no marker) plus node.rs:212-219 (Rust-side cap, no
  notice).
- Verify (integration, node): a cell whose final expression stringifies to
  >64 KB; assert the result carries a truncation marker. A cell printing
  70 KB of 2-byte UTF-16 chars; assert emitted `output` frames' byte total
  stays <= 64 KiB and no lone surrogate appears.

**S-4. `cancelled` misreports a completed command (bash_executor).**
- Where: bash_executor.rs:133-137 — cancellation is sampled after the
  operation resolves; a late token cancels a finished command's report.
- Verify (unit): FakeOperations returns Ok(Some(0)); cancel the token after
  exec resolves via a oneshot; today `cancelled=true, exit_code=None` even
  though the command ran to completion.

**S-5. UNCERTAIN (needs repl_manager.rs, out of file map): ipython stdout may
be unbounded.**
- Where: ipython.rs:1300-1341 assembles result text with no cap; repl.py caps
  only repr/traceback (repl.py:57) and splits stream frames (repl.py:56).
- Verify (integration, real kernel): a cell `print("x" * 5_000_000)`; assert
  the tool result text is bounded (document the bound). If unbounded, promote
  to P0 with the fix in ipython.rs or repl_manager.rs.

**S-6. Python `_BoundedBuffer` multibyte split at caps.**
- Where: bash.py:181-184 (head cap) and bash.py:197-200 (tail trim) slice
  bytes without a UTF-8 boundary check.
- Verify (Python unit): 512 KB + 1 byte of output where the boundary falls
  mid-`é`; assert no U+FFFD pair in `handle.output()`.

**S-7. UNCERTAIN (design choice, verify runbook): corrupt CAS pointer blocks
all saves.**
- Where: snapshot.py:348-361 + snapshot.py:1316 + snapshot.py:1453-1456.
- Verify (Python unit): corrupt `CURRENT.json` hash; assert snapshot returns
  an error AND that an explicit legacy export/restore (snapshot.py:1144-1251)
  can still recover a prior legacy pair; otherwise document the manual
  recovery steps.

---

## 4. TEST PLAN (wave 2)

Tier: u=unit (in-module), i=integration (tests/), e2e (real binary/kernel).

| # | Name | Target | Asserts | Tier |
|---|---|---|---|---|
| T1 | `split_utf8_chunks_reassemble_in_executor` | bash_executor.rs | Two-chunk `h\xc3`/`\xa9llo` produces `héllo` in output AND spill file (currently fails: S-1) | u |
| T2 | `windows_timeout_kills_process_tree` | bash.rs LocalBashOperations | On Windows: shell spawns a child (powershell Start-Process), timeout fires, both shell and child pids are gone within 10 s (probe via GetExitCodeProcess like sessionkernel_t04:71-87) | i |
| T3 | `windows_background_child_notice_and_settle` | bash.rs | Port of direct_bash_lifecycle.rs:11 to Windows: `sleep 30 & echo hi` returns exit 0 quickly, output contains `hi`, and the notice text of bash.rs:264-266 appears when the child holds the pipe | i |
| T4 | `clang_marker_fence_round_trips` | clang.rs | Multi-chunk output around the marker; partial line before marker excluded; marker split across reads handled; exact-64 KiB no false notice (S-2); timeout resets workspace | i (gate: skip if no clang-repl, mirror local_operations_run_the_resolved_shell:1197 skip pattern) |
| T5 | `node_result_truncation_is_visible` | node.rs | >64 KB final expression result carries a truncation marker; total emitted output <= 64 KiB for non-ASCII (S-3) | i |
| T6 | `ipython_result_size_is_bounded` | ipython.rs + real repl.py | Cell prints 2 MB; tool result text is capped (or the absence of a cap is recorded and fixed); details still carry full stdout reference if spilled | i |
| T7 | `busy_kernel_kill_restart_prepends_notice` | ipython.rs | KernelClient returning BusyAfterInterrupt once, then Ok; ctx selects kill; assert `kernel_restarted=true` and KERNEL_RESTART_NOTICE prefix in text (ipython.rs:1327-1333) — currently no test drives this loop | u |
| T8 | `duplicate_request_id_rejected` | repl.py | Send two executes with the same id before done; second gets ProtocolError (repl.py:1501-1510) | i (Python) |
| T9 | `interrupt_parking_and_handoff` | repl.py | Interrupt arriving (a) before activation, (b) during finishing, (c) after finish — each lands on the right request or is dropped, never on the next one (repl.py:365-476) | i (Python) |
| T10 | `snapshot_commit_survives_sigint` | repl.py/snapshot.py | SIGINT during pointer publish: committed result reported as ok (repl.py:1356-1363, snapshot.py:1420-1451); SIGINT during restore apply: all-or-nothing namespace | i (Python) |
| T11 | `corrupt_pointer_fails_closed_with_recovery` | snapshot.py | Corrupt CURRENT.json -> save error (S-7); legacy export path still works when a legacy pair exists | i (Python) |
| T12 | `bounded_buffer_multibyte_boundary` | bash.py | Head/tail cap splits on char boundary (S-6) | u (Python) |
| T13 | `owned_await_cancel_kills_escaped_descendant` | bash.py | POSIX: setsid child killed on cancel of one-shot await within _CANCEL_KILL_WAIT (bash.py:745-775) | i (Python) |
| T14 | `background_output_attribution` | ipython.rs + repl.py | Raw fd write (subprocess) during a cell -> `backgroundOutput` in details, not stdout (repl.py:263-276 vs 326-335; ipython.rs:1318-1326) | i |
| T15 | `executor_cancelled_race` | bash_executor.rs | S-4: late token after Ok does not flip a completed result to cancelled | u |
| T16 | `tool_bash_output_characterization` | bash.rs | Document current passthrough: CRLF and ANSI bytes survive into tool text (P2-1); decide TS parity before changing | i |
| T17 | `node_dispose_execute_lock_race` | node.rs | dispose() concurrent with execute() waiting on the process lock: no deadlock, workspace killed exactly once (node.rs:106-111 vs 127-134) | u |
| T18 | `factory_override_isolation` | ipython.rs | Override set + panic -> subsequent tests see the default factory (P3-5); guard with a drop handle | u |

---

## 5. COVERAGE NOTES (what I could NOT inspect, and why)

NOT EXECUTED (hard constraints): `cargo build/check/test` (parent serializes
builds); any code execution against the repo; network. All analysis is static
file reading.

Out of file map — cited but not audited (UNCERTAIN wherever their behavior
matters):
- `crates/pi-coding-agent/src/utils/shell.rs` — `get_shell_config`,
  `get_shell_env`, `kill_process_tree` (taskkill /T hardening is tested at
  sessionkernel_t04:247, but the implementation was not read),
  `sanitize_binary_output`.
- `crates/pi-coding-agent/src/utils/child_process.rs` — `spawn_kernel`,
  `KernelProcess`/containment (Windows job object semantics used by node/clang),
  `EXIT_STDIO_GRACE_MS` value (bash.rs:255).
- `crates/pi-coding-agent/src/core/kernel/repl_manager.rs` — assembles
  `ExecuteResult.stdout` from stream events; decisive for S-5/P0-1.
- `crates/pi-coding-agent/src/core/kernel/shared.rs`, `state_snapshot.rs`,
  `boot_gate.rs`, `repl.py` protocol peer — the Rust halves of the kernel
  protocol.
- `crates/pi-coding-agent/src/core/model_tool_output_policy.rs` —
  `MODEL_TOOL_OUTPUT_MIN_BYTES`, artifact persistence.
- `prime-agent-runtime/src/rlm/_winjob.py`, `snapshot_safety.py`, `mcp.py` —
  job containment, safe unpickling, MCP shutdown.
- Python test suite (e.g. `test_snapshot_v2.py` referenced at snapshot.py:588,
  any bash/repl protocol tests) — outside the file map; existing Python
  coverage is unknown, so "NOT INSPECTED" above means "not verified in this
  wave", not "does not exist".
- Where bash/node/clang tools get registered into a session (mod.rs:294-304
  registers only ipython; the others are registered by extensions/modes I was
  not given).

Read beyond the strict 12-file list, justified by the granted fd listing of
`tools/` and direct data/control-flow dependency for the assigned focus
areas: `mod.rs` (registry — explicitly allowed), `output_accumulator.rs`,
`truncate.rs`, `node_worker.cjs`, `ipython_lifecycle.rs`,
`ipython_cell_code.rs`, `ipython_bootstrap_tests.rs`,
`ipython_lifecycle_tests.rs`. No other files were opened.

Line numbers refer to the worktree at
`C:/Users/openclawuser/optimus-fh-20261017` (branch
improve/full-harness-validation, base c227b0ae3).
