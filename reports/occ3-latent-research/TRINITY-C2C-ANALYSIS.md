# Trinity-C2C: source analysis and GLM applicability

## Scope and verdict

**Static source review only. No model result was reproduced.** No model/tokenizer load, inference, training, GPU operation, deployment, or project runtime test was performed. This report establishes what the code implements, not that any live service supports or successfully executes it.

Reviewed revisions:

- **T — Trinity-C2C:** [`5752bd9aba285d40dd2d4b1d44ff340d7dd31c55`](https://github.com/makinux/Trinity-C2C/commit/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55).
- **C — official C2C:** [`bdb932aa63637959074a9a6fd59992a4d0d9ad22`](https://github.com/thu-nics/C2C/commit/bdb932aa63637959074a9a6fd59992a4d0d9ad22).
- **P — paper:** [*Cache-to-Cache: Direct Semantic Communication Between Large Language Models*, arXiv:2510.03215v1](https://arxiv.org/html/2510.03215v1), 3 October 2025. Paper results below are attributed, not reproduced. Paper and current code are separate artifacts.

Source identifiers such as **T2:192–234** mean exact line ranges in the commit-pinned inventory at the end. All listed source files were read in full.

**Verdict:** Trinity contains genuine in-process KV capture, fusion, and cache-conditioned decoding. It is not merely a text label for latent transfer. However, its fuser differs from official C2C, its P1 orchestration retains the text plan, and its defaults/control tests do not prove useful source-specific transfer. Neither project establishes **GLM-5.3 compatibility or Ollama KV export/injection access**.

## 1. Capture, layout, and receiver generation

### Trinity

The numpy `KVCache` stores one `(K,V)` pair per layer, each **`[KV heads, sequence, head_dim]`**, with batch 1 removed. `terminal_alignment` pairs final layers backward, leaving unmatched receiver layers unchanged. The simple fuser requires equal KV-head counts and projects only head width; equal widths initialize to identity. It changes only the prefix overlap, never the receiver sequence length. **T1:38–76,118–165.**

The reusable `C2CEngine` loads two local Hugging Face models, freezes them, and defaults to **CPU/float32/eager**, SmolLM2-135M -> Qwen2.5-0.5B. `_encode` performs a fresh forward with `use_cache=True`, captures `pkv.layers[l].keys[0]` and `.values[0]`, and detaches them. Token IDs and character offsets use the same truncation and `add_special_tokens=False`. These are actual stored attention caches, not final hidden-state embeddings. **T2:50–76,97–116.**

The execution path is:

1. Encode sharer and receiver text separately.
2. Align, project, and fuse into receiver-shaped cache rows.
3. Rebuild `DynamicCache` with per-layer `.update()`, restoring batch 1.
4. Append a separately tokenized generation prompt. Its positions start at the cached prefix length; the attention mask covers past plus prompt.
5. Greedily decode one token per receiver forward, relying on in-place cache growth.

Thus receiver attention actually consumes the fused cache. The implementation assumes batch 1 and all-ones attention masks. **T2:192–234,236–282.**

`receiver_only` follows the **same tokenization and decoding path** without fusion. This is the correct local no-fusion baseline, not automatically the P0 chat pipeline or a single tokenization of concatenated context and prompt. **T2:284–295; T8:181–190.**

Two older paths must not be confused with the engine:

- `InProcessLM.generate_from_kv` uses `DynamicCache.from_legacy_cache` and `model.generate`, explicitly marked version-sensitive. Its direct `np.asarray` cache conversion is not a robust CUDA/bfloat16 export adapter. **T1:58–65,174–214.**
- `c2c_realrun` drops the last cached row and feeds the receiver's last token live at its original position. This tests next-token probabilities without duplicate input, but recomputes that final row and scores only the **first token** of each target string. **T4:35–47,61–81.**

### Official C2C

`RosettaModel` uses local torch models and `DynamicCache.key_cache/value_cache` with **`[batch,KV heads,sequence,head_dim]`**. It accepts either shared input IDs or aligned per-model inputs. Earlier input sections prefill the models and fuse selected cache slices; the final section runs receiver forward against that fused prefix. Standard subsequent decoding uses a no-projection index and receiver tokens. This is **prefill fusion followed by receiver decoding**, not continuous cross-model exchange for every token. **C3:22–75,350–508,510–585,665–697,779–792.**

An optional `include_response` path injects current-token K/V before attention via a **Qwen3-specific monkeypatch**. It is not a generic GLM adapter. Multi-source fusion can be sequential or a parallel sum of residual deltas; a replication must pin this mode. **C3:195–278,280–348,413–439,510–581.**

## 2. Fusion equations and exact gate behavior

Let `R` be receiver cache, `S` aligned source cache, and `P` learned projection. K and V are handled separately unless stated otherwise.

| Implementation | Actual combination | Disabled-gate behavior |
|---|---|---|
| Trinity simple/heterogeneous fusers | `(1-g)*R + g*P(S)` over overlap | `g=0` retains R; `g=1` replaces overlap, not residual addition |
| Trinity MLP variant | Same blend; projection adds `GELU(SW1+b1)W2+b2` to the linear path | Zero-initialized residual output starts at the linear fuser |
| Official `C2CProjector` | `R + gate*sigmoid(weight)*P(S,R)` | Closed K and V gates retain R for finite valid inputs |
| Official `AllInOneProjector` | Configurable projection, weights, and target contribution | With defaults, gate 0 leaves `(1-weight)*R`, **not R**; `add_self=False` can leave zero |

**T1:147–165; T6:92–142; T7:26–63; C1:742–770,873–940,947–1010.**

Official `C2CProjector` concatenates flattened source and receiver heads, embeds that joint input, then branches into projected features and dynamic per-token/per-head weights. It is not Trinity's source-only linear projection. **C1:960–988.**

### Trinity gate details

- The numpy `set_gate(0/1)` stores `-/+inf`, yielding exact sigmoid endpoints. In contrast, `init(default_gate=0)` passes through a clamped logit helper, so initialization is not equivalent to the setter. **T1:93–100,137–145.**
- Torch fusers use one gate logit per receiver layer, shared by K and V. Training adds logistic noise `log(u)-log(1-u)` and divides by `tau`; eval uses plain sigmoid. **T6:74–80.**
- The engine's setter uses finite **`-50/+50`**, not infinities. `sigmoid(-50)` is approximately **1.93e-22**, representable and nonzero in float32. The comment claiming fp32 zero is incorrect. Ordinary blends may round to R, but this is not unconditional bit-exact equality. Projection/rotation still runs: gate zero does not bypass invalid shapes/devices, and `0*NaN` is not safe. **T2:79–84,166–169; T6:92–111.**
- No checkpoint means **untrained gate 0.05 by default**, not no fusion. Failure to load a requested checkpoint forces gate zero. Loaded per-layer gates are retained unless explicitly overridden; per-call overrides restore previous gates in `finally`. **T2:111–164,246–282.**

### Official gate details

`C2CProjector` has separate scalar **key and value** gate logits per layer/projector. During training it adds independently sampled Gumbel noise per batch/head/token and applies a temperature-scaled sigmoid. Eval uses **`(logit > 0)`**, so logit 0 is **closed**, not 0.5. Both logits initialize at zero. The dynamic weights remain separate sigmoid controls. **C1:930–940,984–1010.**

The current temperature schedule is exponential interpolation; paper Appendix A.3.5 describes linear annealing. The paper's architecture broadly matches residual fusion, but its settings should not be assumed identical to this code revision. **C1:942–945; P §3.3.2/A.3.5.**

## 3. Token, layer, and positional alignment

### Trinity's path

`char_span_align` chooses the source token with greatest positive character-span overlap, falling back to nearest midpoint. It is documented for **the same text under different tokenizers**. It neither averages all overlapping tokens nor proves semantic equivalence between unrelated strings. Empty source offsets fall back to index 0, which can be invalid. Although the engine accepts arbitrary source/receiver text, callers must enforce a meaningful correspondence. **T5:30–55; T2:256–263.**

The heterogeneous fuser flattens all heads per token and maps **`Hs*ds -> Hr*dr`**, with independent K/V matrices per aligned layer. Equal flattened widths initialize to identity even if head decomposition differs. This handles dimensions but does not establish semantic alignment or preserve GQA group locality. **T5:61–104; T6:59–90.**

For K, Trinity implements:

**undo source RoPE -> gather source rows -> project -> apply receiver RoPE at receiver positions**.

V is gathered/projected without rotation. Actual source/receiver frequency vectors are read from `model.model.rotary_emb.inv_freq`; the code does not merely guess `rope_theta`. **T3:28–64; T6:33–50,92–112.**

This path assumes full-width, split-half, unit-amplitude rotary transforms derived from a fixed frequency vector. It is not a general implementation of partial/interleaved RoPE, dynamic frequencies, scaled rotary amplitudes, compressed caches, or arbitrary attention state. The attribute path itself is model-specific. Additional static risks: `t_rope_cos_sin` creates CPU position tensors while buffers may be on CUDA; the engine moves its fuser to a device but does not cast it to the configured model dtype. These are **unexecuted portability risks**, not reproduced failures. **T3:33–54; T6:33–50,59–74; T2:97–139.**

### Official C2C differs

Official alignment decodes **each receiver token**, re-encodes its substring with the sharer tokenizer, and selects one source token (`FIRST` or `LONGEST`). It constructs a lossy one-to-one source sequence; it is not Trinity's gather from a naturally tokenized complete source cache. Chat template sections are preserved/padded; message sections are aligned token by token. **C2:94–152,188–221,531–639.**

Paper §3.3.3/A.1.2 chooses maximal coverage (`LONGEST`), whereas the class defaults to `FIRST`; the inspected Qwen recipe specifies `first` but disables alignment. **C2:29–35; C10:2–19.**

The SFT path creates one projector per receiver layer and uses terminal or normalized-depth alignment with `K=1`. When the source is shallower, official terminal mapping clamps early source indices to zero, unlike Trinity's unchanged unmatched receiver layers. **C7:311–395; C6:15–84; T1:71–76.**

The official standard projector/wrapper operates directly in stored cache space. It does **not** implement Trinity's explicit unrotate/gather/re-rotate normalization. Its response monkeypatch expects K after Qwen3 key normalization and RoPE. **C1:947–1010; C3:195–251,531–550.**

## 4. Text replay and the causal limitation

`p1.py` first generates an ordinary **text plan** and adds it to the transcript. The Worker then re-encodes **the same transcript** with both models and appends a raw-continuation worker instruction. The source is not the Thinker's original generation cache. At gate zero the receiver still sees the plan as text. Revision re-encodes the new canonical transcript rather than continuing a branched cache. This is a dual-channel design, not a hidden-context-only handoff. **T8:108–137,169–192.**

The older `c2c_thinker_to_worker` only encodes `query + suffix plan_instruction` for the source and `query` for the receiver, then fuses the prefix overlap. Where tokenization/positions match, **a suffix cannot change earlier query states in a causal LM**. With identical models and identity mapping, the shared prefix therefore adds no information from the omitted instruction. Token-boundary accidents are not a principled fix. **T1:147–165,244–260.**

For a private-context experiment, source-only evidence must causally precede the transferred rows. One proposed construction is `private evidence + shared query`, retaining only the query's source-cache slice and correctly handling its positions. This resembles the paper's enrichment oracle, which drops exemplar cache rows after prefill. **It is an experiment proposal, not a GLM result or a feature proved by P1. P §3.2.1.**

## 5. Training, provenance, and limits of the embedded checks

Trinity trains projection matrices, gates, and optionally the MLP residual. Prefix caches are detached; base-model weights are frozen. The **receiver continuation forward must still be differentiable with respect to the injected cache**. Freezing weights does not mean wrapping that training pass in `no_grad()`. The basic toy loss only scores the target's first token. The held-out example is three country/capital training pairs and one test pair, not broad semantic validation. **T7:26–94,100–125,158–190; T6:406–454.**

Official training uses receiver response next-token loss and configuration-driven freezing. The inspected recipe freezes base and teacher and requests 500,000 OpenHermes examples, one epoch, length 2048, learning rate 1e-4, and a 99/1 split. These are **recipe settings, not execution receipts or a deployed trained checkpoint**. **C7:400–451,587–608,650–652,746–780,801–837; C10:21–55.**

Paper §4.1 reports Qwen, Llama, and Gemma evaluations, not GLM-5.3. Its §4.4 **Identical** Qwen3-0.6B/Qwen3-0.6B trained-fuser ablation supports research into homogeneous learned transformations. It does not prove training-free GLM transfer or independent sender information when both models receive identical inputs.

Trinity checkpoints validate class, model-name strings, head dimensions, rotary-buffer hashes, and strict state-dictionary loading with rollback. This does not bind exact model/tokenizer revisions, chat templates, backend behavior, or all layer-map conditions; the explicit shape check only compares heads/dimensions. Loading uses `weights_only=False` and assumes trusted local files. **T6:160–225,232–251.**

Read the checks themselves, not only their success wording:

| Check | What source inspection establishes |
|---|---|
| Numpy selftests | Gate endpoints, shapes, and overlap have synthetic assertions; not language transfer. **T1:272–315.** |
| Self-C2C real-run | Prints target-logprob sanity and sweep; does not assert semantic success. Unequal lengths warn but still use identity alignment. **T4:87–116.** |
| Engine smoke | Asserts equal decoded text at gate zero and changed text at gate 0.8. Change alone can be a random perturbation. **T2:301–327.** |
| Heterogeneous training demo | Checks gradients and prints losses; no general semantic guarantee. **T9:85–125.** |
| RoPE real-demo | Catches even its numeric-comparison assertion, then prints a positive conclusion. That final line alone is not proof. **T3:179–189.** |
| Held-out fuser demo | Prints learned/no-injection/wrong-source losses without asserting the desired inequalities. **T6:440–454.** |

None of those checks was executed in this review.

## 6. Homogeneous GLM-5.3: conditional applicability

Three cases must remain separate:

1. **Exact prefix reuse/resume:** same checkpoint, tokenizer, token IDs, positions, attention policy, and compatible cache representation may allow direct reuse without a learned projection. First prove full-prefill versus split-prefill/export-import equivalence.
2. **Same model + same transcript + identity fusion:** identical caches add no independent sender information. A trained fuser may help as extra learned computation, but that is not proof of cross-agent knowledge transfer.
3. **Same model + different causal histories:** matching tensor dimensions is insufficient. Fusion is an intervention, not ordinary prefix reuse. Specify the source slice, positional mapping, and held-out target regime; learning may be necessary for useful fusion.

Before any authorized live adaptation, pin actual GLM model/tokenizer revisions, quantization/adapters, dtype, and attention backend. Inspect its real cache layout and any compressed/shared/recurrent state; do not assume conventional Qwen-style K/V. Verify normalization/rotary placement, dimensions, frequency/scaling rules, masks, cache positions, and sliding-window behavior. Then implement authorized **capture and injection in the actual inference engine**, with fresh-cache isolation per case.

Version compatibility also matters: official C2C pins **Transformers 4.52.4**, while Trinity requires **5.x** and its new engine uses a different cache API. Direct transplantation needs a version/layout adapter. **C9:5–12; C3:22–44; T11:1–7; T2:192–197.**

Trinity's GLM/Qwen/DeepSeek HTTP role configuration is separate from its actual SmolLM/Qwen in-process C2C pair. Its C2C loader does not use an Ollama KV endpoint. **No Ollama cache-access capability is established here. T10:10–33,65–72; T2:50–76,103–118.**

### Minimum controls for a useful-transfer claim

- **Plumbing:** full versus split prefill; direct versus exported/imported receiver cache; true receiver bypass versus zero fusion. Compare logits/tokens with fixed input IDs and declared tolerances.
- **Null/capacity:** identical source/receiver cache; receiver/self source through the same trained fuser; untrained/random fuser.
- **Semantic specificity:** correct source versus example-level shuffled source and contradictory source. Add shape/length-matched noise or zero source as perturbation controls.
- **Alignment:** correctly shifted/rephased positions versus wrong positions, and layer/head mapping ablations.
- **Channel isolation:** receiver-only text, positive-control full source text, text+latent, and latent-only. Source-only evidence must not leak through receiver text or prompt construction.
- **Generalization:** held-out entities, templates, and lengths; multiple seeds. Score full-target likelihood/task accuracy, not only first-token changes. Report failures and uncertainty.

Include both model prefills, transfer/copy/rephasing, fuser work, and decoding in inference cost; report training cost separately. Paper speedups do not guarantee same-model or network-transport speedups. Treat KV as sensitive derived content, not anonymized data: paper §5 presents privacy-aware collaboration as future work, not a privacy guarantee.

**Evidence boundary:** mechanism and static risks are source-verified. Numerical equivalence, useful GLM-5.3 transfer, backend access, checkpoint quality, performance, and privacy remain unverified.


## Source inventory

All ranges below were read in full. Inline `ID:start–end` citations use these exact revisions.

| ID | Commit-pinned source | Full range read |
|---|---|---|
| T1 | [`trinity/c2c.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c.py#L1-L324) | 1–324 |
| T2 | [`trinity/c2c_edge.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c_edge.py#L1-L331) | 1–331 |
| T3 | [`trinity/c2c_rope.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c_rope.py#L1-L196) | 1–196 |
| T4 | [`trinity/c2c_realrun.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c_realrun.py#L1-L120) | 1–120 |
| T5 | [`trinity/c2c_hetero.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c_hetero.py#L1-L170) | 1–170 |
| T6 | [`trinity/c2c_fuser_hetero.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c_fuser_hetero.py#L1-L461) | 1–461 |
| T7 | [`trinity/c2c_train_fuser.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c_train_fuser.py#L1-L198) | 1–198 |
| T8 | [`trinity/p1.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/p1.py#L1-L225) | 1–225 |
| T9 | [`trinity/c2c_hetero_realrun.py`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/trinity/c2c_hetero_realrun.py#L1-L129) | 1–129 |
| T10 | [`config.yml`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/config.yml#L1-L72) | 1–72 |
| T11 | [`requirements.txt`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/requirements.txt#L1-L11) | 1–11 |
| T12 | [`requirements-gateway-c2c.txt`](https://github.com/makinux/Trinity-C2C/blob/5752bd9aba285d40dd2d4b1d44ff340d7dd31c55/requirements-gateway-c2c.txt#L1-L11) | 1–11 |
| C1 | [`rosetta/model/projector.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/rosetta/model/projector.py#L1-L1048) | 1–1048 |
| C2 | [`rosetta/model/aligner.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/rosetta/model/aligner.py#L1-L639) | 1–639 |
| C3 | [`rosetta/model/wrapper.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/rosetta/model/wrapper.py#L1-L816) | 1–816 |
| C4 | [`rosetta/model/sampling.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/rosetta/model/sampling.py#L1-L82) | 1–82 |
| C5 | [`rosetta/utils/core.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/rosetta/utils/core.py#L1-L94) | 1–94 |
| C6 | [`rosetta/train/model_utils.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/rosetta/train/model_utils.py#L1-L142) | 1–142 |
| C7 | [`script/train/SFT_train.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/script/train/SFT_train.py#L1-L1003) | 1–1003 |
| C8 | [`script/examples/two_stage_rosetta_example.py`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/script/examples/two_stage_rosetta_example.py#L1-L234) | 1–234 |
| C9 | [`pyproject.toml`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/pyproject.toml#L1-L70) | 1–70 |
| C10 | [`recipe/train_recipe/C2C_0.6+0.5.json`](https://github.com/thu-nics/C2C/blob/bdb932aa63637959074a9a6fd59992a4d0d9ad22/recipe/train_recipe/C2C_0.6+0.5.json#L1-L56) | 1–56 |

Paper sections used: §1–§6, Appendix A.1, A.3, and A.4.2. Downloaded v1 HTML SHA-256: `a23a7a50f9599c40c4ff548c9cbcf465e3bf05ae94ad66cfb8b938c281e8eb3e`.
