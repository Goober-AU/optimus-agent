"""Full 500-question LongMemEval-S driver-manifest generator (iteration 3).

LongMemEval-S is the per-question session-subset haystack: the official
cleaned dataset (HuggingFace xiaowu0162/longmemeval-cleaned,
longmemeval_s_cleaned.json, saved locally as data/longmemeval_s.json) holds
500 questions whose haystacks average ~50 history sessions / ~115k estimated
tokens per environment (verified: min 39 / max 66 sessions), while -M is the
~500-session haystack and -oracle the evidence-only variant.

This converter reads the raw S dataset, rebuilds every environment and
scoring question with the campaign S adapter (bench.converters.longmemeval
build_env/build_question), cross-validates the rebuild against the canonical
common full set (data/common/longmemeval/full, env_<qid>.json + qs_<qid>.jsonl)
and against the historical dev5 improved_v2 reference manifest, and emits
driver run manifests with the LongMemEval-S campaign schema (inline envs with
sessions[].events {role,text,ext_id,ts}, gold-free question declarations,
model dgx-glm53/glm-5.3):

  - 20 independent shard manifests of 25 envs (lme_s_full_v1_s00..s19), each
    an own driver run (own output_dir/run_id) so the full set can execute as
    parallel single-process workers, mirroring the LME-M full-run layout;
  - one combined 500-env manifest (lme_s_full_v1);
  - one smoke5 manifest (lme_s_full_smoke5_v1, first five qids).

Settings are the dev5 improved_v2 LongMemEval-S settings verbatim (recall +
distill + rerank on, rerank pool 200, 8 entries / 8000 chars, extraction
32000 / chunk 15000 budgets, disableTools, questionTimeoutS 240) plus the
iteration-3 upgrades: recallQueryExtract (question-only recall query, the
protocol pairing requirement), answerMaxTokens 8192, the LME answer protocol
variant (a) from reports/continuation/iteration3-research/ANSWER-PROTOCOLS.md,
and ingestTimeoutS 43200 (FULL-RUN-DECISION.md mitigation for the c8-contention
timeout risk; the A/B skipped ingest so this does not affect its parity).
recallQueryExpansion stays OFF - explicit false, matching the E3-06 A/B
manifest exactly: that A/B validated the protocol at 1.00 (5/5,
non-regressive) with extract ON and expansion OFF, and expansion has never
been measured on the S family in any arm, so the full run executes the exact
validated answer-stage stack (strict-evidence parity).

Offline: no model, no network, no Jev, no installed profile access. Run from
the worktree root (stdlib only):

  python -B -m bench.converters.longmemeval_s_full --campaign-root <campaign> \
      --single --shard-size 25 --smoke 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time

from bench.evidence import prepare_question_declaration
from bench.converters import longmemeval as lme_s

FAMILY = "longmemeval"
CAPTURE_PROTOCOL = "optimus-memory-capture/temporal-authority/1.0.0"
MODEL = {"provider": "dgx-glm53", "id": "glm-5.3"}
DATASET_FILE = "longmemeval_s.json"
# Campaign input pin for the cleaned S dataset (longmemeval-comparison-inputs
# PREP receipt; the official README downloads longmemeval_s_cleaned.json from
# xiaowu0162/longmemeval-cleaned and this campaign stores it as
# data/longmemeval_s.json).
DATASET_SHA256 = "08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894"
DATASET_SOURCE = "huggingface:xiaowu0162/longmemeval-cleaned longmemeval_s_cleaned.json"
N_QUESTIONS = 500
SHARD_SIZE = 25
SMOKE_RUN_ID = "lme_s_full_smoke5_v1"
FULL_SINGLE_RUN_ID = "lme_s_full_v1"
SHARD_PREFIX = "lme_s_full_v1_s"
VARIANT = "improved"
# ANSWER-PROTOCOLS.md variant (a), LME (longmemeval), 144 words - verbatim.
ANSWER_PROTOCOL_A = (
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
# Iteration-3 additions on top of the dev5 improved_v2 settings.
# recallQueryExpansion is explicitly False (and the draws key dropped, exactly
# like the A/B manifest): the E3-06 A/B that validated the protocol (1.00, 5/5,
# non-regressive, +951 output tokens/answer) ran with expansion OFF, and
# expansion has never been measured on the S family in any arm - keeping it ON
# would make the full 500-q run the first-ever measurement of the unvalidated
# protocol+expansion combo (FULL-RUN-DECISION.md flag / risk register). M's set
# keeps expansion ON with the residual risk accepted there; S deliberately
# differs for strict evidence parity. ingestTimeoutS 43200 is the LME-M-style
# timeout-risk mitigation (ingest is not exercised by the frozen-store A/B).
SETTINGS_ADDITIONS = {
    "recallQueryExtract": True,
    "recallQueryExpansion": False,
    "answerMaxTokens": 8192,
    "answerInstruction": ANSWER_PROTOCOL_A,
    "ingestTimeoutS": 43200,
}
# Measured on the dev5 improved_v2 run (5 S envs, distill+rerank arm, GLM-5.3,
# reports/continuation/iteration2-inputs/runs/longmemeval_dev5_improved_v2.log):
# 243 sessions ingested in 9548.3 s -> 39.3 s/session, 1909.7 s/env; questions
# averaged 6.5 s. The full-run question side adds the protocol-(a) scratchpad
# (~+500 output tokens; expansion stays OFF, see SETTINGS_ADDITIONS) - 12 s/
# question remains the conservative estimate pending first-hour recalibration.
INGEST_S_PER_SESSION = 9548.3 / 243.0
QUESTION_S_ESTIMATE = 12.0


# ---------------------------------------------------------------------------
# Loading + verification (fail closed)
# ---------------------------------------------------------------------------

def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_jsonl_first(path):
    with open(path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if len(rows) != 1:
        raise SystemExit(f"expected exactly one scoring question per env, got {len(rows)} in {path}")
    return rows[0]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def load_rows(campaign_root):
    """Load and structurally verify the raw cleaned S dataset.

    Returns rows keyed by qid with the rebuilt campaign env + scoring
    question, plus provenance. Fails closed on any drift from the official
    S structure (500 questions, unique qids, aligned haystack arrays).
    """
    path = os.path.join(campaign_root, "data", DATASET_FILE)
    if not os.path.isfile(path):
        raise SystemExit(f"raw S dataset not found: {path}")
    provenance = {
        "file": DATASET_FILE,
        "source": DATASET_SOURCE,
        "sha256": sha256_file(path),
        "bytes": os.path.getsize(path),
    }
    if provenance["sha256"] != DATASET_SHA256:
        raise SystemExit(
            f"raw S dataset sha256 drift: {provenance['sha256']} != pinned {DATASET_SHA256}")
    rows = _read_json(path)
    if len(rows) != N_QUESTIONS:
        raise SystemExit(f"expected {N_QUESTIONS} questions, found {len(rows)}")
    qids = [r["question_id"] for r in rows]
    if len(set(qids)) != N_QUESTIONS:
        raise SystemExit("duplicate question_ids in the raw S dataset")
    for r in rows:
        for field in ("question_id", "question_type", "question", "answer",
                      "question_date", "haystack_session_ids", "haystack_dates",
                      "haystack_sessions", "answer_session_ids"):
            if field not in r:
                raise SystemExit(f"{r.get('question_id')}: missing field {field}")
        if not (len(r["haystack_session_ids"]) == len(r["haystack_dates"])
                == len(r["haystack_sessions"])):
            raise SystemExit(f"{r['question_id']}: misaligned haystack arrays")
    return rows, provenance


def rebuild(rows):
    """Rebuild the campaign env + scoring question for every qid."""
    by_qid = {}
    for r in rows:
        by_qid[r["question_id"]] = {
            "row": r,
            "env": lme_s.build_env(r),
            "qs": lme_s.build_question(r),
        }
    return by_qid


def verify_common_full(by_qid, campaign_root):
    """Deep-equal the rebuild against data/common/longmemeval/full.

    Also cross-checks the full-set planning manifest row stats (n_sessions,
    n_msgs, chars, category, abstention, evidence) so the canonical common
    tree, its planning manifest and the raw dataset provably agree.
    """
    full_dir = os.path.join(campaign_root, "data", "common", FAMILY, "full")
    planning = _read_json(os.path.join(full_dir, "manifest.json"))
    plan_rows = {r["qid"]: r for r in planning["questions"]}
    if len(plan_rows) != N_QUESTIONS:
        raise SystemExit(f"planning manifest holds {len(plan_rows)} rows, expected {N_QUESTIONS}")
    mismatched = 0
    for qid, item in by_qid.items():
        env_path = os.path.join(full_dir, f"env_{qid}.json")
        qs_path = os.path.join(full_dir, f"qs_{qid}.jsonl")
        if not os.path.isfile(env_path) or not os.path.isfile(qs_path):
            raise SystemExit(f"common full set incomplete: missing {qid}")
        if _read_json(env_path) != item["env"]:
            raise SystemExit(f"{qid}: rebuilt env differs from data/common/{FAMILY}/full")
        # The pinned common full set predates the 2.0.0 protocol annotations
        # (all 500 qs rows are legacy-format); the sanctioned comparison - used
        # when the dev5 manifests were staged - is env bytes deep-equal plus
        # question CONTENT fields, with protocol annotations regenerated
        # deterministically by the current converter.
        legacy_qs = _read_jsonl_first(qs_path)
        rebuilt_qs = item["qs"]
        for field in ("qid", "env_id", "question", "answer", "evidence", "category",
                      "abstention", "question_date"):
            if legacy_qs.get(field) != rebuilt_qs.get(field):
                raise SystemExit(f"{qid}: rebuilt scoring question {field} differs from "
                                 f"data/common/{FAMILY}/full")
        plan = plan_rows.get(qid)
        if plan is None:
            raise SystemExit(f"{qid}: missing from the full-set planning manifest")
        r = item["row"]
        chars = sum(len(m["content"]) for s in r["haystack_sessions"] for m in s)
        checks = {
            "n_sessions": len(r["haystack_sessions"]),
            "n_msgs": sum(len(s) for s in r["haystack_sessions"]),
            "chars": chars,
            "category": r["question_type"],
            "abstention": lme_s.is_abstention(r),
            "evidence": list(r["answer_session_ids"]),
        }
        for key, value in checks.items():
            if plan.get(key) != value:
                raise SystemExit(f"{qid}: planning-manifest {key} drift "
                                 f"({plan.get(key)!r} != {value!r})")
        mismatched += 1
    return mismatched


def verify_env_contract(qid, env):
    """Structural env checks: chronology, roles, ground-truth resolution."""
    sessions = env.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise SystemExit(f"{qid}: env has no sessions")
    ext_ids = set()
    prev_ts = None
    for session in sessions:
        if not isinstance(session.get("session_id"), str) or not session["session_id"]:
            raise SystemExit(f"{qid}: session without id")
        ts = session.get("ts")
        if not isinstance(ts, str) or not ts:
            raise SystemExit(f"{qid}: session {session['session_id']} without ts")
        if prev_ts is not None and ts < prev_ts:
            raise SystemExit(f"{qid}: session chronology violation at {session['session_id']}")
        prev_ts = ts
        for event in session.get("events", []):
            if event.get("role") not in ("user", "assistant") or not isinstance(event.get("text"), str):
                raise SystemExit(f"{qid}: malformed event in {session['session_id']}")
            if not isinstance(event.get("ext_id"), str) or not isinstance(event.get("ts"), str):
                raise SystemExit(f"{qid}: event without ext_id/ts in {session['session_id']}")
            ext_ids.add(event["ext_id"])
    refs = env.get("ground_truth_refs")
    if not isinstance(refs, list) or not refs:
        raise SystemExit(f"{qid}: missing ground_truth_refs")
    for ref in refs:
        if ref not in ext_ids:
            raise SystemExit(f"{qid}: ground truth ref {ref} does not resolve to an event")


# ---------------------------------------------------------------------------
# Reference validation (dev5 improved_v2 replica + schema diff)
# ---------------------------------------------------------------------------

def dev5_replica(by_qid, reference):
    """Rebuild the dev5 selection and diff it against the reference manifest.

    Env bodies and gold-free question declarations must be field-by-field
    EXACT (run_id/output_dir/settings intentionally differ: the full-run
    settings add extract/expansion and the protocol-(a) instruction).
    """
    ref_env_order = [e["env_id"] for e in reference["envs"]]
    problems = []
    for env_id in ref_env_order:
        item = by_qid.get(env_id)
        if item is None:
            problems.append(f"dev5 env {env_id} missing from the full rebuild")
    if [q["qid"] for q in reference["questions"]] != ref_env_order:
        problems.append("dev5-replica: reference env/question ordering mismatch")
    replica_envs = [by_qid[env_id]["env"] for env_id in ref_env_order]
    if replica_envs != reference["envs"]:
        problems.append("dev5-replica: rebuilt env list differs from the reference "
                         "(order or content)")
    replica_questions = [prepare_question_declaration(by_qid[env_id]["qs"])
                         for env_id in ref_env_order]
    if replica_questions != reference["questions"]:
        problems.append("dev5-replica: rebuilt question declarations differ from the "
                        "reference (order or content)")
    return problems


def validate_schema(manifest, reference):
    """Structural schema validation of a new manifest against dev5 improved_v2.

    Checks key sets and value shapes (not content) for the manifest, envs,
    sessions, events and questions, so every emitted manifest is provably the
    same shape as the campaign reference.
    """
    problems = []
    if set(manifest) != set(reference):
        problems.append(f"manifest key set differs: {sorted(set(manifest) ^ set(reference))}")
    ref_env = reference["envs"][0]
    ref_session = ref_env["sessions"][0]
    ref_event = ref_session["events"][0]
    for env in manifest["envs"]:
        if set(env) != set(ref_env):
            problems.append(f"env {env['env_id']} key set differs: {sorted(set(env) ^ set(ref_env))}")
            continue
        for session in env["sessions"]:
            if set(session) != set(ref_session):
                problems.append(f"env {env['env_id']} session key set differs: "
                                f"{sorted(set(session) ^ set(ref_session))}")
                break
            for event in session["events"]:
                if set(event) != set(ref_event):
                    problems.append(f"env {env['env_id']} event key set differs: "
                                    f"{sorted(set(event) ^ set(ref_event))}")
                    break
    ref_q = reference["questions"][0]
    for q in manifest["questions"]:
        if set(q) != set(ref_q):
            problems.append(f"question {q['qid']} key set differs: {sorted(set(q) ^ set(ref_q))}")
            continue
        for field in ("qid", "env_id", "question", "question_protocol", "scoring_protocol",
                      "question_sha256"):
            if not isinstance(q.get(field), str) or not q[field]:
                problems.append(f"question {q['qid']}: {field} missing/not a string")
        if len(q.get("question_sha256", "")) != 64:
            problems.append(f"question {q['qid']}: question_sha256 is not a sha256 hex digest")
        if q["question_protocol"] != reference["questions"][0]["question_protocol"]:
            problems.append(f"question {q['qid']}: wrong question_protocol")
    return problems


# ---------------------------------------------------------------------------
# Settings + manifest assembly
# ---------------------------------------------------------------------------

def build_settings(reference_settings):
    """Dev5 improved_v2 settings + the iteration-3 additions, fail-closed."""
    settings = dict(reference_settings)
    expected_baseline = {
        "recallOn": True,
        "recallQueryDistillation": True,
        "recallRerank": True,
        "recallRerankCandidates": 200,
        "maxRecallChars": 8000,
        "maxRecallEntries": 8,
        "maxExtractionTokens": 32000,
        "maxImportChunkChars": 15000,
        "disableTools": True,
        "skipIngest": False,
        "questionTimeoutS": 240,
        "answerMaxTokens": 8192,
    }
    for key, value in expected_baseline.items():
        if settings.get(key) != value:
            raise SystemExit(f"reference settings drift: {key}={settings.get(key)!r}, expected {value!r}")
    settings.update(SETTINGS_ADDITIONS)
    return settings


def build_manifest(items, run_id, runs_root, settings, seed):
    """Assemble a driver manifest in the dev5 improved_v2 schema."""
    envs = [item["env"] for item in items]
    questions = [prepare_question_declaration(item["qs"]) for item in items]
    return {
        "captureProtocol": CAPTURE_PROTOCOL,
        "run_id": run_id,
        "benchmark": FAMILY,
        "variant": VARIANT,
        "output_dir": os.path.join(runs_root, run_id).replace("\\", "/"),
        "envs": envs,
        "questions": questions,
        "model": dict(MODEL),
        "settings": dict(settings),
        "seed": seed,
    }


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def emit(manifest, out_dir):
    path = os.path.join(out_dir, f"{manifest['run_id']}.json")
    if os.path.exists(path):
        raise SystemExit(f"refusing to overwrite existing manifest {path}")
    atomic_write_json(path, manifest)
    return {"file": path, "sha256": sha256_file(path),
            "envs": len(manifest["envs"]), "questions": len(manifest["questions"])}


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

def cost_model(by_qid):
    n_envs = len(by_qid)
    n_sessions = sum(len(item["env"]["sessions"]) for item in by_qid.values())
    n_events = sum(len(s["events"]) for item in by_qid.values() for s in item["env"]["sessions"])
    chars = sum(len(e["text"]) for item in by_qid.values() for s in item["env"]["sessions"]
                for e in s["events"])
    ingest_s = n_sessions * INGEST_S_PER_SESSION
    question_s = n_envs * QUESTION_S_ESTIMATE
    driver_hours = (ingest_s + question_s) / 3600.0
    return {
        "n_envs": n_envs,
        "n_sessions": n_sessions,
        "n_events": n_events,
        "chars": chars,
        "est_ingest_tokens": chars // 4,
        "ingest_s_per_session_measured": INGEST_S_PER_SESSION,
        "ingest_seconds_est": ingest_s,
        "question_seconds_est": question_s,
        "driver_hours_est": driver_hours,
        "wall_hours_at_c6": driver_hours / 6.0,
        "wall_hours_at_c8": driver_hours / 8.0,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_campaign = os.path.abspath(os.path.join(here, "..", "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--campaign-root", default=default_campaign,
                    help="campaign root holding data/longmemeval_s.json (default: worktree parent)")
    ap.add_argument("--reference-manifest",
                    default=os.path.join("{campaign}", "reports", "continuation", "iteration2-inputs",
                                         "manifests", "longmemeval_dev5_improved_v2.json"))
    ap.add_argument("--out-dir",
                    default=os.path.join("{campaign}", "reports", "continuation", "iteration3-inputs", "manifests"))
    ap.add_argument("--runs-root",
                    default=os.path.join("{campaign}", "reports", "continuation", "iteration3-inputs", "runs"))
    ap.add_argument("--smoke", type=int, default=5, metavar="N",
                    help="emit a smoke manifest of the first N qids (0 disables)")
    ap.add_argument("--single", action="store_true",
                    help="emit one combined manifest with all 500 envs")
    ap.add_argument("--shard-size", type=int, default=0, metavar="N",
                    help="emit ceil(500/N) shard manifests of N envs each (0 disables)")
    ap.add_argument("--dry-run", action="store_true", help="validate only; write nothing")
    args = ap.parse_args(argv)

    campaign = os.path.abspath(args.campaign_root)

    def resolve(path):
        return os.path.abspath(path.replace("{campaign}", campaign))

    reference_path = resolve(args.reference_manifest)
    out_dir = resolve(args.out_dir)
    runs_root = resolve(args.runs_root)

    t0 = time.time()
    rows, provenance = load_rows(campaign)
    print(f"[lme-s-full] raw dataset verified: {len(rows)} questions, sha256={provenance['sha256'][:16]}... "
          f"({provenance['bytes'] / 1e6:.1f} MB, {DATASET_SOURCE})")

    by_qid = rebuild(rows)
    for qid, item in by_qid.items():
        verify_env_contract(qid, item["env"])
    print(f"[lme-s-full] rebuilt + contract-verified {len(by_qid)} envs ({time.time() - t0:.1f}s)")

    n_common = verify_common_full(by_qid, campaign)
    print(f"[lme-s-full] deep-equal vs data/common/{FAMILY}/full: {n_common}/{N_QUESTIONS} env+qs EXACT")

    reference = _read_json(reference_path)
    seed = reference.get("seed")
    settings = build_settings(reference["settings"])
    print(f"[lme-s-full] settings: dev5 improved_v2 baseline "
          f"({len(reference['settings'])} keys) + extract/protocol-(a)/8192/"
          f"ingestTimeoutS-43200, expansion OFF (E3-06 A/B parity) "
          f"-> {len(settings)} keys")

    problems_total = []

    replica_problems = dev5_replica(by_qid, reference)
    problems_total.extend(replica_problems)
    print(f"[lme-s-full] dev5 replica diff vs {os.path.basename(reference_path)}: "
          f"{'EXACT (envs + question declarations)' if not replica_problems else 'DIFFERS'}")
    for p in replica_problems:
        print(f"    !! {p}")

    # ordered full set: sorted qids (mirrors the LME-M full-run sharding)
    ordered = [by_qid[q] for q in sorted(by_qid)]

    written = []
    if args.smoke:
        smoke = build_manifest(ordered[:args.smoke], SMOKE_RUN_ID, runs_root, settings, seed)
        problems = validate_schema(smoke, reference)
        problems_total.extend(problems)
        print(f"[lme-s-full] smoke{args.smoke} schema validation vs dev5 improved_v2: "
              f"{'PASS' if not problems else 'FAIL'} ({len(problems)} problems)")
        for p in problems:
            print(f"    !! {p}")
        if not args.dry_run and not problems:
            written.append(emit(smoke, out_dir))

    if args.single:
        full = build_manifest(ordered, FULL_SINGLE_RUN_ID, runs_root, settings, seed)
        problems = validate_schema(full, reference)
        problems_total.extend(problems)
        if not args.dry_run:
            written.append(emit(full, out_dir))

    if args.shard_size:
        if args.shard_size < 1:
            raise SystemExit("shard size must be positive")
        expected_shards = (N_QUESTIONS + args.shard_size - 1) // args.shard_size
        for shard_start in range(0, N_QUESTIONS, args.shard_size):
            shard = ordered[shard_start:shard_start + args.shard_size]
            run_id = f"{SHARD_PREFIX}{shard_start // args.shard_size:02d}"
            manifest = build_manifest(shard, run_id, runs_root, settings, seed)
            problems = validate_schema(manifest, reference)
            problems_total.extend(problems)
            if not args.dry_run:
                written.append(emit(manifest, out_dir))
        print(f"[lme-s-full] {expected_shards} shard manifests of {args.shard_size} envs")

    if problems_total:
        print(f"[lme-s-full] VALIDATION PROBLEMS: {len(problems_total)} - no manifests written")
        return 2

    if args.dry_run or not written:
        print("[lme-s-full] dry run / nothing to write")
        return 0

    model = cost_model(by_qid)
    receipt = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "campaign_root": campaign,
        "dataset": provenance,
        "reference_manifest": reference_path,
        "settings": ("dev5 improved_v2 baseline + recallQueryExtract, "
                      "recallQueryExpansion OFF (E3-06 A/B parity), answer protocol (a) "
                      "from ANSWER-PROTOCOLS.md, answerMaxTokens 8192, ingestTimeoutS 43200"),
        "model": MODEL,
        "n_questions": len(by_qid),
        "cost_model": model,
        "manifests": written,
    }
    receipt_dir = os.path.abspath(os.path.join(here, "..", "..", "reports", "lmesfull"))
    os.makedirs(receipt_dir, exist_ok=True)
    receipt_path = os.path.join(receipt_dir, "manifests-receipt.json")
    atomic_write_json(receipt_path, receipt)
    print(f"[lme-s-full] wrote {len(written)} manifests -> {out_dir}")
    for w in written:
        print(f"    {os.path.basename(w['file'])}: {w['envs']} envs / {w['questions']} questions "
              f"sha256={w['sha256'][:16]}...")
    print(f"[lme-s-full] receipt: {receipt_path}")
    print(f"[lme-s-full] cost model: {model['n_sessions']} sessions, "
          f"~{model['est_ingest_tokens']:,} ingest tokens; "
          f"{model['driver_hours_est']:.0f} driver-hours "
          f"(c6 ~{model['wall_hours_at_c6']:.0f} h, c8 ~{model['wall_hours_at_c8']:.0f} h wall)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
