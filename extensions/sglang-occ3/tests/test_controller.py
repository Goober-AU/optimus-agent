"""Offline unit tests for the OCC-3 deployment controller.

Covers the regression that caused the 2026-10-21 outage: docker 29.x
range templates append a trailing blank line even for non-empty ranges,
so line-based (mapfile) capture injected empty-string arguments into
the replicated launch command. The controller must (a) build every
argument array from `docker inspect` JSON, element-exact, (b) reject
any blank element, and (c) structurally reject any candidate whose
recorded configuration differs from the original beyond the explicitly
allowed OCC-3 changes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
spec = importlib.util.spec_from_file_location(
    "occ3_controller", DEPLOY_DIR / "occ3_controller.py"
)
controller_mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("occ3_controller", controller_mod)
spec.loader.exec_module(controller_mod)


OLD_KEY = "a" * 64
NEW_KEY = "b" * 64


def make_norm(**overrides):
    norm = {
        "Entrypoint": ["python3"],
        "Cmd": [],
        "Env": ["A=1", "B=2"],
        "WorkingDir": "/sgl-workspace/sglang",
        "User": "",
        "Binds": [],
        "Mounts": [],
        "PortBindings": {"30001/tcp": [("", "30001")]},
        "NetworkMode": "neo-k3-ingress-net",
        "Networks": ["neo-k3-ingress-net"],
        "IpcMode": "host",
        "PidMode": "",
        "Runtime": "nvidia",
        "ShmSize": 67108864,
        "Ulimits": [],
        "Tmpfs": {},
        "ExtraHosts": [],
        "DeviceRequests": [{"Driver": "", "Count": -1,
                            "DeviceIDs": [], "Capabilities": [["gpu"]]}],
        "RestartPolicy": {"Name": "no"},
        "AutoRemove": False,
        "Privileged": False,
        "CapAdd": [],
        "CapDrop": [],
        "SecurityOpt": [],
        "Labels": {},
        "OomKillDisable": False,
        "Memory": 0,
        "NanoCpus": 0,
        "Init": None,
    }
    norm.update(overrides)
    return norm


# --- the outage regression -------------------------------------------------


def test_docker29_trailing_blank_reproduces_the_outage_bug():
    """Line-based capture of a one-element entrypoint yields an extra ""."""
    template_lines = ["python3", ""]  # mapfile over the range output
    json_array = ["python3"]
    assert len(template_lines) == len(json_array) + 1
    assert template_lines[-1] == ""


def test_blank_elements_are_rejected_everywhere():
    for bad in (["python3", "", "-m"], ["x", " "], [""]):
        with pytest.raises(controller_mod.Failure):
            controller_mod.assert_no_blank_elements(bad, "unit")


def test_built_args_carry_no_blank_elements():
    new_cmd = ["-m", "sglang.launch_server", "--api-key", NEW_KEY]
    args = controller_mod.build_run_args(
        make_norm(), "img", "cand", new_cmd, ["A=1"]
    )
    assert all(isinstance(a, str) and a.strip() for a in args)


def test_built_args_replicate_the_captured_configuration():
    new_cmd = ["-m", "sglang.launch_server", "--api-key", NEW_KEY]
    args = controller_mod.build_run_args(
        make_norm(), "img", "cand", new_cmd, ["A=1", "B=2"]
    )
    joined = " ".join(args)
    assert "--entrypoint python3" in joined
    assert "--workdir /sgl-workspace/sglang" in joined
    assert "--ipc host" in joined
    assert "--runtime nvidia" in joined
    assert "--shm-size 67108864" in joined
    assert "--gpus all" in joined
    assert "--network neo-k3-ingress-net" in joined
    assert "-p 30001:30001/tcp" in joined
    assert "-e A=1" in joined and "-e B=2" in joined
    # the command tail is exactly entrypoint+cmd with no blanks
    tail = args[args.index("img") + 1:]
    assert tail == ["-m", "sglang.launch_server", "--api-key", NEW_KEY]


def test_multi_element_entrypoint_moves_tail_into_command():
    norm = make_norm(Entrypoint=["python3", "-u"], Cmd=[])
    args = controller_mod.build_run_args(
        norm, "img", "cand", ["-m", "x"], []
    )
    tail = args[args.index("img") + 1:]
    assert tail == ["-u", "-m", "x"]


# --- the structural diff allowlist ------------------------------------------


def original_and_candidate(mutate=None):
    original = make_norm(Cmd=["-m", "x", "--api-key", OLD_KEY])
    candidate = make_norm(Cmd=["-m", "x", "--api-key", NEW_KEY])
    if mutate:
        mutate(candidate)
    return original, candidate


def test_key_rotation_is_the_only_accepted_cmd_change():
    original, candidate = original_and_candidate()
    problems = controller_mod.diff_configs(
        original, candidate, 3, OLD_KEY, NEW_KEY
    )
    assert problems == []


def test_injected_empty_argument_is_rejected():
    original, candidate = original_and_candidate(
        mutate=lambda c: c.update(Cmd=["", "-m", "x", "--api-key", NEW_KEY])
    )
    problems = controller_mod.diff_configs(
        original, candidate, 3, OLD_KEY, NEW_KEY
    )
    assert problems


def test_any_other_field_change_is_rejected():
    for mutate in (
        lambda c: c.update(WorkingDir="/elsewhere"),
        lambda c: c.update(Binds=["/host:/ctr"]),
        lambda c: c["PortBindings"].update({"9000/tcp": [("", "9000")]}),
        lambda c: c.update(Networks=["other-net"]),
        lambda c: c.update(Env=["A=1", "B=2", "C=3"]),
        lambda c: c.update(Runtime="runc"),
        lambda c: c.update(IpcMode=""),
        lambda c: c["DeviceRequests"].clear(),
        lambda c: c.update(ShmSize=64),
    ):
        original, candidate = original_and_candidate(mutate=mutate)
        problems = controller_mod.diff_configs(
            original, candidate, 3, OLD_KEY, NEW_KEY
        )
        assert problems, mutate


def test_wrong_key_position_is_rejected():
    original, candidate = original_and_candidate(
        mutate=lambda c: c.update(Cmd=["-m", "x", NEW_KEY, "--api-key"])
    )
    problems = controller_mod.diff_configs(
        original, candidate, 3, OLD_KEY, NEW_KEY
    )
    assert problems


# --- key hygiene -------------------------------------------------------------


def test_replace_api_key_keeps_position():
    cmd = ["-m", "x", "--api-key", OLD_KEY, "--tp-size", "8"]
    out, index = controller_mod.replace_api_key(cmd, NEW_KEY)
    assert index == 3
    assert out[3] == NEW_KEY
    assert out[:3] == cmd[:3] and out[4:] == cmd[4:]


def test_api_key_from_cmd_forms():
    assert controller_mod.api_key_from_cmd(["--api-key", "k"]) == "k"
    assert controller_mod.api_key_from_cmd(["--api-key=k2"]) == "k2"
    assert controller_mod.api_key_from_cmd(["-m", "x"]) is None


def test_controller_redacts_keys_from_logs_and_status(tmp_path):
    controller = controller_mod.Controller(str(tmp_path), verbose=False)
    controller.register_secret(OLD_KEY)
    controller.register_secret(NEW_KEY)
    controller.log("running with " + OLD_KEY + " then " + NEW_KEY)
    controller.write_status({"phase": "t", "key": NEW_KEY})
    log_text = (tmp_path / "controller.log").read_text(encoding="utf-8")
    status_text = json.loads((tmp_path / "status.json").read_text())
    assert OLD_KEY not in log_text and NEW_KEY not in log_text
    assert controller_mod.KEY_SENTINEL in log_text
    assert status_text["key"] == controller_mod.KEY_SENTINEL


def test_controller_refuses_secrets_in_argv(tmp_path):
    controller = controller_mod.Controller(str(tmp_path), verbose=False)
    controller.register_secret(OLD_KEY)
    with pytest.raises(controller_mod.Failure):
        controller.run(["echo", OLD_KEY])
