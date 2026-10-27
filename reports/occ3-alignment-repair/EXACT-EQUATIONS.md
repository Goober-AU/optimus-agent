# EXACT EQUATIONS: the three reference alignment designs

Scope: exact mathematics of the alignment operator in Reference A (StateBridge),
LatentMesh, and CodeTalkers, transcribed from source with line citations, plus the
critical question of whether each fits on the rows it transmits.

Report date: 2026-10-27. Worktree `C:/Users/openclawuser/optimus-occ3-latent-validation`,
branch `fix/occ3-statebridge-alignment-generalisation`.

Labels used throughout:
- SOURCE-ATTESTED: read directly from the cited file:line.
- OFFLINE MEASURED: produced by a command run on this machine; the command is stated.
- HYPOTHESIS: an interpretation that the cited source does not itself assert.

## 0. Paths actually read

The task named `{D}/references/StateBridge/...` and a LatentMesh clone under `crates/`.
The trees on disk differ from that description. What exists and was read in full:

| Design | Path read | Lines |
|---|---|---|
| Reference A (StateBridge) | `references/StateBridge/methods/state_bridge.py` | 1266 |
| Reference A prompts | `references/StateBridge/prompts.py` | 160 |
| Reference A README | `references/StateBridge/README.md` | 176 |
| LatentMesh | `C:/Users/openclawuser/occ3-refs/LatentMesh__lib.rs` | 345 |
| LatentMesh solver | `C:/Users/openclawuser/occ3-refs/LatentMesh__procrustes_mtm.rs` | 208 |
| CodeTalkers | `C:/Users/openclawuser/occ3-refs/CodeTalkers__latent_talk.py` | 208 |

There is no `crates/latentmesh-align/` in this worktree and no
`references/LatentMesh/` clone. LatentMesh exists only as the two flattened files
under `C:/Users/openclawuser/occ3-refs/`. The flattened `lib.rs` declares
`mod procrustes_mtm;` at line 29 and the sibling file is present, so the pair is
complete for the fit/apply mathematics. SOURCE-ATTESTED (path listing), and this
discrepancy is stated rather than silently worked around.

## 1. Notation

Row-vector convention throughout: one row is one observation.

| Symbol | Shape | Meaning |
|---|---|---|
| `N` | scalar | number of rows in the message (Reference A: `B * T`) |
| `d` | scalar | hidden width, `= embedding_dim` |
| `B`, `T` | scalar | batch size and selected sequence length |
| `H` | `N x d` | sender hidden-state rows |
| `E` | `N x d` | reference embedding rows, `E[i] = W_emb[token_ids[i]]` |
| `mu_H`, `mu_E` | `1 x d` | row means |
| `Hc`, `Ec` | `N x d` | centred rows |
| `C_H`, `C_E` | `d x d` | regularised covariance |
| `rho` | scalar | ridge (Reference A: `adaptive_reg`) |
| `eps` | scalar | eigenvalue floor |
| `V`, `lam` | `d x d`, `d` | eigenvectors and eigenvalues of a symmetric matrix |
| `U`, `Sigma`, `Vt` | `d x d`, `d`, `d x d` | SVD of `M = Hw^T Ew` |
| `R` | `d x d` | orthogonal (or improper-orthogonal) Procrustes rotation |
| `Z` | `N x d` | reconstructed rows in embedding space |
| `tau` | scalar | scalar target norm (`target_norm`) |
| `alpha` | scalar | vocabulary anchoring ratio |
| `W_emb` | `V x d` | input embedding matrix, `V` = vocabulary size |
| `e_j` | `1 x d` | row `j` of `W_emb` |
| `1_N` | `N x 1` | vector of ones |

Superscript `+p` denotes the symmetric matrix power defined in section 2.3.

## 2. Reference A: StateBridge

Entry point `_align_hidden_sequence(self, hidden_seq, token_ids)`,
`references/StateBridge/methods/state_bridge.py:211`.

Reshaping to rows: `state_bridge.py:239-241`

```python
H = hidden_float.reshape(-1, hidden)
E = token_embeds.reshape(-1, hidden)
N = H.shape[0]
```

so `N = B * T` and rows are token positions. `T = min(hidden_seq.shape[1],
token_embeds.shape[1])` (`state_bridge.py:235-237`).

### 2.1 Centring

`state_bridge.py:254-258`

```python
mu_H = H.mean(dim=0, keepdim=True)
mu_E = E.mean(dim=0, keepdim=True)
Hc = H - mu_H
Ec = E - mu_E
```

$$ \mu_H = \tfrac{1}{N}\mathbf{1}_N^{\top} H, \qquad \mu_E = \tfrac{1}{N}\mathbf{1}_N^{\top} E $$
$$ H_c = H - \mathbf{1}_N \mu_H, \qquad E_c = E - \mathbf{1}_N \mu_E $$

### 2.2 Regularised covariance

`state_bridge.py:261-263`, with `reg` read from `adaptive_reg` at `state_bridge.py:245`

```python
reg = float(getattr(self, "adaptive_reg", 1e-3))
I = torch.eye(hidden, device=H.device, dtype=H.dtype)
Cov_H = (Hc.T @ Hc) / N + reg * I
Cov_E = (Ec.T @ Ec) / N + reg * I
```

$$ C_H = \tfrac{1}{N} H_c^{\top} H_c + \rho I_d, \qquad C_E = \tfrac{1}{N} E_c^{\top} E_c + \rho I_d, \qquad \rho = 1\text{e-}3 $$

Default `rho` is `adaptive_reg: float = 1e-3` at `state_bridge.py:170`, and the CLI
default is `--adaptive_reg` `1e-3` at `state_bridge.py:1131`. Both are `d x d`
dense. SOURCE-ATTESTED.

### 2.3 `_symmetric_power`

`state_bridge.py:247-252`

```python
def _sym_power(A: torch.Tensor, power: float, eps: float = 1e-6) -> torch.Tensor:
    """Symmetric matrix power via eigendecomposition."""
    A = (A + A.T) * 0.5
    evals, evecs = torch.linalg.eigh(A)
    evals = evals.clamp_min(eps).pow(power)
    return (evecs * evals.unsqueeze(0)) @ evecs.T
```

For symmetric `M` with `M = V diag(lam) V^T` and floor `eps`:

$$ M^{+p} := V \operatorname{diag}\!\left(\max(\lambda_i, \varepsilon)^{p}\right) V^{\top}, \qquad \varepsilon = 1\text{e-}6 $$

Note the floor is applied *before* the power, so `M^{+p}` is well defined for
negative `p`. `evecs * evals.unsqueeze(0)` scales columns of `evecs`, i.e. it
computes `V diag(...)`, and the right multiplication by `V^T` completes the
sandwich. SOURCE-ATTESTED.

### 2.4 Whitening

`state_bridge.py:265-267`

```python
H_w = Hc @ _sym_power(Cov_H, -0.5)
E_w = Ec @ _sym_power(Cov_E, -0.5)
```

$$ H_w = H_c\, C_H^{-1/2}, \qquad E_w = E_c\, C_E^{-1/2} $$

Because rows are observations, `Hw^T Hw / N = C_H^{-1/2} C_H C_H^{-1/2}` is close to
`I_d` up to the ridge. This is the "whiten both spaces" step of the README
(`references/StateBridge/README.md:118`).

### 2.5 Orthogonal Procrustes with determinant handling

`state_bridge.py:269-277`

```python
M = H_w.T @ E_w
U, _, Vt = torch.linalg.svd(M, full_matrices=False)
R = U @ Vt

# Enforce proper rotation (det = +1) to avoid reflection
if torch.det(R) < 0:
    U[:, -1] *= -1
    R = U @ Vt
```

$$ M = H_w^{\top} E_w, \qquad M = U \Sigma V^{\top}, \qquad R = U V^{\top} $$

Solve: `R = argmin_{Q in O(d)} || H_w Q - E_w ||_F`, whose optimum is `U V^T`
(Schoenemann 1966). Constraint: if `det(R) = -1`, the last left singular vector is
negated and `R` is recomputed, which yields `R = U diag(1,...,1,-1) V^T` with
`det(R) = +1`. The code mutates `U` in place (`U[:, -1] *= -1`) before forming `R`,
which is equivalent. SOURCE-ATTESTED.

Caveat on the guard, OFFLINE MEASURED (section 7.4): `torch.det` of a `d x d`
orthogonal matrix returns `-0.0` for `d >= 1024` in float32, so at the production width
`d = 6144` the test `torch.det(R) < 0` is always False and the constraint is never
applied. The stated intent and the executed behaviour differ at production scale.

Note also that `M = H_w^T E_w` has rank at most `n - 1`, because `rank(H_c) <= n - 1`.
At `d = 6144` and `n <= 128` this matrix is at most `127`-rank inside `6144`
dimensions, so the `d - rank` trailing singular triples of `U` and `V` are numerical
noise, yet `R = U V^T` sums over all `d` of them. Section 7.4 quantifies the damage.

### 2.6 Reconstruction

`state_bridge.py:279-282`

```python
sqrt_Cov_E = _sym_power(Cov_E, 0.5)
aligned_flat = (Hc @ _sym_power(Cov_H, -0.5)) @ R @ sqrt_Cov_E + mu_E
aligned = aligned_flat.reshape(batch, T, hidden)
```

$$ Z = H_c\, C_H^{-1/2}\, R\, C_E^{+1/2} + \mathbf{1}_N \mu_E $$

`C_H^{-1/2}` is recomputed at line 281 although `H_w` already holds
`Hc @ C_H^{-1/2}`; that is a redundant eigendecomposition, not a mathematical
difference. SOURCE-ATTESTED.

### 2.7 Norm calibration

`state_bridge.py:284-286`

```python
aligned_norm = aligned.norm(dim=-1, keepdim=True).clamp(min=1e-6)
aligned = aligned * (self.target_norm / aligned_norm)
```

$$ \hat{Z}_i = \tau\, \frac{Z_i}{\max(\lVert Z_i \rVert_2,\; 10^{-6})} $$

with `tau` set once in the constructor, `state_bridge.py:208-209`:

```python
self.vocab_embeds = self.embedding_layer.weight.detach().float()
self.target_norm = self.vocab_embeds.norm(dim=-1).mean().item()
```

$$ \tau = \frac{1}{V} \sum_{j=1}^{V} \lVert e_j \rVert_2 $$

SOURCE-ATTESTED. Note `tau` is a *global* statistic of the embedding table, not a
per-message statistic: this one step is not fitted to the message rows.

### 2.8 Vocabulary anchoring

`state_bridge.py:288-297`

```python
if self.snap_ratio > 0:
    flat = aligned.reshape(-1, hidden)
    sims = torch.matmul(
        F.normalize(flat, dim=-1),
        F.normalize(self.vocab_embeds, dim=-1).T,
    )
    top_ids = sims.argmax(dim=-1)
    snapped = self.vocab_embeds[top_ids].reshape(batch, T, hidden)
    aligned = (1 - self.snap_ratio) * aligned + self.snap_ratio * snapped
```

$$ p_i = \operatorname*{argmax}_{j \in \{1..V\}} \frac{\hat{Z}_i}{\lVert \hat{Z}_i \rVert_2} \cdot \frac{e_j}{\lVert e_j \rVert_2} $$
$$ Z^{\text{out}}_i = (1-\alpha)\, \hat{Z}_i + \alpha\, e_{p_i}, \qquad \alpha = \texttt{snap\_ratio} = 0.3 $$

Applied to the norm-calibrated rows `aligned`, not to the raw `Z` (line 286 runs
before line 290). `top_ids = sims.argmax(dim=-1)` returns the *first* maximum, so
ties resolve to the smallest vocabulary index.

Defaults: `snap_ratio: float = 0.3` at `state_bridge.py:171`, CLI default `0.3` at
`state_bridge.py:1132`, README `--snap_ratio` default 0.3 at
`references/StateBridge/README.md:107`. SOURCE-ATTESTED.

### 2.9 Reasoning / final-message span selection

`state_bridge.py:577-607`. The span is chosen from the *generated* tokens of the
current agent, and the split point is the token `</think>`.

```python
if agent.role != "judger":
    align_start = time.time()
    # Find </think> token position; pass only post-</think> embeddings
    think_end_token_ids = self.model.tokenizer.encode("</think>", add_special_tokens=False)

    think_end_pos = -1
    if len(think_end_token_ids) > 0:
        search_len = len(think_end_token_ids)
        token_list = gen_token_ids[0].tolist()
        for i in range(len(token_list) - search_len + 1):
            if token_list[i:i + search_len] == think_end_token_ids:
                think_end_pos = i + search_len
                break

    if think_end_pos > 0 and think_end_pos < gen_hidden_seq.shape[1]:
        filtered_hidden = gen_hidden_seq[:, think_end_pos:, :]
        filtered_token_ids = gen_token_ids[:, think_end_pos:]

        if filtered_hidden.shape[1] > self.max_prefix_tokens:
            filtered_hidden = filtered_hidden[:, -self.max_prefix_tokens:, :]
            filtered_token_ids = filtered_token_ids[:, -self.max_prefix_tokens:]
    else:
        if gen_hidden_seq.shape[1] > self.max_prefix_tokens:
            filtered_hidden = gen_hidden_seq[:, -self.max_prefix_tokens:, :]
            filtered_token_ids = gen_token_ids[:, -self.max_prefix_tokens:]
        else:
            filtered_hidden = gen_hidden_seq
            filtered_token_ids = gen_token_ids
```

Let `g` be the generated token sequence of length `G`, and `s` the index just after
the first occurrence of `</think>` (`s = i + search_len`). Define the row index set

$$ S = \begin{cases} \{s,\ s+1,\ \dots,\ G-1\}, & 0 < s < G \\ \{0, 1, \dots, G-1\}, & \text{otherwise} \end{cases} $$

then take the last `K` indices of `S`:

$$ S_K = S[\,\max(0, |S| - K),\ \dots,\ |S| - 1\,], \qquad K = \texttt{max\_prefix\_tokens} = 64 $$

and

$$ H = H^{\text{gen}}[S_K], \qquad \text{token\_ids} = g[S_K] $$

Consequences, SOURCE-ATTESTED:
- The reasoning span (everything up to and including `</think>`) is *discarded* when
  a `</think>` token is found and `0 < s < G`.
- The retained span is the *last* `K` generated tokens, so for a short answer the
  span is the whole post-think answer; for a long answer it is the tail.
- The judger never aligns (`if agent.role != "judger"`, line 577), and hidden states
  are not even captured for it (`need_hidden = (agent.role != "judger")`, line 563).
- `K = 64` default at `state_bridge.py:164` and `state_bridge.py:1130`; README
  `--max_prefix_tokens` default 64 at `references/StateBridge/README.md:107`.
- The hidden states come from the *last* transformer layer: a forward hook on
  `model_inner.model.layers[-1]` (`state_bridge.py:403-410`), or, in the
  `output_hidden_states` path, `step_hidden[-1]` (`state_bridge.py:473`). There is no
  pre-norm correction in either path.

## 3. Reference A: does it fit on the rows it transmits?

**Answer: YES. Reference A is fully in-sample. It fits the transform on the same
rows it transmits, per message, at every hop. There is no calibration set, no
held-out split, and no notion of a frozen transform.**

### 3.1 The proof, in four lines

`state_bridge.py:591-604` builds `filtered_hidden` and `filtered_token_ids` from the
*same* generation. `state_bridge.py:607` passes **those two arrays** to the solver:

```python
if filtered_hidden.shape[1] > 0:
    aligned_embeds = self._align_hidden_sequence(filtered_hidden, filtered_token_ids)
```

`state_bridge.py:613` transmits the solver's output for **the same rows**:

```python
current_prefix = self._process_prefix(aligned_embeds)
```

and `_align_hidden_sequence` builds its target `E` *from the argument token ids*
(`state_bridge.py:232-233`):

```python
token_ids = token_ids.to(target_device)
token_embeds = self.embedding_layer(token_ids).float()
```

So the pair `(H, E)` that the solver fits is `(filtered_hidden, embed(filtered_token_ids))`,
and the value transmitted is `f_{H,E}(H)` for that very same `H`. The transform never
leaves the rows that defined it.

### 3.2 Corroborating facts

- `N` used in the covariance is the number of *message rows* (`state_bridge.py:241`,
  `262-263`), not a calibration count.
- `mu_E`, `C_E` are statistics of `embed(filtered_token_ids)`
  (`state_bridge.py:256`, `263`), i.e. of the receiver-space image of the sender's own
  tokens. `E` is therefore not an independent sample of the receiver's input
  distribution; it is the embedding of the message itself.
- The only quantity in the pipeline that is *not* per-message is `tau`
  (`state_bridge.py:209`, global mean embedding norm).
- A full-text search for calibration vocabulary in the clone returns nothing:

  ```
  findstr /S /I /N "calibration held-out heldout" references\StateBridge\*.py references\StateBridge\*.md
  -> no matches
  ```

  OFFLINE MEASURED (command above, run in the worktree root).
- The README describes the reference embeddings as coming from the message, not from a
  corpus: "records the sender's hidden states during generation, along with the
  reference embeddings of the decoded tokens"
  (`references/StateBridge/README.md:117`). SOURCE-ATTESTED.

### 3.3 What the ablation numbers in the README do and do not show

`references/StateBridge/README.md:57` reports that "Removing norm calibration gives
79.5, removing vocabulary anchoring 80.2" against 82.4 full. Those ablations vary the
*post-processing* of an in-sample fit; none of them removes the in-sample fitting.
The README reports no held-out-alignment ablation. SOURCE-ATTESTED (absent claim).

## 4. LatentMesh

`C:/Users/openclawuser/occ3-refs/LatentMesh__lib.rs`.

**Answer: YES, LatentMesh fits on independent calibration examples.** Its API makes
fit and application separate operations, and the crate documents that its shipped
`confidence` number is on-train.

### 4.1 The affine form

`lib.rs:35` (doc) and `lib.rs:270-271` (doc of `apply`):

```rust
/// A fitted alignment: `aligned(z) = μ_r + α · (z − μ_s) · R`.
```

with `mu_s`, `mu_r` stored in the serialized struct (`lib.rs:51-56`). In row-vector
notation for one row `z` (a `1 x dim_sender` row vector):

$$ \operatorname{aligned}(z) \;=\; \mu_r \;+\; \alpha\,\bigl(z - \mu_s\bigr)\, R $$

$$ R \in \mathbb{R}^{d_s \times d_r},\quad \mu_s \in \mathbb{R}^{d_s},\quad \mu_r \in \mathbb{R}^{d_r},\quad \alpha \in \mathbb{R} $$

### 4.2 Fit on calibration pairs

`lib.rs:93-99` (`fit`) and `lib.rs:113-134` (`fit_affine`) both take `pairs:
&[(Vec<f32>, Vec<f32>)]`, documented as "calibration pairs `(a_i, b_i)` sampled from
two spaces" (`lib.rs:2-3`). `fit_affine` computes

$$ \mu_s = \tfrac{1}{n}\sum_{i=1}^{n} a_i, \qquad \mu_r = \tfrac{1}{n}\sum_{i=1}^{n} b_i $$
$$ A = \bigl[a_1 - \mu_s; \dots; a_n - \mu_s\bigr] \in \mathbb{R}^{n \times d_s}, \qquad B = \bigl[b_1 - \mu_r; \dots; b_n - \mu_r\bigr] \in \mathbb{R}^{n \times d_r} $$

(`lib.rs:120-133`, `lib.rs:152-175`), then solves

$$ R = \operatorname*{argmin}_{Q \in O(d)} \lVert A Q - B \rVert_F, \qquad R = U V^{\top} \ \text{ from } \ A^{\top} B = U \Sigma V^{\top} $$

(`lib.rs:193-199`: `let m = a.transpose() * b; ... u * v_t`). There is **no
whitening** anywhere in the crate: no covariance, no eigenvalue floor, no ridge.
`R` is a plain orthogonal matrix, or in the `n < d` fast path

$$ R = \bigl(I_d - Q Q^{\top}\bigr) + Q\, U' V'^{\top} $$

with `Q` the `d x n` orthonormal basis of `rowspace(A)` and `A^T = Q R_qr`,
`W = R_qr B = U' \Sigma' V'^T` (`lib.rs:204-220`, comment at `lib.rs:215-216`:
"exact on the calibrated subspace, identity on its orthogonal complement").

Magnitude calibration (`lib.rs:236-244`) is a least-squares scalar:

$$ \alpha = \frac{\langle A R,\, B \rangle_F}{\lVert A R \rVert_F^2} $$

with `alpha = 1.0` when the denominator is `<= 1e-12`. Note the determinant of `R` is
never corrected: `u * v_t` is returned unconditionally (`lib.rs:198`, `lib.rs:219`),
so `R` may be improper.

### 4.3 Where the transform is applied out-of-sample

`lib.rs:275-293`:

```rust
pub fn apply(&self, z: &[f32]) -> Vec<f32> {
    assert_eq!(z.len(), self.dim_sender, "input dimension mismatch");
    let r = self.projection();
    let zv = if self.mu_s.is_empty() {
        DVector::from_row_slice(z)
    } else {
        DVector::from_iterator(z.len(), z.iter().zip(&self.mu_s).map(|(v, m)| v - m))
    };
    let mut out: Vec<f32> = (self.alpha * (zv.transpose() * r))
        .iter()
        .copied()
        .collect();
    if !self.mu_r.is_empty() {
        for (o, m) in out.iter_mut().zip(&self.mu_r) {
            *o += m;
        }
    }
    out
}
```

`apply` takes an arbitrary single row `z`; nothing links `z` to `pairs`. The fitted
object is serializable (`Serialize`/`Deserialize` at `lib.rs:43`) and carries a
content hash over `(R, alpha, mu_s, mu_r)` (`lib.rs:307-330`), which only makes sense
for a transform that outlives the rows that produced it. This is the structural
separation that Reference A does not have.

### 4.4 The crate states its own on-train caveat

`lib.rs:57-62`:

```rust
/// In `[0,1]`: `1 − relative Frobenius residual` on the calibration set
/// (mean-centred residual for `fit_affine`). A REAL measured number, not
/// asserted — see [`AlignmentTransform::fit`]. NOTE: this is an ON-TRAIN
/// number; the live-experiment design (docs/research/024 §5) reports
/// held-out residual instead, precisely because this one is optimistic.
```

and `lib.rs:9-12` distances the crate from StateBridge's reported results. SOURCE-ATTESTED.

## 5. CodeTalkers

`C:/Users/openclawuser/occ3-refs/CodeTalkers__latent_talk.py`.

**Answer: CodeTalkers performs no alignment at all. The map is the identity, and it
is the identity by construction because one model instance plays both agents.**

- Docstring, `latent_talk.py:6-8`: "One frozen open-weights model
  (Qwen2.5-0.5B-Instruct) plays both agents, so sender and receiver share a latent
  space."
- One model, `latent_talk.py:35`; one embedding table, `latent_talk.py:37`:
  `EMB = model.get_input_embeddings().weight.detach()`.
- The message is a mixture of *that same* embedding table, `latent_talk.py:62-65`:

  ```python
  logits = out.logits[0, -1, :] / temperature
  topv, topi = torch.topk(logits, top_k)
  p = F.softmax(topv, dim=-1)
  v = p @ EMB[topi]
  ```

  $$ v_t = \sum_{i \in \mathrm{top}\text{-}k} p_{t,i}\, e_{c_i}, \qquad p_{t,:} = \operatorname{softmax}\!\left(\frac{\ell_{t,:}}{T}\right)_{\mathrm{top}\text{-}k} $$

  with `e_j` the row `j` of `EMB`, `ell` the next-token logits, `T` the temperature,
  `k` the top-k truncation (`top_k=100` default, `latent_talk.py:157`).
- The receiver splices those vectors between embedding blocks,
  `latent_talk.py:84-89`:

  ```python
  parts = [emb(prefix_ids)[0]]
  if message_vectors is not None:
      parts.append(message_vectors)
  parts.append(emb(suffix_ids)[0])
  embeds = torch.cat(parts).unsqueeze(0)
  ```

Because `v_t` is a convex combination of embedding rows, it lies in the convex hull of
the embedding table by construction: the "alignment" problem is solved by never
leaving the space. No norm calibration, no anchoring, no centring, no rotation.
SOURCE-ATTESTED.

## 6. The fit/transmit axis, side by side

| Design | Fit set | Transmitted rows | Separate calibration set? |
|---|---|---|---|
| Reference A (StateBridge) | `Hc, Ec` from the current message (`state_bridge.py:255-263`) | `f_{H,E}(H)` for the same `H` (`state_bridge.py:607`, `613`) | NO |
| LatentMesh | `pairs` passed to `fit`/`fit_affine` (`lib.rs:93`, `113`) | `apply(z)` for arbitrary `z` (`lib.rs:275`) | YES, by API design |
| CodeTalkers | nothing is fitted | `v_t` mixture of own embedding rows (`latent_talk.py:65`) | not applicable (identity map) |

## 7. Measurements

### 7.1 Reference A is reproducible and the reduced solver equals it

Command (extension interpreter, run from any cwd):

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_galerkin.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

Result (OFFLINE MEASURED, float32, synthetic Gaussian rows, `rho=1e-3`, `eps=1e-6`):

| n | d | rank | `||ch - q^T C_H q||` rel | `||ce - q^T C_E q||` rel | reduced vs full Reference A (no det) | reduced vs full Reference A (det) |
|---|---|---|---|---|---|---|
| 8 | 64 | 14 | 2.14e-07 | 2.45e-07 | 4.090e-06 | 4.087e-06 |
| 16 | 64 | 30 | 3.25e-07 | 2.56e-07 | 3.430e-06 | 3.436e-06 |
| 32 | 128 | 62 | 3.58e-07 | 2.51e-07 | 5.469e-06 | 5.468e-06 |

Reading, with `ch`, `ce` the shipped reduced-space covariances of
`occ3_sglang/statebridge.py:58-59`:
- The Galerkin identity `ce = q^T C_E q` and `ch = q^T C_H q` holds to float32
  roundoff. This is the algebraic reason the Optimus solver and Reference A's solver
  are the same operator.
- The two solvers agree to `3e-6 .. 6e-6` relative Frobenius at these shapes. The
  residual gap is float32 precision, not a modelling difference.
- The determinant branch is numerically inert on these rows: the det-fixed and
  det-free full-space results agree to about `3e-9`. HYPOTHESIS (consistent with the
  measurement): `rank(A) = n-1 << d`, so the singular vector whose sign is flipped
  lies in `null(H_w^T)`, and `H_w (2 u_last v_last^T) C_E^{1/2}` vanishes.

### 7.2 Reference A's in-sample fit is near-exact; its out-of-sample transfer is not

Command:

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_align_math2.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
```

Result (OFFLINE MEASURED). First a parity check: a statement-by-statement
transcription of `occ3_sglang/statebridge.py:45-63` reproduces the shipped
`align_hidden_sequence` output with `max_abs_diff = 0.0` and `rel_fro_diff = 0.0` at
`n=16, d=6144`. The transcription is therefore the shipped operator.

Then, fitting on batch 1 and applying to disjoint i.i.d. batch 2 (centred residual
`||pred - target||_F / ||target||_F`):

| Rows | Setting | In-sample residual | Held-out residual | Unaligned baseline |
|---|---|---|---|---|
| n=16, d=6144 | isotropic | 2e-06 | 1.001935 | 1.468075 |
| n=16, d=6144 | 32-dim anisotropic | 0.593461 | 88899.617188 | 1.237803 |

The in-sample map is exact to float32; the same map applied to fresh rows carries no
transferable structure, and on anisotropic rows the whitening inversion explodes
(`8.9e4` relative) because `rho = 1e-3` does not bound the small-eigenvalue
directions. This is a synthetic-row measurement and must not be read as a number for
real GLM-5.3 hidden states; the mechanism it exposes is structural, the magnitudes are
not.

### 7.3 The in-sample map sends the hidden rows onto the same tokens' embeddings

Command: same as 7.2. Result (OFFLINE MEASURED, `n=32`, `d=6144`, isotropic,
mean-removed cosine per row):

| Quantity | Value |
|---|---|
| `cos(aligned, embed(same tokens))` | 0.999956 |
| `cos(hidden, embed(same tokens))` | -0.001618 |

The aligned prefix points almost exactly along the embedding of the tokens it came
from. The repository already encodes this expectation as a test:
`extensions/sglang-occ3/tests/test_align_diagnostics.py:45-54`,
`test_production_map_is_nearly_insensitive_to_the_hidden_states`, asserts
`cos_prefix_to_embedding > 0.999999`, `relative_prefix_error < 1e-3`,
`hidden_sensitivity < 1e-3`, `shuffled_hidden_sensitivity < 1e-3` (SOURCE-ATTESTED).
That test cites `MATHEMATICAL-DEGENERACY.md` at line 46; no file with that name
exists in the worktree (checked with `dir /s /b *DEGENERACY*`), so the citation is
currently dangling.

### 7.4 The untruncated `d`-dimensional solve is unstable at production width

Commands:

```
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_refA_vs_optimus.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_nullspace.py \
  extensions\sglang-occ3\occ3_sglang\statebridge.py
extensions\sglang-occ3\.venv\Scripts\python.exe \
  reports\occ3-alignment-repair\probes\probe_svd_orthogonality.py
```

`n = 16`, anisotropic synthetic rows, `rho = 1e-3`, `eps = 1e-6`. Relative Frobenius
differences throughout. OFFLINE MEASURED.

| d | Reference A float32 vs float64 | Reference A under a `1e-7` perturbation of `E` | output energy outside `span(q)` |
|---|---|---|---|
| 64 | 9.38e-05 | 3.74e-05 | 0.536 |
| 512 | 9.22e-03 | 4.97e-03 | 0.808 |
| 6144 | 0.2593 | 0.1730 | 0.800 |

The singular spectrum and the determinant:

| d | `||U^T U - I||_max` | zero-norm columns of `U` | `torch.det(R)` | `sigma_16 / sigma_max` |
|---|---|---|---|---|
| 256 | 1e-06 | 0 | 1.0000246 | 4.8e-07 |
| 1024 | 1e-06 | 0 | -0.0 | 4.8e-07 |
| 4096 | 3e-06 | 0 | -0.0 | 7.6e-07 |

Conclusions that the measurements support:
- `U` stays orthonormal, so `R` is a genuine orthogonal matrix. The `det` guard fails
  only because `torch.det` underflows, not because the SVD is malformed.
- At `d = 6144` the Reference A output is not reproducible: a `1e-7` change in the
  target moves it by `0.17`, and float32 differs from float64 by `0.26`.
- `80%` of its mean-removed energy lies outside `span(q)`, the `r`-dimensional
  row space of `[h_c; e_c]` and the only subspace with data support.
- HYPOTHESIS: the cause is the `d - rank` noise-floor singular triples included in
  `R = U V^T`. The mechanism is consistent with all three measurements but the
  individual term contributions were not isolated.

Practical consequence for anyone treating Reference A as the specification to match:
the specification is well defined as algebra (sections 2.1-2.8) and reproduces stably
only when `d` is comparable to the row count. At `d = 6144` with `n <= 128` its
numerical value is arbitrary in the low-order digits of the singular spectrum.

## 8. Summary of facts

1. Reference A's operator is a regularised whitening plus orthogonal Procrustes,
   `Z = Hc Ch^{-1/2} R Ce^{1/2} + 1 mu_E`, followed by scalar norm calibration to a
   global `tau` and a convex combination toward the nearest vocabulary row.
   SOURCE-ATTESTED.
2. Reference A fits and transmits on the same rows, per message. It has no
   calibration set and no frozen transform. SOURCE-ATTESTED.
3. LatentMesh is the only one of the three with a fit/apply separation, and its own
   doc comment flags its shipped `confidence` as an on-train number. SOURCE-ATTESTED.
4. CodeTalkers has no alignment step; both agents share one model, so the map is the
   identity. SOURCE-ATTESTED.
5. The Optimus reduced-subspace solver is algebraically the same operator as Reference
   A's full-space solver, restricted to the union subspace spanned by the message's
   centred rows. Agreement is measured at `3e-6 .. 6e-6` relative Frobenius for
   `d = 64..128`. At `d = 6144` the full-space solve is numerically unstable on its own
   (section 7.4), so the two cannot be compared by direct evaluation at that width.
   OFFLINE MEASURED.
6. Reference A's in-sample map is exact to float32 while its held-out residual is
   ~1.0 or worse. OFFLINE MEASURED on synthetic rows.
