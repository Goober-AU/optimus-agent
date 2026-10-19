# CompactBench live-model practice run (W26, CUSTOM-OPTIMUS + LIVE-MODEL)

GLM 5.3 as BOTH the summarizer (behind the Rust compaction-harness binary,
production path) and the task/eval model (behind the stock CompactBench
runner), on the W12 arm grid, delta-append mode, the deterministic W12
seed prefix (starter suite slots 0-1 = 8 cases per arm), 3 drift cycles.

**Labels: CUSTOM-OPTIMUS + LIVE-MODEL.** This is a practice run on the
public starter suite with a custom adapter, a custom in-process provider
registration and a live endpoint. It is NOT a CompactBench leaderboard
number, NOT comparable to any 1.0.0-scored run, and NOT an official
scorecard for Optimus compaction. The mock numbers in
`SUITE-RESULTS.md` and `COMPACTBENCH-RESULTS.md` stay untouched and clearly
separated (section "Mock vs live" below).

## 1. Wiring choice: option (a), production path

**Decision: extend the harness binary with an env-configured HTTP provider
(`crates/pi-coding-agent/src/bin/compaction_harness.rs`), option (a) in the
brief.** `summarizer: "live"` in the request selects it; every URL and
secret comes from `ROT2_LIVE_*` environment variables; the default
(`fixture`) mode is byte-for-byte unchanged and offline.

Why (a) over the mirror mode (b):

1. The plumbing made it cheap. The pipeline's summary wire call goes
   through `register_api_provider_simple` + `complete_simple`, the same
   seam the fixture and the `compaction_recovery` tests already use. The
   provider follows the existing provider pattern
   (`AssistantMessageEventStream::new_owned` + `producer_handle` +
   `stream.spawn`), so the live summarizer sits INSIDE the production
   `prepare_compaction -> compact -> append_compaction -> restore` path
   the benchmark is supposed to measure. The mirror mode would have moved
   the summarizer OUT of that path and split the labels
   (PRODUCTION-PATH-BINARY for compression, LIVE-MIRROR for quality).
2. Zero new dependencies: `reqwest` (json + native-tls) is already a
   `pi-coding-agent` dependency; no `Cargo.toml` change.
3. Offline rule preserved: no network unless a request explicitly selects
   `live` AND the env is fully configured; the binary fails fast otherwise.

The GLM 5.3 reasoning behavior drove two provider details (both verified
against the live endpoint):

- **max_tokens ladder 8192 -> 16384 -> 32768 -> 65536** (start = max of the
  pipeline-requested budget and `ROT2_LIVE_MAX_TOKENS_START`, default
  8192). GLM on SGLang bills `reasoning_content` tokens against
  `max_tokens`, so an under-sized rung ends `finish_reason: "length"` with
  a truncated summary that the pipeline's validation would reject; the
  ladder retries with a bigger budget inside the provider, and the
  pipeline above only sees clean completions. In this run 36/38 task-state
  and 32/32 plain summarizer requests finished `stop` on the 8192 rung;
  the two 8192-length cases landed on 16384.
- **Transport retries**: 429/5xx/network errors back off 2s/4s/8s, max 3
  per request (Amendment-3). Zero backoff events occurred in the whole
  run.

The task/eval side (evaluation answers + drift continuations) is the
stock runner with a custom provider class (`glm_provider.py`,
`GlmLiveProvider`) registered in-process into `compactbench.providers`'s
registry, because the shared CompactBench checkout is read-only for this
worker (campaign file-map rule). `run_live.py` drives
`run_experiment(RunArgs(...))` directly - the CLI is a thin wrapper over
exactly these arguments - so the runner code path is the stock one. The
Rust arms are selected through `optimus_compactor_live.py`, which loads
the committed W12 adapter unchanged and flips only its
`_SUMMARIZER_MODE` module global to `"live"`; the W12 adapter file itself
is untouched.

## 2. Run configuration

See `run-config.json` for the frozen record. Summary:

| parameter | value |
| --- | --- |
| harness binary | built in this worktree (isolated target dir `C:/Users/openclawuser/rot2-w26-target`), base `fcbd7d2ae` + the live provider on branch `rot2/w26-cblive` |
| suite / cases | `benchmarks/public/starter` (4 templates) x slots 0-1 = 8 cases per arm (verified: identical case ids to W12's first two per template) |
| drift cycles | 3 (4 compactions per case, 32 cycles per Rust arm) |
| difficulty / seeds | medium / default (runner blake2b seeds - same namespace as W12) |
| provider / model | glm-live (in-process) + live harness summarizer / glm-5.3, temperature 0 |
| arms | optimus-rust-task-state, optimus-rust-plain, echo-first-n, full-transcript-oracle (adapter 0.2.0, delta-append) |
| adapter mode | delta-append (unchanged W12 semantics) |
| summarizer ladder | 8192 -> 16384 -> 32768 -> 65536; task ladder 8192 -> 32768 |
| wall clock | ~29 min for the 4 live arms (487 + 592 + 333 + 315 s) |

Offline regression before any live call: 13/13 adapter checks pass
against this build. `probe_harness_behavior.py` has 4 pre-existing F3
threshold-sweep failures AT HEAD that also fail without the live
provider: post-W12 campaign commits changed the library (D5 "skip no-op
compaction when nothing new can be summarized" et al., see
`git log 49f473d8d..HEAD -- crates/pi-coding-agent/src/core/compaction/`).
The observed direction is safe for the adapter (oversized K now returns
the "no compaction prepared" degenerate warning the adapter already
treats as not-landed). Documented, not fixed: this run characterizes
HEAD, and the probe file belongs to W12.

## 3. Budget (Amendment-2 accounting)

| quantity | value |
| --- | --- |
| planned logical calls (ruling Option B) | 512 (448 task + 64 summarizer) |
| HTTP requests logged in `raw-calls.jsonl` | 669 (6 pong, 367 eval, 225 continuation, 71 summarizer) |
| unlogged pre-ledger requests (initial PONG, first smoke x5, direct harness test) | 7 |
| total HTTP requests against the endpoint | 676 |
| backoff events (429/5xx/network) | 0 |
| Amendment-1 extension | NOT triggered: task-state vs plain gap 0.194 > 0.05 (`amend1-decision.json`) |

**Incident (fixed, resumed):** the first `optimus-rust-task-state` arm run
hit a provider bug - a `length` finish re-rolled the SAME 2048 rung
(`continue` inside the transport-retry loop) instead of advancing the
ladder, burning ~128 extra requests before the per-arm budget guard
(168) tripped after 2 of 8 cases. Fix: advance the rung (`break`) and
start the task ladder at 8192 (evidence: 128/167 requests length-finished
at 2048). Verified offline with a fake transport (length at 8192 ->
retry at 32768 -> stop). The arm was resumed (`--resume`; the runner
skips completed case ids), its remaining 6 cases and all later arms ran
clean at rung 8192 (112/112 requests per arm, all `stop`). All recorded
answers - including the two defect-era cases - come from `stop`-finish
responses only; the defect wasted requests, it never recorded a
length-truncated answer. Total spend stayed under the ruling's 768
ceiling; Amendment-1's extension headroom is treated as spent.

## 4. Results (LIVE-MODEL, starter slots 0-1, 8 cases/arm)

Scorer 2.0.0. `compression` = source-tokens / artifact-tokens per cycle
(same definition as the W12 tables; higher = smaller artifact).

| arm | overall | drift_res | constraint_ret | contradiction | compression | eval prompt tokens |
| --- | --- | --- | --- | --- | --- | --- |
| optimus-rust-task-state | **0.6844** | **0.990** | **0.800** | 0.094 | 0.67x | 114,042 |
| echo-first-n | 0.6531 | 0.833 | 0.800 | 0.094 | 1.60x | 53,663 |
| full-transcript-oracle | 0.5781 | 0.721 | 0.650 | 0.078 | 0.92x | 80,366 |
| optimus-rust-plain | 0.4906 | 0.757 | 0.650 | 0.172 | 0.72x | 110,072 |

Per-template mean case scores (8 cases = 2 per template x 4 templates):

| arm | buried_constraint | decision_override | entity_confusion | reference_resolution |
| --- | --- | --- | --- | --- |
| optimus-rust-task-state | 0.775 | 0.812 | 0.675 | 0.475 |
| echo-first-n | 0.512 | 0.875 | 0.525 | 0.700 |
| full-transcript-oracle | 0.662 | 0.625 | 0.550 | 0.475 |
| optimus-rust-plain | 0.650 | 0.562 | 0.275 | 0.475 |

Item-level means (selected; full data in `aggregate.json`):

| check | task-state | plain | echo-first-n | oracle |
| --- | --- | --- | --- | --- |
| recall_constraint (contains) | 1.000 | 1.000 | 0.750 | 0.500 |
| recall_latest_decision (contains) | 0.625 | 0.125 | 0.750 | 0.250 |
| apply_constraint (forbidden_absent) | 0.625 | 0.500 | 0.375 | 0.875 |
| assign_a / assign_b (contains) | 0.375 | 0.125 | 0.000 | 0.000 / 0.125 |
| entity_consistency (contains) | 1.000 | 1.000 | 1.000 | 1.000 |

Summarizer cost (Rust arms only; the controls make no summarizer calls):

| arm | requests | compactions | mean latency | p95 latency | reasoning tokens | prompt tokens |
| --- | --- | --- | --- | --- | --- | --- |
| optimus-rust-task-state | 38 | 32 | 19.0 s | 39.1 s | 122,151 | 62,292 |
| optimus-rust-plain | 32 | 32 | 17.2 s | 35.6 s | 72,886 | 49,715 |

The ledger arm's summarizer burns ~1.7x the reasoning tokens of the plain
arm (the Protected Task State block makes GLM reason harder), and 2 of
its 38 requests needed the 16384 rung after an 8192 length finish.

Engagement (mock-independent observables, same checks as W12): zero
method warnings, zero fallbacks, zero resyncs across all 128 Rust-arm
cycles; every cycle landed a valid cut on the ladder's first candidate;
delta mode demonstrably engaged (eval prompt tokens flatten across
cycles instead of growing with the transcript as the oracle's do).

## 5. Mock vs live (same 8 cases, same binary where applicable)

| arm | mock@HEAD (this binary, fixture summarizer, mock eval) | live (GLM both roles) | W12 committed mock (49f473d8d binary, 20 cases) |
| --- | --- | --- | --- |
| optimus-rust-task-state | 0.3750 / 0.458x | **0.6844** / 0.67x | 0.3750 / 0.48x |
| optimus-rust-plain | 0.3750 / 0.560x | **0.4906** / 0.72x | 0.3750 / 0.55x |
| echo-first-n | 0.3750 / 0.833x | **0.6531** / 1.60x | 0.3750 / 0.83x |
| full-transcript-oracle | 0.3750 / 0.776x | **0.5781** / 0.92x | 0.3750 / 0.78x |

(cells: overall / compression; the mock@HEAD runs are in
`results/mock-<arm>.jsonl`, offline, zero model calls; the W12 committed
column is the 20-case starter run from `SUITE-RESULTS.md`, kept for
reference - it used the pre-D5 binary and all 5 slots)

Reading:

- **The mock quality columns were degenerate by construction (all 0.375);
  the live columns separate the arms.** That was the mission: with a real
  eval model the four arms order task-state > echo-first-n > oracle >
  plain, and the two Rust arms differ by 0.19 overall.
- **The ledger arm wins where the ledger is supposed to help**:
  `recall_latest_decision` 0.625 vs 0.125 plain, `assign_a/b` 0.375 vs
  0.125, drift resistance 0.99 vs 0.76, contradiction rate 0.094 vs
  0.172.
- **GLM writes tighter summaries than the lossy fixture**: compression
  improved from 0.46x to 0.67x (task-state) and 0.56x to 0.72x (plain)
  on the same transcripts, with the same cycle-0 sources (identical
  seeds).
- **Live continuations are real text** (mock continuations were empty
  assistant turns), so the drift vector is thicker: the source
  transcript grows by a GLM-written reply each cycle. This is why
  echo-first-n's compression is 1.60x live (the source outgrew its fixed
  first-10-turn artifact) versus 0.83x under mock-empty continuations.
- **The verbatim oracle is not the ceiling under live eval**: it scores
  0.578, below both task-state (0.684) and echo-first-n (0.653), and it
  is the WORST arm on `recall_constraint` (0.500 - the eval model must
  find the buried rule in a wall of verbatim text). A structured summary
  that surfaces the constraint beats a raw dump for this eval model on
  these templates.
- **Live runs are single-draw and not bit-reproducible**: two smoke runs
  of the identical case (same seed, temperature 0) scored 0.4 and 0.1
  (`recall_constraint` 1.0 then 0.0). GLM at temperature 0 is not
  deterministic. With n = 8 cases per arm, treat small gaps (the
  echo-vs-oracle 0.075) as noise-adjacent; the 0.194 Rust-arm gap is the
  robust signal.

## 6. Caveats

1. Practice run, public starter suite, 8 of 20 cases per template (the
   ruled Option B budget), single live draw per case. No leaderboard
   claim, no production-quality claim - the labels above govern.
2. The live binary is HEAD (`fcbd7d2ae`) + the live provider, NOT the
   W12-era `49f473d8d` binary: post-W12 compaction changes (D5, D6, D9,
   D10, D14, typed state v2) are in the measured path. The mock@HEAD
   baseline column exists exactly to isolate the model effect from the
   binary effect in the mock-vs-live comparison.
3. Two task-state cases (the first two) ran during the provider-ladder
   defect era; their answers are valid stop-finish responses, but their
   task-call counts are not comparable (section 3).
4. The task ladder starts at 8192 (not the runner's 2048): GLM reasoning
   bills against max_tokens and 2048 length-finished 128/167 times in the
   defect-era sample. This is a run-config choice, applied identically
   to every arm.
5. 7 of the 676 total HTTP requests predate the persisted ledger
   (initial PONG, first smoke run, one direct harness verification);
   they are itemized in section 3 and in `run-config.json`.

## 7. Files

| path | content |
| --- | --- |
| `run-config.json` | frozen run configuration, endpoint, ladders, budget, incident record |
| `run_live.py` | driver: PONG verify, per-arm runs, budget ledger, Amendment-1 decision, mock baseline |
| `glm_provider.py` | `GlmLiveProvider` - task/eval HTTP provider (ladder, backoff, call log) |
| `optimus_compactor_live.py` | live wrapper for the W12 adapter (flips `_SUMMARIZER_MODE`) |
| `aggregate.py` | builds `aggregate.json` from the results and raw logs (offline) |
| `results/<arm>.jsonl` | the 4 live results files (scorer 2.0.0) |
| `results/mock-<arm>.jsonl` | the 4 mock@HEAD baselines, same 8 cases, zero model calls |
| `results/smoke.jsonl` | the one-scenario one-cycle smoke run |
| `raw-calls.jsonl` | all 669 logged HTTP requests (purpose, rung, latency, usage, finishes) |
| `raw/<arm>-task.jsonl` | per-arm task-model call logs (pong + eval + continuation) |
| `raw/<arm>-summarizer/*.jsonl` | per-invocation summarizer call logs (one file per harness process) |
| `amend1-decision.json` | the pre-registered Amendment-1 decision record (not triggered) |
| `smoke-benchmarks/` | single-template copy of the starter suite used by the smoke |
