
"""Offline probe of the Optimus alignment math (CPU mirror of occ3_sglang/statebridge.py).

Loads the REAL source file by path (the package __init__ imports sglang, which is
not available on CPU), execs it with a stub package context, and measures:
  1. union subspace rank actually retained
  2. in-sample residual == 0 (transform fits the rows it is applied to)
  3. out-of-sample residual on fresh i.i.d. rows from the same process
  4. how much row energy the subspace truncation discards
  5. det(R) sign of the Procrustes factor
No network, no GPU, no model.
"""
import importlib.util, sys, types, math, json
import torch

SRC = sys.argv[1]

# Build a minimal parent package so "from .latent_types import ..." resolves.
pkg = types.ModuleType("occ3_probe")
pkg.__path__ = []
sys.modules["occ3_probe"] = pkg

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

ext = SRC.rsplit("/", 1)[0].replace("\\", "/")
ac = load("occ3_probe.attachment_contract", ext + "/attachment_contract.py")
setattr(pkg, "attachment_contract", ac)
lt = load("occ3_probe.latent_types", ext + "/latent_types.py")
setattr(pkg, "latent_types", lt)
sb = load("occ3_probe.statebridge", SRC)

ALIGN = sb.align_hidden_sequence

def make(gen, n, d):
    return gen((n, d))

def measure(n, d, seed, source_scale=1.0, target_scale=1.0):
    g = torch.Generator().manual_seed(seed)
    # Sender hidden rows: anisotropic Gaussian (stand-in for a hidden-state cloud).
    a = torch.randn(d, d, generator=g) / math.sqrt(d)
    z = torch.randn(n, d, generator=g) @ a
    H = z * source_scale
    # Receiver embedding rows for the SAME tokens: different mean/scale/aniso map.
    b = torch.randn(d, d, generator=g) / math.sqrt(d)
    E = (torch.randn(n, d, generator=g) @ b) * target_scale + 3.0
    target_norm = 1.0
    out, info = ALIGN(H, E, torch.tensor(target_norm))
    # Recover a residual in the un-normalised space: out is norm-scaled, so compare
    # only after normalising both sides identically.
    outn = out / out.norm(dim=-1, keepdim=True).clamp_min(1e-9)
    En = E / E.norm(dim=-1, keepdim=True).clamp_min(1e-9)
    return dict(n=n, d=d, rank=info.union_rank, retained=info.retained_count,
                H=H, E=E, out=out, outn=outn, En=En, target_norm=target_norm, info=info)

results = []
# --- A. rank retention and truncation energy ---
for n in (8, 16, 32, 64, 128):
    d = 6144
    g = torch.Generator().manual_seed(7)
    H = torch.randn(n, d, generator=g)
    E = torch.randn(n, d, generator=g) * 1.5 + 2.0
    hc = H - H.mean(0, keepdim=True); ec = E - E.mean(0, keepdim=True)
    _, s, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
    thr = max(float(s[0]) * 1e-5, 1e-6)
    rank_code = min(int((s > thr).sum()), 2 * (n - 1))
    total_energy = float((s ** 2).sum())
    kept_energy = float((s[:rank_code] ** 2).sum())
    q = vh[:rank_code].mT
    # Energy of the centered H rows that survives projection onto q.
    p_h = float(((hc @ q) ** 2).sum()) / max(float((hc ** 2).sum()), 1e-30)
    p_e = float(((ec @ q) ** 2).sum()) / max(float((ec ** 2).sum()), 1e-30)
    results.append(dict(case="rank", n=n, d=d, rank=rank_code, cap=2 * (n - 1),
                        union_energy_kept=round(kept_energy / total_energy, 6),
                        H_row_energy_in_subspace=round(p_h, 6),
                        E_row_energy_in_subspace=round(p_e, 6),
                        sigma_max=round(float(s[0]), 3), thr=thr))

# --- B. in-sample vs out-of-sample: does the fit reproduce the transmitted rows? ---
# The shipped code returns the aligned version of ITS OWN rows. There is no apply().
# Demonstrate that the same function applied to a DISJOINT second batch gives a
# large residual, i.e. nothing carries over.
g = torch.Generator().manual_seed(11)
n, d = 16, 6144
H1 = torch.randn(n, d, generator=g)
E1 = torch.randn(n, d, generator=g) * 1.5 + 2.0
out1, info1 = ALIGN(H1, E1, torch.tensor(1.0))
# Cosine agreement per row after both are placed on the target norm sphere.
c1 = torch.nn.functional.cosine_similarity(out1, (E1 / E1.norm(dim=-1, keepdim=True)) * 1.0, dim=-1)
H2 = torch.randn(n, d, generator=g)
E2 = torch.randn(n, d, generator=g) * 1.5 + 2.0
out2, info2 = ALIGN(H2, E2, torch.tensor(1.0))
c_cross = torch.nn.functional.cosine_similarity(out1, (E2 / E2.norm(dim=-1, keepdim=True)), dim=-1)
results.append(dict(case="in_vs_cross", n=n, d=d, rank=info1.union_rank,
                    cos_in_sample_mean=round(float(c1.mean()), 6),
                    cos_in_sample_min=round(float(c1.min()), 6),
                    cos_cross_mean=round(float(c_cross.mean()), 6)))

# --- C. determinism: same rows in => same rows out (a pure in-sample map) ---
out1b, _ = ALIGN(H1, E1, torch.tensor(1.0))
results.append(dict(case="determinism", max_abs_diff=float((out1 - out1b).abs().max())))

# --- D. rotation determinant of the Procrustes factor (no det correction in code) ---
dets = []
for seed in range(20):
    g = torch.Generator().manual_seed(seed)
    n2 = 16
    H = torch.randn(n2, d, generator=g)
    E = torch.randn(n2, d, generator=g)
    hc = H - H.mean(0, keepdim=True); ec = E - E.mean(0, keepdim=True)
    _, s, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
    rank = min(int((s > max(float(s[0]) * 1e-5, 1e-6)).sum()), 2 * (n2 - 1))
    q = vh[:rank].mT
    bh, be = hc @ q, ec @ q
    eye = torch.eye(rank)
    ch = bh.mT @ bh / n2 + 1e-3 * eye
    ce = be.mT @ be / n2 + 1e-3 * eye
    def sym(A, p, f=1e-6):
        w, V = torch.linalg.eigh((A + A.mT) * 0.5)
        return (V * w.clamp_min(f).pow(p)) @ V.mT
    x = bh @ sym(ch, -0.5); y = be @ sym(ce, -0.5)
    u, _, v = torch.linalg.svd(x.mT @ y, full_matrices=False)
    dets.append(round(float(torch.det(u @ v)), 6))
results.append(dict(case="det_R", rank=rank, dets=dets, neg=sum(1 for x in dets if x < 0)))

# --- E. the discarded residual is exactly zeroed, not identity-mapped ---
g = torch.Generator().manual_seed(3)
n3 = 16
H = torch.randn(n3, d, generator=g); E = torch.randn(n3, d, generator=g)
hc = H - H.mean(0, keepdim=True); ec = E - E.mean(0, keepdim=True)
_, s, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
rank = min(int((s > max(float(s[0]) * 1e-5, 1e-6)).sum()), 2 * (n3 - 1))
q = vh[:rank].mT
proj = (hc @ q) @ q.mT
resid = hc - proj
results.append(dict(case="truncation", n=n3, d=d, rank=rank,
                    resid_fro_rel=round(float(resid.norm() / hc.norm()), 6),
                    resid_abs_max=round(float(resid.abs().max()), 6)))

print(json.dumps(results, indent=2))
