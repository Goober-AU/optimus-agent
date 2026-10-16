# OCC-2 live eval run b — live-dev-sym-v2-d2-r1 (dev split, DGX GLM 5.3)

VERDICT: ALL GATES GREEN, PERFECT SCORES. Run id live-dev-sym-v2-d2-r1-scripted-20261016T162253Z.

## Scores (driver score stage, 60 items)
| gate | result |
|---|---|
| QA accuracy | 180/180 (1.0; incl-protocol 1.0) |
| speaker fidelity (strict fixed-point) | 60/60 (1.0) |
| listener comprehension (exact rebuild) | 60/60 (1.0) |
| canaries | 12/12, drift FALSE |
| protocol faults | speaker 0, listener 0, child_lost 0 |
| gates | GREEN (22 checks, 0 failures) |

## Tokens (qwen2.5-0.5b-instruct-local counter)
- wire mean 88.57 tok/item vs OCC-1 144.17 -> **0.6143x** (−38.6%)
- chars 195.55 vs 327.38
- codebook 3466 ch / 1187 tok; amortized 118.7 tok/item @ batch 10; break-even ≈21.5 items/injection
- session-profile amortization remains the deployment recommendation (once-per-session injection)

## Provenance and fixes (all driver/harness-side; model output never edited)
1. run a (live-dev-sym-v2) exposed card ambiguity (3 classes) -> 0/60 strict; card clarified (CARD-CLARIFY.md).
2. listener pass 1 exposed undefined payload schema -> spec added to prompt templates.
3. listener pass 2 exposed my spec key error ("lines" vs line_range) -> spec v2.
Each fix was re-verified by the driver self-test (178/178) before the corresponding re-run.
Evidence archived: replies/speaker.jsonl (60), replies/listener.jsonl (60),
listener_ambiguous-schema.jsonl, listener_v1-lines-spec.jsonl, run a artifacts untouched.

## Matrix
- C1 (glm-5.3 -> glm-5.3): CLOSED.
- X1-X4 (heterogeneous pairs): deferred under DGX-only constraint, labeled.

## Records
- DialectEvaluationRecord: sym-v2-d2-r1@1, samples 60, listenerFidelityMean 1.0, wireTokensVsOcc1 0.6143
  (research/occ2opt_eval/occ2-evals-run-b.json, schema optimus.occ2-evals.v1).
- Holdout remains sealed (opens only after all tuning frozen).
