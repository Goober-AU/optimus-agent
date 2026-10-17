
import subprocess, sys, os, json, time
repo = r"C:/Users/openclawuser/optimus-fh-20261017"
outdir = os.path.join(repo, "reports/full-harness/artifacts")
os.makedirs(outdir, exist_ok=True)
results = {}
for f in sorted(os.listdir(os.path.join(repo, "tests"))):
    if f.startswith("test_") and f.endswith(".py"):
        t0 = time.time()
        p = subprocess.run([sys.executable, "-m", "unittest", f"tests.{f[:-3]}"],
                           cwd=repo, capture_output=True, text=True, timeout=600)
        results[f] = {"rc": p.returncode, "secs": round(time.time()-t0, 1),
                      "tail": p.stderr[-500:]}
        print(f, p.returncode, results[f]["secs"], flush=True)
with open(os.path.join(outdir, "baseline-python-root.json"), "w") as fh:
    json.dump(results, fh, indent=2)
print("DONE", {k: v["rc"] for k, v in results.items()})
