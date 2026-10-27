# Calibrated dual-ridge method: the repair candidate

Status: OFFLINE MEASURED. Module `occ3_sglang/statebridge_calibrated.py`,
tests `tests/test_statebridge_calibrated.py` (8 passed). Not wired into the
worker; the shipped per-message method is unchanged.

## 1. What it replaces

The shipped per-message fit chooses, per message, the rotation that maps the
hidden rows onto the reference embeddings of the *same* rows, so it returns
those embeddings and discards the state (measured: `cos(Z, normcal E) =
1.000000`, state sensitivity `3e-05`).

The calibrated method fixes the transform before the message arrives:

```
A = H_cal - mean(H_cal)                       M x d     calibration rows only
B = E_cal - mean(E_cal)                       M x d
K = (H_msg - mean(H_cal)) A^T                 N x M
Z = mean(E_cal) + (K (A A^T + lambda I)^-1) B          N x d
```

The M x M solve is a Cholesky solve. No d x d matrix, no explicit inverse. The
artifact holds `A`, `B`, the Gram, and the two calibration means: 25 MiB at
M=512, d=6144 in float32, which fits the 256 MiB live-byte budget without
changing it.

## 2. Measured behaviour

Fixture: latent-factor model with a shared factor of controllable strength
`kappa`; calibration and message rows are drawn independently through the same
model, never from the same rows. Message rows n=64, calibration rows M=512.

| hidden dim | kappa | held-out R^2 | calibrated (cos to E, state sensitivity) | shipped (cos to E, state sensitivity) |
|---|---|---|---|---|
| 256 | 0.15 | refused | - | 1.000, 0.0001 |
| 256 | 0.35 | refused | - | 1.000, 0.0002 |
| 256 | 0.75 | 0.416 | **0.739, 1.422** | 1.000, 0.0004 |
| 512 | 0.75 | 0.394 | **0.687, 1.412** | 1.000, 0.0002 |

Read the two columns as the trade the campaign is about. The shipped method is
a perfect copy of the embeddings with no state dependence. The calibrated
method lands at 0.69 to 0.74 row cosine to the true embeddings of rows it never
saw, *and* keeps full state dependence (replacing the message state moves the
output by 1.41 relative, i.e. about as far as two independent vectors can be).

At weak signal it refuses instead of degrading quietly: held-out R^2 below 0.10
raises `LatentError("numerical")`. That is a design property, not a limitation
to work around: a corpus without a real hidden-to-embedding relation must not
produce a prefix.

## 3. The ridge must be relative, and that is a finding

The first implementation used the shipped constant `ridge = 1e-3`. It overfits:
on a d=512 corpus whose centred row energy is 527, held-out R^2 is -0.37 at
ridge 1e-3 and +0.65 at ridge 100. The Gram entries scale with the hidden width,
so a constant cannot serve d=256 and d=6144.

The module therefore takes `ridge_rel` and sets `ridge = ridge_rel *
mean(diag(A A^T))`, default 0.02. This is a different kind of parameter from
the shipped per-message ridge and must be part of the calibrated method
identity, not a default hidden in code.

## 4. Residual weaknesses, stated

- M=2048 is worse than M=512 in the independent harness (0.704 vs 0.763 row
  cosine), and the ridge explains only part of it. Calibration size must be
  fixed by measurement per model, not by "more is better".
- The method is linear. It cannot represent a relation that is not linear in the
  hidden rows.
- The artifact is fitted to one model fingerprint and one layer. A different
  checkpoint or layer invalidates it; the digest must be pinned in the profile.
- Everything here is synthetic. The real question, whether GLM-5.3 pre-norm
  predictor states predict the embeddings of the tokens they produce, is
  unmeasured. The method is only usable if that relation exists; the guard is
  what detects its absence.

## 5. Integration plan (not implemented, needs a compiled host)

1. Method identity: add
   `statebridge.glm53.final-prenorm-predictor.calibrated-dual-ridge.v1` as an
   allowed value in the native profile validator and in the Rust identity
   checks. This is an incompatible representation change, so it is a new method
   id, never a silent substitution.
2. Artifact: fit on the DGX from captured calibration turns, write it with a
   content digest, pin `calibration_digest` and `ridge_rel` in the profile. The
   profile build must refuse an artifact whose held-out R^2 is below the floor.
3. Worker: load the artifact once per runtime, keyed by `(model_fingerprint,
   layer_index)`; count its bytes against `max_live_bytes`; in `align()` call
   `apply_calibration` when the profile method is the calibrated one, and keep
   `align_hidden_sequence` untouched for the old method.
4. Admission: refuse a record whose `hidden_dim` differs from the artifact, and
   refuse a runtime whose artifact digest is missing or mismatched. One-shot
   delivery, expiry and replay rules are unchanged.
5. Receipts: add `calibration_digest`, `ridge_rel` and `heldout_r2` to the
   source receipt. This is a two-sided schema change (Python exact-set parse,
   Rust `deny_unknown_fields`) and needs its own version bump.
6. Tests to add before any live use: artifact round-trip, apply parity against
   the offline harness, refusal on a stale digest, refusal on a dimension
   mismatch, and a memory-bound check that the artifact plus the alignment
   workspace stay under the profile cap on all ranks.

Nothing in step 1-5 can be verified on this host: there is no Rust toolchain
here, so `cargo check` cannot run.
