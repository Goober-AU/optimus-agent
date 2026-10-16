# SILO-BENCH run: pilot-levelI-n2-msg-glm53flash

| field | value |
|---|---|
| comm_mode | occ0_plain_english |
| model | glm-5.3-flash |
| protocol | msg |
| max_rounds | 20 |
| task_count | 10 |
| case_accuracy (S == 1.0) | 1.000 |
| mean S (official success rate) | 1.0 |
| mean P (official partial correctness) | 1.0 |
| mean C (official token consumption) | 5142.9114285714295 |
| mean D (official communication density) | 2.0 |
| cases completed | 10 |
| cases errored/timed out | 0 |
| mean rounds to completion | 6.0 |
| total LLM calls | 100 |
| total prompt tokens (real, server-side) | 149034 |
| total completion tokens (INCLUDES hidden reasoning) | 182834 |
| total inter-agent messages | 40 |
| total inter-agent message payload bytes | 10558 |
| case latency mean / p50 / p95 (s) | 79.9 / 38.1 / 288.7 |
| LLM call latency (per-case mean) mean / p50 / p95 (s) | 6.8 / 3.5 / 22.7 |

## Per-case results

| task | status | rounds | S | P | messages | msg bytes | prompt tok | out tok* | elapsed s |
|---|---|---|---|---|---|---|---|---|---|
| I-01 | completed | 5 | 1.0 | 1.0 | 4 | 409 | 10362 | 2889 | 40.87 |
| I-02 | completed | 7 | 1.0 | 1.0 | 4 | 653 | 15335 | 2536 | 32.36 |
| I-03 | completed | 7 | 1.0 | 1.0 | 4 | 872 | 14299 | 3096 | 27.631 |
| I-04 | completed | 4 | 1.0 | 1.0 | 2 | 366 | 5352 | 1066 | 15.364 |
| I-05 | completed | 6 | 1.0 | 1.0 | 4 | 611 | 14407 | 3461 | 28.255 |
| I-06 | completed | 6 | 1.0 | 1.0 | 4 | 666 | 12834 | 8354 | 39.109 |
| I-07 | completed | 5 | 1.0 | 1.0 | 4 | 847 | 13846 | 7370 | 46.567 |
| I-08 | completed | 7 | 1.0 | 1.0 | 4 | 1333 | 22313 | 6016 | 44.964 |
| I-09 | completed | 6 | 1.0 | 1.0 | 4 | 629 | 13001 | 5156 | 36.999 |
| I-10 | completed | 7 | 1.0 | 1.0 | 6 | 4172 | 27285 | 142890 | 486.75 |

* completion tokens include hidden reasoning tokens reported by the gateway;
  prompt tokens are real server-side counts. S/P/C/D are official engine metrics.
