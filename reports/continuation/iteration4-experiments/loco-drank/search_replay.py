#!/usr/bin/env python3
"""Offline search_ranked replay for the drank campaign (Task 1).

Python reimplementation of crates/pi-coding-agent/src/core/memory/search.rs
(stemmed mode, project-store corpus only; global/shared/session corpora are
empty in the bench env). Verified 50/50 against the binary's search_ranked
on all 7 writecamp fresh stores (stemmed) and conv-26 d4b542a8 (plain mode).

Usage: python search_replay.py <harness_state.json> <query> [top_n]
"""
import functools, json, math, re, sys

WORD_RE = re.compile(r"[\w-]+", re.UNICODE)
SCOPE_ORDER = {"session": 5, "project": 4, "host": 3, "shared": 2, "global": 1}

def words(value):
    seen, out = set(), []
    for m in WORD_RE.finditer(value.lower()):
        w = m.group(0)
        if len(w) >= 2 and w not in seen:
            seen.add(w); out.append(w)
    return out

def stem(word):
    cs = list(word)
    def collapse(sc):
        if len(sc) >= 3 and sc[-1] == sc[-2] and sc[-1] not in "aeiou":
            return sc[:-1]
        return sc
    def has(suf):
        return len(word) >= len(suf) and word[-len(suf):] == suf
    if len(cs) >= 5 and has("ies"): return "".join(cs[:-3]) + "y"
    if len(cs) >= 5 and has("ied"): return "".join(cs[:-3]) + "y"
    if len(cs) >= 6 and has("ing"):
        st = collapse(cs[:-3])
        if len(st) >= 3: return "".join(st)
    if len(cs) >= 5 and has("ed"):
        st = collapse(cs[:-2])
        if len(st) >= 3: return "".join(st)
    if len(cs) >= 4 and has("s") and not has("ss") and not has("us") and not has("as"):
        return "".join(cs[:-1])
    return word

def words_stemmed(value):
    seen, out = set(), []
    for w in words(value):
        s = stem(w)
        if s not in seen:
            seen.add(s); out.append(s)
    return out

def build_corpus(hs, project_id, stemming=True):
    entries = []
    for kind, bucket in hs.get("entries", {}).items():
        for eid, e in bucket.items():
            md = e.get("metadata") or {}
            if md.get("projectId") not in (None, project_id): continue
            host = md.get("hostId")
            if host and host != "host": continue
            active = not ("supersededBy" in md or md.get("status") == "superseded" or "detached" in md)
            combined = "{} {} {}".format(e.get("title",""), e.get("path",""), eid)
            title = words_stemmed(combined) if stemming else words(combined)
            body = words_stemmed(e.get("content") or "") if stemming else words(e.get("content") or "")
            cues = md.get("prospectiveCues")
            if isinstance(cues, list):
                for cue in cues:
                    if isinstance(cue, str):
                        for w in words(cue):
                            body.append(stem(w) if stemming else w)
            span_src = md.get("sourceSpan")
            span = set(stem(w) for w in words(span_src)) if (stemming and isinstance(span_src, str) and span_src.strip()) else (set(words(span_src)) if isinstance(span_src, str) and span_src.strip() else set())
            entries.append({"id": "project:%s:%s" % (kind, eid), "eid": eid, "entry": e,
                            "scope": "host" if host else "project", "active": active,
                            "title": set(title), "body": set(body), "span": span,
                            "created_at": e.get("created_at", "")})
    df_active, active_count = {}, 0
    for ent in entries:
        if ent["active"]: active_count += 1
        for t in ent["title"] | ent["body"]:
            df_active[t] = df_active.get(t, 0) + (1 if ent["active"] else 0)
        for t in ent["span"]:
            df_active[t] = df_active.get(t, 0) + (1 if ent["active"] else 0)
    return {"entries": entries, "df_active": df_active, "active_count": active_count}

def _cmp(a, b):
    if a["score"] != b["score"]: return -1 if a["score"] > b["score"] else 1
    if a["created_at"] != b["created_at"]: return -1 if a["created_at"] > b["created_at"] else 1
    if SCOPE_ORDER[a["scope"]] != SCOPE_ORDER[b["scope"]]:
        return -1 if SCOPE_ORDER[a["scope"]] > SCOPE_ORDER[b["scope"]] else 1
    return -1 if a["id"] < b["id"] else (1 if a["id"] > b["id"] else 0)

_CMP = functools.cmp_to_key(_cmp)

def search(corpus, query, stemming=True):
    terms = words_stemmed(query) if stemming else words(query)
    N = corpus["active_count"]
    idf = [math.log((N + 1.0) / (corpus["df_active"].get(t, 0) + 1.0)) + 1.0 for t in terms]
    denom = sum(idf)
    hits = []
    for ent in corpus["entries"]:
        if not ent["active"]: continue
        num, matched = 0.0, []
        for i, t in enumerate(terms):
            if t in ent["title"]: wgt = 3.0
            elif t in ent["body"]: wgt = 1.0
            elif t in ent["span"]: wgt = 0.5
            else: continue
            matched.append(t); num += wgt * idf[i]
        if terms and not matched: continue
        hits.append({"id": ent["id"], "eid": ent["eid"], "title": ent["entry"].get("title", ""),
                     "score": (num / denom) if denom > 0 else 0.0, "matched": matched,
                     "created_at": ent["created_at"], "scope": ent["scope"]})
    hits.sort(key=_CMP)
    return hits

if __name__ == "__main__":
    hs = json.load(open(sys.argv[1], encoding="utf-8"))
    pid = None
    import glob, os
    pid = os.path.basename(os.path.dirname(os.path.abspath(sys.argv[1])))
    corpus = build_corpus(hs, pid, stemming=True)
    top = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    for i, h in enumerate(search(corpus, sys.argv[2], stemming=True)[:top], start=1):
        print(i, round(h["score"], 4), h["id"], "|", h["title"][:60], "|", h["matched"])
