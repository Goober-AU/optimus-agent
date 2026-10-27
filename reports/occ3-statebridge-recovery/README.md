# OCC-3 StateBridge semantic recovery

Working branch: `fix/occ3-statebridge-semantic-recovery`, cut from `origin/main`
`7a158b0e559eb5ab38fd3b9ae3e98384dcb3181c` (merge of PR #203).

Method unchanged: final-layer pre-norm predictor states -> per-message mathematical
alignment -> continuous input-embedding prefix -> frozen receiver inference. No KV-cache
communication, no training, no fine-tuning, no new protocol, no weight or checkpoint change.

## Status in one line

Captured native state is still the only channel; this branch fixes where the receiver sees
the continuous message and how the trial is instrumented. Semantic transfer remains
**NOT VERIFIED** because no supervised DGX window has run against this build.

## What is verified in this branch

| claim | label | evidence |
|---|---|---|
| The receiver prefix now has a mid-sequence, text-framed layout selected by an ordinary prompt marker; the legacy prepend layout is preserved byte-identically when the marker is absent. | OFFLINE VERIFIED | `occ3_sglang/prefix_layout.py`, `tests/test_prefix_placement.py` (15 passed) |
| Chunked-prefill and draft-window arithmetic generalises to a non-zero offset and reduces exactly to the previous formula at offset 0. | OFFLINE VERIFIED | `prefix_span()` + `test_prefix_span_reduces_to_legacy_window_at_offset_zero` |
| The descriptor carries the offset end to end; a mismatch between the adapter, the reservation and the worker refuses the turn. | OFFLINE VERIFIED | `latent_backend.reserve`, `chat_adapter._generate`, `latent_worker.admit_request`, `mark_injection` |
| Predictor/token pairing is ALIGNMENT B (state at `p` predicts the token at `p+1`) in both prefill and EAGLE/MTP verify; there is no off-by-one. | SOURCE-ATTESTED VERIFIED | `PREDICTOR-TOKEN-ALIGNMENT.md`, `tests/test_predictor_alignment.py` (22 passed) |
| The thin solver reproduces the dense StateBridge reference on full-rank CPU data. | OFFLINE VERIFIED | `analysis/align_audit.json` exp1 (worst relative diff 4.95e-05) |
| The aligned prefix is dominated by the reference token embeddings: out-of-sample reconstruction error is 1.407 versus 0.019 in-sample, and sensitivity to `E` is 63x sensitivity to `H`. | OFFLINE VERIFIED (synthetic GLM-shaped data) | `analysis/align_audit.json` exp3/6/7 |
| Accepted positions can be classified by exact token-id markers into thinking / content / tool-call spans, bounded and privacy-preserving, failing closed without markers. | OFFLINE VERIFIED | `occ3_sglang/span_audit.py`, `tests/test_span_audit.py` (46 passed) |
| A first-token forced-choice margin over BIRD/MAMMAL/FISH/REPTILE is available in one request under pinned SGLang, with exact preconditions. | SOURCE-ATTESTED VERIFIED (capability only) | `SEMANTIC-LIKELIHOOD.md` |

## What is NOT verified

- That the receiver interprets the aligned prefix. No live window has run against this build.
- That the hidden states, rather than the reference token embeddings, carry the message.
- That the selected span contains communicative content for a real GLM-5.3 source turn.
- Any benchmark number, any latency number, any GPU memory number for the new layout.
- `cargo check` / `scripts/check.sh`: no Rust toolchain on the authoring host.

## Documents

- [ROOT-CAUSE.md](ROOT-CAUSE.md) - ranked candidate causes with evidence and status.
- [BUG-LEDGER.md](BUG-LEDGER.md) - one row per defect, including rejected hypotheses.
- [EXTERNAL-REFERENCES.md](EXTERNAL-REFERENCES.md) - StateBridge / CodeTalkers / Interlat / Coconut / LatentMesh comparison with line citations.
- [PREDICTOR-TOKEN-ALIGNMENT.md](PREDICTOR-TOKEN-ALIGNMENT.md) - pinned-source proof that pairing is correct.
- [SOURCE-SPAN-AUDIT.md](SOURCE-SPAN-AUDIT.md) - what the retained window actually contains and how to classify it.
- [RECEIVER-FRAMING.md](RECEIVER-FRAMING.md) - framing and placement design, variants to test.
- [analysis/PROCRUSTES-VALIDATION.md](analysis/PROCRUSTES-VALIDATION.md), [analysis/REFERENCE-TOKEN-ABLATIONS.md](analysis/REFERENCE-TOKEN-ABLATIONS.md) - alignment numerics and the token-reference confound.
- [SEMANTIC-LIKELIHOOD.md](SEMANTIC-LIKELIHOOD.md) - how to score an answer instead of reading it.
- [NEGATIVE-CONTROLS.md](NEGATIVE-CONTROLS.md) - matched control matrix and leakage guards.
- [REGRESSION-GATES.md](REGRESSION-GATES.md) - test environments, commands, red-first discipline.
- [FINAL-REPORT.md](FINAL-REPORT.md) - honest status, blockers, next window plan.

## Next window (needs fresh authorisation)

One supervised v3 attempt, one swap, in this order:

1. Corrected-input matched trials on the *framed marker layout* (positive / no-state / full text),
   same question, same user turn, only the reserved rows differ.
2. The same three arms with the legacy prepend layout, to isolate placement from content.
3. A token-reference-only arm (`alpha=0`, prefix built from the selected tokens' embeddings with
   no hidden-state dependence) to separate the carrier from the token channel.
4. A first-token forced-choice margin for every arm, so an abstention cannot hide a weak signal.

Anything beyond that (span policy change, `alpha=0.3`, unrelated-state and shuffled-state arms,
benchmarks) comes after a semantic hit, not before.
