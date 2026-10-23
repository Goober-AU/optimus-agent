"""Supervisor admission integration: real Supervisor/Services + repaired validator.

The TS9 gap: no test exercised Supervisor (or its Services wiring) against
the native validator admission and its per-phase attempt-dir pin files.
Real Supervisor, real Services selection and the real Journal run offline
against FakeDocker: synthetic phase-suite reports and pin files only, no
server, GPU, helper resolution or live model.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_v3_lifecycle import (FakeDocker, NGINX_CONFIG, ORIGINAL, PROXY, PROXY_IMAGE,
                               approval, swapped)
from deploy.v3 import lifecycle as life
from deploy.v3 import native_validator as nv
from deploy.v3 import supervisor as sup
from deploy.v3.live_validation import NativeHandoffValidator, VALIDATOR_KEY

FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "native_phase_suite_v1.json").read_text())
EXAMPLE = FIXTURE["example"]
PINNED = FIXTURE["admission_pinned_inputs"]
EPOCH_AFTER = EXAMPLE["attempt"]["candidate_started_at_after"]
GATES_BEFORE = {"service_initial": {"ok": True}, "native_initial": {"ok": True}}


def native_plan(**changes):
    value = {"attempt_id": "offline-attempt-0001", "original_id": ORIGINAL,
             "original_image_id": "sha256:" + "a" * 64,
             "candidate_image_id": "sha256:" + "b" * 64,
             "model": "fixture-model", "base_url": "http://127.0.0.1:18111",
             "ingress_base_url": "http://neo-k3-ingress:8080",
             "ingress": {"container_id": PROXY, "image_id": PROXY_IMAGE,
                         "container_name": "neo-k3-ingress",
                         "network_name": "fixture-ingress",
                         "network_id": "net-9",
                         "config_sha256": hashlib.sha256(NGINX_CONFIG).hexdigest()},
             "startup_timeout_s": 1.5, "deployment_timeout_s": 20,
             "rollback_timeout_s": 1.5, "proof_timeout_s": 0.05,
             "controller_timeout_s": 0.15, "supervisor_lease_s": 0.1,
             "poll_interval_s": 0.005, "request_timeout_s": 0.1,
             "stop_timeout_s": 1, "probe_timeout_s": 0.1,
             "validation_timeout_s": 15,
             "validator": VALIDATOR_KEY,
             "native_validator": copy.deepcopy(PINNED)}
    value.update(changes)
    return life.validate_plan(value)


def native_prepared(tmp_path, **changes):
    """PREPARED journal whose plan selects the real native validator."""
    api = FakeDocker()
    reference_path = tmp_path / "independent-inspect.json"
    reference_path.write_text(json.dumps(api.docs[ORIGINAL]), encoding="utf-8")
    changes.setdefault("inspect_schema_evidence", {
        "path": str(reference_path),
        "sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest()})
    journal = life.prepare(api, native_plan(**changes), tmp_path / "attempt",
                           probe=lambda *_: {"result": "PASS", "cpu_only": True,
                                             "scope": "offline-fake"})
    return api, journal


class OfflineServices(life.Services):
    """Real admission and validator wiring; offline service proofs only.

    FakeDocker serves no HTTP, so the recovery-side health/chat/ingress
    proofs stay fixture stubs exactly like FakeServices in the lifecycle
    suite; pre_mutation_admission and validator keep the real Services
    paths this file exists to cover.
    """

    def health(self):
        return {"ok": True, "scope": "offline-fake"}

    def chat(self):
        return {"ok": True, "scope": "offline-fake"}

    def ingress(self):
        return {"ok": True, "scope": "offline-fake"}


def offline_services(journal, api):
    return OfflineServices(journal.original(), journal.read()["plan"], api=api, journal=journal)


def phase_report(journal, phase, challenge):
    """Example report rewritten onto this attempt and this fresh challenge."""
    report = copy.deepcopy(EXAMPLE["report_before" if phase == "before_restart"
                                   else "report_after"])
    state = journal.read()
    report["attempt_id"] = state["attempt_id"]
    report["candidate_id"] = state["candidate_id"]
    report["candidate_started_at"] = state["candidate_epoch"]
    report["validator_challenge"] = challenge["challenge"]
    report["completed_at"] = int(time.time()) + 30
    return report


def pin_document(journal, phase, observed, **overrides):
    profile = PINNED["admission_template"]["source_profile"]
    document = {
        "schema": "occ3.admission.pin.v1",
        "phase": phase,
        "attempt_id": journal.read()["attempt_id"],
        "static_input_sha256": nv.PIN_STATIC_INPUT_SHA256,
        "runtime_epoch": observed["runtime_epoch"],
        "runtime_generation": observed["runtime_generation"],
        "profile_digest": nv._derived_profile_digest(profile, observed),
        "written_at": int(time.time()),
    }
    document.update(overrides)
    return document


def run_phase_with_pins(journal, services, phase):
    """Drive one validator phase through the real Services wiring.

    The validator thread writes the fresh challenge, then waits for the
    externally published report exactly like the controller does; this
    thread plays the executor: it sees the challenge, writes the phase's
    admission pin file and publishes the derivation-consistent report.
    """
    outcome = {}

    def drive():
        validator = services.validator(journal.read())
        outcome["result"] = getattr(validator, phase)()

    thread = threading.Thread(target=drive)
    thread.start()
    challenge_path = journal.directory / ("validator-challenge-" + phase + ".json")
    deadline = time.monotonic() + 20
    while not challenge_path.exists() and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert challenge_path.exists(), "validator challenge was not written"
    challenge = json.loads(challenge_path.read_text(encoding="utf-8"))
    report = phase_report(journal, phase, challenge)
    life.private_json(journal.directory / ("admission-pin-" + phase + ".json"),
                      pin_document(journal, phase, report["observed"]), exclusive=True)
    life.private_json(journal.directory / ("native-phase-suite-" + phase + ".json"), report)
    thread.join(20)
    assert not thread.is_alive()
    return outcome["result"]


def swapped_with_gates(api, journal):
    swapped(api, journal)
    journal.update(gates=dict(GATES_BEFORE))


# -- (i) placeholder admission is refused pre-mutation -----------------------
def test_supervisor_refuses_placeholder_admission_before_authorization(tmp_path, monkeypatch):
    # TS9 defect shape: the admission still carries fill-at-window per-phase
    # pin fields. The repaired admission is static-only (the per-phase pins
    # moved to write-once attempt-dir pin files), so the supervisor must
    # refuse this admission before consuming anything.
    placeholder = copy.deepcopy(PINNED)
    profile = placeholder["admission_template"]["source_profile"]
    profile["profile_digest_before_restart"] = "0" * 64
    profile["profile_digest_after_restart"] = "1" * 64
    api, journal = native_prepared(tmp_path, native_validator=placeholder)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    guardian = sup.Supervisor(api, journal, services=offline_services(journal, api),
                              lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    with pytest.raises(life.Failure, match="before authorization"):
        guardian.run(approval(journal))
    state = journal.read()
    assert state["phase"] == "PREPARED"
    assert not state["authorization_consumed"]
    assert not (tmp_path / "fences" / (state["original_id"] + ".json")).exists()
    assert not any(op in ("start", "stop", "rename", "remove") for op, _ in api.events)


# -- (ii) static-only admission is admitted by the real supervisor ------------
def test_supervisor_admits_static_only_admission_and_consumes_authorization(tmp_path, monkeypatch):
    api, journal = native_prepared(tmp_path)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})

    def broken_spawn():
        raise OSError("synthetic controller spawn failure")

    guardian = sup.Supervisor(api, journal, services=offline_services(journal, api),
                              process_factory=broken_spawn,
                              lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    assert guardian.run(approval(journal)) == 1
    state = journal.read()
    assert state["authorization_consumed"] is True
    assert (tmp_path / "fences" / (state["original_id"] + ".json")).exists()
    admission = state["validation_admission"]
    assert admission["ok"] is True
    assert admission["admission_scope"] == "offline_inputs_only"
    assert admission["runtime_binding"] == "deferred_to_post_start"
    assert admission["bindings"]["profile_digest_source"].startswith(
        "per-phase admission pin files")
    assert "profile_digest_before_restart" not in admission["bindings"]
    assert "profile_digest_after_restart" not in admission["bindings"]
    result = life.read_json(journal.directory / "supervisor-result.json")
    assert result["verified"] and result["result"] == "NOOP"
    assert not any(op in ("start", "stop", "rename") for op, _ in api.events)


# -- (iii) full phase flow with per-phase pin files ---------------------------
def test_supervisor_services_full_phase_flow_with_pin_files(tmp_path):
    api, journal = native_prepared(tmp_path)
    life.consume_authorization(journal, approval(journal))
    swapped_with_gates(api, journal)
    services = offline_services(journal, api)
    assert isinstance(services.validator(journal.read()), NativeHandoffValidator)

    before = run_phase_with_pins(journal, services, "before_restart")
    assert before["ok"] is True, before
    journal.gate("live_before", before)
    journal.update(candidate_epoch=EPOCH_AFTER, restart_done=True, restart_count=1,
                   gates=dict(GATES_BEFORE, live_before={"ok": True}, restart={"ok": True}))

    after = run_phase_with_pins(journal, services, "after_restart")
    assert after["ok"] is True, after
    journal.gate("live_after", after)
    assert before["bindings"]["runtime_epoch"] != after["bindings"]["runtime_epoch"]
    assert after["stale_binding"]["generation_advanced"] is True
    for phase in ("before_restart", "after_restart"):
        pin = json.loads((journal.directory / ("admission-pin-" + phase + ".json"))
                         .read_text(encoding="utf-8"))
        assert pin["schema"] == "occ3.admission.pin.v1"
        assert pin["attempt_id"] == journal.read()["attempt_id"]
        assert pin["static_input_sha256"] == nv.PIN_STATIC_INPUT_SHA256


# -- (iv) after pin equal to before pin is refused ---------------------------
def test_supervisor_after_pin_equal_to_before_pin_refused(tmp_path):
    api, journal = native_prepared(tmp_path)
    life.consume_authorization(journal, approval(journal))
    swapped_with_gates(api, journal)
    services = offline_services(journal, api)
    assert run_phase_with_pins(journal, services, "before_restart")["ok"] is True
    journal.update(candidate_epoch=EPOCH_AFTER, restart_done=True, restart_count=1,
                   gates=dict(GATES_BEFORE, live_before={"ok": True}, restart={"ok": True}))

    outcome = {}

    def drive():
        outcome["result"] = services.validator(journal.read()).after_restart()

    thread = threading.Thread(target=drive)
    thread.start()
    challenge_path = journal.directory / "validator-challenge-after_restart.json"
    deadline = time.monotonic() + 20
    while not challenge_path.exists() and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert challenge_path.exists(), "validator challenge was not written"
    challenge = json.loads(challenge_path.read_text(encoding="utf-8"))
    report = phase_report(journal, "after_restart", challenge)
    # Fabricated pre-restart: the after incarnation is rewritten to the
    # before one (the only way an equal pin stays derivation-consistent) and
    # the after pin file is an exact copy of the before pin.
    report["observed"] = copy.deepcopy(EXAMPLE["report_before"]["observed"])
    report["cases"]["stale_handle_post"]["fresh_enrollment_runtime_epoch"] = \
        report["observed"]["runtime_epoch"]
    report["binding"]["profile_digest"] = EXAMPLE["report_before"]["binding"]["profile_digest"]
    before_pin = json.loads(
        (journal.directory / "admission-pin-before_restart.json").read_text(encoding="utf-8"))
    life.private_json(journal.directory / "admission-pin-after_restart.json",
                      dict(before_pin, phase="after_restart"), exclusive=True)
    life.private_json(journal.directory / "native-phase-suite-after_restart.json", report)
    thread.join(20)
    assert not thread.is_alive()
    result = outcome["result"]
    assert result["ok"] is False, result
    assert result["code"] == "profile_digest_pins_not_per_boot"
