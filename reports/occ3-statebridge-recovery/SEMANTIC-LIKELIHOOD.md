# OCC-3 semantic validation: narrowest correct conditional-likelihood path

Status: design note, no code changed. Scope: repo `C:/Users/openclawuser/optimus-occ3-latent-validation` plus the pinned
runtime tree at `../occ3-latent-validation/references/sglang/python/sglang/srt` (SGLang 0.5.21, pin reported in
`reports/occ3/DGX-ENVIRONMENT.md:19`; the tree itself cannot state its version at rest because
`python/sglang/version.py` resolves it at import time).

Path convention: `SGLANG/<p>` means `references/sglang/python/sglang/srt/<p>`. Repo paths are relative to the repo root.

Claim labels follow the sibling notes in this directory (`ROOT-CAUSE.md`, `RECEIVER-FRAMING.md`):

- **SOURCE-ATTESTED** — the pinned source was read and the exact lines are cited below; every capability claim here is at
  this level.
- **OFFLINE VERIFIED** / **LIVE GPU VERIFIED** — not claimed anywhere in this note. No test was run and no GPU was touched
  for this design.
- **INFERENCE** — marked where used.

Relationship to the sibling notes: this note assumes the repaired prefix placement of `RECEIVER-FRAMING.md` (one ASCII
marker, resolved row, `input_ids` pinned by the adapter for a consume request). A scoring request does not change that
path, because the client never supplies `input_ids` (`chat_adapter.py:470-473` refuses a client value) and the adapter
pins it internally. Nothing here reopens `ROOT-CAUSE.md` RC-1 or its repair.

---

## 0. Verdict in one paragraph

Yes for one narrow case, no for the general case. A single receiver chat-completions request can return the four
log-probabilities `BIRD / MAMMAL / FISH / REPTILE` **at the first generated position**, because the pinned runtime
computes full-vocabulary top-N logprobs at every output position, under EAGLE/MTP verify, and our adapter forwards those
two fields in buffered attachment mode. That costs **zero extra forward passes**: the receiver answer turn already runs
the prefill and the first decode step. What is *not* available on the authenticated chat path is teacher-forced prompt
scoring (`logP(token at prompt position i | prefix)`), because the chat endpoint hard-codes `logprob_start_len = -1`.
Prompt scoring exists in the pinned runtime, but only on `/generate` and `/v1/completions`, which the attachment adapter
does not wrap. Do not call the result "conditional likelihood": call it a **first-token forced-choice margin over one
receiver context**.

---

## 1. Availability table

| # | Mechanism | Pinned SGLang 0.5.21 | Under EAGLE/MTP verify | Our adapter | Evidence |
|---|---|---|---|---|---|
| 1 | Chat `logprobs: true` (logprob of the token the model actually sampled) | Supported | Supported; for `max_new_tokens = 1` the token comes from the ordinary prefill path, not from verify | **Forwarded** (buffered mode). **Refused** in terminal receipt mode | `SGLANG/entrypoints/openai/protocol.py:860`; `SGLANG/entrypoints/openai/serving_chat.py:1251`; `SGLANG/speculative/eagle_worker_common.py:640-641`; `SGLANG/layers/logprob_processor.py:430-432`; `extensions/sglang-occ3/occ3_sglang/chat_adapter.py:468-473` (no field check), `:873-879` (terminal exclusion) |
| 2 | Chat `top_logprobs: N` (top-N of the **full vocabulary** at each output position) | Supported. No clamp, no validation, no server cap on this path; the only hard limit in the tree is the PD-disaggregation metadata cap of 128, unused on our single-node path. Only `max_k <= 8` takes the fused kernel; larger N falls back to a plain `torch.topk` | Supported: the answer row comes from prefill; later rows are verify rows (target logits gathered by `accept_index`, then full-vocab `log_softmax` + top-k) | **Forwarded** (buffered). **Refused** in terminal mode | `SGLANG/entrypoints/openai/protocol.py:861`; `SGLANG/entrypoints/openai/serving_chat.py:1253`; `SGLANG/managers/io_struct.py:559-560,772-780`; `SGLANG/layers/logprob_processor.py:98-120`; `:434-443`; `SGLANG/layers/logsumexp.py:24` |
| 3 | Prompt logprobs on the **chat** endpoint (`logprob_start_len >= 0`) | **Not reachable**: the chat handler passes `logprob_start_len=-1` as a literal, which the scheduler rewrites to `len(origin_input_ids)` = output tokens only | n/a (field never set) | Not reachable: no chat field exists for it | `SGLANG/entrypoints/openai/serving_chat.py:1252`; `SGLANG/managers/scheduler.py:3043-3058`; `SGLANG/managers/io_struct.py:230-232` |
| 4 | `return_logprob` + `logprob_start_len` on native `/generate` | Supported, including per-position start and prefix-cache-safe behaviour | Not exercised by us | **Outside the adapter**: hooks are registered on `OpenAIServingChat` only; `/generate` calls the tokenizer manager with no adapter context and runs unmodified | `SGLANG/managers/io_struct.py:229-236`; `SGLANG/managers/tokenizer_manager.py:1496-1507`; `SGLANG/managers/schedule_batch.py:2298-2314`; `SGLANG/entrypoints/http_server.py:911-923`; `extensions/sglang-occ3/occ3_sglang/chat_adapter.py:35-36,392-394,612-615` |
| 5 | `token_ids_logprob` (logP for token ids you did **not** sample) | Supported natively; the only mechanism that scores an arbitrary candidate exactly | Supported | **Not exposed anywhere on the OpenAI surface**: absent from `protocol.py`, so no chat request can ask for it | `SGLANG/managers/io_struct.py:235-236`; `SGLANG/layers/logprob_processor.py:146-201,445-456`; absence verified by full read of `SGLANG/entrypoints/openai/protocol.py` (`top_logprobs` appears only at `:861` and `:1813`) |
| 6 | `/v1/completions` with `echo` + `logprobs` (prompt logprobs from index 0) | Supported with a warning that echo is not compatible with logprobs | n/a | **Outside the adapter** | `SGLANG/entrypoints/openai/serving_completions.py:77-92,119-122` |

### Explicit answer

**Can we score `P(answer token | context)` for BIRD/MAMMAL/FISH/REPTILE over ONE receiver context without extra
full-model passes?**

**Yes — for the first generated position only, and only from the top-N row of that position.** One request, one prefill,
one decode step (that step produces the answer token itself), all four candidates read from the same row. The cost of the read is one vocabulary `topk`, not a model
pass. Three preconditions must hold, and each is checkable:

1. all four labels are **single tokens** under the pinned tokenizer (see §2.3);
2. each label's token appears in that row, or the caller accepts the bounded interval of §2.2;
3. sampling amplitude is neutral: `temperature in {0,1}`, penalties zero, no grammar/constrained decoding (§2.4).

If any precondition fails, the correct action is refusal, not approximation.

---

## 2. Exact request shape and arithmetic

### 2.1 Request shape (buffered attachment mode only)

`POST /v1/chat/completions`, body = the ordinary receiver turn plus the unchanged 11-field `occ3` attachment
(`extensions/sglang-occ3/occ3_sglang/attachment_contract.py:80-107`; the parser requires **exactly** those fields, so no
new opt-in field is allowed without a contract change).

```
{
  "model": "<pinned model fingerprint>",
  "messages": [ ... the one allowed receiver turn ... ],
  "max_completion_tokens": 1,          // must be > 0; adapter refuses otherwise. 1 = strict mode
  "temperature": 0,                     // 0 or 1 ONLY
  "top_p": 1.0, "top_k": -1, "min_p": 0.0,
  "presence_penalty": 0.0, "frequency_penalty": 0.0, "repetition_penalty": 1.0, "logit_bias": null,
  "logprobs": true,                     // required: without it no logprobs are produced at all
  "top_logprobs": 32,                   // 8 <= N <= 64; 32 is the recommended default
  "n": 1,
  "stream": false,                     // recommended; terminal receipt mode refuses logprobs
  "occ3": { ... unchanged ... }
}
```

Forwarding evidence for each field:

- `logprobs` -> `return_logprob` and `top_logprobs` -> `top_logprobs_num`: `SGLANG/entrypoints/openai/serving_chat.py:1251-1253`.
- `logprob_start_len` is a literal `-1`, so only output positions are returned: `serving_chat.py:1252`, `scheduler.py:3043-3058`.
- `temperature`, `max_new_tokens`, penalties, `logit_bias`, `regex`/`ebnf`/`structural_tag` are all built into the
  sampling params: `SGLANG/entrypoints/openai/protocol.py:1130-1170`.
- Token text is returned for every top entry (`return_text_in_logprobs=True`), plus exact GPT-2 byte recovery from the
  token id: `serving_chat.py:1257`, `:2484-2519`.
- `n == 1` is already enforced by the adapter: `chat_adapter.py:468-469`. A positive `max_new_tokens` is enforced by
  `chat_adapter.py:643-645`.

`max_completion_tokens = 1` is the strict mode: the answer *must* be the first and only token (§2.5). A value up to 8 is
allowed only in the explicit late-answer mode of §2.2, which records `answer_position` and conditions the margin on the
model's own generated prefix.

Bounded sizes: `N=32` with 8 output tokens produces at most ~8 x 32 entries (~30 KB of JSON), far below
`BufferProfile.max_buffer_bytes = 4 MiB` (`chat_adapter.py:214-245`) and below any sane reservation
(`chat_adapter.py:675-676`). Larger N costs one wider `topk` over the vocabulary and a bigger body; it does not cost a
model pass.

`stream: false` is a recommendation, not a requirement: the buffered adapter accepts both, and the streaming handler
assembles logprobs per chunk as well (`serving_chat.py:2010-2021,2680-2699`). What is *not* allowed is terminal receipt
mode: `_terminal_admission` lists `logprobs` and `top_logprobs` among the fields that make a request ineligible, and any non-`(None, False, 0)` value returns a refusal before dispatch
(`chat_adapter.py:873-879` -> `:444-448` -> `unsupported_profile`, 409). This is existing, tested behaviour
(`extensions/sglang-occ3/tests/test_terminal_receipt.py:143-145,159-160,175-176`).

### 2.2 Arithmetic

Let `row` be `choices[0].logprobs.content[p].top_logprobs` — the ordered top-N entries at the answer position `p`, each
`{token, bytes, logprob}` (`serving_chat.py:2500-2528`). Let `L = [BIRD, MAMMAL, FISH, REPTILE]`, `c` the index of the
ground-truth label, and let `score(l)` be the row `logprob` that matches label `l`.

    margin = score(c) - max_{l != c} score(l)          (natural log units)
    bits   = margin / ln 2

**Choosing `p`.** In strict mode (`max_completion_tokens = 1`) the response has exactly one output entry and `p = 0`.
In late-answer mode, `p` is the *last* output position whose emitted token matches one of the four labels; record it as
`answer_position` and refuse with `scoring_position_unsupported` when no position matches. Never search for the position
that maximises the margin: that would select on the outcome.

`margin > 0` means the model prefers the correct label over the best wrong label. Report, alongside it:

    label_mass = sum_{l in L} exp(score(l))       must be <= 1 by construction
    preferred  = argmax_l score(l)

Absent label. The row is the top-N in descending order, so any token outside it satisfies `logP <= v_min`, where
`v_min` is the smallest `logprob` present. That turns a miss into a **bound**, not a failure:

- absent label is an incorrect one: `margin_lo = score(c) - max(present incorrect, v_min)`; `margin_hi = score(c) - max(present incorrect)`.
- absent label is the correct one: only an upper bound exists, `margin_hi = v_min - max(present incorrect)`; report `margin_lo = -inf`.
- `margin_exact = true` only when all four labels are present.

Refuse to report a point value whenever `label_mass < 0.5` (the four-way choice is not concentrated) or when the row is
shorter than 4 entries. All four values come from one softmax, so the margin needs no renormalisation and no
per-candidate rescaling.

Illustrative shape only, no measured values: with `score(BIRD) = -0.21`, `score(MAMMAL) = -3.90`,
`score(FISH) = -2.10`, `score(REPTILE) = -2.05` and ground truth `BIRD`, the margin is
`-0.21 - (-2.05) = +1.84` nats (`+2.66` bits), `label_mass = 0.94`, `preferred = BIRD`.

### 2.3 Leading space and tokenisation length

**Leading space.** The answer-start token depends on the last characters of the rendered chat template, not on the model.
Most templates leave the assistant turn without a trailing space, so the model must emit `" BIRD"` (leading-space token);
a template that ends with a space yields `"BIRD"`. Never assume: match by exact decoded bytes against both forms, accept
exactly one match, prefer the larger value if both are present, and record which form matched (`label_form`). Matching by
bytes is exact, because the response carries token bytes recovered from the token id rather than display text
(`serving_chat.py:2484-2499`, note the comment about fragmentary byte tokens).

**Different token counts.** If a label needs more than one token, the row contains only its first token. Comparing a
one-token label against a two-token label by first token is **not** a likelihood ratio: the second label's probability
mass is split across continuations, and the comparison is biased against it. Therefore:

- the scoring path is valid only when every label is one token;
- that fact must be established once per model fingerprint, offline, from the pinned tokenizer, into a fixture
  (`reports/occ3-statebridge-recovery/semantic_labels_v1.json` in this design: `model_fingerprint`, per label
  `token_ids` (length must be 1) and `bytes`, plus `single_token: true`);
- if any label is not single-token, refuse with `scoring_label_tokenization` and fall back to today's text comparison
  (`crates/pi-coding-agent/examples/occ3_native_handoff.rs:568`).

A true multi-token sum, `score(l) = sum_t logP(t | context, t_<t)`, needs one forward pass per token position. The chat
endpoint cannot provide it (only the sampled continuation is available). Prompt logprobs would provide it, but they are
not reachable from the authenticated chat path (§1 rows 3-4), so the honest answer is refusal, not approximation.

### 2.4 Why temperature and penalties must be pinned

- Greedy: `temperature < eps` is rewritten to `1.0` with `top_k = 1`, and the returned logprobs are
  `log_softmax(raw logits)`: `SGLANG/sampling/sampling_params.py:217-220`, `SGLANG/layers/sampler.py:185-200`.
  `top_k = -1` means the whole vocabulary (`sampling_params.py:221-222`), which is the value to send.
- Non-greedy: logits are divided by temperature and the returned value is `log(softmax(logits/T))`
  (`sampler.py:248,266,275-280`). At `T = 1` this equals the true logprobs; at any other `T` it is a rescaled quantity
  and the margin changes meaning.
- Penalties, `logit_bias` and the grammar mask are applied to the logits **before** the sampler reads them
  (`SGLANG/model_executor/model_runner.py:1966-1975`, `SGLANG/sampling/sampling_batch_info.py:392-398`), so a non-zero
  penalty silently deforms every reported logprob, and a grammar constraint renormalises the distribution over the
  allowed set. Hence: penalties off, and no `response_format`/`regex`/`ebnf`/`structural_tag` in a scoring request.
- `SGLANG_RETURN_ORIGINAL_LOGPROB` is a process-wide env flag (`SGLANG/environ.py:1323`, `sampler.py:74,209-210,284-285`).
  It is not visible to the adapter. Assert it is unset in the trusted installer startup.

### 2.5 EAGLE/MTP correctness

The key point is stronger than "verify also works": **with `max_completion_tokens = 1` the speculative verify path never
produces the answer token.** The first generated token is sampled during the *prefill* (extend) forward pass and appended
to the request there (`SGLANG/managers/scheduler_components/batch_result_processor.py:356-358`); the EAGLE worker only
seeds its first draft step from that token (`SGLANG/speculative/eagle_worker_v2.py:1461-1465,1500-1505,1015-1020`). That
prefill is an ordinary, non-speculative extend, so its logprobs take the ordinary full-vocabulary branch
(`SGLANG/layers/logits_processor.py:349-353`; the branch runs because `is_target_verify()` is false).

For completeness, tokens 2..N under spec do get correct full-vocabulary rows: the verify pass calls
`compute_spec_logprobs` (`SGLANG/speculative/eagle_worker_common.py:640-641`), which gathers the **target** model's logits
at the accepted positions and takes a full-vocabulary `log_softmax` (and a `topk` over all V) on those gathered rows
(`SGLANG/layers/logprob_processor.py:401-443`). The draft runner never contributes values: every draft graph runner sets
`return_logprob = False` (`SGLANG/speculative/eagle_worker_v2.py:965`, `eagle_draft_cuda_graph_runner.py:453`,
`eagle_draft_extend_cuda_graph_runner.py:430`, `frozen_kv_mtp_cuda_graph_runner.py:313`,
`multi_layer_eagle_draft_extend_cuda_graph_runner.py:343`).

Two parity caveats apply to tokens 2..N only, and not to the answer position:

- before `compute_spec_logprobs`, the verify logits have **relaxed** penalties and a repeated grammar mask applied
  (`SGLANG/speculative/eagle_utils.py:773-797`), so a penalty-active request can differ slightly from the non-spec path;
- the value is `log_softmax` in spec and `log(softmax)` in the ordinary path (`sampler.py:248,266,275-280`): same
  mathematics, different kernel, so bitwise equality is not guaranteed.

There is no truncation, renormalisation or alignment defect in either path that this review could find. Keep the one-off
A/B check anyway (spec on/off, same prompt, `temperature 0`, `max_completion_tokens 2`), requiring
`max |delta logprob| < 1e-3` at the answer position.

---

## 3. Why the "one decode step per candidate" alternative is the wrong shape here

The tempting alternative is four tiny scoring requests, one per candidate, reusing the same attachment and the same
injected prefix. **It cannot be built inside this runtime**, for two independent reasons:

1. **Single consume.** The adapter fences every consume attempt on
   `(runtime_id, runtime_generation, state_handle)` and raises `replay_forbidden` on a repeat
   (`chat_adapter.py:620-632`; the fence is taken for every non-capture request at `:653-654`). A second request that
   carries the same handle is refused, and once the attachment expires the same request is refused as
   `attachment_expired` (`:477-478`, `:722-723`). One attachment = one receiver turn.
2. **The harness already asserts one request.** `receiver_request_audit.request_count == 1` is part of the pass
   condition (`crates/pi-coding-agent/examples/occ3_native_phase_suite.rs:1508,1517`).

So a per-candidate scheme costs N attachments, i.e. N source captures and N prefills of the receiver context — for four
candidates, 4 x (prefill + one decode step) plus 4 x capture traffic and 4 x receipt churn. The absolute seconds are not
measured in this note; the ratio is the point, and the top-N read costs 0 x. Compare with the top-N read of §2,
which costs **0 extra forward passes** and one `topk` per output token.

If exact teacher-forced scoring is later required, the smallest *correct* in-runtime change is to expose and forward
`token_ids_logprob` and `logprob_start_len` on the chat request (the native fields already exist at
`SGLANG/managers/io_struct.py:230-236` and reach the scheduler at `tokenizer_manager.py:1501-1503`). That is one prefill
and **no** extra decode step, and it removes the single-token precondition. It is a larger change than the top-N read:
it adds chat fields, adapter admission rules, and a scheduler-side check. Treat it as step two, not as the first step.

Cost summary for the recommended path:

| Item | Cost |
|---|---|
| Prefill | already paid by the answer turn |
| Decode steps | 1 (the answer token), already paid |
| Extra model passes | 0 |
| GPU extra work | one vocabulary `topk` with `k <= 64` per output token; mixed chunked prefill is disabled for `return_logprob` batches, so a small throughput loss on concurrent traffic is possible (`SGLANG/managers/scheduler.py:4129-4138`) |
| Wire | a few KB of JSON at `N=32`; at most ~30 KB if late-answer mode uses 8 output tokens |

---

## 4. Red-first test plan and refusal codes

Red first: every test below must fail on today's tree before any implementation. Several fail for a specific reason:
the current adapter is *silent*. It admits scoring-shaped requests in buffered mode, returns logprobs with no margin, and
records no evidence that a scoring interpretation was intended (`chat_adapter.py:468-473` checks `n`, `input_ids`,
`return_meta_info` and `return_hidden_states`, but never the logprob fields).

### 4.1 Tests

Add to `extensions/sglang-occ3/tests/test_chat_attachment.py` (fixture at `:143-158`, parametrised conflicts at
`:344-350`) and `extensions/sglang-occ3/tests/test_terminal_receipt.py` (`:143-145`).

1. `test_scoring_shape_forwarded` (red: today no assertion exists). Buffered receiver request with the §2.1 shape;
   assert the faked native object carries `return_logprob=True`, `top_logprobs_num=32`, `logprob_start_len=-1`.
2. `test_scoring_requires_logprobs_flag`. `top_logprobs=32` with `logprobs` absent/false -> `scoring_shape_mismatch`
   409. Today: forwarded silently, and `top_logprobs_num=32` has no effect because `return_logprob` is false.
3. `test_scoring_requires_top_logprobs`. `logprobs=true` with `top_logprobs` in `{None, 0}` -> `scoring_shape_mismatch`
   409. Today: returns only the sampled token's logprob, which cannot score candidates.
4. `test_scoring_rejects_non_unit_temperature`. `temperature=0.7` (the fixture default at `test_chat_attachment.py:139`
   is `0.7`) -> `scoring_amplitude_unsupported` 409.
5. `test_scoring_rejects_penalties_and_bias`. Any non-zero presence/frequency penalty, `repetition_penalty != 1.0`, or
   `logit_bias != null` -> `scoring_amplitude_unsupported` 409.
6. `test_scoring_rejects_constrained_decoding`. `response_format`/`regex`/`ebnf` set with `logprobs` ->
   `scoring_constraint_unsupported` 409 (grammar mask renormalises the row).
7. `test_scoring_rejects_oversized_budget`. `max_completion_tokens > 8` with `logprobs` -> `scoring_budget_exceeded` 409.
8. `test_scoring_terminal_mode_refused`. Keep the existing terminal refusal, but make the code explicit:
   `scoring_mode_unsupported` 409 instead of the generic `unsupported_profile` (`test_terminal_receipt.py:159-160`).
9. `test_scoring_refusals_precede_dispatch`. For every case above assert
   `not h.tm.calls and not h.backend.reserves`, mirroring `test_terminal_receipt.py:175-176`.
10. `test_ordinary_request_unchanged`. Without `logprobs`, the response must contain no `logprobs` key and no margin;
    proves no silent behaviour change for ordinary traffic.
11. Harness-side (client) tests, offline on recorded rows: margin from a top-N row equals margin from the full-vocab row
    within `1e-4`; absent-label cases produce the correct interval; a first output token that is not a label yields
    `scoring_position_unsupported`; a two-token label yields `scoring_label_tokenization`.

### 4.2 Refusal codes to add (adapter, fired before dispatch)

Body shape is unchanged: `{"error": {"message": <code>, "type": "occ3_attachment_error", "code": <status>}}`
(`chat_adapter.py:424-428`).

| Code | Status | Condition |
|---|---|---|
| `scoring_shape_mismatch` | 409 | `logprobs` and `top_logprobs` disagree, or `top_logprobs` outside `[4, 64]` |
| `scoring_amplitude_unsupported` | 409 | temperature not in `{0,1}`, any penalty non-neutral, or `logit_bias` set |
| `scoring_constraint_unsupported` | 409 | `response_format` / `regex` / `ebnf` / `structural_tag` present with `logprobs` |
| `scoring_budget_exceeded` | 409 | `max_completion_tokens` above the scoring cap |
| `scoring_mode_unsupported` | 409 | scoring fields under terminal receipt mode (buffered only) |

Client-side (not adapter errors, but fixed strings so they are greppable and never conflated with a pass):
`scoring_position_unsupported`, `scoring_label_tokenization`, `scoring_ambiguous`, `scoring_label_absent`.

Rule: a scoring request must either be admitted in the exact §2.1 shape or refused with one of these codes. It must
never fall through to the ordinary path.

---

## 5. Privacy: keep ground truth off the receiver payload

- **Ground truth never travels.** The animal -> label mapping lives in the harness constant
  (`crates/pi-coding-agent/examples/occ3_native_handoff.rs:67-69`). The receiver payload carries the question and all
  four labels, which are already public in the question string (`:70`), plus the injected prefix. Nothing in the
  request says which label is correct, so the margin is computed **client-side, after the response**, from
  `choices[0].logprobs.content[p].top_logprobs`. The server learns nothing it did not already have.
- **No server-side scoring.** Computing the margin on the server would require sending the correct label, i.e. leaking
  ground truth into the receiver payload. Do not add it. If a server-side number is ever wanted, send only a commitment
  (`digest`, `attachment_contract.py:48-49`) over the ordered label set and return the whole row unblinded.
- **The receipt cannot carry a score, by construction.** The receiver receipt is a 4-key JSON object
  (`use_receipts.py:54-59`); `TerminalUseEvidence` is 20 string/int/bool fields (`use_receipts.py:30-51`); canonical JSON
  forbids NaN (`attachment_contract.py:43-45`). So a float score cannot be attested, and the receipt keeps its current
  meaning: an assertion from the authenticated runtime, not a semantic-use acknowledgment
  (consistent with `reports/occ3/ARCHITECTURE.md:97`).
- **Do not echo the prefix.** With `logprob_start_len = -1`, prompt logprobs are not returned
  (`scheduler.py:3043-3058`), so the injected prefix text is not reflected in the logprobs payload. Add an assertion that
  `input_token_logprobs` is absent in a scoring response.
- **Logging.** The adapter logs bounded codes only (`chat_adapter.py:742-743,767-775`). The new `scoring_*` codes carry
  no label text and no numeric logprob. Client-side evidence should store `margin`, `label_mass`, `answer_position`,
  `label_form`, `margin_exact` and `model_fingerprint` — never the raw row.

---

## 6. Residual risks

1. **Top-N coverage.** If a label is outside the row, the margin is an interval, not a value. With `N=32` this should be
   rare for a four-way prompt; the design must still record the bound rather than silently drop the label.
2. **Tokenisation fixture missing.** No `semantic_labels_*.json` exists today, and the repo has no test that any of the
   four labels is single-token under the pinned tokenizer. This is the first thing to produce.
3. **Amplitude drift.** Any request that sets `temperature != 0/1`, a penalty, or a grammar changes the reported value
   (`sampler.py:248`; `model_runner.py:1966-1975`). Only refusals keep the arithmetic honest.
4. **Process-wide env flag.** `SGLANG_RETURN_ORIGINAL_LOGPROB` changes semantics for every request and is invisible to
   the adapter.
5. **Spec-mode parity.** The answer token is produced at prefill, so scoring is spec-independent; tokens 2..N
   under verify differ only at the kernel/penalty level (`eagle_utils.py:773-797`). Keep the A/B check once per image.
6. **Throughput.** Mixed chunked prefill is disabled for batches that return logprobs (`scheduler.py:4129-4138`).
7. **Fork drift.** The pinned tree carries non-upstream fields (`mm_hashes`, `mm_content_hashes`,
   `return_flat_raw_top_logprobs` at `io_struct.py:205-251`). Any repin must re-run this evidence check.
8. **Routes outside the adapter.** `/generate` and `/v1/completions` give prompt logprobs to any API-key holder, with no
   attachment, no reservation and no receipt: the adapter's hooks only fire when a request carries an adapter context,
   otherwise it calls the original (`chat_adapter.py:613-615`), and the verified-broker middleware only scopes principal
   marking to `/v1/chat/completions` and `/occ3/v1/native/` (`extensions/sglang-occ3/occ3_sglang/native_install.py:102-105`).
   Recommend denying those two routes at the ingress for the scoring profile.
9. **No client capability yet.** `rg logprobs crates/ prime-agent-runtime/` returns no hits, so the Rust harness cannot
   request logprobs today; a scoring turn needs a direct HTTP call or a pi-ai extension.
10. **Wording.** This design measures a first-token forced-choice margin, not a conditional likelihood of a full answer
    string. Prior notes that "likelihood measures remain unproven" (`reports/occ3-latent-research/README.md:20,173`)
    stay true until a run produces margins with the fixture above.

---

## Appendix: evidence index

Runtime, logprob production and forwarding

- `SGLANG/managers/io_struct.py:229-236` — `return_logprob`, `logprob_start_len`, `top_logprobs_num`, `token_ids_logprob`.
- `SGLANG/managers/io_struct.py:555-562,772-780` — defaults (`False`, `-1`, `0`) and per-request normalisation.
- `SGLANG/managers/io_struct.py:1053-1059` — the four fields on `TokenizedGenerateReqInput`.
- `SGLANG/managers/tokenizer_manager.py:1496-1507` — forwarding to the scheduler.
- `SGLANG/managers/tokenizer_manager.py:1278-1285` — the only `logprob_start_len` guard (encoder-SWA replay only).
- `SGLANG/entrypoints/openai/protocol.py:860-861` — chat `logprobs`, `top_logprobs`.
- `SGLANG/entrypoints/openai/protocol.py:1130-1170` — sampling params built from the request.
- `SGLANG/entrypoints/openai/serving_chat.py:1251-1257` — `return_logprob`, hard-coded `logprob_start_len=-1`, `top_logprobs_num`.
- `SGLANG/entrypoints/openai/serving_chat.py:2331-2334,2456-2530` — response assembly, per-entry `token`/`bytes`/`logprob`.
- `SGLANG/entrypoints/openai/serving_completions.py:77-92,119-122` — echo + logprobs path (`logprob_start_len=0`) and its warning.
- `SGLANG/entrypoints/http_server.py:911-923` — `/generate` route.
- `SGLANG/managers/scheduler.py:3043-3058` — `-1` rewritten to the full prompt length (output-only).
- `SGLANG/managers/scheduler.py:4129-4138` — mixed chunked prefill disabled when `return_logprob`.
- `SGLANG/layers/logprob_processor.py:98-120` — full-vocabulary `topk`.
- `SGLANG/layers/logprob_processor.py:146-201` — `token_ids_logprob` gather.
- `SGLANG/layers/logprob_processor.py:386-443` — `compute_spec_logprobs` (target logits, `log_softmax`, top-k).
- `SGLANG/layers/sampler.py:185-200,248,266,275-280,286-292` — where the reported value comes from.
- `SGLANG/model_executor/model_runner.py:1966-1975` — penalties/grammar/logit_bias applied before sampling.
- `SGLANG/sampling/sampling_batch_info.py:392-398` — transform order.
- `SGLANG/sampling/sampling_params.py:217-220` — `temperature < eps` becomes `1.0` with `top_k = 1`.
- `SGLANG/environ.py:1323` — `SGLANG_RETURN_ORIGINAL_LOGPROB` default `False`.
- `SGLANG/layers/logsumexp.py:24` — `FUSED_TOPK_MAX_K = 8` (speed switch only).
- `SGLANG/speculative/eagle_worker_common.py:640-641` — verify-time logprobs.
- `SGLANG/speculative/eagle_worker_v2.py:965` — draft runner never returns logprobs.

Adapter and contract

- `extensions/sglang-occ3/occ3_sglang/chat_adapter.py:35-36,392-394` — hooks registered on `OpenAIServingChat` and the tokenizer manager only.
- `:424-428` — error body shape.
- `:444-448` — terminal refusal path (`unsupported_profile`, 409).
- `:468-473` — buffered admission refusals (no logprob fields).
- `:591-610` — converted-request conflicts (no logprob fields).
- `:612-615` — pass-through when no adapter context exists.
- `:620-632,653-654` — consume fence (`replay_forbidden`).
- `:635-658` — `_generate` profile checks (`max_new_tokens > 0`, `n == 1`).
- `:675-676` — reservation output bound.
- `:722-723` — expiry refusal.
- `:742-743,767-775` — bounded diagnostic logging.
- `:873-879` — terminal exclusion list containing `logprobs`, `top_logprobs`.
- `:214-226` — `BufferProfile` (4 MiB buffer, 2 active, 300 s turn).
- `extensions/sglang-occ3/occ3_sglang/attachment_contract.py:10-11,43-49,80-110` — receipt header, 2048-byte cap, canonical digest, exact 11-field attachment.
- `extensions/sglang-occ3/occ3_sglang/use_receipts.py:30-59` — evidence and receipt field sets (no float).
- `extensions/sglang-occ3/occ3_sglang/native_install.py:102-105` — principal-marking scope.
- `extensions/sglang-occ3/tests/test_terminal_receipt.py:143-145,159-160,175-176` — existing logprobs refusal test.
- `extensions/sglang-occ3/tests/test_chat_attachment.py:139,143-158,344-350` — receiver fixture and conflict list.

Harness

- `crates/pi-coding-agent/examples/occ3_native_phase_suite.rs:1421-1519` — one receiver turn, `request_count == 1`.
- `crates/pi-coding-agent/examples/occ3_native_handoff.rs:67-70` — `CATEGORY_FACTS` and the four-label question.
- `crates/pi-coding-agent/examples/occ3_native_handoff.rs:561-577` — `semantic_answer_match` / `answer_class` string comparison.
