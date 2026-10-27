# Production-scale degeneracy of the per-message StateBridge alignment

Status: OFFLINE MEASURED, production solver, production dimensions, no GPU
required. Date 2026-10-27. Branch `fix/occ3-statebridge-alignment-generalisation`
at `8c4517e65`.

## 1. The mechanism, stated exactly

Row-vector notation. `H` are the accepted sender hidden rows, `E` the input-token
embeddings of the same rows, `n x d` each, `n <= 128`, `d = 6144`.

`statebridge.py:32-70` computes:

```
mh, me = mean(H), mean(E)                       # 1 x d
hc, ec = H - mh, E - me
Q      = top-r right singular vectors of [hc; ec]        # d x r, r <= 2(n-1)
bh, be = hc Q, ec Q                                       # n x r
Ch     = bh^T bh / n + lambda I                           # r x r
Ce     = be^T be / n + lambda I
Xw     = bh Ch^-1/2                                       # whitened H
Yw     = be Ce^-1/2                                       # whitened E
R      = U V^T,  SVD(Xw^T Yw) = U S V^T                   # orthogonal Procrustes
Z      = (Xw R) Ce^1/2 Q^T + me                           # back to d dims
Z      = Z * target / ||Z||_row                           # norm calibration
```

The decisive step is `R`. Procrustes chooses, per message, the rotation that
minimises `||Xw R - Yw||`. It is not transporting the state; it is projecting
the state onto the reference embeddings of the very same rows.

Two consequences follow, and both are measured below:

1. **Content blindness.** When `Xw Xw^T = Yw Yw^T` (centred matrices of equal
   row-space rank, small ridge) a rotation mapping one exactly onto the other
   always exists, whatever `H` is. Then `Xw R = Yw`, so
   `Z = ec + me = E` up to the ridge residual. The hidden states contribute
   nothing except through their spectrum.
2. **Correspondence blindness.** Permuting the rows of `H` permutes `Xw` but
   leaves `Ch` and `Xw^T Yw` unchanged, so `R` is unchanged and the refitted
   product `Xw' R'` is again `Yw`. The output therefore returns the SAME `Z`
   rather than a permuted one: which hidden row corresponds to which token is
   invisible in the carrier.

## 2. Measurement at the shipped configuration

`n = 128`, `d = 6144`, `ridge = 1e-3`, `eigen_floor = 1e-6`, `rank_rtol = 1e-5`,
fixture `H = 0.24 E + 0.76 N(0,1)` (pair cosine 0.24, the measured raw value in
`reports/occ3/SGLANG-SOURCE-AUDIT.md:53`), seed 20261027, float32 CPU.

| quantity | value |
|---|---|
| union rank | 254 |
| effective rank of `Hc` | 124.45 |
| effective rank of `Ec` | 124.42 |
| Procrustes residual `||Xw R - Yw|| / ||Yw||` | 2.5e-05 |
| `cos(Z, normcal(E))` | 1.000000 |
| `||Z - normcal(E)|| / ||normcal(E)||` | 2.2e-05 |
| relative change when `H` rows are rolled by one | 3.1e-05 |
| relative change when `H` rows are permuted | 3.1e-05 |

Command:

```
cd <worktree>/extensions/sglang-occ3
.venv/Scripts/python.exe -m pytest tests/test_alignment_invariance.py tests/test_align_diagnostics.py -q
```

The measured carrier is the norm-calibrated reference embeddings of the sender's
selected tokens to within 2e-5, and the sender's hidden states move it by 3e-5.
That is not weak communication; it is no hidden-state communication at all.

## 3. Reconciliation with the earlier audit

`reports/occ3-statebridge-recovery/analysis/align_audit.json` reports a 1.41
relative change for row-shuffled `H` and 1.8% for unrelated random `H`. Those
are the same phenomenon seen through a different fixture, not a contradiction:

- With an anisotropic, rank-deficient `H` the two whitened Gram matrices do not
  match, the residual is large, and `Xw R` no longer equals `Yw`. The output
  then follows the row correspondence and a permutation of `H` shows up as a
  permutation of `Z`, so `||perm(Z) - Z|| / ||Z||` is about `sqrt(2) = 1.41`.
  It is a measure of how little `Z` resembles its own permutation, not evidence
  that `H` is transported.
- Unrelated iid `H` has a nearly flat spectrum that matches `E`'s, so the exact
  reconstruction regime applies again: `Z` moves only 1.8%.

Both regimes are degenerate. The difference is which failure mode dominates:
content blindness (matched spectra) or correspondence leakage (mismatched
spectra). Neither transports the state.

## 4. Why no knob fixes this

`ridge`, `eigen_floor`, `rank_rtol` and `alpha` all act inside the same
construction, in which `E` for the transmitted rows is an input to the fit.
Increasing ridge or lowering rank reduces the degeneracy only by making the
reconstruction worse; it cannot create a dependence on `H` that the fit
deliberately removes. `alpha` anchors towards the vocabulary rows nearest to
`Z`, which for `Z = E` are the sender's own tokens: it amplifies the token
channel, it does not add a state channel.

The only repair that can preserve `H` is to estimate the transform WITHOUT the
transmitted rows' `E`. That is the independent-calibration family
(`CALIBRATION-DESIGN.md`, `INDEPENDENT-CALIBRATION.md`,
`ORTHOGONAL-PROCRUSTES.md`, `DUAL-RIDGE-ALIGNMENT.md`).

## 5. What this does not prove

- It does not prove that a real GLM-5.3 `H` has the spectra used here. Gate 10
  (real statistics) needs the GPU window.
- It does not prove that a frozen transform will land near the embedding
  manifold. It only proves the current one cannot carry state information.
- It does not prove the receiver cannot read `Z = E`; it proves that reading
  `Z` is reading the sender's token embeddings, which is exactly the confound
  the campaign forbids counting as hidden-state communication.

## 6. Tests added

- `tests/test_alignment_invariance.py` (5 tests): output equals norm-calibrated
  `E`; row permutation of `H` does not change the output; unrelated `H` barely
  moves it; permuting `E` moves it completely; spectral mismatch weakens the
  degeneration. These pin the defect so a repair must update them deliberately.
- `tests/test_align_diagnostics.py` (10 tests): bounded scalar diagnostics.
- `occ3_sglang/align_diagnostics.py`: the measurement instrument. Fixed key
  set, int/float scalars only, no temporary larger than `2 n d`, no tensor
  leaves the call. Not wired into the worker yet.
