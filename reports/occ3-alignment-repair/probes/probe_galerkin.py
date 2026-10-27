
"""Probe 4: cheap (small d) exactness check of two claims:
  (1) ce == q^T Cov_E q  and  ch == q^T Cov_H q   (Galerkin identity), so the
      reduced solver IS the full-space Reference A solver restricted to span(q);
  (2) the shipped Optimus map equals the full-space Reference A map when the
      det constraint is removed, up to float32 roundoff.
Both at d small enough for a dense d x d whitening.
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

def refA(H, E, tn, det_fix):
    N, d = H.shape
    mH, mE = H.mean(0, keepdim=True), E.mean(0, keepdim=True)
    Hc, Ec = H - mH, E - mE
    I = torch.eye(d)
    Ch = (Hc.T @ Hc) / N + REG * I
    Ce = (Ec.T @ Ec) / N + REG * I
    Hw, Ew = Hc @ sym(Ch, -0.5), Ec @ sym(Ce, -0.5)
    U, _, Vt = torch.linalg.svd(Hw.T @ Ew, full_matrices=False)
    R = U @ Vt
    if det_fix and torch.det(R) < 0:
        U = U.clone(); U[:, -1] *= -1; R = U @ Vt
    out = Hw @ R @ sym(Ce, 0.5) + mE
    return out * (tn / out.norm(dim=-1, keepdim=True).clamp_min(1e-6))

def reduced(H, E, tn, det_fix):
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
    if det_fix and torch.det(u @ v) < 0:
        u = u.clone(); u[:, -1] *= -1
    z = ((bh @ sym(ch, -0.5)) @ (u @ v) @ sym(ce, 0.5)) @ q.mT + E.mean(0, keepdim=True)
    return z * (tn / z.norm(dim=-1, keepdim=True).clamp_min(1e-6)), rank, q, ch, ce, hc, ec

out = {}
for (n, d) in ((8, 64), (16, 64), (32, 128)):
    g = torch.Generator().manual_seed(1000 + n)
    H = torch.randn(n, d, generator=g)
    E = torch.randn(n, d, generator=g) * 1.5 + 2.0
    zr, rank, q, ch, ce, hc, ec = reduced(H, E, 1.0, False)
    Ch = (hc.T @ hc) / n + REG * torch.eye(d)
    Ce = (ec.T @ ec) / n + REG * torch.eye(d)
    galerkin_ch = float((ch - q.T @ Ch @ q).abs().max() / ch.abs().max())
    galerkin_ce = float((ce - q.T @ Ce @ q).abs().max() / ce.abs().max())
    a = reduced(H, E, 1.0, False)[0]
    b = refA(H, E, 1.0, det_fix=False)
    c = refA(H, E, 1.0, det_fix=True)
    ship, info = OPT(H, E, torch.tensor(1.0))
    out[f"n{n}_d{d}"] = dict(
        rank=rank, rank_shipped=info.union_rank, cap=2 * (n - 1),
        galerkin_rel_err_ch=galerkin_ch, galerkin_rel_err_ce=galerkin_ce,
        reduced_vs_refA_nodet_rel=float((a - b).norm() / b.norm()),
        reduced_vs_refA_det_rel=float((a - c).norm() / c.norm()),
        shipped_vs_reduced_rel=float((ship - a).norm() / a.norm()),
        det_refA_nofix=float(torch.det(torch.linalg.svd((hc @ sym(Ch,-0.5)).T @ (ec @ sym(Ce,-0.5)), full_matrices=False)[0] @ torch.linalg.svd((hc @ sym(Ch,-0.5)).T @ (ec @ sym(Ce,-0.5)), full_matrices=False)[2])))
print(json.dumps(out, indent=2))
