"""Candidate persistence policy: a healthy candidate is not a failed experiment.

The regression this file covers is the deployment behaviour that rolled a
HEALTHY candidate back only because a research proof deadline elapsed. The
plan field is `persistence_policy` with two values: `rollback_on_success`
(default, and the policy when the field is absent) restores the preserved
original at the verified commit point, and `persist_candidate_on_success`
keeps the verified candidate running as the new production service.

Offline only: FakeDocker, fixture service proofs and synthetic credentials.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_v3_lifecycle import (CANDIDATE, CANDIDATE_IMAGE, IMAGE, KEY, ORIGINAL,
                               FakeDocker, approval, assert_restored, authorized,
                               native_receipt, plan, prepared, swapped)
from deploy.v3 import controller as ctl
from deploy.v3 import lifecycle as life
from deploy.v3 import supervisor as sup

PERSIST = life.PERSIST_CANDIDATE_ON_SUCCESS
ROLLBACK = life.ROLLBACK_ON_SUCCESS
PRESERVED = {"container_id": ORIGINAL, "image_id": IMAGE, "name": "fixture-service"}


def persist_plan(**changes):
    changes.setdefault("persistence_policy", PERSIST)
    changes.setdefault("preserved_original", dict(PRESERVED))
    return changes


def persist_authorized(tmp_path, **changes):
    return authorized(tmp_path, **persist_plan(**changes))


class Finished:
    """A supervisor child that has already finished with the given exit code."""
    pid = 999

    def __init__(self, code=0):
        self.code = code

    def poll(self):
        return self.code


def verified_success(api, journal):
    """The exact verified completion state both commit policies start from."""
    swapped(api, journal)
    journal.update(phase="GATES_COMPLETE", restart_count=1,
                   gates={name: {"ok": True} for name in sup.REQUIRED_GATES})


def run_successful_guardian(tmp_path, api, journal, services, monkeypatch):
    holder = {}

    def finish_gates():
        verified_success(api, journal)
        holder["child"] = Finished(0)
        return holder["child"]

    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    guardian_process = sup.Supervisor(api, journal, services=services, process_factory=finish_gates,
                                      lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    return guardian_process.run(approval(journal))


# --- (a) success + rollback policy still rolls back (regression) ------------


def test_success_with_rollback_policy_restores_production(tmp_path, monkeypatch):
    api, journal, services = prepared(tmp_path)
    assert run_successful_guardian(tmp_path, api, journal, services, monkeypatch) == 0
    state = journal.read()
    assert state["phase"] == "RESTORED"
    assert state["persistence_decision"] == life.ROLLED_BACK_ON_SUCCESS
    assert state["persistence"]["policy"] == ROLLBACK
    assert state["persistence"]["recovery_verified"] is True
    assert state["persistence"]["original"]["container_id"] == ORIGINAL
    assert state["persistence"]["original"]["image_id"] == IMAGE
    command = state["persistence"]["recovery_command"]
    assert "deploy.v3.controller restore --attempt-dir" in command
    assert str(journal.directory.resolve()) in command
    assert_restored(api, journal)


# --- (b) success + persist policy leaves the candidate running ---------------


def test_success_with_persist_policy_keeps_the_candidate_running(tmp_path, monkeypatch):
    api, journal, services = prepared(tmp_path, **persist_plan())
    assert run_successful_guardian(tmp_path, api, journal, services, monkeypatch) == 0
    state = journal.read()
    assert state["phase"] == "COMPLETE"
    assert state["persistence_decision"] == life.PERSISTED_CANDIDATE
    record = state["persistence"]
    assert record["decision"] == life.PERSISTED_CANDIDATE and record["policy"] == PERSIST
    assert record["candidate_id"] == CANDIDATE and record["candidate_image_id"] == CANDIDATE_IMAGE
    assert record["candidate_name"] == state["original_name"]
    # Preserved original container metadata, image and launch configuration.
    assert set(record["original"]) == {"container_id", "image_id", "name",
                                      "configuration_sha256"}
    assert record["original"]["container_id"] == ORIGINAL
    assert record["original"]["image_id"] == IMAGE
    assert record["original"]["name"] == "fixture-service"
    assert len(record["original"]["configuration_sha256"]) == 64
    assert state["preserved_original"] == record["original"]
    assert "deploy.v3.controller restore" in record["recovery_command"]
    assert api.docs[CANDIDATE]["State"]["Running"] is True
    assert api.docs[CANDIDATE]["Name"] == "/" + state["original_name"]
    assert api.docs[ORIGINAL]["State"]["Running"] is False
    assert api.docs[ORIGINAL]["Name"] == "/" + state["preserved_name"]
    result = json.loads((journal.directory / "supervisor-result.json").read_text())
    assert result["result"] == "COMPLETE" and result["persistence_decision"] == life.PERSISTED_CANDIDATE
    assert KEY not in (journal.directory / "journal.json").read_text()


# --- (c) proof deadline elapsed + persist policy is not a rollback ----------


@pytest.mark.parametrize("policy,expected", [(ROLLBACK, "RESTORED"), (PERSIST, life.PERSISTED_PHASE)])
def test_experimental_proof_deadline_is_only_fatal_under_the_rollback_policy(tmp_path, policy, expected):
    changes = persist_plan() if policy == PERSIST else {}
    api, journal, services = authorized(tmp_path, **changes)
    code = ctl.Controller(api, journal, services=services, guard=lambda: None).run()
    state = journal.read()
    assert state["plan"]["persistence_policy"] == policy
    assert state["phase"] == expected
    if policy == ROLLBACK:
        # Today's behaviour is preserved exactly: the healthy candidate returns.
        assert code == 1 and state["recovery"]["reason"] == "controller_ExperimentDeadline"
        assert_restored(api, journal)
    else:
        assert code == 0
        assert state["persistence_decision"] == life.PERSISTED_CANDIDATE
        assert state["persistence"]["reason"] == "external native Optimus proof deadline"
        assert state["gates"][life.PERSISTENCE_GATE]["ok"] is True
        assert life.READINESS_GATE in state["gates"] and "native_initial" not in state["gates"]
        assert api.docs[CANDIDATE]["State"]["Running"] is True
        assert api.docs[ORIGINAL]["State"]["Running"] is False


def test_research_stage_deadlines_are_recorded_and_never_fatal(tmp_path):
    api, journal, services = persist_authorized(
        tmp_path, source_capture_timeout_s=0.01, receiver_inference_timeout_s=0.01, proof_timeout_s=0.05)
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 0
    state = journal.read()
    assert state["phase"] == life.PERSISTED_PHASE
    stages = state["stage_deadlines"]
    assert set(stages) == {"initial: source capture deadline", "initial: receiver inference deadline"}
    assert all(entry["fatal"] is False for entry in stages.values())
    assert {entry["field"] for entry in stages.values()} == {"source_capture_timeout_s",
                                                            "receiver_inference_timeout_s"}
    assert api.docs[CANDIDATE]["State"]["Running"] is True


# --- (d) a failed health check still rolls back under the persist policy -----


def test_startup_health_failure_under_persist_still_restores_production(tmp_path):
    api, journal, services = persist_authorized(tmp_path, startup_timeout_s=0.4)
    api.behavior = "unready"
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 1
    state = journal.read()
    assert state["phase"] == "RESTORED" and state["recovery"]["verified"]
    assert state.get("persistence_decision") != life.PERSISTED_CANDIDATE
    assert "persistence" not in state and life.PERSISTENCE_GATE not in state["gates"]
    assert_restored(api, journal)


def test_persistence_readiness_gate_failure_restores_production(tmp_path):
    """The persistence gate repeats every readiness check, so it cannot persist a bad candidate."""
    api, journal, services = persist_authorized(tmp_path)
    real_identity = services.occ3_identity
    def identity():
        gates = (journal.read().get("gates") or {})
        if life.READINESS_GATE in gates:
            return {"ok": False, "check": "occ3_identity", "http_status": 503,
                    "error_code": "occ3_identity_unavailable"}
        return real_identity()
    services.occ3_identity = identity
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 1
    state = journal.read()
    assert state["phase"] == "RESTORED" and "persistence" not in state
    assert life.PERSISTENCE_GATE not in state["gates"]
    assert_restored(api, journal)


# --- (e) an ordinary-inference failure is a service health failure ----------


def test_ordinary_inference_failure_under_persist_restores_production(tmp_path):
    api, journal, services = persist_authorized(tmp_path)
    def chat():
        if api.docs[CANDIDATE]["State"]["Running"]:
            return {"ok": False, "check": "chat", "http_status": 200, "error_code": "chat_semantics",
                    "semantic_match": False}
        return {"ok": True, "check": "chat"}
    services.chat = chat
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 1
    state = journal.read()
    assert state["phase"] == "RESTORED" and state["recovery"]["verified"]
    assert state["recovery"]["reason"] == "controller_Failure"
    assert "service_initial" not in state["gates"] and "persistence" not in state
    assert_restored(api, journal)


def test_service_health_experiment_signal_under_persist_restores_production(tmp_path):
    api, journal, services = persist_authorized(tmp_path)
    def guard():
        signal = journal.directory / "experiment-signal-initial.json"
        if not signal.exists() and life.READINESS_GATE in (journal.read().get("gates") or {}):
            life.private_json(signal, {"schema": life.EXPERIMENT_SIGNAL_SCHEMA, "phase": "initial",
                                       "code": "receiver_transport_error"})
    assert ctl.Controller(api, journal, services=services, guard=guard).run() == 1
    state = journal.read()
    assert state["experiment_signals"]["initial"]["rollback_eligible"] is True
    assert state["phase"] == "RESTORED"
    assert_restored(api, journal)


# --- (f) a semantic experiment failure never triggers a rollback -----------


@pytest.mark.parametrize("policy", [ROLLBACK, PERSIST])
def test_semantic_experiment_failure_never_triggers_a_rollback(tmp_path, policy):
    changes = persist_plan() if policy == PERSIST else {}
    api, journal, services = authorized(tmp_path, proof_timeout_s=2, **changes)
    services.live_ok = True
    def guardian_and_external_executor():
        state = journal.read()
        request = state.get("restart_request")
        if request and not state.get("restart_count"):
            journal.update(restart_count=1)
            life.private_json(journal.directory / "restart-permit.json", {
                "attempt_id": state["attempt_id"], "candidate_id": CANDIDATE,
                "nonce": request["nonce"], "epoch": request["epoch"],
                "expires_monotonic": time.monotonic() + 1})
        for phase in ("initial", "final"):
            signal = journal.directory / ("experiment-signal-" + phase + ".json")
            if not signal.exists():
                life.private_json(signal, {"schema": life.EXPERIMENT_SIGNAL_SCHEMA, "phase": phase,
                                           "code": "receiver_answer_mismatch"})
            checkpoint = journal.directory / ("checkpoint-" + phase + ".json")
            receipt = journal.directory / ("native-proof-" + phase + ".json")
            if checkpoint.exists() and not receipt.exists():
                life.private_json(receipt, native_receipt(journal, life.read_json(checkpoint)))
    runner = ctl.Controller(api, journal, services=services, guard=guardian_and_external_executor)
    assert runner.run() == 0
    state = journal.read()
    assert state["phase"] == "GATES_COMPLETE"
    recorded = state["experiment_signals"]
    assert set(recorded) == {"initial", "final"}
    assert all(entry["classification"] == life.EXPERIMENT_SEMANTIC for entry in recorded.values())
    assert all(entry["rollback_eligible"] is False for entry in recorded.values())
    assert all(entry["code_known"] is True for entry in recorded.values())
    assert state.get("recovery") is None
    assert api.docs[CANDIDATE]["State"]["Running"] is True and api.docs[ORIGINAL]["State"]["Running"] is False


def test_unknown_experiment_code_stays_in_the_non_fatal_class():
    assert life.classify_experiment_signal(
        {"schema": life.EXPERIMENT_SIGNAL_SCHEMA, "code": "something_new"}) == life.EXPERIMENT_SEMANTIC
    assert life.classify_experiment_signal(
        {"schema": life.EXPERIMENT_SIGNAL_SCHEMA, "code": "receiver_timeout"}) == life.EXPERIMENT_SERVICE_HEALTH
    with pytest.raises(life.Failure, match="schema"):
        life.classify_experiment_signal({"code": "receiver_timeout"})


# --- (g) operator-driven manual rollback still works under the persist policy


def test_operator_manual_restore_after_persistence(tmp_path):
    api, journal, services = persist_authorized(tmp_path)
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 0
    assert journal.read()["phase"] == life.PERSISTED_PHASE
    assert api.docs[CANDIDATE]["State"]["Running"] is True
    result = ctl.restore(api, journal, services=services)
    assert result["verified"] is True and result["result"] == "RESTORED"
    assert result["reason"] == "operator_manual_restore"
    state = journal.read()
    assert state["phase"] == "RESTORED"
    assert state["persistence"]["manual_restore"] == "RESTORED"
    assert state["persistence"]["manual_restore_verified"] is True
    # The persistence decision itself is history and stays recorded.
    assert state["persistence_decision"] == life.PERSISTED_CANDIDATE
    assert state["persistence"]["original"]["container_id"] == ORIGINAL
    assert_restored(api, journal)
    assert ctl.main(["restore"]) == 2  # the documented CLI command requires --attempt-dir


def test_manual_restore_reports_failure_when_production_cannot_be_reverified(tmp_path):
    api, journal, services = persist_authorized(tmp_path, rollback_timeout_s=1.5)
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 0
    services.fail_restore = True
    result = ctl.restore(api, journal, services=services)
    assert result["verified"] is False and result["result"] == "RESTORE_FAILED"
    assert api.docs[ORIGINAL]["State"]["Running"] is True
    assert not api.docs[CANDIDATE]["State"]["Running"]
    assert journal.read()["phase"] == "RESTORE_FAILED"


# --- (h) the plan validator refuses a persist policy without the record -----


def test_plan_validator_rejects_persist_without_preserved_original():
    with pytest.raises(life.Failure, match="preserved original container record"):
        life.validate_plan(plan(persistence_policy=PERSIST))
    with pytest.raises(life.Failure, match="differs from the attempt original"):
        life.validate_plan(plan(persistence_policy=PERSIST,
                                preserved_original={"container_id": "3" * 64, "image_id": IMAGE,
                                                    "name": "fixture-service"}))
    with pytest.raises(life.Failure, match="invalid preserved original container record"):
        life.validate_plan(plan(preserved_original={"container_id": ORIGINAL}))
    with pytest.raises(life.Failure, match="invalid preserved original container name"):
        life.validate_plan(plan(persistence_policy=PERSIST,
                                preserved_original={"container_id": ORIGINAL, "image_id": IMAGE,
                                                    "name": "../escape"}))
    with pytest.raises(life.Failure, match="invalid candidate persistence policy"):
        life.validate_plan(plan(persistence_policy="keep_the_candidate"))
    assert life.validate_plan(plan())["persistence_policy"] == ROLLBACK
    assert life.validate_plan(plan(**persist_plan()))["persistence_policy"] == PERSIST


def test_prepare_refuses_a_preserved_record_that_differs_from_the_original(tmp_path):
    api = FakeDocker()
    with pytest.raises(life.Failure, match="differs from the inspected original"):
        prepared(tmp_path, api=api, persistence_policy=PERSIST,
                 preserved_original={"container_id": ORIGINAL, "image_id": IMAGE,
                                     "name": "not-this-service"})
    assert api.docs[ORIGINAL]["State"]["Running"]
    assert not any(op in ("start", "stop", "rename", "remove") for op, _ in api.events)


# --- timeout types are separate fields, never one shared budget -------------


def test_timeout_types_are_distinct_plan_fields():
    values = list(life.TIMEOUT_FIELDS.values())
    assert len(set(values)) == len(values)
    assert life.RESEARCH_STAGE_DEADLINES == {"source capture deadline": "source_capture_timeout_s",
                                             "receiver inference deadline": "receiver_inference_timeout_s"}
    configured = life.validate_plan(plan(request_timeout_s=7, source_capture_timeout_s=8,
                                         receiver_inference_timeout_s=9, proof_timeout_s=10,
                                         startup_timeout_s=11, maintenance_window_s=12))
    policy = life.timeout_policy(configured)
    assert policy["model_request_timeout_s"] == 7
    assert policy["native_source_capture_deadline_s"] == 8
    assert policy["receiver_inference_deadline_s"] == 9
    assert policy["experimental_proof_deadline_s"] == 10
    assert policy["deployment_health_timeout_s"] == 11
    assert policy["operator_maintenance_window_s"] == 12
    assert policy["candidate_persistence_policy"] == ROLLBACK
    for field in ("source_capture_timeout_s", "receiver_inference_timeout_s", "maintenance_window_s"):
        with pytest.raises(life.Failure, match="invalid deadline option"):
            life.validate_plan(plan(**{field: 0}))


def test_prepare_records_the_named_deadlines_in_the_journal(tmp_path):
    api, journal, _ = persist_authorized(tmp_path)
    state = journal.read()
    policy = state["timeout_policy"]
    assert policy["candidate_persistence_policy"] == PERSIST
    for name, field in life.TIMEOUT_FIELDS.items():
        assert policy[name] == state["plan"][field]
    assert state["preserved_original"]["container_id"] == ORIGINAL
    assert state["preserved_original"]["image_id"] == IMAGE
    assert len(state["preserved_original"]["configuration_sha256"]) == 64
    assert KEY not in (journal.directory / "journal.json").read_text()


def test_maintenance_window_only_bounds_the_persist_policy(tmp_path):
    api, journal, services = persist_authorized(tmp_path, maintenance_window_s=1.0, proof_timeout_s=5)
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 0
    state = journal.read()
    assert state["phase"] == life.PERSISTED_PHASE
    assert state["persistence"]["reason"] == "operator maintenance window closed"
    assert api.docs[CANDIDATE]["State"]["Running"] is True


# --- a persisted candidate can be replaced by a later controlled attempt ----


def test_persisted_candidate_can_be_replaced_by_a_later_controlled_attempt(tmp_path):
    api, journal, services = persist_authorized(tmp_path)
    assert ctl.Controller(api, journal, services=services, guard=lambda: None).run() == 0
    assert journal.read()["phase"] == life.PERSISTED_PHASE
    reference = tmp_path / "later-inspect.json"
    reference.write_text(json.dumps(api.docs[CANDIDATE]), encoding="utf-8")
    later = life.prepare(api, plan(attempt_id="later-upgrade-0001", original_id=CANDIDATE,
                                   original_image_id=CANDIDATE_IMAGE,
                                   candidate_image_id="sha256:" + "d" * 64,
                                   inspect_schema_evidence={
                                       "path": str(reference),
                                       "sha256": hashlib.sha256(reference.read_bytes()).hexdigest()}),
                         tmp_path / "later", probe=lambda *_: {"result": "PASS", "cpu_only": True})
    assert later.read()["phase"] == "PREPARED"
    # The new production container is not fenced, so the later attempt is admitted.
    assert not (journal.directory / "fences" / (CANDIDATE + ".json")).exists()
    sup.claim_original_fence(later, journal.directory / "fences", {"pid": 0, "start_ticks": "offline"})
    assert (journal.directory / "fences" / (CANDIDATE + ".json")).exists()
