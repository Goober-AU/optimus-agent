# Procrustes validation — thin StateBridge alignment, CPU numerical audit

**Scope.** CPU-only audit of `align_hidden_sequence` (`extensions/sglang-occ3/occ3_sglang/statebridge.py:32-70`)
against an exact numpy port, on synthetic GLM-shaped states with `n = 128` rows, `d = 6144`, `float32`.
The dense reference (`occ3-latent-validation/references/StateBridge/methods/state_bridge.py:211-299`) was read, not executed.

**Provenance.** every number below is printed and written by
`reports/occ3-statebridge-recovery/analysis/align_audit.py` into `analysis/align_audit.json` in the same directory.
Command (from the repo root):

```
extensions/sglang-occ3/.venv/Scripts/python.exe reports/occ3-statebridge-recovery/analysis/align_audit.py
```

Runtime 66.4 s on CPU; numpy 2.2.6, python 3.11.15, torch 2.8.0+cpu; seed 20261027.
Defaults under test: `ridge = 1e-3`, `eigen_floor = 1e-6`, `rank_rtol = 1e-5` (`statebridge.py:32-33`).
No source file was modified. No GPU, no DGX, no real GLM hidden state.

**Fixture in one paragraph.** `E` is 128 distinct rows of a synthetic 4096-row vocabulary with log-normal
column decay (`var_i ∝ i^-0.6`), a shared anisotropic mean, and mean row norm 1.0 — the same scale as
`target_norm` in production. `H` is an anisotropic, correlated, non-zero-mean cloud with a 0.24 cosine to its
paired `E` row; 0.24 is the measured raw hidden→embedding cosine recorded at
`reports/occ3/SGLANG-SOURCE-AUDIT.md:53`. Three scale regimes are used: `h1_e1` (H and E near row norm 1),
`h60_e1` (H at residual-stream scale), `h60_e60` (both large).

## 0. Verdicts at a glance

| # | experiment | headline number | confounding verdict |
|---|---|---|---|
| 1 | numpy/torch parity | max rel. difference 4.95e-05 (full rank), 0.136 (rank-deficient H) | **CONFOUND PRESENT** — the torch code itself reproduces `normcal(E)` to cos 1.000000 on *independent* random H |
| 2 | union rank, conditioning | rank always 254, `Cov_H` cond up to 683, `eigen_floor` never binds | **CONFOUND PRESENT** — the ridge, not the eigen floor, sets the conditioning |
| 2b | mechanism (ridge / rank-cap sweep) | two *independent* clouds: Procrustes residual 7.93e-05 | **CONFOUND PRESENT** — whitening makes both clouds tight frames, so the fit is exact for any pairing |
| 2c | row-count sweep | parameters/data 0.812 … 0.988; rel. E-copy 3.61e-04 … 0.022 | **CONFOUND PRESENT** at every legal `n` |
| 6 | out-of-sample fit | in-sample 0.0193, held-out 1.4075 (×73.0), worse than a plain ridge regression (1.030) | **CONFOUND PRESENT** — the map carries no out-of-sample E signal |
| 7 | sensitivity dZ/dH vs dZ/dE | dZ/dH = 0.0164, dZ/dE = 1.0128 (ratio 1:61.8) | **CONFOUND PRESENT** — the output is ~60× more sensitive to E than to H |

Detail per experiment follows. Raw numbers: `analysis/align_audit.json` (keys `exp1_parity`, `exp2_conditioning`,
`exp2b_mechanism`, `exp2c_n_sweep`, `exp6_out_of_sample`, `exp7_sensitivity`, `verdicts`).

## 1. Parity — numpy port against the torch implementation

The port follows `statebridge.py:48-70` statement by statement: centre (48-49), union SVD and rank (50-53),
projection (55-56), ridge covariances (58-59), symmetric-power whitening (60-61), orthogonal Procrustes (62),
reconstruction plus `mu_E` (63), per-row norm calibration (64-68).

| fixture | rank np | rank torch | max abs diff | rel fro diff | Procrustes obj | spectral lower bound | flat frac x | flat frac y | cos(row, E) torch | rel diff to norm-cal E |
|---|---|---|---|---|---|---|---|---|---|---|
| full_rank_independent | 254 | 254 | 3.31e-06 | 4.95e-05 | 5.11e-05 | 2.17e-06 | 0.9922 | 0.9922 | 1 | 2.86e-05 |
| full_rank_paired_glm_shape | 254 | 254 | 1.91e-06 | 2.37e-06 | 0.02302 | 0.00919 | 1 | 1 | 0.9998 | 0.02131 |
| rank_deficient_H | 143 | 143 | 0.01164 | 0.1355 | 0.9344 | 0.9344 | 0.125 | 0.9922 | 0.3773 | 1.116 |
| rank_deficient_H4 | 131 | 131 | 0.005521 | 0.08571 | 0.9836 | 0.9836 | 0.03125 | 0.9922 | 0.2262 | 1.244 |
| zero_H | 127 | 127 | 1.86e-08 | 2.43e-07 | 1 | 1 | 0 | 1 | 0.08772 | 1.351 |

Notes. `spectral lower bound` is the von Neumann bound `sqrt(sum((sx-sy)^2))/||y||` on the orthogonal-Procrustes
residual; `flat frac` is the share of live whitened singular values at `0.9·sqrt(n)`.

* Full-rank data: the two implementations agree to `max abs diff = 3.31e-06`
  (relative Frobenius 4.95e-05 and 2.37e-06), ranks equal in every case. Parity **CONFIRMED**.
* Rank-deficient H: the Procrustes objectives are *identical* (`0.9344` numpy = spectral bound) but the carrier differs by `0.136` relative and `0.012` absolute. The rotation is not unique: only 12% of `x`'s live directions sit at the flat value, against 99% of `y`'s, so the spectrum cannot be matched and the optimum is a plateau on which LAPACK and torch land at different points. This confirms and quantifies `reports/occ3-latent-research/README.md:132-135` ("rank-deficient carrier differences had essentially equal Procrustes objectives").

**VERDICT: CONFOUND PRESENT.** Parity is clean, but the parity fixture itself is the first confound datum:
with `H` replaced by *independent* `N(0,1)` rows the torch implementation returns rows whose cosine to their
(unrelated) `E` row is `1.000000` and whose relative difference from the
norm-calibrated `E` is `2.86e-05`. The aligned output is
the norm-calibrated reference embeddings, not a function of the hidden states.

## 2. Union rank and conditioning

Three spectra (log-normal decay exponent 0.6 / 1.2 / 2.0), eight seeds each, `H` rows correlated through a shared
component (`shared_frac = 0.5`).

| spectrum (8 seeds) | union rank | cond Cov_H | cond Cov_E | dirs < ridge (H) | dirs < ridge (E) | ridge/median lambda (H) | ridge/median lambda (E) | max whitening gain |
|---|---|---|---|---|---|---|---|---|
| lognormal_decay_0.6 | 254 | 20.9 | 21.17 | 57 | 60.5 | 0.2874 | 0.2892 | 31.62 |
| lognormal_decay_1.2 | 254 | 211.8 | 216.6 | 54.75 | 54.12 | 0.6062 | 0.6099 | 31.62 |
| lognormal_decay_2.0 | 254 | 682.6 | 695.6 | 5.5 | 6.5 | 0.9861 | 0.9865 | 31.62 |

* The union rank is `2(n-1) = 254` in every one of the 24 runs: with `d >> 2n` and `rank_rtol = 1e-5` the `rtol` test cuts only the two numerically null directions (`s_first_rejected = 6.96e-05` against `s_retained_min = 0.724`).
* `eigen_floor = 1e-6` never binds: `eigen_floor_binding_dirs` is 0 over all 48 covariance factorisations. The floor is inert.
* Conditioning is set by the ridge. `cond(ch)` reaches 683 and the whitening gain saturates at `1/sqrt(ridge) = 31.62` in every run.
* Between 57 and 5.5 of the 254 retained directions sit below the ridge, i.e. are scaled purely by the ridge, not by their own variance.

**VERDICT: CONFOUND PRESENT.** The ridge is not a mild stabiliser here; it is the dominant term for a
large block of directions (57/254 at decay 0.6).
Those directions are flattened to a common scale, which is exactly the mechanism that makes the fit exact (§2b).

## 2b. Mechanism — why the in-sample Procrustes fit is exact

For a direction with variance `v = s²/n` the whitened singular value is

```
sigma = s / sqrt(v + ridge) = sqrt(n) / sqrt(1 + ridge/v)
```

Every direction with `v >> ridge` therefore has `sigma ≈ sqrt(n)`. A cloud whose live singular values are all
`sqrt(n)` is a **tight frame**: `X Xᵀ = n·I`. Two tight frames with the same `n` and the same number of live
directions are congruent, so the orthogonal Procrustes residual is zero *whatever the content and whatever the
pairing*. That is the whole mechanism; it needs no property of the fixture.

| case | ridge | rank cap | union rank | Procrustes obj | spectral lower bound | flat frac x | flat frac y | cos(row,E) | rel diff to norm-cal E | rel change, unrelated H |
|---|---|---|---|---|---|---|---|---|---|---|
| ridge_sweep | 1.00e-04 |  | 254 | 0.002675 | 0.001156 | 0.9922 | 0.9922 | 1 | 0.00245 | 0.002022 |
| ridge_sweep | 0.001 |  | 254 | 0.02356 | 0.01001 | 0.9921 | 1 | 0.9998 | 0.02181 | 0.01792 |
| ridge_sweep | 0.01 |  | 254 | 0.111 | 0.04392 | 0 | 0 | 0.9944 | 0.1059 | 0.08661 |
| ridge_sweep | 0.1 |  | 254 | 0.1847 | 0.06896 | 0 | 0 | 0.9844 | 0.1768 | 0.1494 |
| ridge_sweep | 1 |  | 254 | 0.1991 | 0.07354 | 0 | 0 | 0.9819 | 0.1901 | 0.1659 |
| ridge_sweep | 10 |  | 254 | 0.2007 | 0.07405 | 0 | 0 | 0.9817 | 0.1915 | 0.1701 |
| iid_control | 0.001 |  | 254 | 7.93e-05 | 7.93e-05 | 0.9922 | 0.9922 | 1 | 3.54e-06 | 4.92e-06 |
| rank_cap_sweep | 0.001 | 128 | 128 | 0.1856 | 0.09692 | 0.4016 | 0.6929 | 0.8642 | 0.5211 | 1.29 |
| rank_cap_sweep | 0.001 | 64 | 64 | 0.5284 | 0.04879 | 0.5156 | 0.8906 | 0.5848 | 0.9112 | 1.361 |
| rank_cap_sweep | 0.001 | 32 | 32 | 0.6797 | 0.03621 | 0.6562 | 1 | 0.3973 | 1.098 | 1.356 |
| rank_cap_sweep | 0.001 | 16 | 16 | 0.8094 | 0.02441 | 0.9375 | 1 | 0.2673 | 1.211 | 1.323 |
| rank_cap_sweep | 0.001 | 8 | 8 | 0.9164 | 0.01439 | 1 | 1 | 0.1854 | 1.276 | 1.261 |

* **Control.** Two completely independent `iid N(0,1)` clouds (no pairing, no shared structure) give a Procrustes residual of `7.93e-05`, `cos(row,E) = 1.000000`, and reproduce the norm-calibrated `E` to `3.54e-06`. The map is an exact interpolator of any cloud onto any other cloud of the same shape.
* **Ridge sweep.** The default `ridge = 1e-3` sits deep inside that regime: residual `0.0236`, E-copy `0.0218`. Lowering the ridge to `1e-4` makes the copy *better* (`2.45e-03`); raising it to `10` only pushes the copy down to `0.192` and still leaves `cos(row,E) = 0.982`.
* **Rank cap.** Capping the map rank at 16 removes the E-copy (`rel diff 1.211`, `cos 0.267`) and makes the output track H instead (`rel change with unrelated H = 1.323`). It also removes the alignment, because with rank `< n-1` the two spectra can no longer be matched. Hypothesis H2, not a repair.
* The map rank is `254 = 2(n-1)`, so the orthogonal rotation carries `r(r-1)/2 ≈ 3.2e4` free real parameters
* against `n·r ≈ 3.25e4` numbers of data. The map has one free parameter per data entry, and it is applied to
  the very rows it was fitted on (`latent_worker.py:324,332`).

**VERDICT: CONFOUND PRESENT.** The alignment is an in-sample interpolation. On these fixtures the output
equals the norm-calibrated reference embeddings to 2.2% at the shipped defaults,
and to 0.0004% when the two clouds have no relation at all.

## 2c. Row-count sweep — does a smaller capture window escape?

| rows n | union rank | rotation params | data entries | params/data | Procrustes obj | flat frac x | cos(row,E) | rel diff to norm-cal E | rel change, unrelated H |
|---|---|---|---|---|---|---|---|---|---|
| 8 | 14 | 91 | 112 | 0.8125 | 6.67e-04 | 0.875 | 1 | 3.61e-04 | 0.001557 |
| 16 | 30 | 435 | 480 | 0.9062 | 0.001729 | 1 | 1 | 0.001219 | 0.002479 |
| 32 | 62 | 1891 | 1984 | 0.9531 | 0.004025 | 1 | 1 | 0.003213 | 0.003964 |
| 64 | 126 | 7875 | 8064 | 0.9766 | 0.009535 | 1 | 1 | 0.008301 | 0.007986 |
| 128 | 254 | 3.21e+04 | 3.25e+04 | 0.9883 | 0.02356 | 0.9921 | 0.9998 | 0.02181 | 0.01792 |

* The parameter/data ratio stays between 0.81 and 0.99 for every legal `n` (2 ≤ n ≤ 128, `statebridge.py:43`).
* The E-copy is *strongest* at small `n` (3.61e-04 at n=8) and weakest at n=128 (0.022). Short windows do not escape; they copy E more faithfully.

**VERDICT: CONFOUND PRESENT** at every legal row count.

## 6. Out-of-sample — fit on rows 0..63, apply to rows 64..127

The fit uses the first 64 rows (its own `mu_H`, `Cov_H`, `R`, `mu_E`, `Cov_E`); the held-out rows are pushed through
that fixed map and compared against their own `E` rows.

| in-sample resid | held-out resid | ratio | constant-mean baseline | plain ridge baseline | shuffled-fit control | leaky target stats | held-out cos(row,E) | held-out top-1 retrieval | held-out Procrustes sim to E |
|---|---|---|---|---|---|---|---|---|---|
| 0.01927 | 1.407 | 73.04 | 1.416 | 1.03 | 1.416 | 1.366 | 0.01007 | 0.0625 | 0.1429 |

* In-sample residual 0.0193 against a held-out residual of 1.407: a factor 73.0.
* The held-out residual is indistinguishable from the constant-mean baseline (1.416) and from a fit with shuffled pairing (1.416).
* A plain dual ridge regression from centred H to centred E, with no whitening and no Procrustes, does *better* (1.030). The aligned map transfers less than the simplest linear baseline.
* Leaking the held-out target statistics (`mu_E`, `Cov_E` recomputed from rows 64..127) does not rescue it (1.366).
* Held-out cosine to the paired reference row 0.0101; top-1 retrieval of the paired row among 64 candidates 0.062 (chance 0.0156).

**VERDICT: CONFOUND PRESENT.** Everything the method reproduces is produced by fitting the rows it then
transforms. There is no measurable held-out H→E signal to transfer under this estimator.

## 7. Sensitivity dZ/dH against dZ/dE

Perturbation is Gaussian noise with `sigma = eps · rms(input)`. `iid` perturbs every entry independently;
`common_mode` adds one direction to every row. The whole pipeline (statistics, rotation, calibration) is refitted.

| eps | perturbation | target | rel. output change | sensitivity per unit rel. input | cos to unperturbed output |
|---|---|---|---|---|---|
| 0.01 | iid | H | 1.64e-04 | 0.01638 | 1 |
| 0.01 | iid | E | 0.01003 | 1.003 | 0.9999 |
| 0.01 | common_mode | H | 9.50e-07 | 9.50e-05 | 1 |
| 0.01 | common_mode | E | 0.01013 | 1.013 | 0.9999 |
| 0.1 | iid | H | 0.001617 | 0.01617 | 1 |
| 0.1 | iid | E | 0.09993 | 0.9993 | 0.995 |
| 0.1 | common_mode | H | 9.32e-07 | 9.32e-06 | 1 |
| 0.1 | common_mode | E | 0.1007 | 1.007 | 0.9949 |

* `dZ/dH = 0.0164`: a 1% change in H moves the output by 0.016%.
* `dZ/dE ≈ 1.013`: a 1% change in E moves the output by 1%.
* Ratio 1 : 61.8. A common-mode shift of H is invisible (`1.0e+00`), because centring removes it.
* The fitted map's own gain is modest (`sigma_max = 1.731`, condition `2.70`), so the insensitivity is not a gain effect; it is the refit plus the per-row norm calibration absorbing the perturbation.

**VERDICT: CONFOUND PRESENT.** The output is E-dominated by roughly two orders of magnitude.

