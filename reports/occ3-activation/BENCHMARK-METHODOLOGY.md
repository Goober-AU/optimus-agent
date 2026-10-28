# Benchmark methodology

## 1. Principle

Do not report a successful transport receipt as a correct semantic result. Transport and semantics
are measured separately and reported separately.

## 2. Arms

| Arm | Encoding |
|---|---|
| TXT | ordinary text |
| OCC-1 | OCC/1 structured body |
| OCC-2 | symbolic dialect with OCC-1 fallback |
| OCC-3 shipped | per-message StateBridge continuous prefix |
| OCC-3 calibrated | frozen independent-calibration prefix |
| no communication | receiver answers with no attachment |
| null prefix | matched-shape noise |
| shuffled sender state | row order permuted |
| unrelated sender state | a different sender's capture |
| reference-token-only | target embeddings, no sender state |
| full text | the private fact in the receiver's prompt |

## 3. Controls held fixed

Same GLM-5.3 checkpoint, same TP8 topology, same FP8 recipe, same decoding settings, same maximum
reasoning configuration unless reasoning effort is itself the labelled factor, same receiver
question, same prefix placement policy, same ordinary prompt.

## 4. Measurements

Exact-match accuracy; semantic recovery accuracy; task completion; correct-answer likelihood over
the whole candidate sequence; correct-state versus shuffled-state margin; latency split into source
capture, alignment and receiver inference; output tokens; end-to-end inference cost; GPU memory
overhead; communication payload size; native failure rate; throughput; cache performance.

## 5. Reporting rules

- complete trials reported separately from interrupted, timed-out, uncertain-native and
  rejected-incompatible trials;
- confidence intervals, not point estimates;
- benchmark seeds recorded;
- raw machine-readable results retained;
- the task set is versioned and never edited after seeing results without a new version.

## 6. Pre-registered feasibility rule

Fixed before any window: FEASIBLE when median turn-disjoint held-out `R2 >= 0.30` **and** median
`cos(prefix, embedding) <= 0.30`; NOT-FEASIBLE when median `R2 < 0.10`; otherwise INCONCLUSIVE.
Any candidate whose copier cosine stays above 0.9 is not a candidate.
