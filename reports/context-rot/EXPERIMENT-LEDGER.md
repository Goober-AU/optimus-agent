# EXPERIMENT LEDGER — every arm in the campaign, its size, its result, its decision

One row per run artefact that exists. Everything else is listed as NOT EXECUTED with the
reason. Numbers are copied from `runs/summary.json` or from the JSONL rows named in the
`source` column; nothing here is extrapolated.

## 1. Runs

| id | question | arms | cells | result | source |
| --- | --- | --- | --- | --- | --- |
| smoke | does the endpoint answer a plain needle? | 1 (exact; 2 lengths x 2 positions) | 4 | 4 / 4 | `runs/smoke/niah.jsonl` |
| C1 main | does accuracy degrade with length, position or needlet type? | 1 | 105 | 105 / 105 | `runs/c1/niah-main.jsonl` |
| C1 distract | can the model reject a distractor? | 2 (`related`, `contradictory`) | 42 | `related` 21 / 21; `contradictory` 0 / 21 and NOT SCORABLE | `runs/c1/niah-distract.jsonl` |
| E1 | do 12 planted facts survive 3 compaction cycles? | 2 (baseline, ledger) | 6 compactions + 144 probes | 71 / 72 per arm, pooled over two readouts | `runs/e1/compact.jsonl` |
| E2 | do 32 probes survive 3 cycles without drift? | 2 (baseline, ledger) | 6 compactions + 192 probes | 96 / 96 per arm (negative result) | `runs/e2/compact.jsonl` |
| E4 | does the tool excerpt keep middle evidence? | 2 (old, new excerpt) | 2 compactions + 8 probes | 0 / 4 old, 4 / 4 new | `runs/e4/tool.jsonl` |

Totals: 509 recorded model calls (165 of them record `usage`), 7,889,358 prompt plus
completion tokens across the calls that record usage. The 344 probe calls record no usage,
so the real token total is higher and is not reported here.

## 2. Per-experiment decision lines

| id | decision | basis |
| --- | --- | --- |
| smoke | KEEP as the endpoint reachability check | 4 / 4, first real-model evidence that the endpoint and the extraction rule work |
| C1 main | KEEP as a saturation regression gate | 105 / 105 at up to 218969 actual prompt tokens; cannot detect an improvement, only a regression |
| C1 distract | REVERT the `contradictory` arm, KEEP `related` | no unique gold exists in the contradictory arm; all 21 answers are deliberation text cut at the 200-token cap |
| E1 | OPT-IN (`compaction.taskState = "off"` default) | one-item differences at n=12 per cell, no reliable gain, +608-618 estimated tokens per request |
| E2 | OPT-IN (`off` default), ledger recall claim recorded as NEGATIVE | 96 / 96 in both arms at every cycle; zero measured gain, +913 to +1751 prompt tokens per cycle and 15-57 % longer summaries |
| E4 | KEEP, always on, no setting | 0 / 4 to 4 / 4 with 106 fewer prompt tokens and 0.6 s less latency; the mechanism is verifiable without a model |

## 3. Design facts that apply to all runs

- One model, one endpoint: `dgx-deepseek41/deepseek-v4.1-flash-1017`. No second model.
- One seed per item, `reps = 1` everywhere. No cell has a repeated sample, so no cell can
  separate a one-item difference from decoding noise.
- `temperature` is never sent; the endpoint default applies.
- Answer extraction takes the text after the last `</think>` delimiter; when the delimiter
  is absent the drivers fall back to the last line. E1/E2/E4 do not record which rule fired.
- Item seeds come from Python `hash()` of a tuple of strings and are salted per process, so
  an item is only reproducible from the `seed` recorded in its JSONL row (`BUG-LEDGER.md` M5).
- Compaction arms share the production summarizer wire shape, the 13107-token output budget
  and the same transcript bytes per cycle; only the appended ledger or the excerpt policy
  differs.
- Lab drivers live outside the tree at `C:/Users/openclawuser/optimus-rot-lab/exp/` and
  `.../probe/`. The in-tree `research/context_rot/` harness produced only its own selftest
  run; it did not produce these artefacts.

## 4. NOT EXECUTED

| planned arm | reason | what it would have answered |
| --- | --- | --- |
| E3 | no run artefact exists (`runs/` holds `c1`, `e1`, `e2`, `e4`, `smoke`); intended scope not recorded in the artefacts | unknown; cannot be reconstructed from data |
| authority-rule distractor arm (replacement for `contradictory`) | not built | whether the model follows "later statement supersedes" when a unique gold exists |
| stress suite A..T with a real model | the suite exists in `research/context_rot/stress.py` with offline unit-test coverage; no real-model run of it was produced | categories E/F (evidence in bash output, evidence in a diff), M (repeated summaries), T (restart after repeated compaction) |
| repeated sampling (`reps` > 1) or more probes per cell | not scheduled | whether the E1 one-item losses are real or decoding noise |
| memory recall experiment | not scheduled | D13 behaviour: does a foreign `projectId` silently remove the recall block |
| Jev retrieval/compaction experiment | not scheduled | whether Jev's drop set removes evidence the task needed |
| second model or endpoint | not scheduled | any cross-model statement. None is made in these reports |
| official benchmark scorers | no scorer, no redistributable corpus, no task harness | comparability with Chroma, NoLiMa, RULER, LongBench, CompactBench, RepoQA, SWE-bench or Terminal-Bench. None is claimed |

## 5. Reuse and cost summary

- C1 is expensive (7.04M of the 7.89M recorded tokens) and saturated. Future regression use
  should keep only the boundary cells: 128k nominal at positions 0.0 and 0.5, plus one
  distractor cell.
- E1/E2 are cheap: 0.76M recorded tokens for 12 summarizer calls (the 336 probe calls record
  no usage). They are the right place to add repetition and harder fixtures.
- E4 costs 18,886 prompt tokens for a decisive answer and should be repeated per release
  rather than per campaign.
