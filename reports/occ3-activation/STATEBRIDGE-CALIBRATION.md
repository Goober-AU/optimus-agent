# StateBridge calibration: the independent dual-ridge method

## 1. The defect in the shipped method

The shipped per-message solver fits a whitened Procrustes map on the message rows `H_msg` and
applies it to those same rows. In the high-dimensional, low-sample regime `N << D` that construction
reproduces the reference embedding cloud: the prefix becomes an in-sample copier of the target
embeddings and the residual dependence on the sender's hidden rows carries no usable state.

Measured on the real GLM-5.3 FP8 TP8 runtime in the previous window (PR #205,
`reports/occ3-alignment-repair/LIVE-WINDOW-1.md`): median `cos(prefix, embedding) = 0.931`,
median within-turn held-out `R2 = 0.101`, verdict INCONCLUSIVE, 0 of 3 diagnostic semantic trials
recovered. That evidence is carried forward, not re-derived here.

## 2. The calibrated method

`occ3_sglang/statebridge_calibrated.py`, fitted once on independent calibration turns and frozen:

```
mu_H = mean(H_cal)          mu_E = mean(E_cal)
A = H_cal - mu_H            B = E_cal - mu_E
K = A A^T                   lambda = ridge_rel * mean(diag(K))
Q = (H_msg - mu_H) A^T
W (K + lambda I) = Q
Z_msg = mu_E + W B
```

The solve is an `M x M` Cholesky solve. No explicit inverse, no `D x D` covariance, no refit per
message. `apply_calibration` is applied only to rows that were never part of the fit.

Method identity: `statebridge.glm53.final-prenorm-predictor.calibrated-dual-ridge.v1`. The
per-message solver keeps its own identity. A calibrated artifact is refused when presented under
the per-message method, and the profile validator refuses any method id the registry does not
advertise.

## 3. Registration and dispatch

`native_profile._METHODS` now holds both methods with their own descriptors: representation,
alignment, fit source, solver, predictor position offset, numerical precision and whether a frozen
artifact is required. `advertised_methods()`, `method_descriptor()`, `require_method()`,
`receipt_descriptor()` and `validate_method_binding()` all agree.

`latent_types.RuntimeProfile.method` defaults to the per-message id, so every existing caller is
unchanged, and accepts any advertised id. A calibrated profile carries a frozen-artifact reference
(`calibration_path`, `calibration_digest`) that is all-or-nothing, absolute, and refused on the
per-message method.

`occ3_sglang/alignment_dispatch.py` is the dispatch seam. `aligner_for(profile, artifact)` returns a
callable; the per-message method returns the shipped solver, the calibrated method returns
`apply_calibration`. The calibrated branch refuses with a named code when the artifact is missing,
when its method id differs from the profile, when its hidden dimension differs, or when its digest
does not match. **There is no fallback branch**: a calibrated refusal can never silently produce
per-message output.

`latent_worker.Worker.align` resolves the aligner once and calls it at the single existing call
site. Capture metadata now reports the method id, and for a calibrated capture also the artifact
digest. Representation, predictor position offset, layer index and hidden dimension are unchanged.

## 4. Turn-disjoint evaluation

`calibration_corpus.py` builds a calibration corpus from whole turns and evaluates on whole turns
that were never in the fit:

- `split_turns` partitions by turn, never by position, so adjacent positions of one generation can
  never straddle the boundary;
- `select_calibration_rows` supports M = 512, 1024 and 2048 whole-turn row budgets and reports which
  was used;
- `evaluate_turn_disjoint` reports held-out R2, relative reconstruction error, cosine to the actual
  targets, refit / hidden-roll / hidden-permute sensitivity, independent-reference sensitivity and
  cosine, token-reference cosine, corpus digest, artifact digest and control separation;
- it refuses to report quality below `MIN_EVALUATION_TURNS = 4`, and fails closed on a no-signal
  corpus, matching `fit_calibration`'s `min_heldout_r2` guard.

## 5. What is still not measured

Every calibrated number produced so far comes from synthetic fixtures, not from a GLM-5.3 capture.
The method is registered, dispatchable, integrity-bound and evaluable; it has not been run against
the real model in this session. The live gate is unchanged and still open:

| Gate | Threshold | Live status |
|---|---|---|
| median turn-disjoint held-out R2 | >= 0.30 feasible | not measured with pooled calibration |
| median `cos(prefix, embedding)` | <= 0.30 | 0.931 with the shipped method |
| semantic recovery | correct state beats matched controls | 0 of 3 in the previous window |

A synthetic fixture at dim 32, rank 8 gives held-out `R2 = 0.833` for the calibrated map against
`0.0` for the mean predictor. That number demonstrates the implementation works on data with signal.
It says nothing about GLM-5.3, and it is reported here only as an implementation check.
