# Predictor/token alignment in OCC-3 capture

**Verdict: the suspected predictor/token off-by-one does not exist.** Every
captured hidden row is paired with the token it actually predicts, and the
relation is the same in prefill and in EAGLE/MTP verify: **the pre-final-norm
state at absolute position `p` predicts the token at position `p + 1`
(ALIGNMENT B)**. `commit_result`'s `arange(len(origin) + output_start - 1, ...)`
assertion is satisfiable, is satisfied, and is the right check — it is not
masking a shifted pairing.

Evidence base: pinned SGLang `e00930c5489053f26d86b179cee0d087f846acbb` (v0.5.21)
at `../occ3-latent-validation/references/sglang` (unpatched; every
`before_sha256` in `extensions/sglang-occ3/patches/*.json` matches), plus this
repo's `extensions/sglang-occ3/occ3_sglang/latent_worker.py`. Paths below are
relative to the pinned source root unless they start with `extensions/`.
Reproduce with `tests/test_predictor_alignment.py` (22 passed, no GPU, no torch).

Line numbers are for the working tree at the time of writing
(`latent_worker.py` sha256 `86b5f972...`, 990 lines). The capture, commit and
`align()` paths cited here are unchanged from the 963-line revision this
analysis started on; the injection-placement code (`prefix_offset` /
`prefix_span`, `_span`) is being changed concurrently by another task. That work
moves *where* a prefix row lands, not *which token* it belongs to, so it does
not affect the alignment result below. The test file pins expressions rather
than line numbers, so it survives that drift.

## Notation

`P = len(req.origin_input_ids)`, `K = len(req.output_ids)` at the start of a
step (`K == output_start == record.accepted_count`), `nd` = verify rows per
request.

## A. Prefill: the last prompt row predicts `next_token_ids[0]` (yes)

- `layers/logits_processor.py:672-680`: for extend without input logprobs,
  `last_index = torch.cumsum(extend_seq_lens, 0) - 1` and
  `pruned_states = hidden_states[last_index]`. Generation logits therefore come
  from the **last** row of each request's chunk, and `next_token_ids[0]` is
  sampled from it.
- `latent_worker.py:508` selects exactly that row
  (`rows = [item.extend_start + item.extend_len - 1]`) and `:513` stores
  `forward_batch.positions[rows]`.
- `model_executor/forward_batch_info.py:1125-1132`: extend positions are
  `compute_position(prefix_lens, seq_lens, ...)`, i.e. `prefix_len + i`, so the
  captured row carries **`P - 1`** for a whole-prompt prefill.
- `latent_worker.py:541` stages `next_token_ids[:1]` as the paired token and
  `:746-749` asserts `positions == arange(P - 1, P - 1 + 1) == [P - 1]`.
- The captured tensor is the pre-norm value: `layers/layer_boundary/residual/access.py:106-120`
  (`norm_output`) calls `capture_output(residual)` on the **completed residual**,
  after the fused add+norm has already written the terminal residual and before
  the norm is applied for the LM head.

Result: row at position `P - 1` → predicts `output_ids[0]` at position `P`.
ALIGNMENT B. `positions[row]` is the row token's own absolute position.

## B. Verify: `predict[id]` is the token AFTER that row's position

Chain of evidence:

1. Two token streams are concatenated: `speculative/eagle_utils.py:168`
   `draft_tokens = torch.cat((bonus_tokens.unsqueeze(1), draft_tokens), 1)`, so
   verify row 0 is the carried-over bonus token and rows 1.. carry the draft
   nodes.
2. Positions: `speculative/eagle_utils.py:224-227` documents
   `positions` as "where each token belongs to", e.g. prompt length 7 →
   `positions = [7, 8, 8, 9]`. The kernel that fills it
   (`python/sglang/kernels/jit/include/sgl_kernel/speculative/eagle.cuh:70-72`
   and `:106-122`) writes `positions[node 0] = seq_len` and
   `positions[node] = seq_len + parent_depth`. So a row's position is the
   absolute position of the token stored in that row, one past the last cached
   prompt token.
3. KV slots: `speculative/eagle_utils.py:555-576` sets
   `batch.input_ids = verify_input.draft_token` and
   `assign_extend_cache_locs_uniform_func(..., start_offset=batch.seq_lens, draft_token_num=...)`.
   Row 0's KV slot is `seq_lens`, i.e. its own position. `seq_lens` lags the
   emitted sequence by exactly one token (see C).
4. `forward_batch.positions` for the verify forward **is**
   `verify_input.positions`: `model_executor/forward_batch_info.py:1096-1100`
   (`ret.positions = ret.spec_info.positions`), built at
   `eagle_utils.py:389-404` (`EagleVerifyInput(positions=position, ...)`). The
   capture hook and `stage_verify` therefore read the identical tensor.
5. Logits: `layers/logits_processor.py:633-652` — `TARGET_VERIFY` is in the
   unpruned branch, `pruned_states = hidden_states`, so `next_token_logits[r]`
   is the LM head applied to hidden row `r` (same row index as `positions[r]`).
6. Kernel semantics, `eagle.cuh:214-256` (`VerifyTreeGreedy`):
   - `:229-230` `accept_index[block start] = retrive_index[...]` = node 0;
   - `:239` `target_token_id = target_predict[last_accepted_retrive_idx]` — the
     target's greedy token read **at the parent row**;
   - `:241-247` on a match, `predicts[last_accepted_retrive_idx] = target_token_id`
     — the child token is written into the **parent's** slot;
   - `:255` `predicts[last_accepted_retrive_idx] = target_predict[last_accepted_retrive_idx]`
     — the last accepted row receives the bonus token that follows it.
7. `eagle_utils.py:799-805` zero-initialises `predict` (so rows outside the
   accepted run keep `0`), `:819-829` passes it as a mutable kernel argument,
   and `:1044-1047` returns `predict, num_correct_drafts + 1, accept_index`.
8. The patch inserts `stage_verify` after `eagle_sample` and before
   `new_seq_lens` (`speculative/eagle_worker_common.py:591-602` anchors), so
   `predict` is already post-mutation, and before the `topk > 1`
   `_finalize_accept_tree_path` compaction (`:643-655`) that would otherwise
   move rows out from under `accept_index`. Verified by
   `test_stage_verify_reads_predict_after_eagle_sample_and_before_compaction`.

**Invariant (plain):** for every row `r`, `predict[r]` is the token at absolute
position `positions[r] + 1`. `predict[accept_index[i, k]]` is therefore the
token that follows the accepted node `k`'s own token — the token committed at
the next output index, not the token at that row.

Corroboration from the bonus path: `kernels/ops/speculative/eagle.py:26-27`
(`bonus_token_idx = accept_stride * pid + accept_len - 1`) makes the bonus the
last accepted slot of `predict`, and
`speculative/eagle_worker_v2.py:1015-1025` feeds `bonus_tokens=next_token_ids`
into the next verify round as row 0. Both are "the token after the row".

## C. Why the commit assertion starts at `len(origin) + output_start - 1`

SGLang's own statement of the lag:
`managers/scheduler_components/batch_result_processor.py:1496`
`token_seq_len = len(req.origin_input_ids) + len(req.output_ids) - 1`.

Reason: prefill appends its first sampled token to `output_ids` before that
token ever reaches KV (`batch_result_processor.py:356-358`), and each verify
step then appends the whole accepted run
(`:1014-1016` `req.output_ids.extend(next_token_id)`, `:788-804`
`accept_tokens = next_token_ids[i*stride : i*stride + accept_lens[i]]`,
"Commit the full accepted run (drafts + bonus).") while `batch.seq_lens` grows
by the same `accept_lens` (`managers/scheduler.py:4561-4565`). Hence, at every
verify step,

    seq_lens == len(origin_input_ids) + len(output_ids) - 1 == P + K - 1

and `positions[accepted row k] = seq_lens + k = P + K - 1 + k`, contiguous
because acceptance always descends one tree level (the kernel only accepts a
child of the last accepted node, `eagle.cuh:234-247`).

`commit_result` (`latent_worker.py:746-749`) asserts exactly
`arange(P + K - 1, P + K - 1 + count)`, with `K == output_start` enforced at
`:740` (`item.output_start == output_start == record.accepted_count`).
`output_end` comes from `_visible_output_len(req)`
(`batch_result_processor.py:213-214`), so `count`
may only be a **prefix** of the accepted run (grammar/stop truncation), which
keeps the assertion valid.

So the answer to "position `P + K` or `P + K - 1`?" is **`P + K - 1`**, and the
reason is not a special "draft rows carry position - 1" convention: draft rows
carry the **absolute position of their own token**, and `P + K - 1` is that
token's position precisely because the newly emitted bonus token is in
`output_ids` but not yet in KV.

Satisfiability, both directions:

- **Rows carry their own position (actual SGLang):** `predict[row]` is the
  token at `position + 1`, so `stage.tokens[0, :count]` equals
  `output_ids[K : K + count]` — the assertion passes. Verify capture commits.
- **Counterfactual "rows carry the predicted token's position":** row 0 would
  sit at `P + K - 2`, so the positions comparison would already fail on the
  first row; and even if it did not, `predict[row]` would be the
  already-emitted `output_ids[K - 1]`, making `stage.tokens` equal
  `output_ids[K - 1 : K - 1 + count]` and failing the token comparison too.
  `commit_result` would therefore fail on every verify step: the request would
  abort with `occ3_commit_failed` and the state would never publish. That is
  the failure mode the assertion is designed to catch, and it is not what the
  kernel produces.

`test_commit_assertion_matches_kernel_positions` and
`test_ring_slots_are_output_token_indices_across_steps` encode this; the
counterfactual is `test_alignment_a_pairing_would_shift_the_ring_by_one`.

## D. Conclusion: which output-token index does each row predict?

| capture site | row | `positions[row]` | predicts output index | ring slot |
| --- | --- | --- | --- | --- |
| prefill final chunk (`latent_worker.py:508`) | last prompt token | `P - 1` | `0` | `0` |
| verify, accepted step `k` (`:571-574`) | `accept_index[k]` | `P + K - 1 + k` | `K + k` | `K + k` |

Ring slot = `(record.accepted_count + i) % capacity` (`:772`) with
`accepted_count` starting at 0 after prefill, so the ring is indexed by output
token index, and for every slot `j`: `hidden position == (token position) - 1`.

- **Is pairing `H[row]` with `embedding(predict[row])` self-consistent? Yes.**
  `align()` (`:304-364`) takes `tokens = ring_t[order]` (`:321`),
  `embeddings = self.embedding(tokens)` (`:325`) and solves
  `align_hidden_sequence(h, embeddings)` (`:333`) row-for-row. `h[j]` is the
  state at position `p_j = P + j - 1` and `embeddings[j]` is the embedding of
  the token at `P + j`, so the fitted map is "state at `q` -> embedding of the
  token at `q + 1`", applied to row `j` to produce the carrier for token `j`.
- **Uniform, not mixed.** Both capture sites are ALIGNMENT B, and the recorded
  metadata says so once: `predictor_position_offset: -1` (`:352`, the only such
  declaration). The offset is not compensated by an index shift later; it is
  absorbed by the learned Procrustes map, so the row index stays the output
  token index throughout.
- **Injection consumes rows by that same index, one row per position.** The
  prefix array is indexed by output token index (`accepted_span_start` .. `-1`,
  `:346-352`), and both injector paths copy the rows in increasing order with
  stride one, so row `k` lands on the consumer position that holds output token
  `start + k` (`inject_target_embeddings`, `:650-685`; `draft_input_embeds` ->
  `_effective(..., 1)`, `:689-697`, `:598-640`). Where that run lands is decided
  by the admitted layout (`prefix_offset`: "prepend" holes or a "marker" block,
  `_span`/`prefix_span`, `:590-595`); a layout change moves rows, it must not
  re-pair them, which is why the row index is the join key. The draft path
  additionally passes `shift=1` because the draft-extend batch is natively
  rotated left by one (`speculative/eagle_worker_v2.py:929-931`,
  `input_ids[1:]` + tail token): a draft row at position `q` consumes the
  embedding of the token at `q + 1`, which is prefix row `q + 1`.
- The pre-norm state is the right carrier to fit against embeddings in the
  first place: it is the tensor the LM head normalises for that same row
  (`residual/access.py:106-120`), so "the state at `p` predicts the token at
  `p + 1`" is the model's own contract, not an OCC-3 convention.


## Residual risks (no off-by-one, but real)

1. **`seq_lens` lag is an external invariant.** The commit assertion is exact,
   so any scheduler state where `batch.seq_lens != P + K - 1` at verify time
   aborts the request (`occ3_commit_failed`) instead of mis-pairing. Overlap
   scheduling must publish `new_seq_lens` for exactly the accepted run
   (`managers/scheduler.py:4561-4565`; overlap path
   `managers/overlap_utils.py:535`). Watch this under PP/overlap combinations;
   it fails loudly, never silently.
2. **Rows outside the accepted run are zero-filled** (`eagle_utils.py:801`).
   `stage_verify` masks them (`latent_worker.py:566-571`, `valid`) and
   `commit_result` reads
   only `[:, :count]`, so today they never reach the ring. Any future change
   that selects padded rows would silently record token id `0`.
3. **Grammar/stop truncation** shortens the committed run through
   `_visible_output_len` = `finished_len`. The committed span is then a prefix
   of the verified run, so the pairing stays right, but the ring ends mid-run.
4. **Profile caps are guards, not alignment:** `nd <= 6` (`:502`) and
   `accept_index` width `<= 6` (`:566`). A wider EAGLE config aborts rather than
   mis-aligns.
5. **Model-specific capture site.** The hook is wired only into
   `models/deepseek_v2.py` (patch #0) at `self.norm`; the NextN draft module's
   own norm (`models/deepseek_nextn.py:231-233`) is left native, so the draft
   forward cannot double-capture. The *index* claim above is
   architecture-independent, but a non-DeepSeek family needs its own hook and
   its own attestation.
6. **Decode-step draft injection is not wired.** `draft_input_embeds` is
   patched only into `_draft_extend_for_prefill`
   (`speculative/eagle_worker_v2.py:965-976`); later draft decode steps use
   native embeddings. This is a coverage/completeness question for the
   statebridge design, not an alignment error, and was not validated on GPU
   here.

## Minimal fix

None is required for the predictor/token pairing; `latent_worker.py` was not
modified. If a future consumer wants the state that sits **at** a position
rather than the state that predicts the next token, the minimal change is
confined to `align()`: pair `h[j]` with `embedding(tokens[j + 1])` (drop the
last retained row, or shift the retained window by one) and set
`predictor_position_offset` to `0`. Doing that while keeping `predict[row]`
would shift every ring slot by one and must not be combined with the current
`commit_result` assertion.

## Tests

`extensions/sglang-occ3/tests/test_predictor_alignment.py` — 22 tests:
10 source-attested assertions on the pinned tree, 2 patch-anchor/ordering
checks (all 12 skip when `OCC3_SGLANG_SOURCE` and the fallback path are absent),
1 pin on our own capture/commit expressions, and 9 pure-Python mirrors
including a greedy `VerifyTreeGreedy` simulation of the index arithmetic.

    cd extensions/sglang-occ3 && .venv/Scripts/python.exe -m pytest tests/test_predictor_alignment.py -q
    22 passed
    # OCC3_SGLANG_SOURCE=<missing>: 10 passed, 12 skipped
