# DGX-Only Evaluation Driver — Design (no implementation)

Campaign: OCC-2 optimization (occ2opt). Owner: occ2-baseline.
Status: DESIGN ONLY. No code in this document has been run against models.

## 1. Purpose and hard constraints

The campaign needs model-in-the-loop validation of the OCC-1 wire (and any
evolved dialect) with a TRUE speaker and a TRUE listener, not the
programmatic pre-encoding that `research/symbolic_comm/eval.py` uses for its
deterministic parts. The only authorized model channel is:

- DGX GLM 5.3 subagents, spawned as native RLM children.
- `agent_message` is the ONLY channel (prompt in, reply out).
- No HTTP model APIs, no network, no local tokenizer access.
- At most 4 concurrent campaign subagents (the 4-slot cap shared with the
  implementation lanes).

Everything below is designed around those constraints and states honestly
what they make unmeasurable (section 9).

## 2. What the driver measures (mapping to eval.py metrics)

| eval.py metric | DGX driver realization | Status |
|---|---|---|
| deterministic fidelity (encode->decode == original) | unchanged: kernel codec, offline, per corpus message | kept, free |
| listener comprehension (payload equality) | listener child reconstructs the payload from wire; `contract.payload_from_listener_json` + `contract.equal` oracle | kept, true listener |
| objective Q&A accuracy | listener child answers the corpus QA items; `research/tasks/checkers.py` scoring vs gold | kept, true listener |
| wire tokens vs baselines | NOT measurable offline (needs a real tokenizer / gateway) | chars proxy only, labeled |
| latency (listener wall clock) | parent-measured wall clock per turn (prompt send -> reply arrival) | kept, agent_message channel |
| (new) speaker fidelity | speaker child encodes a typed payload to wire; deterministic decode + `contract.equal` vs ground truth | new, true speaker |

## 3. Architecture

Roles:

- **Orchestrator**: the campaign parent, or a dedicated eval-runner child if
  the parent must stay free. Owns the corpus splits, gold answers, scoring,
  the eval log, and all child lifecycles. Never forwards gold to children.
- **Speaker child** (Agent A): receives a typed payload (or plain-English in
  free-speaker mode) and must emit the exact wire text.
- **Listener child** (Agent B): receives only the wire text (plus an in-band
  codebook/dialect card when the dialect under test requires one) and must
  answer QA items and reconstruct the payload.

Topologies under the 4-slot cap:

- **T1 paired (recommended)**: 2 pipelines, each = (speaker, listener) pair
  => 2 items in flight, 4 slots. If implementation lanes hold slots, degrade
  to 1 pipeline.
- **T2 sequential single**: one child alternates speaker for item i and
  listener for item j (j from a different batch half). 1 slot, halves
  throughput, and worsens in-session contamination. Use only under slot
  pressure; label runs accordingly.

Slot ledger: the orchestrator records slot occupancy per time window in the
run manifest; eval items never preempt implementation lanes (campaign rule).

## 4. Per-item protocol

Inputs per item: corpus entry from `research/tasks2/msgs_corpus2_<split>.json`
(payload, plain_english, qa[], scenarios, story, id).

### 4.1 Speaker step (scripted-speaker mode, default)

Prompt template (deterministic, recorded verbatim):

```
You are Agent A, the sender in a two-agent protocol. Encode the typed
message below as an OCC/1 wire message. Emit ONLY the wire text starting
with the line "OCC/1", no commentary, no code fences.

Typed message (ground truth; do not paraphrase):
<payload JSON>
```

For a dialect under test, replace the second paragraph with the dialect card
reference and its encoding rules (in-band, exactly what a production speaker
would see).

Reply handling: the child replies via `agent_message.send(..., receiver_role='parent')`
with the wire text. The orchestrator:

1. strips nothing (exactness is part of the test; a fenced reply is a format
   fault, see 5.3),
2. decodes with the kernel codec `Occ1Message.decode` (via frozen
   `research/symbolic_comm/contract.py`),
3. reconstructs the typed message `AgentCommMessage.from_occ1`,
4. compares with `contract.equal` against the ground-truth payload,
5. records `speaker_fidelity_ok` plus first divergence field when false.

### 4.2 Speaker step (free-speaker mode, optional strictness)

The speaker receives ONLY `plain_english` and must produce the typed wire.
This tests speaker comprehension + encoding together. Scored the same way
(false = any semantic divergence). Label runs `mode=free-speaker`; do not
mix modes inside one comparison table.

### 4.3 Listener step

Prompt template:

```
You are Agent B, the receiver in a two-agent protocol. Agent A sent the
wire message below. Answer the numbered questions about its content.
Quote values exactly as they appear on the wire. Output each answer on
its own line as "N. <answer>". Then output a final line "PAYLOAD:" followed
by the reconstructed typed message as one JSON object on one line.

Wire message:
<wire text>
[optional: codebook / dialect card, in-band]

Questions:
1. <qa[0].q>
2. <qa[1].q>
...
```

Reply handling: parse numbered answers by regex `^(\d+)\.\s*(.*)$` per line;
parse the payload after `PAYLOAD:` with `contract.payload_from_listener_json`
(accepts raw or fenced JSON; fence use is recorded). Scoring:

- per QA item: `checkers.check_answer({family, gold}, got)` (A text, D set,
  E numeric rules);
- payload equality: `contract.equal(from_payload(ground_truth), from_payload(listener_json))`;
- per-item record: qa_correct / qa_total, comprehension_ok, and per-scenario
  rollups (the corpus `scenarios` tags give per-scenario accuracy slices).

The listener NEVER sees: the ground-truth payload, gold answers, the
plain_english rendering, or any other item's content. The speaker NEVER sees
the QA questions.

## 5. Batching, concurrency, retries, timeouts

### 5.1 Batches

- Batch size B (default 10) items per child session. After a batch, the
  child is deleted (`rlm.delete_subagent`) and a fresh one is spawned for
  the next batch. This bounds in-session contamination (section 7).
- One child session only ever sees ONE split's items. Evolve batches tune;
  dev batches select; holdout batches run only after every tuning decision
  is frozen (the corpus holdout discipline, applied to the eval loop).
- Item order inside a batch is shuffled with a recorded seed.

### 5.2 Concurrency

- T1: two (speaker, listener) pipelines in parallel; the orchestrator
  sends speaker prompt for item i+1 while the listener for item i is still
  answering (pipelining across items, since agent_message replies arrive as
  steering messages to the orchestrator's own turn loop).
- Throughput estimate: ~2 items per model-turn round trip; with B=10 and
  4 slots, a 90-message evolve pass is ~5 batches per pipeline.

### 5.3 Retries and faults

- **Format fault** (fenced/garbled wire, missing numbered answers, no
  PAYLOAD line): re-prompt the SAME child once with a corrective format
  hint (format only, never content), then mark `protocol_fault` if it
  repeats. Protocol faults are counted in a separate bucket and reported
  both including and excluding them from accuracy denominators.
- **Soft timeout** (default 180 s without reply): nudge once.
- **Hard timeout** (default 360 s): kill the child, respawn fresh, re-queue
  the item at the end of the batch. Log the interruption.
- **Child death / undeliverable reply**: respawn, re-queue remaining items,
  record `child_lost` events.
- **Deterministic fault** (wire decodes but != ground truth in scripted
  mode): NEVER retried as a format issue — it is a measurement, not a glitch.

## 6. Provenance: the eval log

One JSONL file per run: `reports/occ2opt/dgx-eval/<run-id>/log.jsonl`, plus
`summary.json` and `run-manifest.json`. Per-item record:

```
{"item": "e2-e01-01", "split": "evolve", "story": "occ2b-e01",
 "scenarios": ["parent_to_child"], "mode": "scripted-speaker",
 "speaker_child": "<name>", "listener_child": "<name>",
 "speaker_prompt_sha256": "...", "speaker_prompt_chars": 1234,
 "speaker_reply_sha256": "...", "speaker_reply_chars": 567,
 "wire_chars": 749, "speaker_fidelity_ok": true,
 "listener_prompt_sha256": "...", "listener_prompt_chars": 1500,
 "listener_reply_chars": 800, "listener_latency_ms": 4321,
 "speaker_latency_ms": 5210,
 "qa": [{"n": 1, "gold": "...", "got": "...", "pass": true}],
 "qa_correct": 2, "qa_total": 3, "comprehension_ok": true,
 "retry_count": 0, "protocol_fault": false}
```

Every prompt and reply is additionally stored verbatim (prompts/ and
replies/ subdirs, sha256-named). Latency = orchestrator wall clock from
prompt send to reply arrival (agent_message does not expose model timing).
Char counts are recorded because token counts are NOT available (section 9).

run-manifest.json records: corpus file hashes (from receipts.json), dialect
card hash if any, mode, B, seeds, slot ledger, child names per batch, and
the frozen decision log (which dialect/hyperparameters were already locked
before the run — the anti-leakage proof for holdout runs).

## 7. Contamination controls

- Fresh children per batch (delete + respawn). B bounds exposure.
- Split isolation: a child never sees items from two splits.
- Holdout runs happen only after tuning freezes; the manifest proves the
  ordering (decisions logged with hashes before the run id exists).
- Canary items: 2 known-gold items per batch, drawn from the SAME split the
  child is serving, inserted at recorded positions; drift on canaries flags
  in-session contamination for investigation (reported, not silently dropped).
- Documented limitation: within one batch, the child's session retains
  earlier items in context. There is no per-message session reset in the
  current child model (agent_message children are persistent sessions). B is
  the exposure knob; report accuracy vs batch position as a drift diagnostic.

## 8. Mapping onto the eval.py 5-pair matrix (mission section 11)

`eval.py` MODEL_PAIRS: C1 (glm-5.3 -> glm-5.3), X1 (glm-5.3 ->
deepseek-v4-pro), X2 (deepseek-v4-pro -> glm-5.3), X3 (glm-5.3-flash ->
deepseek-v4.1-flash), X4 (deepseek-v4.1-flash -> glm-5.3-flash), where the
speaker side was pre-encoded programmatically (deterministic function of
the payload) and only the listener was a real model.

- **C1 is fully realizable and is the primary offline cell**: DGX children
  are GLM 5.3 on both sides; the driver replaces the programmatic speaker
  with a true speaker. This closes the "missing true speaker-listener
  design" gap for the same-model cell.
- **X1-X4 are NOT realizable under the DGX-only constraint** (no deepseek
  children authorized). Label them "deferred to the online phase" in every
  table; the offline matrix is a single (glm-5.3, glm-5.3) cell plus the
  deterministic codec baselines (OCC-0 / OCC-1 / JSON wire comparisons,
  which remain offline-computable at the codec level).
- The driver's split structure matches eval.py (EVOLVE tune / DEV select /
  HOLDOUT final), and its metrics map 1:1 where measurable (section 2).

## 9. What CANNOT be measured this way — honest labels

1. **Provider usage tokens** (prompt/completion token counts from a real
   tokenizer): agent_message exposes no usage. All size metrics are
   CHARACTERS and must be labeled `chars, not provider tokens`. Note the
   prior campaign finding that even gateway `completion_tokens` counts
   hidden reasoning — offline char counts are also not comparable to any
   online token number.
2. **Heterogeneous model pairs** (X1-X4): deferred to the online phase.
3. **Daemon delivery-plane costs** (queueing, RPC overhead, durable dual
   payload cost in production): the agent_message channel is not the
   daemon delivery plane. Label `agent_message channel`.
4. **Cross-session memory effects within a batch**: see section 7;
   labeled with B.
5. **Free-speaker naturalness beyond the corpus**: the scripted-speaker
   mode measures encoding fidelity from a typed ground truth; the
   free-speaker mode measures comprehension+encoding from plain English.
   Neither measures a human-like speaker composing from live task context
   (no such ground truth exists offline).
6. **Latency at the provider level**: only orchestrator wall clock is
   available; includes daemon queueing. Label `wall-clock, channel-inclusive`.

## 10. Acceptance gates for an eval run (before numbers are quotable)

1. Run manifest complete: corpus hashes match receipts.json, decision log
   frozen before run start (holdout), slot ledger recorded.
2. Zero unlogged child losses; all requeues accounted.
3. Canary drift within tolerance (no monotonic accuracy-vs-position slope
   beyond noise).
4. Protocol-fault rate reported; both accuracy variants (with/without
   protocol faults) quoted together.
5. All sizes labeled chars; X1-X4 labeled deferred; no token claims.

## 11. Example walkthrough (item e2-e01-01, scripted-speaker, evolve)

1. Orchestrator sends speaker child `eval-spk-1` the typed payload of
   e2-e01-01 (TASK, pending, one finding, deps [t77], next_actions x3).
2. Child replies with an OCC/1 wire. Orchestrator decodes with the kernel
   codec; `contract.equal` says the wire round-trips to the same typed
   message => speaker_fidelity_ok = true (a divergence in, say, NEXT order
   would be a real measurement: OCC-1 is order-sensitive for NEXT).
3. Orchestrator sends listener child `eval-lst-1` the wire plus the 3 QA
   questions for the item (status, kind, dependency set...).
4. Listener replies "1. pending 2. TASK 3. t77" + PAYLOAD json.
   checkers: A/A/D all pass => qa 3/3; payload reconstruction equal =>
   comprehension_ok = true.
5. Log record appended with chars, sha256s, latencies; per-scenario
   counters for parent_to_child updated.

## 12. Implementation notes (for whoever builds it)

- Spawn: `handle = await rlm('sub-task', name='eval-spk-1')`; children
  inherit the orchestrator model (GLM 5.3 on DGX). Ask for explicit replies
  (`await agent_message.send(..., receiver_role='parent')`).
- The orchestrator must NOT hold long turns: send prompts, end turn, and
  process replies as they arrive as steering messages (the nonblocking
  control loop pattern); keep per-pipeline state in named variables.
- Delete children after their last batch: `await rlm.delete_subagent(handle)`.
- Scoring and codec validation import only frozen artifacts
  (`research/symbolic_comm/contract.py`, `research/tasks/checkers.py`) and
  the new corpus; nothing under crates/ is touched.
