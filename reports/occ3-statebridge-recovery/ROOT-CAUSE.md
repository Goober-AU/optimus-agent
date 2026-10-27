# OCC-3 StateBridge semantic recovery — root cause ledger

Scope: the existing StateBridge-style OCC-3 path (final-layer pre-norm predictor states ->
mathematical alignment -> continuous input-embedding prefix -> receiver inference). No KV
transport, no training, no fine-tuning, no new protocol.

Baseline: `fix/occ3-glm53-latent-transfer-validation` merged as PR #203
(merge commit `7a158b0e559eb5ab38fd3b9ae3e98384dcb3181c`).
Branch: `fix/occ3-statebridge-semantic-recovery`.

Labels used below:

- OFFLINE VERIFIED — proven by source reading plus a passing or failing test in this repo.
- SOURCE-ATTESTED VERIFIED — proven against pinned SGLang 0.5.21 / reference checkouts.
- LIVE GPU VERIFIED — measured on the DGX B300 during a supervised window.
- INFERENCE — reasoned, not yet measured.

## What PR #203 already established (do not reopen without regression evidence)

Native capture on 8/8 TP ranks, GPU state publication, authorised receiver admission, receiver
embedding injection, a completed native consumption receipt, matched positive/no-state ordinary
payloads, logical cleanup, and restoration of the original production service.

The open problem is semantic: a completed, instrumented native trial returned `UNKNOWN`
while its full-text control answered correctly. That is a completed semantic miss, not a
transport failure.

## RC-1 — The receiver has no framing and the prefix sits before the system turn

Status: OFFLINE VERIFIED defect. Repair in this branch is OFFLINE VERIFIED; live verification pending.

Evidence:

- `extensions/sglang-occ3/occ3_sglang/latent_worker.py` `admit_request()` reserves
  `descriptor.expected_prefix_tokens` zero-id rows and, before this branch, always prepended
  them at absolute row 0. `inject_target_embeddings()` then overwrote exactly those rows with
  the aligned prefix.
- Nothing in the receiver's rendered prompt referred to those rows. The only referent was the
  system text that comes *after* the latent block ("Read the private vault code from the
  attached native state").
- Every prompt-bearing reference implementation frames the latent span in ordinary text and
  places it inside a turn: StateBridge `prompts.py:53-56`
  ("The following is the message from previous Agent (provided in embedding format):" then a
  marker, inside the user message), CodeTalkers `latent_talk.py:98-100`.

Consequence: a correct injection can be semantically inert because the receiver is never told
that positions 0..K-1 are a message, where the message ends, or that it should be used.
This is the cheapest explanation of "state consumed, answer UNKNOWN" that does not require
the alignment to be wrong.

Repair (this branch):

- `occ3_sglang/prefix_layout.py` — `resolve_prefix_layout()` resolves an ordinary ASCII
  marker in the rendered prompt and returns the absolute row where the reserved prefix
  belongs. Absent marker keeps the legacy prepend layout; the receipt records which layout ran.
- `ChatAttachmentAdapter._prefix_layout()` pins the receiver's token ids to the exact sequence
  used to compute the offset, so the scheduler-side reservation cannot diverge from the
  resolved offset.
- `ConsumeWorkerDescriptor.prefix_offset` (validated, default 0) carries the offset to the
  scheduler; `admit_request()` inserts the reserved rows there; `inject_target_embeddings()`
  and `mark_injection()` copy into `[offset, offset+count)` and record absolute injected rows.
- `prefix_span()` generalises the chunked-prefill arithmetic and reduces exactly to the old
  formula at offset 0 (test `test_prefix_span_reduces_to_legacy_window_at_offset_zero`).
- The live example harness now emits the StateBridge-style framing sentence with
  `<<occ3-statebegin>>` / `<<occ3-stateend>>` around the reserved rows.

## RC-2 — The transferred span is the tail of the whole completed response, tool-call syntax included

Status: OFFLINE VERIFIED defect. Not repaired in this branch (needs a frontend-attested boundary).

Evidence:

- `attachment_contract.py` accepts exactly one span policy:
  `whole_completed_response_tail_v1`.
- `latent_worker.py` `align()` selects `n = min(accepted_count, max_prefix_tokens)` rows ending
  at the last accepted token, i.e. the closing tool-call JSON and chat-template end markers of
  the `occ3_handoff` call.
- The reference removes reasoning by tokenising `</think>` and keeping only the states after
  it (`state_bridge.py:579-597`).

Consequence: even a perfect alignment maps mostly handoff syntax. Whether any communicative
content survives in the retained window is unmeasured; the new `span_audit` module makes that
measurable and fails closed when markers are absent.

## RC-3 — Vocabulary anchoring is disabled in the shipped runtime profile

Status: OFFLINE VERIFIED configuration fact. Impact unmeasured on GLM-5.3.

`deploy/native-build-inputs/native-profile-input.json` sets `"anchor_alpha":0.0`, while the
reference uses `snap_ratio = 0.3` and reports an ablation drop when anchoring is removed
(README:57, 82.4 -> 80.2 on its own benchmarks). The tiled anchor path exists and is bounded;
this is an experiment variable, not a proven defect.

## RC-4 — Per-message Procrustes can be dominated by the reference token embeddings

Status: measured offline (see PROCRUSTES-VALIDATION.md and REFERENCE-TOKEN-ABLATIONS.md).
Impact on live semantics unmeasured.

The solver fits the same rows it transforms, with reference embeddings `E` fixed by the
selected tokens. Whether the output depends usefully on `H` after `E` is fixed must be
measured, not assumed. No repair is justified until the ablation numbers exist.

## RC-5 — Predictor/token pairing

Status: analysed against pinned SGLang; see PREDICTOR-TOKEN-ALIGNMENT.md.

Both capture paths pair the pre-norm state at position `p` with the token that state predicts
(position `p+1`), and `commit_result()` refuses on any mismatch between captured positions and
the recomputed `arange(prompt_len + output_start - 1, ...)`. No production change is justified
unless a source-attested failure is produced.

## Not root causes (checked and excluded)

- The prefix overwriting ordinary receiver prompt tokens: excluded. `admit_request()` reserves
  dedicated rows; the ordinary prompt is untouched.
- Missing RadixAttention isolation: isolated by `occ3_private_match` / private KV namespace.
- Replay: fenced by attempt digests and spent descriptors.
