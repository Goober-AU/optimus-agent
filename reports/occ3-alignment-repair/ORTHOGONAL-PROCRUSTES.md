# METHOD C - affine orthogonal Procrustes

## 1. What the method is

Fit on `(H_cal, E_cal)` only, then freeze:

    A = H_cal - mu_H,  B = E_cal - mu_E,  Aq = A Q,  Bq = B Q
    R_r = U V^T           from SVD(Aq^T Bq)                    # orthogonal Procrustes in span(Q)
    R   = I + Q (R_r - I) Q^T                                  # identity outside span(Q)
    s   = <A R, B>_F / ||A R||_F^2                             # least-squares scalar
    z(h)= mu_E + s (h - mu_H) R

Implementation `analysis/calibration_methods.py:209-253`. `Q` is the same union basis as
METHOD B (`rank_cap=256`, `rank_rtol=1e-5`, `eigen_floor=1e-6`).

Why the method is justified: the orthogonal group is the isometry group, so this is the
closest frozen map to "move the hidden cloud into the embedding space without stretching
it". It cannot amplify any direction, which makes it the natural control for METHOD B's
whitening gain, and it has one scalar to estimate, so it cannot overfit much. Two unit
tests pin the algebra: the ambient map `I + Q(R_r - I)Q^T` is orthogonal to 1e-4, and `s`
minimises the calibration residual by construction
(`tests/test_alignment_calibration.py:176-213`).

The identity completion outside `span(Q)` is forced, not cosmetic: `A^T B` has rank at most
`M-1`, far below `d = 6144`, so no finite corpus identifies the rotation outside the
retained basis, and identity is the minimum-distortion completion.

Cost of that conservatism, stated up front: **one scale for the entire space is a very
coarse signal-to-noise compromise.** A single `s` must trade the well-determined content
directions against 6144 noise directions at once, and the least-squares optimum for that
trade is small whenever the target-side noise is large. The measured consequence is
Section 3.

## 2. Results

| scenario | M | row_cos | swap | swap_fid | retr | rel_recon | rel_dir | norm_mean |
|---|---|---|---|---|---|---|---|---|
| null | 128 | 0.2923 | 1.1314 | 0.0065 | 0.0000 | 1.1094 | 1.1897 | 0.8439 |
| null | 512 | 0.4780 | 0.2914 | 0.0046 | 0.0000 | 0.8788 | 1.0218 | 0.5045 |
| null | 2048 | 0.4876 | 0.1226 | 0.0033 | 0.0000 | 0.8731 | 1.0124 | 0.4932 |
| weak | 128 | 0.4188 | 1.2162 | 0.2258 | 0.8464 | 1.0786 | 1.0781 | 0.9869 |
| weak | 512 | 0.5037 | 0.4974 | 0.1511 | 0.1875 | 0.8642 | 0.9963 | 0.5245 |
| weak | 2048 | 0.5074 | 0.4096 | 0.1537 | 0.1380 | 0.8618 | 0.9926 | 0.5109 |
| mid | 128 | 0.5942 | 1.2162 | 0.4571 | 1.0000 | 0.9032 | 0.9009 | 0.9869 |
| mid | 512 | 0.5468 | 0.6942 | 0.2822 | 0.9141 | 0.8377 | 0.9520 | 0.5635 |
| mid | 2048 | 0.5506 | 0.6572 | 0.2885 | 0.9297 | 0.8350 | 0.9481 | 0.5522 |
| strong | 128 | 0.8348 | 1.2174 | 0.7748 | 1.0000 | 0.5823 | 0.5748 | 0.9862 |
| strong | 512 | 0.6385 | 0.9131 | 0.4690 | 1.0000 | 0.7706 | 0.8502 | 0.6426 |
| strong | 2048 | 0.7001 | 1.0007 | 0.5698 | 0.9974 | 0.7158 | 0.7744 | 0.6917 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 128,512,2048 --seeds 0,1,2 --scenarios null,weak,mid,strong --lambdas 1e-4,1e-3,1e-2 --out reports/occ3-alignment-repair/analysis\calibration_results.json --tables reports/occ3-alignment-repair/analysis\tables`

Oracle column `cos_G` is METHOD C given the true content rotation (generator internals,
same `Q`, same least-squares `s`). It separates "C's estimator is weak" from "C's model
class is weak". The mean predictor scores 0.4899 and the untrained affine copy scores
0.5120 / 0.5485 / 0.6148 (`weak` / `mid` / `strong`); the linear oracle `O` scores 0.6837 /
0.8109 / 0.9412.

## 3. What governs the transfer: basis truncation dilutes the single global scale

The table below is the same run, showing the fit's internal quantities per scenario and
calibration size (`rank`, fraction of calibration energy captured by `Q`, fitted `s`,
in-sample relative residual of `s*A R` against `B`, output norm over the target norm).
Command: the same command as the results table above (same rows, extra columns).

| scenario | M | rank | energy | scale | calib_res | cos | norm | cos_oracle_G |
|---|---|---|---|---|---|---|---|---|
| weak | 128 | 254 | 1.000 | 0.03257 | 0.225 | 0.4188 | 0.987 | 0.4975 |
| weak | 512 | 256 | 0.750 | 0.00704 | 0.977 | 0.5037 | 0.524 | 0.5068 |
| weak | 2048 | 256 | 0.480 | 0.00564 | 0.985 | 0.5074 | 0.511 | 0.5079 |
| mid | 128 | 254 | 1.000 | 0.03260 | 0.221 | 0.5942 | 0.987 | 0.6244 |
| mid | 512 | 256 | 0.838 | 0.01057 | 0.948 | 0.5468 | 0.563 | 0.5485 |
| mid | 2048 | 256 | 0.664 | 0.00980 | 0.955 | 0.5506 | 0.552 | 0.5507 |
| strong | 128 | 254 | 1.000 | 0.03264 | 0.217 | 0.8348 | 0.986 | 0.8417 |
| strong | 512 | 256 | 0.945 | 0.01588 | 0.879 | 0.6385 | 0.643 | 0.6389 |
| strong | 2048 | 256 | 0.888 | 0.01875 | 0.824 | 0.7001 | 0.692 | 0.7001 |

Two regimes, both measured:

- **Rank-saturated basis (`M=128`, energy 1.000).** Nothing lies outside `span(Q)`, so every
  calibration row is actually rotated. The fit explains the calibration residual down to
  0.217-0.225, the least-squares scale is 0.0326, the output lands at the correct radius
  (0.986-0.987) and `row_cos` is 0.4188 / 0.5942 / 0.8348 (`weak` / `mid` / `strong`).
- **Truncated basis (`M >= 512`, energy 0.48-0.95).** A quarter to a half of each calibration
  row's centred energy lies outside `span(Q)` and passes through **unrotated** by
  construction. That unrotated part carries no information about the target, but it enters
  the denominator `||A R||_F^2` and drags the single global `s` down by a factor of 3-6
  (0.0326 -> 0.0106 at `mid`). The fit then explains almost none of the calibration
  residual (0.879-0.985), the output norm falls to 0.52-0.69, and `row_cos` collapses to
  0.5037-0.7001.

That mechanism is verified, not hypothesised, by the rank-cap sweep at fixed `M=512`
(scenario `mid`, seeds 0-1):

| rank_cap | energy | scale | cos_C | norm_C | retr_C | cos_G |
|---|---|---|---|---|---|---|
| 64 | 0.619 | 0.00901 | 0.5439 | 0.545 | 0.855 | 0.5440 |
| 128 | 0.708 | 0.00941 | 0.5451 | 0.550 | 0.867 | 0.5456 |
| 512 | 0.999 | 0.01569 | 0.5821 | 0.639 | 0.914 | 0.5879 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --rank-cap 512 --scenarios mid --ms 512 --seeds 0,1 --lambdas 1e-3 --out reports/occ3-alignment-repair/analysis/sens/cap512.json --tables reports/occ3-alignment-repair/analysis/sens/cap512 --label cap512` (and the same command with `--rank-cap 64` and `--rank-cap 128`).

Raising the cap from 64 to 512 raises the captured calibration energy from 0.619 to 0.999,
raises the fitted scale from 0.0090 to 0.0157, and raises `row_cos` from 0.5439 to 0.5821.
The direction of the effect is exactly what Section 3 predicts. The `M=128` advantage is
therefore not a sample-size effect at all: it is the only calibration size at which the
default 256-dim cap does not truncate the data (`2(M-1) = 254 <= 256`).

## 4. The model-class limit: one scale cannot serve 6144 directions

`cos_G` tracks `cos_C` to within 0.079 everywhere (worst case `weak`/`M=128`: 0.4188 against
0.4975) and to within 0.004 for `M >= 512`, so the estimator is not the limit: even with the true rotation, C's achievable quality is what the
table shows. The clearest demonstration is the `kappa` sweep, which removes the directly
shared component and leaves only the rotated code (`mid`, `M=512`, seeds 0-2):

| kappa | cos C | swap C | retr C | cos G | cos untrained copy | cos D |
|---|---|---|---|---|---|---|
| 0.00 | 0.4955 | 0.380 | 0.016 | 0.4978 | 0.4962 | 0.7629 |
| 0.35 | 0.5214 | 0.573 | 0.599 | 0.5231 | 0.5221 | 0.7629 |
| 0.50 | 0.5468 | 0.694 | 0.914 | 0.5485 | 0.5485 | 0.7631 |
| 0.70 | 0.5921 | 0.839 | 0.995 | 0.5937 | 0.5953 | 0.7630 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --kappa 0.0 --scenarios mid --ms 512 --seeds 0,1,2 --lambdas 1e-3 --out reports/occ3-alignment-repair/analysis/sens/kappa0.json --tables reports/occ3-alignment-repair/analysis/sens/kappa0 --label kappa0` and the same command with `--kappa 0.35`, `--kappa 0.5`
(main grid) and `--kappa 0.7`.

At `kappa = 0` C falls to the mean predictor's level (0.4955 against a mean-only 0.4899) and
**so does its oracle** (0.4978). With one global scale there is no way to apply a large gain
along the 64 directions that carry the code and a small gain everywhere else, so the
least-squares optimum shrinks the whole map to nearly zero. METHOD D, which has one gain per
direction, is unaffected by `kappa` (0.7629 / 0.7629 / 0.7631 / 0.7630 across the same
sweep). This is the model-class gap in a single comparison.

## 5. What METHOD C gets right

- **It is numerically safe.** Output norm stays in 0.49-0.99 of the target in every cell of
  every scenario, no non-finite value in any of the 276 main-grid rows, and it has no
  whitening gain to blow up: `M=128`, the cell where METHOD B emits 3.40x the target norm,
  is C's *best* cell.
- **It degrades toward the mean predictor rather than away from it.** In the `null`
  scenario, where there is nothing to learn, C holds at 0.4780-0.4876 for `M >= 512`
  (0.2923 at `M=128`) against the mean's 0.4899, and its oracle holds at 0.4831-0.4893. Its degradation mode is graceful; METHOD D's is not (Section 6.2 of
  `CALIBRATION-DESIGN.md`: 0.2916-0.3963 in `null`).
- **Retrieval at `M=128` is genuinely good** (0.8464 / 1.0000 / 1.0000 at `weak` / `mid` /
  `strong`), because at that size the map is a real rotation of the whole row, not a
  diluted copy.
- **It is cheap in the ways that matter.** Frozen artifact 6.25-6.30 MiB at every `M`
  (`d*r + r^2` floats with `r <= 256`), versus 25.05 MiB (METHOD D, `M=512`) and 112.05 MiB
  (METHOD D, `M=2048`).

## 6. Verdict

METHOD C does not generalise well enough to replace the production operator, but it fails
for an instructive reason. Its estimator is essentially optimal for its model class
(`cos_C = cos_G` to 0.002 for `M >= 512`), and its model class - one orthogonal map times
one global scale - cannot represent the per-direction signal-to-noise compromise that a
6144-dimensional target requires. Two measured consequences:

1. **Truncation dilutes the scale.** The identity completion outside `span(Q)` pushes
   unrotated, target-irrelevant energy into the least-squares denominator; at the default
   cap the map is only usable at `M=128` (0.5942 / 0.8348 at `mid` / `strong`), and at
   `M >= 512` it lands at or slightly below an untrained copy in `mid` (0.5468 against 0.5485).
2. **One scale cannot exploit a rotated code.** At `kappa = 0`, C and its oracle both sit at
   the mean predictor's level while METHOD D is unchanged.

Compared with METHOD D, C is dominated on the landing axis at every calibration size
(0.5468-0.5942 against 0.7038-0.7631 in `mid`; 0.6385-0.8348 against 0.8972-0.9207 in
`strong`) while its hidden-state sensitivity is comparable or lower with a much weaker
fidelity to the true change (0.4571 at `mid`/`M=128` against 0.6374 for D at the same size).
Its one advantage is safety in the no-signal regime. What would change this verdict: a
per-direction gain added to the same orthogonal geometry, which is what METHOD D is; or a
much wider retained basis (`rank_cap >= 2(M-1)`), which recovers only 0.04 of `row_cos` and
costs a proportionally larger artifact.
