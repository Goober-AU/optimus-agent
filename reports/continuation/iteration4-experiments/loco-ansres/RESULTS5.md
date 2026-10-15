# loco-ansres round-5 execution: FINAL9 fresh-store re-run + pass-latest flips

Worker `ansres` | worktree `imp4b-ansres` (branch `improve/mem4b-ansres`) | model dgx-glm53/glm-5.3 |
driver `imp4b-target8k` memory_bench.exe (sha256 4d2ba13f — the same binary as every FINAL9 fresh-store
run this round: writecamp/writefull/formcons/drank; the cache key never includes the driver) |
controller `scripts/memory_question_loop.py` (worktree copy, upstream) | attempts under
`reports/continuation/iteration4/ql-attempts-ansres` (campaign root, outside worktrees).

## What ran

Wrap item #1 of the ANSWER-RESIDUALS sequence: answer-only typeB **cache-reuse** runs (skipIngest
copy-out) on the 8 warm clause-set stores (cache keys verified against the CACHE-AUDIT: conv-26
d4b542a8, conv-41 f3e2ba0, conv-42 beda05a, conv-43 cfbee6d, conv-44 15430ae, conv-47 3f79028,
conv-48 029159a, conv-50 a336692 — all 116 job runs + 1 preflight landed `mode_detail: cache-reuse`;
zero re-ingests), FINAL9 answer instruction (ai sha 2cee82a9) + `recallQueryStemming`, window 24,
local-F1 question-scoped. 117 cells: **R group** = 59 fail-latest FINAL7-frozen-measured cells (1 draw),
**F group** = 58 pass-latest cells (45 stability-STABLE + 13 conv-26 FINAL9-battery; a failing draw got
one a2 redraw). One driver per conversation, 5-min stagger, live-driver fleet check before each launch
(cap 11; the fleet sat at 12-14 from the lmesq/lpcamp2 queues, so 3 launches rode the 15-min gate
deadline — logged in launch5.log). Wall clock 01:27-02:17.

## Outcome (117 cells)

| group | cells | pass | fail | note |
|---|---:|---:|---:|---|
| R (fail-latest, FINAL7-measured) | 59 | **20** | 39 | 39 = FINAL9-confirmed residual |
| F (pass-latest, STABLE + conv-26 battery) | 58 | **53** | 5 | 5 = fresh-store failures x2 draws |
| total | 117 | 73 | 44 | |

- 73 cells flipped to RESOLVED (see ledger resolution blocks; commit + run-ids per cell).
- 44 cells stay FAILED: 39 R-group with FINAL9-confirmed notes, 5 F-group with fresh-store-failure
  notes (they were stability-STABLE under FINAL7-frozen but fail both draws on the denser fresh store).

## R-group conversions (20) — the FINAL9-on-fresh-store effect

conv-41_q131 (0.5), conv-41_q18 (0.5, "His colleague Rob."), conv-42_q11 (0.75), conv-43_q142 (0.688),
conv-43_q9 (0.584), conv-44_q121 (1.0), conv-44_q68 (0.667), conv-44_q74 (0.75), conv-44_q9 (0.707),
conv-47_q139 (0.714), conv-47_q21 (1.0), conv-47_q23 (0.8), conv-47_q43 (0.8), conv-48_q102 (0.5),
conv-48_q173 (0.667), conv-48_q181 (1.0), conv-48_q69 (1.0), conv-50_q16 (1.0), conv-50_q85 (0.5),
conv-50_q90 (0.533).

The temporal-form family converted almost completely: the month-span cells (conv-47_q43
"September 2022", conv-50_q16 "July 2023"), the ISO-leak cell (conv-47_q21 "May 7, 2022" 1.0),
conv-47_q23 "July 2022" — FINAL9 rule (3) fires correctly when the fresh-store note titles carry the
calendar form. Also notable: yes/no form (conv-48_q69 "yes" 1.0), event binding (conv-48_q181
"On a beach." 1.0), and 3 list-completeness cells (conv-44_q9 0.707, conv-43_q9 0.584, conv-42_q11 0.75).

## F-group fresh-store failures (5, kept FAILED)

| qid | a1/a2 | answer | diagnosis |
|---|---|---|---|
| conv-41_q47 | 0.375/0.375 | 'weight training' | exercises list collapsed to 1 item on the denser store |
| conv-42_q75 | 0.383/0.383 | 'coconut milk ice cream and dairy-free chocolate mousse' | desserts list 2 of 4 (was 0.601 FINAL7-frozen) |
| conv-43_q38 | 0.0/0.0 | 'not mentioned' | was STABLE 1.0 frozen; fresh 314-entry store loses the UK gold binding — same dilution class drank's H1 found at w32, now at w24 |
| conv-47_q27 | 0.286/0.286 | 'a course combining gaming and programming' | classes list, wrong item selection |
| conv-48_q82 | 0.287/0.0 | 'not mentioned' (a2) | yoga-locations list; a1 partial, a2 abstains |

## Before/after ledger tally

| | before | after |
|---|---:|---:|
| LoCoMo FAILED | 158 | **85** |
| LoCoMo RESOLVED | 17 | **90** |
| all-benchmark FAILED | 162 | **89** |
| all-benchmark RESOLVED | 26 | **99** |

Round-5 true LoCoMo residual (85 FAILED): answer-stage 71 (44 tonight-confirmed + 23 prior
FINAL9-confirmed + 4 variance-band), deep-rank 8, unextracted/write-time 3, edge-band (w25-36) 3.
The 44+23 = 67 FINAL9-confirmed answer cells are the C1/C2 clause lane's measured target set.

## Files

jobs-r5-*.json (8), results-r5-*.json + .done (8), run5-*.log, launch5.log, launch5-summary.json,
run5.py, launch5.py, flip5.py, tally5.py, tally5.json. Preflight: jobs-r5-preflight.json +
results-r5-preflight.json (conv-42_q11, cache-reuse verified before fleet launch).

*Provenance: all scores read from the controller-appended ledger attempts (worker=ansres, run-ids
ar5-cNN-qXX-a1/a2); no manual score edits; flips applied under the controller's LedgerLock via flip5.py.*
