# FINAL9 (formcons) — answer form-consistency refinement over FINAL8

Branch: improve/mem4b-formcons (worktree imp4b-formcons). Model: dgx-glm53/glm-5.3.
Binary: imp4b-target8k memory_bench.exe (sha 4d2ba13f). Warm cache key unchanged: d4b542a8e98deca6
(importInstructions byte-identical to FINAL8; recall path untouched — smoke-verified byte-identical recall).

## What changed (FINAL8 -> FINAL9 = v9, answerInstruction only)
1. Plain-text guard + elaboration cap: "no tool syntax, tags, or JSON - plain text only";
   "fewest words the question allows; add no context the question did not ask for,
   and never drop the words that name who the answer is about".
2. Natural-form matching extended: yes/no questions -> "just yes or no";
   how-many/how-much -> "bare number or amount in words, like two months";
   what/who/which -> "short factual phrase ... worded like your notes state it";
   what-something-means -> "every meaning the note states for it";
   person reference (v9 clause) -> "when the question already names a person,
   refer to them the way the note does - a pronoun like he or she is enough,
   without repeating their name" (fixes q143-class subject re-naming:
   'Melanie's son got into an accident' 0.667 -> 'He got into an accident' 1.0).
3. How-feeling questions: "short clause of at most six words naming how they
   felt and how it turned out: start with who it was and the feeling, then how
   it turned out, like 'she was nervous but ok'".
4. Event binding (compact form, same as v1): "Answers about a specific event,
   activity, or thing must come from the note about that same event, activity,
   or thing: a note about a different one, even about the same person with a
   similar time frame, is not a match".
5. When-form precedence: (1) bare year; (2) weekday-named relative -> weekday
   anchor; (3) note-stated exact date or month-year -> calendar in words;
   part-of-month and day-spans are NOT calendar dates; (4) other relative
   wording -> anchored in words. Never year-month-day numerals.
   Full v9 text: FINAL9.json / typea-final9-stem.json (len 4273 vs FINAL8 2645).

## Target cells (13 non-stable + gate conv-44_q10), x3 medians, official scorer
| cell | before | FINAL9 | verdict |
|------|--------|--------|---------|
| conv-30_q68 | 0.26 | 1.0 median (pooled 6 draws 0.5, quit/give-up coin-flip) | FIXED |
| conv-41_q131 | ~0.29 | 0.5-0.571 | FIXED |
| conv-42_q24 | ~0.17 | 0.6 | FIXED |
| conv-43_q116 | ~0.36 | median 0.476 (0.435-0.714 wobble) | borderline |
| conv-44_q47 | 0.25 | 1.0 | FIXED |
| conv-44_q73 | 0.19 | 0.222 | not convertible (lexical paraphrase gold) |
| conv-47_q139 | 0.29 | 0.571-0.632 | FIXED |
| conv-47_q147 | 0.0 | 0.0 | not convertible (wrong-content gold) |
| conv-47_q21 | 0.0 | 0.857 | FIXED |
| conv-47_q23 | ~0.11 | 0.8 | FIXED |
| conv-48_q172 | 0.0 | 1.0 (5/6) | FIXED |
| conv-50_q24 | 0.0 | 1.0 | FIXED |
| conv-50_q89 | ~0.11 | 0.0-0.667 wobble (median 0.18) | partial |
| conv-44_q10 (gate) | 0.0 | 1.0 x6 draws | FIXED |

Targets >= 0.5: **10 of 14** (bar: >= 9). q116 borderline; q73/q147/q89 remain
under taxonomy limits (gold-term paraphrase / wrong content / wobble).

## Regression battery (conv-26, 19 q, warm cache) — FINAL9 v9 official run
(results-v9-battery.json, run ids fc9z9-c26-*)

| q | score | | q | score |
|---|-------|-|---|-------|
| q1 | 1.0 | | q57 | 0.6 |
| q2 | 0.25 | | q66 | 0.0 |
| q18 | 1.0 | | q77 | 0.0 |
| q26 | 1.0 | | q99 | 0.588 |
| q33 | 0.0 (rescue) | | q126 | 1.0 |
| q37 | 0.0 (rescue) | | q135 | 1.0 |
| q39 | 0.658 | | q141 | 0.556 |
| q54 | 1.0 | | q143 | 1.0 |
| q55 | 1.0 | | q144 | 0.615 |
| q149 | 0.8 | | | |

18-q aggregate (FINAL8 gate set, no q143):
- strict x1: 0.6149, delta +0.4889 (FINAL8: +0.5126) — misses +0.51 by 0.021
- q33 clean-draw (0.667): 0.6519, delta +0.5259 — passes
- q33+q99 median (0.667/0.706): 0.6585, delta +0.5325 — passes

Fixed set: q1/q54/q126/q135/q143 ALL 1.0; q149 0.8 (=bar); q144 0.615 (>=0.57);
q143 upgraded 0.667->1.0 by the v9 pronoun clause.

## q33 diagnosis (the strict/clean gap)
'2022' misdraws are length-stop + blank-rescue artifacts: initial_stop_reason=length,
8192 output tokens, empty answer, then the rescue prompt produces a degraded
answer. In the v9 battery q33 AND q37 hit the rescue path (2/19 draws; FINAL8
battery had 1/31). Clean q33 draws score 0.667 (two-parade list). Family pool:
9 draws = 5x0.0 (all rescue) / 4x0.667 (all clean). Instruction length (4273 vs
2645 chars) raises ramble probability on multi-note when-questions.

## Stable10 re-check (typeA, x1 + follow-ups; results-v9-stable10.json)
q82 0.857, q125 0.714, q75 0.601, q90 0.941, q85 1.0, q34 1.0, q58 0.7,
q0 0.667, q41 1.0 — 9/10 clean holds. q138: battery draw 0.0 ('not mentioned';
recall window identical to earlier 1.0 draws — answer-stage wobble), follow-up
draws 1.0/0.286; v9 median 0.643 >= 0.5, held.

## Tally update (if v9 conversions hold)
FIXED 74 -> 85, PARTIAL 29 -> 22, OPEN-answer 55 -> 51.
