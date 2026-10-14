#!/usr/bin/env python3
"""Aggregate loco-ans2 results: per-hypothesis medians + regression table."""
import json, statistics, sys, os

EXP = os.path.dirname(os.path.abspath(__file__))

def load(name):
    p = os.path.join(EXP, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}

def med(rows):
    xs = sorted(round(r["score"], 4) for r in rows if r["score"] is not None)
    return round(statistics.median(xs), 4) if xs else None

res = {}
for f in sys.argv[1:]:
    res.update(load(f))

by_h = {}
for tag, row in res.items():
    if tag.startswith(("q149-", "q144-")):
        hyp = tag.rsplit("-r", 1)[0]
        by_h.setdefault(hyp, []).append(row)

print("=== hypothesis medians (q149 bar 0.8, q144 bar 0.5) ===")
for hyp in sorted(by_h):
    rows = by_h[hyp]
    scores = [round(r["score"], 4) for r in rows]
    print(f"{hyp:14s} median={med(rows)} draws={scores}")
    for r in sorted(rows, key=lambda x: x["tag"]):
        print(f"    {r['tag']}: {round(r['score'],4) if r['score'] is not None else None} {repr((r['answer'] or '')[:80])}")
