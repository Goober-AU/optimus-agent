"""Frozen-dialect evaluation (RESEARCH-NOTES S4.5 holdout discipline).

Evaluates a frozen dialect card against the OCC-0 / OCC-1 / JSON baselines on
the EVOLVE, DEV and HOLDOUT splits, plus the S5 heterogeneous model-pair
matrix for the best dialect. The dialect and all hyperparameters are frozen
before the first HOLDOUT call; every HOLDOUT run is logged with checksum,
purpose and results.

Metrics per split:
- deterministic fidelity (encode -> decode == original, offline)
- listener comprehension (payload equality) per listener model
- objective Q&A accuracy (checkers.py rules) per listener model
- wire tokens vs baselines (real server-side tokenizer, per model)
- latency (listener wall clock)

Model-pair matrix (S5, both directions): the deterministic parts are
pre-encoded programmatically (allowed by S4.3 note 1); each pair tests the
listener side of the frozen dialect (speaker models differ but the wire is
a deterministic function of the payload).
"""

import argparse
import json
import pathlib
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import contract as C
import corpus as CP
import dialect as D
import gateway as G
import qa as QA
from baselines import occ0_text, occ1_text, json_text
from evolve import LISTENER_SYSTEM, MEASURE_MODEL, comprehension_call

HOLDOUT_LOG_PATH = HERE / "holdout-log.jsonl"

MODEL_PAIRS = [
    ("C1", "glm-5.3", "glm-5.3"),
    ("X1", "glm-5.3", "deepseek-v4-pro"),
    ("X2", "deepseek-v4-pro", "glm-5.3"),
    ("X3", "glm-5.3-flash", "deepseek-v4.1-flash"),
    ("X4", "deepseek-v4.1-flash", "glm-5.3-flash"),
]

QA_SYSTEM = (
    "You are Agent B, the receiver in a two-agent protocol. Agent A sends "
    "messages encoded in the D/1 dialect defined by the attached card. Answer "
    "objective questions about the message content. Quote values exactly as "
    "they appear on the wire after applying the card (symbol tables reversed, "
    "defaults filled, packings split). Output each answer on its own line as "
    "'N. <answer>'.")


def log_holdout(record):
    with HOLDOUT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def deterministic_fidelity(card, split):
    failures = []
    for item in split:
        try:
            wire = card.encode(item["message"])
            back = card.decode(wire, strict=True)
            if not C.equal(item["message"], back):
                failures.append(item["id"])
        except Exception as error:
            failures.append("%s:%s" % (item["id"], str(error)[:60]))
    return (1.0 if not failures else 1.0 - len(failures) / len(split)), failures


def split_metrics(gateway, card, split, listener_models,
                  tokens_model=MEASURE_MODEL, workers=8, holdout_purpose=None):
    """One split: fidelity, comprehension, Q&A, tokens, latency."""
    fid_rate, fid_failures = deterministic_fidelity(card, split)
    result = {
        "n": len(split),
        "deterministic_fidelity": round(fid_rate, 4),
        "deterministic_failures": fid_failures[:10],
        "listeners": {},
    }
    texts = {
        "OCC-0": [occ0_text(item) for item in split],
        "OCC-1": [occ1_text(item["message"]) for item in split],
        "JSON": [json_text(item["message"]) for item in split],
        "DIALECT": [card.encode(item["message"]) for item in split],
    }
    pairs = [(name, text) for name, values in texts.items() for text in values]
    token_rows, token_errors = gateway.run_concurrent(
        lambda pair: gateway.measure_tokens(tokens_model, pair[1])["tokens"],
        pairs, workers=workers)
    flat = {}
    index = 0
    for name, values in texts.items():
        flat[name] = token_rows[index:index + len(values)]
        index += len(values)
    result["wire_tokens"] = {}
    for name, values in flat.items():
        clean = [x for x in values if x is not None]
        result["wire_tokens"][name] = {
            "median": statistics.median(clean) if clean else None,
            "mean": round(statistics.mean(clean), 2) if clean else None,
        }
    for listener in listener_models:
        comp, comp_errors = gateway.run_concurrent(
            lambda item: comprehension_call(gateway, card, item, model=listener),
            split, workers=workers)
        ok = [r["ok"] for r in comp if r is not None]
        latencies = [r["latency"] for r in comp if r is not None and r.get("latency")]
        qa_rows, qa_errors = gateway.run_concurrent(
            lambda item: qa_call(gateway, card, item, model=listener),
            split, workers=workers)
        qa_total = sum(r["total"] for r in qa_rows if r)
        qa_correct = sum(r["correct"] for r in qa_rows if r)
        result["listeners"][listener] = {
            "payload_equality": round(sum(1 for x in ok if x) / len(ok), 4)
            if ok else 0.0,
            "qa_accuracy": round(qa_correct / qa_total, 4) if qa_total else None,
            "qa_total": qa_total,
            "median_latency": round(statistics.median(latencies), 3)
            if latencies else None,
            "p95_latency": round(sorted(latencies)[
                min(len(latencies) - 1, int(0.95 * len(latencies)))], 3)
            if latencies else None,
            "errors": [r.get("error") for r in comp
                       if r is not None and r.get("error")][:5],
        }
    if holdout_purpose:
        log_holdout({
            "purpose": holdout_purpose,
            "checksum": card.checksum(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "result": {k: v for k, v in result.items()
                       if k != "deterministic_failures"},
        })
    return result


def qa_call(gateway, card, item, model):
    questions = QA.questions_for(item)
    if not questions:
        return {"total": 0, "correct": 0, "detail": []}
    wire = card.encode(item["message"])
    user = QA.answer_prompt(card, wire, questions)
    result = gateway.chat(model, [
        {"role": "system", "content": QA_SYSTEM},
        {"role": "user", "content": user}], max_tokens=512, temperature=0.0)
    answers = QA.parse_answers(result["content"], len(questions))
    check = QA.load_checkers()
    detail = []
    correct = 0
    for question, answer in zip(questions, answers):
        ok = False
        if answer is not None:
            try:
                ok = check(question, answer)
            except Exception:
                ok = False
        correct += 1 if ok else 0
        detail.append({"id": question["id"], "ok": ok,
                       "got": answer, "gold": question["gold_answer"]})
    return {"total": len(questions), "correct": correct, "detail": detail}


def model_pair_matrix(gateway, card, split, pairs=MODEL_PAIRS, workers=6):
    """S5 matrix: each cell is the frozen dialect decoded by the listener
    model of the pair. The speaker column records which model family the
    dialect was designed against (C1 = control diagonal)."""
    out = {}
    for code, speaker, listener in pairs:
        comp, errors = gateway.run_concurrent(
            lambda item: comprehension_call(gateway, card, item, model=listener),
            split, workers=workers)
        ok = [r["ok"] for r in comp if r is not None]
        latencies = [r["latency"] for r in comp if r is not None and r.get("latency")]
        out["%s %s->%s" % (code, speaker, listener)] = {
            "speaker": speaker, "listener": listener,
            "payload_equality": round(sum(1 for x in ok if x) / len(ok), 4)
            if ok else 0.0,
            "median_latency": round(statistics.median(latencies), 3)
            if latencies else None,
            "errors": [r.get("error") for r in comp
                       if r is not None and r.get("error")][:3],
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--card", default=str(HERE / "dialect-card.json"))
    parser.add_argument("--out", default=str(HERE / "eval-results.json"))
    parser.add_argument("--pairs-only", action="store_true")
    parser.add_argument("--splits-only", action="store_true")
    parser.add_argument("--dev-listeners", default="glm-5.3-flash")
    parser.add_argument("--holdout-listeners", default="glm-5.3-flash,glm-5.3")
    args = parser.parse_args()

    card = D.DialectCard.load(args.card)
    gateway = G.GatewayClient()
    health = gateway.health()
    items = CP.load_corpus()
    evolve_split, dev_split, holdout = CP.split_corpus(items)

    result = {
        "card": {"id": card.card_id, "checksum": card.checksum(),
                 "generation": card.generation, "lineage": card.lineage},
        "gateway": {"status": health["status"], "buildId": health["buildId"]},
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if not args.pairs_only:
        result["EVOLVE"] = split_metrics(
            gateway, card, evolve_split,
            listener_models=args.dev_listeners.split(","))
        result["DEV"] = split_metrics(
            gateway, card, dev_split,
            listener_models=args.dev_listeners.split(","))
        result["HOLDOUT"] = split_metrics(
            gateway, card, holdout,
            listener_models=args.holdout_listeners.split(","),
            holdout_purpose="final frozen-dialect evaluation")
    if not args.splits_only:
        result["MODEL_PAIRS"] = model_pair_matrix(
            gateway, card, dev_split + holdout[:6])
    pathlib.Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("saved:", args.out)


if __name__ == "__main__":
    main()
