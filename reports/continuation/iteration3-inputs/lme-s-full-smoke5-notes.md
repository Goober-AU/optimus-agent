# LME-S full-set smoke5 — first full iteration-3 stack S data point

Run: `lme_s_full_smoke5_v1` (2026-10-12). Worker: lme-s-smoke5 (worktree `imp3-lmessmoke`,
branch `improve/mem3-lmes-smoke`, base = imp3 HEAD `820c94cdb`).
Binary: imp3 `target/release/examples/memory_bench.exe` (driver 0.1.26,
driver sha256 `1b35913fefd824784df56916be9c56cddd06d79219a278b8ea03e0e3e6c3f29c`,
build fingerprint `cb11214e8a7ec453c1fde29a8d7c20031c01212095cc57199b71aeefb318c476`).
Model: `dgx-glm53/glm-5.3` (only LLM; judge also GLM-5.3 via `official-lme-v1`).
No Jev (preflight-verified isolation). Settings = full iteration-3 stack: distillation +
rerank(200) + query-extract + expansion(2 draws), recall 8000 chars / 8 entries,
`answerMaxTokens 8192`, extraction 32000, chunk 15000, LME protocol `answerInstruction`,
question timeout 240s, seed 20261010.

Manifest: first 5 qids of the 500-question S full set (001be529, 00ca467f, 0100672e,
01493427, 031748ae), 5 envs / 240 sessions / 640 extracted entries, one question per env.
Wall time 11597s (~3.22h): ingestion 11485s (~47.9s/session; 2146-2419s per env), answers 111.5s.

## Score: 2/5 = 0.40 (official-lme-v1 judge, 5 requests, 0 retries/failures)

| qid | category | question (short) | gold | model answer | score |
|---|---|---|---|---|---|
| 001be529 | single-session-user | asylum decision wait | over a year | "over a year" | **1.0** |
| 00ca467f | multi-session | doctor's appointments in March | 2 | "One (1)" (Mar 20 Thompson only) | 0.0 |
| 0100672e | multi-session | spend per coffee mug | $12 | "notes do not record a per-mug price ... works out to about $12 per mug" | **1.0** |
| 01493427 | knowledge-update | new postcards since restart | 25 | "17 new postcards" (as of 2023-08-11) | 0.0 |
| 031748ae | knowledge-update | engineers led then / now | 4 then, 5 now | full abstention | 0.0 |

vs dev5 (`longmemeval_dev5_improved_v2`): 1.00 — but dev5 used a different 5-question set
(2 injected-decoy abstention items + 1 KU + 1 MS + 1 TR). Smoke5 is the first full-stack S
data point on full-set questions; category mix here is 2 KU + 2 MS + 1 SSU, 0 abstention
items. Both KU questions were missed.

Note: `scores.json` `overall.mean` reports 0.004 because the scorer evaluates all 500
qs-dir questions and flags the 495 not in this smoke run as 0; the smoke metric is the
5 answered questions above.

## Recall / answer behavior

- Recall sizes (post-rerank notes / chars vs direct): Q1 1/987 (8/7996 direct — rerank cut
  8 to 1, still correct), Q2 8/6390 (8/7158), Q3 8/5903 (8/5972), Q4 2/1771 (8/6267),
  Q5 8/5228 (8/5228). All recall helpers (distillation, rerank, expansion) fired on all
  5 questions (1 attempt each, no cache hits).
- Protocol answer shape: all answers follow the instruction — date-listing preamble for
  durations/counts, enumerated items, explicit plans-vs-attended distinction (Q2),
  abstention-with-recorded-facts (Q3, Q5). Q3 shows the protocol abstention still passing
  the official judge when the correct value appears as a flagged derivation ("about $12
  per mug — but that is a calculation, not a stated fact").
- Token usage per final answer (in/out/total, latency): Q1 1157/277/1434 7.4s; Q2
  2633/1186/3819 14.5s; Q3 2446/1468/3914 14.6s; Q4 1408/661/2069 9.2s; Q5
  2666/385/3051 64.9s.
- **Blank rescue (Q5 only):** first attempt hit the 8192 output cap with an empty answer
  (stopReason length, 2263 in / 8192 out — reasoning consumed the whole budget). Rescue
  follow-up produced the abstention above (outcome answered, stop). Combined Q5 cost:
  4929 in / 8577 out / 13506 total. Rescue trigger and both attempts' usage are recorded
  in `answer_blank_rescue`.

## Root causes: all 3 misses are memory-content (extraction) failures, not recall failures

1. **00ca467f (gold 2, answered 1):** the March 3 visit ("finally went to see my primary
   care physician, Dr. Smith, on March 3rd ... diagnosed me with bronchitis", first user
   message of evidence session `answer_39900a0a_1`) was never extracted. The import job
   for that session created one note (bronchitis/numbness question) citing m_0011 only;
   zero of the 110 notes mention Dr. Smith / March 3 / primary care. The model correctly
   counted the one attended March appointment on record (Mar 20, Dr. Thompson) and
   correctly excluded the un-scheduled Dr. Patel and cough-doctor plans.
2. **01493427 (gold 25, answered 17):** the 2023-11-30 update ("scored 8 more postcards",
   "I've added 25 new postcards ... since I started collecting again") was never
   extracted. That session's four notes cover the gathering, rack, theme categorization
   and communities — none capture the count update. The memory's newest state is the
   2023-08-11 note (17), which is what the model answered after checking supersession
   ("both notes agree, no conflict"). No 25-note exists, so no recall configuration
   could have answered 25.
3. **031748ae (gold 4 then 5, abstained):** compound failure. (a) The 2023-10-24 update
   ("I now lead a team of five engineers") was never extracted — that session produced
   only the hike and scavenger-hunt notes. (b) The earlier session's main role note
   (`user_new_role_senior_software_engineer`) wrongly says "the exact number of engineers
   ... was never explicitly stated" (extraction cited m_0001/5/9 and missed m_0011 "team
   of 4"), while a second note (`user-role-senior-swe-team-of-4`) does state 4. Both were
   in the attempt-1 recall set. (c) The model still abstained; its rescue answer cites
   the `team-outing-mount-tamalpais-hike` note ("group hike for 6 people"), which is not
   in `recall_ids`, so the rescue turn's own context refresh and/or model reading are
   implicated — `recall_ids` is snapshotted before the rescue (driver code), so the
   rescue turn's effective context is not directly observable from the record.

## Implications for the full 500 run

- The iteration-3 answer protocol behaves as designed (premise checks, plans-vs-attended,
  supersession discipline, honest abstention) and the Q3 pass shows derivations stated
  with caveats still score. But on knowledge-update questions the ceiling is set by
   extraction: updates stated mid-session in long sessions are dropped (3 of 3 misses
  here trace to un-extracted facts, 2 of them later-state updates in sessions whose
  other content was extracted).
- Extraction gaps observed: facts in the first user message (Q2), count updates in a
  later session (Q4), and a mid-conversation role/size update (Q5). All were user-role
  statements carrying the gold fact; none made it into any note.
- Answer-path robustness: one length-cap blank answer in 5 questions (rescued). The
  8192 cap + max thinking can burn the entire budget on one question; rescue worked but
  the rescued answer may be based on a refreshed recall context.
- Cost signal for planning: ~47.9s/session ingestion (sequential envs), answers are cheap
  (7-65s). The 500-env full run at this rate is ~48h single-driver; the 20-shard plan
  (lmesfull) matches that.

## Artifacts

- Run: `<campaign>/reports/continuation/iteration3-inputs/runs/lme_s_full_smoke5_v1/`
  (run.jsonl, env.jsonl, per-env memory stores, sessions).
- Scores: `runs/lme_s_full_smoke5_v1/scores.json` (committed copy:
  `runs/lme_s_full_smoke5_v1-scores-official-lme-v1.json`).
- Log: `runs/lme_s_full_smoke5_v1.log`. Manifest: `manifests/lme_s_full_smoke5_v1.json`.
- Scoring command: `repo-continuation/bench/.venv/Scripts/python.exe -B -W error -m
  bench.eval.evaluate --bench longmemeval --run <run>/run.jsonl --variant improved
  --qs-dir data/common/longmemeval/full --out <run>/scores.json --models-json
  reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json
  --judge-profile official-lme-v1` (cwd campaign root).
