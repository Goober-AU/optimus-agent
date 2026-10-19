"""Command line interface.

    python -m research.context_rot.cli list
    python -m research.context_rot.cli plan  --suite needle-smoke --seed 1234
    python -m research.context_rot.cli run   --suite needle-smoke --adapter fake
    python -m research.context_rot.cli report --run-dir research/context_rot/runs/<id>
    python -m research.context_rot.cli selftest

Only the flags implemented below are documented in README.md.
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from .adapters import FakeAdapter, OfflineEchoAdapter, OpenAICompatAdapter
from .corpus import TOKEN_ESTIMATOR, TOKEN_ESTIMATOR_NOTE
from .distractors import DISTRACTOR_DESCRIPTIONS, DISTRACTOR_KINDS
from .harness import (DEFAULT_RUNS_ROOT, RunConfig, RunDirExistsError, git_info,
                      load_results, new_run_dir, run_one, run_suite, utc_now_iso)
from .multicycle import (DeterministicTruncatingCompactor, MultiCycleConfig,
                         render_multicycle_markdown, run_cycles,
                         summarize_cycles, write_cycles)
from .needles import NEEDLE_DESCRIPTIONS, NEEDLE_KINDS
from .report import render_markdown, write_csv, write_summary
from .scoring import EXTRACTION_RULES
from .stress import describe_categories
from .tasks import (POSITIONS, SUITE_DESCRIPTIONS, SUITES, InstanceConfig, Item,
                    TRUNCATE_STRATEGIES, generate_suite, visible_payload)

REQUIRED_RECORD_FIELDS = (
    "suite", "item_id", "seed", "model", "provider", "context_target_len",
    "evidence_position", "distractor_kind", "requested_json_style", "prompt_tokens",
    "usage", "latency_ms", "extraction_rule", "correct", "invalid_reason",
    "raw_answer", "gold_fingerprint",
)


def _cli_args(args: argparse.Namespace) -> Dict[str, object]:
    """Namespace -> plain dict (drops the bound handler so it stays JSON-safe)."""
    return {k: v for k, v in vars(args).items() if not callable(v)}


def _parse_int_list(text: str) -> Tuple[int, ...]:
    return tuple(int(part.strip()) for part in str(text).split(",") if part.strip())


def _parse_str_list(text: str) -> Tuple[str, ...]:
    return tuple(part.strip() for part in str(text).split(",") if part.strip())


def _parse_headers(values: Optional[Sequence[str]]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for raw in values or []:
        if ":" not in raw:
            raise SystemExit("--extra-header expects 'Name: value', got %r" % (raw,))
        name, value = raw.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def fake_answer_table(items: Sequence[Item]) -> Dict[str, Tuple[str, str]]:
    """Question -> (gold answer, answer kind) table for the offline fake adapter."""
    return {item.visible.question: (item.gold.answer, item.gold.answer_kind)
            for item in items}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    if args.json:
        payload = {
            "version": __version__,
            "suites": [{"name": name, "description": SUITE_DESCRIPTIONS[name]}
                       for name in SUITES],
            "needle_kinds": [{"name": k, "description": NEEDLE_DESCRIPTIONS[k]}
                             for k in NEEDLE_KINDS],
            "stress_categories": describe_categories(),
            "distractor_kinds": [{"name": k, "description": DISTRACTOR_DESCRIPTIONS[k]}
                                 for k in DISTRACTOR_KINDS],
            "positions": list(POSITIONS),
            "truncate_strategies": list(TRUNCATE_STRATEGIES),
            "extraction_rules": list(EXTRACTION_RULES),
            "adapters": ["fake", "echo", "openai"],
            "compactors": ["deterministic"],
            "token_estimator": TOKEN_ESTIMATOR,
            "token_estimator_note": TOKEN_ESTIMATOR_NOTE,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("context_rot %s" % __version__)
    print("")
    print("suites:")
    for name in SUITES:
        print("  %-14s %s" % (name, SUITE_DESCRIPTIONS[name]))
    print("needle kinds:")
    for kind in NEEDLE_KINDS:
        print("  %-24s %s" % (kind, NEEDLE_DESCRIPTIONS[kind]))
    print("stress categories:")
    for category in describe_categories():
        print("  %-45s %s" % (category["category_id"], category["title"]))
    print("distractor kinds:")
    for kind in DISTRACTOR_KINDS:
        print("  %-20s %s" % (kind, DISTRACTOR_DESCRIPTIONS[kind]))
    print("evidence positions: %s" % ", ".join(POSITIONS))
    print("truncate strategies: %s" % ", ".join(TRUNCATE_STRATEGIES))
    print("extraction rules: %s" % ", ".join(EXTRACTION_RULES))
    print("adapters: fake, echo, openai")
    print("compactors: deterministic")
    print("token estimator: %s" % TOKEN_ESTIMATOR)
    return 0


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def _configs_from_args(args: argparse.Namespace) -> List[InstanceConfig]:
    configs = []
    for length in _parse_int_list(args.context_lens):
        for position in _parse_str_list(args.positions):
            for distractor in _parse_str_list(args.distractors):
                configs.append(InstanceConfig(
                    context_target_len=length,
                    evidence_position=position,
                    distractor_kind=distractor,
                    max_prompt_tokens=getattr(args, "max_prompt_tokens", None),
                    truncate_strategy=getattr(args, "truncate_strategy", "head_tail"),
                ))
    return configs


def cmd_plan(args: argparse.Namespace) -> int:
    from .tasks import suite_plan

    configs = _configs_from_args(args)
    plan = suite_plan(args.suite, args.seed, configs, limit=args.limit)
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    print("suite: %s   seed: %d   items: %d   cells: %d"
          % (plan["suite"], plan["seed"], plan["item_count"], plan["cell_count"]))
    print("")
    for config in plan["configs"]:
        print("  config: %s" % json.dumps(config, sort_keys=True))
    print("")
    print("items:")
    for item_id in plan["items"]:
        print("  %s" % item_id)
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def _build_adapter(args: argparse.Namespace, items: Sequence[Item]):
    if args.adapter == "fake":
        return FakeAdapter(
            fake_answer_table(items),
            seed=args.seed,
            base_accuracy=args.fake_accuracy,
            length_penalty_per_1k=args.fake_length_penalty,
            name="fake-adapter",
        )
    if args.adapter == "echo":
        return OfflineEchoAdapter()
    if args.adapter == "openai":
        if not args.base_url:
            raise SystemExit("--base-url is required with --adapter openai")
        api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
        return OpenAICompatAdapter(
            base_url=args.base_url,
            model=args.model,
            api_key=api_key,
            extra_headers=_parse_headers(args.extra_header),
            timeout=args.timeout,
            provider=args.provider,
        )
    raise SystemExit("unknown adapter: %r" % (args.adapter,))


def cmd_run(args: argparse.Namespace) -> int:
    items = generate_suite(args.suite, args.seed, limit=args.limit)
    if not items:
        raise SystemExit("suite %r generated no items" % (args.suite,))
    adapter = _build_adapter(args, items)

    if args.cycles and args.cycles > 0:
        return _run_multicycle(args, items, adapter)

    if args.adapter == "openai":
        model, provider = args.model, args.provider
    else:
        model = getattr(adapter, "name", args.adapter)
        provider = getattr(adapter, "provider", args.adapter)
    config = RunConfig(
        suite=args.suite,
        seed=args.seed,
        model=model,
        provider=provider,
        adapter_kind=args.adapter,
        context_target_lens=_parse_int_list(args.context_lens),
        evidence_positions=_parse_str_list(args.positions),
        distractor_kinds=_parse_str_list(args.distractors),
        requested_json_style=bool(args.json_style),
        max_prompt_tokens=args.max_prompt_tokens,
        truncate_strategy=args.truncate_strategy,
        concurrency=args.concurrency,
        limit=args.limit,
        runs_root=args.runs_root,
        slug=args.slug,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )
    if not args.quiet:
        print("running suite=%s seed=%d cells=%d" % (
            config.suite, config.seed,
            len(items) * len(config.instance_configs())))

    def progress(record: dict) -> None:
        if args.quiet:
            return
        status = "INVALID" if record.get("correct") is None else (
            "ok" if record.get("correct") else "miss")
        print("  %-52s len=%-6s pos=%-6s dis=%-17s %s" % (
            record.get("item_id"), record.get("context_target_len"),
            record.get("evidence_position"), record.get("distractor_kind"), status))

    try:
        outcome = run_suite(config, adapter, run_dir=args.run_dir,
                            resume=bool(args.resume), items=items,
                            progress=progress, args=_cli_args(args))
    except RunDirExistsError as exc:
        print("refusing to write: %s" % exc, file=sys.stderr)
        return 2
    manifest = outcome["manifest"]
    print("run dir: %s" % outcome["run_dir"])
    print("cells: %d   valid: %d   correct: %d   accuracy: %s   invalid: %d" % (
        manifest["summary"]["cells"], manifest["summary"]["valid"],
        manifest["summary"]["correct"],
        "n/a" if manifest["summary"]["accuracy"] is None
        else "%.3f" % manifest["summary"]["accuracy"],
        manifest["summary"]["invalid"]))
    if args.adapter != "openai":
        print("adapter: %s (synthetic; results validate plumbing, not model quality)"
              % args.adapter)
    return 0


def _run_multicycle(args: argparse.Namespace, items: Sequence[Item], adapter) -> int:
    compactor = DeterministicTruncatingCompactor(keep_tail=args.compactor_keep_tail,
                                                 summary_chars=args.compactor_summary_chars)
    config = MultiCycleConfig(
        cycles=args.cycles,
        budget_tokens=args.budget_tokens,
        context_target_len=_parse_int_list(args.context_lens)[0],
        evidence_position=_parse_str_list(args.positions)[0],
        distractor_kind=_parse_str_list(args.distractors)[0],
        requested_json_style=bool(args.json_style),
        keep_tail=args.compactor_keep_tail,
    )
    run_dir = args.run_dir or new_run_dir(args.runs_root, args.slug + "-multicycle")
    os.makedirs(run_dir, exist_ok=True)
    run_meta = {"run_id": os.path.basename(run_dir), "seed": args.seed,
                "model": args.model, "provider": args.provider}
    records: List[dict] = []
    for item in items:
        records.extend(run_cycles(item, adapter, compactor, config, run_meta=run_meta,
                                  temperature=args.temperature, max_tokens=args.max_tokens))
    write_cycles(os.path.join(run_dir, "cycles.jsonl"), records)
    manifest = {
        "run_id": os.path.basename(run_dir),
        "mode": "multicycle",
        "seeds": {"suite": args.seed, "bootstrap": 1234},
        "suite": args.suite,
        "seed": args.seed,
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "status": "complete",
        "harness_version": __version__,
        "compactor": compactor.name,
        "config": config.as_dict(),
        "cells": len(records),
        "python_version": sys.version.split()[0],
        "model": args.model,
        "provider": args.provider,
        "adapter_kind": args.adapter,
        "args": _cli_args(args),
    }
    manifest.update(git_info())
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    write_summary(os.path.join(run_dir, "summary.md"), records, manifest)
    with open(os.path.join(run_dir, "summary.md"), "a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n")
        handle.write(render_multicycle_markdown(records, config))
    print("run dir: %s" % run_dir)
    for row in summarize_cycles(records):
        print("  cycle %d: cells=%d valid=%d accuracy=%s evidence_retained=%.2f" % (
            row["cycle"], row["cells"], row["valid"],
            "n/a" if row["accuracy"] is None else "%.3f" % row["accuracy"],
            row["evidence_retained"]))
    return 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _latest_run_dir(runs_root: str) -> Optional[str]:
    if not os.path.isdir(runs_root):
        return None
    candidates = [os.path.join(runs_root, name) for name in os.listdir(runs_root)
                  if os.path.isdir(os.path.join(runs_root, name))]
    if not candidates:
        return None
    return sorted(candidates)[-1]


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = args.run_dir or _latest_run_dir(args.runs_root)
    if not run_dir or not os.path.isdir(run_dir):
        print("no run directory found", file=sys.stderr)
        return 2
    with open(os.path.join(run_dir, "manifest.json"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    records = load_results(run_dir)
    if not records:
        cycles_path = os.path.join(run_dir, "cycles.jsonl")
        if os.path.exists(cycles_path):
            cycle_records = [json.loads(line) for line in
                             open(cycles_path, encoding="utf-8") if line.strip()]
            config = MultiCycleConfig(**{k: v for k, v in
                                         (manifest.get("config") or {}).items()
                                         if k in MultiCycleConfig.__dataclass_fields__})
            markdown = render_multicycle_markdown(cycle_records, config)
            out_path = args.out or os.path.join(run_dir, "summary-multicycle.md")
            with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(markdown)
            if args.print:
                print(markdown)
            else:
                print("wrote %s" % out_path)
            return 0
        print("no results in %s" % run_dir, file=sys.stderr)
        return 2
    markdown = render_markdown(records, manifest)
    out_path = args.out or os.path.join(run_dir, "summary.md")
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")
    csv_path = args.csv or os.path.join(run_dir, "results.csv")
    write_csv(csv_path, records)
    if args.print:
        print(markdown)
    else:
        print("wrote %s" % out_path)
        print("wrote %s" % csv_path)
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _deterministic(items_a: Sequence[Item], items_b: Sequence[Item]) -> bool:
    def blob(items):
        return json.dumps([item.as_dict() for item in items], sort_keys=True)
    return blob(items_a) == blob(items_b)


def _gold_isolation_problems(suite: str, seed: int) -> List[str]:
    problems: List[str] = []
    items = generate_suite(suite, seed)
    for item in items:
        payload = visible_payload(item.visible, item.visible.needle_text)
        blob = json.dumps(payload, sort_keys=True).casefold()
        for gold in [item.gold.answer] + list(item.gold.facts):
            if gold and gold.casefold() in blob:
                problems.append("%s: gold %r in payload" % (item.item_id, gold))
        if set(payload) - {"context", "question", "answer_kind"}:
            problems.append("%s: unexpected payload keys" % item.item_id)
    return problems


def cmd_selftest(args: argparse.Namespace) -> int:
    checks: List[dict] = []
    seed = args.seed
    runs_root = args.runs_root
    print("context_rot selftest (offline, fake adapter)")
    print("token estimator: %s" % TOKEN_ESTIMATOR)
    print("")

    # 1. determinism
    a = generate_suite(args.suite, seed)
    b = generate_suite(args.suite, seed)
    checks.append(_check("determinism_same_seed", _deterministic(a, b),
                         "%d items" % len(a)))

    # 2. gold isolation
    problems = _gold_isolation_problems(args.suite, seed)
    checks.append(_check("gold_isolation_payload", not problems,
                         "; ".join(problems[:3]) or "no gold string in visible payload"))

    # 3. offline suite run
    config = RunConfig(
        suite=args.suite,
        seed=seed,
        model="fake-adapter",
        provider="fake",
        adapter_kind="fake",
        context_target_lens=(1500, 4000),
        evidence_positions=("start", "end"),
        distractor_kinds=("none", "contradictory"),
        concurrency=2,
        runs_root=runs_root,
        slug="selftest",
    )
    adapter = FakeAdapter(fake_answer_table(a), seed=seed, base_accuracy=0.85,
                          length_penalty_per_1k=0.01, name="fake-adapter")
    outcome = run_suite(config, adapter, items=a, args=_cli_args(args))
    records = outcome["records"]
    run_dir = outcome["run_dir"]
    checks.append(_check("suite_completed", len(records) == len(a) * len(config.instance_configs()),
                         "%d cells in %s" % (len(records), os.path.basename(run_dir))))
    missing_fields = sorted({
        field for record in records for field in REQUIRED_RECORD_FIELDS
        if field not in record})
    checks.append(_check("record_fields_complete", not missing_fields,
                         ",".join(missing_fields) or "all required fields present"))
    checks.append(_check("some_cells_scored",
                         any(r.get("correct") is not None for r in records),
                         "valid=%d" % sum(1 for r in records if r.get("correct") is not None)))

    # 4. manifest + artefacts
    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    artefacts = ["manifest.json", "results.jsonl", "results.csv", "summary.md"]
    checks.append(_check("artefacts_written",
                         all(os.path.exists(os.path.join(run_dir, name)) for name in artefacts),
                         ", ".join(artefacts)))
    checks.append(_check("manifest_git_head", bool(manifest.get("git_head")),
                         str(manifest.get("git_head"))))
    checks.append(_check("manifest_python", bool(manifest.get("python_version")),
                         str(manifest.get("python_version"))))

    # 5. no-overwrite rule: an explicit, non-empty run dir is refused
    import tempfile
    no_overwrite = False
    detail = ""
    try:
        with tempfile.TemporaryDirectory() as scratch:
            with open(os.path.join(scratch, "results.jsonl"), "w", encoding="utf-8") as handle:
                handle.write("")
            try:
                run_suite(config, adapter, items=a, run_dir=scratch)
            except RunDirExistsError:
                no_overwrite = True
                detail = "explicit non-empty run dir refused"
            else:
                detail = "run_suite accepted an occupied directory"
    except Exception as exc:          # pragma: no cover
        detail = "%s: %s" % (type(exc).__name__, exc)
    suffix_dir = new_run_dir(runs_root, "selftest-suffix")
    os.rmdir(suffix_dir)
    checks.append(_check("refuses_existing_run_dir", no_overwrite,
                         detail or "occupancy check"))

    # 6. truncation -> INVALID, never wrong
    trunc_item = a[-1]
    trunc_instance = InstanceConfig(context_target_len=4000, evidence_position="end",
                                    max_prompt_tokens=500, truncate_strategy="head_only")
    trunc_record = run_one(trunc_item, trunc_instance, adapter,
                           {"run_id": manifest["run_id"], "seed": seed,
                            "model": "fake-adapter", "provider": "fake",
                            "adapter_kind": "fake"})
    checks.append(_check(
        "truncated_evidence_is_invalid",
        trunc_record.get("truncated") is True
        and trunc_record.get("evidence_survived") is False
        and trunc_record.get("correct") is None
        and trunc_record.get("invalid_reason") == "truncated_evidence",
        "tokens %s -> %s, reason=%s" % (trunc_record.get("tokens_original_est"),
                                        trunc_record.get("tokens_final_est"),
                                        trunc_record.get("invalid_reason"))))

    # 7. compaction cycles
    compactor = DeterministicTruncatingCompactor(keep_tail=2, summary_chars=2400)
    cycle_records: List[dict] = []
    cycle_configs = [
        MultiCycleConfig(cycles=2, budget_tokens=1200, context_target_len=3000,
                         evidence_position=position, keep_tail=2)
        for position in ("start", "end")
    ]
    for cycle_config in cycle_configs:
        for item in a[:2]:
            cycle_records.extend(run_cycles(
                item, adapter, compactor, cycle_config,
                run_meta={"run_id": manifest["run_id"], "seed": seed,
                          "model": "fake-adapter", "provider": "fake"}))
    write_cycles(os.path.join(run_dir, "cycles.jsonl"), cycle_records)
    with open(os.path.join(run_dir, "summary.md"), "a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n")
        for cycle_config in cycle_configs:
            handle.write(render_multicycle_markdown(cycle_records, cycle_config))
    cycle_rows = summarize_cycles(cycle_records)
    checks.append(_check("compaction_cycles_ran",
                         len(cycle_rows) == cycle_configs[0].cycles and len(cycle_records) > 0,
                         "%d cycles, %d cells" % (len(cycle_rows), len(cycle_records))))
    checks.append(_check("compaction_drops_messages",
                         any(row["compacted"] > 0 for row in cycle_rows),
                         "compacted cells: %s" % [row["compacted"] for row in cycle_rows]))
    retained = {}
    for record in cycle_records:
        key = record["evidence_position"]
        retained.setdefault(key, []).append(bool(record["evidence_survived"]))
    start_ret = sum(retained.get("start", [])) / max(1, len(retained.get("start", [])))
    end_ret = sum(retained.get("end", [])) / max(1, len(retained.get("end", [])))
    checks.append(_check("compaction_fidelity_gradient", start_ret > end_ret,
                         "evidence retained start=%.2f end=%.2f" % (start_ret, end_ret)))

    # 8. report regeneration
    try:
        text = render_markdown(records, manifest)
        report_ok = "## Accuracy vs context length" in text and "## Degradation vs" in text
    except Exception as exc:         # pragma: no cover
        report_ok = False
        text = str(exc)
    checks.append(_check("report_renders", report_ok, "%d chars" % len(text)))

    # 9. scoring extraction rules
    from .scoring import extract_answer
    rules = {
        "delimiter": extract_answer("thinking...\nFINAL ANSWER: 42", "numeric").rule,
        "last_line": extract_answer("reasoning here\n42", "numeric").rule,
        "json": extract_answer('{"answer": "42"}', "numeric").rule,
    }
    checks.append(_check("extraction_rules_fire",
                         rules["delimiter"] == "delimiter:final_answer"
                         and rules["last_line"] == "last_line"
                         and rules["json"] == "json:field",
                         json.dumps(rules, sort_keys=True)))

    print("")
    failures = 0
    for check in checks:
        status = "PASS" if check["ok"] else "FAIL"
        if not check["ok"]:
            failures += 1
        print("  [%s] %-32s %s" % (status, check["name"], check["detail"]))
    print("")
    print("run dir: %s" % run_dir)
    if failures:
        print("SELFTEST FAILED: %d check(s) failed" % failures)
        return 1
    print("SELFTEST PASSED: %d checks" % len(checks))
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.context_rot.cli",
        description="Context-rot and compaction-fidelity benchmark harness (offline first).")
    parser.add_argument("--version", action="version", version="context_rot %s" % __version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list suites, categories, adapters")
    list_parser.add_argument("--json", action="store_true", help="machine-readable output")
    list_parser.set_defaults(func=cmd_list)

    plan_parser = subparsers.add_parser("plan", help="dry-run: items and cells for a run")
    plan_parser.add_argument("--suite", default="needle-smoke", choices=SUITES)
    plan_parser.add_argument("--seed", type=int, default=1234)
    plan_parser.add_argument("--limit", type=int, default=None, help="first N items of the suite")
    plan_parser.add_argument("--context-lens", dest="context_lens", default="2000,8000",
                             help="comma list of context length targets in estimated tokens")
    plan_parser.add_argument("--positions", default="middle",
                             help="comma list: %s" % ",".join(POSITIONS))
    plan_parser.add_argument("--distractors", default="none",
                             help="comma list: %s" % ",".join(DISTRACTOR_KINDS))
    plan_parser.add_argument("--max-prompt-tokens", dest="max_prompt_tokens", type=int,
                             default=None, help="truncate contexts above this prompt budget")
    plan_parser.add_argument("--truncate-strategy", dest="truncate_strategy",
                             default="head_tail", choices=list(TRUNCATE_STRATEGIES))
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.set_defaults(func=cmd_plan)

    run_parser = subparsers.add_parser("run", help="execute a suite and persist the run")
    run_parser.add_argument("--suite", default="needle-smoke", choices=SUITES)
    run_parser.add_argument("--seed", type=int, default=1234)
    run_parser.add_argument("--adapter", default="fake", choices=["fake", "echo", "openai"],
                            help="fake/echo are offline; openai calls an OpenAI-compatible endpoint")
    run_parser.add_argument("--model", default="fake-adapter", help="model name for --adapter openai")
    run_parser.add_argument("--provider", default="openai-compat",
                            help="provider label stored in records")
    run_parser.add_argument("--base-url", dest="base_url", default=None,
                            help="endpoint base, e.g. http://127.0.0.1:43120/v1")
    run_parser.add_argument("--api-key", dest="api_key", default=None,
                            help="bearer token; defaults to $OPENAI_API_KEY")
    run_parser.add_argument("--extra-header", dest="extra_header", action="append",
                            default=None, help="extra HTTP header 'Name: value' (repeatable)")
    run_parser.add_argument("--timeout", type=float, default=120.0, help="request timeout in seconds")
    run_parser.add_argument("--temperature", type=float, default=0.0)
    run_parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    run_parser.add_argument("--context-lens", dest="context_lens", default="2000,8000",
                            help="comma list of context length targets in estimated tokens")
    run_parser.add_argument("--positions", default="middle",
                            help="comma list: %s" % ",".join(POSITIONS))
    run_parser.add_argument("--distractors", default="none",
                            help="comma list: %s" % ",".join(DISTRACTOR_KINDS))
    run_parser.add_argument("--json-style", dest="json_style", action="store_true",
                            help="request a JSON answer object")
    run_parser.add_argument("--max-prompt-tokens", dest="max_prompt_tokens", type=int,
                            default=None, help="truncate contexts above this prompt budget")
    run_parser.add_argument("--truncate-strategy", dest="truncate_strategy",
                            default="head_tail", choices=list(TRUNCATE_STRATEGIES))
    run_parser.add_argument("--concurrency", type=int, default=1)
    run_parser.add_argument("--limit", type=int, default=None, help="first N items of the suite")
    run_parser.add_argument("--runs-root", dest="runs_root", default=DEFAULT_RUNS_ROOT)
    run_parser.add_argument("--slug", default="run", help="run directory slug")
    run_parser.add_argument("--run-dir", dest="run_dir", default=None,
                            help="explicit run directory (must be new, or use --resume)")
    run_parser.add_argument("--resume", action="store_true",
                            help="continue an existing --run-dir, skipping finished cells")
    run_parser.add_argument("--cycles", type=int, default=0,
                            help="run N compaction cycles instead of one shot")
    run_parser.add_argument("--compactor", default="deterministic", choices=["deterministic"])
    run_parser.add_argument("--compactor-keep-tail", dest="compactor_keep_tail", type=int, default=2)
    run_parser.add_argument("--compactor-summary-chars", dest="compactor_summary_chars",
                            type=int, default=1200,
                            help="characters of dropped messages kept in the compaction summary")
    run_parser.add_argument("--budget-tokens", dest="budget_tokens", type=int, default=2500,
                            help="compaction trigger budget in estimated tokens")
    run_parser.add_argument("--fake-accuracy", dest="fake_accuracy", type=float, default=1.0,
                            help="fake adapter base accuracy before length penalty")
    run_parser.add_argument("--fake-length-penalty", dest="fake_length_penalty", type=float,
                            default=0.0, help="fake adapter accuracy loss per 1000 prompt tokens")
    run_parser.add_argument("--quiet", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    report_parser = subparsers.add_parser("report", help="regenerate markdown and CSV for a run")
    report_parser.add_argument("--run-dir", dest="run_dir", default=None,
                               help="run directory (default: latest under --runs-root)")
    report_parser.add_argument("--runs-root", dest="runs_root", default=DEFAULT_RUNS_ROOT)
    report_parser.add_argument("--out", default=None, help="markdown output path")
    report_parser.add_argument("--csv", default=None, help="CSV output path")
    report_parser.add_argument("--print", dest="print", action="store_true",
                               help="print markdown to stdout")
    report_parser.set_defaults(func=cmd_report)

    selftest_parser = subparsers.add_parser(
        "selftest", help="offline end-to-end check with the fake adapter")
    selftest_parser.add_argument("--seed", type=int, default=1234)
    selftest_parser.add_argument("--suite", default="needle-smoke", choices=SUITES)
    selftest_parser.add_argument("--runs-root", dest="runs_root", default=DEFAULT_RUNS_ROOT)
    selftest_parser.set_defaults(func=cmd_selftest, suite_override=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
