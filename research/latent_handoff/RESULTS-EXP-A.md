# RESULTS - Experiment A: KV-Cache Handoff (OCC-3 probe, protocol v2)

Standalone CPU-first harness in `research/latent_handoff/`. Logical Agent A
processes a completed chat-template turn (user notes + fixed assistant
acknowledgment) with `use_cache=True`; the captured `DynamicCache` is
deep-copied (the transfer artifact) and handed to logical Agent B, which
continues generation from its own user turn, attending to A's context only
through the cache. The receiver question never contains the secret.

Gate (per RESEARCH-NOTES 4.1): full-text positive control >= 80%, with-cache
within 5 points of full text, with-cache > no-cache, and per-trial proof
(with-cache correct AND no-cache wrong).

## Environment

| Field | Value |
|---|---|
| Python | 3.11.15 |
| torch | 2.14.1+cpu |
| transformers | 5.20.0 |
| tokenizers | 0.23.3 |
| safetensors | 0.8.0 |
| numpy | 2.4.6 |
| OS | Windows 10 |
| CPU | AMD EPYC 74F3 24-Core Processor |
| Physical cores | 6 |
| Logical cores | 12 |
| CPU freq (MHz) | 3194.0 |
| torch threads | 6 |
| Total RAM (GiB) | 109.95 |

## Qwen2.5-0.5B-Instruct

- Model: `Qwen/Qwen2.5-0.5B-Instruct` @ revision `7ae557604adf67be50417f59c2c2f167def9a775`
- Parameters: 494,032,768 | layers: 24 | hidden: 896 | vocab: 151936 | dtype: torch.float32 | attn: eager | device: CPU
- Load time: 29.85s | RSS after load: 2292.3 MiB | RSS peak: 2443.1 MiB | RSS end: 2436.4 MiB

### Chat-template probe (primary)

- With-cache correct: **50/50**
- Full-text correct (positive control): **50/50**
- No-cache correct: **0/50**
- Proven trials (with correct AND without wrong): **50/50**
- Positive control pass (full >= 80%): **True**
- With-cache within 5 points of full text: **True**
- With-cache beats no-cache: **True**
- Paired bootstrap with-vs-without: diff=1.0, 95% CI [1.0, 1.0], P(diff>0)=1.0
- **GATE PASS: True**
- Mean sender forward: 0.8282s | mean transfer (deepcopy): 0.01787s | mean receiver generation: 0.8521s | mean fresh generation: 0.6532s | mean full-text generation: 1.1882s
- Mean cache artifact: 1,991,147 bytes fp32 (1944.5 KiB) | 995,573 bytes fp16 | vs text payload ~51 bytes
- Mean sender tokens: 81

#### Per-trial records

| Trial | Target | With-cache | OK | Full-text | OK | No-cache | OK | Sender fwd (s) | Transfer (s) | Recv gen (s) | Cache fp32 (bytes) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | lantern=98 | 98 | True | 98 | True | 1 | False | 0.5192 | 0.00787 | 0.5042 | 1,990,656 |
| 1 | marble=29 | 29 | True | 29 | True | 1 | False | 0.5215 | 0.00475 | 0.4919 | 1,990,656 |
| 2 | saffron=61 | 61 | True | 61 | True | 10 | False | 0.5353 | 0.00705 | 0.5898 | 2,015,232 |
| 3 | obsidian=55 | 55 | True | 55 | True | 1 | False | 0.4482 | 0.00379 | 0.5097 | 1,990,656 |
| 4 | fern=48 | 48 | True | 48 | True | 1 | False | 0.4428 | 0.00354 | 0.4882 | 1,966,080 |
| 5 | opal=97 | 97 | True | 97 | True | 1 | False | 0.479 | 0.00415 | 0.6778 | 1,966,080 |
| 6 | walnut=62 | 62 | True | 62 | True | 1 | False | 0.4408 | 0.00532 | 0.451 | 1,990,656 |
| 7 | juniper=80 | 80 | True | 80 | True | 1 | False | 0.7807 | 0.00703 | 0.4954 | 2,015,232 |
| 8 | jasper=91 | 91 | True | 91 | True | 1 | False | 0.7211 | 0.00567 | 0.483 | 1,990,656 |
| 9 | saffron=82 | 82 | True | 82 | True | 10 | False | 0.4444 | 0.00489 | 0.4645 | 2,015,232 |
| 10 | pelican=76 | 76 | True | 76 | True | 1 | False | 0.7245 | 0.00493 | 0.6176 | 1,966,080 |
| 11 | saffron=53 | 53 | True | 53 | True | 10 | False | 0.5417 | 0.00503 | 0.4647 | 2,015,232 |
| 12 | gorse=87 | 87 | True | 87 | True | 1 | False | 0.4444 | 0.00431 | 0.4593 | 2,015,232 |
| 13 | umber=38 | 38 | True | 38 | True | 1 | False | 0.4996 | 0.02395 | 0.5504 | 1,966,080 |
| 14 | obsidian=97 | 97 | True | 97 | True | 1 | False | 1.1311 | 0.00722 | 0.9887 | 2,015,232 |
| 15 | raven=59 | 59 | True | 59 | True | 1 | False | 2.8523 | 0.14841 | 1.4457 | 2,015,232 |
| 16 | willow=51 | 51 | True | 51 | True | 1 | False | 2.9976 | 0.05264 | 1.909 | 1,990,656 |
| 17 | lantern=68 | 68 | True | 68 | True | 1 | False | 0.707 | 0.0084 | 0.6219 | 1,990,656 |
| 18 | parsley=19 | 19 | True | 19 | True | 1 | False | 0.8784 | 0.00998 | 3.6058 | 1,941,504 |
| 19 | gorse=30 | 30 | True | 30 | True | 1 | False | 1.8481 | 0.0077 | 0.6449 | 1,990,656 |
| 20 | raven=31 | 31 | True | 31 | True | 1 | False | 0.8964 | 0.00574 | 0.6999 | 1,990,656 |
| 21 | granite=25 | 25 | True | 25 | True | 2 | False | 0.947 | 0.05127 | 1.2053 | 1,990,656 |
| 22 | thistle=55 | 55 | True | 55 | True | 1 | False | 1.0695 | 0.01081 | 0.5352 | 1,941,504 |
| 23 | hazel=96 | 96 | True | 96 | True | 1 | False | 1.1865 | 0.01738 | 1.2241 | 1,990,656 |
| 24 | opal=84 | 84 | True | 84 | True | 1 | False | 0.7489 | 0.00545 | 0.9132 | 1,990,656 |
| 25 | gorse=68 | 68 | True | 68 | True | 1 | False | 0.749 | 0.00771 | 1.3963 | 1,966,080 |
| 26 | willow=24 | 24 | True | 24 | True | 1 | False | 0.636 | 0.01909 | 0.5473 | 1,990,656 |
| 27 | meadow=35 | 35 | True | 35 | True | 1 | False | 0.6068 | 0.01054 | 0.659 | 1,990,656 |
| 28 | jasper=27 | 27 | True | 27 | True | 1 | False | 0.5146 | 0.00405 | 0.5271 | 2,015,232 |
| 29 | cobalt=32 | 32 | True | 32 | True | 15 | False | 0.4653 | 0.00326 | 0.4952 | 1,966,080 |
| 30 | pelican=62 | 62 | True | 62 | True | 1 | False | 0.4221 | 0.00459 | 0.4321 | 1,990,656 |
| 31 | estuary=77 | 77 | True | 77 | True | 1 | False | 0.4414 | 0.00424 | 0.5052 | 1,990,656 |
| 32 | falcon=94 | 94 | True | 94 | True | 5 | False | 0.4293 | 0.00419 | 0.4695 | 1,966,080 |
| 33 | lagoon=30 | 30 | True | 30 | True | 1 | False | 0.4035 | 0.00356 | 0.4221 | 1,966,080 |
| 34 | harbor=37 | 37 | True | 37 | True | 1 | False | 0.4215 | 0.00321 | 0.4067 | 1,990,656 |
| 35 | indigo=64 | 64 | True | 64 | True | 4 | False | 0.5375 | 0.00401 | 0.5345 | 1,990,656 |
| 36 | magnolia=72 | 72 | True | 72 | True | 1 | False | 0.4265 | 0.00402 | 0.4387 | 2,015,232 |
| 37 | hazel=11 | 11 | True | 11 | True | 1 | False | 1.3672 | 0.0551 | 0.8936 | 2,015,232 |
| 38 | magnolia=62 | 62 | True | 62 | True | 1 | False | 0.861 | 0.01183 | 0.8613 | 2,039,808 |
| 39 | clover=68 | 68 | True | 68 | True | 1 | False | 1.9491 | 0.00721 | 3.1774 | 1,966,080 |
| 40 | pelican=27 | 27 | True | 27 | True | 1 | False | 0.6085 | 0.00376 | 1.3627 | 1,966,080 |
| 41 | bramble=89 | 89 | True | 89 | True | 1 | False | 1.0049 | 0.0046 | 0.5136 | 1,990,656 |
| 42 | falcon=22 | 22 | True | 22 | True | 5 | False | 0.4392 | 0.00413 | 0.503 | 1,990,656 |
| 43 | clover=86 | 86 | True | 86 | True | 1 | False | 0.4824 | 0.00396 | 0.5663 | 2,015,232 |
| 44 | meadow=67 | 67 | True | 67 | True | 1 | False | 0.5126 | 0.00463 | 0.5369 | 1,966,080 |
| 45 | zephyr=95 | 95 | True | 95 | True | 1 | False | 0.6192 | 0.00588 | 0.8124 | 2,015,232 |
| 46 | nectar=62 | 62 | True | 62 | True | 1 | False | 1.8796 | 0.01172 | 1.3957 | 2,015,232 |
| 47 | quartz=45 | 45 | True | 45 | True | 1 | False | 0.6445 | 0.01609 | 1.4686 | 2,015,232 |
| 48 | juniper=38 | 38 | True | 38 | True | 1 | False | 0.9447 | 0.2693 | 1.7983 | 1,966,080 |
| 49 | flint=25 | 25 | True | 25 | True | 1 | False | 1.2375 | 0.00567 | 0.8399 | 2,015,232 |

## Intervention battery (chat probe)

Receiver next-token full-vocab KL/L1 divergence vs condition A (correct cache) at the first decoding position. Every probe runs on a fresh deepcopy with use_cache=False; the A-vs-A double-probe sanity KL is 0.0 (must be 0).

| Condition | Correct | Mean KL vs A | Mean L1 vs A | Mean gen (s) |
|---|---|---|---|---|
| A_correct_cache | 20/20 | 0.0 | 0.0 | 0.8228 |
| B_no_cache | 0/20 | 2.416766 | 1.734372 | 0.631 |
| C_zeroed_cache | 0/20 | 15.257282 | 1.998105 | 1.4684 |
| D_random_cache | 0/20 | 12.003527 | 1.998214 | 1.4263 |
| E_unrelated_cache | 0/20 | 2.543691 | 1.764373 | 0.4995 |
| F_full_text | 20/20 | 0.0 | 2e-06 | 1.1285 |
| G_compact_text | 18/20 | 0.259071 | 0.4731 | 1.0973 |
| H_shuffled_cache | 20/20 | 0.0 | 1e-06 | 0.6336 |
| H2_unpaired_shuffle | 1/20 | 7.561553 | 1.940283 | 1.3127 |
| I_truncated_cache | 19/20 | 0.024065 | 0.08909 | 0.6519 |
| J_covgauss_cache | 0/20 | 8.542137 | 1.93358 | 1.2536 |
| K_haar_cache | 0/20 | 9.783115 | 1.991705 | 1.8903 |

- Sender turn: 81 tokens | full text: 104 tokens | compact text: 79 tokens | cache fp32: 1,990,656 bytes
- Conditions: A correct cache; B no cache; C zeroed; D per-tensor Gaussian (mean/std matched); E unrelated-task cache; F full text (positive control); G compact text; H consistent shuffle (order-invariance control, null by construction); H2 unpaired shuffle (independent k/v permutations, breaks pairing); I truncated (sender user turn only); J CovGauss-mu (per layer/head mean+cov matched); K Haar rotation (per layer/tensor head-dim rotation).

## SmolLM2-135M-Instruct

- Model: `HuggingFaceTB/SmolLM2-135M-Instruct` @ revision `12fd25f77366fa6b3b4b768ec3050bf629380bac`
- Parameters: 134,515,008 | layers: 30 | hidden: 576 | vocab: 49152 | dtype: torch.float32 | attn: eager | device: CPU
- Load time: 6.76s | RSS after load: 868.6 MiB | RSS peak: 967.8 MiB | RSS end: 963.1 MiB

### Chat-template probe (primary)

- With-cache correct: **15/50**
- Full-text correct (positive control): **15/50**
- No-cache correct: **0/50**
- Proven trials (with correct AND without wrong): **15/50**
- Positive control pass (full >= 80%): **False**
- With-cache within 5 points of full text: **True**
- With-cache beats no-cache: **True**
- Paired bootstrap with-vs-without: diff=0.3, 95% CI [0.18, 0.4205], P(diff>0)=1.0
- **GATE PASS: False**
- Mean sender forward: 0.6261s | mean transfer (deepcopy): 0.05156s | mean receiver generation: 1.2084s | mean fresh generation: 1.4168s | mean full-text generation: 1.4645s
- Mean cache artifact: 3,848,601 bytes fp32 (3758.4 KiB) | 1,924,300 bytes fp16 | vs text payload ~51 bytes
- Mean sender tokens: 83

#### Per-trial records

| Trial | Target | With-cache | OK | Full-text | OK | No-cache | OK | Sender fwd (s) | Transfer (s) | Recv gen (s) | Cache fp32 (bytes) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | lantern=98 | Number: 22 | False | Number: 22 | False | The number that is paired ... | False | 1.3361 | 0.31192 | 4.3296 | 3,824,640 |
| 1 | marble=29 | The number is 60. | False | The number is 60. | False | The number that is paired ... | False | 0.4517 | 0.38691 | 0.8115 | 3,824,640 |
| 2 | saffron=61 | Saffron is 79. | False | Saffron is 79. | False | The number that is paired ... | False | 2.4133 | 0.26244 | 4.4037 | 3,870,720 |
| 3 | obsidian=55 | Obsidian is 99. | False | Obsidian is 99. | False | Obsidian is paired with 10 | False | 0.3066 | 0.09343 | 1.4116 | 3,778,560 |
| 4 | fern=48 | The number that is paired ... | False | The number that is paired ... | False | The number that is paired ... | False | 0.3592 | 0.01167 | 1.5734 | 3,916,800 |
| 5 | opal=97 | Number: 97 | True | Number: 97 | True | The number that is paired ... | False | 1.3906 | 0.02733 | 0.7458 | 3,824,640 |
| 6 | walnut=62 | The number that is paired ... | False | The number that is paired ... | False | The number that is paired ... | False | 4.3333 | 0.01511 | 3.074 | 3,916,800 |
| 7 | juniper=80 | Number: 77 | False | Number: 77 | False | The number that is paired ... | False | 0.5507 | 0.20635 | 0.9525 | 3,916,800 |
| 8 | jasper=91 | Number: 91 | True | Number: 91 | True | The number that is paired ... | False | 0.6725 | 0.00473 | 0.7833 | 3,824,640 |
| 9 | saffron=82 | Number: 48 | False | Number: 48 | False | The number that is paired ... | False | 0.5816 | 0.02361 | 2.1391 | 3,870,720 |
| 10 | pelican=76 | Paired with 'pelican': | False | Paired with 'pelican': | False | The number that is paired ... | False | 0.7171 | 0.42652 | 1.8645 | 3,778,560 |
| 11 | saffron=53 | Number: 90 | False | Number: 90 | False | The number that is paired ... | False | 0.5303 | 0.00684 | 2.6936 | 3,916,800 |
| 12 | gorse=87 | Gorse: 87 | True | Gorse: 87 | True | The number that is paired ... | False | 1.8957 | 0.01331 | 3.7551 | 3,916,800 |
| 13 | umber=38 | Number: 42 | False | Number: 42 | False | The number that is paired ... | False | 0.4872 | 0.0113 | 1.6791 | 3,778,560 |
| 14 | obsidian=97 | Obsidian is paired with 97 | True | Obsidian is paired with 97 | True | Obsidian is paired with 10 | False | 0.499 | 0.00624 | 1.4959 | 3,870,720 |
| 15 | raven=59 | The number that is paired ... | False | The number that is paired ... | False | The number that is paired ... | False | 1.8464 | 0.01839 | 2.1869 | 3,824,640 |
| 16 | willow=51 | Number: 7 | False | Number: 7 | False | The number paired with 'wi... | False | 0.837 | 0.00967 | 0.3366 | 3,824,640 |
| 17 | lantern=68 | The number that is paired ... | False | The number that is paired ... | False | The number that is paired ... | False | 0.3837 | 0.00561 | 1.6429 | 3,824,640 |
| 18 | parsley=19 | Number: 92 | False | Number: 92 | False | The number that is paired ... | False | 2.3331 | 0.07134 | 1.4266 | 3,824,640 |
| 19 | gorse=30 | Gorse: 30 | True | Gorse: 30 | True | The number that is paired ... | False | 1.5041 | 0.00637 | 4.0986 | 3,870,720 |
| 20 | raven=31 | Marble: 59
Obs | False | Marble: 59
Obs | False | The number that is paired ... | False | 0.6806 | 0.36739 | 2.3293 | 3,824,640 |
| 21 | granite=25 | Reply with the number only. | False | Reply with the number only. | False | The number that is paired ... | False | 0.6043 | 0.06246 | 1.1128 | 3,824,640 |
| 22 | thistle=55 | Number: 18 | False | Number: 18 | False | The number that is paired ... | False | 0.4108 | 0.02341 | 0.6062 | 3,778,560 |
| 23 | hazel=96 | Hazel: 96 | True | Hazel: 96 | True | Hazel is paired with 1 | False | 0.425 | 0.00782 | 0.6393 | 3,916,800 |
| 24 | opal=84 | Palatinus. | False | Palatinus. | False | The number that is paired ... | False | 0.2718 | 0.03994 | 0.4717 | 3,824,640 |
| 25 | gorse=68 | Number: 69 | False | Number: 69 | False | The number that is paired ... | False | 0.2093 | 0.00468 | 0.4584 | 3,824,640 |
| 26 | willow=24 | Number: 27 | False | Number: 27 | False | The number paired with 'wi... | False | 0.2078 | 0.00485 | 0.4174 | 3,824,640 |
| 27 | meadow=35 | Meadow is paired with 3 | False | Meadow is paired with 3 | False | The number that is paired ... | False | 0.2108 | 0.0048 | 0.5143 | 3,916,800 |
| 28 | jasper=27 | Number: 62 | False | Number: 62 | False | The number that is paired ... | False | 0.2181 | 0.00739 | 0.4622 | 3,962,880 |
| 29 | cobalt=32 | Cobalt: 32 | True | Cobalt: 32 | True | The number that is paired ... | False | 0.2109 | 0.00681 | 0.5086 | 3,778,560 |
| 30 | pelican=62 | Paired with 'pelican': | False | Paired with 'pelican': | False | The number that is paired ... | False | 0.2437 | 0.00682 | 0.7718 | 3,824,640 |
| 31 | estuary=77 | The number that is paired ... | False | The number that is paired ... | False | The number that is paired ... | False | 0.2157 | 0.00722 | 0.6393 | 3,824,640 |
| 32 | falcon=94 | The number is 69. | False | The number is 69. | False | The number that is paired ... | False | 0.1899 | 0.00497 | 0.6521 | 3,824,640 |
| 33 | lagoon=30 | The number paired with 'la... | False | The number paired with 'la... | False | The number paired with 'la... | False | 0.1846 | 0.00591 | 0.7547 | 3,824,640 |
| 34 | harbor=37 | The number is 18. | False | The number is 18. | False | The number paired with 'ha... | False | 0.1726 | 0.00572 | 0.5731 | 3,824,640 |
| 35 | indigo=64 | Indigo: 64 | True | Indigo: 64 | True | The number that is paired ... | False | 0.2021 | 0.00983 | 0.475 | 3,824,640 |
| 36 | magnolia=72 | Number: 76 | False | Number: 76 | False | The number that is paired ... | False | 0.1829 | 0.00586 | 0.492 | 3,824,640 |
| 37 | hazel=11 | Hazel: 11 | True | Hazel: 11 | True | Hazel is paired with 1 | False | 0.2244 | 0.00676 | 0.667 | 3,916,800 |
| 38 | magnolia=62 | Number: 62 | True | Number: 62 | True | The number that is paired ... | False | 0.176 | 0.00556 | 0.3948 | 3,870,720 |
| 39 | clover=68 | Clover: 68 | True | Clover: 68 | True | The number that is paired ... | False | 0.2492 | 0.00543 | 0.5315 | 3,778,560 |
| 40 | pelican=27 | Paired with 'pelican': | False | Paired with 'pelican': | False | The number that is paired ... | False | 0.2604 | 0.00472 | 0.544 | 3,824,640 |
| 41 | bramble=89 | Bramble: 89 | True | Bramble: 89 | True | The number that is paired ... | False | 0.1998 | 0.00577 | 0.5163 | 3,870,720 |
| 42 | falcon=22 | The number that is paired ... | False | The number that is paired ... | False | The number that is paired ... | False | 0.2002 | 0.00469 | 0.4814 | 3,824,640 |
| 43 | clover=86 | Reply with the number only. | False | Reply with the number only. | False | The number that is paired ... | False | 0.3911 | 0.00691 | 0.7405 | 3,870,720 |
| 44 | meadow=67 | Meadow: 67 | True | Meadow: 67 | True | The number that is paired ... | False | 0.1966 | 0.00515 | 0.5283 | 3,870,720 |
| 45 | zephyr=95 | Number: 60 | False | Number: 60 | False | Zephyr is paired with | False | 0.2186 | 0.00518 | 0.4693 | 3,916,800 |
| 46 | nectar=62 | Number: 62 | True | Number: 62 | True | The number that is paired ... | False | 0.2093 | 0.00441 | 0.4289 | 3,870,720 |
| 47 | quartz=45 | Quartz is paired with 93 | False | Quartz is paired with 93 | False | The number that is paired ... | False | 0.2435 | 0.00633 | 0.6908 | 3,824,640 |
| 48 | juniper=38 | The number is 85. | False | The number is 85. | False | The number that is paired ... | False | 0.2219 | 0.01346 | 0.5278 | 3,916,800 |
| 49 | flint=25 | Flint: 25 | True | Flint: 25 | True | The number that is paired ... | False | 0.2432 | 0.00854 | 0.6173 | 3,824,640 |

### Raw induction-format probe (auxiliary)

- With-cache correct: **49/50**
- Full-text correct (positive control): **49/50**
- No-cache correct: **0/50**
- Proven trials (with correct AND without wrong): **49/50**
- Positive control pass (full >= 80%): **True**
- With-cache within 5 points of full text: **True**
- With-cache beats no-cache: **True**
- Paired bootstrap with-vs-without: diff=0.98, 95% CI [0.94, 1.0], P(diff>0)=1.0
- **GATE PASS: True**
- Mean sender forward: 0.3116s | mean transfer (deepcopy): 0.01939s | mean receiver generation: 0.7981s | mean fresh generation: 0.803s | mean full-text generation: 0.9953s
- Mean cache artifact: 2,729,779 bytes fp32 (2665.8 KiB) | 1,364,889 bytes fp16 | vs text payload ~51 bytes
- Mean sender tokens: 59

#### Per-trial records

| Trial | Target | With-cache | OK | Full-text | OK | No-cache | OK | Sender fwd (s) | Transfer (s) | Recv gen (s) | Cache fp32 (bytes) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | lantern=98 | 98, cactus=20, | True | 98, cactus=20, | True | lamp, light=light, torch | False | 0.1467 | 0.00701 | 0.5095 | 2,626,560 |
| 1 | marble=29 | 29, walnut=92, | True | 29, walnut=92, | True | 3, 20, 2 | False | 0.1709 | 0.00517 | 0.5446 | 2,626,560 |
| 2 | saffron=61 | 79, basil=61, | True | 79, basil=61, | True | saffron, saffron=saff | False | 0.1647 | 0.00564 | 0.489 | 2,626,560 |
| 3 | obsidian=55 | 55, magnolia=99 | True | 55, magnolia=99 | True | 2000, obsidian= | False | 0.1625 | 0.00608 | 0.4926 | 2,810,880 |
| 4 | fern=48 | 48, juniper=88 | True | 48, juniper=88 | True | fern, fern=fern, | False | 0.2167 | 0.00475 | 0.5461 | 2,626,560 |
| 5 | opal=97 | 97, meadow=30, | True | 97, meadow=30, | True | opar, amethyst=am | False | 0.1407 | 0.00562 | 0.5767 | 2,626,560 |
| 6 | walnut=62 | 62, hazel=61, | True | 62, hazel=61, | True | 1, apple=2, pear= | False | 0.2152 | 0.00588 | 0.6282 | 2,810,880 |
| 7 | juniper=80 | 80, dahlia=3 | True | 80, dahlia=3 | True | 1, juniper=2, jun | False | 0.2365 | 0.00429 | 1.1423 | 2,903,040 |
| 8 | jasper=91 | 82, jasper=82 | False | 82, jasper=82 | False | 1, jasper2=2, | False | 0.2928 | 0.00596 | 0.7194 | 2,810,880 |
| 9 | saffron=82 | 82, walnut=69, | True | 82, walnut=69, | True | saffron, saffron=saff | False | 0.2223 | 0.00642 | 0.6083 | 2,626,560 |
| 10 | pelican=76 | 76, umber=56 | True | 76, umber=56 | True | pelican+pigeon | False | 0.273 | 0.00558 | 0.7687 | 2,718,720 |
| 11 | saffron=53 | 53, compass=90, | True | 53, compass=90, | True | saffron, saffron=saff | False | 0.2632 | 0.00555 | 0.5466 | 2,903,040 |
| 12 | gorse=87 | 87, meadow=49, | True | 87, meadow=49, | True | gorse, hay=hay, oats | False | 0.2442 | 0.02011 | 0.67 | 2,626,560 |
| 13 | umber=38 | 38, opal=42 | True | 38, opal=42 | True | 1, 2, 3, | False | 0.1685 | 0.0308 | 0.5507 | 2,810,880 |
| 14 | obsidian=97 | 97, raven=57 | True | 97, raven=57 | True | 2000, obsidian= | False | 0.3878 | 0.02928 | 0.8121 | 2,718,720 |
| 15 | raven=59 | 59, magnolia=63 | True | 59, magnolia=63 | True | raven+1, eagle=e | False | 0.3715 | 0.01284 | 0.7161 | 2,718,720 |
| 16 | willow=51 | 51, kelp=48, | True | 51, kelp=48, | True | 0, willow2=1, willow | False | 0.1863 | 0.04743 | 0.7818 | 2,626,560 |
| 17 | lantern=68 | 68, flint=52, | True | 68, flint=52, | True | lamp, light=light, torch | False | 0.1848 | 0.00631 | 0.5274 | 2,534,400 |
| 18 | parsley=19 | 19, meadow=66, | True | 19, meadow=66, | True | paprika, garlic=garlic | False | 0.212 | 0.00617 | 0.5658 | 2,718,720 |
| 19 | gorse=30 | 30, juniper=10 | True | 30, juniper=10 | True | gorse, hay=hay, oats | False | 0.18 | 0.00453 | 0.5711 | 2,810,880 |
| 20 | raven=31 | 31, obsidian=99 | True | 31, obsidian=99 | True | raven+1, eagle=e | False | 0.2299 | 0.00794 | 0.8109 | 2,718,720 |
| 21 | granite=25 | 25, pelican=67 | True | 25, pelican=67 | True | 1000, basalt=1 | False | 0.1655 | 0.01596 | 0.5886 | 2,718,720 |
| 22 | thistle=55 | 55, fern=18. | True | 55, fern=18. | True | tistle, daisy=daisy | False | 0.5093 | 0.00484 | 0.7603 | 2,810,880 |
| 23 | hazel=96 | 96, obsidian=49 | True | 96, obsidian=49 | True | 5, brown=4, orange= | False | 0.1446 | 0.00502 | 0.4764 | 2,718,720 |
| 24 | opal=84 | 84, cobalt=52, | True | 84, cobalt=52, | True | opar, amethyst=am | False | 0.2012 | 0.01614 | 1.1511 | 2,718,720 |
| 25 | gorse=68 | 68, falcon=74, | True | 68, falcon=74, | True | gorse, hay=hay, oats | False | 0.2217 | 0.00647 | 0.6036 | 2,810,880 |
| 26 | willow=24 | 24, flint=31, | True | 24, flint=31, | True | 0, willow2=1, willow | False | 0.2194 | 0.00621 | 0.5298 | 2,534,400 |
| 27 | meadow=35 | 35, bramble=3 | True | 35, bramble=3 | True | 1, meadow2=2, meadow | False | 0.2346 | 0.02536 | 1.0571 | 2,718,720 |
| 28 | jasper=27 | 27, lantern=62, | True | 27, lantern=62, | True | 1, jasper2=2, | False | 0.1389 | 0.00552 | 0.5427 | 2,810,880 |
| 29 | cobalt=32 | 32, obsidian=76 | True | 32, obsidian=76 | True | 20, iron=25, | False | 0.2253 | 0.00473 | 1.165 | 2,903,040 |
| 30 | pelican=62 | 62, parsley=48, | True | 62, parsley=48, | True | pelican+pigeon | False | 0.4056 | 0.00489 | 2.0732 | 2,626,560 |
| 31 | estuary=77 | 77, indigo=20. | True | 77, indigo=20. | True | new_estuary, inlet=new | False | 0.4006 | 0.12853 | 1.0496 | 2,626,560 |
| 32 | falcon=94 | 94, ember=69 | True | 94, ember=69 | True | Falco coelegas, eagle | False | 1.0812 | 0.20124 | 1.4333 | 2,810,880 |
| 33 | lagoon=30 | 30, raven=93 | True | 30, raven=93 | True | lakes, river=rivers, sea | False | 0.5005 | 0.07457 | 1.2282 | 2,718,720 |
| 34 | harbor=37 | 37, gorse=32 | True | 37, gorse=32 | True | Houston, state=Texas, | False | 0.4227 | 0.00676 | 0.795 | 2,718,720 |
| 35 | indigo=64 | 64, gorse=15 | True | 64, gorse=15 | True | 1, blue=2, red= | False | 0.238 | 0.00732 | 1.1401 | 2,718,720 |
| 36 | magnolia=72 | 72, violin=53, | True | 72, violin=53, | True | magnolia, magnolia=magnolia | False | 0.2304 | 0.00937 | 0.5789 | 2,626,560 |
| 37 | hazel=11 | 11, tundra=53, | True | 11, tundra=53, | True | 5, brown=4, orange= | False | 0.3217 | 0.00654 | 0.5208 | 2,718,720 |
| 38 | magnolia=62 | 62, lantern=39, | True | 62, lantern=39, | True | magnolia, magnolia=magnolia | False | 0.2166 | 0.00611 | 0.6329 | 2,626,560 |
| 39 | clover=68 | 68, thistle=75 | True | 68, thistle=75 | True | 1, clover=2, alfalfa= | False | 0.2772 | 0.02446 | 0.6887 | 2,718,720 |
| 40 | pelican=27 | 27, gorse=84 | True | 27, gorse=84 | True | pelican+pigeon | False | 0.3885 | 0.01437 | 1.2451 | 2,995,200 |
| 41 | bramble=89 | 89, clover=93, | True | 89, clover=93, | True | bramble, 100 | False | 0.4835 | 0.02298 | 1.0341 | 2,810,880 |
| 42 | falcon=22 | 22, walnut=98, | True | 22, walnut=98, | True | Falco coelegas, eagle | False | 0.2969 | 0.01789 | 1.3861 | 2,626,560 |
| 43 | clover=86 | 86, bramble=1 | True | 86, bramble=1 | True | 1, clover=2, alfalfa= | False | 0.5003 | 0.00916 | 0.7861 | 2,903,040 |
| 44 | meadow=67 | 67, bramble=7 | True | 67, bramble=7 | True | 1, meadow2=2, meadow | False | 0.872 | 0.00729 | 0.9405 | 2,810,880 |
| 45 | zephyr=95 | 95, indigo=14, | True | 95, indigo=14, | True | zephyr+1, and | False | 0.3712 | 0.00613 | 1.0823 | 2,903,040 |
| 46 | nectar=62 | 62, violin=57, | True | 62, violin=57, | True | 1, pollen=2, fruit= | False | 0.3334 | 0.0165 | 0.5843 | 2,626,560 |
| 47 | quartz=45 | 45, falcon=90, | True | 45, falcon=90, | True | 1000, diamond=1 | False | 0.4646 | 0.03571 | 0.7328 | 2,718,720 |
| 48 | juniper=38 | 38, opal=85 | True | 38, opal=85 | True | 1, juniper=2, jun | False | 0.2576 | 0.01368 | 0.707 | 2,718,720 |
| 49 | flint=25 | 25, lantern=16, | True | 25, lantern=16, | True | 1, steel=2, wood= | False | 0.7856 | 0.03255 | 0.8136 | 2,718,720 |

## Intervention battery (chat probe)

Receiver next-token full-vocab KL/L1 divergence vs condition A (correct cache) at the first decoding position. Every probe runs on a fresh deepcopy with use_cache=False; the A-vs-A double-probe sanity KL is 0.0 (must be 0).

| Condition | Correct | Mean KL vs A | Mean L1 vs A | Mean gen (s) |
|---|---|---|---|---|
| A_correct_cache | 5/20 | 0.0 | 0.0 | 1.6224 |
| B_no_cache | 0/20 | 1.209449 | 1.0216 | 2.4189 |
| C_zeroed_cache | 0/20 | 2.118687 | 1.32607 | 1.7824 |
| D_random_cache | 0/20 | 8.100362 | 1.880028 | 1.1875 |
| E_unrelated_cache | 0/20 | 0.778079 | 0.834048 | 1.4186 |
| F_full_text | 5/20 | 0.0 | 0.0 | 1.229 |
| G_compact_text | 1/20 | 0.864005 | 0.884471 | 1.8452 |
| H_shuffled_cache | 5/20 | 0.0 | 5e-06 | 1.3734 |
| H2_unpaired_shuffle | 0/20 | 3.128096 | 1.500315 | 1.3858 |
| I_truncated_cache | 1/20 | 0.742832 | 0.840975 | 1.2485 |
| J_covgauss_cache | 0/20 | 4.005751 | 1.650704 | 1.2717 |
| K_haar_cache | 0/20 | 7.830962 | 1.873005 | 1.3846 |

- Sender turn: 83 tokens | full text: 108 tokens | compact text: 79 tokens | cache fp32: 3,824,640 bytes
- Conditions: A correct cache; B no cache; C zeroed; D per-tensor Gaussian (mean/std matched); E unrelated-task cache; F full text (positive control); G compact text; H consistent shuffle (order-invariance control, null by construction); H2 unpaired shuffle (independent k/v permutations, breaks pairing); I truncated (sender user turn only); J CovGauss-mu (per layer/head mean+cov matched); K Haar rotation (per layer/tensor head-dim rotation).

## Bugfix history

The first run (2026-10-16, before the protocol-v2 fixes) reported 0/10 on every
condition including the full-text positive control. Root causes, diagnosed in
RESEARCH-NOTES.md section 4.0 and fixed before the re-run:

1. Prompts were raw text without the model chat template, so even full-text
   transfer failed (broken probe ceiling). Fixed: all prompts render through the
   model chat template; the sender turn is completed by a fixed assistant
   acknowledgment so the cache ends at a turn boundary.
2. interventions.py probed logits on a live shared cache without use_cache=False,
   so every probe extended the cache in place (A-vs-A KL 1.99 instead of 0).
   Fixed: every probe runs on a fresh deepcopy with use_cache=False; the battery
   now includes an A-vs-A double-probe sanity check (KL must be exactly 0).
3. Experiment B fed raw post-final-norm hidden states (std 2.4) back through
   inputs_embeds without the LatentMAS norm rescale (target: mean input-embedding
   row norm), producing 48-85 L2 drift per step. Fixed: the realignment
   (rescale, optional ridge map) from RESEARCH-NOTES section 1.3 is applied per
   latent step; the unmodified feed remains as the "none" transform for
   comparison.
