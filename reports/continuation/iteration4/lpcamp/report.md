# LP dev5 fresh re-ingest campaign — results

Worker: glm-lpcamp. Branch `improve/mem4b-lp-camp`. Binary imp4-targetR (sha b57dc607).
Model dgx-glm53/glm-5.3 only. Judge official-lp-v1, LP_JUDGE_MAX_TOKENS=4096, question-scoped.

## Per-store ingest stats

| qid | entries | cue coverage | gold entries | verify |
| --- | --- | --- | --- | --- |
| lp_232 | ? | ?% | ? | FAIL |
| lp_118 | 776 | 99.1% | 4 | OK |
| lp_18 | 713 | 98.9% | 1 | OK |
| lp_159 | ? | ?% | ? | FAIL |
| lp_69 | ? | ?% | ? | FAIL |

## dev5 table (fresh E3 vs frozen E3 vs baseline)

| qid | v3 baseline | frozen E3 | fresh E3 x2 | pair-pass |
| --- | --- | --- | --- | --- |
| lp_232 | 1.0 | 1.0 (median) | 1.0 / 1.0 / 1.0 | True |
| lp_118 | 0.0 | 1.0 (median) | 1.0 / 0.0 | True |
| lp_18 | 1.0 | 1.0 (median) | 1.0 / 1.0 | True |
| lp_159 | 1.0 | 1.0 (median) | 0.0 / 1.0 | True |
| lp_69 | 1.0 | 1.0 (median) | 1.0 / 1.0 | True |

## Flake exposure

- fresh: 4/12 runs rerank-invalid (2 still scored 1.0; 0 recalled gold via lexical fallback)
- frozen (CC, 40 runs): 8/40 (5 still 1.0; 3 scored 0.0)

## Verdict

- dev5 pair-pass: 5/5 (target 5/5).
- flake exposure fresh: 4/12 rerank-invalid runs; 0 of those still recalled gold via lexical fallback.
- frozen reference (CC): 8/40 rerank-invalid; 5/8 still 1.0 — see report.md table for the durable-verdict comparison.
