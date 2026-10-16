# OCC-3 Research Notes: KV-Cache Handoff and Latent Continuation

Notes for the OCC-3 track (latent communication between compatible local models). No new harness
code here: this records mechanisms, API facts, and the experiment design. A companion harness
(another worker) already exists in `research/latent_handoff/`; its status and a diagnosis of
first results are in 4.0. Author: occ3-research worker, 2026-10-16, branch
improve/occ2-occ3-communication (base ce8480d59 = v0.1.32).

## 1. LatentMAS: exact mechanism

Gen-Verse/LatentMAS pinned at 9a9e4d331eb11430bd9e64754c6b252b06d73031, clone
`C:\Users\openclawuser\optimus-rlm-references\LatentMAS`. Paper: arXiv 2511.20639 (ICML 2026
spotlight). Apache-2.0. Citations `file:line` in that clone; prefixes: `LM:`
methods/latent_mas.py, `M:` models.py, `R:` run.py, `P:` prompts.py, `MI:` methods/__init__.py.

### 1.1 Pipeline and configuration

- Agent chain Planner -> Critic -> Refiner -> Judger (`MI:11-17`). First three produce only
  latent state; only the Judger decodes text (`LM:109`, `LM:156`).
- Fresh chat-template prompt per agent (`LM:93-102`, `M:87-100`). Critic is told the plan
  arrives "in latent KV representation format" (`P:23`); Judger has "latent information for
  reference" that "might contain irrelevant contents" (`P:53-55`).
- `--think` appends the literal `<think` (Qwen3 thinking opener) to each prompt (`LM:112-115`,
  `LM:160-163`), so latent steps continue the thinking segment without emitting text.
- Defaults: latent_steps=10 in the class (`LM:22`) but CLI default 0 (`R:102`); temperature 0.6,
  top_p 0.95 (`R:103-104`); README: tune latent_steps in [0, 80], treat `--latent_space_realign`
  as a hyperparameter (README.md:218-224).
- `sequential_info_only` / `latent_only` exist in code but not in run.py argparse (`LM:40-44`);
  only reachable programmatically (1.4).

### 1.2 generate_latent_batch: the latent step loop (HF backend)

`M:277-350`, called at `LM:130-135` with prompt ids, mask, latent_steps, and the accumulated
`past_key_values`.

1. Prompt forward `model(input_ids, attention_mask, past_key_values, use_cache=True,
   output_hidden_states=True)` (`M:303-310`); with a past cache the mask is `ones([B,
   past_len])` ++ prompt mask (`M:293-301`).
2. `e_t = hidden_states[0][:, -1, :]` ([B, D], embedding output of last token) and `last_hidden
   = hidden_states[-1][:, -1, :]` ([B, D], post-final-norm hidden of last token) (`M:313-315`).
   `e_t` and the local `latent_vecs_all` (`M:318-319`, `M:326`) are recorded but never returned:
   dead bookkeeping here.
3. Latent loop, `latent_steps` iterations (`M:321-348`):
   a. `latent_vec = _apply_latent_realignment(last_hidden, model)` ([B, D], `M:324`, see 1.3).
   b. `latent_embed = latent_vec.unsqueeze(1)` -> [B, 1, D] (`M:331`).
   c. `latent_mask = ones([B, past_len + 1])` (`M:333-338`).
   d. `model(inputs_embeds=latent_embed, attention_mask=latent_mask, past_key_values=past,
      use_cache=True, output_hidden_states=True)` (`M:339-346`): the cache grows one position
      per step; the latent vector is consumed as one pseudo-token.
   e. `last_hidden = hidden_states[-1][:, -1, :]` (`M:348`): the next latent input is the fresh
      final hidden state, a true feedback recurrence.
4. Returns only the extended cache (`M:350`). No `cache_position` is passed; positions derive
   from the cache length (also true in v5, 3.3).

### 1.3 Hidden-state-to-embedding transformation

`_apply_latent_realignment` (`M:204-213`) + `_build_latent_realign_matrix` (`M:158-185`):

- Ridge least-squares map from final-hidden space into the input-embedding row space: `M =
  solve(W_out^T W_out + 1e-5 I, W_out^T W_in)` (`M:172-176`), W_in = input embedding weight,
  W_out = lm_head weight. With tied weights (all Qwen3) `W_out = W_in`, so `M ~ I`.
- Default path (no realign flag): `M = I` explicitly (`M:179-183`); the default transform is
  only a norm rescale.
- Applied: `aligned = hidden_fp32 @ M`, then row-wise rescale to `target_norm = mean ||W_in
  row||_2` (`M:207-212`), cast back. In short: feed back the final hidden state renormalized to
  the average input-embedding row norm (optionally through the ridge map first).

### 1.4 Cache truncation modes (memory policy)

After each non-judger agent (`LM:136-140`), with prev/new the cache lengths before/after
(`_past_length`, `M:23-27`):

- Default: no truncation; the cache accumulates every agent's prompt tokens plus latent steps —
  the paper's "shared latent working memory".
- `sequential_info_only`: keep the last `new - prev` positions (`LM:138-139`) = the previous
  agent's prompt + latents; earlier agents dropped.
- `latent_only` (implies the above): keep the last `latent_steps` positions (`LM:139-140`): each
  agent sees only the previous agent's latents.

`_truncate_past` (`LM:61-79`) converts a `Cache` to legacy tuples, slices the last k positions
along dim -2 (`LM:53-59`), rebuilds via `from_legacy_cache`. 4.x-only API; breaks on v5 (3.5).

### 1.5 Receiver decoding (HF path)

Judger branch (`LM:156-199`) calls `generate_text_batch` (`M:216-267`): `cache_position =
arange(past_len, past_len + L_judger)` (`M:232-239`), `attention_mask = ones(past_len) ++ judger
mask` (`M:240-246`), passed to `model.generate(...)` with the accumulated cache (`M:247-259`).
The judger prompt is appended after all latent positions; decode with `skip_special_tokens=True`
(`M:260-267`). If `latent_steps == 0` the cache is dropped (`LM:158`).

### 1.6 vLLM path: embedding-sequence insertion

`run_batch_vllm` (`LM:251-438`) uses `generate_latent_batch_hidden_state` (`M:352-415`): same
loop, but also returns `cat([hidden_states[0], latent_embed_1, ...], dim=1)` of shape [B, L +
latent_steps, D] — prompt input embeddings then latent vectors, all in embedding space
(`M:387-415`).

- Per agent that [B, L_i + k, D] tensor is recorded (`LM:318`); `sequential_info_only` keeps
  only the last agent's record (`LM:320-321`); `latent_only` slices the last `latent_steps`
  vectors (`LM:312-316`).
- At the Judger: records concatenated (`LM:340`); judger prompt embedded via
  `get_input_embeddings()` (`LM:353-355`); the past embedding block is spliced into the judger
  embedding sequence right after the `<|im_start|>user\n` tokens (`LM:361-377`); the combined
  tensor goes to `vllm_engine.generate(prompt_embeds_list, ...)` (`LM:394-404`). Needs a
  modified vLLM started with `enable_prompt_embeds=True` (`M:49-50`); README: HF backend
  reproduces paper numbers.

### 1.7 Reported results (textual sources only; tables are images)

- Abstract: across 9 benchmarks up to +14.6% accuracy over single-agent and text-MAS baselines,
  70.8%-83.7% fewer output tokens, 4x-4.3x faster end-to-end; training-free.
- Shipped logs (Qwen3-14B, HF backend, seed 42, temperature 0.6, latent_steps=10, think=True):
  HumanEval+ hierarchical 84.76% (139/164)
  (example_logs/qwen3_14b_humanevalplus_hierarchical.txt, final JSON); MBPP+ sequential 76.19%
  (288/378) (example_logs/qwen3_14b_mbppplus_sequential.txt).
- Models: Qwen3-4B/8B/14B only (`R:90-92`); prompts assert Qwen (`P:7`).

## 2. Interlat: trained latent communication

XiaoDu-flying/Interlat cloned to
`C:\Users\openclawuser\optimus-communication-references\Interlat`, pinned at
66a89cb4d4097b2f86cbe48ed9851d6e8578f821 (2026-06-16). Paper: arXiv 2511.09149 (ACL 2026).
Apache-2.0. Prefixes: `CM:` core_training/hidden_model/custom_model.py, `HA:`
eval/alfworld/eval_agent/agents/hidden_agent.py, `BD:` data_collection/base_data_collector.py,
`EM:` eval/alfworld/eval_agent/main.py, `CP:` compression_training/compress.py.

### 2.1 What transfers

- Sender: temporally aligned last-layer hidden states, one vector per generated token.
  `generate_with_hidden_states` runs `model.generate(..., return_dict_in_generate=True,
  output_hidden_states=True)` and takes `hidden_states[step][-1][:, -1, :]` per step, stacking
  to [num_steps, D] (`BD:470-539`, extraction `BD:511-527`).
- Receiver (inference): the block goes through the trained head `pre_ln ->
  MultiheadAttention(hidden, 8 heads, dropout 0.1) -> post_ln (normed + attn) ->
  AdaptiveProjection` (residual MLP, learnable scales 0.2/0.1, [-10, 10] clamp) (`CM:16-47`,
  `CM:74-88`, wiring `HA:520-523`).
- Injection: between the special-token embeddings `<bop>` and `<eop>` (begin/end of plan)
  inserted inside the first user turn after the task text; the base model is called with
  `inputs_embeds` (`HA:613-651` Qwen, `HA:685-694` LLaMA, generate at `HA:743-749`). Tokens are
  real added special tokens with resized embeddings (`CM:367-384`).

### 2.2 What is aligned, and how (training)

`ModelWithInsertedHiddenState.forward` (`CM:492-635`) runs three passes: (1) plan-text pass, no
grad — sender plan ids inserted between `<bop>`/`<eop>` (`CM:545-560`, helper `CM:228-283`); (2)
random-negative pass, no grad — another sample's hidden block from the batch/GPU pool replaces
the real one (`CM:565-589`, `CM:285-316`); (3) normal pass, grad — real hidden block
curriculum-mixed with plan embeddings at a random ratio in {0.1..0.9} (`CM:417-427`, mix
`CM:188-209`), wrapped in `<bop>`/`<eop>` embedding vectors (`CM:429-441`), fed via
`inputs_embeds` (`CM:477-488`).

Loss (`CM:601-619`): CE + plan-similarity + random-contrast, dynamic weights (`CM:221-226`).
Plan similarity: per-token KL(normal || plan) + cosine on flattened next-token distributions
from the first supervised position, margins 0.7/0.3 (`CM:669-713`) — pulls latent-conditioned
behavior toward plan-text behavior. Random contrast: hinge `clamp(0.69 - JS(normal, random),
min=0)` (`CM:715-756`) — the model must react to a wrong latent block, up to a margin. So
Interlat aligns (a) latent semantics with the plan text and (b) receiver sensitivity
("conditional separation").

### 2.3 Latent compression (student over teacher)

`StudentOverTeacher` (`CP:220-266`): the student LM autoregressively emits K=128 latents with
the LatentMAS-style loop — `h2e = LayerNorm + Linear( hidden, hidden, bias=False)`, learnable
`lat_bos` start vector, explicit `position_ids=[[past_len]]` per step, cache chaining
(`CP:251-256`, loop `CP:389-406`). The frozen teacher receives the student's K latents (grad
path), ground-truth data latents (no-grad), or none; student trained with CE +
uncertainty-weighted KL (entropy-gap weights, quantile 0.95, temperature 4.0, window 64) +
pooled-latent cosine alignment (`CP:412-518`). README: full trajectories compress to as few as 8
latent vectors (~1-3% of original length), up to 24x lower communication latency.

### 2.4 Intervention battery (their eval, our template)

`EM:177-274`: covariance-preserving Gaussian resample `CovGauss-0mu: H' ~ N(0, Sigmahat)` /
`CovGauss-mu: H' ~ N(muh, Sigmahat)` (`EM:177-212`), and `RandomRot: H' = muh + (H - muh)
Sigmahat^{-1/2} Q Sigmahat^{1/2}` with Q Haar-random orthogonal from SVD (`EM:214-274`): mean
and covariance preserved, higher-order structure destroyed. Eval variants also include text
plans, no communication, cross-family (qwen2llama) transfer.

### 2.5 Reported results (README, textual)

- ALFWorld success +3-8% absolute vs language-based communication; longer successful
  trajectories (+10-20%), read as informed exploration.
- Task-mismatched latents or RandomRot: 20-40% relative success drops despite preserved
  low-order statistics.
- Cross-family (Qwen -> LLaMA) transfer: up to +8-10% absolute over text baselines, no parameter
  sharing. MATH gains at higher difficulty; backbones Qwen2.5 0.5B/7B, LLaMA3.1-8B.

### 2.6 Design lesson for OCC-3

LatentMAS is training-free and cache-native: latents live in the KV cache; the transform is a
norm rescale (+ optional ridge map); it works because the same model instance is sender and
receiver. Interlat decouples sender/receiver: an explicit [T, D] hidden block is re-injected as
pseudo-tokens, but the receiver head is trained (MHA + projection + curriculum + contrast);
untrained receivers cannot consume raw hidden blocks. Both feed latents via `inputs_embeds` and
rely on the receiver treating them like ordinary prefix tokens.

## 3. transformers 5.20.0 KV-cache cheat sheet (pinned stack)

### 3.1 Pin verification

- transformers 5.20.0 (2026-10-15, PyPI): Python >= 3.10, torch extra requires torch >= 2.6,
  tokenizers >= 0.23.1,< 0.24.
- torch 2.14.1 exists with cp311 win_amd64 wheels; tokenizers 0.23.1 ships a cp310-abi3
  win_amd64 wheel (fine on 3.11). The existing OCC-3 harness venv already runs transformers
  5.20.0 + torch 2.14.1 + tokenizers 0.23.3 on Windows Python 3.11 CPU
  (research/latent_handoff/.venv): pin verified in use. Last 4.x is 4.57.6 (torch >= 2.2) —
  fallback only.

### 3.2 DynamicCache construction and extension

- Construct `DynamicCache(config=model.config)` (cache_utils.py:1813-1858); with a config, layer
  objects are created eagerly from layer_types, otherwise lazily on first update.
- From legacy tuples: `DynamicCache(ddp_cache_data=[(k_0, v_0), ...])` (cache_utils.py:1815,
  1831-1848). Replaces 4.x `from_legacy_cache`, which no longer exists.
- Per-layer shape [batch, num_kv_heads, seq, head_dim] (cache_utils.py:1773-1774).
  `DynamicLayer.update` concatenates k/v along dim -2 in place and returns full k/v
  (cache_utils.py:118-137).
- Length `cache.get_seq_length(layer_idx=0)` (cache_utils.py:1532); `get_query_offset()` equals
  it except MTP. Trimming `cache.crop(-n)` removes n tokens from the end; positive values raise
  ValueError (cache_utils.py:163-180). Also `reset()`, `batch_size`, `batch_select_indices`.
- Iteration `for k, v, sliding in cache` (cache_utils.py:1860-1862). There is NO `__getitem__`;
  `cache[0][0]` raises.
- Offloading: `DynamicCache(config=..., offloading=True)` or `cache_implementation="offloaded"`
  (Cache.__init__ cache_utils.py:1326-1348).

### 3.3 Model forward: inputs_embeds + past_key_values + masks + positions

Verified on v5.20.0 Qwen2 (modeling_qwen2.py) and Llama (modeling_llama.py):

- Exactly one of input_ids / inputs_embeds (modeling_qwen2.py:344). With `use_cache=True` and no
  cache passed, a DynamicCache is auto-created (modeling_qwen2.py:351).
- `cache_position` is REMOVED from forwards. If `position_ids is None`, it is derived as
  `arange(q_len) + past_key_values.get_seq_length()` (modeling_qwen2.py:353-355,
  modeling_llama.py:376-377). Rope offsets are correct automatically while the cache length is
  the true prefix length; a stale position_ids silently mis-rotates.
- `attention_mask`: 2D [batch, seen + q_len], seen = cached length (masking_utils.py
  create_causal_mask docstring, ~line 407). A 4D mask is passed through; a dict of prebuilt
  masks is accepted. Direct call with a cache: `ones([B, cache_len + q_len])`.
- KV growth: passing `past_key_values=cache` with use_cache=True (the config default) EXTENDS
  the cache in place, even in a plain forward. Pass `use_cache=False` or deepcopy when
  re-reading logits from one cache (see 4.0 item 2).
- Hidden states: `output_hidden_states=True` returns a (num_layers + 1) tuple; [0] = embedding
  output (pre first layer), [-1] = post-final-norm (LM-head input). v5 records them via
  `_can_record_outputs` at decoder-layer inputs (modeling_utils.py can_record_outputs
  docstring). Same convention as 4.x.

### 3.4 generate() specifics (generation/utils.py, v5.20.0)

- `inputs_embeds` accepted as main input; a dummy empty input_ids [B, 0] is created, batch
  inferred from any tensor kwarg (utils.py:826-848, 879-887).
- position_ids inside generate: `cumsum(attention_mask) - 1` masked at pads, else `arange(seq +
  past_len)` (utils.py:894-916).
- `cache_position` is a BC shim for remote-code models only and warns (utils.py:768-778). Do not
  pass it (drop LatentMAS `M:258` in the port).
- `return_dict_in_generate=True, output_hidden_states=True` still returns per-step hidden-state
  tuples [B, gen_len, hidden] per layer (generation/outputs.py ~200-215) plus `past_key_values`
  (Cache) when use_cache — Interlat's collector pattern (`BD:491-503`) stays valid.
- CPU-in/CPU-out generation is a supported mode (docs "Keep generation tensors on CPU").

### 3.5 Guaranteed vs removed (4.x -> 5.x migration list)

- Removed: `to_legacy_cache`/`from_legacy_cache`; `__getitem__` on caches; `cache_position` in
  forwards (shim only); legacy tuple caches as forward input. LatentMAS `_truncate_past`
  (`LM:61-79`), `_past_length` (`M:23-27`), and `cache_position=` (`M:258`) all break on v5.
- Replaced: no-arg DynamicCache -> DynamicCache(config=...); tuple indexing ->
  `cache.layers[i].keys/.values` or iteration; manual slicing -> crop(); explicit cache_position
  -> nothing (auto position_ids).
- Stable across both: inputs_embeds path; hidden_states tuple semantics; 2D mask width = seen +
  q; per-layer [B, H_kv, T, D]; generate accepting and extending a passed cache.

### 3.6 Gotchas checklist for the OCC-3 port

1. Any forward with use_cache (default True) extends the passed cache in place: deepcopy before
   re-reading logits from the same cache.
2. attention_mask width must equal cache_len + q_len exactly.
3. Never reuse position_ids from an earlier call; derive or let the model.
4. `hidden_states[-1]` is post-final-norm (LM-head input), [0] is the pre-layer embedding
   (LatentMAS uses [-1] for latents, [0] for e_t, `M:313-314`).
5. bf16/fp32: post-norm hiddens have std ~2.4 vs token-embedding std ~0.12 (SmolLM2-135M,
   measured); feeding hiddens back unmodified is out-of-distribution for the embedding path.
   LatentMAS rescales the norm (`M:207-212`) for this reason.
6. GQA: KV tensors use num_key_value_heads (SmolLM2 3 vs 9 Q; Qwen2.5-0.5B 2 vs 14).
7. Chat models: a cached prefix must end at a turn boundary the receiver's template can continue
   (missing `<|im_end|>`/eos leaves the model mid-turn).

## 4. OCC-3 experiment design (CPU, offline, no external APIs)

Targets in order: HuggingFaceTB/SmolLM2-135M-Instruct (Llama arch, 30 layers, hidden 576, 9 Q /
3 KV heads, head_dim 64, vocab 49152, rope_theta 1e5, tied embeddings, Apache-2.0, 134.5M
params, revision 12fd25f77366fa6b3b4b768ec3050bf629380bac), then Qwen/Qwen2.5-0.5B-Instruct
(Qwen2 arch, 24 layers, hidden 896, 14 Q / 2 KV heads, head_dim 64, vocab 151936, rope_theta
1e6, tied, Apache-2.0, 494M params). KV bytes per cached token: SmolLM2 2*30*3*64 floats = 45
KiB fp32; Qwen 2*24*2*64 = 24 KiB fp32.

### 4.0 Existing harness status and diagnosis (read before extending)

`research/latent_handoff/` already contains kv_handoff.py, latent_continuation.py,
interventions.py, run_experiments.py, model_utils.py and results (smollm2-results.json,
RESULTS-EXP-A.md, RESULTS-EXP-B.md). State: 10 trials, seed 1234, fp32 CPU, greedy.

- Experiment A (private-fact probe): with-cache 0/10, without-cache 0/10. Transfer (deepcopy)
  9.4 ms mean; cache 1.89 MB.
- Experiment B (unmodified post-norm hidden fed back): 0/10 at 1-2 latent steps, 1/10 at 4;
  per-step hidden drift 48-85 L2; the fed-back vector is raw post-norm hidden (std 2.41, max
  46.97) vs token-embedding std 0.116.
- Interventions: A_correct 0/10, B_no_cache 0/10, C_zeroed 0/10, D_random 0/10, E_unrelated
  0/10, F_full_text 0/10, G_compact 0/10.

Diagnosis (three defects, all fixable in place):

1. Broken ceiling: F_full_text is also 0/10 — even with the answer in plain text the 135M model
   fails, so the probe cannot demonstrate handoff. The prompts are raw text without
   chat-template turn structure (kv_handoff.py SENDER_TEMPLATE / RECEIVER_TEMPLATE). Fix: wrap
   sender/receiver turns in the model's chat template, add one in-prompt example, and gate all
   handoff claims on F >= 8/10. F is the positive control.
2. Cache mutation bug: interventions.py `logits_at_answer` calls `model(input_ids=receiver_ids,
   attention_mask=am, past_key_values=cache)` without `use_cache=False`, so every probe grows
   the shared cache in place. The reference-logits call in run_interventions runs first, so
   condition A later sees the receiver prompt twice in the cache; A-vs-A KL is 1.99 instead of 0
   (smollm2-results.json interventions.summary). Fix: use_cache=False in the probe forward or
   deepcopy first. (F_full_text KL=0.0 vs A is expected and healthy: full-sequence recomputation
   equals cache continuation.)
3. Missing realignment in Experiment B: latent_continuation.py feeds `hidden_states[-1][:, -1,
   :]` back unmodified (its docstring says so). Apply the LatentMAS norm rescale (1.3),
   optionally the ridge map, before each inputs_embeds step; the measured drift is consistent
   with the norm mismatch.

None of this invalidates the OCC-3 concept; it invalidates the probe.

### 4.1 Experiment A: KV-cache handoff (protocol v2)

Question: can a receiver answer about content it never saw as text, given only the sender's KV
cache?

- Sender: chat-template prompt with private notes (w=n pairs); greedy, use_cache=True; ensure
  the turn completes (assistant reply ends with eos) so the cache ends at a turn boundary.
  Artifact = deepcopy of the cache.
- Receiver: its own chat-template user turn appended after the cache ("What number is paired
  with {w}? Number only."), greedy, 8 new tokens.
- Trials: 100 per model, seeded (word/number pools as in kv_handoff.py make_trial).
- Gate: with-cache accuracy >= full-text accuracy - 5 points and with-cache > no-cache with
  paired bootstrap CI.
- Conditions (same receiver question): correct cache; no cache; zeroed cache; per-tensor
  Gaussian (mean/std matched); covariance-preserving resample (Interlat CovGauss, 2.4); Haar
  random-rotation (2.4); unrelated task cache; shuffled token order within the cache (same
  permutation all layers); truncated cache (notes tokens only); full text; OCC-1 compact text
  encoded with prime-agent-runtime/src/rlm/occ1.py (same bytes on the wire).
- Measures: first-position KL/L1 vs correct-cache reference (after fixing 4.0 item 2), accuracy,
  receiver latency, peak RSS, artifact bytes (KV fp32/fp16 vs text vs OCC-1).

### 4.2 Experiment B: bounded latent continuation (protocol v2)

Question: does a LatentMAS-style latent loop carry usable state on a 135M model, and when does
it degenerate?

- Port `generate_latent_batch` per 1.2 with the 1.3 transform. Grid: latent_steps {0, 1, 2, 4,
  8, 10, 16}; transform {none, norm-rescale, norm-rescale + ridge}; sender prompt as in A;
  receiver decodes with its question appended (1.5 pattern, without cache_position).
- Controls: fixed random vector per step matched to embedding stats; true next-token embedding
  (oracle text continuation) as upper bound; full text.
- Telemetry per step: L2 drift between consecutive latents, cosine to the first latent,
  next-token entropy and top-1 mass, latent norm vs mean embedding row norm. LatentMAS published
  no stability traces; with an untrained 135M model expect early saturation or drift — the grid
  quantifies it.
- Also run the Interlat-style insertion variant: splice the [k, D] latent block into the
  receiver's embedding sequence right after the user-turn header (1.6), between sentinel token
  embeddings; ablation-only since we do no training.
- Qwen2.5-0.5B: same grid, 50 trials if latency dominates.

### 4.3 What to measure

- Task: private-fact accuracy (primary); optional 50-problem GSM8K subset with boxed-answer
  extraction (LatentMAS utils.py pattern).
- Divergence: full-vocab next-token KL and L1 at the receiver's first decoding position, per
  condition vs the correct-cache reference.
- Cost: sender/transfer/receiver wall-clock; artifact bytes (KV fp32 and fp16, text, OCC-1);
  peak RSS (psutil).
- Determinism: greedy only, fixed seeds, report torch.get_num_threads().

### 4.4 Expected pitfalls

- 135M ceiling: the positive control gates everything; escalate to Qwen2.5-0.5B for headline
  numbers.
- Turn-boundary corruption: appending a receiver prompt to a cache that ends mid-turn makes
  instruct models continue the sender's turn (observed in RESULTS-EXP-A answers).
- Latency: prefill dominates; keep prompts < 128 tokens at 100 trials; deepcopy cost is
  negligible (9.4 ms).
- Cost honesty: a 40-token cache is 1.9 MB vs ~200 text bytes; latent/KV wins only if accuracy
  holds at high compression (few latent steps), which the grid tests.
- If norm-rescale still drifts: try hidden_states[-2] (pre final norm) or an RMS-normed variant;
  LatentMAS chose post-norm + rescale on Qwen3-14B and reported stability, but published no
  per-step traces.
- Never reuse position_ids across calls (3.6 item 3); never probe a cache twice without deepcopy
  (3.6 item 1).

### 4.5 Boundary with the repo campaign rules

crates/pi-coding-agent/src/core/occ/mod.rs requires occ/ modules to stay pure, deterministic,
offline-testable, no model APIs inside them, and the occ/ tree is frozen for workers B and C
(delta, consolidation, prune). OCC-3 experiments therefore live only under
research/latent_handoff/ and later as a separate runtime module outside crates' occ/; nothing
here edits occ1.py, mod.rs, or any Rust file.

## 5. Reference table

| Item | Location | Pin / version | License |
|---|---|---|---|
| LatentMAS repo | optimus-rlm-references\LatentMAS | 9a9e4d331eb11430bd9e64754c6b252b06d73031 (2026-06-17) | Apache-2.0 |
| LatentMAS paper | arXiv 2511.20639 (ICML 2026) | abstract fetched 2026-10-16 | paper |
| Interlat repo | optimus-communication-references\Interlat | 66a89cb4d4097b2f86cbe48ed9851d6e8578f821 (2026-06-16) | Apache-2.0 |
| Interlat paper | arXiv 2511.09149 (ACL 2026) | abstract via README | paper |
| transformers | pypi | 5.20.0 (2026-10-15); last 4.x 4.57.6 | Apache-2.0 |
| torch | pypi | 2.14.1 (cp311 win_amd64 verified) | BSD-style |
| tokenizers | pypi | 0.23.1 (cp310-abi3 win wheel); harness uses 0.23.3 | Apache-2.0 |
| transformers sources cited | github huggingface/transformers tag v5.20.0: cache_utils.py, masking_utils.py, generation/utils.py, models/qwen2/modeling_qwen2.py, models/llama/modeling_llama.py, modeling_utils.py | fetched 2026-10-16 | Apache-2.0 |
| HF kv_cache doc | huggingface.co/docs/transformers/en/kv_cache (v5.20.0) | fetched 2026-10-16 | docs |
| SmolLM2-135M-Instruct | huggingface.co/HuggingFaceTB | revision 12fd25f77366fa6b3b4b768ec3050bf629380bac | Apache-2.0 |
| Qwen2.5-0.5B-Instruct | huggingface.co/Qwen | config fetched 2026-10-16 | Apache-2.0 |
| OCC stack context | worktree prime-agent-runtime/src/rlm/occ1.py; crates/pi-coding-agent/src/core/occ/mod.rs | branch improve/occ2-occ3-communication, base ce8480d59 | repo license |
| Existing OCC-3 harness | worktree research/latent_handoff/ (kv_handoff.py, latent_continuation.py, interventions.py, run_experiments.py, results/smollm2-results.json) | not committed; another worker owns it | repo license |
