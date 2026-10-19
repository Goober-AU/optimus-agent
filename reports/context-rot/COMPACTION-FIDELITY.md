# COMPACTION FIDELITY — E1: do planted facts survive a summary, three cycles deep?

Question: after a model-written compaction summary, can the session still answer questions
about specific facts (paths, counts, error codes, superseded decisions)? Does a
deterministic task-state ledger change that?

## 1. Design

- Fixture: one synthetic optimus-agent session with 10 fact probes (`q1` to `q10`), one
  not-stated trap (`q11_not_stated`, correct answer "NOT STATED") and one superseded-fact
  probe (`q12_replaced`, the sidecar that was replaced by inline checkpoints). 12 probes.
- Three sequential compactions per arm. Between cycles the session grows with a batch of
  `cargo test -p pi-coding-agent --lib` tool results (18 blocks per cycle) plus new turns.
- The summarizer call uses the production wire shape: system = `SUMMARIZATION_SYSTEM_PROMPT`,
  user = `<conversation>...</conversation>` then `<previous-summary>` then the instruction
  block and `KERNEL_PERSIST_SUMMARY_NOTE`. Output budget `floor(0.8 * 16384) = 13107`.
- Arms: `baseline` = production prompt selection for `summary_update_policy = "off"`
  (`UPDATE_SUMMARIZATION_PROMPT` from cycle 2). `ledger` = the consolidating prompt plus the
  rendered ledger (608-618 estimated tokens).
- Probes are asked of the summary alone, and of the summary plus a verbatim tail: the last 6
  messages at cycle 1, then the last ~80,000 characters (about 20,000 tokens, the default
  `keep_recent_tokens`) retained per cycle. The second readout exists because the retained
  tail is the other place a fact can live.
- Source: `runs/e1/compact.jsonl` (6 compaction records + 144 probe records),
  `summary.json` `e1_compactions`, `e1_scores`.

## 2. Cost of each cycle (measured, per-arm inputs are identical)

| arm | cycle | input chars | prev summary chars | ledger tok est | prompt tok | completion tok | summary chars | latency s | 6 sections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1 | 244959 | 0 | 0 | 87072 | 2194 | 3119 | 9.0 | yes |
| baseline | 2 | 150475 | 3119 | 0 | 54503 | 2125 | 4242 | 10.1 | yes |
| baseline | 3 | 149200 | 4242 | 0 | 54184 | 2867 | 5189 | 12.6 | yes |
| ledger | 1 | 244959 | 0 | 608 | 87827 | 1768 | 3930 | 10.7 | yes |
| ledger | 2 | 150475 | 3930 | 615 | 55758 | 5036 | 4770 | 21.9 | yes |
| ledger | 3 | 149200 | 4770 | 618 | 55372 | 2698 | 5593 | 10.2 | yes |

`input chars` is byte-identical per cycle across arms, so the summarizer saw the same
transcript; the difference is the appended ledger and the longer carried summary. Measured
prompt-token deltas: +755 (c1), +1255 (c2), +1188 (c3). All six summaries contained the six
required headings (`sections_ok = true`), so the ledger does not break the format contract.

## 3. Fact recall by cycle (graded, from `summary.json` `e1_scores`)

| arm | c1 summary | c2 summary | c3 summary | c1 +tail | c2 +tail | c3 +tail |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 12/12 | 12/12 | 11/12 | 12/12 | 12/12 | 12/12 |
| ledger | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 11/12 |

Pooled over both readouts each arm scores 71 / 72. Each arm loses exactly one item, in a
different cell, for a different reason:

| arm | cell | probe | answer recorded | diagnosis |
| --- | --- | --- | --- | --- |
| baseline | cycle 3, summary | `q6_test_count` | `583 passed; 0 failed; 0` | wrong fact selected: the question asks for the lib count (4785); the fixture also holds a `runtime_suite` result of 583, and by cycle 3 the model picked the near-miss |
| ledger | cycle 3, summary+tail | `q10_remaining` | `We need answer one short line listing remaining work. ...` | no delimiter within the cap; the driver kept the first line, so no gold token was present. An extraction artefact, not a lost fact |

## 4. Scorer tolerance differs between the two recorded matchers

The lab driver's raw per-probe flag uses a plain substring match plus a guard that rejects
`q5_checkpoint_store` answers mentioning "sidecar" outside the exact phrase "inline in the
transcript under a versioned key". Under that raw flag every `q5` answer fails (12 of 12
cells, both arms), because the model always adds a paraphrase such as "(sidecar withdrawn)".
Raw flags: baseline summary 11, 11, 10; ledger summary 11, 11, 11; both tail readouts 11,
11, 11. The graded numbers in `summary.json` accept those paraphrases. Both matchers agree
on the two genuine single-item misses above. Reported headline uses the graded numbers
because `summary.json` is the authoritative aggregate; the raw flags are quoted here so the
tolerance is not hidden.

## 5. What E1 shows

- The model-written summary retains 11-12 of 12 planted facts per cell at every cycle.
  Nothing like wholesale loss occurs at 3 cycles on this fixture.
- All differences are one item at n=12 per cell. With no repeated sampling, a one-item
  difference is not statistically meaningful, and the design cannot separate it from
  decoding noise. Do not claim the ledger improved recall.
- The one baseline miss is the interesting failure mode: near-miss fact selection (583
  runtime-suite count instead of the 4785 lib count). That is exactly the confusion the
  ledger's `VERIFIED EVIDENCE` block is built to prevent. One observation is not evidence
  that it does prevent it.
- The ledger costs 608-618 estimated tokens per summarizer request and lengthens the
  summary (3930 to 5593 chars versus 3119 to 5189).

## 6. Decision

E1 ledger arm: OPT-IN, default `off` (`compaction.taskState = "off"` is the shipped
default). It costs tokens, buys no measurable recall at n=12 per cell, and its one
defensible benefit (exact evidence lines carried verbatim) is better tested by E4-shaped
fixtures than by this one.

E1 as a gate: KEEP the fixture. Raising the probe count per cell (for example 40 facts and
3 repetitions per cell) is the cheapest way to turn this from a smoke test into a
regression gate.
