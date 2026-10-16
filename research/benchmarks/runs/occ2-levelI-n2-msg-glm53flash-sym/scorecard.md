# SILO-BENCH run: occ2-levelI-n2-msg-glm53flash-sym

| field | value |
|---|---|
| comm_mode | sym_v1_handdesigned |
| model | glm-5.3-flash |
| protocol | msg |
| max_rounds | 20 |
| task_count | 10 |
| case_accuracy (S == 1.0) | 0.900 |
| mean S (official success rate) | 1.0 |
| mean P (official partial correctness) | 1.0 |
| mean C (official token consumption) | 6866.606878306878 |
| mean D (official communication density) | 2.0 |
| cases completed | 9 |
| cases errored/timed out | 1 |
| mean rounds to completion | 6.0 |
| total LLM calls | 88 |
| total prompt tokens (real, server-side) | 253864 |
| total completion tokens (INCLUDES hidden reasoning) | 89859 |
| total inter-agent messages | 36 |
| total inter-agent message payload bytes | 5538 |
| case latency mean / p50 / p95 (s) | 191.4 / 65.6 / 751.1 |
| LLM call latency (per-case mean) mean / p50 / p95 (s) | 8.6 / 5.6 / 18.4 |

## Per-case results

| task | status | rounds | S | P | messages | msg bytes | prompt tok | out tok* | elapsed s |
|---|---|---|---|---|---|---|---|---|---|
| I-01 | completed | 5 | 1.0 | 1.0 | 4 | 431 | 23937 | 3867 | 37.038 |
| I-02 | completed | 5 | 1.0 | 1.0 | 4 | 611 | 27567 | 5350 | 53.78 |
| I-03 | completed | 15 | 1.0 | 1.0 | 6 | 880 | 58010 | 5658 | 72.801 |
| I-04 | completed | 3 | 1.0 | 1.0 | 2 | 469 | 12813 | 4107 | 58.325 |
| I-05 | completed | 5 | 1.0 | 1.0 | 4 | 556 | 22412 | 3807 | 52.023 |
| I-06 | completed | 7 | 1.0 | 1.0 | 4 | 435 | 33610 | 11172 | 105.918 |
| I-07 | completed | 5 | 1.0 | 1.0 | 4 | 749 | 27037 | 39585 | 202.471 |
| I-08 | completed | 4 | 1.0 | 1.0 | 4 | 771 | 24707 | 12231 | 91.383 |
| I-09 | completed | 5 | 1.0 | 1.0 | 4 | 636 | 23771 | 4082 | 39.795 |
| I-10_n2 | timeout | None | n/a | n/a | None | None | None | None | 1200.064 |

* completion tokens include hidden reasoning tokens reported by the gateway;
  prompt tokens are real server-side counts. S/P/C/D are official engine metrics.
