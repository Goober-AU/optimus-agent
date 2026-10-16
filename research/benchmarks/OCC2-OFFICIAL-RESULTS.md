# OCC-2 Official SILO-BENCH Comparison

Pinned official engine (silo-bench @ e7412778), Level I, n2 agents, msg protocol,
max_rounds 20, 1200 s per-case wall-clock cap, content-reconstruction proxy on
(request bytes untouched; see research/benchmarks/adapter/). All runs: 10 official
Level-I cases, real server-side prompt tokens. The occ1 glm-5.3 cell was stopped
mid-run and is excluded.

| Mode | Model | Case acc | mean S | Completed | Errored | Prompt tokens | Calls | Msgs | B/msg | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| OCC-0 glm-5.3 | 0.8 | 0.944 | 9 | 1 | 184416 | 127 | 33 | 232.0 | 52.0 | 904.2 |
| OCC-0 glm-5.3-flash | 1.0 | 1.000 | 10 | 0 | 149034 | 100 | 40 | 263.9 | 38.1 | 288.7 |
| OCC-1 glm-5.3-flash | 1.0 | 1.000 | 10 | 0 | 191634 | 97 | 42 | 289.8 | 43.5 | 418.4 |
| sym-v1 glm-5.3-flash | 0.9 | 1.000 | 9 | 1 | 253864 | 88 | 36 | 153.8 | 65.6 | 751.1 |

Notes:
- sym-v1's errored case is the same I-10 StdDev wall-clock timeout mode seen in the
  OCC-0 glm-5.3 pilot; accuracy 0.9 vs 1.0 is within one timeout case.
- Message bytes: sym-v1 153.8 B/msg vs OCC-1 289.8 B/msg on the identical protocol
  (47.0% smaller inter-agent payloads).
- Token counts are real server-side tokenizer usage from the local model gateway.
- All evaluation models were served via the local model gateway chat-completions route.
