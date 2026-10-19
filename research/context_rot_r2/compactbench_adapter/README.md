# CompactBench Python adapter: OptimusRustCompactor (rot2 W6)

Python-side adapter that drives the REAL Optimus Rust compaction pipeline
through the committed harness binary
(`crates/pi-coding-agent/src/bin/compaction_harness.rs`, built at
`C:/Users/openclawuser/rot2-shared-target/debug/compaction-harness.exe`) under
the official CompactBench runner, plus the offline validation runs.

Design source: `research/context_rot_r2/COMPACTBENCH-ADAPTER-DESIGN.md`
(W5). This adapter implements design sections 2.3-2.5 with the deviations
forced by the committed binary, documented below.

## Files

| file | purpose |
| --- | --- |
| `optimus_compactor.py` | the adapter: `OptimusRustCompactor` (+ task-state/plain arms) and the two controls |
| `test_optimus_compactor.py` | offline contract checks (run with the cb-venv python) |
| `probe_harness_behavior.py` | reproduces the harness findings F1-F3 below |
| `results/*.jsonl` | the validation run outputs (CUSTOM runs, mock provider) |

## Arms

| class | `name` | settings.taskState | what it is |
| --- | --- | --- | --- |
| `OptimusRustCompactorTaskState` | `optimus-rust-task-state` | `prompt` | real pipeline, task-state ledger on |
| `OptimusRustCompactorPlain` | `optimus-rust-plain` | `off` | real pipeline, ledger off |
| `EchoFirstNCompactor` | `echo-first-n` | n/a | control: first N turns verbatim (head-truncation floor) |
| `FullTranscriptOracleCompactor` | `full-transcript-oracle` | n/a | control: full transcript verbatim (ceiling) |

`built-in:null` (empty artifact) is run from the runner itself as the floor.

## How it works

Per benchmark case the runner constructs one fresh compactor instance, which
owns a per-instance temp root. Each `compact()` call:

1. filters the transcript to user/assistant turns (the harness accepts only
   those; system turns are dropped with a warning),
2. computes `keepRecentTokens` candidates that land a valid cut (below),
3. spawns the harness with the FULL transcript in a fresh session dir
   (`call-NNN-attempt-MM`) and settings `{taskState: <arm>, reserveTokens:
   4000, contextWindow: 1e6, maxTokens: 64e3, summarizer: "fixture"}`,
4. on `status: ok` with a non-empty summary, builds the artifact:
   `summaryText` via `fit_summary_text`, `structured_state` from the response
   ledger (mapping below), `selectedSourceTurnIds = summarizedTurnIds +
   keptTurnIds`, harness warnings passed through,
5. if no attempt compacts (all error/no-prepare), falls back to rendering the
   un-compacted transcript as the artifact, with a loud warning. Never silent.

`previous_artifact` is ignored: every call rebuilds the Rust session from the
full raw transcript, so there is no carried artifact to merge (see F1).

### Ledger -> StructuredState mapping (design 2.4)

| ledger field | contract section |
| --- | --- |
| `constraints` | `forbidden_behaviors` |
| `decisions` | `locked_decisions` |
| `superseded` | `deferred_items` |
| `evidence` + `anchors` | `immutable_facts` |
| `openWork` | `unresolved_items` |
| `entity_map` | left empty (pipeline produces none) |
| `retractions` | dropped (no contract section exists) |

Ledger entries are `{"id", "text", "entry"}` dicts; `anchors` are bare
strings. The adapter tolerates bare strings everywhere and clips every text to
the contract bounds (500 chars, 200 items per section). With `taskState:
"off"` the harness returns `ledger: null` and the state is empty.

### The cut-point ladder

The harness sizes the kept tail by extending backwards while a per-message
token estimate stays under `keepRecentTokens`, and the pipeline only accepts
cuts that immediately precede a USER turn (F2). A fixed value is therefore
fragile. `tail_candidates()` mirrors the estimate (`chars/4 + 0.6` per
message, F3) and returns, for each user turn `j` with a non-empty summarize
window `[0, j)`, the midpoint of the cumulative estimates of `[j, end)` and
`[j+1, end)` — the value that stops the tail exactly at `j`. Candidates are
tried most aggressive (shortest tail) first, capped at 10 spawns per call;
the used value and every failed attempt are recorded in `methodMetadata`,
and a multi-attempt landing is surfaced as a warning.

## Harness findings (all reproduced by `probe_harness_behavior.py` unless noted)

- **F1 Sessions do not persist across invocations.** `SessionManager::create`
  starts a NEW session file in the session dir on every invocation; the
  previous session and its compaction entry are never reopened. Consequence:
  the design-doc 2.3 plan (one per-case session dir, delta appends, session
  state carries forward) is not implementable against this binary. The
  adapter sends the FULL transcript to a fresh session dir per call instead,
  so the Rust session equals the live conversation at every cycle. The
  `.rot2-turn-map.json` sidecar DOES accumulate across calls, which
  desynchronizes `keptTurnIds` from the session after the first invocation.
- **F2 The cut must immediately precede a user turn.** A cut that lands
  before an assistant turn makes the pipeline reject the summarizer handoff:
  `status: error`, "Summarization returned an unusable handoff ...". On the
  starter shape, `keepRecentTokens` 20 and 40 both fail while 30 works.
- **F3 Tail sizing is `~chars/4 + 0.6` per message.** Predicted tails matched
  `keptTurnIds` exactly on all four starter templates (see probe F3 output).
- **F4 Token accounting reads zero with the fixture.** `tokensBefore` and
  `compactedContextTokens` come back 0.0 because the estimator is
  usage-based and the harness appends assistant turns with `usage.input = 0`.
  Reported in `methodMetadata` as-is; not used for decisions.
- **F5 Error-path appends are not fully flushed.** After an error response
  the session file held only 2 of 8 appended messages while the turn-map
  sidecar held all 8 (partial flush before `exit(1)`; the ok/no-prepare path
  flushes on drop). Fresh session dirs per attempt make this irrelevant
  here, but it rules out "retry in the same dir" strategies.
- **F6 Ledger lines carry per-session entry hashes.** Summary lines look
  like `- C1 [940ab4db]: ...` where the hash is the session entry id, which
  differs on every invocation. Output is deterministic modulo those hashes
  (see `test_determinism`, which masks them).

## Environment knobs

| variable | default | effect |
| --- | --- | --- |
| `HARNESS` / `ROT2_HARNESS_BIN` | shared debug build | harness binary path |
| `ROT2_RESERVE_TOKENS` | `4000` | passed through as `settings.reserveTokens` |
| `ROT2_KEEP_RECENT_TOKENS` | unset (auto ladder) | pin the value: exactly one attempt, then fallback |
| `ROT2_HARNESS_TIMEOUT_S` | `120` | per-attempt subprocess timeout |
| `ROT2_ECHO_FIRST_N` | `10` | `n` for `EchoFirstNCompactor` |

## Validation runs (CUSTOM, mock provider, offline)

Exact commands, issued from the worktree root
(`C:/Users/openclawuser/optimus-rot2-w6-cbadapter`) with the campaign venv
`C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv`:

```
CBV=C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv/Scripts/python.exe
BENCH=C:/Users/openclawuser/optimus-context-rot-refs/compactbench/benchmarks/public
M=research/context_rot_r2/compactbench_adapter/optimus_compactor.py

$CBV -m compactbench run --method $M:OptimusRustCompactorTaskState \
  --suite starter --provider mock --model mock-deterministic \
  --case-count 2 --drift-cycles 1 --benchmarks-dir $BENCH \
  -o research/context_rot_r2/compactbench_adapter/results/task-state.jsonl
$CBV -m compactbench run --method $M:OptimusRustCompactorPlain     [ ... -o results/plain.jsonl ]
$CBV -m compactbench run --method $M:EchoFirstNCompactor            [ ... -o results/echo-first-n.jsonl ]
$CBV -m compactbench run --method $M:FullTranscriptOracleCompactor  [ ... -o results/full-transcript-oracle.jsonl ]
$CBV -m compactbench run --method built-in:null                    [ ... -o results/null.jsonl ]

$CBV -m compactbench score --results research/context_rot_r2/compactbench_adapter/results/<arm>.jsonl
```

Adapter checks (9/9 passed):

```
$CBV research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py
python  research/context_rot_r2/compactbench_adapter/probe_harness_behavior.py
```

Results (8 cases = 2 per template, 2 cycles each, scorer 2.0.0):

| arm | overall | drift_res | constraint_ret | contradiction | compression | eval prompt tokens |
| --- | --- | --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.375 | 1.000 | 0.400 | 0.000 | 0.54x | 17,777 |
| optimus-rust-plain | 0.375 | 1.000 | 0.400 | 0.000 | 0.64x | 15,881 |
| echo-first-n | 0.375 | 1.000 | 0.400 | 0.000 | 0.80x | 14,071 |
| full-transcript-oracle | 0.375 | 1.000 | 0.400 | 0.000 | 0.80x | 14,071 |
| built-in:null | 0.375 | 1.000 | 0.400 | 0.000 | 101.00x | 7,522 |

Reading the table:

- **All quality scores are degenerate by construction.** With
  `--provider mock` the evaluation answers are canned strings, so every arm
  including null and the oracle scores the same 0.375. Per design doc 2.6
  these runs validate plumbing only. No quality claim is made or possible
  offline.
- **Every Rust-arm cycle ran the real pipeline.** Zero method warnings
  across all 16 cycles of both Rust arms: every `compact()` call landed a
  valid cut on the first ladder candidate, no fallbacks, no "unusable
  handoff" errors.
- The mock-independent observables do separate the arms: compression and
  artifact token mass. The task-state arm's artifacts are larger than the
  plain arm's (ledger lines in the summary plus the structured-state
  section; 0.54x vs 0.64x). Compression below 1x is a small-transcript
  effect: starter cases run ~8 short turns, so the fixture summary's fixed
  six-section boilerplate plus the lossy excerpts plus the duplicated
  ledger text exceed the source size. Real (longer) transcripts would not
  behave this way; nothing was tuned to hide it.

## Labeling rules

These are CUSTOM runs: public `starter` suite, mock eval provider
(`mock-deterministic`), scorer 2.0.0, custom adapter methods. They are not
CompactBench leaderboard numbers, not comparable to any 1.0.0-scored run,
and not usable as quality evidence for Optimus compaction (mock evaluation).
Model-dependent quality claims remain BLOCKED for this campaign per the
offline rules; the harness is ready for a model-backed run when one is
authorized.
