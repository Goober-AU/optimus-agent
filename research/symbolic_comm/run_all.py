#!/usr/bin/env python
"""Reproduce the full OCC-2 Phase 4 + Phase 5 pipeline.

Usage (from the worktree root, Python 3.11):

    python research/symbolic_comm/run_all.py            # full pipeline
    python research/symbolic_comm/run_all.py --gens 6   # shorter evolution

Requires the local model gateway at http://127.0.0.1:43120 and the token
file at the path hardcoded in gateway.py (or OCC2_TOKEN_FILE env var).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gens", type=int, default=10)
    args = parser.parse_args()

    result = subprocess.run(
        [sys.executable, str(HERE / "evolve.py"),
         "--generations", str(args.gens)])
    if result.returncode != 0:
        sys.exit("evolution failed")
    shutil.copyfile(HERE / "evolution-winner.json", HERE / "dialect-card.json")
    print("card frozen: dialect-card.json")
    result = subprocess.run([sys.executable, str(HERE / "eval.py")])
    if result.returncode != 0:
        sys.exit("evaluation failed")
    print("done: eval-results.json")


if __name__ == "__main__":
    main()
