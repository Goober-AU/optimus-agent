# loco-stability: sweep-convert stability confirmation (x2 draws)

Worker `locostab` | worktree `imp4b-stability` (branch improve/mem4b-stability, base fbd538d3c = imp4 HEAD
after the rerank parse-retry merge) | model dgx-glm53/glm-5.3 (only) | driver `imp4-targetR` memory_bench.exe
(sha256 b57dc607..., byte-identical to the sweep's binary). Controller `scripts/memory_question_loop.py`
(unchanged between the sweep base 6b999a1b2 and fbd538d3c). Attempts under
`reports/continuation/iteration4/ql-attempts-locostab` (campaign root, outside worktrees); ledger
auto-append worker=locostab.

**Purpose.** JJ's loco-sweep converted 70 FAILED questions at n=1 draw each under the promoted read
stack. This worker re-ran every convert x2 fresh draws (same config, same frozen-store copy-out,
question-scoped local-F1) and classifies each: STABLE (both >= 0.5), VARIANCE-BAND (one >= 0.5),
UNSTABLE (both < 0.5 = the sweep convert was a fluke draw).

**Note on the convert count.** The ROUND4-SCORECARD prose says '67 sweep converts', but the ledger
and the sweep's own RESULTS.md say 70 (scorecard table total 87 FIXED = 17 core + 70 sweep; the 67
and the '71 fixed' bullet list are prose slips — 71 = 70 converts + conv-47_q102, a writeres fix).
All 70 were re-run here.

## Stack (byte-identical to the sweep)

- typeA copy-out of the frozen imp3-gate improved-s{0,1,2} stores, skipIngest, deterministic recall
  (extract-only, 24 entries / 24000 chars, `base-det-settings.json`) + `recallQueryStemming: true`
  + FINAL7 answerInstruction (`typea-final7-stem.json`).
- Same models.json / judge-models.json / bench venv / scoring cwd as the sweep; local-F1 (deterministic).

## Aggregate

| metric | value |
|---|---|
| converts re-run | 70 (x2 draws = 140 runs + 1 smoke + 1 ledger-collision retry) |
| STABLE (both >= 0.5) | 57 |
| VARIANCE-BAND (one >= 0.5) | 7 |
| UNSTABLE (both < 0.5) | 6 |
| draw pass-rate (>= 0.5) | sweep 70/70, draw1 59/70 (84%), draw2 62/70 (89%) |
| mean local-F1 | sweep draws 0.789, draw1 0.738, draw2 0.768 |
| d1/d2 byte-identical answers | 38/70 (54%) |
| median-of-2-draws >= 0.5 | 63/70 |
| recall window differences across draws | **0/70 questions** (210/210 draw windows byte-identical incl. the sweep draw) |

## Determinism check (task 4)

Every one of the 70 questions produced the **identical recall id list and char count in all draws**
(sweep draw + 2 fresh draws; conv-44_q10 gate draws included). The typeA store copy-out and the
deterministic recall path are fully reproducible; **all observed variance is on the answer side**
(glm-5.3 sampling), exactly the q135/q33 answer-form wobble class. No store or recall drift found.

## Variance taxonomy (answer-side, recall fixed)

1. **Paraphrase/synonym substitution** — right fact, non-gold tokens: conv-30_q68 'quit' vs 'give up'
   (2x 1.0, 2x 0.0 across 4 draws); conv-44_q73 'pet-friendly' vs 'dog-friendly'.
2. **Over-elaboration** — correct content plus extra tokens crushing precision: conv-41_q131,
   conv-42_q24 ('(around mid-June 2022)' appended), conv-43_q116, conv-50_q24 ('Yes — ...since age ten').
3. **Temporal-form selection** — relative-phrase policy vs gold calendar form: conv-47_q23 x2
   'the month after 13 June 2022' (0.25) vs gold 'In July, 2022'; conv-42_q24; conv-44_q10 (below).
4. **ISO-date leak** — 'the day before 2022-05-08' (conv-47_q21 d2, 0.0 vs 0.5 for '8 May 2022')
   — the q57 ISO-leak class FINAL8 closed on conv-26 still leaks elsewhere.
5. **Format corruption / narration glitch** — model echoes a tool payload instead of the answer:
   conv-48_q172 d1 emitted a literal `<memory_data>{...}` block (0.054); d2 = 1.0.
6. **Wrong-content selection** — a different note's fact wins: conv-47_q147 ('playing FIFA 23' vs the
   practice item), conv-50_q89 (wrong music note).

Classes 1-3 match the known q135/q33 answer-form wobble; 4 is a residual FINAL8 gap; 5 is the
no-narration glitch class; 6 is genuine G-class content selection, not form.

## conv-44_q10 regression diagnosis (task 5)

Question: 'When did Audrey's positive reinforcement training course for dogs take place?' gold 'June, 2023'.
Baseline (improved-defaults, no FINAL7): 1.0 stable (gate source). Under the promoted stack, 5
independent draws (JJ g4 + g4-retry + 3 here), **identical 12-note recall window every draw**, gold note
(`mem-conv44-audrey-training-workshop`: 'workshop ... taking place the next month (June 2023) ... it is
a positive reinforcement training class') in-window next to the near-duplicate distractor
`mem-conv44-audrey-training-course-july`:

| draw | answer | score |
|---|---|---:|
| sweep g4 | not mentioned | 0.0 |
| sweep g4-retry | June 2023 | 1.0 |
| stab x1 | not mentioned | 0.0 |
| stab x2 | the month after 11 May 2023 | 0.2857 |
| stab x3 | the month after 11 May 2023 | 0.2857 |

Diagnosis: **answer-stage temporal-form + binding wobble, NOT a retrieval or store regression** — the
gold is in-window in every draw. The FINAL7 precedence rule (2) (relative phrase anchored to a dated
chat) fires on a note that carries BOTH 'the next month' and '(June 2023)', so the model wobbles
between the anchored relative phrase (partial credit), the bare calendar form (full credit), and
'not mentioned' when the July-course distractor wins the binding. Median over 5 draws 0.2857.
JJ's '1.0 on re-draw — draw wobble, not a stable regression' was optimistic: it is a real (form-driven)
regression of the promoted read config on a previously-stable question, same class as the variance-band
converts above. Not counted in the FAILED-queue tally (it is not a FAILED question).

## Updated round-4 tally (task 3)

| class | scorecard | after stability x2 | delta |
|---|---:|---:|---|
| FIXED | 87 | 74 | -13 (6 UNSTABLE -> OPEN-answer, 7 VARIANCE-BAND -> PARTIAL) |
| PARTIAL | 22 | 29 | +7 variance-band |
| OPEN-answer | 49 | 55 | +6 unstable |
| OPEN-retrieval | 8 | 8 | |
| OPEN-write-time | 12 | 12 | |
| DATASET-LIMIT | 1 | 1 | |

LoCoMo only: FIXED 84 -> 71 (57 stable sweep + 13 conv-26 battery + 1 writeres q102), PARTIAL 21 -> 28,
OPEN-answer 49 -> 55. The honest round-4 headline: **74/179 FAILED fixed with 2-draw confirmation
(57 of the sweep converts hold; 7 more hold at median >= 0.5 but wobble below the bar on one draw;**
**6 do not reproduce).** If the bar is 'median of draws >= 0.5' instead, 81/179 (63/70 sweep converts).

## Per-question classification (70 sweep converts)

| qid | cat | baseline | sweep | d1 | d2 | median | class |
|---|---|---:|---:|---:|---:|---:|---|
| conv-30_q12 | temporal | 0.138 | 0.571 | 0.571 | 0.571 | 0.571 | STABLE |
| conv-30_q68 | single-hop | 0.000 | 1.000 | 0.000 | 1.000 | 0.500 | VARIANCE-BAND |
| conv-41_q110 | single-hop | 0.091 | 0.800 | 0.800 | 0.800 | 0.800 | STABLE |
| conv-41_q115 | single-hop | 0.125 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-41_q131 | single-hop | 0.000 | 0.571 | 0.471 | 0.471 | 0.471 | UNSTABLE |
| conv-41_q137 | single-hop | 0.271 | 0.700 | 0.700 | 0.700 | 0.700 | STABLE |
| conv-41_q138 | single-hop | 0.400 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-41_q20 | temporal | 0.069 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-41_q35 | multi-hop | 0.000 | 0.500 | 0.500 | 0.500 | 0.500 | STABLE |
| conv-41_q47 | multi-hop | 0.024 | 0.875 | 0.875 | 0.875 | 0.875 | STABLE |
| conv-41_q54 | temporal | 0.000 | 0.500 | 0.500 | 0.500 | 0.500 | STABLE |
| conv-41_q60 | multi-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-41_q82 | single-hop | 0.000 | 0.667 | 0.857 | 0.857 | 0.857 | STABLE |
| conv-41_q91 | temporal | 0.105 | 1.000 | 1.000 | 0.667 | 0.833 | STABLE |
| conv-42_q125 | single-hop | 0.455 | 0.714 | 0.714 | 0.769 | 0.742 | STABLE |
| conv-42_q168 | single-hop | 0.400 | 0.667 | 0.800 | 0.800 | 0.800 | STABLE |
| conv-42_q175 | single-hop | 0.345 | 0.909 | 1.000 | 0.800 | 0.900 | STABLE |
| conv-42_q20 | temporal | 0.210 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-42_q24 | temporal | 0.273 | 0.600 | 0.462 | 0.600 | 0.531 | VARIANCE-BAND |
| conv-42_q28 | temporal | 0.133 | 0.667 | 0.667 | 0.667 | 0.667 | STABLE |
| conv-42_q31 | temporal | 0.083 | 0.667 | 0.667 | 0.667 | 0.667 | STABLE |
| conv-42_q47 | multi-hop | 0.182 | 0.500 | 0.500 | 0.500 | 0.500 | STABLE |
| conv-42_q56 | multi-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-42_q64 | temporal | 0.148 | 0.571 | 0.667 | 0.667 | 0.667 | STABLE |
| conv-42_q71 | multi-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-42_q75 | multi-hop | 0.218 | 0.514 | 0.698 | 0.630 | 0.664 | STABLE |
| conv-42_q99 | single-hop | 0.308 | 0.833 | 0.880 | 0.833 | 0.857 | STABLE |
| conv-43_q116 | single-hop | 0.046 | 0.533 | 0.476 | 0.476 | 0.476 | UNSTABLE |
| conv-43_q12 | temporal | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-43_q122 | single-hop | 0.133 | 0.857 | 0.857 | 0.857 | 0.857 | STABLE |
| conv-43_q38 | multi-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-43_q45 | temporal | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-43_q71 | single-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-43_q76 | single-hop | 0.186 | 0.556 | 0.556 | 0.556 | 0.556 | STABLE |
| conv-43_q84 | single-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-43_q90 | single-hop | 0.000 | 0.941 | 0.941 | 0.941 | 0.941 | STABLE |
| conv-44_q22 | temporal | 0.250 | 0.500 | 0.500 | 0.500 | 0.500 | STABLE |
| conv-44_q47 | temporal | 0.286 | 0.500 | 0.400 | 0.800 | 0.600 | VARIANCE-BAND |
| conv-44_q73 | single-hop | 0.103 | 0.500 | 0.200 | 0.400 | 0.300 | UNSTABLE |
| conv-44_q85 | single-hop | 0.400 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-47_q119 | single-hop | 0.444 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-47_q138 | single-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-47_q139 | single-hop | 0.359 | 0.632 | 0.632 | 0.435 | 0.533 | VARIANCE-BAND |
| conv-47_q142 | single-hop | 0.000 | 0.909 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-47_q147 | single-hop | 0.214 | 0.600 | 0.000 | 0.000 | 0.000 | UNSTABLE |
| conv-47_q21 | temporal | 0.000 | 0.500 | 0.500 | 0.000 | 0.250 | VARIANCE-BAND |
| conv-47_q23 | temporal | 0.286 | 0.800 | 0.250 | 0.250 | 0.250 | UNSTABLE |
| conv-47_q27 | multi-hop | 0.000 | 0.500 | 0.500 | 0.500 | 0.500 | STABLE |
| conv-47_q34 | multi-hop | 0.333 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-47_q49 | multi-hop | 0.000 | 0.778 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-48_q124 | single-hop | 0.000 | 0.583 | 0.500 | 0.609 | 0.554 | STABLE |
| conv-48_q14 | multi-hop | 0.134 | 0.750 | 1.000 | 0.500 | 0.750 | STABLE |
| conv-48_q16 | single-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-48_q172 | single-hop | 0.000 | 1.000 | 0.054 | 1.000 | 0.527 | VARIANCE-BAND |
| conv-48_q35 | temporal | 0.353 | 0.500 | 0.500 | 0.500 | 0.500 | STABLE |
| conv-48_q58 | single-hop | 0.000 | 0.700 | 0.700 | 0.700 | 0.700 | STABLE |
| conv-48_q65 | temporal | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-48_q82 | multi-hop | 0.228 | 0.847 | 0.847 | 0.635 | 0.741 | STABLE |
| conv-49_q0 | multi-hop | 0.000 | 0.667 | 0.667 | 1.000 | 0.833 | STABLE |
| conv-49_q146 | single-hop | 0.174 | 0.800 | 0.667 | 1.000 | 0.833 | STABLE |
| conv-49_q2 | multi-hop | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-49_q41 | temporal | 0.222 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-49_q52 | temporal | 0.000 | 0.500 | 0.667 | 0.500 | 0.583 | STABLE |
| conv-49_q85 | single-hop | 0.074 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-50_q10 | temporal | 0.000 | 0.909 | 0.909 | 0.909 | 0.909 | STABLE |
| conv-50_q135 | single-hop | 0.286 | 0.889 | 0.889 | 0.889 | 0.889 | STABLE |
| conv-50_q143 | single-hop | 0.182 | 1.000 | 1.000 | 1.000 | 1.000 | STABLE |
| conv-50_q24 | multi-hop | 0.105 | 1.000 | 0.133 | 1.000 | 0.567 | VARIANCE-BAND |
| conv-50_q5 | multi-hop | 0.000 | 0.524 | 1.000 | 0.679 | 0.839 | STABLE |
| conv-50_q89 | single-hop | 0.000 | 0.667 | 0.000 | 0.222 | 0.111 | UNSTABLE |

Answer forms for the 13 non-STABLE converts (sweep / draw1 / draw2):

- **conv-30_q68 (VARIANCE-BAND, median 0.500)**: sweep 1.000 'Quit' | d1 0.000 'give up' | d2 1.000 'quit'
- **conv-41_q131 (UNSTABLE, median 0.471)**: sweep 0.571 'Dropping off baked goods at the homeless shelter where she volunteers' | d1 0.471 'Dropping off baked goods she had made at the homeless shelter where sh' | d2 0.471 'Dropping off baked goods she had made at the homeless shelter where sh'
- **conv-42_q24 (VARIANCE-BAND, median 0.531)**: sweep 0.600 'two weekends after 3 June 2022' | d1 0.462 'two weekends after 3 June 2022 (around mid-June 2022)' | d2 0.600 'Two weekends after June 3, 2022'
- **conv-43_q116 (UNSTABLE, median 0.476)**: sweep 0.533 'Driven to improve, they back each other up.' | d1 0.476 "They back each other up, won't quit, and facing them drives them to ge" | d2 0.476 'Tough opponents drive the team to get better; they back each other up '
- **conv-44_q47 (VARIANCE-BAND, median 0.600)**: sweep 0.500 'about a month' | d1 0.400 'About 1 month' | d2 0.800 'About one month'
- **conv-44_q73 (UNSTABLE, median 0.300)**: sweep 0.500 'Finding a dog-friendly place to live in the city' | d1 0.200 'Finding a dog-friendly place to live' | d2 0.400 'finding a pet-friendly place to live'
- **conv-47_q139 (VARIANCE-BAND, median 0.533)**: sweep 0.632 'Playing Super Mario and The Legend of Zelda on his first gaming system' | d1 0.632 'Playing Super Mario and The Legend of Zelda on his first gaming system' | d2 0.435 'Playing Super Mario and The Legend of Zelda on the Nintendo console th'
- **conv-47_q147 (UNSTABLE, median 0.000)**: sweep 0.600 'practicing with a gamepad and a sense of timing' | d1 0.000 'playing FIFA 23' | d2 0.000 'FIFA 23'
- **conv-47_q21 (VARIANCE-BAND, median 0.250)**: sweep 0.500 'the day before 8 May 2022' | d1 0.500 'the day before 8 May 2022' | d2 0.000 'the day before 2022-05-08'
- **conv-47_q23 (UNSTABLE, median 0.250)**: sweep 0.800 'July 2022' | d1 0.250 'the month after 13 June 2022' | d2 0.250 'the month after 13 June 2022'
- **conv-48_q172 (VARIANCE-BAND, median 0.527)**: sweep 1.000 'Monster Hunter: World' | d1 0.054 '<memory_data>\n{"hint_paths": ["C:/Users/openclawuser/optimus-memory-be' | d2 1.000 'Monster Hunter: World'
- **conv-50_q24 (VARIANCE-BAND, median 0.567)**: sweep 1.000 'Yes.' | d1 0.133 'Yes — he has been tinkering with and fixing car engines himself since ' | d2 1.000 'Yes.'
- **conv-50_q89 (UNSTABLE, median 0.111)**: sweep 0.667 'Music experimenting with different genres, adding electronic elements' | d1 0.000 "Deep, emotional music inspired by people's struggles." | d2 0.222 'Music collaborations with Japanese artists.'

## Ops notes

- 1 transient `PermissionError` on the ledger atomic replace (Windows file-lock collision, same class
  as the sweep's conv-48_q81/102 cells); the run was re-run cleanly as `lstb-c41-q91-d1r` (1.0).
- conv-44_q10 x-draws were appended to the GATE ledger (QUESTION-LEDGER-locosweep-gate.json), matching
  the sweep's gate pattern; the 140 convert draws went to the main ledger, worker=locostab.
- Artifacts: jobs-d1/d2/q10/retry.json, results-*.json, classification.json, spot.py, classify.py,
  run-battery.py (this dir); attempts under reports/continuation/iteration4/ql-attempts-locostab.

*Provenance: generated by classify.py from results-d{1,2}.json + the ledger; recall determinism verified
per-question across all draws (see classification.json fields recall_same_across_draws / recall_same_as_jj).*
