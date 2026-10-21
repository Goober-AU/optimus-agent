"""Offline adapter proofs. No server, GPU, helper resolution or live model.

Every fixture value is synthetic. These tests exercise the real adapter
verification logic, the real Services selection, the real Journal and the real
exit_aware wrapper used by the controller. No native binary is executed: the
phase-suite reports are explicit synthetic observations only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.v3 import lifecycle as life
from deploy.v3 import native_validator as nv
from deploy.v3.live_validation import (LiveValidator, NativeHandoffValidator,
                                       SourceKvBaselineValidator, UnselectedValidator,
                                       VALIDATOR_KEY)

ORIGINAL = "1" * 64
CANDIDATE = "c" * 64
IMAGE = "sha256:" + "a" * 64
CANDIDATE_IMAGE = "sha256:" + "b" * 64
PROXY = "9" * 64
PROXY_IMAGE = "sha256:" + "9" * 64
KEY = "synthetic-private-key-not-real"
FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "native_phase_suite_v1.json").read_text())
EXAMPLE = FIXTURE["example"]
PINNED = FIXTURE["admission_pinned_inputs"]
ATTEMPT = EXAMPLE["attempt"]["attempt_id"]
EPOCH_BEFORE = EXAMPLE["attempt"]["candidate_started_at_before"]
EPOCH_AFTER = EXAMPLE["attempt"]["candidate_started_at_after"]
CHALLENGE_BEFORE = EXAMPLE["challenge_before"]["challenge"]
CHALLENGE_AFTER = EXAMPLE["challenge_after"]["challenge"]


def original_doc():
    return {"Id": ORIGINAL, "Image": IMAGE, "Name": "/fixture-service",
            "Config": {"Image": IMAGE, "Hostname": "fixture-host",
                       "Entrypoint": ["python3"],
                       "Cmd": ["-m", "sglang.launch_server", "--api-key", KEY],
                       "Env": ["A=1"], "WorkingDir": "/w", "User": "", "Labels": {}},
            "HostConfig": {"NetworkMode": "bridge", "Runtime": "runc", "Mounts": [],
                           "Binds": None, "RestartPolicy": {"Name": "always"},
                           "AutoRemove": False, "PortBindings": {}},
            "Mounts": [], "NetworkSettings": {"Networks": {"bridge": {}}},
            "State": {"Running": True, "Restarting": False, "ExitCode": 0,
                      "Status": "running", "StartedAt": "epoch-original"}}


def plan(**changes):
    value = {"attempt_id": ATTEMPT, "original_id": ORIGINAL, "original_image_id": IMAGE,
             "candidate_image_id": CANDIDATE_IMAGE, "model": "SYNTHETIC-model",
             "base_url": "http://127.0.0.1:18111", "ingress_base_url": "http://127.0.0.1:18112",
             "startup_timeout_s": 5, "deployment_timeout_s": 20, "rollback_timeout_s": 5,
             "proof_timeout_s": 5, "controller_timeout_s": 5, "supervisor_lease_s": 5,
             "poll_interval_s": 0.005, "request_timeout_s": 5, "stop_timeout_s": 1,
             "probe_timeout_s": 1, "validation_timeout_s": 5}
    value.update(changes)
    return life.validate_plan(value)


def native_plan(**changes):
    return plan(validator=VALIDATOR_KEY, native_validator=copy.deepcopy(PINNED), **changes)


def make_journal(tmp_path, plan_value):
    return life.Journal.create(tmp_path / "attempt", plan_value, original_doc())


def make_validator(journal, *, timeout=5.0, phase="before_restart"):
    challenge = CHALLENGE_BEFORE if phase == "before_restart" else CHALLENGE_AFTER
    state = journal.read()
    return NativeHandoffValidator(state["plan"], journal, state, timeout=timeout,
                                  challenge_factory=lambda: challenge)


def write_report(journal, phase, report, *, completed_at=None):
    document = copy.deepcopy(report)
    document["completed_at"] = completed_at if completed_at is not None else int(time.time()) + 30
    life.private_json(journal.directory / ("native-phase-suite-" + phase + ".json"), document)


def before_state(journal):
    journal.update(candidate_id=CANDIDATE, candidate_started=True, candidate_epoch=EPOCH_BEFORE,
                   gates={"service_initial": {"ok": True}, "native_initial": {"ok": True}})


def after_state(journal):
    journal.update(candidate_epoch=EPOCH_AFTER, restart_done=True, restart_count=1,
                   gates={"service_initial": {"ok": True}, "native_initial": {"ok": True},
                          "live_before": {"ok": True}, "restart": {"ok": True}})


# -- selection ----------------------------------------------------------------
def test_absent_key_keeps_default_blocked_live_validator(tmp_path):
    journal = make_journal(tmp_path, plan())
    services = life.Services(journal.original(), journal.read()["plan"], journal=journal)
    selected = services.validator(journal.read())
    assert isinstance(selected, LiveValidator) and not isinstance(selected, NativeHandoffValidator)
    assert selected.pre_mutation_admission()["ok"] is False


def test_native_key_selects_adapter(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    services = life.Services(journal.original(), journal.read()["plan"], journal=journal)
    selected = services.validator(journal.read())
    assert isinstance(selected, NativeHandoffValidator)


def test_unknown_key_fails_closed_without_fallback(tmp_path):
    journal = make_journal(tmp_path, plan(validator="someone-elses-validator"))
    services = life.Services(journal.original(), journal.read()["plan"], journal=journal)
    selected = services.validator(journal.read())
    assert isinstance(selected, UnselectedValidator)
    for method in ("pre_mutation_admission", "before_restart", "after_restart"):
        result = getattr(selected, method)()
        assert result["ok"] is False and result["code"] == "unknown_validator_selection"


def test_native_key_without_journal_fails_closed(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    services = life.Services(journal.original(), journal.read()["plan"])
    selected = services.validator(journal.read())
    assert isinstance(selected, UnselectedValidator)
    assert selected.pre_mutation_admission()["code"] == "validator_journal_unavailable"


def test_services_admission_order_blocks_without_pinned_inputs(tmp_path):
    journal = make_journal(tmp_path, plan(validator=VALIDATOR_KEY))
    services = life.Services(journal.original(), journal.read()["plan"], journal=journal)
    admission = services.pre_mutation_admission(journal.read())
    assert admission["ok"] is False
    assert admission["code"] == "native_latent_validation_unavailable"


def test_services_admission_passes_with_pinned_inputs(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    services = life.Services(journal.original(), journal.read()["plan"], journal=journal)
    admission = services.pre_mutation_admission(journal.read())
    assert admission["ok"] is True
    assert admission["admission_scope"] == "offline_inputs_only"
    assert admission["runtime_binding"] == "deferred_to_post_start"


def test_baseline_validator_unchanged():
    assert issubclass(SourceKvBaselineValidator, LiveValidator)
    assert SourceKvBaselineValidator("http://fixture", KEY, "m")._result(
        "before_restart", {})["restart_authorized"] is False


# -- admission inputs --------------------------------------------------------
def test_admission_accepts_synthetic_pinned_inputs(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    result = make_validator(journal).pre_mutation_admission()
    assert result["ok"] is True
    assert result["gpu_proof"] == "not_asserted_preflight"
    assert result["live_success_claimed"] is False
    assert result["http_calls_performed"] == 0 and result["credentials_resolved"] == 0
    assert result["binary_mirror_verified"] == "absent_pinned_digest_only"


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p.pop("native_validator"), "native_validator_inputs_missing"),
    (lambda p: p.update(native_validator={"method_id": nv.METHOD_ID}),
     "admission_fieldset_invalid"),
    (lambda p: p["native_validator"].update(method_id="other-method"), "method_mismatch"),
    (lambda p: p["native_validator"]["harness_artifact"].update(
        binary_sha256="not-a-digest"), "harness_binary_sha256_invalid"),
    (lambda p: p["native_validator"]["harness_artifact"].update(
        platform="linux-x86_64"), "harness_platform_unsupported"),
    (lambda p: p["native_validator"]["source_manifest"]["files"].update(
        {"../escape.rs": "a" * 64}), "source_manifest_path_invalid"),
    (lambda p: p["native_validator"]["interface_versions"].update(
        report="optimus.occ3.native-attempt.v1"), "report_schema_version_unknown"),
    (lambda p: p["native_validator"]["offline_selftest"].update(exit_code=1),
     "offline_selftest_not_clean"),
    (lambda p: p["native_validator"]["offline_selftest"].update(
        binary_sha256="f" * 64), "offline_selftest_binary_mismatch"),
    (lambda p: p["native_validator"]["admission_template"]["identity"].update(
        model_id="tampered"), "identity_fingerprint_mismatch"),
    (lambda p: p["native_validator"]["admission_template"]["source_profile"].update(
        dtype="int8"), "source_dtype_unsupported"),
    (lambda p: p["native_validator"]["admission_template"]["source_profile"].update(
        expected_ranks=4), "expected_ranks_not_tp8"),
    (lambda p: p["native_validator"]["budget_resource_receipt"].pop("root_reviewed"),
     "budget_receipt_invalid"),
    (lambda p: p["native_validator"]["max_budget"].update(default_max_tokens=4096),
     "default_max_tokens_changed"),
    (lambda p: p["native_validator"]["max_budget"].update(context_window=8192),
     "context_window_changed"),
    (lambda p: p["native_validator"]["max_budget"].update(thinking_level="medium"),
     "thinking_level_not_max"),
    (lambda p: p["native_validator"]["layout_strategy"].update(
        bound_at="at_admission"), "layout_bound_not_post_start"),
    (lambda p: p["native_validator"]["endpoint"].update(
        origin="http://synthetic-native.invalid"), "endpoint_origin_not_https"),
    (lambda p: p["native_validator"]["endpoint"].update(control_root="/occ3/v1/"),
     "control_root_invalid"),
    (lambda p: p["native_validator"]["credential_references"].update(
        api_key="SYNTHETIC KEY VALUE"), "api_key_reference_invalid"),
    (lambda p: p["native_validator"]["credential_references"]["headers"].update(
        {"Authorization": "!helper"}), "header_name_not_allowed"),
    (lambda p: p["native_validator"]["control_spec"].update(max_attempts=2),
     "multiple_attempts_allowed"),
    (lambda p: p["native_validator"]["control_spec"].update(no_replay=False),
     "replay_allowed"),
])
def test_admission_refuses_each_invalid_input(tmp_path, mutate, code):
    journal = make_journal(tmp_path, native_plan())
    state = journal.read()
    plan_value = copy.deepcopy(state["plan"])
    mutate(plan_value)
    validator = NativeHandoffValidator(plan_value, journal, state)
    result = validator.pre_mutation_admission()
    assert result["ok"] is False and result["code"] == code


def test_admission_never_resolves_or_leaks_references(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    result = make_validator(journal).pre_mutation_admission()
    dumped = json.dumps(result)
    assert "!synthetic-helper-reference" not in dumped
    assert "!synthetic-pomerium-helper" not in dumped


def test_adapter_source_has_no_network_or_env_surface():
    source = (Path(nv.__file__)).read_text()
    for forbidden in ("urlopen", "requests", "os.environ", "subprocess", "socket"):
        assert forbidden not in source


def test_binary_mirror_digest_is_verified_when_present(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    mirror = journal.directory / "harness-binary-mirror"
    mirror.write_bytes(b"synthetic binary bytes")
    result = make_validator(journal).pre_mutation_admission()
    assert result["ok"] is False and result["code"] == "binary_mirror_digest_mismatch"
    import hashlib as _h
    mirror.write_bytes(b"x" * 64)
    state = journal.read()
    pinned = copy.deepcopy(state["plan"]["native_validator"])
    pinned["harness_artifact"]["binary_sha256"] = _h.sha256(b"x" * 64).hexdigest()
    pinned["offline_selftest"]["binary_sha256"] = pinned["harness_artifact"]["binary_sha256"]
    validator = NativeHandoffValidator(dict(state["plan"], native_validator=pinned),
                                        journal, state)
    result = validator.pre_mutation_admission()
    assert result["ok"] is True
    assert result["binary_mirror_verified"] == "verified_mirrored_bytes_no_execution"


# -- before phase ------------------------------------------------------------
def test_before_restart_passes_with_retention_record(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    result = make_validator(journal).before_restart()
    assert result["ok"] is True, result
    assert result["cleanup_scope"] == "positive_and_no_state_only"
    retained = result["negative_control_retained"]
    assert retained["retention_reason"] == "planned_restart"
    assert retained["artifact_sha256"] == EXAMPLE["report_before"]["negative_control_retained"]["artifact_sha256"]
    assert result["gpu_proof"] is False and result["physical_gpu_free_proven"] is False
    assert result["ordinary_serving_nonregression"] == "separate_controller_service_final_gate"
    assert (journal.directory / "validator-challenge-before_restart.json").exists()
    assert json.dumps(result).count(CHALLENGE_BEFORE) == 0


@pytest.mark.parametrize("missing", ["service_initial", "native_initial"])
def test_before_restart_requires_prior_gates(tmp_path, missing):
    journal = make_journal(tmp_path, native_plan())
    gates = {"service_initial": {"ok": True}, "native_initial": {"ok": True}}
    gates.pop(missing)
    journal.update(candidate_id=CANDIDATE, candidate_started=True, candidate_epoch=EPOCH_BEFORE,
                   gates=gates)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    result = make_validator(journal).before_restart()
    assert result["ok"] is False
    assert result["code"] == "phase_gate_missing:" + missing


def test_before_restart_requires_started_candidate(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    journal.update(gates={"service_initial": {"ok": True}, "native_initial": {"ok": True}})
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "candidate_not_started"


def test_before_restart_timeout_is_bounded_and_blocked(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    result = make_validator(journal, timeout=0.05).before_restart()
    assert result["ok"] is False
    assert result["code"] == "native_phase_suite_report_timeout"
    assert (journal.directory / "validator-challenge-before_restart.json").exists()


def test_before_restart_challenge_is_written_once(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    validator = make_validator(journal)
    assert validator.before_restart()["ok"] is True
    again = make_validator(journal).before_restart()
    assert again["ok"] is False
    assert again["code"] == "challenge_already_exists_no_replay"


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(validator_challenge="wrong"), "challenge_binding_mismatch"),
    (lambda r: r.update(attempt_id="other-attempt-0002"), "attempt_binding_mismatch"),
    (lambda r: r.update(candidate_id="d" * 64), "candidate_binding_mismatch"),
    (lambda r: r.update(candidate_started_at="other-epoch"), "started_at_binding_mismatch"),
    (lambda r: r.update(schema="optimus.occ3.native-attempt.v1"), "report_schema_unknown"),
    (lambda r: r.update(phase="after_restart"), "report_phase_mismatch"),
    (lambda r: r.update(extra_field=True), "report_fieldset_invalid"),
    (lambda r: r["harness"].update(binary_sha256="f" * 64),
     "report_binary_binding_mismatch"),
    (lambda r: r["harness"].update(source_manifest_sha256="0" * 64),
     "report_source_manifest_mismatch"),
    (lambda r: r["binding"].update(identity_fingerprint="sha256:" + "0" * 64),
     "report_identity_binding_mismatch"),
    (lambda r: r["binding"].update(endpoint_origin="https://other.invalid"),
     "report_origin_binding_mismatch"),
    (lambda r: r["observed"].update(expected_ranks=4), "observed_ranks_mismatch"),
    (lambda r: r["observed"].update(ready_ranks=7), "all_ranks_not_ready"),
    (lambda r: r["cases"]["positive"].update(answer_sha256="0" * 64),
     "positive_answer_not_nonce_hash"),
    (lambda r: r["cases"]["positive"].update(lease_state="queued"), "lease_not_consumed"),
    (lambda r: r["cases"]["positive"].update(consumed_before_tools=False),
     "consumed_not_before_tools"),
    (lambda r: r["cases"]["positive"]["receiver_request_audit"].update(max_tokens=4096),
     "positive_audit_invalid:budget_not_default_max"),
    (lambda r: r["cases"]["positive"]["receiver_request_audit"].update(request_count=2),
     "positive_audit_invalid:request_count_invalid"),
    (lambda r: r["cases"]["positive"]["receiver_request_audit"].update(
        source_facts_absent=False), "positive_audit_invalid:source_facts_present"),
    (lambda r: r["cases"]["no_state"].update(answer_sha256="0" * 64),
     "no_state_not_exact_unknown"),
    (lambda r: r["cases"]["no_state"].update(messages=1), "no_state_side_effects_present"),
    (lambda r: r["cases"]["no_state"].update(semantic_nonce_match=True),
     "no_state_nonce_leaked"),
    (lambda r: r["negative_control_retained"].update(retention_reason="convenience"),
     "retention_reason_invalid"),
    (lambda r: r["negative_control_retained"].update(
        runtime_epoch="other-epoch"), "retained_epoch_mismatch"),
    (lambda r: r.update(gpu_free_proven=True), "gpu_free_overclaimed"),
    (lambda r: r.update(logical_cleanup_confirmed=False), "logical_cleanup_unconfirmed"),
    (lambda r: r["cases"]["positive"].update(case_passed=False), "positive_case_failed"),
    (lambda r: r["cases"].pop("stale_handle_pre"), "cases_fieldset_invalid"),
])
def test_before_restart_report_negatives(tmp_path, mutate, code):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    report = copy.deepcopy(EXAMPLE["report_before"])
    mutate(report)
    write_report(journal, "before_restart", report)
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == code, result


def test_before_restart_rejects_completion_before_challenge(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"], completed_at=1)
    result = make_validator(journal).before_restart()
    assert result["code"] == "completion_outside_window"


def test_before_restart_rejects_duplicate_json_keys(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    raw = json.dumps(EXAMPLE["report_before"])[:-1] + ',"phase":"before_restart"}'
    path = journal.directory / "native-phase-suite-before_restart.json"
    path.write_text(raw)
    result = make_validator(journal).before_restart()
    assert result["code"] == "duplicate_json_key"


def test_before_restart_rejects_non_finite_numbers(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    raw = json.dumps(EXAMPLE["report_before"]).replace("1760000000", "NaN")
    (journal.directory / "native-phase-suite-before_restart.json").write_text(raw)
    result = make_validator(journal).before_restart()
    assert result["code"] == "non_finite_number"


def test_before_restart_rejects_symlinked_report(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    real = journal.directory / "real-report.json"
    real.write_text(json.dumps(EXAMPLE["report_before"]))
    link = journal.directory / "native-phase-suite-before_restart.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    result = make_validator(journal).before_restart()
    assert result["code"] == "symlink_refused"


# -- after phase -------------------------------------------------------------
def test_after_restart_passes_with_chained_stale_refusal(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    assert make_validator(journal).before_restart()["ok"] is True
    after_state(journal)
    write_report(journal, "after_restart", EXAMPLE["report_after"])
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is True, result
    refusal = result["stale_handle_refusal"]
    assert refusal["http_status"] == 409 and refusal["error_code"] == "state"
    assert refusal["attempts"] == 1
    assert refusal["artifact_sha256"] == EXAMPLE["report_before"]["negative_control_retained"]["artifact_sha256"]
    assert result["stale_binding"]["before_runtime_epoch"] != result["stale_binding"]["after_runtime_epoch"]


def test_after_restart_requires_before_phase_files(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    after_state(journal)
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is False
    assert result["code"] == "before_phase_binding_unavailable"


def test_after_restart_requires_restart_gates(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    assert make_validator(journal).before_restart()["ok"] is True
    journal.update(candidate_epoch=EPOCH_AFTER, gates={"live_before": {"ok": True}})
    write_report(journal, "after_restart", EXAMPLE["report_after"])
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is False
    assert result["code"] == "phase_gate_missing:restart"


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r["cases"]["stale_handle_post"].update(http_status=401),
     "stale_refusal_not_409"),
    (lambda r: r["cases"]["stale_handle_post"].update(http_status=200),
     "stale_refusal_not_409"),
    (lambda r: r["cases"]["stale_handle_post"].update(error_code="expired"),
     "stale_refusal_code_not_state"),
    (lambda r: r["cases"]["stale_handle_post"].update(error_code="replay"),
     "stale_refusal_code_not_state"),
    (lambda r: r["cases"]["stale_handle_post"].update(attempts=2),
     "stale_probe_attempted_twice"),
    (lambda r: r["cases"]["stale_handle_post"].update(artifact_sha256="0" * 64),
     "stale_artifact_binding_mismatch"),
    (lambda r: r["observed"].update(runtime_epoch="synthetic-runtime-epoch-before"),
     "runtime_epoch_unchanged"),
    (lambda r: r["observed"].update(runtime_generation=41), "runtime_generation_not_advanced"),
    (lambda r: r["cases"]["stale_handle_post"].update(
        fresh_enrollment_runtime_epoch="synthetic-runtime-epoch-before"),
     "fresh_enrollment_epoch_mismatch"),
])
def test_after_restart_negatives(tmp_path, mutate, code):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    assert make_validator(journal).before_restart()["ok"] is True
    after_state(journal)
    report = copy.deepcopy(EXAMPLE["report_after"])
    mutate(report)
    if code == "runtime_epoch_unchanged":
        report["cases"]["stale_handle_post"]["fresh_enrollment_runtime_epoch"] = \
            report["observed"]["runtime_epoch"]
    write_report(journal, "after_restart", report)
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is False and result["code"] == code, result


# -- controller wiring -------------------------------------------------------
class InertAPI:
    def __init__(self):
        self.doc = {"Id": CANDIDATE, "Image": CANDIDATE_IMAGE,
                    "State": {"Running": True, "Restarting": False, "Dead": False}}

    def inspect(self, identity):
        assert identity == self.doc["Id"]
        return self.doc


def test_exit_aware_wrapper_returns_blocked_result_and_gate_fails_closed(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    validator = make_validator(journal, timeout=0.05)
    result = life.exit_aware(InertAPI(), CANDIDATE, CANDIDATE_IMAGE,
                             validator.before_restart, time.monotonic() + 2,
                             guard=lambda: None, interval=0.05)
    assert result["ok"] is False
    with pytest.raises(life.Failure):
        journal.gate("live_before", result)


def test_exit_aware_wrapper_passes_verified_result_to_gate(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    validator = make_validator(journal)
    result = life.exit_aware(InertAPI(), CANDIDATE, CANDIDATE_IMAGE,
                             validator.before_restart, time.monotonic() + 5,
                             guard=lambda: None, interval=0.05)
    assert result["ok"] is True
    journal.gate("live_before", result)
    assert journal.read()["gates"]["live_before"]["cleanup_scope"] == "positive_and_no_state_only"


def test_results_stay_in_private_attempt_scope(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    result = make_validator(journal).before_restart()
    dumped = json.dumps(result)
    assert KEY not in dumped
    assert "!synthetic-helper-reference" not in dumped
    for name in ("validator-challenge-before_restart.json",
                 "native-phase-suite-before_restart.json"):
        assert (journal.directory / name).exists()
    assert not (journal.directory / "native-phase-suite-after_restart.json").exists()


# -- V1: report publication atomicity contract (contract_revision 2) ---------
class StagingWriter:
    """Test harness ONLY: simulates the required harness-side publication.

    Stages into a uniquely named private file, then publishes at the final
    name without replacing an existing target. This simulation is not proof of
    real Windows no-replace atomic publication or ACL behavior.
    """

    def __init__(self, journal):
        self.directory = journal.directory

    def publish(self, phase, document, *, complete=True):
        import uuid
        staging = self.directory / ("stage-" + uuid.uuid4().hex + ".json")
        payload = json.dumps(document)
        if not complete:
            payload = payload[: max(1, len(payload) // 2)]
        with staging.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        final = self.directory / ("native-phase-suite-" + phase + ".json")
        if final.exists():
            staging.unlink()
            return False
        os.rename(staging, final)
        return True


def test_incomplete_private_staging_is_invisible_during_wait(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    writer = StagingWriter(journal)
    import threading

    def finish():
        time.sleep(0.15)
        writer.publish("before_restart", dict(EXAMPLE["report_before"],
                                               completed_at=int(time.time()) + 30))

    threading.Thread(target=finish).start()
    result = make_validator(journal).before_restart()
    assert result["ok"] is True, result
    assert not any(p.name.startswith("stage-") for p in journal.directory.iterdir())


def test_complete_final_publication_within_deadline_is_accepted(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    writer = StagingWriter(journal)
    assert writer.publish("before_restart", dict(EXAMPLE["report_before"],
                                                 completed_at=int(time.time()) + 30)) is True
    result = make_validator(journal).before_restart()
    assert result["ok"] is True, result


def test_malformed_final_is_immediate_refusal_not_retried(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    final = journal.directory / "native-phase-suite-before_restart.json"
    final.write_text('{"schema": "optimus.occ3.native-phase-suite.v1", "phase":')
    started = time.monotonic()
    result = make_validator(journal, timeout=2.0).before_restart()
    elapsed = time.monotonic() - started
    assert result["ok"] is False and result["code"] == "malformed_json"
    assert elapsed < 1.0
    # A late valid publication cannot rescue the burned one-shot challenge.
    final.write_text(json.dumps(dict(EXAMPLE["report_before"],
                                     completed_at=int(time.time()) + 30)))
    again = make_validator(journal).before_restart()
    assert again["code"] == "challenge_already_exists_no_replay"


def test_truncated_mid_write_staging_does_not_burn_challenge(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    writer = StagingWriter(journal)
    import threading

    def finish():
        time.sleep(0.15)
        writer.publish("before_restart", dict(EXAMPLE["report_before"],
                                               completed_at=int(time.time()) + 30))

    thread = threading.Thread(target=finish)
    thread.start()
    # Simulate an abandoned partial staging visible during the wait.
    staging = journal.directory / "stage-partial.json"
    staging.write_text(json.dumps(EXAMPLE["report_before"])[:200])
    result = make_validator(journal).before_restart()
    thread.join()
    assert result["ok"] is True, result


def test_existing_final_target_is_never_replaced(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    writer = StagingWriter(journal)
    first = dict(EXAMPLE["report_before"], completed_at=int(time.time()) + 30)
    assert writer.publish("before_restart", first) is True
    tampered = dict(first, validator_challenge="different")
    assert writer.publish("before_restart", tampered) is False
    result = make_validator(journal).before_restart()
    assert result["ok"] is True
    on_disk = json.loads((journal.directory / "native-phase-suite-before_restart.json").read_text())
    assert on_disk["validator_challenge"] == first["validator_challenge"]


# -- V2: bounded JSON depth guard -------------------------------------------
def _deep_payload(depth, report):
    base = json.dumps(report)
    i = base.find('"observed"')
    j = base.find('"suite_nonce_sha256"')
    inner = '"x"'
    for _ in range(depth):
        inner = '{"observed":' + inner + "}"
    return base[:i] + '"observed": ' + inner + ", " + base[j:]


def test_overdepth_report_under_2mib_is_refused_not_crash(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    payload = _deep_payload(3000, EXAMPLE["report_before"])
    assert len(payload) <= 2 * 1024 * 1024
    (journal.directory / "native-phase-suite-before_restart.json").write_text(payload)
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "malformed_json"


def test_exact_depth_boundary_is_accepted(tmp_path):
    document = json.loads(json.dumps(EXAMPLE["report_before"]))
    assert nv._json_depth_ok(json.dumps(document).encode()) is True


@pytest.mark.parametrize("payload,ok", [
    (b'{"a": {"b": {"c": 1}}}', True),
    (b'{"a": "not } closed", "b": [1, 2]}', True),
    (rb'{"a": "quote \\" and backslash \\\\" and } brace"}', True),
    (b'{"a": "bracket } and brace { inside string"}', True),
    (b'{"a": {"b":', False),
    (b'["a", "b"', False),
    (b'{"a": "unterminated}', False),
])
def test_json_depth_scan_escapes_and_closings(payload, ok):
    assert nv._json_depth_ok(payload) is ok


def test_depth_guard_covers_admission_documents_too(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    deep = {"schema": "x", "n": "x"}
    node = deep
    for _ in range(100):
        node["n"] = {"n": "x"}
        node = node["n"]
    result = NativeHandoffValidator({"validator": VALIDATOR_KEY, "native_validator": deep},
                                    journal, journal.read()).pre_mutation_admission()
    assert result["code"] == "malformed_json"


def test_public_phase_methods_translate_recursion_to_refusal(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    payload = _deep_payload(50000, EXAMPLE["report_before"])
    assert len(payload) <= 2 * 1024 * 1024
    (journal.directory / "native-phase-suite-before_restart.json").write_text(payload)
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "malformed_json"


# -- P3: generation must advance under the controlled profile ----------------
def _after_case(journal, *, generation, epoch="synthetic-runtime-epoch-after"):
    write_report(journal, "after_restart", dict(
        EXAMPLE["report_after"],
        completed_at=int(time.time()) + 30))
    report = json.loads((journal.directory / "native-phase-suite-after_restart.json").read_text())
    report["observed"]["runtime_generation"] = generation
    report["observed"]["runtime_epoch"] = epoch
    report["cases"]["stale_handle_post"]["fresh_enrollment_runtime_epoch"] = epoch
    life.private_json(journal.directory / "native-phase-suite-after_restart.json", report)


def test_fresh_epoch_and_advanced_generation_accepted(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    assert make_validator(journal).before_restart()["ok"] is True
    after_state(journal)
    _after_case(journal, generation=42)
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is True, result
    assert result["stale_binding"]["generation_advanced"] is True


@pytest.mark.parametrize("generation", [41, 40])
def test_unchanged_or_decreased_generation_refused_despite_fresh_epoch(tmp_path, generation):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    assert make_validator(journal).before_restart()["ok"] is True
    after_state(journal)
    _after_case(journal, generation=generation)
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is False
    assert result["code"] == "runtime_generation_not_advanced"


def test_unchanged_epoch_refused_even_with_advanced_generation(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    assert make_validator(journal).before_restart()["ok"] is True
    after_state(journal)
    _after_case(journal, generation=42, epoch="synthetic-runtime-epoch-before")
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is False
    assert result["code"] == "runtime_epoch_unchanged"


def test_volatile_reset_runtime_is_not_admitted_no_auto_retry(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    write_report(journal, "before_restart", EXAMPLE["report_before"])
    assert make_validator(journal).before_restart()["ok"] is True
    after_state(journal)
    _after_case(journal, generation=1, epoch="synthetic-runtime-epoch-after")
    result = make_validator(journal, phase="after_restart").after_restart()
    assert result["ok"] is False
    assert result["code"] == "runtime_generation_not_advanced"
    # No automatic reset/repair/retry: the failed gate record is not rewritten.
    assert journal.read().get("gates", {}).get("live_after") is None


# -- V2 follow-on: strict UTF-8-only encoding bound ---------------------------
def _nested(depth):
    inner = 0
    for _ in range(depth):
        inner = [inner]
    return inner


@pytest.mark.parametrize("enc", ["utf-16", "utf-32"])
def test_non_utf8_small_payload_refused(tmp_path, enc):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    payload = json.dumps(dict(EXAMPLE["report_before"],
                              completed_at=int(time.time()) + 30)).encode(enc)
    (journal.directory / "native-phase-suite-before_restart.json").write_bytes(payload)
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "malformed_json"


@pytest.mark.parametrize("enc", ["utf-16", "utf-32"])
def test_non_utf8_depth41_bypass_refused(tmp_path, enc):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    document = dict(EXAMPLE["report_before"], completed_at=int(time.time()) + 30)
    document["observed"] = {"nested": _nested(41)}
    payload = json.dumps(document, ensure_ascii=False).encode(enc)
    assert len(payload) <= 2 * 1024 * 1024
    (journal.directory / "native-phase-suite-before_restart.json").write_bytes(payload)
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "malformed_json"


def test_valid_utf8_unicode_strings_accepted(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    document = dict(EXAMPLE["report_before"], completed_at=int(time.time()) + 30)
    unicode_epoch = "synthetic-runtime-\u00e9poch-\u2222\u0122\U0001F600"
    document["observed"] = dict(document["observed"], runtime_epoch=unicode_epoch)
    document["negative_control_retained"] = dict(document["negative_control_retained"],
                                                 runtime_epoch=unicode_epoch)
    (journal.directory / "native-phase-suite-before_restart.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8")
    result = make_validator(journal).before_restart()
    assert result["ok"] is True, result


def test_invalid_utf8_bytes_refused(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    (journal.directory / "native-phase-suite-before_restart.json").write_bytes(
        b'{"schema": "\xff\xfe invalid"}')
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "malformed_json"


def test_utf8_bom_refused_fail_closed(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    payload = b"\xef\xbb\xbf" + json.dumps(
        dict(EXAMPLE["report_before"], completed_at=int(time.time()) + 30)).encode("utf-8")
    (journal.directory / "native-phase-suite-before_restart.json").write_bytes(payload)
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "malformed_json"


def test_truncated_multibyte_utf8_refused(tmp_path):
    journal = make_journal(tmp_path, native_plan())
    before_state(journal)
    payload = b'{"a": "\xe2\x82"}'
    (journal.directory / "native-phase-suite-before_restart.json").write_bytes(payload)
    result = make_validator(journal).before_restart()
    assert result["ok"] is False and result["code"] == "malformed_json"


def test_depth_guard_bytes_input_requires_strict_utf8():
    assert nv._json_depth_ok(b'{"a": {"b": 1}}') is True
    assert nv._json_depth_ok(json.dumps({"a": 1}).encode("utf-16")) is False
    assert nv._json_depth_ok(json.dumps({"a": 1}).encode("utf-32")) is False
    assert nv._json_depth_ok(b'\xff\xfe') is False
    assert nv._json_depth_ok('{"a": "\u00e9"}') is True
