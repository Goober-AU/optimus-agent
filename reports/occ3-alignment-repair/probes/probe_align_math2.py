
"""Probe 2: validate a transcription of the Optimus alignment, then measure
in-sample vs held-out residual in the CENTERED frame.

1. Re-derive (q, ch, ce, u, v, me) exactly as occ3_sglang/statebridge.py:45-63 does.
2. Check that an explicit fit/apply pair reproduces the shipped
   align_hidden_sequence(H, E, t) output to float32 tolerance  (transcription check).
3. Fit on batch 1, apply to batch 2 (disjoint i.i.d. rows), report centred residuals.
4. Report how close the aligned prefix is to the SAME tokens' own embeddings.
No network, no GPU, no model. Synthetic Gaussian stand-in for hidden states.
"""
import importlib.util, sys, types, math, json
import torch

SRC = sys.argv[1]

pkg = types.ModuleType("occ3_probe"); pkg.__path__ = []
sys.modules["occ3_probe"] = pkg
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod
ext = SRC.rsplit("/", 1)[0].replace("\\", "/")
ac = load("occ3_probe.attachment_contract", ext + "/attachment_contract.py"); setattr(pkg, "attachment_contract", ac)
lt = load("occ3_probe.latent_types", ext + "/latent_types.py"); setattr(pkg, "latent_types", lt)
sb = load("occ3_probe.statebridge", SRC)
ALIGN = sb.align_hidden_sequence

RIDGE, EFLOOR, RRTOL = 1e-3, 1e-6, 1e-5   # statebridge.py:32-33 defaults

def sym(M, power, floor=EFLOOR):
    w, V = torch.linalg.eigh((M + M.mT) * 0.5)
    return (V * w.clamp_min(floor).pow(power)) @ V.mT

def fit(H, E):
    """Exact transcription of statebridge.py:45-63."""
    n, d = H.shape
    h, e = H.float(), E.float()
    mh, me = h.mean(0, keepdim=True), e.mean(0, keepdim=True)
    hc, ec = h - mh, e - me
    _, s, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
    rank = min(int((s > max(float(s[0]) * RRTOL, EFLOOR)).sum()), 2 * (n - 1))
    q = vh[:rank].mT
    bh, be = hc @ q, ec @ q
    eye = torch.eye(rank)
    ch = bh.mT @ bh / n + RIDGE * eye
    ce = be.mT @ be / n + RIDGE * eye
    u, _, v = torch.linalg.svd((bh @ sym(ch, -0.5)).mT @ (be @ sym(ce, -0.5)), full_matrices=False)
    return dict(q=q, ch=ch, ce=ce, R=u @ v, me=me, rank=rank, n=n, d=d)

def apply(f, Z):
    """z = (x @ R @ Ce^{1/2}) @ q^T + me   (statebridge.py:60-63)."""
    hc = Z.float() - f["me"] - (Z.float().mean(0, keepdim=True) - f["me"])  # centre on sender mean
    hc = Z.float() - Z.float().mean(0, keepdim=True)
    bh = hc @ f["q"]
    x = bh @ sym(f["ch"], -0.5)
    return (x @ f["R"] @ sym(f["ce"], 0.5)) @ f["q"].mT + f["me"]

def resid(pred, target, centre):
    p = pred - centre; t = target - centre
    return float((p - t).norm() / t.norm().clamp_min(1e-12))

out = {}

# ---- 1. transcription check against the shipped function -------------------
g = torch.Generator().manual_seed(5)
n, d = 16, 6144
H = torch.randn(n, d, generator=g)
E = torch.randn(n, d, generator=g) * 1.5 + 2.0
shipped, info = ALIGN(H, E, torch.tensor(1.0))
f = fit(H, E)
raw = apply(f, H)
# undo the norm calibration the shipped function applies last
raw_n = raw * (1.0 / raw.norm(dim=-1, keepdim=True).clamp_min(1e-6))
out["transcription"] = dict(
    rank_shipped=info.union_rank, rank_transcribed=f["rank"],
    max_abs_diff_raw=float((shipped - raw_n).abs().max()),
    rel_fro_diff=float((shipped - raw_n).norm() / shipped.norm()))

# ---- 2. in-sample vs held-out, centred frame ------------------------------
for label, aniso in (("isotropic", False), ("anisotropic", True)):
    g = torch.Generator().manual_seed(21)
    n, d = 16, 6144
    def draw(k):
        Z = torch.randn(k, d, generator=g)
        if aniso:                      # low-rank-ish cloud, 32 effective dims
            A = torch.randn(d, 32, generator=g) / math.sqrt(32)
            Bm = torch.randn(32, d, generator=g)
            Z = (Z @ A) @ Bm * 3.0 + Z
        return Z
    H1, E1 = draw(n), draw(n) * 1.4 + 1.0
    H2, E2 = draw(n), draw(n) * 1.4 + 1.0
    f1 = fit(H1, E1)
    p1 = apply(f1, H1)      # in-sample
    p2 = apply(f1, H2)      # held-out rows through the batch-1 transform
    me = f1["me"]
    out["generalisation_" + label] = dict(
        rank=f1["rank"],
        in_sample_centred_resid=round(resid(p1, E1, me), 6),
        heldout_centred_resid=round(resid(p2, E2, me), 6),
        # baseline: send the sender rows unchanged (no alignment at all)
        unaligned_centred_resid=round(resid(H2, E2, E2.mean(0, keepdim=True)), 6))

# ---- 3. how close is the aligned prefix to the same tokens' own embeddings?
g = torch.Generator().manual_seed(33)
n, d = 32, 6144
H = torch.randn(n, d, generator=g)
E = torch.randn(n, d, generator=g) * 1.5 + 2.0
shipped, info = ALIGN(H, E, torch.tensor(1.0))
meE = E.mean(0, keepdim=True)
c = torch.nn.functional.cosine_similarity(shipped - shipped.mean(0, keepdim=True),
                                         E - meE, dim=-1)
c_raw = torch.nn.functional.cosine_similarity(H - H.mean(0, keepdim=True), E - meE, dim=-1)
out["aligned_vs_own_embeddings"] = dict(
    n=n, rank=info.union_rank,
    centred_cos_aligned_mean=round(float(c.mean()), 6),
    centred_cos_unaligned_mean=round(float(c_raw.mean()), 6))

print(json.dumps(out, indent=2))
