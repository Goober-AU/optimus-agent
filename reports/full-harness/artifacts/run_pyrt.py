
import subprocess, sys, os, json, time, shutil
repo = r"C:/Users/openclawuser/optimus-fh-20261017"
outdir = os.path.join(repo, "reports/full-harness/artifacts")
tdir = os.path.join(repo, "prime-agent-runtime/test")
env = dict(os.environ)
env.pop("PYTHONPATH", None)
uv = shutil.which("uv") or r"C:\Users\openclawuser\.local\bin\uv.exe"
results = {}
for f in sorted(os.listdir(tdir)):
    if f.startswith("test_") and f.endswith(".py"):
        t0 = time.time()
        try:
            p = subprocess.run([uv, "run", "--locked", "--project", "prime-agent-runtime",
                                "python", "-m", "unittest", "discover",
                                "-s", "prime-agent-runtime/test", "-p", f],
                               cwd=repo, env=env, capture_output=True, text=True, timeout=900)
            rc, err = p.returncode, (p.stderr or "")[-400:]
        except subprocess.TimeoutExpired:
            rc, err = "TIMEOUT", ""
        results[f] = {"rc": rc, "secs": round(time.time()-t0, 1), "tail": err}
        print(f, rc, results[f]["secs"], flush=True)
with open(os.path.join(outdir, "baseline-python-runtime.json"), "w") as fh:
    json.dump(results, fh, indent=2)
print("DONE", {k: v["rc"] for k, v in results.items()})
