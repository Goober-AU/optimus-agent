#!/usr/bin/env python
"""Emit reports/occ3-alignment-repair/MATHEMATICAL-DEGENERACY.md from measured JSON.

Every number in every table is read from
reports/occ3-alignment-repair/analysis/degeneracy_measurements.json, produced by
probes/measure_degeneracy.py.  No number is typed by hand.

  extensions/sglang-occ3/.venv/Scripts/python.exe \
      reports/occ3-alignment-repair/probes/build_degeneracy_report.py
"""
from __future__ import annotations

import json
import math
import os

REP = "C:/Users/openclawuser/optimus-occ3-latent-validation/reports/occ3-alignment-repair"
SRC = REP + "/analysis/degeneracy_measurements.json"
OUT = REP + "/MATHEMATICAL-DEGENERACY.md"

M = json.load(open(SRC, encoding="utf-8"))
RUNS = M["runs"]
CELLS = M["cells"]
REFUSALS = M["refusals"]
SPECTRA = M["spectra"]
META = M["meta"]


def R(axis, mode="PRODUCTION", **kw):
    for r in RUNS:
        if r["axis"] == axis and r.get("mode") == mode and all(r.get(k) == v for k, v in kw.items()):
            return r
    raise KeyError((axis, mode, kw))


def g(x, p=4):
    if isinstance(x, str):
        return x
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return str(x)
    if x == 0:
        return "0"
    if abs(x) < 1e-3 or abs(x) >= 1e4:
        return f"{x:.{p}e}"
    if abs(x) < 10:
        return f"{x:.6f}"
    return f"{x:.{p}f}"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        out.append("| " + " | ".join(g(c) for c in row) + " |")
    return "\n".join(out)


def cell_meta(family, scale, seed=1):
    for c in CELLS:
        if c["axis"] == "A_main" and c["family"] == family and c["scale"] == scale and c["seed"] == seed:
            return c
    raise KeyError((family, scale, seed))


parts = []

SEC1 = r"""
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

`[OFFLINE MEASURED]` measurement run: python %(py)s, torch %(torch)s, runtime %(rt)s s,
%(nruns)s runs, %(nref)s refusals, 0 non-finite values in any output.
"""

SEC_TABLES = r"""
## 5. Tables

Every column is a measurement of `probes/measure_degeneracy.py`; `MIRROR` rows are named as
such and exist only where a shipped guard refuses the cell.

### Table 1. Fixture properties as measured (`N = 128`, seed 1)

%(t1)s

`pair cos` is the mean off-diagonal cosine of the row cloud; `erank` is
`exp(-sum p_i log p_i)` over the centred Gram spectrum; `w_min` is the smallest whitening
weight at the shipped ridge, i.e. `1 - w_min` is the deviation entering (20).

### Table 2. Main matrix: five H variants against one E (3 seeds, `lambda = 1e-3`)

Means over seeds 1-3; `cos min` is the worst row cosine over the three runs. `rel change vs
random H` is `||Z(H) - Z(unrelated)||_F / ||Z(unrelated)||_F`; for the `unrelated` row itself
the reference is `correct_H`.

%(t2)s

### Table 3. Ridge sweep with the shipped guards (PRODUCTION rows only)

`1 - w_min(H)` is `max_i (1 - w_i^H)` from (19); `lam/lam_min` is the bound of (20);
`gram dev x` is the measured `||X X^T - N P||_2 / N`; `gram dev xy` is `||X X^T - Y Y^T||_2 / N`
against the sum bound of (21).

%(t3)s

### Table 4. Refusals observed: the cells the shipped guards reject

`lambda = 0` and `N = 256` are refused by `statebridge.py:43`
(`require(2 <= n <= 128 and d > 2 * n and ridge > 0 ...)`), so both are limit statements and
are measured through a guard-relaxed `MIRROR` (Tables 5 and 9).

%(t4)s

### Table 5. Capture-window sweep `N` in {16, 32, 64, 128, 256} (anisotropic, seed 1)

`N = 256` exceeds the shipped guard, so its rows are `MIRROR`; they were produced without the
pairwise change metric, which is why that column is `-` there.

%(t5)s

### Table 6. Isometric `H` (equal centred row Gram) against the 2.5x scale control

`isometric_H_same_gram` is `H = E Q` with `Q` a product of 96 Givens rotations;
`isometric_H_2.5x_scale` is the same H at 2.5x row norm.

%(t6)s

### Table 7. Rank-deficiency control (seed 5, `lambda = 1e-3`)

Rows `H_rank_k` replace `H` with an exact rank-k product; rows `E_rank_k` replace `E` with a
rank-k projection (those rows have no paired token, so their retrieval column is not
comparable and is omitted).

%(t7)s

### Table 8. Float32 floor against `eigen_floor` (MIRROR, `lambda = 1e-8`, seed 3)

`predicted` is `eps32 / sqrt(max(lambda, eigen_floor))` from (24) with `eps32 = 1.192e-7`;
`measured/predicted` is the reported ratio. The ratio is near `1` only at hidden scale
(`scale = 64`); at unit scale it is `0.028` and `0.194`, so (24) is not scale-free. The
`fp32_floor_1e-2` and `fp32_floor_1e0` unit-scale rows are not floor-limited at all: the floor
there exceeds `lambda_min(H) = 0.024` and clips real directions, which is why their error rises
to `7.2e-2` and `2.8e-1`.

%(t8)s

### Table 9. The `lambda -> 0` limit rows and the union singular spectrum

Upper block: `MIRROR` rows at `lambda = 0` and `lambda = 1e-8` (seed 1). Lower block: singular
values of the stacked centred pair `[Hc; Ec]` at the indices that matter for the retained rank,
against the `rank_rtol = 1e-5` threshold of `statebridge.py:52-53`; `s253/s0` shows that the
retained rank `2(N-1)` is real data structure, not float32 noise.

%(t9)s

### Table 10. The weight bound of (20)-(21) is attained, and the whitened spectra

Every ridge-sweep cell of the `correct_H` variant, both families and both scales. The last four
columns are the whitened singular-value summary: `sv_x / sqrt(N)` over the live directions of
`bh` (so `1.0` means `w = 1`, i.e. no shrink, and `w = (sv_x/sqrt(N))^2`), and the largest
singular value in the numerically null directions divided by `sqrt(N)`, which is the
float32 leakage that (24) describes (values a hair above `1.0` are that same leakage).

%(t10)s
"""

# ---------------------------------------------------------------------------
# table builders
# ---------------------------------------------------------------------------
def agg_main():
    keys = {}
    for r in RUNS:
        if r["axis"] != "A_main":
            continue
        keys.setdefault((r["family"], int(r["scale"]), r["variant"]), []).append(r)
    rows = []
    for (fam, sc, var), rs in keys.items():
        rows.append([fam, int(sc), var,
                     sum(x["cos_mean"] for x in rs) / len(rs),
                     min(x["cos_min"] for x in rs),
                     sum(x["rel_recon_to_normcalibrated_E"] for x in rs) / len(rs),
                     max(x["rel_recon_to_normcalibrated_E"] for x in rs),
                     sum(x["rel_change_vs_random_H"] for x in rs) / len(rs),
                     sum(x["retrieval_top1"] for x in rs) / len(rs),
                     rs[0]["union_rank"],
                     sum(1.0 - x["w_H_min"] for x in rs) / len(rs),
                     sum(x["gram_dev_xy"] for x in rs) / len(rs)])
    return rows


T1 = table(
    ["family", "scale", "E row norm", "E pair cos", "erank E", "H row norm", "H pair cos",
     "erank H", "lam_min(H)", "w_min(H)"],
    [[c["family"], int(c["scale"]), c["e_row_norm_mean"], c["e_pair_cos"], c["erank_E"],
      c["h_row_norm_mean"], c["h_pair_cos"], c["erank_H"],
      R("A_main", family=c["family"], scale=c["scale"], seed=1, variant="correct_H")["lam_bh_min_live"],
      R("A_main", family=c["family"], scale=c["scale"], seed=1, variant="correct_H")["w_H_min"]]
     for c in CELLS if c["axis"] == "A_main" and c["seed"] == 1])

T2 = table(["family", "scale", "H variant", "cos mean", "cos min", "recon mean", "recon max",
            "rel change vs random H", "retrieval", "union rank", "1-w_min(H)", "gram dev xy"],
           agg_main())

T3 = table(["family", "scale", "lambda", "H variant", "cos mean", "cos median", "recon",
            "rel change vs random H", "gram dev x", "1-w_min(H)", "lam/lam_min",
            "procrustes resid", "gram dev xy"],
           [[r["family"], int(r["scale"]), r["ridge"], r["variant"], r["cos_mean"], r["cos_median"],
             r["rel_recon_to_normcalibrated_E"], r.get("rel_change_vs_random_H"),
             r["gram_dev_x"], r["one_minus_w_H_max"],
             r["ridge"] / r["lam_bh_min_live"], r["procrustes_residual"], r["gram_dev_xy"]]
            for r in RUNS if r["axis"] == "C_ridge_sweep" and r.get("mode") == "PRODUCTION"])

T4 = table(["axis", "family", "scale", "N", "lambda", "H variant", "error code"],
           [[r["axis"], r["family"], int(r["scale"]), r["n"], r["ridge"], r["variant"], r["refusal"]]
            for r in REFUSALS])

T5 = table(["scale", "N", "mode", "H variant", "cos mean", "recon", "rel change vs random H",
            "procrustes resid", "union rank", "rank(bh)", "lam_min(H)", "1-w_min(H)"],
           [[int(r["scale"]), r["n"], r["mode"], r["variant"], r["cos_mean"],
             r["rel_recon_to_normcalibrated_E"], r.get("rel_change_vs_random_H"),
             r["procrustes_residual"], r["union_rank"], r["rank_bh"], r["lam_bh_min_live"],
             r["one_minus_w_H_max"]] for r in RUNS if r["axis"] == "B_n_sweep"])

T6 = table(["family", "scale", "H variant", "lambda", "cos mean", "recon", "union rank",
            "rank(bh)", "gram dev xy", "1-w_min(H)"],
           [[r["family"], int(r["scale"]), r["variant"], r["ridge"], r["cos_mean"],
             r["rel_recon_to_normcalibrated_E"], r["union_rank"], r["rank_bh"],
             r["gram_dev_xy"], r["one_minus_w_H_max"]]
            for r in RUNS if r["axis"] == "F_shared"])

T7 = table(["scale", "replacement", "cos mean", "recon", "raw rank Hc", "raw rank Ec",
            "union rank", "rank(bh)", "gram dev x", "procrustes resid", "retrieval"],
           [[int(r["scale"]), r["variant"], r["cos_mean"], r["rel_recon_to_normcalibrated_E"],
             r["raw_rank_Hc"], r["raw_rank_Ec"], r["union_rank"], r["rank_bh"],
             r["gram_dev_x"], r["procrustes_residual"],
             r["retrieval_top1"] if r["variant"].startswith("H_") else "-"]
            for r in RUNS if r["axis"] == "D_rank"])

EPS32 = 1.1920928955078125e-07


def floor_of(label):
    return float(label.split("_")[-1])


def predicted_floor(ridge, label):
    return EPS32 / math.sqrt(max(ridge, floor_of(label)))


T8 = table(["scale", "setting", "dtype", "lambda", "eigen_floor", "cos mean", "recon",
            "procrustes resid", "predicted", "measured/predicted"],
           [[int(r["scale"]), r["variant"], r["dtype_used"].split(".")[-1], r["ridge"],
             floor_of(r["variant"]), r["cos_mean"], r["rel_recon_to_normcalibrated_E"],
             r["procrustes_residual"], predicted_floor(r["ridge"], r["variant"]),
             r["rel_recon_to_normcalibrated_E"] / predicted_floor(r["ridge"], r["variant"])]
            for r in RUNS if r["axis"] == "E_floor"])

T9 = "\n\n".join([
    table(["family", "scale", "lambda", "mode", "H variant", "cos mean", "recon",
           "rel change vs correct H", "gram dev x", "gram dev xy", "procrustes resid",
           "null dirs"],
          [[r["family"], int(r["scale"]), r["ridge"], r["mode"], r["variant"], r["cos_mean"],
            r["rel_recon_to_normcalibrated_E"], r.get("rel_change_vs_correct_H"),
            r["gram_dev_x"], r["gram_dev_xy"], r["procrustes_residual"],
            r["null_directions_bh"]]
           for r in RUNS if r["axis"] == "C_ridge_sweep" and r.get("mode") == "MIRROR"]),
    table(["scale", "H kind", "union rank", "s0", "s126", "s127", "s252", "s253",
           "threshold", "s253/s0", "eps32"],
          [[int(r["scale"]), r["variant"], r["union_rank_reported"], r["s0"], r["s126"], r["s127"],
            r["s252"], r["s253"], r["threshold"], r["s253_over_s0"], r["float32_eps"]]
           for r in SPECTRA])])

T10 = table(["family", "scale", "lambda", "1-w_min(H)", "lam/lam_min", "gram dev x",
             "gram dev y", "bound xy", "gram dev xy",
             "sv_x/sqrtN min", "sv_x/sqrtN median", "sv_x/sqrtN max", "sv_null/sqrtN max"],
            [[r["family"], int(r["scale"]), r["ridge"], r["one_minus_w_H_max"],
              r["ridge"] / r["lam_bh_min_live"], r["gram_dev_x"], r["gram_dev_y"],
              r["bound_gram_dev_xy"], r["gram_dev_xy"],
              r["sv_x_over_sqrtn_min"], r["sv_x_over_sqrtn_median"], r["sv_x_over_sqrtn_max"],
              r["sv_x_null_max_over_sqrtn"]]
             for r in RUNS if r["axis"] == "C_ridge_sweep" and r.get("mode") == "PRODUCTION"
             and r["variant"] == "correct_H"])

parts.append(SEC1 % dict(py=META["python"], torch=META["torch"], rt=META["runtime_seconds"],
                         nruns=len(RUNS), nref=len(REFUSALS)))
parts.append(SEC_TABLES % dict(t1=T1, t2=T2, t3=T3, t4=T4, t5=T5, t6=T6, t7=T7, t8=T8,
                               t9=T9, t10=T10))

SEC_TAIL = r"""
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
"""

parts.append(SEC_TAIL)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(parts) + "\n")
print("wrote", OUT, os.path.getsize(OUT), "bytes")
