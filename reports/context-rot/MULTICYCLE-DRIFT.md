# MULTI-CYCLE DRIFT — E2: 30 planted facts, 32 probes, three compaction cycles

Question: does repeated compaction erode recall of session facts, and does the
deterministic task-state ledger slow or stop that erosion?

## 1. Design

- Fixture (`exp/session2.py`): 30 planted items (12 constraints, 8 decisions, 4 corrections,
  6 evidence records) distributed across a long synthetic session, interleaved with tool
  output.
- Probes: 31 fact probes (`c1`-`c12`, `d1`-`d8`, `x1`-`x4`, `e1`-`e6`, `o1`) plus one
  not-stated trap (`q99_not_stated`) = 32 probes. All are asked of the summary alone.
- Three sequential compactions per arm, with 18 tool-result blocks between cycles (this
  fixture defines no new user turns). Summarizer wire shape and budgets are the same as E1
  (`COMPACTION-FIDELITY.md`).
- Arms: `baseline` (production prompts, `summary_update_policy = "off"`) and `ledger`
  (consolidating prompt plus the rendered ledger).
- Source: `runs/e2/compact.jsonl` (6 compaction records + 192 probe records),
  `summary.json` `e2_compactions`, `e2_scores`.

## 2. Cost per cycle

| arm | cycle | input chars | prev summary chars | ledger tok est | prompt tok | completion tok | summary chars | latency s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1 | 201515 | 0 | 0 | 72742 | 2073 | 3695 | 6.5 |
| baseline | 2 | 114383 | 3695 | 0 | 44502 | 1366 | 3976 | 8.4 |
| baseline | 3 | 113086 | 3976 | 0 | 45281 | 1281 | 4246 | 6.4 |
| ledger | 1 | 201515 | 0 | 761 | 73655 | 5117 | 4841 | 15.5 |
| ledger | 2 | 114383 | 4841 | 761 | 45977 | 2944 | 6232 | 8.2 |
| ledger | 3 | 113086 | 6232 | 761 | 47032 | 2149 | 6677 | 6.7 |

Measured prompt-token deltas between arms: +913 (cycle 1), +1475 (cycle 2), +1751
(cycle 3). The constant ledger block is 761 estimated tokens; the rest of each delta is the
longer previous summary being carried forward. Input chars are byte-identical per cycle, so
the transcript itself did not differ.

## 3. Recall by cycle

| arm | cycle 1 | cycle 2 | cycle 3 | pooled |
| --- | --- | --- | --- | --- |
| baseline | 32/32 | 32/32 | 32/32 | 96/96 |
| ledger | 32/32 | 32/32 | 32/32 | 96/96 |

**This is a negative result.** Both arms answer all 32 probes correctly at every cycle.
For this model, the model-written summary retains simple facts across three cycles on its
own, and the ledger produced NO measurable recall gain at all. There is no cell in which
the two arms differ.

## 4. Drift measurements that did move

Recall did not drift, but two quantities did:

| quantity | baseline | ledger |
| --- | --- | --- |
| summary length, cycle 1 to 3 | 3695 to 4246 chars (+15 %) | 4841 to 6677 chars (+38 %) |
| summarizer prompt tokens, cycle 1 to 3 (falls with the transcript) | 72742 to 45281 | 73655 to 47032 |

Summaries grow every cycle in both arms; that growth is the visible trace of summary-of-
summary rewriting. With the ledger the summaries are 1.31x, 1.57x and 1.57x the baseline
length at cycles 1, 2 and 3. The ledger's instruction to copy items without paraphrasing
is the plausible cause, but no experiment isolates it.

## 5. Why this negative result is weaker than it looks, and why it still counts

The fixture sits at a ceiling: the baseline already scores 100 %, so no treatment can show
an improvement, and the design has no power to detect a small degradation either. Three
properties make the fixture easy:

1. Each cycle's new material is 18 tool-result blocks that restate the lib test total, so
   only the test-total facts are re-exposed to the summarizer. The constraint and decision
   probes are never restated after their first cycle and still survive, which is the model's
   doing, not the fixture's.
2. The previous summary is re-fed verbatim, and this model's summaries restate identifiers
   instead of compressing them away: summary length grows every cycle in both arms.
3. Nothing is forced out by length. The summary stays well inside its 13107-token budget at
   every cycle (3695-6677 chars), and there are only three cycles.

Consequences for interpretation: the result is a valid statement about THIS model on THIS
fixture, and it is the honest headline for the ledger's recall claim (no gain measured). It
is not evidence that summaries are lossless in general. E1 supplies the counter-example in
the same campaign: one near-miss fact selection at cycle 3 (`4785` lib count answered as
`583` runtime count), which is drift of the kind this experiment would need many more
probes to measure.

## 6. Wall clock

Compaction latency in E2 is 6.4-15.5 s; in E1 it is 9.0-21.9 s (the 21.9 s cell also
produced 5036 completion tokens). The 300 s episode deadline (CC:761) is never approached.
Provider latency dominates, so the ledger's cost is tokens, not time.

## 7. Decision

E2 ledger arm: OPT-IN, default `off`. Keep the shipped default `compaction.taskState =
"off"`; the measured benefit at this fixture size is zero and the token cost is real
(+913 to +1751 prompt tokens per cycle, plus a 15-57 % longer summary).

E2 as a gate: KEEP the fixture as a floor check (a future regression that drops a fact at
cycle 3 would be visible), but do not use it to justify the ledger. To test drift properly,
the next version needs a harder fixture: facts stated once and never repeated, 5-8 cycles,
and 3 repetitions per cell so a one-item difference is separable from decoding noise.
