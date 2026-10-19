# RETRIEVAL SCORECARD — every retrieval surface the campaign touched, and what it scored

This is one table over all surfaces, so a single surface's good or bad number cannot be
read as a general statement about retrieval in Optimus. The model, endpoint, corpus and
extraction caveats in `BASELINE.md` apply to every row.

## 1. Scorecard

| # | surface | experiment | cells | result | status |
| --- | --- | --- | --- | --- | --- |
| 1 | planted value from a long document, 2k-128k nominal (1565-218969 actual tokens) | C1 main | 105 | 105 / 105 | measured, ceiling |
| 2 | planted value by evidence position, 5 positions | C1 main | 105 (21 per position) | 21 / 21 per position | measured, ceiling |
| 3 | planted value by needlet type | C1 main | 105 (35 per type) | 35 / 35 per type | measured, ceiling |
| 4 | discrimination against 10 same-shape related distractors | C1 distractor `related` | 21 | 21 / 21 | measured |
| 5 | authority resolution against a same-label contradiction | C1 distractor `contradictory` | 21 | 0 / 21 (no valid gold) | NOT SCORABLE |
| 6 | session facts from a compaction summary after 1-3 cycles | E1, 12 probes | 36 summary + 36 summary+tail | 71 / 72 per arm, pooled over both readouts | measured, one-item differences |
| 7 | session facts from a compaction summary after 1-3 cycles, 32 probes | E2, 32 probes | 96 per arm | 96 / 96 per arm | measured, at ceiling |
| 8 | failure evidence from a 2000-char tool excerpt, old policy | E4 `old` | 4 | 0 / 4 | measured |
| 9 | failure evidence from a 2000-char tool excerpt, new policy | E4 `new` | 4 | 4 / 4 | measured |
| 10 | memory recall (`memory/search.rs` recall block) | none | - | - | NOT EXECUTED |
| 11 | Jev retrieval and Jev compaction filters | none | - | - | NOT EXECUTED |
| 12 | the same tasks on a second model or endpoint | none | - | - | NOT EXECUTED |
| 13 | official benchmark scorers (Chroma context-rot, NoLiMa, RULER, LongBench, RepoQA, CompactBench, SWE-bench, Terminal-Bench) | none | - | - | NOT EXECUTED |

Aggregate per surface type: long-context retrieval 126 / 126 over rows 1 and 4 (rows 2 and 3
are breakdowns of row 1, not extra cells); post-compaction recall 71 / 72 (E1) and 96 / 96
(E2) per arm; excerpt-level evidence recovery 0 / 4 before the change and 4 / 4 after.

## 2. Reading the table

- Rows 1-4 are a saturation result, not a capability claim. They say that a planted fact is
  retrievable at these sizes for this model; they say nothing about multi-hop reasoning,
  aggregation over many facts, or any published benchmark.
- Row 5 is a design defect. The contradictory distractor restates the same label with a
  different code, so there is no unique gold; all 21 answers are deliberation text cut off
  at the 200-token cap. See `CONTEXT-ROT-CURVES.md` section 5.
- Rows 6-7 disagree in the only way that matters for planning: E1 loses one item per arm and
  E2 loses none. E1 has 12 probes and a near-miss distractor (`583` runtime count next to
  `4785` lib count); E2 has 32 probes and no near-miss competing with any gold. The pair
  shows that single-item losses are visible at n=12 and invisible at n=32, which is a
  statement about the fixtures, not about the model.
- Rows 8-9 are the only place where a policy change moved a retrieval score, and it moved it
  from nothing to everything. That is what makes it defensible: the information was absent
  from one arm's summary and present in the other's.
- Rows 10-13 are unmeasured, and the audit predicts two of them would be poor: memory recall
  fails closed and silently when one entry has a foreign `projectId` (MST:406-409), and Jev
  truncation records a character count with no durable handle (JC:576-593). Not executing
  these is the largest gap in the campaign.

## 3. Retrieval defects that are audited but unmeasured

| defect | citation | retrieval consequence | status |
| --- | --- | --- | --- |
| D13 memory recall fails closed | MST:406-409, MS:1153-1155 | one foreign `projectId` drops the whole recall block with no marker | OPEN |
| D11 stale harness digests replayed | AS:16176-16207, MSG:1325-1360 | two digests claim to be the same session state | OPEN |
| D12 memory and harness text arrive as unfenced user turns | MSG:1325-1360, MS:1168, HP:1173 | retrieved text is instruction-shaped and role-identical to a user message | OPEN |
| D15 middle of a tool result unrepresentable | CU:125-150, CU:337, CU:428 | failure evidence lost before summarization | FIXED for text lines (E4), images still dropped |
| D16 user images counted as zero tokens | CC:1265 | screenshot-heavy turns under-count, so the trigger fires late | OPEN |
| D20 summarizer request is not the model's request | AS:17910-17924, AS:18056-18065 | the summary describes a larger list than the session sent | OPEN |

## 4. Decision

Rows 1-4 (C1 main): KEEP as a regression gate. Row 5: REVERT the arm and redesign with an
authority rule. Rows 6-7 (E1/E2 with the ledger): OPT-IN, default off, no measured gain.
Rows 8-9 (E4): KEEP, always on. Rows 10-12: NOT EXECUTED, and they are the next experiments
in priority order (memory recall, Jev filters, second model). Row 13 is out of scope: no
official scorer was available to run.
