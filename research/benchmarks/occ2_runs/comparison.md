# OCC-2 SILO-BENCH comparison (generated)

| mode | model | case_accuracy | mean_S | mean_P | mean_C | mean_D | cases_completed | rounds_mean | total_llm_calls | total_prompt_tokens | total_messages | total_message_payload_bytes | bytes_per_message | case_latency_p50_s | case_latency_p95_s | header_compliance |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| occ0 | glm-5.3 | 0.8 | 0.9444444444444444 | 0.9444444444444444 | 4159.719000135667 | 1.8333333333333333 | 9 | 7.888888888888889 | 127 | 184416 | 33 | 7657 | 232.0 | 51.9685 | 904.1877499999993 | None |
| occ0 | glm-5.3-flash | 1.0 | 1.0 | 1.0 | 5142.9114285714295 | 2.0 | 10 | 6.0 | 100 | 149034 | 40 | 10558 | 263.9 | 38.054 | 288.6676499999995 | None |
| occ1 | glm-5.3-flash | 1.0 | 1.0 | 1.0 | 5620.36658730159 | 2.1 | 10 | 5.6 | 97 | 191634 | 42 | 12173 | 289.8 | 43.537 | 418.41524999999933 | 1.0 |
| sym-v1-hand-designed | glm-5.3-flash | 0.9 | 1.0 | 1.0 | 6866.606878306878 | 2.0 | 9 | 6.0 | 88 | 253864 | 36 | 5538 | 153.8 | 65.563 | 751.147149999999 | 0.9487 |
