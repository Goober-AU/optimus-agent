"""Deterministic reproduction of the Knowledge Triage repeated-compaction
stability experiment (their experiments/multiturn_stability.py).

Arms:
  typecompact    - their reference operator loop, verbatim semantics
  naive_head     - deterministic substitute for their LLM vanilla arm:
                   their own hierarchical_summarize proxy (keep first items)
  naive_tail     - their temporal_window proxy (keep last items); closest
                   analogue of a keep-recent-tail policy
  naive_truncate - pure character truncation (their truncate_to_tokens alone)

All arms are deterministic and offline. Their vanilla arm used
openai:gpt-5.4-mini and claude_code:claude-sonnet-4-6 LLM calls; those numbers
are NOT reproduced here and are carried in the output JSON only as a
clearly-labelled reference block copied from their committed results file.

Run from the worktree root:
  python research/context_rot_r2/repro/typecompact_repro.py
Optional:
  python research/context_rot_r2/repro/typecompact_repro.py --n all --rounds 10

Requires only the standard library plus the cloned knowledge-triage repo on
sys.path (auto-located). Missing third-party imports used by the package
(tenacity, openai) are stubbed before import; no code path that would use
them is executed.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import statistics
import sys
import types
from typing import Optional

# --------------------------------------------------------------------------
# Locate the cloned knowledge-triage repo (no install, no network).
# --------------------------------------------------------------------------

CANDIDATES = [
    os.environ.get("KT_REPO"),
    str(pathlib.Path(__file__).resolve().parents[3] / "refs" / "cikm26-knowledge-triage"),
    "C:/Users/openclawuser/optimus-rot2-w5-research/refs/cikm26-knowledge-triage",
]


def find_repo() -> pathlib.Path:
    for c in CANDIDATES:
        if c and (pathlib.Path(c) / "knowledge_triage" / "operators.py").exists():
            return pathlib.Path(c)
    raise SystemExit("knowledge-triage repo not found; set KT_REPO or clone into refs/")


KT_ROOT = find_repo()
sys.path.insert(0, str(KT_ROOT))


def _install_stubs() -> None:
    """Stub third-party modules the package imports but this repro never uses."""
    import importlib.util

    if importlib.util.find_spec("tenacity") is None:
        tenacity = types.ModuleType("tenacity")

        def _no_op_decorator(*args, **kwargs):
            def wrap(fn):
                return fn
            return wrap

        tenacity.retry = _no_op_decorator
        tenacity.stop_after_attempt = lambda *a, **k: None
        tenacity.wait_exponential = lambda *a, **k: None
        sys.modules["tenacity"] = tenacity

    if importlib.util.find_spec("openai") is None:
        openai_stub = types.ModuleType("openai")

        class _Unavailable:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("offline stub: OpenAI client unavailable")

        openai_stub.OpenAI = _Unavailable
        sys.modules["openai"] = openai_stub


_install_stubs()

from knowledge_triage import KnowledgeBase, KnowledgeType  # noqa: E402
from knowledge_triage.operators import (  # noqa: E402
    hierarchical_summarize,
    temporal_window,
    type_compact,
)
from knowledge_triage.preservation import constraint_preserved  # noqa: E402

SAMPLE_DIR = KT_ROOT / "data" / "sample"
THEIR_RESULTS = KT_ROOT / "results" / "multiturn_stability.json"

REPORT_CYCLES = (1, 3, 5, 8, 10)


# --------------------------------------------------------------------------
# Corpus loading: mirrors experiments/_helpers.build_kb_e2 segmentation rules,
# but classification uses their offline regex baseline (classifier.py), not the
# LLM-adjudicated cascade behind the committed paper numbers.
# --------------------------------------------------------------------------

def build_kb_e2_regex(path: pathlib.Path) -> Optional[KnowledgeBase]:
    import re

    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return None
    if len(text) < 800 or len(text) > 30000:
        return None
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.fullmatch(r"[-=*_~`|]{2,}", line):
            continue
        if line.startswith("```"):
            continue
        if line.startswith("- ") or line.startswith("* "):
            line = line[2:].strip()
        if len(re.findall(r"[A-Za-z]{2,}", line)) < 3:
            continue
        items.append(line)
    if len(items) < 10:
        return None
    return KnowledgeBase.from_lines(items)


def load_configs(n, seed: int) -> list:
    files = sorted(p for p in SAMPLE_DIR.glob("*.md") if p.name != "README.md")
    rng = random.Random(seed)
    rng.shuffle(files)
    out = []
    for path in files:
        kb = build_kb_e2_regex(path)
        if kb is None or not any(i.type == KnowledgeType.CONSTRAINT for i in kb.items):
            continue
        out.append((path.name, kb))
        if n and len(out) >= n:
            break
    return out


# --------------------------------------------------------------------------
# Loop mechanics copied from their experiments/multiturn_stability.py.
# --------------------------------------------------------------------------

def truncate_to_tokens(text: str, target_tokens: int) -> str:
    return text[: target_tokens * 4]


def kb_text(kb: KnowledgeBase) -> str:
    return "\n".join(i.text for i in kb.items)


def c_recall_against_original(original_constraints, output_text) -> Optional[float]:
    if not original_constraints:
        return None
    hits = sum(1 for c in original_constraints if constraint_preserved(c, output_text))
    return hits / len(original_constraints)


def run_typecompact(kb: KnowledgeBase, n_rounds: int, ratio: float):
    original = [i.text for i in kb.items if i.type == KnowledgeType.CONSTRAINT]
    cur = kb
    budget = max(64, int(kb.total_tokens * ratio))
    per_round: list[Optional[float]] = []
    unsafe_rounds = 0
    for _ in range(n_rounds):
        out_kb, status = type_compact(cur, budget)
        if status != "ok":
            unsafe_rounds += 1
        text = truncate_to_tokens(kb_text(out_kb), budget)
        per_round.append(c_recall_against_original(original, text))
        cur = out_kb
        budget = max(64, int(out_kb.total_tokens * ratio))
    return per_round, unsafe_rounds


def run_naive_head(kb: KnowledgeBase, n_rounds: int, ratio: float):
    """Their hierarchical_summarize proxy: keep first items until budget."""
    return _run_naive_kb(kb, n_rounds, ratio, hierarchical_summarize)


def run_naive_tail(kb: KnowledgeBase, n_rounds: int, ratio: float):
    """Their temporal_window proxy: keep last items until budget."""
    return _run_naive_kb(kb, n_rounds, ratio, temporal_window)


def _run_naive_kb(kb: KnowledgeBase, n_rounds: int, ratio: float, policy):
    original = [i.text for i in kb.items if i.type == KnowledgeType.CONSTRAINT]
    cur = kb
    budget = max(64, int(kb.total_tokens * ratio))
    per_round: list[Optional[float]] = []
    for _ in range(n_rounds):
        out_kb = policy(cur, budget)
        text = truncate_to_tokens(kb_text(out_kb), budget)
        per_round.append(c_recall_against_original(original, text))
        cur = KnowledgeBase.from_lines(text.splitlines())
        budget = max(64, int(out_kb.total_tokens * ratio))
    return per_round, 0


def run_naive_truncate(kb: KnowledgeBase, n_rounds: int, ratio: float):
    """Pure character truncation; their vanilla loop with the LLM call removed."""
    original = [i.text for i in kb.items if i.type == KnowledgeType.CONSTRAINT]
    cur_text = kb_text(kb)
    budget = max(64, int(kb.total_tokens * ratio))
    per_round: list[Optional[float]] = []
    for _ in range(n_rounds):
        cur_text = truncate_to_tokens(cur_text, budget)
        per_round.append(c_recall_against_original(original, cur_text))
        budget = max(64, int(len(cur_text) // 4 * ratio))
    return per_round, 0


ARMS = {
    "typecompact": run_typecompact,
    "naive_head": run_naive_head,
    "naive_tail": run_naive_tail,
    "naive_truncate": run_naive_truncate,
}


def mean_rounds(rows, n_rounds):
    out = {}
    for r in range(1, n_rounds + 1):
        vals = [row[r - 1] for row in rows if row[r - 1] is not None]
        out[f"cycle_{r}"] = round(statistics.mean(vals), 4) if vals else None
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--n", default="20", help="number of configs, or 'all'")
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--ratio", type=float, default=0.50)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--out", default=str(pathlib.Path(__file__).with_name("typecompact_repro.json")))
    args = p.parse_args()

    n = 0 if args.n == "all" else int(args.n)
    configs = load_configs(n, args.seed)
    print(f"suite={SAMPLE_DIR} n_configs={len(configs)} rounds={args.rounds} ratio={args.ratio}")

    per_arm = {}
    for name, fn in ARMS.items():
        rows, unsafe_total = [], 0
        for label, kb in configs:
            row, unsafe = fn(kb, args.rounds, args.ratio)
            rows.append(row)
            unsafe_total += unsafe
        per_arm[name] = {
            "mean_c_recall": mean_rounds(rows, args.rounds),
            "n_configs": len(configs),
            "n_unsafe_status_rounds_typecompact": unsafe_total if name == "typecompact" else None,
        }
        mr = per_arm[name]["mean_c_recall"]
        shown = {c: mr.get(f"cycle_{c}") for c in REPORT_CYCLES if f"cycle_{c}" in mr}
        print(f"{name}: " + " ".join(f"c{c}={v}" for c, v in shown.items()))

    their = None
    if THEIR_RESULTS.exists():
        their = json.loads(THEIR_RESULTS.read_text(encoding="utf-8"))

    result = {
        "name": "rot2_w5_typecompact_repro",
        "provenance": {
            "upstream_repo": "https://github.com/searchsim-org/cikm26-knowledge-triage",
            "upstream_commit": None,  # filled below
            "upstream_experiment": "experiments/multiturn_stability.py",
            "upstream_paper": "The Compaction Cliff in Long-Running AI Agent Memory (CIKM 26)",
        },
        "protocol": {
            "suite": str(SAMPLE_DIR) + " (shipped stratified AAC sample, 327 files)",
            "segmentation": "their build_kb_e2 rules (800-30000 chars, >=10 items)",
            "classifier": "their regex reference classifier (classifier.py classify_instruction); "
                          "the committed paper numbers used the LLM-adjudicated cascade "
                          "(classifier_cascade + classify_batch_cached use_llm=True)",
            "n_configs": len(configs),
            "rounds": args.rounds,
            "ratio": args.ratio,
            "seed": args.seed,
            "report_cycles": list(REPORT_CYCLES),
            "metric": "mean constraint recall vs the ORIGINAL constraint set, "
                      "constraint_preserved key-token check, per cycle",
        },
        "ours_deterministic": per_arm,
        "their_committed_llm_numbers_reference_only": their,
        "deviations_from_upstream": [
            "Their vanilla arm calls openai:gpt-5.4-mini and claude_code:claude-sonnet-4-6; "
            "this offline reproduction replaces them with three deterministic baselines "
            "(naive_head = their hierarchical_summarize proxy, naive_tail = their "
            "temporal_window proxy, naive_truncate = pure character truncation). "
            "Their LLM numbers are NOT claimed as ours.",
            "Classification uses the regex baseline, not the LLM-adjudicated cascade, "
            "so the identified constraint sets differ from the paper's runs.",
            "Their experiment ran 5 rounds on 20 configs of the full-corpus E2 stratum; "
            "we run 10 rounds on the shipped 327-file sample subset (n configurable), "
            "cycles 6-10 are our extension beyond their protocol.",
            "Their loop reclassifies post-compaction text with the cached cascade each "
            "round for LLM arms; naive_head/naive_tail rebuild the KB with the regex "
            "classifier (from_lines), which is the offline equivalent.",
        ],
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "kt_repo_root": str(KT_ROOT),
        },
    }

    # Pin the upstream commit via git if available.
    import subprocess

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(KT_ROOT)
        ).stdout.strip()
        result["provenance"]["upstream_commit"] = sha or None
    except Exception:
        pass

    out_path = pathlib.Path(args.out)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
