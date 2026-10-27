# Calibration design for the OCC3 StateBridge alignment

Status of every statement in this document:

- **SOURCE-ATTESTED**: read from the pinned sources in this worktree.
- **OFFLINE MEASURED**: produced by a command shown in this repository; the command
  line is repeated in every table caption.
- **HYPOTHESIS**: an inference that this study does not measure.

Scope: whether a *frozen* alignment, fitted on independent calibration rows, can map
new hidden states into the receiver embedding space well enough to be useful. No GPU,
no model weights, no network. All measurements are CPU Torch on synthetic fixtures
whose generator is in `analysis/calibration_experiment.py`.

## 1. What the production bridge does today

SOURCE-ATTESTED, `extensions/sglang-occ3/occ3_sglang/statebridge.py`:

| fact | citation |
|---|---|
| operator signature, `ridge=1e-3`, `eigen_floor=1e-6`, `rank_rtol=1e-5` | statebridge.py:32-33 |
| row count must satisfy `2 <= n <= 128` and `d > 2n` | statebridge.py:43 |
| one union right-singular basis from the stacked centred pair | statebridge.py:50-55 |
| rank is `min(#s > max(s0*rtol, floor), 2*(n-1))` | statebridge.py:52-53 |
| both covariances get the same `+ ridge*I` | statebridge.py:58-59 |
| whitening of both sides, then orthogonal Procrustes on the whitened pair | statebridge.py:60-63 |
| output is rescaled so every row has one scalar norm | statebridge.py:64-68 |
| anchor blend `(1-alpha)*prefix + alpha*nearest` with `alpha=0.3` | statebridge.py:115,155 |
| scalar target norm is the mean row norm of the embedding table | statebridge.py:87-113 |
| workspace plan allows a 64 MiB solver slack | statebridge.py:158-166 |

Attested by reading the cited lines in this worktree; no command produces these rows.

SOURCE-ATTESTED, `extensions/sglang-occ3/occ3_sglang/latent_worker.py`:

- `align()` selects the retained rows, calls the embedding table on those token ids, and
  calls `align_hidden_sequence(h, embeddings, self.target_norm)` at line 333 on
  exactly the rows it just read, then drops the retained ring at line 363.
- The aligned prefix is the *same* rows that were used to fit the operator: there is no
  calibration set, no frozen parameter store, and no held-out path anywhere in the
  extension (a case-insensitive search for `calibrat|frozen|fit_on|out of sample` over
  `*.py` matches only test files and the offline analysis added by this study).
- `target_norm` is computed once per worker from `mean_embedding_norm` (line 326-327),
  so the scalar norm is already a calibration statistic; everything else is per message.

### 1.1 Why in-sample numbers from METHOD A cannot answer the question

Two properties follow from the algebra at statebridge.py:58-63.

**Property 1 (no held-out evaluation exists).** The map is fitted to `(H_msg, E_msg)`
and applied to `H_msg`. Any residual it reports is an in-sample residual.

**Property 2 (the operator is an in-sample interpolator).** With `ch = bh^T bh/n + lI`
and `x = bh ch^{-1/2}`,

    x^T x = ch^{-1/2} (bh^T bh) ch^{-1/2} = n I - n l ch^{-1},

so on the directions where the calibration eigenvalue is large compared with `l` the
whitened cloud `x` has Gram matrix `n I`; the same holds for `y` on the embedding side.
Two row sets with the same Gram matrix are congruent: one orthogonal `R` maps one onto
the other exactly, and the SVD-Procrustes at statebridge.py:62 finds that `R`. Hence the
residual collapses to the float32 floor and `z` reproduces the norm-calibrated `E_msg`,
whatever `H_msg` contains. This is the mechanism, not a fixture artefact: it depends only
on the rank being saturated, which is the normal case because `rank = 2(n-1)` is exactly
the centred row-space dimension of the stacked pair.

OFFLINE MEASURED, the mechanism directly, `analysis/probe_in_sample_interpolation.py`
(`d=6144`, `n=128`, target norm 1.0, seeds 0-2):

| row population | rank | rel direction error | row cosine | swap change | shuffle change |
|---|---|---|---|---|---|
| isotropic Gaussian rows | 254 | 0.000028 | 1.000000 | 0.000039 | 0.000039 |
| anisotropic fixture rows (scenario `mid`) | 254 | 0.0686-0.0713 | 0.9975 | 0.00012 | 0.00012 |

Command: `python reports/occ3-alignment-repair/analysis/probe_in_sample_interpolation.py`

On isotropic rows the row space is rank saturated (`d > 2n`), the two whitened clouds are
congruent, and the operator reproduces `E_msg` to the float32 floor while its sensitivity
to which hidden rows it was given is 0.000039 - four orders below the target
signal. On the anisotropic fixture the rank criterion at statebridge.py:52-53 truncates
and a small residual survives (0.0686-0.0713), but the conclusion is unchanged: the output
is a function of `E_msg`.

OFFLINE MEASURED, `analysis/calibration_experiment.py`, fixture rows (`d=6144`,
`n=128`, anisotropic spectrum, pair cosine 0.24), scenario `mid`, seed 0:

| quantity | value |
|---|---|
| METHOD A relative reconstruction error to norm-calibrated `E_msg`, scale free | 0.0700 |
| METHOD A mean row cosine to `E_msg` | 0.9976 |
| METHOD A relative output change when `H_msg` is replaced by unrelated rows | 0.0001 |
| METHOD A relative output change when `H_msg` rows are shuffled | 0.0001 |
| CONTROL F (the target rows themselves) row cosine | 1.0000 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 128,512,2048 --seeds 0,1,2 --scenarios null,weak,mid,strong --lambdas 1e-4,1e-3,1e-2 --out reports/occ3-alignment-repair/analysis\calibration_results.json --tables reports/occ3-alignment-repair/analysis\tables` (scenario `mid`, `M=128`, `lam_rel=1e-3`; seed mean).

The hidden states therefore contribute 0.01% to METHOD A's output. The
consequence for the whole study is that "does it land near the embedding manifold" and
"does it keep the hidden-state signal" are *independent* axes, and METHOD A sits at one
extreme of the first axis with nothing on the second.

## 2. The candidates

Notation: `H_msg` (n x d) hidden rows, `E_msg` (n x d) reference-token embedding rows for
the same messages, `H_cal` (M x d), `E_cal` (M x d) an independent calibration draw,
`mu` row means, `A = H_cal - mu_H`, `B = E_cal - mu_E`.

### METHOD A - per-message whitened Procrustes (baseline)

Fitted and applied on the same rows; the production operator. Baseline only: by
Property 2 it measures an interpolation, not an alignment.

### METHOD B - frozen whitened Procrustes

`z = ((h - mu_H) Q) ch^{-1/2} R ce^{1/2} Q^T + mu_E`, with `Q`, `ch`, `ce`, `R` from
`(H_cal, E_cal)` only. Mathematically the same operator as A, with the parameter/apply
split that production lacks.

Justification: whitening is a Mahalanobis normalisation of each population, and the
rotation is the orthogonal map that carries the whitened hidden geometry onto the
whitened embedding geometry. If both populations are draws from the same two
distributions, the population whitener is the *right* object and the per-message fit is
just a noisy estimate of it. The estimator is therefore justified; the question is
whether it transfers.

Two structural hazards, both measurable:
1. `ce^{1/2}` and `ch^{-1/2}` are fitted to the calibration spectrum. Any direction whose
   calibration variance is small is amplified by up to `1/sqrt(ridge) = 31.6` on new
   rows, which carry real energy there. When the basis rank exceeds the calibration
   data rank (`rank > M-1`) the ridge floor, not the data, sets the gain.
2. The map is rank limited to `span(Q)`: energy outside the retained basis is discarded.

### METHOD C - affine orthogonal Procrustes

`z = mu_E + s (h - mu_H) R`, `R` orthogonal, `s` the least-squares scalar for that `R`:

    R_r = U V^T from SVD(M_h^T M_e),  M_h = (H_cal - mu_H) Q,  M_e = (E_cal - mu_E) Q
    R = I + Q (R_r - I) Q^T,          (R^T R = I exactly)
    s = <(H_cal - mu_H) R, E_cal - mu_E>_F / ||(H_cal - mu_H) R||_F^2

Justification: the orthogonal group is the isometry group, so this is the *closest*
frozen map to "move the hidden cloud without stretching it". It cannot amplify any
direction, so it is the most conservative candidate and the natural control for
whitening hazards. The identity completion outside `span(Q)` is forced: `H_cal^T E_cal`
has rank at most `M-1`, far below `d=6144`, so the rotation outside `span(Q)` is not
identified by any finite corpus and identity is the minimum-distortion completion.

Cost of that conservatism: one scale `s` for the whole space is a very coarse SNR
compromise. A single global shrink factor must trade the well-determined content
directions against the noise directions, so on a fixture with a large isotropic noise
floor the residual-optimal `s` is small and the output sits near `mu_E`.

### METHOD D - dual ridge

    Z = mu_E_cal + (H_msg - mu_H_cal) A^T (A A^T + lambda I)^{-1} B,   lambda = lam_rel * mean(diag(A A^T))

solved with a Cholesky factor of the M x M kernel (no explicit inverse, no d x d matrix).
By the push-through identity this equals the primal ridge estimator
`mu_E + (h - mu_H)(A^T A + lambda I)^{-1} A^T B`, i.e. the minimum-mean-square-error linear
predictor of the embedding from the hidden row under an isotropic Gaussian prior.

Justification: it is the only candidate that shrinks each direction according to its own
signal-to-noise ratio, which is what a d=6144 problem with M <= 2048 rows needs. It also
never whitens the *target*, so it cannot inflate embedding noise, and it has no basis
rank to choose. The price is that the frozen artifact is `2*M*d + M^2` numbers, linear in
the calibration size, versus `d*r + r^2` for B and C.

### CONTROL F - direct reference-token embeddings

`z = E_msg`. Perfect manifold landing, zero hidden-state dependence. This is the
confound made explicit: any method that scores like F on reconstruction while showing no
sensitivity to `H` is a target copier.

### CONTROL K - zero state

`z = 0`. Floor for reconstruction and for norm statistics; retrieval is defined as 0 and
the cosine metrics are reported as `nan` because a zero row has no direction.

### Oracle bounds (not methods)

- **O**: the generator's own predictable part of `e` given the message code,
  `sqrt(rho_E) m_E + sqrt(1-rho_E) sqrt(f) u_E`, renormalised to the scalar target norm.
  No method that sees only `H_msg` can beat it; it bounds *all* linear candidates.
- **G**: METHOD C given the true content rotation (polar factor of the exact map in the
  `Q` basis). It separates "C's estimator is weak" from "C's model class is weak".
Both are labelled `oracle` in every table and use generator internals.

## 3. Fixture design

Generator (analysis/calibration_experiment.py, class `World`):

    c_i ~ N(0, diag(spec^2)),  spec_k = (k+1)^-0.7 normalised,  kappa = 0.5
    u_h,i = ( sqrt(1-kappa) Uh c_i + sqrt(kappa) U0 c_i ) / |c_i|
    u_e,i = ( sqrt(1-kappa) Ue Wc c_i + sqrt(kappa) U0 c_i ) / |c_i|
    h_i   = sqrt(rho_H) m_H + sqrt(1-rho_H)( sqrt(f) u_h,i + sqrt(1-f) v_h,i )
    e_i   = sqrt(rho_E) m_E + sqrt(1-rho_E)( sqrt(f) u_e,i + sqrt(1-f) v_e,i )

with `Uh, Ue, U0` random orthonormal `d x r` bases (`U0` orthogonal to the other two),
`Wc` a random `r x r` orthogonal mixing, `v` isotropic unit noise, and per-row lognormal
magnitudes (`sigma=0.15`). `rho_H`, `rho_E` are bisected so the mean pairwise cosine of
each population is exactly the requested 0.24; `f` is the share of the non-mean energy
that is content (shareable) rather than idiosyncratic; `kappa` is the share of the content
that is shared in the same ambient subspace on both sides.

Role of `kappa`: `kappa` is the share of the content that is the *same* ambient vector on
both sides, i.e. the part that an untrained copy can exploit. It is **not** what makes a
rotated map learnable. The population cross-covariance `E[h e^T]` carries the code through
the rotated path too, and measured on this fixture the sample cross-covariance SNR is
5.2 at `kappa = 0` against 6.8 at `kappa = 0.7` (`M=512`, scenario `mid`, 3 seeds;
`analysis/sens/kappa0.json`, `analysis/sens/kappa70.json`). What `kappa` does control is
whether copying `h` can score: at `kappa = 0` CONTROL I falls to the mean-only level
(row cosine 0.4962, retrieval 0.005) and METHOD C with its oracle rotation also collapses
(row cosine 0.4978, retrieval 0.013), while METHOD D is unchanged (0.763 at every
`kappa in {0, 0.35, 0.7}`). The default is therefore `kappa = 0.5`, and the `kappa` sweep
is the experiment that separates "the fit generalises" from "the fixture lets a copy
work". An earlier draft of this document predicted that `kappa = 0` would make the map
unlearnable because the sample cross-covariance would be noise dominated; that prediction
is falsified by the measurement above and is recorded here so the sweep is not read as a
learnability control.

Independence: message rows, probe rows and calibration rows are three independent draws
of rows from the same generator parameters (same bases, spectrum, `rho`, `f`, `kappa`);
only the row codes differ. Calibration sets for `M in {128, 512, 2048}` are nested
prefixes of one draw, so the M sweep isolates the sample size.

Reference-token retrieval: the vocabulary is the 128 message embedding rows plus 4096
background rows drawn from the same embedding marginal, so chance is 1/4224 = 0.000237.
An output row counts as a hit when its nearest vocabulary entry by cosine is its own
reference row.

## 4. Metrics and why each one exists

| # | metric | definition | what it catches |
|---|---|---|---|
| 1 | `rel_recon` | `||z - Ehat||_F / ||Ehat||_F`, `Ehat` = `E_msg` rows scaled to the scalar target norm | the specified reconstruction error |
| 1b | `rel_recon_dir` | the same with both sides row-normalised, so magnitude cannot help | scale-free direction error; CONTROL F is exactly 0 |
| 2 | `mean_row_cos` | mean over rows of `cos(z_i, Ehat_i)` | manifold landing, scale free |
| 3 | `swap_rel_change` | `||z(H_probe) - z(H_msg)||_F / ||z(H_msg)||_F`, `H_probe` unrelated rows of the same family | the hidden-state contribution; a target copier scores 0 |
| 3b | `swap_fid_cos` | `cos(flatten(dz), flatten(Ehat_probe - Ehat_msg))` | whether that contribution points at the truth, not merely "changes" |
| 4 | `shuffle_rel_change` | `||z(H_permuted) - z(H)||_F / ||z(H)||_F` | whether the row *pairing* matters; permutation-invariant copiers score 0 |
| 5 | `retrieval_top1` | nearest-vocabulary-entry accuracy | the operationally useful question: is the right token identified |
| 6 | norm statistics | mean and sd of `||z_i||` over the scalar target norm, plus p05/p95 | whether the map lands at the right radius; production forces the mean exactly |
| 7 | `nonfinite` | non-finite entries over all three applications | numerical safety |
| 8 | cost | `M x M` or `2M x d` shapes solved, a flop estimate, and the resident size of the frozen artifact | whether the candidate is affordable |

This table defines the metrics; it is not itself a measurement. Every results table below
cites the command that produced its columns.

Metric 3 is the load-bearing one. Reconstruction error and row cosine can both be gamed
by copying the targets (CONTROL F), and retrieval can be gamed by any map that lands in
the right neighbourhood, so the hidden-state sensitivity is measured by perturbing the
hidden rows while holding the target rows fixed. `swap_fid_cos` guards the opposite
failure: a map can be very sensitive to `H` in a direction that has nothing to do with the
targets.

Flop estimates use these conventions and are rough orders of magnitude only: exact SVD of
an `m x n` matrix (`m <= n`, thin) `4 m^2 n`; randomised SVD at rank `r` with `niter`
power iterations `2 m n r (2 niter + 3)`; `m x k` by `k x n` matmul `2 m k n`; `m x m`
Cholesky `m^3/3`; triangular solve with `n` right-hand sides `2 m^2 n`.

## 5. What a real calibration corpus must contain to avoid the confound

1. **Row-disjointness.** Calibration rows must come from sessions/documents that are
   disjoint from the message rows. Nested or overlapping prefixes make the "held-out"
   residual an in-sample residual.
2. **An independent target.** `E_cal` must be the embedding of the token the *consumer*
   needs, chosen by a rule that does not read the hidden state being mapped (the token
   that follows in the reference continuation, an external annotation, or a teacher
   forward pass). If `E_cal` is derived from the same forward-pass statistics that
   produced `H_cal` - which is exactly what happens when the target rows are the
   per-message embeddings of METHOD A - the fit absorbs the answer.
3. **Exchangeability with the message rows.** Same layer, same pre-norm predictor point,
   same dtype, same capture rule, same embedding matrix revision, same template and
   position distribution, same token-length regime. A frozen whitener is a
   second-moment statement; it transfers only between populations with the same
   second moments.
4. **Enough rows for the cross-covariance to be identifiable, not merely enough rows.**
   The binding quantity is the SNR of `sum_i h_i e_i^T`, which degrades with `d`. Report
   that number for the intended corpus (Section 3 shows how) rather than a row count.
5. **A real reference-token vocabulary.** Retrieval must be scored against the actual
   embedding table, and the scalar target norm must be `mean_embedding_norm` of that
   table (statebridge.py:87-113), never a statistic of the message rows.
6. **No target leakage through the basis.** If the calibration basis `Q` were computed
   from `[H_cal; E_cal]`, the map sees the target rows during fitting. That is legitimate
   for a frozen transform (it is the calibration set), but the message rows must never
   contribute to `Q`, or the comparison against METHOD A loses its meaning.
7. **Both axes, always.** Any acceptance test needs a hidden-state-sensitivity floor
   alongside the reconstruction ceiling, otherwise a target copier passes.

## 6. Verdict

### 6.1 The direct answer

**Yes, one frozen candidate generalises, and it is METHOD D.** Fitted on 512 independent
calibration rows and frozen, the dual ridge maps new hidden rows to a mean row cosine of
0.9207 against the norm-calibrated reference rows in the `strong` scenario (oracle ceiling
0.9412, i.e. 97.8% of it), with reference-token retrieval 1.0000 and a direction error of
0.3983 (oracle 0.3429). Its output changes by 1.2123 relative when the hidden rows are
replaced by unrelated rows of the same family, against 1.2068 for the oracle and 0.0005
for METHOD A. In the `mid` scenario the same configuration reaches 0.7631 row cosine
(94.1% of the 0.8109 oracle) at 1.1344 sensitivity (oracle 1.1264), and in `weak`
0.6150 (89.9% of the 0.6837 oracle) at 1.0135 (oracle 0.9864). The highest row cosine
among all non-oracle candidates whose hidden-state sensitivity is at least 1.0 is METHOD D in
every scenario that has a shared code (0.6150 `weak`, 0.7631 `mid`, 0.9207 `strong`), and in
`mid` and `strong` the top three are all METHOD D.

The second answer matters as much: **METHOD A is not an alignment.** It reproduces the
target rows (row cosine 0.9976 in `mid`, 0.9932 in `strong`, retrieval 1.0000) while its
output moves by 0.0001 when the hidden states are replaced - 0.01% of its own
norm, i.e. the float32 floor of the fit. On these fixtures METHOD A is numerically
indistinguishable from CONTROL F, the target copier: the pair (`cos`, `swap`) is
(0.9976, 0.0001) for A and (1.0000, 0.0000) for F. Any acceptance argument of the form
"the production bridge reconstructs the reference embeddings well" is therefore measuring
the interpolation property of Section 1.1, not the transfer of hidden-state information.

Third: the frozen baseline is strong enough that the question is not rhetorical. An
untrained affine copy, `mu_E + s (h - mu_H)` (CONTROL I), already reaches 0.5485 row
cosine in `mid` and 0.6148 in `strong`, because the fixture's shared component makes a
plain copy partially correct. The row mean, renormalised to the scalar target norm (which is what the
oracle reduces to in the `null` scenario), scores 0.4899. The informative comparison for every fitted candidate is therefore against
0.5485, not against 0.0.

### 6.2 The trade-off curve

Two axes, both measured, both required: how near the embedding manifold the output lands
(`row_cos`, `retr`, `rel_dir`) and how much of the output is actually driven by the hidden
rows (`swap_change`, with `swap_fid` checking that the change points at the true
difference). A method that wins the first axis alone is a copier; a method that wins the
second alone is noise.

| scenario | method | M | row_cos | swap_change | swap_fid | retr | rel_dir | norm_mean |
|---|---|---|---|---|---|---|---|---|
| mid | F | 128 | 1.0000 | 0.0000 | 0.000 | 1.0000 | 0.0000 | 1.012 |
| mid | A | 128 | 0.9976 | 0.0001 | 0.334 | 1.0000 | 0.0700 | 1.074 |
| mid | O | 128 | 0.8109 | 1.1264 | 0.741 | 1.0000 | 0.6149 | 1.000 |
| mid | D | 128 | 0.7411 | 1.0812 | 0.637 | 1.0000 | 0.7195 | 0.779 |
| mid | D | 512 | 0.7631 | 1.1344 | 0.668 | 1.0000 | 0.6884 | 0.823 |
| mid | D | 2048 | 0.7038 | 1.2016 | 0.595 | 1.0000 | 0.7697 | 0.920 |
| mid | C | 128 | 0.5942 | 1.2162 | 0.457 | 1.0000 | 0.9009 | 0.987 |
| mid | C | 512 | 0.5468 | 0.6942 | 0.282 | 0.9141 | 0.9520 | 0.563 |
| mid | C | 2048 | 0.5506 | 0.6572 | 0.288 | 0.9297 | 0.9481 | 0.552 |
| mid | G | 128 | 0.6244 | 1.0986 | 0.466 | 1.0000 | 0.8667 | 0.789 |
| mid | G | 512 | 0.5485 | 0.6355 | 0.285 | 0.8802 | 0.9503 | 0.549 |
| mid | B | 128 | 0.1764 | 1.3932 | 0.115 | 0.4922 | 1.2835 | 3.400 |
| mid | B | 512 | 0.5690 | 0.8802 | 0.342 | 0.9948 | 0.9284 | 0.627 |
| mid | B | 2048 | 0.5647 | 0.9517 | 0.342 | 0.9974 | 0.9330 | 0.660 |
| mid | I | 128 | 0.5485 | 0.6034 | 0.270 | 0.7448 | 0.9503 | 0.556 |
| strong | F | 128 | 1.0000 | 0.0000 | 0.000 | 1.0000 | 0.0000 | 1.012 |
| strong | A | 128 | 0.9932 | 0.0005 | 0.446 | 1.0000 | 0.1163 | 1.074 |
| strong | O | 128 | 0.9412 | 1.2068 | 0.922 | 1.0000 | 0.3429 | 1.000 |
| strong | D | 128 | 0.8972 | 1.1904 | 0.856 | 1.0000 | 0.4535 | 0.931 |
| strong | D | 512 | 0.9207 | 1.2123 | 0.887 | 1.0000 | 0.3983 | 0.952 |
| strong | D | 2048 | 0.8991 | 1.2281 | 0.858 | 1.0000 | 0.4492 | 0.982 |
| strong | C | 128 | 0.8348 | 1.2174 | 0.775 | 1.0000 | 0.5748 | 0.986 |
| strong | C | 512 | 0.6385 | 0.9131 | 0.469 | 1.0000 | 0.8502 | 0.643 |
| strong | C | 2048 | 0.7001 | 1.0007 | 0.570 | 0.9974 | 0.7744 | 0.692 |
| strong | G | 128 | 0.8417 | 1.1917 | 0.782 | 1.0000 | 0.5626 | 0.919 |
| strong | G | 512 | 0.6389 | 0.9000 | 0.470 | 1.0000 | 0.8499 | 0.636 |
| strong | B | 128 | 0.3484 | 1.3807 | 0.280 | 1.0000 | 1.1416 | 2.523 |
| strong | B | 512 | 0.6469 | 1.0371 | 0.488 | 1.0000 | 0.8404 | 0.721 |
| strong | B | 2048 | 0.7211 | 1.1207 | 0.608 | 0.7031 | 0.7468 | 0.804 |
| strong | I | 128 | 0.6148 | 0.8341 | 0.417 | 0.9922 | 0.8778 | 0.622 |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 128,512,2048 --seeds 0,1,2 --scenarios null,weak,mid,strong --lambdas 1e-4,1e-3,1e-2 --out reports/occ3-alignment-repair/analysis\calibration_results.json --tables reports/occ3-alignment-repair/analysis\tables`

Reading this as a curve, not a ranking:

- The corner `row_cos -> 1` at `swap_change -> 0` is occupied by CONTROL F and METHOD A.
  It is reachable only by copying the targets; it is not an operating point of an
  alignment.
- The upper-right region (`row_cos >= 0.89` and `swap_change >= 1.19` in `strong`) contains
  METHOD D at all three calibration sizes and the oracle. No other candidate enters it in
  any scenario.
- Every candidate with sensitivity above 1.0 and a landing below 0.70 buys its sensitivity
  with wrongness: METHOD C at `M=128` (0.8348 at `strong`, 0.5942 at `mid`) and METHOD B at
  `M=128` (0.3484, sensitivity 1.3807, output norm 2.52x the target) are sensitive in
  directions that do not track the reference rows, which is what `swap_fid` reports
  (0.7748 and 0.2801 against the oracle's 0.9219).
- The `null` scenario is the stress case where no shared code exists. There METHOD D falls
  *below* the mean predictor (row cosine 0.3899-0.3963 against 0.4899) while METHOD C and
  its oracle stay near it (C 0.4780-0.4876 for `M >= 512`, 0.2923 at `M=128`; oracle
  0.4831-0.4893). A frozen dual ridge is not safe when the
  corpus contains no learnable code; it must be able to fall back.

### 6.3 Where each candidate lands, and why

| candidate | best `row_cos` (non-oracle) | sensitivity | verdict |
|---|---|---|---|
| A production | 0.9976 (`mid`) | 0.0001 | not an alignment; interpolates the targets (Section 1.1) |
| B frozen whitened Procrustes | 0.7211 (`strong`, `M=2048`) | 1.1207 | dominated: 3.40x norm at `M=128`, and at `M=512` 0.0205 above the untrained copy |
| C affine orthogonal Procrustes | 0.8348 (`strong`, `M=128`) | 1.2174 | limited by the model class: its own oracle collapses at `kappa=0` (0.4978) |
| D dual ridge | 0.9207 (`strong`, `M=512`) | 1.2123 | generalises: 94-98% of the oracle ceiling with full hidden-state dependence |
| I affine copy (control) | 0.6148 (`strong`) | 0.8341 | the baseline every fitted candidate must beat |

Command: `python reports/occ3-alignment-repair/analysis/calibration_experiment.py --ms 128,512,2048 --seeds 0,1,2 --scenarios null,weak,mid,strong --lambdas 1e-4,1e-3,1e-2 --out reports/occ3-alignment-repair/analysis\calibration_results.json --tables reports/occ3-alignment-repair/analysis\tables` (best cell over the four scenarios and three calibration sizes).

METHOD B is disqualified by the mechanism of Section 2: its `ce^{1/2}` gain is fitted to
the calibration spectrum, and when the retained rank exceeds the calibration data rank the
ridge floor sets the gain instead. On the fixture the retained rank is 254 for `M=128`
against a data rank of 127, and the frozen map emits rows 3.40x the target norm with a row
cosine of 0.1764. See `INDEPENDENT-CALIBRATION.md`.

METHOD C is not disqualified by its estimator - at `M >= 512` it tracks its own oracle
closely (0.5468 against 0.5485 at `mid`/`M=512`, 0.6385 against 0.6389 at
`strong`/`M=512`) - but by its model class: one orthogonal map and one global scale cannot express a
per-direction signal-to-noise compromise, and at `kappa = 0` both C and its oracle fall to
the mean (0.4955 and 0.4978). See `ORTHOGONAL-PROCRUSTES.md`.

METHOD D is the only candidate whose model class matches the problem, and its weaknesses
are scale-related rather than structural: the regularisation constant must scale with the
calibration size, and more calibration rows make the frozen artifact large (112 MiB at
`M=2048`). See `DUAL-RIDGE-ALIGNMENT.md`.

### 6.4 What this study does not measure

- **The anchor blend is not modelled.** Production blends the aligned prefix 30% toward
  the nearest embedding row (SOURCE-ATTESTED, statebridge.py:115,155 applied at
  latent_worker.py:340). That step raises retrieval and lowers hidden-state sensitivity
  for every candidate, including A and D. Its effect is bounded and not measured here;
  a candidate's `swap_change` would shrink and its `retr` would rise.
- **No real corpus.** Every number above is from the synthetic generator of Section 3.
  Whether a real calibration corpus has the same cross-covariance SNR is the open
  question, and Section 5 items 1-7 are the conditions under which it could.
- **Linear candidates only.** No MLP, no per-head or per-layer map, no token-conditional
  model. A nonlinear map could beat the oracle `O` on this fixture, so the 94-98% figures
  are relative to a linear ceiling.

## 7. Reproduction

Environment: Python `extensions/sglang-occ3/.venv/Scripts/python.exe`, torch 2.8.0+cpu,
6 threads, CPU only. Worktree `C:/Users/openclawuser/optimus-occ3-latent-validation`,
branch `fix/occ3-statebridge-alignment-generalisation`. Nothing in `occ3_sglang/` or
`crates/` is modified; `statebridge.py` is imported unchanged.

| file | role |
|---|---|
| `analysis/calibration_methods.py` | the six candidates as pure fit/apply pairs, plus production parity helpers |
| `analysis/calibration_experiment.py` | generator, metrics, cost accounting, markdown tables, CLI |
| `analysis/run_all.py` | the main grid plus the eight sensitivity runs |
| `analysis/probe_in_sample_interpolation.py` | the Section 1.1 mechanism probe |
| `analysis/calibration_results.json` | every per-seed number quoted in the four reports |
| `analysis/sens/*.json` | `kappa` (0.0, 0.35, 0.7), rank cap (64, 128, 512), spectrum 1.2, lambda sweep |
| `analysis/tables/*.md` | the generated tables, each carrying its own command line |
| `extensions/sglang-occ3/tests/test_alignment_calibration.py` | candidate unit tests, 14 passed in 2.79 s |

File inventory, not a measurement table.

Commands, in order:

    python reports/occ3-alignment-repair/analysis/run_all.py
    python reports/occ3-alignment-repair/analysis/probe_in_sample_interpolation.py
    cd extensions/sglang-occ3 && .venv/Scripts/python.exe -m pytest tests/test_alignment_calibration.py -q

The test run must start from `extensions/sglang-occ3`, whose `conftest.py` puts the
extension package on `sys.path`; invoked from the repository root the same command fails to
import `occ3_sglang` (measured).

`run_all.py` writes the main grid and the eight sensitivity runs; total wall clock 414 s
on 6 CPU threads. Per-seed rows are retained so every mean in these reports can be
recomputed with a different aggregation.

## 8. Limits and threats to validity

1. **One generator family.** The fixture is a Gaussian factor model with lognormal row
   norms. Real hidden states have heavy-tailed coordinates, outliers and strong
   anisotropy across layers; a frozen second-moment map may behave differently there.
2. **`d=6144`, `r=64`.** The cross-covariance SNR depends on `d` and on the content rank,
   so the absolute numbers are specific to this shape. The ranking of the candidates is
   more robust than the levels: it held across four scenarios, four `kappa` values, three
   rank caps, two spectra and three seeds.
3. **Three seeds.** Means over seeds 0-2. Seed-to-seed spread is in the JSON; the ordering
   of the candidates is stable across seeds, the second decimal of `row_cos` is not.
4. **The oracles `O` and `G` use generator internals.** They bound linear candidates on
   *this* fixture. A different fixture would move them, and with them the percentages in
   Section 6.1.
5. **`M=2048` is not free.** METHOD D's frozen artifact at `M=2048` is 112 MiB
   (`2*M*d + M^2` floats), 18x the `M=128` artifact. The report quotes it per cell.
6. **No end-to-end consumer.** Retrieval top-1 against a 4224-row vocabulary is a proxy
   for "the receiver sees the right token". No downstream logprob or generation quality is
   measured here.
