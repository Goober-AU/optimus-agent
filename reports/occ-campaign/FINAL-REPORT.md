# OCC Campaign Final Report

Branch `improve/rlm-multi-agent-comm`, base `159ce68cc` (telemusai/optimus-agent v0.1.30 main).
Campaign window: 2026-10-15. Host: Windows, private Rust toolchain 1.98.1.

Scope statement. Everything in this report was measured OFFLINE: no network, no model APIs,
no real credentials. Model-dependent claims (agent comprehension of encodings, real tokenizer
counts, official RLM/SILO/MARBLE benchmark scores) are explicitly NOT claimed. "Tokens" below
are proxy tokens: a whitespace-plus-punctuation split, documented as a tokenizer approximation.

---

## A. Research summary

Eight references were analyzed offline (clones at exact SHAs; full analysis in
`docs/research/agent-communication-landscape.md`).

| Reference | What it is | SHA / source | License | Decision |
|---|---|---|---|---|
| RLM (alexzhang13/rlm, arXiv 2512.24601) | Recursive LM-as-orchestrator over a persistent REPL; subcalls, budgets, compaction | `d04208af` | MIT | **ADOPT (techniques)** |
| MOC (Guan et al., PMLR v306 guan26i) | Multi-order evidence streams + Semantic-Topological Merging (ISM) under token budgets | `9c67c925` | no license file | **EXPERIMENT** |
| AgentPrune (Zhang et al., ICLR'25, arXiv 2410.02506) | Trainable communication graph with periodic edge-mask pruning | `c544dd6a` | no license file | **EXPERIMENT (offline variant)** |
| LatentMAS (Zou et al., PMLR v306 zou26k, arXiv 2511.20639) | Pure latent collaboration via KV-cache/hidden-state transfer | `9a9e4d33` | Apache-2.0 | **REJECT (for this transport)** |
| MARBLE / MultiAgentBench (ulab-uiuc) | Topology benchmark with LLM-judged communication/planning scores | `8d60fa17` | MIT | **ADOPT (metrics ideas only)** |
| silo-bench (jwyjohn, arXiv 2603.01045) | Role-free distributed coordination benchmark; round engine; S/P/C/D metrics | `e7412778` | Unlicense (public domain) | **ADOPT (offline harness ideas)** |
| MAST (Cemri et al., arXiv 2503.13657) | 14-mode / 3-category multi-agent failure taxonomy | `a70542e5` | no license file | **ADOPT (test checklist)** |
| CLSR / LSF (Pei et al., PMLR v306 pei26a) | Agents invent and evolve compact symbolic protocols (LSFs); per-query router | paper PDF only | paper only | **EXPERIMENT (design input)** |

Decisions in detail:

- **RLM — ADOPT (technique-level).** Optimus's runtime is already an RLM descendant; the
  reference's battle-tested mechanisms (budget/timeout inheritance, depth degradation to a
  plain LM, consecutive-error circuit breaker with best-partial answers, batched subcalls with
  per-slot error isolation, compaction with reachable full history) map directly onto
  `rlm_runtime.rs` / `rlm_continuation.rs` / the REPL protocol. MIT-licensed. The depth-limit
  degradation was ported this campaign (EXP-001); the rest are documented next steps.
- **MOC — EXPERIMENT.** The ISM budgeted similarity-merge loop is the only reference that frames
  consolidation as multi-hop evidence with an explicit token budget and order preservation. Its
  merge core is an LLM call (five prompts, best-of-by-embedding) and its topology learner needs
  torch/GNN; both were replaced by a deterministic structural merge (implemented as
  `occ::consolidation`) before anything could be tested offline.
- **AgentPrune — EXPERIMENT (offline variant).** The never-prune exemption list and the
  periodic fractional pruning skeleton port well; the REINFORCE training loop needs hundreds of
  rollouts against an accuracy oracle, impossible offline. Only the policy skeleton and the
  exemption invariants were adopted (implemented as `occ::prune`).
- **LatentMAS — REJECT (see H).** Requires same-model KV/tensor access, tokenizer identity, and
  in-process or shared-GPU memory; fundamentally incompatible with hosted black-box APIs and
  the daemon's text transport.
- **MARBLE — ADOPT (metrics ideas only).** Its LLM-judge rubric cannot run offline; its
  milestone/KPI decomposition and relationship-checked message-tool pattern informed the
  fidelity suite's assertion design.
- **silo-bench — ADOPT (offline harness ideas).** Its round-engine shape (per-agent state,
  round barrier, per-agent ground truth, S/P/C/D metrics) is exactly the shape of the offline
  communication-fidelity suite that was built. Public domain.
- **MAST — ADOPT (as test checklist).** The 14 failure modes map one-to-one onto offline
  transport fault-injection cases (duplicate, lost, out-of-order, corrupted, wrong-recipient,
  lifecycle faults). This grounded the corruption matrix in the codec and fidelity suites.
- **CLSR / LSF — EXPERIMENT (design input).** An LSF is a typed compact-communication contract
  with a symbol table; this validates OCC-1's grammar direction. No code exists; claims are
  model-dependent; used as design vocabulary only.

Nonexistent reference, recorded honestly: `pzqpzq/LSF_MDia` does not exist (GitHub 404; the
owner has zero public repos; no name-variant matches in GitHub search). The campaign fell back
to the closest public paper on the described topic: Pei et al., "When LLMs Develop Languages:
Symbolic Communication for Efficient Multi-Agent Reasoning" (PMLR v306 pei26a), analyzed from
the PMLR PDF.

License notes. rlm (MIT), MARBLE (MIT), silo-bench (Unlicense/public domain) are permissive:
small algorithmic adaptations with attribution are fine. LatentMAS is Apache-2.0 (NOTICE-style
attribution if code were copied; none was). **MOC, AgentPrune, and MAST ship no license file:
treated as all-rights-reserved — ideas only, no code copied**; everything derived from them
here is a re-implementation from the papers' descriptions, with citation.

Five most transferable techniques (exact sources in the research report, section 5):
1. RLM budget/timeout inheritance + depth degradation for subcalls (`rlm/core/rlm.py:_subcall`).
2. Batched subcalls with per-slot error isolation and bounded concurrency
   (`lm_handler.py:_handle_batched`, `comms_utils.py:send_lm_request_batched`).
3. silo-bench round engine + S/P/C/D metrics as an offline communication-fidelity harness.
4. MAST failure taxonomy as a fault-injection test matrix.
5. MOC ISM loop skeleton with a deterministic (non-LLM) merge core.
(Honorable mentions: AgentPrune's never-prune exemption list; RLM's
compaction-with-reachable-history pattern for evidence references.)

## B. Code assessment (existing architecture as touched by this campaign)

Integration surface (read, not rewritten except where listed in C):

- `crates/pi-coding-agent/src/core/rlm_runtime.rs` (944 lines): RLM host-request handlers,
  subagent runtime, spawn/collect/delete/list.
- `crates/pi-coding-agent/src/core/rlm_continuation.rs` (619 lines): child terminal
  classification, continuation state, result receipts.
- `crates/pi-coding-agent/src/core/rlm_max_depth.rs`: depth status (extended this campaign).
- `crates/pi-coding-agent/src/core/agent_messages.rs` (1467 lines): family roster, reach checks,
  message normalization/capacity, receipts, prompt-id framing.
- `crates/pi-coding-agent/src/core/semantic_edges.rs` (1366 lines): per-request semantic-edge
  ledger and fold.
- `crates/pi-coding-agent/src/core/agent_session/`: `dispatch_delivery.rs` (243),
  `task_queue.rs` (37, serialized promise tail), `native_subagents.rs` (80, the `subagent`
  tool: spawn/list/collect).
- `prime-agent-runtime/src/rlm/{repl.py, lifecycle.py, execution.py, harness.py}`: Python REPL
  kernel protocol, host requests, harness CRUD.

Assessment. The architecture is sound for the OCC layering: the daemon transport is
authoritative (sender identity established by the daemon, family scoping and reach
restrictions unchanged), and OCC sits on top as interchangeable encodings of one typed payload
rather than a parallel lifecycle. Plain English remains the default; OCC-1 is opt-in.

Bottlenecks and reliability weaknesses observed (citing BUG-LEDGER):

- **Depth-limit failure class (fixed).** Before this campaign, a session at the recursion limit
  that tried to spawn a child got a bare error (`RLM recursion depth limit reached (...)` in
  `subagent_runs.rs`); the reference RLM instead degrades to a plain LM completion. The
  campaign ported the degradation rule (EXP-001); see C and G.
- **BUG-001 (fixed): silent truncation in the new OCC codec.** `indexed_count` counted
  contiguous presence, so a corrupted message with `F1` + `F3` silently dropped `F3` and decoded
  as a shorter valid message — a silent data-loss path in agent-to-agent results. High
  severity; see D.
- **BASELINE-001: Windows test-environment fragility.** On clean main, 22 of 22 cargo lib
  failures on this host are a missing `KERNEL_CONTAINMENT_TEST_PYTHON` env var; with the env
  var set, 8 pre-existing Windows-only harness/env failures remain (legacy directory-lock
  reclaim, Git Bash `PATH` order assertion, a missing `.port-env/tmp` fixture dir, ipython
  bootstrap restore, session-shutdown helper env, an admission-waiter snapshot, a nested
  kernel_job, shell bin-dir prepend). None touch OCC modules or campaign-modified files. They
  are recorded as the clean-main Windows baseline; fixing them is out of campaign scope.
- **Serialization point.** `agent_session/task_queue.rs` is a 37-line serialized promise tail:
  delivery completion is funneled through one queue. Combined with `dispatch_delivery.rs`
  batching of compatible family messages, this is the natural congestion point for high
  fan-in; the reference RLM solves the same problem with a bounded-concurrency semaphore plus
  per-slot error isolation (documented in G as a next step, not implemented).
- **No budget/timeout inheritance.** Unlike the reference `_subcall` (children receive
  `max_budget - cumulative_cost` and `max_timeout - elapsed`, spend folded back upward), the
  Optimus spawn path does not propagate remaining budget/timeout. Deep trees can overspend
  without a circuit breaker. Researched, not implemented (G/J).
- **Platform-blocked probes.** `scripts/test-native-subagents.mjs` and
  `scripts/test-subagent-modes.py` assert Linux `/proc`; they cannot run on this Windows host
  (see H).

## C. Implemented improvements

Feature -> files -> tests -> measurements. All campaign commits are on the branch; diff stat vs
base: 21 files changed, 11,520 insertions(+), 3 deletions(-).

1. **RLM depth-limit graceful degradation (EXP-001; commit `fe723f86b`).**
   - Files: `core/rlm_max_depth.rs` (`rlm_depth_limit_reached_error`), `core/agent_session/
     subagent_runs.rs` (spawn guard now calls it), `core/prompts/rlm.rs` (deepest-agent line
     added when `allow_recursion` is false).
   - Rationale (from the reference rlm repo, MIT, SHA `d04208af`): in `rlm/core/rlm.py:_subcall`,
     `next_depth >= max_depth` degrades the subcall to a plain LM completion — "an RLM is an LM
     at max depth" — instead of raising. Ported here as prompt/error-text guidance (no lifecycle
     change): the spawn error keeps its machine-readable prefix byte-identical (existing parsers
     unaffected) and now tells the deepest agent to answer directly from its current context;
     the max-depth system prompt states the same duty proactively.
   - Tests: 2 new unit tests in `rlm_max_depth.rs`, 1 new prompt test in `prompts/rlm.rs`;
     all green.
   - Measurements: not measurable offline (prompt/error text only). No lifecycle behavior
     change; correctness is the byte-identical machine-readable prefix plus the added guidance.
   - Decision: KEEP (changelog fragment `.changes/rlm-depth-degradation.md`).

2. **OCC-1 codec + `optimus.agent-comm.v1` typed contract (EXP-002; commits `8f98b218b`,
   `8b14c8cb7`).**
   - Files: `core/occ/occ1.rs` (537 lines; line grammar, key validation, per-kind dialect),
     `core/occ/contract.rs` (792 lines; `AgentCommMessage`, `Finding`, `EvidenceRef`,
     `Verification`, validate/to_occ1/from_occ1), `core/occ/mod.rs` (270 lines; level model and
     frozen API specs for the worker modules).
   - One typed payload behind every text encoding: OCC-0 plain English (default, unchanged),
     OCC-1 compact structured English (implemented), OCC-2/OCC-3 future (not implemented).
     Deterministic parse/validate; anything the grammar cannot express is rejected, never
     silently dropped.
   - Tests: 10 scaffold unit tests green at the foundation commit; now part of the 178-test
     core::occ run (103 adversarial codec + 39 consolidation/prune + 23 delta + 13 inline
     contract/occ1 tests).
   - Decision: KEEP (foundation).

3. **Required next actions in the contract (`8b14c8cb7`).** First-class `next_actions` field so
   the OCC-1 `NEXT` line round-trips; the campaign brief requires OCC-1 messages to carry
   required next actions. Verified by the 10 contract unit tests (10/10 green at that commit).

4. **Delta-based messaging with OCC-1 wire encoding (worker B; commit `1b537f5fc`).**
   - Files: `core/occ/delta.rs` (1122 lines) + `core/occ/delta_tests.rs`.
   - `DeltaTracker` sequences change-only updates per task: rejects wrong-task deltas, buffers
     out-of-order deltas (cap 1024), treats replays as idempotent duplicates, distinguishes
     stale bases from recoverable gaps, exposes exactly-once receipts via `drain_ready`;
     restart recovery is exact through serializable `state()`/`restore()`. Wire encoding is
     strict: positional finding/evidence ids, canonical update order, self-checking evidence
     reference values.
   - Tests: 20 unit tests at the commit (happy path, gap filling, duplicate replay, wrong task,
     stale base, buffer cap, seed/unknown findings, save/restore mid-stream, wire round-trips,
     wire fault injection); 23 after the EADD tests (below). All green.

5. **EADD wire form for `FieldUpdate::AddEvidence` (worker B finding; commit `cc1ec0870`).**
   - The last non-round-trippable variant: `AddEvidence` could not be expressed on the OCC-1
     wire. Fix: `EADD=E<n>` (repeatable, after the `E<n>` entries) encodes `AddEvidence`
     references to evidence introduced by the same delta; references to evidence the delta does
     not introduce are rejected. Three new tests: round-trip, foreign-reference rejection,
     out-of-range rejection.

6. **Adversarial codec suite (worker A; landed in `cc1ec0870`).**
   - Files: `core/occ/codec_tests.rs` (2197 lines, 103 tests).
   - Covers grammar conformance, index-gap rejection, dialect/encoder alignment, pinned error
     variants, and the corruption/semantic-fault classes below (E). All 103 green.

7. **Evidence consolidation (worker C; landed in `cc1ec0870`).**
   - Files: `core/occ/consolidation.rs` (405 lines) + `consolidation_tests.rs` (18 tests).
   - MOC-inspired but fully deterministic: normalized-statement merge, evidence dedup by key
     mode (Locator / LocatorAndDigest / LocatorDigestRevision), same-statement disagreements
     become explicit `Conflict`s with every position preserved verbatim, uncertainties carried
     through, ordered output, inputs never mutated. 18 tests green.

8. **Redundant-message pruning (worker C; landed in `cc1ec0870`).**
   - Files: `core/occ/prune.rs` (322 lines) + `prune_tests.rs` (21 tests).
   - AgentPrune-inspired suppression with safety rules BEFORE any heuristic: never suppress
     BLOCKED/CANCELLED/CORRECTION/ACK kinds, FAILED status, verification failures,
     contradictory findings, unseen dependencies, or a task's first message; duplicate
     detection per task over sha256 digests; history eviction biases toward re-delivery.
     21 tests green.

9. **BUG-001 fix: index-gap rejection (commit `cc1ec0870`).** See D.

10. **BUG-002 fix: dialect/encoder alignment (commit `cc1ec0870`).** See D.

11. **Offline communication-fidelity suite with encoding comparison (worker E; commit
    `b930e28cc`).**
    - Files: `tests/occ_comm_fidelity.rs` (1235 lines, 7 tests t01–t07) +
      `tests/fixtures/occ/scenarios.json` (28 scenarios, 7 holdout).
    - Round-trip equality through validate -> to_occ1 -> encode -> decode -> from_occ1 for
      every scenario; systematic wire corruption never decodes equal and never panics;
      semantic faults all `ContractError`; encoding-size comparison plain vs concise vs JSON
      vs OCC-1; codec latency. Numbers in E/F.

12. **Documentation.** `docs/research/agent-communication-landscape.md` (720 lines),
    `reports/occ-campaign/EXPERIMENT-LEDGER.md`, `reports/occ-campaign/BUG-LEDGER.md`,
    changelog fragments, and the BlankLine error-doc correction (`690301551`: interior lines
    carrying whitespace are `MissingSeparator`; only truly empty lines are `BlankLine` —
    pinned by the codec suite).

Final state of the campaign test gate: full `core::occ` run — 178 passed, 0 failed
(103 codec + 39 consolidation/prune + 23 delta + 7 contract-inline + 6 occ1-inline); fidelity
suite 7/7; no new failures beyond the recorded 8-failure Windows baseline.

## D. Bug report

**BUG-001 — OCC-1 index gaps silently truncate instead of rejecting. Status: FIXED
(fix commit `cc1ec0870`; regression tests green in the adversarial codec suite).**
- Found by: worker `occ-codec-tests` (adversarial test design review), confirmed by parent
  code read.
- Observed: `AgentCommMessage::from_occ1` accepted a message with `F1` and `F3` but no `F2`;
  `indexed_count` stopped at the first missing index, so `F3` was silently dropped and the
  message decoded as a shorter valid message.
- Root cause: `indexed_count` in `contract.rs` counted contiguous presence
  (`while has(prefix+count+1) count += 1`) instead of validating contiguity against the highest
  index present. Affected the F/E/ART/DEP index families.
- Severity: high — silent data loss in agent-to-agent results; a corrupted message could drop
  findings/evidence with no error.
- Fix: `indexed_count` returns `Result<usize, ContractError>`; it scans all keys with the
  prefix, takes the highest numeric suffix, and errors when any index `1..=highest` is
  missing; all four call sites propagate the error. Principle enforced: corrupted compact
  messages are rejected safely, never partially accepted.
- Regression tests: `contract::tests::index_gaps_are_rejected_not_silently_truncated` (F-gap
  and E-gap both rejected) plus matching adversarial-suite cases.

**BUG-002 — dialect/encoder misalignment. Status: FIXED (fix commit `cc1ec0870`).**
- Found by: the adversarial and fidelity suites (worker suites).
- Observed: the standard `Occ1Dialect` required-field table and the `optimus.agent-comm.v1`
  contract encoder did not agree: a message encoded by the contract could fail standard
  dialect validation because the dialect demanded fields the encoder never emits.
- Fix: the standard dialect table now mirrors exactly what the contract encoder emits for
  every kind (`TASK`, `KIND`, `STATUS`, plus `E1` for evidence-bearing messages), so
  contract-encoded messages always pass standard validation and nothing else is demanded that
  the encoder cannot produce; the contract additionally requires EVIDENCE-kind messages to
  carry at least one evidence reference.
- Verification: the 103-test adversarial codec suite and fidelity tests t01/t02 (round-trip
  through validate -> encode -> decode for all 28 scenarios).
- Related documentation correction in `690301551`: the BlankLine error doc was wrong —
  interior lines carrying whitespace are `MissingSeparator`, only truly empty lines are
  `BlankLine`; the codec suite pins this.

**BASELINE-001 — Windows test-env failures on clean main. Status: BASELINE_EXISTING
(14 of 22 cleared by setting `KERNEL_CONTAINMENT_TEST_PYTHON`).**
- Suite: `cargo test --locked --workspace --lib` at `159ce68cc` on this Windows host.
- Run 1 (no env): 3252 passed, 22 failed, 7 ignored — all 22 panic on missing
  `KERNEL_CONTAINMENT_TEST_PYTHON` (kernel_job and repl_manager tests unwrap/expect it).
- Run 2 (env set to the controlled uv-managed CPython 3.11.15): 3266 passed, 8 failed,
  7 ignored. The remaining 8 are test-harness/environment defects on Windows, not OCC-path
  product bugs: `dir_lock` legacy-lock reclaim (expects `Reclaimed`, gets `Held`), the bash
  `spawn_context_env` `PATH`-order assertion under Git Bash, a missing `.port-env/tmp` fixture
  dir for the sdk faux-provider, ipython bootstrap restore, an extensions session-shutdown
  helper env, an `rlm_session_t10` admission-waiter snapshot, a nested kernel_job bash/worker
  case, and the shell bin-dir prepend test. None touch OCC modules or campaign-modified files.
- Action: the campaign regression gate sets `KERNEL_CONTAINMENT_TEST_PYTHON` on every cargo
  run; the 8 failures are the clean-main Windows baseline and are not attributed to campaign
  changes unless a run regresses beyond them. Fixing them is out of campaign scope.

**Environment note — `scripts/check.sh` requires `python3`.** The script runs
`python3 -m unittest discover ...` and `python3 scripts/check-rust-layout.py`. On this Windows
host `python3` is not resolvable in Git Bash (only `python` is on PATH), so `check.sh` cannot
run as written. The campaign ran the equivalent commands with `python` directly (I).

## E. Benchmark scorecard

All numbers are offline and deterministic. Official online suites (RLM paper tasks, SILO
full runs, MARBLE) were NOT run in this phase; no model-in-the-loop score is claimed.

Baseline (BASE-000, clean main at `159ce68cc`, this host):

| Check | Baseline result |
|---|---|
| `cargo check --locked --workspace --all-targets` | PASS (warnings only) |
| `python -m unittest discover -s tests -p 'test_*.py'` | 282 tests, OK (10 skipped) |
| runtime tests (`uv run --project prime-agent-runtime ...`) | 465 tests: 8 failures, 17 errors, 30 skipped (pre-existing, Windows-env) |
| `cargo test --locked --workspace --lib` (no env) | 3252 passed, 22 failed, 7 ignored (all 22 = missing `KERNEL_CONTAINMENT_TEST_PYTHON`) |
| same, env set (clean worktree) | 3266 passed, 8 failed, 7 ignored (the BASELINE-001 eight) |

Campaign (branch tip `690301551`, same host, same env gate):

| Metric | Denominator | Result |
|---|---|---|
| core::occ unit tests | 178 tests | 178 passed, 0 failed |
| — adversarial codec (worker A) | 103 tests | all green |
| — consolidation + prune (worker C) | 39 tests (18 + 21) | all green |
| — delta messaging (worker B) | 23 tests (20 at first commit + 3 EADD) | all green |
| — contract/occ1 inline | 13 tests | all green |
| communication-fidelity suite | 7 tests (t01–t07) | all green |
| round-trip fidelity | 28 scenarios (7 holdout) | 28/28 exact `PartialEq` equality through validate -> to_occ1 -> encode -> decode -> from_occ1 |
| wire corruption | 224 corruptions | 128 rejected with pinned variants; 96 grammatical-but-changed; 0 decoded equal to the original; 0 panics |
| semantic faults | 25 faults | 25/25 rejected with `ContractError` |
| encoding size vs plain English | 28 scenarios | -47.7% bytes, -40.6% proxy tokens; occ1 <= plain in 28/28 |
| encoding size vs JSON | 28 scenarios | -36.0% bytes, -29.6% proxy tokens; occ1 <= json in 28/28 |
| codec latency | 100 iterations/scenario, 28 scenarios | encode mean 24.8 us, decode mean 40.5 us (budget 5 ms/op) |
| depth-limit degradation | 3 new tests | green; machine-readable prefix byte-identical |
| cargo lib suite vs baseline | — | no new failures beyond the recorded 8-failure Windows baseline |

Honest scope of the encoding-size claims: bytes are exact serialized lengths; proxy tokens are
a whitespace+punctuation split (a tokenizer approximation). Real tokenizer counts and agent
comprehension of the encodings require an online run and are NOT claimed here.

## F. Communication comparison

Aggregate over the 28-scenario corpus (per-scenario tables in the fidelity suite output;
train and holdout reported separately there):

| Encoding | Bytes | Proxy tokens | Latency |
|---|---|---|---|
| Plain English (OCC-0, default) | 28,022 | 5,171 | not measured (no codec) |
| Concise English | 13,665 | 2,710 | not measured (no codec) |
| JSON | 22,904 | 4,357 | not measured |
| OCC-1 | 14,659 | 3,069 | encode mean 24.8 us / decode mean 40.5 us (100 iterations/scenario) |

- OCC-1 vs plain English: -47.7% bytes, -40.6% proxy tokens; `occ1 <= plain` in 28/28
  scenarios.
- OCC-1 vs JSON: -36.0% bytes, -29.6% proxy tokens; `occ1 <= json` in 28/28 scenarios.
- Concise English is smaller than OCC-1 in bytes and proxy tokens, but it is untyped: the same
  semantic content is not machine-validatable, cannot express evidence references or required
  next actions structurally, and corruption is not detectable. OCC-1 trades ~7% bytes vs
  concise for a typed, validatable, corruption-detecting contract.
- Latency was measured for the OCC-1 codec path only (the other rows have no codec to time);
  the suite's latency budget is 5 ms per operation on a loaded shared runner.

OCC-2 (learned symbolic dialects) and OCC-3 (true latent communication between compatible
local models) are explicitly NOT implemented; they are future work (J). Latent communication
was honestly rejected for this transport per the research report: it requires same-model
KV-cache/hidden-state access, tokenizer identity, and in-process or shared-GPU tensor
transfer — none of which hosted black-box APIs or the daemon's text transport can provide
(LatentMAS REJECT, section A/H).

## G. RLM and delegation comparison

What changed this campaign:

- **Depth-limit degradation (the only lifecycle-touching change).** Before: a session at the
  recursion limit that attempted a spawn received a bare `RLM recursion depth limit reached`
  error. After (`fe723f86b`): the error keeps the machine-readable limit facts byte-identical
  and adds the degradation instruction ("you are the deepest agent: answer directly from your
  current context instead of delegating further"); the max-depth system prompt states the same
  duty proactively. This ports the reference RLM rule that an RLM at maximum depth is a plain
  LM, not a failure — removing a failure class at the depth boundary without touching the
  spawn/collect lifecycle.

Researched but NOT implemented (documented next steps, from the reference rlm repo and the
research report section 4.1):

- **Budget/timeout inheritance.** Reference `_subcall` subtracts the parent's consumed budget
  and elapsed time before spawning (`max_budget - cumulative_cost`, `max_timeout - elapsed`);
  child spend folds back upward on exhaustion. Port target: the `rlm_runtime.rs` spawn path
  with remaining-budget fields, `semantic_edges.rs` spans for elapsed time.
- **Batched subcalls with per-slot error isolation.** Reference `_handle_batched` uses
  `asyncio.gather(return_exceptions=True)` with a semaphore, index-stable results. Port
  target: `native_subagents.rs` collect + `task_queue.rs`; the concurrency bound should be
  per-session, not process-global as in the reference.
- Further reference techniques recorded for later: consecutive-error circuit breaker with
  best-partial answers, depth-based model routing, length-prefixed JSON framing for the REPL
  host-request protocol (verify current framing first), compaction summaries as first-class
  evidence references, terminal-answer interception as a dedicated schema.

No delegation lifecycle behavior other than the depth-limit text/prompt changed; the OCC
modules are pure logic layered on the unchanged transport.

## H. Negative results

- **LatentMAS: REJECT.** True latent communication needs (1) the same model or bit-identical
  checkpoints on both sides, (2) tokenizer identity, (3) direct writable `past_key_values` /
  `inputs_embeds` access, (4) batch-shape stability, (5) cache-format compatibility. Hosted
  black-box APIs expose none of these, and the daemon's family transport is fundamentally
  token-space. Even a local prototype (single OSS model, ~24 GB+ VRAM for 8–14B bf16, HF
  weights resident, realignment matrix, Judger text fallback) would invalidate OCC-1's text
  grammar and evidence-reference model. Recorded as the honest boundary: compact text contracts
  over latent-internal models — what OCC-1 already is.
- **LSF_MDia: repository does not exist.** GitHub 404; owner `pzqpzq` has zero public repos;
  no name-variant matches. The CLSR paper (PMLR v306 pei26a) is the recorded fallback source;
  no executable artifact existed to evaluate.
- **Linux-only probe scripts: platform blocker.** `scripts/test-native-subagents.mjs` and
  `scripts/test-subagent-modes.py` assert Linux `/proc`; they cannot run on this Windows host.
  Recorded as a platform blocker, not a pass or a fail.
- **Online benchmark phase: not run.** No authorized model API usage in this offline phase;
  therefore no real-tokenizer counts, no comprehension measurements, and no official
  RLM/SILO/MARBLE scores. All campaign numbers are offline-deterministic only.
- **AddEvidence wire-form gap (worker B) — found and closed.** `FieldUpdate::AddEvidence` was
  the last variant that could not round-trip through the OCC-1 wire grammar. Closed with the
  `EADD=E<n>` wire form, restricted to evidence introduced by the same delta; foreign
  references stay rejected. Three regression tests added (round-trip, foreign-reference
  rejection, out-of-range rejection).
- **MOC ISM's merge core: unusable as shipped.** Its five-prompt LLM merge and embedding-API
  selection cannot run offline and would dominate OCC's cost; replaced by a deterministic
  structural merge before testing. The GCN topology learner was skipped entirely (family
  topology is fixed by the daemon).
- **No reference provides an offline-verifiable accuracy claim.** Every headline number in the
  papers requires model calls; OCC tests therefore assert transport properties (delivery,
  ordering, fidelity, grammar conformance, corruption detection), not task accuracy.

## I. Reproducibility

- Repository state: base `159ce68cc` (telemusai/optimus-agent v0.1.30 main), branch
  `improve/rlm-multi-agent-comm`, tip `690301551`. Campaign commits:
  `8f98b218b` (OCC scaffold), `8b14c8cb7` (next actions), `4df3adba1` (research report),
  `fe723f86b` (depth degradation), `974c8dd3a` (changelog fragment), `1b537f5fc` (delta
  messaging), `b930e28cc` (fidelity suite), `cc1ec0870` (index-gap fix, dialect/encoder
  alignment, EADD, worker suites), `690301551` (BlankLine doc fix, ledgers).
- Dataset: `crates/pi-coding-agent/tests/fixtures/occ/scenarios.json` — 28 deterministic
  scenarios (ids s01–s28), 7 holdout (s04, s08, s12, s16, s20, s24, s28; rule: id number
  divisible by 4, asserted by t01), covering all 9 message kinds (TASK, PROGRESS, RESULT,
  EVIDENCE, REQUEST_DETAIL, ACK, CORRECTION, BLOCKED, CANCELLED) and 7 statuses (pending,
  in_progress, partial, done, failed, blocked, cancelled), including conflicting reports,
  corrections, negative constraints, digests/line ranges/revisions, unknowns, and
  security-relevant identities.
- Rust environment (Git Bash on Windows, every cargo call):
  ```
  export RUSTUP_HOME=/c/Users/openclawuser/optimus-rust-toolchain/rustup
  export CARGO_HOME=/c/Users/openclawuser/optimus-rust-toolchain/cargo
  export PATH="/c/Users/openclawuser/optimus-rust-toolchain/cargo/bin:/c/Program Files/nodejs:/c/Users/openclawuser/.local/bin:$PATH"
  ```
- Cargo test gate (the env var is the BASELINE-001 regression gate):
  ```
  export KERNEL_CONTAINMENT_TEST_PYTHON=C:/Users/openclawuser/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe
  cargo test --locked -p pi-coding-agent --lib -- --test-threads=1          # core::occ: 178 passed
  cargo test --locked -p pi-coding-agent --test occ_comm_fidelity -- --test-threads=1   # fidelity: 7 tests
  ```
  Expected: 178/178 in core::occ, 7/7 fidelity; elsewhere in the lib suite only the 8
  recorded BASELINE-001 Windows failures. Cargo discipline on this host: single stream, no
  `--workspace`/`--all-targets` test runs, shared target-dir lock may wait.
- Python tests (run with `python`, not `python3`, on this host):
  ```
  python -m unittest discover -s tests -p 'test_*.py'
  uv run --locked --project prime-agent-runtime python -m unittest discover -s prime-agent-runtime/test
  ```
- Known limitations: testing was Windows-only on this host (the Linux probe scripts and the
  Linux-only behaviors they assert were not executed); `scripts/check.sh` hardcodes `python3`,
  which is not on PATH here, so its steps were run individually with `python`; proxy tokens
  are an approximation of real tokenizer counts; the 8 pre-existing Windows harness failures
  remain in the baseline; the runtime Python suite keeps its pre-existing 8 failures / 17
  errors (Windows-env, unrelated to OCC).

## J. Remaining research

1. **OCC-2 dialect discovery loop.** Learned symbolic dialects in the CLSR sense: exemplar
   induction, selection by correctness and token cost, retention across agents; a harness-side
   lifecycle (e.g., `record_refinement`) for grammar/schema evolution. Requires an online phase
   for any accuracy claim.
2. **OCC-3 latent prototype requirements.** Single OSS model with in-process HF weights,
   KV-cache transfer between agents, tokenizer identity, a latent-realignment matrix, ~24 GB+
   VRAM for 8–14B bf16, and a text fallback path for final decoding. Explicitly out of scope
   for the hosted-API transport; a local-GPU experiment only.
3. **Budget/timeout inheritance port.** Carry remaining budget/timeout into spawn requests;
   fold child spend upward on exhaustion; circuit-break deep trees. Uses
   `semantic_edges.rs` spans for elapsed time.
4. **Batched subcalls with per-slot error isolation.** Index-stable batched collect with a
   per-session concurrency semaphore, building on `native_subagents.rs` + `task_queue.rs`.
5. **MOC ISM full merge policy with a SILO fidelity oracle.** The deterministic merge core
   measured on SILO Level-I aggregation tasks (known answers): a deterministic reducer over
   the consolidated context must reproduce the exact expected output; lossy merges break the
   reducer and fail the oracle. Also: never-merge classes for receipts and terminal markers.
6. **Adaptive delegation.** AgentPrune-style never-prune exemptions as hard invariants (already
   implemented in `occ::prune`), plus utility-driven edge policy from the semantic-edge ledger
   (downstream references), delta-rule messaging (send only the delta vs parent-known state),
   and periodic fractional pruning with floor-at-1 — all gated behind measured utility.
7. **Online benchmark phase.** Real tokenizer counts, comprehension measurements against
   plain/JSON baselines, and official RLM/SILO/MARBLE runs — all requiring authorized model
   API usage, out of scope for this offline phase.

---

Report written by the campaign report writer from the campaign ledgers, the research report,
the campaign git history (9 commits, 21 files, +11,520/-3), and the measured offline suite
outputs. Every number above traces to an offline, deterministic run on this host; nothing
model-dependent is claimed.
