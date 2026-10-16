# SILO-BENCH run: occ2-levelI-n2-msg-glm53flash-occ1

| field | value |
|---|---|
| comm_mode | occ1_structured |
| model | glm-5.3-flash |
| protocol | msg |
| max_rounds | 20 |
| task_count | 10 |
| case_accuracy (S == 1.0) | 1.000 |
| mean S (official success rate) | 1.0 |
| mean P (official partial correctness) | 1.0 |
| mean C (official token consumption) | 5620.36658730159 |
| mean D (official communication density) | 2.1 |
| cases completed | 10 |
| cases errored/timed out | 0 |
| mean rounds to completion | 5.6 |
| total LLM calls | 97 |
| total prompt tokens (real, server-side) | 191634 |
| total completion tokens (INCLUDES hidden reasoning) | 173395 |
| total inter-agent messages | 42 |
| total inter-agent message payload bytes | 12173 |
| case latency mean / p50 / p95 (s) | 109.5 / 43.5 / 418.4 |
| LLM call latency (per-case mean) mean / p50 / p95 (s) | 8.8 / 4.3 / 28.2 |

## Per-case results

| task | status | rounds | S | P | messages | msg bytes | prompt tok | out tok* | elapsed s |
|---|---|---|---|---|---|---|---|---|---|
| I-01 | completed | 6 | 1.0 | 1.0 | 4 | 905 | 17455 | 2613 | 26.167 |
| I-02 | completed | 7 | 1.0 | 1.0 | 4 | 893 | 22341 | 3197 | 34.272 |
| I-03 | completed | 4 | 1.0 | 1.0 | 4 | 1098 | 13383 | 3210 | 28.097 |
| I-04 | completed | 3 | 1.0 | 1.0 | 3 | 843 | 7885 | 1826 | 19.181 |
| I-05 | completed | 7 | 1.0 | 1.0 | 4 | 1007 | 21741 | 3489 | 35.86 |
| I-06 | completed | 5 | 1.0 | 1.0 | 4 | 974 | 14574 | 9135 | 68.066 |
| I-07 | completed | 5 | 1.0 | 1.0 | 4 | 1050 | 18373 | 8167 | 56.625 |
| I-08 | completed | 5 | 1.0 | 1.0 | 4 | 1795 | 22220 | 5280 | 51.214 |
| I-09 | completed | 5 | 1.0 | 1.0 | 4 | 946 | 13624 | 3754 | 78.573 |
| I-10 | completed | 9 | 1.0 | 1.0 | 7 | 2662 | 40038 | 132724 | 696.468 |

* completion tokens include hidden reasoning tokens reported by the gateway;
  prompt tokens are real server-side counts. S/P/C/D are official engine metrics.
