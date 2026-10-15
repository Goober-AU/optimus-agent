# ANSWER-RESIDUALS — round-5 triage of the G-class (answer-stage) residual bucket

Worker `ansres` | worktree `imp4b-ansres` (branch `improve/mem4b-ansres`, base 240e9fbc9 = imp4 HEAD) |
**LOCAL-ONLY: no endpoint calls, no drivers, no memory_bench launches.** Sources: the live
`QUESTION-LEDGER.json` (snapshot: last attempt 2026-10-14T23:50:54Z), drank's
`loco-drank/task1-rank-analysis.json` (gold ranks, 8 fresh stores), an offline
`search_replay.py` replication for writefull's conv-30/conv-49 stores (replay verified 24/24 against
the binary recall of `wf30-q17-a1`; conv-30_q3's gold-carrier rank 130 reproduces drank's
consolidation-measurement note exactly), and the `run.jsonl` answer traces of every question's latest
banked-family attempt (FINAL7/8/9 answer instruction, window 24, `recallQueryStemming`, no rerank).

## 1. Enumeration — what the answer-stage residual set actually is

LoCoMo ledger-FAILED = 158. Cross-referencing drank's rank analysis (+ my conv-30/49 replay) against
the 10 fresh clause-set stores:

| stage | count | questions |
|---|---:|---|
| **answer-stage (gold in top-24 window)** | **144** | 133 (drank 8-store sweep) + 11 (conv-30/49: q17, q44, q50, q51, q59 / q103, q128, q144, q18, q26, q46) |
| deep-rank, gold rank 37-176 | 8 | conv-41_q50@51, conv-42_q56@50, conv-47_q11@54, conv-48_q185@49, conv-50_q40@176, conv-42_q151@60, conv-44_q24@50, conv-30_q3@39 |
| unextracted gold (write-time) | 3 | conv-42_q184, conv-50_q140, conv-30_q76 |
| edge band w25-36 | 3 | conv-50_q28 (1 of 3 gold parts in 25-32; others @122/@150), conv-49_q51@29, conv-47_q97@33-36 |

Non-LoCoMo FAILED (4): lme_v2 499488a6 = write-time (axtree cap); lme_v2 f928c075 and LME-S 00ca467f
are fixed-but-unflipped (1.0 evidence committed); LME-S 0e5e2d1a = write-side assistant-fact exclusion
(already a queued round-5 KU clause). **None are answer-stage.**

Reconciliation with the "49 G-class": 49 was the round-4 scorecard count on *frozen* stores (55 after
the stability re-class, 51 projected after formcons). On *fresh* stores the write-time lane closed, so
the gold-in-window set expands to 144. Of those 144:

- **82 fail their latest banked draw** (the true residual: 70 never-converted + 6 stability-UNSTABLE +
  4 STABLE draw-luck + 2 variance-band).
- **62 pass their latest banked draw but stay ledger-FAILED** (45 stability-STABLE sweep converts that
  no worker flipped + 13 conv-26 FINAL9-battery cells + 4 variance-band). These are flip-pending
  bookkeeping, not round-5 headroom.
- **Measurement caveat:** 59 of the 82 were last measured under FINAL7 on frozen stores. Only 23 have
  FINAL9 window-24 runs; 20 of those fail every FINAL9 draw (FINAL9-confirmed residual), and
  conv-26_q33 / conv-47_q138 / conv-47_q34 pass some FINAL9 draws (wobble). The first cheap round-5
  move is re-running the 59 under FINAL9 on the fresh stores (writefull/writecamp answer-batch pattern)
  before writing any new clause — formcons already showed two apparent residuals (conv-47_q23 0.8,
  conv-50_q16 rank-1 note carries "(July 2023)" in its title) are FIXED by FINAL9's rule (3) and only
  lack a re-run.

## 2. Failure-mode classification (taxonomy from the stability audit)

Per-question classification of all 144 cells is in `answer-residuals.json` (machine-readable) —
method: latest banked-family answer trace (run.jsonl answer + local-F1 score) read against the gold.
Counts below: **fail-latest / pass-latest**.

| fix family | fail | pass | fail-latest questions | FINAL9 status |
|---|---:|---:|---|---|
| **brevity / over-elaboration** | 19 | 1 | conv-30 q50,q51,q59; conv-41_q131; conv-42 q138,q143,q170; conv-43 q116,q87; conv-44_q121; conv-47 q138,q139; conv-48 q102,q173,q181,q69; conv-49_q128; conv-50 q48,q155 | SATURATED ("fewest words" clause exists; FINAL10's harder compression was REJECTED — q33 0.667->0.20, q99 0.706->0.40) |
| **binding (event/aspect/fact)** | 16 | 0 | conv-26_q66; conv-30_q17; conv-41_q125; conv-42 q165,q197,q91; conv-43 q138,q16,q164; conv-44 q41,q68; conv-47_q147; conv-49_q144; conv-50 q147,q85,q89 | PARTIALLY covered (two binding clauses exist; residual is selection enforcement) |
| **abstention / sufficiency** | 12 | 0 | conv-26 q37,q77; conv-30_q44; conv-41_q68; conv-42_q41; conv-43_q20; conv-49_q103; conv-50 q137,q61,q67,q88,q97 | **GENUINELY NEW** (FINAL9 defines the abstention string, never when to abstain) |
| **paraphrase / natural-form** | 11 | 3 | conv-41_q18; conv-43 q142,q59; conv-44 q14,q73,q74,q92; conv-47_q34; conv-48_q162; conv-50 q87,q90 | SATURATED ("worded like your notes" exists; residual = gold wording differs from every note's wording) |
| **list completeness** | 8 | 5 | conv-41_q57; conv-42 q11,q63; conv-43 q9,q61; conv-44_q9; conv-49 q18,q26 | PARTIALLY covered (combine-items clause exists; multi-note aggregation still fails) |
| **temporal-form / date-form** | 9 | 5 | conv-42_q20; conv-47 q21(ISO),q23,q43,q52; conv-48 q101,q81; conv-50 q10,q16 | MOSTLY covered (precedence rules 1-4 + ISO ban); **month-span computation is a genuine policy gap** — rule (4) forbids the conversion the gold wants |
| **open-world / dataset-limit** | 6 | 0 | conv-26_q2; conv-42 q4,q85; conv-43_q66; conv-44_q44; conv-49_q46 | NOT CLAUSE-FIXABLE (world-knowledge synthesis) |
| **payload / length-stop** | 1 | 1 | conv-26_q33 (empty, stop=length + degraded rescue); conv-48_q172 (d1 `<memory_data>` echo, wobble) | CODE LANE (rescue needs a hard small max_tokens, per FINAL10's own finding) |

Notable sub-families inside temporal-form: conv-47_q23 and conv-50_q16 have the calendar month in the
rank-1 note title ("dream job offer (starting July 2022)", "booked mountain region trip (July 2023)")
— FINAL9 rule (3) already covers them (q23 measured 0.8 under FINAL9); they need a re-run, not a clause.
The true month-span cells are conv-47_q43 ("the month after 21 August 2022" vs gold "In September,
2022" — the note says "next month", no calendar anywhere) and conv-47_q52 (approximation granularity).

## 3. Top-3 round-5 candidate clauses (ranked by questions touched x gate risk)

### C1. Sufficiency-first clause (anti-abstention) — 12 fail-latest touched, 5 FINAL9-confirmed. Gate risk: LOW-MEDIUM.

Clause direction (one sentence, appended before the not-mentioned tail): *"Before replying
'not mentioned', check every provided note for the question's subject; if any note states the fact —
in different words, without the question's exact phrasing, or only in its body text — answer from that
note; only reply 'not mentioned' when no note states the fact."*

- Why: the largest genuinely-new family. In every one of the 12 cells the gold carrier sits in the
  recall window (ranks 1-24, often rank 1-3: conv-49_q103's "Evan's goal of adding more vegetables" at
  rank 14, conv-50_q97's custom-car note at rank 3, conv-50_q67's "Japanese house" note at rank 2) and
  the model abstains anyway. Spot-checked traces show title-vs-body and inference-shyness patterns.
- FINAL9-confirmed subset (fails every FINAL9 draw): conv-26_q37, conv-26_q77, conv-30_q44,
  conv-49_q103, conv-50_q61.
- Gate risk: low on the FAILED queue (no passing cell abstains; the dataset-limit cells that SHOULD
  abstain already score 0.0 and cannot regress). Residual risk: false-positive answers on truly
  unanswerable questions — no existing gate protects the abstention path, so add one held-out
  unanswerable probe from an UNTESTED conversation before promoting.
- **Gate set (3 previously-passing per conversation):** conv-50 q24 / q135 / q143 (1.0, 1.0, 1.0);
  conv-26 q1 / q54 / q55 (1.0 x3 battery); conv-49 q138 / q94 / q151 (1.0 x3 writefull gates, fresh
  store). Bar: all 9 hold >= baseline - 0.1 and no new 'not mentioned' on any previously-answered gate.

### C2. All-items note-scan clause (list-completeness strengthening) — 8 fail-latest + 5 wobble touched, 2 FINAL9-confirmed. Gate risk: MEDIUM.

Clause direction (extends the existing all-items sentence): *"For a question asking for all items of a
kind, first enumerate every provided note about that person or topic, then name one distinct item from
each note that states one, in one or two words each."*

- Why: 8 clean failures are partial lists (conv-49_q26 answers 1 of 3 extracted motivations; q18 lists
  4 of 7 suggestions plus a wrong item; conv-42_q11 2 of 4 allergens; conv-43_q9 2 of 4 deals). The
  gold parts are in-window (multi-note), so the miss is aggregation, not retrieval. FINAL9's
  "do not stop at the first or most salient" is not strong enough to force a per-note scan.
- FINAL10 caution: its list CAP (six-item ceiling) was part of the rejected combination; this is the
  opposite direction (scan-more) and must not degrade the current over-includers.
- **Gate set:** conv-26 q18 / q39 / q149 (1.0 list cell + 0.658 over-list canary + 0.8 attribute-list
  canary — the over-inclusion guards); conv-41 q47 / q60 / q35 (0.875 / 1.0 / 0.5); conv-49 q138 /
  q94 / q151 (fresh store). Bar: 9/9 hold; over-list canaries may not drop below 0.5.

### C3. Month-span resolution clause (temporal policy amendment) — 3 clean touched, 0 FINAL9-confirmed. Gate risk: MEDIUM-HIGH.

Clause direction (amends precedence rule 4, does not add a rule): *"...if the relative wording names a
span of about a month ('next month', 'the following month') relative to a dated conversation, reply
with the computed calendar month and year in words; spans of days or weeks keep the anchored wording."*

- Why: conv-47_q43's note says only "next month" (rank 1, no calendar anywhere); gold is
  "In September, 2022"; FINAL9 rule (4) actively instructs the anchored form that scores 0.25. This is
  the one family where the terminal instruction CAUSES the failure. conv-47_q52 (approximate season
  vs "the few months before...") and conv-48_q81 (wrong date selection) ride the same edge.
- Smallest touch count, highest interference risk: it edits the sealed precedence zone that sank
  FINAL10. The day/week carve-out is load-bearing — it protects the three passing weekday-anchor
  cells below.
- **Gate set:** conv-42 q28 / q31 / q64 (0.667 x3 — the weekday-anchor canaries that MUST hold);
  conv-47 q49 / q142 / q119 (1.0 x3); conv-50 q135 / q143 / q24 (1.0 x3, plus q10 as the
  improvement canary: 0.4 -> must not fall).

### Not recommended as clauses (documented for completeness)

- **Brevity enforcement** (19 cells): FINAL9 already carries the strongest safe compression; FINAL10's
  anti-padding sentence degraded q33/q99 and failed 2/5 gates. Remaining lever is the code-side
  length-stop rescue (hard small max_tokens on the rescue prompt), not instruction text.
- **Binding strengthening** (16 cells): FINAL9's two binding clauses cover the semantics; the residual
  is selection enforcement under variance (conv-50_q89-class). No clause wording found that does not
  collide with the list-completeness direction.
- **Open-world synthesis** (6 cells): world-knowledge gold (conv-42_q4-class); no memory-side fix.

## 4. Recommended round-5 sequence

1. Re-run the 59 FINAL7-measured fail-latest cells under FINAL9 on the fresh stores (answer-batch
   pattern, ~1 draw each) — free conversions expected (q23-class); re-tally before clause work.
2. Flip the 45 stability-STABLE + 13 conv-26-battery pass-latest cells to RESOLVED under the
   controller's ledger lock (bookkeeping; shrinks the FAILED queue to the true residual).
3. Then C1 (sufficiency-first) as the first new clause candidate — largest touch, lowest gate risk —
   with its 9-cell gate set + one held-out abstention probe; C2 and C3 only if C1's gates stay green.

*Provenance: every number above recomputed from the ledger snapshot + drank artifacts + run.jsonl
traces listed in the header; per-question evidence in `answer-residuals.json` (144 rows). No runs were
launched; no ledger writes were made by this worker.*
