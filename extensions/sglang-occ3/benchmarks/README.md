# OCC Communication Benchmark (OCC-1 / OCC-2 / OCC-3)

Offline-first scaffolding for benchmarking agent-communication protocols
against control arms on one SGLang server (target deployment: DGX B300
serving GLM-5.3 FP8 TP8, 1M context, EAGLE adaptive speculative decoding).
The harness never assumes the server is up: every script degrades with a
clear error, and `bench_occ3.py --selftest` validates the harness logic
offline with a stub client. No live run has been made from this directory;
all current evidence is stub data.

## Arms

Eight first-class arms per the campaign spec (SEMANTIC COMMUNICATION
TESTS). Each arm is an `ArmSpec` config object (id, role, delivery mode,
carrier source, expected channel behavior), so the same tasks run
identically across arms.

| Arm | Role | Delivery mechanism | Carrier source |
| --- | --- | --- | --- |
| no_comm | floor | Nothing is delivered; the receiver sees only its task prompt. | none |
| random_latent | latent control | Deterministic pseudorandom information-free carrier written through the OCC-3 send/recv channel mechanics. | random symbol soup |
| unrelated_latent | latent control | Carrier is the sender-only evidence of a different task (fixed rotation): real fluent text, wrong content. | other task's evidence |
| zeroed_latent | latent control | Constant information-free carrier through the same channel mechanics. Text-level stand-in, not a literal all-zero KV tensor. | repeated neutral symbol |
| occ1 | protocol | OCC-1 compact structured English codec (optimus.agent-comm.v1; kernel codec rlm.occ1); receiver prefills the delivered envelope. | task message |
| occ2 | protocol | Local stand-in for occ2::encode_dual: compact symbolic header plus verbatim body; receiver prefills the delivered envelope. | task message |
| occ3 | protocol | Latent communication: sender writes message KV into a salted radix-cache namespace (one prefill) via POST /occ3/v1/send; receiver reads it zero-copy as a prefix hit via POST /occ3/v1/recv and prefills only the task suffix. Same-exact-model only. | task message |
| full_text | upper bound | Full relevant text: the sender evidence is delivered verbatim and prefilled with the task. Alias: text. | task message |

Semantic-transfer rule: OCC-3 must beat the random_latent and zeroed_latent
controls (the significance floor in analyze.py) to claim semantic
communication. If OCC-3 performs no better than random latent state, it
has not demonstrated semantic transfer. The no_comm floor measures
chance-level answering with nothing delivered.

## Measured dimensions

a) **Wire size** (`wire_chars`): characters in the sender's outbound
   payload. full_text: raw message; occ1/occ2: full delivered envelope;
   latent arms: the carrier text sent through the channel
   (`carrier_tokens` records the KV size in tokens); no_comm: 0.

b) **End-to-end send-to-recv latency**:
   - `send_ms`: latent arms = HTTP round trip of `/occ3/v1/send`, which
     includes the one carrier prefill; text arms = local codec encode
     time only (no network send happens for those arms).
   - `recv_ms`: latent arms = `/occ3/v1/recv` round trip (prefix hit
     plus suffix prefill plus decode); text arms = `/generate` round
     trip on the delivered text plus the receiver task; no_comm =
     `/generate` on the receiver task alone.
   - `e2e_ms`: the full measured window from delivery start to completed
     receiver output. The optional radix-cache flush and the channel
     release are outside the measured window.

c) **Receiver-side compute** (`receiver_prefill_tokens`): tokens the
   receiver actually prefilled, computed as `prompt_tokens -
   cached_tokens` from the server response meta. OCC-3 should prefill
   roughly the suffix only (the carrier is served from cache, verified
   by `cached_tokens`); the text arms prefill the full prompt.

d) **Semantic fidelity** (`success`, `failed_checks`): task-success rate
   on asymmetric-information tasks, per the task's verifier (see below).

e) **Cache-hit stability** (`hit`, `hit_rate`, `cache_miss_rate`): for
   latent arms, `hit` is the plugin-verified prefix hit
   (`cached_tokens` covers the carrier). With `--eviction-fill N`, N
   filler generations run between send and recv to probe the eviction
   window; `cache_miss_rate` is the fraction of recvs that missed.

## Task set (optimus.occ3.tasks.v2)

`tasks_asymmetric.json` is a v2 object: `{"schema":
"optimus.occ3.tasks.v2", "tasks": [...]}`. A bare v1 list is still
accepted (tier 1, exact match). 30 tasks across three difficulty tiers:

- Tier 1 (12 tasks, the original v1 tasks, unchanged): direct extraction
  of three facts from short sender-only evidence.
- Tier 2 (8 tasks): multi-fact extraction with distractors (4 checks).
- Tier 3 (10 tasks): defect identification/repair on sender-only source
  snippets and distractor-heavy synthesis, verified by scripted checks.

Each task:

```json
{
  "id": "t21_defect_off_by_one",
  "domain": "coding-defect",
  "tier": 3,
  "sender_msg": "source file with a specific defect plus the corrected line",
  "receiver_task": "asks the receiver to answer using ONLY the message above",
  "eval_checks": [],
  "verifier": {
    "type": "scripted",
    "all_of": ["count_remaining", "i in 0..items.len()"],
    "any_of_groups": [["off-by-one", "off by one"]],
    "none_of": []
  }
}
```

Verifier types:

- `exact_match` (default): every `eval_checks` substring must appear in
  the receiver output, case-insensitively.
- `scripted`: all `all_of` substrings must appear; each `any_of_groups`
  group needs at least one member present; no `none_of` substring may
  appear. `none_of` constrains the output only, never the evidence.

Construction rules, enforced by the selftest:

- The answers exist only in `sender_msg` (sender-only evidence); the
  receiver prompt never contains any check substring (no leakage).
- Exact-match checks and scripted `all_of` members appear in the sender
  evidence; each `any_of` group has at least one member phrased there.
- No task's verifier passes on another task's canned answer, so the
  unrelated-latent control is content-disjoint by construction.
- New tasks must not place a check inside another task's sender message.
- The 12 original tasks are byte-preserved (digest-checked by the
  selftest).

## Experiment ledger (optimus.occ3.benchmark-ledger.v1)

`--ledger runs/ledger.jsonl` appends one JSONL entry per arm run with the
SCIENTIFIC EXPERIMENT LEDGER fields: `hypothesis`, `code_revision`,
`model_revision`, `sglang_revision`, `runtime_configuration`, `benchmark`,
`dataset` (file + sha256), `task_count`, `random_seed`, `arm`,
`treatment_arm`, `control_arms`, `accuracy` and `accuracy_by_tier`,
`latency` (e2e/send/recv medians with p10/p90), `token_usage`
(receiver prefill/completion/carrier medians), `gpu_memory` (only what is
passed via `--gpu-memory-mib`; the harness does not measure it),
`failure_rate`, `confidence_interval` (Wilson 95 percent on accuracy),
`implementation_cost` (wire_chars and encode_ms medians; proxy only),
`decision` (KEEP/REVISE/REVERT/INCONCLUSIVE/BLOCKED) and `notes`.
Every entry is validated on append and on read.

## Analysis and the semantic-transfer gate

`analyze.py` merges results files, recomputes per-arm statistics from
raw trials, and reports:

- the arm-by-metric medians table,
- accuracy with 95 percent Wilson confidence intervals,
- a pairwise accuracy significance matrix (two-proportion z-test),
- the semantic-transfer gate: each gate arm (default occ3) must beat every
  floor arm (default random_latent, zeroed_latent) with p < alpha
  (default 0.05) and an accuracy margin of at least --min-margin.

Gate verdicts: PASS, FAIL, or UNDERSAMPLED (missing arm data). The
markdown report also shows the no_comm floor as a reference.
`--json-out` writes machine-readable comparisons
(schema occ-bench-analysis/2).

## Usage

```bash
# All eight arms over the same 30 tasks (per-arm results files are
# written as results_<arm>.json; one ledger entry is appended per arm).
python bench_occ3.py --arm all --endpoint http://127.0.0.1:30000 --trials 20 \
    --out results.json --ledger runs/ledger.jsonl \
    --code-rev <git-sha> --model-rev <model-id> --sglang-rev <sglang-rev>

# Or a single arm (alias text=full_text).
python bench_occ3.py --arm occ3 --endpoint http://127.0.0.1:30000 --trials 20 --out results_occ3.json

# Eviction-window probe: filler traffic between latent send and recv.
python bench_occ3.py --arm occ3 --eviction-fill 8 --trials 20 --out results_occ3_evict.json

# Offline validation of the harness itself (no GPU, no server, no network):
# runs all 8 arms end to end with stub data and writes a ledger.
python bench_occ3.py --selftest [--selftest-dir DIR]

# Compare arms (the gate uses the random/zeroed controls as the floor).
python analyze.py results_no_comm.json results_random_latent.json results_unrelated_latent.json \
    results_zeroed_latent.json results_occ1.json results_occ2.json results_occ3.json \
    results_full_text.json --out results.md --json-out comparisons.json
```

Latent arms (occ3, random_latent, unrelated_latent, zeroed_latent) need
the server API key in the environment (`OCC3_API_KEY`, then
`SGLANG_API_KEY` as fallback; override the primary name with
`--api-key-env`). The key is read from the environment only and is never
printed, passed on the command line, or stored; results files record
only the environment variable name that was used.

## Method notes

- Same prompts, task file and seed for every arm; temperature 0 for
  evaluation; identical `max_new_tokens` (default 256) across arms.
- Run at least 20 trials per task per arm (`--trials 20`); report
  medians with p10/p90. Mean, min, max and counts are also stored.
- The radix cache is flushed before every trial (`POST /flush_cache`,
  outside the timed window) so prefill measurements are cold. Disable
  with `--no-flush`.
- Run all arms back to back on the same server instance without
  reloading the model; compare arms only within the same run window.
- Control-arm carriers are deterministic functions of (seed, task,
  trial), so runs are reproducible offline.

## Files

- `tasks_asymmetric.json` - 30 asymmetric-information tasks (v2 schema).
- `bench_occ3.py` - the benchmark harness CLI (see `--help`).
- `analyze.py` - comparison table, significance gate and report generator.
- `README.md` - this file.

## Output schema (results.json)

```json
{
  "schema": "occ-bench-results/2",
  "meta": {"arm", "arm_spec", "endpoint", "tasks_file", "trials_per_task",
           "temperature", "max_new_tokens", "seed", "eviction_fill",
           "flush_cache", "ttl_s", "api_key_env", "selftest", "timestamp_utc"},
  "tasks": [{"id", "domain", "tier", "eval_checks", "verifier"}],
  "aggregate": {"n_trials", "n_errors", "<metric>": {"median", "p10", "p90",
               "mean", "min", "max", "count"}, "success_rate", "hit_rate",
               "cache_miss_rate", "by_tier": {"1": {"n_trials", "n_success", "success_rate"}}},
  "trials": [{"task_id", "domain", "tier", "arm", "trial", "wire_chars",
              "encode_ms", "send_ms", "recv_ms", "e2e_ms", "prompt_tokens",
              "cached_tokens", "completion_tokens", "receiver_prefill_tokens",
              "carrier_tokens", "hit", "success", "failed_checks", "error"}]
}
```

## Exit codes (bench_occ3.py)

- 0: run completed without transport errors (fidelity failures are
  data, not errors).
- 1: run completed but some trials hit transport or protocol errors;
  results are still written.
- 2: fatal setup problem (unreachable endpoint, rejected key, missing
  OCC-3 plugin routes, unreadable tasks file, missing API key for a
  latent arm, unwritable output, invalid ledger).

## Caveats and threats to validity

- **No live claims.** Everything run so far is the offline selftest with
  stub data. The stub answers are constructed to pass or fail per arm by
  design; they validate harness plumbing (arms, verifiers, ledger,
  analysis), not model semantics. Live numbers must come from real runs
  against the deployed server.
- **zeroed_latent is a text-level stand-in.** The public plugin API
  accepts text only, so the zeroed-latent arm sends a constant
  information-free carrier through the real channel mechanics. It is not
  a literal all-zero KV tensor; it bounds what an uninformative latent
  state can do at the text boundary.
- **random_latent uses symbol soup.** The pseudorandom carrier contains
  only neutral symbols so no alphanumeric check can match it by
  accident; it is a stand-in for random KV content, not random tensors.
- **occ2 is a stand-in.** The production `occ2::encode_dual` seam lives
  inside the Optimus native transport and cannot be imported from a
  standalone script, so the occ2 arm measures a dual symbolic envelope
  (adaptive header plus verbatim body), not production adaptive
  compression. Treat its `wire_chars` as envelope-overhead evidence only.
- **occ1 codec source.** The arm uses the kernel `rlm.occ1` codec when
  importable (for example when the harness runs inside the Optimus
  runtime) and a grammar-identical local mirror otherwise.
- **Envelope-as-prompt.** The text-arm receiver prefills the delivered
  envelope as-is. A production receiver could decode first and prefill
  only the body; the plain-text baseline approximates that lower bound
  and occ1/occ2 the upper bound.
- **OCC-3 constraints.** Latent transfer requires the same exact model on
  both sides (a single server here); channels are bounded by the
  registry cap, TTL and the carrier token cap. The plugin caps recv
  decode at 1024 new tokens.
- **Fidelity checks are substring-based.** They cannot prove semantic
  equivalence, only that the required facts surfaced in the output.
  Scripted verifiers add grouped phrasing tolerance and output
  exclusions (none_of), not semantic grading.
- **Known pre-existing weaknesses in the 12 original tasks (kept
  unchanged per campaign scope, allowlisted in the selftest):** the
  t09 receiver prompt contains the word "catering" and the t12 receiver
  prompt contains "vegan", so those single checks are passable without
  the message (the remaining checks still gate success); five
  original-to-original check-in-sender collisions exist (t04 "34" in
  t09, t05 "teal" in t04, t08 "14" in t02, t10 "18" in t04, t10 "25" in
  t09), which only weakens the live unrelated-latent floor for those
  pairs. New tasks avoid all such collisions.
- **no_comm floor is chance-level, not zero.** A live receiver without
  any message can still guess common words; the floor measures what is
  guessable from the receiver prompt alone.
- **GPU memory is not measured by the harness.** The ledger records it
  only when an operator passes `--gpu-memory-mib`.
- **Serving noise.** EAGLE speculative decoding and batching variance
  affect absolute latencies; compare arms only within the same window.
- **Statistics.** The gate uses an unadjusted two-proportion z-test
  (normal approximation); with small n or extreme rates, prefer the
  Wilson intervals and replicate before claiming KEEP/REVERT decisions.
- **Proxy settings.** The HTTP library honors `HTTP_PROXY`/`HTTPS_PROXY`
  environment variables; set `NO_PROXY` for local endpoints if a proxy
  interferes.
