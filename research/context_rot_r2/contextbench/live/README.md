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
   ladder rungs) at a ~0.85 fire rate, i.e. ~8.5 wire calls per
   (instance, arm) pair; 30 instances x 2 arms x 5 cycles keep the
   expected total (~510) under the 600-request hard cap with headroom.
   The cap is enforced: the runner refuses to start a new attempt once
   reached and records the stop.

Selected slice: 30 instances, 15 repos, 7 languages
(python 6, go 5, javascript 5, typescript 5, c 4, rust 3, cpp 2);
the exact instance ids are in `run_config.json`.

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

## 5. Wire-call accounting

| phase | requests |
| --- | --- |
| endpoint PONG verification | 2 (`GET /models`, one PONG chat) |
| live smoke (2 instances, arm A) | 17 |
| main run | see `run_config.json` (hard cap 583 for the main run; 17 + 583 = 600 total) |

`raw-calls.jsonl` records every main-run request: purpose, arm, instance,
cycle, attempt, maxTokens, httpStatus, latencyMs, transportRetries,
finishReason, token usage, content/reasoning char counts, error. No
content, no file paths, no credentials are logged.

## 6. Results

See `results.json` (per-item records, per-arm aggregates, paired
statistics, call stats) and the LIVE section of
`reports/context-rot-r2/CONTEXTBENCH-RESULTS.md` for the verdict tables.

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
