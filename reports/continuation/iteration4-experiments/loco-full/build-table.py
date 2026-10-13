#!/usr/bin/env python3
"""Build the LoCoMo full-stack gate table: fresh vs frozen-det vs baseline."""
import json, sys, glob

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
EXP = ROOT + "/imp4/reports/continuation/iteration4-experiments"
EXPDIR = ROOT + "/imp4-locofull/reports/continuation/iteration4-experiments/loco-full"

h2h = json.load(open(EXP + "/loco-h2h/h2h-results.json", encoding="utf-8"))
frozen = {}
for qid, d in h2h["runs"].items():
    frozen[qid.replace("conv-26_", "")] = d

# q143 det on frozen store = 0.5 (loco-answers regression gate)
frozen.setdefault("q143", {"baseline": None, "det": 0.5})

battery = sys.argv[1:] or ["q1","q2","q18","q26","q33","q37","q39","q55","q57","q66","q77","q99","q144","q149","q54","q126","q135","q141","q143"]
tag = sys.argv[1] if len(sys.argv) > 1 else "a"

fresh = {}
for tg in sys.argv[2:]:
    pass
import os
for p in sorted(glob.glob(EXPDIR + "/battery-*.json")):
    data = json.load(open(p, encoding="utf-8"))
    for q, r in data.items():
        fresh[q] = r

print(f"{'q':6} {'cat':14} {'baseline':>9} {'frozen':>7} {'fresh':>7} {'d(f-fz)':>8} {'d(f-bl)':>8}")
rows = []
for q in battery:
    fr = fresh.get(q, {}).get("score")
    fz = frozen.get(q, {}).get("det")
    bl = frozen.get(q, {}).get("baseline")
    if fr is None:
        continue
    d_fz = (fr - fz) if (fr is not None and fz is not None) else None
    d_bl = (fr - bl) if (fr is not None and bl is not None) else None
    cat = frozen.get(q, {}).get("category", "?")
    print(f"{q:6} {cat:14} {bl if bl is not None else '':>9} {fz if fz is not None else '':>7} {fr:>7.3f} "
          f"{d_fz if d_fz is not None else '':>8} {d_bl if d_bl is not None else '':>8}")
    rows.append({"q": q, "category": cat, "baseline": bl, "frozen_det": fz, "fresh": fr,
                 "delta_frozen": d_fz, "delta_baseline": d_bl, "answer": fresh[q].get("answer")})

# aggregates
fr_ = [r["fresh"] for r in rows]
bl_ = [r["delta_baseline"] for r in rows if r["delta_baseline"] is not None]
fz_ = [r["delta_frozen"] for r in rows if r["delta_frozen"] is not None]
print(f"\nn={len(rows)} fresh mean={sum(fr_)/len(fr_):.4f}")
if bl_: print(f"mean delta vs baseline: {sum(bl_)/len(bl_):+.4f} (frozen det all-18 was +0.296)")
if fz_: print(f"mean delta vs frozen-det: {sum(fz_)/len(fz_):+.4f}")

out = {"rows": rows,
       "aggregate": {"n": len(rows), "fresh_mean": sum(fr_)/len(fr_),
                     "mean_delta_baseline": (sum(bl_)/len(bl_)) if bl_ else None,
                     "mean_delta_frozen": (sum(fz_)/len(fz_)) if fz_ else None}}
json.dump(out, open(EXPDIR + "/gate-table.json", "w", encoding="utf-8", newline="\n"), indent=1)
print("\nsaved gate-table.json")
