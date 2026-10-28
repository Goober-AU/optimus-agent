# Negative controls

## 1. Why controls decide the claim

A receiver that has never seen a private fact can still guess. "hello" is not evidence. A claim of
semantic transfer is only meaningful when the correct sender state beats matched controls that
differ from it in exactly one respect.

## 2. The control set

Defined in `occ3_sglang/calibration_corpus.py` (`CONTROL_KINDS`, `score_controls`, `separation`,
`separate_candidates`):

| Control | What it changes | What it rules out |
|---|---|---|
| correct | nothing | the baseline claim |
| wrong-sender | the sender, same task shape | cross-task generic hints |
| shuffled | row order within the state | any row-order-invariant artefact |
| null | matched-shape, matched-norm noise | "any prefix helps" |
| no attachment | no prefix at all | receiver-guess baseline |
| reference-token-only | the target embeddings without the sender state | the copier confound |
| full-text | the fact in text | achievability upper bound |

The reference-token-only control is the direct test of the shipped method's failure mode: if a
prefix built only from the receiver's own embeddings scores like the real one, the sender state is
contributing nothing.

## 3. Implementation check on a synthetic fixture

On the dim-32 rank-8 informative fixture, mean cosine of each candidate to the evaluation target
embeddings:

| Candidate | Cosine |
|---|---|
| correct | 0.9191 |
| shuffled | 0.0641 |
| null | 0.0508 |
| wrong-sender | -0.0007 |
| reference-token-only | 0.0491 |

Separation on the scoring path: null gives a margin delta of 3.0 and an accuracy delta of 1.0 over 8
trials; shuffled gives 2.0 and 1.0 over 8 trials.

These numbers show the control machinery separates a genuinely informative candidate from the
controls when one exists. They are an implementation check on synthetic data. They are **not**
evidence about GLM-5.3.

## 4. What the shipped method would score

The shipped method's prefix has cosine 0.931 to the target embedding cloud on the real runtime. It
is, by measurement, close to the reference-token-only control. That is the point of the control: the
method that most resembles the copier must not be able to claim transfer on the strength of that
resemblance.

## 5. Outstanding

No control has been run against the real runtime with the calibrated method. See
`SEMANTIC-VALIDATION.md`.
