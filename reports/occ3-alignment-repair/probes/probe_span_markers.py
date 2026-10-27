"""Reproduce every number in SOURCE-SPAN-SELECTION.md.

Run with the extension interpreter from the extension root:

    cd extensions/sglang-occ3
    .venv/Scripts/python.exe ../..//reports/occ3-alignment-repair/probes/probe_span_markers.py

No SGLang, no torch, no GPU, no tokenizer: the fake tokenizer below is the one
used by tests/test_span_markers.py, and every id sequence is built from the
resolved marker table, not from literals typed by hand.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
EXT = os.path.join(ROOT, "extensions", "sglang-occ3")
sys.path.insert(0, EXT)
sys.path.insert(0, os.path.join(EXT, "tests"))

from test_span_markers import FakeTokenizer, ADDED, body          # noqa: E402
from occ3_sglang.span_audit import (                             # noqa: E402
    CONTENT, CONTROL_MARKERS, THINKING, TOOL_ARGUMENTS, TOOL_CALL, UNKNOWN,
    MAX_MARKER_TOKENS, classify_accepted, span_from_policy,
)
from occ3_sglang.span_markers import (                           # noqa: E402
    DEFAULT_PROFILE, ROLES, THINKING_CLOSE, THINKING_OPEN,
    resolve_marker_table, resolve_stop_ids, vocabulary_bound,
)
from occ3_sglang.use_receipts import source_receipt_upper_bound   # noqa: E402


def codepoints(text):
    return " ".join("U+%04X" % ord(char) for char in text)


def section(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main():
    markers = resolve_marker_table(FakeTokenizer())
    section("1. resolved marker table (fake tokenizer, deterministic ids)")
    print("profile          :", DEFAULT_PROFILE)
    print("vocabulary bound :", vocabulary_bound(FakeTokenizer()))
    for role in ROLES:
        ids = markers.by_role()[role]
        print("%-20s %s" % (role, "absent" if ids is None else "len=%d" % len(ids)))
    section("2. the two thinking delimiters, by code point")
    print("THINKING_OPEN  :", THINKING_OPEN, "->", codepoints(THINKING_OPEN))
    print("THINKING_CLOSE :", THINKING_CLOSE, "->", codepoints(THINKING_CLOSE))
    print("stop id set    :", sorted(resolve_stop_ids(FakeTokenizer())))

    section("3. offline classification of a content-then-tool-call turn")
    content = body("Paris is the capital.")
    ids = (markers.thinking_end_ids + content
           + markers.tool_call_start_ids + body("occ3_handoff")
           + markers.argument_key_start_ids + body("value")
           + markers.argument_key_end_ids + markers.argument_value_start_ids
           + body("42") + markers.argument_value_end_ids
           + markers.tool_call_end_ids + markers.turn_end_ids)
    audit = classify_accepted(ids, list(range(len(ids))), markers.span_markers())
    print("accepted rows    :", len(ids))
    print("counts           :", audit.counts)
    print("tool_call_complete:", audit.tool_call_complete,
          "message_truncated:", audit.message_truncated)
    for policy in ("last_accepted", "final_content", "final_content_no_tool"):
        selection = span_from_policy(audit, policy)
        print("%-22s %s" % (policy, "None" if selection is None else
                            "rows %d..%d (%d rows)" % (selection.start_index,
                                                       selection.end_index,
                                                       selection.length)))

    section("4. offline classification of a tool-only turn (glm47 finish=tool_calls)")
    ids2 = (markers.tool_call_start_ids + body("occ3_handoff")
            + markers.argument_key_start_ids + body("value")
            + markers.argument_key_end_ids + markers.argument_value_start_ids
            + body("42") + markers.argument_value_end_ids
            + markers.tool_call_end_ids + markers.turn_end_ids)
    audit2 = classify_accepted(ids2, list(range(len(ids2))), markers.span_markers())
    print("accepted rows    :", len(ids2))
    print("counts           :", audit2.counts)
    for policy in ("last_accepted", "final_content", "final_content_no_tool"):
        selection = span_from_policy(audit2, policy)
        print("%-22s %s" % (policy, "None" if selection is None else
                            "rows %d..%d (%d rows)" % (selection.start_index,
                                                       selection.end_index,
                                                       selection.length)))

    section("5. receipt headroom")
    limit = 2048
    upper = source_receipt_upper_bound(limit)
    print("max-width occ3.source.v1 receipt bytes:", len(upper))
    span_block = {
        "span_schema": "occ3.span.v1",
        "span_policy": "content_tail_no_tool_v2",
        "span_marker_profile": DEFAULT_PROFILE,
        "span_marker_schema": "occ3.span.markers.v1",
        "span_reasoning_terminator": "tool_call_start",
        "span_turn_end_attested": True,
        "span_thinking_count": 128,
        "span_content_count": 128,
        "span_tool_call_count": 128,
        "span_tool_arguments_count": 128,
        "span_control_count": 128,
        "span_unknown_count": 128,
        "span_window_start_position": 2**64 - 1 - 128,
        "span_window_end_position": 2**64 - 1,
        "span_contiguous": True,
        "span_contains_final_content": True,
        "span_contains_tool_call": True,
        "span_message_truncated": True,
    }
    extra = len(json.dumps(span_block, separators=(",", ":"), sort_keys=True))
    print("candidate v2 span block canonical bytes:", extra)
    print("projected v2 receipt bytes             :", len(upper) + extra + 1)
    print("fits %d: %s" % (limit, len(upper) + extra + 1 <= limit))

    section("6. marker bound parity")
    print("span_audit.MAX_MARKER_TOKENS        :", MAX_MARKER_TOKENS)
    print("sglang MAX_REQUEST_REASONING_END_TOKEN_IDS:",
          read_pinned_constant())


def read_pinned_constant():
    path = r"C:/Users/openclawuser/occ3-latent-validation/references/sglang/python/sglang/srt/sampling/sampling_params.py"
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MAX_REQUEST_REASONING_END_TOKEN_IDS"):
                return line.strip().split("=")[1].strip()
    return "not found"


if __name__ == "__main__":
    main()
