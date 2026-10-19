# BUG LEDGER — audit defects, what the PR fixed, what is still open

Source of every defect row: `research/context_rot/ARCHITECTURE-AUDIT.md` section 12, which
carries `ABBREV:line` citations against `8ae54da`. Status is read from the PR diff in this
worktree (`git diff`, `git status`), not from intent.

Status vocabulary: FIXED (behaviour changed and a test covers it), MITIGATED (the failure
mode is reduced, the data path is unchanged), OPEN (unchanged by this PR).

## 1. Audit defects

| # | severity | defect | citation | status in this PR | evidence |
| --- | --- | --- | --- | --- | --- |
| D1 | HIGH | threshold hook gates on `settings.enabled` while the executor also requires `auto_compaction_enabled`, so a turn past the trigger is stopped and nothing compacts | AS:4811-4814 vs AS:16498 (flag AS:2829) | OPEN | `agent_session.rs` diff is one settings-plumbing line (AS:19343) |
| D2 | HIGH | an interactive turn that crosses the trigger is stopped and not resumed unless a continuation flag is set | AS:16827-16833, flag AS:4839-4865 | OPEN | not touched |
| D3 | HIGH | the cut point charged custom messages and branch summaries zero tokens, so `keep_recent_tokens` was only a lower bound | CC:1433-1438 | FIXED | new `entry_token_cost` charges `CustomMessage` text at chars/4 and `BranchSummary` at chars/4; `Compaction` and `Other` still cost zero by design |
| D4 | HIGH | summary-of-summary is the data path under both `summary_update_policy` values: previous prose is read from the entry, the source messages are excluded, the new summary replaces the old | CC:1552-1560, 1895-1902, 607-610 | MITIGATED, not fixed | the ledger and the consolidate prompt add protected material; no source re-read was added. With the shipped default `taskState = "off"` the mitigation is not active |
| D5 | HIGH | compaction can run with nothing new to summarize | CC:1574-1618, 1630-1635 | OPEN | unchanged |
| D6 | HIGH | an extension compaction bypasses `validate_summary` and can replace the prefix with an empty summary | AS:18171-18186, CC:2207-2299 | OPEN | unchanged |
| D7 | MED-HIGH | a null or incompatible provider checkpoint disables the whole compaction | CK:10-17, SM:1151-1162 | OPEN | unchanged; note the PR persists `taskState: None` on the provider-checkpoint arm, so a checkpointed compaction carries no ledger |
| D8 | MED-HIGH | migration can drop `firstKeptEntryId` permanently | SM:3281-3292, 3836-3838 | OPEN | legacy path untouched |
| D9 | MED-HIGH | the deadline fallback writes a write-only flag and its boilerplate becomes the next `previous_summary` | CC:2571-2578, 2620-2645 | PARTIAL | `deadline_truncation_result` now carries `task_state`, so the ledger survives a deadline truncation; the flag is still write-only and the boilerplate still becomes the next previous summary |
| D10 | MED | branch summaries truncate silently, skip tool results, never retry | CB:258-281, 154-156, 388, 426 | OPEN | unchanged |
| D11 | MED | stale harness digests stay in provider context next to the fresh one | AS:16176-16207, MSG:1325-1360 | OPEN | unchanged |
| D12 | MED | memory and harness text arrive as unfenced, instruction-shaped user turns | MSG:1325-1360, MS:1168, HP:1173 | OPEN | unchanged |
| D13 | MED | memory recall fails closed and silently on one foreign `projectId` or a short-skip | MST:406-409, MS:1153-1155 | OPEN | unchanged |
| D14 | MED | the read-files inventory in every summary is always empty | CU:68-80, CU:108-113, CC:2584 | OPEN | unchanged |
| D15 | MED | the summarizer cannot see the middle of a tool result, or any image | CU:125-150, 337, 428; images CU:322-325 | FIXED for text, OPEN for images | `EVIDENCE_LINE_RE` recovery keeps middle evidence lines inside the same budget; image blocks are still filtered |
| D16 | MED | user-attached images are counted as zero tokens | CC:1265 | OPEN | unchanged |
| D17 | LOW-MED | the token estimate excludes the system prompt and tool schemas | CC:1251-1330, AG:1162-1166 | OPEN | unchanged; CONTEXT HEALTH divides the tray usage from the same estimate path (CT:220-273) by the window, so the score inherits the understatement |
| D18 | LOW | compaction failure is invisible to the model | AS:16689-16752, MSG:1333 | OPEN | unchanged |
| D19 | MED | two live-list producers have no drain or cap | AS:16736-16739, 8609-8627, 9676-9679 | OPEN | unchanged |
| D20 | MED | the summarizer's request is not the request the model saw | AS:17910-17924, AS:18056-18065 | OPEN | unchanged |

Count: 2 fixed (D3, D15 text), 1 partial (D9), 1 mitigated (D4), 16 open (D1, D2, D5, D6,
D7, D8, D10-D14, D16-D20).

## 2. Measurement defects found in the campaign data

These are defects in the measurement, not in the product. They are listed here so no report
silently quotes the affected numbers.

| # | defect | evidence | effect | disposition |
| --- | --- | --- | --- | --- |
| M1 | the 200-token NIAH cap ends the turn inside deliberation, so `last_line` extraction returns reasoning text | 21 / 42 distractor cells and 1 / 105 main cells have `completion_tokens` = 200 and `extraction_rule` = `last_line` | the contradictory distractor arm is unscoreable; one main cell scored correct from reasoning text | raise the cap for authority arms, or require a delimiter before scoring |
| M2 | the `contradictory` distractor reuses the same label with a different code, so no unique gold exists | `runs/c1/niah-distract.jsonl`, distractor = `contradictory`, 21 rows | 0 / 21 is a scorer artefact, not a model error | redesign with an explicit supersession rule |
| M3 | E1/E2/E4 probe rows do not record `extraction_rule` | no such field in any probe object | 3 answers are unsplit deliberation text; 2 scored correct by substring match because the gold token sits inside the deliberation, and the third is the single ledger miss in E1 (`q10_remaining`) | record the rule and the raw completion in probe rows |
| M4 | two matchers disagree on `q5_checkpoint_store` | raw `correct` flags reject all 12 q5 answers; `summary.json` accepts them | up to one item per cell attributable to paraphrase tolerance | keep one matcher; document its rule |
| M5 | item seeds are derived from Python `hash()` of a tuple of strings, which is salted per process | two runs of the same expression returned 2568 and 37643 during this review | rerunning `probe/niah.py` with the same arguments does not regenerate the same items; each item is only reproducible from the `seed` field in its JSONL row | derive seeds from a stable digest (blake2b of the tuple) before the next campaign |

## 3. Observations in the new code (not defects, reviewer should confirm as intended)

| # | observation | location |
| --- | --- | --- |
| O1 | ledger is not persisted on the provider-checkpoint arm | `compaction.rs`, `task_state: None` in the native-compaction branch |
| O2 | ledger growth is bounded: caps 12/10/8/8/12/6 items, 320 chars per item, 6000 rendered chars, with a visible dropped count | `task_state.rs` constants |
| O3 | `is_empty()` suppresses an empty ledger, so the summarizer prompt is byte-identical to baseline when nothing is recognised | `prepare_compaction`, `(!ledger.is_empty()).then_some(ledger)` |
| O4 | the evidence excerpt can exceed `max_chars` by the marker text (the unit test allows 2000 + 400) | `utils.rs`, `truncate_for_summary_keeps_middle_evidence_that_head_and_tail_drop` |

## 4. Decision

Ship the two fixes (D3, D15 text) because both are correctness changes with tests, and they
do not alter behaviour when the condition is absent. Do not claim D4 is resolved: the
ledger mitigates it only when `taskState = "prompt"`, and the shipped default is `off`.
D1 and D2 remain the highest-severity open items for user-visible damage: they can stop a
turn and compact nothing, or compact and not resume.
