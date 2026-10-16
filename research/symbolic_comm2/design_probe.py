
import json, pathlib, sys, re, collections, statistics
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
import dialect as D
from contract import AgentCommMessage
from baselines import occ1_text
from transformers import AutoTokenizer
MODELS = pathlib.Path(r"C:/Users/openclawuser/optimus-occ23-models/hub")
QWEN = str(next((MODELS / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots").glob("*")))
tok = AutoTokenizer.from_pretrained(QWEN, local_files_only=True)
def ntok(t): return len(tok(t, add_special_tokens=False)["input_ids"])
def ntok_many(ts): return [len(x) for x in tok(ts, add_special_tokens=False)["input_ids"]]

card = D.DialectCard.load(str(HERE.parent / "symbolic_comm" / "dialect-card.json"))
raw = json.loads((HERE.parent / "tasks2" / "msgs_corpus2_evolve.json").read_text(encoding="utf-8"))
msgs = [AgentCommMessage.from_payload(e["payload"]) for e in raw["messages"]]

# 1. what does the TS line look like?
w0 = card.encode(msgs[0])
print("--- sample wire ---")
print(w0[:400])
print("-------------------")

# 2. header candidates
print("header candidates:", {h: ntok(h) for h in ["D/1","d","D","D2","2","#","d2","D/2"]})

# 3. base36 casing cost on a real digest
sha = "22f20801191539b2c888c695878804959d03ee676b58f1d6322f3808862e2632"
n = int(sha, 16)
def b36(n, upper):
    digs = "0123456789abcdefghijklmnopqrstuvwxyz"
    if upper: digs = digs.upper()
    s = ""
    while n: s = digs[n % 36] + s; n //= 36
    return s or "0"
lo, up = b36(n, False), b36(n, True)
print("digest hex64:", ntok(sha), "tok,", len(sha), "chars")
print("digest b36lower:", ntok(lo), "tok,", len(lo), "chars")
print("digest b36upper:", ntok(up), "tok,", len(up), "chars")
# several digests
digs = []
revs = []
for m in msgs:
    for k, v in m.occ1_fields():
        if k.startswith("ED") and k[2:].isdigit():
            if re.fullmatch(r"[0-9a-f]{64}", v): digs.append(v)
            else: print("odd ED:", k, v[:40])
        if k.startswith("ER") and k[2:].isdigit(): revs.append(v)
print("revs:", revs[:5], len(revs))
print("digests found:", len(digs))
hl = [ntok(d) for d in digs]
bl = [ntok(b36(int(d, 16), False)) for d in digs]
bu = [ntok(b36(int(d, 16), True)) for d in digs]
print("hex mean %.1f  b36lower mean %.1f  b36upper mean %.1f (n=%d)" % (statistics.mean(hl), statistics.mean(bl), statistics.mean(bu), len(digs)))

# 4. status distribution
st = collections.Counter()
for m in msgs:
    for k, v in m.occ1_fields():
        if k == "STATUS": st[v] += 1
print("status dist:", dict(st))

# 5. TS wire form + epoch b36 cost
ts_lines = []
for m in msgs:
    for line in card.encode(m).split("\n"):
        if line.startswith("w="): ts_lines.append(line)
print("ts line samples:", ts_lines[:3])
t14 = [ntok(l) for l in ts_lines]
import datetime
def epoch36(line):
    digits = line[2:]
    dt = datetime.datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
    return b36(int(dt.timestamp()), False)
tb = [ntok("w=" + epoch36(l)) for l in ts_lines]
print("ts 14-digit mean %.2f  b36 mean %.2f (n=%d)" % (statistics.mean(t14), statistics.mean(tb), len(t14)))

# 6. digest token share of the whole wire
tot = sum(ntok(card.encode(m)) for m in msgs)
dshare = sum(hl) + sum(ntok(x) for m in msgs for x in re.findall(r"~[0-9a-f]{6,12}", card.encode(m)))
print("total wire tok: %d, digest-ish tok: %d (%.1f%%)" % (tot, dshare, 100.0*dshare/tot))

# 7. prose prefix mining potential (i/n lines on corpus2)
pref = collections.Counter()
for m in msgs:
    for line in card.encode(m).split("\n"):
        al, _, val = line.partition("=")
        if al[:1] in ("i", "n") and al[1:2].isdigit() or al == "n":
            for size in (4, 6, 8, 10):
                if len(val) > size + 4: pref[(al, val[:size])] += 1
common = [(k, c) for k, c in pref.most_common(30) if c >= 5]
print("common prose prefixes (>=5):", common[:15])
