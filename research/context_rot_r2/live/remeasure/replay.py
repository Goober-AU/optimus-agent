"""R2-E2-RM: offline re-measure of the R2-E2 live corpus through the post-W11 ledger.

Replays the committed W7 corpus (research/context_rot_r2/live/raw-calls.jsonl)
through the W7 Python mirror of the production compaction pipeline, then swaps
the v1 task-state ledger for the post-W11 port (w11_port.py, commit 1754a3e1b)
and re-derives every cycle's Protected Task State block.

Two passes per scenario:

  pass 1 (fixture, pre-W11): the mirror runs unmodified with a corpus-backed
    summarizer; every summarizer request must byte-match a corpus record and
    every cycle must reproduce the committed results.json (summary lengths,
    entry ids, ledger item counts). This pins the replayed session structure
    (entry ids, cut points, ledger windows) to the live run.

  pass 2 (post-W11): the same cycle loop with rm.build_ledger swapped for the
    ported build_ledger at the exact call site W11 changed. The ledger never
    reads summaries (it merges details.taskState over entry windows), so the
    post-W11 ledgers are exact; the corpus summaries are held fixed for the
    compaction entries (they cannot influence the ledger or the cut points).

  pass 3 (post-W11+D21, added by W28): the pass-2 replay repeated with the
    render swapped for the D21 port (d21_render.render_ledger), which exempts
    correction carriers from the section caps (task_state.rs render_ledger on
    rot2/w28-d21). The render only feeds the recorded cycle renders and the
    D21 scoring columns; pass 1 still byte-matches the corpus and pass 2
    still reproduces the committed W24 numbers, so the before/after D21
    comparison is computed inside one run of this driver.

Scoring: the same probes with the same versioned matchers (probe_protocol
"live-v1"), applied to the replayed ledger renders:

  - recall kinds (constraint, decision, evidence, task): the matcher runs on
    the full rendered block, mirroring the live question "quote the item"
    (the live model could quote from current or history sections);
  - currency kind (supersession): the matcher runs on the render's CURRENT
    composite scoped to the corrected module (the items listed under ACTIVE
    CONSTRAINTS and DECISIONS (current), at the render's caps, that name the
    module), mirroring the live question "which architecture is the CURRENT
    choice for the X module" and the corpus-verified mechanism that GLM
    follows the render's current assertion.

A structural classification (inverted / ambiguous / clean / lost) is computed
per correction from the ledger itself, independent of the matcher, together
with where the render shows the correction text at all (active,
superseded-new-text or absent).

No model calls: the corpus is the regression fixture. Model-dependent claims
(what GLM would answer on a post-W11 render) are reported as predictions,
never as measured results.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from context_rot_r2.live import probe_protocol as pp
from context_rot_r2.live import rust_mirror as rm
from context_rot_r2.live.remeasure import d21_render, w11_port

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_DIR = os.path.dirname(HERE)
CORPUS_PATH = os.path.join(LIVE_DIR, "raw-calls.jsonl")
RESULTS_PATH = os.path.join(LIVE_DIR, "results.json")

SCENARIOS = ("r2-e2-multicycle", "r2-e2-hard")
ARMS = ("default", "taskState")
PROMPT_VERSION = rm.COMPACTION_PROMPT_V2
WIRE_MAX_TOKENS = 8192.0
MODEL = rm.ModelConfig(context_window=1_048_576.0, max_tokens=64_000.0)

LIVE_POINTS = {"r2-e2-multicycle": ["1", "2", "3", "4", "5"], "r2-e2-hard": ["2", "4", "5"]}


class FixtureError(AssertionError):
    pass


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


class Corpus:
    def __init__(self, path: str) -> None:
        self.records: List[Dict[str, Any]] = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    self.records.append(json.loads(line))
        self.summarizer: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
        for record in self.records:
            if record.get("meta", {}).get("kind") != "summarizer":
                continue
            messages = record["request"]["messages"]
            key = (messages[0]["content"], messages[1]["content"], int(record["request"]["maxTokens"]))
            if key in self.summarizer:
                raise FixtureError(f"duplicate summarizer record for maxTokens={key[2]}")
            self.summarizer[key] = record
        self.consumed: set = set()

    def summarizer_fn(self, system: str, user: str, max_tokens: float) -> rm.SummarizerCallResult:
        key = (system, user, int(max_tokens))
        record = self.summarizer.get(key)
        if record is None:
            raise FixtureError(
                "replayed summarizer request is not in the corpus "
                f"(maxTokens={int(max_tokens)}, userChars={len(user.encode('utf-8'))})"
            )
        self.consumed.add(record["callId"] + "@" + str(int(max_tokens)))
        response = record["response"]
        return rm.SummarizerCallResult(
            text=response["content"],
            stop_reason=response["finishReason"] or "",
            usage=response["usage"],
            latency_s=record["latencyS"],
            attempts=record["attempts"],
        )

    def leftover_summarizer_records(self) -> List[str]:
        used_keys = set()
        for record in self.records:
            if record.get("meta", {}).get("kind") != "summarizer":
                continue
            messages = record["request"]["messages"]
            key = (messages[0]["content"], messages[1]["content"], int(record["request"]["maxTokens"]))
            if key in self.summarizer and (record["callId"] + "@" + str(int(record["request"]["maxTokens"]))) in self.consumed:
                used_keys.add(record["callId"])
        return [
            record["callId"]
            for record in self.records
            if record.get("meta", {}).get("kind") == "summarizer" and record["callId"] not in used_keys
        ]


# ---------------------------------------------------------------------------
# Cycle replay
# ---------------------------------------------------------------------------


def replay_arm(
    scenario: Dict[str, Any],
    arm: str,
    corpus: Optional[Corpus],
    expect: Dict[str, Any],
    fixed_summaries: Optional[List[str]] = None,
    ledger_builder=None,
    render_fn=None,
) -> Dict[str, Any]:
    """One arm replay. corpus=None with fixed_summaries set: post-W11 pass.

    render_fn swaps the render used to record each cycle's Protected Task
    State block (the D21 pass); it never touches the fixture pass, whose
    requests must keep byte-matching the corpus.
    """
    if (corpus is None) != (fixed_summaries is not None):
        raise ValueError("exactly one of corpus / fixed_summaries")
    settings = rm.arm_settings(scenario, arm)
    session = rm.Session(salt=f"{scenario['name']}|{arm}")
    for spec in scenario["seedEntries"]:
        session.append_scenario_entry(spec)

    original_builder = rm.build_ledger
    if ledger_builder is not None:
        # The post-W11 pass swaps the mirror's build_ledger at the same call
        # site the W11 commit changed (compaction.rs prepare_compaction ->
        # task_state.rs build_ledger). Everything else runs unmodified.
        rm.build_ledger = ledger_builder
    try:
        cycles: List[Dict[str, Any]] = []
        summaries: List[str] = []
        for cycle in range(scenario["cycles"]):
            for spec in scenario["cycleEntries"][cycle]:
                session.append_scenario_entry(spec)
            path = session.entries
            live = session.live_messages()
            live_tokens = rm.estimate_context_tokens(live)["tokens"]

            preparation = rm.prepare_compaction(path, settings, live_tokens)
            if preparation is None:
                raise FixtureError(f"{scenario['name']}|{arm}|cycle {cycle}: prepare_compaction returned None")

            task_state = preparation["task_state"]
            render = render_fn or rm.render_ledger
            protected_state = render(task_state) if task_state else None
            text_instructions = rm.with_retained_state(None, preparation["retained_state_anchor"])
            budget_tokens = rm.summary_output_budget(
                settings.reserve_tokens, settings.summary_budget_mode, preparation["tokens_before"]
            )

            if corpus is not None:
                summary, call_records = rm.generate_bounded_summary(
                    preparation["messages_to_summarize"],
                    MODEL,
                    settings.reserve_tokens,
                    preparation["previous_summary"],
                    text_instructions if text_instructions else None,
                    settings.summary_update_policy,
                    PROMPT_VERSION,
                    budget_tokens,
                    protected_state,
                    corpus.summarizer_fn,
                    WIRE_MAX_TOKENS,
                )
            else:
                summary = fixed_summaries[cycle]
                call_records = []

            session.append_compaction(
                summary, preparation["first_kept_entry_id"], preparation["tokens_before"], {"taskState": task_state}
            )
            summaries.append(summary)

            summary_text, kept = session.build_context()
            context_tokens = rm.estimate_context_tokens(
                [rm.Entry(kind="compaction", summary=summary_text or "")] + list(kept)
            )["tokens"]
            cycles.append({
                "cycle": cycle,
                "firstKeptEntryId": preparation["first_kept_entry_id"],
                "summarizedEntries": len(preparation["messages_to_summarize"]),
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
                "ledger": task_state,
                "render": protected_state or "",
            })
        final_summary, _kept = session.build_context()
    finally:
        rm.build_ledger = original_builder

    return {"cycles": cycles, "summaries": summaries, "finalSummary": final_summary}


def verify_against_live(replay: Dict[str, Any], expect_arm: Dict[str, Any], label: str) -> List[str]:
    problems: List[str] = []
    expect_cycles = [c for c in expect_arm["cycles"] if c.get("ran")]
    if len(expect_cycles) != len(replay["cycles"]):
        problems.append(f"{label}: cycle count {len(replay['cycles'])} != live {len(expect_cycles)}")
        return problems
    for got, want in zip(replay["cycles"], expect_cycles):
        for field in ("firstKeptEntryId", "summarizedEntries", "retainedEntries", "summaryChars",
                      "summarizerRequestChars", "summarizerCalls", "ledgerItems", "compactedContextTokens"):
            if got[field] != want[field]:
                problems.append(
                    f"{label} cycle {got['cycle']}: {field} replay={got[field]} live={want[field]}"
                )
    if replay["finalSummary"] != expect_arm["finalSummary"]:
        problems.append(f"{label}: final summary mismatch")
    return problems


def verify_structure_matches(pre: Dict[str, Any], post: Dict[str, Any], label: str) -> List[str]:
    problems: List[str] = []
    for a, b in zip(pre["cycles"], post["cycles"]):
        for field in ("cycle", "firstKeptEntryId", "summarizedEntries", "retainedEntries"):
            if a[field] != b[field]:
                problems.append(f"{label} cycle {a['cycle']}: {field} pre={a[field]} post={b[field]}")
    for i, (sa, sb) in enumerate(zip(pre["summaries"], post["summaries"])):
        if sa != sb:
            problems.append(f"{label} summary {i} drifted")
    return problems


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def module_scoped_current(correction_text: str, ledger: Optional[Dict[str, Any]], cap: bool,
                          priority: bool = False) -> str:
    """CURRENT item texts naming the corrected module.

    cap=True restricts to the items the render lists (render caps); cap=False
    uses the ledger's full current sections (the ledger's own assertion).
    priority=True models the D21 render (correction carriers first, cap on
    the remaining slots) instead of the plain head-take caps.
    """
    module = correction_module(correction_text)
    if not module or not ledger:
        return ""
    if cap and priority:
        constraints = d21_render.prioritize_corrections(ledger["constraints"], rm.CONSTRAINTS_CAP)[0]
        decisions = d21_render.prioritize_corrections(ledger["decisions"], rm.DECISIONS_CAP)[0]
    elif cap:
        constraints = ledger["constraints"][: rm.CONSTRAINTS_CAP]
        decisions = ledger["decisions"][: rm.DECISIONS_CAP]
    else:
        constraints = ledger["constraints"]
        decisions = ledger["decisions"]
    return "\n".join(t for t in (i["text"] for i in constraints + decisions) if module in t)


def correction_module(correction_text: str) -> Optional[str]:
    match = re.search(r"for the ([A-Za-z]+) module instead", correction_text)
    return match.group(1) if match else None


def correction_anchor(correction_text: str) -> str:
    match = re.search(r"(R2-CORR-[0-9a-f]+)", correction_text)
    return match.group(1) if match else correction_text[:40]


def classify_correction(correction_text: str, ledger: Optional[Dict[str, Any]],
                         priority: bool = False) -> Dict[str, Any]:
    """Verdict for one correction.

    structural (the ledger's own assertion, uncapped):
      inverted:  the pre-correction decision is current and the correction is not
      ambiguous: both the correction and the pre-correction decision are current
      clean:     the correction is current and the pre-correction decision is not
      lost:      neither is current
    renderedCurrent: the same classification restricted to the items the render
      lists (ACTIVE CONSTRAINTS + DECISIONS at the render caps).
    correctionRenderedAs: where the full render shows the correction text at
      all: "active", "superseded-new-text" or "absent".
    """
    module = correction_module(correction_text)
    old_needle = f"architecture A for the {module} module" if module else None
    anchor = correction_anchor(correction_text)

    def scan(ledger_dict: Optional[Dict[str, Any]], cap: Optional[int]) -> Dict[str, bool]:
        if not ledger_dict:
            return {"correctionCurrent": False, "oldDecisionCurrent": False}
        constraints = ledger_dict["constraints"]
        decisions = ledger_dict["decisions"]
        if cap is not None and priority:
            constraints = d21_render.prioritize_corrections(constraints, rm.CONSTRAINTS_CAP)[0]
            decisions = d21_render.prioritize_corrections(decisions, rm.DECISIONS_CAP)[0]
        elif cap is not None:
            constraints = constraints[: rm.CONSTRAINTS_CAP]
            decisions = decisions[: rm.DECISIONS_CAP]
        corr = any(anchor in i["text"] for i in constraints + decisions)
        old = bool(old_needle) and any(old_needle in i["text"] for i in constraints + decisions)
        return {"correctionCurrent": corr, "oldDecisionCurrent": old}

    structural = scan(ledger, None)
    rendered = scan(ledger, rm.CONSTRAINTS_CAP)

    def verdict(state: Dict[str, bool]) -> str:
        if state["oldDecisionCurrent"] and not state["correctionCurrent"]:
            return "inverted"
        if state["oldDecisionCurrent"] and state["correctionCurrent"]:
            return "ambiguous"
        if state["correctionCurrent"]:
            return "clean"
        return "lost"

    rendered_as = "absent"
    if ledger:
        if priority:
            rendered_constraints = d21_render.prioritize_corrections(ledger["constraints"], rm.CONSTRAINTS_CAP)[0]
            rendered_decisions = d21_render.prioritize_corrections(ledger["decisions"], rm.DECISIONS_CAP)[0]
        else:
            rendered_constraints = ledger["constraints"][: rm.CONSTRAINTS_CAP]
            rendered_decisions = ledger["decisions"][: rm.DECISIONS_CAP]
        if any(anchor in i["text"] for i in list(rendered_constraints) + list(rendered_decisions)):
            rendered_as = "active"
        elif any(anchor in s["newText"] for s in ledger["superseded"][: rm.SUPERSEDED_CAP]):
            rendered_as = "superseded-new-text"

    return {
        "module": module,
        "structural": verdict(structural),
        "renderedCurrent": verdict(rendered),
        "correctionRenderedActive": rendered["correctionCurrent"],
        "correctionRenderedAs": rendered_as,
    }


def live_probe_status(results: Dict[str, Any], scenario_name: str, arm: str, probe_id: str) -> Dict[str, str]:
    rounds = results["results"][scenario_name]["arms"][arm]["probeRounds"]
    return {point: next(r["score"]["status"] for r in rounds[point] if r["id"] == probe_id)
            for point in LIVE_POINTS[scenario_name] if point in rounds}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run() -> Dict[str, Any]:
    corpus = Corpus(CORPUS_PATH)
    with open(RESULTS_PATH, encoding="utf-8") as handle:
        live = json.load(handle)

    # D21 port guard: without correction carriers the ported render must be
    # byte-identical to the mirror render (the Rust test
    # no_corrections_render_is_byte_equal_to_the_previous_output pins the same
    # property on the Rust side).
    plain_ledger = {
        "constraints": [
            {"id": f"C{i}", "text": f"CONSTRAINT: rule {i} must hold for module_{i}", "entry": "e1", "why": [], "correction": False}
            for i in range(rm.CONSTRAINTS_CAP + 3)
        ],
        "decisions": [
            {"id": f"D{i}", "text": f"Decision: choice {i}", "entry": "e2", "why": [], "correction": False}
            for i in range(rm.DECISIONS_CAP + 3)
        ],
        "superseded": [], "evidence": [], "anchors": [], "openWork": [], "retractions": [], "retired": [],
    }
    guard_ok = d21_render.render_ledger(plain_ledger) == rm.render_ledger(plain_ledger)

    out: Dict[str, Any] = {
        "experiment": "R2-E2-RM",
        "scoringVersion": pp.PROTOCOL_VERSION,
        "method": {
            "corpus": "research/context_rot_r2/live/raw-calls.jsonl (W7 live run, 581 calls)",
            "fixture": "pre-W11 mirror replay must byte-match every corpus summarizer request "
                       "and reproduce the committed results.json cycle fields",
            "postW11": "w11_port.build_ledger (commit 1754a3e1b) swapped in at the "
                       "prepare_compaction call site; ledger windows and cut points asserted "
                       "identical to the fixture pass",
            "postW11D21": "the same post-W11 ledger rendered through d21_render.render_ledger "
                          "(correction carriers exempt from the section caps; task_state.rs "
                          "render_ledger of rot2/w28-d21); the render feeds the recorded cycle "
                          "renders and the D21 scoring columns only",
            "scoring": "same probes, same live-v1 matchers; recall kinds (constraint, "
                       "decision, evidence, task) scored on the full rendered block; the "
                       "supersession kind scored on the render's CURRENT composite scoped to "
                       "the corrected module (ACTIVE CONSTRAINTS + DECISIONS at the render "
                       "caps, mirroring the live per-module question), plus a structural "
                       "inverted/ambiguous/clean/lost classification from the ledger",
            "offline": "no model calls; model-dependent claims are predictions only",
        },
        "fixtureCheck": {},
        "replay": {},
        "scoring": {},
        "verdict": [],
    }

    for scenario_name in SCENARIOS:
        with open(os.path.join(LIVE_DIR, f"{scenario_name}.scenario.json"), encoding="utf-8") as handle:
            scenario = json.load(handle)
        scenario_result: Dict[str, Any] = {"taskState": {}}
        fixture: Dict[str, Any] = {}

        pre_replay = None
        for arm in ARMS:
            expect_arm = live["results"][scenario_name]["arms"][arm]
            replay = replay_arm(scenario, arm, corpus, expect_arm)
            problems = verify_against_live(replay, expect_arm, f"{scenario_name}|{arm}")
            fixture[arm] = {
                "ok": not problems,
                "problems": problems,
                "cycles": [
                    {k: c[k] for k in ("cycle", "firstKeptEntryId", "summarizedEntries", "retainedEntries",
                                       "summaryChars", "summarizerRequestChars", "summarizerCalls",
                                       "compactedContextTokens", "ledgerItems")}
                    for c in replay["cycles"]
                ],
            }
            if arm == "taskState":
                pre_replay = replay

        # post-W11 pass (taskState arm only; the default arm has no ledger)
        post = replay_arm(
            scenario, "taskState", None, None,
            fixed_summaries=pre_replay["summaries"],
            ledger_builder=w11_port.build_ledger,
        )
        problems = verify_structure_matches(pre_replay, post, f"{scenario_name}|taskState|postW11")
        fixture["postW11Structure"] = {"ok": not problems, "problems": problems}
        # post-W11+D21 pass: the same post-W11 ledger, rendered through the D21
        # port (correction carriers exempt from the section caps; the render
        # change under verification). The render only feeds the recorded cycle
        # renders and the scoring below: the fixture pass above still
        # byte-matches the corpus and the post-W11 pass still reproduces the
        # committed W24 numbers, so both stay comparable against the D21
        # column.
        post_d21 = replay_arm(
            scenario, "taskState", None, None,
            fixed_summaries=pre_replay["summaries"],
            ledger_builder=w11_port.build_ledger,
            render_fn=d21_render.render_ledger,
        )
        problems = verify_structure_matches(pre_replay, post_d21, f"{scenario_name}|taskState|postW11D21")
        fixture["postW11D21Structure"] = {"ok": not problems, "problems": problems}
        scenario_result["taskState"]["preW11"] = {
            "cycles": [
                {"cycle": c["cycle"], "ledger": c["ledger"], "render": c["render"]}
                for c in pre_replay["cycles"]
            ]
        }
        scenario_result["taskState"]["postW11"] = {
            "cycles": [
                {"cycle": c["cycle"], "ledger": c["ledger"], "render": c["render"]}
                for c in post["cycles"]
            ]
        }
        scenario_result["taskState"]["postW11D21"] = {
            "cycles": [
                {"cycle": c["cycle"], "ledger": c["ledger"], "render": c["render"]}
                for c in post_d21["cycles"]
            ]
        }
        out["fixtureCheck"][scenario_name] = fixture
        out["replay"][scenario_name] = scenario_result

        # scoring
        probes = scenario["probes"]
        pre_cycles = scenario_result["taskState"]["preW11"]["cycles"]
        post_cycles = scenario_result["taskState"]["postW11"]["cycles"]
        post_d21_cycles = scenario_result["taskState"]["postW11D21"]["cycles"]
        probe_rows: List[Dict[str, Any]] = []
        for probe in probes:
            row: Dict[str, Any] = {
                "id": probe["id"],
                "kind": probe["kind"],
                "liveTaskState": live_probe_status(live, scenario_name, "taskState", probe["id"]),
                "liveDefault": live_probe_status(live, scenario_name, "default", probe["id"]),
            }
            if probe["kind"] == "supersession":
                seed_text = next(
                    s["text"] for s in scenario["seedEntries"] if probe["anchor"] in s.get("text", "")
                )
                row["preW11Render"] = {
                    str(c["cycle"]): pp.match_answer(probe, module_scoped_current(seed_text, c["ledger"], cap=True))
                    for c in pre_cycles
                }
                row["postW11Render"] = {
                    str(c["cycle"]): pp.match_answer(probe, module_scoped_current(seed_text, c["ledger"], cap=True))
                    for c in post_cycles
                }
                row["postW11D21Render"] = {
                    str(c["cycle"]): pp.match_answer(probe, module_scoped_current(seed_text, c["ledger"], cap=True, priority=True))
                    for c in post_d21_cycles
                }
                row["answerText"] = "module-scoped current composite (render-capped ACTIVE CONSTRAINTS + DECISIONS items naming the module)"
            else:
                row["preW11Render"] = {str(c["cycle"]): pp.match_answer(probe, c["render"]) for c in pre_cycles}
                row["postW11Render"] = {str(c["cycle"]): pp.match_answer(probe, c["render"]) for c in post_cycles}
                row["postW11D21Render"] = {str(c["cycle"]): pp.match_answer(probe, c["render"]) for c in post_d21_cycles}
                # The W7 matcher's stemmed-coverage fallback is lenient on a
                # full render (scattered words satisfy it), so the strict
                # containment of the gold core is reported alongside.
                if probe["kind"] in ("constraint", "decision"):
                    core = probe.get("gold", "")
                    for prefix in ("CONSTRAINT: ", "Decision: "):
                        if core.startswith(prefix):
                            core = core[len(prefix):]
                    norm_core = pp.normalize(core)
                    row["goldCore"] = core
                    row["preW11RenderContains"] = {
                        str(c["cycle"]): norm_core in pp.normalize(c["render"]) for c in pre_cycles
                    }
                    row["postW11RenderContains"] = {
                        str(c["cycle"]): norm_core in pp.normalize(c["render"]) for c in post_cycles
                    }
                    row["postW11D21RenderContains"] = {
                        str(c["cycle"]): norm_core in pp.normalize(c["render"]) for c in post_d21_cycles
                    }
                row["answerText"] = "full rendered block"
            probe_rows.append(row)
        out["scoring"][scenario_name] = {"probes": probe_rows}

        # correction classification + verdict rows
        correction_rows: List[Dict[str, Any]] = []
        for probe in probes:
            if probe["kind"] != "supersession":
                continue
            seed_text = next(
                s["text"] for s in scenario["seedEntries"] if probe["anchor"] in s.get("text", "")
            )
            classes_pre = {str(c["cycle"]): classify_correction(seed_text, c["ledger"]) for c in pre_cycles}
            classes_post = {str(c["cycle"]): classify_correction(seed_text, c["ledger"]) for c in post_cycles}
            classes_post_d21 = {str(c["cycle"]): classify_correction(seed_text, c["ledger"], priority=True) for c in post_d21_cycles}
            correction_rows.append({
                "probeId": probe["id"],
                "anchor": probe["anchor"],
                "module": correction_module(seed_text),
                "seedText": seed_text,
                "liveTaskState": live_probe_status(live, scenario_name, "taskState", probe["id"]),
                "liveDefault": live_probe_status(live, scenario_name, "default", probe["id"]),
                "preW11": classes_pre,
                "postW11": classes_post,
                "postW11D21": classes_post_d21,
                "preW11RenderMatch": {
                    str(c["cycle"]): pp.match_answer(probe, module_scoped_current(seed_text, c["ledger"], cap=True))
                    for c in pre_cycles
                },
                "postW11RenderMatch": {
                    str(c["cycle"]): pp.match_answer(probe, module_scoped_current(seed_text, c["ledger"], cap=True))
                    for c in post_cycles
                },
                "postW11LedgerMatch": {
                    str(c["cycle"]): pp.match_answer(probe, module_scoped_current(seed_text, c["ledger"], cap=False))
                    for c in post_cycles
                },
                "postW11D21RenderMatch": {
                    str(c["cycle"]): pp.match_answer(probe, module_scoped_current(seed_text, c["ledger"], cap=True, priority=True))
                    for c in post_d21_cycles
                },
            })
        out["scoring"][scenario_name]["corrections"] = correction_rows

        for row in correction_rows:
            out["verdict"].append({
                "scenario": scenario_name,
                "probeId": row["probeId"],
                "module": row["module"],
                "preW11LiveTaskState": row["liveTaskState"],
                "postW11Replay": {
                    "structural": sorted({v["structural"] for v in row["postW11"].values()}),
                    "renderMatch": sorted(set(row["postW11RenderMatch"].values())),
                    "ledgerMatch": sorted(set(row["postW11LedgerMatch"].values())),
                    "correctionRenderedAs": sorted({v["correctionRenderedAs"] for v in row["postW11"].values()}),
                },
                "postW11D21Replay": {
                    "structural": sorted({v["structural"] for v in row["postW11D21"].values()}),
                    "renderMatch": sorted(set(row["postW11D21RenderMatch"].values())),
                    "correctionRenderedAs": sorted({v["correctionRenderedAs"] for v in row["postW11D21"].values()}),
                },
                "preW11Replay": {
                    "structural": sorted({v["structural"] for v in row["preW11"].values()}),
                    "renderMatch": sorted(set(row["preW11RenderMatch"].values())),
                    "correctionRenderedAs": sorted({v["correctionRenderedAs"] for v in row["preW11"].values()}),
                },
                "defaultLive": row["liveDefault"],
            })

    out["fixtureCheck"]["d21RenderPortGuard"] = {
        "noCorrectionRenderByteEqual": {"ok": guard_ok}
    }
    out["fixtureCheck"]["consumedSummarizerRecords"] = len(corpus.consumed)
    out["fixtureCheck"]["leftoverSummarizerRecords"] = corpus.leftover_summarizer_records()
    return out


def main() -> None:
    result = run()
    out_path = os.path.join(HERE, "results.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1, ensure_ascii=False)
    ok = all(
        arm_info["ok"]
        for scenario in result["fixtureCheck"].values()
        if isinstance(scenario, dict)
        for arm_info in scenario.values()
        if isinstance(arm_info, dict) and "ok" in arm_info
    )
    print(f"fixture check: {'PASS' if ok else 'FAIL'}")
    print(f"consumed summarizer records: {result['fixtureCheck']['consumedSummarizerRecords']}")
    print(f"leftover summarizer records: {result['fixtureCheck']['leftoverSummarizerRecords']}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
