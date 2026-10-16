"""OCC-2 single-case runner for the SILO-BENCH adapter (subprocess target).

Mirrors adapter/runner.py's single-case path (official engine import,
content-reconstruction proxy for GLM, init_case -> run_round loop, summary
via the adapter's summarize_case) with ONE addition: the OCC-2 message-
encoding hook from encoding_hook.py is applied between init_case and the
first run_round. The official engine, tools, scoring, and task data are
untouched.

Usage (called by occ2_run.py):
    python occ2_case.py --config occ2_config.json --run <run> --task-file I-01_n2.json \
        --workspace <ws> --summary-out <path>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ADAPTER = HERE.parent / "adapter"
for p in (str(ADAPTER), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import runner as adapter_runner  # noqa: E402  (adapter's runner.py)
from encoding_hook import apply_guidance, build_guidance  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--summary-out", required=True)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    run_spec = next((r for r in cfg["runs"] if r["name"] == args.run), None)
    if run_spec is None:
        print(f"run spec not found: {args.run}", file=sys.stderr)
        return 2

    api_base = cfg["gateway"]["base_url"]
    api_key = adapter_runner._load_token(cfg["gateway"]["token_file"])
    model = run_spec["model"]
    protocol = run_spec["protocol"]
    max_rounds = run_spec["max_rounds"]
    comm_mode = run_spec["comm_mode"]

    engine = adapter_runner._import_official_engine(cfg["silo_repo"], api_base, api_key, model)

    # Content-reconstruction proxy for GLM models (same as adapter runner).
    proxy_info = None
    proxy_cfg = cfg.get("gateway", {}).get("content_reconstruction_proxy", {})
    if proxy_cfg.get("enabled") and model.startswith("glm"):
        from src.utils.parsing import TOOL_CALL_RE  # official regex

        fence_open, _, fence_close = TOOL_CALL_RE.pattern.partition("(.*?)")
        out_root = (Path(__file__).parent / cfg.get("output_root", "../runs")).resolve()
        rewrite_log = out_root / args.run / "proxy_rewrites.jsonl"
        rewrite_log.parent.mkdir(parents=True, exist_ok=True)
        from gateway_proxy import ReconstructionProxy

        proxy = ReconstructionProxy(
            upstream_base=api_base,
            fence_open=fence_open,
            fence_close=fence_close,
            rewrite_log=rewrite_log,
        )
        port = proxy.start(port=int(proxy_cfg.get("listen_port", 43250)))
        proxy_info = {"url": f"http://127.0.0.1:{port}", "port": port}
        print(f"[occ2-case] content-reconstruction proxy on 127.0.0.1:{port} -> {api_base}", flush=True)

    engine_api_base = proxy_info["url"] if proxy_info else api_base

    bench_dir = Path(cfg["silo_repo"]) / "benchmarks"
    task_full = bench_dir / args.task_file
    if not task_full.exists():
        print(f"task file missing in clone: {task_full}", file=sys.stderr)
        return 2

    # --- init (official) ---
    t0 = time.monotonic()
    case_dir = engine.init_case(
        task_file=str(task_full),
        protocol=protocol,
        model=model,
        api_base=engine_api_base,
        api_key=api_key,
        max_rounds=max_rounds,
        workspace=args.workspace,
    )

    # --- OCC-2 encoding hook (the only adapter-side intervention) ---
    guidance = build_guidance(comm_mode)
    audit = apply_guidance(case_dir, guidance)
    print(f"[occ2-case] {args.task_file}: guidance applied "
          f"({audit['guidance_chars']} chars x {len(audit['agents'])} agents)", flush=True)

    # --- rounds (official) ---
    status = "completed"
    error = None
    try:
        for _ in range(max_rounds):
            done = engine.run_round(case_dir)
            if done:
                break
    except Exception as e:  # engine failure - record honestly
        status = "engine_error"
        error = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0

    summary = adapter_runner.summarize_case(
        Path(case_dir), args.task_file, protocol, model, comm_mode, elapsed, status, error,
        "gateway+content-reconstruction-proxy" if proxy_info else "direct",
    )
    summary["encoding_hook"] = audit

    out = Path(args.summary_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    off = summary.get("official", {})
    print(
        f"[occ2-case] done {args.task_file}: status={summary['status']} "
        f"rounds={off.get('rounds_executed')} "
        f"S={((off.get('metrics') or {}).get('S_success_rate'))} "
        f"elapsed={summary['elapsed_s']}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
