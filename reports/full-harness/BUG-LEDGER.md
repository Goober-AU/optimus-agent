# Bug Ledger — Full-Harness Campaign

Status legend: FIXED (in this PR, gated), PINNED (test added, behavior intentional/known),
DOCUMENTED (evidence recorded, not fixed — see notes), FALSE-POSITIVE (audit claim disproven).

| ID | Severity | Area | Finding | Status | Evidence |
|----|----------|------|---------|--------|----------|
| SB-1 | P1 | agent loop | Sequential-batch cancellation leaves dangling tool calls | FIXED | agent_loop_cancellation_contract.rs 3/3; soak |
| SB-3 | P2 | agent loop | Before-hook abort recorded as raw error text | FIXED | in-file test |
| SB-2 | P2 | agent loop | ExecutionScope monotonic growth | FIXED | prune_settled tests; entries bounded 16+->2 |
| SB-4 | P3 | agent loop | Custom-tail continue sends empty context | FIXED | agent.rs tests |
| SB-5 | P3 | agent loop | Stall message drops streamed partial content | DOCUMENTED | stream_watchdog owner comment; retries-off history loses partial |
| S-1 | P1 | bash executor | Split-UTF-8 corruption incl. spill file | FIXED | executor tests 10/10 |
| S-4 | P1 | bash executor | Late token flips completed command to cancelled | FIXED | executor tests |
| P1-1/2 | P1 | bash tool | No Windows job containment (tree survives kill) | FIXED | containment tests 8/8 incl detached-child tree-kill |
| S-5 | P0-cand | ipython | Error event text unbounded (stdout/stderr were capped) | FIXED | ipython_output_bounds 1/1 (real kernel) |
| K-1 | P0-class | kernel | Frames dropped at budget never flag truncation (~1.9MB silent loss) | FIXED | repl_manager unit tests |
| K-2 | P1 | bash.py | _BoundedBuffer splits multibyte chars at caps | FIXED | test_bash.py 68 OK |
| K-3 | P1 | bash.py | Single reap-callback slot: concurrent awaiter hangs notifier | FIXED | test_bash.py |
| S-2 | P3 | clang | False truncation notice at exactly 64 KiB | FIXED | in-file tests |
| S-3 | P2 | node | Byte-budget drift, lone surrogates, silent result truncation | FIXED | node_clang 12/12; worker protocol tests |
| T17 | P2 | node | dispose/execute lock race | FIXED | race test |
| E-1 | P2 | edit | from_utf8_lossy writes U+FFFD back into files | FIXED | edit_tool_behavior 18/18 |
| M-2 | P2 | acp_mcp | Silent arg coercion (empty tool name) | FIXED | validation matrix tests |
| T-1 | P3 | truncate | CRLF blank-line byte accounting + terminator loss | FIXED | CRLF tests |
| SB-02 | P2 | memory jobs | Import apply permanently poisoned after failed apply | FIXED | memory_source_integrity |
| SB-03 | P3 | memory store | Corrupt host-id bricks MemoryStore::new | FIXED | quarantine+re-mint test |
| SB-08 | P3 | memory store | Invalid settings silently ignored | FIXED | logging tests |
| SB-04 | P3 | memory search | sourceSpan silent 200-char cap | FIXED | marked-truncation test |
| SB-01 | P2 | harness.py | Malformed entry silently deleted on next save | FIXED | quarantine sidecar tests (6) |
| SB-07/G-01 | P1 | controller | New memory settings rejected by official controller | FIXED | 43/43 pytest; 36/36 driver keys |
| personaSupersession | P2 | driver+controller | Key not in driver ManifestSettings | DOCUMENTED | cannot whitelist a no-op key |
| BUG-1 | P2 | OCC | Name-addressed sends never negotiate OCC-2 | FIXED | policy test + occ2 e2e 2/2 |
| BUG-2 | P1 | supervisor | agentOrigin-gated reach fence (direct-socket trust) | PINNED | two-sided safety test (TS parity cba2a919a) |
| BUG-3 | P3 | subagent runs | Duplicate on_session_published | FIXED | ptr_eq dedupe tests |
| BUG-4 | P2 | subagent runs | Terminal-notice failure only eprintln | FIXED | 3-attempt bounded retry tests |
| PR166-gap | P0-test | OCC | No true e2e negotiation test existed | FIXED | occ2_e2e_negotiation.rs (flagship) |
| S5 | P1 | supervisor | get_state roster fallback zeroes sessionActions | FIXED | stale:true additive fields + saturate test |
| S1 | P1 | session mgr | Torn head destructively resets, no quarantine | FIXED | torn-head recovery 4/4 |
| S3 | P2 | session lease | Dead compromise-detection flag | FIXED | mtime/guard check |
| S2 | P2 | session mgr | write_file_atomic_sync ignores fsync | FIXED | honored at boundary; documented |
| S4 | P2 | session mgr | fork_from ignores writeln errors | FIXED | checked writes |
| QV-1 | P1 | session | queue_visible default false drops queued work on abort (TS=true) | FIXED | soak abort test |
| W6-P0-1 | P0-claim | session | /goal wedge | FALSE-POSITIVE | fixed upstream in e666afe9a before base; pinned tests were green |
| W6-P0-3 | P0-claim | slash | Stale 42-command guard | FIXED | regenerated matrix 5/5 |
| GOLDEN | P1-test | pi-ai | Golden loop contradicts its own Start fixture | FIXED | test fix, deterministic |
| REPL-1 | P3-test | repl | Startup 500ms budget flakes under load | FIXED | 4000ms + reap barrier; 32/32 files |
| W8-P0 | P0-test | infra | question-loop pytest suite never executed by any gate | FIXED | runner inventory + suite now run (43/43) |

## Not fixed (documented, with reasons)

- SB-5: partial-content retention on stall — semantics debate (provider-replay
  guard) beyond safe offline change; evidence in drafts/W1.
- SB-06: jobs.rs run() discards failed-status write errors (let _ =) — needs
  jobs.rs owner decision on error surface.
- daemon_mode.rs occ2_send_policy id-only peer find — BUG-1 twin at the
  daemon-bridged seam; one-line extension, routed but not landed (owner
  constraint); flagged in drafts/W3.
- harness.py answerInstructionPlacement staleness — legacy key, driver
  ignores; removing risks breaking existing campaign files.
- native_host.rs dispatch-classification drift (jev/occ/stats/monitor missing
  from in-file lists) — registry-side guards added instead; file outside
  wave-2 ownership.
- Cross-platform (Linux/macOS): NOT EXECUTED (Windows-only host).
- Memory quality benchmarks: NOT EXECUTED (need authorised datasets/models);
  controller fixed so they can be driven.
