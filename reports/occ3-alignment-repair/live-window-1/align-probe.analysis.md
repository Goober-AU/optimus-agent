# Probe analysis — `align-probe.jsonl`

- input: `C:\Users\openclawuser\occ3-window-20261027b\align-probe.jsonl`
- sha256: `ab5c0ffbc5a6a8c4061e1fe3b521d33f10cbea893f665dd27175c1e63dc47b01`
- turns: 3
- generated: 2026-10-28T02:05:51Z
- analyser: `occ3.probe.analyser.v1`

Scalar values only. The analyser refuses any payload that is not an int, a float, or a flat list of floats (for `canonical_decile`), so no tensor, hidden state, embedding or token can appear in this file. Floats are printed to 4 significant figures, so a value shown as `1` can be a value just below 1; count keys are printed as integers.

## 1. Per-turn scalars

### 1a. Shape, spectrum and profile
```
turn  rows  hidden_dim  union_rank  hidden_eff_rank  embedding_eff_rank  canonical_top1  canonical_top8  canonical_top32  canonical_mean  canonical_participation  n>0.90  n>0.95  n>0.99  anchor_alpha  max_prefix_tokens
----  ----  ----------  ----------  ---------------  ------------------  --------------  --------------  ---------------  --------------  -----------------------  ------  ------  ------  ------------  -----------------
   1   103        6144         169            26.62               30.36          0.9814          0.9663           0.9267            0.32                    64.44      26       7       0             0                128
   2   102        6144         168            27.81               29.11          0.9856          0.9671           0.9287           0.325                    64.56      26      10       0             0                128
   3   128        6144         201            29.96               33.87          0.9843          0.9633           0.9285          0.2985                    71.12      26       8       0             0                128
```

### 1b. Relation statistics
```
turn  heldout_r2_within_turn  cos_prefix_to_embedding  relative_prefix_to_embedding  procrustes_residual  hidden_roll_sensitivity  hidden_permute_sensitivity  prefix_norm_mean  hidden_norm_mean  embedding_norm_mean  nonfinite_count
----  ----------------------  -----------------------  ----------------------------  -------------------  -----------------------  --------------------------  ----------------  ----------------  -------------------  ---------------
   1                  0.1011                   0.9314                        0.4933               0.9295                    0.479                      0.4833            0.6988             159.4               0.5527                0
   2                  0.1305                   0.9369                        0.4586               0.9069                   0.4714                      0.4652            0.6988             159.2               0.5664                0
   3                 0.06746                   0.9167                        0.4996                1.067                   0.5362                      0.5361            0.6988             162.4               0.5743                0
```

### 1c. Canonical deciles (group means of the retained canonical correlations)

```
turn      d1      d2      d3      d4         d5         d6         d7         d8         d9        d10
----  ------  ------  ------  ------  ---------  ---------  ---------  ---------  ---------  ---------
   1  0.9491  0.9044  0.8331  0.6205    0.07341  1.468e-07  4.412e-08  1.267e-09  3.115e-10  1.015e-10
   2  0.9521  0.9052  0.8448  0.6365    0.07374  1.515e-07  3.083e-08  8.198e-10  3.691e-10  1.324e-10
   3  0.9459  0.8877  0.8069  0.3589  2.218e-07   1.21e-07  3.172e-08  1.423e-09  4.793e-10  1.621e-10
```

## 2. Cross-turn statistics

```
                         key     median        min        max  n
----------------------------  ---------  ---------  ---------  -
                        rows        103        102        128  3
                  hidden_dim       6144       6144       6144  3
                  union_rank        169        168        201  3
             hidden_eff_rank      27.81      26.62      29.96  3
          embedding_eff_rank      30.36      29.11      33.87  3
              canonical_top1     0.9843     0.9814     0.9856  3
              canonical_top8     0.9663     0.9633     0.9671  3
             canonical_top32     0.9285     0.9267     0.9287  3
              canonical_mean       0.32     0.2985      0.325  3
     canonical_participation      64.56      64.44      71.12  3
         canonical_above_090         26         26         26  3
         canonical_above_095          8          7         10  3
         canonical_above_099          0          0          0  3
      heldout_r2_within_turn     0.1011    0.06746     0.1305  3
         procrustes_residual     0.9295     0.9069      1.067  3
     cos_prefix_to_embedding     0.9314     0.9167     0.9369  3
relative_prefix_to_embedding     0.4933     0.4586     0.4996  3
     hidden_roll_sensitivity      0.479     0.4714     0.5362  3
  hidden_permute_sensitivity     0.4833     0.4652     0.5361  3
            prefix_norm_mean     0.6988     0.6988     0.6988  3
            hidden_norm_mean      159.4      159.2      162.4  3
         embedding_norm_mean     0.5664     0.5527     0.5743  3
             nonfinite_count          0          0          0  3
                anchor_alpha          0          0          0  3
           max_prefix_tokens        128        128        128  3
        canonical_decile[d1]     0.9491     0.9459     0.9521  3
        canonical_decile[d2]     0.9044     0.8877     0.9052  3
        canonical_decile[d3]     0.8331     0.8069     0.8448  3
        canonical_decile[d4]     0.6205     0.3589     0.6365  3
        canonical_decile[d5]    0.07341  2.218e-07    0.07374  3
        canonical_decile[d6]  1.468e-07   1.21e-07  1.515e-07  3
        canonical_decile[d7]  3.172e-08  3.083e-08  4.412e-08  3
        canonical_decile[d8]  1.267e-09  8.198e-10  1.423e-09  3
        canonical_decile[d9]  3.691e-10  3.115e-10  4.793e-10  3
       canonical_decile[d10]  1.324e-10  1.015e-10  1.621e-10  3
```

Medians, minima and maxima are taken over the turns that carry the key (`n` of 3). A key absent from every turn prints MISSING.

## 3. Intrinsic rank from the canonical spectrum

### 3a. The bound

Canonical correlation analysis of the centred hidden rows `H` and the centred embedding
rows `E` gives correlations `rho_1 >= rho_2 >= ... >= rho_r` on the retained subspace of
dimension `r = union_rank`. For any `k <= r`, the in-sample canonical `R^2` of the best
linear predictor that uses `k` directions is `sum_{i<=k} rho_i^2`, and no predictor of the
embedding from the hidden rows can exceed it. Hence the stated bound

```
    R2_heldout_within_turn  <=  sum_{i=1..k} rho_i^2        for the retained dimension k <= union_rank
    R2_heldout_within_turn  <=  sum_{i=1..union_rank} rho_i^2 = in-sample canonical R2
```

The intrinsic rank estimate is the smallest `k` that the measured held-out `R2` does not
already exhaust:

```
    k_min = min { k : sum_{i<=k} rho_i^2 >= max(R2_heldout_within_turn, 0) }
```

`k_min` is a floor on the number of directions the relation needs, not a measurement of
its rank: a larger `R2` can only push `k_min` up, never down.

### 3b. Per-turn estimate

```
turn  union_rank  heldout_r2_within_turn  k_min  k_min_status  sum_top1  sum_top8  sum_top32  sum_covered                              note
----  ----------  ----------------------  -----  ------------  --------  --------  ---------  -----------  --------------------------------
   1         169                  0.1011      1         exact   0.9008*    7.207*       27.5        44.85  k = 1 is the floor of the domain
   2         168                  0.1305      1         exact   0.9066*    7.253*      27.62         45.6  k = 1 is the floor of the domain
   3         201                 0.06746      1         exact   0.8948*    7.158*     27.35*        49.25  k = 1 is the floor of the domain
```

A `*` marks a prefix sum taken inside a decile group, where the group mean is used and the
sum is therefore a lower bound.

### 3c. Cross-turn estimate and its limits

- median `k_min` = 1, min = 1, max = 1 over 3 of 3 turns
- median `union_rank` = 169
- `k_min` = 1 in 3 of 3 turns (turns [1, 2, 3])
- covered deciles per turn (out of `union_rank`): [160, 160, 200]

Reading of the estimate:

1. The single-direction sum `rho_1^2` already covers the measured `R2` in every turn, so the bound is satisfied at `k = 1` throughout and the canonical spectrum does not resolve the rank of the relation. `k_min` therefore carries no information beyond that floor. Whitening makes two full-rank clouds congruent: this is the same saturation that `calibration_probe.canonical_spectrum` documents, and it holds for independent rows too.
2. Only `heldout_r2_within_turn` separates a relation from no relation. The spectrum cannot, and a large `canonical_top8` is not evidence of one.
3. The probe reports group means, so a prefix sum is exact only when `k` ends a decile group (`step = union_rank // 10`); inside a group the group mean is used, which underestimates the prefix sum and therefore overestimates `k_min`. Such entries are marked `under` in `k_min_status` and with a `*` in the sum columns.
4. The probe keeps at most 10 decile groups, so a turn with `union_rank > 10 * step` has an unquantised tail; `sum_covered` reports what is actually known.


## 4. Pre-registered decision rule

- FEASIBLE needs `median heldout_r2_within_turn >= 0.3`. A frozen hidden->embedding map must explain at least 30% of the held-out embedding variance of a turn to be worth the window, and 0.30 is three times the artifact's own ship gate; the level was fixed before the data, not fitted to it.
- NOT-FEASIBLE when `median heldout_r2_within_turn < 0.1`. 0.10 is exactly statebridge_calibrated.MIN_HELDOUT_R2, the level below which fit_calibration refuses to freeze a calibration artifact, so a corpus that scores below it cannot produce a usable map at all.
- FEASIBLE also needs `median cos_prefix_to_embedding <= 0.3`. The shipped solver fits the rotation on the very rows it is scored on, so cos_prefix_to_embedding saturates near 1 for independent rows as well (proven: A == target copier, statebridge.py:58-63; measured 0.99999995 on independent 40x96 rows). A cosine above 0.30 therefore cannot certify that the prefix carries embedding content, while a cosine at or below 0.30 proves the prefix is measurably not that in-sample copier.

The rule is applied to the cross-turn median of every turn in the file, in this order:

- 1. any decisive key missing on any turn -> INCONCLUSIVE (no verdict on partial evidence)
- 2. median heldout_r2_within_turn >= 0.30 and median cos_prefix_to_embedding <= 0.30 -> FEASIBLE
- 3. median heldout_r2_within_turn < 0.10 -> NOT-FEASIBLE
- 4. otherwise -> INCONCLUSIVE

Why the cosine bound belongs to this rule: the shipped fit solves a Procrustes rotation
on the transmitted rows and scores it on those same rows, so `prefix` reproduces the
target embedding by construction (`statebridge.py:58-63`, `A == target copier`). The
degeneracy is proven, not estimated, so a large cosine is expected even when the two row
sets are unrelated. A cosine at or below 0.30 is the only reading that cannot be produced
by the copier, which is why FEASIBLE requires it; a large cosine leaves the window unable
to separate the copier from a real relation, which is INCONCLUSIVE, not FEASIBLE.

## 5. Verdict

**INCONCLUSIVE** — median R2 0.1011 sits between 0.1 and 0.3; the pre-registered rule has no verdict in this band

```
median heldout_r2_within_turn = 0.1011
median cos_prefix_to_embedding = 0.9314
turns = 3
turns with a missing decisive key = 0
```

## 6. Missing values and refusals

No key is missing: every turn carries all 26 expected keys.

## 7. Limits of this analysis

1. `heldout_r2_within_turn` is produced by a fixed 50/50 split of the turn's own rows, so it is a within-turn estimate and it is noisy when a turn has few rows.
2. `hidden_permute_sensitivity` depends on `torch.randperm` and therefore differs between runs on the same input; it is reported but it is not part of any threshold.
3. The analyser reads scalars only. It cannot re-derive, re-fit or re-score a turn, and it does not claim to.
4. `cos_prefix_to_embedding` is an in-sample statistic by construction. It can show that the prefix is not the copier; it cannot show that the relation generalises. Only `heldout_r2_within_turn` speaks to generalisation.

