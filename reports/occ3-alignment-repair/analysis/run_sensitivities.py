"""Sequential driver for the calibration sensitivities.

One process, one thread pool, one config after another: the sensitivity runs are
independent but they contend for the same six CPU threads, so running them
sequentially is faster than running them concurrently.

  python reports/occ3-alignment-repair/analysis/run_sensitivities.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calibration_experiment as ce  # noqa: E402

OUT = "reports/occ3-alignment-repair/analysis/sens"
BASE = ["--ms", "512", "--seeds", "0,1", "--lambdas", "1e-3"]

RUNS = [
    ("kappa0", ["--kappa", "0.0", "--scenarios", "mid"]),
    ("kappa35", ["--kappa", "0.35", "--scenarios", "mid"]),
    ("kappa70", ["--kappa", "0.7", "--scenarios", "mid"]),
    ("cap64", ["--rank-cap", "64", "--scenarios", "mid"]),
    ("cap128", ["--rank-cap", "128", "--scenarios", "mid"]),
    ("cap512", ["--rank-cap", "512", "--scenarios", "mid"]),
    ("spec12", ["--spec-exp", "1.2", "--scenarios", "mid,strong"]),
    ("lam-sweep", ["--scenarios", "mid", "--ms", "128,512,2048", "--seeds", "0,1",
                   "--lambdas", "1e-4,1e-2,1e-1"]),
]


def main():
    started = time.time()
    for name, argv in RUNS:
        target = os.path.join(OUT, name)
        os.makedirs(target, exist_ok=True)
        print(f"=== {name}: {' '.join(argv)}", flush=True)
        ce.main(BASE[:0] + argv + ["--out", os.path.join(OUT, f"{name}.json"),
                                   "--tables", target, "--label", name])
    print(f"all sensitivities done in {time.time() - started:.1f}s", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
