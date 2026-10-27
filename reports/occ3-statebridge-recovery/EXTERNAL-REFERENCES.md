# External references vs our OCC-3 StateBridge implementation

Read-only comparison of the external latent-communication references against our
`statebridge.glm53.final-prenorm-predictor.continuous-prefix.v1` receiver path.
Every claim is a quoted line, or is marked **INFERENCE**.

Legend: `R` = reference checkout (`C:/Users/openclawuser/occ3-latent-validation/references/`,
`C:/Users/openclawuser/occ3-refs/`); `O` = this worktree, paths relative to
`C:/Users/openclawuser/optimus-occ3-latent-validation`. Citations are `file:line`.

**This worktree is being edited while I read it.** `latent_worker.py`, `latent_backend.py`
and `chat_adapter.py` all changed during this task (the marker/`prefix_offset` wiring landed
mid-read). The table in section 0 pins the exact bytes each line number refers to. Re-hash
before quoting; trust the digest, not the number.

## 0. Read snapshot

| file | sha256 (first 16) | bytes |
|---|---|---|
| `R state_bridge.py` | `480d17ea4334f2d2` | 53184 |
| `R prompts.py` | `d53daee7e98e98af` | 7056 |
| `R README.md` | `4c2e4c9e80e99007` | 10261 |
| `R hidden_agent.py` | `f31602de21b0c862` | 32586 |
| `R model_insert_hidden.py` | `be6fd884ba0ade41` | 72441 |
| `R align_block.py` | `96ea7eadc490f977` | 10773 |
| `R hidden_state_loader.py` | `0b30b2a9d8988777` | 4967 |
| `R latent_talk.py` | `1e62aa938f7370be` | 8547 |
| `R coconut.py` | `b8e36fc5005cdb82` | 9196 |
| `R lib.rs` | `c50714132d924e51` | 15667 |
| `O statebridge.py` | `b2f2dddb7b834f0b` | 8054 |
| `O latent_worker.py` | `77daa80d1bdd9639` | 52594 |
| `O prefix_layout.py` | `936e8f17ea72c778` | 3857 |
| `O chat_adapter.py` | `f4e093ce0746656a` | 84520 |
| `O attachment_contract.py` | `e924785ffa047782` | 18720 |
| `O latent_types.py` | `d6dfc9e2dc3e9e54` | 8166 |
| `O latent_backend.py` | `dd6477666b3956e9` | 17544 |
| `O recv_path.py` | `e46eeb60eaf30778` | 2449 |
| `O tests/test_statebridge.py` | `fe462b86bff13a17` | 5801 |
| `O tests/test_prefix_placement.py` | `ab577c6b5ffc6b4c` | 9283 |
| `O native-profile-input.json` | `6e676f1c6fc795ba` | 414 |
| `O capture patch` | `b25fd58c2be1bea5` | 30415 |

## 0.1 What each reference is

- **StateBridge** - training-free alignment of last-layer hidden states into the receiver's
  input-embedding space, injected as a continuous prefix. `R README.md:29`: "aligns them to
  the receiver's input embedding space with a closed-form orthogonal transformation, and
  prepends the result as a continuous prefix".
- **Interlat** - *trained* latent communication: learnable projection, MHA, and new special
  tokens added to the tokenizer.
- **CodeTalkers** (`latent_talk.py`) - soft-token protocol in one frozen model; a message is a
  sequence of probability-weighted token-embedding mixtures.
- **Coconut** (`coconut.py`) - continuous thoughts inside a single context; the last hidden
  state replaces the input embedding at a `latent_token_id` slot.
- **LatentMesh** (`lib.rs`) - alignment only: orthogonal Procrustes fitted on calibration
  pairs, with affine centering and a least-squares magnitude scalar. No prompt, no prefix.

## 1. Behaviour table

| # | Behaviour | Reference (file:line + quote) | Optimus (file:line) | Gap | Justified adaptation for GLM-5.3 / SGLang |
|---|---|---|---|---|---|
| a | Which generated tokens are selected (post-thinking filtering) | `R state_bridge.py:579` `# Find </think> token position; pass only post-</think> embeddings`; `:592` `filtered_hidden = gen_hidden_seq[:, think_end_pos:, :]`; `:596` then keeps the last `max_prefix_tokens` of that span. Fallback if no `</think>` is found: `:600` `filtered_hidden = gen_hidden_seq[:, -self.max_prefix_tokens:, :]` (thinking kept). | No thinking boundary. Every accepted row goes into a ring: `O latent_worker.py:773` `rh[destination].copy_(stage.hidden[request_index, i])`; the span then starts at `:309` `n = min(record.accepted_count, record.descriptor.max_prefix_tokens)`. The policy is named, not filtered: `O attachment_contract.py:151` `span_policy: str = "whole_completed_response_tail_v1"`. | Reasoning tokens are in the prefix whenever the answer is shorter than K or generation stops mid-thought. The reference removes them by construction, except in its own fallback branch. | The worker holds no tokenizer (only a `tokenizer_manager` argument at `O latent_worker.py:391`), so it cannot find a text sentinel. GLM-5.3 emits thinking and answer in one id stream; the boundary must arrive as an attested id offset from the frontend. |
| b | Pairing a hidden state with a reference token embedding | `R state_bridge.py:438` `gen_token_ids = seqs[:, input_len:] if gen_len > 0 else seqs[:, :0]`; `:233` `token_embeds = self.embedding_layer(token_ids).float()`; lengths reconciled by silent truncation at `:456-463`. Pairing is positional: capture row `i` is the last-position hidden of forward `i`; `gen_token_ids[i]` is the token sampled from it. | Explicit and attested. `O latent_worker.py:573` `tokens = predict.reshape(-1).index_select(0, indices).reshape(1, -1).clone()` (verify path); `:541` `tokens = next_token_ids.reshape(-1)[:1].clone().reshape(1, 1)` (prefill path); `:749` refuses unless `stage.positions[...]` equals the recomputed `torch.arange(len(req.origin_input_ids) + output_start - 1, ...)` (`:746-747`); metadata carries `:352` `"predictor_position_offset": -1, "representation": "final_layer_prenorm_predictor"`. | None in semantics: both pair the *predictor* state with the token it predicted. Ours refuses on mismatch, the reference truncates and can slip by one (INFERENCE, see 1.1-1). | SGLang accepts up to 6 tokens per verify step, so the emitted ids are the only authority; the pair must be re-checked against `output_ids`, not assumed. |
| c | Alignment math stages and regularisation | Center `:254-258`; regularised covariance `:262` `Cov_H = (Hc.T @ Hc) / N + reg * I`; whiten `:266-267`; Procrustes `:270-272` `U, _, Vt = torch.linalg.svd(M, full_matrices=False)`; reconstruct `:281`; reflection guard `:275-277` `if torch.det(R) < 0:`. `reg` 1e-3 (`:170`). `alignment_mode` is stored (`:190`) and never read. | Same five stages in a thin union subspace: `O statebridge.py:50` `_, singular, vh = torch.linalg.svd(torch.cat((hc, ec), dim=0), full_matrices=False)`; `:53` `rank = min(int((singular > threshold).sum()), 2 * (n - 1))`; `:58-59` ridge 1e-3; `:60-63` whiten, `u @ v`, then `z = (x @ (u @ v) @ _symmetric_power(ce, 0.5, eigen_floor)) @ q.mT + me`. | Ours has no determinant correction, so the map may be a reflection where the reference forces a rotation. Regularisation is identical (1e-3). | Dense path at `hidden_dim = 6144` (`O latent_types.py:110-111`) costs a 6144x6144 fp32 covariance (~151 MiB) plus an O(d^3) `eigh` per message. The union subspace has rank <= 2(n-1) <= 254, ~0.2% of those entries, and runs on rank 0 then broadcasts (`O latent_worker.py:333`, `:340`). |
| d | Norm calibration | `R state_bridge.py:209` `self.target_norm = self.vocab_embeds.norm(dim=-1).mean().item()` over the local weight; applied per row at `:284-286` `aligned = aligned * (self.target_norm / aligned_norm)`. | Same statistic and application: `O statebridge.py:68` `z = z * (target / norms)`, with the mean computed once by `mean_embedding_norm` (`O statebridge.py:87-112`, called at `O latent_worker.py:327`). | No difference in the formula. Ours counts only real rows and refuses unless `total[1] == embedding.num_embeddings` (`O statebridge.py:111`); the reference averages whatever the local tensor holds. | The embedding is vocab-sharded over 8 ranks, so a local mean is a silent bias; ours is an all-reduce over exactly the unpadded rows. |
| e | Vocabulary anchoring | Always on at `snap_ratio = 0.3` (`:171`): cosine `argmax` over the whole vocabulary `:291-295`, then `:297` `aligned = (1 - self.snap_ratio) * aligned + self.snap_ratio * snapped`. | Implemented, switched off in the shipped profile. `O statebridge.py:118-120` `require(alpha in (0.0, 0.3) ...)` then `if alpha == 0: return prefix`; the mix itself is `:155` `((1 - alpha) * prefix.float() + alpha * nearest.float())`. Call site `O latent_worker.py:340`; alpha from profile: `"anchor_alpha":0.0` in `O native-profile-input.json`, default at `O latent_types.py:118`. | Anchoring is inactive in our deployment; 0.3 is permitted (`O latent_types.py:135`) but pinned to 0.0. | A full V x d float gather is not affordable on a sharded worker. Ours streams 256-row tiles and keeps an exact global cosine max with a smallest-token tie-break (`O statebridge.py:128-136`, all-reduce `:147-152`), then gathers only the winners (`:154-155`). |
| f | Where the continuous prefix is inserted | Mid-prompt, at a text marker inside the user turn: `R prompts.py:54` places `EMBEDDING_HINT_MARKER` at the head of the user prompt; `R state_bridge.py:544-548` tokenizes the text before the marker into `insert_position`; `:370` `full_embeds = torch.cat([left_embeds, prefix_embeds, right_embeds], dim=1)`. No marker means prepend (`:381`). | Reserved rows, never text. `O latent_worker.py:450-456` reserves `count` ids `at = descriptor.prefix_offset` (`new_ids = list(original[:at]) + holes + list(original[at:])`); `:674` `hidden_states[local:local + count].copy_(record.prefix[source:source + count])`. The marker path is now wired: `O chat_adapter.py:633-636` falls back to `PrefixLayout(LAYOUT_PREPEND, 0)` when `PREFIX_MARKER not in text`; `:641` `layout = resolve_prefix_layout(text, tokenizer)`; `:697-699` pins `obj.input_ids` and sets `ctx.prefix_offset`; `O latent_backend.py:161,179` carry it into the descriptor; `:761` refuses a mismatch. Marker token: `O prefix_layout.py:21` `PREFIX_MARKER = "<<occ3-statebegin>>"`. | Placement is available but *client-opt-in*: with no marker the prefix sits at absolute row 0, before BOS/system tokens, and shifts every later position by K. Nothing in the repo puts a marker in a prompt. | The receiver prompt must not be rewritten: the admitted request sets `skip_radix_cache_insert, occ3_private_kv, occ3_private_prefix = True` (`O latent_worker.py:474`), and the private-KV/radix invariants assume the client's own token stream. Reserve-and-overwrite plus a client-authored marker is the only shape that keeps prompt text, cache keys and receipts consistent. |
| g | Explicit natural-language framing that a latent message exists | `R prompts.py:53-56` `embedding_context_hint = f"The following is the message from previous Agent (provided in embedding format):\n{EMBEDDING_HINT_MARKER}\n\n"`, prepended to every receiving turn (`:69`, `:84`, `:98`), plus role bodies `:72` "The plan information is provided in embedding representation format." and `:100-102` "You are provided with embedding information for reference ... Ignore it if it is not helpful". `R latent_talk.py:98-100` states the same contract: "Agent A has sent you a message. The message appears between <msg> and </msg>." | **No framing sentence anywhere.** Grep over `extensions/sglang-occ3` for `previous agent`, `latent message`, `embedding format`, `provided in embedding`, `representation format` returns zero hits. The only signal is the bare delimiter `<<occ3-statebegin>>` left in the client text (`O prefix_layout.py:21`), and `O prefix_layout.py:4-5` states the policy: "The receiver's ordinary prompt is never rewritten." | Largest semantic gap: the receiver sees K anonymous continuous rows plus a delimiter string, with no instruction that they are a message or how to use them. Every reference tells the model what it is reading, and two of them tell it to ignore irrelevant content. | The adapter must not author prompt text: it handles untrusted client text (`O chat_adapter.py:633` only inspects it) and must stay a transport, so the sentence belongs to the client, next to the marker. Nothing in the repo supplies it. |
| h | How prefix length is chosen | Fixed global K, default 64: `R state_bridge.py:164` `max_prefix_tokens: int = 64` (CLI `:1130`); effective length = min(K, available post-think tokens) at `:595-597`. Pooling strategies could shorten it (`:321-340`) but the strategy is hard-wired to `"scale"` (`:1095`), i.e. multiply by 1.0, no length change. | Declared by the client before the message exists: `retained_count_limit in (8, 16, 32, 64, 128)` (`O latent_backend.py:214`, `O latent_types.py:132`) -> `max_prefix_tokens` (`O latent_backend.py:224`) -> ring size (`O latent_worker.py:767`); effective length = min(accepted, K) at `O latent_worker.py:309`. Receiver reserves the actual count: `expected_prefix_tokens=source["retained_count"]` (`O latent_backend.py:178`). | Same `min(...)` formula. Difference: K is a per-capture contract fixed before the first forward, and the shipped profile pins K = 128 versus the reference's tested 64. Never padded up to K in either. | K also sizes the receiver's reserved rows, the capture ring and the workspace bound (`O statebridge.py:158-168`), so it must be known before capture starts, not at alignment time. |

### 1.1 Notes where the table is too terse

1. **(b) pairing, INFERENCE.** The reference hook (`R state_bridge.py:119-133`) appends the last
   position of every forward, including the prompt forward, so capture row 0 predicts
   generated token 0; row `i` therefore pairs with `gen_token_ids[i]` as "the token it
   predicted". Its reconciliation at `:456-463` is asymmetric: it keeps the **last** `gen_len`
   hidden rows but, when `h_len < t_len`, keeps the **first** `h_len` token ids. If the lengths
   ever differ by one, the reference pairs row `i+1` with token `i`. That branch looks
   unreachable in the plain generate loop, so this stays an inference. Copy our invariant
   (`O latent_worker.py:749`), not their truncation.
2. **(b) which tensor, INFERENCE about HF internals.** The reference hook sits on
   `model_inner.model.layers[-1]` (`R state_bridge.py:402-410`), i.e. the last decoder layer
   output *before* the final norm, while its non-hook path uses `hidden_states[-1]` (`:473`),
   which is post-final-norm. So "StateBridge aligns the final-layer hidden state" is not one
   object across its own two paths. Ours is single-valued: pre-norm, at the norm boundary
   (`O capture patch`: "before final normalization, including fused add+norm"), declared at
   `O latent_worker.py:352`.
3. **(b) degenerate input.** `R state_bridge.py:242-243` returns raw, unaligned, unnormalised
   hidden states when `N < 2`. `O statebridge.py:43` refuses (`2 <= n <= 128 and d > 2 * n`).
   Fail-closed is deliberate; keep it.
4. **(c) what the parity gate actually proves.** `O tests/test_statebridge.py` executes the
   pinned reference `_align_hidden_sequence` verbatim (source hash asserted at `:98`) and
   requires relative error `< 1e-3` (`:114-138`). The fixture sets `snap_ratio=0.0` (`:132`)
   and is full centred row rank (`:117-119`), so anchoring and the determinant branch are not
   covered. The gate also *skips* when the reference checkout is absent (`:88-90`).
5. **(e) what those ablations are worth.** `R README.md:57`: "Full StateBridge 82.4. ...
   Removing norm calibration gives 79.5, removing vocabulary anchoring 80.2." Qwen3-4B
   averages, not GLM-5.3, but they rank the two: norm calibration first, anchoring second.
   We ship norm calibration on, anchoring off.
6. **(f) what the default costs.** `R README.md:125` notes the receiver "assigns sequential
   position indices over the concatenated input". Our prepend layout consumes positions
   `[0, K)` of the receiver sequence, so every chat-template token shifts by K against the
   native template. The marker path keeps the template prefix intact but needs the client to
   type `<<occ3-statebegin>>` in the right place.

## 2. What we are missing, ranked by expected semantic impact

1. **Receiver framing text (row g).** Highest impact. Every prompt-bearing reference
   (StateBridge, Interlat, CodeTalkers) tells the model that a latent message exists, where it
   is, and how to treat it; we insert rows and a delimiter only. Where it would change: the client prompt that must carry the sentence, resolved by
   `resolve_prefix_layout` (`O prefix_layout.py:59-81`) and consumed at
   `O chat_adapter.py:641` - the plumbing is in place, the words are absent. Because the
   marker is kept in the pinned ids (`O chat_adapter.py:642` encodes the text as-is), a
   sentence can be added without touching the worker or the descriptor.
2. **Vocabulary anchoring switched off (row e).** Second impact per `R README.md:57`
   (82.4 -> 80.2). Where: `"anchor_alpha":0.0` in `O native-profile-input.json` and the
   default at `O latent_types.py:118`; the tiled solver `O statebridge.py:115-155` already
   accepts 0.3 and is bounded by `workspace_bound` (`:158-168`), so the change is a profile
   value plus a live window, not new code.
3. **No post-thinking token selection (row a).** Third impact: with GLM-5.3 a short answer or a
   `max_new_tokens` cut yields a prefix made of reasoning. Where: the span contract
   `O attachment_contract.py:151` (validated at `:163` and `:432`) and the ring append at
   `O latent_worker.py:771-774`. A fix needs an attested think-end id offset from the
   frontend, since the worker has no tokenizer.
4. **Placement is opt-in (row f).** Medium impact, currently unmeasured: nothing in this repo
   inserts the marker into a prompt, so every live message uses the legacy prepend layout
   even though
   `O prefix_layout.py:84-100` can place the rows after the marker. Where: the client prompt
   construction; the adapter side is done (`O chat_adapter.py:697-699`,
   `O latent_backend.py:161,179`, `O latent_worker.py:450-456`).
5. **No determinant guard in the solver (row c).** Low impact, cheap: add the `det(R) < 0`
   correction of `R state_bridge.py:275-277` at `O statebridge.py:62-63` and extend the parity
   fixture to exercise it. Until then "matches the dense reference" holds for one fixture,
   not for every message.
6. **Deliberately absent, not missing:** `_process_prefix` scaling/noise/pooling
   (`R state_bridge.py:301-342`). The reference itself pins `"scale"` with
   `prefix_scale = 1.0` (`:186`, `:1095`), so it is a no-op there. Do not port it as if it
   were a technique.

## 3. What must NOT be copied

1. **Trained projectors and their losses (Interlat).** `AdaptiveProjection`
   (`R model_insert_hidden.py:646-677`), the MHA stack `process_hidden_states` (`:760-781`),
   the `AlignBlock` cross-attention / conditional-LoRA adapter (`R align_block.py:169-267`),
   the curriculum hidden/plan mix (`:1209-1211`), and the plan-similarity KL plus
   random-contrast terms (`:1354-1360`). They need checkpoints and a training pipeline; our
   method is training-free and reproducible from the pinned `ALIGNMENT` string
   (`O latent_types.py:13`) plus code, with no extra weights.
2. **Vocabulary surgery.** Interlat adds `<FIRST_HUMAN_END>`, `<bop>`, `<eop>` and resizes the
   embedding matrix (`R hidden_agent.py:423-424`, helper `:96-106`). That is a model
   modification: it changes the tokenizer and embedding rows, which invalidates our
   `model_fingerprint` / layout digest and the frozen-model contract.
3. **KV-cache transfer as the semantic channel.** `R README.md:42`: "KV-cache transfer
   averages 55.1% while plain text averages 73.9%" (OLMo3-7B-Think). Our repo also carries a
   carrier/KV path whose only delivery evidence is a cache count: `O recv_path.py:51`
   `hit = cached >= channel.carrier_len`. Keep it as transport if needed; never treat a cache
   hit as proof that latent semantics arrived.
4. **Soft-token protocols.** CodeTalkers' superposition rollout (`R latent_talk.py:53-70`,
   `v = p @ EMB[topi]`) and Coconut's latent-slot feedback (`R coconut.py:143-150` "replace it
   with the preceding last hidden states"). Both change the sender's decode loop (it emits
   vectors instead of sampled tokens) and the receiver's. Our contract is capture-only: the
   sender's ids are the client's ids, checked at `O latent_worker.py:749`, and the receiver's
   KV must stay reproducible from the admitted token layout. Porting either breaks both.
5. **LatentMesh's fitted transform, as-is.** `R lib.rs:93-134` fits `R`/`alpha`/`mu_s`/`mu_r`
   on calibration pairs, `:246-249` reports a confidence, `:275-293` applies it. We already
   use its one transferable idea, identity completion outside the calibrated subspace
   (`O statebridge.py:34-39`); a fitted transform would add stored calibration state and a
   second model's hidden states - a new external dependency, not a per-message solve.

## 4. Open risks

- Line numbers are pinned to section 0, and three of those files changed during this read.
  Any review must re-hash first; a mismatch means re-derive, not re-quote.
- The parity test skips without the reference checkout (`O tests/test_statebridge.py:88-90`),
  so a clean CI machine does not enforce it.
- `anchor_alpha = 0.3` is allowed but has never run on GLM-5.3 here; enabling it changes every
  prefix row and needs its own live window, not a config flip.
- Whether GLM-5.3 tolerates a prefix placed before its system turn is unmeasured. INFERENCE
  from the prompt-bearing references: StateBridge, Interlat and CodeTalkers all put the latent
  span after natural text inside a turn; Coconut feeds latents in its own generation stream;
  LatentMesh defines no prompt at all.
- No reference reports a 6144-dim, 77-layer, 8-way-TP receiver, so every cost figure above is
  our own arithmetic, not a published number.
