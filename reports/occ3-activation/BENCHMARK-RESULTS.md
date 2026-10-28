# Benchmark results

## 1. Status

**No OCC-1 / OCC-2 / OCC-3 comparison benchmark was run in this session.** The arm table, the
controls and the reporting rules are defined in `BENCHMARK-METHODOLOGY.md`. No arm has a live
number.

This file exists so that the absence is recorded rather than implied.

## 2. Results that do exist, and what they are

| Result | Value | Source | Live? |
|---|---|---|---|
| median within-turn held-out R2, shipped method | 0.101 | PR #205, `LIVE-WINDOW-1.md` | yes |
| median cosine, shipped prefix to embeddings | 0.931 | PR #205 | yes |
| median Procrustes residual | 0.930 | PR #205 | yes |
| hidden-state roll sensitivity | 0.479 | PR #205 | yes |
| semantic recovery | 0 of 3 | PR #205 | yes |
| calibrated held-out R2 on synthetic fixture | 0.833 | `calibration_corpus` fixture, dim 32 rank 8 | no |
| control separation on synthetic fixture | null 3.0 margin / shuffled 2.0 margin | `NEGATIVE-CONTROLS.md` | no |

The two synthetic rows are implementation checks. They must never be quoted as model results.

## 3. Performance and cost

Not measured in this session. Latency, GPU memory overhead, payload size, throughput and cache
behaviour for OCC-3 remain unmeasured on the calibrated method. The shipped method's transport
latency is unchanged by this PR because the transport is unchanged.

## 4. Why

One authorised live window with a rebuilt, re-attested candidate image would have been needed. That
swap was not performed; see `SEMANTIC-VALIDATION.md` section 4 and `FINAL-REPORT.md`.

## 5. What the next window must produce

`TEST 1` to `TEST 24` of the campaign plan, in order, with `TEST 5` (framed versus legacy receiver
layout) before `TEST 8` (calibrated mapping), because framing must be shown not to explain a
difference before the alignment mathematics is blamed for one.
