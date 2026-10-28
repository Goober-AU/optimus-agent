# CURRENT METHOD AUDIT: what Optimus actually implements

Scope: the alignment operator shipped in `extensions/sglang-occ3/occ3_sglang/statebridge.py`,
its call site in `latent_worker.py`, and a precise, materiality-labelled comparison
against Reference A. Facts and equations only; no fixes proposed.

Worktree `C:/Users/openclawuser/optimus-occ3-latent-validation`, branch
`fix/occ3-statebridge-alignment-generalisation`. All line numbers are from the
checked-out files at the time of writing.

Labels: SOURCE-ATTESTED (read from file:line), OFFLINE MEASURED (command stated),
HYPOTHESIS (interpretation not asserted by the source).

## 0. Notation

One row is one observation. Rows of `hidden` are the sender's captured hidden states;
rows of `embeddings` are the input-embedding rows of the **same** token positions.

| Symbol | Shape | Meaning |
|---|---|---|
| `n` | scalar | `hidden.shape[0]`, the retained row count |
| `d` | scalar | `hidden.shape[1]` = `hidden_dim` = 6144 |
| `H` | `n x d` | sender hidden rows |
| `E` | `n x d` | embedding rows, `E[i] = W_emb[token_ids[i]]` |
| `mu_h`, `mu_e` | `1 x d` | row means |
| `hc`, `ec` | `n x d` | centred rows |
| `sigma` | `min(2n, d)` | singular values of the stacked centred matrix |
| `theta` | scalar | rank threshold |
| `r` | scalar | `union_rank`, retained subspace dimension |
| `q` | `d x r` | orthonormal basis of the union subspace |
| `bh`, `be` | `n x r` | centred rows in the `q` basis |
| `ch`, `ce` | `r x r` | ridge-regularised reduced covariance |
| `x`, `y` | `n x r` | whitened `bh`, `be` |
| `u`, `v` | `r x r`, `r x r` | SVD factors of `x^T y`; `v` holds `V^T` |
| `R` | `r x r` | `u @ v`, the Procrustes rotation |
| `z` | `n x d` | reconstructed rows in embedding space |
| `tau` | scalar | `target_norm`, global mean embedding-row norm |
| `alpha` | scalar | `anchor_alpha`, vocabulary anchoring ratio |
| `e_j` | `1 x d` | row `j` of the embedding matrix |

`M^{+p}` is the symmetric matrix power defined in section 1.3.

## 1. The implemented equations, statement by statement

Function `align_hidden_sequence(hidden, embeddings, target_norm, *, ridge=1e-3,
eigen_floor=1e-6, rank_rtol=1e-5)`, `statebridge.py:32-33`.

### 1.1 Preconditions

`statebridge.py:41-47`

```python
require(hidden.ndim == 2 and hidden.shape == embeddings.shape, "profile")
n, d = hidden.shape
require(2 <= n <= 128 and d > 2 * n and ridge > 0 and eigen_floor > 0, "profile")
require(hidden.device == embeddings.device, "profile")
with torch.no_grad():
    h, e = hidden.float(), embeddings.float()
    require(bool(torch.isfinite(h).all()) and bool(torch.isfinite(e).all()), "numerical")
```

$$ 2 \le n \le 128, \qquad d > 2n, \qquad \rho > 0, \qquad \varepsilon > 0 $$

`d > 2n` is never binding at `d = 6144` and `n <= 128`. SOURCE-ATTESTED.

### 1.2 Centring

`statebridge.py:48-49`

```python
mh, me = h.mean(0, keepdim=True), e.mean(0, keepdim=True)
hc, ec = h - mh, e - me
```

$$ \mu_h = \tfrac{1}{n}\mathbf{1}_n^{\top} H, \qquad \mu_e = \tfrac{1}{n}\mathbf{1}_n^{\top} E $$
$$ h_c = H - \mathbf{1}_n \mu_h, \qquad e_c = E - \mathbf{1}_n \mu_e $$

### 1.3 Symmetric power

`statebridge.py:26-29`

```python
def _symmetric_power(matrix, power, floor):
    torch = _torch()
    vals, vectors = torch.linalg.eigh((matrix + matrix.mT) * 0.5)
    return (vectors * vals.clamp_min(floor).pow(power)) @ vectors.mT
```

$$ M^{+p} := V \operatorname{diag}\!\left(\max(\lambda_i, \varepsilon)^{p}\right) V^{\top}, \qquad M = V \operatorname{diag}(\lambda) V^{\top} $$

The floor is applied before the power, so negative powers are bounded.
`(vectors * vals)` broadcasts `vals` over the last axis, i.e. scales columns of `V`.
Numerically identical to Reference A's `_sym_power`
(`references/StateBridge/methods/state_bridge.py:247-252`), which writes
`evals.unsqueeze(0)` for the same broadcast. SOURCE-ATTESTED.

### 1.4 Union subspace and rank selection

`statebridge.py:50-55`

```python
_, singular, vh = torch.linalg.svd(torch.cat((hc, ec), dim=0), full_matrices=False)
require(bool(torch.isfinite(singular).all()) and float(singular[0]) > 0, "numerical")
threshold = max(float(singular[0]) * rank_rtol, eigen_floor)
rank = min(int((singular > threshold).sum()), 2 * (n - 1))
require(rank > 0, "numerical")
q = vh[:rank].mT
```

$$ \begin{bmatrix} h_c \\ e_c \end{bmatrix} = U \Sigma V^{\top}, \qquad \Sigma = \operatorname{diag}(\sigma_1, \dots) $$
$$ \theta = \max(\sigma_1 \cdot \texttt{rank\_rtol},\ \varepsilon) $$
$$ r = \min\bigl(\bigl|\{\, i : \sigma_i > \theta \,\}\bigr|,\ 2(n-1)\bigr) $$
$$ q = V_{:,\,1:r} \in \mathbb{R}^{d \times r}, \qquad q^{\top} q = I_r $$

The cap `2(n-1)` is exact, not heuristic: each block is centred, so
`rank(hc) <= n-1` and `rank([hc; ec]) <= 2(n-1)`. SOURCE-ATTESTED (the code asserts
the number; the rank reasoning is HYPOTHESIS but standard).

### 1.5 Reduced covariance with ridge

`statebridge.py:56-59`

```python
bh, be = hc @ q, ec @ q
eye = torch.eye(rank, dtype=h.dtype, device=h.device)
ch = bh.mT @ bh / n + ridge * eye
ce = be.mT @ be / n + ridge * eye
```

$$ b_h = h_c q, \qquad b_e = e_c q $$
$$ c_h = \tfrac{1}{n} b_h^{\top} b_h + \rho I_r, \qquad c_e = \tfrac{1}{n} b_e^{\top} b_e + \rho I_r $$

These are the Galerkin restrictions of Reference A's full-space covariances:
`c_h = q^T C_H q` and `c_e = q^T C_E q`, with `C_H = hc^T hc / n + rho I_d`
(measured in section 5.1 to float32 roundoff).

### 1.6 Whitening

`statebridge.py:60-61`

```python
x = bh @ _symmetric_power(ch, -0.5, eigen_floor)
y = be @ _symmetric_power(ce, -0.5, eigen_floor)
```

$$ x = b_h\, c_h^{-1/2}, \qquad y = b_e\, c_e^{-1/2} $$

### 1.7 Orthogonal Procrustes, without determinant handling

`statebridge.py:62`

```python
u, _, v = torch.linalg.svd(x.mT @ y, full_matrices=False)
```

$$ x^{\top} y = U' \Sigma' V'^{\top}, \qquad R' = U' V'^{\top} $$

There is **no determinant correction anywhere in the file**: no call to
`torch.det`, no sign flip, no branch. The returned factor is whatever `U' V'^T` is,
including improper rotations with `det = -1`. The docstring acknowledges this at
`statebridge.py:36-38`:

```python
"""Thin union-subspace Procrustes with identity completion outside union.

The full proper rotation uses an unused complement sign when necessary.
That sign cannot change the observed centered rows. Degenerate zero-info
messages refuse instead of yielding a fabricated carrier.
"""
```

Two claims in that docstring do not match the body:
- "identity completion outside union": the body never adds back the component of
  `h_c` orthogonal to `q`. It computes `bh = hc @ q` (line 56) and returns
  `(...) @ q.mT` (line 63), so any component of `h_c` in the orthogonal complement of
  `span(q)` is dropped, not carried through unchanged. Measured magnitude in 5.1.
- "proper rotation": no properness constraint is enforced (line 62).

SOURCE-ATTESTED for both statements; the measurement in 5.1 bounds the first.

### 1.8 Reconstruction

`statebridge.py:63`

```python
z = (x @ (u @ v) @ _symmetric_power(ce, 0.5, eigen_floor)) @ q.mT + me
```

$$ Z = \Bigl(x\, R'\, c_e^{+1/2}\Bigr)\, q^{\top} + \mathbf{1}_n \mu_e $$

Expanded, this is

$$ Z = \bigl(h_c q\bigr) c_h^{-1/2} R' c_e^{+1/2} q^{\top} + \mathbf{1}_n \mu_e $$

so the operator is the Reference A operator conjugated by
`P = q q^T` (HYPOTHESIS; confirmed numerically in 5.1).

### 1.9 Norm calibration

`statebridge.py:64-68`

```python
norms = z.norm(dim=-1, keepdim=True)
target = torch.as_tensor(target_norm, device=z.device, dtype=z.dtype)
require(target.numel() == 1 and bool(torch.isfinite(target)) and float(target) > 0, "numerical")
require(bool(torch.isfinite(z).all()) and bool((norms > eigen_floor).all()), "numerical")
z = z * (target / norms)
```

$$ \hat{Z}_i = \tau\, \frac{Z_i}{\lVert Z_i \rVert_2}, \qquad \tau = \texttt{target\_norm} $$

The denominator is not clamped; instead the code *refuses* when any
`||Z_i|| <= eigen_floor` (line 67). Reference A clamps with `min=1e-6` and continues
(`references/StateBridge/methods/state_bridge.py:285`). Different failure policy, same
formula on the non-degenerate branch.

`tau` itself is computed by `mean_embedding_norm`, `statebridge.py:87-112`:

$$ \tau = \frac{\sum_{\text{tiles}} \sum_{i} \lVert W_{\text{emb}}[i] \rVert_2}{\#\{\text{rows counted}\}} $$

accumulated in `float64` (line 92) over the non-padded local shard ranges
(`shard_ranges`, lines 73-84) and all-reduced across tensor-parallel ranks (line 108),
with a post-condition `float(total[1]) == embedding.num_embeddings` (line 111). The
estimand is the mean row norm of the full embedding table, identical to Reference A
(`state_bridge.py:208-209`), computed without materialising the table in float32.

### 1.10 Vocabulary anchoring

`statebridge.py:115-155`. The early-out is decisive for the shipped configuration:

```python
def anchor_tiled(prefix, embedding, *, alpha=0.3, tp_group=None, tile_rows=1024):
    """Exact global max cosine + smallest-token tie; never gather vocab weights."""
    torch = _torch()
    require(alpha in (0.0, 0.3) and 1 <= tile_rows <= 4096, "profile")
    if alpha == 0:
        return prefix
```

with the scan `statebridge.py:128-136` and the blend `statebridge.py:154-155`:

```python
nearest = embedding(ids)
return ((1 - alpha) * prefix.float() + alpha * nearest.float()).to(prefix.dtype).contiguous()
```

$$ p_i = \operatorname*{arg\,max}_{j}\ \hat{Z}_i \cdot \frac{e_j}{\lVert e_j \rVert_2}, \qquad \text{ties} \to \text{smallest } j $$
$$ Z^{\text{out}}_i = (1-\alpha)\, \hat{Z}_i + \alpha\, e_{p_i} $$

The query rows are normalised at line 125 and the tile rows at line 131, so the score
is a cosine. The tie rule is explicit at lines 134-136
(`better = (local_score > scores) | ((local_score == scores) & (token < ids))`), which
differs from Reference A's plain `argmax` (first maximum) only on exact ties.

`alpha` is restricted to the set `{0.0, 0.3}` (line 118). **The shipped value is 0.0
(section 4), so the anchoring block is skipped at runtime and the returned prefix is
the norm-calibrated `Z` unchanged** (`statebridge.py:119-120`). SOURCE-ATTESTED.

### 1.11 What is returned

`statebridge.py:70`

```python
return z.to(dtype=hidden.dtype).contiguous(), AlignmentInfo(ALIGNMENT, rank, n, d)
```

`ALIGNMENT = "union-subspace-procrustes.v1"` (`latent_types.py:13`). The caller
discards it: `aligned, _ = align_hidden_sequence(...)` (`latent_worker.py:333`). The
string never reaches the capture metadata (`latent_worker.py:350-357`) or the receipt.

## 2. The call site: `align()`

`latent_worker.py:304`, relevant statements verbatim.

### 2.1 Row selection

`latent_worker.py:309-321`

```python
n = min(record.accepted_count, record.descriptor.max_prefix_tokens)
require(n >= 2 and record.retained, "lineage")
...
ring_h, ring_t = record.retained
cursor = record.accepted_count % ring_h.shape[0]
if record.accepted_count < ring_h.shape[0]:
    order = torch.arange(n, device=self.device)
else:
    order = (torch.arange(n, device=self.device) + cursor) % ring_h.shape[0]
selected = ring_h.index_select(0, order)
prepared.update(n=n, h=selected, tokens=ring_t.index_select(0, order), prefix=torch.empty_like(selected))
```

The retained ring is a circular buffer of the last `max_prefix_tokens` accepted rows,
written at index `accepted_count % max_prefix_tokens` (`latent_worker.py:767-774`) and
counted at `latent_worker.py:785`. `order` recovers them in temporal order, so
`selected` is the last `n` accepted hidden rows and `tokens` is the matching token
ids, from the same `order`. `align()` runs once per response, at terminal commit
(`latent_worker.py:787-788`, `if terminal: runtime.align(record, req)`), so the fit
always sees the completed response's tail and is never updated incrementally.

### 2.2 Fit and transmit on the same rows

`latent_worker.py:324-341`

```python
n, h, tokens = prepared["n"], prepared["h"], prepared["tokens"]
embeddings = self.embedding(tokens)
if self.target_norm is None:
    self.target_norm = mean_embedding_norm(self.embedding, tp_group=self.group,
                                          tile_rows=self.profile.tile_rows)
# All ranks reach the outcome handshake even if the root solver fails.
solver_error, prefix = "", prepared["prefix"]
if self.rank == 0:
    try:
        aligned, _ = align_hidden_sequence(h, embeddings, self.target_norm)
        prefix.copy_(aligned)
    except Exception:
        solver_error = "numerical"
errors = self.gather(solver_error)
require(not any(errors), "numerical")
torch.distributed.broadcast(prefix, src=self.group.ranks[0], group=self.group.device_group)
prefix = anchor_tiled(prefix, self.embedding, alpha=self.profile.anchor_alpha,
                      tp_group=self.group, tile_rows=self.profile.tile_rows)
```

Line 325 takes the embedding of the **same** token ids that produced `h`
(line 321). Line 333 passes exactly `(h, embeddings)` to the solver. Line 334 copies
the solver output into `prefix`. Line 343 stores it as the record's prefix.

### 2.3 Metadata asserted about the span

`latent_worker.py:346-357`

```python
end = record.accepted_count
start = end - n
sequence_digest = digest(list(req.output_ids[start:end]))
commit = digest([record.descriptor.reservation_id, start, end, sequence_digest, self.layout_digest])
metadata = {"capture_commit_id": commit, "accepted_span_start": start,
            "accepted_span_end": end, "retained_count": n,
            "predictor_position_offset": -1, "representation": "final_layer_prenorm_predictor",
            "layer_index": self.profile.layer_index, "hidden_dim": self.profile.hidden_dim,
            ...
```

`accepted_span_start = accepted_count - n` and `retained_count = n`, so the receipt
declares the prefix to be the tail `n` tokens of the accepted response.

## 3. Is the Optimus transform fitted on the very rows it transmits?

**YES.** There is no calibration set, no held-out rows, and no frozen transform
anywhere in the shipped path.

The proof is three lines:

1. `latent_worker.py:325`: `embeddings = self.embedding(tokens)` -- the target side is
   built from `prepared["tokens"]`, which line 321 built by
   `ring_t.index_select(0, order)`, the **same `order`** that produced
   `h = ring_h.index_select(0, order)`.
2. `latent_worker.py:333`: `align_hidden_sequence(h, embeddings, self.target_norm)` --
   both arguments of the fit are those arrays.
3. `latent_worker.py:334`: `prefix.copy_(aligned)` -- the value transmitted is the
   solver's output for those very rows, and line 343 stores it as `record.prefix`.

So the operator is `f_{H,E}` evaluated at `H`, where `(H, E)` is what defined `f`.
The transform is never applied to a row it was not fitted on. There is no `apply`
entry point on the Optimus side; `align_hidden_sequence` returns `z` for the same
`hidden` it received, exactly as Reference A does.

Corroborating facts, SOURCE-ATTESTED:
- `mean_embedding_norm` (`statebridge.py:87`) is the only fitted-once quantity, and it
  is a global statistic of the embedding table, not of the message.
- A search for calibration vocabulary in the extension returns only
  `align_diagnostics.py:113-142`, which computes a *diagnostic* against the same rows:
  ```
  cd extensions\sglang-occ3 && findstr /S /I /N "calib heldout held_out" *.py
  -> align_diagnostics.py:113, :114, :115, :142 only
  ```
  OFFLINE MEASURED.
- `align_diagnostics` is not called by the worker at all; the only references in the
  extension and tests are `occ3_sglang/align_diagnostics.py:94` (its own definition)
  and `tests/test_align_diagnostics.py`. So the shipped runtime emits no alignment
  diagnostic. OFFLINE MEASURED (`findstr /S /I /N "align_diagnostics
  alignment_diagnostics" *.py`).

## 4. Numerical parameters actually shipped

The runtime profile is read from the packaged artifact
`extensions/sglang-occ3/deploy/native-build-inputs/native-profile-input.json`
(`native_profile.py:282-291`, `_PROFILE_FIELDS` at `native_profile.py:41-45`), and
`RuntimeProfile(**static)` is constructed from it at `latent_worker.py:851-857`.

The artifact on disk, verified by hashing:

```
sha256 = 6e676f1c6fc795ba412c4327168b51c8ef6f65056664638c4954eec58e75348a
{"anchor_alpha":0.0,"expected_ranks":8,"hidden_dim":6144,"layer_index":77,
 "max_active":1,"max_live_bytes":268435456,"max_prefix_tokens":128,"max_records":256,
 "max_ttl_seconds":3600,
 "method":"statebridge.glm53.final-prenorm-predictor.continuous-prefix.v1",
 "model_fingerprint":"sha256:db2c8830820da8bd3370bbfacf0799e33085ef7b8b1ca27840b88e3e8101b732",
 "model_id":"glm-5.3","tile_rows":256,"workspace_bytes":134217728}
```

OFFLINE MEASURED (sha256 of the file; the digest matches the value cited at
`tests/test_v3_native_validator.py:930`).

| Parameter | Shipped value | Where defined | Bound by the profile digest? |
|---|---|---|---|
| `ridge` | `1e-3` | function default, `statebridge.py:32` | NO |
| `eigen_floor` | `1e-6` | function default, `statebridge.py:33` | NO |
| `rank_rtol` | `1e-5` | function default, `statebridge.py:33` | NO |
| `anchor_alpha` | `0.0` | `native-profile-input.json`; dataclass default `latent_types.py:118`; allowed set `{0.0, 0.3}` at `latent_types.py:135` and `statebridge.py:118` | YES |
| `max_prefix_tokens` | `128` | `native-profile-input.json`; default `latent_types.py:112`; allowed `{8,16,32,64,128}` `latent_types.py:132`; upper bound 128 `attachment_contract.py:432` | YES |
| `hidden_dim` | `6144` | `native-profile-input.json`; default `latent_types.py:110`; hard-pinned `latent_types.py:131`; must equal `config.hidden_size` `latent_worker.py:87` | YES |
| `layer_index` | `77` | `native-profile-input.json`; default `latent_types.py:111`; hard-pinned `latent_types.py:131`; 78-layer model required `latent_worker.py:87` | YES |
| `tile_rows` | `256` | `native-profile-input.json`; default `latent_types.py:119` | YES |
| `expected_ranks` | `8` | `native-profile-input.json`; default `latent_types.py:109`; hard-pinned `latent_types.py:131` | YES |
| `tau` (`target_norm`) | computed at runtime | `latent_worker.py:326-328` -> `statebridge.py:87-112` | not a profile field |

`anchor_alpha` is additionally restricted to `{0.0, 0.3}` by the profile validator
(`statebridge.py:118`), so the only two permitted anchoring strengths are "off" and
Reference A's default.

Two facts about the digest boundary, SOURCE-ATTESTED:
- `binding_digest` is `digest(asdict(self))` over the dataclass fields
  (`latent_types.py:138-140`), so `ridge`, `eigen_floor` and `rank_rtol` are outside
  it. Changing any of those three changes the alignment arithmetic without changing
  the attested profile digest.
- `ALIGNMENT = "union-subspace-procrustes.v1"` (`latent_types.py:13`) is also outside
  the profile; `METHOD` (`latent_types.py:12`) is inside.

## 5. Differences from Reference A, and whether each is mathematically material

Reference A means `references/StateBridge/methods/state_bridge.py:211-299`.
"Material" is judged only on the question of whether the two operators produce
different rows from the same `(H, E, tau)`.

| # | Difference | Optimus | Reference A | Material? |
|---|---|---|---|---|
| D1 | Domain of the covariance and rotation | reduced `r`-dim subspace `q`, truncated to `min(#{sigma > theta}, 2(n-1))` (`statebridge.py:50-63`) | full `d`-dim space, no truncation (`state_bridge.py:261-282`) | Same intended operator, different numerical conditioning. At `d = 64..128` with `d` comparable to `r`, they agree to `3.4e-6 .. 5.5e-6` relative. At `d = 6144` the full-space result is itself ill-determined (26% change float32 vs float64, 17% change from a `1e-7` input perturbation, 80% of its energy outside `span(q)`), so no agreement figure is meaningful there. See 6.5 |
| D2 | Determinant constraint | none (`statebridge.py:62`); the file contains no `det` call | flips the last left singular vector when `det < 0` (`state_bridge.py:274-277`) | NO in practice at production width: `torch.det` of the `d x d` factor returns `-0.0` for `d >= 1024`, so `det < 0` is never true and Reference A's guard never fires (measured `referenceA_vs_referenceA_noDet = 0.0` at `d = 6144`). The guard is dead code at `6144`. Optimus's omission therefore matches Reference A's actual behaviour, though not its stated intent, and it contradicts the docstring claim at `statebridge.py:36` |
| D3 | Reasoning-span selection | tail of the whole completed response (`span_policy` `"whole_completed_response_tail_v1"`, `attachment_contract.py:151`, `:163`, `:437`; rows taken at `latent_worker.py:309-321`) | post-`</think>` tokens only, then the last `K` (`state_bridge.py:577-604`) | Material to *what* is transmitted, not to the algebra. Optimus retains reasoning tokens that Reference A discards |
| D4 | `max_prefix_tokens` | `128` | `64` (`state_bridge.py:164`, `:1130`; README `:107`) | No algebraic change; changes `n`, hence `r = 2(n-1)` and the covariance sample size |
| D5 | Vocabulary anchoring strength | `0.0`, block skipped (`statebridge.py:119-120`) | `0.3`, always applied (`state_bridge.py:171`, `:290-297`) | YES for shipped behaviour: the anchoring term is absent. The README ablation reports 82.4 -> 80.2 when anchoring is removed on Qwen3-4B (`references/StateBridge/README.md:57`) |
| D6 | Anchoring tie-break | smallest token id (`statebridge.py:134-136`) | first `argmax` (`state_bridge.py:295`) | NO except on exact ties |
| D7 | `tau` computation | sharded, `float64` accumulator, all-reduce, exact row count (`statebridge.py:87-112`) | materialised `float32` vocab, `float32` mean (`state_bridge.py:208-209`) | NO: same estimand, different precision |
| D8 | Degenerate-norm policy | refuses when `||Z_i|| <= eigen_floor` (`statebridge.py:67`) | clamps to `1e-6` and continues (`state_bridge.py:285`) | Only on degenerate rows |
| D9 | `C_H^{-1/2}` reuse | `x` reused in the reconstruction (`statebridge.py:60`, `:63`) | recomputed at `state_bridge.py:281` | NO (compute only) |
| D10 | Anchoring after broadcast | all ranks run `anchor_tiled` after the broadcast (`latent_worker.py:339-341`) | single process | NO (compute only) |
| D11 | Fit set | the transmitted rows | the transmitted rows | NO: **identical in-sample property** |

Summary judgement: D2, D6, D7, D8, D9, D10 are not material; D3 and D5 change the
transmitted content; D4 changes the sample size; D1 changes the numerical
conditioning of the solve and, at `d = 6144`, the two outputs differ by `0.33` to
`0.63` relative Frobenius while the full-space one is demonstrably unstable (6.5).
**No difference touches the in-sample property in D11.** On that axis Optimus and
Reference A are the same method, and the generalisation defect is inherited rather
than introduced.

## 6. Measurements

All commands use the extension interpreter
`extensions/sglang-occ3/.venv/Scripts/python.exe` (reports `3.11.15`, `torch 2.8.0+cpu`,
`cuda.is_available() = False`). No network, no GPU, no model.

### 6.1 The transcription is exact, and the reduced solver equals the full-space one

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_align_math2.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

A statement-by-statement transcription of `statebridge.py:45-63` reproduces the
shipped `align_hidden_sequence` output at `n=16, d=6144` with
`max_abs_diff = 0.0` and `rel_fro_diff = 0.0`. OFFLINE MEASURED.

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_galerkin.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

| n | d | `r` | cap `2(n-1)` | `||ch - q^T C_H q||` rel | reduced vs full Reference A | det-fixed vs det-free |
|---|---|---|---|---|---|---|
| 8 | 64 | 14 | 14 | 2.14e-07 | 4.090e-06 | 2.5e-09 |
| 16 | 64 | 30 | 30 | 3.25e-07 | 3.430e-06 | 5.5e-09 |
| 32 | 128 | 62 | 62 | 3.58e-07 | 5.469e-06 | 1.4e-08 |

OFFLINE MEASURED, synthetic Gaussian rows, `rho = 1e-3`, `eps = 1e-6`.

### 6.2 Truncation never engages on generic rows

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_align_math.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

| n | d | `r` computed | cap `2(n-1)` | union energy kept | `H` row energy inside `span(q)` |
|---|---|---|---|---|---|
| 8 | 6144 | 14 | 14 | 1.000000 | 1.000001 |
| 16 | 6144 | 30 | 30 | 1.000000 | 1.000000 |
| 32 | 6144 | 62 | 62 | 1.000000 | 1.000000 |
| 64 | 6144 | 126 | 126 | 1.000000 | 1.000000 |
| 128 | 6144 | 254 | 254 | 1.000000 | 1.000000 |

OFFLINE MEASURED, isotropic Gaussian rows. `rank_rtol = 1e-5` selected every singular
value above the cap in every case, so the cap `2(n-1)` binds and the truncation is
lossless for these rows. Consequently the docstring's "identity completion outside
union" (`statebridge.py:34`) is never exercised and its wording is untested by these
inputs. Whether real GLM-5.3 hidden rows are low-rank enough to engage the truncation
is **not measured here** and must not be inferred from these numbers.

Also measured in that run: the rotation factor's determinant was `-1` in 12 of 20
random draws (`dets = [-1.00001, -1.000006, 1.000005, ...]`), all within `1e-5` of
`+-1`. So the missing D2 constraint is violated in the majority of draws, yet it is
numerically inert for the transmitted rows (6.1).

### 6.3 The in-sample map reproduces the same tokens' embeddings

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_align_math2.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

| Rows | Setting | In-sample centred residual | Held-out residual | Unaligned baseline |
|---|---|---|---|---|
| n=16, d=6144 | isotropic | 2e-06 | 1.001935 | 1.468075 |
| n=16, d=6144 | 32-dim anisotropic | 0.593461 | 88899.617188 | 1.237803 |
| n=32, d=6144 | isotropic | `cos(aligned, embed(same tokens)) = 0.999956` | -- | `cos(hidden, embed(same tokens)) = -0.001618` |

OFFLINE MEASURED on synthetic rows. Reading: the operator interpolates the rows it was
fitted on and carries no structure to fresh rows; on anisotropic rows the whitening
inverse is unbounded enough to produce an `8.9e4` relative error out of sample. The
magnitudes are properties of the synthetic generator, not of GLM-5.3.

The third row is already encoded as an executable expectation in this repository:
`tests/test_align_diagnostics.py:45-54` asserts `cos_prefix_to_embedding > 0.999999`
and `hidden_sensitivity < 1e-3` for the production map, under the test name
`test_production_map_is_nearly_insensitive_to_the_hidden_states`. SOURCE-ATTESTED.

### 6.5 The full-space reference solve is numerically degenerate at production width

Three runs, same interpreter, synthetic anisotropic rows unless stated.

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_refA_vs_optimus.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

`n = 16`, `d = 6144`, anisotropic rows, `rho = 1e-3`:

| Quantity | Value |
|---|---|
| `r` (Optimus `union_rank`, recomputed) | 30 |
| Optimus output vs its own transcription | 0.0 (bit-identical) |
| Optimus vs Reference A (full-space, float32) | 0.3314 relative Frobenius |
| Reference A with det guard vs without | 0.0 |
| `det(R)` returned by `torch.det`, `d = 6144` | -0.0 |

OFFLINE MEASURED. The `det` result explains the zero: at this width `torch.det`
returns `-0.0`, so `if torch.det(R) < 0` (`state_bridge.py:275`) is never true and the
proper-rotation guard never fires.

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_svd_orthogonality.py
```

Is the large orthogonal factor itself broken? A rank-15 `d x d` matrix, then
`R = U V^T` from `full_matrices=False`:

| d | `||U^T U - I||_max` | zero-norm columns of `U` | `det(R)` | `sigma_16 / sigma_max` |
|---|---|---|---|---|
| 256 | 1e-06 | 0 | 1.0000246 | 4.8e-07 |
| 1024 | 1e-06 | 0 | -0.0 | 4.8e-07 |
| 2048 | 2e-06 | 0 | -0.0 | 6.8e-07 |
| 4096 | 3e-06 | 0 | -0.0 | 7.6e-07 |

OFFLINE MEASURED. `U` stays orthonormal, so `R` is a genuine orthogonal matrix; only
`torch.det` fails, from `d = 1024` upward. The singular values beyond the true rank sit
at or below the float32 noise floor relative to `sigma_max` (`4.8e-07 .. 7.6e-07`
against `eps_f32 = 1.19e-07` times the matrix norm), yet the full-space Procrustes sums
over all `d` of them.

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_nullspace.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

`n = 16`, anisotropic rows, Reference A in float32 and float64, plus a perturbation of
`E` by `1e-7` of its mean absolute value:

| d | full-space float32 vs float64 | full-space, `1e-7` perturbation of `E` | reduced vs float32 full-space | full-space energy outside `span(q)` |
|---|---|---|---|---|
| 64 | 9.38e-05 | 3.74e-05 | 1.10e-04 | 0.536 |
| 512 | 9.22e-03 | 4.97e-03 | 1.22e-02 | 0.808 |
| 6144 | 0.2593 | 0.1730 | 0.6270 | 0.800 |

OFFLINE MEASURED (relative Frobenius throughout).

Reading, and the limit of what it establishes:
- At `d = 64` the full-space solve is stable: a `1e-7` input perturbation moves the
  output by `3.7e-05`, and float32 agrees with float64 to `9.4e-05`. At this scale the
  reduced and full-space solvers are interchangeable.
- At `d = 6144` the full-space output moves by `0.17` under a `1e-7` perturbation and
  by `0.26` between float32 and float64. It is not a reproducible target.
- `80%` of the full-space output's mean-removed energy lies outside `span(q)`, the only
  subspace in which the data has support (`rowspace([hc; ec])`, dimension `r = 30`).
  That energy is manufactured by the `d - rank` noise-floor singular directions, each
  contributing `H_w u_i v_i^T` with `u_i` not orthogonal to `rowspace(hc)`.
- HYPOTHESIS: this is why the `0.33` and `0.63` gaps appear. The mechanism is
  consistent with all three measurements, but the decomposition into individual terms
  was not isolated.

Consequence for the comparison in D1: the `0.33 .. 0.63` difference at `d = 6144` is
not evidence that Optimus implements a different operator. It is evidence that the
untruncated `d`-dimensional solve cannot serve as a numerical reference at this width,
while the `r`-dimensional truncated solve is well posed. The algebraic identity of the
two operators is established at `d <= 128` (6.1); at `d = 6144` it is not testable with
the full-space code as written.

### 6.4 The shipped diagnostic would report an in-sample statistic

`align_diagnostics.py:82-83` computes

```python
residual = float(((whitened_h @ rotation - whitened_e).norm() /
                  whitened_e.norm().clamp_min(torch.finfo(z.dtype).tiny)))
```

which is the Procrustes residual on the same rows that produced `rotation`, and
`align_diagnostics.py:141-142` reports `cos_prefix_to_embedding` and
`relative_prefix_error` against `embed(same tokens)`. Given 6.3 these are
near-optimal by construction (residual `~2e-6`, cosine `~1.0`) and cannot detect the
absence of transferable structure. HYPOTHESIS for the interpretation; the three
expressions are SOURCE-ATTESTED and the values are OFFLINE MEASURED in 6.3.

Two further SOURCE-ATTESTED defects in that module, not exercised by the shipped path
because the module is not wired in:
- The docstring at `align_diagnostics.py:99-101` claims the two sensitivity
  measurements use "deterministic permutations", but `align_diagnostics.py:122` uses
  `torch.randperm(...)` with no seed, so `shuffled_hidden_sensitivity` is not
  reproducible.
- `tests/test_align_diagnostics.py:46` cites `MATHEMATICAL-DEGENERACY.md`; no file of
  that name exists in the worktree (`dir /s /b *DEGENERACY*` returns only
  `measure_degeneracy.py` under the reports directory).

## 7. Facts, stated plainly

1. The shipped operator is: centre, project onto the union subspace of the two centred
   row blocks, ridge-whiten each side inside that subspace, orthogonal Procrustes
   without a determinant constraint, unwhiten with the embedding square root, project
   back, add the embedding mean, rescale every row to `tau`. SOURCE-ATTESTED.
2. It is fitted on the very rows it transmits. `latent_worker.py:321` builds `h` and
   `tokens` from one `order`; line 325 embeds those tokens; line 333 fits on
   `(h, embeddings)`; line 334 transmits the fit of `h`. SOURCE-ATTESTED.
3. There is no calibration set, no held-out split, and no frozen or serialised
   transform in the extension. SOURCE-ATTESTED.
4. Reference A has the same in-sample property, so this is not an implementation
   divergence from the reference; it is the reference's own design.
5. Algebraically the shipped operator equals Reference A's full-space operator
   restricted to `span(q)`. Measured at `3.4e-6 .. 5.5e-6` relative Frobenius for
   `d = 64..128`, where the full-space solve is stable. At `d = 6144` the full-space
   solve is itself unstable (17% change under a `1e-7` input perturbation, 26%
   between float32 and float64), so the two outputs differ by `0.33 .. 0.63` and no
   agreement figure is meaningful. OFFLINE MEASURED.
6. The material differences from Reference A are: anchoring disabled (`alpha = 0.0`),
   a different span policy that keeps the whole-response tail instead of the
   post-`</think>` span, and `max_prefix_tokens = 128` instead of `64`.
   SOURCE-ATTESTED.
7. `ridge = 1e-3`, `eigen_floor = 1e-6` and `rank_rtol = 1e-5` are function defaults
   at `statebridge.py:32-33`, are not in the pinned profile, and are therefore outside
   `binding_digest`. SOURCE-ATTESTED.
