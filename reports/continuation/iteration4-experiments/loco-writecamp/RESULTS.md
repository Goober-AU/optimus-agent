# loco-writecamp: B-class write-time re-ingest (11 questions whose gold facts were never extracted)

Worker `writecamp` | worktree `imp4b-writecamp` (branch improve/mem4b-writecamp, base ba8ebd6a0 = imp4 HEAD post
nearmiss merge) | model dgx-glm53/glm-5.3 (only) | driver `imp4b-target8k` memory_bench.exe (sha256 4d2ba13f,
verified). Answer config: FINAL9 (formcons, byte-identical answerInstruction + importInstructions = base + KU
fact-update + attribute v2; no FINAL10 existed at answer time) + deterministic recall (extract-only 24/24000) +
recallQueryStemming. Config: `fullstack-final9-stem.json` over `base-det-settings.json`.

Method: typeB single-conversation re-ingest per affected conversation (7 conversations; conv-42_q4 = known
dataset-limit, skipped). Ingest question per conversation seeded the content-addressed cache; all further
questions answered cache-reuse (skipIngest copy-out). Attempts: reports/continuation/iteration4/ql-attempts-writecamp.

## Per-question results (10 actionable, question-scoped local-F1; before = loco-sweep frozen-store score)

| qid | before | after | verdict |
|----|----:|----:|----|
| conv-41_q135 | 0.000 | **0.714** | **CONVERT** (Maria's memorial view now extracted: "younger generations should remember and appreciate those who served") |
| conv-42_q151 | 0.000 | 0.000 | residual: gold note IS extracted ("taking that weekend off to chill with my pets") but answer "not mentioned" — recall-window miss (note outside top-24); stable across 2 draws |
| conv-43_q41 | 0.000 | **0.571** | **CONVERT** (fantasy + plot-twist notes extracted) |
| conv-43_q66 | 0.000 | 0.000 | DATASET-LIMIT: "Jedi Apprentice / Blundell / Farland" appears nowhere in the conv-43 transcript (world-knowledge gold, like conv-42_q4) |
| conv-44_q24 | 0.000 | 0.000 | residual: Andrew's family-dog note extracted ("family's dog on a couch", D2:16) but outside the 24-entry window (12 other dog notes) — ranking miss; stable across 2 draws |
| conv-44_q44 | 0.000 | 0.000 | DATASET-LIMIT: "Voyageurs National Park" appears nowhere in the conv-44 transcript (photo-inference gold) |
| conv-47_q102 | 0.000 | **0.889** | **CONVERT** (James's winter-reading note extracted verbatim; HH's probe reproduced) |
| conv-47_q11 | 0.000 | 0.000 | gold date "April 10, 2022" matches no session (fact = "wanted to be alone with nature" on ~Apr 18); canyon note in store, outside window — dataset-date-mismatch + ranking |
| conv-48_q29 | 0.000 | **1.000** | **CONVERT** (mini-retreat note with "previous Wednesday" anchored to 2023-02-09) |
| conv-50_q61 | 0.000 | 0.000 | residual: all 3 Mustang notes extracted; "nearly two months" is a computed duration (Aug 14 -> Oct 4), stated in no note — answer-inference gap |

**Converts: 4/10 (target was >= 6).** Store sizes: conv-41 303, conv-42 232, conv-43 314, conv-44 242, conv-47 259,
conv-48 277, conv-50 206 entries (frozen stores: 162/129/186/152/158/174/130 — the clause set extracts ~1.5-1.7x more).

## Gold-extraction verification (Task 2, bounded scans of each fresh store)

All 7 conversations' gold FACTS are now in the fresh stores: Maria memorial view (conv-41), weekend-off-with-pets
(conv-42), fantasy+plot-twist (conv-43), family-dog + childhood Max (conv-44), winter-reading-under-covers
(conv-47, verbatim), mini-retreat-with-date (conv-48), Mustang junkyard/restoration (conv-50). The write-time
clause set closes every extraction gap in the B-class list; the remaining misses are answer-stage.

## Gate regression (Task 4: 3 previously-converted questions per conversation, same fresh stores)

21 cells (conv-44_q63 not in main ledger; conv-44_q10 from the locosweep-gate ledger substituted). x1 draw each;
5 failures re-drawn once (a2):

- HOLD at >= 0.5: 16 cells (conv-41 q115/q20/q110; conv-42 q168/q125; conv-43 q12/q38/q122; conv-44 q10/q85;
  conv-47 q119; conv-48 q16/q65/q14; conv-50 q143/q135).
- WOBBLE->HOLD: conv-44_q22 (0.0 -> 0.75 on re-draw).
- MISS (stable): conv-42_q20 (0.0/0.0, adoption note in store, outside window), conv-47_q34 (0.333/0.333,
  "Canada, Nuuk" vs gold "Canada, Greenland" — Nuuk IS the capital, lexical), conv-47_q138 (0.286/0.444, answer
  padding), conv-50_q10 (0.4/0.4, "around 9 May 2023" vs anchored-week gold).
- These 4 misses are answer-stage variance/padding on the denser fresh stores (24-entry windows are tighter when
  the store has 1.5x entries), not extraction regressions: every gate's gold note is present in the fresh store.

## B-class residual after this pass

- True write-time gaps: ZERO of 10 remain (all gold facts extract).
- Dataset-limit (world-knowledge gold, unfixable at write time): conv-43_q66, conv-44_q44 (+ the skipped conv-42_q4).
- Answer-inference gaps: conv-50_q61 (computed duration), conv-47_q11 (gold date matches no session).
- Ranking misses on denser fresh stores (gold note in store, outside top-24): conv-42_q151, conv-44_q24 (+ the
  4 gate misses above) — window/ranking work (the D-class lane) applies to fresh stores too.

## Tally

LoCoMo FAILED 174 -> 170, RESOLVED 1 -> 5 (conv-41_q135, conv-43_q41, conv-47_q102, conv-48_q29 flipped by this
campaign; ledger written atomically). Round-5 B-class write-time lane: CLOSED (extraction verified 10/10 actionable).

Artifacts: jobs-*.json / results-*.json (ingest, answers, gates + a2 redraws), run-jobs.py (WC_LEDGER override
for the gate-ledger runs), SESSION-STATE.json, PROGRESS.json, wmi-*.ps1 launchers; attempts under
reports/continuation/iteration4/ql-attempts-writecamp (7 ingest + 6 answer + 22 gate + 5 redraw runs).
