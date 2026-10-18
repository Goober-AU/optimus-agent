
"""w2-bash python-side acceptance probe (repo src, real Windows job containment).

Every check prints one line: PASS/FAIL name detail. Exit code 1 on any FAIL.
"""
import asyncio
import os
import subprocess
import sys
import time

import rlm
from rlm import bash as bash_module

print("rlm.__file__:", os.path.dirname(rlm.__file__))
print("PRIME_AGENT_BASH_SHELL:", os.environ.get("PRIME_AGENT_BASH_SHELL"))

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" :: " + str(detail) if detail else ""))
    if not ok:
        failures.append(name)


def win_alive(pid):
    out = subprocess.run(
        [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tasklist.exe"),
         "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
        capture_output=True, text=True, timeout=20,
    ).stdout
    return str(pid) in out and "No tasks" not in out


async def main():
    started = time.monotonic()
    result = await bash_module.bash("echo hi")
    check("await_bash_exit0", result.exit_code == 0, result.exit_code)
    check("await_bash_output", "hi" in result.output, repr(result.output))
    check("await_bash_duration", result.duration >= 0, result.duration)

    # background handle API
    handle = bash_module.bash("sleep 6")
    check("handle_running_before", handle.running is True)
    check("handle_poll_none", handle.poll() is None)
    pid = handle.pid
    handle.output()
    handle.tail(5)
    handle.kill()
    killed = await asyncio.wait_for(handle._wait(), 20)
    check("kill_terminates", killed.exit_code is not None, killed.exit_code)
    gone = False
    for _ in range(100):
        if not win_alive(pid):
            gone = True
            break
        await asyncio.sleep(0.1)
    check("kill_process_gone", gone, pid)

    # poll and output after completion
    handle = bash_module.bash("printf 'done\\n'")
    result2 = await handle
    check("poll_equals_await", handle.poll() == result2)
    check("output_after_done", "done" in handle.output())

    # concurrent handles
    handles = [bash_module.bash(f"sleep 0.{i}; echo token-{i}") for i in range(1, 4)]
    results = await asyncio.wait_for(asyncio.gather(*handles), 60)
    for i, res in enumerate(results, 1):
        check(f"concurrent_{i}", res.exit_code == 0 and f"token-{i}" in res.output, repr(res.output))

    # immediate-await cancellation
    before = time.monotonic()
    pid_file = os.path.join(os.environ.get("TEMP", "."), f"w2probe-{os.getpid()}.pid")
    handle = bash_module.bash(f"echo $$ > '{pid_file}'; sleep 30")
    waiter = asyncio.ensure_future(handle._wait_owned())
    await asyncio.sleep(1.0)
    spawned_pid = handle.pid
    waiter.cancel()
    try:
        await waiter
        cancelled = False
    except asyncio.CancelledError:
        cancelled = True
    check("immediate_await_cancelled", cancelled)
    check("immediate_await_bounded", time.monotonic() - before < 15, round(time.monotonic() - before, 2))
    gone = False
    for _ in range(100):
        if not win_alive(spawned_pid):
            gone = True
            break
        await asyncio.sleep(0.1)
    check("immediate_await_process_gone", gone, spawned_pid)

    # descendant holding the stdout pipe must not block the await
    began = time.monotonic()
    result3 = await asyncio.wait_for(bash_module.bash("sleep 25 & echo held-pipe-ok"), 30)
    elapsed = time.monotonic() - began
    check("background_child_returns_fast", elapsed < 10, round(elapsed, 2))
    check("background_child_output", "held-pipe-ok" in result3.output, repr(result3.output))
    check("background_child_exit0", result3.exit_code == 0, result3.exit_code)

    # empty output and non-zero exit through the wrapper
    empty = await bash_module.bash("true")
    check("empty_output", empty.output == "" and empty.exit_code == 0, repr(empty.output))
    failing = await bash_module.bash("exit 7")
    check("nonzero_exit", failing.exit_code == 7, failing.exit_code)
    missing = await bash_module.bash("pi_no_such_command_zz")
    check("missing_command_127", missing.exit_code == 127, missing.exit_code)
    unicode_result = await bash_module.bash("printf 'h\\303\\251llo \\317\\200\\n'")
    check("unicode_output", unicode_result.output == "h\u00e9llo \u03c0\n", repr(unicode_result.output))

    # pipeline status (pipefail is enabled by the wrapper for bash)
    pipeline = await bash_module.bash("false | cat")
    check("pipeline_status", pipeline.exit_code == 1, pipeline.exit_code)

    print("total elapsed", round(time.monotonic() - started, 2))
    print("FAILURES:", failures)


asyncio.run(main())
sys.exit(1 if failures else 0)
