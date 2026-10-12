# LME-v2 dev5 improved v3 (premise-channel answerInstruction) — score comparison

- Run: `lme_v2_dev5_improved_v3` (iteration-3 inputs, env web, 100 sessions, 5 questions)
- Settings: expansion+extract+distill+rerank ON, pool 200, budget 12000 chars / 12 entries, premise-channel answerInstruction, tools off
- Model: dgx-glm53/glm-5.3, no Jev
- Overall: **0.40** vs iteration-2 v2 baseline **0.40** (delta +0.00)
- Procedure: 1.00 (2 q) vs baseline 1.00; static-environment: 0.00 (3 q) vs baseline 0.00

## Per-question (gold vs v2 answer vs v3 answer)

| qid | category | gold | v2 | v3 | v2 ans | v3 ans | recall ch/ent | helper outcomes |
|---|---|---|---|---|---|---|---|---|
| f928c075 | static-environment | moderator | 0.0 | 0.0 | \boxed{Account} | \boxed{UNKNOWN} | 11996/12 | recallQueryDistillation=empty_output/length/0ch; recallRerank=empty_output/length/0ch; recallQueryExpansion=empty_output/length/0ch |
| 99b6e22f | procedure | B | 1.0 | 1.0 | \boxed{B} | Comparing the two flows from the Orders page with the target | 11996/10 | recallQueryDistillation=empty_output/length/0ch; recallRerank=empty_output/length/0ch; recallQueryExpansion=empty_output/length/0ch |
| 36fe48ef | static-environment | six | 0.0 | 0.0 | My provided context and persistent memor | I need to find evidence about a "Toolbox" section on a moder | 11996/11 | recallQueryDistillation=empty_output/length/0ch; recallRerank=empty_output/length/0ch; recallQueryExpansion=empty_output/length/0ch |
| 499488a6 | static-environment | none | 0.0 | 0.0 |  | I'll check the workspace for trajectory evidence about the ` | 11996/12 | recallQueryDistillation=empty_output/length/0ch; recallRerank=empty_output/length/0ch; recallQueryExpansion=used/stop/121ch |
| a59d50f7 | procedure | three, Proceed to Checkout, Next, Place Order | 1.0 | 1.0 | \boxed{three, Proceed to Checkout, Next, | \boxed{three, Proceed to Checkout, Next, Place Order}  (Save | 11996/10 | recallQueryDistillation=empty_output/length/0ch; recallRerank=empty_output/length/0ch; recallQueryExpansion=used/stop/152ch |

## Verdict on the premise-channel instruction

- No static questions converted.
- Still wrong: `f928c075` gold `moderator`, v3 `UNKNOWN`
- Still wrong: `36fe48ef` gold `six`, v3 `I need to find evidence about a "Toolbox" section on a moderated forum page in the Postmill forum. Let me check the working directory for trajectory data.`
- Still wrong: `499488a6` gold `none`, v3 `I'll check the workspace for trajectory evidence about the `Hide this forum` feature, since the memory notes don't mention it directly.`

- Procedure questions: `99b6e22f` PASS, `a59d50f7` PASS

## Run log tail (verbatim)

```
[q 3/5] 99b6e22f asking (596 chars)
[q 3/5] 99b6e22f done in 15391ms
[q 4/5] a59d50f7 asking (328 chars)
[q 4/5] a59d50f7 done in 20189ms
[q 5/5] f928c075 asking (290 chars)
{"provider":"dgx-glm53","model":"glm-5.3","api":"openai-completions","stopReason":"length","inputTokens":4139.0,"outputTokens":8192.0,"totalTokens":12331.0,"attempt":1,"retryEnabled":false,"ts":"2026-10-12T19:16:15.482Z","level":"warn","component":"agent-core.stream-watchdog","msg":"assistant response ended at the provider output cap"}
[q 5/5] f928c075 done in 81884ms
[run lme_v2_dev5_improved_v3] complete
```
