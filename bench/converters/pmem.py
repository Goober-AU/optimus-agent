"""PersonaMem v1 (32k) data adapter using Optimus local protocols.

PersonaMem v1 is an official long-context personalization benchmark (MIT).
The official protocol sends one shared chat context per persona variant and
asks one multiple-choice question per row, sliced to
``end_index_in_shared_context``. This adapter maps that protocol onto the
memory-bench controller:

* **Per-question environments (default, official arm).** Each distinct
  ``(shared context, end_index)`` pair becomes one EnvSpec keyed
  ``pm1_p<persona>v<variant>e<end>``: the context is sliced exactly to the
  question's position, so no question can ever see turns that follow it.
  222 distinct slices serve all 589 questions of the 32k split.
* **Shared-environment arm (optional, PMEM_SHARED_ENV=1).** One EnvSpec per
  shared context (``pm1_p<persona>v<variant>``) holding the full message
  list; cheaper to ingest, but mid-conversation questions would see future
  turns, so it is a documented deviation arm, never the claim number.
* Question presentation follows the official format verbatim: the raw
  question, the official instruction ("Find the most appropriate model
  response and give your final answer (a), (b), (c), or (d) after the special
  token <final_answer>."), then the option list.
* Scoring is the official deterministic extractor (letter after
  ``<final_answer>`` else the whole response, ``\\(([a-d])\\)`` with a
  ``\\b([a-d])\\b`` fallback, set-equality, no retry), implemented as the
  versioned eval type ``pmem_mcq``.
* Sessions are recovered from the dataset's per-block ``system`` messages
  ("Current user persona: ..."): every system message starts a new session,
  matching the generator's block structure (4-7 sessions per 32k context).
* ``question_type`` aliases across the three dataset generations are
  normalized to the seven canonical types; the 32k split carries six of them
  (``acknowledge_latest_preferences`` is absent and the two recall-facts
  aliases merge).

Events are undated (the released contexts strip dates from text), so the
driver runs in LegacyUndated capture mode: no ``ts`` fields, no
``captureProtocol``, no synthetic timestamps. System messages are emitted as
``user`` events prefixed ``System:`` because the controller accepts only
user/assistant/toolResult roles.

Data acquisition/licensing is the caller's responsibility (HF
``bowen-upenn/PersonaMem`` revision ``a8076d5608c93ba2a28983cd78aa99b01a163ae7``;
local copy ``pmem-data/`` verified byte-identical).
"""

from __future__ import annotations

import ast
import csv
import json
import os

from bench.protocol import annotate_question

from .common import (clean_outputs, SEED, get_data_root, family_dir, write_json, write_jsonl,
                     stable_sample, largest_remainder, env_stats)

FAMILY = "pmem"
DEV_N = 60

# ---------------------------------------------------------------------------
# question_type normalization (three generations of names across the splits)
# ---------------------------------------------------------------------------

CANONICAL_TYPES = {
    "recall_user_shared_facts",
    "suggest_new_ideas",
    "acknowledge_latest_preferences",
    "track_full_preference_evolution",
    "revisit_reasons_behind_preference_updates",
    "provide_preference_aligned_recommendations",
    "generalize_to_new_scenarios",
}

TYPE_ALIASES = {
    # canonical                            generation-time and variant aliases
    "recall_user_shared_facts": ("recall_user_shared_facts",
                                 "recalling_facts_mentioned_by_the_user"),
    "suggest_new_ideas": ("suggest_new_ideas",
                          "identifying_new_things_not_mentioned_by_the_user"),
    "acknowledge_latest_preferences": ("acknowledge_latest_preferences",
                                       "recalling_the_latest_user_preferences",
                                       "acknowledge_latest_user_preferences"),
    "track_full_preference_evolution": ("track_full_preference_evolution",
                                        "tracking_the_full_sequence_of_preference_updates",
                                        "track_full_preference_updates"),
    "revisit_reasons_behind_preference_updates": ("revisit_reasons_behind_preference_updates",
                                                  "recalling_the_reasons_behind_previous_updates"),
    "provide_preference_aligned_recommendations": ("provide_preference_aligned_recommendations",
                                                   "recommendation_aligned_with_users_latest_preferences"),
    "generalize_to_new_scenarios": ("generalize_to_new_scenarios",
                                    "generalizing_to_new_scenarios",
                                    "generalizing_past_reasons_in_memory_to_new_scenarios"),
}

ALIAS_TO_CANONICAL = {alias: canonical
                      for canonical, aliases in TYPE_ALIASES.items()
                      for alias in aliases}

OFFICIAL_INSTRUCTION = ("Find the most appropriate model response and give your "
                        "final answer (a), (b), (c), or (d) after the special token "
                        "<final_answer>.")

# Split selection: run_converters --pmem-split sets PMEM_SPLIT before this
# module is imported (mirrors the --lme-v2-axtree-cap precedent). The module
# default keeps direct `python -m bench.converters.pmem` invocations on 32k.
SPLIT = os.environ.get("PMEM_SPLIT", "32k").lower()
if SPLIT not in ("32k", "128k", "1m"):
    raise ValueError(f"unsupported PersonaMem split: {SPLIT!r} (want 32k|128k|1m)")

DATASET_ID = "bowen-upenn/PersonaMem"
DATASET_REVISION = "a8076d5608c93ba2a28983cd78aa99b01a163ae7"


def canonical_type(raw: str) -> str:
    canonical = ALIAS_TO_CANONICAL.get((raw or "").strip())
    if canonical is None:
        raise ValueError(f"unknown PersonaMem question_type: {raw!r}")
    return canonical


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_questions():
    path = os.path.join(get_data_root(), f"questions_{SPLIT}.csv")
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_contexts():
    """shared_context_id -> message list (ordered dict per JSONL line)."""
    path = os.path.join(get_data_root(), f"shared_contexts_{SPLIT}.jsonl")
    contexts = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            for context_id, messages in json.loads(line).items():
                contexts[context_id] = messages
    return contexts


def build_persona_variants(question_rows):
    """Map every referenced context to (persona_id, variant, n_variants).

    A context is only ever referenced by one persona (verified on the 32k
    split); the variant ordinal is the context's rank among that persona's
    referenced contexts sorted by shared_context_id.
    """
    per_persona = {}
    for row in question_rows:
        per_persona.setdefault(int(row["persona_id"]), set()).add(row["shared_context_id"])
    mapping = {}
    for persona in sorted(per_persona):
        ordered = sorted(per_persona[persona])
        for ordinal, context_id in enumerate(ordered, start=1):
            mapping[context_id] = (persona, ordinal, len(ordered))
    return mapping


# ---------------------------------------------------------------------------
# env / question construction
# ---------------------------------------------------------------------------

def _event(message, index):
    role = message.get("role", "user")
    text = message.get("content", "")
    if role == "system":
        # The controller accepts only user/assistant/toolResult roles; keep the
        # persona framing as user evidence, clearly labeled.
        return {"role": "user", "text": f"System: {text}", "ext_id": f"c{index}"}
    if role not in ("user", "assistant"):
        return {"role": "user", "text": f"{role}: {text}", "ext_id": f"c{index}"}
    return {"role": role, "text": text, "ext_id": f"c{index}"}


def context_sessions(sliced_context):
    """Split a sliced context into sessions on system messages.

    The generator emits one 'Current user persona: ...' system message per
    conversation block, so each system message starts a new session.
    """
    sessions = []
    current = None
    for index, message in enumerate(sliced_context):
        if message.get("role") == "system" or current is None:
            current = []
            sessions.append(current)
        current.append(_event(message, index))
    return [{"session_id": f"s{n}", "events": events}
            for n, events in enumerate(sessions, start=1) if events]


def build_env(context_id, sliced_context, end_index, pv_meta):
    persona, variant, n_variants = pv_meta
    return {
        "env_id": f"pm1_p{persona}v{variant}e{end_index}",
        "sessions": context_sessions(sliced_context),
        "ground_truth_refs": [],
        "family": FAMILY,
        "notes": {
            "source": "bowen-upenn/PersonaMem questions_32k.csv + shared_contexts_32k.jsonl",
            "dataset_revision": "a8076d5608c93ba2a28983cd78aa99b01a163ae7",
            "persona_id": persona,
            "variant": variant,
            "n_variants": n_variants,
            "shared_context_id": context_id,
            "end_index_in_shared_context": end_index,
            "context_messages_total": None,  # filled by the caller from _context_len
            "env_mode": "per_question_slice",
        },
    }


def parse_options(raw):
    options = ast.literal_eval(raw)
    if (not isinstance(options, list) or len(options) != 4
            or not all(isinstance(o, str) and o.strip() for o in options)):
        raise ValueError("PersonaMem options must be a 4-element string list")
    return options


def gold_letter(raw):
    letter = (raw or "").strip().lower().strip("() ")
    if len(letter) != 1 or letter not in "abcd":
        raise ValueError(f"PersonaMem correct_answer is not a single a-d letter: {raw!r}")
    return letter


def presented_question(row, options):
    return (f"{row['user_question_or_message']}\n\n"
            f"{OFFICIAL_INSTRUCTION}\n\n"
            f"{options}")


def build_question(row, env_id):
    options = parse_options(row["all_options"])
    answer = gold_letter(row["correct_answer"])
    canonical = canonical_type(row["question_type"])
    question = {
        "qid": f"pm1_r{row['_row']}",
        "env_id": env_id,
        "question": presented_question(row, row["all_options"]),
        "answer": answer,
        "evidence": [],
        "category": canonical,
        "eval": {
            "type": "pmem_mcq",
            "params": {
                "answer": answer,
                "options": options,
                "source": "PersonaMem inference.py extract_answer (official semantics)",
            },
        },
        # extras for attribution and the category breakdowns
        "question_type_raw": row["question_type"],
        "topic": row["topic"],
        "question_uuid": row["question_id"],
        "persona_id": int(row["persona_id"]),
        "end_index_in_shared_context": int(row["end_index_in_shared_context"]),
        "distance_to_ref_in_blocks": int(row["distance_to_ref_in_blocks"]),
        "distance_to_ref_in_tokens": int(row["distance_to_ref_in_tokens"]),
        "distance_to_ref_proportion_in_context": row["distance_to_ref_proportion_in_context"],
        "context_length_in_tokens": int(row["context_length_in_tokens"]),
    }
    return annotate_question(question, FAMILY)


def shared_env_id(pv_meta):
    persona, variant, _ = pv_meta
    return f"pm1_p{persona}v{variant}"


def build_shared_env(context_id, full_context, pv_meta):
    persona, variant, n_variants = pv_meta
    return {
        "env_id": f"pm1_p{persona}v{variant}",
        "sessions": context_sessions(full_context),
        "ground_truth_refs": [],
        "family": FAMILY,
        "notes": {
            "source": "bowen-upenn/PersonaMem questions_32k.csv + shared_contexts_32k.jsonl",
            "dataset_revision": "a8076d5608c93ba2a28983cd78aa99b01a163ae7",
            "persona_id": persona,
            "variant": variant,
            "n_variants": n_variants,
            "shared_context_id": context_id,
            "env_mode": "shared",
            "leak_note": "mid-conversation questions see future turns; deviation arm only",
        },
    }


def source_provenance():
    """SHA-256 provenance of the two source files for this split."""
    import hashlib

    def sha256_file(path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    files = {}
    for name in (f"questions_{SPLIT}.csv", f"shared_contexts_{SPLIT}.jsonl"):
        path = os.path.join(get_data_root(), name)
        files[name] = {"sha256": sha256_file(path)}
    return {
        "dataset": DATASET_ID,
        "revision": DATASET_REVISION,
        "split": SPLIT,
        "files": files,
    }


# ---------------------------------------------------------------------------
# selection + build
# ---------------------------------------------------------------------------

def dev_selection(question_rows):
    """Stratified DEV_N by canonical question_type (largest-remainder
    allocation, md5(seed:qid) ordering inside each stratum)."""
    by_type = {}
    for row in question_rows:
        by_type.setdefault(canonical_type(row["question_type"]), []).append(row["_row"])
    alloc = largest_remainder(DEV_N, {t: len(v) for t, v in by_type.items()})
    selected = {}
    for qtype, rows in sorted(by_type.items()):
        selected[qtype] = stable_sample(SEED, sorted(rows), alloc[qtype])
    return selected, alloc


def build(dev_only=True):
    rows = load_questions()
    contexts = load_contexts()
    for index, row in enumerate(rows):
        row["_row"] = index
        row["_context_len"] = len(contexts[row["shared_context_id"]])
        if row["shared_context_id"] not in contexts:
            raise ValueError(f"question references unknown shared context {row['shared_context_id']}")
        end = int(row["end_index_in_shared_context"])
        if end <= 0 or end > row["_context_len"]:
            raise ValueError(f"end_index {end} out of range for context of {row['_context_len']} messages")
    pv_meta = build_persona_variants(rows)

    shared_mode = os.environ.get("PMEM_SHARED_ENV", "") == "1"
    # Split-nested layout: <common>/pmem/<split>/{env_*.json,qs_*.jsonl,manifest.json}
    # and <common>/pmem/<split>/full/... (runner contract A2).
    out_dir = os.path.join(family_dir(FAMILY), SPLIT)
    os.makedirs(out_dir, exist_ok=True)
    clean_outputs(out_dir)
    selected, alloc = dev_selection(rows)
    selected_rows = set()
    for cell in selected.values():
        selected_rows.update(cell)

    # envs: per-question slices (official) or one per shared context (deviation)
    envs = {}          # env_id -> env dict
    env_of_row = {}    # row index -> env_id
    for row in rows:
        context_id = row["shared_context_id"]
        end = int(row["end_index_in_shared_context"])
        if shared_mode:
            env_id = shared_env_id(pv_meta[context_id])
            if env_id not in envs:
                envs[env_id] = build_shared_env(context_id, contexts[context_id], pv_meta[context_id])
        else:
            env_id = f"pm1_p{pv_meta[context_id][0]}v{pv_meta[context_id][1]}e{end}"
            if env_id not in envs:
                env = build_env(context_id, contexts[context_id][:end], end, pv_meta[context_id])
                env["notes"]["context_messages_total"] = row["_context_len"]
                envs[env_id] = env
        env_of_row[row["_row"]] = env_id

    envs_meta = []
    total_chars = 0
    questions_by_env = {}
    for row in rows:
        questions_by_env.setdefault(env_of_row[row["_row"]], []).append(row)

    for env_id in sorted(envs):
        env = envs[env_id]
        st = env_stats(env)
        total_chars += st["chars"]
        write_json(os.path.join(out_dir, f"env_{env_id}.json"), env)
        qrows = [build_question(row, env_id)
                 for row in sorted(questions_by_env[env_id], key=lambda r: r["_row"])
                 if (dev_only and row["_row"] in selected_rows) or not dev_only]
        write_jsonl(os.path.join(out_dir, f"qs_{env_id}.jsonl"), qrows)
        envs_meta.append({
            "env_id": env_id, "file": f"env_{env_id}.json", "qs_file": f"qs_{env_id}.jsonl",
            "n_questions": len(qrows),
            "persona_id": env["notes"]["persona_id"],
            "variant": env["notes"]["variant"],
            "shared_context_id": env["notes"]["shared_context_id"],
            **{k: st[k] for k in ("n_sessions", "n_events", "chars", "est_tokens")},
        })

    n_q = sum(e["n_questions"] for e in envs_meta)
    from bench.protocol import QUESTION_PROTOCOLS, SCORING_PROTOCOLS
    manifest = {
        "family": FAMILY,
        "seed": SEED,
        "pmem_split": SPLIT,
        "env_mode": "shared" if shared_mode else "per_question_slice",
        "split": SPLIT,
        "question_protocol": QUESTION_PROTOCOLS[FAMILY],
        "scoring_protocol": SCORING_PROTOCOLS[FAMILY],
        "source": source_provenance(),
        "sampling": {
            "method": "stratified by canonical question_type; largest-remainder allocation; "
                      "within-stratum md5(seed:row) ordering",
            "strata_alloc": {t: len(v) for t, v in sorted(selected.items())},
            "type_aliases": {canonical: list(aliases)
                             for canonical, aliases in sorted(TYPE_ALIASES.items())},
        },
        "n_envs": len(envs_meta),
        "n_questions": n_q,
        "total_env_chars": total_chars,
        "estimated_ingest_tokens": total_chars // 4,
        "envs": envs_meta,
    }
    write_json(os.path.join(out_dir, "manifest.json"), manifest)

    # ---- full planning manifest (all 589 with their env binding) ----
    full_dir = os.path.join(out_dir, "full")
    os.makedirs(full_dir, exist_ok=True)
    full_rows = []
    full_counts = {}
    for row in rows:
        full_rows.append({
            "qid": f"pm1_r{row['_row']}",
            "env_id": env_of_row[row["_row"]],
            "category": canonical_type(row["question_type"]),
            "question_type_raw": row["question_type"],
            "topic": row["topic"],
            "answer": gold_letter(row["correct_answer"]),
            "evidence": [],
            "question_uuid": row["question_id"],
            "persona_id": int(row["persona_id"]),
        })
        full_counts[env_of_row[row["_row"]]] = full_counts.get(env_of_row[row["_row"]], 0) + 1
    # envs[] mirrors the dev manifest shape (runner contract A3: env_id/file/
    # qs_file/n_questions with sum == n_questions); file names resolve relative
    # to the family dir (full/ qs files carry the same names).
    full_envs = [{
        "env_id": env_id, "file": f"env_{env_id}.json", "qs_file": f"qs_{env_id}.jsonl",
        "n_questions": full_counts[env_id],
        "persona_id": envs[env_id]["notes"]["persona_id"],
        "variant": envs[env_id]["notes"]["variant"],
        "shared_context_id": envs[env_id]["notes"]["shared_context_id"],
    } for env_id in sorted(full_counts)]
    write_json(os.path.join(full_dir, "manifest.json"), {
        "family": FAMILY,
        "pmem_split": SPLIT,
        "seed": SEED,
        "env_mode": "shared" if shared_mode else "per_question_slice",
        "split": SPLIT,
        "question_protocol": QUESTION_PROTOCOLS[FAMILY],
        "scoring_protocol": SCORING_PROTOCOLS[FAMILY],
        "source": source_provenance(),
        "n_envs": len(full_envs),
        "n_questions": len(full_rows),
        "note": "planning manifest for full 589-question 32k runs",
        "envs": full_envs,
        "questions": full_rows,
    })

    if not dev_only:
        clean_outputs(full_dir)
        for env_id in sorted(envs):
            write_json(os.path.join(full_dir, f"env_{env_id}.json"), envs[env_id])
            qrows = [build_question(row, env_id)
                     for row in sorted(questions_by_env[env_id], key=lambda r: r["_row"])]
            write_jsonl(os.path.join(full_dir, f"qs_{env_id}.jsonl"), qrows)
    return manifest
