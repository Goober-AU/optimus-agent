# LoCoMo official 300-question run — RESULT (round-4 headline)

Executed 2026-10-15 (user-greenlit GO) per FULL-RUN-PLAN.md 1.1. Answer-only
over the 10 warm clause-set stores; local-F1 official scoring; zero new ingest,
zero judge calls.

## Headline

| metric | value |
|---|---|
| **overall mean F1** | **0.4702** |
| **overall acc@0.5** | **0.49** |
| Wilson 95% | [0.4339, 0.5463] |
| n / flagged | 300 / 1 (conv-49_q144 empty_answer) |
| cats 1-4 (n=233) mean F1 | 0.6055 (acc@0.5 0.6309) |

## Comparison (full 300-q population, same local-F1 protocol)

| run | mean F1 | acc@0.5 | flagged |
|---|---|---|---|
| **THIS RUN (banked: FINAL9-stem + W-camp clauses, det recall 24/24000, answer-only)** | **0.4702** | **0.4900** | 1 |
| iteration-3 gate3 improved-defaults | 0.2148 | 0.1833 | 6 |
| iteration-2 anchor (improved-defaults) | 0.2039 | 0.1667 | - |
| base variant (campaign root scores/locomo_base.json) | 0.2891 | 0.2667 | 0 |

Delta vs best previous full-population number (base 0.2891): +0.181 F1 absolute
(1.63x). Vs the iteration-3 memory config: +0.255 (2.19x).

## Per-conversation table

| conv | n | mean F1 | acc@0.5 |
|---|---|---|---|
| conv-26 | 30 | 0.541 | 0.60 |
| conv-30 | 17 | 0.362 | 0.35 |
| conv-41 | 29 | 0.520 | 0.62 |
| conv-42 | 40 | 0.381 | 0.42 |
| conv-43 | 37 | 0.403 | 0.35 |
| conv-44 | 23 | 0.498 | 0.57 |
| conv-47 | 28 | 0.482 | 0.50 |
| conv-48 | 36 | 0.579 | 0.61 |
| conv-49 | 29 | 0.559 | 0.52 |
| conv-50 | 31 | 0.369 | 0.35 |
| **TOTAL** | **300** | **0.470** | **0.49** |

By category: 1 (single-hop) 0.5192, 2 (temporal) 0.7123, 3 (multi-hop) 0.2801,
4 (open-domain knowledge) 0.6327, 5 (adversarial) 0.0.

## Run provenance

- binary: imp4b-target8k sha 4d2ba13f (validated by writecamp/writefull answers)
- settings: FINAL9-stem importInstructions + det recall 24/24000 + stem
  (bank.json = writecamp HEAD settings; skipIngest=true)
- stores: 10/10 warm clause-set typeB caches (keys re-verified pre-launch)
- canary: conv-26_q1 end-to-end 1.0 (answer + local-F1) before the wave
- driver wall: 2026-10-15 01:17 -> 05:01 (~3.7 h incl. ~2 h cap-gated ramp
  while the fleet held 10/12; each driver 4-30 min)
- artifacts (outside worktrees): C:/Users/openclawuser/loco-official/
  (merged/run.jsonl 300 records, merged/scores-official.json, per-conv runs,
  logs/, progress.json, LAUNCHES.json via launch-log.jsonl)
- 1 auto-retry was consumed by the c30 manifest-path miss at 01:16 (fixed,
  relaunched 01:17); all 10 runs then completed rc=0 with full record counts.
