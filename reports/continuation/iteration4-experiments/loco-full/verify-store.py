#!/usr/bin/env python3
"""Verify the fresh conv-26 typeB store: attribute notes, KU notes, entry count."""
import json, os, sys, glob

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
cache_dir = sys.argv[1] if len(sys.argv) > 1 else ROOT + "/reports/continuation/iteration4/ql-typeb-cache/conv-26--d4b542a8e98deca6"
frozen_dir = ROOT + "/imp3-gate/reports/continuation/iteration3-inputs/locomo/runs/improved-s0/envs/conv-26"

def load_mem(env_dir):
    hs_path = glob.glob(env_dir + "/agent/memory/projects/project_*/harness_state.json")[0]
    hs = json.load(open(hs_path, encoding="utf-8"))
    return hs["entries"]["memory"], hs

fresh, _ = load_mem(cache_dir + "/envs/conv-26")
frozen, _ = load_mem(frozen_dir)
print("FRESH entries:", len(fresh), " | FROZEN entries:", len(frozen))

# 1) attribute notes (strength/motivation quotes from session 18)
attr_keys = ["strength", "motivation", "support"]
mel_attr = {}
for t, d in fresh.items():
    c = (d.get("content") or "")
    tl = t.lower()
    if "melanie" in tl and any(k in tl or k in c.lower() for k in attr_keys):
        mel_attr[t] = c[:160]
print("\nMelanie strength/motivation/support notes (fresh):", len(mel_attr))
for t, c in sorted(mel_attr.items()):
    print("  --", t, "|", c)

# gold statement check: session-18 quotes must be present
must = ["give me the strength to keep going", "biggest motivation and support"]
for m in must:
    hit = [t for t, d in fresh.items() if m in (d.get("content") or "")]
    print(f"quote '{m}':", "FOUND in " + str(hit) if hit else "MISSING")

# 2) KU update entries (never-merge clause): look for update-shaped titles
ku_like = [t for t in fresh if any(w in t.lower() for w in ("update", "now_", "team_of", "postcards"))]
print("\nKU-shaped titles (fresh):", len(ku_like), ku_like[:10])

# 3) frozen-set overlap
fo = set(frozen) & set(fresh)
print("\noverlap with frozen:", len(fo), "/", len(frozen))
new = set(fresh) - set(frozen)
print("new titles:", len(new))
gone = set(frozen) - set(fresh)
print("dropped titles:", len(gone))
