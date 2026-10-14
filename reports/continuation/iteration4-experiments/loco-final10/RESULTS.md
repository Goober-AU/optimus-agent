# FINAL10 — combined candidate (NN base + MM's three clauses)

Composition: NN (loco-formcons FINAL9) answerInstruction verbatim + three MM insertions:
(A) six-item full-list cap after the combine-items clause, (B) anti-padding sentence,
(C) ten-word final check before the "not mentioned" tail. importInstructions and
recallQueryStemming (true) unchanged from NN. Settings file: FINAL10.json
(typea-final10-stem.json = typeA variant, byte-diff vs NN target manifests is answerInstruction only).

Driver: rebuilt from merged HEAD 527810802 (exe sha 03144510). Battery = typeB cache-reuse
on warm cache conv-26--d4b542a8e98deca6 (same cache NN/MM used). Spots = typeA copy-out on the
frozen imp3-gate store (same as NN's target runs). Model dgx-glm53/glm-5.3.

## conv-26 19-q battery (x1, x3 redraws on disagreements)

| cell | draws | median |
|---|---|---|
| q1 | 1.0 | 1.0 |
| q2 | 0.25, 0.286, 0.286 | 0.286 |
| q18 | 1.0 | 1.0 |
| q26 | 1.0 | 1.0 |
| q33 | 0.182, 0.2, 0.2 | 0.200 |
| q37 | 0.0, 0.0, 0.667 | 0.0 |
| q39 | 0.633 | 0.633 |
| q54 | 1.0 | 1.0 |
| q55 | 0.5, 1.0, 1.0 | 1.0 |
| q57 | 0.6 | 0.6 |
| q66 | 0.0, 0.0, 0.0 | 0.0 |
| q77 | 0.0, 0.0, 0.0 | 0.0 |
| q99 | 0.148, 0.4, 0.706 | 0.400 |
| q126 | 1.0 | 1.0 |
| q135 | 1.0 | 1.0 |
| q141 | 0.625 | 0.625 |
| q143 | 1.0 | 1.0 |
| q144 | 0.615 | 0.615 |
| q149 | 0.8 | 0.8 |

## Gates

| gate | FINAL10 | NN FINAL9 ref | verdict |
|---|---|---|---|
| 18-q median aggregate >= +0.53 | 0.620 (+0.494) | 0.6585 (+0.5325) | FAIL |
| q149 >= 0.8 | 0.800 | 0.8 | pass (edge) |
| q144 >= 0.57 | 0.615 | 0.615 | pass |
| fixed set 1.0 | 1.0 all | 1.0 | pass |
| q99 >= 0.65 | 0.400 | 0.706 | FAIL |

Strict x1 aggregate: 0.578 (+0.452).

## Spot checks

- MM converts hold: conv-42_q100 0.5, conv-49_q123 1.0, conv-48_q61 0.5 — all >= 0.5.
- NN 10 targets hold: 0.5-1.0 all (q68 1.0, q131 0.5, q24 0.6, q47 1.0, q139 0.632,
  q21 0.857, q23 0.8, q172 1.0, q50-24 1.0, q10 1.0).
- Length-cap cells: conv-41_q50 3/3 non-empty (0.0/0.0/0.4 but text present);
  conv-30_q44 4/5 non-empty. x3 blank at cap AND the direct-terse rescue itself
  length-stopped (8192 output tokens, still_blank) — rescue needs a hard small
  max_tokens, not the full answer cap.

## Verdict

REJECTED. FINAL10 fails 2/5 gates (aggregate +0.494 < +0.53; q99 0.40 < 0.65).
The combination is worse than either parent variant: q33 0.667 (NN) / 0.666 (MM) -> 0.200,
q99 0.706 (NN) / 0.705 (MM) -> 0.400. NN's four-rule when-form precedence plus MM's
ten-word final check over-compress multi-event/motivation answers; anti-padding erodes
the two why-cells' evidence sentences. NN's loco-formcons FINAL9 remains the iteration
winner (passed all five gates in its own run). Do not adopt MM's three clauses on top of NN.

Run artifacts: results-battery.json, results-redraw-b.json (x3 cells), results-spots.json,
results-redraw-a.json (conv-30_q44 x4/x5), results-q10.json, jobs-*.json, run10.py.
Attempt dirs: reports/continuation/iteration4/ql-attempts-f10 (run ids f10c-*/f10d-*/f10s-*/f10m-*/f10n-*;
f10b-* .stale-typeb = quarantined first-launch collision; f10b-* .stale-typea = discarded
wrong-store typeA battery).
