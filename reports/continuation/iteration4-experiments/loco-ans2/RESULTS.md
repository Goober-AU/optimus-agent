# loco-ans2: answer-stage residuals on the fresh LoCoMo full store (q149 combine-dilution, q144 phrasing wobble)

Worktree: `imp4b-locoans2` (branch `improve/mem4b-loco-ans2`, base ad4309cf1 = imp4 HEAD post-merge,
contains 3f937a2ed). Pre-pause battery ran in `imp4-locoans2` (branch `improve/mem4-loco-ans2`, base 0cbad1d32,
pre-merge binary sha256 c04ab631...); this restart re-verified and finalized on the post-merge fleet binary.
Store: fresh typeB cache `d4b542a8e98deca6` (176 entries, KU + attribute v2 importInstructions),
env conv-26 from `locomo-gate3-improved-s0.json`; warm cache-reuse copy-out (skipIngest) — the store is never
re-ingested or mutated. Model: dgx-glm53/glm-5.3 (only). Driver: `imp4-targetR` (sha256 b57dc607..., post-merge,
stemming settings unchanged). Controller: `imp4/scripts/memory_question_loop.py --mode typeB`
(attempts under `reports/continuation/iteration4/ql-attempts-locoans2/`, campaign-root ledger auto-appends;
worker `locoans2` = pre-pause battery, `locoans2b` = post-restart battery). Recall: det extract-only, 24/24000,
identical recall window across both binaries (recall_ids_count 24, recall_chars 13761 on every q149 draw).
All numbers are official locomo_token_f1. Runners: `run-ans2.py` (pre-pause), `run-verify-b57.py` (restart).

## SHIP instruction (FINAL7.json; gate instruction + 2 clause edits)

1. h7-combine clause replaced (combine-dilution fix, recency single-note):

> When several notes each state part of what a person or group gives, means, or is to someone, answer from the
> most recent note that directly states what that person or group gives, means, or is to them, combining the
> parts stated in that note and naming each attribute in one or two words (the quality or thing itself, no
> verbs or longer phrases); a metaphor for how much someone matters is not an attribute.

2. how-question clause appended (phrasing-wobble fix):

> ... naming how they felt and how it turned out.: state the feeling first, then the outcome, and never reply
> with the outcome alone.

## q149 "What do Melanie's family give her?" (gold "Strength and motivation"; bar >= 0.8)

| config | binary | median | draws | answers |
|---|---|---|---|---|
| gate h7-combine (baseline) | c04ab | 0.571 | .571/.571/.571 | "love, support, strength, motivation, joy" x3 (5-attribute dilution) |
| H1 question-scoped combine | c04ab | 0.571 | .571/.571/.571 | union unchanged |
| H2 arity cap (2-3 attrs) | c04ab | 0.400 | .4/.4/.4 | "love, support, motivation" (wrong trio) |
| H3 verb-scope | c04ab | 0.500 | .5/.5/.667 | "love, strength" x2, "strength" |
| H4a recency single-note | c04ab | 0.800 | .667/.8/.8 | "strength, motivation, support" family |
| H4a rerun (variance control) | c04ab | 0.800 | .8/.8/.8 | "strength, motivation, support" x3 |
| H4b verb+recency | c04ab | 0.500 | .5/.5/.5 | "love, strength" x3 |
| H4c single most-direct note | c04ab | 0.000 | .0/.0/.8 | "love and support" x2 (wrong note) |
| FINAL = h4a+h2b | c04ab | 0.500 | .333/.5/.5 | h2b's "name both ..." phrasing leaks: 2-item answers |
| FINAL3 = every-attribute(give,mean,are)+h2b | c04ab | 0.667 | .571/.667/.667 | "the world" enters via "mean" |
| FINAL2 = every-attribute(give,are)+h2b | c04ab | 0.800 | .5/.8/.8 | "strength, motivation, support" x2, "motivation, support" |
| FINAL2 re-verified | b57dc | 0.667 | .667/.4/.8/.667/.8/.5 | "the world" leak 3/6 (note: "they mean the world to her" is IN the gold note) |
| FINAL5 = F2+metaphor-excl(example) | b57dc | 0.500 | .8/.5/.5 | world gone, but under-enumeration ("motivation, support" x2) |
| FINAL6 = h4a-combine+feeling-first how | b57dc | 0.733 | .8/.8/.667 + .667/.8/.667 | world-leak 3/6, no under-enumeration |
| **FINAL7 = h4a+metaphor-excl+feeling-first (SHIP)** | **b57dc** | **0.800** | **.8/.8/.8/.5/.8/.8** | "strength, motivation, support" x5, "motivation, support" x1 |

The winning mechanism is recency-scoped single-note combine: the gold statements (D18:9 "give her the strength
to keep going" + D18:11 "her biggest motivation and support") live in ONE note (imported_d8298...); union-combining
across the 5 other family-attribute notes (love/support/joy) dilutes F1 to 0.571. The b57dc re-verification exposed
one residual failure mode FINAL2 missed at n=3: the gold note itself states "they mean the world to her", so
every-attribute enumeration admits "the world" (~50% of draws). FINAL7 keeps h4a's "combining the parts" latitude
(no 2-item leak) and adds a general metaphor exclusion (no gold terms, no examples) — world-leak 0/6, the only
residual wobble is one under-enumeration draw (.5, "motivation, support"), median 0.8 at n=6.

## q144 "How did Melanie's son handle the accident?" (gold "He was scared but reassured by his family"; bar >= 0.5 stable)

| config | binary | median | draws | answers |
|---|---|---|---|---|
| gate baseline | c04ab | 0.333 | .333/.5/.333 | "He was thankfully okay." etc. (feeling dropped) |
| H1 old-G4 control on fresh store | c04ab | 0.333 | .333/.533/.333 | same wobble with the frozen-passing instruction |
| H2 feeling-first template | c04ab | 0.364 | .364/.333/.571 | "He was okay." (feeling still dropped 2/3) |
| H2b never-outcome-alone | c04ab | 0.533 | .533/.533/.571 | "He was scared but ended up okay." etc. |
| FINAL2 (pre-pause ship) | c04ab | 0.571 | .0/.571/.571/.615/.571 | 4/5 pass; one 'not mentioned' abstention |
| FINAL2 re-verified | b57dc | 0.571 | .333/.571/.615 | wobble unchanged on the fresh binary |
| FINAL5/F6 how-wordings | b57dc | .571/.486 | 3+6 draws | comparable; FINAL6's median dipped below .5 at n=6 |
| **FINAL7 (SHIP)** | **b57dc** | **0.571** | **.571/.571/.571/.615** | feeling present 4/4, no abstention |

H1 control confirms the wobble is store-extraction phrasing, not config: the exact frozen-store-passing G4
instruction (0.533 x3 on frozen) wobbles identically on the fresh store. The fresh note attributes fear to the
whole family ("The whole family was freaked out and really scared, but thankfully her son was okay"), so the
note-faithful son answer needs the feeling forced in: "state the feeling first, then the outcome, and never reply
with the outcome alone" does that (note-faithful ceiling 'He was scared but okay' = 0.6154).
FINAL4 (pre-pause) verified that a "no-both" rewording ("a reply that names only the outcome is incomplete")
loses the feeling again (0.375 median) — the never-alone phrasing is load-bearing.

## Regression on FINAL7 (fresh store, binary b57dc607; x3 draws)

| qid | bar | median | draws | verdict |
|---|---|---|---|---|
| q1 | 1.0 | 1.0 | 1/1/1 | hold |
| q54 | 1.0 | 1.0 | 1/1/1 | hold |
| q126 | 1.0 | 1.0 | 1/1/1 (+1.0 recovered) | hold (one ledger-append race, run scored 1.0; clean redo 1.0) |
| q135 | 1.0 | 1.0 | 1/1/1 | hold |
| q143 | 1.0 | 1.0 | 1/1/1 | hold |
| q141 | >= 0.5 | 0.625 | .625/.471/.625 | hold |
| q26 | fresh win 1.0 | 1.0 | 1/1/0 | hold (one 'not mentioned' abstention; pre-existing class: 4/9 historical q26 attempts abstained on frozen too) |
| q33 | fresh win 0.667 | 0.667 | .667/.769/.667 | hold |
| q55 | record only (w24) | 0.0 | 0/0/0 | w24 abstention class; within its 0.0/1.0 wobble envelope; w32 remains AA's lane |

## Verdict

- q149 bar (>= 0.8): MET — FINAL7 median 0.8 at n=6 on the post-merge binary (.8 x5, .5 x1; world-leak 0/6).
- q144 bar (>= 0.5 stable): MET — FINAL7 4/4 draws >= 0.571, no abstention, feeling present in every draw.
- Regression: all bars hold (q1/q54/q126/q135/q143 = 1.0; q141 0.625; q26 1.0; q33 0.667); q55 recorded at w24 (0/3 this batch, wobble envelope unchanged, w32 = AA's lane).
- Aggregate win vs gate baseline: q149 +0.229 (0.571 -> 0.8), q144 +0.238 (0.333 -> 0.571).
- Binary continuity: the pre-pause battery (c04ab631) and this restart (b57dc607) share the identical warm
  cache, identical recall window (24 notes / 13761 chars on every q149 draw) and identical question protocol;
  the only difference is answer-stage sampling, which the x3/x6 medians absorb.

Wobble classes recorded honestly: q26/q55 abstention draws are recall-adjacent answer-stage variance
(pride-parade/book/sunset notes sit near the 24-entry window edge); they predate this change (time/when clauses
are verbatim from the gate instruction) and q26 medians hold at n>=3. q149's residual under-enumeration draw
(1/6) is the remaining answer-stage variance; no gold terms or gold examples appear in any instruction.
