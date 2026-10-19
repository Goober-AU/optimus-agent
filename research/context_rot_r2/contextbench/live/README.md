# ContextBench live practice run (rot2 W27) - CUSTOM-OPTIMUS, LIVE-MODEL

A model-backed practice run of the ContextBench dev subset through the
REAL Optimus compaction pipeline with a live GLM 5.3 summarizer, evaluated
by the offline-validated W10 metric mirror. Every number in this directory
carries the labels **CUSTOM-OPTIMUS** and **LIVE-MODEL**: this is a
practice run, not an official scorecard, and no number here is comparable
to a ContextBench leaderboard result or to upstream-scored runs.

## 1. What ran

| item | value |
| --- | --- |
| pipeline | `compaction-harness` binary (production path: `prepare_compaction` -> `compact` -> `append_compaction` -> `SessionManager` restore) |
| binary source | this worktree, `crates/pi-coding-agent/src/bin/compaction_harness.rs` with the live env-configured HTTP summarizer (W26 pattern, `summarizer: "live"` + `ROT2_LIVE_*` env) |
| model | GLM 5.3 (DGX K3 LaunchPad, SGLang, `max_model_len` 1,048,576), temperature 0 |
| max_tokens ladder | 8192 -> 16384 -> 32768 -> 65536 (reasoning tokens bill against `max_tokens`; only clean `finish_reason: "stop"` completions surface) |
| summarizer wire calls | every HTTP request logged to `raw-calls.jsonl` (status, latency, tokens, retries, finish reason; never content) |
| transport policy | 3 retries with 2/4/8 s backoff on 429/5xx/network errors, non-retryable statuses terminal |
| arms | A = baseline (`taskState off`), B = task-state ledger (`taskState prompt`); 5 append+compact cycles per (instance, arm); delta-append sessions (W12 mode) |
| evaluation | W10 mirror (`research/context_rot_r2/contextbench/`): `harness.evaluate_task` per record, `summarize_arm` aggregates, `compare_arms` paired statistics |
| endpoint verification | PONG-verified before launch (`GET /models` 200 with `glm-5.3`; one-chat PONG, `finish_reason stop`) |

## 2. Slice rule (deterministic, blake2b-seeded; never silently applied)

1. Candidate pool: dev-side instances of the committed repo-level split
   (`data/dev_holdout_split.json`, W10) whose gold context has 4..12
   annotation items and 1200..9000 total content characters - 110 of the
   308 dev instances qualify.
2. Within each language, candidates are ordered by
   `blake2b("contextbench|w27-live-slice|w27.1|" + instance_id)`.
3. Languages are ordered by descending candidate count (ties: name) and
   instances are taken round-robin across languages until 30 are
   selected, so every language in the pool is represented (java is absent
   from the pool: its dev instances fall outside the size window).
4. The holdout side (192 instances, 30 repos) is never touched - not for
   selection and not for distractors.
5. Size rationale: the live smoke measured 1.75-2.25 summarizer HTTP
   requests per fired compaction cycle (parallel window chunks plus
   ladder rungs); the corrected run is 26 instances x 2 arms x 4 cycles
   = 208 cycle attempts, which used 439 requests (measured 1.64-2.1 per
   fired cycle, 206/208 fired). The cap is enforced: the runner refuses
   to start a new attempt once reached and records the stop (none
   triggered).

Selected slice: 26 instances, 15 repos, 7 languages
(python 5, go 4, javascript 4, typescript 4, c 4, rust 3, cpp 2);
the exact instance ids are in `run_config.json`. The first attempt
selected 30 instances x 5 cycles; it was invalidated mid-run (section 5)
and the corrected slice is the 26-instance prefix of the same
deterministic order, sized into the remaining wire-call budget.

## 3. Method: retention through compaction, not free exploration

Offline rules waive network for the GLM endpoint only: no repository
checkouts exist, so real agent exploration is impossible. The practice
run therefore measures the campaign's actual question - how much
task-relevant context survives the production compaction path - with a
synthetic exploration transcript per instance:

- turn 0 (user): repo, base commit, the problem statement (capped at
  12,000 chars), and an instruction to explore;
- then one user/assistant pair per read: the user requests a file span,
  the assistant echoes the span content with absolute line numbers;
- reads = the instance's gold annotations (what a successful agent needed)
  plus distractor reads: gold annotations of OTHER dev instances (same
  repo preferred, then same language, then any dev; content 100..2500
  chars; exact (file, start, end) duplicates of the target gold
  excluded), deterministically interleaved by
  `random.Random(blake2b-seed)`;
- the read list is split into 5 near-equal chunks, one chunk appended per
  cycle; after every append the harness compacts (reserve/keep budget
  scaled to 30%/10% of the estimated transcript tokens, min 200/60 - the
  practice-run scaling that makes ContextBench-sized transcripts actually
  trigger compaction).

Retained context after cycle c = summary text + task-state ledger (arm B)
+ kept tail turns. References are recovered exactly from the kept tail
(a read keeps its span iff its content turn survived; a lone request
turn keeps the file only) and by regex from the summary/ledger text
(paths normalized like the gold side). The per-cycle retained references
form the trajectory `pred_steps`; the final cycle's references form
`pred_files`/`pred_spans`.

Consequences, stated up front:

- **coverage** here = retention of the gold context through compaction
  (what the working context still carries), not exploration recall;
- **AUC-coverage** = running-union retention across cycles (upstream
  semantics: what was retained at each step, cumulatively);
- **precision** penalizes retained distractors and hallucinated paths
  (paths are NOT filtered against a checkout - offline rule; upstream
  `_filter_step_to_repo` existence filtering stays a
  live-phase-with-checkout item);
- **EditLoc** uses the upstream gold-patch fallback (the records carry no
  model patch); it is arm-independent and reported for completeness only;
- `patch_success` stays UNAVAILABLE (needs checkouts + the f2p harness).

## 4. Arms (documented substitution)

W10's offline arm B (jev-relevance) is a `pi-jev` evidence/search surface
that the compaction-harness binary cannot express. The live practice arms
are the two production compaction policies the binary does express:

| arm | name | live semantics |
| --- | --- | --- |
| A | baseline | production compaction, `taskState off` (no protected ledger) |
| B | task-state-ledger | production compaction, `taskState prompt` (protected task-state ledger on) |

The substitution is deliberate and labeled: with the ledger on, the
summarizer prompt carries the Protected Task State blocks, so arm B
should retain gold context differently from arm A. Both arms share the
identical transcript, settings and seeds (matched pairs), so per-instance
deltas isolate the policy effect.

## 5. Wire-call accounting (all requests against the endpoint)

| phase | requests |
| --- | --- |
| endpoint PONG verification | 2 (`GET /models`, one PONG chat) |
| live smoke (2 instances, arm A, 5 cycles) | 17 |
| first main attempt - INVALIDATED | 123 (15/60 pairs; see below) |
| corrected main run (26 x 2 x 4) | 439 |
| **total** | **581 / 600 hard cap** |

The invalidated attempt: the first main run (30 instances x 5 cycles)
used W10's `dataset.load_rows` column list, which does not select
`problem_statement` - the transcripts carried no issue text, so the
relevance signal the distractors probe was absent. Detected from the
model's own summaries ("The issue body was blank..."), stopped after 15
pairs, moved to `runs/w27-live-blind-invalid/` (gitignored), and counted
against the cap. The loader fix and a selftest guard are committed; the
corrected run is the one reported here.

`raw-calls.jsonl` records every corrected-run request: purpose, arm,
instance, cycle, attempt, maxTokens, httpStatus, latencyMs,
transportRetries, finishReason, token usage, content/reasoning char
counts, error. No content, no file paths, no credentials are logged.
Observed: 439 requests, mean latency 20.4 s (median 12.9 s, max 143.8 s),
0 transport-retry events, 0 error calls, 47 max_tokens ladder climbs,
finish reasons 388 stop / 51 length (every ladder eventually landed
stop).

## 6. Results (26 paired instances; per-item records in `results.json`)

Arm aggregates (W10 mirror semantics; micro-averaged final metrics,
macro-averaged trajectory metrics):

| metric | arm A baseline | arm B task-state-ledger | paired delta B-A | sign test p | Wilcoxon p |
| --- | --- | --- | --- | --- | --- |
| final file coverage | 0.974 | 0.974 | +0.000 (0+/0-) | 1.000 | 1.000 |
| final file precision | 0.357 | 0.326 | -0.029 (3+/11-) | 0.057 | 0.064 |
| final line coverage | 0.852 | 0.916 | +0.069 (10+/6-) | 0.455 | 0.196 |
| final line precision | 0.320 | 0.586 | +0.097 (18+/4-) | **0.004** | **0.003** |
| AUC coverage (file) | 0.860 | 0.863 | +0.003 | 0.625 | 0.715 |
| AUC coverage (line) | 0.583 | 0.605 | +0.022 | 0.815 | 0.420 |
| redundancy (file) | 0.672 | 0.661 | -0.011 | 0.629 | 0.190 |
| redundancy (line) | 0.587 | 0.605 | +0.017 | 1.000 | 0.954 |
| EditLoc recall / precision | 0.046 / 1.000 | 0.046 / 1.000 | 0 (gold-patch fallback, arm-independent) | - | - |

Reading (practice-run observations, not confirmatory claims):

- **Gold files survive compaction almost fully in both arms**
  (file coverage 0.974): the summarizer keeps naming every file it saw,
  gold or not, so file-granularity retention barely separates arms.
- **Line-granularity retention separates them**: arm B retains a
  significantly larger fraction of gold lines among its retained lines
  (precision 0.586 vs 0.320; 18 wins / 4 losses, sign p = 0.004) and
  numerically more gold lines overall (coverage 0.916 vs 0.852). With the
  protected task-state instructions in the prompt, the summary leans
  toward the issue-relevant spans; without them, retained lines are
  dominated by distractor spans.
- **Distractors are the precision payload**: the transcripts interleave
  gold reads with same-repo distractor reads, so precision measures
  relevance filtering under compaction. By raw retained-line counts arm A
  ends 47% gold / 53% non-gold; arm B ends 69% gold / 31% non-gold -
  arm B drops proportionally far more distractor material.
- **Ledger engagement is partial**: the parsed task-state ledger
  populated in 34 of 104 arm-B cycles; the arm-B effect observed here is
  the prompt-level Protected Task State instructions, not a fully
  populated ledger.
- **Both arms lose line detail over cycles**: line coverage 0.85-0.92
  after 4 cycles with ~60% line redundancy across cycles
  (re-mentioned spans).
- **EditLoc is a dataset constant here** (gold-patch fallback): recall
  0.046 / precision 1.000 - for most of this slice the gold patch's
  deleted lines fall outside the gold context spans; reported for
  completeness, carries no arm information.

Full paired statistics (per-instance deltas, exact sign test,
tie-corrected Wilcoxon normal approximation) are in `results.json`
(`comparisons`); per-item records are under `records.A` / `records.B`.

## 7. Metric citations

All metric semantics are W10's mirrors of upstream ContextBench
(commit `1436c28a8eb95496da4ea69ad458b9f8a8eb7d61`, Apache-2.0); the full
citation table is W10's draft scorecard section 5
(`research/context_rot_r2/contextbench/RETRIEVAL-SCORECARD-DRAFT.md`).
This run only *consumes* that mirror (`harness.evaluate_task`,
`summarize_arm`, `compare_arms`); no metric formula was reimplemented
here.

## 8. Limitations

- No repository checkouts: byte-span and symbol granularities stay
  unavailable, predicted paths are unfiltered, and the exploration is
  synthetic (gold-derived), so file-level precision is dominated by
  distractor retention rather than by free-exploration noise.
- Practice-scaled compaction budgets (30%/10% of a ~1-4.7k-token
  transcript), not production context sizes; absolute retention numbers
  are not transferable, arm deltas are the meaningful quantity.
- GLM 5.3 summaries are model output: the summary text determines which
  references survive, so retained-reference counts measure the model +
  prompt + policy stack, not the pipeline alone. That is the intended
  production-path measurement.
- 30 instances x 2 arms x 5 cycles is a practice-sized sample; the paired
  statistics are descriptive, not confirmatory.
