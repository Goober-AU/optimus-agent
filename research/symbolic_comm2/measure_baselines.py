"""Baseline token/byte measurements for dialect v2 (offline, local tokenizer).

Measures, with the same Qwen2.5-0.5B tokenizer used for CODEBOOK-COST:
- OCC-1 wire text (deterministic mirror of the contract encoder)
- the frozen v1 dialect card (sym-v1-pt-pt-r1, D/1 wire)
on both the v1 corpus profile (research/tasks/msgs_corpus.json, 69 msgs)
and the corpus2 evolve split (research/tasks2, 90 msgs). Also prints a
per-line token breakdown to show where the tokens are.
"""
import json, pathlib, sys, statistics

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))

import dialect as D
from contract import AgentCommMessage
from baselines import occ1_text

from transformers import AutoTokenizer
MODELS = pathlib.Path(r"C:/Users/openclawuser/optimus-occ23-models/hub")
QWEN = str(next((MODELS / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots").glob("*")))
tok = AutoTokenizer.from_pretrained(QWEN, local_files_only=True)

def ntok(text):
    return len(tok(text, add_special_tokens=False)["input_ids"])

def load(path):
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    messages = raw["messages"] if isinstance(raw, dict) else raw
    out = []
    for entry in messages:
        out.append({"id": entry["id"], "message": AgentCommMessage.from_payload(entry["payload"])})
    return out

CORPUS1 = HERE.parent / "tasks" / "msgs_corpus.json"
CORPUS2_EVOLVE = HERE.parent / "tasks2" / "msgs_corpus2_evolve.json"
CORPUS2_DEV = HERE.parent / "tasks2" / "msgs_corpus2_dev.json"

card = D.DialectCard.load(str(HERE.parent / "symbolic_comm" / "dialect-card.json"))

def profile(name, items):
    occ1_tokens, occ1_bytes = [], []
    d1_tokens, d1_bytes = [], []
    for item in items:
        o = occ1_text(item["message"])
        d = card.encode(item["message"])
        occ1_tokens.append(ntok(o)); occ1_bytes.append(len(o))
        d1_tokens.append(ntok(d)); d1_bytes.append(len(d))
    n = len(items)
    print(f"[{name}] n={n}")
    print(f"  OCC-1 : {statistics.mean(occ1_tokens):7.2f} tok  {statistics.mean(occ1_bytes):7.1f} B  (median tok {statistics.median(occ1_tokens):.1f})")
    print(f"  D/1 v1: {statistics.mean(d1_tokens):7.2f} tok  {statistics.mean(d1_bytes):7.1f} B  (median tok {statistics.median(d1_tokens):.1f})")
    print(f"  ratio : {statistics.mean(d1_tokens)/statistics.mean(occ1_tokens):.4f} tok  {statistics.mean(d1_bytes)/statistics.mean(occ1_bytes):.4f} B")
    return {"occ1_tok": statistics.mean(occ1_tokens), "occ1_b": statistics.mean(occ1_bytes),
            "d1_tok": statistics.mean(d1_tokens), "d1_b": statistics.mean(d1_bytes)}

c1 = profile("corpus1 (69)", load(CORPUS1))
c2 = profile("corpus2 evolve (90)", load(CORPUS2_EVOLVE))
c2d = profile("corpus2 dev (60)", load(CORPUS2_DEV))

# per-line token breakdown on corpus1 for the D/1 wire
items = load(CORPUS1)
line_tokens = {}
for item in items:
    for line in card.encode(item["message"]).split("\n"):
        key = line.split("=", 1)[0]
        line_tokens.setdefault(key, []).append(ntok(line))
print("\nper-line mean tokens by wire key (corpus1, D/1):")
for key in sorted(line_tokens, key=lambda k: -statistics.mean(line_tokens[k])):
    vals = line_tokens[key]
    print(f"  {key:6s} n={len(vals):3d} mean={statistics.mean(vals):5.2f} total={sum(vals):5d}")

# line anatomy: how many tokens do key, '=', value take separately
print("\nline anatomy samples:")
for line in ["m=m-201", "t=occ2-01", "s=0", "k=T", "w=20261016090200", "D/1",
             "i1=implement the OCC-2 dialect discovery prototype behind a feature flag"]:
    print(f"  {line[:50]!r:55s} -> {ntok(line)} tok (chars {len(line)})")
print("\nseparator variants (KEY<sep>VALUE token counts):")
for sep in ["=", ":", " ", ""]:
    print(f"  sep {sep!r}: 'm{sep}m-201'={ntok('m'+sep+'m-201')}, 's{sep}0'={ntok('s'+sep+'0')}, 'k{sep}T'={ntok('k'+sep+'T')}")
