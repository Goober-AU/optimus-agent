#!/usr/bin/env python3
"""Explicit, opt-in real-provider search validation. Never run by check.sh."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


async def validate(args):
    if not args.live:
        raise SystemExit("Pass --live to authorize real provider requests against the selected existing profile.")
    binary = args.binary.resolve(strict=True)
    profile = args.agent_dir.expanduser().resolve(strict=True)
    module_path = args.skill_dir.resolve(strict=True) / "src/websearch/websearch.py"
    os.environ["OPTIMUS_NATIVE_BINARY"] = str(binary)
    os.environ["PRIME_AGENT_CODING_AGENT_DIR"] = str(profile)
    spec = importlib.util.spec_from_file_location("validated_search", module_path)
    skill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill)
    queries = ["Python 3.14 final release date site:python.org",
               "NASA Artemis II splashdown date site:nasa.gov"]
    results = []
    for query in queries:
        started = time.monotonic()
        data = await skill._fetch_codex(query, 60, 5, "live", args.model)
        assert data.get("status") == "ok", f"Live search failed: {data.get('error', 'unknown')}"
        assert data.get("search_calls", 0) > 0 and data.get("sources"), "No completed hosted search/source evidence"
        results.append({"query": query, "seconds": round(time.monotonic() - started, 2), **data})
    started = time.monotonic()
    combined = await skill.run(queries[0], timeout=60, num_results=5, codex_model=args.model)
    assert "Codex web search (live)" in combined and "https://" in combined, "Default skill path did not return Codex sources"
    serper_configured = bool(skill._resolve_api_key())
    receipt = {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "build_provenance": json.loads(subprocess.check_output([str(binary), "--build-provenance"], text=True)),
               "model": args.model, "live_codex_searches": results,
               "default_skill_seconds": round(time.monotonic() - started, 2),
               "default_skill_output": combined, "serper_configured": serper_configured,
               "serper_results_observed": "Serper Google search" in combined}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": True, "codex_live_queries": len(results) + 1,
                      "sources_per_query": [len(r["sources"]) for r in results],
                      "serper_configured": serper_configured,
                      "receipt": str(args.output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--agent-dir", type=Path, required=True)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parents[1] / "resources/agent/skills/websearch")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(validate(parser.parse_args()))
