# OCC Communication Benchmark (OCC-1 / OCC-2 / OCC-3)

Offline-first scaffolding for benchmarking three agent-communication
protocols against a plain-text baseline on one SGLang server (target
deployment: DGX B300 serving GLM-5.3 FP8 TP8, 1M context, EAGLE adaptive
speculative decoding). The harness never assumes the server is up: every
script degrades with a clear error, and `bench_occ3.py --selftest`
validates the harness logic offline with a stub client.

## Arms

| Arm | Delivery mechanism | Receiver cost model |
| --- | --- | --- |
| text | Baseline. The sender message is delivered verbatim as text. | The receiver prefills message plus task in one plain `POST /generate` call. |
| occ1 | OCC-1 compact structured English codec (`optimus.agent-comm.v1`; kernel codec `rlm.occ1`). Plain-text envelope: first line `OCC/1`, then `KEY=VALUE` lines. | Same as text; the delivered envelope is prefilled. |
| occ2 | OCC-2 adaptive symbolic encoding (Optimus send seam `occ2::encode_dual`). The harness uses a local stand-in envelope: compact symbolic header plus verbatim body (see Caveats). | Same as text; the delivered envelope is prefilled. |
| occ3 | OCC-3 latent communication. The sender writes message KV into a salted radix-cache namespace (one prefill) via `POST /occ3/v1/send`; the receiver reads it zero-copy as a prefix hit via `POST /occ3/v1/recv`. | The receiver prefills only the task suffix; `cached_tokens` verifies the carrier prefix hit. Same-exact-model only. |

## Measured dimensions

a) **Wire size** (`wire_chars`): characters in the sender's outbound
   message payload. text: raw message; occ1/occ2: full delivered
   envelope; occ3: the `text` field of the send request (JSON transport
   overhead excluded; `carrier_tokens` records the KV size in tokens).

b) **End-to-end send-to-recv latency**:
   - `send_ms`: occ3 = HTTP round trip of `/occ3/v1/send`, which includes
     the one carrier prefill; text/occ1/occ2 = local codec encode time
     only (no network send happens for those arms).
   - `recv_ms`: occ3 = `/occ3/v1/recv` round trip (prefix hit plus
     suffix prefill plus decode); others = `/generate` round trip on the
     delivered text plus the receiver task.
   - `e2e_ms`: the full measured window from delivery start to completed
     receiver output. The optional radix-cache flush and the channel
     release are outside the measured window.

c) **Receiver-side compute** (`receiver_prefill_tokens`): tokens the
   receiver actually prefilled, computed as `prompt_tokens -
   cached_tokens` from the server response meta. OCC-3 should prefill
   roughly the suffix only (the carrier is served from cache, verified
   by `cached_tokens`); the text arms prefill the full prompt. This is
   the core trade-off: occ3 pays one prefill at send time, the text arms
   pay a full re-prefill at receive time.

d) **Semantic fidelity** (`success`, `failed_checks`): task-success rate
   on asymmetric-information tasks. The receiver must use facts that
   exist only in the sender message (latent carrier for occ3, delivered
   text for the others). A trial succeeds when every eval substring
   appears in the receiver output, case-insensitively.

e) **Cache-hit stability** (`hit`, `hit_rate`, `cache_miss_rate`): for
   occ3, `hit` is the plugin-verified prefix hit (`cached_tokens` covers
   the carrier). With `--eviction-fill N`, N filler generations run
   between send and recv to probe the eviction window;
   `cache_miss_rate` is the fraction of recvs that missed. Without
   filler traffic it measures baseline stability.

## Method notes

- Same prompts and seeds for every arm; temperature 0 for evaluation;
  identical `max_new_tokens` (default 256) across arms.
- Run at least 20 trials per task per arm (`--trials 20`); report
  medians with p10/p90. Mean, min, max and counts are also stored.
- OCC-1 and OCC-2 arms deliver the message as text through their codecs;
  the receiver then prefills the delivered text plus the receiver task
  with a plain `POST /generate` call (the same text; no decoding step),
  so the prefill cost of the delivered envelope is measured directly.
- The OCC-3 arm uses only the plugin routes: `send` writes the message
  KV once (one prefill under the channel's salt); `recv` replays the
  carrier as a prefix hit and prefills just the suffix.
- The radix cache is flushed before every trial (`POST /flush_cache`,
  outside the timed window) so prefill measurements are cold. Disable
  with `--no-flush`.
- Run all arms back to back on the same server instance without
  reloading the model; compare arms only within the same run window.

## Files

- `tasks_asymmetric.json` - 12 hand-written asymmetric-information tasks.
- `bench_occ3.py` - the benchmark harness CLI (see `--help`).
- `analyze.py` - comparison table and markdown report generator.

## Task file format

JSON list; each item:

```json
{
  "id": "t01_logistics_dock_move",
  "domain": "logistics",
  "sender_msg": "short paragraph carrying facts or instructions",
  "receiver_task": "asks the receiver to answer using ONLY the prior message",
  "eval_checks": ["gate 12", "41", "upright"]
}
```

`id` and `domain` are optional; `sender_msg`, `receiver_task` and
`eval_checks` are required. Checks are matched case-insensitively as
substrings of the receiver output; each task includes at least one
numeric answer and one keyword, and every task is unanswerable without
the sender message. Sender messages stay under 120 words and span
varied domains (logistics, scheduling, coding facts, measurements,
preferences, priorities, travel, inventory, budget, safety, service
naming, dining).

## Usage

Each arm runs 12 tasks times 20 trials = 240 receiver calls.

```bash
# Plain-text baseline and the two text codecs.
python bench_occ3.py --arm text --endpoint http://127.0.0.1:30000 --trials 20 --out results_text.json
python bench_occ3.py --arm occ1 --endpoint http://127.0.0.1:30000 --trials 20 --out results_occ1.json
python bench_occ3.py --arm occ2 --endpoint http://127.0.0.1:30000 --trials 20 --out results_occ2.json

# OCC-3 latent arm (needs the server API key in the environment).
# POSIX shells:  export OCC3_API_KEY=...
# Windows cmd:   set OCC3_API_KEY=...
python bench_occ3.py --arm occ3 --endpoint http://127.0.0.1:30000 --trials 20 --out results_occ3.json

# Eviction-window probe: filler traffic between send and recv.
python bench_occ3.py --arm occ3 --eviction-fill 8 --trials 20 --out results_occ3_evict.json

# Offline validation of the harness itself (no GPU, no server).
python bench_occ3.py --selftest

# Compare arms.
python analyze.py results_text.json results_occ1.json results_occ2.json results_occ3.json --out results.md
```

The API key is read from the environment only (`OCC3_API_KEY`, then
`SGLANG_API_KEY` as fallback; override the primary name with
`--api-key-env`). It is never written to files, never passed on the
command line, and never printed; `results.json` records only the
environment variable name that was used.

## OCC-3 plugin API used by the harness

- `GET  /occ3/v1/status`       -> `{version, identity, metrics}`
- `GET  /occ3/v1/capabilities` -> capability advertisement
- `POST /occ3/v1/send`         `{channel, text, ttl_s?}` -> `{channel, salt, carrier_tokens, ...}`
- `POST /occ3/v1/recv`         `{channel, suffix, max_new_tokens, temperature}` -> `{output_text, output_ids, cached_tokens, hit, ...}`
- `POST /occ3/v1/release`      `{channel}`

All routes require `Authorization: Bearer <key>`. Channel ids must match
`[A-Za-z0-9._-]{1,64}`; the harness generates unique per-trial ids and
releases each channel after its recv. The plugin caps recv decode at
1024 new tokens and bounds TTL (1 to 3600 seconds; server default 900).

## Output schema (results.json)

```json
{
  "schema": "occ-bench-results/1",
  "meta": {"arm", "endpoint", "tasks_file", "trials_per_task", "temperature",
           "max_new_tokens", "seed", "eviction_fill", "flush_cache", "ttl_s",
           "api_key_env", "selftest", "timestamp_utc"},
  "tasks": [{"id", "domain", "eval_checks"}],
  "aggregate": {"n_trials", "n_errors", "<metric>": {"median", "p10", "p90",
               "mean", "min", "max", "count"}, "success_rate", "hit_rate",
               "cache_miss_rate"},
  "trials": [{"task_id", "domain", "arm", "trial", "wire_chars", "encode_ms",
              "send_ms", "recv_ms", "e2e_ms", "prompt_tokens", "cached_tokens",
              "completion_tokens", "receiver_prefill_tokens", "carrier_tokens",
              "hit", "success", "failed_checks", "error"}]
}
```

Metric mapping: `wire_chars` (a), `send_ms`/`recv_ms`/`e2e_ms` (b),
`receiver_prefill_tokens` (c), `success`/`failed_checks` (d),
`hit` plus `hit_rate`/`cache_miss_rate` (e).

`analyze.py` recomputes per-arm medians from the raw trial records,
prints an arm x metric table, and writes `results.md` (markdown tables,
no emojis) including p10/p90 detail for e2e latency and receiver
prefill.

## Exit codes (bench_occ3.py)

- 0: run completed without transport errors (fidelity failures are
  data, not errors).
- 1: run completed but some trials hit transport or protocol errors;
  results are still written.
- 2: fatal setup problem (unreachable endpoint, rejected key, missing
  OCC-3 plugin routes, unreadable tasks file, missing API key for the
  occ3 arm, unwritable output).

## Caveats and threats to validity

- **occ2 is a stand-in.** The production `occ2::encode_dual` seam lives
  inside the Optimus native transport and cannot be imported from a
  standalone script, so the occ2 arm measures a dual symbolic envelope
  (adaptive header plus verbatim body), not production adaptive
  compression. Treat its `wire_chars` as envelope-overhead evidence
  only.
- **occ1 codec source.** The arm uses the kernel `rlm.occ1` codec when
  importable (for example when the harness runs inside the Optimus
  runtime) and a grammar-identical local mirror otherwise.
- **Envelope-as-prompt.** The text-arm receiver prefills the delivered
  envelope as-is. A production receiver could decode first and prefill
  only the body; the plain-text baseline approximates that lower bound
  and occ1/occ2 the upper bound.
- **OCC-3 constraints.** Latent transfer requires the same exact model on
  both sides (a single server here); channels are bounded by the
  registry cap, TTL and the carrier token cap.
- **Fidelity checks are substring-based.** They cannot prove semantic
  equivalence, only that the required facts surfaced in the output.
- **Serving noise.** EAGLE speculative decoding and batching variance
  affect absolute latencies; compare arms only within the same window.
- **Proxy settings.** The HTTP library honors `HTTP_PROXY`/`HTTPS_PROXY`
  environment variables; set `NO_PROXY` for local endpoints if a proxy
  interferes.
