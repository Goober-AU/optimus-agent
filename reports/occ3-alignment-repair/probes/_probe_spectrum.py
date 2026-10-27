
import sys, json, math
sys.path.insert(0, r"C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3")
sys.path.insert(0, r"C:/Users/openclawuser/optimus-occ3-latent-validation/reports/occ3-alignment-repair/probes")
import numpy as np, torch
import measure_degeneracy as MD

cell = MD.make_cell("isotropic", 1.0, 128, 1)
h = torch.from_numpy(cell["h"]["correct_H"].astype(np.float32)); e = torch.from_numpy(cell["e"].astype(np.float32))
n = 128
mh, me = h.mean(0, keepdim=True), e.mean(0, keepdim=True)
hc, ec = h-mh, e-me
_, s, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
thr = max(float(s[0])*1e-5, 1e-6); r = min(int((s>thr).sum()), 2*(n-1))
q = vh[:r].mT; bh, be = hc@q, ec@q
eye = torch.eye(r)
ch = bh.mT@bh/n + 1e-3*eye
def sym(A,p,f=1e-6):
    w,V = torch.linalg.eigh((A+A.mT)*0.5); return (V*w.clamp_min(f).pow(p))@V.mT
x = bh@sym(ch,-0.5); y = be@sym(ch,-0.5)
lam = torch.linalg.eigvalsh(bh.double().mT@bh.double()/n).numpy()
print("rank r", r, "union sv[0]", float(s[0]))
print("lam_bh sorted desc[:5]", lam[::-1][:5], "asc[:5]", lam[:5])
print("n lam_bh > 1e-6*max:", int((lam > 1e-6*lam.max()).sum()), " n > 1e-9*max:", int((lam>1e-9*lam.max()).sum()))
sv = torch.linalg.svdvals(x).numpy()
print("sv_x/sqrt(n) desc[:5]", (sv[::-1][:5]/math.sqrt(n)), "asc[:5]", (sv[:5]/math.sqrt(n)))
print("min ratio", sv.min()/math.sqrt(n), "max", sv.max()/math.sqrt(n))
g = (x.double()@x.double().mT).numpy()
proj = n*(np.eye(n) - np.ones((n,n))/n)
d = g - proj
print("||g-proj||_2/n", np.linalg.norm(d,2)/n, "eig of g/n desc[:3]", np.linalg.eigvalsh(g/n)[::-1][:3],
      "asc[:3]", np.linalg.eigvalsh(g/n)[:3])
w = torch.linalg.eigvalsh(ch.double()).numpy()
print("ch eig desc[:5]", w[::-1][:5], "asc[:5]", w[:5])
