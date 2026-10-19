# COMPACTBENCH RESULTS — custom practice-suite runs of the Optimus compaction adapter

Scope: the W12 CompactBench suite runs committed on this branch. Report
writer W21, 2026-10-20; every number cites the committed artefacts, and the
run itself is W12's (`research/context_rot_r2/compactbench_adapter/SUITE-RESULTS.md`
is the primary source; the per-arm JSONL results under
`research/context_rot_r2/compactbench_adapter/results/w12/` were re-read
for this report, including an independent re-verification of the degeneracy
claim in section 5).

Label: CUSTOM-OPTIMUS for every number in this file. These are custom runs
on the PUBLIC practice suites with the mock eval provider
(`mock-deterministic`, scorer 2.0.0) and a custom adapter (0.2.0,
delta-append). They are not CompactBench leaderboard numbers, not
comparable to any 1.0.0-scored run, and not usable as quality evidence for
Optimus compaction. The pipeline behind each Rust-arm cycle is PRODUCTION-PATH
(the real Rust compaction code); the quality scores are mock-eval artifacts
and are not mixed into any production-path table.

## 1. Run configuration

| parameter | value |
| --- | --- |
| harness binary | built in the W12 worktree from the fixed source `49f473d8d`+ (`SessionManager::continue_recent` persistence, non-zero usage anchors, flush-on-append) |
| suites | `benchmarks/public/starter` (4 templates) and `benchmarks/public/elite_practice` (20 templates) |
| case count | 5 per template: starter 20 cases, elite 100 cases, all cases, no subset |
| drift cycles | 3 per case = 4 compactions per case (80 cycles/arm starter, 400/arm elite) |
| difficulty / seeds | medium / default (runner-derived blake2b seeds, deterministic) |
| provider / model | mock / mock-deterministic |
| arms | optimus-rust-task-state, optimus-rust-plain, echo-first-n, full-transcript-oracle (adapter 0.2.0, delta-append), built-in null / naive-summary / structured-state / hierarchical-summary / hybrid-ledger |
| adapter mode | delta-append (see section 3) |
| total wall clock | 73 s for all 18 runs |

Exact commands are recorded in
`research/context_rot_r2/compactbench_adapter/SUITE-RESULTS.md`
("Run configuration").

## 2. What the fixed harness changed (W12 probe validation)

`research/context_rot_r2/compactbench_adapter/probe_harness_behavior.py`
re-validated the six W6 findings against the fixed binary. Reversed by the
fix: F1 (sessions now persist and accumulate via `continue_recent`; a
copied session dir silently forks a fresh session, so the adapter keeps one
stable live dir per case and never clones), F4 (tokensBefore /
compactedContextTokens now non-zero, anchored on the last assistant usage,
conservative), F5 (appends flushed and the turn map stored BEFORE
compaction; after an error exit a same-dir retry with `newTurns=[]`
compacts the extended session). Unchanged: F2 (the cut must immediately
precede a user turn) and F6 (per-session entry hashes). F3 was refined:
message j joins the kept tail iff `cum[j+1] <= keepRecentTokens`
(chars/4 + 0.6 per message, measured from the end), and in delta mode the
walk CLAMPS at the previous compaction's `firstKeptEntryId` - an oversized
budget degrades to an empty summarize window, never to re-folding old
turns.

## 3. Delta-append validation (why the mode is trustworthy)

Decision: delta-append. One persistent per-case session dir; each
`compact()` call appends only the turns the session has not seen (re-read
from the turn-map sidecar before every attempt); the harness re-compacts
the live session; `previous_artifact` stays ignored because the session
carries the state the next compaction must see. Evidence, per
`SUITE-RESULTS.md` part 2:

1. Turn bookkeeping partitions exactly: across three sequential calls,
   `summarizedTurnIds + keptTurnIds` always equals the cumulative turn map,
   split prefix | suffix at the cut, and the cut always precedes a user
   turn. `summarizedTurnIds` is cumulative - the artifact transitively
   contains folded turns via the carried summary.
2. Ledger and summary carry forward: the turn-2 constraint survives three
   compactions in the taskState ledger; the third summary embeds the
   decayed previous summary, so real summary-of-summary drift is
   measurable (the thing the superseded full-transcript mode could not
   measure).
3. Retries are safe without clones: F5's flush-before-compaction ordering
   leaves appends durable after a failure; the adapter re-reads the
   sidecar and retries with zero new turns. Only kills/crashes and failed
   flushes abandon a dir.
4. Overshoot is safe: a keepRecentTokens beyond the live window clamps at
   the previous `firstKeptEntryId` (probe F3: K = 500 keeps the whole
   window with an empty-window warning).
5. Full-transcript mode's premise is gone (it existed only as the F1
   workaround); with F1 fixed it measures the wrong thing.

Resync semantics (exercised by the adapter checks): if the live session's
map is not an id-and-content prefix of the incoming transcript, the adapter
opens a fresh session dir, re-sends the full transcript, and continues in
delta mode from there. Adapter checks: 13/13 pass
(`research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py`,
including the delta round-trip, three-cycle partition, retry-after-failure
and resync tests).

## 4. Results (CUSTOM-OPTIMUS, mock provider)

Scorer 2.0.0 aggregates per results file; "eval prompt tokens" is the run's
`token_usage.prompt_tokens`, dominated by the artifact-embedding eval
prompts. Source files:
`research/context_rot_r2/compactbench_adapter/results/w12/<suite>/<arm>.jsonl`.

starter (4 templates x 5 cases = 20 cases, 80 cycles/arm):

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

elite_practice (20 templates x 5 cases = 100 cases, 400 cycles/arm):

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

## 5. Every quality number is degenerate by construction

The mock provider returns a constant empty string for every completion, so
the eval answers do not depend on the artifact at all. Per-case quality is
bit-identical across arms - re-verified for this report directly from the
committed JSONLs: all 20 starter cases and all 100 elite cases have
identical per-cycle quality fields (cycle score, penalized score, item
scores) across all nine arms, including `built-in:null`. The 0.375 /
0.3326 overall and the drift / constraint columns are artifacts of each
template's check-type mix (`contains_normalized` items score 0 on an empty
answer; `forbidden_absent` items score 1). No quality claim is made or
possible from these runs.

The built-in baselines' artifacts are empty too (their summarizer calls
hit the same mock), so their compression collapses to the null floor
(108.76x starter / 178.24x elite) and their only separation is eval-prompt
overhead.

## 6. The mock-independent observables (what the runs do measure)

- Artifact mass: task-state is the largest (compression 0.48x starter /
  0.74x elite - the artifact is ~2.1x / ~1.3x the source: fixture
  boilerplate + ledger lines + structured-state section), then plain
  (0.55x / 0.90x), oracle (0.78x / 0.79x, pure render overhead),
  echo-first-n (0.83x starter; 1.12x elite - keeps the first 10 turns
  verbatim). Compression below 1x is a small-transcript effect (W6
  finding, unchanged).
- Delta mode demonstrably engaged: per-cycle means (starter, task-state)
  - eval prompt tokens 963 -> 1376 -> 1439 -> 1441 (the artifact mass is
    constant across cycles: decayed prior summary capped at 400 chars +
    live-window excerpts + ledger) while the oracle's grows monotonically
    with the transcript (755 -> 987 -> 1044 -> 1101); same shape on elite
    (969 -> 1398 -> 1452 -> 1453)
  - compression rises across cycles (0.431 -> 0.499) for the same reason
  - per-cycle latency drops (414 -> ~100 ms) because later cycles append
    only the two continuation turns.
- Pipeline health: zero method warnings across all 1,920 custom-arm cycles
  (480 per Rust arm per suite, plus the controls): no fallbacks, no
  multi-attempt ladder landings, no degenerate windows, no dropped system
  turns; every Rust cycle ran the real pipeline and landed on the ladder's
  first candidate.

## 7. What a model-backed run needs

The adapter is validated end to end for one (delta mode, retries, resync;
sections 2-3). A run that produces quality numbers additionally needs:

1. authorization for model calls (the campaign's offline rule forbids
   provider APIs and paid tokens - the blocker, not the plumbing);
2. a real eval provider and a real summarizer behind the harness (the mock
   and the deterministic lossy fixture are the only things making the
   quality columns degenerate);
3. model-generated drift continuations (in these runs they are mock-empty
   assistant turns, so the drift vector is a real user prompt plus an empty
   reply per cycle);
4. the token-scale caveat kept in mind: scorer compression uses tiktoken
   cl100k_base while harness estimates are chars/4-based (ordering only).

## 8. Files

- `research/context_rot_r2/compactbench_adapter/SUITE-RESULTS.md` - W12's
  run report (this file's primary source)
- `research/context_rot_r2/compactbench_adapter/results/w12/<suite>/<arm>.jsonl` -
  the 18 results files
- `research/context_rot_r2/compactbench_adapter/results/*.jsonl` - the W6
  full-transcript validation runs (superseded mode, kept for the record;
  not comparable to the w12 runs)
- `research/context_rot_r2/compactbench_adapter/optimus_compactor.py` -
  adapter 0.2.0 (delta-append)
- `research/context_rot_r2/compactbench_adapter/probe_harness_behavior.py` -
  the harness-fix probe
- `research/context_rot_r2/compactbench_adapter/test_optimus_compactor.py` -
  13 offline adapter checks
