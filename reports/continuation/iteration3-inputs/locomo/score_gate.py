"""LoCoMo gate v2: merge improved shards, score, and compare vs the iteration-2 anchor.

Usage (from any cwd, with any python >= 3.8):
  python score_gate.py                 # merge shards only
  python score_gate.py --score        # merge + score improved arm (deterministic local F1, no LLM)
  python score_gate.py --rescore-anchor # re-score the iteration-2 anchor run with THIS bench code
  python score_gate.py --compare       # print the comparison table vs the iteration-2 anchor

The scoring subprocess uses the repo-continuation bench venv with cwd at the
imp3-gate worktree root (bench/ package at improve/mem3-gate2 HEAD). The gold
question files are read read-only from the iteration-2 gate common dir; the
question set is byte-identical (declarations sha 9e88a6fd...).
"""
import argparse, json, os, subprocess, sys

GATE = os.path.dirname(os.path.abspath(__file__))
CAMPAIGN = os.path.abspath(os.path.join(GATE, "..", "..", "..", "..", ".."))
BENCH_REPO_CWD = os.path.abspath(os.path.join(GATE, "..", "..", "..", ".."))
BENCH_PY = os.path.join(CAMPAIGN, "repo-continuation", "bench", ".venv", "Scripts", "python.exe")
I2_GATE = os.path.join(CAMPAIGN, "reports", "continuation", "iteration2-inputs", "locomo")
QS_DIR = os.path.join(I2_GATE, "common", "locomo")
ANCHOR_RUN = os.path.join(I2_GATE, "runs", "improved-merged", "run.jsonl")
ANCHOR_SCORE = os.path.join(I2_GATE, "scores", "improved-defaults.json")
RUNS = os.path.join(GATE, "runs")
SCORES = os.path.join(GATE, "scores")
SHARDS = ["improved-s0", "improved-s1", "improved-s2"]
VARIANT = "improved-defaults"

# Iteration-2 anchor numbers (binary 4ece283e @ 7997601778) for the gate.
ANCHOR = {
    "overall_mean_f1": 0.2039,
    "overall_acc05": 0.1667,
    "cats14_mean_f1": 0.2626,
    "cats14_acc05": 0.2146,
    "by_category_mean_f1": {"1": 0.161, "2": 0.2279, "3": 0.1616, "4": 0.322, "5": 0.0},
    "by_category_n": {"1": 43, "2": 48, "3": 15, "4": 127, "5": 67},
}


def merge():
    os.makedirs(os.path.join(RUNS, "improved-merged"), exist_ok=True)
    out = os.path.join(RUNS, "improved-merged", "run.jsonl")
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
    # verify qid set equals the 300 declarations
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
    run_score(os.path.join(RUNS, "improved-merged", "run.jsonl"), out, VARIANT)


def rescore_anchor():
    os.makedirs(SCORES, exist_ok=True)
    out = os.path.join(SCORES, "anchor-rescore.json")
    if os.path.exists(out):
        print(f"skip existing {out}")
        return
    run_score(ANCHOR_RUN, out, VARIANT)


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
    }


def compare():
    new = arm_summary(os.path.join(SCORES, f"{VARIANT}.json"))
    anc = arm_summary(ANCHOR_SCORE)
    resc = None
    resc_path = os.path.join(SCORES, "anchor-rescore.json")
    if os.path.isfile(resc_path):
        resc = arm_summary(resc_path)
    print(f"{'metric':<28}{'anchor(it2)':>12}{'anchor-rescored':>16}{'gate-v2(imp3)':>15}{'delta':>9}")
    def line(label, a, r, b):
        rs = f"{r:.4f}" if r is not None else "-"
        print(f"{label:<28}{a:>12.4f}{rs:>16}{b:>15.4f}{b - a:>+9.4f}")
    line("overall mean F1", anc["overall_mean_f1"], resc and resc["overall_mean_f1"], new["overall_mean_f1"])
    line("overall acc@0.5", anc["overall_acc05"], resc and resc["overall_acc05"], new["overall_acc05"])
    line("cats 1-4 mean F1", anc["cats14_mean_f1"], resc and resc["cats14_mean_f1"], new["cats14_mean_f1"])
    line("cats 1-4 acc@0.5", anc["cats14_acc05"], resc and resc["cats14_acc05"], new["cats14_acc05"])
    names = {"1": "1 multi-hop", "2": "2 temporal", "3": "3 open-domain", "4": "4 single-hop", "5": "5 adversarial"}
    for c in ("1", "2", "3", "4", "5"):
        a, b = anc["by_category"][c], new["by_category"][c]
        assert a["n"] == b["n"], f"category {c} n mismatch {a['n']} vs {b['n']}"
        r = resc and resc["by_category"][c]["mean_f1"]
        rs = f"{r:.4f}" if r is not None else "-"
        print(f"{names[c] + ' mean F1':<28}{a['mean_f1']:>12.4f}{rs:>16}{b['mean_f1']:>15.4f}{b['mean_f1'] - a['mean_f1']:>+9.4f}  (n={a['n']})")
    return new, anc, resc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--rescore-anchor", action="store_true")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    merge()
    if args.rescore_anchor:
        rescore_anchor()
    if args.score:
        score()
    if args.compare:
        compare()
