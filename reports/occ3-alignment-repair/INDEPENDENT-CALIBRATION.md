# METHOD B - frozen whitened Procrustes on independent calibration rows

## 1. What the method is

Fit on `(H_cal, E_cal)` only, then freeze and apply to any new row:

    Q, ch, ce, R  = fit(H_cal, E_cal)                     # union basis, whitening, rotation
    z(h)          = ((h - mu_H) Q) ch^{-1/2} R ce^{1/2} Q^T + mu_E

`Q` is the right-singular basis of the stacked centred pair (rank `min(rank_cap, 2(M-1))`,
energy threshold `rank_rtol=1e-5`, floor `eigen_floor=1e-6`), `ch`, `ce` are the two
covariances in that basis with `+ ridge*I` (`ridge=1e-3`), `R` is the orthogonal Procrustes
map between the whitened clouds. Implementation:
`analysis/calibration_methods.py:169-207` (`fit_whitened_procrustes`,
`apply_whitened_procrustes`). The fit path is a byte-for-byte restatement of
`statebridge.py:50-63`; the only change is that the parameters are returned and reused
instead of refitted per message.

Why the method is justified: whitening is a Mahalanobis normalisation of each population,
and the rotation is the isogonal map that carries one whitened geometry onto the other. If
the message rows and the calibration rows are draws from the same two distributions, the
*population* whitener is the right object and the production per-message fit is a noisy
estimate of it. The estimator is therefore principled. The question this report answers is
whether it transfers.

Three properties follow from the algebra and are unit-tested
(`extensions/sglang-occ3/tests/test_alignment_calibration.py`):

1. On the rows it was fitted on, B reproduces production to float32 precision: the test
   pins max absolute difference below 5e-5 for `(n,d)` = (8,64), (32,512) and (128,6144)
   (`extensions/sglang-occ3/tests/test_alignment_calibration.py:59-70`).
2. Off-sample, any direction whose calibration variance is below `ridge` is amplified by up
   to `1/sqrt(ridge) = 31.6` relative to a unit-variance direction, on rows that carry real
   energy there. This is the whitening gain hazard.
3. Energy outside `span(Q)` is discarded: the map sends those components to `mu_E`
   identically (tested directly with rows orthogonal to `span(Q)`).

## 2. Results

| scenario | M | row_cos | swap | swap_fid | retr | rel_recon | rel_dir | norm_mean |
|---|---|---|---|---|---|---|---|---|
| null | 128 | 0.0717 | 1.3965 | 0.0023 | 0.0000 | 3.7002 | 1.3625 | 3.5722 |
| null | 512 | 0.4260 | 0.7056 | 0.0108 | 0.0026 | 0.9170 | 1.0714 | 0.5684 |
| null | 2048 | 0.3918 | 0.8436 | 0.0047 | 0.0000 | 0.9490 | 1.1029 | 0.6121 |
| weak | 128 | 0.1105 | 1.3928 | 0.0487 | 0.0339 | 3.8198 | 1.3338 | 3.7448 |
| weak | 512 | 0.5202 | 0.7154 | 0.2229 | 0.6875 | 0.8559 | 0.9796 | 0.5694 |
| weak | 2048 | 0.5112 | 0.8357 | 0.2236 | 0.9219 | 0.8657 | 0.9887 | 0.6059 |
| mid | 128 | 0.1764 | 1.3932 | 0.1148 | 0.4922 | 3.4268 | 1.2835 | 3.4001 |
| mid | 512 | 0.5690 | 0.8802 | 0.3420 | 0.9948 | 0.8254 | 0.9284 | 0.6270 |
| mid | 2048 | 0.5647 | 0.9517 | 0.3424 | 0.9974 | 0.8326 | 0.9330 | 0.6599 |
| strong | 128 | 0.3484 | 1.3807 | 0.2801 | 1.0000 | 2.4246 | 1.1416 | 2.5226 |
| strong | 512 | 0.6469 | 1.0371 | 0.4880 | 1.0000 | 0.7687 | 0.8404 | 0.7207 |
| strong | 2048 | 0.7211 | 1.1207 | 0.6077 | 0.7031 | 0.7000 | 0.7468 | 0.8044 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 128,512,2048 --seeds 0,1,2 --scenarios null,weak,mid,strong --lambdas 1e-4,1e-3,1e-2 --out reports/occ3-alignment-repair/analysis\calibration_results.json --tables reports/occ3-alignment-repair/analysis\tables`

`row_cos` is the mean row cosine against the norm-calibrated reference rows, `swap` the
relative output change when the hidden rows are replaced by unrelated rows of the same
family, `swap_fid` the cosine between that change and the true change in the target rows,
`retr` reference-token top-1 accuracy (chance 0.000237), `rel_dir` the scale-free
reconstruction error, `norm_mean` the mean output norm over the scalar target norm (1.0 is
correct). The mean predictor scores `row_cos = 0.4899`; the untrained affine copy
`mu_E + s(h - mu_H)` scores 0.5120 (`weak`), 0.5485 (`mid`), 0.6148 (`strong`); the linear
oracle scores 0.6837, 0.8109, 0.9412.

## 3. Reading the table

**M = 128 is a magnitude failure, not a direction failure.** The retained rank is 254 while
the calibration data rank is 127, so the ridge floor - not the data - sets the whitening
gain. Output norms are 2.42x to 3.82x the target, reconstruction error 2.42 to 3.82, and
`row_cos` 0.07 to 0.35. Row normalisation does not rescue it: `rel_dir` is 1.14 to 1.36,
i.e. the direction is wrong too, and `swap_fid` is 0.002 in the `null` scenario, which says
the 1.40 sensitivity of this configuration is pure noise amplification. The production
operator hides this failure by construction, because it rescales every row to the scalar
target norm (statebridge.py:64-68); a frozen artifact does not, so the same operator
becomes visibly unstable the moment it is frozen.

**M >= 512 lands 0.008 to 0.032 above the untrained copy and far below the oracle.** `mid`:
0.5690 at `M=512` (copy 0.5485, oracle 0.8109). `strong`: 0.6469 (copy 0.6148, oracle
0.9412). `weak`: 0.5202 (copy 0.5120, oracle 0.6837). The whitener is not recovering the
content direction: it is reproducing the mean plus a fraction of the shared component, which
is what the untrained copy already does.

**Retrieval is unstable across the calibration size.** `weak`: 0.0339 / 0.6875 / 0.9219 for
`M = 128 / 512 / 2048`; `strong`: 1.0000 / 1.0000 / 0.7031. A method whose top-1 accuracy
can double and then collapse as more calibration data arrives is not a frozen artifact
anyone can ship, because the failure is invisible in `row_cos` (0.6469 to 0.7211 while
retrieval falls from 1.0000 to 0.7031).

**`null` is the honest stress case.** With no shared code the method emits norm 3.57 at
`M=128` and `row_cos` 0.39 to 0.43 at `M >= 512`, below the mean predictor's 0.4899. It has
no fallback: nothing in the fit recognises that there is no signal.

## 4. Sensitivity checks

### 4.1 The retained rank is the lever at M = 128 and irrelevant at M = 512

| rank_cap | M | row_cos | swap | retr | norm_mean |
|---|---|---|---|---|---|
| 64 | 512 | 0.5729 | 0.814 | 0.977 | 0.600 |
| 128 | 512 | 0.5723 | 0.840 | 0.977 | 0.610 |
| 512 | 512 | 0.5776 | 1.086 | 0.551 | 0.745 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --rank-cap 64 --scenarios mid --ms 512 --seeds 0,1 --lambdas 1e-3 --out reports/occ3-alignment-repair/analysis/sens/cap64.json --tables reports/occ3-alignment-repair/analysis/sens/cap64 --label cap64` (with `--rank-cap` 64, 128 and 512 respectively;
seeds 0-1, scenario `mid`). Raising the cap from 64 to 512 moves `row_cos` by 0.005 and
collapses retrieval from 0.977 to 0.551: a wider basis adds noise, not signal. The
`M=128` failure is therefore caused by rank saturation (rank > data rank), not by the cap
value, and it cannot be reproduced at `M=512` by changing the cap.

### 4.2 The method only works when the fixture makes copying work

| kappa | row_cos | swap | retr | untrained copy `row_cos` |
|---|---|---|---|---|
| 0.00 | 0.4783 | 0.678 | 0.312 | 0.4962 |
| 0.35 | 0.5258 | 0.793 | 0.930 | 0.5221 |
| 0.50 | 0.5690 | 0.880 | 0.995 | 0.5485 |
| 0.70 | 0.6416 | 0.990 | 1.000 | 0.5953 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --kappa 0.0 --scenarios mid --ms 512 --seeds 0,1,2 --lambdas 1e-3 --out reports/occ3-alignment-repair/analysis/sens/kappa0.json --tables reports/occ3-alignment-repair/analysis/sens/kappa0 --label kappa0` and the same command with `--kappa 0.35`, `0.5`
from the main grid and `--kappa 0.7` (scenario `mid`, `M=512`, seeds 0-2).

At `kappa = 0` B is at the mean predictor's level (0.4783 against 0.4962) - the whitened
Procrustes cannot use a purely rotated code. Its apparent quality at `kappa = 0.5` is the
directly shared component, which is exactly what the untrained copy already exploits
(0.5690 against 0.5485: a gain of 0.020).

### 4.3 A more concentrated spectrum inflates `row_cos` and destroys retrieval

`spec_exp = 1.2`, `mid`: `row_cos` 0.5806, `retr` 0.469 (against 0.5690 / 0.9948 at the
default 0.7). `strong`: `row_cos` 0.7868, `retr` 0.117. As the spectrum concentrates, the
whitener's gain is dominated by a few directions, the outputs collapse onto a low-dimensional
cone, mean cosine rises and discriminability collapses. Command:
`python reports/occ3-alignment-repair/analysis/calibration_experiment.py --spec-exp 1.2 --scenarios mid,strong --ms 512 --seeds 0,1 --lambdas 1e-3 --label spec12 --out reports/occ3-alignment-repair/analysis/sens/spec12.json --tables reports/occ3-alignment-repair/analysis/sens/spec12`.

## 5. Verdict

METHOD B does not generalise. It is dominated on both axes by METHOD D at every calibration
size and by the untrained affine copy in `row_cos` at `kappa = 0`. Its frozen form is
numerically unsafe at `M=128` (norm 3.4x), its retrieval is non-monotone in the calibration
size, and its residual advantage over a plain copy is 0.008 to 0.032 `row_cos`.

The estimator is not the problem, the model class is: a whitened orthogonal map is a
*normalised* isometry, and normalising by the calibration spectrum turns the ridge floor
into an amplifier on every direction the calibration set does not constrain. The two
mechanisms that would fix it are both already in METHOD D: a per-direction gain determined by
the joint signal-to-noise ratio rather than by the marginal spectra, and a shrinkage target
(the calibration mean) that degrades gracefully when there is nothing to learn. What would
change this verdict: a corpus whose `M` is much larger than `d` (so that `rank > M-1` cannot
happen and the whitener is estimated from a well-conditioned spectrum). At `d=6144` that is
not the regime of a 128-2048 row calibration set.
