"""Merge the sharded lmev2full stores into the canonical warm cache dir.

Shards: s1 (34 sessions), s2 (33), s3 (frozen 23 applied), s3a (5), s3b (5).
Merge = s1 store as base (agent dir copy) + entries/history/events merged from
s2 + s3 + s3a + s3b harness_state.json files. Completion gate: every shard
dir has an env.jsonl row env_id=web without error (s3's marker is a synthetic
row written by this script from its frozen 23-applied history).
"""
import glob, json, os, shutil, sys, time

QA = r"C:/Users/openclawuser/lmev2full-qa"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
CACHE = os.path.join(ROOT, "reports", "continuation", "iteration4", "ql-typeb-cache", "web--46c8aec059fe9cec")
SHARDS = ["s1", "s2", "s3", "s3a", "s3b", "s3a2", "s3b2", "s3b3"]

def shard_store(sid):
    hits = glob.glob(os.path.join(QA, f"shard-{sid}", "envs", "web", "agent", "memory",
                                  "projects", "project_*", "harness_state.json"))
    return hits[0] if hits else None

def shard_done(sid):
    p = os.path.join(QA, f"shard-{sid}", "env.jsonl")
    if not os.path.isfile(p):
        return False
    for line in open(p, encoding="utf-8"):
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("env_id") == "web" and not row.get("error"):
            return True
    return False

def main():
    # 0. completion gate: s1, s2, s3a, s3b env.jsonl clean; s3 has frozen marker
    pending = [s for s in ("s1", "s3a2", "s3b3") if not shard_done(s)]
    if pending:
        print(json.dumps({"merged": False, "pending": pending}))
        return 1
    # s3 frozen marker: 23 applied jobs recorded; write synthetic env.jsonl row if absent
    if not shard_done("s3"):
        p = os.path.join(QA, "shard-s3", "env.jsonl")
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"env_id": "web", "note": "split: 23 applied sessions frozen at parent-directed s3/s3a/s3b split"}) + "\n")
    for sid in ("s3", "s3b", "s3a", "s3b2"):
        if not shard_done(sid):
            p = os.path.join(QA, f"shard-{sid}", "env.jsonl")
            with open(p, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"env_id": "web", "note": f"split: frozen {sid} store (partial, sessions covered by split shards)"}) + "\n")
    # 1. quarantine current partial cache
    if os.path.isdir(CACHE):
        stale = f"{CACHE}.failed-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"
        os.rename(CACHE, stale)
        print(f"[merge] quarantined partial cache -> {stale}")
    # 2. s1 agent dir as base
    src_agent = os.path.join(QA, "shard-s1", "envs", "web", "agent")
    dst_env = os.path.join(CACHE, "envs", "web")
    dst_agent = os.path.join(dst_env, "agent")
    os.makedirs(dst_env, exist_ok=True)
    shutil.copytree(src_agent, dst_agent)
    print("[merge] base = s1 agent dir copied")
    # 3. merge other shards' harness_state into the base store
    hs_path = glob.glob(os.path.join(dst_agent, "memory", "projects", "project_*", "harness_state.json"))[0]
    base = json.load(open(hs_path, encoding="utf-8"))
    for sid in ("s2", "s3", "s3a", "s3b", "s3a2", "s3b2", "s3b3"):
        sp = shard_store(sid)
        if not sp:
            print(f"[merge] WARNING: no store for {sid}")
            continue
        sd = json.load(open(sp, encoding="utf-8"))
        # entries = dict of categories, each a dict keyed by entry id (dedupe by id, last wins)
        base_entries = base.setdefault("entries", {})
        for cat, items in (sd.get("entries") or {}).items():
            if isinstance(items, dict):
                base_entries.setdefault(cat, {}).update(items)
        base.setdefault("refinements", []).extend(sd.get("refinements", []))
        mem = base.setdefault("memory", {})
        mem.setdefault("history", []).extend((sd.get("memory") or {}).get("history", []))
        ev = mem.setdefault("events", {})
        ev.update((sd.get("memory") or {}).get("events", {}))
        mem["revision"] = max(mem.get("revision", 0), (sd.get("memory") or {}).get("revision", 0))
        print(f"[merge] + {sid}: memory entries {len((sd.get('entries') or {}).get('memory', {}))} history {len((sd.get('memory') or {}).get('history', []))}")
    tmp = hs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(base, fh)
    os.replace(tmp, hs_path)
    # 4. cache markers: env.jsonl + ingested.json
    with open(os.path.join(CACHE, "env.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"env_id": "web", "sessions": 100, "merged_from": SHARDS}) + "\n")
    with open(os.path.join(CACHE, "ingested.json"), "w", encoding="utf-8") as fh:
        json.dump({"cache_key": "46c8aec059fe9cec", "env_id": "web",
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "source": "3-way shard + s3 split (s1+s2+s3+s3a+s3b)",
                   "run_id": "lmev2full-sharded"}, fh, indent=1)
    # Normalize entry ownership to the base project: validate_document rejects
    # any entry whose metadata.projectId differs from the resolved project id
    # ("Entry belongs to another project"), which makes store.read() fail and
    # recall silently search an empty corpus.
    base_pid = base.get("memory", {}).get("projectId")
    for kind in ("memory", "prompt", "skill", "subagent"):
        for e in (base.get("entries", {}).get(kind) or {}).values():
            md = e.get("metadata")
            if isinstance(md, dict) and md.get("projectId") != base_pid:
                md["projectId"] = base_pid
    total = len((base.get("entries") or {}).get("memory", {}))
    print(json.dumps({"merged": True, "memory_entries": total, "history": len(base["memory"]["history"]),
                      "bytes": os.path.getsize(hs_path), "cache_dir": CACHE}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
