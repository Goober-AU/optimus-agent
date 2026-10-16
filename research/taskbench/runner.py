"""OCC-2/OCC-3 campaign: real-model two-agent task-accuracy baseline runner.

Protocol (occ2-taskbench worker):
- Agent A (speaker, glm-5.3) receives ONLY the task sender_input and produces
  a communication message for Agent B.
  - MODE=occ0: plain English, hard limit 120 words.
  - MODE=occ1: OCC-1 compact structured English (OCC/1 header + KEY=VALUE
    lines), strictly parsed with the runtime codec
    (prime-agent-runtime/src/rlm/occ1.py); one retry on parse failure.
- Agent B (listener: glm-5.3 or glm-5.3-flash) receives ONLY the message from
  A plus the receiver_task, and answers. B's system prompt is byte-identical
  in both modes, so B prompt_tokens differences between modes measure the
  message encoding cost on the real server-side tokenizer.
- Scoring: research/tasks/checkers.py against gold_answer. Strict score uses
  B's stripped content as-is. A lenient diagnostic (last non-empty line, and
  newline->comma normalization for family D set answers) is recorded
  separately and is NOT the headline accuracy.
- Retries: one format retry for A (occ0 word-limit violation, occ1 parse
  failure, empty output) and for B (empty output). Transport errors get up to
  3 attempts with backoff, recorded separately. WRONG ANSWERS ARE NEVER
  RETRIED (that would leak the gold answer into the protocol).
- Determinism: temperature 0, seed 42, no other sampling.
- The A call depends only on (mode, task): the speaker does not know the
  listener, so one A call per (mode, task) serves both listener variants
  (100 A + 200 B = 300 gateway calls for the full 10-tasks-per-family run).

Usage:
  python research/taskbench/runner.py smoke            # 2 tasks x both modes
  python research/taskbench/runner.py run [--tasks-per-family N] [--modes occ0,occ1]
  python research/taskbench/runner.py aggregate         # rebuild results JSON only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # worktree root
sys.path.insert(0, str(ROOT / "prime-agent-runtime" / "src"))

from rlm.occ1 import Occ1Error, Occ1Message  # noqa: E402

TASKS_FILE = ROOT / "research" / "tasks" / "tasks_dev.json"
CHECKERS_FILE = ROOT / "research" / "tasks" / "checkers.py"
TOKEN_FILE = Path(
    r"C:\Users\openclawuser\optimus-rust-test-20260915\cloud-model-gateway"
    r"\local-token.txt"
)

GATEWAY = "http://127.0.0.1:43120/ollama/v1/chat/completions"
SPEAKER_MODEL = "glm-5.3"
LISTENER_MODELS = ["glm-5.3", "glm-5.3-flash"]
FAMILIES = ["A", "B", "C", "D", "E"]

SEED = 42
TEMPERATURE = 0.0
OCC0_WORD_LIMIT = 120
A_MAX_TOKENS = 2048
B_MAX_TOKENS = 1024
TRANSPORT_ATTEMPTS = 3
CONCURRENCY = 4
REQUEST_TIMEOUT_S = 300.0

RUN_TAG = time.strftime("%Y%m%d-%H%M%S")
RESULTS_JSONL = HERE / "taskbench_runs.jsonl"
RESULTS_JSON = HERE / "results_taskbench.json"

A_SYSTEM_OCC0 = """You are Agent A, the sender in a two-agent protocol. Agent B will receive only your message and must answer a question about this briefing. You do not know which detail B will need, so convey the complete content of the briefing.

Write your message in plain English. Hard limit: at most 120 words.

Output ONLY the message text. No greeting, no commentary, no markdown."""

A_SYSTEM_OCC1 = """You are Agent A, the sender in a two-agent protocol. Agent B will receive only your message and must answer a question about this briefing. You do not know which detail B will need, so convey the complete content of the briefing.

Encode your message in OCC-1 compact structured English. Format:
- Line 1 is exactly: OCC/1
- Every following line is KEY=VALUE.
- KEY: uppercase letters, digits, underscores, starts with a letter (examples: REGION, STEP2, EVENT1).
- VALUE: plain text, no newlines.
- Choose short, meaningful keys that identify each value.

Example:
OCC/1
SERVICE=auth-api
PORT=8443
REPLICAS=4

Output ONLY the OCC-1 message. No markdown, no code fences, no commentary."""

B_SYSTEM = """You are Agent B, the receiver in a two-agent protocol. Below is a message from Agent A. It may be plain English or compact structured text (KEY=VALUE lines after an OCC/1 header). Use only what the message contains to answer the question.

Output ONLY the answer: a value, a name, a number, or a comma-separated list, exactly as the question requests. No explanation."""


def load_token() -> str:
    env = os.environ.get("OCC_GATEWAY_TOKEN_FILE")
    path = Path(env) if env else TOKEN_FILE
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("gateway token file is empty")
    return token


def load_tasks() -> list[dict]:
    data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    return data["tasks"]


def load_checkers():
    ns = {}
    exec(CHECKERS_FILE.read_text(encoding="utf-8"), ns)
    return ns["check_answer"]


async def chat_call(
    client: httpx.AsyncClient,
    token: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    label: str,
) -> dict:
    """One gateway call with transport-level retries. Returns call record."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": TEMPERATURE,
        "seed": SEED,
    }
    transport_retries = 0
    last_error = None
    for attempt in range(TRANSPORT_ATTEMPTS):
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                GATEWAY,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=REQUEST_TIMEOUT_S,
            )
            latency = time.perf_counter() - t0
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                transport_retries += 1
                if resp.status_code in (429, 500, 502, 503, 504) and attempt + 1 < TRANSPORT_ATTEMPTS:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                break
            data = resp.json()
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            usage = data.get("usage", {})
            return {
                "ok": True,
                "model": model,
                "label": label,
                "content": content,
                "finish_reason": choice.get("finish_reason"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "latency_s": round(latency, 3),
                "transport_retries": transport_retries,
                "error": None,
            }
        except Exception as exc:  # transport-level failure
            latency = time.perf_counter() - t0
            last_error = f"{type(exc).__name__}: {exc}"
            transport_retries += 1
            if attempt + 1 < TRANSPORT_ATTEMPTS:
                await asyncio.sleep(2.0 * (attempt + 1))
    return {
        "ok": False,
        "model": model,
        "label": label,
        "content": "",
        "finish_reason": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "latency_s": round(latency, 3),
        "transport_retries": transport_retries,
        "error": last_error,
    }


def parse_occ1(text: str):
    """Strict OCC-1 parse of a candidate message. Returns (ok, error_str)."""
    try:
        Occ1Message.decode(text)
        return True, None
    except Occ1Error as exc:
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # defensive: codec should only raise Occ1Error
        return False, f"{type(exc).__name__}: {exc}"


def occ0_violation(text: str):
    """Returns a feedback string when the occ0 constraint is violated."""
    words = len(text.split())
    if words > OCC0_WORD_LIMIT:
        return (
            f"Your previous message used {words} words, over the {OCC0_WORD_LIMIT}"
            " word limit. Rewrite it in at most 120 words."
        )
    return None


async def speaker_call(client, token, task, mode) -> dict:
    """Agent A: produce the message. One format retry allowed."""
    system = A_SYSTEM_OCC0 if mode == "occ0" else A_SYSTEM_OCC1
    user = f"Briefing (your sender input):\n\n{task['sender_input']}"
    record = {
        "attempts": [],
        "format_retries": 0,
        "message": None,
        "parse_ok": None,
        "constraint_ok": None,
        "error": None,
    }

    call = await chat_call(
        client, token, SPEAKER_MODEL, system, user, A_MAX_TOKENS, f"A[{mode}]"
    )
    record["attempts"].append(call)
    if not call["ok"]:
        record["error"] = call["error"]
        return record

    content = call["content"].strip()
    feedback = None
    if mode == "occ1":
        ok, err = parse_occ1(content)
        record["parse_ok"] = ok
        if not ok:
            feedback = (
                "Your previous output was not valid OCC-1. Error: "
                f"{err}. Output ONLY a valid OCC-1 message: first line OCC/1, "
                "then KEY=VALUE lines, uppercase keys, no newlines in values, "
                "no markdown or code fences."
            )
    else:
        feedback = occ0_violation(content)
        record["constraint_ok"] = feedback is None

    if feedback is None and content:
        record["message"] = content
        return record

    if not content:
        feedback = "Your previous output was empty. Output the message text only."
        record["constraint_ok"] = False

    # One counted format retry with feedback.
    record["format_retries"] = 1
    retry_call = await chat_call(
        client,
        token,
        SPEAKER_MODEL,
        system + "\n\n" + feedback,
        user,
        A_MAX_TOKENS,
        f"A[{mode}]retry",
    )
    record["attempts"].append(retry_call)
    if not retry_call["ok"]:
        record["error"] = retry_call["error"]
        # fall back to the first attempt's content if it exists
        record["message"] = content or None
        return record

    retry_content = retry_call["content"].strip()
    if mode == "occ1":
        ok2, err2 = parse_occ1(retry_content)
        record["parse_ok"] = ok2
        record["message"] = retry_content if ok2 else (content or retry_content)
    else:
        violation2 = occ0_violation(retry_content)
        record["constraint_ok"] = violation2 is None
        record["message"] = retry_content
    return record


async def listener_call(client, token, task, mode, message, listener) -> dict:
    """Agent B: answer from the message only."""
    user = f"Message from Agent A:\n{message}\n\nQuestion: {task['receiver_task']}"
    call = await chat_call(
        client, token, listener, B_SYSTEM, user, B_MAX_TOKENS, f"B[{mode},{listener}]"
    )
    record = {
        "attempts": [call],
        "format_retries": 0,
        "answer": None,
        "error": None,
    }
    content = call["content"].strip() if call["ok"] else ""
    if not call["ok"]:
        record["error"] = call["error"]
        return record
    if content:
        record["answer"] = content
        return record
    # one counted retry for an empty answer
    record["format_retries"] = 1
    retry_call = await chat_call(
        client,
        token,
        listener,
        B_SYSTEM + "\n\nYour previous output was empty. Output ONLY the answer.",
        user,
        B_MAX_TOKENS,
        f"B[{mode},{listener}]retry",
    )
    record["attempts"].append(retry_call)
    if retry_call["ok"] and retry_call["content"].strip():
        record["answer"] = retry_call["content"].strip()
    return record


def lenient_answer_variants(answer: str, family: str) -> list[str]:
    """Diagnostic extraction variants. Never used for the headline score."""
    variants = [answer]
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    if lines:
        variants.append(lines[-1])
    if family == "D":
        normalized = answer.replace("\n", ",").replace(";", ",")
        variants.append(normalized)
        if lines:
            variants.append(",".join(lines))
    return variants


async def run_task(client, token, check_answer, task, mode) -> dict:
    """Full two-agent run for one (mode, task): 1 A call + 2 B calls (+retries)."""
    a_rec = await speaker_call(client, token, task, mode)
    record = {
        "task_id": task["task_id"],
        "family": task["family"],
        "difficulty": task["difficulty"],
        "mode": mode,
        "speaker": SPEAKER_MODEL,
        "a": {
            "attempts": a_rec["attempts"],
            "format_retries": a_rec["format_retries"],
            "parse_ok": a_rec["parse_ok"],
            "constraint_ok": a_rec["constraint_ok"],
            "error": a_rec["error"],
            "message": a_rec["message"],
            "message_chars": len(a_rec["message"]) if a_rec["message"] else 0,
            "message_bytes": len(a_rec["message"].encode("utf-8")) if a_rec["message"] else 0,
            "message_words": len(a_rec["message"].split()) if a_rec["message"] else 0,
            "occ1_fields": (
                len(Occ1Message.decode(a_rec["message"]).fields)
                if a_rec["message"] and mode == "occ1" and a_rec["parse_ok"]
                else None
            ),
        },
        "b": {},
    }
    message = a_rec["message"]
    for listener in LISTENER_MODELS:
        b_rec = await listener_call(
            client, token, task, mode, message if message else "(empty message)", listener
        )
        answer = b_rec["answer"]
        strict = check_answer(task, answer) if answer is not None else False
        lenient = any(
            check_answer(task, v)
            for v in lenient_answer_variants(answer or "", task["family"])
        )
        record["b"][listener] = {
            "attempts": b_rec["attempts"],
            "format_retries": b_rec["format_retries"],
            "error": b_rec["error"],
            "answer": answer,
            "correct": strict,
            "lenient_correct": lenient,
        }
    return record


def pick_tasks(tasks, per_family: int) -> list[dict]:
    selected = []
    for fam in FAMILIES:
        fam_tasks = [t for t in tasks if t["family"] == fam]
        selected.extend(fam_tasks[:per_family])
    return selected


def load_completed() -> set[tuple[str, str]]:
    done = set()
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done.add((rec["mode"], rec["task_id"]))
            except json.JSONDecodeError:
                continue
    return done


async def run_matrix(per_family: int, modes: list[str], limit: int | None) -> None:
    token = load_token()
    tasks = load_tasks()
    check_answer = load_checkers()
    selected = pick_tasks(tasks, per_family)
    done = load_completed()
    jobs = [
        (mode, task)
        for mode in modes
        for task in selected
        if (mode, task["task_id"]) not in done
    ]
    if limit:
        jobs = jobs[:limit]
    print(
        f"selected {len(selected)} tasks, {len(done)} (mode,task) units already done, "
        f"{len(jobs)} to run now",
        flush=True,
    )

    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient() as client:
        async def run_one(task, mode):
            async with sem:
                rec = await run_task(client, token, check_answer, task, mode)
            with RESULTS_JSONL.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=True) + "\n")
            b0 = rec["b"][LISTENER_MODELS[0]]
            b1 = rec["b"][LISTENER_MODELS[1]]
            print(
                f"[{rec['mode']}] {rec['task_id']}: "
                f"A_retries={rec['a']['format_retries']} "
                f"parse_ok={rec['a']['parse_ok']} "
                f"words={rec['a']['message_words']} "
                f"| {LISTENER_MODELS[0]}={b0['correct']} "
                f"{LISTENER_MODELS[1]}={b1['correct']}",
                flush=True,
            )

        await asyncio.gather(*[run_one(task, mode) for mode, task in jobs])
    print("run complete", flush=True)


def aggregate() -> dict:
    """Rebuild the aggregated results JSON from the JSONL run log."""
    by_unit = {}
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rec = json.loads(line)
                by_unit[(rec["mode"], rec["task_id"])] = rec
    records = list(by_unit.values())

    def pct(values, q):
        if not values:
            return None
        s = sorted(values)
        idx = min(len(s) - 1, int(round(q * (len(s) - 1))))
        return round(s[idx], 3)

    def stats(values):
        values = [v for v in values if v is not None]
        if not values:
            return {"count": 0}
        return {
            "count": len(values),
            "mean": round(statistics.mean(values), 2),
            "median": round(statistics.median(values), 2),
            "p95": pct(values, 0.95),
        }

    agg = {
        "meta": {
            "protocol": "occ2-taskbench two-agent baseline",
            "speaker": SPEAKER_MODEL,
            "listeners": LISTENER_MODELS,
            "temperature": TEMPERATURE,
            "seed": SEED,
            "occ0_word_limit": OCC0_WORD_LIMIT,
            "a_max_tokens": A_MAX_TOKENS,
            "b_max_tokens": B_MAX_TOKENS,
            "tasks_file": str(TASKS_FILE.relative_to(ROOT)),
            "gateway": GATEWAY,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "num_records": len(records),
        },
        "cells": {},
        "per_family": {},
        "notes": [],
    }

    for mode in ("occ0", "occ1"):
        for listener in LISTENER_MODELS:
            recs = [r for r in records if r["mode"] == mode]
            cell = {
                "n_tasks": len(recs),
                "correct": sum(1 for r in recs if r["b"][listener]["correct"]),
                "accuracy": None,
                "lenient_correct": sum(1 for r in recs if r["b"][listener]["lenient_correct"]),
                "b_prompt_tokens": stats(
                    [r["b"][listener]["attempts"][0].get("prompt_tokens") for r in recs]
                ),
                "b_completion_tokens": stats(
                    [r["b"][listener]["attempts"][0].get("completion_tokens") for r in recs]
                ),
                "b_latency_s": stats(
                    [r["b"][listener]["attempts"][0].get("latency_s") for r in recs]
                ),
                "b_format_retries": sum(1 for r in recs if r["b"][listener]["format_retries"]),
                "b_transport_retries": sum(
                    a.get("transport_retries", 0)
                    for r in recs
                    for a in r["b"][listener]["attempts"]
                ),
                "b_errors": sum(1 for r in recs if r["b"][listener]["error"]),
            }
            if recs:
                cell["accuracy"] = round(cell["correct"] / len(recs), 4)
                cell["lenient_accuracy"] = round(cell["lenient_correct"] / len(recs), 4)
            agg["cells"][f"{mode}|{listener}"] = cell

        a_recs = [r for r in records if r["mode"] == mode]
        agg["cells"][f"{mode}|speaker"] = {
            "n_tasks": len(a_recs),
            "a_prompt_tokens": stats(
                [r["a"]["attempts"][0].get("prompt_tokens") for r in a_recs]
            ),
            "a_completion_tokens": stats(
                [r["a"]["attempts"][0].get("completion_tokens") for r in a_recs]
            ),
            "a_latency_s": stats(
                [r["a"]["attempts"][0].get("latency_s") for r in a_recs]
            ),
            "a_format_retries": sum(1 for r in a_recs if r["a"]["format_retries"]),
            "a_transport_retries": sum(
                a.get("transport_retries", 0) for r in a_recs for a in r["a"]["attempts"]
            ),
            "a_errors": sum(1 for r in a_recs if r["a"]["error"]),
            "message_words": stats([r["a"]["message_words"] for r in a_recs]),
            "message_chars": stats([r["a"]["message_chars"] for r in a_recs]),
            "message_bytes": stats([r["a"]["message_bytes"] for r in a_recs]),
            "occ0_constraint_ok": (
                sum(1 for r in a_recs if r["a"]["constraint_ok"]) if a_recs else None
            ),
            "occ1_parse_ok": sum(1 for r in a_recs if r["a"]["parse_ok"]) if a_recs else None,
            "occ1_fields": stats([r["a"]["occ1_fields"] for r in a_recs]),
        }

    for mode in ("occ0", "occ1"):
        for listener in LISTENER_MODELS:
            for fam in FAMILIES:
                recs = [
                    r
                    for r in records
                    if r["mode"] == mode and r["family"] == fam
                ]
                if not recs:
                    continue
                key = f"{mode}|{listener}|{fam}"
                agg["per_family"][key] = {
                    "n": len(recs),
                    "correct": sum(1 for r in recs if r["b"][listener]["correct"]),
                    "accuracy": round(
                        sum(1 for r in recs if r["b"][listener]["correct"]) / len(recs), 4
                    ),
                    "lenient_accuracy": round(
                        sum(1 for r in recs if r["b"][listener]["lenient_correct"]) / len(recs), 4
                    ),
                    "b_prompt_tokens_mean": stats(
                        [r["b"][listener]["attempts"][0].get("prompt_tokens") for r in recs]
                    )["mean"],
                }

    # call accounting and failure detail
    total_calls = 0
    format_retries = 0
    transport_retries = 0
    truncated = 0
    for r in records:
        total_calls += len(r["a"]["attempts"]) + sum(
            len(b["attempts"]) for b in r["b"].values()
        )
        format_retries += r["a"]["format_retries"] + sum(
            b["format_retries"] for b in r["b"].values()
        )
        for a in r["a"]["attempts"]:
            transport_retries += a.get("transport_retries", 0)
            if a.get("finish_reason") not in ("stop", None):
                truncated += 1
        for b in r["b"].values():
            for a in b["attempts"]:
                transport_retries += a.get("transport_retries", 0)
                if a.get("finish_reason") not in ("stop", None):
                    truncated += 1
    agg["call_accounting"] = {
        "total_gateway_calls": total_calls,
        "format_retries": format_retries,
        "transport_retries": transport_retries,
        "truncated_calls": truncated,
    }

    task_gold = {t["task_id"]: t["gold_answer"] for t in load_tasks()}
    failures = []
    for r in records:
        for listener, b in r["b"].items():
            if not b["correct"]:
                failures.append(
                    {
                        "mode": r["mode"],
                        "task_id": r["task_id"],
                        "family": r["family"],
                        "difficulty": r["difficulty"],
                        "listener": listener,
                        "answer": b["answer"],
                        "lenient_correct": b["lenient_correct"],
                        "gold_answer": task_gold.get(r["task_id"]),
                        "a_error": r["a"]["error"],
                        "b_error": b["error"],
                    }
                )
    agg["failures"] = failures

    # wire-cost comparison: same task, same listener, B prompt delta occ0 vs occ1
    by_key = {(r["mode"], r["task_id"]): r for r in records}
    deltas = []
    for (mode, task_id), rec in by_key.items():
        if mode != "occ0":
            continue
        other = by_key.get(("occ1", task_id))
        if not other:
            continue
        for listener in LISTENER_MODELS:
            p0 = rec["b"][listener]["attempts"][0].get("prompt_tokens")
            p1 = other["b"][listener]["attempts"][0].get("prompt_tokens")
            if p0 is not None and p1 is not None:
                deltas.append(
                    {
                        "listener": listener,
                        "task_id": task_id,
                        "occ0_b_prompt": p0,
                        "occ1_b_prompt": p1,
                        "delta": p1 - p0,
                    }
                )
    for listener in LISTENER_MODELS:
        dv = [d["delta"] for d in deltas if d["listener"] == listener]
        agg.setdefault("wire_cost", {})[listener] = {
            "pairs": len(dv),
            "b_prompt_delta_occ1_minus_occ0": stats(dv),
            "occ0_minus_occ1_mean": (
                round(-statistics.mean(dv), 2) if dv else None
            ),
        }

    RESULTS_JSON.write_text(
        json.dumps(agg, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {RESULTS_JSON}")
    return agg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("smoke", help="2 tasks x both modes, small concurrency")
    run_p = sub.add_parser("run", help="run the mode x listener matrix")
    run_p.add_argument("--tasks-per-family", type=int, default=10)
    run_p.add_argument("--modes", default="occ0,occ1")
    run_p.add_argument("--limit", type=int, default=None)
    sub.add_parser("aggregate", help="rebuild results JSON from the run log")
    args = parser.parse_args()

    if args.cmd == "smoke":
        asyncio.run(run_matrix(per_family=1, modes=["occ0", "occ1"], limit=None))
        aggregate()
    elif args.cmd == "run":
        modes = [m.strip() for m in args.modes.split(",") if m.strip()]
        asyncio.run(
            run_matrix(
                per_family=args.tasks_per_family,
                modes=modes,
                limit=args.limit,
            )
        )
        aggregate()
    else:
        aggregate()


if __name__ == "__main__":
    main()
