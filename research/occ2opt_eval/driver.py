#!/usr/bin/env python3
"""DGX-only evaluation driver for the OCC-2 optimization campaign (occ2opt).

Bookkeeping / scoring / provenance engine for the T1 paired speaker-listener
topology designed in reports/occ2opt/DGX-EVAL-DRIVER.md. This process NEVER
talks to a model or the network: the DGX GLM 5.3 model calls happen only
through agent_message between campaign subagents (speaker child + listener
child), spawned and driven by the PARENT. The driver:

  - plans per-item prompts and instruction manifests for the parent,
  - consumes the reply files the parent collects from the children,
  - scores with the frozen checkers (research/tasks/checkers.py) and the
    kernel-codec round-trip oracle (research/symbolic_comm/contract.py,
    which loads prime-agent-runtime/src/rlm/occ1.py offline),
  - writes per-item JSONL records, a run manifest, and a summary,
  - applies the acceptance gates from the design doc.

Safety: --live requires BOTH the --confirm-live flag AND the environment
variable OPTIMUS_OCC2OPT_EVAL_LIVE=1. Nothing model-touching exists in this
file at all; --live only unlocks writing live run directories.

Usage:
  python research/occ2opt_eval/driver.py --split dev --mode scripted --batch 10 --dry-run
  python research/occ2opt_eval/driver.py --self-test
  python research/occ2opt_eval/driver.py --split dev --mode scripted --batch 10 --live \
      --stage speaker --confirm-live        (parent-invoked; needs the env var)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in ("research/tasks", "research/symbolic_comm"):
    _full = os.path.join(_ROOT, _p)
    if _full not in sys.path:
        sys.path.insert(0, _full)

import checkers  # frozen QA family checkers (A text / D set / E numeric)
import contract as SYM  # frozen typed contract + kernel OCC-1 codec oracle

import occ2_codec  # OCC-2 wire codec mirrors (sym-v1 production, sym-v2 promoted)

RECEIPTS_PATH = os.path.join(_ROOT, "research", "tasks2", "receipts.json")
CORPUS_FMT = os.path.join(_ROOT, "research", "tasks2", "msgs_corpus2_%s.json")

SPLITS = ("evolve", "dev", "holdout")
MODES = ("scripted", "free")
DIALECTS = ("sym-v1", "sym-v2-d2-r1")
STAGES = ("speaker", "listener", "score")
CANARIES_PER_BATCH = 2
LIVE_ENV = "OPTIMUS_OCC2OPT_EVAL_LIVE"

# Designed dry-run fault injection counts (all on distinct non-canary items).
N_SPEAKER_FORMAT_FAULTS = 1
N_SPEAKER_SEMANTIC_FAULTS = 2
N_LISTENER_QA_FAULTS = 1
N_LISTENER_COMPREHENSION_FAULTS = 1
N_LISTENER_FORMAT_FAULTS = 1

SYNTH_BASE_STAMP = datetime.datetime(2026, 11, 3, 9, 0, 0, tzinfo=datetime.timezone.utc)

LABELS = {
    "sizes": "chars, not provider tokens",
    "heterogeneous_model_pairs": "X1-X4 deferred to the online phase",
    "matrix_cell": "C1 (glm-5.3 -> glm-5.3)",
    "latency_dry_run": "synthetic (dry-run)",
    "latency_live": "wall-clock, channel-inclusive (agent_message)",
    "channel": "agent_message (DGX GLM 5.3 children, parent-orchestrated)",
}

REQUIRED_LABEL_KEYS = ("sizes", "heterogeneous_model_pairs", "matrix_cell", "channel")

# Local tokenizer for wire-token metrics (metrics only, never gates).
# Same model as the CODEBOOK-COST measurement: Qwen2.5-0.5B-Instruct,
# add_special_tokens=False, loaded from the offline model directory.
TOKENIZER_MODELS_DIR = os.path.join(
    _ROOT, "..", "optimus-occ23-models", "hub"
)
TOKENIZER_REPO = "models--Qwen--Qwen2.5-0.5B-Instruct"
TOKENIZER_SOURCE_LABEL = "qwen2.5-0.5b-instruct-local"

_TOKENIZER = None
_TOKENIZER_FAILED = False


def _load_tokenizer():
    """Lazy local-tokenizer load; None when unavailable (metrics degrade,
    gates unaffected)."""
    global _TOKENIZER, _TOKENIZER_FAILED
    if _TOKENIZER is not None or _TOKENIZER_FAILED:
        return _TOKENIZER
    try:
        import glob as _glob

        snapshots = os.path.join(os.path.abspath(TOKENIZER_MODELS_DIR), TOKENIZER_REPO, "snapshots")
        matches = sorted(_glob.glob(os.path.join(snapshots, "*")))
        if not matches:
            raise FileNotFoundError("no snapshot under %s" % snapshots)
        from transformers import AutoTokenizer

        _TOKENIZER = AutoTokenizer.from_pretrained(matches[-1], local_files_only=True)
    except Exception:  # noqa: BLE001 - any failure degrades metrics only
        _TOKENIZER_FAILED = True
        _TOKENIZER = None
    return _TOKENIZER


def count_tokens_texts(texts):
    """Token counts for a list of texts, or None when the tokenizer is
    unavailable. Batched; add_special_tokens=False."""
    tokenizer = _load_tokenizer()
    if tokenizer is None or not texts:
        return None
    out = []
    batch = 32
    for start in range(0, len(texts), batch):
        chunk = [t if t is not None else "" for t in texts[start:start + batch]]
        encoded = tokenizer(chunk, add_special_tokens=False)["input_ids"]
        out.extend(len(ids) for ids in encoded)
    return out


def count_tokens_text(text):
    if text is None:
        return None
    counts = count_tokens_texts([text])
    return None if counts is None else counts[0]


# ---------------------------------------------------------------- utils

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.write("\n")


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


def synth_stamp(offset_seconds):
    moment = SYNTH_BASE_STAMP + datetime.timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Gate:
    """Check ledger used by the acceptance gates and the self-test."""

    def __init__(self):
        self.total = 0
        self.failures = []

    def check(self, ok, label):
        self.total += 1
        if not ok:
            self.failures.append(label)
        return bool(ok)


# ---------------------------------------------------------------- corpus

def load_receipts():
    return json.loads(open(RECEIPTS_PATH, encoding="utf-8").read())


def load_split(split):
    """Load one corpus split and verify its sha256 against receipts.json."""
    path = CORPUS_FMT % split
    if not os.path.isfile(path):
        raise SystemExit("missing corpus split file: %s" % path)
    receipts = load_receipts()
    expected = receipts["splits"][split]["sha256"]
    actual = sha256_file(path)
    if expected != actual:
        raise SystemExit("corpus split %s hash mismatch: receipts %s != file %s"
                         % (split, expected[:12], actual[:12]))
    doc = json.loads(open(path, encoding="utf-8").read())
    items = doc["messages"]
    ids = [item["id"] for item in items]
    if len(set(ids)) != len(ids):
        raise SystemExit("corpus split %s has duplicate ids" % split)
    return items, actual, receipts


# ---------------------------------------------------------------- planning

def plan_batches(items, batch_size, seed):
    """Shuffle item presentation order per batch; pick canary items.

    Returns a list of batch dicts:
      {"index": i, "item_ids": [...], "canary_ids": {...}, "positions": {id: pos}}
    """
    rng = random.Random(seed)
    batches = []
    for start in range(0, len(items), batch_size):
        chunk = items[start:start + batch_size]
        order = [item["id"] for item in chunk]
        rng.shuffle(order)
        n_canary = min(CANARIES_PER_BATCH, len(order))
        canary_ids = set(rng.sample(order, n_canary))
        batches.append({
            "index": len(batches),
            "item_ids": order,
            "canary_ids": sorted(canary_ids),
            "positions": {item_id: pos for pos, item_id in enumerate(order)},
        })
    return batches


def presentation_order(items, batches):
    """Items in the exact order children see them (batch by batch)."""
    by_id = {item["id"]: item for item in items}
    ordered = []
    for batch in batches:
        for item_id in batch["item_ids"]:
            ordered.append(by_id[item_id])
    return ordered


# ---------------------------------------------------------------- prompts

SPEAKER_PROMPT_SCRIPTED = """You are Agent A, the sender in a two-agent protocol. Encode the typed message below as an OCC/1 wire message, using the OCC/1 grammar from your agent-message skill documentation. Emit ONLY the wire text starting with the line "OCC/1", no commentary, no code fences.

Typed message (ground truth; do not paraphrase):
"""

SPEAKER_PROMPT_FREE = """You are Agent A, the sender in a two-agent protocol. Read the plain-English report below and encode the information it carries as an OCC/1 wire message, using the OCC/1 grammar from your agent-message skill documentation. Emit ONLY the wire text starting with the line "OCC/1", no commentary, no code fences.

Plain-English report (the facts to encode):
"""

LISTENER_PROMPT_HEAD = """You are Agent B, the receiver in a two-agent protocol. Agent A sent the wire message below. Answer the numbered questions about its content. Quote values exactly as they appear on the wire. Output each answer on its own line as "N. <answer>". Then output a final line "PAYLOAD:" followed by the reconstructed typed message as one JSON object on one line.

Wire message:
"""

LISTENER_PROMPT_MID = """

Questions:
"""

# OCC-2 dialect mode: the speaker emits the dialect codebook (verbatim,
# exactly as the runtime renders it) followed by a blank line and the wire.
SPEAKER_PROMPT_SCRIPTED_OCC2 = """You are Agent A, the sender in a two-agent protocol. Encode the typed message below as an OCC-2 {dialect} wire message, using the codebook below. Emit ONLY the codebook (verbatim, exactly as given), then a blank line, then the wire message whose first line is exactly {header}. No commentary, no code fences.

Codebook (emit verbatim, before the wire):
{codebook}

Typed message (ground truth; do not paraphrase):
"""

SPEAKER_PROMPT_FREE_OCC2 = """You are Agent A, the sender in a two-agent protocol. Read the plain-English report below and encode the information it carries as an OCC-2 {dialect} wire message, using the codebook below. Emit ONLY the codebook (verbatim, exactly as given), then a blank line, then the wire message whose first line is exactly {header}. No commentary, no code fences.

Codebook (emit verbatim, before the wire):
{codebook}

Plain-English report (the facts to encode):
"""

LISTENER_PROMPT_HEAD_OCC2_FIRST = """You are Agent B, the receiver in a two-agent protocol. Agent A sent the wire message below, encoded in the OCC-2 {dialect} dialect; its codebook appears first and defines every message in this conversation. Answer the numbered questions about the wire's content. Quote values exactly as they appear on the wire. Output each answer on its own line as "N. <answer>". Then output a final line "PAYLOAD:" followed by the reconstructed typed message as one JSON object on one line.
The reconstructed typed message must use the canonical schema (omit fields the wire does not carry): {{"task_id": string, "kind": "TASK"|"PROGRESS"|"EVIDENCE"|"RESULT"|"ACK"|"BLOCKED"|"CORRECTION"|"CANCELLED"|"REQUEST_DETAIL", "status": "pending"|"in_progress"|"done"|"partial"|"blocked"|"failed"|"cancelled", "message_id": string, "parent_task_id": string|null, "trace_id": string, "sender_identity": string, "recipient_identity": string, "sequence": integer, "timestamp": RFC3339 string, "expiry": string|null, "findings": [{{"id": "F1", "statement": string, "confidence": number|null, "evidence": ["E1", ...]}}], "evidence": [{{"id": "E1", "kind": "artifact"|"build"|"commit"|"finding"|"log"|"lines"|"src"|"tool", "locator": string, "digest": string|null, "revision": string|null, "line_range": [integer, integer]|null, "verified": true|false|null}}], "artifacts": [string], "dependencies": [string], "next_actions": [string], "verification": {{"claim": string, "method": string, "receipt": string, "status": "pass"|"fail"|"unknown"}}|null}}. Findings and evidence ids are assigned positionally (F1, F2, ... / E1, E2, ...); findings reference evidence by id. Timestamps decode from epoch36. Verified flags decode from !1/!0; unknown stays null. Line ranges decode from @<start>-<end> to a two-integer array.

Codebook:
{codebook}

Wire message:
"""

LISTENER_PROMPT_HEAD_OCC2 = """You are Agent B, the receiver in a two-agent protocol. Agent A sent the wire message below, encoded in the OCC-2 {dialect} dialect whose codebook was provided earlier in this conversation. Answer the numbered questions about the wire's content. Quote values exactly as they appear on the wire. Output each answer on its own line as "N. <answer>". Then output a final line "PAYLOAD:" followed by the reconstructed typed message as one JSON object on one line.
The reconstructed typed message must use the canonical schema (omit fields the wire does not carry): {{"task_id": string, "kind": "TASK"|"PROGRESS"|"EVIDENCE"|"RESULT"|"ACK"|"BLOCKED"|"CORRECTION"|"CANCELLED"|"REQUEST_DETAIL", "status": "pending"|"in_progress"|"done"|"partial"|"blocked"|"failed"|"cancelled", "message_id": string, "parent_task_id": string|null, "trace_id": string, "sender_identity": string, "recipient_identity": string, "sequence": integer, "timestamp": RFC3339 string, "expiry": string|null, "findings": [{{"id": "F1", "statement": string, "confidence": number|null, "evidence": ["E1", ...]}}], "evidence": [{{"id": "E1", "kind": "artifact"|"build"|"commit"|"finding"|"log"|"lines"|"src"|"tool", "locator": string, "digest": string|null, "revision": string|null, "line_range": [integer, integer]|null, "verified": true|false|null}}], "artifacts": [string], "dependencies": [string], "next_actions": [string], "verification": {{"claim": string, "method": string, "receipt": string, "status": "pass"|"fail"|"unknown"}}|null}}. Findings and evidence ids are assigned positionally (F1, F2, ... / E1, E2, ...); findings reference evidence by id. Timestamps decode from epoch36. Verified flags decode from !1/!0; unknown stays null. Line ranges decode from @<start>-<end> to a two-integer array.

Wire message:
"""


def build_speaker_prompt(item, mode, codec=None):
    if codec is not None:
        template = SPEAKER_PROMPT_FREE_OCC2 if mode == "free" else SPEAKER_PROMPT_SCRIPTED_OCC2
        head = template.format(
            dialect=codec.dialect_id,
            header=repr(codec.wire_header_hint()),
            codebook=codec.codebook_text(),
        )
        if mode == "scripted":
            return head + json.dumps(item["payload"], ensure_ascii=True, sort_keys=True)
        return head + item["plain_english"]
    if mode == "scripted":
        body = SPEAKER_PROMPT_SCRIPTED + json.dumps(item["payload"], ensure_ascii=True, sort_keys=True)
    else:
        body = SPEAKER_PROMPT_FREE + item["plain_english"]
    return body


def build_listener_prompt(item, wire_text, codec=None, with_codebook=True):
    questions = "\n".join("%d. %s" % (n + 1, qa["q"]) for n, qa in enumerate(item["qa"]))
    if codec is not None:
        if with_codebook:
            head = LISTENER_PROMPT_HEAD_OCC2_FIRST.format(
                dialect=codec.dialect_id, codebook=codec.codebook_text()
            )
        else:
            head = LISTENER_PROMPT_HEAD_OCC2.format(dialect=codec.dialect_id)
        return head + wire_text + LISTENER_PROMPT_MID + questions
    return LISTENER_PROMPT_HEAD + wire_text + LISTENER_PROMPT_MID + questions


# ---------------------------------------------------------------- reply parsing

_ANSWER_RE = re.compile(r"^(\d+)\.\s*(.*)$")


def parse_speaker_reply(text, codec=None):
    """Validate a speaker reply. Returns (ok, wire, reason[, codebook_ok]).

    Exactness is part of the test: only trailing whitespace may be trimmed.
    A fenced or empty reply is a protocol fault. In dialect mode the reply
    is the codebook (verbatim), a blank line, then the wire; a bare wire is
    accepted and recorded (codebook_ok False), but any other preamble is a
    protocol fault.
    """
    if codec is not None:
        ok, wire, reason, codebook_ok = codec.parse_speaker_output(text)
        if not ok:
            return False, None, reason, None
        try:
            codec.decode(wire)
        except Exception as error:  # noqa: BLE001 - decode failure is a protocol fault
            return False, None, "undecodable wire: %s" % error, None
        return True, wire, None, codebook_ok
    if text is None:
        return False, None, "no reply"
    stripped = text.rstrip()
    if not stripped:
        return False, None, "empty reply"
    if stripped.startswith("```"):
        return False, None, "fenced reply"
    if not stripped.startswith("OCC/1"):
        return False, None, "does not start with the OCC/1 header"
    try:
        SYM.Occ1Message.decode(stripped)
    except Exception as error:  # noqa: BLE001 - decode failure is a protocol fault
        return False, None, "undecodable wire: %s" % error
    return True, stripped, None


def normalize_header_keys(wire):
    """Alias the kernel-grammar header keys to the frozen-contract key names.

    The production kernel grammar (rlm.occ1) uses MID/TO/FROM; the frozen
    research contract decodes MSGID/SENDER/RECIPIENT. Live speakers follow the
    kernel grammar. This is a pure key alias, not a semantic change.
    """
    out_lines = []
    for line in wire.splitlines():
        m = re.match(r"^(MID|TO|FROM)=(.*)$", line.strip())
        if m:
            alias = {"MID": "MSGID", "TO": "RECIPIENT", "FROM": "SENDER"}[m.group(1)]
            line = "%s=%s" % (alias, m.group(2))
        out_lines.append(line)
    return "\n".join(out_lines)


def normalize_ev_values(wire):
    """Normalize boolean EV tokens to the pass/fail grammar the frozen contract expects.

    The kernel codec (rlm.occ1) accepts true/false for verified flags and the
    corpus gold uses booleans, while the frozen research contract only accepts
    pass/fail. Live speakers following the kernel grammar emit EV1=true, which
    is semantically identical. Normalize before the frozen decode; no semantic
    change.
    """
    out_lines = []
    for line in wire.splitlines():
        m = re.match(r"^(EV\d+)=(true|false)$", line.strip())
        if m:
            line = "%s=%s" % (m.group(1), "pass" if m.group(2) == "true" else "fail")
        out_lines.append(line)
    return "\n".join(out_lines)


def _reattach_verification(decoded, back):
    """Re-attach the kernel-grammar verification block the frozen contract predates.

    The kernel OCC-1 grammar carries VERIFY/VMETHOD/VRECEIPT; the frozen research
    contract's from_occ1 predates that block and leaves verification null. The
    gold corpus (v1 and v2) carries verification payloads, so without this the
    oracle would mis-score every verification-bearing message. Deterministic
    key alias only.
    """
    verify_line = decoded.get("VERIFY")
    if verify_line is None:
        return
    claim, sep, status_val = verify_line.rpartition(":")
    if not sep or not status_val:
        claim, status_val = verify_line, "unknown"
    method = decoded.get("VMETHOD")
    receipt = decoded.get("VRECEIPT")
    back.verification = SYM.Verification(claim=claim, status=status_val, method=method, receipt=receipt)


def score_speaker_wire(item, wire, codec=None):
    """Decode the wire and compare against the ground-truth typed payload."""
    if codec is not None:
        try:
            back = codec.decode(wire)
        except Exception as error:  # noqa: BLE001
            return False, "undecodable wire: %s" % error, None
        ground = SYM.AgentCommMessage.from_payload(item["payload"])
        ok = SYM.equal(ground, back)
        detail = None if ok else "wire decodes but differs from the ground truth"
        return ok, detail, back
    wire = normalize_ev_values(normalize_header_keys(wire))
    try:
        decoded = SYM.Occ1Message.decode(wire)
        back = SYM.AgentCommMessage.from_occ1(decoded)
        _reattach_verification(decoded, back)
        ground = SYM.AgentCommMessage.from_payload(item["payload"])
        ok = SYM.equal(ground, back)
        detail = None if ok else "wire decodes but differs from the ground truth"
        return ok, detail, back
    except Exception as error:  # noqa: BLE001
        return False, "decode failed: %s" % error, None


PENDING_PAYLOAD = object()


def parse_listener_reply(text):
    """Parse numbered answers plus the PAYLOAD line.

    Returns (answers, payload_text, fenced, ok, reason). Missing PAYLOAD is a
    protocol fault; answers may still parse and are returned when present.
    """
    if text is None:
        return {}, None, False, False, "no reply"
    stripped = text.rstrip()
    if not stripped:
        return {}, None, False, False, "empty reply"
    answers = {}
    payload_text = None
    fenced = False
    for line in stripped.split("\n"):
        match = _ANSWER_RE.match(line.strip())
        if match:
            answers[int(match.group(1))] = match.group(2).strip()
            continue
        if line.strip().startswith("PAYLOAD:"):
            inline = line.strip()[len("PAYLOAD:"):].strip()
            payload_text = inline if inline else PENDING_PAYLOAD
    if payload_text is PENDING_PAYLOAD:
        # PAYLOAD: appeared alone; the JSON is on the following non-empty line.
        seen = False
        for line in stripped.split("\n"):
            if seen and line.strip():
                payload_text = line.strip()
                break
            if line.strip().startswith("PAYLOAD:"):
                seen = True
        if payload_text is PENDING_PAYLOAD:
            return answers, None, fenced, False, "missing PAYLOAD line"
    if payload_text is None:
        return answers, None, fenced, False, "missing PAYLOAD line"
    return answers, payload_text, fenced, True, None


def score_listener(item, answers, payload_text):
    """Score QA answers and payload reconstruction.

    Returns (qa_rows, qa_correct, comprehension_ok, comp_detail).
    """
    qa_rows = []
    qa_correct = 0
    for n, qa in enumerate(item["qa"], start=1):
        got = answers.get(n)
        passed = got is not None and checkers.check_answer(
            {"family": qa["family"], "gold_answer": qa["a"]}, got)
        qa_rows.append({"n": n, "gold": qa["a"], "got": got, "pass": bool(passed)})
        if passed:
            qa_correct += 1
    comprehension_ok = False
    comp_detail = None
    if payload_text is None:
        comp_detail = "payload missing"
    else:
        try:
            listener_payload, fenced = SYM.payload_from_listener_json(payload_text)
            ground = SYM.AgentCommMessage.from_payload(item["payload"])
            rebuilt = SYM.AgentCommMessage.from_payload(listener_payload)
            comprehension_ok = SYM.equal(ground, rebuilt)
            if not comprehension_ok:
                comp_detail = "reconstructed payload differs from the ground truth"
        except Exception as error:  # noqa: BLE001
            comp_detail = "payload unparseable: %s" % error
    return qa_rows, qa_correct, comprehension_ok, comp_detail


# ---------------------------------------------------------------- dry-run synthesis

_STATUS_Q_RE = re.compile(r"^What is the status of the task in message ([a-z0-9-]+)\?$")


def has_status_question(item):
    return any(_STATUS_Q_RE.match(qa["q"]) for qa in item["qa"])


def derive_answer(question, payload, expected_id=None):
    """Re-derive one QA answer from a payload dict (the wire's view).

    Mirrors the question templates of research/tasks2/gen_corpus2.make_qa.
    Matching is prefix-based (the embedded message id is validated when
    expected_id is given).
    """
    if expected_id is not None and expected_id not in question:
        raise ValueError("question does not reference item %r" % expected_id)
    # Note: to_payload() omits empty lists, so every access is .get-based.
    if question.startswith("What is the status of the task in message"):
        return payload.get("status", "")
    if question.startswith("What kind of message is message"):
        return payload.get("kind", "")
    if question.startswith("Who is the sender of message"):
        return payload.get("sender_identity", "")
    if question.startswith("Which task is message") and " about?" in question:
        return payload.get("task_id", "")
    if question.startswith("How many findings does message"):
        return str(len(payload.get("findings", [])))
    if question.startswith("How many evidence entries does message"):
        return str(len(payload.get("evidence", [])))
    if question.startswith("What is the sequence number of message"):
        return str(payload.get("sequence", 0))
    if question.startswith("Which task dependencies does message"):
        return ",".join(payload.get("dependencies", [])) or "none"
    if question.startswith("Which artifacts does message"):
        return ",".join(payload.get("artifacts", [])) or "none"
    if question.startswith("Which evidence kinds does message"):
        kinds = sorted({entry["kind"] for entry in payload.get("evidence", [])})
        return ",".join(kinds) if kinds else "none"
    if question.startswith("What verification status does message"):
        return payload.get("verification", {}).get("status", "unknown")
    confidence_match = re.match(
        r"^What is the confidence value of point (\d+) in message", question)
    if confidence_match:
        index = int(confidence_match.group(1)) - 1
        return "%.3f" % payload.get("findings", [{}])[index].get("confidence", 0.0)
    raise ValueError("unknown question template: %r" % question)


def mutated_wire(item, kind, codec=None):
    """A semantically diverged but well-formed wire (speaker semantic fault)."""
    payload = json.loads(json.dumps(item["payload"]))
    if kind == "status":
        payload["status"] = "blocked" if payload["status"] != "blocked" else "pending"
    elif kind == "next_drop":
        if len(payload.get("next_actions", [])) < 2:
            raise ValueError("item has fewer than 2 next_actions")
        payload["next_actions"] = payload["next_actions"][:-1]
    else:
        raise ValueError("unknown mutation %r" % kind)
    message = SYM.AgentCommMessage.from_payload(payload)
    if codec is not None:
        return codec.encode(message)
    return message.to_occ1().encode()


def decode_wire_payload(wire, codec=None):
    if codec is not None:
        return codec.decode_wire_payload(wire)
    decoded = SYM.Occ1Message.decode(wire)
    back = SYM.AgentCommMessage.from_occ1(decoded)
    return back.to_payload()


def ground_truth_wire(item, codec=None):
    message = SYM.AgentCommMessage.from_payload(item["payload"])
    if codec is not None:
        return codec.encode(message)
    return message.to_occ1().encode()


def synthesize_speaker_reply(item, mode, fault, rng, codec=None):
    """Synthetic speaker reply: ground-truth wire, or a designed fault."""
    if codec is not None:
        body = codec.codebook_text() + "\n\n" + codec.encode(
            SYM.AgentCommMessage.from_payload(item["payload"])
        )
        if fault == "format":
            return "```\n" + body + "\n```"
        if fault == "status":
            return codec.codebook_text() + "\n\n" + mutated_wire(item, "status", codec)
        if fault == "next_drop":
            return codec.codebook_text() + "\n\n" + mutated_wire(item, "next_drop", codec)
        return body
    if fault == "format":
        return "```\n" + ground_truth_wire(item) + "\n```"
    if fault == "status":
        return mutated_wire(item, "status")
    if fault == "next_drop":
        return mutated_wire(item, "next_drop")
    return ground_truth_wire(item)


def synthesize_listener_reply(item, wire, fault, rng, codec=None):
    """Synthetic listener reply derived from the wire it received."""
    payload = decode_wire_payload(wire, codec)
    answers = [derive_answer(qa["q"], payload, expected_id=item["id"]) for qa in item["qa"]]
    if fault == "qa_corrupt":
        answers[0] = answers[0] + " corrupted"
    lines = ["%d. %s" % (n + 1, answer) for n, answer in enumerate(answers)]
    if fault == "format":
        return "\n".join(lines)
    emit = payload
    if fault == "payload_drift":
        emit = dict(payload)
        emit["task_id"] = emit["task_id"] + "-drifted"
    return "\n".join(lines) + "\nPAYLOAD: " + json.dumps(emit, ensure_ascii=True, sort_keys=True)


def choose_fault_items(items, batches, rng):
    """Pick distinct non-canary items for each designed dry-run fault."""
    canaries = set()
    for batch in batches:
        canaries.update(batch["canary_ids"])
    pool = [item for item in items if item["id"] not in canaries]

    def take(predicate, taken):
        for item in pool:
            if item["id"] not in taken and predicate(item):
                return item
        return None

    taken = set()
    picks = {}
    for key, predicate in (
        ("speaker_format", lambda item: True),
        ("speaker_status", has_status_question),
        ("speaker_next_drop", lambda item: len(item["payload"].get("next_actions", [])) >= 2),
        ("listener_qa", lambda item: True),
        ("listener_comprehension", lambda item: True),
        ("listener_format", lambda item: True),
    ):
        item = take(predicate, taken)
        if item is None:
            raise SystemExit("dry-run fault planning failed for %s" % key)
        picks[key] = item["id"]
        taken.add(item["id"])
    return picks


# ---------------------------------------------------------------- run state

def new_run(split, mode, batch_size, dry, limit=None, run_dir=None, seed=None,
             dialect=None):
    if split not in SPLITS:
        raise SystemExit("unknown split %r" % split)
    if mode not in MODES:
        raise SystemExit("unknown mode %r" % mode)
    if dialect is not None and dialect not in DIALECTS:
        raise SystemExit("unknown dialect %r (choose from %s)" % (dialect, ", ".join(DIALECTS)))
    items, corpus_sha, receipts = load_split(split)
    if limit:
        items = items[:limit]
    codec = occ2_codec.get_codec(dialect) if dialect else None
    seed = seed if seed is not None else (
        "occ2opt-eval-%s-%s-b%d" % (split, mode, batch_size)
        if dialect is None else "occ2opt-eval-%s-%s-%s-b%d" % (split, mode, dialect, batch_size)
    )
    if isinstance(seed, str):
        seed = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
    batches = plan_batches(items, batch_size, seed)
    if dry:
        run_id = "dry-%s-%s-b%d" % (split, mode, batch_size)
        if dialect:
            run_id = "dry-%s-%s-%s-b%d" % (split, dialect, mode, batch_size)
    else:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = "live-%s-%s-%s" % (split, mode, stamp)
        if dialect:
            run_id = "live-%s-%s-%s-%s" % (split, dialect, mode, stamp)
    run_dir = run_dir or os.path.join(_HERE, "runs", run_id)
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "split": split,
        "mode": mode,
        "dialect": dialect,
        "batch_size": batch_size,
        "dry": dry,
        "seed": seed,
        "items": items,
        "batches": batches,
        "corpus_sha": corpus_sha,
        "receipts": receipts,
        "codec": codec,
    }


def run_path(run, *parts):
    return os.path.join(run["run_dir"], *parts)


def write_run_manifest(run, fault_plan=None):
    manifest = {
        "run_id": run["run_id"],
        "driver": "research/occ2opt_eval/driver.py",
        "design": "reports/occ2opt/DGX-EVAL-DRIVER.md",
        "split": run["split"],
        "mode": run["mode"],
        "dialect": run.get("dialect"),
        "dialect_codec": (
            {
                "dialect_id": run["codec"].dialect_id,
                "version": run["codec"].version,
                "codebook_sha256_prefix": run["codec"].codebook_sha_prefix(),
            }
            if run.get("codec") is not None else None
        ),
        "batch_size": run["batch_size"],
        "limit": len(run["items"]),
        "seed": run["seed"],
        "dry_run": run["dry"],
        "corpus": {
            "split_file": "research/tasks2/msgs_corpus2_%s.json" % run["split"],
            "sha256": run["corpus_sha"],
            "receipts": "research/tasks2/receipts.json",
        },
        "batches": [
            {"index": b["index"], "items": b["item_ids"], "canaries": b["canary_ids"]}
            for b in run["batches"]
        ],
        "decision_log": {
            "frozen_before_run": True,
            "decisions": [],
            "note": "dry-run: no tuning decisions; synthetic replies" if run["dry"]
            else "live run; the parent froze all tuning decisions before start",
        },
        "slot_ledger": {
            "topology": "T1 paired: 2 pipelines x (1 speaker + 1 listener)",
            "peak_slots": 4,
            "note": "synthetic children in dry-run; the parent records live occupancy"
            if run["dry"] else "the parent appends actual occupancy per batch",
        },
        "labels": dict(LABELS),
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    if fault_plan:
        manifest["dry_run_fault_plan"] = fault_plan
    write_json(run_path(run, "run-manifest.json"), manifest)
    return manifest


def load_run_manifest(run):
    return json.loads(open(run_path(run, "run-manifest.json"), encoding="utf-8").read())


# ---------------------------------------------------------------- stages

def stage_speaker_plan(run):
    """Write speaker prompts + parent instructions. No model contact."""
    os.makedirs(run_path(run, "prompts"), exist_ok=True)
    rows = []
    for batch in run["batches"]:
        pipeline = (batch["index"] % 2) + 1
        for item_id in batch["item_ids"]:
            item = next(i for i in run["items"] if i["id"] == item_id)
            prompt = build_speaker_prompt(item, run["mode"], codec=run.get("codec"))
            rows.append({
                "item": item_id,
                "batch": batch["index"],
                "pipeline": pipeline,
                "child_hint": "eval-spk-%d" % pipeline if not run["dry"] else "synthetic-speaker-%d" % pipeline,
                "prompt": prompt,
            })
    write_jsonl(run_path(run, "prompts", "speaker.jsonl"), rows)
    if not run["dry"]:
        write_parent_instructions_speaker(run, rows)
    return rows


def write_parent_instructions_speaker(run, rows):
    dialect = run.get("dialect")
    dialect_flag = "" if dialect is None else " --dialect %s" % dialect
    text = [
        "# Live speaker stage - parent instructions",
        "",
        "Run dir: %s" % run["run_dir"],
        "Mode: %s. Split: %s. Batches: %d.%s"
        % (run["mode"], run["split"], len(run["batches"]),
           "" if dialect is None else " Dialect: %s (OCC-2 mode)." % dialect),
        "",
        "1. Spawn two DGX GLM 5.3 speaker children (names eval-spk-1, eval-spk-2),",
        "   one per pipeline column in prompts/speaker.jsonl.",
        "2. For every row, send the full prompt text to that row's child via",
        "   agent_message and request an explicit reply to the parent.",
        "3. Save every reply as ONE line in replies/speaker.jsonl:",
        '   {"child": "eval-spk-1", "item": "<item id>", "reply": "<full reply text>",',
        '    "sent_utc": "...", "received_utc": "...", "latency_ms": 1234}',
        "   The reply must be the raw text the child produced.",
    ]
    if dialect is None:
        text += [
            "   The reply must be the raw OCC/1 wire text the child produced.",
        ]
    else:
        text += [
            "   OCC-2 mode: the expected reply is the dialect codebook, a blank",
            "   line, then the wire (first line %r for sym-v1; 'd' for"
            " sym-v2-d2-r1). A bare wire (codebook omitted) still scores; any"
            " other preamble is a protocol fault." % run["codec"].wire_header_hint(),
        ]
    text += [
        "4. A child that dies or never answers: do NOT invent a reply; leave the",
        "   item absent (the driver accounts it as child_lost).",
        "5. Then run: python research/occ2opt_eval/driver.py --split %s --mode %s"
        " --batch %d%s --live --stage listener --confirm-live"
        % (run["split"], run["mode"], run["batch_size"], dialect_flag),
        "",
        "Slot ledger: 2 speakers now; 2 listeners replace them at the listener stage",
        "(peak 4 of 4 campaign slots during the overlap).",
    ]
    with open(run_path(run, "prompts", "PARENT-INSTRUCTIONS.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(text) + "\n")


def stage_speaker_collect(run):
    """Dry-run: synthesize speaker replies (faults per the plan). Live: read file."""
    if not run["dry"]:
        path = run_path(run, "replies", "speaker.jsonl")
        if not os.path.isfile(path):
            raise SystemExit("live speaker stage: no replies file at %s" % path)
        return read_jsonl(path)
    rng = random.Random(run["seed"] ^ 0x53504541)
    fault_plan = choose_fault_items(run["items"], run["batches"], rng)
    rows = []
    clock = 0
    for batch in run["batches"]:
        pipeline = (batch["index"] % 2) + 1
        for item_id in batch["item_ids"]:
            item = next(i for i in run["items"] if i["id"] == item_id)
            fault = None
            if fault_plan.get("speaker_format") == item_id:
                fault = "format"
            elif fault_plan.get("speaker_status") == item_id:
                fault = "status"
            elif fault_plan.get("speaker_next_drop") == item_id:
                fault = "next_drop"
            reply = synthesize_speaker_reply(item, run["mode"], fault, rng, codec=run.get("codec"))
            latency = rng.randint(1500, 9000)
            rows.append({
                "child": "synthetic-speaker-%d" % pipeline,
                "item": item_id,
                "reply": reply,
                "sent_utc": synth_stamp(clock),
                "received_utc": synth_stamp(clock + latency // 1000),
                "latency_ms": latency,
            })
            clock += 37
    os.makedirs(run_path(run, "replies"), exist_ok=True)
    write_jsonl(run_path(run, "replies", "speaker.jsonl"), rows)
    return rows, fault_plan


def stage_listener_plan(run, speaker_rows):
    """Parse speaker replies; write listener prompts for items with valid wire."""
    codec = run.get("codec")
    prompts = []
    missing = []
    by_item = {row["item"]: row for row in speaker_rows}
    for item in run["items"]:
        row = by_item.get(item["id"])
        if row is None:
            missing.append({"item": item["id"], "reason": "no speaker reply (child lost)"})
            continue
        parsed = parse_speaker_reply(row.get("reply"), codec)
        if codec is not None:
            ok, wire, reason = parsed[0], parsed[1], parsed[2]
        else:
            ok, wire, reason = parsed
        if not ok:
            missing.append({"item": item["id"], "reason": "speaker protocol fault: %s" % reason})
            continue
        prompts.append({"item": item["id"], "wire": wire, "speaker_child": row.get("child")})
    rows = []
    wire_by_item = {}
    if codec is None:
        for prompt in prompts:
            item = next(i for i in run["items"] if i["id"] == prompt["item"])
            text = build_listener_prompt(item, prompt["wire"])
            wire_by_item[item["id"]] = prompt["wire"]
            rows.append({
                "item": item["id"],
                "prompt": text,
                "speaker_child": prompt["speaker_child"],
            })
    else:
        # OCC-2 mode: rows in presentation order (batch by batch, position
        # ascending). The first item of each batch carries the codebook,
        # mirroring the runtime's once-per-session legend at batch
        # granularity; later items rely on the child remembering it.
        codebook_items = []
        for batch in run["batches"]:
            for position, item_id in enumerate(batch["item_ids"]):
                prompt = next((p for p in prompts if p["item"] == item_id), None)
                if prompt is None:
                    continue
                item = next(i for i in run["items"] if i["id"] == item_id)
                with_codebook = position == 0
                text = build_listener_prompt(item, prompt["wire"], codec=codec,
                                             with_codebook=with_codebook)
                wire_by_item[item["id"]] = prompt["wire"]
                rows.append({
                    "item": item["id"],
                    "prompt": text,
                    "speaker_child": prompt["speaker_child"],
                    "batch": batch["index"],
                    "pipeline": (batch["index"] % 2) + 1,
                    "child_hint": "eval-lst-%d" % ((batch["index"] % 2) + 1),
                    "carries_codebook": with_codebook,
                })
                if with_codebook:
                    codebook_items.append(item["id"])
    os.makedirs(run_path(run, "prompts"), exist_ok=True)
    write_jsonl(run_path(run, "prompts", "listener.jsonl"), rows)
    state = {
        "missing": missing,
        "wire_by_item": wire_by_item,
    }
    if codec is not None:
        state["codebook_carrier_items"] = codebook_items
    write_json(run_path(run, "state-speaker.json"), state)
    if not run["dry"]:
        write_parent_instructions_listener(run, rows)
    return rows, missing


def write_parent_instructions_listener(run, rows):
    dialect = run.get("dialect")
    dialect_flag = "" if dialect is None else " --dialect %s" % dialect
    skipped = sum(1 for row in rows if not row.get("carries_codebook", True) and row.get("item") is None)
    text = [
        "# Live listener stage - parent instructions",
        "",
        "Run dir: %s" % run["run_dir"],
        "",
        "1. Spawn two DGX GLM 5.3 listener children (eval-lst-1, eval-lst-2).",
    ]
    if dialect is None:
        text += [
            "2. For every row in prompts/listener.jsonl, send the prompt text and",
            "   request an explicit reply to the parent.",
        ]
    else:
        text += [
            "2. OCC-2 mode: every row carries a child_hint (eval-lst-1 or",
            "   eval-lst-2, by batch parity). Send each row to its child_hint",
            "   child, and send the rows of the same batch to the SAME child in",
            "   file order: the first row of each batch carries the dialect",
            "   codebook (carries_codebook true), the rest rely on the child",
            "   remembering it - that mirrors the runtime's once-per-session",
            "   codebook injection and is exactly what is being measured.",
        ]
    text += [
        "3. Save every reply as ONE line in replies/listener.jsonl:",
        '   {"child": "eval-lst-1", "item": "<item id>", "reply": "<full reply text>",',
        '    "sent_utc": "...", "received_utc": "...", "latency_ms": 1234}',
        "4. Then run: python research/occ2opt_eval/driver.py --split %s --mode %s"
        " --batch %d%s --live --stage score --confirm-live --run-dir %s"
        % (run["split"], run["mode"], run["batch_size"], dialect_flag, run["run_dir"]),
        "",
        "Note: use the venv python for the score stage so wire-token metrics",
        "are recorded (the local Qwen tokenizer); any python works otherwise.",
        "",
        "Note: %d items were skipped at this stage (speaker protocol faults or lost" % skipped,
        "children); see state-speaker.json.",
    ]
    with open(run_path(run, "prompts", "PARENT-INSTRUCTIONS.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(text) + "\n")


def stage_listener_collect(run):
    """Dry-run: synthesize listener replies from the wire. Live: read file."""
    if not run["dry"]:
        path = run_path(run, "replies", "listener.jsonl")
        if not os.path.isfile(path):
            raise SystemExit("live listener stage: no replies file at %s" % path)
        return read_jsonl(path)
    state = json.loads(open(run_path(run, "state-speaker.json"), encoding="utf-8").read())
    rng = random.Random(run["seed"] ^ 0x4C495354)
    fault_plan = choose_fault_items(run["items"], run["batches"],
                                    random.Random(run["seed"] ^ 0x53504541))
    rows = []
    clock = 1000
    for item in run["items"]:
        wire = state["wire_by_item"].get(item["id"])
        if wire is None:
            continue
        fault = None
        if fault_plan.get("listener_qa") == item["id"]:
            fault = "qa_corrupt"
        elif fault_plan.get("listener_comprehension") == item["id"]:
            fault = "payload_drift"
        elif fault_plan.get("listener_format") == item["id"]:
            fault = "format"
        reply = synthesize_listener_reply(item, wire, fault, rng, codec=run.get("codec"))
        latency = rng.randint(1500, 9000)
        rows.append({
            "child": "synthetic-listener-%d" % (((clock // 37) % 2) + 1),
            "item": item["id"],
            "reply": reply,
            "sent_utc": synth_stamp(clock),
            "received_utc": synth_stamp(clock + latency // 1000),
            "latency_ms": latency,
        })
        clock += 41
    write_jsonl(run_path(run, "replies", "listener.jsonl"), rows)
    return rows


# ---------------------------------------------------------------- scoring

def stage_score(run):
    """Score everything; write log.jsonl + summary.json; return (records, summary)."""
    speaker_prompts = {row["item"]: row for row in read_jsonl(run_path(run, "prompts", "speaker.jsonl"))}
    speaker_rows = {row["item"]: row for row in read_jsonl(run_path(run, "replies", "speaker.jsonl"))}
    state = json.loads(open(run_path(run, "state-speaker.json"), encoding="utf-8").read())
    wire_by_item = state["wire_by_item"]
    missing_state = {row["item"]: row["reason"] for row in state["missing"]}
    codebook_carriers = set(state.get("codebook_carrier_items", []))
    listener_prompts = {row["item"]: row for row in read_jsonl(run_path(run, "prompts", "listener.jsonl"))}
    listener_rows = {row["item"]: row for row in read_jsonl(run_path(run, "replies", "listener.jsonl"))}

    codec = run.get("codec")
    records = []
    unaccounted = []
    occ1_wires = {}
    occ1_tokens = {}
    if codec is not None:
        # OCC-1 baseline per item for the wire-token delta columns.
        occ1_ids = [i["id"] for i in run["items"]]
        for item_id in occ1_ids:
            occ1_wires[item_id] = ground_truth_wire(
                next(i for i in run["items"] if i["id"] == item_id)
            )
        occ1_token_counts = count_tokens_texts([occ1_wires[i] for i in occ1_ids])
        if occ1_token_counts is not None:
            occ1_tokens = dict(zip(occ1_ids, occ1_token_counts))
    for item in presentation_order(run["items"], run["batches"]):
        item_id = item["id"]
        batch = next(b for b in run["batches"] if item_id in b["positions"])
        record = {
            "item": item_id,
            "split": run["split"],
            "story": item["story"],
            "scenarios": item["scenarios"],
            "mode": run["mode"],
            "dialect": run.get("dialect"),
            "codebook_ok": None,
            "carries_codebook": item_id in codebook_carriers if codebook_carriers else None,
            "wire_tokens": None,
            "occ1_wire_chars": None,
            "occ1_wire_tokens": None,
            "wire_char_delta": None,
            "wire_token_delta": None,
            "batch": batch["index"],
            "position_in_batch": batch["positions"][item_id],
            "canary": item_id in batch["canary_ids"],
            "speaker_child": None,
            "listener_child": None,
            "speaker_prompt_sha256": None,
            "speaker_prompt_chars": None,
            "speaker_reply_sha256": None,
            "speaker_reply_chars": None,
            "wire_chars": None,
            "speaker_fidelity_ok": None,
            "speaker_fault": None,
            "listener_prompt_sha256": None,
            "listener_prompt_chars": None,
            "listener_reply_sha256": None,
            "listener_reply_chars": None,
            "speaker_latency_ms": None,
            "listener_latency_ms": None,
            "qa": [],
            "qa_correct": None,
            "qa_total": None,
            "comprehension_ok": None,
            "comprehension_detail": None,
            "protocol_fault": False,
            "fault_stage": None,
            "child_lost": False,
            "listener_skipped": False,
            "skip_reason": None,
        }

        sp_prompt = speaker_prompts.get(item_id)
        if sp_prompt is not None:
            record["speaker_prompt_sha256"] = sha256_text(sp_prompt["prompt"])
            record["speaker_prompt_chars"] = len(sp_prompt["prompt"])

        sp_row = speaker_rows.get(item_id)
        if sp_row is None:
            # No speaker reply at all: child lost, unless the state accounts it.
            reason = missing_state.get(item_id)
            if reason is None:
                unaccounted.append(item_id)
            record["child_lost"] = True
            record["skip_reason"] = reason or "no speaker reply and no state entry"
            records.append(record)
            continue
        if item_id not in wire_by_item and item_id not in missing_state:
            unaccounted.append(item_id)
            record["child_lost"] = True
            record["skip_reason"] = "inconsistent state: reply present but no disposition"
            records.append(record)
            continue

        record["speaker_child"] = sp_row.get("child")
        record["speaker_reply_sha256"] = sha256_text(sp_row.get("reply") or "")
        record["speaker_reply_chars"] = len(sp_row.get("reply") or "")
        record["speaker_latency_ms"] = sp_row.get("latency_ms")

        parsed = parse_speaker_reply(sp_row.get("reply"), codec)
        if codec is not None:
            ok, wire, reason, codebook_ok = parsed
            record["codebook_ok"] = codebook_ok
        else:
            ok, wire, reason = parsed
        if not ok:
            record["protocol_fault"] = True
            record["fault_stage"] = "speaker"
            record["speaker_fault"] = reason
            record["listener_skipped"] = True
            record["skip_reason"] = "speaker protocol fault: %s" % reason
            records.append(record)
            continue
        record["wire_chars"] = len(wire)
        if codec is not None:
            record["occ1_wire_chars"] = len(occ1_wires[item_id])
            record["wire_char_delta"] = record["occ1_wire_chars"] - record["wire_chars"]
            if item_id in occ1_tokens:
                record["occ1_wire_tokens"] = occ1_tokens[item_id]
            record["wire_tokens"] = count_tokens_text(wire)
            if record["wire_tokens"] is not None and record["occ1_wire_tokens"] is not None:
                record["wire_token_delta"] = (
                    record["occ1_wire_tokens"] - record["wire_tokens"]
                )
        fidelity, detail, _back = score_speaker_wire(item, wire, codec)
        record["speaker_fidelity_ok"] = fidelity
        record["speaker_fault"] = detail

        ls_prompt = listener_prompts.get(item_id)
        if ls_prompt is None:
            # No valid wire path to a listener prompt (state said missing).
            record["child_lost"] = True
            record["skip_reason"] = missing_state.get(item_id, "no listener prompt")
            records.append(record)
            continue
        record["listener_prompt_sha256"] = sha256_text(ls_prompt["prompt"])
        record["listener_prompt_chars"] = len(ls_prompt["prompt"])

        ls_row = listener_rows.get(item_id)
        if ls_row is None:
            record["child_lost"] = True
            record["skip_reason"] = "no listener reply (child lost)"
            records.append(record)
            continue
        record["listener_child"] = ls_row.get("child")
        record["listener_reply_sha256"] = sha256_text(ls_row.get("reply") or "")
        record["listener_reply_chars"] = len(ls_row.get("reply") or "")
        record["listener_latency_ms"] = ls_row.get("latency_ms")

        answers, payload_text, fenced, ok, reason = parse_listener_reply(ls_row.get("reply"))
        if not ok:
            # Protocol faults are unscored: counted in a separate bucket only.
            record["protocol_fault"] = True
            record["fault_stage"] = "listener"
            record["skip_reason"] = "listener protocol fault: %s" % reason
            records.append(record)
            continue
        qa_rows, qa_correct, comp_ok, comp_detail = score_listener(item, answers, payload_text)
        record["qa"] = qa_rows
        record["qa_correct"] = qa_correct
        record["qa_total"] = len(item["qa"])
        record["comprehension_ok"] = comp_ok
        record["comprehension_detail"] = comp_detail
        records.append(record)

    write_jsonl(run_path(run, "log.jsonl"), records)
    summary = build_summary(run, records, unaccounted)
    write_json(run_path(run, "summary.json"), summary)
    return records, summary


def build_summary(run, records, unaccounted):
    items_total = len(run["items"])
    sp_scored = [r for r in records if r["speaker_fidelity_ok"] is not None]
    sp_faults = [r for r in records if r["fault_stage"] == "speaker"]
    ls_scored = [r for r in records if r["qa_total"] is not None and not r["protocol_fault"]]
    ls_faults = [r for r in records if r["fault_stage"] == "listener"]
    skipped = [r for r in records if r["listener_skipped"]]
    child_lost = [r for r in records if r["child_lost"]]
    qa_total = sum(r["qa_total"] for r in records if r["qa_total"] is not None)
    qa_correct = sum(r["qa_correct"] for r in records if r["qa_correct"] is not None)
    # Both accuracy variants, quoted together: scored answers only, and
    # answers over ALL attempted listener items (protocol faults count zero).
    attempted = [r for r in records if r["listener_prompt_sha256"] is not None]
    attempted_expected = sum(len(next(i for i in run["items"] if i["id"] == r["item"])["qa"])
                             for r in attempted)
    comp_scored = [r for r in records if r["comprehension_ok"] is not None and not r["protocol_fault"]]
    comp_ok = [r for r in comp_scored if r["comprehension_ok"]]

    canary_records = [r for r in records if r["canary"]]
    canary_qa_total = sum(r["qa_total"] for r in canary_records if r["qa_total"] is not None)
    canary_qa_correct = sum(r["qa_correct"] for r in canary_records if r["qa_correct"] is not None)
    per_batch = []
    for batch in run["batches"]:
        rows = [r for r in canary_records if r["batch"] == batch["index"]]
        total = sum(r["qa_total"] for r in rows if r["qa_total"] is not None)
        correct = sum(r["qa_correct"] for r in rows if r["qa_correct"] is not None)
        per_batch.append({
            "batch": batch["index"],
            "canary_items": len(rows),
            "qa_total": total,
            "qa_correct": correct,
            "accuracy": round(correct / total, 4) if total else None,
        })
    accuracies = [row["accuracy"] for row in per_batch if row["accuracy"] is not None]
    drift = False
    if len(accuracies) >= 3:
        for i in range(len(accuracies) - 2):
            if accuracies[i] > accuracies[i + 1] > accuracies[i + 2]:
                drift = True
                break

    def rate(num, den):
        return round(num / den, 4) if den else None

    summary = {
        "run_id": run["run_id"],
        "split": run["split"],
        "mode": run["mode"],
        "dialect": run.get("dialect"),
        "dry_run": run["dry"],
        "items_total": items_total,
        "batches": len(run["batches"]),
        "batch_size": run["batch_size"],
        "speaker": {
            "scored": len(sp_scored),
            "fidelity_ok": sum(1 for r in sp_scored if r["speaker_fidelity_ok"]),
            "protocol_faults": len(sp_faults),
            "fidelity_rate": rate(sum(1 for r in sp_scored if r["speaker_fidelity_ok"]), len(sp_scored)),
        },
        "listener": {
            "skipped_speaker_fault": len(skipped),
            "protocol_faults": len(ls_faults),
            "scored": len(ls_scored),
        },
        "qa": {
            "total": qa_total,
            "correct": qa_correct,
            "accuracy": rate(qa_correct, qa_total),
            "accuracy_incl_protocol_faults": rate(qa_correct, attempted_expected),
        },
        "comprehension": {
            "scored": len(comp_scored),
            "ok": len(comp_ok),
            "rate": rate(len(comp_ok), len(comp_scored)),
        },
        "canaries": {
            "items": len(canary_records),
            "qa_total": canary_qa_total,
            "qa_correct": canary_qa_correct,
            "accuracy": rate(canary_qa_correct, canary_qa_total),
            "per_batch": per_batch,
            "drift_detected": drift,
        },
        "faults": {
            "speaker_protocol": len(sp_faults),
            "speaker_semantic": sum(1 for r in sp_scored if not r["speaker_fidelity_ok"]),
            "listener_protocol": len(ls_faults),
            "child_lost": len(child_lost),
            "unaccounted_items": len(unaccounted),
            "unaccounted_items_list": list(unaccounted),
        },
        "child_loss": {
            "count": len(child_lost),
            "items": [r["item"] for r in child_lost],
        },
        "labels": dict(LABELS),
    }
    codec = run.get("codec")
    if codec is not None:
        with_wire = [r for r in records if r["wire_chars"] is not None]
        with_tokens = [r for r in with_wire if r["wire_tokens"] is not None]
        codebook_emitted = [r for r in records if r["codebook_ok"]]
        codebook = codec.codebook_text()
        codebook_tokens = count_tokens_text(codebook)
        summary["wire_tokens"] = {
            "source": TOKENIZER_SOURCE_LABEL if with_tokens else "unavailable",
            "scored_items": len(with_wire),
            "tokens_measured_items": len(with_tokens),
            "mean_wire_tokens": (
                round(sum(r["wire_tokens"] for r in with_tokens) / len(with_tokens), 4)
                if with_tokens else None
            ),
            "mean_occ1_tokens": (
                round(sum(r["occ1_wire_tokens"] for r in with_tokens) / len(with_tokens), 4)
                if with_tokens else None
            ),
            "mean_token_delta": (
                round(sum(r["wire_token_delta"] for r in with_tokens) / len(with_tokens), 4)
                if with_tokens else None
            ),
            "mean_token_ratio": (
                round(
                    sum(r["wire_tokens"] for r in with_tokens)
                    / sum(r["occ1_wire_tokens"] for r in with_tokens),
                    4,
                )
                if with_tokens else None
            ),
            "mean_wire_chars": (
                round(sum(r["wire_chars"] for r in with_wire) / len(with_wire), 4)
                if with_wire else None
            ),
            "mean_occ1_chars": (
                round(sum(r["occ1_wire_chars"] for r in with_wire) / len(with_wire), 4)
                if with_wire else None
            ),
        }
        summary["codebook_overhead"] = {
            "injections_per_batch": 1,
            "batches": len(run["batches"]),
            "carrier_items": (
                len([r for r in records if r.get("carries_codebook")])
                if any(r.get("carries_codebook") is not None for r in records) else None
            ),
            "codebook_chars": len(codebook),
            "codebook_tokens": codebook_tokens,
            "tokens_source": TOKENIZER_SOURCE_LABEL if codebook_tokens is not None else "unavailable",
            "speaker_codebook_emission_rate": (
                round(len(codebook_emitted) / max(1, len([r for r in records if r["codebook_ok"] is not None])), 4)
            ),
            "amortized_chars_per_item": (
                round(len(codebook) * len(run["batches"]) / max(1, items_total), 4)
            ),
            "amortized_tokens_per_item": (
                round(codebook_tokens * len(run["batches"]) / max(1, items_total), 4)
                if codebook_tokens is not None else None
            ),
        }
    return summary


# ---------------------------------------------------------------- acceptance gates

def apply_gates(run, summary, records, unaccounted, receipts):
    gate = Gate()
    manifest_path = run_path(run, "run-manifest.json")
    gate.check(os.path.isfile(manifest_path), "gate1: run-manifest.json missing")
    manifest = load_run_manifest(run) if os.path.isfile(manifest_path) else {}
    gate.check(manifest.get("corpus", {}).get("sha256") == receipts["splits"][run["split"]]["sha256"],
               "gate1: corpus hash does not match receipts.json")
    gate.check(manifest.get("decision_log", {}).get("frozen_before_run") is True,
               "gate1: decision log not frozen before the run")
    gate.check("slot_ledger" in manifest, "gate1: slot ledger missing")
    labels = manifest.get("labels", {})
    for key in REQUIRED_LABEL_KEYS:
        gate.check(labels.get(key) == LABELS[key], "gate1: manifest label %s missing or wrong" % key)

    gate.check(len(records) == len(run["items"]), "gate2: log record count != items")
    gate.check(not unaccounted, "gate2: %d unaccounted items" % len(unaccounted))
    lost = summary.get("child_loss", {}).get("items", [])
    for record in records:
        if record["child_lost"] and not record["skip_reason"]:
            gate.check(False, "gate2: child loss without a recorded reason (%s)" % record["item"])

    canaries = summary.get("canaries", {})
    gate.check(canaries.get("items", 0) > 0, "gate3: no canary items recorded")
    gate.check("drift_detected" in canaries, "gate3: canary drift analysis missing")
    gate.check(canaries.get("drift_detected") is False, "gate3: canary drift detected")
    if run["dry"]:
        gate.check(canaries.get("accuracy") == 1.0,
                   "gate3: dry-run canary accuracy %r != 1.0" % canaries.get("accuracy"))

    faults = summary.get("faults", {})
    for key in ("speaker_protocol", "speaker_semantic", "listener_protocol", "child_lost"):
        gate.check(key in faults, "gate4: fault bucket %s missing" % key)
    qa = summary.get("qa", {})
    gate.check("accuracy" in qa and "accuracy_incl_protocol_faults" in qa,
               "gate4: both accuracy variants must be quoted together")

    sum_labels = summary.get("labels", {})
    gate.check(sum_labels.get("sizes") == "chars, not provider tokens",
               "gate5: sizes not labeled as chars")
    gate.check(sum_labels.get("heterogeneous_model_pairs") == LABELS["heterogeneous_model_pairs"],
               "gate5: heterogeneous pairs not labeled deferred")
    gate.check(sum_labels.get("channel") == LABELS["channel"], "gate5: channel label missing")
    gate.check(sum_labels.get("matrix_cell") == LABELS["matrix_cell"], "gate5: matrix cell label missing")
    return gate


def gate_report(gate):
    return {
        "checks": gate.total,
        "failures": gate.failures,
        "passed": not gate.failures,
        "gate_names": ["manifest_complete", "child_loss_accounting", "canary_drift",
                        "fault_rate_reporting", "labeling_rules"],
    }


# ---------------------------------------------------------------- orchestration

def dry_run(split, mode, batch_size, limit=None, run_dir=None, dialect=None):
    """Full pipeline with synthetic replies. Exercises every stage offline."""
    run = new_run(split, mode, batch_size, dry=True, limit=limit, run_dir=run_dir,
                  dialect=dialect)
    if os.path.isdir(run["run_dir"]):
        shutil.rmtree(run["run_dir"])
    os.makedirs(run["run_dir"], exist_ok=True)
    fault_plan = choose_fault_items(run["items"], run["batches"],
                                    random.Random(run["seed"] ^ 0x53504541))
    write_run_manifest(run, fault_plan)
    stage_speaker_plan(run)
    speaker_rows, _plan = stage_speaker_collect(run)
    stage_listener_plan(run, speaker_rows)
    stage_listener_collect(run)
    records, summary = stage_score(run)
    gate = apply_gates(run, summary, records, [], run["receipts"])
    summary["gates"] = gate_report(gate)
    write_json(run_path(run, "summary.json"), summary)
    print("dry-run complete: %s" % run["run_dir"])
    print("  items=%d batches=%d speaker(scored=%d fidelity_ok=%d proto_faults=%d)"
          % (summary["items_total"], summary["batches"], summary["speaker"]["scored"],
             summary["speaker"]["fidelity_ok"], summary["speaker"]["protocol_faults"]))
    print("  listener(scored=%d skipped=%d proto_faults=%d) qa(%d/%d acc=%s incl=%s)"
          % (summary["listener"]["scored"], summary["listener"]["skipped_speaker_fault"],
             summary["listener"]["protocol_faults"], summary["qa"]["correct"], summary["qa"]["total"],
             summary["qa"]["accuracy"], summary["qa"]["accuracy_incl_protocol_faults"]))
    print("  comprehension(%d/%d) canaries(%d items, acc=%s, drift=%s) child_lost=%d"
          % (summary["comprehension"]["ok"], summary["comprehension"]["scored"],
             summary["canaries"]["items"], summary["canaries"]["accuracy"],
             summary["canaries"]["drift_detected"], summary["faults"]["child_lost"]))
    print("  gates: %s (%d checks, %d failures)"
          % ("GREEN" if not gate.failures else "RED", gate.total, len(gate.failures)))
    return (not gate.failures), run


def load_run(run_dir, dialect=None):
    """Rebuild a run dict from an existing run directory (live stages 2-3)."""
    manifest_path = os.path.join(run_dir, "run-manifest.json")
    if not os.path.isfile(manifest_path):
        raise SystemExit("no run-manifest.json in %s (run the speaker stage first)" % run_dir)
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    manifest_dialect = manifest.get("dialect")
    if dialect is not None and manifest_dialect is not None and dialect != manifest_dialect:
        raise SystemExit(
            "--dialect %s contradicts the run manifest dialect %s" % (dialect, manifest_dialect)
        )
    run = new_run(manifest["split"], manifest["mode"], manifest["batch_size"],
                  dry=False, limit=manifest.get("limit"), run_dir=run_dir,
                  dialect=manifest_dialect)
    run["run_id"] = manifest["run_id"]
    if "seed" in manifest:
        run["seed"] = manifest["seed"]
        run["batches"] = plan_batches(run["items"], manifest["batch_size"], manifest["seed"])
    return run


def live_speaker(args):
    run = new_run(args.split, args.mode, args.batch, dry=False, run_dir=args.run_dir,
                  dialect=args.dialect)
    if os.path.isfile(run_path(run, "run-manifest.json")):
        raise SystemExit("run dir already has a manifest; pass --run-dir of that run for later stages")
    os.makedirs(run["run_dir"], exist_ok=True)
    write_run_manifest(run)
    stage_speaker_plan(run)
    print("live speaker prompts written: %s" % run_path(run, "prompts", "speaker.jsonl"))
    print("parent instructions: %s" % run_path(run, "prompts", "PARENT-INSTRUCTIONS.md"))
    dialect_flag = "" if not args.dialect else " --dialect %s" % args.dialect
    print("next: drive the children, collect replies/speaker.jsonl, then")
    print("  python research/occ2opt_eval/driver.py --split %s --mode %s --batch %d%s"
          " --live --stage listener --confirm-live --run-dir %s"
          % (args.split, args.mode, args.batch, dialect_flag, run["run_dir"]))
    return 0


def live_listener(args):
    if not args.run_dir:
        raise SystemExit("--run-dir is required for --stage listener")
    run = load_run(args.run_dir, dialect=args.dialect)
    path = run_path(run, "replies", "speaker.jsonl")
    if not os.path.isfile(path):
        raise SystemExit("missing %s (collect speaker replies first)" % path)
    speaker_rows = read_jsonl(path)
    rows, missing = stage_listener_plan(run, speaker_rows)
    print("live listener prompts written: %d items (%d skipped: speaker faults/losses)"
          % (len(rows), len(missing)))
    dialect_flag = "" if not run.get("dialect") else " --dialect %s" % run["dialect"]
    print("next: drive the children, collect replies/listener.jsonl, then")
    print("  python research/occ2opt_eval/driver.py --split %s --mode %s --batch %d%s"
          " --live --stage score --confirm-live --run-dir %s"
          % (run["split"], run["mode"], run["batch_size"], dialect_flag, run["run_dir"]))
    return 0


def live_score(args):
    if not args.run_dir:
        raise SystemExit("--run-dir is required for --stage score")
    run = load_run(args.run_dir, dialect=args.dialect)
    for name in ("replies", "speaker.jsonl"), ("replies", "listener.jsonl"):
        if not os.path.isfile(run_path(run, *name)):
            raise SystemExit("missing %s" % run_path(run, *name))
    records, summary = stage_score(run)
    unaccounted = summary["faults"]["unaccounted_items_list"]
    gate = apply_gates(run, summary, records, unaccounted, run["receipts"])
    summary["gates"] = gate_report(gate)
    write_json(run_path(run, "summary.json"), summary)
    print("scored %d items; gates %s (%d failures)"
          % (len(records), "GREEN" if not gate.failures else "RED", len(gate.failures)))
    for failure in gate.failures:
        print("  GATE FAILURE: %s" % failure)
    return 0 if not gate.failures else 1


# ---------------------------------------------------------------- self-test

def _expected_summary():
    return {
        "items_total": 60, "batches": 6,
        "speaker": {"scored": 59, "fidelity_ok": 57, "protocol_faults": 1, "fidelity_rate": 0.9661},
        "listener": {"skipped_speaker_fault": 1, "protocol_faults": 1, "scored": 58},
        "qa": {"total": 174, "correct": 172, "accuracy": 0.9885, "accuracy_incl_protocol_faults": 0.9718},
        "comprehension": {"scored": 58, "ok": 55, "rate": 0.9483},
        "canaries": {"items": 12, "accuracy": 1.0, "drift_detected": False},
        "faults": {"speaker_protocol": 1, "speaker_semantic": 2, "listener_protocol": 1,
                    "child_lost": 0, "unaccounted_items": 0},
    }


def _assert_summary(led, summary, mode):
    expected = _expected_summary()
    for key, want in expected.items():
        got = summary.get(key)
        if isinstance(want, dict):
            for sub, subwant in want.items():
                led.check(got.get(sub) == subwant,
                          "%s.%s: got %r want %r" % (key, sub, got.get(sub), subwant))
        else:
            led.check(got == want, "%s: got %r want %r" % (key, got, want))
    led.check(summary["mode"] == mode, "mode label wrong")


def self_test():
    """Full offline gate: dry-runs, exact fault detection, determinism, refusals."""
    led = Gate()
    runs_dir = os.path.join(_HERE, "runs")

    # 1-2: scripted + free dry-runs on the dev split, exact expected numbers.
    for mode in MODES:
        ok, run = dry_run("dev", mode, 10, run_dir=os.path.join(runs_dir, "selftest-%s" % mode))
        led.check(ok, "dry-run %s gates not green" % mode)
        summary = json.loads(open(run_path(run, "summary.json"), encoding="utf-8").read())
        _assert_summary(led, summary, mode)
        led.check(summary["gates"]["passed"] is True, "%s: gate report not passed" % mode)

    # 3: determinism - a second scripted run must be byte-identical.
    ok2, run2 = dry_run("dev", "scripted", 10, run_dir=os.path.join(runs_dir, "selftest-scripted-again"))
    led.check(ok2, "determinism re-run gates not green")
    a_dir = os.path.join(runs_dir, "selftest-scripted")
    for name in ("log.jsonl", "summary.json"):
        led.check(open(os.path.join(a_dir, name), "rb").read()
                  == open(os.path.join(run2["run_dir"], name), "rb").read(),
                  "determinism: %s differs between runs" % name)

    # 4: live-mode refusal guards (subprocess, env scrubbed).
    env_clean = {k: v for k, v in os.environ.items() if k != LIVE_ENV}
    base = [sys.executable, os.path.abspath(__file__), "--split", "dev", "--mode", "scripted",
            "--batch", "10", "--live"]
    r1 = subprocess.run(base, capture_output=True, text=True, env=env_clean)
    led.check(r1.returncode != 0 and "--confirm-live" in (r1.stderr + r1.stdout),
              "live without --confirm-live must refuse")
    r2 = subprocess.run(base + ["--confirm-live"], capture_output=True, text=True, env=env_clean)
    led.check(r2.returncode != 0 and LIVE_ENV in (r2.stderr + r2.stdout),
              "live without the env var must refuse")
    env_live = dict(env_clean)
    env_live[LIVE_ENV] = "1"
    guard_dir = os.path.join(runs_dir, "selftest-live-guard")
    r3 = subprocess.run(base + ["--confirm-live", "--stage", "speaker", "--run-dir", guard_dir],
                        capture_output=True, text=True, env=env_live)
    led.check(r3.returncode == 0, "live speaker planning must succeed with both guards")
    led.check(not os.path.isdir(os.path.join(guard_dir, "replies")),
              "live planning must not fabricate replies")
    shutil.rmtree(guard_dir, ignore_errors=True)

    # 5: negative control NC1 - tampered corpus hash must fail gate 1.
    nc1 = os.path.join(runs_dir, "selftest-nc1")
    shutil.rmtree(nc1, ignore_errors=True)
    shutil.copytree(a_dir, nc1)
    manifest = json.loads(open(os.path.join(nc1, "run-manifest.json"), encoding="utf-8").read())
    manifest["corpus"]["sha256"] = "0" * 64
    write_json(os.path.join(nc1, "run-manifest.json"), manifest)
    run_nc1 = load_run(nc1)
    records_nc1, summary_nc1 = stage_score(run_nc1)
    gate_nc1 = apply_gates(run_nc1, summary_nc1, records_nc1, [], run_nc1["receipts"])
    led.check(any("gate1" in f for f in gate_nc1.failures),
              "NC1: tampered corpus hash must fail gate 1")

    # 6: negative control NC2 - an unaccounted item must fail gate 2.
    nc2 = os.path.join(runs_dir, "selftest-nc2")
    shutil.rmtree(nc2, ignore_errors=True)
    shutil.copytree(a_dir, nc2)
    replies = read_jsonl(os.path.join(nc2, "replies", "speaker.jsonl"))
    victim = replies[3]["item"]
    write_jsonl(os.path.join(nc2, "replies", "speaker.jsonl"), [r for r in replies if r["item"] != victim])
    state = json.loads(open(os.path.join(nc2, "state-speaker.json"), encoding="utf-8").read())
    state["wire_by_item"].pop(victim, None)
    state["missing"] = [row for row in state["missing"] if row["item"] != victim]
    write_json(os.path.join(nc2, "state-speaker.json"), state)
    run_nc2 = load_run(nc2)
    records_nc2, summary_nc2 = stage_score(run_nc2)
    unaccounted = summary_nc2["faults"]["unaccounted_items_list"]
    gate_nc2 = apply_gates(run_nc2, summary_nc2, records_nc2, unaccounted, run_nc2["receipts"])
    led.check(any("gate2" in f for f in gate_nc2.failures),
              "NC2: unaccounted item must fail gate 2")
    led.check(victim in unaccounted, "NC2: victim item not flagged unaccounted")

    # 7: no network/http symbols in the driver + codec sources.
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    codec_src_path = os.path.join(_HERE, "occ2_codec.py")
    codec_src = open(codec_src_path, encoding="utf-8").read() if os.path.isfile(codec_src_path) else ""
    # Composed by concatenation so the driver source never literally contains
    # the banned import names (the self-check must not match itself).
    banned = ["import re" + "quests", "import ht" + "tp", "url" + "lib", "sock" + "et",
              "http" + "x", "import soc" + "ket", "import url" + "lib"]
    for token in banned:
        led.check(token not in src, "driver source must not reference network APIs")
        led.check(token not in codec_src, "codec source must not reference network APIs")

    # 8: OCC-2 dialect dry-runs - both dialects green with the exact same
    # designed fault numbers as the OCC-1 protocol mode.
    for dialect in DIALECTS:
        run_dir = os.path.join(runs_dir, "selftest-occ2-%s" % dialect)
        ok_d, run_d = dry_run("dev", "scripted", 10, run_dir=run_dir, dialect=dialect)
        led.check(ok_d, "OCC-2 dry-run %s gates not green" % dialect)
        summary = json.loads(open(run_path(run_d, "summary.json"), encoding="utf-8").read())
        _assert_summary(led, summary, "scripted")
        led.check(summary["dialect"] == dialect, "%s: dialect label wrong" % dialect)
        led.check(summary["gates"]["passed"] is True, "%s: gate report not passed" % dialect)
        overhead = summary.get("codebook_overhead") or {}
        led.check(overhead.get("injections_per_batch") == 1,
                  "%s: codebook overhead block missing" % dialect)
        led.check(overhead.get("carrier_items") == summary["batches"],
                  "%s: one codebook carrier per batch expected" % dialect)
        led.check(overhead.get("codebook_chars", 0) > 0,
                  "%s: codebook chars not measured" % dialect)
        led.check(overhead.get("speaker_codebook_emission_rate") == 1.0,
                  "%s: synthetic speakers must emit the codebook" % dialect)
        wire_tokens = summary.get("wire_tokens") or {}
        led.check(wire_tokens.get("scored_items") == 59,
                  "%s: wire tokens scored items wrong" % dialect)
        if wire_tokens.get("source") != "unavailable":
            led.check(wire_tokens.get("mean_wire_tokens", 0) > 0,
                      "%s: mean wire tokens not measured" % dialect)
            led.check(wire_tokens.get("mean_occ1_tokens", 0) > 0,
                      "%s: mean occ1 tokens not measured" % dialect)
        # listener rows: presentation order, one carrier per batch, child hints
        lrows = read_jsonl(run_path(run_d, "prompts", "listener.jsonl"))
        carriers = [r for r in lrows if r.get("carries_codebook")]
        led.check(len(carriers) == summary["batches"],
                  "%s: listener codebook carriers != batches" % dialect)
        led.check(all("child_hint" in r for r in lrows),
                  "%s: listener rows must carry child hints" % dialect)
        first = lrows[0]["prompt"]
        led.check("Codebook:" in first and lrows[0]["carries_codebook"],
                  "%s: first listener prompt must carry the codebook" % dialect)
        non_carrier = next(r for r in lrows if not r.get("carries_codebook"))
        led.check("Codebook:" not in non_carrier["prompt"],
                  "%s: later prompts must not repeat the codebook" % dialect)

    # 9: OCC-2 codec unit checks - hash pins, round-trip on the whole dev
    # split, speaker-output parsing (codebook / bare / commentary / fenced),
    # and the card-text clarifications from the live-eval findings
    # (line order, never-compress policy, bare-index evidence links).
    v1c = occ2_codec.get_codec("sym-v1")
    led.check(v1c.codebook_sha_prefix() == occ2_codec.SYM_V1_CODEBOOK_SHA256[:12],
              "sym-v1 codebook prefix must match the Rust pin")
    led.check(v1c.codebook_text().startswith("OCC-2 sym-v1@1 codebook (sha256:1efce9992ee2):"),
              "sym-v1 legend rendering drifted")
    v2c = occ2_codec.get_codec("sym-v2-d2-r1")
    _dev_items, _sha, _receipts = load_split("dev")
    for codec, name in ((v1c, "sym-v1"), (v2c, "sym-v2-d2-r1")):
        round_ok = 0
        for entry in _dev_items:
            message = SYM.AgentCommMessage.from_payload(entry["payload"])
            wire = codec.encode(message)
            back = codec.decode(wire)
            if SYM.equal(message, back) and codec.encode(back) == wire:
                round_ok += 1
        led.check(round_ok == len(_dev_items),
                  "%s: codec round-trip %d/%d" % (name, round_ok, len(_dev_items)))
    sample = SYM.AgentCommMessage.from_payload(_dev_items[0]["payload"])
    for codec, name in ((v1c, "sym-v1"), (v2c, "sym-v2-d2-r1")):
        wire = codec.encode(sample)
        ok_a, wire_a, _r, cb = codec.parse_speaker_output(codec.codebook_text() + "\n\n" + wire)
        led.check(ok_a and cb is True, "%s: codebook+wire must parse" % name)
        ok_b, wire_b, _r, cb_b = codec.parse_speaker_output(wire)
        led.check(ok_b and cb_b is False, "%s: bare wire must parse (codebook omitted)" % name)
        ok_c, _w, reason_c, _cb = codec.parse_speaker_output("commentary\n" + wire)
        led.check(not ok_c and "preamble" in reason_c, "%s: commentary must fault" % name)
        ok_d, _w, reason_d, _cb = codec.parse_speaker_output("```\n" + wire + "\n```")
        led.check(not ok_d and reason_d == "fenced reply", "%s: fenced must fault" % name)

    # 9b: card-text clarifications (CARD-CLARIFY.md). The codebook must
    # state the canonical line order, the precise never-compress policy,
    # and the bare-index evidence-link format.
    v2_book = v2c.codebook_text()
    led.check("line order:" in v2_book and "emit the family lines first" in v2_book,
              "v2 codebook must state the canonical line order")
    led.check("the symbol alphabet still applies" in v2_book
              and "TS/EXP still travel epoch36" in v2_book,
              "v2 codebook must state the never-compress policy precisely")
    led.check("bare 1-based indices" in v2_book and "never v1=e1,e2" in v2_book,
              "v2 codebook must state the bare-index evidence-link format")
    led.check("travel verbatim" not in v2_book and "no recoding" not in v2_book,
              "v2 codebook must drop the ambiguous verbatim phrasing")

    # 10: OCC-2 determinism - a repeat dialect run must be byte-identical.
    ok_r, run_r = dry_run("dev", "scripted", 10,
                          run_dir=os.path.join(runs_dir, "selftest-occ2-sym-v1-again"),
                          dialect="sym-v1")
    led.check(ok_r, "OCC-2 determinism re-run gates not green")
    d_dir = os.path.join(runs_dir, "selftest-occ2-sym-v1")
    for name in ("log.jsonl", "summary.json"):
        led.check(open(os.path.join(d_dir, name), "rb").read()
                  == open(os.path.join(run_r["run_dir"], name), "rb").read(),
                  "OCC-2 determinism: %s differs between runs" % name)

    # 11: degraded tokenizer - token metrics vanish, gates stay green.
    # Works on a throwaway copy so the real run artifacts are not clobbered.
    import shutil as _shutil
    deg_dir = os.path.join(runs_dir, "selftest-occ2-degraded")
    if os.path.isdir(deg_dir):
        _shutil.rmtree(deg_dir)
    _shutil.copytree(d_dir, deg_dir)
    saved_loader = globals()["_load_tokenizer"]
    try:
        globals()["_TOKENIZER"] = None
        globals()["_TOKENIZER_FAILED"] = True
        _run_deg = load_run(deg_dir)
        _records_deg, _summary_deg = stage_score(_run_deg)
        _gate_deg = apply_gates(_run_deg, _summary_deg, _records_deg, [], _run_deg["receipts"])
        led.check(not _gate_deg.failures, "degraded tokenizer must not fail gates")
        led.check(_summary_deg["wire_tokens"]["source"] == "unavailable",
                  "degraded tokenizer must report unavailable")
        led.check(_summary_deg["codebook_overhead"]["codebook_tokens"] is None,
                  "degraded tokenizer must null codebook tokens")
    finally:
        globals()["_load_tokenizer"] = saved_loader
        globals()["_TOKENIZER"] = None
        globals()["_TOKENIZER_FAILED"] = False
        _shutil.rmtree(deg_dir, ignore_errors=True)

    if led.failures:
        for failure in led.failures:
            sys.stderr.write("SELF-TEST FAILURE %s\n" % failure)
        sys.stderr.write("self-test: %d/%d checks failed\n" % (len(led.failures), led.total))
        return 1
    print("self-test: %d/%d checks passed" % (led.total, led.total))
    print("dry-run artifacts: %s" % a_dir)
    return 0


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="DGX-only OCC eval driver (bookkeeping engine)")
    parser.add_argument("--split", choices=SPLITS)
    parser.add_argument("--mode", choices=MODES, default="scripted")
    parser.add_argument("--dialect", choices=DIALECTS, default=None,
                        help="OCC-2 mode: encode/decode with this dialect "
                             "(default: the OCC-1 protocol of the prior runs)")
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--stage", choices=STAGES, default="speaker")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if bool(args.dry_run) == bool(args.live):
        parser.error("exactly one of --dry-run or --live is required")
    if args.split is None:
        parser.error("--split is required")
    if args.batch < 1:
        parser.error("--batch must be >= 1")

    if args.live:
        if not args.confirm_live:
            sys.stderr.write("REFUSED: --live requires --confirm-live (nothing model-touching"
                             " may happen by accident)\n")
            return 2
        if os.environ.get(LIVE_ENV) != "1":
            sys.stderr.write("REFUSED: --live requires the environment variable %s=1\n" % LIVE_ENV)
            return 2

    if args.dry_run:
        ok, _run = dry_run(args.split, args.mode, args.batch, limit=args.limit,
                           run_dir=args.run_dir, dialect=args.dialect)
        return 0 if ok else 1

    handlers = {"speaker": live_speaker, "listener": live_listener, "score": live_score}
    return handlers[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
