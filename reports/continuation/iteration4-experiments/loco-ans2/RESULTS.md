# loco-ans2: answer-stage residuals on the fresh LoCoMo full store (q149 combine-dilution, q144 phrasing wobble)

Worktree: `imp4-locoans2` (branch `improve/mem4-loco-ans2`, base 0cbad1d32 = improve/memory-iteration-4).
Store: fresh typeB cache `d4b542a8e98deca6` (176 entries, KU + attribute v2 importInstructions),
env conv-26 from `locomo-gate3-improved-s0.json`; warm cache-reuse copy-out (skipIngest) — the store is never re-ingested or mutated.
Model: dgx-glm53/glm-5.3 (only). Driver: `imp4-targetR` (sha256 c04ab631...). Controller:
`imp4/scripts/memory_question_loop.py --mode typeB` (attempts under `reports/continuation/iteration4/ql-attempts-locoans2/`, ledger auto-appends).
Recall: det extract-only, 24/24000 — window unchanged (w32 is AA's lane). All numbers are official locomo_token_f1, x3 draws (medians) unless noted.

## Final tightened instruction (FINAL2.json; gate instruction + 2 clause edits)

1. h7-combine clause replaced (combine-dilution fix):

> When several notes each state part of what a person or group gives, means, or is to someone, answer from the
> most recent note that directly states what that person or group gives, means, or is to them, and name every
> attribute that note states they give or are to them, each in one or two words (the quality or thing itself,
> no verbs or longer phrases).

2. how-question clause appended (phrasing-wobble fix):

> ... naming how they felt and how it turned out.; always name both the feeling and the outcome, never the outcome alone.

## q149 "What do Melanie's family give her?" (gold "Strength and motivation"; bar >= 0.8)

| config | median | draws | answers |
|---|---|---|---|
| gate h7-combine (baseline) | 0.571 | .571/.571/.571 | "love, support, strength, motivation, joy" x3 (5-attribute dilution) |
| H1 question-scoped combine | 0.571 | .571/.571/.571 | union unchanged |
| H2 arity cap (2-3 attrs) | 0.400 | .4/.4/.4 | "love, support, motivation" (wrong trio) |
| H3 verb-scope | 0.500 | .5/.5/.667 | "love, strength" x2, "strength" |
| **H4a recency single-note (worker fix)** | **0.800** | .667/.8/.8 | "strength, motivation, support" family |
| H4a rerun (variance control) | 0.800 | .8/.8/.8 | "strength, motivation, support" x3 |
| H4b verb+recency | 0.500 | .5/.5/.5 | "love, strength" x3 |
| H4c single most-direct note | 0.000 | .0/.0/.8 | "love and support" x2 (wrong note) |
| FINAL = h4a+h2b | 0.500 | .333/.5/.5 | h2b's "name both ..." phrasing leaks: closing-phrase subset |
| FINAL3 = every-attribute(give,mean,are)+h2b | 0.667 | .571/.667/.667 | "the world" enters via "mean" |
| **FINAL2 = every-attribute(give,are)+h2b (SHIP)** | **0.800** | **.5/.8/.8** | "strength, motivation, support" x2, "motivation, support" |

The winning mechanism is recency-scoped single-note combine: the gold statements (D18:9 "give her the strength
to keep going" + D18:11 "her biggest motivation and support") live in ONE note (imported_d8298...); union-combining
across the 5 other family-attribute notes (love/support/joy) dilutes F1 to 0.571. H4a alone is 0.8-median stable
(6 draws: .667/.8/.8/.8/.8/.8) but the h2b how-clause's "name both the feeling and the outcome" wording leaks
into the attribute clause (2-item answers); the "every attribute" enumeration (H5B) counters the leak and
restores the 0.8 median. "give or are" (not "give, mean, or are") keeps the "mean the world to her" metaphor out.

## q144 "How did Melanie's son handle the accident?" (gold "He was scared but reassured by his family"; bar >= 0.5 stable)

| config | median | draws | answers |
|---|---|---|---|
| gate baseline | 0.333 | .333/.5/.333 | "He was thankfully okay." etc. (feeling dropped) |
| H1 old-G4 control on fresh store | 0.333 | .333/.533/.333 | same wobble with the frozen-passing instruction |
| H2 feeling-first template | 0.364 | .364/.333/.571 | "He was okay." (feeling still dropped 2/3) |
| **H2b never-outcome-alone (worker fix)** | **0.533** | .533/.533/.571 | "He was scared but ended up okay." etc. |
| FINAL = h4a+h2b | 0.533 | .375/.533/.571 | "It was scary..." (It/scary drift 1/3) |
| **FINAL2 (5 draws)** | **0.571** | .0/.571/.571/.615/.571 | 4/5 pass; one 'not mentioned' abstention |

H1 control confirms the wobble is store-extraction phrasing, not config: the exact frozen-store-passing G4
instruction (0.533 x3 on frozen) wobbles identically on the fresh store. The fresh note attributes fear to the
whole family ("The whole family was freaked out and really scared, but thankfully her son was okay"), so the
note-faithful son answer needs the feeling forced in: h2b's "always name both the feeling and the outcome,
never the outcome alone" does that (note-faithful ceiling 'He was scared but okay' = 0.6154).
A "no-both" rewording (FINAL4: "a reply that names only the outcome is incomplete") loses the feeling again
(0.375 median) — the both/never-alone phrasing is load-bearing.
FINAL4 also verified: q149 0.8 median but q144 0.375 -> eliminated.

## Regression on FINAL2 (fresh store; x3 draws, q33 n=6)

| qid | bar | median | draws | verdict |
|---|---|---|---|---|
| q1 | 1.0 | 1.0 | 1/1/1 | hold |
| q54 | 1.0 | 1.0 | 1/1/1 | hold |
| q126 | 1.0 | 1.0 | 1/1/1 | hold |
| q135 | 1.0 | 1.0 | 1/1/1 | hold |
| q143 | 1.0 | 1.0 | 1/1/1 | hold |
| q141 | >= 0.5 | 0.556 | .556/.556/.769 | hold |
| q26 | fresh win 1.0 | 1.0 | 1/1/0 | hold (one 'not mentioned' abstention draw; pre-existing class: 4/9 historical q26 attempts abstained on frozen too) |
| q33 | fresh win 0.667 | 0.667 | 0/0/.667/.667/1.0/.667 | hold (abstention wobble 2/6; passing draws list both summer parades) |
| q55 | record only (w24) | 1.0 | 0/1/1 | within its 0.0/1.0 wobble envelope; w32 remains AA's lane |

## Verdict

- q149 bar (>= 0.8): MET — FINAL2 median 0.8 (0.5/0.8/0.8); h4a mechanism alone 0.8 median over 6 draws.
- q144 bar (>= 0.5 stable): MET — FINAL2 median 0.5714 (5 draws: 4 pass, 1 abstention); feeling present in every non-abstaining draw.
- Regression: all bars hold (q1/q54/q126/q135/q143 = 1.0; q141 0.556; q26 1.0; q33 0.667); q55 recorded at w24 (1.0 median this batch, wobble unchanged).
- Aggregate win vs gate baseline: q149 +0.229 (0.571 -> 0.8), q144 +0.238 (0.333 -> 0.571).

Wobble classes recorded honestly: q26/q33/q55 abstention draws are recall-adjacent answer-stage variance
(pride-parade/book/sunset notes sit near the 24-entry window edge); they predate this change (time/when clauses
are verbatim from the gate instruction) and the medians hold at n>=3.
