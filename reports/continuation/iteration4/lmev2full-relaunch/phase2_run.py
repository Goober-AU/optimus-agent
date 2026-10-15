"""Phase 2 (fixed) for the lmev2full gate: 4 answer runs on the merged warm cache.

Fixes vs v1: (1) typeA uses read-settings.json as --base-settings ONLY (no
--settings overrides -> no write-time-key rejection); (2) direct v1c runs
replicate the controller's copy_store_into_scratch (projects.json alias
rebind) so the copied store resolves instead of an empty new project.
"""
import json, os, shutil, subprocess, sys, glob

QA = r"C:/Users/openclawuser/lmev2full-qa"
WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lmev2full"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
CACHE = os.path.join(ROOT, "reports", "continuation", "iteration4", "ql-typeb-cache", "web--46c8aec059fe9cec")
VENV = os.path.join(ROOT, "repo-continuation", "bench", ".venv", "Scripts", "python.exe")
MODELS = os.path.join(ROOT, "reports", "continuation", "c06-instruction-boundary", "inputs", "runs", "locomo-c06-ib-system_append-q12-s0p1", "envs", "conv-30", "agent", "models.json")
JUDGES = os.path.join(ROOT, "reports", "continuation", "iteration2-inputs", "lme-temporal-flip", "judge-models.json")
SCRATCH_LEDGER = os.path.join(WT, "reports", "continuation", "iteration4-inputs", "lmev2full-scratch-ledger.json")
CTRL = os.path.join(WT, "scripts", "memory_question_loop.py")
DRV = os.path.join(QA, "lmev2_ing3.exe")
READ_SETTINGS = os.path.join(WT, "reports", "continuation", "iteration4-inputs", "manifests", "lme_v2_dev5_full_v4.read-settings.json")

def run(cmd, **kw):
    print("[cmd]", " ".join(str(c) for c in cmd)[:220], flush=True)
    return subprocess.run(cmd, **kw)

def main():
    env = dict(os.environ)
    env["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
    env["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"
    sys.path.insert(0, os.path.join(WT, "scripts"))
    import memory_question_loop as M

    # (1)-(2) controller typeA on scratch ledger: base-settings = read-settings, NO overrides
    for qid, rid in [("f928c075", "lmev2full-f928a"), ("499488a6", "lmev2full-49948a")]:
        ad = os.path.join(QA, rid)
        if not os.path.isdir(ad):
            args = [sys.executable, CTRL, qid,
                    "--ledger", SCRATCH_LEDGER,
                    "--mode", "typeA",
                    "--base-settings", READ_SETTINGS,
                    "--driver", DRV, "--driver-workdir", WT,
                    "--models-json", MODELS, "--judge-models-json", JUDGES,
                    "--bench-venv", VENV, "--scoring-cwd", ROOT,
                    "--attempts-root", QA,
                    "--manifest-dir", os.path.join(ROOT, "reports", "continuation", "iteration3-inputs", "manifests"),
                    "--run-id", rid]
            h = run(args, env=env, capture_output=True, text=True)
            print((h.stdout or "")[-2500:], flush=True)
            print((h.stderr or "")[-500:], flush=True)
            print(f"[typeA {qid}] rc={h.returncode}", flush=True)

    # (3) direct v1c runs with proper store rebind
    qs_rows = {}
    for line in open(os.path.join(ROOT, "reports", "continuation", "lme-v2-comparison-inputs", "private-scoring-questions", "qs_web.jsonl"), encoding="utf-8"):
        r = json.loads(line); qs_rows[r["qid"]] = r
    for qid, rid in [("99b6e22f", "lmev2full-99b6e"), ("a59d50f7", "lmev2full-a59d5")]:
        ad = os.path.join(QA, rid)
        if not os.path.isdir(os.path.join(ad, "envs", "web", "agent")):
            os.makedirs(ad, exist_ok=True)
            # controller-style copy: agent dir + workspace + projects.json rebind
            copy_info = M.copy_store_into_scratch(os.path.join(CACHE, "envs", "web"), ad, "web")
            print(f"[direct {qid}] store copied: {copy_info['copied_files']} files, project {copy_info['project_id']}", flush=True)
            man_p = os.path.join(QA, f"{rid}-manifest.json")
            h = run([DRV, man_p], env={**env, "MEMORY_BENCH_MODELS_JSON": MODELS},
                    capture_output=True, text=True, cwd=WT)
            (open(os.path.join(ad, "driver.log"), "a", encoding="utf-8", newline="\n")
             .write("\n" + h.stdout + "\n" + h.stderr))
            print(f"[direct {qid}] driver rc={h.returncode}", flush=True)
        # question-scoped scoring
        qs_dir = os.path.join(ad, "scoring", "qs-dir")
        os.makedirs(qs_dir, exist_ok=True)
        qr = M.ensure_protocol_annotations(qs_rows[qid], "lme_v2")
        with open(os.path.join(qs_dir, f"qs_{qid}.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(qr) + "\n")
        records = M.extract_question_records(os.path.join(ad, "run.jsonl"), qid)
        with open(os.path.join(ad, "scoring", "run.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        out = os.path.join(ad, "scoring", "scores.json")
        h = run([VENV, "-m", "bench.eval.evaluate", "--bench", "lme_v2",
                 "--run", os.path.join(ad, "scoring", "run.jsonl"), "--variant", "improved",
                 "--qs-dir", qs_dir, "--out", out, "--models-json", JUDGES],
                env={k: v for k, v in env.items() if k != "MEMORY_BENCH_MODELS_JSON"},
                capture_output=True, text=True, cwd=ROOT)
        print(f"[score {qid}] rc={h.returncode}", (h.stdout or "")[-300:], flush=True)
    print("[phase2] done", flush=True)

if __name__ == "__main__":
    main()
