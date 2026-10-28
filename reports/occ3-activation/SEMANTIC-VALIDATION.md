# Semantic validation

## 1. Verdict

**NOT VERIFIED.** No new live semantic measurement was taken in this session. This document records
what the method requires, what the previous window measured, and exactly what is outstanding.

## 2. What must be demonstrated

Sender-private information must transfer from a sender's captured hidden state to a receiver that
never received that information in text, and the correct-state condition must beat matched negative
controls on the same receiver question, decoding configuration and ordinary prompt.

Protocol per trial:

1. Sender turn receives a private fact that never appears in the receiver's context.
2. The sender's final-layer pre-norm hidden state over the final semantic span is captured.
3. The receiver receives an ordinary question about the fact and no fact.
4. The receiver's inference runs with the continuous prefix attached at the framed marker position.
5. Score exact-match accuracy and the likelihood of the correct answer sequence, not first-token
   logprobs unless they actually score the whole candidate.

Controls, all matched in shape: correct sender state, wrong-sender state, shuffled rows,
matched-shape null state, no attachment, full-text upper bound as the achievability reference.

## 3. What the previous window established (carried forward, unchanged)

From `reports/occ3-alignment-repair/LIVE-WINDOW-1.md`, real GLM-5.3 FP8 TP8 runtime:

| Measurement | Value |
|---|---|
| median within-turn held-out R2 | 0.101 (range 0.067 to 0.131) |
| median cosine, aligned prefix to reference embeddings | 0.931 |
| median Procrustes residual | 0.930 |
| hidden-state roll sensitivity | 0.479 |
| semantic recovery | 0 of 3 diagnostic trials |
| verdict by the pre-registered rule | INCONCLUSIVE |

The transport was intact in all three trials: capture succeeded, the handoff was admitted, the
receiver answered. The receiver produced the expected answer zero times and the expected nonce zero
times. The pipe works; the payload was not informative.

## 4. Why this session did not add a live number

A live semantic run requires replacing the running production container, rebuilding and re-attesting
a candidate image with the calibrated method and its dispatch, and holding the 8-GPU model across
the experiment. That is a production-disrupting swap. It was not performed. Production
`neo-glm53` (image `sha256:b1259f3ea327...`) was neither stopped nor replaced during this session,
its restart count is 0, and its health endpoint returned 200 throughout.

Stating a semantic result without that run would be fabrication, so none is stated.

## 5. What is ready for the next window

- the calibrated method is registered, dispatchable and integrity-bound;
- the turn-disjoint evaluation harness exists and refuses to report quality without enough turns;
- the negative-control scoring library exists and separates correct from shuffled and null on an
  informative fixture;
- the persistent deployment mode means one authorised swap can be followed by many experiments
  without another full model reload;
- the OCC3 indicator gives an immediate, honest signal of whether a completed native delivery
  actually happened, which is a precondition for interpreting any semantic result.

## 6. Pre-registered gates for promotion

Promotion past experimental status requires all of: pooled independent calibration completed on the
real runtime; turn-disjoint held-out accuracy measured; hidden-state contribution measured;
reference-token confound controlled; semantic recovery demonstrated; correct-state beats every
matched control with a confidence interval that excludes zero; fresh sender-only facts transferred;
two independent subagents communicating; no ordinary text leakage; valid completed native receipts;
repeatability across trials; OCC-1/OCC-2/OCC-3 benchmarks complete.

None of these gates is claimed as passed.
