
"""Probe 3: Reference A (full d-dim whitening Procrustes) vs the shipped Optimus
reduced-subspace version, on IDENTICAL rows. Isolates:
  (a) the Galerkin/truncation difference,
  (b) the missing det(R)=+1 correction,
  (c) the vocabulary-anchoring difference.
Synthetic stand-in rows. CPU, float32. No network, no GPU, no model.
"""
import importlib.util, sys, types, json, math
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
OPTIMUS = sb.align_hidden_sequence

REG, EPS, RRTOL = 1e-3, 1e-6, 1e-5

def sym(M, p, eps=EPS):
    M = (M + M.mT) * 0.5
    w, V = torch.linalg.eigh(M)
    return (V * w.clamp_min(eps).pow(p)) @ V.mT

def reference_A(H, E, target_norm, det_fix=True):
    """state_bridge.py:254-288 verbatim (snapping handled by caller)."""
    N, d = H.shape
    mu_H, mu_E = H.mean(0, keepdim=True), E.mean(0, keepdim=True)
    Hc, Ec = H - mu_H, E - mu_E
    I = torch.eye(d)
    Cov_H = (Hc.T @ Hc) / N + REG * I
    Cov_E = (Ec.T @ Ec) / N + REG * I
    Hw = Hc @ sym(Cov_H, -0.5)
    Ew = Ec @ sym(Cov_E, -0.5)
    M = Hw.T @ Ew
    U, _, Vt = torch.linalg.svd(M, full_matrices=False)
    R = U @ Vt
    if det_fix and torch.det(R) < 0:
        U = U.clone(); U[:, -1] *= -1; R = U @ Vt
    out = (Hc @ sym(Cov_H, -0.5)) @ R @ sym(Cov_E, 0.5) + mu_E
    nrm = out.norm(dim=-1, keepdim=True).clamp_min(1e-6)
    return out * (target_norm / nrm), R

def optimus_reduced(H, E, target_norm, det_fix=False):
    n, d = H.shape
    hc, ec = H - H.mean(0, keepdim=True), E - E.mean(0, keepdim=True)
    _, s, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
    rank = min(int((s > max(float(s[0]) * RRTOL, EPS)).sum()), 2 * (n - 1))
    q = vh[:rank].mT
    bh, be = hc @ q, ec @ q
    I = torch.eye(rank)
    ch = bh.mT @ bh / n + REG * I
    ce = be.mT @ be / n + REG * I
    x = bh @ sym(ch, -0.5); y = be @ sym(ce, -0.5)
    u, _, v = torch.linalg.svd(x.mT @ y, full_matrices=False)
    if det_fix and torch.det(u @ v) < 0:
        u = u.clone(); u[:, -1] *= -1
    z = (x @ (u @ v) @ sym(ce, 0.5)) @ q.mT + E.mean(0, keepdim=True)
    nrm = z.norm(dim=-1, keepdim=True)
    return z * (target_norm / nrm), rank, u @ v

g = torch.Generator().manual_seed(101)
n, d = 16, 6144
A = torch.randn(d, 48, generator=g) / math.sqrt(48)
H = (torch.randn(n, d, generator=g) @ A) @ torch.randn(48, d, generator=g) * 2.5 + torch.randn(n, d, generator=g)
Bm = torch.randn(d, 48, generator=g) / math.sqrt(48)
E = (torch.randn(n, d, generator=g) @ Bm) @ torch.randn(48, d, generator=g) * 1.7 + 1.2 + torch.randn(n, d, generator=g)
H, E = H.float(), E.float()
TN = 1.0

res = {}
ref, Rref = reference_A(H, E, TN, det_fix=True)
ref_nodet, Rref_nodet = reference_A(H, E, TN, det_fix=False)
opt, rank, Ropt = optimus_reduced(H, E, TN, det_fix=False)
opt_det, _, Ropt_det = optimus_reduced(H, E, TN, det_fix=True)
ship, info = OPTIMUS(H, E, torch.tensor(TN))

def rel(a, b):
    return float((a - b).norm() / b.norm())

res["rank"] = dict(optimus=info.union_rank, recomputed=rank, d=d)
res["det"] = dict(det_R_reference_A=float(torch.det(Rref)),
                  det_R_reference_A_nofix=float(torch.det(Rref_nodet)),
                  det_R_optimus=float(torch.det(Ropt)))
res["diffs_relative_frobenius"] = dict(
    optimus_vs_transcription=rel(opt, ship),
    optimus_vs_referenceA=rel(opt, ref),
    optimus_vs_referenceA_noDet=rel(opt, ref_nodet),
    optimusDet_vs_referenceA=rel(opt_det, ref),
    referenceA_vs_referenceA_noDet=rel(ref, ref_nodet))
print(json.dumps(res, indent=2))
