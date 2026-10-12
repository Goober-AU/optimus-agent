#!/usr/bin/env python
"""LME-v2 static-environment mini manifest for capture-fidelity validation.

Builds a tiny LME-v2 run manifest (env `web`) from the RAW trajectories that
contain the three static-environment gold facts, with the accessibility-tree
cap raised above the historical 2000-char artifacts:

  * 36fe48ef gold "six"      -- Toolbox section links (traj 1ad1af4b state 7,
                                offset 2728 of a 3549-char tree)
  * 499488a6 gold "none"     -- `Hide this forum` control (traj 1ad1af4b)
  * f928c075 gold "moderator" -- `Moderator on:` own-profile section
                                (traj b1c719fb state 2, offset 33927 of a
                                34491-char tree)

The historical artifacts cap trees at 2000 chars, which removes every one of
these facts (measured offsets: all >= 2309). This generator re-captures the
chosen trajectories at a higher cap (default 40000 = effectively full for
these trees) via the converter's LME_V2_AXTREE_CAP, then declares the request
in settings.captureAxtreeMaxChars so the driver fails closed if the env was
generated with a lower cap.

Usage (from the repository root, with MEMORY_BENCH_DATA_ROOT pointing at the
raw campaign data):

  python scripts/lmev2_static_mini_manifest.py --cap 40000 \
      --run-id lmev2_static_mini_cap40k --out-dir <reports>/iteration3-inputs/runs

The manifest embeds only the selected sessions and the three static questions,
so one driver ingest of this manifest costs minutes, not the ~5.3h of a full
100-session dev5 env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_TRAJECTORIES = ["1ad1af4b", "b1c719fb", "2e66df34"]
DEFAULT_QUESTIONS = ["36fe48ef", "499488a6", "f928c075"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cap", type=int, default=40000,
                    help="LME_V2_AXTREE_CAP applied while capturing the mini env "
                         "(must be >= 1; 40000 retains the target trees fully)")
    ap.add_argument("--trajs", default=",".join(DEFAULT_TRAJECTORIES),
                    help="comma-separated trajectory ids to capture")
    ap.add_argument("--questions", default=",".join(DEFAULT_QUESTIONS),
                    help="comma-separated question ids to embed")
    ap.add_argument("--run-id", default="lmev2_static_mini")
    ap.add_argument("--out-dir", required=True,
                    help="run output root; the manifest lands in <out-dir>/manifests/")
    ap.add_argument("--settings", default=None,
                    help="optional JSON file overriding the run settings block")
    args = ap.parse_args(argv)
    if args.cap < 1:
        ap.error("--cap must be >= 1")
    trajs = [t.strip() for t in args.trajs.split(",") if t.strip()]
    wanted = [q.strip() for q in args.questions.split(",") if q.strip()]

    # The converter resolves LME_V2_AXTREE_CAP at import time.
    os.environ["LME_V2_AXTREE_CAP"] = str(args.cap)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    from bench.converters import lme_v2

    env, stats = lme_v2.build_domain_env("web", trajs)
    recorded = env["notes"]["axtree_cap_chars"]
    if recorded != args.cap:
        raise SystemExit(f"converter recorded cap {recorded} != requested {args.cap}")

    questions = {q["id"]: q for q in lme_v2.load_questions()}
    missing = [qid for qid in wanted if qid not in questions]
    if missing:
        raise SystemExit(f"unknown question ids: {missing}")

    from bench.evidence import prepare_question_declaration
    from bench.protocol import CAPTURE_PROTOCOL_VERSION

    settings = {
        "answerMaxTokens": 8192,
        "maxExtractionTokens": 32000,
        "maxImportChunkChars": 15000,
        "maxRecallChars": 12000,
        "maxRecallEntries": 12,
        "disableTools": True,
        "autoRefine": {"enabled": False},
        "answerInstruction": (
            "You are answering questions about past agent trajectories you have on file. "
            "Answer from the provided context and your persistent memory notes. "
            "If the question's premise is not supported by the evidence, say so briefly and "
            "give the correct value from the evidence instead. If evidence is genuinely "
            "absent, reply: UNKNOWN. For multiple-choice questions reply with the option "
            "letter in \\boxed{}. For list questions reply with the comma-separated values "
            "in \\boxed{}. Keep the answer short."
        ),
        "questionTimeoutS": 300,
        "ingestTimeoutS": 7200,
        "skipIngest": False,
        "recallOn": True,
        "captureAxtreeMaxChars": args.cap,
    }
    if args.settings:
        with open(args.settings, "r", encoding="utf-8") as handle:
            settings.update(json.load(handle))

    out_root = os.path.abspath(args.out_dir)
    manifests_dir = os.path.join(out_root, "manifests")
    os.makedirs(manifests_dir, exist_ok=True)
    manifest = {
        "run_id": args.run_id,
        "benchmark": "lme_v2",
        "variant": "improved",
        "captureProtocol": CAPTURE_PROTOCOL_VERSION,
        "output_dir": os.path.join(out_root, "runs", args.run_id),
        "envs": [env],
        "questions": [
            prepare_question_declaration(lme_v2.build_question(questions[qid]))
            for qid in wanted
        ],
        "model": {"provider": "dgx-glm53", "id": "glm-5.3"},
        "settings": settings,
        "seed": 20261007,
    }
    path = os.path.join(manifests_dir, f"{args.run_id}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)

    print(f"wrote {path}")
    print(f"  cap: {args.cap} (recorded {recorded})")
    print(f"  sessions: {len(env['sessions'])} -> {', '.join(trajs)}")
    print(f"  questions: {', '.join(wanted)}")
    print(f"  states: {stats['states_total']} total, {stats['states_truncated']} truncated, "
          f"{stats['axtree_chars_raw']:,} raw axtree chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
