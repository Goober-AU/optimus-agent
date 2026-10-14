#!/usr/bin/env python3
"""Build the FINAL8 full-stack table from results-battery-*.json + q57 v3 draws (medians)."""
import glob, json, os, statistics, sys

EXPDIR = os.path.dirname(os.path.abspath(__file__))

BASELINE = {
    "q1": 0.0, "q2": 0.0, "q18": 0.2685, "q26": 0.0606, "q33": 0.0678, "q37": 0.0,
    "q39": 0.1088, "q55": 0.0, "q57": 0.0556, "q66": 0.0, "q77": 0.0, "q99": 0.2574,
    "q144": 0.2927, "q149": 0.0741, "q54": 0.1818, "q126": 0.2105, "q135": 0.3158, "q141": 0.375,
}
GG_FULL2 = {  # loco-full2 validated medians (q57 was the one regression: 0.0)
    "q1": 1.0, "q2": 0.2857, "q18": 1.0, "q26": 1.0, "q33": 1.0, "q37": 0.0,
    "q39": 0.5333, "q55": 1.0, "q57": 0.0, "q66": 0.0, "q77": 0.0, "q99": 0.4828,
    "q144": 0.5714, "q149": 0.8, "q54": 1.0, "q126": 1.0, "q135": 1.0, "q141": 0.5263, "q143": 1.0,
}
BATTERY = ["q1","q2","q18","q26","q33","q37","q39","q55","q57","q66","q77","q99",
           "q144","q149","q54","q126","q135","q141","q143"]
CMP18 = [q for q in BATTERY if q != "q143"]

draws = {q: [] for q in BATTERY}
for path in sorted(glob.glob(os.path.join(EXPDIR, "results-battery-*.json"))):
    data = json.load(open(path, encoding="utf-8"))
    for tag, row in data.items():
        q = tag.rsplit("-r", 1)[0]
        if row.get("score") is not None and q in draws:
            draws[q].append((tag, row["score"], row.get("answer"), row.get("recall_n")))
# q57 v3 validation draws (the FINAL8-v3 fix)
for path in [os.path.join(EXPDIR, "results-q57x3-v3.json")]:
    data = json.load(open(path, encoding="utf-8"))
    for tag, row in data.items():
        q = tag.rsplit("-r", 1)[0]
        if row.get("score") is not None and q in draws:
            draws[q].append((tag, row["score"], row.get("answer"), row.get("recall_n")))

table = {}
for q in BATTERY:
    ds = [s for _, s, _, _ in draws[q]]
    med = statistics.median(ds) if ds else None
    table[q] = {
        "n": len(ds), "draws": ds, "median": med,
        "baseline": BASELINE.get(q), "gg_full2": GG_FULL2.get(q),
        "d_baseline": (round(med - BASELINE[q], 4) if (med is not None and q in BASELINE) else None),
        "d_gg": (round(med - GG_FULL2[q], 4) if (med is not None and q in GG_FULL2) else None),
    }

agg = {}
have = [q for q in CMP18 if table[q]["median"] is not None]
agg["n18"] = len(have)
agg["final8_mean18"] = round(sum(table[q]["median"] for q in have)/len(have), 4)
agg["baseline_mean18"] = round(sum(BASELINE[q] for q in have)/len(have), 4)
agg["delta_vs_baseline"] = round(sum(table[q]["d_baseline"] for q in have)/len(have), 4)
agg["gg_mean18"] = round(sum(GG_FULL2[q] for q in have)/len(have), 4)
agg["delta_vs_gg"] = round(sum(table[q]["d_gg"] for q in have)/len(have), 4)

targets = {}
targets["aggregate_ge_0.49"] = (agg["delta_vs_baseline"] is not None and agg["delta_vs_baseline"] >= 0.49)
targets["q149_ge_0.8"] = table["q149"]["median"] is not None and table["q149"]["median"] >= 0.8
targets["q144_ge_0.5"] = table["q144"]["median"] is not None and table["q144"]["median"] >= 0.5
targets["q18_eq_1.0"] = table["q18"]["median"] is not None and table["q18"]["median"] == 1.0
targets["q55_ge_0.5"] = table["q55"]["median"] is not None and table["q55"]["median"] >= 0.5
targets["q57_ge_0.5"] = table["q57"]["median"] is not None and table["q57"]["median"] >= 0.5
fixed = {}
for q, bar in [("q1", 1.0), ("q54", 1.0), ("q126", 1.0), ("q135", 0.5), ("q141", 0.5), ("q143", 0.5)]:
    fixed[q] = {"bar": bar, "median": table[q]["median"],
                "hold": table[q]["median"] is not None and table[q]["median"] >= bar}
targets["fixed_set_holds"] = all(v["hold"] for v in fixed.values())

out = {"schema": "loco-timeform-final8/1", "battery": table, "aggregate": agg,
       "targets": targets, "fixed_set": fixed}
with open(os.path.join(EXPDIR, "gate-table.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(out, f, indent=1)
    f.write("\n")

hdr = "| q | n | baseline | GG FULL2 | FINAL8 | d(bl) | d(GG) |"
print(hdr)
print("|---|---|---|---|---|---|---|")
for q in BATTERY:
    t = table[q]
    def fmt(v):
        return "—" if v is None else (("%.4f" % v).rstrip("0").rstrip("."))
    print("| %s | %d | %s | %s | %s | %s | %s |" % (
        q, t["n"], fmt(t["baseline"]), fmt(t["gg_full2"]), fmt(t["median"]),
        fmt(t["d_baseline"]), fmt(t["d_gg"])))
print()
print("aggregate (18-q, no q143): FINAL8 mean %s, delta vs baseline %s (target >= +0.49)" %
      (agg["final8_mean18"], agg["delta_vs_baseline"]))
print("delta vs GG FULL2: %s" % agg["delta_vs_gg"])
print("targets:", json.dumps(targets))
if len(sys.argv) > 1 and sys.argv[1] == "--verbose":
    for q in BATTERY:
        print(q, [(t, s) for t, s, _, _ in draws[q]])
