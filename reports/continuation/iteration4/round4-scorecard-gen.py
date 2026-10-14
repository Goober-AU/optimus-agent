#!/usr/bin/env python3
"""Round-4 scorecard generator (evidence worker, worktree imp4b-evidence).

Read-only over the campaign QUESTION-LEDGER + committed experiment artifacts;
writes reports/continuation/iteration4/ROUND4-SCORECARD.md in this worktree.
"""
import json, os, statistics, importlib.util
from collections import Counter, defaultdict

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + r"/imp4b-evidence"
LSW = ROOT + r"/imp4b-locosweep/reports/continuation/iteration4-experiments/loco-sweep"
EXP = WT + r"/reports/continuation/iteration4-experiments"
OUT = WT + r"/reports/continuation/iteration4/ROUND4-SCORECARD.md"

spec = importlib.util.spec_from_file_location("build_report", os.path.join(LSW, "build-report.py"))
br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)

L = json.load(open(os.path.join(ROOT, "reports/continuation/iteration4/QUESTION-LEDGER.json"), encoding="utf-8"))
GATE = json.load(open(os.path.join(ROOT, "reports/continuation/iteration4/QUESTION-LEDGER-locosweep-gate.json"), encoding="utf-8"))
SNAP_TS = max((a["ts"] for e in L["entries"] for a in (e.get("attempts") or [])), default="n/a")

def setcls(rec, cl, why):
    rec["class"] = cl; rec["why"] = why

def classify_all():
    out = {}
    c26_fixed = {"q1","q18","q26","q33","q39","q54","q55","q126","q135","q141","q143","q144","q149"}
    c26_partial = {"q2": "0.0->0.286, bar unmet", "q37": "frozen-store stem 1.0 vs fresh-stack 0.0 (store-band)",
                   "q99": "0.483-0.512 variance band straddling the 0.5 bar"}
    c26_open = {"q66": ("OPEN-write-time", "write-time extraction gap (loco-full2 residual map)"),
                "q77": ("OPEN-answer", "open-domain inference policy")}
    special = {
        "conv-42_q4": ("DATASET-LIMIT", "gold tokens absent from the transcript (writeres scan)"),
        "conv-47_q102": ("FIXED", "attribute clause v2 typeB re-ingest 0.8889 (writeres)"),
        "conv-41_q50": ("OPEN-write-time", "attribute-statement class; gold cov 0.33, never extracted"),
        "f928c075": ("FIXED", "comparative-uniqueness clause 1.0 x3 (lmev2-q3)"),
        "499488a6": ("OPEN-write-time", "needs full-env cap-40k re-ingest (lmev2q2)"),
        "lp_232": ("FIXED", "E3 read-time config median 1.0 2/3 (lpq232 + e3val)"),
        "lp_118": ("PARTIAL", "E3 median 1.0 2/3 frozen; full-stack 1/3 fresh; rerank parse-flake bound"),
        "00ca467f": ("FIXED", "update-drop fix + re-ingest 1.0 (updatedrop)"),
    }
    for e in L["entries"]:
        if e["status"] != "FAILED":
            continue
        qid, bench = e["qid"], e["benchmark"]
        atts = e.get("attempts") or []
        best_a = max(atts, key=lambda a: (a.get("score") if a.get("score") is not None else -1)) if atts else None
        rec = {"benchmark": bench, "baseline": e.get("baseline"), "attempts": len(atts),
               "best": best_a.get("score") if best_a else None,
               "best_run": best_a.get("run_id") if best_a else None,
               "best_worker": best_a.get("worker") if best_a else None, "class": None, "why": ""}
        if bench == "locomo" and qid.startswith("conv-26_"):
            q = "q" + qid.split("_q")[1]
            if q in c26_fixed:
                setcls(rec, "FIXED", "FINAL8 battery median >= 0.5 (loco-timeform gate-table)")
            elif q in c26_partial:
                setcls(rec, "PARTIAL", c26_partial[q])
            else:
                setcls(rec, c26_open[q][0], c26_open[q][1])
        elif qid in special:
            setcls(rec, special[qid][0], special[qid][1])
        elif bench == "locomo":
            sw = [a for a in atts if a.get("worker") == "locosweep"]
            if not sw:
                setcls(rec, "OPEN-unattempted", "no round-4 attempts yet (sweep pending)")
            else:
                latest = sw[-1]
                sc = latest.get("score") or 0
                bl = e.get("baseline") or 0
                rec["sweep_score"] = sc
                if sc >= 0.5:
                    setcls(rec, "FIXED", "generalization sweep (det 24/24000 + stem + FINAL7, frozen store) %.3f" % sc)
                elif 0.3 <= sc < 0.5 and sc > bl:
                    setcls(rec, "PARTIAL", "near-miss %.3f (baseline %.3f), bar unmet" % (sc, bl))
                else:
                    conv = int(qid.split("_")[0].split("-")[1])
                    cl2 = br.classify_bdG(qid, e, latest, br.store_notes(conv), br.qs_records(conv)[qid])
                    setcls(rec, "OPEN-" + {"B": "write-time", "D": "retrieval", "G": "answer"}[cl2["stage"]],
                           "sweep %.3f; stage %s (gold_in_window=%s, store_cov=%.2f)" % (sc, cl2["stage"], cl2["in_window"], cl2["cov"]))
        else:
            setcls(rec, "OPEN-unattempted", "no attempts")
        out[qid] = rec
    return out

CLS = classify_all()
bybench = defaultdict(list)
for qid, rec in CLS.items():
    bybench[rec["benchmark"]].append((qid, rec))

def bq(bench, cl):
    return sorted(q for q, r in bybench[bench] if r["class"] == cl)

def fmt(x):
    return ("%.3f" % x) if isinstance(x, (int, float)) else str(x)

# ------- aggregates -------
cls_counts = Counter(r["class"] for r in CLS.values())
total_failed = len(CLS)
status_counts = Counter(e["status"] for e in L["entries"])

# sweep gate tallies
gate_attempts = [(e["qid"], a.get("score"), a.get("outcome")) for e in GATE["entries"] for a in (e.get("attempts") or [])]
gate_pass = sum(1 for _, s, o in gate_attempts if o == "pass")

# sweep coverage
sweep_rows = [(q, r) for q, r in CLS.items() if r["benchmark"] == "locomo" and not q.startswith("conv-26_") and "sweep" in r["why"] or (r["benchmark"] == "locomo" and not q.startswith("conv-26_") and r.get("sweep_score") is not None)]
sweep_n = sum(1 for q, r in bybench["locomo"] if r.get("sweep_score") is not None or (r["class"] in ("FIXED","PARTIAL","OPEN-retrieval","OPEN-write-time","OPEN-answer") and not q.startswith("conv-26_") and q not in ("conv-42_q4","conv-47_q102","conv-41_q50")))

lines = []
A = lines.append
A("# ROUND-4 SCORECARD — question-ledger re-tally + final evidence package")
A("")
A("Generated by the round-4 evidence worker (worktree `imp4b-evidence`, branch `improve/mem4b-evidence`,")
A("base = imp4 HEAD 72a295fc6 after the FINAL8 merge). Read-only re-tally of the existing")
A("QUESTION-LEDGER attempts + committed experiment artifacts; no baseline run data or attempt history")
A("was modified. Fleet binary: `imp4-targetR` sha256 b57dc607 (post-merge, v0.1.29 + PR #144).")
A("")
A("**Ledger snapshot: last attempt ts %s.**" % SNAP_TS)
A("Live workers at snapshot time: `locosweep` (LoCoMo generalization sweep, worker-owned ledger),")
A("`lmesseq` (LME-S sequential queue). Their appended attempts are included; counts below are the")
A("snapshot truth, not a final round-4 close-out.")
A("")
A("## 1. Headline — every FAILED question classified")
A("")
A("The ledger was built with 180 FAILED at round-4 kickoff. Workers flipped 36fe48ef and conv-26_q57")
A("to RESOLVED and lp_232 RESOLVED->FAILED (judge-lucky draw disproven at the milestone gate), so the")
A("current queue is **%d FAILED + %d RESOLVED + %d UNTESTED** (=%d entries)." % (status_counts["FAILED"], status_counts["RESOLVED"], status_counts["UNTESTED"], sum(status_counts.values())))
A("(Task brief said 178; the delta is the two status flips above landing after the brief was written.)")
A("")
A("| class | count | meaning |")
A("|---|---:|---|")
A("| FIXED | %d | >= 0.5 under a validated general config, regression-clean |" % cls_counts["FIXED"])
A("| PARTIAL | %d | converted but bar unmet (0.3-0.5) or variance/store-band |" % cls_counts["PARTIAL"])
A("| OPEN-retrieval (D) | %d | gold in store, recall window/ranking misses it |" % cls_counts["OPEN-retrieval"])
A("| OPEN-write-time (B) | %d | gold never extracted — the re-ingest campaign lane |" % cls_counts["OPEN-write-time"])
A("| OPEN-answer (G) | %d | recall contains the evidence, answer stage fails |" % cls_counts["OPEN-answer"])
A("| DATASET-LIMIT | %d | gold absent from the source transcript (q4 class) |" % cls_counts["DATASET-LIMIT"])
A("| OPEN-unattempted | %d | no round-4 attempts yet |" % cls_counts["OPEN-unattempted"])
A("| **total** | **%d** | |" % total_failed)
A("")

# ------- per-benchmark sections -------
def bench_section(bench, title):
    A("### %s" % title)
    A("")
    cc = Counter(r["class"] for _, r in bybench[bench])
    A("FAILED queue: %d questions. Round-4 classes: %s." % (len(bybench[bench]),
        ", ".join("%s %d" % (k, v) for k, v in sorted(cc.items()))))
    return cc

# LoCoMo
cc = bench_section("locomo", "2.1 LoCoMo (300-q dev set; 175 FAILED at round start)")
A("")
core_fixed = ["conv-26_q1","conv-26_q18","conv-26_q26","conv-26_q33","conv-26_q39","conv-26_q54","conv-26_q55","conv-26_q126","conv-26_q135","conv-26_q141","conv-26_q143","conv-26_q144","conv-26_q149","conv-43_q71","conv-49_q52","conv-41_q35","conv-47_q102"]
sweep_only = len([q for q in bq("locomo", "FIXED") if q not in core_fixed])
A("**Questions fixed this round: %d** — core validated set %d (multi-draw battery/stemsweep/writeres" % (len(bq("locomo","FIXED")), len(core_fixed)))
A("evidence) + generalization-sweep converts %d (n=1 draws under the promoted read config)." % sweep_only)
A("")
A("- conv-26 (the battery conversation, fresh typeB full store d4b542a8, FINAL8 stack):")
A("  %s — all validated in ONE config (loco-full2 + loco-timeform batteries)." % ", ".join(q.split("_")[1] for q in bq("locomo","FIXED") if q.startswith("conv-26_")))
A("- Other conversations (frozen stores, promoted read config det 24/24000 + recallQueryStemming +")
A("  FINAL7 instruction; locosweep generalization sweep): %d fixed" % len([q for q in bq("locomo","FIXED") if not q.startswith("conv-26_")]))
A("  (%s)." % ", ".join(q for q in bq("locomo","FIXED") if not q.startswith("conv-26_")))
A("- Plus conv-26_q57 flipped RESOLVED by the worker (FINAL8: 0.0 -> 0.6 3/3, zero ISO leaks).")
A("")
A("**Validated configs (exact settings files):**")
A("- Full stack (write+read, PR evidence): `reports/continuation/iteration4-experiments/loco-timeform/fullstack-final8-stem.json`")
A("  (answer instruction `loco-timeform/FINAL8.json`; store = fresh typeB re-ingest, KU fact-update +")
A("  attribute-v2 importInstructions, warm cache d4b542a8e98deca6).")
A("- Read-only on frozen stores (sweep config): `reports/continuation/iteration4-experiments/loco-sweep/typea-final7-stem.json`")
A("  = loco-full `base-det-settings.json` + `recallQueryStemming: true` + loco-ans2 `FINAL7.json`.")
A("")
A("**Battery result (18-q comparison set, no q143): FINAL8 mean 0.6386 vs baseline 0.1260 = +0.5126**")
A("(recomputed from `loco-timeform/gate-table.json`; loco-full2 was +0.4962, frozen det +0.296).")
A("Sweep regression gate: %d/%d previously-passing spot-checks held; ONE regression — conv-44_q10" % (gate_pass, len(gate_attempts)))
A("1.0 -> 0.0 (answer-stage 'not mentioned'; the only observed regression of the promoted read config")
A("on a previously-correct question; awaiting the locosweep worker's diagnosis).")
A("")
A("**Residuals:** %s." % ", ".join("%s %d" % (k.replace("OPEN-",""), v) for k, v in sorted(cc.items()) if k.startswith("OPEN") or k in ("PARTIAL","DATASET-LIMIT")))
A("- OPEN-answer (G, %d) is the dominant residual: content-completion misses, over-combination," % cc["OPEN-answer"])
A("  inference-policy ('not mentioned' where gold is in-window), and near-miss phrasing (0.3-0.5 band).")
A("- OPEN-write-time (B, %d) + the attribute class: rides the re-ingest campaign (KU + attribute-v2 clauses)." % cc["OPEN-write-time"])
A("- OPEN-retrieval (D, %d): gold below the 24-entry window (window-32 is a documented trap on q135; needs rank work, not width)." % cc["OPEN-retrieval"])
A("- DATASET-LIMIT (1): conv-42_q4 — gold requires world-knowledge synthesis; deprioritized.")
A("")
A("**Next lever:** (1) the full-population typeB re-ingest with the write clauses (kills the B-class);")
A("(2) answer-stage iteration on the G-class (largest bucket) — FINAL8 already nails form; the misses are")
A("content selection; (3) a rank-side fix for the D-class (per-question recall-width policy or better")
A("tie-break; widening destabilizes q135).")
A("")

# LME-S
bench_section("longmemeval_s", "2.2 LME-S (498-q queue; smoke5 2/5 at round start)")
A("")
A("- **00ca467f FIXED** (the round's write-time flagship): root cause = import update proposal silently")
A("  dropped its edits (`edits: []` -> apply wrote nothing); fixed by the update-drop code fix + typeB")
A("  re-ingest -> 1.0 (gold 'Dr. Smith Mar 3' now in store). Sealed smoke5 store still lacks the fact;")
A("  rides the re-ingest campaign.")
A("- RESOLVED regression set: 01493427 + 031748ae (iteration-3 KU instruction).")
A("- Sequential queue (official order): Q1 031748ae_abs FIXED 1.0 (role/title entity-binding clause,")
A("  regression gate 4/4); Q2 06878be2 passed 1.0 on first attempt with the banked config (queue")
A("  question, not a FAILED-question fix; lmesseq worker, live at snapshot). Queue next: 06db6396;") 
A("  resume via `run_lmes_seq.py` (wrapper handles bare-qid S/M disambiguation + captureProtocol).")
A("")
A("**Validated config:** `reports/continuation/iteration4-experiments/lmes-seq/ov-banked.json`")
A("(KU importInstructions + role/title + comma-list + supersession answer clauses).")
A("")
A("**Residuals:** 1 FAILED (fixed); 495 UNTESTED queue. **Next lever:** resume the sequential queue —")
A("per-question full env ingestion is the cost (~2-4h each); the question-side stack is now stable.")
A("")

# LME-M
A("### 2.3 LME-M (499-q queue; no FAILED yet — never scored)")
A("")
A("- 499 UNTESTED, 0 round-4 attempts. The bc8a6e93_abs typeB ingest was killed ~2h in at the full stop;")
A("  content-addressed cache `ql-typeb-cache/bc8a6e93_abs--8abf2d78200defe2` exists but the capture")
A("  protocol forbids resuming partial output -> cold restart ~4h.")
A("- Regression reference 15745da0_abs: 1.0 on targetR (v1c env-1q; known_correct_not_ledgered).")
A("")
A("**Next lever:** restart the bc8a6e93_abs ingest, then the dev5 remainder; same banked question stack as LME-S.")
A("")

# LMEv2
cc = bench_section("lme_v2", "2.4 LMEv2 (dev5 2/5 at round start; static-environment web)")
A("")
A("- **36fe48ef RESOLVED** (worker-flipped): page-structure clause + no-inferred-permissions + conditional")
A("  answer selection -> 1.0 ('six'); full evidence committed under `reports/continuation/iteration4-inputs/runs/lmev2q1_*`.")
A("- **f928c075 FIXED**: comparative-uniqueness answer clause -> 1.0 x3 (`lmev2-q3/summary.json`).")
A("- 499488a6 OPEN-write-time: mini-env unanswerable; needs the full 100-session env re-capture at")
A("  captureAxtreeMaxChars 40000 (v4 manifest salvaged: `ad4309cf1`).")
A("")
A("**Validated config:** `reports/continuation/iteration4-inputs/manifests/lmev2q1_h3_fid_v2.json` +")
A("`lmev2-q3/h2-compare-instruction.json` (mini cap-40k env).")
A("")
A("**Residuals:** 1 OPEN-write-time. **Next lever:** the full-env cap-40k re-ingest (~34h at 15K chunks,")
A("restart from zero per capture protocol) — the re-ingest campaign's longest lane; it unblocks 499488a6")
A("and the full-run decision.")
A("")

# LP
cc = bench_section("locomo_plus", "2.5 LoCoMo-Plus (dev5 0.8 after the judge fix)")
A("")
A("- **lp_232 FIXED**: E3 (pool-500 raw-query rerank, distill/expansion OFF) -> median 1.0 2/3;")
A("  regressions lp_18/lp_159/lp_69 = 1.0. (The original dev5 1.0 was a judge-lucky draw — flipped")
A("  RESOLVED->FAILED at the milestone gate, then genuinely fixed at E3.)")
A("- **lp_18 RESOLVED** (eval_error rescored 1.0; judge max_tokens root cause).")
A("- **lp_118 PARTIAL**: E3 median 1.0 (2/3) on the frozen store; full-stack fresh-store arm 1/3 — the")
A("  residual is the rerank output-format parse flake (invalid_output -> lexical fallback), NOT")
A("  budgets/cues/extraction/narration (all validated fixed).")
A("")
A("**Fleet decision (validated): E3 read-time config** — `lp-q232-settings-e3-pool500-raw.json`:")
A("`recallRerankCandidates: 500, recallQueryExpansion: false, recallQueryDistillation: false` over the")
A("v3 LP base, with LP_JUDGE_MAX_TOKENS >= 4096. Blind 20-sample x2: base 0.45 -> E3 0.925 (+0.475),")
A("10/11 base-failures fixed, 9/9 base-passes held, 0 pair-mean regressions (`lp-e3-fleet-validation.md`,")
A("recomputed from `lp-e3-fleet-results.json`). Cue-independent (0%-cue base stores).")
A("")
A("**Residuals:** lp_118 parse-flake class (rerank used 80%; 5/8 flakes still 1.0 via lexical fallback);")
A("lp_146-class semantic-bridge misses (0/2 in the sample). **Next lever:** rerank invalid_output")
A("bounded-retry code fix (candidate in PLAN); optional fresh cue-backed re-ingest (~8h/5 stores)")
A("kills flake exposure and attacks lp_146.")
A("")

# code fixes
A("## 3. Code fixes shipped upstream — PR #144 (merged, v0.1.29)")
A("")
A("| # | fix | commit | validation |")
A("|---|---|---|---|")
A("| 1 | update-drop: cited update proposals convert to CREATEs (`imported_<sha24>`, updateTargetId metadata); never silent | 9e4174952 | 00ca467f 0.0 -> 1.0; 128/128 memory:: tests |")
A("| 2 | recall-helper budgets 64/256/2048 -> 2048/2048/4096 (hidden-reasoning starvation) | ad5106de6 | distill 17/18 empty -> 10/10 used; targetR gate 8/9 GREEN |")
A("| 3 | content-aware rerank `recallRerankWithContent` + `recallRerankContentChars` (opt-in, default off) | 4540f8ac1 | q135-class gold selected 5/6 vs 0/4 title-only |")
A("| 4 | `recallQueryStemming` Porter-lite, symmetric query/index (default false) | e9f469fe4 | q18 1.0 x2; 5/9 deep-rank golds convert; 12-cell gate holds |")
A("| 5 | store settings plumbing incl. `recallRerankCandidates` bound 1..500 (E3 pool) | 4540f8ac1 | E3 fleet validation +0.475 |")
A("| 6 | controller `memory_question_loop.py` + protocol-annotation unblock (998 LME-S/M rows runnable) | 0dc38b2e6 + c8b84e401 | 31/31 tests; every ledger attempt this round |")
A("")
A("Upstream landed as ada9f7ac9 in v0.1.29 + maintainer rework ad0969925 (stem-aware CorpusIndex,")
A("bit-identical) + PR #145 perf (cached parsed store). Tests on the merged branch: lib memory::")
A("138/138, example memory_bench 75/75.")
A("")

# honesty pass
A("## 4. Claims-vs-artifacts honesty pass (PLAN.md round-4 sections)")
A("")
A("Every round-4 claim was checked against a committed artifact. Verified exactly (recomputed, not")
A("trusted): FINAL8 aggregate +0.5126 and FULL2 +0.4962 (`loco-timeform/gate-table.json`,")
A("`loco-full2/gate-table.json`); E3 0.45 -> 0.925, 10/11 fixed, 9/9 held (`lp-e3-fleet-results.json`);")
A("q102 0.8889 (`write-residue/q102-wrq102-attrv2-scores.json`); stem sweep 5/9 converts + 12 gate cells")
A("(`stem-sweep/RESULTS.md`); h2h det +0.296 vs fleet +0.241, q135 fleet regression (`loco-h2h/RESULTS.md`,")
A("ledger `h2h_log`); lp_18/lp_232 rescoring (ledger `rescore_log` + `rescores/lp18-lp232/`).")
A("")
A("Gaps found (all minor / live):")
A("")
A("1. **lmev2q2 (499488a6) has no experiment summary** — PLAN's diagnosis details ('Hide this forum'")
A("   appears 13x in raw axtrees but 0/23 distilled entries') are backed only by 6 ledger attempts +")
A("   run dirs under `ql-attempts/`, not a committed summary file.")
A("2. **rerank-content has settings + ledger attempts but no summary.json/RESULTS.md** — the 5/6 vs 0/4")
A("   and pool-200 thinking-cliff numbers are reconstructible from the ledger but not written up.")
A("3. **Live-worker results not yet in PLAN.md** (expected): the locosweep generalization numbers and")
A("   lmesseq Q2 (06878be2, 1.0) exist only in the ledger until those workers commit their reports.")
A("4. Task brief's '178 FAILED' vs 179 actual: two worker status flips (above) landed after the brief.")
A("5. **Sweep gate regression (live signal):** conv-44_q10, previously 1.0, scored 0.0 under the")
A("   promoted read config in the locosweep spot-check gate (11/12 held). It is NOT a FAILED-queue")
A("   question, so it is outside the classification above; the locosweep worker's report will own the")
A("   diagnosis. The sweep converts are n=1 draws under this same config — treat near-bar converts")
A("   with repeat-run confirmation before promotion claims.")
A("")
A("No claim was found WITHOUT any artifact; the gaps above are missing write-ups or live-worker")
A("results, not missing data.")
A("")

# next levers
A("## 5. Decision points (for the round-4 report / next round)")
A("")
A("1. **Upstream-PR call (the user's decision): PR #144 is merged in v0.1.29; the campaign branch's")
A("   remaining delta vs main is evidence-only.** The LoCoMo full-stack claim (aggregate +0.513 on the")
A("   battery) is the strongest single number; LME-S/M queue work is the natural round-5 lane.")
A("2. **One re-ingest campaign, three benchmarks (the B-class lane):** LoCoMo full-population typeB")
A("   re-ingest (KU + attribute v2), LMEv2 full-env cap-40k (~34h), LP fresh cue-backed (~8h/5 stores).")
A("   Kills the write-time residuals without new code.")
A("3. **Two code-fix candidates queued:** rerank invalid_output bounded retry (LP parse-flake class;")
A("   bounds lp_118/lp_230-class variance) and temporal-anchor query boost (D-class).")
loco_g = sum(1 for _, r in bybench["locomo"] if r["class"] == "OPEN-answer")
A("4. **Answer-stage (G) is the largest LoCoMo residual class (%d questions)** — a round-5" % loco_g)
A("   answer-instruction loop over the sweep's non-converts has the most headroom.")
A("")
A("---")
A("")
A("*Classification artifact: this file was generated from the ledger snapshot at %s; the per-question*" % SNAP_TS)
A("*class table is reproducible from the ledger + the build-report classifier in the locosweep worktree.*")

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("wrote", OUT)
print("classes:", dict(cls_counts))
for b in sorted(bybench):
    print(b, dict(Counter(r["class"] for _, r in bybench[b])))
