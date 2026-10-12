#!/usr/bin/env python3
"""Split LongMemEval-M full-run shard manifests into per-env driver manifests.

The 20 shard manifests (lme_m_full_v1_s00..s19.json, 25 envs each, ~146 MB)
make the full 500-env run a 20-worker job at best: one driver per shard
ingests its 25 envs sequentially, so a shard is ~25 x 8 h of wall clock. This
splitter re-shards the same inputs into ONE manifest per environment (~500
manifests, ~6 MB each) so the launcher can drive ~500 independent workers
(bounded by endpoint tolerance, not by shard count).

What is copied verbatim from each shard manifest: captureProtocol,
benchmark, variant, model, seed, the full settings block (ingestTimeoutS
43200, answer budgets, recall discipline) and the env body + gold-free
question declaration. What changes:

  - run_id    lme_m_env_<sha256(env_id)[:8]>   (short: Windows 260 limit)
  - output_dir <runs-root>/lme_m_env_<hash8>  (see PATH BUDGET below)
  - envs/questions hold exactly the one env / one question of that run
  - settings.maxImportChunkChars is raised 15000 -> 24000 by default (J's
    measured lever: ~26% fewer extraction calls, worst-case output ~18.2k
    tokens vs the un-raisable 32000 maxExtractionTokens cap; see
    docs/LMEM-SHARD-LAUNCHER.md section 3). Pass --keep-shard-chunk-chars
    to copy the shard value untouched instead.

PATH BUDGET (why run dirs are NOT nested under runs/lme_m_env_runs/):
the driver's deepest artifact is
  <output_dir>\envs\<qid>\agent\memory\projects\project_<24hex>\jobs\import_<24hex>.source.jsonl
i.e. output_dir + 111 + len(qid) characters (measured on the dev5 run, max
248 with its 125-char output_dir). Qid lengths in the full set are 8 (x368),
12 (x25), 13 (x102) and 17 (x5, the gpt4_*_abs envs). A nested
runs/lme_m_env_runs/lme_m_env_<hash8> output_dir is 135 chars, which gives
263 chars for 17-char qids and 259 for 13-char qids - over the Windows
260-char limit for 5 envs and a 1-char margin for 102 more. Run dirs
therefore live directly under <runs-root> (output_dir 121 chars, worst case
249, matching the shard layout's own 248). The splitter refuses to emit any
manifest whose worst-case artifact path reaches 260.

Sidecars: an _index.json (env_id -> run_id/shard/session count) and this
run's validation report are written next to the manifests. The launcher's
manifest-dir glob ignores _-prefixed files.

Exit code 0 only if every validation passes. Read-only on the shard
manifests; run data is never touched.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

BENCH_ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
DEFAULT_SHARD_DIR = BENCH_ROOT + "/reports/continuation/iteration3-inputs/manifests"
DEFAULT_RUNS_ROOT = BENCH_ROOT + "/reports/continuation/iteration3-inputs/runs"
DEFAULT_OUT_DIR = DEFAULT_SHARD_DIR + "/lme_m_env"
SHARD_GLOB = "lme_m_full_v1_s*.json"
RUN_PREFIX = "lme_m_env_"
# <output_dir> + \envs\<qid>\agent\memory\projects\project_<24>\jobs\import_<24>.source.jsonl
# = output_dir + 111 + len(qid) characters (see module docstring).
ARTIFACT_FIXED_CHARS = (
    len("/envs/") + len("/agent/memory/projects/project_") + 24
    + len("/jobs/import_") + 24 + len(".source.jsonl")
)  # = 111
WINDOWS_PATH_LIMIT = 260
RECOMMENDED_MAX_IMPORT_CHUNK_CHARS = 24000


def atomic_write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def env_run_id(env_id: str, hash_len: int) -> str:
    digest = hashlib.sha256(env_id.encode("utf-8")).hexdigest()
    return f"{RUN_PREFIX}{digest[:hash_len]}"


def worst_artifact_path_len(output_dir: str, qid: str) -> int:
    return len(output_dir) + ARTIFACT_FIXED_CHARS + len(qid)


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iter_shards(shard_dir: str, pattern: str):
    paths = sorted(glob.glob(os.path.join(shard_dir, pattern)))
    if not paths:
        raise SystemExit(f"no shard manifests match {os.path.join(shard_dir, pattern)}")
    for p in paths:
        yield p, load_json(p)


def split(args: argparse.Namespace) -> dict:
    os.makedirs(args.out_dir, exist_ok=True)
    index = {"envs": []}
    seen_run_ids: dict[str, str] = {}
    seen_output_dirs: dict[str, str] = {}
    shard_env_counts = {}
    shard_settings = None
    n_written = 0

    for shard_path, shard in iter_shards(args.shard_dir, args.shard_glob):
        shard_name = os.path.splitext(os.path.basename(shard_path))[0]
        if shard_settings is None:
            shard_settings = shard["settings"]
        elif shard["settings"] != shard_settings:
            raise SystemExit(f"{shard_name}: settings differ from the first shard; refusing")

        questions = {q["env_id"]: q for q in shard["questions"]}
        env_ids = [e["env_id"] for e in shard["envs"]]
        if set(questions) != set(env_ids) or len(questions) != len(env_ids):
            raise SystemExit(f"{shard_name}: env ids and question env_ids do not match 1:1")

        settings = dict(shard["settings"])
        if not args.keep_shard_chunk_chars:
            settings["maxImportChunkChars"] = args.max_import_chunk_chars

        for env in shard["envs"]:
            env_id = env["env_id"]
            run_id = env_run_id(env_id, args.hash_len)
            output_dir = os.path.join(args.runs_root, run_id)
            if run_id in seen_run_ids:
                raise SystemExit(f"run_id collision on {run_id} ({seen_run_ids[run_id]} vs {env_id}); rerun with --hash-len 10")
            if output_dir in seen_output_dirs:
                raise SystemExit(f"output_dir collision on {output_dir}")
            seen_run_ids[run_id] = env_id
            seen_output_dirs[output_dir] = env_id
            worst = worst_artifact_path_len(output_dir, env_id)
            if worst >= WINDOWS_PATH_LIMIT:
                raise SystemExit(
                    f"{env_id}: worst artifact path {worst} >= {WINDOWS_PATH_LIMIT} "
                    f"({output_dir}); shorten --runs-root or run prefix")

            manifest = {
                "captureProtocol": shard["captureProtocol"],
                "run_id": run_id,
                "benchmark": shard["benchmark"],
                "variant": shard["variant"],
                "output_dir": output_dir,
                "envs": [env],
                "questions": [questions[env_id]],
                "model": shard["model"],
                "settings": settings,
                "seed": shard["seed"],
            }
            out_path = os.path.join(args.out_dir, f"{run_id}.json")
            if os.path.exists(out_path) and not args.force:
                raise SystemExit(f"refusing to overwrite existing manifest {out_path} (use --force)")
            atomic_write_json(out_path, manifest)
            n_written += 1
            index["envs"].append({
                "env_id": env_id,
                "run_id": run_id,
                "manifest": os.path.join(args.out_dir, f"{run_id}.json"),
                "output_dir": output_dir,
                "shard": shard_name,
                "n_sessions": len(env["sessions"]),
                "worst_artifact_path_len": worst,
            })
        shard_env_counts[shard_name] = len(env_ids)
        print(f"[split] {shard_name}: {len(env_ids)} env manifests written")

    index["shard_env_counts"] = shard_env_counts
    index["n_env_manifests"] = n_written
    index["run_prefix"] = RUN_PREFIX
    index["hash_len"] = args.hash_len
    index["settings_source"] = "shard manifests, maxImportChunkChars->%d" % (
        args.max_import_chunk_chars if not args.keep_shard_chunk_chars
        else shard_settings["maxImportChunkChars"])
    atomic_write_json(os.path.join(args.out_dir, "_index.json"), index)
    print(f"[split] wrote {n_written} env manifests + _index.json to {args.out_dir}")
    return index


def validate(args: argparse.Namespace) -> int:
    """Full validation pass over the emitted (or existing) env manifest set.

    Streams: shard manifests and env manifests are loaded one at a time and
    only small summaries are kept (the full set is ~3 GB of JSON); the three
    schema samples are re-loaded individually for the field-by-field compare.
    """
    failures = []

    # ---- pass 1: shard-side expectations (streamed, discarded) ----------
    shard_qids: dict[str, int] = {}
    shard_envs: dict[str, int] = {}
    for shard_path, shard in iter_shards(args.shard_dir, args.shard_glob):
        for q in shard["questions"]:
            shard_qids[q["qid"]] = shard_qids.get(q["qid"], 0) + 1
        for e in shard["envs"]:
            shard_envs[e["env_id"]] = shard_envs.get(e["env_id"], 0) + 1
    expected_total = sum(shard_envs.values())

    # ---- pass 2: env-manifest side (streamed, summaries only) -----------
    manifest_files = sorted(
        p for p in glob.glob(os.path.join(args.out_dir, "*.json"))
        if not os.path.basename(p).startswith("_")
    )
    env_qids: dict[str, int] = {}
    summaries: dict[str, dict] = {}
    settings_hashes = set()
    for path in manifest_files:
        m = load_json(path)
        s = {
            "path": path,
            "run_id": m["run_id"],
            "output_dir": m["output_dir"],
            "env_ids": [e["env_id"] for e in m["envs"]],
            "qids": [q["qid"] for q in m["questions"]],
            "worst_path": max(worst_artifact_path_len(m["output_dir"], e["env_id"])
                              for e in m["envs"]),
        }
        summaries[m["run_id"]] = s
        settings_hashes.add(json.dumps(m["settings"], sort_keys=True))
        for qid in s["qids"]:
            env_qids[qid] = env_qids.get(qid, 0) + 1
        del m

    # ---- check 1: counts -------------------------------------------------
    n_manifests = len(manifest_files)
    if n_manifests != expected_total:
        failures.append(f"manifest count {n_manifests} != sum of shard envs {expected_total}")
    print(f"[validate] manifests={n_manifests} expected={expected_total} "
          f"({'OK' if n_manifests == expected_total else 'FAIL'})")

    # ---- check 2: every qid exactly once ---------------------------------
    dup_env = {q: c for q, c in env_qids.items() if c != 1}
    missing = set(shard_qids) - set(env_qids)
    extra = set(env_qids) - set(shard_qids)
    if dup_env or missing or extra:
        failures.append(f"qid set mismatch: env dups={dup_env} "
                        f"missing={sorted(missing)[:5]} extra={sorted(extra)[:5]}")
    print(f"[validate] qids unique and complete across the set: "
          f"{'OK' if not (dup_env or missing or extra) else 'FAIL'} "
          f"({len(env_qids)} qids)")

    # ---- check 3: uniqueness of run ids / output dirs / settings ----------
    run_ids = [s["run_id"] for s in summaries.values()]
    output_dirs = [s["output_dir"] for s in summaries.values()]
    if len(set(run_ids)) != len(run_ids):
        failures.append("duplicate run_ids across manifests")
    if len(set(output_dirs)) != len(output_dirs):
        failures.append("duplicate output_dirs across manifests")
    if len(settings_hashes) != 1:
        failures.append("settings differ across env manifests")
    print(f"[validate] run_ids unique={len(set(run_ids)) == len(run_ids)} "
          f"output_dirs unique={len(set(output_dirs)) == len(output_dirs)} "
          f"settings uniform={len(settings_hashes) == 1}")

    # ---- check 4: path budget -------------------------------------------
    worst_overall = max((s["worst_path"] for s in summaries.values()), default=0)
    for s in summaries.values():
        if s["worst_path"] >= WINDOWS_PATH_LIMIT:
            failures.append(f"{s['env_ids']}: worst artifact path {s['worst_path']} "
                            f">= {WINDOWS_PATH_LIMIT}")
    print(f"[validate] worst artifact path {worst_overall} "
          f"({'OK' if worst_overall < WINDOWS_PATH_LIMIT else 'FAIL'}, limit {WINDOWS_PATH_LIMIT})")

    # ---- check 5: field-by-field schema check on 3 samples ---------------
    # Deterministic samples: first by run_id, the longest env_id, the median.
    flat = sorted(summaries.values(), key=lambda s: s["run_id"])
    samples = []
    if flat:
        samples.append(flat[0])
        samples.append(max(flat, key=lambda s: max(len(e) for e in s["env_ids"])))
        samples.append(flat[len(flat) // 2])
    seen_sample_ids = set()
    for s in samples:
        if s["run_id"] in seen_sample_ids:
            continue
        seen_sample_ids.add(s["run_id"])
        env_id = s["env_ids"][0]
        # Locate the owning shard by streaming; stop at the first hit.
        shard = None
        shard_name = None
        for shard_path, cand in iter_shards(args.shard_dir, args.shard_glob):
            if any(e["env_id"] == env_id for e in cand["envs"]):
                shard, shard_name = cand, os.path.splitext(
                    os.path.basename(shard_path))[0]
                break
        if shard is None:
            failures.append(f"sample {s['run_id']}: env {env_id} not found in any shard")
            continue
        m = load_json(s["path"])
        shard_env = next(e for e in shard["envs"] if e["env_id"] == env_id)
        shard_q = next(q for q in shard["questions"] if q["env_id"] == env_id)
        expected_chunk = (shard["settings"]["maxImportChunkChars"]
                          if args.keep_shard_chunk_chars else args.max_import_chunk_chars)
        expected_settings = dict(shard["settings"])
        expected_settings["maxImportChunkChars"] = expected_chunk
        checks = {
            "captureProtocol": m["captureProtocol"] == shard["captureProtocol"],
            "benchmark": m["benchmark"] == shard["benchmark"],
            "variant": m["variant"] == shard["variant"],
            "model": m["model"] == shard["model"],
            "seed": m["seed"] == shard["seed"],
            "run_id": m["run_id"] == env_run_id(env_id, args.hash_len),
            "output_dir": m["output_dir"] == os.path.join(args.runs_root, m["run_id"]),
            "envs[0] deep-equal": m["envs"][0] == shard_env,
            "questions[0] deep-equal": m["questions"][0] == shard_q,
            "settings deep-equal": m["settings"] == expected_settings,
            "n_envs=1/n_questions=1": len(m["envs"]) == 1 and len(m["questions"]) == 1,
        }
        bad = [k for k, ok in checks.items() if not ok]
        status = "OK" if not bad else f"FAIL {bad}"
        print(f"[validate] sample {s['run_id']} ({env_id}, shard {shard_name}): {status}")
        if bad:
            failures.append(f"sample {s['run_id']}: fields failing: {bad}")
        del m, shard

    if failures:
        print("[validate] FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("[validate] all checks passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Split LME-M full-run shard manifests into per-env driver manifests.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--shard-dir", default=DEFAULT_SHARD_DIR)
    ap.add_argument("--shard-glob", default=SHARD_GLOB)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--runs-root", default=DEFAULT_RUNS_ROOT,
                    help="env run output dirs are written to <runs-root>/<run_id>")
    ap.add_argument("--hash-len", type=int, default=8,
                    help="hex chars of sha256(env_id) used in run ids")
    ap.add_argument("--max-import-chunk-chars", type=int,
                    default=RECOMMENDED_MAX_IMPORT_CHUNK_CHARS,
                    help="maxImportChunkChars written into the env manifests (J's lever)")
    ap.add_argument("--keep-shard-chunk-chars", action="store_true",
                    help="copy maxImportChunkChars from the shard manifests instead of overriding")
    ap.add_argument("--force", action="store_true", help="overwrite existing env manifests")
    ap.add_argument("--check-only", action="store_true",
                    help="only validate the existing out-dir against the shards")
    args = ap.parse_args()

    if not args.check_only:
        split(args)
    return validate(args)


if __name__ == "__main__":
    sys.exit(main())
