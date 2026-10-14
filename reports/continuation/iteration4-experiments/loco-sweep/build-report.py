#!/usr/bin/env python3
"""loco-sweep aggregate report builder.

Reads locosweep attempts from the main ledger (conv != 26), gate attempts from
the gate ledger, classifies non-converts (B/D/G), and prints the RESULTS tables.
Usage: python build-report.py
"""
import json, os, re, collections

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
GATE_LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER-locosweep-gate.json"

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def conv_of_qid(qid):
    m = re.match(r"conv-(\d+)_q\d+$", qid)
    return int(m.group(1)) if m else None

def classify_bdG(qid, e, att, notes, qs):
    gold = qs.get("answer") or (qs.get("eval") or {}).get("answer")
    ans = att.get("answer_excerpt") or ""
    rec_ids = set(i.split("memory:")[-1] for i in (att.get("recall_ids") or []))
    gtoks = [t.strip(".,?!'\"").lower() for t in re.split(r"[\s/]+", str(gold))
             if len(t) > 3 and t.strip(".,?!'\"").isalnum()]
    best_note, best_hit = None, 0.0
    for k, v in notes.items():
        text = ((v.get("title") or "") + " " + (v.get("content") or "")).lower()
        hits = sum(1 for t in gtoks if t in text)
        cov = hits / max(1, len(gtoks))
        if cov > best_hit:
            best_hit, best_note = cov, k
    gold_in_store = best_hit >= 0.99 and best_note is not None
    gold_in_window = best_note in rec_ids if best_note else False
    if "not mentioned" in ans.lower() and not gold_in_store:
        stage = "B"
    elif "not mentioned" in ans.lower() and gold_in_store and not gold_in_window:
        stage = "D"
    else:
        stage = "G"
    return {"gold": gold, "stage": stage, "gold_note": best_note,
            "cov": round(best_hit, 2), "in_window": gold_in_window}

QS_CACHE, NOTE_CACHE = {}, {}
def qs_records(conv):
    if conv not in QS_CACHE:
        p = ROOT + "/reports/continuation/iteration2-inputs/locomo/common/locomo/qs_conv-%d.jsonl" % conv
        QS_CACHE[conv] = {json.loads(l)["qid"]: json.loads(l) for l in open(p, encoding="utf-8")}
    return QS_CACHE[conv]

STORE = {}
def store_notes(conv):
    if conv not in NOTE_CACHE:
        sp = None
        for e in load(LEDGER)["entries"]:
            if e.get("benchmark") == "locomo" and e.get("env_id") == "conv-%d" % conv:
                sp = e["store_path"]; break
        pj = os.path.join(sp, "agent", "memory", "projects")
        proj = [d for d in os.listdir(pj) if d.startswith("project_")][0]
        NOTE_CACHE[conv] = json.load(open(os.path.join(pj, proj, "harness_state.json")))["entries"]["memory"]
    return NOTE_CACHE[conv]

def main():
    L = load(LEDGER)
    rows = []
    for e in L["entries"]:
        if e.get("benchmark") != "locomo":
            continue
        atts = [a for a in (e.get("attempts") or []) if a.get("worker") == "locosweep"]
        if not atts:
            continue
        c = conv_of_qid(e["qid"])
        if c == 26:
            continue
        latest = atts[-1]
        bl = e.get("baseline")
        rows.append({"qid": e["qid"], "conv": c, "cat": e.get("category"),
                     "baseline": bl, "score": latest.get("score"),
                     "outcome": latest.get("outcome"), "stage_ctl": latest.get("failure_stage"),
                     "answer": latest.get("answer_excerpt"),
                     "converted": bl is not None and bl < 0.5 and (latest.get("score") or 0) >= 0.5})
    rows.sort(key=lambda r: (r["conv"], r["qid"]))
    print("== SWEEP ROWS: %d ==" % len(rows))
    byc = collections.defaultdict(list)
    for r in rows:
        byc[r["conv"]].append(r)
    tot_conv = sum(1 for r in rows if r["converted"])
    print("converts: %d/%d (%.1f%%)" % (tot_conv, len(rows), 100.0 * tot_conv / max(1, len(rows))))
    for c in sorted(byc):
        rs = byc[c]
        cv = sum(1 for r in rs if r["converted"])
        print("conv-%d: %d/%d converts" % (c, cv, len(rs)))
    # classify non-converts
    print()
    print("== NON-CONVERT CLASSIFICATION (B/D/G) ==")
    mix = collections.Counter()
    for r in rows:
        if r["converted"] or r["outcome"] not in ("fail", "flagged"):
            continue
        e = next(x for x in L["entries"] if x["qid"] == r["qid"])
        att = [a for a in e["attempts"] if a.get("worker") == "locosweep"][-1]
        cls = classify_bdG(r["qid"], e, att, store_notes(r["conv"]), qs_records(r["conv"])[r["qid"]])
        r["cls"] = cls
        mix[cls["stage"]] += 1
        print("%-14s bl=%.3f sweep=%.3f %s | Q: %s" % (r["qid"], r["baseline"] or 0, r["score"] or 0,
              cls["stage"], (qs_records(r["conv"])[r["qid"]].get("question") or "")[:70]))
        print("    gold: %s" % (cls["gold"],))
        print("    ans: %s | cov=%.2f in_window=%s" % ((r["answer"] or "")[:100], cls["cov"], cls["in_window"]))
    print()
    print("== STAGE MIX ==", dict(mix))
    # near misses
    nm = [r for r in rows if not r["converted"] and r["score"] is not None and 0.3 <= r["score"] < 0.5]
    print("near-misses 0.3<=s<0.5:", [(r["qid"], round(r["score"], 3)) for r in nm])
    # gates
    print()
    print("== GATES ==")
    try:
        G = load(GATE_LEDGER)
        for e in G["entries"]:
            atts = [a for a in (e.get("attempts") or []) if a.get("worker") == "locosweep"]
            for a in atts:
                print("%-14s baseline=1.0 sweep=%s outcome=%s" % (e["qid"], a.get("score"), a.get("outcome")))
    except FileNotFoundError:
        print("no gate ledger attempts yet")

if __name__ == "__main__":
    main()
