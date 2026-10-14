#!/usr/bin/env python3
"""Build loco-ans2 summary.json from the QUESTION-LEDGER (run_ids a2*)."""
import json, re, statistics, os

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
EXP = os.path.dirname(os.path.abspath(__file__))

L = json.load(open(LEDGER, encoding="utf-8"))
rows = {}
for e in L["entries"]:
    if not e["qid"].startswith("conv-26_"):
        continue
    for a in e.get("attempts") or []:
        rid = a.get("run_id", "")
        if rid.startswith("a2"):
            rows[rid] = {"qid": e["qid"], "score": a.get("score"),
                         "answer": a.get("answer_excerpt"), "outcome": a.get("outcome")}

def draws(pattern):
    rx = re.compile(pattern + r"r\d+$")
    return sorted(v["score"] for rid, v in rows.items() if rx.match(rid))

def med(prefix):
    xs = draws(prefix)
    return round(statistics.median(xs), 4) if xs else None

def ans(pattern):
    rx = re.compile(pattern + r"r\d+$")
    return [v["answer"] for rid, v in sorted(rows.items()) if rx.match(rid)]

summary = {
 "schema": "loco-ans2-results/1",
 "worker": "locoans2",
 "worktree": ROOT + "/imp4-locoans2 (improve/mem4-loco-ans2, base 0cbad1d32)",
 "model": "dgx-glm53/glm-5.3 (only)",
 "driver": "imp4-targetR/release/examples/memory_bench.exe (sha256 c04ab631...)",
 "controller": "imp4/scripts/memory_question_loop.py --mode typeB (warm cache-reuse, copy-out, skipIngest)",
 "store": "fresh typeB cache d4b542a8e98deca6 (176 entries, KU + attribute v2 importInstructions; env conv-26 from locomo-gate3-improved-s0.json)",
 "recall": "det extract-only, maxRecallEntries 24 / 24000 chars (window unchanged; w32 is AA's lane)",
 "attempts_root": ROOT + "/reports/continuation/iteration4/ql-attempts-locoans2",
 "q149_hypotheses": {
   "gate-baseline (h7-combine)": {"median": med(r"a2q149h1") if False else 0.5714, "draws": [0.5714,0.5714,0.5714], "answers": ["love, support, strength, motivation, joy x3 (gate lf4 runs)"]},
   "H1 question-scoped combine": {"median": med(r"a2q149h1"), "draws": draws(r"a2q149h1"), "answers": ans(r"a2q149h1")},
   "H2 arity cap (2-3 attrs)": {"median": med(r"a2q149h2"), "draws": draws(r"a2q149h2"), "answers": ans(r"a2q149h2")},
   "H3 verb-scope": {"median": med(r"a2q149h3"), "draws": draws(r"a2q149h3"), "answers": ans(r"a2q149h3")},
   "H4a recency single-note (worker fix)": {"median": med(r"a2q149h4a"), "draws": draws(r"a2q149h4a"), "answers": ans(r"a2q149h4a")},
   "H4a rerun (variance control)": {"median": med(r"a2h4a2"), "draws": draws(r"a2h4a2"), "answers": ans(r"a2h4a2")},
   "H4b verb+recency": {"median": med(r"a2q149h4b"), "draws": draws(r"a2q149h4b"), "answers": ans(r"a2q149h4b")},
   "H4c single most-direct note": {"median": med(r"a2q149h4c"), "draws": draws(r"a2q149h4c"), "answers": ans(r"a2q149h4c")},
 },
 "q144_hypotheses": {
   "gate-baseline": {"median": 0.333, "draws": [0.333, 0.5, 0.333], "answers": ["He was thankfully okay. / He was scared but thankfully ended up okay. / thankfully he was okay (gate lf4 runs)"]},
   "H1 old-G4 control (frozen-passing instruction on fresh store)": {"median": med(r"a2q144h1"), "draws": draws(r"a2q144h1"), "answers": ans(r"a2q144h1")},
   "H2 feeling-first template": {"median": med(r"a2q144h2"), "draws": draws(r"a2q144h2"), "answers": ans(r"a2q144h2")},
   "H2b never-outcome-alone (worker fix)": {"median": med(r"a2q144hb"), "draws": draws(r"a2q144hb"), "answers": ans(r"a2q144hb")},
 },
 "combined_instructions": {
   "FINAL (h4a+h2b)": {"q149": {"median": med(r"a2fq149"), "draws": draws(r"a2fq149"), "answers": ans(r"a2fq149")},
                        "q144": {"median": med(r"a2fq144"), "draws": draws(r"a2fq144"), "answers": ans(r"a2fq144")}},
   "FINAL2 (every-attribute h5b + h2b)": {"q149": {"median": med(r"a2f2q149"), "draws": draws(r"a2f2q149"), "answers": ans(r"a2f2q149")},
                                          "q144": {"median": med(r"a2f2q144"), "draws": draws(r"a2f2q144"), "answers": ans(r"a2f2q144")}},
   "FINAL3 (every-attribute h5 + h2b)": {"q149": {"median": med(r"a2f3q149"), "draws": draws(r"a2f3q149"), "answers": ans(r"a2f3q149")}},
   "FINAL4 (h4a + no-both how)": {"q149": {"median": med(r"a2f4q149"), "draws": draws(r"a2f4q149"), "answers": ans(r"a2f4q149")},
                                  "q144": {"median": med(r"a2f4q144"), "draws": draws(r"a2f4q144"), "answers": ans(r"a2f4q144")}},
 },
 "regression_final": {},
 "regression_final2": {},
}

REGQ = ["q1", "q26", "q33", "q54", "q55", "q126", "q135", "q141", "q143"]
for q in REGQ:
    summary["regression_final"][q] = {"median": med(r"a2g" + q), "draws": draws(r"a2g" + q), "answers": ans(r"a2g" + q)}
    summary["regression_final2"][q] = {"median": med(r"a2r2g" + q), "draws": draws(r"a2r2g" + q), "answers": ans(r"a2r2g" + q)}

with open(os.path.join(EXP, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(summary, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("summary.json written")
for k, v in summary["q149_hypotheses"].items():
    print(f"q149 {k:44s} median={v['median']} draws={v['draws']}")
for k, v in summary["q144_hypotheses"].items():
    print(f"q144 {k:44s} median={v['median']} draws={v['draws']}")
for k, v in summary["combined_instructions"].items():
    line = f"{k}: "
    for qk, qv in v.items():
        line += f"{qk} median={qv['median']} draws={qv['draws']}  "
    print(line)
for q in REGQ:
    print(f"reg-FINAL  {q:5s} median={summary['regression_final'][q]['median']} draws={summary['regression_final'][q]['draws']}")
    print(f"reg-FINAL2 {q:5s} median={summary['regression_final2'][q]['median']} draws={summary['regression_final2'][q]['draws']}")
