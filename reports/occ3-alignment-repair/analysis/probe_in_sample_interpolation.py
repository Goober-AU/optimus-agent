"""Is the production operator an in-sample interpolator? Direct measurement.

Fits ``occ3_sglang.statebridge.align_hidden_sequence`` on (H, E) and reports, for
isotropic Gaussian rows and for fixture-shaped anisotropic rows:

  rel_dir   relative direction error between the output and the norm-calibrated E
  row_cos   mean row cosine between the output and the norm-calibrated E
  swap      relative output change when H is replaced by unrelated rows (E fixed)
  shuffle   relative output change when the H rows are permuted

  python reports/occ3-alignment-repair/analysis/probe_in_sample_interpolation.py
"""
from __future__ import annotations

import os
import sys

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "extensions", "sglang-occ3"))
sys.path.insert(0, HERE)

import calibration_methods as cm  # noqa: E402
import calibration_experiment as ce  # noqa: E402
from occ3_sglang.statebridge import align_hidden_sequence  # noqa: E402

torch.set_grad_enabled(False)
N, D = 128, 6144


def measure(h, e, h_other, target=1.0):
    z, info = align_hidden_sequence(h, e, target)
    en = cm.renorm_rows(e, target)
    zu, eu = cm.unit_rows(z), cm.unit_rows(en)
    other, _ = align_hidden_sequence(h_other, e, target)
    perm = torch.randperm(h.shape[0], generator=torch.Generator().manual_seed(3))
    shuffled, _ = align_hidden_sequence(h[perm], e, target)
    return {
        "rank": info.union_rank,
        "rel_dir": float((zu - eu).norm() / eu.norm()),
        "row_cos": float((zu * eu).sum(dim=-1).mean()),
        "swap": float((other - z).norm() / z.norm()),
        "shuffle": float((shuffled - z).norm() / z.norm()),
    }


def main():
    print(f"production align_hidden_sequence, n={N}, d={D}, target_norm=1.0")
    print(f"{'rows':22s} {'seed':>4s} {'rank':>5s} {'rel_dir':>8s} {'row_cos':>8s} {'swap':>8s} {'shuffle':>8s}")
    for seed in (0, 1, 2):
        g = torch.Generator().manual_seed(500 + seed)
        h = torch.randn(N, D, generator=g)
        e = torch.randn(N, D, generator=g)
        h2 = torch.randn(N, D, generator=g)
        m = measure(h, e, h2)
        print(f"{'isotropic gaussian':22s} {seed:4d} {m['rank']:5d} {m['rel_dir']:8.6f} "
              f"{m['row_cos']:8.6f} {m['swap']:8.6f} {m['shuffle']:8.6f}")
    for seed in (0, 1, 2):
        w = ce.World(dim=D, content_rank=64, spec_exp=0.7, target_pair=0.24, f_content=0.55,
                     shared=True, seed=seed, norm_sigma=0.15, kappa=0.5)
        h, e, _ = w.msg(N)
        h2, _, _ = w.probe(N)
        m = measure(h, e, h2)
        print(f"{'anisotropic fixture':22s} {seed:4d} {m['rank']:5d} {m['rel_dir']:8.6f} "
              f"{m['row_cos']:8.6f} {m['swap']:8.6f} {m['shuffle']:8.6f}")
    print("isotropic rows: row-space rank is saturated so the whitened clouds are congruent and")
    print("the fitted rotation reproduces E; anisotropic rows: the rank criterion truncates, so a")
    print("small residual remains. In both cases the swap/shuffle columns show that the output is")
    print("determined by E, not by H.")


if __name__ == "__main__":
    raise SystemExit(main())
