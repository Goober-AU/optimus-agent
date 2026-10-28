# OCC-3 StateBridge alignment repair — live window 1 (2026-10-28)

Status: **measurement window complete, production restored, verdict INCONCLUSIVE by the
pre-registered rule.** No claim of semantic success is made anywhere in this report.

- Campaign: OCC-3 StateBridge mathematical repair, branch
  `fix/occ3-statebridge-alignment-generalisation`.
- Baseline: merged PR #204, main `8c4517e659b4fa07fc8dab7444f810df97893a70`.
- Host: DGX `TRY-72998-gpu01`, 8x B300 SXM6, GLM-5.3 FP8, SGLang 0.5.21, TP8.
- Window: 2026-10-27T23:45Z to 2026-10-28T02:15Z. Production `neo-glm53` was stopped for
  the window and restored with `docker start` at the end.

## 1. Question this window answers

The shipped solver builds the transmitted prefix from the same rows it transmits. The
proof in `MATHEMATICAL-DEGENERACY.md` says that construction makes the prefix an
in-sample copier of the target embedding cloud. If that is true on the real model, the
receiver receives the token embeddings it could have computed itself, and no amount of
ridge, rank or anchoring tuning can add sender information.

Two numbers decide whether a calibrated replacement is worth building:

1. `cos(prefix, embedding)` — how close the shipped prefix is to the embedding copier.
2. `heldout_r2_within_turn` — how much of the next token's input embedding is
   predictable from the pre-norm hidden state at the previous position, fitted on half
   the rows of the turn and scored on the other half.

## 2. What was actually measured

Three real capture turns, ordinary English prompts, full source-to-receiver handoff,
all scalars emitted from inside the worker by `occ3_sglang/calibration_probe.py`.
Pairs are (final pre-norm hidden state at position t, input embedding of the token at
t+1), which is the `predictor_position_offset = -1` convention of the shipped metadata.

| turn | rows | union rank | hidden eff rank | embedding eff rank | held-out R2 | cos(prefix, E) | rel(prefix, E) | Procrustes residual | roll sens. | permute sens. | mean |h| | mean |e| | mean |prefix| |
|------|------|-----------|-----------------|--------------------|-------------|----------------|----------------|---------------------|------------|--------------|---------|---------|----------------|
| 1 | 103 | 169 | 26.62 | 30.36 | 0.1011 | 0.9314 | 0.4933 | 0.9295 | 0.4790 | 0.4833 | 159.4 | 0.553 | 0.699 |
| 2 | 102 | 168 | 27.81 | 29.11 | 0.1305 | 0.9369 | 0.4586 | 0.9069 | 0.4714 | 0.4652 | 159.2 | 0.566 | 0.699 |
| 3 | 128 | 201 | 29.96 | 33.87 | 0.0675 | 0.9167 | 0.4996 | 1.0671 | 0.5362 | 0.5361 | 162.4 | 0.574 | 0.699 |

Canonical decile medians (1 = strongest):
`0.9491, 0.9044, 0.8331, 0.6205, 0.07341, 1.468e-07, 3.172e-08, 1.267e-09, 3.691e-10, 1.324e-10`

## 3. Findings

### 3.1 The shipped prefix is close to the embedding copier, not equal to it

Median `cos(prefix, E) = 0.931` with row-normalised targets. That is far from the exact
`1.000000` that the full-rank proof forces, and the reason is visible in the same table:
the union subspace has rank 168-201 for 102-128 rows, so the rank-relative truncation and
the finite ridge remove part of the copy. The transform therefore keeps a real dependence
on the sender's hidden rows: re-fitting on a one-row roll of `H` moves the prefix by
`0.47-0.54` of its norm.

Both statements are true at once, and both matter:

- The prefix is *mostly* the target embedding cloud (0.93 cosine), so most of the
  transmitted energy is information the receiver already has.
- The prefix is *not exactly* that cloud, so a residual hidden-state dependence survives.
  The semantic result below shows that this residual does not carry usable state.

### 3.2 The real hidden-to-embedding relation is weak on held-out rows

Median within-turn held-out `R2 = 0.101` (range 0.067 to 0.131) against a mean predictor
of 0. This is the first measured value of this quantity on the production model. It is
above zero, so the relation exists, and it is far below the 0.30 the calibrated method
needs to justify a 24-96 MiB artifact.

The measurement is a **pessimistic bound**, and the report says so instead of dressing it
up: with 102-128 rows and a 0.5 split, the fit may use at most ~51 directions of 6144.
A pooled calibration over many turns (M = 512-2048) has strictly more capacity. The
window therefore cannot rule the calibrated method out, and it does not support it either.

### 3.3 The canonical spectrum is uninformative here, as predicted

`canonical_top1 = 0.98` and `canonical_top8 = 0.97` look like strong evidence of a
relation, and they are not: the whitened clouds are congruent for any full-rank data, so
the spectrum saturates even for independent rows. The decile medians show where the
congruence actually dies, at decile 5 (`0.073` then `1.5e-07`), so the effective
congruence is about 40 directions. This confirms the test-level finding in
`tests/test_calibration_probe.py` on real data.

### 3.4 Semantic outcome of the shipped path: 0 of 3

| case | source state | handoff admitted | receiver | answer class | answer match | nonce match |
|------|--------------|------------------|----------|--------------|--------------|-------------|
| w9-case-00 | Captured | yes | answered, state absent | UNKNOWN | false | false |
| w9-case-01 | Captured | yes | answered, state absent | UNKNOWN | false | false |
| w9-case-02 | Captured | no | uncertain | OTHER | false | false |

The handoff, receipt and one-shot delivery machinery worked. The receiver produced an
answer for every turn and never produced the expected answer or the expected nonce. This
is consistent with 3.1 and 3.2: the pipe is intact, the payload is not informative.

## 4. Verdict

Pre-registered rule (`analyze_probe.py`, fixed before the window):

- FEASIBLE when median held-out R2 >= 0.30 and median cos <= 0.30,
- NOT-FEASIBLE when median held-out R2 < 0.10,
- otherwise INCONCLUSIVE.

Result: **INCONCLUSIVE**, median R2 `0.1011`, median cos `0.9314`. The R2 median sits
just above the 0.10 floor of the dead band, and the cosine bound fails by a wide margin,
because the shipped method is a near-copier by construction.

Machine-readable verdict: `live-window-1/align-probe.verdict.json`.
Per-turn scalars: `live-window-1/align-probe.jsonl` (26 keys per line, no tensor, no token
id, no text).

## 5. What the next window must do

1. Capture **pooled** calibration rows across many turns in one runtime, so the fit can
   use 512-2048 rows instead of 51. The calibrated method needs that capacity; this
   window cannot decide without it.
2. Report held-out R2 with a turn-disjoint split, which is the only honest estimate of
   generalisation and the number the ship gate uses (`MIN_HELDOUT_R2 = 0.10`).
3. Keep the copier diagnostic as a control: any candidate whose `cos(prefix, E)` stays
   above 0.9 is not a candidate.

## 6. Reproducibility of the environment (hard-won, needed by anyone repeating this)

1. `SGLANG_USE_PICKLE_IPC=0` is **mandatory**. Without it the patched scheduler refuses
   every attached request with `occ3_unsupported_profile` before prefill, the adapter
   reports `occ3_inference_uncertain`, and the client sees HTTP 409. The previous window
   container had it; the first containers in this window did not.
2. The extension is imported from
   `/opt/sglang/lib/python3.12/site-packages/occ3_sglang`, **not** from
   `/opt/occ3-bridge/occ3_sglang`. Mounting the latter changes nothing.
3. All modules of `occ3_sglang` are digest-attested at install. Editing any listed module
   (including `latent_worker.py`) or patching a pinned SGLang file such as
   `managers/scheduler.py` declines the install with `source_digest_mismatch` or
   `experimental_startup_refused`. Window instrumentation must live in an unattested
   file: this window used `zz_occ3_probe.pth` plus `occ3_probe_hook.py` in
   site-packages, which wraps `latent_worker.align_hidden_sequence` after import and
   returns the original result unchanged.
4. Restore is `docker stop <candidate>` then `docker start neo-glm53`. The original
   container keeps its configuration: image `sha256:b1259f3ea327...`, no binds, restart
   policy `no`, `python3` entrypoint.

## 7. Limitations

- Three turns, one model, one layer (77), one dtype (FP8), one parallelism (TP8).
- Within-turn split only; no turn-disjoint generalisation estimate yet.
- No frozen semantic task set was run, so the 0/3 result is a diagnostic, not a
  benchmark.
- The probe measures the relation at the fitted rank, not the full 6144-dimensional
  optimum.
