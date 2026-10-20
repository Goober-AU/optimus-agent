"""Post-W11 v1 task-state ledger: port of commit 1754a3e1b into the W7 mirror.

Source of truth: crates/pi-coding-agent/src/core/compaction/task_state.rs at
HEAD of rot2/w24-remeasure (identical to the W11 commit; `git diff
1754a3e1b HEAD -- .../task_state.rs` is empty). The W7 mirror
(research/context_rot_r2/live/rust_mirror.py, pre-W11) was validated
byte-for-byte against the live corpus; this module applies exactly the W11
delta to its `build_ledger`:

1. Content-derived item ids (task_state.rs content_id, lines 263-268): the
   section prefix plus the first 8 hex chars of the SHA-256 of the dedupe
   key, e.g. `C-a1b2c3d4`. Constraint ids at line 368, retraction ids at
   line 390, decision ids at line 405, evidence ids at line 445.
2. `correction` marker on every task-state item (task_state.rs line 90,
   serde default false): user-correction carriers are marked at constraint
   capture (line 374) and can never serve as supersession targets.
3. Capture-order map (task_state.rs lines 321-327): constraints and
   decisions captured in the current window record their position; items
   carried in from `previous` are absent from the map and always count as
   older.
4. Correction-safe supersession (task_state.rs lines 470-536):
   - targets exclude correction carriers (line 481) and any item captured
     at or after the correction itself (lines 484-487, older-only);
   - when a later correction matches a target that is already superseded,
     the newest correction wins: the existing pair's new_* fields are
     overwritten instead of skipped (lines 519-527).

render_ledger is unchanged by W11 (ids are opaque strings); the W7 mirror's
render is reused verbatim.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence

from context_rot_r2.live import rust_mirror as rm


def content_id(prefix: str, key: str) -> str:
    """task_state.rs content_id (lines 263-268)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    head = int.from_bytes(digest[0:4], "big")
    return f"{prefix}-{head:08x}"


def build_ledger(entries: Sequence[rm.Entry], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Post-W11 build_ledger: the W7 mirror's rules plus the W11 delta."""
    ledger = previous if previous is not None else rm.empty_ledger()
    seen = set()
    for item in ledger["constraints"] + ledger["decisions"] + ledger["openWork"]:
        seen.add(rm.dedupe_key(item["text"]))

    corrections: List[Dict[str, Any]] = []
    # Capture order of the items this window adds, so supersession targets can
    # be restricted to older items. Items carried in from `previous` are absent
    # from the map and always count as older (task_state.rs lines 321-327).
    capture_order: Dict[str, int] = {}
    captured = 0
    evidence_seen: Dict[str, int] = {}
    for index, item in enumerate(ledger["evidence"]):
        evidence_seen[rm.evidence_key(item["text"])] = index
    anchor_counts: Dict[str, set] = {}
    anchor_first: Dict[str, int] = {}

    for position, entry in enumerate(entries):
        info = rm.ledger_entry_text(entry)
        if info is None:
            continue
        role, text, _is_error = info
        entry_id = entry.id

        if role == "user":
            is_correction = rm.SUPERSEDE_RE.search(text) is not None
            for sentence in rm.sentences(text):
                for clause in rm.split_clauses(sentence):
                    if rm.RATIONALE_RE.search(clause):
                        target = corrections[-1] if corrections else (ledger["constraints"][-1] if ledger["constraints"] else None)
                        if target is not None:
                            parts = clause.split(":", 1)
                            why_text = parts[1].strip() if len(parts) == 2 else clause.strip()
                            target.setdefault("why", []).append(rm.clip(why_text, 200))
                        continue
                    if not (is_correction or rm.CONSTRAINT_RE.search(clause)):
                        continue
                    if len(clause) < 12:
                        continue
                    key = rm.dedupe_key(clause)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    # W11: content-derived id (task_state.rs line 368).
                    item = {
                        "id": content_id("C", key),
                        "text": rm.clip(clause, rm.ITEM_TEXT_MAX),
                        "entry": rm.short_id(entry_id),
                        "why": [],
                        # W11: correction marker (task_state.rs line 374).
                        "correction": is_correction,
                    }
                    capture_order[item["id"]] = captured
                    captured += 1
                    if is_correction:
                        corrections.append(item)
                    ledger["constraints"].append(item)

        if role in ("user", "assistant"):
            for sentence in rm.sentences(text):
                if role == "assistant" and rm.RETRACTION_RE.search(sentence):
                    ledger["retractions"].append({
                        "id": content_id("R", rm.dedupe_key(sentence)),
                        "text": rm.clip(sentence, 240),
                        "entry": rm.short_id(entry_id),
                        "why": [],
                        "correction": False,
                    })
                    continue
                if not rm.DECISION_RE.search(sentence):
                    continue
                key = rm.dedupe_key(sentence)
                if not key or key in seen:
                    continue
                seen.add(key)
                item = {
                    "id": content_id("D", key),
                    "text": rm.clip(sentence, rm.ITEM_TEXT_MAX),
                    "entry": rm.short_id(entry_id),
                    "why": [],
                    "correction": False,
                }
                capture_order[item["id"]] = captured
                captured += 1
                ledger["decisions"].append(item)
            for capture in rm.ANCHOR_RE.finditer(text):
                token = next((g for g in capture.groups() if g is not None), None)
                if token is None:
                    continue
                if rm.LOG_LEVEL_RE.match(token):
                    continue
                anchor_counts.setdefault(token, set()).add(entry_id)
                anchor_first.setdefault(token, position)

        if role == "tool" and rm.EVIDENCE_RE.search(text):
            line = next((l for l in text.split("\n") if rm.EVIDENCE_RE.search(l)), text)
            key = rm.evidence_key(line)
            if key in evidence_seen:
                existing = ledger["evidence"][evidence_seen[key]]
                existing["repeats"] = existing.get("repeats", 1) + 1
                existing["lastEntry"] = rm.short_id(entry_id)
            else:
                evidence_seen[key] = len(ledger["evidence"])
                ledger["evidence"].append({
                    "id": content_id("E", key),
                    "text": rm.clip(line, 200),
                    "entry": rm.short_id(entry_id),
                    "repeats": 1,
                    "lastEntry": rm.short_id(entry_id),
                })

    # Supersession: a user correction retires the best-matching OLDER decision or
    # constraint that shares a specific anchor (identifier, path, digit-bearing
    # token). Shared generic words alone never retire a constraint. Corrections
    # are authoritative user statements and never serve as targets, so two
    # corrections cannot retire each other; when a later correction matches a
    # target that is already superseded, the newest correction carries the
    # requirement (task_state.rs lines 470-536).
    for correction in corrections:
        correction_order = capture_order.get(correction["id"], 0)
        correction_tokens = rm.word_tokens(correction["text"])
        best = None  # (is_decision, index, score)
        candidates = ([(True, i, item) for i, item in enumerate(ledger["decisions"])]
                      + [(False, i, item) for i, item in enumerate(ledger["constraints"])])
        for is_decision, index, item in candidates:
            # W11: corrections are never targets (task_state.rs line 481).
            if item["id"] == correction["id"] or item.get("correction"):
                continue
            # W11: only strictly older items are targets (lines 484-487).
            item_order = capture_order.get(item["id"])
            if item_order is not None and item_order >= correction_order:
                continue
            item_tokens = rm.word_tokens(item["text"])
            shared = correction_tokens & item_tokens
            specific = sum(
                1
                for token in shared
                if "_" in token or "/" in token or any(c.isdigit() for c in token) or len(token) > 8
            )
            score = specific * 3 + len(shared)
            if best is None or score > best[2]:
                best = (is_decision, index, score)
        if best is None or best[2] < 3:
            continue
        is_decision, index, _score = best
        section = ledger["decisions"] if is_decision else ledger["constraints"]
        old = section[index]
        # W11: newest correction wins on a shared target (lines 519-527).
        existing = next((e for e in ledger["superseded"] if e["oldId"] == old["id"]), None)
        if existing is not None:
            existing["newId"] = correction["id"]
            existing["newText"] = correction["text"]
            existing["newEntry"] = correction["entry"]
            continue
        ledger["superseded"].append({
            "oldId": old["id"],
            "oldEntry": old["entry"],
            "newId": correction["id"],
            "newText": correction["text"],
            "newEntry": correction["entry"],
        })

    retired = {item["oldId"] for item in ledger["superseded"]}
    for item_id in sorted(retired):
        if item_id not in ledger["retired"]:
            ledger["retired"].append(item_id)
    ledger["constraints"] = [i for i in ledger["constraints"] if i["id"] not in retired]
    ledger["decisions"] = [i for i in ledger["decisions"] if i["id"] not in retired]

    # Open work: the newest first-person statement of what remains.
    for entry in reversed(entries):
        info = rm.ledger_entry_text(entry)
        if info is None or info[0] != "assistant":
            continue
        text = info[1]
        if not rm.OPEN_WORK_RE.search(text):
            continue
        candidate = {
            "id": "O1",
            "text": rm.clip(text, 240),
            "entry": rm.short_id(entry.id),
            "why": [],
            "correction": False,
        }
        key = rm.dedupe_key(candidate["text"])
        if key not in seen:
            ledger["openWork"] = [i for i in ledger["openWork"] if i["id"] != "O1"]
            seen.add(key)
        ledger["openWork"] = [i for i in ledger["openWork"] if i["id"] != "O1"]
        ledger["openWork"].insert(0, candidate)
        break

    # Anchors: identifiers that recur, weighted to exact, specific shapes.
    ranked = [
        (token, len(entry_ids), anchor_first.get(token, len(entries)))
        for token, entry_ids in sorted(anchor_counts.items())
        if len(entry_ids) >= 2
    ]
    ranked.sort(key=lambda t: (-t[1], -rm.anchor_weight(t[0]), t[2]))
    for token, _, _ in ranked:
        if token in ledger["anchors"]:
            continue
        if len(ledger["anchors"]) >= rm.ANCHORS_CAP:
            break
        ledger["anchors"].append(token)

    return ledger
