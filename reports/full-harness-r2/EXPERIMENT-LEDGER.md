# Experiment Ledger - Full-Harness Round 2

One row per probing experiment, including the ones that failed and the ones that produced no
product defect. "Repro" is the command a reviewer can paste; every command ran through the
single build queue and its log is under `artifacts/logs/`.

| # | Hypothesis | Method | Result | Outcome |
|---|---|---|---|---|
| X1 | A terminal provider error leaves tool calls unanswered in the live session | Drive `AgentSession` with a faux provider that emits a complete tool call and then a transport error; inspect stream, `TurnEnd`, disk JSONL, replay | Confirmed: stream had no tool result, `turn_end.tool_results` empty, disk row absent, replay refused the context | Fixed (CORE-R2-1), test `full_harness_scenarios` |
| X2 | Cancel before a tool starts records the wrong reason | Cancel immediately after the tool-call frame | Confirmed: recorded the provider request abort text | Fixed (CORE-R2-2) |
| X3 | `memory restore` cannot recover an authoritative store that is damaged | Truncate / non-UTF-8 / fabricated-receipt store, then restore from backup | Confirmed: restore failed or trusted the damaged revision | Fixed (MEM-R2-1..3), tests in `store.rs` + `memory_source_integrity.rs` |
| X4 | OCC-2 negotiation ignores a name-addressed peer | Daemon negotiation with `receiver` by name, by id, by session | Confirmed by name only | Fixed (OCC-R2-1) |
| X5 | A large bash timeout panics the tool | `timeout` far above `u64::MAX` seconds | Confirmed panic in `Duration::from_secs` | Fixed (BASH-R2-1) |
| X6 | A deferred throw in a Node cell kills the workspace | `setTimeout(() => { throw ... })` then a second cell | Confirmed: pipe closed, all bindings lost, message blamed an oversized response | Fixed (NODE-R2-1/2), `node_workspace` 7/7 |
| X7 | Kernel snapshot/restore is isolated between two clients | Two clients, interleaved execute/snapshot/restore | Confirmed isolated; no defect. Latency bound recorded | Test `ipython_output_bounds` |
| X8 | Project memory isolation holds between two projects under one root | Two projects, same root, interleaved writes and searches | Confirmed isolated | Test `scenario_memory_e2e` |
| X9 | An interrupted stream leaves a durable partial transcript | Kill the stream mid-answer, reopen the session | Confirmed: transcript tail intact, no temp file, epoch advanced | Test `scenario_stream_persist` |
| X10 | A failed write leaves the previous store bytes untouched | Inject a write failure, read back | Confirmed unchanged, no `.tmp` leftover | Test `scenario_stream_persist` |
| X11 | The 16 pristine failures are product defects | Isolation re-run with `KERNEL_CONTAINMENT_TEST_PYTHON` | Refuted: all 16 pass once the fixture interpreter is named; without it they panic `NotPresent` | Documented harness gap (recommendation R1) |
| X12 | The narrow-terminal throughput assertion is a product regression | Read the renderer, compute visible widths at 40 and 41 | Refuted: the renderer is correct; PR #174 widened the row by 3 columns and the test kept the old threshold | Test fixed |
| X13 | `scenario_mult_agent` failures were product defects in result delivery | Forensic read of the run log and the surviving temp dirs | Refuted: four fixture defects (child naming read the system prompt, string-continuation ate Python indentation, `noSkills` starved the kernel of `agent_message`, single-shot transcript reads) | Fixture fixed |
| X14 | Running the Python runtime suite with a bare interpreter is valid | `python -m unittest discover` from `prime-agent-runtime/test` with `PYTHONPATH=src` | Refuted: every kernel fixture dies with "runtime closed its protocol stream"; the suite needs the project environment | Recommendation R2 |

## Negative results worth keeping

- A wider lexical window does not fix a lexical stem mismatch; that class of miss needs
  stemming (carried over from round 1).
- Throttling does not change pass/fail for any non-timing test in this campaign: the same
  tests passed at 4 pinned CPUs and with the full host.
