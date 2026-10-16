"""SILO-BENCH adapter runner.

Drives the OFFICIAL SILO-BENCH engine (imported from the pinned, read-only
clone) against the local OpenAI-compatible model gateway. The runner does
not reimplement any engine logic: init_case / run_round / evaluate come from
`silo_repo/src/engine.py` unmodified. Task files are loaded from the clone's
benchmarks/ directory; no tasks are fabricated.

Usage (single case, used by pilot_run.py):
    python runner.py --config config.json \
        --run pilot-levelI-n2-msg-glm53 --task-file I-01_n2.json \
        --workspace ../runs/<run>/workspace --summary-out ../runs/<run>/case-I-01.json

Usage (whole run spec, sequential in-process):
    python runner.py --config config.json --run pilot-levelI-n2-msg-glm53
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _load_token(token_file: str) -> str:
    with open(token_file, "r", encoding="utf-8") as f:
        tok = f.read().strip()
    if not tok:
        raise RuntimeError("gateway token file is empty")
    return tok


def _import_official_engine(silo_repo: str, api_base: str, api_key: str, model: str):
    """Import the official engine with env vars set (config priority: env > yaml)."""
    repo = Path(silo_repo).resolve()
    if not (repo / "src" / "engine.py").exists():
        raise FileNotFoundError(f"official engine not found under {repo}")
    os.environ["SILO_API_BASE"] = api_base
    os.environ["SILO_API_KEY"] = api_key
    os.environ["SILO_MODEL"] = model
    sys.path.insert(0, str(repo))
    from src import engine  # noqa: E402  (official module)

    return engine


def _parse_log_events(case_dir: Path) -> list[dict]:
    """Read the official per-agent JSONL logs."""
    events = []
    logs_dir = case_dir / "logs"
    if not logs_dir.exists():
        return events
    for p in sorted(logs_dir.glob("agent-*.jsonl")):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return events


def _iso_to_dt(s: str):
    from datetime import datetime

    return datetime.fromisoformat(s)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation percentile (same as numpy default)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def summarize_case(
    case_dir: Path,
    task_file: str,
    protocol: str,
    model: str,
    comm_mode: str,
    elapsed_s: float,
    status: str,
    error: str | None = None,
    llm_transport: str = "direct",
) -> dict:
    """Build the adapter case summary from official artifacts.

    Everything under `official` comes from the engine's own outputs
    (results.json, metadata.json, logs/). Everything under `adapter` is
    computed here and clearly labeled.
    """
    summary: dict = {
        "task_file": task_file,
        "protocol": protocol,
        "model": model,
        "comm_mode": comm_mode,
        "case_dir": str(case_dir),
        "status": status,
        "elapsed_s": round(elapsed_s, 3),
        "error": error,
        "llm_transport": llm_transport,
    }

    meta_path = case_dir / "metadata.json"
    results_path = case_dir / "results.json"

    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        ex = meta.get("execution", {})
        cfg = meta.get("config", {})
        task = meta.get("task", {})
        summary["official"] = {
            "case_id": task.get("case_id"),
            "case_name": task.get("case_name"),
            "paradigm": task.get("paradigm"),
            "num_agents": cfg.get("agent_count"),
            "max_rounds": cfg.get("max_rounds"),
            "rounds_executed": ex.get("current_round"),
            "execution_status": ex.get("status"),
            "total_input_tokens": ex.get("total_input_tokens"),
            "total_output_tokens": ex.get("total_output_tokens"),
            "all_submitted": ex.get("all_submitted"),
        }

    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            results = json.load(f)
        summary["official"]["metrics"] = results.get("metrics")
        summary["official"]["ground_truth"] = results.get("ground_truth")
        summary["official"]["submissions"] = results.get("submissions")
        summary["official"]["success"] = results.get("success")

    # Adapter-computed statistics from the official logs.
    events = _parse_log_events(case_dir)
    req_by_agent_round = {}
    per_call_latency = []
    llm_calls = 0
    prompt_tokens_calls = []
    output_tokens_calls = []
    for ev in events:
        if ev.get("event") == "llm_request":
            key = (ev.get("agent_id"), ev.get("round"))
            req_by_agent_round[key] = ev.get("timestamp")
        elif ev.get("event") == "llm_response":
            llm_calls += 1
            key = (ev.get("agent_id"), ev.get("round"))
            t_req = req_by_agent_round.get(key)
            if t_req:
                try:
                    dt = (_iso_to_dt(ev["timestamp"]) - _iso_to_dt(t_req)).total_seconds()
                    if dt >= 0:
                        per_call_latency.append(dt)
                except (KeyError, ValueError):
                    pass
            if ev.get("input_tokens"):
                prompt_tokens_calls.append(ev["input_tokens"])
            if ev.get("output_tokens"):
                output_tokens_calls.append(ev["output_tokens"])

    # Inter-agent message volume (adapter metric for the OCC campaign:
    # real bytes that crossed the wire between agents).
    msg_count = 0
    msg_payload_bytes = 0
    msg_file_bytes = 0
    rounds_dir = case_dir / "rounds"
    if rounds_dir.exists():
        for rdir in sorted(rounds_dir.glob("round-*")):
            mdir = rdir / "env" / "messages"
            if not mdir.exists():
                continue
            for mp in mdir.glob("*.json"):
                msg_count += 1
                raw = mp.read_bytes()
                msg_file_bytes += len(raw)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    msg_payload_bytes += len(str(payload.get("content", "")).encode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

    lat_sorted = sorted(per_call_latency)
    summary["adapter"] = {
        "llm_calls": llm_calls,
        "prompt_tokens_sum": sum(prompt_tokens_calls),
        "output_tokens_sum": sum(output_tokens_calls),
        "output_tokens_caveat": (
            "gateway usage.completion_tokens includes hidden reasoning tokens; "
            "prompt_tokens are the real server-side count of what was sent"
        ),
        "llm_latency_s": {
            "mean": round(sum(per_call_latency) / len(per_call_latency), 3) if per_call_latency else None,
            "p50": round(_percentile(lat_sorted, 0.50), 3) if lat_sorted else None,
            "p95": round(_percentile(lat_sorted, 0.95), 3) if lat_sorted else None,
            "max": round(lat_sorted[-1], 3) if lat_sorted else None,
        },
        "inter_agent_messages": msg_count,
        "inter_agent_message_payload_bytes": msg_payload_bytes,
        "inter_agent_message_file_bytes": msg_file_bytes,
    }
    return summary


def run_single_case(
    engine,
    task_file: str,
    protocol: str,
    model: str,
    api_base: str,
    api_key: str,
    max_rounds: int,
    workspace: str,
    comm_mode: str,
    llm_transport: str = "direct",
) -> tuple[dict, str | None]:
    """Run one official case; returns (summary, case_dir)."""
    t0 = time.monotonic()
    case_dir = engine.init_case(
        task_file=task_file,
        protocol=protocol,
        model=model,
        api_base=api_base,
        api_key=api_key,
        max_rounds=max_rounds,
        workspace=workspace,
    )
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
    summary = summarize_case(
        Path(case_dir), task_file, protocol, model, comm_mode, elapsed, status, error,
        llm_transport,
    )
    return summary, case_dir


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.json"))
    ap.add_argument("--run", required=True, help="run spec name inside config.json")
    ap.add_argument("--task-file", default=None, help="single task file (e.g. I-01_n2.json)")
    ap.add_argument("--workspace", default=None, help="workspace dir override")
    ap.add_argument("--summary-out", default=None, help="single-case summary JSON path")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    run_spec = next((r for r in cfg["runs"] if r["name"] == args.run), None)
    if run_spec is None:
        print(f"run spec not found: {args.run}", file=sys.stderr)
        return 2

    api_base = cfg["gateway"]["base_url"]
    api_key = _load_token(cfg["gateway"]["token_file"])
    model = run_spec["model"]
    protocol = run_spec["protocol"]
    max_rounds = run_spec["max_rounds"]
    comm_mode = run_spec.get("comm_mode", cfg.get("comm_mode_default", "occ0_plain_english"))

    engine = _import_official_engine(cfg["silo_repo"], api_base, api_key, model)

    # Content-reconstruction proxy for GLM models (see gateway_proxy.py).
    # The gateway strips SILO's fenced XML tool calls from message.content
    # and re-packages them as native tool_calls; the official engine only
    # parses content. The proxy forwards requests verbatim and merges the
    # extracted blocks back into content.
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
        # NOTE: no /v1 suffix - the OpenAI SDK appends /chat/completions to
        # base_url, and the proxy forwards upstream_base + path verbatim.
        # upstream_base already ends with /ollama/v1.
        proxy_info = {
            "url": f"http://127.0.0.1:{port}",
            "port": port,
            "upstream": api_base,
        }
        print(f"[runner] content-reconstruction proxy on 127.0.0.1:{port} -> {api_base}", flush=True)

    engine_api_base = proxy_info["url"] if proxy_info else api_base

    bench_dir = Path(cfg["silo_repo"]) / "benchmarks"
    workspace = Path(args.workspace) if args.workspace else Path(cfg.get("output_root", "../runs")) / args.run / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    task_files = (
        [args.task_file] if args.task_file else list(run_spec["task_files"])
    )

    summaries = []
    for tf in task_files:
        full = bench_dir / tf
        if not full.exists():
            print(f"task file missing in clone: {full}", file=sys.stderr)
            return 2
        print(f"[runner] case {tf} model={model} protocol={protocol}", flush=True)
        summary, case_dir = run_single_case(
            engine, str(full), protocol, model, engine_api_base, api_key,
            max_rounds, str(workspace), comm_mode,
            llm_transport=("gateway+content-reconstruction-proxy" if proxy_info else "direct"),
        )
        summaries.append(summary)
        off = summary.get("official", {})
        print(
            f"[runner] done {tf}: status={summary['status']} "
            f"rounds={off.get('rounds_executed')} "
            f"S={((summary.get('official', {}).get('metrics') or {}).get('S_success_rate'))} "
            f"elapsed={summary['elapsed_s']}s",
            flush=True,
        )
        if args.summary_out and args.task_file:
            out = Path(args.summary_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
                f.write("\n")

    if not args.task_file:
        out = Path(cfg.get("output_root", "../runs")) / args.run / "cases.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"summaries": summaries}, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
