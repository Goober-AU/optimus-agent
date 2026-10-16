"""Deterministic offline cost analysis of the OCC-2 sym-v1 codebook overhead.

Mirrors the runtime code exactly:
- crates/pi-coding-agent/src/core/agent_messages.rs :: agent_session_message_prompt_body()
  renders the codebook line + "\n\n" + symbolic section for every decoded OCC-2 message.
- crates/pi-coding-agent/src/core/occ/dialect.rs :: DialectCard::sym_v1() symbol table
  (BTreeMap iteration = alphabetical by canonical name).
- crates/pi-coding-agent/src/core/occ/occ2.rs :: encode_symbolic() / encode_dual().

No model calls, no network. Tokenizer: local HF cache only.
"""
import json, hashlib, pathlib, statistics, collections

WT = pathlib.Path(__file__).resolve().parents[2]
CORPUS = WT / "research" / "tasks" / "msgs_corpus.json"
MODELS = pathlib.Path(r"C:/Users/openclawuser/optimus-occ23-models/hub")

# ---------------------------------------------------------------- codebook
# agent_messages.rs: slots rendered "{symbol}:{name}", subfields "{name}:{symbol}",
# joined ", ". BTreeMap => alphabetical by key.
SLOTS = {  # canonical name -> symbol (dialect.rs sym_v1)
    "artifact": "G", "dependency": "D", "evidence.ref": "E", "expiry": "X",
    "finding.statement": "F", "kind": "K", "message_id": "M", "next_action": "N",
    "parent_task_id": "P", "recipient": "B", "sender": "A", "sequence": "Q",
    "status": "S", "task_id": "T", "timestamp": "C", "trace_id": "R",
    "verification.claim": "V",
}
SUBFIELDS = {  # canonical name -> marker
    "evidence.digest": "d", "evidence.line_range": "l", "evidence.revision": "r",
    "evidence.verified": "v", "finding.confidence": "c", "finding.evidence": "e",
    "verification.method": "m", "verification.receipt": "r", "verification.status": "s",
}
SYMBOL = {v: k for k, v in SLOTS.items()}  # symbol -> canonical name
MARKER = {v: k for k, v in SUBFIELDS.items()}

canonical = json.dumps(
    {"grammar": {"operator": "=", "list_separator": ","},
     "symbol_table": {"slots": dict(sorted(SLOTS.items())),
                      "subfields": dict(sorted(SUBFIELDS.items()))}},
    separators=(",", ":"))
CODEBOOK_SHA256 = hashlib.sha256(canonical.encode()).hexdigest()
HASH_PREFIX = CODEBOOK_SHA256[:12]  # CODEBOOK_PREFIX_LEN = 12

slots_str = ", ".join(f"{sym}:{name}" for name, sym in sorted(SLOTS.items()))
subfields_str = ", ".join(f"{name}:{sym}" for name, sym in sorted(SUBFIELDS.items()))
# Exact format!() string from agent_session_message_prompt_body().
CODEBOOK_LINE = (
    f"OCC-2 sym-v1 codebook: {slots_str}. Indexed fields use 1-based indices; "
    f"suffixes: {subfields_str}. Values follow '='; evidence references are "
    f"comma-separated. Reply using OCC-1; transport handles encoding."
)
OCC2_HEADER = f"OCC/2 D=sym-v1@1 C={HASH_PREFIX}"
FALLBACK_SENTINEL = "#FALLBACK-OCC1"

# ---------------------------------------------------------------- encoders
def sym_v1_symbolic(p):
    """Mirror occ2.rs encode_symbolic(): field order mirrors the OCC-1 encoder."""
    out = OCC2_HEADER
    def field(key, value):
        nonlocal out
        out += "\n" + key + "=" + str(value)
    if p.get("message_id") is not None: field("M", p["message_id"])
    field("T", p["task_id"])
    if p.get("parent_task_id") is not None: field("P", p["parent_task_id"])
    if p.get("trace_id") is not None: field("R", p["trace_id"])
    if p.get("sender_identity") is not None: field("A", p["sender_identity"])
    if p.get("recipient_identity") is not None: field("B", p["recipient_identity"])
    field("K", p["kind"])
    field("S", p["status"])
    if p.get("sequence") is not None: field("Q", p["sequence"])
    for n, f in enumerate(p.get("findings") or [], 1):
        field(f"F{n}", f["statement"])
        if f.get("confidence") is not None:
            field(f"F{n}c", f"{f['confidence']:.3f}")
        if f.get("evidence"):
            refs = ",".join(str(int(r[1:])) for r in f["evidence"])  # E1 -> 1
            field(f"F{n}e", refs)
    for n, ev in enumerate(p.get("evidence") or [], 1):
        field(f"E{n}", f"{ev['kind']}:{ev['locator']}")
        if ev.get("line_range") is not None:
            lr = ev["line_range"]; field(f"E{n}l", f"{lr[0]}-{lr[1]}")
        if ev.get("digest") is not None: field(f"E{n}d", ev["digest"])
        if ev.get("revision") is not None: field(f"E{n}r", ev["revision"])
        if ev.get("verified") is not None:
            field(f"E{n}v", "1" if ev["verified"] else "0")
    for n, art in enumerate(p.get("artifacts") or [], 1):
        field(f"G{n}", art)
    v = p.get("verification")
    if v:
        field("V", v["claim"]); field("Vs", v["status"]); field("Vm", v["method"])
        if v.get("receipt") is not None: field("Vr", v["receipt"])
    for n, dep in enumerate(p.get("dependencies") or [], 1):
        field(f"D{n}", dep)
    for action in p.get("next_actions") or []:
        field("N", action)
    if p.get("timestamp") is not None: field("C", p["timestamp"])
    if p.get("expiry") is not None: field("X", p["expiry"])
    return out

def occ1_encode(p):
    """OCC-1 wire text. Key set/field order per dialect.py FIXED_KEYS/FAMILY_KEYS
    and occ2.rs 'field order mirrors the OCC-1 contract encoder exactly'.
    EV renders pass/fail (dialect.py ENUM_DOMAINS EVIDENCE_VERIFIED)."""
    out = "OCC/1"
    def field(key, value):
        nonlocal out
        out += "\n" + key + "=" + str(value)
    if p.get("message_id") is not None: field("MSGID", p["message_id"])
    field("TASK", p["task_id"])
    if p.get("parent_task_id") is not None: field("PARENT", p["parent_task_id"])
    if p.get("trace_id") is not None: field("TRACE", p["trace_id"])
    if p.get("sender_identity") is not None: field("SENDER", p["sender_identity"])
    if p.get("recipient_identity") is not None: field("RECIPIENT", p["recipient_identity"])
    field("KIND", p["kind"])
    field("STATUS", p["status"])
    if p.get("sequence") is not None: field("SEQ", p["sequence"])
    for n, f in enumerate(p.get("findings") or [], 1):
        field(f"F{n}", f["statement"])
        if f.get("confidence") is not None:
            field(f"FC{n}", f"{f['confidence']:.3f}")
        if f.get("evidence"):
            field(f"FE{n}", ",".join(f["evidence"]))
    for n, ev in enumerate(p.get("evidence") or [], 1):
        field(f"E{n}", f"{ev['kind']}:{ev['locator']}")
        if ev.get("line_range") is not None:
            lr = ev["line_range"]; field(f"EL{n}", f"{lr[0]}-{lr[1]}")
        if ev.get("digest") is not None: field(f"ED{n}", ev["digest"])
        if ev.get("revision") is not None: field(f"ER{n}", ev["revision"])
        if ev.get("verified") is not None:
            field(f"EV{n}", "pass" if ev["verified"] else "fail")
    for n, art in enumerate(p.get("artifacts") or [], 1):
        field(f"ART{n}", art)
    v = p.get("verification")
    if v:
        field("V", v["claim"]); field("VS", v["status"]); field("VM", v["method"])
        if v.get("receipt") is not None: field("VR", v["receipt"])
    for n, dep in enumerate(p.get("dependencies") or [], 1):
        field(f"DEP{n}", dep)
    for action in p.get("next_actions") or []:
        field("NEXT", action)
    if p.get("timestamp") is not None: field("TS", p["timestamp"])
    if p.get("expiry") is not None: field("EXP", p["expiry"])
    return out

def dual_encode(p):
    """Mirror occ2.rs encode_dual(): symbolic + sentinel + OCC-1."""
    return sym_v1_symbolic(p) + "\n" + FALLBACK_SENTINEL + "\n" + occ1_encode(p)

def prompt_body(p):
    """What the listener model sees per message today:
    codebook + blank line + symbolic (agent_session_message_prompt_body)."""
    return CODEBOOK_LINE + "\n\n" + sym_v1_symbolic(p)

# ---------------------------------------------------------------- tokenizer
from transformers import AutoTokenizer
QWEN = str(next((MODELS / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots").glob("*")))
SMOL = str(next((MODELS / "models--HuggingFaceTB--SmolLM2-135M-Instruct" / "snapshots").glob("*")))
tok_qwen = AutoTokenizer.from_pretrained(QWEN, local_files_only=True)
tok_smol = AutoTokenizer.from_pretrained(SMOL, local_files_only=True)

def ntok(text, tok=tok_qwen):
    return len(tok(text, add_special_tokens=False)["input_ids"])

# ---------------------------------------------------------------- corpus
raw = json.loads(CORPUS.read_text(encoding="utf-8"))
rows = []
for e in raw:
    p = e["payload"]
    sym = sym_v1_symbolic(p)
    dual = dual_encode(p)
    occ1 = occ1_encode(p)
    plain = e.get("plain_english", "")
    rows.append({
        "id": e["id"], "kind": p["kind"], "holdout": bool(e.get("holdout", False)),
        "sym_chars": len(sym), "sym_tok": ntok(sym),
        "body_chars": len(CODEBOOK_LINE) + 2 + len(sym),
        "body_tok": ntok(CODEBOOK_LINE + "\n\n" + sym),
        "occ1_chars": len(occ1), "occ1_tok": ntok(occ1),
        "plain_chars": len(plain), "plain_tok": ntok(plain),
        "dual_chars": len(dual), "dual_tok": ntok(dual),
        "plain_text": plain, "sym_text": sym,
    })

codebook = {
    "line": CODEBOOK_LINE,
    "chars": len(CODEBOOK_LINE),
    "tok_qwen": ntok(CODEBOOK_LINE),
    "tok_smol": ntok(CODEBOOK_LINE, tok_smol),
    "sha256": CODEBOOK_SHA256,
    "wire_header": OCC2_HEADER,
}

# stratified sample: 3 per kind by id (deterministic), all 9 kinds
by_kind = collections.defaultdict(list)
for r in rows: by_kind[r["kind"]].append(r)
sample = sorted([r for k in sorted(by_kind) for r in sorted(by_kind[k], key=lambda x: x["id"])[:3]],
                key=lambda r: r["id"])

def agg(rs, key):
    vals = [r[key] for r in rs]
    return {"mean": statistics.mean(vals), "median": statistics.median(vals),
            "min": min(vals), "max": max(vals), "n": len(vals)}

summary = {}
for key in ["sym_chars","sym_tok","body_chars","body_tok","occ1_chars","occ1_tok",
            "plain_chars","plain_tok","dual_chars","dual_tok"]:
    summary[key] = {"all69": agg(rows, key), "sample27": agg(sample, key)}
summary["per_kind_sym_tok"] = {k: agg(v, "sym_tok") for k, v in sorted(by_kind.items())}
summary["per_kind_occ1_tok"] = {k: agg(v, "occ1_tok") for k, v in sorted(by_kind.items())}
summary["per_kind_plain_tok"] = {k: agg(v, "plain_tok") for k, v in sorted(by_kind.items())}
summary["codebook"] = codebook
summary["dual_factor_chars"] = agg(rows, "dual_chars")["mean"] / agg(rows, "sym_chars")["mean"]
summary["dual_factor_tok"] = agg(rows, "dual_tok")["mean"] / agg(rows, "sym_tok")["mean"]
summary["dual_ratios"] = [{"id": r["id"], "kind": r["kind"],
                           "chars": round(r["dual_chars"]/r["sym_chars"], 3),
                           "tok": round(r["dual_tok"]/r["sym_tok"], 3)} for r in rows]

# ---------------------------------------------------------------- amortization
m_sym = agg(rows, "sym_tok")["mean"]
m_occ1 = agg(rows, "occ1_tok")["mean"]
m_plain = agg(rows, "plain_tok")["mean"]
m_body = agg(rows, "body_tok")["mean"]
cb_tok = codebook["tok_qwen"]
amort = {"mean_sym_tok": m_sym, "mean_occ1_tok": m_occ1, "mean_plain_tok": m_plain,
         "codebook_tok": cb_tok, "curve": []}
for n in range(1, 51):
    amort["curve"].append({
        "n": n,
        "per_msg_codebook": round(cb_tok + m_sym) * 0 + round(n * (cb_tok + m_sym)),  # total
        "once_per_session": round(cb_tok + n * m_sym),
        "occ1": round(n * m_occ1),
        "plain": round(n * m_plain),
    })
# totals are already cumulative; fix the per_msg field naming
for c in amort["curve"]:
    c["per_msg_codebook_total"] = c.pop("per_msg_codebook")
    c["once_total"] = c.pop("once_per_session")
    c["occ1_total"] = c.pop("occ1")
    c["plain_total"] = c.pop("plain")

# ---------------------------------------------------------------- decomposition
# Listener scaffolding exactly as create_agent_session_message_prompt() renders it,
# with realistic daemon metadata (uuid v4 ids, real session names).
SENDER_ACTIVE = "01a142c2-5529-7649-a43d-d5f420e3389f"
SENDER_SESSION = "01a144bc-008c-75fd-b0e7-90ffd4e67f9b"
RECV_ACTIVE = "01a142c2-5529-7649-a43d-d5f420e3389f"
RECV_SESSION = "01a144bc-008c-75fd-b0e7-90ffd4e67f9b"
MSG_ID = "agentmsg_" + "3f2a9c1e-8b4d-4e7a-9c5f-1d2e3f4a5b6c"

def listener_scaffold(relationship="sibling", sender_name="worker-b", recv_name="coordinator"):
    if relationship == "parent":
        label = "[from parent]"
    else:
        label = f"[from {relationship}:{sender_name}]"
    lines = [
        label,
        "Agent-to-agent message received.",
        "Source: agent_message",
        f"From: {sender_name}, active {SENDER_ACTIVE}, session {SENDER_SESSION}",
        f"To: {recv_name}, active {RECV_ACTIVE}, session {RECV_SESSION}",
        f"Message id: {MSG_ID}",
        "",
    ]
    return "\n".join(lines)

scaf_sib = listener_scaffold("sibling", "worker-b", "coordinator")
scaf_par = listener_scaffold("parent")
decomp = {
    "listener_scaffold_sibling": {"chars": len(scaf_sib), "tok": ntok(scaf_sib), "text": scaf_sib},
    "listener_scaffold_parent": {"chars": len(scaf_par), "tok": ntok(scaf_par), "text": scaf_par},
    "codebook": {"chars": codebook["chars"], "tok": cb_tok},
    "wire_payload_symbolic": {"chars": agg(rows,"sym_chars")["mean"], "tok": m_sym},
    "wire_payload_dual_duplication": {
        "chars": agg(rows,"dual_chars")["mean"] - agg(rows,"sym_chars")["mean"],
        "tok": agg(rows,"dual_tok")["mean"] - m_sym,
        "note": "sentinel + OCC-1 fallback section; wire/storage only, not in the model prompt"},
    "task_from_parent_prefix": {"chars": len("[task from parent]\n\n"), "tok": ntok("[task from parent]\n\n")},
    "model_side_placeholders": [
        "MEASURE-ON-DGX: speaker reasoning tokens to compose the OCC-1 body before encoding",
        "MEASURE-ON-DGX: listener reasoning tokens to decode symbolic lines",
        "MEASURE-ON-DGX: clarification round-trips caused by codebook misunderstanding",
    ],
}

out = {"codebook": codebook, "summary": summary, "amortization": amort,
       "decomposition": decomp,
       "rows": [{k: v for k, v in r.items() if not k.endswith("_text")} for r in rows],
       "sample_ids": [r["id"] for r in sample]}
(WT / "research" / "occ2opt_analysis" / "results.json").write_text(
    json.dumps(out, indent=1), encoding="utf-8")

# ---------------------------------------------------------------- report data
print("CODEBOOK:", codebook["chars"], "chars,", codebook["tok_qwen"], "qwen tok,",
      codebook["tok_smol"], "smol tok")
print("codebook line:\n", CODEBOOK_LINE)
print("\nall69 mean: sym %.1f B / %.1f tok | body %.1f B / %.1f tok | occ1 %.1f B / %.1f tok | plain %.1f B / %.1f tok"
      % (agg(rows,"sym_chars")["mean"], m_sym, agg(rows,"body_chars")["mean"], m_body,
         agg(rows,"occ1_chars")["mean"], m_occ1, agg(rows,"plain_chars")["mean"], m_plain))
print("dual factor: %.3f chars, %.3f tok" % (summary["dual_factor_chars"], summary["dual_factor_tok"]))
print("sample ids:", [r["id"] for r in sample])
print("per-kind sym tok:", {k: round(v["mean"],1) for k,v in summary["per_kind_sym_tok"].items()})
print("per-kind occ1 tok:", {k: round(v["mean"],1) for k,v in summary["per_kind_occ1_tok"].items()})
print("scaffold sibling:", decomp["listener_scaffold_sibling"]["chars"], "chars",
      decomp["listener_scaffold_sibling"]["tok"], "tok")
print("scaffold parent:", decomp["listener_scaffold_parent"]["chars"], "chars",
      decomp["listener_scaffold_parent"]["tok"], "tok")
print("\nSILO reference: symbolic 153.8 B/msg, OCC-1 289.8 B/msg")
for label, rs in [("all69", rows), ("non-holdout51", [r for r in rows if not r["holdout"]]),
                  ("holdout18", [r for r in rows if r["holdout"]])]:
    print(" %-14s sym mean %.1f B, occ1 mean %.1f B" % (label,
          statistics.mean(r["sym_chars"] for r in rs), statistics.mean(r["occ1_chars"] for r in rs)))
