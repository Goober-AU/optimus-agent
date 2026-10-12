"""LoCoMo iteration-3 stack arm: merge shards, score, compare vs both anchors.

Usage (from any cwd, with any python >= 3.8):
  python score_stack.py                 # merge shards only
  python score_stack.py --score        # merge + score stack arm (deterministic local F1, no LLM)
  python score_stack.py --compare       # print the comparison vs both anchors + paired stats

Anchors:
  - iteration-2 (binary 4ece283e @ 7997601778): 0.2626 cats1-4 / 0.2039 overall
  - iteration-3 gate defaults (binary ab1e1474 @ bdffcad50): 0.2766 cats1-4 / 0.2148 overall
Scoring subprocess: repo-continuation bench venv, cwd at this worktree root
(bench/ package at improve/memory-iteration-3 HEAD). Gold question files are
read read-only from the iteration-2 gate common dir; question set is
byte-identical (declarations sha 9e88a6fd...).
"""
import argparse, json, math, os, subprocess, sys

GATE = os.path.dirname(os.path.abspath(__file__))
CAMPAIGN = os.path.abspath(os.path.join(GATE, "..", "..", "..", "..", ".."))
BENCH_REPO_CWD = os.path.abspath(os.path.join(GATE, "..", "..", "..", ".."))
BENCH_PY = os.path.join(CAMPAIGN, "repo-continuation", "bench", ".venv", "Scripts", "python.exe")
I2_GATE = os.path.join(CAMPAIGN, "reports", "continuation", "iteration2-inputs", "locomo")
QS_DIR = os.path.join(I2_GATE, "common", "locomo")
RUNS = os.path.join(GATE, "runs-stack")
SCORES = os.path.join(GATE, "scores")
SHARDS = ["stack-s0", "stack-s1", "stack-s2"]
VARIANT = "improved-stack"

ANCHOR_IT2 = {
    "overall_mean_f1": 0.2039,
    "overall_acc05": 0.1667,
    "cats14_mean_f1": 0.2626,
    "cats14_acc05": 0.2146,
    "by_category_mean_f1": {"1": 0.161, "2": 0.2279, "3": 0.1616, "4": 0.322, "5": 0.0},
    "by_category_n": {"1": 43, "2": 48, "3": 15, "4": 127, "5": 67},
}
ANCHOR_G3 = {
    "overall_mean_f1": 0.2148,
    "overall_acc05": 0.1833,
    "cats14_mean_f1": 0.2766,
    "cats14_acc05": 0.2361,
    "by_category_mean_f1": {"1": 0.1307, "2": 0.2485, "3": 0.2806, "4": 0.3361, "5": 0.0},
    "by_category_n": {"1": 43, "2": 48, "3": 15, "4": 127, "5": 67},
}


def merge():
    os.makedirs(os.path.join(RUNS, "stack-merged"), exist_ok=True)
    out = os.path.join(RUNS, "stack-merged", "run.jsonl")
    n = 0
    with open(out, "w", encoding="utf-8", newline="\n") as w:
        for s in SHARDS:
            p = os.path.join(RUNS, s, "run.jsonl")
            if not os.path.isfile(p):
                print(f"missing {p}", file=sys.stderr)
                continue
            with open(p, encoding="utf-8") as r:
                for line in r:
                    if line.strip():
                        w.write(line)
                        n += 1
    print(f"merged {n} rows -> {out}")
    decl = os.path.join(GATE, "declarations", "locomo-dev300-model-questions.jsonl")
    decl_qids = set()
    with open(decl, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                decl_qids.add(json.loads(line)["qid"])
    man_qids, variants = set(), set()
    with open(out, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                man_qids.add(row["qid"])
                variants.add(row.get("variant"))
    print(f"qids: {len(man_qids)} unique vs {len(decl_qids)} declarations; identical set: {man_qids == decl_qids}; variants: {variants}")
    return n


def run_score(run_path, out_path, variant):
    cmd = [BENCH_PY, "-B", "-m", "bench.eval.evaluate", "--run", run_path,
           "--bench", "locomo", "--qs-dir", QS_DIR, "--out", out_path,
           "--variant", variant]
    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=BENCH_REPO_CWD)
    print("rc:", r.returncode)
    return r.returncode


def score():
    os.makedirs(SCORES, exist_ok=True)
    out = os.path.join(SCORES, f"{VARIANT}.json")
    if os.path.exists(out):
        print(f"skip existing {out}")
        return
    run_score(os.path.join(RUNS, "stack-merged", "run.jsonl"), out, VARIANT)


def arm_summary(path):
    sc = json.load(open(path, encoding="utf-8"))
    rows = [r for r in sc["results"] if r.get("score") is not None]
    cats14 = [r for r in rows if str(r.get("category")) in ("1", "2", "3", "4")]

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    by_cat = {}
    for c in ("1", "2", "3", "4", "5"):
        cat_rows = [r for r in rows if str(r.get("category")) == c]
        by_cat[c] = {
            "n": len(cat_rows),
            "mean_f1": round(mean([r["score"] for r in cat_rows]), 4),
            "acc05": round(mean([1.0 if r["score"] >= 0.5 else 0.0 for r in cat_rows]), 4),
        }
    return {
        "n": len(rows),
        "overall_mean_f1": round(mean([r["score"] for r in rows]), 4),
        "overall_acc05": round(mean([1.0 if r["score"] >= 0.5 else 0.0 for r in rows]), 4),
        "cats14_mean_f1": round(mean([r["score"] for r in cats14]), 4),
        "cats14_acc05": round(mean([1.0 if r["score"] >= 0.5 else 0.0 for r in cats14]), 4),
        "by_category": by_cat,
        "_rows": {r["qid"]: r for r in rows},
    }


def paired_stats(new_rows, old_rows, cats=("1", "2", "3", "4")):
    qids = [q for q in new_rows if q in old_rows]
    ds = []
    for q in qids:
        if str(new_rows[q].get("category")) in cats:
            ds.append(float(new_rows[q]["score"]) - float(old_rows[q]["score"]))
    n = len(ds)
    if not n:
        return None
    mean = sum(ds) / n
    var = sum((d - mean) ** 2 for d in ds) / (n - 1) if n > 1 else 0.0
    se = math.sqrt(var / n) if n else 0.0
    t = mean / se if se > 0 else 0.0
    p = math.erfc(abs(t) / math.sqrt(2.0))  # two-sided normal approx
    return {"n": n, "mean_delta": round(mean, 4), "se": round(se, 4), "t": round(t, 3), "p_norm": round(p, 4)}


def compare():
    new = arm_summary(os.path.join(SCORES, f"{VARIANT}.json"))
    anc_it2 = ANCHOR_IT2
    anc_g3 = ANCHOR_G3
    g3_rows = arm_summary(os.path.join(SCORES, "improved-defaults.json"))["_rows"]
    print(f"{'metric':<28}{'anchor(it2)':>12}{'gate-defaults':>14}{'stack':>10}{'d vs g3':>10}")
    def line(label, a, g, b):
        print(f"{label:<28}{a:>12.4f}{g:>14.4f}{b:>10.4f}{b - g:>+10.4f}")
    line("overall mean F1", anc_it2["overall_mean_f1"], anc_g3["overall_mean_f1"], new["overall_mean_f1"])
    line("overall acc@0.5", anc_it2["overall_acc05"], anc_g3["overall_acc05"], new["overall_acc05"])
    line("cats 1-4 mean F1", anc_it2["cats14_mean_f1"], anc_g3["cats14_mean_f1"], new["cats14_mean_f1"])
    line("cats 1-4 acc@0.5", anc_it2["cats14_acc05"], anc_g3["cats14_acc05"], new["cats14_acc05"])
    names = {"1": "1 multi-hop", "2": "2 temporal", "3": "3 open-domain", "4": "4 single-hop", "5": "5 adversarial"}
    for c in ("1", "2", "3", "4", "5"):
        a, g, b = (anc_it2["by_category_mean_f1"][c], anc_g3["by_category_mean_f1"][c],
                   new["by_category"][c]["mean_f1"])
        assert anc_it2["by_category_n"][c] == new["by_category"][c]["n"], f"category {c} n mismatch"
        print(f"{names[c] + ' mean F1':<28}{a:>12.4f}{g:>14.4f}{b:>10.4f}{b - g:>+10.4f}  (n={new['by_category'][c]['n']})")
    ps = paired_stats(new["_rows"], g3_rows)
    if ps:
        print(f"paired cats1-4 delta vs gate-defaults: mean {ps['mean_delta']:+.4f}, se {ps['se']:.4f}, t={ps['t']}, p_norm={ps['p_norm']}")
    return new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    merge()
    if args.score:
        score()
    if args.compare:
        compare()
