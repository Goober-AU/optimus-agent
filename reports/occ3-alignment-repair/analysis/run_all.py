"""Run the calibration study end to end: main grid, then the sensitivity grids.

  python reports/occ3-alignment-repair/analysis/run_all.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calibration_experiment as ce  # noqa: E402

ANALYSIS = "reports/occ3-alignment-repair/analysis"
SENS = os.path.join(ANALYSIS, "sens")

MAIN = ["--ms", "128,512,2048", "--seeds", "0,1,2",
        "--scenarios", "null,weak,mid,strong", "--lambdas", "1e-4,1e-3,1e-2",
        "--out", os.path.join(ANALYSIS, "calibration_results.json"),
        "--tables", os.path.join(ANALYSIS, "tables")]

SENSITIVITIES = [
    ("kappa0", ["--kappa", "0.0", "--scenarios", "mid", "--ms", "512", "--seeds", "0,1,2"]),
    ("kappa35", ["--kappa", "0.35", "--scenarios", "mid", "--ms", "512", "--seeds", "0,1,2"]),
    ("kappa70", ["--kappa", "0.7", "--scenarios", "mid", "--ms", "512", "--seeds", "0,1,2"]),
    ("cap64", ["--rank-cap", "64", "--scenarios", "mid", "--ms", "512", "--seeds", "0,1"]),
    ("cap128", ["--rank-cap", "128", "--scenarios", "mid", "--ms", "512", "--seeds", "0,1"]),
    ("cap512", ["--rank-cap", "512", "--scenarios", "mid", "--ms", "512", "--seeds", "0,1"]),
    ("spec12", ["--spec-exp", "1.2", "--scenarios", "mid,strong", "--ms", "512", "--seeds", "0,1"]),
    ("lam", ["--scenarios", "mid", "--ms", "128,512,2048", "--seeds", "0,1",
             "--lambdas", "1e-5,1e-3,1e-1"]),
]  # every run keeps the primary lambda, so a sweep entry only adds the listed ones


def main():
    started = time.time()
    print("=== main grid", flush=True)
    ce.main(MAIN)
    for name, argv in SENSITIVITIES:
        target = os.path.join(SENS, name)
        os.makedirs(target, exist_ok=True)
        extra = [] if "--lambdas" in argv else ["--lambdas", "1e-3"]
        print(f"=== {name}", flush=True)
        ce.main(argv + extra + ["--out", os.path.join(SENS, f"{name}.json"),
                                "--tables", target, "--label", name])
    print(f"all done in {time.time() - started:.1f}s", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
