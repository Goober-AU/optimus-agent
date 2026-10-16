
from transformers import AutoTokenizer
import pathlib, json, sys
MODELS = pathlib.Path(r"C:/Users/openclawuser/optimus-occ23-models/hub")
QWEN = str(next((MODELS / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots").glob("*")))
tok = AutoTokenizer.from_pretrained(QWEN, local_files_only=True)
def show(s):
    ids = tok(s, add_special_tokens=False)["input_ids"]
    print(f"{s!r:50s} -> {len(ids):3d} tok  {tok.convert_ids_to_tokens(ids)}")

print("== timestamp forms ==")
show("w=20261016090200")          # current MT6-collapsed
show("TS=2026-10-16T09:02:00Z")   # OCC-1 form
show("w=1792260120")              # epoch seconds
show("w=gsrvvb4")                 # base36 of epoch? (1792260120 -> base36)
import datetime
ep = int(datetime.datetime(2026,10,16,9,2,0,tzinfo=datetime.timezone.utc).timestamp())
print("epoch:", ep)
def b36(n):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    s = ""
    while n: s = digits[n % 36] + s; n //= 36
    return s or "0"
print("base36:", b36(ep))
show("w=" + b36(ep))
show("w=" + str(ep))

print("\n== header forms ==")
for h in ["D/1", "d", "/", "\\", "1", "D"]:
    show(h)

print("\n== newline merging ==")
show("k=T")
show("s=0")
show("k=T\ns=0")
show("k=T\ns=0\n")
show("m=m-201\nt=occ2-01")

print("\n== separator-less ==")
show("mm-201")
show("s0")
show("kT")
show("tocc2-01")
show("w" + str(ep))

print("\n== epoch digit tokenization ==")
show("1792260120")
show(str(ep))
show("20261016090200")

print("\n== sha digest lines ==")
show("e2=s:^src/core/occ/dialect.rs@14-61#22f20801191539b2c888c695878804959d03ee676b58f1d6322f3808862e2632~ce8480d59")
show("22f20801191539b2c888c695878804959d03ee676b58f1d6322f3808862e2632")
