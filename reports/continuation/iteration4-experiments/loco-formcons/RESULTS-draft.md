# loco-formcons: FINAL9 — answer form-consistency pass (12 of 14 non-stable cells + conv-44_q10)

Worker `formcons` | worktree `imp4b-formcons` (branch improve/mem4b-formcons, base bcca4df07 = imp4 HEAD
after the stability merge) | model dgx-glm53/glm-5.3 (only) | driver `imp4b-target8k` memory_bench.exe
(sha256 4d2ba13f..., the 8192-budget binary with rerank parse-retry) | controller
`scripts/memory_question_loop.py` (upstream) | attempts under
`reports/continuation/iteration4/ql-attempts-fc9` (campaign root, outside worktrees); ledger
auto-append worker=formcons (q10 draws to QUESTION-LEDGER-locosweep-gate.json, matching the sweep's
gate pattern).

**Purpose.** LL's scoped next lever: one answer-instruction refinement (FINAL9 = FINAL8 + four
general clauses) targeting the answer-form variance classes from the stability confirmation —
over-elaboration, temporal-form selection, ISO-date leak off conv-26, payload glitches, and the
conv-44_q10 binding regression — without touching recall (recall deterministic, byte-identical
windows confirmed per draw).

## FINAL9 (answerInstruction only; importInstructions byte-identical to FINAL8 -> cache key unchanged)

Four changes over FINAL8:

1. **Elaboration cap + payload guard** (opening): "Reply with ONLY the answer itself and nothing
   else: no preamble, no explanation, no citations, and never any tool syntax, tags, or JSON - plain
   text only. Answer in the fewest words the question allows; add no context the question did not
   ask for, and never drop the words that name who the answer is about."
2. **Yes/no and how-many forms**: "for questions asking whether something is true or possible,
   reply with just yes or no; for how-many or how-much questions reply with the bare number or
   amount in words, like two months;"
3. **Event binding**: "Answers about a specific event, activity, or thing must come from the note
   about that same event, activity, or thing: a note about a different one, even about the same
   person with a similar time frame, is not a match."
4. **When-form precedence reworked** (replaces FINAL8's rule 2 + the 'recently' clause):
   (1) year questions -> bare year; (2) relative wording that names a weekday -> weekday phrase
   anchored to the conversation date in words; (3) the note's own exact date or plain month and
   year (even a full date marked approximate) -> calendar answer in words, but a month narrowed to
   a part or an approximation (like 'late June 2023', 'mid-August 2022' or 'around mid-August 2023')
   or a span of days is not the event's date; (4) otherwise relative wording anchored to the
   conversation date in words; plus a global "Never write a date as year-month-day numerals".
   The FINAL8 'recently' clause is folded into rule (4) (same 'recently before 3 July 2023' form).

Full text: FINAL9.json / typea-final9-stem.json (this dir).

## Per-cell results (x3 draws, medians; before = stability-confirmation medians)

(final table below)

## Regression: conv-26 19-q battery (warm cache d4b542a8, FINAL9 = FINAL8 + clauses)

(final table below)

## Verdict

(final)
