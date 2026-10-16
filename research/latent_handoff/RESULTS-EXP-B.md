# RESULTS - Experiment B: Bounded Latent Continuation (protocol v2)

LatentMAS-style latent loop ported to transformers 5.20.0 (RESEARCH-NOTES
sections 1.2-1.3). The sender's final hidden state (post final-norm) is
re-aligned and fed back through `inputs_embeds` for a bounded number of
latent steps, extending the KV cache one position per step; the extended
cache is then transferred to Agent B. No text is decoded and re-encoded
anywhere in the loop.

Transforms: `none` (raw hidden, the broken v1 baseline kept for comparison),
`rescale` (row-wise rescale to the mean input-embedding row norm, the
LatentMAS default), `rescale_ridge` (ridge map then rescale). Control:
`random` (fixed random vector rescaled to the target norm).

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

## Qwen2.5-0.5B-Instruct (chat probe)

- Model: `Qwen/Qwen2.5-0.5B-Instruct` @ revision `7ae557604adf67be50417f59c2c2f167def9a775`
- Parameters: 494,032,768 | layers: 24 | hidden: 896 | vocab: 151936 | dtype: torch.float32 | attn: eager | device: CPU
- Load time: 29.85s | RSS after load: 2292.3 MiB | RSS peak: 2443.1 MiB | RSS end: 2436.4 MiB

## Projection check (hidden state -> input embedding space)

- hidden_size: 896 | embedding dim: 896 | dims match: **True** | needs projection: False
- Token embedding stats: {'mean': -4e-05, 'std': 0.01647, 'min': -0.08789, 'max': 0.06787, 'fro_norm': 4.438}
- Last hidden state stats: {'mean': 0.36874, 'std': 8.02046, 'min': -56.14058, 'max': 65.39924, 'fro_norm': 240.1981}
- hidden_states[-1] is post final-norm (LM-head input); fed back via inputs_embeds after the configured realignment.

## Realigner metadata

- none: target_norm=0.45263, embedding_std=0.01525, ridge max diagonal deviation from 1=None
- rescale: target_norm=0.45263, embedding_std=0.01525, ridge max diagonal deviation from 1=None
- rescale_ridge: target_norm=0.45263, embedding_std=0.01525, ridge max diagonal deviation from 1=4e-06

## Latent-step grid (private-fact probe, chat receiver)

| Config | Correct | Mean step (s) | Mean drift | Mean cos-to-first | Mean next-entropy | Mean top1 mass | Recv gen (s) |
|---|---|---|---|---|---|---|---|
| none_steps0 | 10/10 | - | - | - | - | - | 0.7928 |
| none_steps1 | 8/10 | 0.2135 | - | - | 0.0023 | 0.9998 | 0.6376 |
| none_steps2 | 8/10 | 0.1535 | 112.4703 | 0.9061 | 0.5302 | 0.8012 | 0.9946 |
| none_steps4 | 2/10 | 0.153 | 95.5192 | 0.7597 | 1.204 | 0.7802 | 1.0342 |
| none_steps8 | 0/10 | 0.1581 | 81.9376 | 0.5556 | 4.0846 | 0.4374 | 1.371 |
| rescale_steps0 | 10/10 | - | - | - | - | - | 0.7237 |
| rescale_steps1 | 9/10 | 0.1597 | - | - | 5.4019 | 0.126 | 0.6025 |
| rescale_steps2 | 3/10 | 0.1468 | 0.5778 | 0.1852 | 4.5704 | 0.233 | 1.1123 |
| rescale_steps4 | 0/10 | 0.1475 | 0.3992 | 0.2158 | 5.5212 | 0.1733 | 1.2237 |
| rescale_steps8 | 0/10 | 0.1499 | 0.2724 | 0.2202 | 6.1731 | 0.133 | 1.3595 |
| rescale_ridge_steps0 | 10/10 | - | - | - | - | - | 0.5955 |
| rescale_ridge_steps1 | 9/10 | 0.1373 | - | - | 5.402 | 0.126 | 0.8237 |
| rescale_ridge_steps2 | 3/10 | 0.1445 | 0.5778 | 0.1852 | 4.5704 | 0.233 | 1.1297 |
| rescale_ridge_steps4 | 0/10 | 0.1547 | 0.3992 | 0.2158 | 5.5212 | 0.1733 | 1.2526 |
| rescale_ridge_steps8 | 0/10 | 0.1403 | 0.2724 | 0.2202 | 6.1729 | 0.133 | 1.3291 |
| random_steps1 | 10/10 | 0.1482 | - | - | 5.1677 | 0.2236 | 0.7239 |
| random_steps2 | 10/10 | 0.1446 | - | - | 5.5267 | 0.2026 | 0.6733 |
| random_steps4 | 10/10 | 0.1468 | - | - | 5.3454 | 0.2346 | 0.7619 |
| random_steps8 | 9/10 | 0.1579 | - | - | 5.1578 | 0.2558 | 0.7891 |

## Per-step telemetry (trial 0)

### none_steps1

- step 1: latent_norm=240.1981 latent_std=8.02046 hidden_std=8.94491 drift=None cos_first=None entropy=0.003 top1=0.9998

### none_steps2

- step 1: latent_norm=240.1981 latent_std=8.02046 hidden_std=8.94491 drift=None cos_first=None entropy=0.003 top1=0.9998
- step 2: latent_norm=267.888 latent_std=8.94491 hidden_std=9.80668 drift=112.7817 cos_first=0.9071 entropy=1.1388 top1=0.5634

### none_steps4

- step 1: latent_norm=240.1981 latent_std=8.02046 hidden_std=8.94491 drift=None cos_first=None entropy=0.003 top1=0.9998
- step 2: latent_norm=267.888 latent_std=8.94491 hidden_std=9.80668 drift=112.7817 cos_first=0.9071 entropy=1.1388 top1=0.5634
- step 3: latent_norm=293.7459 latent_std=9.80668 hidden_std=10.54843 drift=92.7677 cos_first=0.7561 entropy=1.0501 top1=0.8593
- step 4: latent_norm=315.9814 latent_std=10.54843 hidden_std=11.26591 drift=79.2072 cos_first=0.6264 entropy=3.1138 top1=0.5916

### none_steps8

- step 1: latent_norm=240.1981 latent_std=8.02046 hidden_std=8.94491 drift=None cos_first=None entropy=0.003 top1=0.9998
- step 2: latent_norm=267.888 latent_std=8.94491 hidden_std=9.80668 drift=112.7817 cos_first=0.9071 entropy=1.1388 top1=0.5634
- step 3: latent_norm=293.7459 latent_std=9.80668 hidden_std=10.54843 drift=92.7677 cos_first=0.7561 entropy=1.0501 top1=0.8593
- step 4: latent_norm=315.9814 latent_std=10.54843 hidden_std=11.26591 drift=79.2072 cos_first=0.6264 entropy=3.1138 top1=0.5916
- step 5: latent_norm=337.4332 latent_std=11.26591 hidden_std=12.04457 drift=73.3256 cos_first=0.525 entropy=7.2655 top1=0.0506
- step 6: latent_norm=360.6715 latent_std=12.04457 hidden_std=12.85439 drift=73.8319 cos_first=0.4408 entropy=7.5442 top1=0.0903
- step 7: latent_norm=384.8401 latent_std=12.85439 hidden_std=13.58381 drift=72.9683 cos_first=0.3679 entropy=6.7167 top1=0.166
- step 8: latent_norm=406.6349 latent_std=13.58381 hidden_std=14.15196 drift=65.5988 cos_first=0.3078 entropy=6.2916 top1=0.1188

### rescale_steps1

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.6367 drift=None cos_first=None entropy=5.5897 top1=0.1286

### rescale_steps2

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.6367 drift=None cos_first=None entropy=5.5897 top1=0.1286
- step 2: latent_norm=0.4526 latent_std=0.01513 hidden_std=7.20202 drift=0.5756 cos_first=0.1914 entropy=2.8463 top1=0.3723

### rescale_steps4

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.6367 drift=None cos_first=None entropy=5.5897 top1=0.1286
- step 2: latent_norm=0.4526 latent_std=0.01513 hidden_std=7.20202 drift=0.5756 cos_first=0.1914 entropy=2.8463 top1=0.3723
- step 3: latent_norm=0.4526 latent_std=0.01513 hidden_std=9.20173 drift=0.3641 cos_first=0.235 entropy=6.2217 top1=0.1247
- step 4: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.83824 drift=0.3515 cos_first=0.184 entropy=6.8757 top1=0.1147

### rescale_steps8

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.6367 drift=None cos_first=None entropy=5.5897 top1=0.1286
- step 2: latent_norm=0.4526 latent_std=0.01513 hidden_std=7.20202 drift=0.5756 cos_first=0.1914 entropy=2.8463 top1=0.3723
- step 3: latent_norm=0.4526 latent_std=0.01513 hidden_std=9.20173 drift=0.3641 cos_first=0.235 entropy=6.2217 top1=0.1247
- step 4: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.83824 drift=0.3515 cos_first=0.184 entropy=6.8757 top1=0.1147
- step 5: latent_norm=0.4526 latent_std=0.01513 hidden_std=9.20798 drift=0.2417 cos_first=0.2577 entropy=6.4616 top1=0.0967
- step 6: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.66235 drift=0.1497 cos_first=0.2478 entropy=6.7954 top1=0.0588
- step 7: latent_norm=0.4526 latent_std=0.01512 hidden_std=8.79297 drift=0.1413 cos_first=0.2319 entropy=7.0765 top1=0.0813
- step 8: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.46402 drift=0.0827 cos_first=0.2365 entropy=6.885 top1=0.0741

### rescale_ridge_steps1

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.63671 drift=None cos_first=None entropy=5.5897 top1=0.1286

### rescale_ridge_steps2

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.63671 drift=None cos_first=None entropy=5.5897 top1=0.1286
- step 2: latent_norm=0.4526 latent_std=0.01513 hidden_std=7.20202 drift=0.5756 cos_first=0.1914 entropy=2.8463 top1=0.3723

### rescale_ridge_steps4

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.63671 drift=None cos_first=None entropy=5.5897 top1=0.1286
- step 2: latent_norm=0.4526 latent_std=0.01513 hidden_std=7.20202 drift=0.5756 cos_first=0.1914 entropy=2.8463 top1=0.3723
- step 3: latent_norm=0.4526 latent_std=0.01513 hidden_std=9.20173 drift=0.3641 cos_first=0.235 entropy=6.2217 top1=0.1247
- step 4: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.83821 drift=0.3515 cos_first=0.184 entropy=6.8756 top1=0.1147

### rescale_ridge_steps8

- step 1: latent_norm=0.4526 latent_std=0.01511 hidden_std=8.63671 drift=None cos_first=None entropy=5.5897 top1=0.1286
- step 2: latent_norm=0.4526 latent_std=0.01513 hidden_std=7.20202 drift=0.5756 cos_first=0.1914 entropy=2.8463 top1=0.3723
- step 3: latent_norm=0.4526 latent_std=0.01513 hidden_std=9.20173 drift=0.3641 cos_first=0.235 entropy=6.2217 top1=0.1247
- step 4: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.83821 drift=0.3515 cos_first=0.184 entropy=6.8756 top1=0.1147
- step 5: latent_norm=0.4526 latent_std=0.01513 hidden_std=9.20796 drift=0.2417 cos_first=0.2577 entropy=6.4617 top1=0.0967
- step 6: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.66235 drift=0.1497 cos_first=0.2478 entropy=6.7955 top1=0.0587
- step 7: latent_norm=0.4526 latent_std=0.01512 hidden_std=8.79304 drift=0.1413 cos_first=0.2319 entropy=7.0766 top1=0.0813
- step 8: latent_norm=0.4526 latent_std=0.01513 hidden_std=8.46394 drift=0.0827 cos_first=0.2365 entropy=6.8849 top1=0.0741

### random_steps1

- step 1: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.3038 top1=0.0528

### random_steps2

- step 1: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.3038 top1=0.0528
- step 2: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=7.1537 top1=0.0868

### random_steps4

- step 1: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.3038 top1=0.0528
- step 2: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=7.1537 top1=0.0868
- step 3: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=5.5191 top1=0.22
- step 4: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.5745 top1=0.1783

### random_steps8

- step 1: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.3038 top1=0.0528
- step 2: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=7.1537 top1=0.0868
- step 3: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=5.5191 top1=0.22
- step 4: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.5745 top1=0.1783
- step 5: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.5078 top1=0.2082
- step 6: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.2496 top1=0.2547
- step 7: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=5.9696 top1=0.2602
- step 8: latent_norm=0.4526 latent_std=None hidden_std=None drift=None cos_first=None entropy=6.0292 top1=0.2785


## SmolLM2-135M-Instruct (chat probe)

- Model: `HuggingFaceTB/SmolLM2-135M-Instruct` @ revision `12fd25f77366fa6b3b4b768ec3050bf629380bac`
- Parameters: 134,515,008 | layers: 30 | hidden: 576 | vocab: 49152 | dtype: torch.float32 | attn: eager | device: CPU
- Load time: 6.76s | RSS after load: 868.6 MiB | RSS peak: 967.8 MiB | RSS end: 963.1 MiB

## Projection check (hidden state -> input embedding space)

- hidden_size: 576 | embedding dim: 576 | dims match: **True** | needs projection: False
- Token embedding stats: {'mean': -0.00036, 'std': 0.12123, 'min': -1.07812, 'max': 1.03906, 'fro_norm': 26.5058}
- Last hidden state stats: {'mean': 0.01016, 'std': 1.99887, 'min': -9.26196, 'max': 24.10439, 'fro_norm': 47.9318}
- hidden_states[-1] is post final-norm (LM-head input); fed back via inputs_embeds after the configured realignment.

## Realigner metadata

- none: target_norm=3.2021, embedding_std=0.13557, ridge max diagonal deviation from 1=None
- rescale: target_norm=3.2021, embedding_std=0.13557, ridge max diagonal deviation from 1=None
- rescale_ridge: target_norm=3.2021, embedding_std=0.13557, ridge max diagonal deviation from 1=2.2e-05

## Latent-step grid (private-fact probe, chat receiver)

| Config | Correct | Mean step (s) | Mean drift | Mean cos-to-first | Mean next-entropy | Mean top1 mass | Recv gen (s) |
|---|---|---|---|---|---|---|---|
| none_steps0 | 2/10 | - | - | - | - | - | 1.3231 |
| none_steps1 | 2/10 | 0.1403 | - | - | 1.9441 | 0.6259 | 1.1989 |
| none_steps2 | 2/10 | 0.1116 | 48.8983 | 0.5819 | 1.4491 | 0.6091 | 1.0749 |
| none_steps4 | 1/10 | 0.1552 | 23.5853 | 0.5334 | 1.1463 | 0.6475 | 1.3729 |
| rescale_steps0 | 2/10 | - | - | - | - | - | 1.4155 |
| rescale_steps1 | 2/10 | 0.1336 | - | - | 3.3469 | 0.3934 | 1.5376 |
| rescale_steps2 | 2/10 | 0.1261 | 2.6383 | 0.6605 | 2.5262 | 0.4658 | 1.2103 |
| rescale_steps4 | 2/10 | 0.1729 | 1.4182 | 0.5799 | 2.3186 | 0.4697 | 2.2808 |
| rescale_ridge_steps0 | 2/10 | - | - | - | - | - | 2.5852 |
| rescale_ridge_steps1 | 2/10 | 0.3891 | - | - | 3.347 | 0.3934 | 1.884 |
| rescale_ridge_steps2 | 2/10 | 0.1607 | 2.6382 | 0.6606 | 2.5264 | 0.4659 | 2.142 |
| rescale_ridge_steps4 | 2/10 | 0.2188 | 1.4182 | 0.58 | 2.3187 | 0.4697 | 1.788 |
| random_steps1 | 2/10 | 0.1154 | - | - | 4.4437 | 0.3033 | 1.7994 |
| random_steps2 | 2/10 | 0.2291 | - | - | 4.6252 | 0.312 | 1.6939 |
| random_steps4 | 2/10 | 0.1872 | - | - | 5.0134 | 0.2718 | 1.47 |

## Per-step telemetry (trial 0)

### none_steps1

- step 1: latent_norm=47.9318 latent_std=1.99887 hidden_std=2.38985 drift=None cos_first=None entropy=1.9462 top1=0.6239

### none_steps2

- step 1: latent_norm=47.9318 latent_std=1.99887 hidden_std=2.38985 drift=None cos_first=None entropy=1.9462 top1=0.6239
- step 2: latent_norm=57.3395 latent_std=2.38985 hidden_std=2.27982 drift=49.1954 cos_first=0.5758 entropy=0.9668 top1=0.5841

### none_steps4

- step 1: latent_norm=47.9318 latent_std=1.99887 hidden_std=2.38985 drift=None cos_first=None entropy=1.9462 top1=0.6239
- step 2: latent_norm=57.3395 latent_std=2.38985 hidden_std=2.27982 drift=49.1954 cos_first=0.5758 entropy=0.9668 top1=0.5841
- step 3: latent_norm=54.687 latent_std=2.27982 hidden_std=2.27254 drift=18.158 cos_first=0.5072 entropy=0.846 top1=0.6777
- step 4: latent_norm=54.5085 latent_std=2.27254 hidden_std=2.27578 drift=4.0139 cos_first=0.496 entropy=0.812 top1=0.7085

### rescale_steps1

- step 1: latent_norm=3.2021 latent_std=0.13354 hidden_std=2.43189 drift=None cos_first=None entropy=3.3399 top1=0.3986

### rescale_steps2

- step 1: latent_norm=3.2021 latent_std=0.13354 hidden_std=2.43189 drift=None cos_first=None entropy=3.3399 top1=0.3986
- step 2: latent_norm=3.2021 latent_std=0.13342 hidden_std=2.35395 drift=2.6557 cos_first=0.6561 entropy=1.6954 top1=0.5446

### rescale_steps4

- step 1: latent_norm=3.2021 latent_std=0.13354 hidden_std=2.43189 drift=None cos_first=None entropy=3.3399 top1=0.3986
- step 2: latent_norm=3.2021 latent_std=0.13342 hidden_std=2.35395 drift=2.6557 cos_first=0.6561 entropy=1.6954 top1=0.5446
- step 3: latent_norm=3.2021 latent_std=0.13348 hidden_std=2.34276 drift=1.123 cos_first=0.5378 entropy=2.0302 top1=0.453
- step 4: latent_norm=3.2021 latent_std=0.13349 hidden_std=2.35114 drift=0.516 cos_first=0.5259 entropy=2.0364 top1=0.5224

### rescale_ridge_steps1

- step 1: latent_norm=3.2021 latent_std=0.13354 hidden_std=2.4319 drift=None cos_first=None entropy=3.34 top1=0.3986

### rescale_ridge_steps2

- step 1: latent_norm=3.2021 latent_std=0.13354 hidden_std=2.4319 drift=None cos_first=None entropy=3.34 top1=0.3986
- step 2: latent_norm=3.2021 latent_std=0.13342 hidden_std=2.35398 drift=2.6556 cos_first=0.6561 entropy=1.6956 top1=0.5447

### rescale_ridge_steps4

- step 1: latent_norm=3.2021 latent_std=0.13354 hidden_std=2.4319 drift=None cos_first=None entropy=3.34 top1=0.3986
- step 2: latent_norm=3.2021 latent_std=0.13342 hidden_std=2.35398 drift=2.6556 cos_first=0.6561 entropy=1.6956 top1=0.5447
- step 3: latent_norm=3.2021 latent_std=0.13348 hidden_std=2.34277 drift=1.1229 cos_first=0.5379 entropy=2.0303 top1=0.4529
- step 4: latent_norm=3.2021 latent_std=0.13349 hidden_std=2.35114 drift=0.5162 cos_first=0.5259 entropy=2.0365 top1=0.5224

### random_steps1

- step 1: latent_norm=3.2021 latent_std=None hidden_std=None drift=None cos_first=None entropy=0.8063 top1=0.8941

### random_steps2

- step 1: latent_norm=3.2021 latent_std=None hidden_std=None drift=None cos_first=None entropy=0.8063 top1=0.8941
- step 2: latent_norm=3.2021 latent_std=None hidden_std=None drift=None cos_first=None entropy=3.397 top1=0.3076

### random_steps4

- step 1: latent_norm=3.2021 latent_std=None hidden_std=None drift=None cos_first=None entropy=0.8063 top1=0.8941
- step 2: latent_norm=3.2021 latent_std=None hidden_std=None drift=None cos_first=None entropy=3.397 top1=0.3076
- step 3: latent_norm=3.2021 latent_std=None hidden_std=None drift=None cos_first=None entropy=4.7766 top1=0.2781
- step 4: latent_norm=3.2021 latent_std=None hidden_std=None drift=None cos_first=None entropy=3.968 top1=0.2132


## SmolLM2-135M-Instruct (raw induction probe)

The chat-template probe is above this model's capability (positive control fails), so the latent grid is re-run in the induction format whose positive control passes.

| Config | Correct | Mean step (s) | Mean drift | Mean cos-to-first | Mean next-entropy | Mean top1 mass | Recv gen (s) |
|---|---|---|---|---|---|---|---|
| none_steps0 | 9/10 | - | - | - | - | - | 0.652 |
| none_steps1 | 9/10 | 0.0709 | - | - | 2.3363 | 0.4143 | 0.6723 |
| none_steps2 | 9/10 | 0.076 | 49.5628 | 0.6382 | 1.6482 | 0.5145 | 0.6778 |
| none_steps4 | 10/10 | 0.0798 | 24.8325 | 0.612 | 1.2237 | 0.598 | 0.6788 |
| rescale_steps0 | 9/10 | - | - | - | - | - | 0.6901 |
| rescale_steps1 | 9/10 | 0.0765 | - | - | 2.7435 | 0.2691 | 0.6547 |
| rescale_steps2 | 9/10 | 0.0682 | 2.3878 | 0.7218 | 2.4164 | 0.3671 | 0.596 |
| rescale_steps4 | 9/10 | 0.0747 | 1.3124 | 0.6861 | 2.1049 | 0.4252 | 0.6498 |
| rescale_ridge_steps0 | 9/10 | - | - | - | - | - | 0.6798 |
| rescale_ridge_steps1 | 9/10 | 0.0909 | - | - | 2.7439 | 0.2691 | 0.7427 |
| rescale_ridge_steps2 | 9/10 | 0.0773 | 2.3877 | 0.7219 | 2.4167 | 0.3671 | 0.7679 |
| rescale_ridge_steps4 | 9/10 | 0.0699 | 1.3124 | 0.6861 | 2.1051 | 0.4251 | 0.6455 |
| random_steps1 | 9/10 | 0.0852 | - | - | 4.3905 | 0.3148 | 0.6312 |
| random_steps2 | 9/10 | 0.074 | - | - | 5.0343 | 0.268 | 0.718 |
| random_steps4 | 9/10 | 0.0797 | - | - | 5.4823 | 0.1934 | 0.6557 |

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
