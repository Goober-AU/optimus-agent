"""Execution-provenance token plumbing for the kernel line protocol (v4).

The token is internal host<->runtime IPC: it rides the execute frame and is
echoed on host_request frames issued from that execution's context. This
file exercises the runtime side only; host-side refusal semantics live in
the Rust manager tests.
"""

from __future__ import annotations

import unittest

from test_repl import ReplProcess, reply_ok

TOKEN = "ab" * 32
OTHER = "cd" * 32


def until_host_request(repl: ReplProcess) -> dict:
    while True:
        event = repl.read_event()
        if event.get("event") == "host_request":
            return event


def drain_done(repl: ReplProcess, rid: str) -> None:
    while True:
        event = repl.read_event()
        if event.get("event") == "done" and event.get("id") == rid:
            return


class ExecutionProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repl = ReplProcess()
        self.addCleanup(self.repl.close)
        self.ready_event, _ = self.repl.ready()

    def test_ready_announces_protocol_4(self):
        self.assertEqual(self.ready_event["event"], "ready")
        self.assertEqual(self.ready_event["protocol"], 4)

    def test_tokenless_execute_omits_the_field_on_host_requests(self):
        self.repl.send({"type": "execute", "id": "t0", "code": (
            "from rlm.repl import host_request\n"
            "reply = await host_request({'type': 'demo', 'value': 1})\n"
            "assert reply == {'status': 'ok', 'result': {}}"
        )})
        request = until_host_request(self.repl)
        self.assertNotIn("executionToken", request)
        reply_ok(self.repl, request)
        drain_done(self.repl, "t0")

    def test_in_cell_host_request_carries_the_cells_token(self):
        self.repl.send({"type": "execute", "id": "t1", "code": (
            "from rlm.repl import host_request\n"
            "reply = await host_request({'type': 'demo', 'value': 1})\n"
            "assert reply == {'status': 'ok', 'result': {}}"
        ), "executionToken": TOKEN})
        request = until_host_request(self.repl)
        self.assertEqual(request["executionToken"], TOKEN)
        reply_ok(self.repl, request)
        drain_done(self.repl, "t1")

    def test_detached_task_keeps_the_creating_cells_token(self):
        # The cell schedules a detached task and returns immediately; the
        # task fires after the cell's done event and must still carry the
        # cell's token (asyncio tasks copy the cell's context).
        self.repl.send({"type": "execute", "id": "t2", "code": (
            "import asyncio\n"
            "async def _late():\n"
            "    await asyncio.sleep(0.2)\n"
            "    from rlm.repl import host_request\n"
            "    await host_request({'type': 'demo'})\n"
            "asyncio.create_task(_late())"
        ), "executionToken": TOKEN})
        drain_done(self.repl, "t2")
        request = until_host_request(self.repl)
        self.assertEqual(request["executionToken"], TOKEN)
        reply_ok(self.repl, request)
        # A later tokenless cell must not inherit the old token.
        self.repl.send({"type": "execute", "id": "t3", "code": (
            "from rlm.repl import host_request\n"
            "await host_request({'type': 'demo'})"
        )})
        request = until_host_request(self.repl)
        self.assertNotIn("executionToken", request)
        reply_ok(self.repl, request)
        drain_done(self.repl, "t3")

    def test_the_next_cells_token_replaces_the_previous(self):
        self.repl.send({"type": "execute", "id": "t4", "code": "z = 3",
                        "executionToken": TOKEN})
        drain_done(self.repl, "t4")
        self.repl.send({"type": "execute", "id": "t5", "code": (
            "from rlm.repl import host_request\n"
            "await host_request({'type': 'demo'})"
        ), "executionToken": OTHER})
        request = until_host_request(self.repl)
        self.assertEqual(request["executionToken"], OTHER)
        reply_ok(self.repl, request)
        drain_done(self.repl, "t5")

    def test_non_string_token_values_are_treated_as_absent(self):
        # The runtime never fabricates or echoes a malformed token: a
        # non-string executionToken is ignored, so no field is attached.
        # (Host-side, a PRESENT malformed token still refuses.)
        self.repl.send({"type": "execute", "id": "t6", "code": (
            "from rlm.repl import host_request\n"
            "await host_request({'type': 'demo'})"
        ), "executionToken": 12345})
        request = until_host_request(self.repl)
        self.assertNotIn("executionToken", request)
        reply_ok(self.repl, request)
        drain_done(self.repl, "t6")

    def test_the_token_never_leaks_into_other_events(self):
        self.repl.send({"type": "execute", "id": "t7", "code": (
            "import sys\n"
            "print('cell output')\n"
            "sys.stderr.write('cell stderr\\n')\n"
            "raise ValueError('boom')"
        ), "executionToken": TOKEN})
        drain_done(self.repl, "t7")
        self.repl.shutdown()
        for line in self.repl.raw_lines:
            self.assertNotIn(TOKEN, line, line)
            self.assertNotIn(OTHER, line, line)

    def test_shutdown_after_a_tokened_cell_is_clean(self):
        self.repl.send({"type": "execute", "id": "t8", "code": "w = 4",
                        "executionToken": TOKEN})
        drain_done(self.repl, "t8")
        self.assertEqual(self.repl.shutdown(), 0)


if __name__ == "__main__":
    unittest.main()
