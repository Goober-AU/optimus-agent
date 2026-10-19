"""Post-D21 render: correction carriers exempt from the ledger render caps.

Source of truth: crates/pi-coding-agent/src/core/compaction/task_state.rs
(working tree of rot2/w28-d21, on top of commit 2fbb14f33). The W7 mirror's
`render_ledger` (rust_mirror.py, unchanged by W11 and reused verbatim by the
W24 re-measure) is ported here with exactly the D21 delta:

1. Correction carriers are exempt from the section caps (task_state.rs
   lines 668, 677, 718): every capped section is ordered through
   `prioritize_corrections`, so correction carriers always render, ahead of
   the non-correction items, and the cap applies to the remaining slots.
2. `prioritize_corrections` (task_state.rs lines 757-777): corrections in
   capture order first, then the leading non-correction items up to the
   remaining slots; returns the render order and the omitted count. A
   section without corrections behaves exactly like a plain head-take of
   `cap` items, so the no-correction render is byte-identical to the
   mirror's.
3. The dropped-count line states how many corrections were protected when
   the constraints cap omitted anything (task_state.rs lines 729-737):
   "N protected correction(s) rendered; M lower-priority item(s) omitted
   from this block: ..." - N counts the correction carriers in the
   constraints section (decisions and open-work items are never correction
   carriers under the current capture rules).

The fixture pass of replay.py keeps using the mirror render unchanged; this
port is only swapped in for the post-W11+D21 scoring pass, where the render
is recorded and scored but never used to build a corpus-matched request.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from context_rot_r2.live import rust_mirror as rm


def prioritize_corrections(items: List[Dict[str, Any]], cap: int) -> Tuple[List[Dict[str, Any]], int]:
    """task_state.rs prioritize_corrections (lines 757-777)."""
    corrections = [item for item in items if item.get("correction")]
    plain = [item for item in items if not item.get("correction")]
    slots = max(cap - len(corrections), 0)
    omitted = max(len(plain) - slots, 0)
    return corrections + plain[:slots], omitted


def _push_items(out: List[str], title: str, items: List[Dict[str, Any]], omitted: int,
                dropped: List[Tuple[int, str]], render) -> None:
    """Mirror _push_items with the D21 signature (task_state.rs lines 781-816):
    the caller passes the pre-ordered items and the omitted count instead of
    the cap."""
    if not items:
        return
    out.append(f"\n**{title}**\n")
    for item in items:
        out.append(f"- {render(item)}\n")
    if omitted > 0:
        label = {
            "ACTIVE CONSTRAINTS": "active constraint",
            "DECISIONS (current)": "current decision",
            "OPEN WORK": "open-work item",
        }.get(title, "item")
        dropped.append((omitted, label))


def render_ledger(ledger: Dict[str, Any]) -> str:
    """Mirror render_ledger with the D21 correction-cap exemption."""
    if rm.ledger_is_empty(ledger):
        return ""
    out: List[str] = []
    out.append("## Protected Task State (deterministic, provenance-tagged)\n")
    out.append(rm.RENDER_HEADER)
    dropped: List[Tuple[int, str]] = []
    # task_state.rs lines 668-669: corrections first, cap on the rest.
    constraints, omitted_constraints = prioritize_corrections(ledger["constraints"], rm.CONSTRAINTS_CAP)
    protected_corrections = sum(1 for item in ledger["constraints"] if item.get("correction"))
    _push_items(out, "ACTIVE CONSTRAINTS", constraints, omitted_constraints, dropped,
                lambda item: (f"{item['id']} [{item['entry']}]: {item['text']}"
                              + (f" (why: {'; '.join(item.get('why', []))})" if item.get("why") else "")))
    decisions, omitted_decisions = prioritize_corrections(ledger["decisions"], rm.DECISIONS_CAP)
    _push_items(out, "DECISIONS (current)", decisions, omitted_decisions, dropped,
                lambda item: f"{item['id']} [{item['entry']}]: {item['text']}")
    if ledger["superseded"]:
        out.append("\n**SUPERSEDED (history, not current)**\n")
        for item in ledger["superseded"][:rm.SUPERSEDED_CAP]:
            out.append(f"- {item['oldId']} [{item['oldEntry']}] replaced by {item['newId']} [{item['newEntry']}]: {item['newText']}\n")
        if len(ledger["superseded"]) > rm.SUPERSEDED_CAP:
            dropped.append((len(ledger["superseded"]) - rm.SUPERSEDED_CAP, "supersession"))
    if ledger["evidence"]:
        out.append("\n**VERIFIED EVIDENCE**\n")
        for item in ledger["evidence"][:rm.EVIDENCE_CAP]:
            repeats = f" (x{item['repeats']}, latest [{item['lastEntry']}])" if item.get("repeats", 1) > 1 else ""
            out.append(f"- {item['id']} [{item['entry']}]: {item['text']}{repeats}\n")
        if len(ledger["evidence"]) > rm.EVIDENCE_CAP:
            dropped.append((len(ledger["evidence"]) - rm.EVIDENCE_CAP, "evidence item"))
    if ledger["anchors"]:
        out.append("\n**EXACT ANCHORS**: ")
        out.append(", ".join(ledger["anchors"]))
        out.append("\n")
    if ledger["retractions"]:
        out.append("\n**RETRACTIONS (assistant-recorded history)**\n")
        for item in ledger["retractions"][:rm.RETRACTIONS_CAP]:
            out.append(f"- [{item['entry']}] {item['text']}\n")
    open_work, omitted_open_work = prioritize_corrections(ledger["openWork"], rm.OPEN_WORK_CAP)
    _push_items(out, "OPEN WORK", open_work, omitted_open_work, dropped,
                lambda item: f"[{item['entry']}] {item['text']}")
    if dropped:
        total = sum(count for count, _ in dropped)
        detail = ", ".join(f"{count} {label}(s)" for count, label in dropped)
        # task_state.rs lines 729-737: state the protected-correction count
        # when the constraints cap omitted anything.
        protected = ""
        if protected_corrections > 0 and omitted_constraints > 0:
            protected = f"{protected_corrections} protected correction(s) rendered; "
        out.append(f"\n_({protected}{total} lower-priority item(s) omitted from this block: {detail}; the full ledger is persisted on the compaction entry.)_\n")
    text = "".join(out)
    if len(text) > rm.RENDER_MAX_CHARS:
        text = text[:rm.RENDER_MAX_CHARS]
        text += "\n_(ledger block truncated to its character budget; the full ledger is persisted on the compaction entry.)_\n"
    return text
