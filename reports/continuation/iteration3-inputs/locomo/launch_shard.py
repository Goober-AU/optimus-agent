"""Launch one LoCoMo gate-v2 improved shard (detached background process).

Usage: python launch_shard.py s0|s1|s2 [--all]

Starts memory_bench.exe (imp3 build, sha ab1e1474...) on the shard manifest,
fully detached from this interpreter (survives shell/kernel exit). The log
goes to logs/improved-<shard>.log; PIDs are appended to LAUNCHES.json.
"""
import argparse, datetime, hashlib, json, os, subprocess, sys

GATE = os.path.dirname(os.path.abspath(__file__))
CAMPAIGN = os.path.abspath(os.path.join(GATE, "..", "..", "..", "..", ".."))
BINARY = os.path.join(CAMPAIGN, "imp3", "target", "release", "examples", "memory_bench.exe")
MODELS_JSON = os.path.join(CAMPAIGN, "reports", "continuation", "frozen-experiments",
                           "private-config", "models.json")
SHARDS = ("s0", "s1", "s2")
EXPECTED_BINARY_SHA256 = "ab1e1474af636a18b47b8969c22fa1e82cfbd811ad5efe71b30be0122ee169fc"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def launch(shard):
    manifest = os.path.join(GATE, "manifests", f"locomo-gate3-improved-{shard}.json")
    log_path = os.path.join(GATE, "logs", f"improved-{shard}.log")
    for p in (manifest, BINARY, MODELS_JSON):
        if not os.path.isfile(p):
            sys.exit(f"missing: {p}")
    digest = sha256_file(BINARY)
    if digest != EXPECTED_BINARY_SHA256:
        sys.exit(f"binary sha256 mismatch: {digest} != {EXPECTED_BINARY_SHA256}")
    out_dir = json.load(open(manifest, encoding="utf-8"))["output_dir"]
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        sys.exit(f"refusing: output dir not empty (capture protocol forbids resume): {out_dir}")
    env = dict(os.environ)
    env["MEMORY_BENCH_MODELS_JSON"] = MODELS_JSON
    log = open(log_path, "ab", buffering=0)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([BINARY, manifest], stdout=log, stderr=subprocess.STDOUT,
                            cwd=GATE, env=env, creationflags=creationflags, close_fds=True)
    receipt = {
        "shard": shard,
        "manifest": manifest,
        "log": log_path,
        "memory_bench_pid": proc.pid,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "binary_sha256": digest,
    }
    launches_path = os.path.join(GATE, "LAUNCHES.json")
    launches = []
    if os.path.isfile(launches_path):
        try:
            launches = json.load(open(launches_path, encoding="utf-8"))
        except ValueError:
            launches = []
    launches.append(receipt)
    with open(launches_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(launches, f, indent=1)
        f.write("\n")
    print(f"launched {shard}: pid={proc.pid} log={log_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("shard", choices=SHARDS, nargs="?")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    todo = SHARDS if args.all or not args.shard else (args.shard,)
    for s in todo:
        launch(s)
