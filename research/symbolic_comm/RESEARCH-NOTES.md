# OCC-2 Research Notes: Learned Symbolic Dialects for Optimus Agent Communication

Scope: research input for the OCC-2 track of the OCC-2 + OCC-3 campaign. Sources: the
CLSR paper (PDF), the MOC and AgentPrune repos (pinned SHAs), the OCC-1 system in this
worktree, and live probes of the local model gateway. No code changes. Paths are exact;
line numbers refer to the pinned SHAs in section 6. Key result: an OCC-2
dialect-discovery protocol (section 4) that runs offline against the gateway's
OpenAI-compatible chat endpoint, plus a heterogeneous model-pair test matrix and a
conservative fallback policy (section 5).

## 1. CLSR algorithm summary

Paper: Pei, Huang, Wang, "When LLMs Develop Languages: Symbolic Communication for Efficient
Multi-Agent Reasoning", ICML 2026, PMLR v306 (pei26a). Local PDF:
`C:\Users\openclawuser\optimus-rlm-references\pei26a_clsr.pdf` (36 pages).

### 1.1 What an LSF is

A Language Symbolism Framework (LSF) is a symbolic communication protocol with three
components (Sec 2.2): symbol naming (compact lexicon), syntax (compositional grammar /
output schema), and constraints (well-formedness and usage rules). Each LSF is a card
`Sk = (Vk, Gk, Rk, psi_k, rho_k)`: symbol inventory, grammar, usage rules, a
symbol-to-operation mapping, and an empirical profile (cost, accuracy, domains, failure
modes). Cards are 500-2000 tokens (Sec 4.2): text, but a reusable protocol object, not a
per-query instruction string.

### 1.2 How dialects emerge (evolutionary bootstrapping, Sec 2.3)

The training set is split into M disjoint groups G1..GM. Per generation t:
1. Generation: sample exemplars (dozens in context; 200-2000 total per benchmark,
   Sec 4.2) and prompt the LLM to "design an LSF based on the exemplars in the chat
   history to minimize the number of tokens while maintaining the reasoning capacity"
   (App. B). No manual editing.
2. Evaluation: run every LSF in pool S(t) on G(t+1) queries; record answers and
   generated-token counts.
3. Selection and mutation: pick "high-leverage" traces = correct AND token-efficient vs
   competing correct traces (Pareto: accuracy first, then minimal tokens). Feed them
   plus parent LSFs to the LLM: refine or recombine the parent protocol, keep
   conventions that support correct concise reasoning, remove redundant notation,
   repair ambiguous rules, add verification tags only when they improve reliability
   (App. B).
4. Repeat until validation saturates (5th evolution pool used by default, Sec 4.2).

Elitism: best LSFs are copied unchanged into the next generation; mutation and
recombination fill the rest (Sec 2.3). An "agent" is an independently sampled
black-box LLM instance (backbone, seed, exemplar subset, context); more agents = wider
search (A.5.3, Fig 3). Temperatures: 0.9 to generate LSFs (strict to soft), 0.3 for
inference (Sec 4.2).

### 1.3 Routing at test time (Sec 2.4-2.5, App. C)

Offline, each LSF's profile is distilled into a router card like `[ALG1] linear/eq |
perf:.81/42 | out:ans,brief | stop:conf>=0.6` (App. B). At test time a two-stage
latent-free LLM router: Stage 1 emits `C:<category-list>` to prune the pool; Stage 2
emits `M:<mode>;L:<lsf-list>;R:<round-spec>;K:<n>;A:<agg>;S:<stop>` with M:S single
LSF, M:A multi-LSF aggregation (majority vote), M:C multi-round implicit composition.
All generated tokens across rounds, LSFs, router, and aggregation count toward cost;
easy queries get T=1, hard queries T>1 rounds.

### 1.4 Cost model and theory

Cost C (Eq. 1, Sec 2.1) counts every online generated completion token; objective is
max expected correctness s.t. E[C] <= B. Theorem 3.2 (lower bound): E[|T|] >=
Ireq(x, delta)/kappa_theta(x): compression wins by raising per-token information, not
by beating the bound. Theorem 3.3: under an "interpreter-realizability" premise,
multi-round LSF protocols subsume program-execution pipelines. Sociolinguistic reading
(Sec 3.2): least effort (keep state-carrying symbols: bindings, subgoal markers,
verification tags); conventionalization (symbols survive by supporting reliable
inference); code-switching (router picks terse vs redundant protocols).

### 1.5 Key numbers

- Headline: 3-6x fewer generated tokens vs standard CoT at maintained accuracy
  (abstract; Table 1: roughly 3/4 reduction typical, e.g. Qwen3-8B MMLU-Pro 276 -> 96
  tokens, GPQA 1085 -> 228, GSM8K 243 -> 89, HotpotQA 134 -> 48, equal or better acc).
- Table 2 (Qwen3-8B): CLSR 92.8 acc / 90 tokens (GSM8K) vs PoT 92.1/113, PAL 92.5/148;
  T=3 ensemble 94.8/214 (GSM8K), 89.7/417 (MATH500): beats PoT/PAL without an executor.
- Table 3 (rounds): adaptive T (Tmax=3) is best and picks T=1 for most instances; fixed
  T=1 is cheapest but loses 2-6 accuracy points on hard sets (AIME 38.4 vs 44.7).
- Table 4 (cross-model transfer; the heterogeneity evidence, section 5): matched
  generator/inference diagonal is best; swapping cuts tokens but degrades accuracy;
  weaker generator + stronger inference model is worst; GPQA most sensitive.
- Ablations: deeper evolution helps with diminishing returns (A.5.1); larger batch
  avoids early collapse (A.5.2); more agents improve accuracy; aggressive
  length-coefficient pressure under-allocates reasoning on hard instances (A.5.3).
- A.8 cache-aware accounting C_eq = C_out + gamma_in*C_in_uncached +
  gamma_cache*C_in_cached: CLSR stays competitive (Qwen3-8B MMLU-Pro: 93 generated /
  121 cache-aware vs SoT 120/144). Compute: 8x RTX 4090, days of offline evolution,
  amortized over serving; online routing < 1 s (Sec 4.2).
- Stated limitations (Sec 5): offline evolution is expensive; needs exemplars with
  reasoning content; compact traces are less human-interpretable (their mitigation:
  decode with a second LLM).

## 2. Offline implementability with an OpenAI-compatible API

Everything CLSR does is black-box prompting plus deterministic bookkeeping. The gateway
at http://127.0.0.1:43120 (health 200 verified 2026-10-16; probes in section 5)
provides the interface and real tokenizer usage (`usage.prompt_tokens`,
`usage.completion_tokens`).

| CLSR component | Offline? | Notes |
|---|---|---|
| LSF synthesis from exemplars | YES | one completion per candidate, temp 0.9 |
| Evaluation rollouts | YES | low temp; score vs ground truth offline |
| High-leverage selection | YES | pure code: correctness + token Pareto |
| Mutation / recombination | YES | one completion per child |
| Profile summary + router-card distillation | YES | one completion per LSF |
| Category + protocol routing | YES, replaceable | deterministic selector viable for OCC |

Pseudocode for the offline discovery loop (OCC-2 adapted):

```text
INPUT:  corpus of typed OCC contract messages with known content
        split EVOLVE / DEV / HOLDOUT (never tune on HOLDOUT)
        speaker Ms, listener Ml (section 5)
        hyper: POP=8 candidates, GEN=4 generations, temp_gen=0.9

pool = []
for k in 1..POP:                        # generation 1
    card = Ms.chat(SYNTHESIS_PROMPT(exemplars), temperature=0.9)
    pool.append(parse_dialect_card(card))      # reject bad structure

for g in 1..GEN:
    rollouts = []
    for dialect in pool:
        for msg in EVOLVE_batch_g:              # fresh group per gen
            enc = speaker_encode(Ms, msg, dialect)   # chat call
            dec = listener_decode(Ml, enc, dialect)  # chat call
            fidelity = contract_equal(dec, msg)      # deterministic
            rollouts.append((dialect, msg, enc.usage, fidelity))
    elites = top by (fidelity, -median_tokens)        # elitism
    parents = pareto_filter(rollouts)   # correct-and-cheap first
    children = [Ms.chat(MUTATION_PROMPT(parents, traces), 0.9)
                for _ in range(POP - len(elites))]
    pool = elites + parse_all(children)  # recombination = two parents
    if dev_metric(pool) saturated: break

for dialect in pool:                    # CLSR App. B profile prompt
    descriptor = Ms.chat(SUMMARIZE_PROMPT(rollouts_of_dialect))
    card = distill_router_card(descriptor)
return pool with cards, profiles, lineage
```

Fidelity oracle (the OCC-specific replacement for CLSR's answer-equality check): decode
must reproduce the typed contract payload exactly (modulo documented normalizations);
stronger than answer equality, needs no labels beyond the corpus.

Two CLSR mechanisms should NOT be ported as-is: T>1 multi-round composition buys
accuracy on hard reasoning (Table 3) but multiplies online tokens, and OCC messages are
short structured payloads, not deliberation traces (keep T=1 for v1); and the LLM
router exists because CLSR pools mix protocols across domains, whereas an OCC-2 pool
per task family with a deterministic selector (lowest median tokens subject to a
fidelity floor) is simpler and auditable.

Measurement caveat from live probes (2026-10-16): gateway `usage.completion_tokens`
includes hidden reasoning tokens (glm-5.3-flash: content "ok" with completion_tokens=92;
glm-5.2: "2" with 95). Record BOTH gateway completion tokens (cost/latency proxy, CLSR
Eq. 1 semantics) AND visible wire tokens of the encoded message, tokenized with the
listener model's tokenizer. Report separately; never sum.

## 3. MOC and AgentPrune: communication improvement vs message shortening

### 3.1 MOC (yao-guan/MOC @ 9c67c925, no license file)

Multi-order = two edge classes: spatial (within-round DAG) and temporal (cross-round
memory). Evidence: `MOC\src\graph\graph.py:233-256` (construct_spatial_connection,
edge sampling with cycle checks), `graph.py:258-282` (construct_temporal_connection;
round 0 adds no temporal edges, lines 261-262), per-round loop `graph.py:291-315`
(build both edge sets, topological execution, update_memory), and
`MOC\src\graph\node.py:113-142` (spatial = predecessors' last output; temporal =
previous-round output from last_memory).

What genuinely improves information flow:
1. Multi-hop context access: `graph.py:407-446` (get_neighbors_by_hops) collects k-hop
   predecessors in topological order; `graph.py:722-777` feeds them to the merge.
   Removes information loss from 1-hop-only visibility; topology, not compression.
2. ISM (Iterative Semantic Merging) has two fidelity safeguards around its compression:
   - Best-of-5 by similarity to sources: `graph.py:494-534` defines five merge
     strategies (narrative synthesis, logical integrity, technical precision,
     telegram-style actionable, dedup+structure), all constrained "Do not add new
     information"; `graph.py:555-581` embeds originals with all-MiniLM-L6-v2
     (`MOC\src\llm\profile_embedding.py`) and keeps the highest-cosine candidate.
   - Budget with structure preservation: `graph.py:615` sets target count K =
     |C|/neighbor_hops + r*neighbor_hops; merging pairs must exceed max_similarity -
     epsilon (`graph.py:645-653`), are disjoint (`graph.py:663-675`), keep hop=min and
     index order (`graph.py:683-684,706`), roles concatenated not dropped
     (`graph.py:686`); stops when count fits the budget (`graph.py:636,659-661`). Target
     is kppa=45 percent compression (`graph.py:481`), while trying to preserve distinct
     contributions, logical dependencies (strategy 2, `graph.py:504-509`) and technical
     values (strategy 3, `graph.py:512-517`).
3. Failure containment: on LLM merge failure ISM falls back to concatenation, never
   drops messages (`graph.py:583-585`).

What is only shortening or incidental: the 45 percent target is arbitrary; cosine
similarity to originals is a weak oracle (verbatim copies score 1.0, defeating the
purpose); the distiller is a hardcoded local `gemma2:9b` ollama model (`graph.py:541`);
and the topology modes (FullConnected / Random / Chain,
`MOC\experiments\run_experiment.py:113,158-184`) are plumbing.

OCC-2 relevance: adopt the structure (hop-ordered aggregation, never-drop-on-failure
fallback, similarity-guarded merging); reject embedding similarity as the only fidelity
check: OCC-2's round-trip contract equality (section 2) is strictly stronger and
deterministic. `occ::consolidation` already carries this lesson's deterministic variant
(mod.rs campaign rules).

### 3.2 AgentPrune (yanweiyue/AgentPrune @ c544dd6a, no license file)

Trainable communication graph: edges are Bernoulli variables with learnable logits,
trained by REINFORCE on task utility, with periodic magnitude pruning. Evidence:
`AgentPrune\AgentPrune\graph\graph.py:169-218` (edge sampling by sigmoid(logits/temp),
cycle prevention), `AgentPrune\experiments\train_mmlu.py:29` (Adam over exactly
`[graph.spatial_logits, graph.temporal_logits]`), `train_mmlu.py:56-59` (loss =
mean(-log_prob * utility), utility = 0/1 accuracy: policy gradient on edge choices),
`train_mmlu.py:76-77` (periodic update_masks(pruning_rate)), `graph.py:317-338` (prune
round(num_edges*rate) edges, floored at 1, by lowest logit).

What genuinely improves communication:
1. It deletes redundant EDGES, not message content: remaining nodes exchange full
   outputs (`AgentPrune\AgentPrune\graph\node.py:114-130`,
   `AgentPrune\AgentPrune\agents\analyze_agent.py:24-45`, prompts concatenating full predecessor
   outputs). Fidelity per kept edge is 100 percent: "fewer messages", not "shorter
   messages".
2. The utility signal is end-to-end task accuracy: an edge survives only if removing
   it would hurt answers on the training batch. A real measurement of communication
   value; exactly the selection pressure OCC-2 needs for symbol retention.
3. Robustness: adversarial agents ("You are a liar who only tells lies.",
   `AgentPrune\AgentPrune\agents\adversarial_agent.py:16-18`) let training drop malicious edges;
   headline gains include accuracy recovered from pruning adversarial noise.

What is only shortening or incidental: no message compression exists anywhere in the
codebase (edge pruning is the only token saving); REINFORCE needs hundreds of labeled
rollouts with an accuracy oracle, which is why the offline campaign adopted only the
never-prune exemptions and the pruning skeleton (FINAL-REPORT.md, sections A and J);
and the spatial/temporal split is the same topology plumbing as MOC (shared GDesigner
lineage; both READMEs acknowledge it).

Verdict: AgentPrune contributes the selection-signal design (utility-driven retention,
periodic fractional pruning with floor) and the never-suppress safety classes (already in
`occ::prune`). It contributes nothing to dialect design; its nodes are compression-free.

## 4. OCC-2 dialect discovery protocol for Optimus

Design constraint: OCC levels are interchangeable encodings of ONE typed payload
(`crates\pi-coding-agent\src\core\occ\mod.rs` module docs, lines 5-14). An OCC-2 dialect is
therefore a learned compression layer over the `optimus.agent-comm.v1` contract
(`crates\pi-coding-agent\src\core\occ\contract.rs:22`), not a new contract. Decoded output
must be contract-equal or the dialect is invalid.

### 4.1 Candidate grammar representation

A dialect card (versioned, checksummed, plain text):

```text
DIALECT/1
ID=<family>-<gen>-<n>            e.g. taskcard-g3-07
LINEAGE=<parents> CHECKSUM=<sha256 over card body>
# 1. Key aliases: contract field key -> dialect key (1-2 chars)
KA=K:TASK->t K:KIND->k K:STATUS->s K:SEQ->q ...
# 2. Value symbol tables (enum domains only)
VS=STATUS:pending->0 in_progress->1 done->2 partial->3 blocked->4 failed->5 cancelled->6
VS=KIND:TASK->T PROGRESS->P RESULT->R EVIDENCE->E ...
VS=VERIFY:pass->+ fail->- unknown->?
# 3. Microtemplates for structured values (regex -> compact form)
MT=PATH:src/->@ MT=LINES:L{n}-{m}->{n}~{m} MT=DIGEST:sha256:{40hex}->#{8}
# 4. Field defaults (elided field == default on decode)
FD=K:CONF=unknown FD=K:DELIVERY=accepted
# 5. Usage rules (free text for models; machine-checked invariants below)
UR=... ordered EVIDENCE blocks stay positional; contradictions never elided ...
# 6. Never-compress classes (hard, mirrors occ::prune never-suppress)
NC=BLOCKED,CANCELLED,FAILED,CORRECTION,ACK,verification=fail,contradictions
```

Deterministic invariants, checked before any model call:
- Alias targets must be legal OCC-1 keys (`[A-Z][A-Z0-9_]*`, bounds per
  `occ1.rs:12-24,27-31`) or short lowercase keys inside the dialect envelope; the
  envelope keeps the OCC/1-style line grammar so decoders can always fall back to
  OCC-1 per field.
- Never-compress classes must round-trip verbatim (section 3.2 lesson).
- Enum tables must be total over the contract's closed enums (kinds, statuses,
  verification, delivery, evidence kinds; lists in `occ1.rs:51-69` and the
  `contract.rs` kinds/statuses/delivery/evidence_kinds/verification modules).
- No two aliases map to one key; no symbol collides with a value that can appear
  free-form.

### 4.2 Mutation operators

Deterministic (cheap): K-alias shortening (2+ char alias -> unused 1-char key); symbol
pruning (drop symbols unused in the last generation's rollouts); default induction
(promote the most frequent value of a field to FD); microtemplate induction (mine
repeated structural patterns in values: file paths, line ranges, hex digests, URLs;
add an MT rule); template removal (retire an MT rule whose decode ever mismatches).

Model-assisted (one gateway call each; CLSR mutation prompt adapted): recombine two
parent dialects (union of tables with model-suggested conflict resolution, then
deterministic validation); repair ambiguous usage rules from failure traces; add
verification tags (a CLSR-observed surviving convention, Sec 3.2) only when the fidelity
oracle shows a failure mode they fix.

### 4.3 Speaker / listener test procedure

Per dialect, per corpus message, per model pair (Ms speaker, Ml listener):
1. Encode: Ms sees the dialect card plus the typed message and emits the dialect-encoded
   message. Deterministic parts may be pre-encoded programmatically; free-text fields
   (statements, reasons) must go through the model to measure real compression.
2. Decode: Ml sees the dialect card plus the encoded message and reconstructs the
   typed contract payload.
3. Fidelity check (deterministic, offline): decoded == source contract, modulo
   documented normalizations (whitespace, default fields, symbolized enums). Any
   evidence loss, status change, or dropped finding is a hard failure; contradictions
   and never-compress classes must match byte-for-byte.
4. Comprehension check (model-based, sampled): Ml answers k objective questions about
   the decoded content (which findings, which evidence locators, which status); guards
   against syntactically clean but semantically scrambled decodes.
5. Metrics per message: wire tokens (Ml tokenizer over encoded text), gateway
   completion tokens for both calls, wall-clock latency, fidelity, comprehension.

### 4.4 Pareto frontier and selection

Objectives per dialect: (fidelity rate on DEV, median wire tokens, p95 latency,
comprehension score). Selection (CLSR's Pareto criterion adapted): among dialects with
fidelity >= FLOOR (propose 1.0 on DEV; any failure disqualifies), rank by median wire
tokens, tie-break by p95 latency. Report the full frontier, not just the winner: CLSR
Table 3 shows the cheap end sacrifices hard-case accuracy, so publish both the cheapest
passing dialect and a conservative one (fewer elisions, more defaults explicit).

### 4.5 Holdout discipline

- Three disjoint corpus splits: EVOLVE (evolution rollouts, fresh group per
  generation, CLSR Sec 2.3), DEV (selection and saturation), HOLDOUT (final report
  only). The pool and all hyperparameters freeze before the first HOLDOUT call.
  One-strike rule: any HOLDOUT-driven change re-randomizes HOLDOUT and is logged as
  a protocol deviation.
- Every HOLDOUT run is logged: dialect checksum, model pair, date, purpose, results.
  The log is part of the deliverable.
- Baselines on the same HOLDOUT split: OCC-0 English prose, OCC-1 standard encoding,
  JSON of the typed payload. A dialect ships only if it beats OCC-1 on wire tokens at
  equal fidelity and comprehension.

### 4.6 Dialect card fields (deployed artifact)

Section 4.1's card plus, after evaluation: generation and lineage (parents, operator
chain), per-split metrics (fidelity, median/p95 wire tokens, latency, comprehension),
failure-mode notes, compatibility matrix (section 5), validation date, gateway build
(health `buildId`), invariant check results. Cards are versioned; deployment pins a
checksum. Record evolution via the harness refinement ledger
(`rlm.harness.record_refinement`), as FINAL-REPORT.md section J suggests.

## 5. Heterogeneous model-pair testing plan

Motivation from CLSR Table 4 (Sec A.5.5): matched generator/inference diagonal is best
in accuracy; swapping reduces tokens but degrades accuracy; weaker generator + stronger
inference model is the worst combination (Qwen3-4B generator on Qwen3-8B inference:
GPQA 42.1 vs 47.7 matched); GPQA is most sensitive. Port to OCC-2: a dialect evolved by
model A may be misread by model B; the failure mode is information loss per token, not
more tokens.

Gateway status probed 2026-10-16 (health 200, build
optimus-gateway-monitoring-repair-20260918.1): working via /ollama/v1/chat/completions
are glm-5.3, glm-5.3-flash, glm-5.2, deepseek-v4-pro, deepseek-v4.1-flash.
deepseek-v4-flash retired 2026-09-25 (HTTP 410, "Try glm-5.3-flash"): model lists go
stale, so re-probe /health before each session. FW-Kimi-K3, FW-GLM-5.3, gpt-5.6-sol,
gpt-6-astra return HTTP 503 (Azure auth temporarily unavailable); optional until the
azure login helper is rerun.

Test matrix (both directions; same-model cells are controls):

| Pair | Speaker | Listener | Family | Purpose |
|---|---|---|---|---|
| C1 | glm-5.3 | glm-5.3 | same | control: ceiling fidelity |
| C2 | glm-5.3-flash | glm-5.3-flash | same | control: cheap model |
| S1 | glm-5.3 | glm-5.3-flash | big->small | strong writer, weak reader |
| S2 | glm-5.3-flash | glm-5.3 | small->big | CLSR worst case analog |
| S3 | glm-5.2 | glm-5.3 | cross-version | version drift |
| S4 | deepseek-v4-pro | deepseek-v4.1-flash | cross-version | version drift |
| X1 | glm-5.3 | deepseek-v4-pro | cross | family boundary |
| X2 | deepseek-v4-pro | glm-5.3 | cross | reverse |
| X3 | glm-5.3-flash | deepseek-v4.1-flash | cross, cheap | budget tier |
| X4 | deepseek-v4.1-flash | glm-5.3-flash | cross, cheap | reverse |
| A1+ | Azure-routed models | each vs glm-5.3, deepseek-v4-pro | cross-vendor | optional, post-auth |

Protocol per cell: evolve the dialect with the speaker (CLSR's recommendation);
evaluate on DEV with that pair; run the frozen dialect on HOLDOUT in both directions
(evolved by A: spoken by A, decoded by B; and spoken by B, decoded by A). Record the
compatibility matrix on the card (4.6). Expected from CLSR Table 4: same-family cells
lose little; cross-family lose more fidelity; small-speaker -> big-listener is the
risk cell.

Conservative fallback policy (three-tier ladder):
1. Default runtime encoding is OCC-1 (per mod.rs).
2. OCC-2 is used for a (speaker, listener, task-family) triple ONLY if its card shows
   HOLDOUT fidelity 1.0 and comprehension >= OCC-1 baseline for that exact triple;
   otherwise OCC-1.
3. On any runtime decode failure, fidelity miss, or double-decode disagreement
   (decode twice at temperature 0), re-send in OCC-1 and log; three incidents in a
   session disable OCC-2 for that pair for the session (AgentPrune-style floor: never
   lose the message, only the compression). Fallback is never silent: incidents update
   the dialect card's failure log and feed the next mutation generation (CLSR mutation
   prompt uses failure traces this way, App. B).

## 6. Reference table

| Source | Location | Pin | License | Role here |
|---|---|---|---|---|
| CLSR paper (Pei et al., ICML 2026, PMLR v306 pei26a) | `C:\Users\openclawuser\optimus-rlm-references\pei26a_clsr.pdf` | PMLR v306 pei26a PDF | paper; PMLR open access | primary algorithm source |
| CLSR code LSF_MDia | https://github.com/pzqpzq/LSF_MDia | UNAVAILABLE | - | verified 404 on 2026-10-16 (web + API; user pzqpzq also 404, no public account). Paper footnote 1 promises code; not delivered. Nothing to pin or clone. |
| MOC (Guan et al., arXiv 2606.02359) | https://github.com/yao-guan/MOC (local: `C:\Users\openclawuser\optimus-rlm-references\MOC`) | 9c67c92507570704a7df73e452552a3f49e83897 (2026-06-06) | NO license file: all-rights-reserved; ideas only, no code copying | ISM merge structure, hop aggregation, fallback discipline |
| AgentPrune (Zhang et al., ICLR'25, arXiv 2410.02506) | https://github.com/yanweiyue/AgentPrune (local: `C:\Users\openclawuser\optimus-rlm-references\AgentPrune`) | c544dd6a1858c02c6d5d371d23c6e6ff55e0be21 (2025-03-23) | NO license file: all-rights-reserved; ideas only | utility-driven selection, never-prune classes, pruning floor |
| OCC-1 system | `crates\pi-coding-agent\src\core\occ\{mod,occ1,contract}.rs` | this worktree (base ce8480d59 = v0.1.32) | repo license | the contract OCC-2 must round-trip |
| OCC campaign report | `reports\occ-campaign\FINAL-REPORT.md` | this worktree | repo license | section A decisions, section J open items |
| Prior landscape report | `docs\research\agent-communication-landscape.md` | this worktree | repo license | cross-reference; CLSR summary in its 3.8 |
| Model gateway | http://127.0.0.1:43120 (local) | build optimus-gateway-monitoring-repair-20260918.1, probed 2026-10-16 | internal | speaker/listener models; real usage token counts |

License handling: MOC and AgentPrune carry no license file; this document describes
their mechanisms with citations and recommends re-implementation from these descriptions
(as the offline campaign did for `occ::prune` and `occ::consolidation`), with no code
copying. CLSR has no released code at all.
