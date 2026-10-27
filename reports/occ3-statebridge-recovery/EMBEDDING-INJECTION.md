# Embedding injection: receiver continuous-prefix geometry, end to end

Scope: where the receiver's `expected_prefix_tokens` (P) rows live in the prefill sequence, what the
injected rows overwrite, and whether positive and no-state receiver turns are input-equivalent at the
model level. Read-only trace; no source file was modified by this task.

## 0. Traced revision (pin) and observed drift

Line numbers below are pinned to the tree state at **2026-10-27 13:33:45 UTC**. The four Python files were
rewritten twice while this trace was running, so re-derive line numbers before editing.

| file | sha256 (first 16) | bytes | mtime |
|---|---|---|---|
| `extensions/sglang-occ3/occ3_sglang/latent_worker.py` | `86b5f972f28eecdb` | 51605 | 13:33:45 |
| `extensions/sglang-occ3/occ3_sglang/chat_adapter.py` | `d8eb3cc9056e2c60` | 82873 | 13:33:45 |
| `extensions/sglang-occ3/occ3_sglang/latent_backend.py` | `6ffb2f594165f656` | 17259 | 13:33:45 |
| `extensions/sglang-occ3/occ3_sglang/attachment_contract.py` | `b4c6a8fe66ad3758` | 18229 | 13:33:45 |
| `extensions/sglang-occ3/occ3_sglang/prefix_layout.py` | `936e8f17ea72c778` | 3857 | 13:20:11 |
| `extensions/sglang-occ3/patches/scheduler-cache-ipc.v1.json` | `38e22e6a036eb919` | 67403 | 08:49:51 |
| `extensions/sglang-occ3/patches/capture-eager-mtp.v1.json` | `b25fd58c2be1bea5` | 30415 | 07:33:12 |
| `extensions/sglang-occ3/tests/test_prefix_placement.py` | `ab577c6b5ffc6b4c` | 9283 | 13:25:08 |

Drift observed in this session, in order:

1. `latent_worker.py` at `f5c95bfffc9f7efc` (09:29) - reservation always prepended at absolute row 0, no
   `prefix_offset` field, no `prefix_layout.py`.
2. 13:31:41 - `prefix_layout.py` deleted; the three consumers rewritten without `prefix_offset` (prepend-only again).
3. 13:33:45 - marker layout restored: `prefix_layout.py`, `ConsumeWorkerDescriptor.prefix_offset`,
   `_span()`, receipt fields.

Consequence for this report: **both layouts exist and are selectable at runtime**. "Prepend" (no marker in the
client's rendered prompt) is the default and is the layout the question below assumes; "marker" is the
opt-in layout now wired end to end. Every answer states which layout it applies to.

Offline state of the traced snapshot: `tests/test_latent_worker.py` + `tests/test_chat_attachment.py` +
`tests/test_span_audit.py` = **89 passed, 53 skipped**; `tests/test_prefix_placement.py` = **15 passed**.

## 1. Verdict

Dedicated prefix positions **do** exist on the consume path, and the ordinary rendered prompt is **not**
rewritten. `latent_worker.admit_request()` inserts P zero-id rows into the receiver's own token stream
(`latent_worker.py:446-456`, commit at `470-475`): at `descriptor.prefix_offset` in the marker layout, or at
absolute row 0 in the legacy prepend layout. `inject_target_embeddings()` then overwrites exactly those P
rows of `hidden_states` with the aligned source prefix (`latent_worker.py:667-674`).

So the "positive run destroys the receiver's BOS/template/SYSTEM tokens" hypothesis is **false** in this tree.
The real defects are (a) placement/framing - P anonymous rows with no in-prompt referent, and (b) position
space - the receiver sequence gains P positions, so positive and no-state are not input-equivalent even
though the rendered prompt text (and its hash) is identical.

## 2. Question table

| Q | Answer | Evidence (file:line) | Verdict |
|---|---|---|---|
| **Q1** | Fresh consume request, radix match 0, legacy prepend layout (no marker - the default): `item.prefix_len = 0`. `count = prefix_overlap(P, 0, extend_len) = min(extend_len, P)` = **P (128)** for any first chunk of 128+ tokens (marker layout: `min(extend_len-offset, P)`, i.e. 0 while the chunk ends before the marker). `prefix_len` is `len(req.prefix_indices)`, which `occ3_private_match` leaves empty on purpose (`num_matched_prefix_tokens = 0`). | `latent_worker.py:396,404,419` (context from `batch.prefix_lens`); `latent_worker.py:585-587` (`prefix_overlap`); `latent_worker.py:590-595` (`_span` -> `prefix_span`); applied `managers/schedule_batch.py:2741` (`prefix_lens = [len(r.prefix_indices) for r in reqs]`); `scheduler-cache-ipc.v1.json` -> `mem_cache/common.py` rep 5 (`occ3_private_match`, `num_matched_prefix_tokens = 0`). Executed: `prefix_span(128,0,0,8192) == (0,128,0)`. | VERIFIED |
| **Q2** | **Yes, a path exists** (consume only, never capture): `admit_request` builds `new_ids` = original with P zero ids spliced in, then writes `req.origin_input_ids`, `origin_input_ids_unpadded`, `full_untruncated_fill_ids` and sets `occ3_private_prefix` so the radix tree cannot merge or reuse it. `at = descriptor.prefix_offset` (0 = legacy prepend, >0 = marker). No other code path enlarges the prefill: rust sends text only (0 hits for `input_ids` in `crates/pi-coding-agent/src/core/occ3_connectors/sglang.rs`; `prefix` there is only `continuous_prefix` readiness at `:283`, `:476` and the `statebridge-prefix` method name at `:658`); the adapter refuses client ids and client embeds; the patches insert no ids. | `latent_worker.py:446-456` (splice), `470-475` (commit), `475` (`occ3_private_prefix`); `chat_adapter.py:476-479` (client `input_ids`/`return_hidden_states` refused), `606-610` (`conflicts` incl. `input_embeds`, `positional_embed_overrides`), `696-700` (adapter pins `obj.input_ids` to the same ids it offset-measured); wiring `scheduler-cache-ipc.v1.json` -> `managers/scheduler.py` rep 4 (applied `scheduler.py:3024-3032`) and `managers/tokenizer_manager.py` rep 4 (applied `tokenizer_manager.py:1276-1281`, budget charges `len(input_ids)+prefix_count`); descriptor field `attachment_contract.py:370,379,394`; `latent_backend.py:161,169,179`; tests `tests/test_prefix_placement.py:93-108`, `tests/test_latent_worker.py:47,410`. Executed end to end in this session: the real patched `handle_generate_request` with a stub `Req` and the real `admit_request` produced `origin_input_ids == [0]*8 + [101..105]`. | VERIFIED |
| **Q3** | (a) `hidden_states` at the call site **is** `embed_tokens(forward_batch.input_ids)` of the extend chunk - no clone, no sidecar: the overlay calls `inject_target_embeddings` on the line after the native lookup and the row count is pinned to `extend_len`. (b) Therefore rows `[local, local+count)` are the embeddings of the chunk's ids at those offsets - which are the **reserved zero rows**, not the prompt. Prompt token 0 sits at row `P` (prepend) or stays at row 0 with the reserved block starting at `offset` (marker). "Rows 0..127 are the rendered prompt's first tokens (SYSTEM first)" is **false** unless the reservation is removed. | `capture-eager-mtp.v1.json` -> `models/deepseek_v2.py` rep 2 (call inserted directly after `hidden_states = self.embed_tokens(input_ids)`, attested `deepseek_v2.py:3024-3029`); `latent_worker.py:608` (`input_ids.numel() == extend_len`), `669-670` (shape must be `(extend_len, hidden_dim)`), `674` (in-place copy). | VERIFIED |
| **Q4** | Nothing in the receiver's prompt is destroyed today. What differs between positive and no-state, at the same HTTP body hash: (1) **P extra positions** - `positions = arange(prefix_len, prefix_len+extend_len)`, so every prompt token after the reservation is shifted by +P (prepend) or by +P after the marker offset; (2) the injected rows were captured at **source** positions (`predictor_position_offset = -1`) but are attended at receiver positions `[offset, offset+P)`; (3) P anonymous attention keys with no referent in the text, so a correct injection can be semantically inert. If the reservation were ever absent, the destroyed content would be the GLM template control/BOS tokens and the head of the SYSTEM turn - and nothing would catch it: the terminal gate counts positions only and the receipt carries a count only. | positions `model_executor/forward_batch_info.py:2140-2154` (attested); capture positions `latent_worker.py:513`; `predictor_position_offset` `latent_worker.py:352`; span `latent_worker.py:667-674`; terminal count gate `latent_worker.py:753-756`; receipt `latent_worker.py:791-797`; client-side check `use_receipts.py:70`. | VERIFIED (mechanics) / INFERENCE (semantic causal link) |
| **Q5** | Arithmetic is `count_i = max(0, min(extend_len_i, P - prefix_len_i))` at offset 0, and `prefix_span` generalises it. Injected rows accumulate as **absolute** positions, and the terminal state flips only when the union equals P; the terminal commit refuses otherwise. With P=128, chunk 64: chunk 1 injects 64 rows, chunk 2 injects the other 64, then `state = "injected"`. Nothing is skipped when `extend_len < P`; the completion is only lost if the sequence ends before the reserved block is covered, and then the request is refused (`lineage`), not silently accepted. | `latent_worker.py:585-595`, `667-674`, `700-719`; terminal `753-756`; `prefix_layout.py:84-100`. Executed: `(128,0,0,64)=(0,64,0)`, `(128,0,64,64)=(0,64,64)`, `(128,0,128,64)=(0,0,0)`, `(8,0,0,3)/(8,3,3)/(8,6,3)` -> 3,3,2 rows; marker `(128,400,0,500)=(400,100,0)` then `(128,400,500,500)=(0,28,100)`. | VERIFIED |
| **Q6** | Yes - every TP rank injects byte-identical rows. The prefix tensor is solved once on rank 0 and broadcast to the group; the anchor tiling is rank-invariant (all-reduce MAX then MIN over the sharded vocabulary); each rank then copies the same slice into its own `hidden_states` (embedding lookup is replicated, so rows agree before any TP collective). Agreement is enforced, not assumed: every preflight all-gathers `{phase, request, binding, error}` with binding `{count, start/offset, dtype}` and poisons the runtime on disagreement. | `latent_worker.py:339` (`torch.distributed.broadcast(prefix, src=self.group.ranks[0], ...)`), `337-338` (solver-error gather), `157-175` (`preflight`), `615-617` and `683-684` (bindings), `statebridge.py:150-152` (all_reduce MAX/MIN), `latent_worker.py:609-610` (prefix shape = `(P, hidden_dim)` on every rank). | VERIFIED |
| **Q7** | See section 6. The branch already carries most of the red-first set in `tests/test_prefix_placement.py`; the still-missing assertions are listed there and are the ones that would have caught the original failure. | `tests/test_prefix_placement.py:48-228`; fakes pattern `tests/test_latent_worker.py:181-201,222-296`. | VERIFIED (test inventory) |

## 3. Q4 in one paragraph

The renderer's output never changes, so the body hash matches and the prompt text is preserved; that is exactly
why the failure looked semantic rather than structural. What changes is the model input: P rows of zeros are
inserted as real sequence positions, so the receiver's own tokens start at position P (prepend) or shift by P
after the marker, and the P latent rows are attended at positions they never occupied on the source side. A
positive turn therefore differs from no-state by more than the injected content - it also carries a global
position shift and P unlabelled attention keys. Both are invisible in the receipt (`injected_prefix_count`
only, plus a digest of the injected row indices), so a "matched positive/no-state" comparison is not a
like-for-like comparison at the model level even when the two HTTP bodies are identical.

## 4. Q5 arithmetic, spelled out

`prefix_span(tokens=P, offset, prefix_len, extend_len, shift)` returns `(local_start, count, source_start)`:
`chunk_start = prefix_len + shift`, `low = max(chunk_start, offset)`, `high = min(chunk_start + extend_len,
offset + P)`, and `(low - chunk_start, high - low, low - offset)` when `high > low`, else `(0,0,0)`
(`prefix_layout.py:84-100`). Rows written: `hidden_states[local : local+count] <- prefix[source : source+count]`
(`latent_worker.py:674`); recorded positions: `range(prefix_len+local, prefix_len+local+count)`
(`latent_worker.py:713`).

* Prepend (offset 0), P=128, first chunk 8192: `count = 128`, `injected_positions = {0..127}`, state `injected`
  after the first chunk (`latent_worker.py:716-719`).
* Prepend, P=128, chunk-limited to 64: chunk 1 `{0..63}`, chunk 2 `{64..127}` - still complete, no gap.
* Marker at offset 400, P=128, chunk 8192: chunk 1 writes local rows 400..527 (absolute 400..527);
  if a chunk boundary splits the block (`prefix_len=256`), it writes local 144..271 instead - the split is
  arithmetic, not a special case.
* Straddling the end of a 500-token chunk: `(400,100,0)` then `(0,28,100)` - 100 + 28 = 128.
* Draft/NextN replay (`shift=1`, `latent_worker.py:697`): `prefix_span(128,0,0,8192,1) = (0,127,1)`; the last
  accepted token is skipped because the draft batch already re-reads it.
* If the reserved block is never covered, `len(record.injected_positions) != P` and the terminal commit raises
  `lineage` (`latent_worker.py:753-756`): fail-closed, no receipt.

## 5. Minimal correct design (described, not implemented here)

The shape in this snapshot is right; what is missing is attestation, not geometry.

1. **Reserve rows, never rewrite text.** `chat_adapter._prefix_layout()` resolves an ordinary ASCII marker in
   the client's rendered prompt with the same tokenizer that will tokenize the turn, pins `obj.input_ids` to the
   ids it measured, and records `(layout, offset)` in the context (`chat_adapter.py:625-649`, `696-704`).
   Absent marker keeps the legacy prepend at row 0 and must be reported as such, never as the marker layout.
2. **Carry the offset in the descriptor.** `ConsumeWorkerDescriptor.prefix_offset` (default 0, bounded
   `<= 2**20`, `attachment_contract.py:379,392`) travels frontend -> scheduler so the worker never guesses;
   `latent_backend.reserve()` sets it from `NativeRequestView.prefix_offset` (`latent_backend.py:161,179`).
3. **Splice, then inject by span.** `admit_request` splices P hole ids at `offset` (`latent_worker.py:446-456`)
   and `_span()`/`prefix_span()` gives `(local, count, source)` for every chunk, target and draft
   (`latent_worker.py:590-595`, `prefix_layout.py:84-100`).
4. **Fail closed on any divergence.** `mark_injection` re-derives the span and requires the executed span to
   match (`latent_worker.py:710-712`) before adding absolute positions.
5. **What receipts must record** (current gaps in parentheses):
   * `prefix_layout` (`"marker"`/`"prepend"`) and `prefix_offset` - present in the worker terminal metadata
     (`latent_worker.py:791-794`) but **dropped** by `latent_backend.finish()`
     (`latent_backend.py:203-207`) and therefore absent from `TerminalUseEvidence` and from
     `use_receipts.validate_evidence()` (`use_receipts.py:62-85`);
   * the reserved span `[offset, offset+P)` and the *first and last* absolute injected position, not only
     `injected_prefix_count` (`latent_worker.py:792`);
   * a digest of the per-chunk span list `(prefix_len, extend_len, local, count, source)` so a verifier can
     replay the arithmetic without tokens;
   * the ids that were replaced - at minimum `sha256` of the reserved id vector, or a proof that every reserved
     id equals the reserved hole id - so "reserved" can be distinguished from "overwritten prompt";
   * the tokenizer/prompt identity used to compute `offset` (the adapter's pinned id digest), so a marker
     offset can never silently drift from the sequence the scheduler prefills.

## 6. Red-first regression assertions (Q7)

Fixtures to reuse: `tests/test_latent_worker.py:181-201` (`Buffer`/shape-only fake), `:222-246`
(`_Rows`/`_RowSpan` content fake, `_Ids`), `:257-264` (`_injection_runtime`), `profile()` from
`test_latent_types.py`, `consume(...)` from `test_latent_registry.py`, and the applied-overlay extractor
`PinnedCase.sources.function("managers/scheduler.py", "handle_generate_request", "Scheduler", ...)` from
`tests/test_latent_ipc_patch.py:54-113` (needs the pinned snapshot; it is present at
`occ3-campaign/v3-controlled-deploy/original-sglang-source-attestation-v2`).

Must fail before the fix, pass after:

1. `admit_request` inserts exactly P ids at `prefix_offset` and changes no other id: prompt `[11,12,13]`,
   offset 2, P=4 -> `[11,12,0,0,0,0,13]` for `origin_input_ids`, `origin_input_ids_unpadded` **and**
   `full_untruncated_fill_ids`. (Exists: `tests/test_prefix_placement.py:93-108`.)
2. The ids at the reserved rows are the reserved hole id, not prompt ids: assert
   `input_ids[offset:offset+P] == [hole]*P` and `input_ids[offset+P:] == prompt_tail`, with a prompt that
   itself contains the hole id elsewhere, so a shift bug cannot pass.
3. Injection writes only those rows, content-exact: `_Rows` fake, prompt rows keep native lookup values,
   `hidden[offset:offset+P] == prefix`, neighbours untouched, `occ3_effective_embeds == (offset+P*0.., count)`.
   (Exists: `tests/test_prefix_placement.py:167-187`.)
4. Terminal gate is layout-aware: drive `commit_result(..., terminal=True)` with a record whose
   `injected_positions` covers `[0, P)` while `descriptor.prefix_offset = 200`; it must raise `lineage`
   (today only the count is compared, `latent_worker.py:753-756`). **Missing.**
5. Rank disagreement is fatal: fake `gather` returning `{"count": P}` on rank 0 and `{"count": P-1}` on rank 1
   must raise `LatentError("ranks")` and set `runtime.poisoned` (`latent_worker.py:158-175`). **Missing for the
   injection phase specifically** (there are consensus tests for align/metadata only).
6. Legacy prepend is *reported*, not inferred: a receiver turn with no marker must produce receipt fields
   `prefix_layout="prepend", prefix_offset=0`, and the frontend `TerminalUseEvidence` must carry them.
   **Missing** (`latent_backend.py:203-207`, `use_receipts.py:62-85`).
7. Chunked-prefill coverage: for `P=128`, chunk sizes 1..P+1, the union of injected spans must equal
   `range(offset, offset+P)` exactly once - and a chunk sequence that cannot cover the block must refuse at
   the terminal gate. Pure-integer test over `prefix_span` + `mark_injection`. **Missing** (only
   `test_prefix_span_*` unit cases exist).
8. Draft shift: `draft_input_embeds` on a chunk that starts at the marker boundary must inject
   `P-1` rows with `source=1` (`(128,0,0,8192,1) -> (0,127,1)`), and must return `None` outside the window. **Partial** (`tests/test_latent_worker.py:206-219`).

## 7. Open risks

1. **Churn.** The reservation code was added, removed and re-added inside 5 minutes (section 0). Any conclusion
   here is only valid for the pinned hashes; a run against an older image measures the prepend-only layout.
2. **Positional mismatch is unfixed.** Injected rows still occupy receiver positions rather than the source
   positions they were captured at (`predictor_position_offset = -1`, `latent_worker.py:352`);
   `positional_embed_overrides` is refused outright (`latent_worker.py:444`, `chat_adapter.py:606-610`).
3. **Framing is the client's job.** Nothing in `extensions/sglang-occ3` writes a sentence telling the receiver
   that the rows are a message; the marker is an ordinary ASCII string the client must place.
4. **Silent duplicate injection if `batch.prefix_lens` is ever absent**: `prepare_batch` reads it with
   `getattr(..., None)` and `batch_context` then defaults `prefix` to 0 (`latent_worker.py:396,404,419`),
   which would re-inject the block into every chunk without any error. The pinned `ScheduleBatch` does define
   `prefix_lens` (`managers/schedule_batch.py:2523-2524,2741`), so this is a latent hazard, not a live defect.
5. **`mark_injection` runs only in the last-PP-rank logits branch** (`capture-eager-mtp.v1.json` ->
   `models/deepseek_v2.py` rep 1). Any future forward that skips that branch leaves `occ3_effective_embeds`
   set and drops the positions from `injected_positions`; today `pp_size == 1` is required, so the risk is
   structural rather than active.
6. **Pinned ids bypass the server's tokenizer call.** In the marker layout the adapter replaces the
   request text with ids it produced using `add_special_tokens=False` (`chat_adapter.py:642,698`,
   `prefix_layout.py:40-47,59-81`). If the string the adapter sees is ever *not* the fully rendered chat
   template, the receiver silently loses the template/BOS control tokens - the exact content the prepend
   layout already displaces. Not verified against the live `/v1/chat/completions` route in this trace.
7. **Docs drift.** `reports/occ3-statebridge-recovery/EXTERNAL-REFERENCES.md:67` describes the marker path as
   wired with line numbers that were correct at 13:29 and wrong at 13:31:41 and 13:33:45; treat that table as
   revision-scoped, not current.
