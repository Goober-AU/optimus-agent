"""CLI for the ContextBench offline pipeline.

Subcommands:
  verify   - check the pinned parquet sha256 in a checkout
  split    - write the dev/holdout split manifest from the parquet
  smoke    - write the stratified 20-instance dev smoke subset
  arms     - list the arm configs and their status

All commands are offline. `split`/`smoke` need pyarrow (cb-venv).
"""

import argparse
import json
import os
import sys

from . import GOLD_PARQUET_RELPATH
from .arms import ARMS
from .dataset import (
    build_manifest,
    load_rows,
    stratified_subset,
    split_by_repo,
    verify_parquet_sha256,
    write_manifest,
)


def _default_parquet(refs_root: str) -> str:
    return os.path.join(refs_root, GOLD_PARQUET_RELPATH.replace("/", os.sep))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.context_rot_r2.contextbench",
        description="ContextBench offline evaluation pipeline",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_verify = sub.add_parser("verify", help="verify the pinned parquet sha256")
    p_verify.add_argument("--refs-root", required=True, help="path to the ContextBench checkout")

    p_split = sub.add_parser("split", help="write the dev/holdout split manifest")
    p_split.add_argument("--refs-root", required=True, help="path to the ContextBench checkout")
    p_split.add_argument("--out", required=True, help="manifest output path")
    p_split.add_argument("--dev-fraction", type=float, default=0.6)

    p_smoke = sub.add_parser("smoke", help="write the stratified dev smoke subset")
    p_smoke.add_argument("--refs-root", required=True, help="path to the ContextBench checkout")
    p_smoke.add_argument("--manifest", required=True, help="split manifest path")
    p_smoke.add_argument("--out", required=True, help="smoke subset output path")
    p_smoke.add_argument("--k", type=int, default=20)

    sub.add_parser("arms", help="list arm configs")

    args = parser.parse_args(argv)

    if args.cmd == "arms":
        for arm in ARMS.values():
            print(f"{arm.arm} {arm.name:28s} {arm.status:12s} {arm.description}")
            if arm.blocked_reason:
                print(f"   blocked: {arm.blocked_reason}")
        return 0

    if args.cmd == "verify":
        parquet = _default_parquet(args.refs_root)
        ok = verify_parquet_sha256(parquet)
        print(f"{parquet}: {'OK' if ok else 'SHA256 MISMATCH'}")
        return 0 if ok else 1

    if args.cmd == "split":
        parquet = _default_parquet(args.refs_root)
        if not verify_parquet_sha256(parquet):
            print(f"ERROR: sha256 mismatch for {parquet}", file=sys.stderr)
            return 1
        rows = load_rows(parquet)
        split = split_by_repo(rows, dev_fraction=args.dev_fraction)
        manifest = build_manifest(split, parquet)
        write_manifest(manifest, args.out)
        dev_n = manifest["split"]["dev"]["instance_count"]
        hold_n = manifest["split"]["holdout"]["instance_count"]
        print(
            f"split: dev {dev_n} instances / {manifest['split']['dev']['repo_count']} repos, "
            f"holdout {hold_n} instances / {manifest['split']['holdout']['repo_count']} repos "
            f"(seed {split.seed})"
        )
        print(f"manifest: {args.out}")
        return 0

    if args.cmd == "smoke":
        parquet = _default_parquet(args.refs_root)
        if not verify_parquet_sha256(parquet):
            print(f"ERROR: sha256 mismatch for {parquet}", file=sys.stderr)
            return 1
        with open(args.manifest, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        rows = load_rows(parquet)
        dev_ids = {
            inst["instance_id"] for inst in manifest["split"]["dev"]["instances"]
        }
        dev_rows = [row for row in rows if row["instance_id"] in dev_ids]
        subset = stratified_subset(dev_rows, k=args.k)
        payload = {
            "subset_version": "w10.1",
            "source": "dev split of data/contextbench_verified.parquet",
            "manifest": os.path.abspath(args.manifest),
            "k": args.k,
            "language_counts": {
                lang: sum(1 for r in subset if r["language"] == lang)
                for lang in sorted({r["language"] for r in subset})
            },
            "instances": [
                {
                    "instance_id": r["instance_id"],
                    "original_inst_id": r["original_inst_id"],
                    "repo": r["repo"],
                    "language": r["language"],
                    "source": r["source"],
                }
                for r in subset
            ],
        }
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        print(f"smoke subset: {len(subset)} instances -> {args.out}")
        print(f"languages: {payload['language_counts']}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
