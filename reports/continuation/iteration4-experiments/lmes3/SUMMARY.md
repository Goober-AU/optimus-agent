# LME-S batch-3 (lmesq3b) — disjoint slice, official order idx 25-29

Worker: lmesq3b. Branch: improve/mem4b-lmes3 (fresh from imp4 HEAD 85c63983a).
Binary: imp4b-target8k sha 4d2ba13f (verified at each launch).
Config: banked smoke5 + ov-banked (KU + A/G4 + H1 + H2b) — unchanged from BB's
validated set; typeB ingest; captureProtocol forced
(optimus-memory-capture/temporal-authority/1.0.0); judge official-lme-v1,
question-scoped. Attempts root C:/Users/openclawuser/lmesq3-att (short, MAX_PATH-safe).

## Results (sequential, stop-at-first-failure; first failure = last question)

| qid | category | outcome | score |
|---|---|---|---|
| 0db4c65d | temporal-reasoning | PASS | 1.0 |
| 0ddfec37 | knowledge-update | PASS | 1.0 |
| 0ddfec37_abs | knowledge-update (abstention twin) | PASS | 1.0 |
| 0e4e4c46 | knowledge-update (current-state) | PASS | 1.0 |
| 0e5e2d1a | single-session-assistant | FAIL | 0.0 |

4/5 pass. No code or settings changes were made; no regression gates needed.

## 0e5e2d1a diagnosis (failure_stage=answer; root cause=write-side)

Question: "how many subjects were in that study?" (binaural beats for
anxiety/depression). Gold: 38 subjects (Music and Medicine study).

- Write side: the ONLY binaural note written is
  user_binaural_beats_anxiety_depression_interest_2023_05_26 — an interest
  summary without study details. The 38-subjects fact exists only in the
  ASSISTANT's reply in source session answer_ultrachat_113156 (three studies
  listed; #3 = Music and Medicine, 38 subjects, 30 min daily, three weeks).
  importInstructions exclude "unsupported assistant claims" and extract
  "durable host-neutral project facts" — the study details were dropped at
  ingest (stage B).
- Recall: correct — the interest note ranked 1/1 binaural notes (only one
  exists); recall is not the bottleneck.
- Answer: model abstained exactly per H1/G4 rules ("no note about a study
  published in Music and Medicine exists"). Abstention is CORRECT given the
  store content — the fact is genuinely absent.

### Hypotheses (per task spec; writecamp owns fresh re-ingests)

1. Read-time (G4-family answerInstruction variants / det wide window): CANNOT
   fix — no read-time lever recovers a fact that was never written.
2. Write-time (KU-family clause): allow assistant-stated study facts when
   they directly answer a user question in-session ("when the user asks about
   studies/research, record each named study with journal, subject count and
   duration"). Tension: contradicts the deliberate "unsupported assistant
   claims" exclusion; LongMemEval's single-session-assistant category tests
   exactly such facts (assistant replies as memory source).
3. Write-time (narrower): extend the existing dated-possession title-naming
   clause to named-study facts (journal + count in title) so later
   count/size questions can find them lexically.

Recommendation: route to writecamp (fresh re-ingest lane) — hypothesis 2 or 3;
single-session-assistant questions will keep failing otherwise.

## Ops notes

- Stagger rule respected: first driver gated to 12:31:00Z (writefull driver
  started 12:23:26Z).
- One transient sse_idle_timeout on 0ddfec37 ingest (13:33Z); driver retried
  internally, PASS — same pattern seen in BB's passing run 078150f1.
- Chain wall-clock 12:28-16:48Z. Per-question: 61.1 / 51.3 / 46.8 / 49.5 / 51.3 min.
- Ledger auto-appends under worker=lmesq3b (all 5 recorded).
