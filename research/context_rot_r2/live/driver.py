"""Multicycle arm runner for R2-E2 (live GLM 5.3 summarizer + prober).

Mirrors run_arm() from tests/rot2_multicycle.rs cycle by cycle:

  seed entries -> per cycle { append filler -> prepare_compaction ->
  compact (summarizer wire call) -> append_compaction -> restore } ->
  probe evaluation over the restored provider context.

The summarizer and the prober are injected functions so the same runner
executes the deterministic fixture (validation against R2-E1) and the live
GLM 5.3 endpoint (R2-E2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from context_rot_r2.live import rust_mirror as rm

# messages.rs COMPACTION_SUMMARY_PREFIX / COMPACTION_SUMMARY_SUFFIX: the user
# carrier that wraps the compaction summary in the restored provider context.
COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"


@dataclass
class ArmResult:
    arm: str
    cycles: List[Dict[str, Any]] = field(default_factory=list)
    probes: List[Dict[str, Any]] = field(default_factory=list)
    final_summary: str = ""
    final_context_messages: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


def context_texts(summary: Optional[str], kept: Sequence[rm.Entry]) -> List[str]:
    """Per-message plain text of the restored provider context.

    Mirrors context_text() in tests/rot2_multicycle.rs: the compaction
    summary first, then the retained entries.
    """
    texts: List[str] = []
    if summary is not None:
        texts.append(summary)
    for entry in kept:
        texts.append(entry.text)
    return texts


def restored_request_messages(summary: Optional[str], kept: Sequence[rm.Entry]) -> List[Dict[str, str]]:
    """The restored provider context as chat messages (probe input).

    Production shape (messages.rs convert_single_message): the compaction
    summary is a user message wrapped in the COMPACTION_SUMMARY_PREFIX /
    SUFFIX carrier; custom agent messages become user messages; tool
    results are tool-role messages.  Deviation: the scenario's tool results
    are synthetic orphans (no preceding assistant tool call), which the
    chat/completions endpoint rejects, so they are flattened into user
    messages with the same "[Tool result ...]" rendering the summarizer
    request uses.
    """
    messages: List[Dict[str, str]] = []
    if summary is not None:
        messages.append({"role": "user", "content": COMPACTION_SUMMARY_PREFIX + summary + COMPACTION_SUMMARY_SUFFIX})
    for entry in kept:
        if entry.kind == "user":
            messages.append({"role": "user", "content": entry.text})
        elif entry.kind == "assistant":
            messages.append({"role": "assistant", "content": entry.text})
        elif entry.kind == "toolResult":
            messages.append({"role": "user", "content": f"[Tool result {entry.tool_name}]: {entry.text}"})
        elif entry.kind == "agentMessage":
            messages.append({"role": "user", "content": entry.text})
    return messages


def run_arm(
    scenario: Dict[str, Any],
    arm: str,
    summarizer_fn: rm.SummarizerFn,
    prompt_version: str,
    wire_max_tokens: float,
    model: Optional[rm.ModelConfig] = None,
    probe_hook: Optional[Callable[[str, Optional[str], List[rm.Entry], int], None]] = None,
    limit_cycles: Optional[int] = None,
    summarizer_retry: Optional[rm.SummarizerFn] = None,
    validation_attempts: int = 1,
) -> ArmResult:
    """Runs one arm. probe_hook(arm, summary, kept, cycle) runs after each cycle."""
    model = model or rm.ModelConfig()
    settings = rm.arm_settings(scenario, arm)
    session = rm.Session(salt=f"{scenario['name']}|{arm}")
    for spec in scenario["seedEntries"]:
        session.append_scenario_entry(spec)

    result = ArmResult(arm=arm)
    total_cycles = scenario["cycles"] if limit_cycles is None else min(scenario["cycles"], limit_cycles)
    for cycle in range(total_cycles):
        for spec in scenario["cycleEntries"][cycle]:
            session.append_scenario_entry(spec)
        path = session.entries
        live = session.live_messages()
        live_tokens = rm.estimate_context_tokens(live)["tokens"]

        preparation = rm.prepare_compaction(path, settings, live_tokens)
        if preparation is None:
            result.cycles.append({
                "cycle": cycle,
                "ran": False,
                "skipReason": "prepare_compaction returned None",
                "compactedContextTokens": live_tokens,
            })
            continue

        summarized_entries = len(preparation["messages_to_summarize"])
        task_state = preparation["task_state"]
        protected_state = rm.render_ledger(task_state) if task_state else None
        text_instructions = rm.with_retained_state(None, preparation["retained_state_anchor"])
        budget_tokens = rm.summary_output_budget(
            settings.reserve_tokens, settings.summary_budget_mode, preparation["tokens_before"]
        )
        summary = None
        call_records: List[Dict[str, Any]] = []
        last_error: Optional[RuntimeError] = None
        for attempt in range(1, max(1, validation_attempts) + 1):
            fn = summarizer_fn if attempt == 1 else (summarizer_retry or summarizer_fn)
            try:
                summary, call_records = rm.generate_bounded_summary(
                    preparation["messages_to_summarize"],
                    model,
                    settings.reserve_tokens,
                    preparation["previous_summary"],
                    text_instructions if text_instructions else None,
                    settings.summary_update_policy,
                    prompt_version,
                    budget_tokens,
                    protected_state,
                    fn,
                    wire_max_tokens,
                )
                break
            except RuntimeError as error:
                last_error = error
                continue
        if summary is None:
            assert last_error is not None
            result.failures.append(f"cycle {cycle}: {last_error}")
            # Production keeps the session unchanged on a failed compaction.
            continue

        details: Dict[str, Any] = {"taskState": task_state}
        session.append_compaction(
            summary, preparation["first_kept_entry_id"], preparation["tokens_before"], details
        )

        summary_text, kept = session.build_context()
        context_tokens = rm.estimate_context_tokens(
            [rm.Entry(kind="compaction", summary=summary_text or "")] + list(kept)
        )["tokens"]
        texts = context_texts(summary_text, kept)
        joined = "\n".join(texts)
        anchors_present = [p["id"] for p in scenario["probes"] if p["anchor"] in joined]
        usage_totals = {"promptTokens": 0, "completionTokens": 0, "reasoningTokens": 0}
        for call in call_records:
            usage = call.get("usage") or {}
            usage_totals["promptTokens"] += usage.get("prompt_tokens") or 0
            usage_totals["completionTokens"] += usage.get("completion_tokens") or 0
            usage_totals["reasoningTokens"] += usage.get("reasoning_tokens") or 0
        result.cycles.append({
            "cycle": cycle,
            "ran": True,
            "firstKeptEntryId": preparation["first_kept_entry_id"],
            "summarizedEntries": summarized_entries,
            "retainedEntries": len(kept),
            "summaryChars": len(summary),
            "summarizerRequestChars": sum(c["userChars"] for c in call_records),
            "summarizerCalls": len(call_records),
            "compactedContextTokens": context_tokens,
            "ledgerPresent": task_state is not None,
            "ledgerItems": (
                sum(len(task_state.get(k, [])) for k in
                    ("constraints", "decisions", "superseded", "evidence", "anchors", "openWork", "retractions"))
                if task_state else 0
            ),
            "anchorsPresent": anchors_present,
            "summarizerUsage": usage_totals,
        })
        if probe_hook is not None:
            probe_hook(arm, summary_text, kept, cycle)

    summary_text, kept = session.build_context()
    texts = context_texts(summary_text, kept)
    joined = "\n".join(texts)
    for probe in scenario["probes"]:
        found_in: List[str] = []
        for index, text in enumerate(texts):
            if probe["anchor"] in text:
                found_in.append("summary" if index == 0 else f"message[{index - 1}]")
        result.probes.append({
            "id": probe["id"],
            "anchor": probe["anchor"],
            "retained": bool(found_in),
            "foundIn": found_in,
        })
    result.final_summary = texts[0] if texts else ""
    result.final_context_messages = texts
    return result
