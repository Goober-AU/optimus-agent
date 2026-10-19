import json
import unittest

from research.context_rot.adapters import (AdapterError, FakeAdapter, OfflineEchoAdapter,
                                           OpenAICompatAdapter)
from research.context_rot.tasks import render_messages


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response


def _messages(question="how many?"):
    class _Visible:
        answer_kind = "numeric"
        question = "placeholder"
    payload = {"context": "ctx", "question": question, "answer_kind": "numeric"}
    return render_messages(payload)


class OpenAICompatAdapterTest(unittest.TestCase):
    def test_request_shape(self):
        session = _Session(_FakeResponse({
            "model": "m1", "usage": {"prompt_tokens": 10, "completion_tokens": 3,
                                     "total_tokens": 13},
            "choices": [{"message": {"content": "12"}, "finish_reason": "stop"}],
        }))
        adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:43120/v1", model="m1",
                                      api_key="k", extra_headers={"X-Route": "gpu-a"},
                                      timeout=5, session=session)
        response = adapter.complete(_messages(), json_style=True, temperature=0.2,
                                    max_tokens=64)
        self.assertEqual(response.text, "12")
        self.assertEqual(response.usage["prompt_tokens"], 10)
        self.assertFalse(response.synthetic)
        call = session.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:43120/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer k")
        self.assertEqual(call["headers"]["X-Route"], "gpu-a")
        self.assertEqual(call["json"]["model"], "m1")
        self.assertEqual(call["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(call["json"]["max_tokens"], 64)
        self.assertEqual(call["json"]["temperature"], 0.2)
        self.assertEqual(call["timeout"], 5)

    def test_http_error_raises_adapter_error(self):
        session = _Session(_FakeResponse({"error": "nope"}, status_code=500))
        adapter = OpenAICompatAdapter(base_url="http://x/v1", model="m", session=session)
        with self.assertRaises(AdapterError) as ctx:
            adapter.complete(_messages())
        self.assertEqual(ctx.exception.status, 500)

    def test_bad_payload_raises(self):
        session = _Session(_FakeResponse({"nothing": True}))
        adapter = OpenAICompatAdapter(base_url="http://x/v1", model="m", session=session)
        with self.assertRaises(AdapterError):
            adapter.complete(_messages())

    def test_requires_base_url_and_model(self):
        with self.assertRaises(ValueError):
            OpenAICompatAdapter(base_url="", model="m")
        with self.assertRaises(ValueError):
            OpenAICompatAdapter(base_url="http://x", model="")


class OfflineAdaptersTest(unittest.TestCase):
    def test_echo_abstains(self):
        adapter = OfflineEchoAdapter()
        response = adapter.complete(_messages())
        self.assertIn("ABSTAIN", response.text)
        self.assertTrue(response.synthetic)
        self.assertEqual(response.provider, "offline")

    def test_fake_adapter_looks_up_the_answer(self):
        adapter = FakeAdapter({"how many?": ("12", "numeric")}, seed=1)
        response = adapter.complete(_messages())
        self.assertTrue(response.synthetic)
        self.assertIn("12", response.text)
        self.assertEqual(response.usage["source"], "estimate")
        self.assertIn("prompt_tokens", response.usage)

    def test_fake_adapter_is_deterministic(self):
        adapter = FakeAdapter({"how many?": ("12", "numeric")}, seed=3,
                              base_accuracy=0.5, length_penalty_per_1k=0.01)
        first = [adapter.complete(_messages()).text for _ in range(3)]
        second = [FakeAdapter({"how many?": ("12", "numeric")}, seed=3,
                              base_accuracy=0.5, length_penalty_per_1k=0.01)
                  .complete(_messages()).text for _ in range(3)]
        self.assertEqual(first, second)

    def test_fake_adapter_json_style(self):
        adapter = FakeAdapter({"how many?": ("12", "numeric")}, seed=1)
        response = adapter.complete(_messages(), json_style=True)
        self.assertIn("answer", json.loads(response.text.splitlines()[-1]))

    def test_fake_adapter_unknown_question_abstains(self):
        adapter = FakeAdapter({}, seed=1)
        self.assertIn("ABSTAIN", adapter.complete(_messages("other question?")).text)

    def test_zero_accuracy_gives_wrong_or_abstain(self):
        adapter = FakeAdapter({"how many?": ("12", "numeric")}, seed=1, base_accuracy=0.0)
        text = adapter.complete(_messages()).text
        self.assertNotIn("FINAL ANSWER: 12", text)
        self.assertTrue("ABSTAIN" in text or "14" in text)


class ProtocolTest(unittest.TestCase):
    def test_adapters_satisfy_protocol(self):
        from research.context_rot.adapters import ModelAdapter
        self.assertIsInstance(FakeAdapter({}), ModelAdapter)
        self.assertIsInstance(OfflineEchoAdapter(), ModelAdapter)
        self.assertIsInstance(OpenAICompatAdapter("http://x", "m"), ModelAdapter)
