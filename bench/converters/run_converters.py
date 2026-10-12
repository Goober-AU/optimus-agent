#!/usr/bin/env python
"""Entry point: regenerate the common-format benchmark files.

Default (no flags): dev subsets + dev/full manifests for all four families.
  longmemeval   100 envs/questions      (data/common/longmemeval/)
  locomo        10 envs, 300 questions  (data/common/locomo/)
  lme_v2        2 envs, 120 questions  (data/common/lme_v2/)
  locomo_plus   120 envs/questions      (data/common/locomo_plus/)

--full ADDITIONALLY emits the complete env/qs sets under <family>/full/
for the supplied inputs. --full first regenerates dev outputs in the family
roots, then writes the full sets. Use a fresh dedicated output root.

Deterministic: seed 20261007 (bench.converters.common.SEED); sampling uses
md5(seed:key) ordering -- see common.py.

Usage:
  python -m bench.converters.run_converters                     # dev subsets
  python -m bench.converters.run_converters --family locomo     # one family
  python -m bench.converters.run_converters --full              # dev + full sets
"""

from __future__ import annotations

import argparse
import time

FAMILY_NAMES = ("longmemeval", "locomo", "lme_v2", "locomo_plus")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family", choices=sorted(FAMILY_NAMES), default=None,
                    help="convert only this family (default: all)")
    ap.add_argument("--full", action="store_true",
                    help="also emit the complete env/qs sets under <family>/full/")
    ap.add_argument("--data-root", help="raw input directory (or MEMORY_BENCH_DATA_ROOT)")
    ap.add_argument("--out-dir", help="common output root (or MEMORY_BENCH_COMMON_ROOT)")
    ap.add_argument("--lme-v2-axtree-cap", type=int, default=None, metavar="CHARS",
                    help="override the lme_v2 state-observation accessibility-tree cap "
                         "(sets LME_V2_AXTREE_CAP for this invocation; must be >= 1). "
                         "Raises retention above the converter default; the chosen "
                         "value is recorded in each env's notes.axtree_cap_chars and in "
                         "the family manifest stats. Use a large explicit value (for "
                         "example 1000000) for effectively-full capture.")
    args = ap.parse_args(argv)

    # The lme_v2 converter resolves LME_V2_AXTREE_CAP at import time, so the
    # flag must be applied before the family modules are imported.
    if args.lme_v2_axtree_cap is not None:
        if args.lme_v2_axtree_cap < 1:
            ap.error("--lme-v2-axtree-cap must be >= 1")
        import os
        os.environ["LME_V2_AXTREE_CAP"] = str(args.lme_v2_axtree_cap)

    from . import longmemeval, locomo, lme_v2, locomo_plus
    from .common import configure_paths
    families = {
        "longmemeval": longmemeval,
        "locomo": locomo,
        "lme_v2": lme_v2,
        "locomo_plus": locomo_plus,
    }
    configure_paths(args.data_root, args.out_dir)

    fams = [args.family] if args.family else list(families)
    for name in fams:
        mod = families[name]
        t0 = time.time()
        manifest = mod.build(dev_only=not args.full)
        dt = time.time() - t0
        print(f"[{name}] envs={manifest['n_envs']} questions={manifest['n_questions']} "
              f"chars={manifest['total_env_chars']:,} "
              f"~tokens={manifest['estimated_ingest_tokens']:,} ({dt:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
