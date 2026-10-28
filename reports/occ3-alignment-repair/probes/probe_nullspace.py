
"""Probe 5: why do the reduced (Optimus) and full-space (Reference A) solvers
disagree by ~33% at d=6144 but agree to ~4e-6 at d=64..128?

Hypothesis: at d >> n the full-space orthogonal factor R = U V^T is rank-deficient
(rank(Hw^T Ew) <= n-1), so the returned d x d matrix depends on an ARBITRARY
null-space completion, and part of the output leaves span(q) where the data
constrains nothing. Tests:
  T1  is the full-space output numerically well-determined? float32 vs float64,
      and a 1e-7 relative perturbation of the target E.
  T2  what fraction of the full-space output energy leaves span(q) + mu_e ?
Also reports the same at d=512 and d=64 for scale comparison.
"""
import importlib.util, sys, types, json
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
OPT = sb.align_hidden_sequence

REG, EPS, RRTOL = 1e-3, 1e-6, 1e-5
def sym(M, p, eps=EPS):
    M = (M + M.mT) * 0.5
    w, V = torch.linalg.eigh(M)
    return (V * w.clamp_min(eps).pow(p)) @ V.mT

def refA(H, E, tn, dtype=torch.float32, det_fix=True):
    H, E = H.to(dtype), E.to(dtype)
    N, d = H.shape
    mH, mE = H.mean(0, keepdim=True), E.mean(0, keepdim=True)
    Hc, Ec = H - mH, E - mE
    I = torch.eye(d, dtype=dtype)
    Ch = (Hc.T @ Hc) / N + REG * I
    Ce = (Ec.T @ Ec) / N + REG * I
    Hw, Ew = Hc @ sym(Ch, -0.5), Ec @ sym(Ce, -0.5)
    U, _, Vt = torch.linalg.svd(Hw.T @ Ew, full_matrices=False)
    R = U @ Vt
    if det_fix:
        dR = torch.det(R)
        if dR < 0:
            U = U.clone(); U[:, -1] *= -1; R = U @ Vt
    out = Hw @ R @ sym(Ce, 0.5) + mE
    return (out * (tn / out.norm(dim=-1, keepdim=True).clamp_min(1e-6))).to(torch.float32)

def reduced(H, E, tn):
    n, d = H.shape
    hc, ec = H - H.mean(0, keepdim=True), E - E.mean(0, keepdim=True)
    _, s, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
    rank = min(int((s > max(float(s[0]) * RRTOL, EPS)).sum()), 2 * (n - 1))
    q = vh[:rank].mT
    bh, be = hc @ q, ec @ q
    I = torch.eye(rank)
    ch = bh.mT @ bh / n + REG * I
    ce = be.mT @ be / n + REG * I
    u, _, v = torch.linalg.svd((bh @ sym(ch, -0.5)).mT @ (be @ sym(ce, -0.5)), full_matrices=False)
    z = ((bh @ sym(ch, -0.5)) @ (u @ v) @ sym(ce, 0.5)) @ q.mT + E.mean(0, keepdim=True)
    return z * (tn / z.norm(dim=-1, keepdim=True).clamp_min(1e-6)), q

def rel(a, b):
    return float((a - b).norm() / b.norm().clamp_min(1e-30))

out = {}
for d in (64, 512, 6144):
    n = 16
    g = torch.Generator().manual_seed(4242)
    A = torch.randn(d, 48, generator=g) / (48 ** 0.5)
    H = (torch.randn(n, d, generator=g) @ A) @ torch.randn(48, d, generator=g) * 2.5 + torch.randn(n, d, generator=g)
    Bm = torch.randn(d, 48, generator=g) / (48 ** 0.5)
    E = (torch.randn(n, d, generator=g) @ Bm) @ torch.randn(48, d, generator=g) * 1.7 + 1.2 + torch.randn(n, d, generator=g)
    H, E = H.float(), E.float()
    z32 = refA(H, E, 1.0, torch.float32)
    z64 = refA(H, E, 1.0, torch.float64)
    g2 = torch.Generator().manual_seed(9)
    E_pert = E + 1e-7 * E.abs().mean() * torch.randn(n, d, generator=g2)
    z32p = refA(H, E_pert, 1.0, torch.float32)
    zr, q = reduced(H, E, 1.0)
    me = E.mean(0, keepdim=True)
    # fraction of full-space output energy outside span(q) after mean removal
    c = z32 - me
    in_q = (c @ q) @ q.mT
    frac_outside = float((c - in_q).norm() / c.norm())
    out[f"d{d}"] = dict(
        n=n, rank=int(q.shape[1]),
        fullspace_float32_vs_float64=rel(z32, z64),
        fullspace_perturbation_1e7=rel(z32p, z32),
        reduced_vs_float64_fullspace=rel(zr, z64),
        reduced_vs_float32_fullspace=rel(zr, z32),
        fullspace_output_energy_outside_span_q=round(frac_outside, 6))
print(json.dumps(out, indent=2))
