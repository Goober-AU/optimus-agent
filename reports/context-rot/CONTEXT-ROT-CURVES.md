# CONTEXT-ROT CURVES — needle-in-a-haystack extension (C1)

Question: does answer accuracy degrade with prompt length, with evidence position, or with
needlet type, for the model and corpus under test?

## 1. Design

- Grid: 7 nominal lengths x 5 positions x 3 needlet kinds x 1 repetition = 105 cells.
- Kinds: `exact` (passcode `7391-AX`, question shares its tokens), `semantic` (folder
  `KESTREL-4`, question shares almost no tokens), `causal` (a failure cause described,
  question asks for the cause).
- Position is the fraction of filler before the needle (0.0 = start, 1.0 = end); the cut is
  snapped to a newline. Filler is seeded synthetic text, register `seed % 4`.
- Prompt: system "Answer with only the requested value." then `Read the document below.`
  + `<document>` + `Question: ...`. Output cap 200 tokens. Scoring is a per-kind substring
  rule (see `BASELINE.md`). Source: `runs/c1/niah-main.jsonl`, `summary.json` `niah_main`.

## 2. Accuracy by nominal length

| nominal | n | correct | accuracy | median prompt tokens | min | max | median latency (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2k | 15 | 15 | 1.00 | 2625 | 1565 | 3689 | 6.7 |
| 4k | 15 | 15 | 1.00 | 5626 | 3033 | 7248 | 6.3 |
| 8k | 15 | 15 | 1.00 | 12394 | 5985 | 13992 | 8.2 |
| 16k | 15 | 15 | 1.00 | 20687 | 11903 | 27487 | 8.2 |
| 32k | 15 | 15 | 1.00 | 49110 | 23774 | 54844 | 23.8 |
| 64k | 15 | 15 | 1.00 | 81884 | 47260 | 109574 | 24.1 |
| 128k | 15 | 15 | 1.00 | 196402 | 94339 | 218969 | 37.9 |

Total 105 / 105 correct, 0 errors. Actual prompt tokens span 1565 to 218969: the largest
cells exceed the 128k label because filler is generated at 4 characters per token while the
endpoint reports its own count. The curve is flat at 1.00 across a 140x range of actual
prompt size.

## 3. Accuracy by needlet kind and by position

| kind | n | correct | accuracy | median latency (s) | median completion tokens |
| --- | --- | --- | --- | --- | --- |
| exact | 35 | 35 | 1.00 | 6.8 | 29 |
| semantic | 35 | 35 | 1.00 | 33.0 | 79 |
| causal | 35 | 35 | 1.00 | 8.3 | 126 |

Accuracy by position is 21 / 21 at each of 0.00, 0.25, 0.50, 0.75 and 1.00 (3 kinds x 7
lengths per position). Position 0.5 is perfect in every kind and length: no lost-in-the-
middle trough appears, and a position effect would have had 21 opportunities per position.

## 4. Latency is a separate curve and is not attributable to length alone

Median latency rises in steps: 6.3-8.2 s up to 16k, 23.8-24.1 s at 32k-64k, 37.9 s at
128k. But at every one of the 7 lengths the semantic needle is slower than the exact
needle, including 2k (37.8 s vs 6.7 s), where prompt size and completion length are both
small. Provider-side per-request variance therefore dominates at short lengths. The only
defensible statement: at 219k actual prompt tokens the median request takes about 38 s and
still answers correctly. Do not read this table as a cost-per-token model.

## 5. The distractor arm is NOT SCORABLE as built

`runs/c1/niah-distract.jsonl` has 42 cells and `summary.json` reports 21 / 42. Separate the
sub-arms:

| sub-arm | n | correct | what the distractor is |
| --- | --- | --- | --- |
| `related` | 21 | 21 | 10 same-shape statements with different labels (`Orion`, `Vega`, ...) and codes |
| `contradictory` | 21 | 0 / 21 | the SAME label `Helios` restated with code `0000-ZZ` |

`related` is a valid discrimination test and the model passes it 21/21 at every length,
including 218441 prompt tokens. `contradictory` is a protocol defect, not a model failure:

1. The distractor reuses the exact label and sentence frame, so the document holds two
   equally authoritative statements. No unique gold exists to score against.
2. All 21 records hit the 200-token cap exactly (`completion_tokens` = 200 for every one),
   emitted no `</think>`, and were parsed by the `last_line` fallback. The recorded
   "answer" is deliberation text such as "Document contains conflicting passcodes:
   7391-AX then 0000-ZZ. Need determine?".
3. The `exact` rule requires the gold present AND `0000-ZZ` absent, so any answer naming
   both codes fails by construction. The model abstained and said so; the scorer logged a
   miss.

The correct design states authority explicitly ("superseding the earlier code", "change of
plan: ...") so a unique gold exists and the skill under test is rule-following, not
guessing. That rule is what the shipped ledger implements (`superseded` section;
`task_state.rs` test `a_later_correction_retires_the_earlier_decision`). Never quote 21/42
as an accuracy figure.

## 6. One main-arm record relies on reasoning text

1 of 105 main cells (`causal`, 32000 nominal, position 0.75) emitted no delimiter within
200 tokens (`completion_tokens` = 200). Extraction fell back to `last_line` and returned
deliberation text that happened to contain "disk had reached the warn threshold", so it
scored correct. The count is unaffected but the claim "all answers were cleanly extracted"
is false for that cell.

## 7. What this establishes and what it does not

- Establishes: on this task, corpus and endpoint, the model retrieves a planted value from
  up to 218969 actual prompt tokens at every tested position and for all three needlet
  types, with no measurable degradation.
- Does not establish that context rot is absent in general. The compaction experiments show
  a specific fact can be lost or mis-selected after compaction, which is a different
  mechanism (prose rewrite, not long-context retrieval). See `COMPACTION-FIDELITY.md`.
- Does not establish comparability with Chroma's published NIAH numbers: synthetic corpus,
  substring scorer instead of an LLM judge, different grid.

## 8. Decision

C1 needle extension: KEEP as a saturation gate. A change to context assembly that lowers
accuracy at 219k tokens is detected cheaply; a ceiling of 105/105 cannot detect an
improvement, only a regression.

C1 contradictory distractor: REVERT the arm as designed and replace it with an
authority-rule arm. Do not cite the current 0/21 result.
