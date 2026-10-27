
# In-sample degeneracy of the per-message whitened Procrustes fit

Target (read-only): `extensions/sglang-occ3/occ3_sglang/statebridge.py`, `align_hidden_sequence()`
at `statebridge.py:32-70`, production call site `latent_worker.py:333`.

Labels on every claim:

- `[SOURCE-ATTESTED]` a fact read from the checked-in source, cited as `file:line`.
- `[OFFLINE MEASURED]` a number produced by a command in this report, cited by table.
- `[HYPOTHESIS]` an interpretation that this report does not measure.

No DGX, no network, no GPU, no model weights, no git. Every measurement is CPU float32
Torch 2.8.0 on synthetic fixtures of `N = 128` rows and `D = 6144` columns.

## 1. Question and answer

`[SOURCE-ATTESTED]` Once per message the worker takes the accepted span of at most 128 rows
(`latent_worker.py:309`), gathers the final-layer pre-norm hidden states `h`
(`latent_worker.py:320-321`), builds the input embeddings of the same tokens,
`embeddings = self.embedding(tokens)` (`latent_worker.py:325`), and calls
`align_hidden_sequence(h, embeddings, self.target_norm)` with shipped defaults
`ridge = 1e-3`, `eigen_floor = 1e-6`, `rank_rtol = 1e-5` (`statebridge.py:32-33`). The result
becomes the aligned prefix (`latent_worker.py:334`).

The question: does the returned tensor depend on `H`?

The answer, derived in section 3 and measured in sections 5-9:

1. `[OFFLINE MEASURED]` At the shipped ridge the returned `Z` is the same for a correct `H`,
   an unrelated random `H`, a row-shuffled `H` and a scale-matched random `H`, to `2.3e-2`
   relative Frobenius at unit row amplitude and `2.4e-5` at hidden-state row amplitude
   (Table 2). Row-shuffling `H` leaves the output in its original row order (Table 2 and
   `test_row_permutation_of_h_does_not_permute_the_output`).
2. `[OFFLINE MEASURED]` The only condition that breaks the invariance is rank deficiency of the
   centred `H` (Table 7): a rank-8 `H` gives `cos(Z_i, E_i) = 0.281` and a relative
   reconstruction error of `1.199`.
3. `[HYPOTHESIS]` Therefore an in-sample alignment residual, or an in-sample cosine between `Z`
   and `E`, is not evidence that the two representations are related. It cannot fail on any
   full-rank pair of clouds, including a pair with no relation at all.

## 2. Notation and the shipped algorithm

`[SOURCE-ATTESTED]` With `H, E` in `R^{N x d}` and `N <= 128 < d/2 = 3072`:

```
(1)   mu_H = (1/N) 1^T H,          Hc = H - 1 mu_H^T,       1 in R^N
      mu_E = (1/N) 1^T E,          Ec = E - 1 mu_E^T

(2)   Sigma_H = Hc^T Hc / N + lambda I_d ,     Sigma_E = Ec^T Ec / N + lambda I_d

(3)   X = Hc Sigma_H^(-1/2) ,                  Y = Ec Sigma_E^(-1/2)

(4)   X^T Y = U S V^T   (SVD) ,                R* = U V^T

(5)   Z = X R* Sigma_E^(1/2) + mu_E
```

`[SOURCE-ATTESTED]` The code realises (1)-(5) in a reduced coordinate system: it takes the
truncated right singular vectors `q` of the stacked pair `[Hc; Ec]` (`statebridge.py:50-55`),
sets `bh = hc @ q` and `be = ec @ q` (`statebridge.py:56`), builds
`ch = bh^T bh / N + lambda I` and `ce = be^T be / N + lambda I` (`:58-59`), whitens (`:60-61`),
takes the Procrustes factor of `x^T y` (`:62`), and applies
`z = (x (U V^T) ce^(1/2)) q^T + mu_E` (`:63`). The powers come from `_symmetric_power`
(`statebridge.py:26-29`), which clamps eigenvalues at `eigen_floor` *before* taking the power,
for both `+1/2` and `-1/2`. The last lines rescale every row to `target_norm`
(`:64-68`), so `Z` is defined only up to that per-row scale and the comparison target is the
norm-calibrated `E`:

```
(6)   E_cal = E * (target_norm / ||E_i||)   row-wise
```

`[SOURCE-ATTESTED]` `target_norm` is `mean_embedding_norm(...)`, the mean row norm of the input
embedding weight (`statebridge.py:87-112`, called at `latent_worker.py:326-328`).

Remark 1 (the reduced coordinates are not an approximation).

```
(7)   Hc = bh q^T with q^T q = I_r ,  r = retained rank
      Sigma_H = q ch q^T + lambda (I - q q^T)
      Hc Sigma_H^(-1/2) = (bh ch^(-1/2)) q^T   =>   X X^T = x x^T exactly
```

So every statement below is proved in the ambient `d` dimensions (1)-(5) and is realised
unchanged by the shipped `r`-dimensional code. `eigen_floor` acts only inside `q`.

## 3. Derivation

### 3.1 Lemma 1 (row-space Gram identity)

Let `A in R^{N x d}` be centred with thin SVD `A = U_A diag(s_i) V_A^T`, `i = 1..k`,
`k = rank(A) <= N-1`. Then `Sigma_A = V_A (S^2/N + lambda) V_A^T + lambda (I - V_A V_A^T)`:

```
(8)   Sigma_A^(-1) = V_A (S^2/N + lambda)^(-1) V_A^T + lambda^(-1) (I - V_A V_A^T)

(9)   A Sigma_A^(-1) A^T = U_A diag( s_i^2 / (s_i^2 + N lambda) ) U_A^T
                          = N U_A diag(w_i) U_A^T ,     w_i = s_i^2 / (s_i^2 + N lambda)
```

`w_i` is in `(0,1]`, increasing in `s_i^2`, and equals `1` exactly when `lambda = 0`.
Applying (9) to (3):

```
(10)  X X^T = N U_H diag(w^H) U_H^T ,      Y Y^T = N U_E diag(w^E) U_E^T
```

`U_H` is the orthonormal basis of `col(Hc)` and carries nothing of `E`; `w^H` carries only the
spectrum of `Hc`. No other property of `H` enters `X X^T`.

### 3.2 Theorem 1 (lambda -> 0: the identity holds for any pair)

Centring gives `1^T Hc = 0`, so `col(Hc)` is a subspace of `1^perp`. If `rank(Hc) = N-1` then
`dim col(Hc) = N-1 = dim 1^perp`, so

```
(11)  U_H U_H^T = P = I - 1 1^T / N        (rank-(N-1) orthogonal projector)

(12)  lambda -> 0 :   X X^T -> N P ,   and identically   Y Y^T -> N P
```

Two clouds with equal Gram matrices are congruent, so an orthogonal map between them always
exists: with `X = U S V_X^T` and `Y = U S V_Y^T` (the same `U, S` because `X X^T = Y Y^T`),
`Y = X Q` for the orthogonal `Q = V_X V_Y^T`. Section 3.3 shows that the shipped `R*` is
exactly that `Q`, not merely some map that exists.

`[OFFLINE MEASURED]` (12) contains no property of `H` and none of `E`: it follows from
centring plus full centred row rank. Measured at `lambda = 0` (mirror, Table 9):
`||X X^T - N (I - 11^T/N)||_2 / N = 4.97e-6` at unit scale and `1.55e-4` at hidden scale,
where the residual is the float32 floor of section 3.6, not the ridge.

### 3.3 Theorem 2 (exact in-sample recovery in the limit)

Hypothesis: `X X^T = Y Y^T` (Theorem 1) and `X != 0`.

```
(13)  X = U S V_X^T ,   Y = U S V_Y^T       (the same U and S: X X^T = Y Y^T)
      A := X^T Y = V_X S U^T U S V_Y^T = V_X S^2 V_Y^T      (an SVD of A)
      R* = U_A V_A^T = V_X V_Y^T
      X R* = U S V_X^T V_X V_Y^T = U S V_Y^T = Y
```

`A` is square of rank `k < r`, so its polar factor is not unique; the computation above shows
that the product `U V^T` returned by any *single* SVD of `A` still equals `V_X V_Y^T`, because
one decomposition returns `U_A = V_X W` and `V_A = V_Y W` with a common orthogonal `W` on every
degenerate block. So the shipped `R*` is exactly the map used in (13). Substituting into (5):

```
(14)  Z = Y Sigma_E^(1/2) + mu_E = Ec Sigma_E^(-1/2) Sigma_E^(1/2) + mu_E
      Sigma_E^(-1/2) Sigma_E^(1/2) = V diag(clamp(v,f)^(-1/2)) diag(clamp(v,f)^(1/2)) V^T = I

(15)  Z = Ec + mu_E = E          exactly, for any H with rank(Hc) = N-1
```

Line (14) uses that `_symmetric_power` (`statebridge.py:26-29`) takes `+1/2` and `-1/2` from
the *same* `eigh` basis with the *same* `clamp_min(eigen_floor)`, so the clamp cancels instead
of biasing. That cancellation is why the limit is exact rather than approximate: the floor is a
gauge, not a regulariser.

`[OFFLINE MEASURED]` Table 9, `lambda = 0` rows (mirror, guard relaxed; the shipped guard
refuses `lambda = 0`, Table 4): `cos(Z_i, E_i) = 1.0` and the relative error to `E_cal` is
`2.6e-6` at unit scale and `8.5e-5` at hidden scale for the anisotropic fixture (`3.3e-6` and
`1.19e-4` for the isotropic one), which is the float32 floor of 3.6 and not the ridge.

### 3.4 Corollary 2a: the term that carries H, and why it is annihilated

The single `H`-carrying term of (5) is `X = Hc Sigma_H^(-1/2)`. `H` enters `Z` only through
`bh = Hc q` (which sets the coordinates and `ch`) and through `R*`, which is a function of both
clouds. The composite map of (5) is

```
(16)  Z - mu_E = Hc A(H) ,      A(H) = Sigma_H^(-1/2) R*(H, E) Sigma_E^(1/2)

(17)  by (15):   Hc A(H) = Ec         (A(H) is a right inverse of Hc on Ec)
```

`[SOURCE-ATTESTED]` System (17) has `N` scalar equations per column and `d > 2N` unknowns per
column, with `rank(Hc) = N-1`; it is underdetermined by construction. Every full-rank `Hc`
admits such an `A(H)`. The fitted map is fixed by the requirement to interpolate the `N`
fitted rows, and that requirement says nothing about how `H` and `E` are related.
`[HYPOTHESIS]` This is the mechanism, not a symptom: an interpolant exists for every pair of
clouds.

Equivalently, the `H`-dependent factors cancel pairwise. `X` shrinks direction `i` by
`w_i^(1/2)` (9) and `R*` re-expands it onto `Y`; in the limit (15) the product is `Y` for any
`H`.

### 3.5 The finite ridge lambda = 1e-3

`[SOURCE-ATTESTED]` The shipped call passes no `ridge`, so `lambda = 1e-3`
(`statebridge.py:32`) is compared against the centred row variance `s_i^2 / N`. Define

```
(18)  lambda_i = s_i^2 / N ,   lambda_min = min_i lambda_i    (live directions of Hc)

(19)  1 - w_i = N lambda / (s_i^2 + N lambda) = lambda / (lambda_i + lambda)

(20)  ||X X^T - N P||_2 / N = max_i (1 - w_i^H) = lambda / (lambda_min^H + lambda)
       <= lambda / lambda_min^H
(21)  ||X X^T - Y Y^T||_2 / N <= (1 - w_min^H) + (1 - w_min^E)
(22)  Z - E = (X R* - Y) Sigma_E^(1/2) ,   ||Z - E||_F <= ||X R* - Y||_F sqrt(lambda_max(Sigma_E))
```

`[OFFLINE MEASURED]` (20) is an equality, not an inequality, whenever `U_H` is a full
orthonormal basis of `1^perp`, i.e. exactly in the regime of Theorem 1. Table 10 verifies it to
four digits in every ridge-sweep cell, e.g. `1 - w_min^H = 2.568e-2` against a measured
`||X X^T - N P||_2 / N = 2.568e-2` at unit scale, `lambda = 1e-3`.

Measured size of `lambda / lambda_min` at the shipped ridge:

- `[OFFLINE MEASURED]` unit row amplitude, `lambda_min^H` in `2.38e-2 .. 3.79e-2`:
  `lambda / lambda_min = 2.6e-2 .. 4.2e-2`, so `w_min^H = 0.960` and the Gram identity (12) is
  violated by `0.040` relative. Table 3: `||X X^T - Y Y^T||_2 / N = 1.762e-1` at
  `lambda = 1e-3` for anisotropic `H`, against the bound of (21), `2.562e-1`.
- `[OFFLINE MEASURED]` hidden-state row amplitude (row norm 64), `lambda_min^H = 97.6`:
  `lambda / lambda_min = 1.02e-5`, `w_min^H = 0.999990`. Table 3.

So the finite ridge matters only when the centred row variance is within about two decades of
`lambda`. `[OFFLINE MEASURED]` Over every PRODUCTION run with a full-rank `H` and
`lambda <= 1e-1` (Tables 2, 3, 5 and 6, 158 rows) the mean cosine between `Z` and `E_cal` stays
at or above `0.9637`; the one row below that, `0.9600`, is the deliberate `MIRROR` cell with
`eigen_floor = 1.0`, where the floor itself clips real directions (Table 8).

### 3.6 Why lambda -> 0 is unreachable in float32, and what eigen_floor costs

`[SOURCE-ATTESTED]` The retained coordinate count is
`r = min(#{s_i > max(s_0 rank_rtol, eigen_floor)}, 2 (N-1))` (`statebridge.py:52-55`).
`[OFFLINE MEASURED]` In 197 of the 198 runs whose two clouds both have full centred row rank,
`r = 2 (N-1) = 254` while `rank(bh) = N-1 = 127` (the exception is the `MIRROR` row with
`eigen_floor = 1.0`, where that floor also becomes the `rank_rtol` threshold of
`statebridge.py:52-53` and cuts the retained rank to `127`)
(Tables 2 and 9): exactly half of the whitening coordinates are numerically null for `bh`.
Those coordinates hold `0 + lambda` in `ch`, so after `clamp_min(eigen_floor)` their gain is

```
(23)  gain = 1 / sqrt( max(lambda, eigen_floor) )        = 1000 at the shipped defaults
```

and they amplify float32 rounding of `bh` (relative size `eps32 = 1.192e-7`) by that gain:

```
(24)  floor  ~  eps32 / sqrt( max(lambda, eigen_floor) )
```

`[OFFLINE MEASURED]` Table 8, hidden scale, `lambda = 1e-8`, relative error to `E_cal`:
`1.196e-4` at floor `1e-6`, `1.517e-5` at `1e-4`, `2.768e-6` at `1e-2`, `2.341e-6` at `1.0`,
and `2.521e-8` in float64 at floor `1e-6`. The float32 points divided by the gain of (23) give
`1.20e-7`, `1.52e-7`, `2.77e-7`, i.e. `1.0x`, `1.3x`, `2.3x` `eps32`, so at hidden scale (24)
holds to within a factor 2.4, with a crossover to a ridge-limited error above floor `1e-2`.
`[OFFLINE MEASURED]` At unit scale the same formula *over*-predicts: measured/predicted is
`0.028` at floor `1e-6` and `0.194` at `1e-4` (Table 8), so there the error is not
leakage-dominated and (24) is only an upper-bound shape, not a value. The direction of the
floor's influence is the same at both scales, which is what (23) is used for below.

`[OFFLINE MEASURED]` Consequence at hidden scale (Table 3, anisotropic): the relative error to
`E_cal` is `8.4e-5` at `lambda = 1e-6`, `1.0e-5` at `1e-4`, `9.5e-6` at `1e-3`, `8.6e-5` at
`1e-2`, `8.5e-4` at `1e-1`. The shipped `lambda = 1e-3` sits at the minimum of the measured
curve: large enough to damp the null-direction gain of (23) by a factor 32 relative to the
floor, small enough that (19) stays near `1e-5`. `[HYPOTHESIS]` That is where two error sources
cross, not a designed identifiability trade-off: the ridge adds no constraint to the fit.
Equation (17) is underdetermined (`d > 2N` unknowns per column against `N` equations) for every
`lambda`, and the ridge only rescales the whitening weights of (9).

### 3.7 Rank deficiency is the one hypothesis that bites

If `rank(Hc) = k < N-1` then `U_H` in (10) is only a `k`-frame:

```
(25)  X X^T = N U_k diag(w) U_k^T   =>   ||X X^T - N P||_2 / N = 1
      Z - mu_E lies in col(Hc), a k-dimensional subspace  =>  Z != E
```

`[OFFLINE MEASURED]` Table 7: a rank-8 `H` gives `||X X^T - N P||_2 / N = 1.000`,
`cos(Z_i, E_i) = 0.281` and error `1.199`; rank 64 gives `0.717` and `0.753`; rank 126 gives
`0.994` and `0.112`. Rank deficiency of `E` instead (lower rows of Table 7) degrades the error
more gently (`4.1e-4 .. 1.5e-1`) because `Z` still reproduces `Ec` as far as `Ec` exists.
`[HYPOTHESIS]` Real captured spans can be rank deficient when tokens repeat or the span is
short; that is the only way this solver's output can depend on `H` at all.

### 3.8 Corollary 3 (when the ridge cannot matter at all)

Equation (19) shows the ridge acts only through `w`. Suppose the centred row Grams are equal,
`Hc Hc^T = Ec Ec^T` (for example `H = E Q` with `Q` orthogonal). Then `bh bh^T = be be^T`, so
the left singular vectors and all singular values agree, `w^H = w^E`, and `X X^T = Y Y^T`
exactly at every `lambda`. Theorem 2 then applies unchanged:

```
(26)  Hc Hc^T = Ec Ec^T   =>   w^H = w^E   =>   Z = E   for every lambda
```

More generally, if `w^H_i = c w^E_i` for a constant `c`, then `X X^T = c Y Y^T` and the same
polar argument gives `X R* = sqrt(c) Y`, hence `Z = sqrt(c) Ec + mu_E`: a pure scale error,
which (6) divides out per row. A common *scale* factor between `H` and `E` does not satisfy
this, because the ridge is added rather than multiplied.

```
(27)  Z - E = (sqrt(c) - 1) Ec  +  (X R* - sqrt(c) Y) Sigma_E^(1/2)
              \___ scale part __/     \_____ profile part _____/
```

`[OFFLINE MEASURED]` Table 6. With `H = E Q` at the same scale, `Z` reproduces `E_cal` at
`lambda = 1e-3, 1e-1, 1.0, 1e2` alike (cosine `1.0`, error `2.1e-6 .. 3.5e-6`, i.e. the
float32 floor of 3.6). With the `2.5x` scale control the same cell degrades monotonically with
the ridge (cosine `0.9995 -> 0.957`, error `3.0e-2 -> 2.9e-1` for `lambda = 1e-3 -> 1e2`,
anisotropic, unit scale): equal spectra with a common scale are not enough.

## 4. Reproduction

Fixtures (`probes/measure_degeneracy.py`), `D = 6144`, `N = 128` unless swept:

- `isotropic`: iid `N(0,1)` rows, each rescaled to row norm `scale` for `scale` in `{1, 64}`.
- `anisotropic` ("GLM-shaped"): decaying per-coordinate standard deviation `(i+1)^(-0.6)`
  normalised to mean 1, plus one shared unit direction of weight `sqrt(0.24)`, each row
  rescaled. Measured mean off-diagonal cosine of `E`: `0.2402` (Table 1), the target `0.24`.
- `E = W[ids]` for a 512-row vocabulary `W` from the same family with 128 distinct ids, so
  every row has a reference token; `target_norm = mean_i ||E_i||`.
- `correct_H = 2.5 (E Q) + 0.5 noise`, `Q` = 96 random Givens rotations (orthogonal, so it
  preserves the centred row Gram of `E` exactly and does not preserve its row space);
  `row_shuffled_H` = `correct_H` with rows permuted; `unrelated_random_H` = fresh isotropic
  rows rescaled to the same mean row norm; `scale_matched_random_H` = fresh isotropic rows
  rescaled to `||correct_H||_F`; `correct_H_exact_rotation = 2.5 (E Q)`.

Two solvers, and every table row states which one produced it:

- `PRODUCTION`: `occ3_sglang.statebridge.align_hidden_sequence`, imported unchanged, called
  with shipped defaults unless the row names a ridge.
- `MIRROR`: a statement-by-statement copy of `statebridge.py:48-68` (`mirror` in
  `probes/measure_degeneracy.py`) that additionally returns `bh, be, ch, ce, x, y, R*, w`.
  Parity is recorded per run: max `|mirror - production| = 0.0` over all 210 runs, so the
  internals are the production internals and not a surrogate. `MIRROR` rows appear only where
  the shipped guards refuse the cell (`lambda = 0`, `N = 256`, an altered `eigen_floor`).

Commands, all run for this report:

```
cd C:/Users/openclawuser/optimus-occ3-latent-validation
extensions/sglang-occ3/.venv/Scripts/python.exe reports/occ3-alignment-repair/probes/measure_degeneracy.py
extensions/sglang-occ3/.venv/Scripts/python.exe reports/occ3-alignment-repair/probes/build_degeneracy_report.py
cd extensions/sglang-occ3 && .venv/Scripts/python.exe -m pytest tests/test_alignment_degeneracy.py -q
```

`[OFFLINE MEASURED]` measurement run: python 3.11.15, torch 2.8.0+cpu, runtime 113.67 s,
210 runs, 12 refusals, 0 non-finite values in any output.


## 5. Tables

Every column is a measurement of `probes/measure_degeneracy.py`; `MIRROR` rows are named as
such and exist only where a shipped guard refuses the cell.

### Table 1. Fixture properties as measured (`N = 128`, seed 1)

| family | scale | E row norm | E pair cos | erank E | H row norm | H pair cos | erank H | lam_min(H) | w_min(H) |
|---|---|---|---|---|---|---|---|---|---|
| isotropic | 1 | 1.000000 | 4.5795e-05 | 125.7132 | 2.549940 | -6.5142e-06 | 125.6994 | 0.037946 | 0.974323 |
| isotropic | 64 | 64.0000 | 4.5795e-05 | 125.7132 | 163.1961 | -6.5142e-06 | 125.6994 | 155.4265 | 0.999994 |
| anisotropic | 1 | 1.000000 | 0.240174 | 122.0039 | 2.660901 | 0.302318 | 122.0292 | 0.023824 | 0.959716 |
| anisotropic | 64 | 64.0000 | 0.240174 | 122.0039 | 170.2976 | 0.302318 | 122.0292 | 97.5815 | 0.999990 |

`pair cos` is the mean off-diagonal cosine of the row cloud; `erank` is
`exp(-sum p_i log p_i)` over the centred Gram spectrum; `w_min` is the smallest whitening
weight at the shipped ridge, i.e. `1 - w_min` is the deviation entering (20).

### Table 2. Main matrix: five H variants against one E (3 seeds, `lambda = 1e-3`)

Means over seeds 1-3; `cos min` is the worst row cosine over the three runs. `rel change vs
random H` is `||Z(H) - Z(unrelated)||_F / ||Z(unrelated)||_F`; for the `unrelated` row itself
the reference is `correct_H`.

| family | scale | H variant | cos mean | cos min | recon mean | recon max | rel change vs random H | retrieval | union rank | 1-w_min(H) | gram dev xy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| isotropic | 1 | correct_H | 0.999968 | 0.999956 | 0.008046 | 0.008098 | 0.009497 | 1.000000 | 254 | 0.025712 | 0.120816 |
| isotropic | 1 | unrelated_random_H | 0.999933 | 0.999907 | 0.011564 | 0.011599 | 0 | 1.000000 | 254 | 0.148388 | 0.040958 |
| isotropic | 1 | row_shuffled_H | 0.999957 | 0.999941 | 0.009274 | 0.009320 | 0.009499 | 1.000000 | 254 | 0.025712 | 0.126864 |
| isotropic | 1 | scale_matched_random_H | 0.999957 | 0.999941 | 0.009275 | 0.009344 | 0.009501 | 1.000000 | 254 | 0.025932 | 0.126661 |
| isotropic | 1 | correct_H_exact_rotation | 0.999969 | 0.999957 | 0.007931 | 0.007980 | 0.009492 | 1.000000 | 254 | 0.026660 | 0.119508 |
| isotropic | 64 | correct_H | 1.000000 | 1.000000 | 5.6476e-06 | 5.7147e-06 | 2.3660e-05 | 1.000000 | 254 | 6.4430e-06 | 6.0133e-05 |
| isotropic | 64 | unrelated_random_H | 1.000000 | 1.000000 | 2.3122e-05 | 2.3331e-05 | 0 | 1.000000 | 254 | 4.2539e-05 | 1.2015e-04 |
| isotropic | 64 | row_shuffled_H | 1.000000 | 1.000000 | 5.7453e-06 | 5.7775e-06 | 2.3666e-05 | 1.000000 | 254 | 6.4430e-06 | 6.0983e-05 |
| isotropic | 64 | scale_matched_random_H | 1.000000 | 1.000000 | 3.7106e-06 | 3.7372e-06 | 2.3221e-05 | 1.000000 | 254 | 6.4996e-06 | 4.3212e-05 |
| isotropic | 64 | correct_H_exact_rotation | 1.000000 | 1.000000 | 4.8273e-06 | 4.8829e-06 | 2.3455e-05 | 1.000000 | 254 | 6.6871e-06 | 5.3412e-05 |
| anisotropic | 1 | correct_H | 0.999520 | 0.999220 | 0.030981 | 0.031162 | 0.021848 | 1.000000 | 254 | 0.040029 | 0.174334 |
| anisotropic | 1 | unrelated_random_H | 0.999807 | 0.999428 | 0.019663 | 0.019903 | 0 | 1.000000 | 254 | 0.148388 | 0.103898 |
| anisotropic | 1 | row_shuffled_H | 0.999481 | 0.999040 | 0.032209 | 0.032422 | 0.021554 | 1.000000 | 254 | 0.040029 | 0.187787 |
| anisotropic | 1 | scale_matched_random_H | 0.999430 | 0.999001 | 0.033755 | 0.033956 | 0.023186 | 1.000000 | 254 | 0.023852 | 0.195637 |
| anisotropic | 1 | correct_H_exact_rotation | 0.999529 | 0.999240 | 0.030686 | 0.030862 | 0.021678 | 1.000000 | 254 | 0.041677 | 0.172037 |
| anisotropic | 64 | correct_H | 1.000000 | 1.000000 | 9.5965e-06 | 9.6813e-06 | 1.6393e-05 | 1.000000 | 254 | 1.0180e-05 | 7.3343e-05 |
| anisotropic | 64 | unrelated_random_H | 1.000000 | 1.000000 | 1.5824e-05 | 1.5882e-05 | 0 | 1.000000 | 254 | 4.2539e-05 | 9.7863e-05 |
| anisotropic | 64 | row_shuffled_H | 1.000000 | 1.000000 | 9.8735e-06 | 9.9557e-06 | 1.6336e-05 | 1.000000 | 254 | 1.0180e-05 | 7.1014e-05 |
| anisotropic | 64 | scale_matched_random_H | 1.000000 | 1.000000 | 9.6558e-06 | 9.7241e-06 | 1.6169e-05 | 1.000000 | 254 | 5.9654e-06 | 6.2976e-05 |
| anisotropic | 64 | correct_H_exact_rotation | 1.000000 | 1.000000 | 9.4223e-06 | 9.5225e-06 | 1.6309e-05 | 1.000000 | 254 | 1.0617e-05 | 6.7417e-05 |

### Table 3. Ridge sweep with the shipped guards (PRODUCTION rows only)

`1 - w_min(H)` is `max_i (1 - w_i^H)` from (19); `lam/lam_min` is the bound of (20);
`gram dev x` is the measured `||X X^T - N P||_2 / N`; `gram dev xy` is `||X X^T - Y Y^T||_2 / N`
against the sum bound of (21).

| family | scale | lambda | H variant | cos mean | cos median | recon | rel change vs random H | gram dev x | 1-w_min(H) | lam/lam_min | procrustes resid | gram dev xy |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| isotropic | 1 | 1.0000e-06 | correct_H | 1.000000 | 1.000000 | 9.5958e-06 | 1.5682e-05 | 2.8286e-05 | 2.6353e-05 | 2.6353e-05 | 5.6352e-05 | 1.4793e-04 |
| isotropic | 1 | 1.0000e-06 | unrelated_random_H | 1.000000 | 1.000000 | 1.7066e-05 | 0 | 1.8943e-04 | 1.7578e-04 | 1.7581e-04 | 1.9429e-05 | 7.7870e-05 |
| isotropic | 1 | 1.0000e-06 | correct_H_exact_rotation | 1.000000 | 1.000000 | 9.3300e-06 | 1.5692e-05 | 2.8927e-05 | 2.7291e-05 | 2.7291e-05 | 5.5992e-05 | 1.4684e-04 |
| isotropic | 1 | 1.0000e-04 | correct_H | 1.000000 | 1.000000 | 9.0466e-04 | 0.001056 | 0.002629 | 0.002628 | 0.002635 | 0.005557 | 0.014188 |
| isotropic | 1 | 1.0000e-04 | unrelated_random_H | 0.999999 | 0.999999 | 0.001293 | 0 | 0.017276 | 0.017277 | 0.017581 | 0.001317 | 0.005248 |
| isotropic | 1 | 1.0000e-04 | correct_H_exact_rotation | 1.000000 | 1.000000 | 8.9320e-04 | 0.001055 | 0.002721 | 0.002722 | 0.002729 | 0.005515 | 0.014050 |
| isotropic | 1 | 0.001000 | correct_H | 0.999967 | 0.999967 | 0.008067 | 0.009501 | 0.025676 | 0.025677 | 0.026353 | 0.053135 | 0.120494 |
| isotropic | 1 | 0.001000 | unrelated_random_H | 0.999933 | 0.999933 | 0.011571 | 0 | 0.149522 | 0.149522 | 0.175809 | 0.011765 | 0.041731 |
| isotropic | 1 | 0.001000 | correct_H_exact_rotation | 0.999968 | 0.999968 | 0.007955 | 0.009493 | 0.026567 | 0.026566 | 0.027291 | 0.052706 | 0.119150 |
| isotropic | 1 | 0.010000 | correct_H | 0.999290 | 0.999287 | 0.037682 | 0.048670 | 0.208569 | 0.208568 | 0.263533 | 0.383242 | 0.427935 |
| isotropic | 1 | 0.010000 | unrelated_random_H | 0.998398 | 0.998395 | 0.056610 | 0 | 0.637430 | 0.637431 | 1.758093 | 0.057228 | 0.098244 |
| isotropic | 1 | 0.010000 | correct_H_exact_rotation | 0.999323 | 0.999320 | 0.036801 | 0.048645 | 0.214400 | 0.214401 | 0.272914 | 0.378553 | 0.416012 |
| isotropic | 1 | 0.100000 | correct_H | 0.998584 | 0.998581 | 0.053220 | 0.094102 | 0.724921 | 0.724922 | 2.635329 | 1.153211 | 0.307980 |
| isotropic | 1 | 0.100000 | unrelated_random_H | 0.995676 | 0.995662 | 0.092997 | 0 | 0.946181 | 0.946181 | 17.5809 | 0.093857 | 0.026709 |
| isotropic | 1 | 0.100000 | correct_H_exact_rotation | 0.998737 | 0.998735 | 0.050266 | 0.094030 | 0.731842 | 0.731842 | 2.729138 | 1.124814 | 0.295711 |
| isotropic | 64 | 1.0000e-06 | correct_H | 1.000000 | 1.000000 | 1.1717e-04 | 6.2656e-04 | 3.5028e-04 | 6.4339e-09 | 6.4339e-09 | 1.9393e-04 | 9.6707e-04 |
| isotropic | 64 | 1.0000e-06 | unrelated_random_H | 1.000000 | 1.000000 | 6.1630e-04 | 0 | 0.002444 | 4.2922e-08 | 4.2922e-08 | 7.9204e-04 | 0.003111 |
| isotropic | 64 | 1.0000e-06 | correct_H_exact_rotation | 1.000000 | 1.000000 | 8.6248e-05 | 6.2165e-04 | 1.3846e-04 | 6.6629e-09 | 6.6629e-09 | 1.4431e-04 | 7.8493e-04 |
| isotropic | 64 | 1.0000e-04 | correct_H | 1.000000 | 1.000000 | 1.4315e-05 | 7.4963e-05 | 1.0181e-05 | 6.4339e-07 | 6.4339e-07 | 2.4192e-05 | 1.2292e-04 |
| isotropic | 64 | 1.0000e-04 | unrelated_random_H | 1.000000 | 1.000000 | 7.3592e-05 | 0 | 2.5836e-04 | 4.2922e-06 | 4.2922e-06 | 9.3477e-05 | 3.6972e-04 |
| isotropic | 64 | 1.0000e-04 | correct_H_exact_rotation | 1.000000 | 1.000000 | 1.5550e-05 | 7.5195e-05 | 2.0012e-04 | 6.6629e-07 | 6.6629e-07 | 2.1854e-05 | 2.1487e-04 |
| isotropic | 64 | 0.001000 | correct_H | 1.000000 | 1.000000 | 5.5204e-06 | 2.3523e-05 | 1.0355e-05 | 6.4339e-06 | 6.4339e-06 | 1.5863e-05 | 5.8761e-05 |
| isotropic | 64 | 0.001000 | unrelated_random_H | 1.000000 | 1.000000 | 2.3067e-05 | 0 | 1.1383e-04 | 4.2920e-05 | 4.2922e-05 | 2.8883e-05 | 1.1792e-04 |
| isotropic | 64 | 0.001000 | correct_H_exact_rotation | 1.000000 | 1.000000 | 4.7449e-06 | 2.3385e-05 | 9.8505e-06 | 6.6629e-06 | 6.6629e-06 | 1.5372e-05 | 5.3793e-05 |
| isotropic | 64 | 0.010000 | correct_H | 1.000000 | 1.000000 | 2.2542e-05 | 2.7404e-05 | 6.5454e-05 | 6.4335e-05 | 6.4339e-05 | 1.3661e-04 | 3.5257e-04 |
| isotropic | 64 | 0.010000 | unrelated_random_H | 1.000000 | 1.000000 | 3.2949e-05 | 0 | 4.2920e-04 | 4.2904e-04 | 4.2922e-04 | 3.4051e-05 | 1.3469e-04 |
| isotropic | 64 | 0.010000 | correct_H_exact_rotation | 1.000000 | 1.000000 | 2.2026e-05 | 2.7434e-05 | 6.7170e-05 | 6.6625e-05 | 6.6629e-05 | 1.3617e-04 | 3.5053e-04 |
| isotropic | 64 | 0.100000 | correct_H | 1.000000 | 1.000000 | 2.2315e-04 | 2.6021e-04 | 6.4228e-04 | 6.4298e-04 | 6.4339e-04 | 0.001362 | 0.003516 |
| isotropic | 64 | 0.100000 | unrelated_random_H | 1.000000 | 1.000000 | 3.1878e-04 | 0 | 0.004275 | 0.004274 | 0.004292 | 3.2485e-04 | 0.001311 |
| isotropic | 64 | 0.100000 | correct_H_exact_rotation | 1.000000 | 1.000000 | 2.2027e-04 | 2.6007e-04 | 6.6618e-04 | 6.6585e-04 | 6.6629e-04 | 0.001352 | 0.003482 |
| anisotropic | 1 | 1.0000e-06 | correct_H | 1.000000 | 1.000000 | 3.5291e-05 | 2.5339e-05 | 4.2905e-05 | 4.1973e-05 | 4.1975e-05 | 7.9121e-05 | 2.3359e-04 |
| anisotropic | 1 | 1.0000e-06 | unrelated_random_H | 1.000000 | 1.000000 | 2.3806e-05 | 0 | 1.8793e-04 | 1.7578e-04 | 1.7581e-04 | 3.6177e-05 | 1.5479e-04 |
| anisotropic | 1 | 1.0000e-06 | correct_H_exact_rotation | 1.000000 | 1.000000 | 3.4893e-05 | 2.5115e-05 | 4.4433e-05 | 4.4052e-05 | 4.4054e-05 | 7.8748e-05 | 2.3293e-04 |
| anisotropic | 1 | 1.0000e-04 | correct_H | 0.999994 | 0.999994 | 0.003451 | 0.002387 | 0.004180 | 0.004180 | 0.004198 | 0.007802 | 0.022671 |
| anisotropic | 1 | 1.0000e-04 | unrelated_random_H | 0.999998 | 0.999998 | 0.002223 | 0 | 0.017277 | 0.017277 | 0.017581 | 0.003383 | 0.014451 |
| anisotropic | 1 | 1.0000e-04 | correct_H_exact_rotation | 0.999994 | 0.999994 | 0.003420 | 0.002369 | 0.004386 | 0.004386 | 0.004405 | 0.007740 | 0.022411 |
| anisotropic | 1 | 0.001000 | correct_H | 0.999522 | 0.999531 | 0.030906 | 0.021808 | 0.040285 | 0.040284 | 0.041975 | 0.072849 | 0.176163 |
| anisotropic | 1 | 0.001000 | unrelated_random_H | 0.999810 | 0.999818 | 0.019496 | 0 | 0.149521 | 0.149522 | 0.175809 | 0.029517 | 0.105498 |
| anisotropic | 1 | 0.001000 | correct_H_exact_rotation | 0.999532 | 0.999540 | 0.030610 | 0.021632 | 0.042195 | 0.042195 | 0.044054 | 0.072213 | 0.173699 |
| anisotropic | 1 | 0.010000 | correct_H | 0.988058 | 0.988178 | 0.154543 | 0.119800 | 0.295652 | 0.295651 | 0.419752 | 0.470046 | 0.453212 |
| anisotropic | 1 | 0.010000 | unrelated_random_H | 0.996099 | 0.996197 | 0.088329 | 0 | 0.637431 | 0.637431 | 1.758093 | 0.132381 | 0.193799 |
| anisotropic | 1 | 0.010000 | correct_H_exact_rotation | 0.988372 | 0.988504 | 0.152500 | 0.118648 | 0.305817 | 0.305818 | 0.440544 | 0.463408 | 0.428565 |
| anisotropic | 1 | 0.100000 | correct_H | 0.963749 | 0.963780 | 0.269262 | 0.238212 | 0.807600 | 0.807600 | 4.197517 | 1.218427 | 0.373186 |
| anisotropic | 1 | 0.100000 | unrelated_random_H | 0.990488 | 0.990798 | 0.137930 | 0 | 0.946181 | 0.946181 | 17.5809 | 0.207257 | 0.063484 |
| anisotropic | 1 | 0.100000 | correct_H_exact_rotation | 0.965078 | 0.965133 | 0.264281 | 0.235513 | 0.815001 | 0.815001 | 4.405437 | 1.186235 | 0.360720 |
| anisotropic | 64 | 1.0000e-06 | correct_H | 1.000000 | 1.000000 | 8.4419e-05 | 3.9909e-04 | 3.3916e-04 | 1.0248e-08 | 1.0248e-08 | 1.6717e-04 | 9.8342e-04 |
| anisotropic | 64 | 1.0000e-06 | unrelated_random_H | 1.000000 | 1.000000 | 3.9089e-04 | 0 | 0.001711 | 4.2922e-08 | 4.2922e-08 | 5.7640e-04 | 0.002656 |
| anisotropic | 64 | 1.0000e-06 | correct_H_exact_rotation | 1.000000 | 1.000000 | 6.0293e-05 | 3.9500e-04 | 1.8356e-04 | 1.0755e-08 | 1.0755e-08 | 1.1652e-04 | 6.1929e-04 |
| anisotropic | 64 | 1.0000e-04 | correct_H | 1.000000 | 1.000000 | 1.0479e-05 | 4.9910e-05 | 9.2012e-06 | 1.0248e-06 | 1.0248e-06 | 2.1172e-05 | 8.2723e-05 |
| anisotropic | 64 | 1.0000e-04 | unrelated_random_H | 1.000000 | 1.000000 | 4.8754e-05 | 0 | 2.1951e-04 | 4.2922e-06 | 4.2922e-06 | 6.7670e-05 | 2.8465e-04 |
| anisotropic | 64 | 1.0000e-04 | correct_H_exact_rotation | 1.000000 | 1.000000 | 8.1845e-06 | 4.9494e-05 | 6.3602e-06 | 1.0755e-06 | 1.0755e-06 | 1.5109e-05 | 8.2158e-05 |
| anisotropic | 64 | 0.001000 | correct_H | 1.000000 | 1.000000 | 9.5104e-06 | 1.6522e-05 | 1.2509e-05 | 1.0248e-05 | 1.0248e-05 | 2.1120e-05 | 6.7939e-05 |
| anisotropic | 64 | 0.001000 | unrelated_random_H | 1.000000 | 1.000000 | 1.5882e-05 | 0 | 9.8270e-05 | 4.2920e-05 | 4.2922e-05 | 2.2535e-05 | 1.0051e-04 |
| anisotropic | 64 | 0.001000 | correct_H_exact_rotation | 1.000000 | 1.000000 | 9.3133e-06 | 1.6467e-05 | 1.3080e-05 | 1.0755e-05 | 1.0755e-05 | 2.0258e-05 | 6.2607e-05 |
| anisotropic | 64 | 0.010000 | correct_H | 1.000000 | 1.000000 | 8.5607e-05 | 5.9422e-05 | 1.0274e-04 | 1.0247e-04 | 1.0248e-04 | 1.9290e-04 | 5.7055e-04 |
| anisotropic | 64 | 0.010000 | unrelated_random_H | 1.000000 | 1.000000 | 5.5403e-05 | 0 | 4.3281e-04 | 4.2904e-04 | 4.2922e-04 | 8.4347e-05 | 3.6517e-04 |
| anisotropic | 64 | 0.010000 | correct_H_exact_rotation | 1.000000 | 1.000000 | 8.4882e-05 | 5.9006e-05 | 1.0734e-04 | 1.0754e-04 | 1.0755e-04 | 1.9107e-04 | 5.6610e-04 |
| anisotropic | 64 | 0.100000 | correct_H | 1.000000 | 1.000000 | 8.5098e-04 | 5.8733e-04 | 0.001024 | 0.001024 | 0.001025 | 0.001917 | 0.005666 |
| anisotropic | 64 | 0.100000 | unrelated_random_H | 1.000000 | 1.000000 | 5.4926e-04 | 0 | 0.004273 | 0.004274 | 0.004292 | 8.3650e-04 | 0.003633 |
| anisotropic | 64 | 0.100000 | correct_H_exact_rotation | 1.000000 | 1.000000 | 8.4351e-04 | 5.8294e-04 | 0.001073 | 0.001074 | 0.001076 | 0.001902 | 0.005605 |

### Table 4. Refusals observed: the cells the shipped guards reject

`lambda = 0` and `N = 256` are refused by `statebridge.py:43`
(`require(2 <= n <= 128 and d > 2 * n and ridge > 0 ...)`), so both are limit statements and
are measured through a guard-relaxed `MIRROR` (Tables 5 and 9).

| axis | family | scale | N | lambda | H variant | error code |
|---|---|---|---|---|---|---|
| B_n_sweep | anisotropic | 1 | 256 | 0.001000 | correct_H | profile |
| B_n_sweep | anisotropic | 1 | 256 | 0.001000 | unrelated_random_H | profile |
| B_n_sweep | anisotropic | 64 | 256 | 0.001000 | correct_H | profile |
| B_n_sweep | anisotropic | 64 | 256 | 0.001000 | unrelated_random_H | profile |
| C_ridge_sweep | isotropic | 1 | 128 | 0 | correct_H | profile |
| C_ridge_sweep | isotropic | 1 | 128 | 0 | unrelated_random_H | profile |
| C_ridge_sweep | isotropic | 64 | 128 | 0 | correct_H | profile |
| C_ridge_sweep | isotropic | 64 | 128 | 0 | unrelated_random_H | profile |
| C_ridge_sweep | anisotropic | 1 | 128 | 0 | correct_H | profile |
| C_ridge_sweep | anisotropic | 1 | 128 | 0 | unrelated_random_H | profile |
| C_ridge_sweep | anisotropic | 64 | 128 | 0 | correct_H | profile |
| C_ridge_sweep | anisotropic | 64 | 128 | 0 | unrelated_random_H | profile |

### Table 5. Capture-window sweep `N` in {16, 32, 64, 128, 256} (anisotropic, seed 1)

`N = 256` exceeds the shipped guard, so its rows are `MIRROR`; they were produced without the
pairwise change metric, which is why that column is `-` there.

| scale | N | mode | H variant | cos mean | recon | rel change vs random H | procrustes resid | union rank | rank(bh) | lam_min(H) | 1-w_min(H) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16 | PRODUCTION | correct_H | 0.999992 | 0.004010 | 0.002875 | 0.008912 | 30 | 15 | 0.268358 | 0.003713 |
| 1 | 16 | PRODUCTION | unrelated_random_H | 0.999999 | 0.001333 | 0 | 0.002671 | 30 | 15 | 0.057864 | 0.016988 |
| 1 | 32 | PRODUCTION | correct_H | 0.999969 | 0.007880 | 0.005611 | 0.017987 | 62 | 31 | 0.120517 | 0.008229 |
| 1 | 32 | PRODUCTION | unrelated_random_H | 0.999995 | 0.003269 | 0 | 0.005936 | 62 | 31 | 0.026980 | 0.035739 |
| 1 | 64 | PRODUCTION | correct_H | 0.999875 | 0.015809 | 0.011112 | 0.036514 | 126 | 63 | 0.054805 | 0.017919 |
| 1 | 64 | PRODUCTION | unrelated_random_H | 0.999967 | 0.008104 | 0 | 0.013490 | 126 | 63 | 0.012678 | 0.073108 |
| 1 | 128 | PRODUCTION | correct_H | 0.999522 | 0.030906 | 0.021808 | 0.072849 | 254 | 127 | 0.023824 | 0.040284 |
| 1 | 128 | PRODUCTION | unrelated_random_H | 0.999810 | 0.019496 | 0 | 0.029517 | 254 | 127 | 0.005688 | 0.149522 |
| 1 | 256 | MIRROR | correct_H | 0.998245 | 0.059251 | - | 0.143030 | 510 | 255 | 0.009993 | 0.090964 |
| 1 | 256 | MIRROR | unrelated_random_H | 0.998966 | 0.045474 | - | 0.064367 | 510 | 255 | 0.002507 | 0.285128 |
| 64 | 16 | PRODUCTION | correct_H | 1.000000 | 3.8675e-06 | 6.7302e-06 | 1.3376e-05 | 30 | 15 | 1099.1955 | 9.0976e-07 |
| 64 | 16 | PRODUCTION | unrelated_random_H | 1.000000 | 5.6942e-06 | 0 | 1.1640e-05 | 30 | 15 | 237.0091 | 4.2192e-06 |
| 64 | 32 | PRODUCTION | correct_H | 1.000000 | 3.6400e-06 | 1.6005e-05 | 1.3012e-05 | 62 | 31 | 493.6391 | 2.0258e-06 |
| 64 | 32 | PRODUCTION | unrelated_random_H | 1.000000 | 1.5718e-05 | 0 | 2.3598e-05 | 62 | 31 | 110.5119 | 9.0487e-06 |
| 64 | 64 | PRODUCTION | correct_H | 1.000000 | 5.2674e-06 | 1.9367e-05 | 1.3377e-05 | 126 | 63 | 224.4820 | 4.4547e-06 |
| 64 | 64 | PRODUCTION | unrelated_random_H | 1.000000 | 1.9049e-05 | 0 | 2.8611e-05 | 126 | 63 | 51.9309 | 1.9256e-05 |
| 64 | 128 | PRODUCTION | correct_H | 1.000000 | 9.5104e-06 | 1.6522e-05 | 2.1120e-05 | 254 | 127 | 97.5815 | 1.0248e-05 |
| 64 | 128 | PRODUCTION | unrelated_random_H | 1.000000 | 1.5882e-05 | 0 | 2.2535e-05 | 254 | 127 | 23.2980 | 4.2920e-05 |
| 64 | 256 | MIRROR | correct_H | 1.000000 | 1.9126e-05 | - | 4.1947e-05 | 510 | 255 | 40.9329 | 2.4430e-05 |
| 64 | 256 | MIRROR | unrelated_random_H | 1.000000 | 2.0800e-05 | - | 3.0091e-05 | 510 | 255 | 10.2695 | 9.7366e-05 |

### Table 6. Isometric `H` (equal centred row Gram) against the 2.5x scale control

`isometric_H_same_gram` is `H = E Q` with `Q` a product of 96 Givens rotations;
`isometric_H_2.5x_scale` is the same H at 2.5x row norm.

| family | scale | H variant | lambda | cos mean | recon | union rank | rank(bh) | gram dev xy | 1-w_min(H) |
|---|---|---|---|---|---|---|---|---|---|
| isotropic | 1 | isometric_H_same_gram | 0.001000 | 1.000000 | 2.4884e-06 | 254 | 127 | 6.4026e-06 | 0.147352 |
| isotropic | 1 | isometric_H_same_gram | 0.100000 | 1.000000 | 2.4905e-06 | 254 | 127 | 5.2979e-07 | 0.945301 |
| isotropic | 1 | isometric_H_same_gram | 1.000000 | 1.000000 | 2.4701e-06 | 254 | 127 | 5.6918e-08 | 0.994247 |
| isotropic | 1 | isometric_H_same_gram | 100.0000 | 1.000000 | 2.4552e-06 | 254 | 127 | 4.9349e-10 | 0.999942 |
| isotropic | 1 | isometric_H_2.5x_scale | 0.001000 | 0.999968 | 0.008036 | 254 | 127 | 0.120447 | 0.026907 |
| isotropic | 1 | isometric_H_2.5x_scale | 0.100000 | 0.998724 | 0.050524 | 254 | 127 | 0.295607 | 0.734402 |
| isotropic | 1 | isometric_H_2.5x_scale | 1.000000 | 0.998609 | 0.052752 | 254 | 127 | 0.049488 | 0.965097 |
| isotropic | 1 | isometric_H_2.5x_scale | 100.0000 | 0.998574 | 0.053404 | 254 | 127 | 5.3113e-04 | 0.999638 |
| anisotropic | 1 | isometric_H_same_gram | 0.001000 | 1.000000 | 2.1657e-06 | 254 | 127 | 6.3004e-06 | 0.210571 |
| anisotropic | 1 | isometric_H_same_gram | 0.100000 | 1.000000 | 2.2308e-06 | 254 | 127 | 4.6438e-07 | 0.963865 |
| anisotropic | 1 | isometric_H_same_gram | 1.000000 | 1.000000 | 2.1477e-06 | 254 | 127 | 5.4667e-08 | 0.996265 |
| anisotropic | 1 | isometric_H_same_gram | 100.0000 | 1.000000 | 2.1155e-06 | 254 | 127 | 4.1998e-10 | 0.999963 |
| anisotropic | 1 | isometric_H_2.5x_scale | 0.001000 | 0.999537 | 0.030434 | 254 | 127 | 0.169641 | 0.040931 |
| anisotropic | 1 | isometric_H_2.5x_scale | 0.100000 | 0.965336 | 0.263302 | 254 | 127 | 0.346223 | 0.810168 |
| anisotropic | 1 | isometric_H_2.5x_scale | 1.000000 | 0.958306 | 0.288770 | 254 | 127 | 0.067675 | 0.977105 |
| anisotropic | 1 | isometric_H_2.5x_scale | 100.0000 | 0.957296 | 0.292247 | 254 | 127 | 7.4669e-04 | 0.999766 |
| isotropic | 64 | isometric_H_same_gram | 0.001000 | 1.000000 | 3.4746e-06 | 254 | 127 | 1.4799e-05 | 4.2190e-05 |
| isotropic | 64 | isometric_H_same_gram | 0.100000 | 1.000000 | 2.4722e-06 | 254 | 127 | 7.0021e-06 | 0.004201 |
| isotropic | 64 | isometric_H_same_gram | 1.000000 | 1.000000 | 2.5503e-06 | 254 | 127 | 6.5535e-06 | 0.040484 |
| isotropic | 64 | isometric_H_same_gram | 100.0000 | 1.000000 | 2.4918e-06 | 254 | 127 | 2.0568e-06 | 0.808399 |
| isotropic | 64 | isometric_H_2.5x_scale | 0.001000 | 1.000000 | 5.0714e-06 | 254 | 127 | 5.1056e-05 | 6.7506e-06 |
| isotropic | 64 | isometric_H_2.5x_scale | 0.100000 | 1.000000 | 2.2242e-04 | 254 | 127 | 0.003526 | 6.7461e-04 |
| isotropic | 64 | isometric_H_2.5x_scale | 1.000000 | 0.999998 | 0.002161 | 254 | 127 | 0.033776 | 0.006705 |
| isotropic | 64 | isometric_H_2.5x_scale | 100.0000 | 0.998918 | 0.046512 | 254 | 127 | 0.428572 | 0.403010 |
| anisotropic | 64 | isometric_H_same_gram | 0.001000 | 1.000000 | 3.3143e-06 | 254 | 127 | 2.6214e-05 | 6.5117e-05 |
| anisotropic | 64 | isometric_H_same_gram | 0.100000 | 1.000000 | 2.2042e-06 | 254 | 127 | 6.9393e-06 | 0.006470 |
| anisotropic | 64 | isometric_H_same_gram | 1.000000 | 1.000000 | 2.1381e-06 | 254 | 127 | 7.6642e-06 | 0.061140 |
| anisotropic | 64 | isometric_H_same_gram | 100.0000 | 1.000000 | 2.2027e-06 | 254 | 127 | 1.7486e-06 | 0.866883 |
| anisotropic | 64 | isometric_H_2.5x_scale | 0.001000 | 1.000000 | 9.5009e-06 | 254 | 127 | 6.6522e-05 | 1.0419e-05 |
| anisotropic | 64 | isometric_H_2.5x_scale | 0.100000 | 1.000000 | 8.3838e-04 | 254 | 127 | 0.005429 | 0.001041 |
| anisotropic | 64 | isometric_H_2.5x_scale | 1.000000 | 0.999967 | 0.008146 | 254 | 127 | 0.050828 | 0.010312 |
| anisotropic | 64 | isometric_H_2.5x_scale | 100.0000 | 0.978290 | 0.208375 | 254 | 127 | 0.428557 | 0.510271 |

### Table 7. Rank-deficiency control (seed 5, `lambda = 1e-3`)

Rows `H_rank_k` replace `H` with an exact rank-k product; rows `E_rank_k` replace `E` with a
rank-k projection (those rows have no paired token, so their retrieval column is not
comparable and is omitted).

| scale | replacement | cos mean | recon | raw rank Hc | raw rank Ec | union rank | rank(bh) | gram dev x | procrustes resid | retrieval |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | H_rank_8 | 0.258776 | 1.217558 | 8 | 127 | 135 | 8 | 1.000000 | 0.968130 | 0.531250 |
| 1 | E_rank_8 | 0.989034 | 0.148092 | 127 | 8 | 135 | 127 | 0.025793 | 3.820708 | - |
| 1 | H_rank_64 | 0.710727 | 0.760623 | 64 | 127 | 191 | 64 | 1.000000 | 0.705780 | 1.000000 |
| 1 | E_rank_64 | 0.999501 | 0.031607 | 127 | 64 | 191 | 127 | 0.025797 | 0.983388 | - |
| 1 | H_rank_126 | 0.996031 | 0.089090 | 126 | 127 | 253 | 126 | 1.000000 | 0.108782 | 1.000000 |
| 1 | E_rank_126 | 0.999987 | 0.005110 | 127 | 126 | 253 | 127 | 0.025794 | 0.135248 | - |
| 64 | H_rank_8 | 0.281242 | 1.198964 | 8 | 127 | 135 | 8 | 1.000000 | 0.961602 | 0.703125 |
| 64 | E_rank_8 | 0.999994 | 0.003596 | 127 | 8 | 135 | 127 | 2.1201e-05 | 3.856706 | - |
| 64 | H_rank_64 | 0.716601 | 0.752860 | 64 | 127 | 191 | 64 | 1.000000 | 0.698435 | 1.000000 |
| 64 | E_rank_64 | 0.999939 | 0.011049 | 127 | 64 | 191 | 127 | 6.3543e-05 | 0.991980 | - |
| 64 | H_rank_126 | 0.993738 | 0.111910 | 126 | 127 | 253 | 126 | 2.257857 | 0.113524 | 1.000000 |
| 64 | E_rank_126 | 1.000000 | 4.1213e-04 | 127 | 126 | 253 | 127 | 9.1284e-05 | 0.089121 | - |

### Table 8. Float32 floor against `eigen_floor` (MIRROR, `lambda = 1e-8`, seed 3)

`predicted` is `eps32 / sqrt(max(lambda, eigen_floor))` from (24) with `eps32 = 1.192e-7`;
`measured/predicted` is the reported ratio. The ratio is near `1` only at hidden scale
(`scale = 64`); at unit scale it is `0.028` and `0.194`, so (24) is not scale-free. The
`fp32_floor_1e-2` and `fp32_floor_1e0` unit-scale rows are not floor-limited at all: the floor
there exceeds `lambda_min(H) = 0.024` and clips real directions, which is why their error rises
to `7.2e-2` and `2.8e-1`.

| scale | setting | dtype | lambda | eigen_floor | cos mean | recon | procrustes resid | predicted | measured/predicted |
|---|---|---|---|---|---|---|---|---|---|
| 1 | fp32_floor_1e-6 | float32 | 1.0000e-08 | 1.0000e-06 | 1.000000 | 3.2867e-06 | 4.1611e-06 | 1.1921e-04 | 0.027571 |
| 1 | fp32_floor_1e-4 | float32 | 1.0000e-08 | 1.0000e-04 | 1.000000 | 2.3089e-06 | 1.9908e-06 | 1.1921e-05 | 0.193687 |
| 1 | fp32_floor_1e-2 | float32 | 1.0000e-08 | 0.010000 | 0.997407 | 0.072011 | 0.151911 | 1.1921e-06 | 6.0407e+04 |
| 1 | fp32_floor_1e0 | float32 | 1.0000e-08 | 1.000000 | 0.960013 | 0.282796 | 1.649314 | 1.1921e-07 | 2.3723e+06 |
| 1 | fp64_floor_1e-6 | float64 | 1.0000e-08 | 1.0000e-06 | 1.000000 | 9.5421e-08 | 5.5872e-07 | 1.1921e-04 | 8.0045e-04 |
| 64 | fp32_floor_1e-6 | float32 | 1.0000e-08 | 1.0000e-06 | 1.000000 | 1.1964e-04 | 1.9886e-04 | 1.1921e-04 | 1.003638 |
| 64 | fp32_floor_1e-4 | float32 | 1.0000e-08 | 1.0000e-04 | 1.000000 | 1.5173e-05 | 2.2946e-05 | 1.1921e-05 | 1.272834 |
| 64 | fp32_floor_1e-2 | float32 | 1.0000e-08 | 0.010000 | 1.000000 | 2.7682e-06 | 2.9319e-06 | 1.1921e-06 | 2.322135 |
| 64 | fp32_floor_1e0 | float32 | 1.0000e-08 | 1.000000 | 1.000000 | 2.3407e-06 | 1.8204e-06 | 1.1921e-07 | 19.6349 |
| 64 | fp64_floor_1e-6 | float64 | 1.0000e-08 | 1.0000e-06 | 1.000000 | 2.5207e-08 | 1.3639e-10 | 1.1921e-04 | 2.1145e-04 |

### Table 9. The `lambda -> 0` limit rows and the union singular spectrum

Upper block: `MIRROR` rows at `lambda = 0` and `lambda = 1e-8` (seed 1). Lower block: singular
values of the stacked centred pair `[Hc; Ec]` at the indices that matter for the retained rank,
against the `rank_rtol = 1e-5` threshold of `statebridge.py:52-53`; `s253/s0` shows that the
retained rank `2(N-1)` is real data structure, not float32 noise.

| family | scale | lambda | mode | H variant | cos mean | recon | rel change vs correct H | gram dev x | gram dev xy | procrustes resid | null dirs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| isotropic | 1 | 0 | MIRROR | correct_H | 1.000000 | 3.2729e-06 | 0 | 4.9710e-06 | 1.8511e-05 | 4.0830e-06 | 127 |
| isotropic | 1 | 0 | MIRROR | unrelated_random_H | 1.000000 | 1.1335e-05 | 1.1755e-05 | 4.1237e-05 | 5.6661e-05 | 1.4095e-05 | 127 |
| isotropic | 1 | 1.0000e-08 | MIRROR | correct_H | 1.000000 | 3.1960e-06 | 0 | 5.3065e-06 | 1.6729e-05 | 3.9712e-06 | 127 |
| isotropic | 1 | 1.0000e-08 | MIRROR | unrelated_random_H | 1.000000 | 1.1497e-05 | 1.1924e-05 | 4.1838e-05 | 5.8112e-05 | 1.4178e-05 | 127 |
| isotropic | 64 | 0 | MIRROR | correct_H | 1.000000 | 1.1937e-04 | 0 | 1.5479e-04 | 9.4518e-04 | 1.9481e-04 | 127 |
| isotropic | 64 | 0 | MIRROR | unrelated_random_H | 1.000000 | 6.4111e-04 | 6.5247e-04 | 0.002284 | 0.003387 | 8.4536e-04 | 127 |
| isotropic | 64 | 1.0000e-08 | MIRROR | correct_H | 1.000000 | 1.1924e-04 | 0 | 1.4864e-04 | 9.5261e-04 | 1.9475e-04 | 127 |
| isotropic | 64 | 1.0000e-08 | MIRROR | unrelated_random_H | 1.000000 | 6.4111e-04 | 6.5242e-04 | 0.002284 | 0.003387 | 8.4536e-04 | 127 |
| anisotropic | 1 | 0 | MIRROR | correct_H | 1.000000 | 2.6091e-06 | 0 | 5.6779e-06 | 1.4309e-05 | 3.6822e-06 | 127 |
| anisotropic | 1 | 0 | MIRROR | unrelated_random_H | 1.000000 | 7.4851e-06 | 7.8892e-06 | 3.2992e-05 | 4.4083e-05 | 1.0373e-05 | 127 |
| anisotropic | 1 | 1.0000e-08 | MIRROR | correct_H | 1.000000 | 2.6764e-06 | 0 | 5.1834e-06 | 1.5836e-05 | 3.9129e-06 | 127 |
| anisotropic | 1 | 1.0000e-08 | MIRROR | unrelated_random_H | 1.000000 | 7.4248e-06 | 7.9288e-06 | 3.6239e-05 | 4.2661e-05 | 1.0360e-05 | 127 |
| anisotropic | 64 | 0 | MIRROR | correct_H | 1.000000 | 8.4995e-05 | 0 | 2.9461e-04 | 8.6282e-04 | 1.7107e-04 | 127 |
| anisotropic | 64 | 0 | MIRROR | unrelated_random_H | 1.000000 | 4.1621e-04 | 4.2472e-04 | 0.001875 | 0.002625 | 6.0606e-04 | 127 |
| anisotropic | 64 | 1.0000e-08 | MIRROR | correct_H | 1.000000 | 8.4818e-05 | 0 | 2.5825e-04 | 8.5944e-04 | 1.6959e-04 | 127 |
| anisotropic | 64 | 1.0000e-08 | MIRROR | unrelated_random_H | 1.000000 | 4.1621e-04 | 4.2453e-04 | 0.001875 | 0.002625 | 6.0606e-04 | 127 |

| scale | H kind | union rank | s0 | s126 | s127 | s252 | s253 | threshold | s253/s0 | eps32 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | isometric_H | 254 | 1.589481 | 1.204759 | 0.344190 | 0.020256 | 0.018181 | 1.5895e-05 | 0.011438 | 1.1921e-07 |
| 1 | independent_H | 254 | 1.203385 | 0.995743 | 0.994493 | 0.801123 | 0.792736 | 1.2034e-05 | 0.658755 | 1.1921e-07 |
| 64 | isometric_H | 254 | 101.7268 | 77.1046 | 22.0281 | 1.296354 | 1.163594 | 0.001017 | 0.011438 | 1.1921e-07 |
| 64 | independent_H | 254 | 77.0166 | 63.7276 | 63.6476 | 51.2719 | 50.7351 | 7.7017e-04 | 0.658755 | 1.1921e-07 |

### Table 10. The weight bound of (20)-(21) is attained, and the whitened spectra

Every ridge-sweep cell of the `correct_H` variant, both families and both scales. The last four
columns are the whitened singular-value summary: `sv_x / sqrt(N)` over the live directions of
`bh` (so `1.0` means `w = 1`, i.e. no shrink, and `w = (sv_x/sqrt(N))^2`), and the largest
singular value in the numerically null directions divided by `sqrt(N)`, which is the
float32 leakage that (24) describes (values a hair above `1.0` are that same leakage).

| family | scale | lambda | 1-w_min(H) | lam/lam_min | gram dev x | gram dev y | bound xy | gram dev xy | sv_x/sqrtN min | sv_x/sqrtN median | sv_x/sqrtN max | sv_null/sqrtN max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| isotropic | 1 | 1.0000e-06 | 2.6353e-05 | 2.6353e-05 | 2.8286e-05 | 1.7214e-04 | 1.9689e-04 | 1.4793e-04 | 0.999989 | 0.999990 | 0.999991 | 9.4632e-06 |
| isotropic | 1 | 1.0000e-04 | 0.002628 | 0.002635 | 0.002629 | 0.016771 | 0.019399 | 0.014188 | 0.998685 | 0.999008 | 0.999247 | 9.6129e-07 |
| isotropic | 1 | 0.001000 | 0.025677 | 0.026353 | 0.025676 | 0.145716 | 0.171393 | 0.120494 | 0.987078 | 0.990217 | 0.992560 | 3.1171e-07 |
| isotropic | 1 | 0.010000 | 0.208568 | 0.263533 | 0.208569 | 0.630412 | 0.838980 | 0.427935 | 0.889624 | 0.913420 | 0.932309 | 1.0317e-07 |
| isotropic | 1 | 0.100000 | 0.724922 | 2.635329 | 0.724921 | 0.944620 | 1.669542 | 0.307980 | 0.524479 | 0.578743 | 0.631846 | 3.1103e-08 |
| isotropic | 64 | 1.0000e-06 | 6.4339e-09 | 6.4339e-09 | 3.5028e-04 | 9.4746e-04 | 4.8077e-08 | 9.6707e-04 | 0.999979 | 1.000002 | 1.000175 | 4.7999e-04 |
| isotropic | 64 | 1.0000e-04 | 6.4339e-07 | 6.4339e-07 | 1.0181e-05 | 1.2197e-04 | 4.8077e-06 | 1.2292e-04 | 0.999999 | 1.000000 | 1.000001 | 6.6057e-05 |
| isotropic | 64 | 0.001000 | 6.4339e-06 | 6.4339e-06 | 1.0355e-05 | 6.2936e-05 | 4.8075e-05 | 5.8761e-05 | 0.999997 | 0.999998 | 0.999998 | 1.8493e-05 |
| isotropic | 64 | 0.010000 | 6.4335e-05 | 6.4339e-05 | 6.5454e-05 | 4.1493e-04 | 4.8060e-04 | 3.5257e-04 | 0.999974 | 0.999976 | 0.999977 | 6.0925e-06 |
| isotropic | 64 | 0.100000 | 6.4298e-04 | 6.4339e-04 | 6.4228e-04 | 0.004148 | 0.004790 | 0.003516 | 0.999679 | 0.999757 | 0.999816 | 2.0529e-06 |
| anisotropic | 1 | 1.0000e-06 | 4.1973e-05 | 4.1975e-05 | 4.2905e-05 | 2.7424e-04 | 3.1724e-04 | 2.3359e-04 | 0.999985 | 0.999986 | 0.999988 | 1.8230e-05 |
| anisotropic | 1 | 1.0000e-04 | 0.004180 | 0.004198 | 0.004180 | 0.026796 | 0.030976 | 0.022671 | 0.997909 | 0.998606 | 0.999511 | 1.8156e-06 |
| anisotropic | 1 | 0.001000 | 0.040284 | 0.041975 | 0.040285 | 0.215895 | 0.256179 | 0.176163 | 0.979651 | 0.986318 | 0.995144 | 5.7273e-07 |
| anisotropic | 1 | 0.010000 | 0.295651 | 0.419752 | 0.295652 | 0.733575 | 1.029226 | 0.453212 | 0.839254 | 0.884105 | 0.954400 | 1.7616e-07 |
| anisotropic | 1 | 0.100000 | 0.807600 | 4.197517 | 0.807600 | 0.964954 | 1.772554 | 0.373186 | 0.438634 | 0.513422 | 0.710957 | 5.7982e-08 |
| anisotropic | 64 | 1.0000e-06 | 1.0248e-08 | 1.0248e-08 | 3.3916e-04 | 9.9000e-04 | 7.7469e-08 | 9.8342e-04 | 0.999980 | 1.000001 | 1.000170 | 9.5005e-04 |
| anisotropic | 64 | 1.0000e-04 | 1.0248e-06 | 1.0248e-06 | 9.2012e-06 | 8.2173e-05 | 7.7469e-06 | 8.2723e-05 | 0.999999 | 1.000000 | 1.000001 | 1.2197e-04 |
| anisotropic | 64 | 0.001000 | 1.0248e-05 | 1.0248e-05 | 1.2509e-05 | 7.6649e-05 | 7.7465e-05 | 6.7939e-05 | 0.999996 | 0.999997 | 0.999997 | 3.6689e-05 |
| anisotropic | 64 | 0.010000 | 1.0247e-04 | 1.0248e-04 | 1.0274e-04 | 6.7167e-04 | 7.7423e-04 | 5.7055e-04 | 0.999948 | 0.999966 | 0.999988 | 1.1657e-05 |
| anisotropic | 64 | 0.100000 | 0.001024 | 0.001025 | 0.001024 | 0.006677 | 0.007701 | 0.005666 | 0.999488 | 0.999660 | 0.999881 | 3.6781e-06 |


## 6. Findings

F1 `[OFFLINE MEASURED]` The fitted output is invariant to `H` at the shipped ridge. Across the
five `H` variants (correct, unrelated, row-shuffled, scale-matched, exact rotation) the
relative change of `Z` is at most `2.327e-2` at unit row amplitude and `2.389e-5` at
hidden-state row amplitude (Table 2, worst of three seeds, both families), while the same `E`
is reproduced with `cos(Z_i, E_i) >= 0.999001` in every cell.

F2 `[OFFLINE MEASURED]` An unrelated random `H` is not worse than the correct `H`. At unit
scale the anisotropic fixture gives `cos(Z_i, E_i) = 0.999807` for the unrelated `H` and
`0.999520` for the correct one, and the isotropic fixture gives `0.999933` against `0.999968`
(Table 2): the pair carries no advantage, and in the anisotropic cell the ordering is the wrong
way round. At hidden scale both are `1.000000`.

F3 `[OFFLINE MEASURED]` Per-row correspondence of `H` is irrelevant. Row-shuffling `H` leaves
`Z` unshuffled and almost unchanged (`5.5e-6` at hidden scale, `2.0e-3` to `4.1e-3` at unit
scale, Table 2), and `cos(Z_i, E_perm(i))` is `0.242`, i.e. the chance-level angle of the
cloud.

F4 `[OFFLINE MEASURED]` The whitening coordinate system is half noise by construction: the
retained rank is `2(N-1) = 254` in 197 of the 198 runs whose clouds both have full centred row
rank, while `rank(bh) = N-1 = 127`, so `N-1 = 127` coordinates carry no signal and the
`eigen_floor` gain (23) is `1000` (Tables 2, 8 and 9). The `rank_rtol = 1e-5` threshold never binds for full-rank clouds
(Table 9: `s253/s0 = 0.011` and `0.659`, both far above `1e-5`); the `2(N-1)` cap is what
binds.

F5 `[OFFLINE MEASURED]` The finite ridge is visible only when the centred row variance is
within about two decades of `lambda`. `max_i (1 - w_i^H)` is `4.03e-2` at unit scale and
`1.02e-5` at hidden scale, both at `lambda = 1e-3` (Tables 3 and 10). The bound (20) is
attained to four digits in every cell of Table 10, so the deviation is fully explained by the
whitening weights and needs no other mechanism.

F6 `[OFFLINE MEASURED]` At hidden scale the error floor is float32, not the ridge:
`eps32 / sqrt(max(lambda, eigen_floor))` predicts `1.20e-7 x 1000 = 1.20e-4` at
`lambda = 1e-8` and floor `1e-6` and the measurement is `1.196e-4`; the same law holds to
within 2.4x at floor `1e-4` (`1.52e-5`) and `1e-2` (`2.77e-6`); float64 gives `2.52e-8`
(Table 8). At unit scale the formula over-predicts by `36x` and `5x` (measured/predicted
`0.028` and `0.194`), so the law is a hidden-scale result, not a scale-free one.

F7 `[OFFLINE MEASURED]` Because of F6 the shipped `lambda = 1e-3` is at the minimum of the
measured error curve at hidden scale (`9.5e-6`, against `8.4e-5` at `1e-6` and `8.5e-4` at
`1e-1`, Table 3). It does not, however, constrain the fit: the ridge enters only through the
weights of (9), and the map is still fixed by interpolation (17), which is underdetermined for
every `lambda`. The ridge buys numerical damping, not identifiability.

F8 `[OFFLINE MEASURED]` Rank deficiency is the only measured way to make `Z` depend on `H`
(Table 7): rank 8 gives `cos 0.281`, rank 64 gives `0.717`, rank 126 gives `0.994`. The same
table shows the reverse direction: a rank-deficient `E` still gets reproduced as far as it
exists (`recon 4.1e-4 .. 1.5e-1`), because the interpolation of (17) is on the `E` side.

F9 `[OFFLINE MEASURED]` Reference-token top-1 retrieval is `1.0` in every full-rank cell and
equals the retrieval of `E_cal` itself in 208 of 210 runs; the two exceptions are the rank-8
`H` rows (`0.531` and `0.703` against `1.0`). Retrieval therefore inherits the degeneracy and
cannot discriminate between `H` variants either.

F10 `[HYPOTHESIS]` Two regimes, `1e-2` and `1e-5` apart, are possible at the shipped ridge
depending on the row amplitude of the real hidden states. This report does not measure a GLM
capture. If real pre-norm residual rows at `d = 6144` have norms of order 10-100, the
degeneracy is total (F1 at hidden scale); if they are of order 1, it is the unit-scale case of
`cos 0.9994` with a `3.4e-2` reconstruction error. Either way the fit does not depend on the
`H`-`E` relation.

## 7. Proposals (no code was changed)

These are proposals only: `statebridge.py` was not modified, and no proposal below is
validated by this report.

P1 `[HYPOTHESIS]` Do not use the in-sample residual or the in-sample cosine as an alignment
quality signal. On this solver it is a constant of the arithmetic (F1-F3, F9); a quality gate
built on it cannot fail, so it cannot protect anything.

P2 If a gate is needed, hold rows out. Fit on a subset `k < N` and score on the remainder: the
system (17) has `N` equations for `d` unknowns, but a `k`-row fit has only `k` equations and
the held-out rows expose the misfit. The prior audit's `align_audit.json` already measures the
held-out failure (`exp6_out_of_sample`); this report supplies the reason.

P3 If the intent is a *transferable* map, the object returned by (16) is not it: in the limit
(15) `A(H)` is a right inverse of the fitted `Hc`, and at any `lambda` it contains no `H`-`E`
relation beyond `mu_E`, `Sigma_E` and the row pairing used to construct `R*`. Any downstream use that assumes `Z` was produced by a
shared map would be reading a fit that has `N d` free parameters against `N` constraints.

P4 Numerical, not structural: `eigen_floor = 1e-6` is an absolute floor in a quantity whose
scale follows the data. `[OFFLINE MEASURED]` At hidden scale raising it to `1e-2` lowers the
measured error from `1.196e-4` to `2.768e-6` with no measured cost (Table 8), because
`lambda_min(H) = 97.6` there. At unit scale the same change costs `7.2e-2` reconstruction
error, because `1e-2` then exceeds `lambda_min(H) = 0.024` and clips real directions. One
constant therefore cannot serve both amplitudes; a relative floor
(`eigen_floor = c * lambda_max(ch)`) would be scale-invariant but needs its own audit. It is
not applied here.

P5 `AlignmentInfo.union_rank` (`statebridge.py:53`, `:70`) reports a number that is `2(N-1)`
for every full-rank capture at `N = 128` (F4, 197 of 197 measured runs). As a receipt field it
carries no information about the capture. If a rank receipt is wanted, report `rank(bh)` or
the ratio `s_retained_min / s_0` instead.

## 8. Test file

`extensions/sglang-occ3/tests/test_alignment_degeneracy.py` (new, 12 tests, passes on the
current tree):

```
cd C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3
.venv/Scripts/python.exe -m pytest tests/test_alignment_degeneracy.py -q
12 passed
```

Claim to test map, with the measured tolerance justification recorded in each docstring:

- Theorem 1 + Theorem 2 at `lambda = 1e-6`: `test_independent_h_collapses_to_e_at_small_ridge`
  (`cos >= 1 - 1e-4`, change between unrelated `H` `<= 2e-3`, measured `6.3e-4`).
- Corollary 2a on the `E` side of (16): `test_row_permutation_of_h_does_not_permute_the_output`
  (`5.5e-6` measured against a `1e-4` tolerance, and `cos` to the permuted `E` below `0.5`
  against a measured `0.242`).
- (20)-(21): `test_finite_ridge_deviation_equals_the_weight_bound`, two-sided
  (`gram dev x <= (1 + 1e-3) (1 - w_min)` and `>= 0.5 (1 - w_min)`), plus
  `test_deviation_grows_with_ridge_and_vanishes_below_it` (monotone in `lambda`).
- Corollary 3 (26): `test_ridge_is_irrelevant_when_the_row_grams_agree`
  (`lambda` up to `1e2`, error `<= 1e-4` against a measured `3.5e-6`).
- Shipped regime: `test_shipped_configuration_is_in_the_degenerate_regime`, one branch per
  measured regime (`cos >= 0.999`, `recon <= 5e-2`, `change <= 5e-2` at unit scale;
  `cos >= 1 - 1e-6`, `recon <= 1e-4`, `change <= 1e-4` at hidden scale).
- Red-first control: `test_control_large_ridge_breaks_the_degeneracy` asserts that at
  `lambda = 1e-1` the same quantities leave those bands (`cos 0.9637`, `recon 0.2693`,
  `1 - w_min 0.808`), so the tolerances above are not vacuous.
- (25): `test_rank_deficiency_is_the_one_thing_that_breaks_it` (rank 8 gives `cos < 0.5`,
  `recon > 0.9`, `gram dev x >= 0.999`).
- Guards: `test_guards_refuse_the_two_edges_the_derivation_uses` witnesses the `ridge > 0` and
  `n <= 128` refusals of `statebridge.py:43` (Table 4).

## 9. Limits of this report

- `[OFFLINE MEASURED]` All numbers are synthetic-fixture numbers at `D = 6144` on CPU float32
  `torch 2.8.0+cpu`. No GLM capture, no CUDA, no vocabulary gather is measured here.
- `[OFFLINE MEASURED]` Seeds: three seeds on the main matrix (Table 2) and one seed on the
  sweep axes (Tables 3-10). The per-seed range on the main matrix is at most `1.12e-5` in
  mean cosine and `4.07e-4` in relative reconstruction, both far below the reported
  differences between regimes.
- `[OFFLINE MEASURED]` `N = 256` is not measurable with the shipped guards (Table 4); its rows
  are `MIRROR`. The `lambda = 0` limit is likewise `MIRROR`; every other row is the shipped
  function, and mirror parity is exactly `0.0` (bit-identical) on all 210 runs, so the
  distinction cannot change a measured value, only which cells are legal.
- `[OFFLINE MEASURED]` `target_norm` is set to the fixture mean row norm of `E`
  (`mean_i ||E_i||`), the same statistic the shipped `mean_embedding_norm` computes
  (`statebridge.py:87-112`). No production vocabulary is measured.
- `[HYPOTHESIS]` Nothing in this report says the deployed GLM lives at unit or at hidden scale;
  F10 states both readings and their measured consequences.

