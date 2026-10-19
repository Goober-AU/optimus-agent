"""E3 arm runner: W7 multicycle driver + simulated session restart.

Port of context_rot_r2/live/driver.py run_arm() (read in full; same cycle
loop, same records) with one addition required by TASK-F: after a configured
cycle the live session is serialized to its persisted JSON shape and
reloaded into a fresh Session object, exactly as a process restart would
re-read the session store, and the restored provider context must be
byte-identical before and after the round-trip.  Everything else (settings,
cut point, summarizer wire call, ledger, probe hook, retention records)
is the W7 infrastructure reused unchanged via rust_mirror.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from context_rot_r2.live import driver
from context_rot_r2.live import rust_mirror as rm

context_texts = driver.context_texts
restored_request_messages = driver.restored_request_messages


@dataclass
class E3ArmResult(driver.ArmResult):
    restarts: List[Dict[str, Any]] = field(default_factory=list)


def serialize_entries(session: rm.Session) -> str:
    """The persisted session shape: every entry with its id and fields."""
    payload = []
    for entry in session.entries:
        payload.append(
            {
                "kind": entry.kind,
                "text": entry.text,
                "usageInput": entry.usage_input,
                "toolCallId": entry.tool_call_id,
                "toolName": entry.tool_name,
                "fromName": entry.from_name,
                "id": entry.id,
                "summary": entry.summary,
                "firstKeptEntryId": entry.first_kept_entry_id,
                "tokensBefore": entry.tokens_before,
                "details": entry.details,
            }
        )
    return json.dumps(payload, ensure_ascii=False)


def reload_session(payload: str, salt: str) -> rm.Session:
    """Fresh Session re-read from the persisted shape (ids preserved)."""
    records = json.loads(payload)
    session = rm.Session(salt=salt)
    entries = []
    for record in records:
        entry = rm.Entry(
            kind=record["kind"],
            text=record.get("text", ""),
            usage_input=record.get("usageInput"),
            tool_call_id=record.get("toolCallId", ""),
            tool_name=record.get("toolName", ""),
            from_name=record.get("fromName", ""),
            summary=record.get("summary", ""),
            first_kept_entry_id=record.get("firstKeptEntryId", ""),
            tokens_before=record.get("tokensBefore", 0.0),
            details=record.get("details"),
        )
        entry.id = record["id"]
        entries.append(entry)
    session.entries = entries
    # New ids derive from salt|n; continue past the reloaded range.
    session._next = len(entries)
    return session


def _context_fingerprint(summary: Optional[str], kept: Sequence[rm.Entry]) -> str:
    return json.dumps(
        {
            "summary": summary,
            "entries": [
                {
                    "kind": e.kind,
                    "id": e.id,
                    "text": e.text,
                    "summary": e.summary,
                    "firstKeptEntryId": e.first_kept_entry_id,
                    "tokensBefore": e.tokens_before,
                    "details": e.details,
                }
                for e in kept
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _entry_fingerprint(session: rm.Session) -> str:
    return json.dumps(
        [
            {
                "kind": e.kind,
                "id": e.id,
                "text": e.text,
                "summary": e.summary,
                "firstKeptEntryId": e.first_kept_entry_id,
                "tokensBefore": e.tokens_before,
                "details": e.details,
            }
            for e in session.entries
        ],
        sort_keys=True,
        ensure_ascii=False,
    )


def run_arm_e3(
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
    restart_after_cycle: Optional[int] = None,
) -> E3ArmResult:
    """Runs one arm. probe_hook(arm, summary, kept, cycle) runs after each cycle.

    restart_after_cycle: after that 1-indexed cycle (compaction + probe),
    the session is serialized and reloaded into a fresh Session object, and
    the restored provider context must be identical across the round-trip.
    """
    model = model or rm.ModelConfig()
    settings = rm.arm_settings(scenario, arm)
    session = rm.Session(salt=f"{scenario['name']}|{arm}")
    for spec in scenario["seedEntries"]:
        session.append_scenario_entry(spec)

    result = E3ArmResult(arm=arm)
    total_cycles = scenario["cycles"] if limit_cycles is None else min(scenario["cycles"], limit_cycles)
    for cycle in range(total_cycles):
        for spec in scenario["cycleEntries"][cycle]:
            session.append_scenario_entry(spec)
        path = session.entries
        live = session.live_messages()
        live_tokens = rm.estimate_context_tokens(live)["tokens"]

        preparation = rm.prepare_compaction(path, settings, live_tokens)
        if preparation is None:
            result.cycles.append(
                {
                    "cycle": cycle,
                    "ran": False,
                    "skipReason": "prepare_compaction returned None",
                    "compactedContextTokens": live_tokens,
                }
            )
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
        result.cycles.append(
            {
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
                    sum(
                        len(task_state.get(k, []))
                        for k in (
                            "constraints",
                            "decisions",
                            "superseded",
                            "evidence",
                            "anchors",
                            "openWork",
                            "retractions",
                        )
                    )
                    if task_state
                    else 0
                ),
                "anchorsPresent": anchors_present,
                "summarizerUsage": usage_totals,
            }
        )
        if probe_hook is not None:
            probe_hook(arm, summary_text, kept, cycle)

        if restart_after_cycle is not None and cycle + 1 == restart_after_cycle:
            before_entries = _entry_fingerprint(session)
            before_context = _context_fingerprint(*session.build_context())
            payload = serialize_entries(session)
            session = reload_session(payload, salt=f"{scenario['name']}|{arm}")
            after_entries = _entry_fingerprint(session)
            after_context = _context_fingerprint(*session.build_context())
            identical = before_entries == after_entries and before_context == after_context
            result.restarts.append(
                {
                    "afterCycle": restart_after_cycle,
                    "entriesRoundTripped": len(session.entries),
                    "contextRoundTripIdentical": identical,
                }
            )
            if not identical:
                result.failures.append(
                    f"restart after cycle {restart_after_cycle}: persisted round-trip changed the restored context"
                )

    summary_text, kept = session.build_context()
    texts = context_texts(summary_text, kept)
    joined = "\n".join(texts)
    for probe in scenario["probes"]:
        found_in: List[str] = []
        for index, text in enumerate(texts):
            if probe["anchor"] in text:
                found_in.append("summary" if index == 0 else f"message[{index - 1}]")
        result.probes.append(
            {
                "id": probe["id"],
                "anchor": probe["anchor"],
                "retained": bool(found_in),
                "foundIn": found_in,
            }
        )
    result.final_summary = texts[0] if texts else ""
    result.final_context_messages = texts
    return result
