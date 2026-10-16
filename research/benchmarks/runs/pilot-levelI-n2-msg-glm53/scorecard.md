# SILO-BENCH run: pilot-levelI-n2-msg-glm53

| field | value |
|---|---|
| comm_mode | occ0_plain_english |
| model | glm-5.3 |
| protocol | msg |
| max_rounds | 20 |
| task_count | 10 |
| case_accuracy (S == 1.0) | 0.800 |
| mean S (official success rate) | 0.9444444444444444 |
| mean P (official partial correctness) | 0.9444444444444444 |
| mean C (official token consumption) | 4159.719000135667 |
| mean D (official communication density) | 1.8333333333333333 |
| cases completed | 9 |
| cases errored/timed out | 1 |
| mean rounds to completion | 7.888888888888889 |
| total LLM calls | 127 |
| total prompt tokens (real, server-side) | 184416 |
| total completion tokens (INCLUDES hidden reasoning) | 115801 |
| total inter-agent messages | 33 |
| total inter-agent message payload bytes | 7657 |
| case latency mean / p50 / p95 (s) | 265.6 / 52.0 / 904.2 |
| LLM call latency (per-case mean) mean / p50 / p95 (s) | 9.7 / 3.8 / 28.5 |

## Per-case results

| task | status | rounds | S | P | messages | msg bytes | prompt tok | out tok* | elapsed s |
|---|---|---|---|---|---|---|---|---|---|
| I-01 | completed | 6 | 1.0 | 1.0 | 3 | 476 | 11875 | 2663 | 40.669 |
| I-02 | completed | 9 | 1.0 | 1.0 | 4 | 665 | 24936 | 3049 | 53.83 |
| I-03 | completed | 8 | 1.0 | 1.0 | 3 | 553 | 19400 | 3315 | 49.724 |
| I-04 | completed | 6 | 1.0 | 1.0 | 2 | 476 | 13261 | 2210 | 35.414 |
| I-05 | completed | 8 | 1.0 | 1.0 | 4 | 688 | 19643 | 3886 | 50.107 |
| I-06 | completed | 8 | 0.5 | 0.5 | 5 | 2037 | 25302 | 67232 | 470.531 |
| I-07 | completed | 6 | 1.0 | 1.0 | 4 | 1101 | 17246 | 12833 | 166.076 |
| I-08 | completed | 7 | 1.0 | 1.0 | 4 | 921 | 15243 | 4816 | 49.326 |
| I-09 | completed | 13 | 1.0 | 1.0 | 4 | 740 | 37510 | 15797 | 529.547 |
| I-10_n2 | timeout | None | n/a | n/a | None | None | None | None | 1210.712 |

* completion tokens include hidden reasoning tokens reported by the gateway;
  prompt tokens are real server-side counts. S/P/C/D are official engine metrics.
