# Feature Coverage Matrix - Full-Harness Round 2

Status values: PASS, FAIL, NOT EXECUTED, BLOCKED. Every PASS below names the run that proves it;
logs are in `artifacts/logs/`. Round 2 starts from the merged round-1 state (PR #172, v0.1.35)
plus PR #174.

| Subsystem | Entry points audited | Tier | Evidence | Status |
|---|---|---|---|---|
| Core agent loop | `agent_loop.rs` run_loop, execute_tool_calls(_sequential/_parallel), answer_unexecuted_tool_calls | unit + end-to-end | gate A 103 passed; `full_harness_scenarios` A + B 4/4 | PASS |
| Tool dispatch / abort cleanup | `execution_scope.rs`, tool abort cleanup | unit | gate B2 3747 passed, 0 failed | PASS |
| Providers / streaming | `pi-ai` providers, `stream.rs`, PR #174 metrics | unit + fixture | gate H 899 passed, 0 failed; gate T throughput 1/1 | PASS |
| Bash (Rust) | `bash_executor.rs`, `tools/bash.rs` | unit + real shell | `bash_executor_unicode_and_cancel` 8/8; 16 new real-path unit tests | PASS |
| Bash (Python) | `rlm/bash.py`, `bash_guard.py`, `_winjob.py` | runtime suite | gate C 128 tests OK, 37 skipped | PASS |
| IPython / kernel | `core/kernel/*`, `tools/ipython.rs` | integration, real kernel | `ipython_output_bounds` 2/2 (snapshot isolation, latency bound) | PASS |
| Node | `tools/node.rs`, `node_worker.cjs` | integration, real node | `node_workspace` 7/7 in the final run | PASS |
| Clang | `tools/clang.rs` | - | clang-repl not installed on this host | BLOCKED |
| File tools / edit / MCP | `tools/edit.rs`, `file_mutation_queue.rs`, `acp_mcp.rs` | unit | gate B2 (whole-crate lib) | PASS |
| Memory storage | `memory/store.rs`, service, jobs | unit + integration | gate F: `memory_source_integrity` 3/3; new store tests | PASS |
| Memory retrieval | `memory/search.rs`, `jev_retrieval.rs` | unit | gate E2: `pi-jev` 97 passed, 1 pre-existing flake (fails in pristine baseline too) | PASS with note |
| Memory recall e2e | `scenario_memory_e2e.rs` | end-to-end | gate F: 16/16 (project isolation, search freshness) | PASS |
| Refinement / extraction | `refinement/*`, `memory/extraction.rs` | unit + scripted provider | gate B2 | PASS |
| Persona | `memory/persona.rs` | unit | gate B2 | PASS |
| Compaction | `core/compaction/*` | unit + integration | `compaction_recovery` 9/9 | PASS |
| Session persistence | `session_manager.rs`, action store, lease | unit + integration | `scenario_stream_persist` K/L 3/3; gate B2 | PASS |
| Daemon / IPC | `modes/daemon/*`, `agent_connection/*` | unit + integration | gate B2; tracked `occ2_e2e_negotiation` 2/2 | PASS |
| OCC-2 peer selection | `daemon_mode.rs` `occ2_send_policy` | unit | OCC-R2-1 fix + selector test in gate B2 | PASS |
| OCC-1 messaging | `agent_messages.rs`, `occ/occ1.rs` | unit | gate B2 | PASS |
| Native subagents | `rlm_runtime.rs`, `subagent_runs.rs` | unit | gate B2 | PASS |
| Native subagents (scenario) | `scenario_mult_agent.rs` C/D/I/J | end-to-end | compiles, 0/4: children end `protocol_exhausted_after_3_continuations` because the scripted fixture serves one script | NOT SHIPPED |
| Settings / slash commands | `slash_commands.rs` | unit + matrix | not run this round (round 1 covers it); gate B2 covers the unit layer | NOT EXECUTED |
| CLI / headless | `print_mode.rs`, `native_cli.rs` | integration | not run this round | NOT EXECUTED |
| Python runtime suite | `prime-agent-runtime` | runtime | 603 tests: 19 failures, 91 errors, 52 skipped - identical in the pristine baseline; individual classes pass | FAIL (pre-existing) |
| Cross-platform | Windows host | - | Linux and macOS not executed | NOT EXECUTED |
