#!/usr/bin/env python3
"""loco-stability classifier: sweep converts x2 draws -> STABLE / VARIANCE-BAND / UNSTABLE.

Reads results-d1/d2/q10.json (runner outputs), the ledger (JJ's original n=1
sweep attempt + my locostab attempts with recall_ids), and emits
classification.json + the RESULTS.md tables.

Classification (draws d1, d2; smoke/q10 extras recorded but not used for the tally):
  STABLE        both draws >= 0.5
  VARIANCE-BAND exactly one draw >= 0.5 (answer forms recorded)
  UNSTABLE      both draws < 0.5 (the sweep convert was a fluke draw)
Also verifies recall determinism across draws (ids identical) per question.
"""
import json, os, re, statistics, collections

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
EXP = ROOT + "/imp4b-stability/reports/continuation/iteration4-experiments/loco-stability"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

L = load(LEDGER)
ent = {}
for e in L["entries"]:
    if e.get("benchmark") == "locomo":
        ent[e["qid"]] = e

r1 = load(EXP + "/results-d1.json")
r2 = load(EXP + "/results-d2.json")
rr = load(EXP + "/results-retry.json") if os.path.isfile(EXP + "/results-retry.json") else {}
for tag, row in rr.items():
    # retry replaces an unscored d1 row (transient ledger-write collision); tag c41q91d1r -> c41q91d1
    orig_tag = tag[:-1]
    base = r1.get(orig_tag)
    if base is not None and base.get("qid") == row["qid"] and base.get("score") is None:
        merged = dict(row); merged["tag"] = orig_tag
        r1[orig_tag] = merged
extra = {}
for tag, row in (load(EXP + "/results-q10.json") if os.path.isfile(EXP + "/results-q10.json") else {}).items():
    extra.setdefault(row["qid"], []).append(row)

rows = []
for tag in sorted(r1):
    qid = r1[tag]["qid"]
    e = ent[qid]
    sw = [a for a in (e.get("attempts") or []) if a.get("worker") == "locosweep"]
    jj = sw[-1] if sw else None
    mine = {a["run_id"]: a for a in (e.get("attempts") or []) if a.get("worker") == "locostab"}
    d1 = mine.get(r1[tag]["run_id"])
    d2tag = tag[:-2] + "d2" if tag.endswith("d1") else None
    d2 = mine.get(r2[tag[:-2] + "d2"]["run_id"]) if d2tag and d2tag in r2 else None
    draws = [d1, d2]
    scores = [d.get("score") for d in draws if d]
    extras = [a for rid, a in sorted(mine.items()) if rid not in (r1[tag]["run_id"], (r2.get(tag[:-2] + "d2") or {}).get("run_id"))]
    ge = [s for s in scores if s is not None and s >= 0.5]
    if len(scores) < 2:
        cls = "INCOMPLETE"
    elif len(ge) == 2:
        cls = "STABLE"
    elif len(ge) == 1:
        cls = "VARIANCE-BAND"
    else:
        cls = "UNSTABLE"
    # recall determinism across draws
    rid_sets = [tuple(d.get("recall_ids") or []) for d in draws if d]
    recall_same = (len(set(rid_sets)) == 1) if rid_sets else None
    jj_rid = tuple(jj.get("recall_ids") or []) if jj else None
    rows.append({
        "qid": qid, "category": e.get("category"), "baseline": e.get("baseline"),
        "jj_score": jj.get("score") if jj else None,
        "jj_answer": (jj.get("answer_excerpt") or "")[:120] if jj else None,
        "d1_score": d1.get("score") if d1 else None,
        "d1_answer": (d1.get("answer_excerpt") or "")[:120] if d1 else None,
        "d1_flag": d1.get("flag") if d1 else None,
        "d2_score": d2.get("score") if d2 else None,
        "d2_answer": (d2.get("answer_excerpt") or "")[:120] if d2 else None,
        "d2_flag": d2.get("flag") if d2 else None,
        "extra_scores": [a.get("score") for a in extras],
        "extra_answers": [(a.get("answer_excerpt") or "")[:120] for a in extras],
        "median": statistics.median(scores) if scores else None,
        "mean": (sum(scores) / len(scores)) if scores else None,
        "class": cls,
        "recall_same_across_draws": recall_same,
        "recall_same_as_jj": (jj_rid in set(rid_sets)) if (jj_rid and rid_sets) else None,
        "recall_ids_count": len(rid_sets[0]) if rid_sets else None,
    })

counts = collections.Counter(r["class"] for r in rows)
out = {"counts": dict(counts), "n": len(rows), "rows": rows}
with open(EXP + "/classification.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(out, f, indent=1)
    f.write("\n")
print("classification:", dict(counts), "of", len(rows))
for cl in ("UNSTABLE", "VARIANCE-BAND"):
    print("==", cl)
    for r in rows:
        if r["class"] == cl:
            print("  %-14s jj=%s d1=%s (%s) d2=%s (%s) recall_same=%s" % (
                r["qid"], r["jj_score"], r["d1_score"], (r["d1_answer"] or "")[:40],
                r["d2_score"], (r["d2_answer"] or "")[:40], r["recall_same_across_draws"]))
print("recall mismatches across draws:", [r["qid"] for r in rows if r["recall_same_across_draws"] is False])
print("recall mismatch vs JJ:", [r["qid"] for r in rows if r["recall_same_as_jj"] is False])
