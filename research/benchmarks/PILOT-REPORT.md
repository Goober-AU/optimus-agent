# SILO-BENCH pilot report (bench-official)

Date: 2026-10-16. Worktree `improve/occ2-occ3-communication` (base v0.1.32).
Everything lives under `research/benchmarks/` (adapter + runs). No commits
made; parent integrates.

## 1. What ran

The OFFICIAL SILO-BENCH engine (pinned clone `e74127782ed1c42fff474249961f022c063d76f2`,
read-only) executed unmodified against the local model gateway
(`http://127.0.0.1:43120/ollama/v1`). The adapter (`research/benchmarks/adapter/`)
only orchestrates: it imports `init_case` / `run_round` / `evaluate` from the
clone, runs each case in a subprocess with a hard 1200 s wall-clock budget,
and aggregates the official per-case `results.json` metrics into scorecards.

Two runs, both `comm_mode: occ0_plain_english` (SILO default: inter-agent
messages pass through verbatim, no encoding - exactly the OCC-0 baseline the
campaign needs), protocol `msg` (P2P), Level I (aggregation tasks I-01..I-10),
2 agents per case, `max_rounds: 20`:

| run | model | tasks | official result |
|---|---|---|---|
| pilot-levelI-n2-msg-glm53 | glm-5.3 | 10 | case accuracy 0.8, mean S 0.944 (9 completed) |
| pilot-levelI-n2-msg-glm53flash | glm-5.3-flash | 10 | case accuracy 1.0, mean S 1.0 (10 completed) |

"Case accuracy" = fraction of cases with official `success: true` (S == 1.0,
i.e. every agent submitted the correct answer). Timed-out cases count as
failures in accuracy; mean S/P/C/D are averaged over completed cases only.

## 2. Numbers

### glm-5.3 (primary)

| metric | value |
|---|---|
| case accuracy (S == 1.0) | 0.8 (8/10) |
| mean S (official) | 0.944 |
| mean P (official) | 0.944 |
| mean C (official, tokens/round) | 4159.7 |
| mean D (official, messages / N(N-1)) | 1.83 |
| cases completed / timed out | 9 / 1 |
| mean rounds to completion | 7.9 |
| total LLM calls | 127 |
| total prompt tokens (real, server-side) | 184,416 |
| total completion tokens (INCLUDES hidden reasoning) | 115,801 |
| total inter-agent messages | 33 (7,657 payload bytes) |
| case latency mean / p50 / p95 | 265.6 s / 52.0 s / 904.2 s |
| per-case-mean LLM call latency mean / p50 / p95 | 9.7 s / 3.8 s / 28.5 s |

Per case: I-01..I-05, I-07..I-09 all S=1.0; I-06 (Checksum) S=0.5;
I-10 (Standard Deviation) TIMEOUT at 1200 s (was in round 6 of 20,
neither agent had submitted; killed by the budget, not by max_rounds).

### glm-5.3-flash (cheaper run)

| metric | value |
|---|---|
| case accuracy (S == 1.0) | 1.0 (10/10) |
| mean S / P (official) | 1.0 / 1.0 |
| mean C (official) | 5142.9 |
| mean D (official) | 2.0 |
| cases completed / timed out | 10 / 0 |
| mean rounds to completion | 6.0 |
| total LLM calls | 100 |
| total prompt tokens (real, server-side) | 149,034 |
| total completion tokens (INCLUDES hidden reasoning) | 182,834 |
| total inter-agent messages | 40 (10,558 payload bytes) |
| case latency mean / p50 / p95 | 79.9 s / 38.1 s / 288.7 s |

Note the flash model outperformed the big model on this suite: it converged
on I-06 and I-10 where glm-5.3 burned enormous reasoning budgets (I-06
glm-5.3: 67,232 completion tokens vs flash 8,354; I-10 flash: 142,890
completion tokens but S=1.0 within 495 s, while glm-5.3 exceeded the 1200 s
budget without submitting).

## 3. Failure analysis (honest)

- I-06 glm-5.3 (S=0.5): both agents exchanged local XOR checksums over P2P;
  agent 0 even issued a correction ("my CORRECT local XOR is 87, NOT 71").
  Agent 0 submitted the correct global answer 251; agent 1 submitted 235 -
  it integrated stale or wrong data despite having received the correction.
  Textbook SILO-BENCH "communication-reasoning gap": information was on the
  wire, integration failed. Message log is in the run workspace.
- I-10 glm-5.3 (timeout): the two-phase std-dev protocol (exchange sum+count,
  then square sums) did not converge within the wall-clock budget; the run
  was still mid-coordination (6 rounds used of 20). This is a budget
  artifact, not an engine failure - rerun with a larger `case_timeout_s`
  before drawing model conclusions.

## 4. Gateway caveats (all documented, all handled)

1. GLM tool-call stripping. The gateway extracts SILO's fenced XML tool-call
   blocks from the model's visible output into native OpenAI `tool_calls`
   (finish_reason="tool_calls", empty `message.content`) in (at least) two
   shapes: complete block in `function.name`, or tool tag in `function.name`
   plus the parameters block inside `function.arguments` as a JSON key. The
   official engine parses only `message.content`, so GLM rounds degenerate
   without help. Fix: `adapter/gateway_proxy.py`, a localhost reverse proxy
   that forwards request bytes verbatim (so `usage.prompt_tokens` remain the
   real server-side counts of exactly what the engine sent) and merges the
   extracted blocks back into `message.content`. It fired on 100 responses
   (117 blocks) in the glm-5.3 run and 102 responses (129 blocks) in the
   flash run; every rewrite is logged in `runs/<run>/proxy_rewrites.jsonl`.
   deepseek models pass the fence through natively and do not need it.
2. Hidden reasoning in completion tokens. `usage.completion_tokens` at this
   gateway includes hidden reasoning, so output-token totals overestimate
   visible wire volume and must never be used for wire-cost claims.
   `usage.prompt_tokens` are real and are what the scorecards report as the
   communication-volume measure (plus inter-agent message bytes, an
   adapter-side wire-volume metric: content bytes of the P2P messages that
   actually crossed between agents).
3. Infinite retry in the official client (`src/utils/llm.py` tenacity retry
   on any exception). Handled with the per-case subprocess timeout; a stuck
   case is killed and recorded as `timeout`.

## 5. What remains for the full milestone run

1. Scale up within Level I n2 first (it is complete and proven): the pilot
   used all 10 Level-I cases at n2. Next: repeat with 3-5 seeds per case for
   variance, then n5 (agent counts 5), then Level II/III at n2/n5. Watch the
   wall-clock: glm-5.3 case latency p95 was ~900 s at n2 with 2 agents; n5
   triples the per-round LLM calls.
2. Raise `case_timeout_s` (1200 -> 1800+) or lower `max_rounds` per level;
   I-10 shows the current budget can cut off slow-but-converging cases.
3. Add `broadcast` and `sfs` protocol runs (engine supports them; adapter
   config just needs new run specs) to measure protocol effects.
4. OCC-2 comparison point: rerun the same task set with an encoded
   inter-agent channel and compare S/P against prompt tokens and message
   bytes. The adapter records exactly the measurement points needed
   (per-case S, P, real prompt tokens, message count, message payload
   bytes, latencies), and the official scorer stays the referee.
5. If a MARBLE datapoint is wanted: werewolf environment only, after
   verifying the gateway honors request-level `tools` (native function
   calling, werewolf_agent.py L481/L828); budget dozens-to-hundreds of LLM
   calls per game and an LLM judge for speech-quality scores.

## 6. Reproduction

```
# venv (Python 3.14.3; engine requires >= 3.13)
uv venv --python 3.14 research/benchmarks/adapter/.venv
uv pip install --python research/benchmarks/adapter/.venv/Scripts/python.exe \
    openai pydantic pyyaml srsly tenacity httpx

# run a pilot (uses gateway token from local-token.txt; never printed)
research/benchmarks/adapter/.venv/Scripts/python.exe research/benchmarks/adapter/pilot_run.py \
    --config research/benchmarks/adapter/config.json --run pilot-levelI-n2-msg-glm53
```

Outputs: `research/benchmarks/runs/<run>/scorecard.{json,md}`,
`cases/case-*.json` (per-case summaries), `workspace/` (official engine
artifacts: per-round contexts, states, messages, JSONL logs),
`proxy_rewrites.jsonl` (content-reconstruction audit trail).
