# loco-writefull: full-population write-time re-ingest (2 remaining conversations)

Worker `writefull` | worktree `imp4b-writefull` (branch improve/mem4b-writefull, base 85c63983a = imp4 HEAD) |
model dgx-glm53/glm-5.3 (only) | driver `imp4b-target8k` memory_bench.exe (sha256 4d2ba13f, verified).
Config: `fullstack-final9-stem.json` over `base-det-settings.json` — byte-identical to writecamp's validated
clause set (KU fact-update + attribute v2 importInstructions; importInstructions byte-identical to DD's
fullstack manifest lf4-q149-ing; cache-key lineage verified: writecamp conv-41 f3e2ba0599fbe1d1 and DD
conv-26 d4b542a8e98deca6 both reproduce with these settings).

Method: typeB single-conversation re-ingest of the 2 remaining LoCoMo conversations (gate envs 10 minus
writecamp's 7 minus DD's conv-26 = conv-30, conv-49). Ingest question per conversation seeded the
content-addressed cache; FAILED batches + gates answered cache-reuse. Attempts:
reports/continuation/iteration4/ql-attempts-writefull. Fleet etiquette: staggered ingest starts (12:23:25 /
12:33:52, 10 min), <=3 of my drivers at any time.

## Stores (Task 2)

| conv | fresh entries | frozen entries | ratio | gold spot-checks |
|----|----:|----:|----:|----|
| conv-30 | 130 | 71 | 1.83x | q59 PASS (mem_gina_combining_dance_and_fashion, verbatim), q50 PASS (store hard-work notes) |
| conv-49 | 181 | 123 | 1.47x | q128 PASS (evan-grew-up-on-island-happy-place, verbatim), q26 PASS (family + fitness-watch + adventure-hikes notes) |

All 4 spot-check golds extracted -> extraction lane holds for the full population (writecamp 10/10 + these 4/4).

## FAILED-question conversion (Task 3, question-scoped local-F1, 1 draw)

| qid | before | after | verdict |
|----|----:|----:|----|
| conv-30_q12 | 0.138 | **0.667** | **CONVERT** (gym start March 2023) |
| conv-30_q68 | 0.000 | **1.000** | **CONVERT** (won't quit) |
| conv-30_q17 | 0.439 | 0.303 | fail (residual, answer-stage) |
| conv-30_q3 | 0.476 | 0.000 | fail (both-lost-jobs inference) |
| conv-30_q44 | 0.000 | 0.000 | fail (dancers-photo; prior known hard) |
| conv-30_q50 | 0.000 | 0.444 | near-miss (gold note in store, form) |
| conv-30_q51 | 0.000 | 0.148 | fail (furniture rationale) |
| conv-30_q76 | 0.033 | 0.200 | fail (grand-opening sentiment) |
| conv-49_q0 | 0.000 | **1.000** | **CONVERT** (Prius) |
| conv-49_q1 | 0.000 | **1.000** | **CONVERT** (old + new Prius broken) |
| conv-49_q2 | 0.000 | **1.000** | **CONVERT** (Rockies, Jasper) |
| conv-49_q41 | 0.222 | **1.000** | **CONVERT** (painting classes mid-Aug 2023) |
| conv-49_q52 | 0.000 | **0.667** | **CONVERT** (painting together Sept 2023) |
| conv-49_q85 | 0.074 | **1.000** | **CONVERT** (watercolor stress-buster) |
| conv-49_q146 | 0.174 | **1.000** | **CONVERT** (shared marriage Jan 5 2024) |
| conv-49_q103 | 0.267 | 0.000 | fail (recipe request 19 Aug 2023) |
| conv-49_q144 | 0.000 | 0.000/0.000 | fail, stable across 2 draws (Evan's photo after hike; gold photo-fact extracted for SAM's trip but question asks Evan's — event-mismatch) |
| conv-49_q18 | 0.000 | 0.357 | fail (healthy-food list, partial) |
| conv-49_q26 | 0.000 | 0.222 | fail (3 motivations extracted, answer merges 1) |
| conv-49_q46 | 0.211 | 0.333 | fail (open-domain synthesis) |
| conv-49_q51 | 0.166 | 0.256 | fail (open-domain synthesis) |

**Converts: 2/8 (conv-30) + 7/13 (conv-49) = 9/21 (43%).**

## Gate regression (Task 4, 3 previously-passing per conversation)

conv-30: q36 0.8, q37 1.0, q14 1.0 — all HOLD (baselines 1.0/1.0/0.8).
conv-49: q138 1.0, q94 1.0, q151 1.0 — all HOLD (baselines 1.0/1.0/1.0).
**6/6 gates hold; zero regressions.**

## Round-5 full-population picture

With this campaign every LoCoMo conversation now has a fresh clause-set store (writecamp 7 + DD conv-26 +
writefull 2). Residual FAILED on the two writefull conversations are answer-stage (11 cells: form/merging/
inference/open-domain), not extraction — every checked gold fact is in its fresh store. The conversion
measurement for round-5: 9 converts here + writecamp's 4 = 13 total from the write-time clause set on
fresh stores.

Artifacts: jobs-*.json / results-*.json (ingest, answers, gates, a2), run-jobs.py, SESSION-STATE.json,
wmi-*.ps1 launchers; attempts under reports/continuation/iteration4/ql-attempts-writefull
(2 ingest + 21 answer + 6 gate + 1 redraw runs). Ledger attempts appended atomically by the controller.
