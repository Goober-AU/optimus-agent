# METHOD D - dual ridge alignment

## 1. What the method is

Fit on `(H_cal, E_cal)` only, then freeze; apply to any new row:

    A = H_cal - mu_H_cal,  B = E_cal - mu_E_cal
    K = A A^T + lambda I            (M x M, Cholesky),  lambda = lam_rel * mean(diag(K_without_lambda))
    Z = mu_E_cal + (H_msg - mu_H_cal) A^T K^{-1} B

Implementation `analysis/calibration_methods.py:255-290` (`fit_dual_ridge`,
`apply_dual_ridge`). The solve is a Cholesky factor plus triangular solves; no explicit
inverse and no `d x d` matrix anywhere. The frozen artifact is `mu_H`, `mu_E`, `A^T K^{-1}`
(`d x M`) and `B` (`M x d`), i.e. `2*M*d + M^2 + 2*d` floats.

By the push-through identity,

    A^T (A A^T + lambda I)^{-1} B = (A^T A + lambda I)^{-1} A^T B

so this is exactly the primal ridge estimator - the linear minimum-mean-square-error
predictor of the embedding row from the hidden row under an isotropic Gaussian prior on the
map. The dual form only changes *how* it is computed: an `M x M` system instead of a
`d x d` one, which is the difference between 0.5 MFLOP and 190 GFLOP per solve at
`M=512`, `d=6144`.

Why the method is justified on principle:

1. **Per-direction gain.** Ridge applies the shrinkage `lambda_i/(lambda_i + lambda)` in the
   eigenbasis of `A^T A`, so well-determined directions keep their gain and noise directions
   are suppressed. This is the signal-to-noise compromise that a single global scale
   (METHOD C) cannot make, and the one that whitening (METHOD B) makes in the wrong
   direction, because B normalises by the *marginal* spectra instead of by the joint
   cross-covariance.
2. **No target whitening.** `ce^{1/2}` never appears, so embedding noise cannot be
   amplified. The output's scale comes from `B` directly.
3. **No basis rank to choose.** `Q`, `rank_cap`, `rank_rtol` and `eigen_floor` all disappear,
   together with the truncation and identity-completion pathologies of B and C.
4. **Stable solver.** A Cholesky factor of a strictly positive definite `M x M` matrix; the
   only failure mode is `lambda` too small relative to the row norms, which is a tuning
   question, not a numerical one (tested: 200 random configurations, no non-finite output).

## 2. Results

| scenario | M | row_cos | swap | swap_fid | retr | rel_recon | rel_dir | norm_mean |
|---|---|---|---|---|---|---|---|---|
| null | 128 | 0.3963 | 0.8486 | 0.0247 | 0.0026 | 0.9488 | 1.0988 | 0.6194 |
| null | 512 | 0.3899 | 0.8571 | 0.0165 | 0.0000 | 0.9517 | 1.1046 | 0.6184 |
| null | 2048 | 0.2916 | 1.1587 | 0.0122 | 0.0000 | 1.1049 | 1.1903 | 0.8257 |
| weak | 128 | 0.6085 | 0.9049 | 0.4205 | 0.7292 | 0.7958 | 0.8848 | 0.6525 |
| weak | 512 | 0.6150 | 1.0135 | 0.4352 | 0.9922 | 0.7960 | 0.8775 | 0.7057 |
| weak | 2048 | 0.5243 | 1.1723 | 0.3408 | 0.9818 | 0.9234 | 0.9754 | 0.8655 |
| mid | 128 | 0.7411 | 1.0812 | 0.6374 | 1.0000 | 0.6760 | 0.7195 | 0.7786 |
| mid | 512 | 0.7631 | 1.1344 | 0.6682 | 1.0000 | 0.6543 | 0.6884 | 0.8229 |
| mid | 2048 | 0.7038 | 1.2016 | 0.5947 | 1.0000 | 0.7510 | 0.7697 | 0.9200 |
| strong | 128 | 0.8972 | 1.1904 | 0.8561 | 1.0000 | 0.4553 | 0.4535 | 0.9314 |
| strong | 512 | 0.9207 | 1.2123 | 0.8871 | 1.0000 | 0.4061 | 0.3983 | 0.9522 |
| strong | 2048 | 0.8991 | 1.2281 | 0.8583 | 1.0000 | 0.4622 | 0.4492 | 0.9818 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 128,512,2048 --seeds 0,1,2 --scenarios null,weak,mid,strong --lambdas 1e-4,1e-3,1e-2 --out reports/occ3-alignment-repair/analysis\calibration_results.json --tables reports/occ3-alignment-repair/analysis\tables`

`cos_oracle` is the linear oracle `O` (the generator's predictable part of the target row),
which no method seeing only `H_msg` can beat. The mean predictor scores 0.4899 and the
untrained affine copy `mu_E + s(h - mu_H)` scores 0.5120 / 0.5485 / 0.6148
(`weak` / `mid` / `strong`).

Fraction of the oracle ceiling reached at `M=512`: **0.899** (`weak`), **0.941** (`mid`),
**0.978** (`strong`). Direction error relative to the oracle's: 1.10x, 1.12x, 1.16x. Hidden
state sensitivity: 1.0135 / 1.1344 / 1.2123 against the oracle's 0.9864 / 1.1264 / 1.2068,
i.e. D is 1-3% *more* sensitive than the oracle, with lower fidelity of that sensitivity to
the true change (`swap_fid` 0.4352 / 0.6682 / 0.8871 against 0.5474 / 0.7414 / 0.9219), so a
small part of D's sensitivity is noise rather than signal. Reference-token retrieval is
1.0000 in `mid` and `strong` at every calibration size, and 0.7292-0.9922 in `weak`.

## 3. Sensitivity checks

### 3.1 Lambda: almost inert at small M, a real lever at large M

| M | lam_rel | row_cos | rel_dir | norm_mean | retr |
|---|---|---|---|---|---|
| 128 | 1e-05 | 0.7414 | 0.7192 | 0.777 | 1.0000 |
| 128 | 0.001 | 0.7414 | 0.7192 | 0.776 | 1.0000 |
| 128 | 0.1 | 0.7433 | 0.7165 | 0.759 | 0.9961 |
| 512 | 1e-05 | 0.7635 | 0.6878 | 0.819 | 1.0000 |
| 512 | 0.001 | 0.7636 | 0.6876 | 0.818 | 1.0000 |
| 512 | 0.1 | 0.7714 | 0.6762 | 0.799 | 1.0000 |
| 2048 | 1e-05 | 0.7030 | 0.7707 | 0.916 | 1.0000 |
| 2048 | 0.001 | 0.7039 | 0.7695 | 0.915 | 1.0000 |
| 2048 | 0.1 | 0.7501 | 0.7070 | 0.850 | 1.0000 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --scenarios mid --ms 128,512,2048 --seeds 0,1 --lambdas 1e-5,1e-3,1e-1 --out reports/occ3-alignment-repair/analysis/sens/lam.json --tables reports/occ3-alignment-repair/analysis/sens/lam --label lam` (scenario `mid`; `1e-5` and `1e-1` from a seeds 0-1 sweep,
`1e-3` from the main grid).

Over four orders of magnitude of `lambda` the row cosine moves by 0.002 at `M=128` and by
0.008 at `M=512`. Only at `M=2048` does the choice matter (0.7030 -> 0.7501, +0.047). Two
consequences:

- **The method is not tuned to the fixture.** A 100x change in the only hyper-parameter
  leaves the ranking and the operating point intact at the recommended size.
- **The `M=2048` degradation is only partly a regularisation-scale artifact.** A
  size-proportional `lambda` (`4e-3` at `M=2048`, i.e. 4x the `M=512` value) recovers only
  +0.003 of the 0.059 gap (`python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 2048 --seeds 0,1,2 --scenarios mid,strong --lambdas 4e-3 --label lamlaw --out reports/occ3-alignment-repair/analysis/sens/lamlaw.json --tables reports/occ3-alignment-repair/analysis/sens/lamlaw`), while a 100x larger `lambda` recovers +0.046. The
  residual mechanism is not isolated in this study (HYPOTHESIS: the dual kernel's dimension
  grows with `M`, so more noise directions enter the solve while the rank-64 code subspace is
  already saturated by a few hundred rows).

### 3.2 The advantage does not depend on the directly shared component

| kappa | row_cos | swap | retr | norm_mean |
|---|---|---|---|---|
| 0.00 | 0.7629 | 1.133 | 1.0000 | 0.828 |
| 0.35 | 0.7629 | 1.133 | 1.0000 | 0.828 |
| 0.50 | 0.7631 | 1.134 | 1.0000 | 0.823 |
| 0.70 | 0.7630 | 1.134 | 1.0000 | 0.828 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --kappa 0.7 --scenarios mid --ms 512 --seeds 0,1,2 --lambdas 1e-3 --out reports/occ3-alignment-repair/analysis/sens/kappa70.json --tables reports/occ3-alignment-repair/analysis/sens/kappa70 --label kappa70` and the same command with `--kappa 0.0` and `--kappa 0.35`; `0.50` from
the main grid (scenario `mid`, `M=512`, seeds 0-2).

D is invariant to `kappa` to three decimals. The other candidates are not: at `kappa = 0`
METHOD C and its oracle fall to the mean predictor (0.4955, 0.4978) and METHOD B falls to
0.4783, while D holds 0.7629 and retrieval 1.0000. This is the cleanest single demonstration
that D's gain comes from the joint cross-covariance rather than from a shared ambient
component that a copy could exploit.

### 3.3 No basis, so no basis pathology

`--rank-cap 64 / 128 / 512` leaves D bit-identical (0.7636 in all three runs): the method
has no retained basis to truncate. Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --rank-cap 512 --scenarios mid --ms 512 --seeds 0,1 --lambdas 1e-3 --out reports/occ3-alignment-repair/analysis/sens/cap512.json --tables reports/occ3-alignment-repair/analysis/sens/cap512 --label cap512` and the same with `--rank-cap 64`
and `--rank-cap 128`.

### 3.4 A more concentrated spectrum helps

`spec_exp = 1.2`, `M=512`, seeds 0-1: `mid` 0.7777 (against 0.7636), `strong` 0.9262
(against 0.9207) with retrieval 1.0000. A more concentrated code spectrum is easier for a
ridge, which is the expected direction. Command: same as Section 3.3 with `--spec-exp 1.2`
and `--scenarios mid,strong`.

## 4. Cost

| M | frozen artifact | one-off fit | apply, per message | row_cos (`mid`) |
|---|---|---|---|---|
| 128 | 6.11 MiB | 0.403 GFLOP | 1.57 MFLOP | 0.7411 |
| 512 | 25.05 MiB | 6.485 GFLOP | 6.29 MFLOP | 0.7631 |
| 2048 | 112.05 MiB | 105.94 GFLOP | 25.17 MFLOP | 0.7038 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 128,512,2048 --seeds 0,1,2 --scenarios null,weak,mid,strong --lambdas 1e-4,1e-3,1e-2 --out reports/occ3-alignment-repair/analysis\calibration_results.json --tables reports/occ3-alignment-repair/analysis\tables` (cost columns from the same records as the results table; MFLOP is a
flop-count estimate under the conventions stated in `CALIBRATION-DESIGN.md` Section 4, not a
wall-clock measurement).

The per-message comparison with production is the practical argument: METHOD A spends 2.426
GFLOP per message (2.010 GFLOP fitting a 254-dim SVD plus 0.416 GFLOP applying it), while
frozen D at `M=512` spends 6.29 MFLOP per message, a factor of 386. The 6.485 GFLOP one-off
fit pays for itself after about 3 messages, and the 25 MiB artifact replaces a per-message
6.25 MiB temporary. At `M=2048`
the artifact grows to 112 MiB, which is the reason to prefer `M=512` unless `M=2048` buys
something measurable (it does not: 0.7038 against 0.7631 in `mid`).

## 5. Failure modes

1. **No signal, no fallback.** In the `null` scenario D lands *below* the mean predictor:
   0.3963 / 0.3899 / 0.2916 against 0.4899, with output norms 0.62 / 0.62 / 0.83 and
   retrieval 0.0026 / 0.0000 / 0.0000. The estimator always finds *some* linear map in a
   6144-dimensional space, and with a small `lambda` relative to the kernel it fits
   calibration noise. Any deployment needs a guard: a held-out `R^2` on the calibration rows,
   falling back to `mu_E` when it is below a threshold. That guard is not implemented or
   measured here (HYPOTHESIS for its effectiveness).
2. **The output radius undershoots.** Mean output norm over the target is 0.779-0.920 in
   `mid` and 0.931-0.982 in `strong`. Ridge regression is unbiased only in expectation over
   the residual, and the residual contains idiosyncratic noise that no map from `H` can
   predict, so the predicted row is systematically shorter than the target row. This costs
   `rel_recon` but not `row_cos`; a deployment that needs the radius should renormalise, and
   renormalisation does not change `row_cos` or `retr`.
3. **`M=2048` is worse than `M=512`** in `mid` (0.7038 against 0.7631) and `strong` (0.8991
   against 0.9207). Section 3.1 bounds how much of that is `lambda`; the rest is unexplained.
4. **The artifact is linear in `M`.** 112 MiB at `M=2048`. A production deployment at that
   size needs a memory budget, or a subsampled calibration set.

## 6. Verdict

**This is the answer to the central question: yes, a frozen alignment fitted on independent
calibration rows generalises usefully, and the dual ridge is the candidate that does it.**
At `M=512` it reaches 89.9-97.8% of the linear oracle's row cosine across the three
signal-bearing scenarios, with reference-token retrieval 1.0000 in `mid` and `strong`,
while keeping full hidden-state dependence (1.0135-1.2123, against 0.0001-0.0005 for the
production operator). It has one hyper-parameter and is insensitive to it at the recommended
size, no basis rank, no whitening gain, a stable Cholesky solve, and a per-message cost 386x
below production's refit.

What it needs before deployment: a null/`R^2` guard (Section 5.1), a decision on output
renormalisation (Section 5.2), and a real calibration corpus that satisfies
`CALIBRATION-DESIGN.md` Section 5. Recommended operating point: `M = 512`, `lam_rel = 1e-3`
to `1e-2`, artifact 25 MiB, one-off fit 6.485 GFLOP.
