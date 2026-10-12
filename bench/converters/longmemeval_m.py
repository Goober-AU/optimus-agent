"""Local LongMemEval-M data adapter (500-session haystack variant).

LongMemEval-M reuses the LongMemEval-S question set (same 500 qids, question
texts, answers and evidence session ids) over the official cleaned M haystacks
(~500 sessions, ~1.2M estimated tokens per environment vs ~115k for S). The
question and scoring protocols stay the `longmemeval` family; only the
ingested history differs from the S adapter. Question rows therefore hash
differently from their S counterparts whenever the M history moved evidence
sessions or question dates, which is expected.

Common outputs (H1 contract, family dir `longmemeval_m`):
  data/common/longmemeval_m/env_<qid>.json    -- M haystack environments
  data/common/longmemeval_m/qs_<qid>.jsonl    -- scoring questions (gold kept)
  data/common/longmemeval_m/manifest.json    -- dev5 selection manifest
  data/common/longmemeval_m/full/manifest.json -- full 500 planning manifest

Driver manifests are assembled by `build_run_manifest()` /
`build_full_shards()` with the schema of the LongMemEval-S campaign manifests
(envs[].sessions[].events with role/text/ext_id/ts, gold-free question
declarations, model dgx-glm53/glm-5.3). The M dataset file is the HuggingFace
`longmemeval_m_cleaned.json` saved locally as `longmemeval_m.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time

from .common import (clean_outputs, get_data_root, family_dir, write_json, write_jsonl,
                      env_stats)
from . import longmemeval as lme_s

DATASET_FILE = "longmemeval_m.json"
OUT_FAMILY = "longmemeval_m"
QUESTION_FAMILY = "longmemeval"
# Same content root as the S campaign prep (reports/continuation/
# longmemeval-comparison-inputs): the five dev qids are the first five of the
# declared 100-qid LongMemEval dev set ordered by SHA256(qid + seed).
DEV_SELECT_SEED = "20261010"
N_SELECT = 5
PINNED_DEV5 = ("15745da0_abs", "bc8a6e93_abs", "e61a7584", "e831120c", "gpt4_78cf46a3")
CAPTURE_PROTOCOL = "optimus-memory-capture/temporal-authority/1.0.0"
# ANSWER-PROTOCOLS.md variant (a), LME (longmemeval), 144 words - verbatim
# (bench/ANSWER-PROTOCOLS.md @ 0f13fa7e0). Validated by the E3-06 A/B
# (iteration3-inputs/runs/lme_protocol_ab: protocol 1.00 (5/5) with
# recallQueryExtract true, non-regressive vs the old S-inherited instruction;
# +951 output tokens/answer visible-scratchpad cost, accepted for the
# intermediate-step-tolerant official-lme-v1 judge).
ANSWER_INSTRUCTION = (
    "Answer only from the memory notes provided above. Supersession: when notes "
    "conflict about the same fact, the latest \"at\" timestamp wins; never average "
    "or merge versions. Before any date, order, or duration answer, list the dates "
    "you see for each relevant fact, then reason from it. Before any count, "
    "enumerate each unique item with its date, keep only items consistent with the "
    "latest state, then count. Verify the note's entity \u2014 person, item, or show "
    "\u2014 is exactly the one asked about; a similar name is not a match. When a fact "
    "changed across sessions, state the current value first, earlier values as "
    "background. Answer comparisons only when both sides are recorded. Check the "
    "premise first: if it assumes what the notes contradict, say so. If no note "
    "states the answer, say the available memory does not record it, then give what "
    "is recorded."
)
# Iteration-3 improved arm: recall + distill/rerank/extract/expansion on,
# rerank pool 25, six entries / 8000 chars recall discipline; remaining keys
# mirror the LongMemEval-S improved manifest (chunking, extraction and answer
# budgets). ingestTimeoutS is raised from the 3600s default because one M
# haystack is ~10x the S ingest work.
IMPROVED_ARM_SETTINGS = {
    "recallOn": True,
    "recallQueryDistillation": True,
    "recallRerank": True,
    "recallRerankCandidates": 25,
    "recallQueryExtract": True,
    "recallQueryExpansion": True,
    "recallQueryExpansionDraws": 2,
    "maxRecallEntries": 6,
    "maxRecallChars": 8000,
    "answerMaxTokens": 8192,
    "maxExtractionTokens": 32000,
    "maxImportChunkChars": 15000,
    "disableTools": True,
    "autoRefine": {"enabled": False},
    "answerInstruction": ANSWER_INSTRUCTION,
    "questionTimeoutS": 240,
    "ingestTimeoutS": 43200,
    "skipIngest": False,
}
RUN_SEED = 20261010


def load_rows():
    path = os.path.join(get_data_root(), DATASET_FILE)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dataset_provenance():
    path = os.path.join(get_data_root(), DATASET_FILE)
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            digest.update(block)
            size += len(block)
    return {
        "file": DATASET_FILE,
        "source": "huggingface:xiaowu0162/longmemeval-cleaned longmemeval_m_cleaned.json",
        "sha256": digest.hexdigest(),
        "bytes": size,
    }


def dev5_selection(rows):
    """The five LongMemEval-S dev5 qids, derived from the declared S dev set.

    Ranks the declared 100-qid S dev manifest (data/common/longmemeval/
    manifest.json) by SHA256(qid + seed) ascending, ties by qid, and takes the
    first five. Fails closed if the derived selection differs from the
    recorded historical dev5 selection.
    """
    dev_manifest_path = os.path.join(get_data_root(), "common", "longmemeval", "manifest.json")
    with open(dev_manifest_path, "r", encoding="utf-8") as f:
        s_manifest = json.load(f)
    if s_manifest.get("family") != "longmemeval" or s_manifest.get("n_envs") != 100:
        raise SystemExit("declared S dev manifest is not the 100-env longmemeval dev set")
    pool = sorted(env["env_id"] for env in s_manifest["envs"])
    ranked = sorted(pool, key=lambda q: (hashlib.sha256((q + DEV_SELECT_SEED).encode("utf-8")).hexdigest(), q))
    selected = tuple(sorted(ranked[:N_SELECT]))
    if selected != PINNED_DEV5:
        raise SystemExit(
            f"derived dev5 selection {selected} does not match the pinned LongMemEval-S "
            f"dev5 selection {PINNED_DEV5}")
    by_id = {r["question_id"]: r for r in rows}
    missing = [q for q in selected if q not in by_id]
    if missing:
        raise SystemExit(f"dev5 qids missing from the M dataset: {missing}")
    return selected, [ranked.index(q) + 1 for q in selected]


def build_common():
    """Write the dev5 canonical env/qs set, dev manifest and full planning manifest."""
    rows = load_rows()
    selected, ranks = dev5_selection(rows)
    by_id = {r["question_id"]: r for r in rows}
    out_dir = family_dir(OUT_FAMILY)
    clean_outputs(out_dir)

    envs_meta = []
    total_chars = 0
    for qid, rank in zip(selected, ranks):
        env = lme_s.build_env(by_id[qid])
        st = env_stats(env)
        total_chars += st["chars"]
        write_json(os.path.join(out_dir, f"env_{qid}.json"), env)
        write_jsonl(os.path.join(out_dir, f"qs_{qid}.jsonl"), [lme_s.build_question(by_id[qid])])
        envs_meta.append({
            "env_id": qid, "file": f"env_{qid}.json", "qs_file": f"qs_{qid}.jsonl",
            "dev_rank": rank,
            "category": by_id[qid]["question_type"], "abstention": lme_s.is_abstention(by_id[qid]),
            **{k: st[k] for k in ("n_sessions", "n_events", "chars", "est_tokens")},
        })

    manifest = {
        "family": OUT_FAMILY,
        "question_family": QUESTION_FAMILY,
        "dataset": dataset_provenance(),
        "selection": {
            "rule": ("the LongMemEval-S dev5 qids: first five of the declared 100-qid S dev "
                     "set ordered by SHA256(qid + seed) ascending, ties by qid"),
            "seed": DEV_SELECT_SEED,
            "source_manifest": "data/common/longmemeval/manifest.json",
        },
        "n_envs": len(envs_meta),
        "n_questions": len(envs_meta),
        "total_env_chars": total_chars,
        "estimated_ingest_tokens": total_chars // 4,
        "envs": envs_meta,
    }
    write_json(os.path.join(out_dir, "manifest.json"), manifest)

    # ---- full 500 planning manifest (stats only; env bodies emitted per shard) ----
    full_dir = family_dir(OUT_FAMILY, full=True)
    full_rows = []
    for r in rows:
        n_empty = sum(1 for s in r["haystack_sessions"] if not s)
        chars = sum(len(m["content"]) for s in r["haystack_sessions"] for m in s)
        full_rows.append({
            "qid": r["question_id"],
            "env_id": r["question_id"],
            "category": r["question_type"],
            "abstention": lme_s.is_abstention(r),
            "question_date": r["question_date"],
            "evidence": list(r["answer_session_ids"]),
            "n_sessions": len(r["haystack_sessions"]),
            "n_nonempty_sessions": len(r["haystack_sessions"]) - n_empty,
            "n_msgs": sum(len(s) for s in r["haystack_sessions"]),
            "chars": chars,
            "est_tokens": chars // 4,
        })
    write_json(os.path.join(full_dir, "manifest.json"), {
        "family": OUT_FAMILY,
        "question_family": QUESTION_FAMILY,
        "dataset": manifest["dataset"],
        "n_questions": len(full_rows),
        "note": "planning manifest for full 500-question LongMemEval-M runs",
        "questions": full_rows,
    })
    return manifest


def _read_common(qids):
    """Load canonical env objects and scoring questions for the given qids."""
    out_dir = family_dir(OUT_FAMILY)
    envs, questions = [], []
    for qid in sorted(qids):
        with open(os.path.join(out_dir, f"env_{qid}.json"), "r", encoding="utf-8") as f:
            envs.append(json.load(f))
        with open(os.path.join(out_dir, f"qs_{qid}.jsonl"), "r", encoding="utf-8") as f:
            questions.extend(json.loads(line) for line in f if line.strip())
    return envs, questions


def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def build_run_manifest(out_root, run_id="lme_m_dev5_improved_v1", variant="improved",
                       qids=None, settings=None, benchmark="longmemeval"):
    """Assemble a driver manifest plus its canonical/scoring companions.

    Follows the LongMemEval-S campaign manifest schema exactly: inline envs,
    gold-free question declarations, model dgx-glm53/glm-5.3 and the shared
    answer instruction. Companions (canonical env copies, private scoring
    questions, gold-free declarations) are written next to the manifest so a
    later scoring pass can bind run records to gold without the raw dataset.
    """
    from bench.evidence import prepare_question_declaration

    if qids is None:
        qids = list(PINNED_DEV5)
    if settings is None:
        settings = dict(IMPROVED_ARM_SETTINGS)
    out_root = os.path.abspath(out_root)
    manifest_dir = os.path.join(out_root, "manifests")
    canonical_dir = os.path.join(out_root, "canonical-envs")
    scoring_dir = os.path.join(out_root, "private-scoring-questions")
    goldfree_dir = os.path.join(out_root, "gold-free-questions")
    for d in (manifest_dir, canonical_dir, scoring_dir, goldfree_dir):
        os.makedirs(d, exist_ok=True)

    envs, questions = _read_common(qids)
    if [e["env_id"] for e in envs] != sorted(qids):
        raise SystemExit("canonical env ids do not match the requested qids")
    if {q["qid"] for q in questions} != set(qids):
        raise SystemExit("scoring questions do not match the requested qids")

    declarations = [prepare_question_declaration(q) for q in questions]
    by_qid = {d["qid"]: d for d in declarations}
    out_dir = family_dir(OUT_FAMILY)
    for qid in sorted(qids):
        with open(os.path.join(out_dir, f"env_{qid}.json"), "rb") as f:
            env_bytes = f.read()
        with open(os.path.join(canonical_dir, f"env_{qid}.json"), "wb") as f:
            f.write(env_bytes)
        with open(os.path.join(out_dir, f"qs_{qid}.jsonl"), "rb") as f:
            qs_bytes = f.read()
        with open(os.path.join(scoring_dir, f"qs_{qid}.jsonl"), "wb") as f:
            f.write(qs_bytes)
    goldfree_path = os.path.join(goldfree_dir, "lme-m-dev5-goldfree.jsonl")
    with open(goldfree_path, "w", encoding="utf-8") as f:
        for qid in sorted(qids):
            f.write(json.dumps(by_qid[qid], ensure_ascii=False, allow_nan=False) + "\n")

    manifest = {
        "captureProtocol": CAPTURE_PROTOCOL,
        "run_id": run_id,
        "benchmark": benchmark,
        "variant": variant,
        "output_dir": os.path.join(out_root, "runs", run_id),
        "envs": envs,
        "questions": [by_qid[q] for q in sorted(qids)],
        "model": {"provider": "dgx-glm53", "id": "glm-5.3"},
        "settings": settings,
        "seed": RUN_SEED,
    }
    path = os.path.join(manifest_dir, f"{run_id}.json")
    if os.path.exists(path):
        raise SystemExit(f"refusing to overwrite existing manifest {path}")
    _atomic_write_json(path, manifest)
    return path, manifest


def build_full_shards(out_root, shard_size=25, variant="improved",
                      settings=None, benchmark="longmemeval", prefix="lme_m_full_v1_s"):
    """Emit one manifest per shard of the full 500-question LongMemEval-M set.

    Each shard is an independent driver run (own output_dir and run_id) so the
    full set can execute as parallel single-process workers; scoring merges the
    per-shard run.jsonl files. Env bodies are built from the raw dataset on
    demand; the common family directory only needs the dev5 selection present.

    Shard run ids stay short on purpose: the driver writes
    <output_dir>/envs/<qid>/agent/memory/projects/project_<24hex>/jobs/
    import_<24hex>.source.jsonl, and with the default iteration3 runs root the
    longest qid (17 chars) already reaches ~248 characters, just under the
    260-character Windows path limit (LongPathsEnabled=0 on this host).
    """
    if settings is None:
        settings = dict(IMPROVED_ARM_SETTINGS)
    from bench.evidence import prepare_question_declaration
    rows = load_rows()
    qids = sorted(r["question_id"] for r in rows)
    if shard_size < 1:
        raise SystemExit("shard size must be positive")
    out_root = os.path.abspath(out_root)
    manifest_dir = os.path.join(out_root, "manifests")
    os.makedirs(manifest_dir, exist_ok=True)
    by_id = {r["question_id"]: r for r in rows}

    # Scoring questions (gold included) for the full set: small JSONL rows
    # under the common family full/ dir, mirroring the S converter layout.
    full_qs_dir = family_dir(OUT_FAMILY, full=True)
    for qid in qids:
        write_jsonl(os.path.join(full_qs_dir, f"qs_{qid}.jsonl"),
                    [lme_s.build_question(by_id[qid])])

    paths = []
    for shard_start in range(0, len(qids), shard_size):
        shard = qids[shard_start:shard_start + shard_size]
        run_id = f"{prefix}{shard_start // shard_size:02d}"
        envs, questions = [], []
        for qid in shard:
            envs.append(lme_s.build_env(by_id[qid]))
            questions.append(lme_s.build_question(by_id[qid]))
        declarations = [prepare_question_declaration(q) for q in questions]
        manifest = {
            "captureProtocol": CAPTURE_PROTOCOL,
            "run_id": run_id,
            "benchmark": benchmark,
            "variant": variant,
            "output_dir": os.path.join(out_root, "runs", run_id),
            "envs": envs,
            "questions": declarations,
            "model": {"provider": "dgx-glm53", "id": "glm-5.3"},
            "settings": settings,
            "seed": RUN_SEED,
        }
        path = os.path.join(manifest_dir, f"{run_id}.json")
        if os.path.exists(path):
            raise SystemExit(f"refusing to overwrite existing manifest {path}")
        _atomic_write_json(path, manifest)
        paths.append(path)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", help="raw input directory (or MEMORY_BENCH_DATA_ROOT)")
    ap.add_argument("--out-dir", help="common output root (or MEMORY_BENCH_COMMON_ROOT)")
    ap.add_argument("--run-manifest-root", default=None,
                    help="emit the dev5 driver manifest and companions under this root "
                         "(manifests/, canonical-envs/, private-scoring-questions/, "
                         "gold-free-questions/, runs/)")
    ap.add_argument("--full-shards", type=int, default=0, metavar="N",
                    help="additionally emit ceil(500/N) full-run shard manifests of N envs each")
    ap.add_argument("--shard-prefix", default="lme_m_full_v1_s")
    args = ap.parse_args(argv)
    from .common import configure_paths
    configure_paths(args.data_root, args.out_dir)

    t0 = time.time()
    manifest = build_common()
    print(f"[{OUT_FAMILY}] dev5 envs={manifest['n_envs']} questions={manifest['n_questions']} "
          f"chars={manifest['total_env_chars']:,} ~tokens={manifest['estimated_ingest_tokens']:,} "
          f"({time.time() - t0:.1f}s)")
    if args.run_manifest_root:
        path, run_manifest = build_run_manifest(args.run_manifest_root)
        print(f"[{OUT_FAMILY}] run manifest {path} "
              f"({len(run_manifest['envs'])} envs, {len(run_manifest['questions'])} questions)")
    if args.full_shards:
        t1 = time.time()
        paths = build_full_shards(args.run_manifest_root or os.getcwd(),
                                   shard_size=args.full_shards, prefix=args.shard_prefix)
        print(f"[{OUT_FAMILY}] {len(paths)} full-run shard manifests ({time.time() - t1:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
