# loco-nearmiss: answer near-miss lane (length-cap guard + near-miss refinements)

Worker `nearmiss` | worktree `imp4b-nearmiss` (branch improve/mem4b-nearmiss, base 57b717e67 = imp4 HEAD
post loco-sweep merge) | model dgx-glm53/glm-5.3 (only) | driver built from this worktree
(imp4b-nearmiss-target, sha256 03f2ae2dce8e7bdf...; lib memory:: 138/138 debug, example 75/75 release;
the ONLY driver change is the blank-at-cap rescue rework below).

Lane: the 20 sweep near-misses (0.3-0.5 local-F1) + the 2 FINAL7 length-cap cells (conv-41_q50, conv-30_q44).
Attempts: reports/continuation/iteration4/ql-attempts-lconnm (campaign root, outside worktrees).

## 1. Length-cap diagnosis (what does the model loop on?)

Raw provider replay of both cells (same recall block + FINAL7 instruction, native thinking, 8192 cap):
- conv-41_q50 'What attributes describe John?': 36.4k reasoning chars, 0 content chars. The model
  re-reads the 24 John notes, builds candidate attribute lists, second-guesses completeness
  ("hmm, or should I include more?"), re-litigates the question reading, and never opens the answer
  channel before the cap. The old in-session blank rescue replays the same looped reasoning turn
  (reasoning_content is replayed to the model) and caps again -> still_blank.
- conv-30_q44 'What does Gina say about the dancers in the photo?': same shape (34k reasoning chars,
  0 content): re-reads the dance/photo notes, cannot decide which photo, never commits.
- No API flag disables the reasoning channel on this endpoint (reasoning_effort omitted/none,
  thinking disabled, enable_thinking false all still reason; thinkingLevelMap off=null).
- Gold-side reality: conv-30_q44's gold ('They look graceful') is ABSENT from the frozen store
  (write-time gap); conv-41_q50's gold attributes are inference labels (selfless/family-oriented/
  passionate/rational from dialog turns D2:14/D3:5/D4:6/D26:6) with only 'passionate' literally
  findable. Both cells were mis-staged as G; their ceiling on frozen stores is ~0.2-0.4.

## 2. Fix (two layers, both general)

a) Instruction guard (GUARD, ships inside FINAL9's answerInstruction):
   - "Keep every answer brief: at most two sentences and never more items than the question asks for.
     Examine each note at most once, never restart an analysis you have already completed, and as
     soon as you can state an answer, state it; when readings conflict or stay uncertain, still
     commit to the best-supported one rather than none."
   - combine clause + "A full list rarely exceeds six items: name the items your notes state most
     directly and stop there"
   Raw replay: converts both loop cells to visible answers in single draws, but 1/3-1/2 draws still
   loop -> not sufficient alone.
b) Driver rework (the real correctness fix): blank-at-cap rescue no longer retries inside the looped
   session. It now makes ONE direct provider call outside the agent runtime: the exact recall block
   (MemoryService::recall over the same scratch store/settings), a minimal QA system prompt, and a
   terse instruction ("Using the notes above, answer in at most six words. Reply with ONLY the answer
   itself.\n\nQuestion: ..."), no tools. answer_blank_rescue records mode=direct_terse_prompt,
   rescue_stop_reason and rescue_usage; the main attempt's length-stop usage stays in record.usage.

Validation (x3 on both cells, guard + rescue):
- conv-41_q50: 3/3 non-empty (0.222/0.0/0.2; 2/3 draws needed the rescue and both were answered).
- conv-30_q44: 3/3 non-empty (0.0/0.0/0.0; 1/3 draw looped and was rescued). Scores stay 0 because
  the gold is absent from the frozen store; the correctness bug (empty answer, 2x8192 tokens wasted)
  is gone in 6/6 runs.
- With GUARD alone (old binary): c41q50 3/3 answered, c30q44 2/3 (1 loop) -> the guard reduces loop
  incidence, the rescue closes the remainder.

## 3. Near-miss cluster analysis (20 cells, sweep answers vs gold)

- A. partial list / missing gold items (6): conv-43_q9, conv-42_q63, conv-44_q9, conv-42_q11,
  conv-43_q61, conv-50_q28. Window analysis: the missing items' notes are usually IN the window
  (Nike 'considering' note, Valorant tournament note, positive-reinforcement workshop note) but the
  model excludes them as not-explicitly-offered/played, or the gold item is absent from the store
  (cockroaches, dog-owners group title). Inference conservatism, not scan failure: a mechanical
  "walk each note" clause (R2) moved nothing.
- B. dilution / over-long answer around a correct core (11): conv-49_q123, conv-49_q46, conv-48_q102,
  conv-49_q128, conv-42_q170, conv-30_q59, conv-30_q17, conv-44_q121, conv-42_q100, conv-48_q61,
  conv-50_q48. Answers append unasked clauses, parentheticals, dates, or second items; local-F1
  punishes the padding. The biggest cluster.
- C. value/date binding (3): conv-43_q16 (four vs three weeks), conv-48_q81 (2023-09-17 vs 'in October
  2023'), conv-43_q59 (career-high 40 points vs 'highest point score').

## 4. Refinements (up to 3, general wording, battery-gated; FINAL8 numbers must hold)

R0 (GUARD, refinement 1): the two guard sentences above. Battery x1 + medians on disagreements:
q1/q54/q126/q135/q143 all 1.0 (q135 median 1.0 over 3 draws: 0.631/1.0/1.0 = draw wobble), q149 0.8
(4 draws), q144 0.571 (4 draws), q33 1.0, aggregate (18-q medians) +0.538 vs FINAL8's +0.513 -> HOLDS.
R1 (=FINAL9, refinement 2, targets cluster B): GUARD + "The conversation files are already summarized
in your notes; never mention checking, searching, or examining anything, and answer immediately from
the notes alone." + "When you have the answer, stop: never append reasons, examples, dates, or
background the question did not ask for, and never use parenthetical asides. When the question asks
for a reason, feeling, or description, reply with the asked part of the sentence your note states,
trimmed alone." + "Final check before replying: if your draft is longer than ten words or contains a
clause the question did not ask for, cut it to the asked fact alone." Battery: fixed set 1.0, q149 0.8,
q144 median 0.571 (0.333/0.571/0.571), q33 median 0.666 (=FINAL8), q99 0.705 (up from 0.483),
aggregate (18-q medians) +0.527 -> HOLDS. Probes: q123 1.0, q121 0.666, q100 0.571 -> SHIPPED as FINAL9.
R2 (refinement 3, targets cluster A): R1 + "For questions about all items of a kind, include every
item your notes connect to that kind, even when a note states it only indirectly through an event,
win, plan, or mention involving the item." Battery holds (+0.515 by medians) but A-cluster probes are
FLAT vs sweep (q9-c43 0.465, q63 0.466, q9-c44 0.453, q61 0.4, q11 0.33) -> NOT shipped; the raw-screen
gain on q44_q9 did not survive the real driver.

## 5. Near-miss batch with FINAL9 (medians over up to 3 draws)

| qid | sweep | FINAL9 | verdict |
|----|----:|----:|----|
| conv-42_q100 | 0.400 | 1.000 | **CONVERT** (0.571/1.0/1.0) |
| conv-49_q123 | 0.462 | 1.000 | **CONVERT** (1.0/0.4/1.0) |
| conv-48_q61 | 0.444 | 0.500 | **CONVERT** (0.5/0.5/0.5) |
| conv-43_q9 | 0.472 | 0.472 | flat (partial list: Nike/Gatorade inference gap) |
| conv-42_q63 | 0.467 | 0.466 | flat (missing Valorant) |
| conv-44_q121 | 0.381 | 0.461 | up, not over (0.44/0.46/0.5) |
| conv-44_q9 | 0.453 | 0.453 | flat |
| conv-43_q16 / conv-43_q61 / conv-48_q102 / conv-49_q46 | ~0.4 | ~0.4 | flat |
| conv-30_q17 / conv-50_q28 / conv-50_q48 / conv-49_q128 / conv-48_q81 / conv-42_q11 / conv-30_q59 | 0.31-0.39 | 0.32-0.39 | flat |
| conv-43_q59 | 0.367 | 0.200 | dip (adds '40-point' value form) |
| conv-42_q170 | 0.357 | 0.173 | dip (anti-padding trims 'Michigan' framing) |

- **3/20 convert** (median >= 0.5); ledger flipped to RESOLVED for the 3.
- Honest net: batch mean 0.399 (sweep) -> 0.407 (FINAL9 medians); the 2 dips offset most of the
  conversion gain outside the converts themselves.

## 6. Verdict

- Length-cap correctness bug: FIXED (guard + direct terse rescue; 6/6 non-empty vs 2/2 empty before).
- Near-miss lane: 3/20 converts, all cluster-B dilution cells; cluster A (partial lists) is
  inference-limited and did not move with general wording; 2 cluster-C/B cells dipped.
- FINAL9 battery holds every FINAL8 gate: aggregate +0.527 (medians; FINAL8 +0.513), q149 0.8,
  q144 0.571, fixed set 1.0.
- Artifacts: FINAL9.json, typea/fullstack settings, run-battery.py, jobs-*/results-* (battery,
  probes, near-miss batch, redraws, wobble medians); attempts under ql-attempts-lconnm; ledger
  worker=nearmiss, 3 statuses flipped RESOLVED.
