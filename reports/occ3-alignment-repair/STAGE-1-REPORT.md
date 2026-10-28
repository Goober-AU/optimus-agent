# Stage 1 report: the alignment cannot carry state, and why

Branch `fix/occ3-statebridge-alignment-generalisation`, base `8c4517e65`
(merged PR #204). All numbers below are offline measurements on this host with
the production solver; none is a GLM-5.3 measurement.

## 1. Five findings

**F1. The current carrier is the reference token embeddings, not the state.**
At n=128, d=6144, ridge=1e-3, seed 20261027: `cos(Z, normcal E) = 1.000000`,
`||Z - normcal E||/||normcal E|| = 2.2e-05`, and the hidden states move the
output by `3.1e-05`. This holds with `H` fully independent of `E` (pair cosine
0) as well as at cosine 0.24 and 0.90. Permuting the hidden rows does not even
permute the output.

**F2. The mechanism is structural, not a port defect.** Procrustes picks, per
message, the rotation that minimises `||Xw R - Yw||`, where `Yw` is the whitened
reference embedding of the same rows. The map is therefore *fitted to the
answer*: for centred matrices of equal row-space rank the whitened Gram
matrices coincide, `Xw R = Yw` exactly, and `Z = E`. Ridge only perturbs this.
See `PRODUCTION-SCALE-DEGENERACY.md` for the derivation and
`tests/test_alignment_invariance.py` for the pinned behaviour.

**F3. The reference implementation has the same property.** StateBridge fits on
the same rows it transmits (`state_bridge.py:591-604`, `:607`, `:613`), with no
calibration set anywhere in the repository. Optimus inherited the in-sample
property; it did not introduce it. LatentMesh is the only reference with a
fit/apply separation. See `EXACT-EQUATIONS.md`.

**F4. The shipped solver is the reference operator.** The reduced-subspace
solver is the Galerkin restriction of the full-space one (`ch = q^T C_H q` to
3e-7), agrees with a full-space transcription to 3e-6..6e-6, and a statement by
statement transcription reproduces the shipped function bit-identically. There
is nothing to fix in the port itself.

**F5. Every frozen alternative buys state dependence at the cost of manifold
accuracy.** Preliminary sweep, `analysis/calibration_experiment.py`, M=512
calibration rows, two seeds, `rel_recon` = error to norm-calibrated `E`,
`swap` = relative change when the message state is replaced:

| method | rel_recon (median) | swap (median) | runs | meaning |
|---|---|---|---|---|
| A, current per-message | 0.072 | 0.0001 | 12 | on-manifold, no state |
| F, embeddings only | 0.000 | 0.0000 | 12 | the confound ceiling |
| B, frozen whitened | 1.030 | 0.980 | 36 | state survives, far off-manifold |
| C, frozen affine orthogonal | 0.973 | 0.794 | 36 | state survives, far off-manifold |
| D, frozen dual ridge | 0.823 | 1.128 | 108 | state survives, off-manifold |
| G, identity blend | 0.976 | 0.597 | 36 | state survives, off-manifold |

Medians over every scenario, seed, calibration size and ridge in
`analysis/calibration_results.json` (264 runs, `analysis/calibration_experiment.py`).

No frozen linear method dominates. The trade-off is not a tuning gap: the
manifold constraint *is* the `E`-reconstruction constraint that destroys state
dependence.

## 2. The structural argument

The receiver's input space is reached through the input embedding matrix, so a
prefix row is readable to the extent it looks like something the model expects
there. The current method guarantees that by construction (it outputs `E`), at
the exact cost of carrying no state. Any method that preserves state produces
rows the model has not been trained to read at those positions.

Two ways out, and only two:

1. **Stay on the manifold and encode state as a token mixture.** Project the
   state through the model's own frozen LM head into a soft token distribution
   and take the corresponding convex combination of embedding rows. That vector
   is exactly what the model itself would compute for a soft token, so it is
   readable by construction, and it is state-derived. This is Candidate D of the
   brief, which the brief restricts to a diagnostic unless the method scope is
   extended. It needs one GPU measurement to know whether it works.
2. **Leave the manifold and rely on the receiver attending to the perturbation.**
   Cheap to test (Candidate G), and its failure would be informative: it would
   prove a map onto the embedding manifold is mandatory.

## 3. What is blocked

Everything that would decide between those two needs real GLM-5.3 states:

- Real spectra of `H` and `E` (Gate 10). The synthetic fixtures *build in* the
  `H`-`E` relation at cosine 0.24, so no offline result can show that a real
  relation exists to be found.
- Whether the receiver reads any prefix at all under the framed layout (Gate 11).
- Whether state dependence survives at the receiver (Gate 18/19).

None of this can be measured without a supervised window with the candidate
image, and the production service must be restored afterwards.

## 4. The window I am asking for

One authorized swap, automatic restoration, in this order:

1. Framing arms L0/L1/L3 with positive, matched-shape null and no-attachment
   controls. Decides whether the receiver sees the prefix at all.
2. Candidate G sweep, `beta` in {0, 0.05, 0.1, 0.2}. Decides whether the
   receiver can read state that is off the embedding manifold.
3. `EXPERIMENT-PLAN.md` stage 3 task sets and the first-token margin.

Stage 4 (frozen calibration maps B/C/D) needs a second window, because it first
needs a calibration capture run under the same image.

## 5. What this stage does not claim

- No semantic transfer has been demonstrated.
- No benchmark has been run.
- The Rust harness change from PR #204 was never compiled on this host, and this
  stage did not compile it either.
- Candidate D is out of the approved method scope until the user says otherwise.
