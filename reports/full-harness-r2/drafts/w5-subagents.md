# W5 - Native Subagents, Agent Messaging, OCC-1/OCC-2, Daemon and IPC

Area owner: w5-subagents (round 2). Target dir: C:/Users/openclawuser/optimus-fh2-target-w5.
Evidence is appended as runs complete; every claim below is backed by a command and its result.

## 1. Work items

| # | Item | Where | Status |
|---|------|-------|--------|
| S1 | Scenario C: multi-subagent coding | tests/scenario_mult_agent.rs | see section 4 |
| S2 | Scenario D: children exchange OCC messages, parent receives results | tests/scenario_mult_agent.rs | see section 4 |
| S3 | Scenario I: one child fails, partial results stay honest | tests/scenario_mult_agent.rs | see section 4 |
| S4 | Scenario J: saturated worker, second client reopen, stale roster flag | tests/scenario_mult_agent.rs | see section 4 |
| S5 | daemon_mode.rs OCC-2 peer lookup accepted ids only (round-1 leftover) | modes/daemon/daemon_mode.rs | fixed + unit test |
| S6 | Round-1 fixes re-verified (occ2 e2e, name-addressed negotiation, bounded notice retries) | tests/ | see section 5 |

## 2. Round-1 leftovers in this area (from reports/full-harness/BUG-LEDGER.md)

- `daemon_mode.rs occ2_send_policy id-only peer find` - BUG-1 twin at the daemon-bridged
  seam. Addressed in S5.
- `SB-06 jobs.rs run() discards failed-status write errors` - `crates/pi-coding-agent/src/core/memory/jobs.rs`
  is OUTSIDE this area's file map (w4-memory owns core/memory/). Reported, not edited.
- `SB-5 stall partial-content drop` - the producer is `core/agent_session/subagent_runs.rs`
  and the agent-loop stream watchdog, both outside this map. Reported, not edited.

## 3. Static findings

### S5 - OCC-2 negotiation was skipped for name-addressed receivers (daemon-bridged seam)

- File: `crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs` (`DaemonAgentMessageController::occ2_send_policy`).
- Before: `listed.agents.iter().find(|agent| agent.active_session_id == target || agent.session_id == target)`.
- The agent-message surface resolves a receiver by session name, active session id or
  persistent session id (`core/agent_messages.rs::handle_agent_message_send` matches the
  family roster entry against all three). The in-process seam
  (`core/agent_session/subagent_runs.rs::occ2_target_send_policy`) matches all three since
  round 1. The daemon-bridged seam matched two, so a selector that the send path resolves
  would not negotiate: the send silently stayed OCC-1.
- Fix: one shared helper `occ2_peer_entry(&agents, target)` that matches all three selector
  forms, with the reason written down at the definition.
- Test: `mod occ2_peer_selection_tests::peer_lookup_accepts_the_selectors_the_send_surface_resolves`.

## 4. Scenario evidence (section 35)

File: `crates/pi-coding-agent/tests/scenario_mult_agent.rs` (new, 1578 lines).
Every scenario drives the real `optimus-rust` daemon (`--mode daemon --offline`) over its
own socket, a private temp state dir and a loopback HTTP model fixture. There is no mock
session, no mock supervisor and no network egress: the fixture provider answers each
completion, and each assertion reads the daemon-written session transcript, the collect
receipt, the delivered agent message or the roster.

State of this evidence: the file is **written and statically reviewed against the source,
not yet compiled or run by this worker** (hard rule: the parent owns the single build
queue). `rustfmt --edition 2021` parses and formats it (exit 0), so it is syntactically
valid Rust; type checking and behaviour are unverified until the jobs in section 6 run.
Nothing below is claimed as PASS.

### 4.1 The previous draft could not compile and proved the wrong things

The interrupted worker's file (preserved at `reports/full-harness-r2/drafts/_w5-scenario_mult_agent.previous.rs.txt`,
46223 bytes) was never compiled. Defects found by reading, all fixed in the rewrite:

- D1 (does not compile): scenario D bound `delivered`/`text` inside `runtime.block_on` and
  used them after it returned.
- D2 (scenario unreachable): the draft scenario D spawned children with the `subagent`
  tool from the default IPython session, where `ExecutionMode::tools()` is `["ipython"]`
  only, so the `subagent` tool does not exist in that session. A child cannot be spawned
  there, so the OCC send seam was never exercised. The rewrite uses the native RLM surface:
  an IPython-mode parent calls the kernel `rlm(...)`/`rlm.collect(...)`.
- D3 (wrong contract bound): the draft passed `timeout_ms: 120000`; the tool schema caps it
  at 60000.
- D4 (dead responder): the model fixture's responder logic never produced the tool call it
  then asserted on, so the scenario would have hung or failed on an unrelated assertion.
- D5 (brittle assertion): the failed child was asserted to still be on the roster. An
  errored child may be disposed; the rewrite asserts the durable session-dir transcript and
  only checks the roster row *if the row is present*.
- D6 (fixture hygiene): the daemon was created without `noSkills`/`noExtensions`, so the
  fixture would load the host's skills/extensions instead of being offline and private.

### 4.2 Scenario C - multi-subagent coding (`scenario_c_multi_subagent_coding_collects_and_validates`, :680)

- Fixture: three source modules in the private workspace; only `module_beta.txt` carries
  `BUG_42`. Parent is switched to Direct mode (`/mode direct`, the mode whose tool list
  contains `subagent`), then prompts once.
- Parent turn: `subagent` spawn alpha, spawn beta, spawn gamma, `collect` (timeout 60000),
  then one `bash` validation call. Children are independent jobs: each child's own bash
  reads **only** its module and writes `{name}-seen.txt` and `{name}-report.txt`.
- Proves: three receipts are `settled` and `done`, and each receipt carries its own child's
  answer preview (no sibling attribution); the parent's own grep output names
  `beta-report.txt:2:BUG_42` and contains no `alpha-report.txt:`/`gamma-report.txt:` hit
  line; no parent tool call failed; after the daemon is torn down, the on-disk
  `alpha-seen.txt`/`beta-seen.txt`/`gamma-seen.txt` prove each child saw only its own
  module and that `gamma-seen.txt` never contains `BUG_42`.

### 4.3 Scenario I - one child fails, partial results stay honest (`scenario_i_subagent_failure_keeps_partial_results_honest`, :909)

- Three children each do one real bash tool call (`printf <name>_PARTIAL > <name>-partial.txt`).
  `gamma` then reports `GAMMA_PARTIAL_REPORT: ...` plus `RLM_CHILD_STATUS: failed`; alpha and
  beta report `<NAME>_COMPLETE` plus `RLM_CHILD_STATUS: complete`.
- Proves, in four independent places, that the failure is reported and not erased:
  1. Collect receipts: alpha and beta are `settled`, `status == "done"` with their own answer
     previews and `error == null`; gamma is `settled` but `status == "error"` with a string
     error, and its preview carries `GAMMA_PARTIAL_REPORT` while never containing
     `RLM_CHILD_STATUS: complete`.
  2. The failed child's own durable transcript (found through its `session_dir`, not through
     a live roster row) keeps `GAMMA_PARTIAL_REPORT`, `RLM_CHILD_STATUS: failed` and the
     output of its own tool call, so partial work survives the failure.
  3. If a roster row for gamma still exists it must not read `activity == "working"`,
     `isStreaming == true` or `hasRunningRlmChildren == true`.
  4. The parent's own transcript receives `RLM child gamma` ... `failed` (the durable
     deferred notice; the test sends one plain prompt first when the notice has not landed,
     because the notice is admitted with `wake: false`) and its final report line.
  - Finally, all three `{name}-partial.txt` files exist with `{name}_PARTIAL`, so the two
    successful children's work is durable too.

### 4.4 Scenario D - children exchange a structured OCC message (`scenario_d_children_exchange_occ_messages_and_parent_receives_results`, :1200)

- Reachability gate: `PRIME_AGENT_KERNEL_PYTHON` must point at an existing python with the
  current `prime-agent-runtime` and the `agent_message` skill (:1339 `kernel_python`).
  Without it the test prints
  `BLOCKED scenario D: PRIME_AGENT_KERNEL_PYTHON is not set ...` and returns: it is marked
  BLOCKED, never faked. The variable is handed to the daemon child process explicitly.
- Parent: IPython mode (the native RLM surface). It spawns `listener` then `sender` through
  the kernel `rlm(...)` and collects both with `rlm.collect(timeout_ms=55000)`; the cell
  asserts both receipts settled and `done` before printing `COLLECT_ALL_DONE`.
- Sender child: one real `ipython` call running
  `await agent_message.send(BODY, receiver_role='sibling', receiver_name='listener')`,
  retried while the sibling is not yet published. `BODY` is a real OCC-1 contract message
  (`AgentCommMessage::new("RESULT", ...)` plus a `Finding`) whose statement is
  `"FINDING_OCC_CHILD: ...".repeat(10)` - over 300 characters, which clears the 220-character
  `SMALL_MESSAGE_CHAR_FLOOR` that would otherwise keep a small message OCC-1 by design.
- Listener child: acks only when its own visible text contains `FINDING_OCC_CHILD`.
- Proves, from the listener's daemon-written session file and the fixture's request log:
  1. the delivered `agent_message` body starts with `OCC/2 D=sym-v1@1 C=` - the
     daemon-bridged, name-addressed send seam negotiated OCC-2 (the S5 defect would have
     silently left it OCC-1);
  2. the dual body embeds `#FALLBACK-OCC1` and the embedded OCC-1 section is byte-identical
     to the body the sender produced, so nothing was reworded in transit;
  3. the receiver recorded `occ2Delivery.kind == "symbolic"`, i.e. it decoded the symbolic
     section rather than falling back;
  4. a real depth >= 1 model request contains both `OCC/2 D=sym-v1@1 C=` and
     `FINDING_OCC_CHILD`: the symbolic payload reached a child model and was consumed with
     its intended meaning, so "sent" is not confused with "consumed";
  5. the listener acknowledged, never printed `LISTENER_NO_PAYLOAD`, and the parent's own
     transcript records both `('sender', 'done', True)` and `('listener', 'done', True)`
     plus `SCENARIO_D_PARENT_COMPLETE` (both results reach the correct parent).
- Environment check (read-only, run directly with the candidate python, no build):
  `runtime_ready_check()` - the exact string `core/kernel/bootstrap.rs` runs to accept an
  override python - exits 0 for
  `C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidate/python311/Scripts/python.exe`,
  and that python imports every `DEFAULT_RLM_EXTRA_PACKAGES` module (requests, httpx, yaml,
  tomli, dotenv, pandas, numpy, scipy, bs4, lxml, pydantic). Its `rlm.collect` returns
  results with `session_name`/`status`/`settled`, and `_repl.PROTOCOL_VERSION == 3` matches
  `REPL_PROTOCOL_VERSION`. So the gate is satisfiable on this host with the env var set; it
  fails closed without it.

### 4.5 Scenario J - saturated worker, second client reopen, stale roster (`scenario_j_saturated_worker_reopen_stays_responsive_and_stale_is_marked`, :1409)

- Parent is a Direct-mode chat that spawns five children (`w1`..`w4` and `memo`); every child
  runs one bash call that `sleep 8`s before writing its done file, and `memo` also writes
  `../agent/memory/scenario-j.json`. The worker's own command loop is serial per connection,
  so a second client's reads cannot be answered by the busy worker while the turn runs.
- Client 2, concurrently (same current-thread runtime, `tokio::join!` with the busy turn):
  1. polls `roster_subscribe` until five `runtimeKind == "subagent"` rows are published,
     which proves the five children really exist while the worker is busy;
  2. `get_state` under saturation must answer within 25 s, and when it takes >= 4500 ms it
     must be marked `stale: true` with `fallback: "roster"` - a fallback that pretends to be
     authoritative fails the test; a fast answer must not be marked stale;
  3. `follow_up` must report its admission (`queued` is a boolean) and is answered later, not
     dropped;
  4. `create` with `sessionPath` reopens the same chat from the second client and must bind
     the **same** session file; `get_messages` stays readable under saturation and contains
     the original prompt.
- After the turn drains: `get_state` is authoritative again (not stale); the parent's
  transcript holds five collect entries that are all `settled`/`done`, two
  `SCENARIO_J_PARENT_COMPLETE` lines (the original turn and the admitted follow-up, which also
  appears as `SATURATION_FOLLOW_UP`), and the four `{name}-done` files plus the memory
  artifact exist on disk.

### 4.6 Fixture invariants used by every scenario

- The daemon child is launched with `env_clear()` plus a private `HOME`, `USERPROFILE`,
  `TMP`/`TEMP`/`TMPDIR`, `XDG_*`, `PRIME_AGENT_CODING_AGENT_DIR`, `PRIME_AGENT_SESSION_DIR`,
  `PI_OFFLINE=1`, `NO_PROXY=127.0.0.1,localhost`, `TERM=dumb`, the parent `PATH`/`SystemRoot`
  and, only for scenario D, `PRIME_AGENT_KERNEL_PYTHON` (:234-267).
- Model fixture: `/v1/chat/completions`, `model == "fixture-model"`, `stream == true`, one
  content delta per turn, SSE framed, loopback only, 1 MiB body cap, 5 s socket timeouts
  (:107-170).
- Daemon lifecycle: `wait_for_daemon` requires the real handshake and
  `daemon_protocol_info()` equality (:345); `OwnedDaemon` asks the daemon to shut down and
  then kills and reaps the child, so a scenario never leaves an orphan (:312-343).

## 5. Round-1 re-verification

Pending runs (jobs 3 and 4 below). What those jobs cover, and which scenario backs it:

| Round-1 fix | Re-verified by |
|---|---|
| OCC-R2-1 name-addressed OCC-2 negotiation (daemon-bridged seam) | job 3 (`occ2` lib unit test incl. `occ2_peer_entry`) and job 4 (scenario D assertion 1) |
| Bounded terminal-notice retries | job 1 (scenario I: the failure notice reaches the parent exactly as `RLM child gamma ... failed`) |
| Idempotent child publication | job 1 (scenario C/I/J: one collect entry per child, no duplicate or swapped receipt) |
| Stale roster marking under saturation (PR #168) | job 1 (scenario J: >= 4500 ms `get_state` must be `stale: true` + `fallback: "roster"`) |
| In-process OCC-2 seam parity | unchanged by this worker; covered by the existing `occ_comm_fidelity` target |

## 6. Commands and results

All jobs run through `C:/Users/openclawuser/fh2-build-queue/fh2q.py` with owner
`w5-subagents`, the campaign prelude, target dir `C:/Users/openclawuser/optimus-fh2-target-w5`
and `-j 2 --test-threads=1`. Use the absolute Git `timeout`; a bare `timeout` resolves to
`C:\Windows\System32\TIMEOUT.EXE` and the job dies immediately.

### Job 1 - compile only (fastest signal; the file has never been built)

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w5-subagents \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w5" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/drafts/_w5-compile.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 2400 \
  cargo test --locked -p pi-coding-agent --test scenario_mult_agent --no-run -j 2
```

### Job 2 - scenarios C, I, J (no kernel needed)

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w5-subagents \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w5" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/drafts/_w5-scenario-c-i-j.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 2400 \
  cargo test --locked -p pi-coding-agent --test scenario_mult_agent -j 2 -- --test-threads=1 scenario_c
```

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w5-subagents \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w5" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/drafts/_w5-scenario-i.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 2400 \
  cargo test --locked -p pi-coding-agent --test scenario_mult_agent -j 2 -- --test-threads=1 scenario_i
```

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w5-subagents \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w5" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/drafts/_w5-scenario-j.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 2400 \
  cargo test --locked -p pi-coding-agent --test scenario_mult_agent -j 2 -- --test-threads=1 scenario_j
```

### Job 3 - scenario D with a real kernel (needs the python override)

`fh2q.py` has no `--env` flag, so the override goes through the absolute Git `env` binary as
the first command word. Do not set `PRIME_AGENT_KERNEL_VENV`: the override path must win.

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w5-subagents \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w5" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/drafts/_w5-scenario-d.log" \
  -- "C:/Program Files/Git/usr/bin/env" \
     "PRIME_AGENT_KERNEL_PYTHON=C:/Users/openclawuser/optimus-update-preparation-20260920-004938/candidate/python311/Scripts/python.exe" \
     "C:/Program Files/Git/usr/bin/timeout" 2400 \
     cargo test --locked -p pi-coding-agent --test scenario_mult_agent -j 2 -- --test-threads=1 scenario_d
```

Expected when the gate python is missing or rejected: the test prints
`BLOCKED scenario D: ...` and passes with no assertions executed. That is a BLOCKED result,
not a pass; check the log text, the exit code alone does not distinguish them.

### Job 4 - round-1 re-verification of the OCC/daemon seams

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w5-subagents \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w5" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/drafts/_w5-occ2-lib.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 2400 \
  cargo test --locked -p pi-coding-agent --lib -j 2 -- --test-threads=1 occ2
```

```sh
python3 C:/Users/openclawuser/fh2-build-queue/fh2q.py run \
  --owner w5-subagents \
  --cwd "C:/Users/openclawuser/optimus-fh2-20261018" \
  --target-dir "C:/Users/openclawuser/optimus-fh2-target-w5" \
  --log "C:/Users/openclawuser/optimus-fh2-20261018/reports/full-harness-r2/drafts/_w5-occ2-e2e.log" \
  -- "C:/Program Files/Git/usr/bin/timeout" 2400 \
  cargo test --locked -p pi-coding-agent --test occ2_e2e_negotiation -j 2 -- --test-threads=1
```

### Results

| Job | Result |
|---|---|
| 1 compile | not run (worker rule: parent owns the queue) |
| 2 C / I / J | not run |
| 3 D | not run |
| 4 occ2 lib / e2e | not run |

### What remains unverified, and the residual risk

1. **The file has never been compiled.** `rustfmt` proves it parses; it does not prove it
   type-checks. The helpers were written against the real signatures
   (`DaemonClient::request(body, Option<u64>, DaemonClientRequestOptions)`,
   `RlmCollectResultEntry` snake_case fields, `roster.summary.{runtimeKind,rlmChildId,
   sessionName,activeSessionId,activity,isStreaming,hasRunningRlmChildren}`,
   `message.{role,toolName,details,isError}`) but a mismatch is a compile error, not a
   behavioural finding. Job 1 is the gate.
2. **Scenario C timing.** The parent issues its `bash` validation only after `collect`
   returns, so it needs no roster polling; if `collect` returns before a child's report file
   exists the grep would miss it. `collect` waits for settlement, and the child writes the
   file before it reports, so the order is enforced by the child, not by a sleep.
3. **Scenario I notice admission.** The failure notice is a durable custom message admitted
   with `wake: false`. The test sends one plain prompt when the notice has not landed by the
   end of the turn. If the notice were never admitted at all, the scenario fails (correctly);
   if the prompt itself is the only reason it appears, the transcript assertion still proves
   durability of the notice text, not the wake policy.
4. **Scenario D is BLOCKED without the override python.** With job 3's env it is reachable:
   the exact bootstrap acceptance check exits 0 on that interpreter. Still unverified:
   whether this repo's kernel host handshake and the installed `prime-agent-runtime`
   (candidate 0.1.34, protocol 3) agree on every host request used here
   (`rlm.run`, `rlm.collect`, `agent_message.send`), and whether the delivered custom message
   is rendered into the child's *next* completion request exactly as asserted.
5. **Scenario J saturation determinism.** The stale-marking assertion depends on the worker's
   command loop being serial, so that a `get_state` forwarded while a turn runs cannot be
   answered inside the 5000 ms bound. If the worker ever answers concurrently, the test takes
   the non-stale branch (latency < 4500 ms) and passes without exercising the fallback; it
   would then prove responsiveness but not staleness. The >= 4500 ms branch is the one that
   proves PR #168.
6. **Scenario J follow-up ordering.** `follow_up` and the `create(sessionPath)` reopen are
   forwarded to the busy worker, so both complete only after the turn drains
   (`request_result` uses a 240 s bound). The assertions are about admission and identity,
   not latency, so a slow drain cannot turn them into false passes.
7. **Not covered by this file** (reported, not edited): `SB-06` `jobs.rs` failed-status write
   (`core/memory/jobs.rs` is w4-memory's map) and `SB-5` stall partial-content drop (producer
   is `core/agent_session/subagent_runs.rs` plus the loop stream watchdog, outside this map).
