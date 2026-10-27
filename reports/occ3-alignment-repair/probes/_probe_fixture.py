
import sys, numpy as np, math
sys.path.insert(0, r"C:/Users/openclawuser/optimus-occ3-latent-validation/reports/occ3-alignment-repair/probes")
import measure_degeneracy as MD
for fam in ("isotropic","anisotropic"):
    cell = MD.make_cell(fam, 1.0, 128, 1)
    e = cell["e"]; n=len(e)
    c = MD.row_cos(e,e)
    print(fam, "pair cos off-diag", (c.sum()-n)/(n*(n-1)), "row norm", np.linalg.norm(e,axis=1).mean(),
          "erank", MD.erank(e)[0], "min/max row norm", np.linalg.norm(e,axis=1).min(), np.linalg.norm(e,axis=1).max())
    w = cell["w"]; cw = MD.row_cos(w[:64], w[:64])
    print("   vocab pair cos", (cw.sum()-64)/(64*63))
