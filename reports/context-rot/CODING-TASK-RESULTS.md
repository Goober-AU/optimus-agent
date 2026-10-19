# CODING-TASK RESULTS — E4: does the summarizer keep the evidence it excerpts?

Question: when a tool result is longer than the summarizer's 2000-character excerpt budget,
does the excerpt keep the failure evidence that sits in the middle? This is the coding-task
question the whole campaign reduces to, because a coding session's decisive facts are
usually a test total, a failing test name, or a compiler error inside a long log.

## 1. Design

- Fixture: 12 `cargo test -p pi-coding-agent` results, each wrapped in an assistant tool
  call and a tool result. Run #7 carries the only record of the failure, placed in the
  middle of its output: `test result: FAILED. 4782 passed; 3 failed; 7 ignored`,
  `failures: session_manager::tests::restores_after_compaction`, and
  `error[E0599]: no method named rehydrate found for struct SessionStore`.
- Both arms: identical summarizer system prompt, identical instruction block, identical
  2000-character excerpt budget, identical probes. Only the excerpt policy differs.
- Probes (4): which test failed, how many passed, how many failed, which compiler error.
- Source: `runs/e4/tool.jsonl` (2 arm records + 8 probe records), `runs/summary.json` `e4`.

## 2. The mechanism, verified without a model

Run #7's tool body is 7033 characters. The failure line sits at character 3421, which is
49 % into the body. The two policies produce:

| property | old policy (head/tail) | new policy (evidence recovery) |
| --- | --- | --- |
| excerpt length | 2000 chars | 1769 chars |
| head kept | 1431 chars | 954 chars (477 chars reserved for middle evidence) |
| middle evidence lines kept | 0 | 4 |
| marker text | "5102 characters truncated; first 1431 and last 500 kept" | "5387 characters truncated; 4 evidence line(s) from the omitted middle kept ... end of evidence excerpt" |
| contains `4782 passed` | no | yes |
| contains `test result: FAILED` | no | yes |
| contains `restores_after_compaction` | no | yes |
| contains `E0599` | no | yes |

The information is absent from the old conversation and present in the new one. That is a
deterministic property of the policy, so the probe result below needs no probabilistic
explanation. Implementation: `truncate_for_summary` in `compaction/utils.rs` reserves at
most a third of the head window for evidence-bearing lines matched by `EVIDENCE_LINE_RE`,
pulls the indented continuation lines that name the failing test or source position, and
shrinks the head to pay for it. Text with no middle evidence is byte-identical to the old
output (test `truncate_for_summary_is_unchanged_without_middle_evidence`).

## 3. Result

| arm | input chars | prompt tokens | completion tokens | latency s | summary chars | probes correct |
| --- | --- | --- | --- | --- | --- | --- |
| old (head/tail) | 25865 | 9496 | 1463 | 4.0 | 2825 | 0 / 4 |
| new (evidence recovery) | 25634 | 9390 | 1181 | 3.4 | 2866 | 4 / 4 |

Per probe: `which_test_failed`, `how_many_passed`, `how_many_failed` and
`which_compile_error` all fail on the old arm and all pass on the new arm. The old arm's
four answers are all explicit abstentions, for example "NOT STATED - no `FAILED`/
`test result` line identifying a failing lib test appeared in the retained output of the 12
`cargo test -p pi-coding-agent` runs." That is the correct behaviour for the summary it was
given: the policy removed the information, the model did not invent it.

The new arm's answers are exact: `session_manager::tests::restores_after_compaction`,
`4782 passed`, `3 tests failed`, and
`error[E0599]: no method named rehydrate found for struct SessionStore`.

Costs move slightly in the new arm's favour: 106 fewer prompt tokens (9496 vs 9390), 0.6 s
less latency, and a summary 41 characters longer. The shorter input follows from the
smaller excerpt (1769 vs 2000 chars in the failing run).

## 4. Limits of this result

- One summarization per arm (n=1) and 4 probes. There is no repetition, so the 4/4 is one
  draw. The claim rests on the mechanism in section 2, not on the sample size: the old arm
  could not have answered any of the four probes from a summary written from text that did
  not contain the strings.
- The fixture is synthetic and single-error. A real repository session has many concurrent
  failures competing for the same evidence budget; the `and N further evidence line(s)
  beyond the excerpt budget` clause makes that competition visible but it was not measured.
- Only text lines are recovered. Image blocks are still dropped by the summarizer (audit
  D15/D16), so a screenshot that carried the answer remains unrepresentable.

## 5. Coding-shaped work NOT EXECUTED

| planned item | status | reason |
| --- | --- | --- |
| RepoQA needle-in-repository | NOT EXECUTED | no run artefact exists; no repository corpus in the lab; official scorer not run |
| SWE-bench / Terminal-Bench task runs | NOT EXECUTED | no run artefact exists; no task harness or subset in the lab; official scorer not run |
| stress suite categories A..T with a real model (includes E "evidence in long bash output" and F "evidence in a git diff") | NOT EXECUTED | the 20-category suite exists in `research/context_rot/stress.py` and the harness's 125 offline unit tests include stress-category coverage, but no real-model run was produced |
| E3 | NOT EXECUTED | no run artefact exists in the lab (`runs/` holds `c1`, `e1`, `e2`, `e4`, `smoke` only); its intended scope is not recorded here |

Every number in section 3 came from a synthetic fixture. No figure in this file is a
coding-benchmark score.

## 6. Decision

E4 evidence-recovery excerpt: KEEP, always on, no setting. It is the one measured,
decisive improvement in the campaign: 0/4 to 4/4 on the probes, with smaller input tokens
and lower latency, and it changes nothing when a tool result carries no middle evidence.
