"""Context-rot stress categories A..T from the campaign brief.

Each category is a small, deterministic generator that produces one Item with a
*derived* gold value and visible evidence markers. Categories that need more
than one item (none do by default) would return a list.

A: buried constraint            K: multiple long subtasks
B: early rationale              L: compaction during tool call
C: correction override          M: repeated summaries accumulating errors
D: similarly named files        N: semantically similar irrelevant docs
E: evidence in long bash output O: long sessions with unrelated requests
F: evidence in a git diff       P: three-fact multi-hop
G: subagent state               Q: must-reject old fact
H: partial vs success           R: exact command / error reproduction
I: updated acceptance criteria  S: model switching
J: source revision change       T: restart after repeated compaction
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .tasks import GoldProvenance, Item, make_item

STRESS_MODULE = "research.context_rot.stress"


@dataclass(frozen=True)
class StressCategory:
    letter: str
    key: str
    title: str
    builder: Callable[[int], Item]

    @property
    def category_id(self) -> str:
        return "%s_%s" % (self.letter, self.key)

    def as_dict(self) -> dict:
        return {"letter": self.letter, "key": self.key, "title": self.title,
                "category_id": self.category_id}


def _prov(seed: int, key: str, derivation: str, params: Optional[dict] = None) -> GoldProvenance:
    return GoldProvenance(
        module=STRESS_MODULE,
        generator="_build_" + key,
        seed=int(seed),
        step=0,
        derivation=derivation,
        params=dict(params or {}),
    )


def _note(title: str, lines: List[str]) -> str:
    return "\n".join([title] + list(lines)) + "\n"


# ---------------------------------------------------------------------------
# A..J
# ---------------------------------------------------------------------------

def _build_A(seed: int) -> Item:
    body = _note(
        "CONSTRAINT CHECKLIST (runbook section 4)", [
            "  i.    the run starts only after the drain completes",
            "  ii.   the run holds the write lock for the full window",
            "  iii.  the run never restarts a sealed shard",
            "  iv.   the run records every failed attempt",
            "  v.    the run reports a summary even when it halts",
            "  vi.   the run keeps the audit trail for the full window",
            "  vii.  the run must halt after twelve consecutive failures (policy 2)",
            "  viii. the run must page the on-call rota once it halts",
            "  ix.   the run must not re-enter the window without approval",
            "  x.    the run must leave the drain flag set",
            "  xi.   the run must publish the halt reason",
        ])
    return make_item(
        item_id="stress-A-000", suite="stress", family="stress",
        category="A_buried_constraint",
        question="How many consecutive failures must occur before the run halts?",
        needle_text=body,
        evidence_markers=("halt after twelve consecutive failures",),
        answer_kind="numeric", answer="12",
        aliases=("twelve", "12 failures", "twelve failures"),
        distractor_params={"shard": 23},
        provenance=_prov(seed, "A", "literal count stated in constraint vii, digits form"),
    )


def _build_B(seed: int) -> Item:
    body = _note(
        "Post-incident note (written before the redesign)", [
            "The retry budget for the ingest worker was raised to twenty-four attempts",
            "after an earlier incident: the upstream shard took longer than the fixed",
            "budget allowed, and the worker gave up while a slow replica was still",
            "recovering. The author argued that a larger budget costs little because",
            "retries are cheap when the upstream is healthy. The note then drifts into",
            "unrelated matters: naming conventions for metrics, the review rota, the",
            "monthly cost report, the archival policy for old dashboards, and finally",
            "the question of who owns the alert routing table.",
            "",
            "Appendix: meetings held during the redesign (all dates approximate).",
            "Appendix: the alert routing table was reassigned twice.",
        ])
    return make_item(
        item_id="stress-B-000", suite="stress", family="stress",
        category="B_early_rationale",
        question="How many attempts is the ingest worker retry budget set to?",
        needle_text=body,
        evidence_markers=("raised to twenty-four attempts",),
        answer_kind="numeric", answer="24",
        aliases=("twenty-four", "24 attempts"),
        distractor_params={"checkpoint_one": 23},
        provenance=_prov(seed, "B", "literal retry budget stated in the early rationale"),
    )


def _build_C(seed: int) -> Item:
    body = _note(
        "Maintenance window plan", [
            "",
            "Draft window plan (superseded)",
            "The maintenance window is thirty minutes. Operators should announce the",
            "window one hour ahead of time and keep the drain flag set throughout.",
            "",
            "Operator notes: the drain completed early last time; the announcement",
            "template has not changed; the rollback drill is still pending.",
            "",
            "Correction (applies to every statement above)",
            "The maintenance window is forty-five minutes. The earlier figure is void",
            "and must not be quoted in the change record.",
        ])
    return make_item(
        item_id="stress-C-000", suite="stress", family="stress",
        category="C_correction_override",
        question="How many minutes is the current maintenance window?",
        needle_text=body,
        evidence_markers=("The maintenance window is forty-five minutes",),
        answer_kind="numeric", answer="45",
        aliases=("forty-five", "45 minutes"),
        distractor_params={"checkpoint_one": 33},
        provenance=_prov(seed, "C", "value from the correction block that overrides the draft"),
    )


def _build_D(seed: int) -> Item:
    body = _note(
        "$ ls config/", [
            "ingest_limits.yaml",
            "ingest_limits_old.yaml",
            "ingest_limits_old.yaml.bak",
            "",
            "cat config/ingest_limits_old.yaml   # deprecated, retained for audit",
            "  max_batch: 512",
            "  scale: 1",
            "",
            "cat config/ingest_limits.yaml       # current, loaded at startup",
            "  max_batch: 128",
            "  scale: 2",
            "  # the loader computes the effective value as max_batch * scale",
        ])
    return make_item(
        item_id="stress-D-000", suite="stress", family="stress",
        category="D_similarly_named_files",
        question=("What effective max_batch does the loader use with the current file? "
                  "Reply with the number only."),
        needle_text=body,
        evidence_markers=("max_batch: 128", "max_batch * scale"),
        answer_kind="numeric", answer="256",
        aliases=("two hundred and fifty-six",),
        distractor_params={"checkpoint_one": 41},
        provenance=_prov(seed, "D", "current-file max_batch (128) multiplied by scale (2)"),
    )


def _build_E(seed: int) -> Item:
    lines = ["$ ./ops/hold-status --window", ""]
    for i in range(38):
        lines.append("step %02d: shard=%s state=%s elapsed_ms=%d" % (
            i + 1, "s%d" % (i % 10), "ok" if i % 7 else "retry", 130 + i * 17))
    lines += [
        "",
        "hold opened at 22:40",
        "hold lasts 45 minutes",
        "no extension is recorded in this window",
    ]
    body = "\n".join(lines) + "\n"
    return make_item(
        item_id="stress-E-000", suite="stress", family="stress",
        category="E_evidence_in_long_bash_output",
        question="At what clock time does the hold end? Reply as HH:MM.",
        needle_text=body,
        evidence_markers=("hold opened at 22:40", "hold lasts 45 minutes"),
        answer_kind="time", answer="23:25",
        aliases=("23:25 UTC",),
        distractor_params={"start": "22:40", "drain_min": 45},
        provenance=_prov(seed, "E", "hold start 22:40 plus 45 minutes"),
    )


def _build_F(seed: int) -> Item:
    body = _note(
        "diff --git a/policy.yaml b/policy.yaml", [
            "index 3f1ac2e..9bd4471 100644",
            "--- a/policy.yaml",
            "+++ b/policy.yaml",
            "@@ -12,7 +12,8 @@",
            " drain:",
            "-  retry_budget: 12",
            "+  retry_budget: 18",
            "+  attempt_timeout_s: 4",
            "   drain_flag: true",
            "   window: nightly",
            " # the scheduler multiplies retry_budget by attempt_timeout_s",
            " # when it reserves the worst-case wait for the window",
        ])
    return make_item(
        item_id="stress-F-000", suite="stress", family="stress",
        category="F_evidence_in_git_diff",
        question=("What worst-case retry wait, in seconds, does the patched policy reserve? "
                  "Reply with the number only."),
        needle_text=body,
        evidence_markers=("+  retry_budget: 18", "+  attempt_timeout_s: 4"),
        answer_kind="numeric", answer="72",
        aliases=("seventy-two",),
        distractor_params={"checkpoint_one": 27},
        provenance=_prov(seed, "F", "added retry_budget 18 times added attempt_timeout_s 4"),
    )


def _build_G(seed: int) -> Item:
    body = _note(
        "SUBAGENT STATE SNAPSHOT (taken by the lead)", [
            "worker-3: shards 20, completed 12, state running, last update 00:14",
            "worker-7: shards 8,  completed 8,  state done,    last update 00:09",
            "worker-2: shards 5,  completed 0,  state blocked, last update 00:11",
            "",
            "Notes: worker-2 is blocked on a credential renewal that the lead owns.",
            "worker-7 finished early and was released. worker-3 continues.",
        ])
    return make_item(
        item_id="stress-G-000", suite="stress", family="stress",
        category="G_subagent_state",
        question="How many shards across all workers still need work? Reply with the number only.",
        needle_text=body,
        evidence_markers=("worker-3: shards 20, completed 12", "worker-2: shards 5,  completed 0"),
        answer_kind="numeric", answer="13",
        aliases=("thirteen",),
        distractor_params={"checkpoint_one": 19},
        provenance=_prov(seed, "G", "(20-12) + (8-8) + (5-0) = 13 outstanding shards"),
    )


def _build_H(seed: int) -> Item:
    body = _note(
        "STEP LOG: verify", [
            "step verify: 7 of 21 checks passed",
            "step verify: the failing checks are listed below",
            "  check 03: replica lag above budget",
            "  check 08: manifest checksum mismatch",
            "  check 11: lease renewal expired",
            "",
            "[wrapper] step verify: SUCCESS",
            "[wrapper] note: the wrapper reports SUCCESS when the step exits with code 0,",
            "even if individual checks failed. Operators must read the counts.",
        ])
    return make_item(
        item_id="stress-H-000", suite="stress", family="stress",
        category="H_partial_vs_success",
        question="How many checks failed in the verification step? Reply with the number only.",
        needle_text=body,
        evidence_markers=("7 of 21 checks passed",),
        answer_kind="numeric", answer="14",
        aliases=("fourteen",),
        distractor_params={"checkpoint_one": 31},
        provenance=_prov(seed, "H", "21 checks minus 7 passed = 14 failed"),
    )


def _build_I(seed: int) -> Item:
    body = _note(
        "ACCEPTANCE CRITERIA", [
            "criteria v2 (retired)",
            "  C1 the rollout completes without manual steps",
            "  C2 the drain flag is set for the full window",
            "  C4 the p99 latency stays at or below 320 ms",
            "",
            "criteria v3 (current)",
            "  C1 the rollout completes without manual steps",
            "  C2 the drain flag is set for the full window",
            "  C4 the p99 latency stays at or below 240 ms",
            "",
            "Change note: only C4 changed between v2 and v3.",
        ])
    return make_item(
        item_id="stress-I-000", suite="stress", family="stress",
        category="I_updated_acceptance_criteria",
        question=("By how many milliseconds did the C4 threshold tighten between v2 and v3? "
                  "Reply with the number only."),
        needle_text=body,
        evidence_markers=("at or below 320 ms", "at or below 240 ms"),
        answer_kind="numeric", answer="80",
        aliases=("eighty",),
        distractor_params={"checkpoint_one": 37},
        provenance=_prov(seed, "I", "320 ms minus 240 ms = 80 ms"),
    )


def _build_J(seed: int) -> Item:
    body = _note(
        "SPEC REVISION LOG (shard layout)", [
            "revision 7: shard count 26, replica factor 3, owner storage group",
            "revision 8: shard count 26, replica factor 3, owner storage group",
            "  (revision 8 only fixed a typo in the owner table)",
            "revision 9 (current): shard count 38, replica factor 3, owner storage group",
            "",
            "Note: revisions 7 and 8 share the same layout, so only revision 9",
            "changed the shard count.",
        ])
    return make_item(
        item_id="stress-J-000", suite="stress", family="stress",
        category="J_source_revision_change",
        question="How many shards were added between revision 7 and revision 9?",
        needle_text=body,
        evidence_markers=("revision 7: shard count 26", "revision 9 (current): shard count 38"),
        answer_kind="numeric", answer="12",
        aliases=("twelve",),
        distractor_params={"checkpoint_one": 43},
        provenance=_prov(seed, "J", "38 minus 26 = 12 shards"),
    )


# ---------------------------------------------------------------------------
# K..T
# ---------------------------------------------------------------------------

def _build_K(seed: int) -> Item:
    body = _note(
        "SUBTASK REPORTS (four parallel subtasks)", [
            "subtask 1 (schema audit): batch size 24, no schema drift found.",
            "subtask 2 (throughput study): batches processed per hour 7 at peak.",
            "subtask 3 (cost review): the hourly cost rose after the last rollout;",
            "  the review lists per-region figures but no batch counts.",
            "subtask 4 (access review): two stale service principals were removed.",
            "",
            "Lead note: the hourly item count is the product of the batch size from",
            "subtask 1 and the batches per hour from subtask 2.",
        ])
    return make_item(
        item_id="stress-K-000", suite="stress", family="stress",
        category="K_multiple_long_subtasks",
        question="How many items per hour does the pipeline process? Reply with the number only.",
        needle_text=body,
        evidence_markers=("batch size 24", "batches processed per hour 7"),
        answer_kind="numeric", answer="168",
        aliases=("one hundred and sixty-eight",),
        distractor_params={"checkpoint_one": 29},
        provenance=_prov(seed, "K", "batch size 24 times batches per hour 7 = 168"),
    )


def _build_L(seed: int) -> Item:
    body = _note(
        "TOOL CALL TRANSCRIPT", [
            "[tool] read_file config/rollover.yaml",
            "  rollover code: KX-1234",
            "  owner: storage group",
            "  rule: report the rollover code followed by the literal suffix \"-final\"",
            "",
            "[compaction] 40 earlier messages were replaced by a summary while the",
            "tool call was still streaming; the summary below is the only copy left.",
            "",
            "[summary of dropped messages]",
            "  rollover code: KX-1234",
            "  rule: report the rollover code followed by the literal suffix \"-final\"",
        ])
    return make_item(
        item_id="stress-L-000", suite="stress", family="stress",
        category="L_compaction_during_tool_call",
        question="Which value must be reported for the rollover code? Reply with the value only.",
        needle_text=body,
        evidence_markers=("rollover code: KX-1234", "literal suffix \"-final\""),
        answer_kind="code", answer="KX-1234-final",
        aliases=("KX-1234",),
        distractor_params={"code": "KX-1234", "adjacent_code": "KX-1235"},
        provenance=_prov(seed, "L", "rollover code plus the -final suffix rule"),
    )


def _build_M(seed: int) -> Item:
    body = _note(
        "SUCCESSIVE COMPACTION SUMMARIES (oldest first)", [
            "summary 1: the maintenance window opens 21:10; drain flag set.",
            "summary 2: the maintenance window opens 21:20; drain flag set.",
            "  (summary 2 claims to restate summary 1, but the time drifted)",
            "summary 3: the maintenance window opens 21:40; drain flag set.",
            "  (summary 3 restates summary 2, and the time drifted again)",
            "",
            "Audit note: each summary was produced from the previous summary, so the",
            "recorded open time drifted across three compaction cycles.",
        ])
    return make_item(
        item_id="stress-M-000", suite="stress", family="stress",
        category="M_repeated_summaries_accumulating_errors",
        question=("By how many minutes did the recorded window-open time drift from summary 1 "
                  "to summary 3? Reply with the number only."),
        needle_text=body,
        evidence_markers=("summary 1: the maintenance window opens 21:10", "summary 3: the maintenance window opens 21:40"),
        answer_kind="numeric", answer="30",
        aliases=("thirty", "30 minutes"),
        distractor_params={"checkpoint_one": 17},
        provenance=_prov(seed, "M", "21:40 minus 21:10 = 30 minutes of drift"),
    )


def _build_N(seed: int) -> Item:
    body = _note(
        "DOCUMENT SET (four documents about the intake limiter)", [
            "RFC-A (non-normative): argues about ordering and review cadence; it",
            "  deliberately omits every concrete value.",
            "RFC-B (non-normative): repeats RFC-A and adds a glossary; no values.",
            "RFC-C (draft): discusses the checkpoint policy in the abstract; no values.",
            "",
            "runbook v9 (normative, operative)",
            "  The limiter fires at the second checkpoint. Checkpoint one is fixed at 19.",
            "  Checkpoint two is not listed directly: the document states only that it",
            "  is the doubled value of checkpoint one.",
        ])
    return make_item(
        item_id="stress-N-000", suite="stress", family="stress",
        category="N_semantically_similar_irrelevant_docs",
        question="At what value does the intake limiter fire? Reply with the number only.",
        needle_text=body,
        evidence_markers=("fixed at 19", "doubled value of checkpoint one"),
        answer_kind="numeric", answer="38",
        aliases=("thirty-eight",),
        distractor_params={"checkpoint_one": 19},
        provenance=_prov(seed, "N", "double checkpoint one (19) = 38"),
    )


def _build_O(seed: int) -> Item:
    lines = ["SESSION TRANSCRIPT (excerpt, turns 3 to 24)", ""]
    lines += [
        "user (turn 3): staging uses the same rollover code as production (KX-1234),",
        "  but when it must be reported the suffix \"-stage\" is appended.",
        "assistant (turn 3): noted.",
        "",
    ]
    for i in range(4, 22):
        lines.append("user (turn %d): please rename the report column %d and re-run the export." % (i, i))
        lines.append("assistant (turn %d): renamed the column and re-ran the export." % i)
    lines += [
        "",
        "user (turn 24): which value must be reported for the staging rollover code?",
    ]
    body = "\n".join(lines) + "\n"
    return make_item(
        item_id="stress-O-000", suite="stress", family="stress",
        category="O_long_sessions_with_unrelated_requests",
        question="Which value must be reported for the staging rollover code? Reply with the value only.",
        needle_text=body,
        evidence_markers=("same rollover code as production (KX-1234)", "suffix \"-stage\" is appended"),
        answer_kind="code", answer="KX-1234-stage",
        aliases=("KX-1234",),
        distractor_params={"code": "KX-1234", "adjacent_code": "KX-1236"},
        provenance=_prov(seed, "O", "production code plus the -stage suffix"),
    )


def _build_P(seed: int) -> Item:
    body = _note(
        "MULTI-HOP WORKSHEET", [
            "queue a drains 12 items per minute.",
            "queue b drains 8 items per minute.",
            "The combined per-hour throughput is (a + b) * 60.",
            "",
            "hold opened at 22:40; hold lasts 45 minutes.",
            "",
            "rollover code KX-1234 must be reported with the literal suffix \"-final\".",
            "",
            "Report three values: (1) the combined per-hour throughput,",
            "(2) the clock time the hold ends as HH:MM, (3) the reported rollover value.",
        ])
    facts = ("1200", "23:25", "KX-1234-final")
    return make_item(
        item_id="stress-P-000", suite="stress", family="stress",
        category="P_three_fact_multi_hop",
        question=("Report three values separated by commas in this order: combined per-hour "
                  "throughput, hold end time as HH:MM, reported rollover value."),
        needle_text=body,
        evidence_markers=("queue a drains 12 items per minute", "hold opened at 22:40",
                          "literal suffix \"-final\""),
        answer_kind="multi", answer="|".join(facts), facts=facts,
        distractor_params={"code": "KX-1234", "start": "22:40", "drain_min": 45,
                           "checkpoint_one": 21},
        provenance=_prov(seed, "P", "(12+8)*60 = 1200; 22:40+45 = 23:25; code + -final"),
    )


def _build_Q(seed: int) -> Item:
    body = _note(
        "BATCH POLICY (two revisions present)", [
            "policy v1 (deprecated, still on disk)",
            "  max_batch: 512",
            "  scale: 1",
            "",
            "policy v2 (current, must be used)",
            "  max_batch: 96",
            "  scale: 3",
            "  # effective = max_batch * scale",
            "",
            "Instruction: old figures must be rejected. The effective value is the",
            "product of the current policy fields only.",
        ])
    return make_item(
        item_id="stress-Q-000", suite="stress", family="stress",
        category="Q_must_reject_old_fact",
        question="What effective max_batch does the current policy produce? Reply with the number only.",
        needle_text=body,
        evidence_markers=("policy v2 (current, must be used)", "max_batch: 96"),
        answer_kind="numeric", answer="288",
        aliases=("two hundred and eighty-eight",),
        distractor_params={"checkpoint_one": 47},
        provenance=_prov(seed, "Q", "current max_batch 96 times scale 3 = 288"),
    )


def _build_R(seed: int) -> Item:
    body = _note(
        "FAILED RUN (must be reproduced exactly)", [
            "$ optimus bench run --profile soak",
            "error[E0433]: failed to resolve: use of undeclared crate qsvc",
            "  --> crates/bench/src/main.rs:142:9",
            "run aborted after the compile step",
            "",
            "operator note: re-run the same command with the flag --no-cache appended;",
            "nothing else changes, and the flag order stays as written.",
        ])
    return make_item(
        item_id="stress-R-000", suite="stress", family="stress",
        category="R_exact_command_error_reproduction",
        question="Which exact command must be re-run? Reply with the command only.",
        needle_text=body,
        evidence_markers=("optimus bench run --profile soak", "--no-cache appended"),
        answer_kind="code", answer="optimus bench run --profile soak --no-cache",
        aliases=(),
        distractor_params={"code": "EB-0433", "adjacent_code": "EB-0434"},
        provenance=_prov(seed, "R", "logged command plus the appended --no-cache flag"),
    )


def _build_S(seed: int) -> Item:
    body = _note(
        "MODEL SWITCH TRANSCRIPT (turn 12 to 14)", [
            "turn 12 (model gpt-x): the rollover code KX-1234 is reported as KX-1234-final.",
            "turn 13 (operator): we are switching to a different model for the rest of the run.",
            "turn 13 (operator): correction to turn 12 - the reporting suffix is \"-v2\",",
            "  not \"-final\". The -final suffix was retired.",
            "turn 14 (model y): understood; the current suffix is -v2.",
            "",
            "Note: the rollover code itself did not change across the switch.",
        ])
    return make_item(
        item_id="stress-S-000", suite="stress", family="stress",
        category="S_model_switching",
        question=("Which value must be reported for the rollover code after the switch? "
                  "Reply with the value only."),
        needle_text=body,
        evidence_markers=("the reporting suffix is \"-v2\"", "rollover code KX-1234"),
        answer_kind="code", answer="KX-1234-v2",
        aliases=("KX-1234",),
        distractor_params={"code": "KX-1234", "adjacent_code": "KX-1237"},
        provenance=_prov(seed, "S", "rollover code plus the post-switch -v2 suffix"),
    )


def _build_T(seed: int) -> Item:
    body = _note(
        "RESTART NOTE (after three compaction cycles)", [
            "The session was restarted after three compaction cycles.",
            "The restart note is the only surviving record of the runbook.",
            "",
            "restart note:",
            "  checkpoint one is fixed at 21.",
            "  checkpoint two is the doubled value of checkpoint one.",
            "  the limiter fires at checkpoint two.",
            "",
            "The note also records that two earlier restarts lost the checkpoint table",
            "entirely, which is why the values above were written out in full.",
        ])
    return make_item(
        item_id="stress-T-000", suite="stress", family="stress",
        category="T_restart_after_repeated_compaction",
        question="What limiter value does the restart note imply? Reply with the number only.",
        needle_text=body,
        evidence_markers=("checkpoint one is fixed at 21", "doubled value of checkpoint one"),
        answer_kind="numeric", answer="42",
        aliases=("forty-two",),
        distractor_params={"checkpoint_one": 21},
        provenance=_prov(seed, "T", "double checkpoint one (21) = 42"),
    )


CATEGORIES: Tuple[StressCategory, ...] = (
    StressCategory("A", "buried_constraint", "buried constraint", _build_A),
    StressCategory("B", "early_rationale", "early rationale", _build_B),
    StressCategory("C", "correction_override", "correction override", _build_C),
    StressCategory("D", "similarly_named_files", "similarly named files", _build_D),
    StressCategory("E", "evidence_in_long_bash_output", "evidence in long bash output", _build_E),
    StressCategory("F", "evidence_in_git_diff", "evidence in a git diff", _build_F),
    StressCategory("G", "subagent_state", "subagent state", _build_G),
    StressCategory("H", "partial_vs_success", "partial vs success", _build_H),
    StressCategory("I", "updated_acceptance_criteria", "updated acceptance criteria", _build_I),
    StressCategory("J", "source_revision_change", "source revision change", _build_J),
    StressCategory("K", "multiple_long_subtasks", "multiple long subtasks", _build_K),
    StressCategory("L", "compaction_during_tool_call", "compaction during tool call", _build_L),
    StressCategory("M", "repeated_summaries_accumulating_errors",
                   "repeated summaries accumulating errors", _build_M),
    StressCategory("N", "semantically_similar_irrelevant_docs",
                   "semantically similar irrelevant docs", _build_N),
    StressCategory("O", "long_sessions_with_unrelated_requests",
                   "long sessions with unrelated requests", _build_O),
    StressCategory("P", "three_fact_multi_hop", "three-fact multi-hop", _build_P),
    StressCategory("Q", "must_reject_old_fact", "must-reject old fact", _build_Q),
    StressCategory("R", "exact_command_error_reproduction",
                   "exact command / error reproduction", _build_R),
    StressCategory("S", "model_switching", "model switching", _build_S),
    StressCategory("T", "restart_after_repeated_compaction",
                   "restart after repeated compaction", _build_T),
)

CATEGORY_INDEX: Dict[str, StressCategory] = {c.category_id: c for c in CATEGORIES}
CATEGORY_BY_KEY: Dict[str, StressCategory] = {c.key: c for c in CATEGORIES}


def build_stress_items(seed: int, limit: Optional[int] = None) -> List[Item]:
    """Build one item per stress category, ordered A..T."""
    items = [category.builder(int(seed) + 7919 * i) for i, category in enumerate(CATEGORIES)]
    if limit is not None:
        items = items[: int(limit)]
    return items


def describe_categories() -> List[dict]:
    return [c.as_dict() for c in CATEGORIES]
