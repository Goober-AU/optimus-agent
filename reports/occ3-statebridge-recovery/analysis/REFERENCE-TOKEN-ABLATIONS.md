# Reference-token ablations — is the StateBridge prefix driven by the reference embeddings?

**Question.** `align_hidden_sequence` maps captured hidden rows `H` onto the reference embeddings `E` of the same
tokens (`latent_worker.py:324,332`). This report measures how much of the returned prefix is fixed by `E` and how
much by `H`, and it measures what `anchor_tiled` adds on top (`statebridge.py:115-155`).

**Scope.** CPU only, synthetic GLM-shaped states: `n = 128` rows, `d = 6144`, `float32`, vocabulary of 4096 rows.
Every number is produced by `reports/occ3-statebridge-recovery/analysis/align_audit.py` and stored in
`analysis/align_audit.json` (`exp3_confound`, `exp4_target_side`, `exp5_anchoring`, `exp7_sensitivity`).
Seed 20261027; runtime 66.4 s. No real GLM state, no GPU, no source change.

Fixture: `E` = 128 distinct vocabulary rows, mean row norm 1.0; `H` = anisotropic correlated rows with a 0.24
cosine to their paired `E` row (`reports/occ3/SGLANG-SOURCE-AUDIT.md:53`). `cos(H_i, E_i) = 0.2397` as built.
Three regimes: `h1_e1`, `h60_e1` (H at residual-stream scale), `h60_e60`.

## 0. Verdicts at a glance

| # | experiment | headline number | confounding verdict |
|---|---|---|---|
| 3 | E fixed, H varied | unrelated random H: cos(row,E) 0.999870, rel. change vs correct-H Z 0.0179 | **CONFOUND PRESENT** |
| 4 | E row shuffle, E-only carriers | permuted E: cos to the *permuted* rows 0.99974, cos to the true rows 8.67e-04 | **CONFOUND PRESENT** — the output follows whatever E is supplied |
| 5 | vocabulary anchoring, alpha 0 vs 0.3 | cos to nearest row 0.99976 before, 0.99988 after; ids changed 0% | **CONFOUND PRESENT** — the alignment already lands on the vocabulary, so the anchor is nearly a no-op |
| 7 | sensitivity (reference) | dZ/dH 0.0164 vs dZ/dE 1.0128 | **CONFOUND PRESENT** |

**Answer in one line.** In every non-degenerate variant the aligned output is the norm-calibrated reference
embedding of the paired token, to within 2.2% (primary regime) and
1.6% (residual-stream regime); replacing the hidden states with
uncorrelated noise changes the output by 1.8% and 0.0006% respectively.

## 3. The confound set — E fixed, H varied

### 3a. Primary regime (`h1_e1`: H and E mean row norms 1.03 / 1.00)

| H variant | rel. diff to norm-cal E | variance of Z explained by E | cos(row, E) | mean angle (deg) | rel. recon error of E | rel. change vs correct-H Z | top-1 retrieval | eff. rank of Z | spectrum rel. diff to E |
|---|---|---|---|---|---|---|---|---|---|
| correct_H | 0.02181 | 0.9995 | 0.9998 | 1.249 | 0.02833 | 0 | 1 | 122.6 | 0.0325 |
| row_shuffled_H | 0.02297 | 0.9995 | 0.9997 | 1.316 | 0.02923 | 0.02364 | 1 | 122.8 | 0.0369 |
| unrelated_random_H | 0.01615 | 0.9997 | 0.9999 | 0.9254 | 0.02425 | 0.01792 | 1 | 122.8 | 0.03782 |
| scale_matched_random_H | 0.01722 | 0.9997 | 0.9999 | 0.9865 | 0.02498 | 0.01858 | 1 | 122.8 | 0.0373 |
| zero_H | 1.352 | -0.8269 | 0.08655 | 85.03 | 1.351 | 1.351 | 0.007812 | 0 | 1 |

### 3b. Residual-stream regime (`h60_e1`: H mean row norm 61.8, E 1.00)

| H variant | rel. diff to norm-cal E | variance of Z explained by E | cos(row, E) | mean angle (deg) | rel. recon error of E | rel. change vs correct-H Z | top-1 retrieval | eff. rank of Z | spectrum rel. diff to E |
|---|---|---|---|---|---|---|---|---|---|
| correct_H | 0.01615 | 0.9997 | 0.9999 | 0.9254 | 0.02425 | 0 | 1 | 122.8 | 0.03782 |
| row_shuffled_H | 0.01615 | 0.9997 | 0.9999 | 0.9254 | 0.02425 | 7.74e-06 | 1 | 122.8 | 0.03782 |
| unrelated_random_H | 0.01615 | 0.9997 | 0.9999 | 0.9254 | 0.02425 | 5.87e-06 | 1 | 122.8 | 0.03782 |
| scale_matched_random_H | 0.01615 | 0.9997 | 0.9999 | 0.9254 | 0.02425 | 6.11e-06 | 1 | 122.8 | 0.03782 |
| zero_H | 1.352 | -0.8269 | 0.08655 | 85.03 | 1.351 | 1.355 | 0.007812 | 0 | 1 |

### 3c. Both large (`h60_e60`: H 61.8, E 60.1)

| H variant | rel. diff to norm-cal E | variance of Z explained by E | cos(row, E) | mean angle (deg) | rel. recon error of E | rel. change vs correct-H Z | top-1 retrieval | eff. rank of Z | spectrum rel. diff to E |
|---|---|---|---|---|---|---|---|---|---|
| correct_H | 2.87e-05 | 1 | 1 | 0.001645 | 0.9834 | 0 | 1 | 121.8 | 0.006463 |
| row_shuffled_H | 2.86e-05 | 1 | 1 | 0.001641 | 0.9834 | 3.94e-05 | 1 | 121.8 | 0.006465 |
| unrelated_random_H | 1.86e-05 | 1 | 1 | 0.001067 | 0.9834 | 3.36e-05 | 1 | 121.8 | 0.006466 |
| scale_matched_random_H | 2.77e-05 | 1 | 1 | 0.001588 | 0.9834 | 3.93e-05 | 1 | 121.8 | 0.006462 |
| zero_H | 1.352 | -0.8269 | 0.08655 | 85.03 | 0.9987 | 1.352 | 0.007812 | 0 | 1 |

Readings:

* In 3b every H variant returns the *same* Z: shuffling H, replacing H by independent noise, or rescaling it changes the output by at most `7.7e-06` relative — i.e. by nothing.
* In 3a the effect is larger but still small: the output moves by `1.8%` when H is replaced by unrelated noise, and it still matches its `E` row to `cos 0.999870` (mean angle `0.93°`), with top-1 retrieval of the paired row at 1.00 (chance 1/128 = 0.0078).
* The effective rank of Z (122.6) reproduces the effective rank of E (121.6); the relative difference between the two spectra is `0.032`. The second-order geometry of the output is E's.
* `zero_H` is the only variant that does not reproduce E: the whitened carrier is identically zero, so `z = mu_E` repeated, the centred spectrum is identically zero and 100% of the row energy sits in the `mu_E` direction. That is a degenerate carrier, not an H-sensitive one.

**VERDICT: CONFOUND PRESENT.** Under unrelated random H the output is still the reference embedding of the
paired token to 99.99% cosine. The hidden state does not decide the prefix; the reference rows do.

## 4. Fixed H, shuffled E; and E-only diagnostic carriers

| case | rel. recon error of E | cos(row, E) | rel. change vs correct-H Z | Procrustes sim to correct Z | Procrustes sim to E | eff. rank | share of row energy in mu_E |
|---|---|---|---|---|---|---|---|
| E_row_shuffled | 1.412 | 8.67e-04 | 1.414 | 0.9997 | 0.9996 | 122.7 | 0.007697 |
| carrier_zero_H | 1.351 | 0.08655 | 1.351 | 0 | 0 | 0 | 1 |
| carrier_E_as_its_own_H | 0.0181 | 1 | 0.02181 | 0.9998 | 0.9998 | 121.8 | 0.007494 |
| carrier_random_rows_correct_state | 1.393 | 0.02942 | 1.394 | 0.6251 | 0.6267 | 97.15 | 0.4082 |
| carrier_E_rows_correct_state | 1.181 | 0.3014 | 1.183 | 0.3471 | 0.3476 | 121.4 | 0.03358 |

The `E_row_shuffled` row is measured against the *true* E. Against the *permuted* E its cosine is `0.99974`: the map reproduces whichever reference rows it is given, in the order it is given them. Permuting the target cannot remove the confound; it relabels which embedding is copied.

* `carrier_E_as_its_own_H` (fit and apply on E) is a near-identity map: it returns `E` to `0.0181` and has `Procrustes sim 0.9998` to the real aligned output. The two outputs are the same point cloud.
* `carrier_random_rows_correct_state` and `carrier_E_rows_correct_state` show the important limit: rows that were **not** in the fit are not mapped onto E (`cos 0.0294` and `0.3014`). The E-copy is a property of the fit rows, not of the map alone — and in production the map is applied to exactly the rows it was fitted on (`latent_worker.py:332`).

**VERDICT: CONFOUND PRESENT.**

## 5. Vocabulary anchoring — alpha = 0 against alpha = 0.3

`anchor_tiled` returns the prefix unchanged at `alpha = 0` (`statebridge.py:119-120`); at `alpha = 0.3` it blends
`0.7·z + 0.3·w_argmax` where `w` is the vocabulary row with the largest cosine (`statebridge.py:124-136,155`).
Vocabulary: 4096 synthetic rows, mean row norm 1.0.

| metric | alpha = 0 (no anchor) | alpha = 0.3 (anchor_tiled) |
|---|---|---|
| cos to nearest vocabulary row | 0.9998 | 0.9999 |
| nearest id equals the paired token (recall) | 1 | 1 |
| rel. change from the alpha=0 output | 0 | 0.008511 |
| mean row norm ratio to alpha=0 | 1 | 1 |
| share of blend energy from the vocabulary row | 0 | 0.09022 |
| share of blend energy from the unblended row | 1 | 0.4896 |
| share of blend energy, cross term | 0 | 0.4202 |
| fraction of nearest ids changed | 0 | 0 |

| H variant | nearest-id agreement with correct-H anchor | rel. change, alpha=0 | rel. change, alpha=0.3 | contraction factor | paired-token recall after anchoring |
|---|---|---|---|---|---|
| correct_H | 1 | 0 | 0 | 0 | 1 |
| row_shuffled_H | 1 | 0.02366 | 0.01656 | 0.6997 | 1 |
| unrelated_random_H | 1 | 0.01792 | 0.01254 | 0.6997 | 1 |
| scale_matched_random_H | 1 | 0.01864 | 0.01305 | 0.6997 | 1 |
| zero_H | 0.007812 | 1.351 | 1.23 | 0.9103 | 0.007812 |

Readings:

* The alignment alone already lands within `cos 0.99976` of a vocabulary row, and that row is the paired token in `100%` of cases. The anchor therefore changes nothing discrete: `0%` of nearest ids change and the output moves by `0.85%`.
* What the anchor does do is shrink any surviving H-derived difference by exactly `1 - alpha`: the contraction factor is `0.6997` for every non-degenerate H variant. Anchor energy decomposes into `49.0%` unblended, `9.0%` vocabulary row, `42.0%` cross term.
* Caveat: `E` *is* a set of vocabulary rows, so `cos(z, nearest) ≈ cos(z, e_i)` follows from `z ≈ e_i`. This measures how close the output already is to being a vocabulary row; it is not independent evidence about `anchor_tiled`'s design.

**VERDICT: CONFOUND PRESENT** (and the anchor amplifies rather than dilutes it: H-derived differences are
multiplied by 0.70, and 30% of the row is replaced by a vocabulary row that came from the same confound).

## 6. How much of the output is E alone — the arithmetic

The map is affine by construction (`statebridge.py:63`): `z_i = (h_i - mu_H) q ch^(-1/2) R ce^(1/2) q^T + mu_E`,
then per-row calibration to `target_norm` (`statebridge.py:64-68`). `E` enters through `mu_E`, `ce^(1/2)` and the
rotation target; `H` enters only through the per-row projection `(h_i - mu_H) q`.

| quantity | primary (h1_e1) | residual-stream (h60_e1) |
|---|---|---|
| variance of Z explained by norm-calibrated E (1 - rel²) | 0.999525 | 0.999739 |
| same, with H replaced by unrelated noise (rel. diff 0.0162 / 0.0162) | 0.999739 | 0.999739 |
| variance of Z explained by the E mean direction alone | 0.00769 | 0.00665 |
| effective rank of Z / of E | 122.6 / 121.6 | 122.8 / 121.6 |

The mean direction is *not* the mechanism (`share of row energy in mu_E` is below 1%); the mechanism is that the
whitened Procrustes fit is exact, so `z_i = e_i` and the residual is only the interpolation error (see
`PROCRUSTES-VALIDATION.md` §2b: two independent clouds give a Procrustes residual of 7.9e-05).

## 7. What a minimal change could do — hypotheses, not repairs

No claim of repair is made. Three candidate changes were measured; each is a hypothesis that must be validated
on real GLM states before it is adopted.

**H1 — cross-fit the map (fit on one row set, apply to a disjoint one).** Numbers: in-sample residual 0.0193, held-out residual 1.407 (×73); held-out cosine to the paired row 0.0101; held-out top-1 retrieval 0.062 against a chance level of 0.0156; held-out residual no better than the constant-mean baseline (1.416) and worse than a plain dual ridge regression (1.030). Cross-fitting removes the E-copy, but on this fixture it leaves nothing to transfer.

**H2 — cap the map rank below `n-1`.** Numbers: rank cap 64 gives `cos(row,E) 0.585`, cap 16 gives `0.267`, and the output starts to follow H instead (`rel change with unrelated H = 1.323`). The rank cap removes the confound and the alignment together.

**H3 — change the target pairing or the centring.** Measured negative. Permuting the `E` rows leaves the output following the permuted rows (`cos 0.99974`), so re-pairing relabels the copy instead of removing it. Centring is already applied (`statebridge.py:48-49`) and `mu_E` carries under 1% of the row energy, so neither centring nor a mean correction is a lever.

**H4 (for completeness) — raise the ridge.** `ridge = 10` (10,000× the default) still leaves `cos(row,E) = 0.982` and an E-copy residual of 0.192. Not a lever.


**Recommendation.** Before any repair is designed, measure three numbers on real captured tensors: (i) the
share of whitened singular values at `0.9·sqrt(n)` (`flat_frac`), (ii) the leave-one-out residual against the
in-sample residual, (iii) `cos(Z, normcal(E))`. If (i) is near 1 and (ii) is a large factor, the confound
reproduces on real data and the method cannot transfer hidden-state content as written.

## 8. Open risks

1. **Synthetic fixtures.** The vocabulary and the hidden cloud are drawn from log-normal spectra with a shared
   mean and a 0.24 pairing cosine. Real GLM states are more anisotropic and have outlier channels; the
   exactness of the fit is governed by `flat_frac` (the share of live whitened directions at `sqrt(n)`), which
   must be measured, not assumed. The two independent-cloud control is the strongest evidence and does not
   depend on the fixture: any two clouds of 128 rows whiten to tight frames and are then congruent.
2. **In-sample only.** The E-copy holds for the rows the map was fitted on. Rows that were not in the fit are
   not mapped onto E (§4). In production the map is always applied to its own fit rows
   (`latent_worker.py:324,332`), so the confound applies — but it is an in-sample property, not a property of
   the linear map in isolation.
3. **float32 Procrustes residual.** `||xR - y||²` is a difference of `O(n·d)` quantities, so the measured
   residual is round-off limited once it is small: at `ridge = 1e-3` the achieved `0.0236` exceeds the spectral
   lower bound `0.0100`. The conclusion is unaffected (both are small), but the achieved residual must not be
   quoted as a precise value.
4. **`target_norm` scale.** `mean_embedding_norm` is not measured here. The confound gets *stronger* as the
   input variance grows relative to `ridge` (§3b), so a real `target_norm` and real hidden-state scale could
   move the numbers either way; the ridge sweep in PROCRUSTES-VALIDATION §2b bounds the sensitivity.
5. **The dense reference was not executed.** Equivalence with the thin port on the observed rows is taken from
   the algebra (the dense `Cov^(-1/2)` acts on the same row space) plus `reports/occ3-latent-research/README.md:132-135`.
   A 6144² `eigh` was out of scope on CPU.
6. **Vocabulary.** 4096 synthetic rows, not the GLM vocabulary, so the anchored token ids are not GLM ids;
   only the geometry of the anchor is measured.

