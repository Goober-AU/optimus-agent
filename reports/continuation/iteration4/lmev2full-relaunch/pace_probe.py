"""Pace probe v3 for the sharded lmev2full ingest (s1, s2 done; s3 frozen; s3a, s3b live)."""
import glob, json, os, subprocess, time

STATE = r"C:/Users/openclawuser/lmev2full-qa/gate-state.json"
PACE_LOG = r"C:/Users/openclawuser/lmev2full-qa/pace-log.jsonl"

def tasklist_alive(pid, expect):
    h = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                       capture_output=True, text=True, timeout=60)
    body = [l for l in h.stdout.splitlines() if l.startswith('"') and not l.startswith('"Image')]
    name = body[0].split('","')[0].strip('"') if body else "DEAD"
    return name == expect

def main():
    g = json.load(open(STATE, encoding="utf-8"))
    image = g["shards"]["driver_image"]
    live = g["shards"].get("live_drivers", {})   # {"s1": pid, ...}
    done = True
    for sid in ("s1", "s3a2", "s3b3"):
        p = os.path.join(g["shards"]["out_root"], f"shard-{sid}", "env.jsonl")
        ok = False
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8"):
                try: row = json.loads(line)
                except Exception: continue
                if row.get("env_id") == "web" and not row.get("error"): ok = True
        done = done and ok
    alive = {k: tasklist_alive(p, image) for k, p in live.items()}
    entries = bytes_ = 0
    for sid in ("s1", "s2", "s3", "s3a", "s3b", "s3a2", "s3b2", "s3b3"):
        hits = glob.glob(os.path.join(g["shards"]["out_root"], f"shard-{sid}", "envs", "web",
                                      "agent", "memory", "projects", "project_*", "harness_state.json"))
        if hits:
            bytes_ += os.path.getsize(hits[0])
            try:
                entries += len(json.load(open(hits[0], encoding="utf-8")).get("entries", []))
            except Exception:
                pass
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='memory_bench.exe' OR "
          "Name='lmev2_ing3.exe'\" | ForEach-Object { \"{0}\" -f $_.ProcessId }")
    h = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=120)
    mine = set(str(p) for p in live.values())
    siblings = len([t for t in h.stdout.split() if t.strip() and t.strip() not in mine])
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "chain_alive": all(alive.values()), "done": done,
           "entries": entries, "store_bytes": bytes_,
           "alive_detail": alive, "siblings": siblings}
    with open(PACE_LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
    print(json.dumps(row))

if __name__ == "__main__":
    main()
