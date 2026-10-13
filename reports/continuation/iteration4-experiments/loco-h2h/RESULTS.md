# LoCoMo conv-26 config head-to-head (regression gate)

Date: 2026-10-13 | worker: locoh2h | model: dgx-glm53/glm-5.3 (only)
Store: frozen conv-26 (imp3-gate improved-s0, 119 memory entries), TYPE A copy-out, question-scoped local-F1.
Configs: FLEET = loco-q2 `h3-precedence-timeform.json` (extract+distill+rerank(200)+expansion ON, short-form+time-precedence answer).
DETERMINISTIC = loco-q5 `h3b-deterministic-recall.json` (extract only, window 24 entries / 24000 chars, event-tolerant answer).

## Head-to-head table (local-F1)

| q | cat | baseline | fleet | det |
|----|----|---------:|------:|----:|
| q2 | 3/open-domain | 0.000 | 0.333 | 0.250 |
| q18 | 1/multi-hop | 0.269 | 0.000 | 0.444 |
| q26 | 2/temporal | 0.061 | 0.000 | 0.000 |
| q33 | 2/temporal | 0.068 | **0.667** | 0.000 |
| q37 | 1/multi-hop | 0.000 | 0.400 | 0.000 |
| q39 | 1/multi-hop | 0.109 | 0.183 | 0.450 |
| q55 | 1/multi-hop | 0.000 | **1.000** | **1.000** |
| q57 | 2/temporal | 0.056 | 0.000 | **0.600** |
| q66 | 1/multi-hop | 0.000 | 0.000 | 0.000 |
| q77 | 3/open-domain | 0.000 | 0.000 | 0.000 |
| q99 | 4/single-hop | 0.257 | **0.526** | 0.412 |
| q144 | 4/single-hop | 0.293 | 0.000 | 0.364 |
| q149 | 4/single-hop | 0.074 | 0.000 | 0.000 |
| q1 | 2/temporal (fixed) | 0.000 | 1.000 | 1.000 |
| q54 | 2/temporal (fixed) | 0.182 | 1.000 | 1.000 |
| q126 | 4/single-hop (fixed) | 0.210 | 1.000 | 1.000 |
| q135 | 4/single-hop (fixed) | 0.316 | **0.000 REGRESSION** | 0.556 |
| q141 | 4/single-hop (fixed) | 0.375 | 0.500 | 0.526 |

(q143 excluded: owned by glm-locoq6 live loop.)

## Computed

- Fixed (>=0.5), comparison set n=13: fleet 3 (q33, q55, q99); det 2 (q55, q57); both 1 (q55); fleet-only q33+q99; det-only q57.
- No-regression set n=5: fleet 1 regression (q135 1.0 -> 0.0); det 0 regressions (q135 0.556, still >=0.5; loco-q5 single-shot was 1.0 — answer-model variance).
- Mean F1 delta vs baseline: comparison fleet +0.148, det +0.180; all-18 fleet +0.241, det +0.296.
- Helper flakiness (18 fleet runs, from llm_helper_observations): distillation empty_output 17/18 (94%), expansion empty 8/18, rerank used 15/18 + invalid 1 + empty 2. Det: all helpers disabled 18/18.
- Cost per answer (means): fleet recall 2362 chars / 3.0 ids, 1617 tok-in, 636 tok-out, 28.6 s. Det recall 16818 chars / 24.0 ids, 5464 tok-in, 240 tok-out, 20.1 s. Fleet rerank alone costs ~4.4k tokens per used call.

## q135 regression mechanism (fleet)

Fleet rerank (title-blind) compressed recall to 1 entry `melanie_son_accident_family_roadtrip_october_2023` and dropped the gold `melanie_injury_pottery_break_sept_2023`; answer "A car accident involving her son" scored 0.0. Det's 24-entry window kept the gold; answer scored 0.556-1.0.

## VERDICT

DETERMINISTIC (loco-q5 h3b) is the LoCoMo promotion candidate. Zero regressions, higher mean F1 delta, zero LLM-helper flake in the recall path, faster and reproducible. Fleet's 2 extra fixes (q33, q99) sit on a helper stack that is empty 94% (distill) / 44% (expansion) of the time. Hybrid: keep deterministic recall + wide window (24/24000) as base; add rerank only as a content-aware variant (N's improve/mem4-content-rerank) and only with a raw-extract fallback on empty/invalid output. The wide window is safe at conv-26 scale (119 entries): mean 16.8k recall chars, input 5.5k tokens, latency still lower than fleet's.

Artifacts: configs + `h2h-results.json` in this dir; 36 attempts under `reports/continuation/iteration4/ql-attempts-locoh2h` (run_id `h2h-<tag>-<q>`, worker `locoh2h`); ledger attempts appended to `QUESTION-LEDGER.json`.
