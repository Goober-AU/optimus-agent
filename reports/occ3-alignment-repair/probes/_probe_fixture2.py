
import sys, numpy as np, math
sys.path.insert(0, r"C:/Users/openclawuser/optimus-occ3-latent-validation/reports/occ3-alignment-repair/probes")
import measure_degeneracy as MD
rng = np.random.default_rng(7)
sd = MD.decay_sd()
u = rng.standard_normal(MD.DIM)*sd; u /= np.linalg.norm(u)
x = MD.draw_rows(rng, 8, "anisotropic", 1.0, sd, u)
print("row norm", np.linalg.norm(x,axis=1))
print("pair cos", MD.row_cos(x,x)[0,1:4])
print("dot with u", x@u)
print("expected", math.sqrt(0.24), math.sqrt(0.76))
