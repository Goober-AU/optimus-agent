# LP dev5 fresh re-ingest campaign — results

Worker: glm-lpcamp. Branch `improve/mem4b-lp-camp`. Binary imp4-targetR (sha b57dc607).
Model dgx-glm53/glm-5.3 only. Judge official-lp-v1, LP_JUDGE_MAX_TOKENS=4096, question-scoped.

## Per-store ingest stats

| qid | entries | cue coverage | gold entries | verify |
| --- | --- | --- | --- | --- |
| lp_232 | 1084 | 99.3% | 3 | OK |
| lp_118 | 776 | 99.1% | 4 | OK |
| lp_18 | 713 | 98.9% | 1 | OK |
| lp_159 | 1102 | 98.9% | 1 | OK |
| lp_69 | 1026 | 99.3% | 3 | OK |

Notes: lp_232 stats from the repair-run verify (run lpcamp-rep232-1x1); lp_159/lp_69 ingested by
glm-writecamp into the shared cache-root (run-ids lpcamp-ing-l159 / lpcamp-ing-l69), entry counts
re-verified from the stored harness_state; lp_118/lp_18 from my ingest-queue verify.

## dev5 table (fresh E3 vs frozen E3 vs baseline)

| qid | v3 baseline | frozen E3 | fresh E3 x2 | pair-pass |
| --- | --- | --- | --- | --- |
| lp_232 | 1.0 | 1.0 (median) | 1.0 / 1.0 / 1.0 | True |
| lp_118 | 0.0 | 1.0 (median) | 1.0 / 0.0 | True |
| lp_18 | 1.0 | 1.0 (median) | 1.0 / 1.0 | True |
| lp_159 | 1.0 | 1.0 (median) | 0.0 / 1.0 | True |
| lp_69 | 1.0 | 1.0 (median) | 1.0 / 1.0 | True |

## Flake exposure

- fresh: 4/12 runs rerank-invalid (2 still scored 1.0; 0 recalled gold via lexical fallback).
  Flaked runs: lp232-r1 (1.0), lp118-r2 (0.0), lp159-r1 (0.0), lp159-r2 (1.0).
- frozen (CC, 40 runs): 8/40 (5 still 1.0; 3 scored 0.0)

## Verdict

- dev5 pair-pass: 5/5 (target 5/5).
- flake exposure fresh: 4/12 rerank-invalid runs; 0 of those still recalled gold via lexical fallback.
- frozen reference (CC): 8/40 rerank-invalid; 5/8 still 1.0 — see report.md table for the durable-verdict comparison.
