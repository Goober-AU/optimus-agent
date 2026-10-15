# Agent Communication Landscape: Research Report for the OCC Campaign

Date: 2026-10-15
Scope: Reference research for the Optimus Compact Communication (OCC) campaign on branch
`improve/rlm-multi-agent-comm` (worktree `/c/Users/openclawuser/optimus-occ-20261015`, base
telemusai/optimus-agent main @ 159ce68cc, v0.1.30). All analysis is offline: source code of the
reference repositories plus the papers' stated claims. No model APIs were called; no benchmark
numbers in this report were re-measured. Model-dependent claims are cited as reported by the
papers and marked as unverified for the OCC offline test plan.

Integration surface judged against (read during this research, not modified):
- `crates/pi-coding-agent/src/core/rlm_runtime.rs` (944 lines): RLM host-request handlers, subagent runtime, spawn/collect/delete/list.
- `crates/pi-coding-agent/src/core/rlm_continuation.rs` (619 lines): child terminal classification, continuation state, result receipts.
- `crates/pi-coding-agent/src/core/rlm_max_depth.rs` (55 lines): depth status.
- `crates/pi-coding-agent/src/core/agent_messages.rs` (1467 lines): family roster, reach checks, message normalization/capacity, receipts, prompt-id framing.
- `crates/pi-coding-agent/src/core/semantic_edges.rs` (1366 lines): per-request semantic-edge ledger and fold.
- `crates/pi-coding-agent/src/core/agent_session/`: `dispatch_delivery.rs` (243), `task_queue.rs` (37, serialized promise tail), `native_subagents.rs` (80, `subagent` tool: spawn/list/collect).
- `prime-agent-runtime/src/rlm/{repl.py, lifecycle.py, execution.py, harness.py}`: Python REPL kernel protocol, host requests, harness CRUD.

Note: `crates/pi-coding-agent/src/core/occ/` does not exist yet in this worktree; the OCC crate is
greenfield. This report therefore judges applicability against the existing runtime files above.

---

## 1. Clone ledger (exact SHAs)

All clones live in `C:/Users/openclawuser/optimus-rlm-references/` (`--depth 50`).

| Repo | URL | HEAD SHA | Status |
|---|---|---|---|
| rlm | https://github.com/alexzhang13/rlm | `d04208afbad29ca675ab13478c40ee8bebc84bfe` | cloned (MIT) |
| MOC | https://github.com/yao-guan/MOC | `9c67c92507570704a7df73e452552a3f49e83897` | cloned (no LICENSE file) |
| LatentMAS | https://github.com/Gen-Verse/LatentMAS | `9a9e4d331eb11430bd9e64754c6b252b06d73031` | cloned (Apache-2.0) |
| AgentPrune | https://github.com/yanweiyue/AgentPrune | `c544dd6a1858c02c6d5d371d23c6e6ff55e0be21` | cloned (no LICENSE file) |
| MARBLE | https://github.com/ulab-uiuc/MARBLE | `8d60fa17b5596b44458a52d4296061b9fc13d6f2` | cloned (MIT) |
| silo-bench | https://github.com/jwyjohn/acl26-silo-bench | `e74127782ed1c42fff474249961f022c063d76f2` | cloned (Unlicense/public domain) |
| MAST | https://github.com/multi-agent-systems-failure-taxonomy/MAST | `a70542e541b2104ef8fcd785778179e173fb8d70` | cloned (no LICENSE file) |
| LSF_MDia | https://github.com/pzqpzq/LSF_MDia | n/a | **repo does not exist / is not public** (GitHub 404; owner `pzqpzq` has zero public repos; no `LSF_MDia`/`LSF-MDia`/`LSFMDia` matches in GitHub search). Fell back to the paper (Pei et al., "When LLMs Develop Languages: Symbolic Communication for Efficient Multi-Agent Reasoning", PMLR v306 pei26a; PDF fetched to `optimus-rlm-references/pei26a_clsr.pdf`). |

License notes for borrowable material:
- rlm (MIT), MARBLE (MIT, (c) 2024 Haofei Yu), silo-bench (Unlicense, public domain): permissive;
  small algorithmic adaptations with attribution are fine. Do not paste large third-party code.
- LatentMAS (Apache-2.0): permissive; requires NOTICE-style attribution if code is copied.
- MOC, AgentPrune, MAST: **no license file in the repos**. Treat as all-rights-reserved: read for
  ideas, do not copy code. Paper ideas (methods, algorithms described in papers) are not code and
  can be re-implemented from the papers' descriptions; check the papers themselves for license
  statements before any verbatim reuse.
- CLSR paper (pei26a): analyzed from the PMLR PDF only; no code available.

---

## 2. Decision summary table

| Reference | Core idea | Decision | One-line justification |
|---|---|---|---|
| RLM (alexzhang13) | Recursive LM-as-orchestrator over a persistent REPL; sub-(R)LM calls as functions; compaction; batched parallel subcalls | **ADOPT (techniques)** | Multiple concrete, battle-tested mechanisms map directly onto `rlm_runtime.rs`/`rlm_continuation.rs`/REPL protocol; MIT-licensed and structurally aligned with Optimus's existing design. |
| MOC (Guan et al.) | Multi-order (multi-hop) evidence streams + Semantic-Topological Merging (ISM) under token budgets | **EXPERIMENT** | ISM's budgeted similarity-merge loop is a strong candidate for OCC evidence consolidation, but the published core needs an LLM merge step and torch/GNN scaffolding that must be replaced with a deterministic non-LLM core first. |
| AgentPrune (Zhang et al., ICLR'25) | Trainable spatial/temporal communication graph with periodic edge-mask pruning (REINFORCE-style) | **EXPERIMENT (offline variant)** | The edge-mask + periodic-prune skeleton and never-prune classes port well to family-scoped delivery; the gradient training loop cannot run offline, so adopt the policy skeleton, not the optimizer. |
| LatentMAS (Zou et al.) | Pure latent collaboration: agents pass hidden states / KV-cache entries instead of text | **REJECT (for OCC)** | Requires same-model hidden-state/KV access, matching tokenizer, single-process or shared-GPU memory; fundamentally incompatible with hosted black-box APIs and the daemon's text-based family transport. Optional future local-GPU prototype only. |
| MARBLE / MultiAgentBench | Configurable topology benchmark (coding/db/research/werewolf/minecraft) with LLM-judged communication + planning scores | **ADOPT (metrics ideas only)** | Its LLM-judged rubric cannot run offline, but its milestone/KPI decomposition and communication-log scoring prompts are adaptable as offline deterministic checks. |
| SILO-bench | Role-free distributed coordination benchmark: 3 protocols, 3 complexity levels, 30 tasks, per-agent ground truth | **ADOPT (offline harness ideas)** | Round-based protocol engines (msg/broadcast/sfs) with per-agent state dirs, plus S/P/C/D metrics, are exactly the shape of offline synthetic communication-fidelity tests OCC needs; public-domain license. |
| MAST (Cemri et al.) | 14-mode failure taxonomy across 3 categories over annotated MAS traces | **ADOPT (as test checklist)** | Directly maps to a fault-injection checklist for the daemon transport (duplicate/lost/out-of-order/corruption/wrong-recipient/lifecycle) — cheap, offline, high value. |
| CLSR / LSF (Pei et al.) | Agents invent and evolve compact symbolic protocols (LSFs); router selects per query for accuracy-token tradeoff | **EXPERIMENT (design input for OCC-1)** | The "message-passing contract + symbol table + evolution loop" design validates OCC-1's grammar direction, but the evidence base is model-dependent and no code exists; use as design vocabulary, not implementation. |

---

## 3. Per-reference analysis

### 3.1 RLM — alexzhang13/rlm (MIT, SHA d04208afbad29ca675ab13478c40ee8bebc84bfe)

**Algorithm.** An RLM is an LM plus a REPL that the model drives turn by turn. `RLM.completion()`
(`rlm/core/rlm.py:completion`, ~line 240) spawns per-call `LMHandler` (a localhost TCP server,
`rlm/core/lm_handler.py:LMHandler`) and an environment (default `LocalREPL`), then loops:
1. Build user prompt ("Turn i/N:") via `build_user_prompt` (`rlm/utils/prompts.py`).
2. `LMHandler.completion(message_history)` returns a response.
3. `find_code_blocks` extracts ```` ```repl ```` blocks; each is executed in the environment
   (`_completion_turn`, rlm.py). Stdout is appended to history via `format_iteration`.
4. The REPL's `answer` dict signals completion: an `answer["ready"] = True` write is intercepted by
   a `__setitem__` hook in the injected `_RLMAnswerDict` and sent to the handler
   (`rlm/environments/ipython_repl.py`, scaffold code; also `LocalREPL` equivalent).
5. Limits checked each iteration (`_check_iteration_limits`): max_iterations, max_budget (USD),
   max_timeout, max_tokens, max_errors (consecutive-error circuit breaker); each has a distinct
   exception type and `_best_partial_answer` fallback (`_default_answer` asks the model one final
   time over history; exceptions carry the partial answer).

**Recursion budgets and subcalls.** `_subcall` (rlm.py, ~line 470) is the heart of recursion:
- Computes `next_depth = depth + 1`. If `next_depth >= max_depth`, it degrades to a plain LM
  completion ("RLM is an LM at max depth") using an optional cheap `other_backends` client.
- Before spawning a child it *subtracts the parent's already-consumed budget/timeout*: the child
  receives `max_budget - cumulative_cost` and `max_timeout - elapsed`. This makes budgets strictly
  inherited and non-renewable down the tree — the tree shares one wallet and one clock.
- The child RLM is constructed with depth+1, its own logger, inherited custom_sub_tools (tool
  restriction propagation: children get the sub-tool set, not the full parent tool set), and the
  same max_iterations/max_errors.
- Child cost is folded back into the parent's `_cumulative_cost` after completion; budget
  exhaustion in a child propagates spent amounts upward via `BudgetExceededError.spent`.
- `llm_query` (single LM call), `llm_query_batched`, `rlm_query` (recursive child with own REPL),
  `rlm_query_batched` are injected into the REPL namespace. Batched calls run concurrently:
  `LMRequestHandler._handle_batched` (lm_handler.py) uses `asyncio.gather` with a
  `batch_max_concurrent` semaphore; failures are isolated per prompt (`return_exceptions=True`
  then per-slot error completions). The REPL-side broker bounds concurrency *globally* per
  `max_concurrent_subcalls` regardless of fan-in (documented in IPythonREPL docstring).
- Model routing by depth and name: `LMHandler.get_client(model, depth)` prefers explicit model
  match, then depth-based routing (depth 1 -> cheaper `other_backend_client`), then default.

**Wire protocol.** 4-byte big-endian length prefix + UTF-8 JSON (`socket_send`/`socket_recv`,
`rlm/core/comms_utils.py`). Typed envelopes `LMRequest`/`LMResponse` support single vs batched
(`prompts` list, `chat_completions` list). One TCP connection per request (`socket_request`);
broken-pipe from racing parallel clients is explicitly tolerated in the handler.

**REPL state reuse / persistence.** `persistent=True` reuses the environment across
`completion()` calls. The `SupportsPersistence` protocol (`rlm/environments/base_env.py`, 5
methods: `update_handler_address`, `load_context`, `add_context`, `add_history`, `get_*_count`)
versions contexts as `context_0..n` and histories as `history_0..n`; user prompts are informed via
`build_user_prompt` notes ("You have 3 prior conversation histories..."). There is **no
checkpointing** in this repo (rg for "checkpoint" over `rlm/` and `docs/` returns nothing);
Optimus's snapshot mechanism is ahead of the reference here.

**Compaction.** When `compaction=True` and history tokens >= `compaction_threshold_pct` (default
0.85) of the model context limit (`_get_compaction_status` uses `count_tokens` +
`get_context_limit` per model), `_compact_history` runs: (1) ask the model for a 1-3 paragraph
summary with an explicit "preserve exact intermediate values" instruction; (2) append the summary
to the REPL's compaction log (`append_compaction_entry`); (3) rebuild history as `[system,
metadata, summary, "You have been compacted N times... Use SHOW_VARS() and history"]`. Key detail:
the full history is *not lost* — it stays reachable in the REPL `history` variable, so compaction
is a context-window move, not data deletion.

**Claimed improvements and evaluation conditions (paper, arXiv 2512.24601).** RLMs process inputs
up to two orders of magnitude beyond context windows; median +26% vs compaction, +130% vs CodeAct
with sub-calls, +13% vs Claude Code on GPT-5 across four long-context tasks at comparable cost;
RLM-Qwen3-8B (post-trained) beats Qwen3-8B by 28.3% on average. Conditions: hosted frontier and
open models with API access, long-context QA/retrieval/code benchmarks; *not reproducible
offline*. Structural mechanisms above are what transfers.

**Implementation complexity.** Core (rlm.py + lm_handler.py + comms_utils.py + prompts.py + types)
is roughly 3,000-3,500 lines of Python; environments add ~170KB (ipython_repl.py alone 67KB).
Dependencies: torch (only for the training/ dir), httpx/openai/anthropic clients, ipykernel.
The Optimus-relevant subset (budgets, batching, terminal classification, compaction) needs no ML
deps.

**Applicability to Optimus.** Very high; Optimus's runtime is already an RLM descendant. Concrete
ports for `rlm_*.rs` and the REPL protocol are in section 4.1.

**Risks.** (1) Python-only reference; Rust ports need re-derivation, not translation. (2) The
paper's headline numbers depend on model quality, so do not advertise them for OCC offline tests.
(3) Their concurrency bound is global-per-broker; Optimus sessions have per-parent child sets, so
the bound must be per-session, not process-global.

**Decision: ADOPT (technique-level).** The budget-inheritance, depth-degradation,
terminal-marker, and batched-subcall patterns are directly portable and improve OCC-1's
determinism story.

---

### 3.2 MOC — yao-guan/MOC (no license file, SHA 9c67c92507570704a7df73e452552a3f49e83897)

**Algorithm.** MOC ("Multi-Order Communication", Guan et al., PMLR v306 guan26i) augments an
AgentPrune-style spatial/temporal graph:
- Spatial edges: within-round information flow; temporal edges: cross-round memory flow
  (`Node.update_memory` stores last inputs/outputs; `get_temporal_info` reads predecessors'
  `last_memory`). Graph execution is a Kahn topological loop over `spatial_successors`
  (`Graph.run`/`Graph.arun` in `src/graph/graph.py`), repeated `num_rounds` times, then a decision
  node aggregates.
- Multi-order evidence: `get_neighbors_by_hops(node_id, max_hops)` BFS-collects k-hop
  *predecessors* into `{hop: [nodes]}` (graph.py:~560). The receiving agent therefore sees not
  just direct predecessors' outputs but multi-hop evidence, topologically ordered.
- Semantic-Topological Merging (ISM) — `iterative_semantic_merging_with_clustering` (graph.py:~600):

**ISM: exact inputs, outputs, order of operations.**
Inputs: `neighbors: List[(hop, id, role, message)]`, target count formula, `r`, `epsilon`,
`kppa` (compression %).
1. Compute the message-count budget: `max_items = len(neighbors)/neighbor_hops + r*neighbor_hops`
   (i.e., roughly one merged item per hop level plus slack). If `len(neighbors) <= max_items`,
   emit them verbatim (`"Agent {id}, role is {role}, output is:\n{message}"` joined by blank
   lines) and stop.
2. Phase 1 — encode: for each neighbor, embed content (`encode_text` -> sentence embedding,
   `src/llm/profile_embedding.py` -> OpenAI-style embedding API) into list `P` of
   `(content_embedding, hop, id, role, message, original_idx)`.
3. Phase 2 — loop while `len(P) > max_items`:
   a. Cosine similarity matrix over content embeddings; diagonal set to -1.
   b. Threshold = `max(sim) - epsilon`; all pairs (i<j) with `sim >= threshold` are candidates,
      sorted by similarity desc.
   c. Greedy disjoint-pair selection (an index may be used once per iteration).
   d. For each selected pair: merged_hop = min(hops); merged_id = "i & j"; merged_role =
      "role_i & role_j"; merged_content = `merge_multiple_messages([id_i, role_i, content_i,
      id_j, role_j, content_j], kppa)`; merged embedding = encode(merged_content).
   e. Re-sort by original index, reassign consecutive indices, iterate.
4. Output: the final `P` rendered as `"Agent {id}, role is {role}, output is:\n{content}"`
   joined by "\n\n" — the aggregated context string handed to the node's prompt.
5. `merge_multiple_messages` (graph.py:~380): builds a two-agent message set, fires **five**
   different summarization prompts (narrative synthesis / logical integrity / technical precision
   / telegram-style actionable / deduplication, each "target ~kppa% of original tokens, do not add
   information"), embeds both originals, embeds each output, picks the output with the highest
   summed cosine similarity to the two originals. Local `ollama` client with `gemma2:9b` at
   temperature 0.1 in the shipped code. Falls back to plain concatenation on failure.
- Topology learning: `Graph.arun` encodes the task text, runs a 2-layer GCN + MLP over role
  embeddings (`src/gnn/gcn.py`), and derives `spatial_logits = min_max_norm(logits @ logits.T)`;
  edges are Bernoulli-sampled per query with cycle checks (`check_cycle` DFS over
  spatial_successors), collecting log-probs for training. Pruning by `update_masks` (see 3.3 —
  AgentPrune's identical implementation).

**Claimed improvement and conditions.** Paper abstract: consistently improves task performance and
reduces communication costs across six datasets (MMLU, MMLU-Pro, AQuA, GSM8K, SVAMP, HumanEval —
test slices of 5-300 examples per `datasets/data_process.py` and `run_experiment.py` config) and
multiple LLM backbones. Costs tracked as `Cost`, `PromptTokens`, `CompletionTokens`, plus
`Conpressed*` counters for the merge calls (`src/utils/globals.py`). The merge step itself costs
LLM tokens (gemma2:9b via local ollama in code). Conditions: hosted or local LLMs, embedding API —
not offline-reproducible as shipped.

**Implementation complexity.** ~1,100 lines Python core (graph.py 784 + node.py ~230 + gcn.py);
deps: torch, numpy, scikit-learn, ollama, embedding API, sentence embeddings. Heavyweight for
what OCC needs.

**Applicability.** The ISM loop skeleton (count budget -> similarity merge -> ordered render)
fits OCC evidence consolidation: it is the only reference that frames consolidation as
"multi-hop evidence with explicit token budget and order preservation". But its merge core is an
LLM call, and its topology learner needs torch + training rollouts. For OCC, replace steps 3d
merge with deterministic merge (see 4.2) and skip the GCN entirely (family topology is fixed by
the daemon).

**Risks.** (1) No license — reimplement from the paper description, cite, do not copy code.
(2) Semantic-fidelity-by-embedding-selection is O(n^2) per iteration and embedding-API dependent.
(3) The five-prompt merge costs 5 LLM calls per merge — the paper's cost wins include this cost
against full-message baselines; a compact OCC core must not.

**Decision: EXPERIMENT.** Port ISM as `occ::evidence::consolidate` with a deterministic merge;
measure against plain concatenation in offline synthetic scenarios before any adoption.

---

### 3.3 AgentPrune — yanweiyue/AgentPrune (no license file, SHA c544dd6a1858c02c6d5d371d23c6e6ff55e0be21)

**Algorithm.** ("Cut the Crap", Zhang et al., ICLR'25, arXiv 2410.02506.) A multi-agent system
is a graph with two edge kinds:
- Spatial edges: which agents' current-round outputs flow to which agents within a round.
- Temporal edges: which agents' previous-round outputs (memory) persist to which agents next
  round.
Each potential edge has a learnable logit; per query, edges are Bernoulli-sampled from
`sigmoid(logit)` with DFS cycle checks (`construct_spatial_connection`,
`AgentPrune/graph/graph.py:~120`). Sampling collects joint log-probs
(sum over edges of `log p` if kept, `log(1-p)` if cut). Execution is Kahn topological
(`Graph.run`). Training (`experiments/run_gsm8k.py:main`): batch of tasks -> for each, a realized
copy of the graph runs -> utility = task correctness (1/0) -> loss = `-log_prob * utility`
(policy-gradient / REINFORCE-style, no critic) -> `optimizer.step()` on
`[spatial_logits, temporal_logits]` (Adam, lr 0.1). Every `imp_per_iterations` (default 5)
batches, `update_masks(pruning_rate)` (graph.py:~290) hard-prunes: rank live edges by logit, mask
the lowest `pruning_rate` fraction to 0 (at least 1). After `num_iterations` (default 10) the
graph is frozen and evaluated.

**Exact message-pruning policy.**
- Unit of pruning: an *edge* (a sender->receiver channel for one round-kind), not a message.
- Selection rule: lowest `sigmoid(logit)` first, logit trained by correctness-weighted
  log-probability ascent; already-pruned edges are excluded from ranking (masked to min-1).
- Cadence: periodic (every N training batches), rate-fractional (25% default), never exceeding
  available live edges; at least one edge pruned per call.
- Determinism note: at inference, kept edges are Bernoulli-*sampled* per query unless masks are
  fixed; the shipped experiments freeze after training.

**What must never be pruned (derived from code + paper, mapped to Optimus):**
- Decision aggregation: `connect_decision_node` wires every agent to the decision node *after*
  pruning; the final aggregation channel is structural, not learnable. In Optimus terms: the
  parent-result channel (child -> parent reply) must never be prunable; `assert_direct...`
  reachability and receipts in `agent_messages.rs` are the analogue.
- Termination and lifecycle channels: `Unaware of Termination Conditions` (MAST 1.5) shows
  stopping-condition messages are load-bearing; pruning them strands agents. In Optimus:
  `RlmChildTerminalClassification` markers (`rlm_continuation.rs:classify_rlm_child_terminal`),
  stop/steer interrupts, explicit_stop.rs and retained_stop.rs paths must be exempt.
- Coordination/synchronization messages: wait/submit-round tools (SILO-bench) and queue-capacity
  backpressure (`assert_agent_message_queue_capacity`) are protocol-level, not content-level;
  they carry no redundancy and must pass through.
- Verified-result messages: messages carrying receipts with valid acknowledgements
  (`rlm_result_receipt_is_valid`) have already paid commit-fence cost; pruning after commit
  breaks the commit fence.
- Error/failure reports: `adversarial_agent.py` shows injected-noise agents are pruned *away* in
  AgentPrune's own experiments, but genuine error signals (BudgetExceededError-style partial
  answers) must be delivered, or the parent loses its `_best_partial_answer`.
- System/meta messages: `is_agent_session_message` framing lines and `[from ...]` relationship
  headers (`parse_agent_session_message_prompt_id`) are parse-critical.

**Claimed improvement and conditions.** Paper (README + abstract): prunes redundant or malicious
messages, improving accuracy and efficiency; reported numbers (from the paper, not re-measured)
include up to 28.1%-72.8% token-cost reduction with accuracy gains on MMLU/GSM8K/HumanEval using
GPT-4o-class backbones, robustness to adversarial agents (`adversarial_agent.py`). Conditions:
hundreds of training rollouts against an accuracy oracle — impossible offline.

**Implementation complexity.** Core graph+node ~700 lines Python + torch. The *policy skeleton*
(masks, periodic fractional pruning, logit stats) without training is ~100-200 lines of Rust.

**Applicability.** Optimus transport is family-scoped with daemon-established sender identity,
which is exactly the trust model AgentPrune assumes (it defends against adversarial senders).
But OCC cannot run gradient training offline. Adoption path: a heuristic edge/mask policy —
per (sender, receiver, kind) slots with EWMA statistics of message usefulness (observed
downstream references, e.g. via semantic_edges.rs ledger), periodic fractional pruning with
never-prune exemptions above. That is "AgentPrune-style" without torch.

**Risks.** (1) No license. (2) Heuristic logits are unvalidated without a training oracle; wrong
prunes in agent systems are expensive (MAST 2.4 information withholding). (3) Bernoulli sampling
introduces nondeterminism that OCC-1 grammar tests would need to pin.

**Decision: EXPERIMENT (offline policy variant).** Implement the never-prune exemption list as
hard invariants now (testable offline); gate any actual pruning behind measured utility.

---

### 3.4 LatentMAS — Gen-Verse/LatentMAS (Apache-2.0, SHA 9a9e4d331eb11430bd9e64754c6b252b06d73031)

**Algorithm.** ("Latent Collaboration in Multi-Agent Systems", Zou et al., PMLR v306 zou26k /
arXiv 2511.20639.) Agents (Planner, Critic, Refiner, Judger — `methods/__init__.py:
default_agents`) collaborate without emitting text. Per agent in sequence:
1. Build the agent's chat prompt (`build_agent_message_{sequential,hierarchical}_latent_mas` in
   `prompts.py`).
2. `ModelWrapper.generate_latent_batch` (`models.py:generate_latent_batch`): a forward pass over
   the prompt tokens with `use_cache=True` yields `past_key_values` (the KV cache). Then, for
   `latent_steps` iterations: take the last-layer hidden state `h_t`, apply the latent-realignment
   transform, feed it back as a *one-token pseudo-input* via `inputs_embeds`, extend the KV cache,
   and repeat. The agent's "reasoning" is `latent_steps` of hidden-state autoregression with no
   emitted tokens.
3. The KV cache (working memory) is passed to the next agent, which prepends its own prompt to
   the shared cache. The final Judger decodes *text* from the accumulated latent memory
   (`generate_text_batch` with `past_key_values`).
4. Variants truncate the inherited cache to `latent_steps` tokens (`_truncate_past`,
   `latent_only` mode) to bound memory.

**Latent realignment.** `_apply_latent_realignment` (models.py): hidden-state dims are
recalibrated across model checkpoints by a precomputed matrix multiply + norm match
(`_ensure_latent_realign_matrix`), needed because vLLM and HF checkpoints of the same model can
have subtly different layernorm/embedding statistics. This is the concrete "training-free
alignment" the README claims.

**Precise technical requirements for TRUE latent communication** (from the code):
1. Same model instance (or bit-identical checkpoints) on both sides of every hop; vLLM + a second
   HF copy of the same model in the shipped config (`--use_second_HF_model`, two GPUs).
2. Tokenizer identity across agents (prompts are tokenized once; cache positions are absolute).
3. Direct tensor access: `past_key_values` and `hidden_states[-1]` must be writable/forwardable —
   i.e., in-process or shared-GPU-memory transfer of `torch.Tensor` objects. `enable_prompt_embeds`
   is required on the vLLM side to accept `inputs_embeds`.
4. Batch shape stability: cache tensors are sliced per `batch_size`; padding must match.
5. Cache-format compatibility: both legacy tuple caches and `transformers.cache_utils.Cache`
   handled by `_truncate_past`; version drift in transformers breaks this.

**Why hosted black-box APIs cannot support it:** they expose none of (1)-(3) — no KV export, no
`inputs_embeds`, no checkpoint control; the realignment matrix is checkpoint-specific; and
billing/pricing is per-token with no latent pathway. The daemon's family-scoped text transport
(agent_messages.rs) is fundamentally token-space.

**What a local-GPU prototype would need:** a single OSS model (e.g., Qwen3-8B/14B per their
logs) served by one process holding HF weights; agents as sequential cache-holders; ~24GB+ VRAM
for 8-14B in bf16; vLLM optional only if the HF twin is also resident; the realignment matrix
computation script; and a fallback text path for the Judger. Their example logs
(`example_logs/qwen3_14b_*.txt`) show the full trajectories.

**Claimed improvement and conditions.** Up to +14.6% accuracy, 70.8%-83.7% fewer output tokens,
4-4.3x faster end-to-end across 9 benchmarks (math/science/commonsense/code) vs text-based MAS
and single agents. Conditions: single open model, local GPUs, specific latency settings —
entirely model- and hardware-dependent.

**Implementation complexity.** ~1,100 lines Python (latent_mas.py 19.6KB, models.py 17.6KB) but
needs torch + vllm + transformers and 1-2 GPUs.

**Risks for OCC.** Fatal incompatibility with the campaign constraints (offline, no model APIs,
Rust daemon, family transport). Even as a prototype it invalidates OCC-1's text grammar and the
evidence-reference model.

**Decision: REJECT.** Document as the latent-space endgame that OCC explicitly does not pursue;
the honest boundary is "compact text contracts over latent-internal models", i.e., what OCC-1
already is.

---

### 3.5 MARBLE / MultiAgentBench — ulab-uiuc/MARBLE (MIT, SHA 8d60fa17b5596b44458a52d4296061b9fc13d6f2)

**Algorithm.** Engine-driven benchmark (`marble/engine/engine.py`, 53KB) with:
- **Topologies:** `marble/graph/agent_graph.py` — coordination modes 'parallel'/'hierarchical'/
  'cooperative'/'tree' from config; relationship triples (node1, node2, relationship) drive who
  may message whom.
- **Communication:** `BaseAgent.act` (marble/agent/base_agent.py) exposes tools to the model:
  `new_communication_session` / `communicate_to(target, message, session_id)`; `_handle_communicate_to`
  verifies a graph relationship exists, else returns an error dict — same spirit as Optimus's
  `assert_agent_family_reach`. Messages land in per-agent `msg_box[session][peer]` with
  FORWARD_TO/RECV_FROM tags; `seralize_message` renders the box into the prompt.
- **Memory:** `ShortTermMemory` (limit 10; when full, the two oldest entries are LLM-summarized
  into one `old_memory_summary` entry — a compacting queue), `LongTermMemory`, `SharedMemory`.
- **Evaluation:** `marble/evaluator/evaluator.py` — task_completion (environment-determined),
  token_consumption, LLM-judged `communication_score` and `planning_score` (rubric prompts in
  `evaluator_prompts.json`), KPI milestones (per-agent, from `milestone_prompt.yaml`), and
  `code_quality` (LLM-judged). Tasks: coding, database (SQL), research, Minecraft, Werewolf,
  world (multiagentbench/*.jsonl with per-task milestones).

**Claimed improvement and conditions.** MARBLE is a benchmark, not a method: it contrasts
coordination modes and reasoning paradigms (CoT/ReAct/Reflexion configs shipped). All scoring of
communication quality is LLM-as-judge at temperature 0 (deterministic-ish but model-dependent).

**Implementation complexity.** Large (engine 53KB + base_agent 38KB + werewolf_env 144KB); deps:
litellm, docker (Minecraft/db), Together/OpenAI APIs.

**Applicability.** Not as code (too heavy, online). Adapt three ideas: (a) relationship-checked
message tools mirror Optimus reach checks (validation for OCC-1's "wrong recipient" fault case);
(b) milestone/KPI decomposition maps to offline per-message assertions; (c) the
communication-score rubric (concise, relevant, non-redundant, timely) is a checklist OCC can
score deterministically for structured-grammar messages (presence of required fields, evidence
refs resolvable, no duplication of parent-visible data).

**Risks.** LLM-judge scores are not offline-reproducible; Minecraft/db environments need docker
and network.

**Decision: ADOPT (metrics ideas only).** Use its rubric and milestone structure as design
inputs for OCC's offline communication-fidelity suite.

---

### 3.6 SILO-bench — jwyjohn/acl26-silo-bench (Unlicense, SHA e74127782ed1c42fff474249961f022c063d76f2)

**Algorithm.** ("Silo-Bench", Zhang et al., arXiv 2603.01045.) Role-free coordination benchmark:
each of N agents holds a private data shard; agents get only protocol tools and must
self-organize.
- **Round engine** (`src/engine.py:run_round`): strictly lock-step. Round t: for each agent
  (sequentially, state in `rounds/round-t/agent-i/{state,context}.json`): load prior context,
  call the LLM, parse tool calls, execute them against `rounds/round-t/env/`, stop at
  round-ending tools (`wait`, `submit_result`). Messages/files written in round t are visible in
  round t+1 (copy-forward of `shared_kv.json` for SFS). Submitted agents are skipped with state
  copied forward. `init_case` seeds shards; `evaluate` scores.
- **Protocols** (`src/{msg,broadcast,sfs}/tools.py`): msg = `send_message, receive_messages,
  wait, submit_result`; broadcast adds `broadcast_message, list_agents`; sfs = `list_files,
  read_file, write_file, delete_file` over a shared key-value store.
- **Tasks** (`benchmark_generator/paradigm_{i,ii,iii}.py`): Level I Aggregation (global max, vote,
  checksum, top-k, ...), Level II Mesh (prefix sum, moving average, cellular automaton,
  boundary palindrome, ...), Level III Global Shuffle (distributed sort, median, union-find
  components, BFS distance, PageRank step, ...). Each task ships per-agent ground truth
  (`expected_output.per_agent_values`), optimal topology and message-count metadata
  (e.g., "1 message to collect + 1 to broadcast"), and verification logic.
- **Metrics** (`src/utils/metrics.py`): S (success rate, exact-match per agent),
  P (partial correctness, task-tailored, e.g., LIS-based for segmented tasks),
  C (output tokens per round), D (communication density = messages / (N(N-1))).

**Claimed finding.** "Communication-Reasoning Gap": agents exchange information actively but fail
to integrate distributed state; coordination overhead compounds with scale (2->100 agents).
Model-dependent; their environment harness itself is what OCC should borrow.

**Implementation complexity.** Engine ~700 lines; the whole src is ~2,500 lines pure Python +
pydantic. No ML deps. Public domain.

**Applicability — offline synthetic communication-fidelity scenarios.** Direct: the engine's
*shape* (per-agent state dirs, round barrier, tool-call grammar, per-agent ground truth) ports to
a Rust test harness where agents are deterministic fake models (scripted policy functions), so
S/P/C/D become purely transport-level measures:
- msg/broadcast/sfs over the daemon family transport with fault injection (3.7);
- ISM-consolidation quality (3.2): measure information retention after consolidation vs raw
  concatenation using SILO tasks with known aggregable answers;
- OCC-1 grammar conformance: tool calls become structured OCC-1 messages; S measures whether the
  message contents suffice for a deterministic solver to compute the expected output — a
  no-model-needed communication-fidelity oracle.
The 30 tasks with complexity metadata (O(N) vs O(N log N)) give a ready-made difficulty ladder.

**Risks.** Their agents are LLMs; replacing them with scripted policies tests transport, not
model behavior — keep the claim scoped to "communication fidelity", not "coordination ability".
Their n=100 JSONs are huge (one 54MB); generate fresh small instances with their generators
instead of committing theirs.

**Decision: ADOPT (offline harness ideas + task generators).** This is the highest-value
benchmark reference for OCC's offline test plan.

---

### 3.7 MAST — multi-agent-systems-failure-taxonomy/MAST (no license file, SHA a70542e541b2104ef8fcd785778179e173fb8d70)

**Algorithm/taxonomy** ("Why Do Multi-Agent LLM Systems Fail?", Cemri et al., arXiv 2503.13657;
repo = taxonomy definitions, example traces from AG2/AutoGen, HyperAgent, ChatDev, OpenManus,
AppWorld + an LLM-judge notebook). Fourteen failure modes in three categories
(`taxonomy_definitions_examples/definitions.txt`, confirmed by the judge prompt in
`llm_judge_pipeline.ipynb`):

1. Specification & Requirements
   - 1.1 Disobey Task Specification
   - 1.2 Disobey Role Specification
   - 1.3 Step Repetition
   - 1.4 Loss of Conversation History
   - 1.5 Unaware of Termination Conditions
2. Inter-Agent Misalignment
   - 2.1 Conversation Reset
   - 2.2 Fail to Ask for Clarification
   - 2.3 Task Derailment
   - 2.4 Information Withholding
   - 2.5 Ignored Other Agent's Input
   - 2.6 Action-Reasoning Mismatch
3. Task Verification & Validation
   - 3.1 Premature Termination
   - 3.2 No or Incorrect Verification
   - 3.3 Weak Verification

**Fault-injection test checklist for OCC (mapping to daemon transport).** Each MAST mode maps to
a testable transport/lifecycle fault; these are implementable offline against the daemon and the
RLM session code:

| MAST mode | OCC fault-injection test |
|---|---|
| 1.3 Step Repetition | Duplicate delivery of an agent_message (same id delivered twice); assert idempotent consumption via action-store lifecycle states (dispatch_delivery.rs). |
| 1.4 Loss of Conversation History | Drop a pending message (simulate lost delivery); assert receipts detect the gap and continuation state still terminates with a partial answer (rlm_continuation.rs best-partial path). |
| 1.5 Unaware of Termination Conditions | Withhold the child terminal marker; assert max-iteration / max-depth fallback fires (rlm_max_depth.rs). |
| 2.1 Conversation Reset | Restart/resume a session mid-delivery (daemon restart); assert queue replay reconstructs exact message order (action store ordering). |
| 2.2 Fail to Ask for Clarification | Ambiguous partial message; assert the family transport's error channel returns a resolvable ask-back rather than silent drop. |
| 2.4 Information Withholding | Message filtered by a pruning policy; assert never-prune classes (receipts, terminal markers, stop signals) survive (see 3.3 list). |
| 2.5 Ignored Other Agent's Input | Out-of-order delivery (deliver reply before request); assert ordering barrier or explicit sequencing IDs catch it. |
| 2.6 Action-Reasoning Mismatch | Corrupted message body (bit-flipped/truncated JSON); assert normalization rejects and requests re-send (normalize_agent_session_message). |
| 3.1 Premature Termination | Kill a child between admission and first output (rlm delete mid-run); assert parent gets a classified terminal, not a hang. |
| 3.2/3.3 Verification | Wrong-recipient delivery (sender spoofs target; daemon-established identity should refuse); weak ack (receipt without matching payload hash) must be rejected (rlm_result_receipt_is_valid). |

**Claimed improvement and conditions.** MAST is an empirical study: taxonomy derived from ~100k
annotated failure points across 7 frameworks, with inter-annotator agreement artifacts shipped.
The 1K+ annotated traces (MAD dataset on HuggingFace) are for LLM-judge work — not needed here.

**Implementation complexity.** Checklist only — no code to port; ~200-400 lines of Rust tests to
realize the table above.

**Risks.** No license on the repo; the *taxonomy categories* are from the paper (arXiv preprint,
standard arXiv license) — cite the paper, don't copy their text verbatim into code comments.

**Decision: ADOPT (as test checklist).** Highest ratio of value to implementation cost in this
report; grounds OCC fault injection in a published taxonomy.

---

### 3.8 CLSR / LSF — Pei et al., PMLR v306 pei26a (paper only; repo LSF_MDia does not exist)

**Algorithm.** CLSR (Communicative Language Symbolism Routing): agents invent and evolve
"Language Symbolism Frameworks" — each LSF is a reusable protocol with (a) compact symbols, (b)
usage rules, (c) a **message-passing contract**. Evolution loop: exemplar-driven induction ->
selection by correctness and token cost -> retention of effective LSFs across agents
(cultural-language-evolution framing). At inference, a "latent-free router" picks per query
between a single cheap LSF call, an ensemble of LSFs, or multi-round LSF composition, optimizing
accuracy vs generated tokens. The paper also derives an information-theoretic lower bound on
token cost and shows multi-round LSF protocols conditionally subsume program-execution pipelines.

**Claimed improvement and conditions.** 3-6x reduction in latency-oriented generated tokens vs
standard CoT with maintained accuracy, across knowledge QA, math, and multi-hop benchmarks with
Qwen3-8B/32B, LLaMA3-8B, DeepSeek-R1 as agents. Model-dependent; no code released
(`pzqpzq/LSF_MDia` 404).

**Applicability.** Conceptual validation for OCC-1: an LSF is essentially a typed
compact-communication contract with a symbol table — the OCC-1 grammar plus per-task schema
files are the same design. Their evolution loop suggests a harness-skill lifecycle for
grammar/schemas (test-time refinement recorded via `record_refinement`). The router idea maps to
Optimus choosing compact vs verbose message format per task (delta messaging vs full state).

**Risks.** Zero verifiable implementation; claims rest on hosted models; treating it as more
than design vocabulary would be unfaithful. The "interpreter-realizability premise" in their
theory is doing heavy lifting (the compact symbols only help if the receiving model reliably
interprets them — the same risk OCC-1 has with small local models).

**Decision: EXPERIMENT (design input only).** Cite as motivation for OCC-1's message-passing
contract; adopt nothing executable.

---

## 4. Extra-focus deliverables for live implementation

### 4.1 RLM techniques portable to the rlm_*.rs files and the REPL protocol

1. **Budget/timeout inheritance for subcalls** (`rlm/core/rlm.py:_subcall`): before spawning a
   child, subtract the parent's consumed budget and elapsed time; propagate child spend upward
   on `BudgetExceededError`. Port into `rlm_runtime.rs`'s spawn handler: the
   `RlmCreateSessionRequest`/`RlmRunRequest` path should carry remaining budget/timeout fields
   computed from the parent's ledger (semantic_edges.rs already records per-request spans to
   derive elapsed time), and `RlmDeleteSubagentResult`/child terminal paths should fold cost up.
2. **Depth-degradation rule** (`_subcall`, `_fallback_answer`): at `next_depth >= max_depth`, a
   subcall becomes a plain single completion, not an error. `rlm_max_depth.rs` should expose this
   as the canonical behavior (currently only status); `rlm_continuation.rs`'s
   `classify_rlm_child_terminal` then sees a normal terminal rather than a failure. This mirrors
   the reference exactly and removes a failure class.
3. **Consecutive-error circuit breaker with best-partial answer** (`_check_iteration_limits`,
   `_best_partial_answer`, `CancellationError(partial_answer=...)`): Optimus continuation
   messages should carry the last visible-text partial answer on limit exhaustion, so parents can
   degrade gracefully. `read_rlm_visible_text` (rlm_continuation.rs) is the right extraction
   point.
4. **Batched subcalls with per-slot error isolation** (`lm_handler.py:_handle_batched`,
   `comms_utils.py:send_lm_request_batched`): `asyncio.gather(return_exceptions=True)` ->
   per-prompt error completions; ordered, index-stable results. The `subagent` tool's `collect`
   (native_subagents.rs) plus `task_queue.rs`'s serialized-tail already give the queue; add an
   `rlm collect --wait`-style batched variant with a `max_concurrent_subcalls` semaphore scoped
   per session (the reference bounds globally per broker — per-session is the correct Optimus
   scope).
5. **Length-prefixed JSON framing** (`comms_utils.py:socket_send/recv`): 4-byte BE length +
   JSON. The Python REPL protocol (`prime-agent-runtime/src/rlm/repl.py:_handle_request_line`)
   can adopt the same framing for host requests if it currently line-delimits, eliminating
   newline-in-payload escaping bugs (verify current framing first; do not churn if already
   length-prefixed).
6. **Compaction with reachable full history** (`_compact_history` + `history` REPL variable):
   threshold-triggered summary; full history remains retrievable. For OCC: compaction summaries
   as first-class evidence references (the summary cites the turn range it covers), so OCC-1
   messages can reference "history[12..40]" without re-sending content. Fits
   `semantic_edges.rs`'s `begin_compaction`/`finish_compaction` events — attach evidence-ref
   metadata there.
7. **Terminal-answer interception via a write hook** (`_RLMAnswerDict.__setitem__`): the answer
   channel is a state mutation, not a print. The Rust-side analogue is a dedicated tool/schema
   (`answer` tool or OCC-1 RESULT message) whose invocation the session treats as terminal —
   `starts_agent_run`/`is_agent_session_message` in agent_messages.rs show the framing pattern.
8. **Depth-based model routing** (`LMHandler.get_client`): explicit model override first, then
   cheap-backend-for-depth-1, then default. `rlm_runtime.rs:find_rlm_model_matches` +
   `ScopedModelEntry` can implement "children default to a cheaper scoped model" as policy.

### 4.2 MOC ISM as a deterministic non-LLM core for OCC evidence consolidation

Spec (drops the LLM and the GCN; keeps the loop and the budget):

```
consolidate(messages: Vec<Evidence>, budget: usize, r: f32, epsilon: f32) -> String
  // Evidence = {sender_id, role, hop, content, refs, timestamp}
  1. max_items = len(messages)/HOPS + r*HOPS   // HOPS = family diameter (parent+siblings+children => typically 1-2)
  2. if len(messages) <= max_items: render all verbatim, ordered by (hop, timestamp, sender_id)
  3. else:
     fingerprints = hash-dedup by content hash first (exact duplicates + near-dupes by
                    normalized-token Jaccard >= 0.9 -> merge metadata, keep one)
     loop while len(P) > max_items:
       sim(i,j) = lexical overlap (token-idf cosine over message content; no embedding API)
       threshold = max(sim) - epsilon; pick disjoint argmax pairs
       merge(pair) = deterministic union:
         - refs = union(refs_i, refs_j)                       // evidence refs, deduped, order-stable
         - content = structural merge: key-value pairs of i then j, keys deduped by
                     normalized name; conflicts kept as "k: v1 | v2" (never dropped)
         - role = "role_i & role_j"; hop = min; sender = "id_i&id_j"
       stop if no pair meets threshold (emit with a truncation marker listing pruned senders)
  4. render: for each item, "Agent {id}, role is {role}, output is:\n{content}" + refs line
```
Determinism: no RNG, no LLM, idf computed over the message set itself. Fidelity oracle: on SILO
Level-I tasks (aggregations with known answers), a deterministic reducer over the rendered
context must produce the exact expected output — that is the offline acceptance test for the
merge policy (lossy merges break the reducer). Never-merge classes: messages carrying receipts
or terminal markers are excluded from merging (they must survive verbatim, consistent with 3.3).

### 4.3 AgentPrune: never-prune classes (testable invariants for OCC)

Hard invariants (implement as `assert`-level checks + tests, not policy):
1. Child->parent result replies (receipt-validated; `rlm_result_receipt_is_valid`).
2. Terminal markers and continuation-state messages (`classify_rlm_child_terminal` outputs).
3. Stop/steer/heartbeat and other lifecycle interrupts (explicit_stop.rs, native_heartbeat.rs).
4. Queue-capacity backpressure errors (`assert_agent_message_queue_capacity` failures).
5. Parse-critical framing ([from ...] headers, prompt-id lines) — these are part of transport,
   not payload.
6. Any message referenced by an unacknowledged receipt (commit-fence guarantee).
Policy surface (measure, then prune): sibling-to-sibling status chatter, repeated identical
content (dedupe), messages whose evidence refs are already parent-visible (delta rule: send
only the delta vs. parent-known state). Fractional periodic pruning with floor-at-1 and
exemption list is the AgentPrune skeleton; utility signal can come from semantic_edges.rs
(downstream requests that reference a message's content hash) instead of gradient training.

### 4.4 MAST -> OCC fault-injection checklist

See the table in 3.7 — that is the deliverable. Suggested suite naming: `occ_fault_injection/`
with one test per MAST mode ID (e.g., `t_mast_13_duplicate_delivery.rs`). All tests offline:
in-process daemon + scripted sessions, no model calls.

### 4.5 MARBLE / SILO-bench -> offline synthetic communication-fidelity scenarios

Design: a Rust test/bench crate `occ_scenarios/` implementing SILO's round engine shape:
- N scripted agents (deterministic policy functions, not models) over the daemon family
  transport; protocol tools msg/broadcast/sfs mapped to agent_message send + shared files.
- Generate task instances with a Rust port of SILO's paradigm generators (task list: global max,
  word frequency, vote, checksum, top-k, prefix sum, moving average, trapping rain, distributed
  sort, union-find components, BFS distance, PageRank step — 12 tasks spanning I/II/III suffice).
- Metrics S/P/C/D exactly as SILO defines (all computable without models: S and P from scripted
  submissions vs per-agent ground truth; C from token counts of serialized payloads; D from
  delivered-message counts).
- Scenario classes: (a) clean run (upper bound S=1), (b) ISM consolidation on/off (fidelity
  delta), (c) pruning on/off with never-prune violations as failures, (d) each MAST fault
  injected at a random round (robustness delta in S).
MARBLE contributes the rubric dimensions for the OCC-1 conformance linter: every message must
be concise (no duplicated parent-known content), relevant (evidence refs resolve), non-redundant
(no exact-duplicate content), and attributable (sender identity from the daemon, never
self-declared).

---

## 5. The five most transferable techniques (with exact file references)

1. **RLM budget/timeout inheritance + depth-degradation for subcalls** —
   `rlm/core/rlm.py` `_subcall` (child gets `max_budget - cumulative_cost`,
   `max_timeout - elapsed`; `next_depth >= max_depth` degrades to plain completion) and
   `completion` (per-iteration `_check_iteration_limits`, `_best_partial_answer`). Direct port
   target: `rlm_runtime.rs` spawn path + `rlm_max_depth.rs` + `rlm_continuation.rs`.
2. **Batched subcalls with per-slot error isolation and bounded concurrency** —
   `rlm/core/lm_handler.py` `LMRequestHandler._handle_batched` (asyncio gather +
   `return_exceptions=True`, semaphore) and `rlm/core/comms_utils.py`
   `send_lm_request_batched` (index-stable responses). Port target: `subagent` collect in
   `agent_session/native_subagents.rs` + `task_queue.rs`.
3. **SILO-bench's round engine + S/P/C/D metrics as an offline communication-fidelity harness** —
   `src/engine.py` (`run_round`, `init_case`, `evaluate`), `src/utils/metrics.py`
   (`compute_success_rate`, `compute_partial_correctness`, `compute_token_consumption`,
   `compute_communication_density`), `benchmark_generator/paradigm_{i,ii,iii}.py`. Port target:
   OCC offline scenario crate; public-domain license.
4. **MAST failure taxonomy as a fault-injection test matrix** —
   `taxonomy_definitions_examples/definitions.txt` (14 modes; 3 categories) +
   `llm_judge_pipeline.ipynb` (mode checklist). Port target: `occ_fault_injection/` suite
   against `dispatch_delivery.rs` / `agent_messages.rs` / `rlm_continuation.rs` (duplicate,
   lost, out-of-order, corruption, wrong-recipient, premature-termination, weak-ack cases).
5. **MOC ISM loop skeleton with a deterministic merge core** — `src/graph/graph.py`
   `iterative_semantic_merging_with_clustering` (budget K = |C|/hops + r*hops; greedy disjoint
   pairing at `max(sim)-epsilon`; order restoration by original index) with
   `merge_multiple_messages` replaced by the structural key-union merge of 4.2. Port target:
   OCC evidence consolidation; cites Guan et al. (no code reuse — unlicensed repo).

(Honorable mention: AgentPrune's never-prune exemption list, 3.3 — half technique, half test
plan; and RLM's compaction-with-reachable-history pattern for evidence references, 4.1 item 6.)

---

## 6. Open questions and honest limits

- No reference provides an offline-verifiable *accuracy* claim: every headline number in 3.1-3.4
  and 3.6-3.8 requires model calls. OCC tests must therefore assert transport properties
  (delivery, ordering, fidelity-of-consolidation, grammar conformance), not task accuracy, unless
  a local model is later added.
- MOC and AgentPrune ship no licenses; this report's recommendations are re-implementations from
  papers, with citation. Verify the papers' own license statements before any derivative
  distribution.
- The pzqpzq/LSF_MDia repo does not exist; if the campaign has a different intended source for
  the "machine dialectology" reference, the paper analyzed here (pei26a, CLSR) is the closest
  public match to the described topic.
- SILO-bench's engine executes agents sequentially within a round (deterministic order); the
  daemon's dispatch batches compatible family messages (`dispatch_delivery.rs:
  compatible_family_message_actions`). When porting scenarios, either keep sequential order or
  assert commutativity, or S becomes order-sensitive.
