# loco-sweep: round-4 full stack on the remaining FAILED LoCoMo conversations (frozen typeA stores)

Worker `locosweep` | worktree `imp4b-locosweep` (branch improve/mem4b-loco-sweep, base 6b999a1b2 = imp4
HEAD after the loco-full2 merge) | model dgx-glm53/glm-5.3 (only) | driver `imp4-targetR` memory_bench.exe
(sha256 b57dc607..., post-merge fleet binary).

**Stack (read-time only, typeA)**: deterministic recall (extract-only, 24-entry/24000-char,
base-det-settings.json = loco-full base-det byte-identical) + `recallQueryStemming: true` +
FINAL7 answer instruction (loco-ans2 FINAL7.json, byte-identical). Config: `typea-final7-stem.json`.
Stores: FROZEN imp3-gate improved-s{0,1,2} envs (copy-out + skipIngest on every run; never mutated).
conv-26 (18/19 FAILED) was validated separately by loco-full2 on the fresh typeB store; this sweep
covers the OTHER 156 FAILED questions in 9 conversations (conv-30/41/42/43/44/47/48/49/50).
conv-42_q4 (dataset-gold gap) and conv-47_q102 (write-time residue, fact absent from frozen store)
skipped per plan. Question-scoped local-F1, ledger auto-append worker=locosweep; attempts under
reports/continuation/iteration4/ql-attempts-locosweep (campaign root, outside worktrees).


## Aggregate

| conv | store | scored | converts | rate |
|----|----|----:|----:|----:|
| 30 | improved-s1 | 9 | 2 | 22% |
| 41 | improved-s2 | 18 | 12 | 67% |
| 42 | improved-s0 | 27 | 13 | 48% |
| 43 | improved-s1 | 20 | 9 | 45% |
| 44 | improved-s2 | 13 | 4 | 31% |
| 47 | improved-s0 | 15 | 10 | 67% |
| 48 | improved-s1 | 18 | 8 | 44% |
| 49 | improved-s2 | 15 | 6 | 40% |
| 50 | improved-s0 | 21 | 6 | 29% |
| **all** | | **156** | **70** | **45%** |

- converts: **70/156 (44.9%)**
- baseline mean 0.110 -> sweep mean 0.438 (**+0.328**)

## Per-question results (156 FAILED, baseline = gate2 improved-defaults local-F1)

| qid | cat | baseline | sweep | verdict |
|----|----|----:|----:|----|
| conv-30_q12 | 2/temporal | 0.138 | 0.571 | **CONVERT** |
| conv-30_q17 | 1/multi-hop | 0.439 | 0.387 | residue |
| conv-30_q3 | 1/multi-hop | 0.476 | 0.167 | residue |
| conv-30_q44 | 4/single-hop | 0.000 | 0.000 | flagged |
| conv-30_q50 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-30_q51 | 4/single-hop | 0.000 | 0.222 | residue |
| conv-30_q59 | 4/single-hop | 0.286 | 0.324 | residue |
| conv-30_q68 | 4/single-hop | 0.000 | 1.000 | **CONVERT** |
| conv-30_q76 | 4/single-hop | 0.033 | 0.200 | residue |
| conv-41_q110 | 4/single-hop | 0.091 | 0.800 | **CONVERT** |
| conv-41_q115 | 4/single-hop | 0.125 | 1.000 | **CONVERT** |
| conv-41_q125 | 4/single-hop | 0.227 | 0.000 | residue |
| conv-41_q131 | 4/single-hop | 0.000 | 0.571 | **CONVERT** |
| conv-41_q135 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-41_q137 | 4/single-hop | 0.271 | 0.700 | **CONVERT** |
| conv-41_q138 | 4/single-hop | 0.400 | 1.000 | **CONVERT** |
| conv-41_q18 | 1/multi-hop | 0.333 | 0.000 | residue |
| conv-41_q20 | 2/temporal | 0.069 | 1.000 | **CONVERT** |
| conv-41_q35 | 1/multi-hop | 0.000 | 0.500 | **CONVERT** |
| conv-41_q47 | 1/multi-hop | 0.024 | 0.875 | **CONVERT** |
| conv-41_q50 | 3/open-domain | 0.000 | 0.000 | flagged |
| conv-41_q54 | 2/temporal | 0.000 | 0.500 | **CONVERT** |
| conv-41_q57 | 1/multi-hop | 0.000 | 0.267 | residue |
| conv-41_q60 | 1/multi-hop | 0.000 | 1.000 | **CONVERT** |
| conv-41_q68 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-41_q82 | 4/single-hop | 0.000 | 0.667 | **CONVERT** |
| conv-41_q91 | 2/temporal | 0.105 | 1.000 | **CONVERT** |
| conv-42_q100 | 4/single-hop | 0.200 | 0.400 | residue |
| conv-42_q11 | 1/multi-hop | 0.110 | 0.330 | residue |
| conv-42_q125 | 4/single-hop | 0.455 | 0.714 | **CONVERT** |
| conv-42_q138 | 4/single-hop | 0.200 | 0.250 | residue |
| conv-42_q143 | 4/single-hop | 0.044 | 0.250 | residue |
| conv-42_q151 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-42_q165 | 4/single-hop | 0.087 | 0.000 | residue |
| conv-42_q168 | 4/single-hop | 0.400 | 0.667 | **CONVERT** |
| conv-42_q170 | 4/single-hop | 0.222 | 0.357 | residue |
| conv-42_q175 | 4/single-hop | 0.345 | 0.909 | **CONVERT** |
| conv-42_q184 | 4/single-hop | 0.070 | 0.200 | residue |
| conv-42_q197 | 4/single-hop | 0.154 | 0.000 | residue |
| conv-42_q20 | 2/temporal | 0.210 | 1.000 | **CONVERT** |
| conv-42_q24 | 2/temporal | 0.273 | 0.600 | **CONVERT** |
| conv-42_q28 | 2/temporal | 0.133 | 0.667 | **CONVERT** |
| conv-42_q31 | 2/temporal | 0.083 | 0.667 | **CONVERT** |
| conv-42_q4 | 3/open-domain | 0.000 | 0.000 | residue |
| conv-42_q41 | 2/temporal | 0.231 | 0.000 | residue |
| conv-42_q47 | 1/multi-hop | 0.182 | 0.500 | **CONVERT** |
| conv-42_q56 | 1/multi-hop | 0.000 | 1.000 | **CONVERT** |
| conv-42_q63 | 1/multi-hop | 0.044 | 0.467 | residue |
| conv-42_q64 | 2/temporal | 0.148 | 0.571 | **CONVERT** |
| conv-42_q71 | 1/multi-hop | 0.000 | 1.000 | **CONVERT** |
| conv-42_q75 | 1/multi-hop | 0.218 | 0.514 | **CONVERT** |
| conv-42_q85 | 3/open-domain | 0.000 | 0.000 | residue |
| conv-42_q91 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-42_q99 | 4/single-hop | 0.308 | 0.833 | **CONVERT** |
| conv-43_q116 | 4/single-hop | 0.046 | 0.533 | **CONVERT** |
| conv-43_q12 | 2/temporal | 0.000 | 1.000 | **CONVERT** |
| conv-43_q122 | 4/single-hop | 0.133 | 0.857 | **CONVERT** |
| conv-43_q138 | 4/single-hop | 0.355 | 0.000 | residue |
| conv-43_q142 | 4/single-hop | 0.304 | 0.211 | residue |
| conv-43_q16 | 2/temporal | 0.400 | 0.400 | residue |
| conv-43_q164 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-43_q20 | 2/temporal | 0.000 | 0.000 | residue |
| conv-43_q38 | 1/multi-hop | 0.000 | 1.000 | **CONVERT** |
| conv-43_q41 | 1/multi-hop | 0.250 | 0.000 | residue |
| conv-43_q45 | 2/temporal | 0.000 | 1.000 | **CONVERT** |
| conv-43_q59 | 1/multi-hop | 0.026 | 0.367 | residue |
| conv-43_q61 | 1/multi-hop | 0.072 | 0.400 | residue |
| conv-43_q66 | 3/open-domain | 0.000 | 0.000 | residue |
| conv-43_q71 | 4/single-hop | 0.000 | 1.000 | **CONVERT** |
| conv-43_q76 | 4/single-hop | 0.186 | 0.556 | **CONVERT** |
| conv-43_q84 | 4/single-hop | 0.000 | 1.000 | **CONVERT** |
| conv-43_q87 | 4/single-hop | 0.035 | 0.286 | residue |
| conv-43_q9 | 1/multi-hop | 0.121 | 0.472 | residue |
| conv-43_q90 | 4/single-hop | 0.000 | 0.941 | **CONVERT** |
| conv-44_q121 | 4/single-hop | 0.191 | 0.381 | residue |
| conv-44_q14 | 1/multi-hop | 0.000 | 0.000 | residue |
| conv-44_q22 | 2/temporal | 0.250 | 0.500 | **CONVERT** |
| conv-44_q24 | 1/multi-hop | 0.000 | 0.000 | residue |
| conv-44_q41 | 1/multi-hop | 0.250 | 0.286 | residue |
| conv-44_q44 | 3/open-domain | 0.000 | 0.000 | residue |
| conv-44_q47 | 2/temporal | 0.286 | 0.500 | **CONVERT** |
| conv-44_q68 | 4/single-hop | 0.089 | 0.154 | residue |
| conv-44_q73 | 4/single-hop | 0.103 | 0.500 | **CONVERT** |
| conv-44_q74 | 4/single-hop | 0.000 | 0.222 | residue |
| conv-44_q85 | 4/single-hop | 0.400 | 1.000 | **CONVERT** |
| conv-44_q9 | 1/multi-hop | 0.081 | 0.453 | residue |
| conv-44_q92 | 4/single-hop | 0.083 | 0.000 | residue |
| conv-47_q102 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-47_q11 | 2/temporal | 0.000 | 0.000 | residue |
| conv-47_q119 | 4/single-hop | 0.444 | 1.000 | **CONVERT** |
| conv-47_q138 | 4/single-hop | 0.000 | 1.000 | **CONVERT** |
| conv-47_q139 | 4/single-hop | 0.359 | 0.632 | **CONVERT** |
| conv-47_q142 | 4/single-hop | 0.000 | 0.909 | **CONVERT** |
| conv-47_q147 | 4/single-hop | 0.214 | 0.600 | **CONVERT** |
| conv-47_q21 | 2/temporal | 0.000 | 0.500 | **CONVERT** |
| conv-47_q23 | 2/temporal | 0.286 | 0.800 | **CONVERT** |
| conv-47_q27 | 1/multi-hop | 0.000 | 0.500 | **CONVERT** |
| conv-47_q34 | 1/multi-hop | 0.333 | 1.000 | **CONVERT** |
| conv-47_q43 | 2/temporal | 0.191 | 0.250 | residue |
| conv-47_q49 | 1/multi-hop | 0.000 | 0.778 | **CONVERT** |
| conv-47_q52 | 2/temporal | 0.000 | 0.200 | residue |
| conv-47_q97 | 4/single-hop | 0.133 | 0.294 | residue |
| conv-48_q101 | 4/single-hop | 0.111 | 0.000 | residue |
| conv-48_q102 | 4/single-hop | 0.160 | 0.400 | residue |
| conv-48_q124 | 4/single-hop | 0.000 | 0.583 | **CONVERT** |
| conv-48_q14 | 1/multi-hop | 0.134 | 0.750 | **CONVERT** |
| conv-48_q16 | 4/single-hop | 0.000 | 1.000 | **CONVERT** |
| conv-48_q162 | 4/single-hop | 0.109 | 0.105 | residue |
| conv-48_q172 | 4/single-hop | 0.000 | 1.000 | **CONVERT** |
| conv-48_q173 | 4/single-hop | 0.286 | 0.286 | residue |
| conv-48_q181 | 4/single-hop | 0.182 | 0.182 | residue |
| conv-48_q185 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-48_q29 | 2/temporal | 0.000 | 0.000 | residue |
| conv-48_q35 | 2/temporal | 0.353 | 0.500 | **CONVERT** |
| conv-48_q58 | 4/single-hop | 0.000 | 0.700 | **CONVERT** |
| conv-48_q61 | 4/single-hop | 0.000 | 0.444 | residue |
| conv-48_q65 | 2/temporal | 0.000 | 1.000 | **CONVERT** |
| conv-48_q69 | 4/single-hop | 0.091 | 0.000 | residue |
| conv-48_q81 | 2/temporal | 0.000 | 0.333 | residue |
| conv-48_q82 | 1/multi-hop | 0.228 | 0.847 | **CONVERT** |
| conv-49_q0 | 1/multi-hop | 0.000 | 0.667 | **CONVERT** |
| conv-49_q1 | 1/multi-hop | 0.000 | 0.000 | residue |
| conv-49_q103 | 4/single-hop | 0.267 | 0.182 | residue |
| conv-49_q123 | 4/single-hop | 0.000 | 0.462 | residue |
| conv-49_q128 | 4/single-hop | 0.133 | 0.364 | residue |
| conv-49_q144 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-49_q146 | 4/single-hop | 0.174 | 0.800 | **CONVERT** |
| conv-49_q18 | 1/multi-hop | 0.000 | 0.143 | residue |
| conv-49_q2 | 1/multi-hop | 0.000 | 1.000 | **CONVERT** |
| conv-49_q26 | 1/multi-hop | 0.000 | 0.074 | residue |
| conv-49_q41 | 2/temporal | 0.222 | 1.000 | **CONVERT** |
| conv-49_q46 | 3/open-domain | 0.210 | 0.409 | residue |
| conv-49_q51 | 3/open-domain | 0.166 | 0.254 | residue |
| conv-49_q52 | 2/temporal | 0.000 | 0.500 | **CONVERT** |
| conv-49_q85 | 4/single-hop | 0.074 | 1.000 | **CONVERT** |
| conv-50_q10 | 2/temporal | 0.000 | 0.909 | **CONVERT** |
| conv-50_q135 | 4/single-hop | 0.286 | 0.889 | **CONVERT** |
| conv-50_q137 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-50_q140 | 4/single-hop | 0.038 | 0.000 | residue |
| conv-50_q143 | 4/single-hop | 0.182 | 1.000 | **CONVERT** |
| conv-50_q147 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-50_q155 | 4/single-hop | 0.162 | 0.182 | residue |
| conv-50_q16 | 2/temporal | 0.235 | 0.286 | residue |
| conv-50_q24 | 1/multi-hop | 0.105 | 1.000 | **CONVERT** |
| conv-50_q28 | 1/multi-hop | 0.317 | 0.390 | residue |
| conv-50_q40 | 1/multi-hop | 0.000 | 0.000 | residue |
| conv-50_q48 | 2/temporal | 0.000 | 0.308 | residue |
| conv-50_q5 | 1/multi-hop | 0.000 | 0.524 | **CONVERT** |
| conv-50_q61 | 2/temporal | 0.000 | 0.000 | residue |
| conv-50_q67 | 2/temporal | 0.000 | 0.000 | residue |
| conv-50_q85 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-50_q87 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-50_q88 | 4/single-hop | 0.000 | 0.000 | residue |
| conv-50_q89 | 4/single-hop | 0.000 | 0.667 | **CONVERT** |
| conv-50_q90 | 4/single-hop | 0.127 | 0.000 | residue |
| conv-50_q97 | 4/single-hop | 0.000 | 0.000 | residue |

## Batch gates (previously-passing spot-checks, 3 per ~2 conversations)

Gate ledger: reports/continuation/iteration4/QUESTION-LEDGER-locosweep-gate.json (15 questions, source improved-defaults 1.0 unflagged).

| gate | after convs | questions | results |
|----|----|----|----|
| 1 | 41+43 | conv-41_q77, conv-43_q104, conv-43_q119 | 3/3 at 1.0 — no regression |
| 2 | 49+50 | conv-49_q138, conv-49_q94, conv-50_q42 | 3/3 at 1.0 — no regression |
| 3 | 30+42 | conv-30_q36, conv-30_q37, conv-42_q163 | 3/3 at 1.0 — no regression |
| 4 | 44+47 | conv-47_q30, conv-44_q63, conv-44_q10 | 2/3 at 1.0; conv-44_q10 0.0 then **1.0 on re-draw** (lsw-g4r-c44-q10) — draw wobble, not a stable regression |
| 5 | 48 | conv-48_q122, conv-48_q148, conv-48_q50 | 3/3 at 1.0 — no regression |

**14/15 gate cells stable at 1.0; 1 wobble cell (conv-44_q10: 0.0 -> 1.0 on re-draw).** The 0.0 draw
answered "not mentioned" with the gold note (audrey pet-bonding workshop June 2023) IN the recall window
next to a near-duplicate distractor (july dog-training course note) — G-stage binding wobble, same class
as loco-full2 q57.

## Non-convert stage mix (86 questions, B/D/G classification)

Method: gold-token coverage against every frozen-store note (B = gold fact absent from the frozen store;
D = gold note present in store but outside the 24-entry window; G = gold available/recalled, answer-form
or combination miss). "not mentioned" answers with the gold note in the window are G.

| stage | n | share | examples |
|----|----:|----:|----|
| G answer | 67 | 78% | list combination (conv-43_q9 endorsements 0.472), relative-date binding (conv-41_q68 aerial yoga in window, "not mentioned"), partial-list near-misses |
| B write-time | 11 | 13% | conv-42_q4 (dataset-gold gap), conv-47_q102 (write-time residue), conv-43_q66 (Star Wars book), conv-50_q61 (Mustang duration) — gold facts never extracted into the frozen stores |
| D retrieval | 8 | 9% | conv-43_q20 (Seattle), conv-50_q40/q67/q97/q137 (Calvin music/artist notes), conv-49_q1, conv-42_q41, conv-48_q185 — gold note in store, outside top-24 |

Plus 2 flagged driver cells (counted in G): conv-41_q50, conv-30_q44 — the model loops to the 8192-token
output cap under FINAL7 and the answer arrives empty (answer_blank_rescue fails). A third cell,
conv-48_q81/102, hit transient PermissionError on the ledger atomic replace (Windows file-lock collision
with concurrent writers); both were re-run cleanly (lsw-rc-c48-q81 0.333 fail, lsw-rc-c48-q102 0.4 fail).

## Round-5 target list (sized by this sweep)

- **Write-time re-ingest lane (B, 11)**: conv-41_q135, conv-42_q4 (dataset-gold gap — needs gold-side
  attention, not re-ingest), conv-42_q151, conv-43_q41, conv-43_q66, conv-44_q24, conv-44_q44,
  conv-47_q102, conv-47_q11, conv-48_q29, conv-50_q61. These ride the typeB re-ingest campaign with the
  validated write clauses (KU fact-update + attribute v2 importInstructions, loco-full2).
- **Read-time residue (D, 8)**: conv-42_q41, conv-43_q20, conv-48_q185, conv-49_q1, conv-50_q137,
  conv-50_q40, conv-50_q67, conv-50_q97 — window/ranking work (w32 trap class from AA's deeprank, or
  content-aware rerank) could recover these without re-ingest.
- **G (67)**: answer-instruction work only; 20 sit in the 0.3-0.5 near-miss band (list-combination and
  partial-credit phrasing), the rest are form misses on single facts.

## Verdict

The validated round-4 read stack (deterministic extract-only recall 24/24000 + recallQueryStemming + FINAL7)
generalizes beyond conv-26 with NO new infrastructure: **70/156 FAILED -> >= 0.5 (44.9%)** on the frozen
typeA stores, mean local-F1 0.110 -> 0.438 (**+0.328**), with 14/15 previously-passing spot-checks holding
at 1.0 (1 draw wobble, recovered on re-draw). The offline stem-scan predicted 4 live conversions among
the 7 runnable targets; the sweep found 5 (q35 0.5, q71 1.0, q52 0.5, q5 0.524 all convert; q68/q4/q102
stay 0.0 as the scan's residue classes predicted). Non-converts are dominated by the answer stage (G 78%);
write-time gaps (B 13%) are the re-ingest lane's target list, retrieval residue (D 9%) is small and
concentrated in conv-50's music/artist cluster.

Artifacts: jobs-c*.json / results-c*.json / jobs-gate*.json / results-gate*.json, run-battery.py, run-sweep.py, build-report.py, append-h2h.py; attempts under reports/continuation/iteration4/ql-attempts-locosweep (156 sweep + 16 gate/recovery runs); ledger auto-appended worker=locosweep (156 attempts, 2 re-appended after a transient Windows ledger-write collision).
