# CompactBench public practice suites — W12 runs (CUSTOM)

Full public practice-suite runs of the Optimus compaction adapter against the
FIXED harness binary (commit 49f473d8d: `SessionManager::continue_recent`
session persistence, non-zero usage anchors, flush-on-append), re-validating
the W6 adapter and switching it to delta-append mode before the runs.

CUSTOM labeling: these are custom runs with the mock eval provider
(`mock-deterministic`, scorer 2.0.0) on the PUBLIC practice suites. They are
not CompactBench leaderboard numbers, not comparable to any 1.0.0-scored run,
and not usable as quality evidence for Optimus compaction. See "Caveats".

## Run configuration

| parameter | value |
| --- | --- |
| harness binary | built in this worktree from 49f473d8d+ (`C:/Users/openclawuser/rot2-w12-target/debug/compaction-harness.exe`, isolated target dir) |
| suites | `benchmarks/public/starter` (4 templates), `benchmarks/public/elite_practice` (20 templates) |
| case count | 5 per template (starter 20 cases, elite 100 cases — all cases, no subset) |
| drift cycles | 3 (4 compactions per case: 80 cycles/arm starter, 400/arm elite) |
| difficulty / seed group | medium / default (runner-derived blake2b seeds, deterministic) |
| provider / model | mock / mock-deterministic |
| arms | optimus-rust-task-state, optimus-rust-plain, echo-first-n, full-transcript-oracle (adapter 0.2.0, delta-append), built-in:null, naive-summary, structured-state, hierarchical-summary, hybrid-ledger |
| adapter mode | delta-append (see "Mode decision") |
| total wall clock | 73 s for all 18 runs (Rust arms: 4.1 s starter, 32.5 s elite task-state / 18.7 s elite plain) |

Exact commands (from the worktree root; `HARNESS` points at the fixed
binary, not the shared pre-fix debug build):

```
CBV=C:/Users/openclawuser/optimus-rot2-w5-research/refs/cb-venv/Scripts/python.exe
BENCH=C:/Users/openclawuser/optimus-context-rot-refs/compactbench/benchmarks/public
M=research/context_rot_r2/compactbench_adapter/optimus_compactor.py
export HARNESS=C:/Users/openclawuser/rot2-w12-target/debug/compaction-harness.exe

$CBV -m compactbench run --method $M:OptimusRustCompactorTaskState \
  --suite elite_practice --provider mock --model mock-deterministic \
  --case-count 5 --drift-cycles 3 --benchmarks-dir $BENCH \
  -o research/context_rot_r2/compactbench_adapter/results/w12/elite_practice/optimus-rust-task-state.jsonl
# likewise :OptimusRustCompactorPlain, :EchoFirstNCompactor,
# :FullTranscriptOracleCompactor, and built-in:null / built-in:naive-summary /
# built-in:structured-state / built-in:hierarchical-summary / built-in:hybrid-ledger,
# for --suite starter and --suite elite_practice.
$CBV -m compactbench score --results <file>
```

## Part 1 — harness-fix validation (all reproduced by `probe_harness_behavior.py`)

| finding | pre-fix (W6) | post-fix (this run) | evidence |
| --- | --- | --- | --- |
| F1 session persistence | `SessionManager::create` forks a new session file per invocation; delta-append unimplementable | **REVERSED**: `continue_recent` reopens the latest session in the dir; entries accumulate (`session, 8x message, compaction, 2x message, compaction`), the turn-map sidecar stays in sync, one session file across three calls | probe F1, D1 |
| F1' clone hazard | (not previously known) | the session entry's `cwd` must equal the session dir: a copied session dir silently forks a fresh session (probe F1 clone check) — the adapter uses one stable live dir per case and never clones | probe F1 |
| F2 cut alignment | cut must immediately precede a user turn | unchanged (20 and 40 error, 30 lands before turn 5) | probe F2 |
| F3 tail sizing | ~chars/4 + 0.6 per message | refined: message j joins the kept tail iff `cum[j+1] <= keepRecentTokens` (cum = chars/4 + 0.6 per message, measured from the end); in delta mode the walk CLAMPS at the previous compaction's firstKeptEntryId, so an oversized budget degrades to an empty summarize window, never to re-summarizing folded turns | probe F3 threshold sweep |
| F4 token accounting | tokensBefore/compactedContextTokens read 0.0 | **REVERSED**: non-zero (26.0 / 17.0 in the probe), anchored on the last assistant usage; conservative (undercounts prior user turns) | probe F4 |
| F5 flush discipline | error paths leave partial appends (2 of 8 persisted) | **REVERSED**: appends are flushed and the turn map stored BEFORE compaction; after an error exit the session holds the new turns, and a same-dir retry with `newTurns=[]` compacts the extended session | probe F5 |
| F6 entry hashes | ledger lines carry per-session entry hashes | unchanged (determinism test still masks them) | adapter determinism check |

Adapter checks: **13/13 pass** (`test_optimus_compactor.py`, including the new
delta round-trip, three-cycle partition, retry-after-failure and resync
tests); probe checks all pass. Full-transcript mode is gone from the adapter:
delta-append superseded it (below).

## Part 2 — mode decision: delta-append (design doc 2.3), evidence-based

**Decision: delta-append.** The adapter keeps one persistent per-case session
dir; each `compact()` call appends only the turns the session has not seen
(re-read from the turn-map sidecar before every attempt), and the harness
re-compacts the live session. `previous_artifact` stays ignored: the harness
session carries its own compaction state, which is exactly the state the next
compaction must see.

Evidence the round-trip is correct (brief requirement "verify keptTurnIds
bookkeeping across calls"):

1. **Turn bookkeeping partitions exactly.** Across three sequential calls
   (full transcript, then +2, then +2 continuation turns with continuing ids),
   `summarizedTurnIds + keptTurnIds` always equals the cumulative turn map,
   split prefix|suffix at the cut, and the cut always precedes a user turn
   (probe D1, adapter three-cycle check). `summarizedTurnIds` is cumulative
   (turns folded in earlier cycles remain listed as summarized), which is the
   correct semantics for `selectedSourceTurnIds` — the artifact transitively
   contains them via the carried summary.
2. **Ledger and summary carry forward.** The turn-2 constraint survives three
   compactions in the taskState ledger; the third summary embeds the decayed
   previous summary (`(decayed prior summary) ...`), i.e. real
   summary-of-summary drift is measurable — the thing full-transcript mode
   could not measure (it re-summarized the raw transcript every cycle).
3. **Retries are safe without clones.** F5's flush-before-compaction ordering
   means a failed attempt leaves the appends durable; the adapter re-reads the
   sidecar and retries with zero new turns (adapter retry check). The only
   paths that abandon a dir are kills/crashes (timeout, no stdout) and a
   failed flush, which could tear the flush-then-sidecar invariant.
4. **Overshoot is safe.** A keepRecentTokens beyond the live window clamps at
   the previous firstKeptEntryId (probe F3: K=500 keeps the whole window with
   an empty-window warning) — the ladder can never re-fold old turns.
5. **Full-transcript mode's premise is gone.** It existed only as the F1
   workaround ("sessions do not persist"); with F1 fixed it measures the wrong
   thing (per-cycle lossiness, not repeated-compaction drift).

Resync semantics (documented, exercised by the adapter checks): if the live
session's map is not an id-and-content prefix of the incoming transcript
(foreign transcript, torn state), the adapter opens a fresh session dir and
re-sends the full transcript; the next call continues in delta mode from
there.

## Part 3 — results

Scorer 2.0.0 aggregates per results file. "eval prompt tokens" is the run's
`token_usage.prompt_tokens` (the artifact-embedding eval prompts dominate it).

### starter (4 templates x 5 cases = 20 cases, 80 cycles/arm)

| arm | overall | drift_res | constraint_ret | contradiction | compression | eval prompt tokens |
| --- | --- | --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.3750 | 1.000 | 0.400 | 0.000 | 0.48x | 104,396 |
| optimus-rust-plain | 0.3750 | 1.000 | 0.400 | 0.000 | 0.55x | 95,502 |
| echo-first-n | 0.3750 | 1.000 | 0.400 | 0.000 | 0.83x | 75,354 |
| full-transcript-oracle | 0.3750 | 1.000 | 0.400 | 0.000 | 0.78x | 77,750 |
| built-in:null | 0.3750 | 1.000 | 0.400 | 0.000 | 108.76x | 38,993 |
| built-in:naive-summary | 0.3750 | 1.000 | 0.400 | 0.000 | 108.76x | 54,340 |
| built-in:structured-state | 0.3750 | 1.000 | 0.400 | 0.000 | 108.76x | 63,600 |
| built-in:hierarchical-summary | 0.3750 | 1.000 | 0.400 | 0.000 | 108.76x | 72,949 |
| built-in:hybrid-ledger | 0.3750 | 1.000 | 0.400 | 0.000 | 108.76x | 73,887 |

### elite_practice (20 templates x 5 cases = 100 cases, 400 cycles/arm)

| arm | overall | drift_res | constraint_ret | contradiction | compression | eval prompt tokens |
| --- | --- | --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.3326 | 0.900 | 0.375 | 0.000 | 0.74x | 527,183 |
| optimus-rust-plain | 0.3326 | 0.900 | 0.375 | 0.000 | 0.90x | 457,841 |
| echo-first-n | 0.3326 | 0.900 | 0.375 | 0.000 | 1.12x | 405,072 |
| full-transcript-oracle | 0.3326 | 0.900 | 0.375 | 0.000 | 0.79x | 492,386 |
| built-in:null | 0.3326 | 0.900 | 0.375 | 0.000 | 178.24x | 184,657 |
| built-in:naive-summary | 0.3326 | 0.900 | 0.375 | 0.000 | 178.24x | 298,206 |
| built-in:structured-state | 0.3326 | 0.900 | 0.375 | 0.000 | 178.24x | 344,521 |
| built-in:hierarchical-summary | 0.3326 | 0.900 | 0.375 | 0.000 | 178.24x | 443,922 |
| built-in:hybrid-ledger | 0.3326 | 0.900 | 0.375 | 0.000 | 178.24x | 432,785 |

### Reading the table

- **Every quality number is degenerate by construction.** The mock provider
  returns a constant empty string for every completion, so the eval answers do
  not depend on the artifact at all: per-case scores are bit-identical across
  all nine arms (verified: task-state == null == oracle on starter, plain ==
  hybrid-ledger on elite). The 0.375/0.3326 overall and the drift/constraint
  values are artifacts of each template's check-type mix (contains_normalized
  items score 0 on an empty answer, forbidden_absent items score 1). No quality
  claim is made or possible offline.
- **The built-in baselines' artifacts are empty too** (their summarizer calls
  hit the same mock and return empty strings), so their compression collapses
  to the null floor (108.76x / 178.24x) and their only arm separation is eval
  prompt overhead.
- **The mock-independent observables do separate the custom arms.** The
  task-state arm produces the largest artifacts (compression 0.48x starter /
  0.74x elite, i.e. the artifact is ~2.1x the transcript on starter and ~1.3x
  on elite: fixture boilerplate + ledger lines in the summary + the
  structured-state section),
  then plain (0.55x / 0.90x), then the oracle (0.78x / 0.79x, pure render
  overhead), then echo-first-n (0.83x starter; 1.12x elite — keeps the first
  10 turns verbatim, which on the longer elite transcripts is ~10% of the
  source). Compression below 1x remains a small-transcript effect (W6
  finding, unchanged).
- **Delta mode demonstrably engaged in the runs.** Per-cycle means (starter,
  task-state): eval prompt tokens per cycle 963 -> 1376 -> 1439 -> 1441
  (flattens: the artifact mass is constant across cycles — decayed prior
  summary capped at 400 chars + live-window excerpts + ledger) while the
  oracle's grows monotonically with the transcript (755 -> 987 -> 1044 ->
  1101); compression rises across cycles (0.431 -> 0.499) for the same reason;
  per-cycle latency drops (414 -> ~100 ms) because later cycles append only
  the two continuation turns. Same shape on elite (969 -> 1398 -> 1452 ->
  1453).
- **Zero method warnings across all 1,920 custom-arm cycles** (480 per Rust
  arm per suite, plus the controls): no fallbacks, no multi-attempt ladder
  landings, no degenerate windows, no dropped system turns. Every Rust cycle
  ran the real pipeline and landed on the ladder's first candidate.

## Caveats

1. **Mock provider scores measure nothing about retention.** The eval answers
   are constant empty strings; the quality columns are check-mix artifacts.
   They validate plumbing only (design doc 2.6). Model-dependent quality
   claims remain BLOCKED for this campaign per the offline rules.
2. **The summarizer is the deterministic lossy fixture**, not a model. The
   Rust arms exercise the real pipeline mechanics (cut selection, ledger
   protection, summary decay) against a fixed lossy function, so arm
   differences isolate pipeline behavior, not model skill.
3. **Drift-cycle continuations are also mock-generated** (empty assistant
   turns), so the drift vector is thinner than a model-backed run: the
   transcripts grow by a real user prompt plus an empty assistant reply each
   cycle.
4. **Compression uses tiktoken cl100k_base** against the source transcript
   (per cycle), while the harness token estimates are chars/4-based; the two
   scales are not comparable beyond ordering.
5. Practice suites are practice: the templates are public, the runs are
   custom-method custom-mode, and nothing here is a leaderboard or official
   number. The adapter is, however, now validated end to end for a
   model-backed run (delta mode, retries, resync) when one is authorized.

## Files

- results/w12/<suite>/<arm>.jsonl — the 18 results files (this report's source)
- results/*.jsonl — the W6 full-transcript validation runs (superseded mode;
  kept for the record, not comparable to the w12 runs)
- optimus_compactor.py — adapter 0.2.0 (delta-append)
- probe_harness_behavior.py — the fix-validation probe (this report, part 1)
- test_optimus_compactor.py — 13 offline adapter checks
