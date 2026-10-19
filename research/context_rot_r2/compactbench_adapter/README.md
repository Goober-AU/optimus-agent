# CompactBench Python adapter: OptimusRustCompactor (rot2 W6 -> W12)

Python-side adapter that drives the REAL Optimus Rust compaction pipeline
through the committed harness binary
(`crates/pi-coding-agent/src/bin/compaction_harness.rs`) under the official
CompactBench runner, plus the offline validation runs. Since W12 the adapter
runs in DELTA-APPEND mode against the fixed binary (commit 49f4738d8d:
`SessionManager::continue_recent`, usage anchors, flush-on-append).

Design source: `research/context_rot_r2/COMPACTBENCH-ADAPTER-DESIGN.md`
(W5). This adapter implements design sections 2.3-2.5: one persistent
per-case session dir, delta appends, repeated compaction of the same session.

## Files

| file | purpose |
| --- | --- |
| `optimus_compactor.py` | the adapter: `OptimusRustCompactor` (+ task-state/plain arms, delta-append) and the two controls |
| `test_optimus_compactor.py` | offline contract checks (run with the cb-venv python) |
| `probe_harness_behavior.py` | reproduces the harness findings F1-F5, D1 below |
| `results/*.jsonl` | the W6 full-transcript validation runs (superseded mode, kept for the record) |
| `results/w12/<suite>/<arm>.jsonl` | the W12 practice-suite runs (delta mode); see `SUITE-RESULTS.md` |
| `SUITE-RESULTS.md` | the full practice-suite report (starter + elite_practice, all arms) |

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
owns one persistent session dir (the "live" dir) under a per-instance temp
root. Each `compact()` call:

1. filters the transcript to user/assistant turns (the harness accepts only
   those; system turns are dropped with a warning),
2. reads the live dir's `.rot2-turn-map.json` sidecar and checks it is an
   id-and-content prefix of the incoming transcript: if so, only the missing
   turns are appended (delta); otherwise a fresh session dir is opened and
   the full transcript re-sent (resync),
3. walks the cut-point ladder (below); before every attempt it re-reads the
   sidecar, so an attempt whose appends landed but whose compaction failed is
   retried with zero new turns instead of duplicating them,
4. on `status: ok` with a non-empty summary and no degenerate warning, builds
   the artifact: `summaryText` via `fit_summary_text`, `structured_state`
   from the response ledger (mapping below), `selectedSourceTurnIds =
   summarizedTurnIds + keptTurnIds` (cumulative: the harness lists every
   turn folded so far, which is exactly what the artifact transitively
   contains), harness warnings passed through,
5. if no attempt lands, falls back to rendering the un-compacted transcript
   as the artifact, with a loud warning. Never silent.

`previous_artifact` is ignored by design: the harness session carries its
own compaction state (summary + kept tail + ledger) across calls, and that
state — not a re-rendered artifact — is what the next compaction sees. This
is the repeated-compaction semantics the benchmark's drift metric measures.

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

The harness sizes the kept tail by walking backwards: message `j` joins the
tail iff the estimate of the tail without it (`cum[j+1]`, `chars/4 + 0.6`
per message) is `<= keepRecentTokens`, and the pipeline only accepts cuts
that immediately precede a USER turn (F2). `tail_candidates()` mirrors the
estimate and returns, for each user turn `j` with a non-empty summarize
window `[0, j)`, the midpoint of `cum[j]` and `cum[j+1]` — the value that
includes exactly `[j, end)` in the tail. Candidates are tried most aggressive
(shortest tail) first, capped at 10 spawns per call; the used value and every
failed attempt are recorded in `methodMetadata`, and a multi-attempt landing
is surfaced as a warning. On a session with a previous compaction the walk
clamps at the previous `firstKeptEntryId`, so an oversized candidate keeps
the whole live window (degenerate, treated as not-landed) rather than
re-folding old turns.

## Harness findings (all reproduced by `probe_harness_behavior.py`)

Validated against the FIXED binary (49f473d8d). Findings marked REVERSED
contradict the W6 probe that ran against the pre-fix binary.

- **F1 [REVERSED] Sessions persist across invocations.** `continue_recent`
  reopens the most recent session file in the session dir (falling back to a
  new one); entries accumulate and the turn-map sidecar stays in sync. F1'
  (new): the session entry's `cwd` must equal the session dir, so a COPIED
  session dir is not continuable — a naive clone silently forks a fresh
  session. The adapter uses one stable live dir per case and never clones.
- **F2 The cut must immediately precede a user turn.** A cut that lands
  before an assistant turn makes the pipeline reject the summarizer handoff:
  `status: error`, "Summarization returned an unusable handoff ...". On the
  starter shape, `keepRecentTokens` 20 and 40 both fail while 30 works.
- **F3 Tail sizing, refined.** Message `j` joins the kept tail iff
  `cum[j+1] <= keepRecentTokens` (`chars/4 + 0.6` per message, measured from
  the end). Predicted tails matched `keptTurnIds` exactly on the starter
  templates and on delta sessions (probe F3 threshold sweep), including the
  clamp at the live-window start.
- **F4 [REVERSED] Token accounting is non-zero with the fixture.**
  `tokensBefore` and `compactedContextTokens` come back positive because
  assistant turns carry a conservative `ceil(chars/4)` usage anchor; the
  estimator anchors on the last assistant usage and undercounts prior user
  turns. Reported in `methodMetadata` as-is; not used for decisions.
- **F5 [REVERSED] Appends are durable on every path.** The harness flushes
  the appended turns and stores the turn map BEFORE running compaction, so an
  error-path exit leaves the session holding the new turns. This is what
  makes same-dir ladder retries safe (re-read the sidecar, resend nothing).
  Kill/crash paths (adapter timeout, no stdout) and a failed flush still
  leave an unknown state; the adapter abandons the dir for those.
- **F6 Ledger lines carry per-session entry hashes.** Summary lines look
  like `- C1 [940ab4db]: ...` where the hash is the session entry id, which
  differs on every invocation. Output is deterministic modulo those hashes
  (see `test_determinism`, which masks them).
- **D1 Delta-append round-trips.** Across sequential calls on one session
  dir: `summarizedTurnIds + keptTurnIds` partition the cumulative turn map
  at the cut (which precedes a user turn), the ledger carries constraints
  forward across calls, the summary embeds the decayed previous summary,
  and one session file serves the whole case.

## Mode decision (W12): delta-append

The W6 adapter sent the full transcript to a fresh session dir per call
because the pre-fix binary could not persist sessions (old F1). With F1
fixed, delta-append is the scientifically correct mode (design 2.3): each
cycle re-compacts the LIVE session — previous summary, kept tail, ledger —
so drift compounds through summary-of-summary decay. Full-transcript mode
re-summarized the raw transcript every cycle, measuring per-cycle lossiness
instead. Evidence for the switch and the round-trip verification: README
findings above, `SUITE-RESULTS.md` part 2, and the delta/retry/resync checks
in `test_optimus_compactor.py`.

## Environment knobs

| variable | default | effect |
| --- | --- | --- |
| `HARNESS` / `ROT2_HARNESS_BIN` | shared debug build | harness binary path (must be a build with 49f4738d8d for delta mode) |
| `ROT2_RESERVE_TOKENS` | `4000` | passed through as `settings.reserveTokens` |
| `ROT2_KEEP_RECENT_TOKENS` | unset (auto ladder) | pin the value: exactly one attempt, then fallback |
| `ROT2_HARNESS_TIMEOUT_S` | `120` | per-attempt subprocess timeout |
| `ROT2_ECHO_FIRST_N` | `10` | `n` for `EchoFirstNCompactor` |

## Validation runs (CUSTOM, mock provider, offline)

Adapter checks: **13/13 passed** (delta round trip, three-cycle partition,
retry-after-failure, resync, plus the W6 contract checks), and all probe
checks pass, against the worktree build of the fixed binary:

```
CBV=C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv/Scripts/python.exe
export HARNESS=C:/Users/openclawuser/rot2-w12-target/debug/compaction-harness.exe

$CBV research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py
python  research/context_rot_r2/compactbench_adapter/probe_harness_behavior.py
```

The full practice-suite runs (starter + elite_practice, 9 arms, 3 drift
cycles, delta mode) are reported in `SUITE-RESULTS.md` with commands. The
W6 validation table below is kept for the record; it ran the superseded
full-transcript mode (2 cases/template, 1 drift cycle, pre-fix binary), so
its numbers are not comparable to the w12 runs.

Results (8 cases = 2 per template, 2 cycles each, scorer 2.0.0, superseded
full-transcript mode, pre-fix binary):

| arm | overall | drift_res | constraint_ret | contradiction | compression | eval prompt tokens |
| --- | --- | --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.375 | 1.000 | 0.400 | 0.000 | 0.54x | 17,777 |
| optimus-rust-plain | 0.375 | 1.000 | 0.400 | 0.000 | 0.64x | 15,881 |
| echo-first-n | 0.375 | 1.000 | 0.400 | 0.000 | 0.80x | 14,071 |
| full-transcript-oracle | 0.375 | 1.000 | 0.400 | 0.000 | 0.80x | 14,071 |
| built-in:null | 0.375 | 1.000 | 0.400 | 0.000 | 101.00x | 7,522 |

Reading the table:

- **All quality scores are degenerate by construction.** With
  `--provider mock` the evaluation answers are constant (the mock returns a
  fixed string regardless of the prompt), so every arm including null and
  the oracle scores the same. Per design doc 2.6 these runs validate plumbing
  only. No quality claim is made or possible offline.
- **Every Rust-arm cycle ran the real pipeline.** In the w12 runs, zero
  method warnings across all 1,920 custom-arm cycles: every `compact()`
  call landed a valid cut on the first ladder candidate, no fallbacks, no
  "unusable handoff" errors.
- The mock-independent observables do separate the arms: compression and
  artifact token mass. The task-state arm's artifacts are larger than the
  plain arm's (ledger lines in the summary plus the structured-state
  section). Compression below 1x is a small-transcript effect: the practice
  cases run short transcripts, so the fixture summary's fixed six-section
  boilerplate plus the lossy excerpts plus the duplicated ledger text can
  exceed the source size. Nothing was tuned to hide it.

## Labeling rules

These are CUSTOM runs: public practice suites, mock eval provider
(`mock-deterministic`), scorer 2.0.0, custom adapter methods, custom
delta-append mode (w12) or full-transcript mode (w6, superseded). They are
not CompactBench leaderboard numbers, not comparable to any 1.0.0-scored
run, and not usable as quality evidence for Optimus compaction (mock
evaluation). Model-dependent quality claims remain BLOCKED for this campaign
per the offline rules; the harness is ready for a model-backed run when one
is authorized.
