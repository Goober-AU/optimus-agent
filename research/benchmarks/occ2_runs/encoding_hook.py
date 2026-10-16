"""OCC-2 message-encoding hook for the SILO-BENCH adapter.

Seam (documented, engine untouched): the official engine writes each agent's
system prompt into rounds/round-000000/agent-XXX/context.json at init_case
time (src/engine.py init_case -> generate_system_prompt) and then re-reads
it every round. This hook appends a communication-encoding contract to that
system message AFTER init_case and BEFORE the first run_round call. The
engine code, tools, task data, and official S/P/C/D scoring are not touched;
only the inter-agent message rendering contract changes.

Modes:
- occ1_structured: inter-agent send_message bodies are OCC-1 compact
  structured English (OCC/1 header + KEY=VALUE lines over the
  optimus.agent-comm.v1 contract fields, full-length keys).
- sym_v1_handdesigned: same payload schema, encoded with the hand-designed
  sym-v1 DIALECT/1 card (research/symbolic_comm: ALIASES_V1 / FAMILIES_V1 /
  SYMBOLS_V1 / microtemplates_v1 / ALL_PACKINGS, exactly the seed_cards()
  "sym-v1" entry in evolve.py; no evolved card was frozen yet, so the run is
  labeled hand-designed).

The occ0 baseline (pilot) uses the engine's default prompt with no hook.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SYMDIR = (HERE.parents[1] / "symbolic_comm").resolve()


def _sym_v1_constants():
    """Extract the sym-v1 seed constants from evolve.py without importing it.

    evolve.py pulls in gateway.py (requests), which the adapter venv does not
    have; the constants are plain literals, so they are read via ast instead.
    Single source of truth stays evolve.py:seed_cards() / microtemplates_v1().
    """
    import ast

    src = (SYMDIR / "evolve.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "microtemplates_v1":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return):
                    out["microtemplates_v1"] = ast.literal_eval(stmt.value)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in ("ALIASES_V1", "FAMILIES_V1", "SYMBOLS_V1", "ALL_PACKINGS"):
                out[name] = ast.literal_eval(node.value)
    missing = {"ALIASES_V1", "FAMILIES_V1", "SYMBOLS_V1", "ALL_PACKINGS",
               "microtemplates_v1"} - set(out)
    if missing:
        raise RuntimeError(f"sym-v1 constants missing from evolve.py: {sorted(missing)}")
    return out


def build_sym_v1_card():
    """Rebuild the hand-designed sym-v1 seed card from symbolic_comm."""
    if str(SYMDIR) not in sys.path:
        sys.path.insert(0, str(SYMDIR))
    import dialect as D  # noqa: E402  (stdlib-only imports)

    consts = _sym_v1_constants()
    card = D.DialectCard(
        card_id="sym-v1",
        generation=0,
        packings=consts["ALL_PACKINGS"],
        key_aliases=consts["ALIASES_V1"],
        family_aliases=consts["FAMILIES_V1"],
        value_symbols=consts["SYMBOLS_V1"],
        microtemplates=consts["microtemplates_v1"],
        field_defaults={
            "RECIPIENT": "daemon://coordinator",
            "SENDER": "daemon://coordinator",
            "SEQ": "1",
        },
        usage_rules=(
            "Findings and evidence stay positional (i1..in, e1..en). "
            "Elided fields equal their defaults. Contradictions and "
            "blocked payloads travel verbatim."
        ),
    )
    return card


OCC1_GUIDANCE = """## Inter-Agent Message Encoding: OCC-1 (MANDATORY)

This multi-agent system communicates with OCC-1 compact structured English.
This section overrides the plain-text content examples shown above: the
content parameter of every send_message call MUST be an OCC-1 document, not
prose. Format:

- Line 1 is exactly: OCC/1
- Every following line is KEY=VALUE. No blank lines, no other prose, no
  newlines inside values.

Map what you want to communicate onto the standard OCC-1 message fields:

- TASK=<case id, e.g. I-06> (required, first line after the header)
- KIND=<one of TASK, PROGRESS, RESULT, EVIDENCE, REQUEST_DETAIL, ACK, CORRECTION, BLOCKED, CANCELLED> (required)
- STATUS=<one of pending, in_progress, done, partial, blocked, failed, cancelled> (required)
- F1=, F2=, ... findings, one fact or value per line (e.g. F1=local maximum is 914)
- FE1=1,2 (optional: evidence references for finding 1)
- ART1=, ART2=, ... values or artifacts exchanged (numbers, sets, lists, verbatim)
- DEP1=, DEP2=, ... dependencies
- NEXT=<one requested action per repeated NEXT line>
- V=<verified claim>, VS=<pass|fail|unknown>, VM=<verification method> (when you verified something)
- MSGID=, PARENT=, TRACE=, SENDER=, RECIPIENT=, SEQ=, TS=, EXP= optional metadata

Messages you receive from other agents arrive in this same OCC-1 encoding:
reconstruct their meaning by reading each KEY=VALUE line. Use as many
finding lines as you need; keep each finding on its own line.

Scope: this encoding applies ONLY to send_message content. The answer
parameter of submit_result stays the plain final value (for example 827 or
30.58), exactly as the task requests. Tool calls themselves keep the
standard XML format shown above.
"""

SYM_GUIDANCE_TEMPLATE = """## Inter-Agent Message Encoding: DIALECT/1 sym-v1 (MANDATORY)

This multi-agent system communicates with the D/1 symbolic dialect defined
by the card below. This section overrides the plain-text content examples
shown above: the content parameter of every send_message call MUST be a D/1
wire document, not prose. Format:

- Line 1 is exactly: D/1
- Every following line is KEY=VALUE where KEY is a short dialect key from
  the card. No blank lines, no other prose, no newlines inside values.

The payload schema is the same message contract as OCC-1 (task, kind,
status, findings, evidence, verification, artifacts, dependencies, next
actions); you encode it with the card instead of full-length keys:

- Use the card key aliases (t=TASK, k=KIND, s=STATUS, i<n>=F<n>, n=NEXT,
  h<n>=ART<n>, u<n>=DEP<n>, e<n>=E<n>, vc=V, vs=VS, vm=VM, ...).
- Use the symbol tables for KIND, STATUS, and verification values (for
  example KIND=PROGRESS is k=P, STATUS=done is s=2).
- Use the packings: all NEXT actions on one line n=action|action, all
  dependencies u=id|id, artifacts h=path|path, confidence c<n>=permille.
- A field equal to its default may be omitted entirely.
- t=TASK (case id) is still required; findings i1=, i2=, ... stay one fact
  per line, positional.

Messages you receive from other agents arrive in this same D/1 encoding:
expand each short key via the card, reverse the symbols, split the packings,
and fill defaults for omitted fields, then reconstruct the meaning.

Scope: this encoding applies ONLY to send_message content. The answer
parameter of submit_result stays the plain final value (for example 827 or
30.58), exactly as the task requests. Tool calls themselves keep the
standard XML format shown above.

DIALECT CARD (sym-v1, hand-designed):
---
{card_text}
---
"""


def build_guidance(comm_mode: str) -> str:
    if comm_mode == "occ1_structured":
        return OCC1_GUIDANCE
    if comm_mode == "sym_v1_handdesigned":
        card = build_sym_v1_card()
        return SYM_GUIDANCE_TEMPLATE.format(card_text=card.card_text())
    raise ValueError(f"unknown comm_mode for encoding hook: {comm_mode}")


def apply_guidance(case_dir, guidance: str) -> dict:
    """Append the encoding contract to every agent's system prompt.

    Edits rounds/round-000000/agent-*/context.json in place (the artifact the
    engine itself wrote at init_case); returns audit info. Must run after
    engine.init_case and before the first engine.run_round call.
    """
    case_dir = Path(case_dir)
    round0 = case_dir / "rounds" / "round-000000"
    agents = []
    for ctx_path in sorted(round0.glob("agent-*/context.json")):
        with open(ctx_path, "r", encoding="utf-8") as f:
            ctx = json.load(f)
        msgs = ctx.get("messages") or []
        if not msgs or msgs[0].get("role") != "system":
            raise RuntimeError(f"no system message in {ctx_path}")
        before = len(msgs[0]["content"])
        msgs[0]["content"] = msgs[0]["content"] + "\n\n" + guidance
        with open(ctx_path, "w", encoding="utf-8") as f:
            json.dump(ctx, f, indent=2)
        agents.append({
            "agent": ctx_path.parent.name,
            "system_chars_before": before,
            "system_chars_after": len(msgs[0]["content"]),
        })
    # Audit copy inside the case workspace (not read by the engine).
    with open(case_dir / "encoding-guidance.txt", "w", encoding="utf-8") as f:
        f.write(guidance)
    return {"guidance_chars": len(guidance), "agents": agents}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True,
                    choices=["occ1_structured", "sym_v1_handdesigned"])
    ap.add_argument("--print", action="store_true", help="print the guidance")
    args = ap.parse_args()
    g = build_guidance(args.mode)
    if args.print:
        print(g)
    else:
        print(f"mode={args.mode} guidance_chars={len(g)}")
