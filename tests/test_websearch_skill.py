"""Offline search aggregation and native-helper lifecycle tests."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

SOURCE = Path(__file__).resolve().parents[1] / "resources/agent/skills/websearch/src/websearch/websearch.py"
SPEC = importlib.util.spec_from_file_location("search_under_test", SOURCE)
search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search)

CODEX = {"status": "ok", "summary": "Codex finding.", "search_calls": 1,
         "sources": [{"title": "Primary", "url": "https://example.com/report?utm_source=openai"}]}
SERPER = {"organic": [{"title": "Duplicate", "link": "https://example.com/report#section"},
                      {"title": "Additional", "link": "https://other.example/report", "snippet": "Extra evidence."}],
          "knowledgeGraph": {"title": "Entity", "description": "Knowledge retained."},
          "peopleAlsoAsk": [{"question": "Why?", "snippet": "Related answer."}]}


class WebsearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="optimus search tests ")
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {"PRIME_AGENT_CODING_AGENT_DIR": self.temp.name}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def test_auto_runs_providers_concurrently_and_appends_unique_results(self):
        started = set()
        both = asyncio.Event()

        async def provider(name, value):
            started.add(name)
            if len(started) == 2:
                both.set()
            await both.wait()
            return value

        async def codex(*args): return await provider("codex", CODEX)
        async def serper(*args): return await provider("serper", SERPER)
        with patch.object(search, "_resolve_api_key", return_value="synthetic"), \
             patch.object(search, "_fetch_codex", codex), patch.object(search, "_fetch_serper", serper):
            output = await asyncio.wait_for(search.run("fixture"), 2)
        self.assertLess(output.index("Codex web search"), output.index("Serper Google search"))
        self.assertEqual(output.count("https://example.com/report"), 1)
        self.assertNotIn("utm_source", output)
        self.assertIn("https://other.example/report", output)
        self.assertIn("Knowledge retained", output)
        self.assertIn("Related answer", output)

    async def test_codex_only_needs_no_serper_and_forwards_cached_mode(self):
        with patch.object(search, "_resolve_api_key") as key, \
             patch.object(search, "_fetch_codex", new=AsyncMock(return_value=CODEX)) as codex, \
             patch.object(search, "_fetch_serper", new=AsyncMock()) as serper:
            output = await search.run("fixture", provider="codex", mode="cached")
        key.assert_not_called()
        serper.assert_not_called()
        codex.assert_awaited_once_with("fixture", 45, 5, "cached", "gpt-6-astra")
        self.assertIn("Codex web search (cached)", output)

    async def test_serper_only_never_starts_codex(self):
        with patch.object(search, "_resolve_api_key", return_value="synthetic"), \
             patch.object(search, "_fetch_codex", new=AsyncMock()) as codex, \
             patch.object(search, "_fetch_serper", new=AsyncMock(return_value=SERPER)):
            output = await search.run("fixture", provider="serper")
        codex.assert_not_called()
        self.assertIn("Serper Google search", output)

    async def test_provider_failure_preserves_other_results_and_redacts_errors(self):
        with patch.object(search, "_resolve_api_key", return_value="synthetic"), \
             patch.object(search, "_fetch_codex", new=AsyncMock(return_value=CODEX)), \
             patch.object(search, "_fetch_serper", new=AsyncMock(side_effect=RuntimeError("SECRET response body"))):
            output = await search.run("fixture")
        self.assertIn("Codex finding", output)
        self.assertIn("Serper search unavailable", output)
        self.assertNotIn("SECRET", output)
        with patch.object(search, "_resolve_api_key", return_value="synthetic"), \
             patch.object(search, "_fetch_codex", new=AsyncMock(return_value={"status":"error", "error":"SECRET"})), \
             patch.object(search, "_fetch_serper", new=AsyncMock(return_value=SERPER)):
            output = await search.run("fixture")
        self.assertIn("Extra evidence", output)
        self.assertIn("Codex search unavailable", output)
        self.assertNotIn("SECRET", output)

    async def test_no_login_gives_setup_guidance_and_makes_no_serper_request(self):
        with patch.object(search, "_native_binary", return_value=None), \
             patch.object(search, "_fetch_serper", new=AsyncMock()) as serper:
            output = await search.run("fixture")
        serper.assert_not_called()
        self.assertIn("/login", output)
        self.assertIn("OpenAI Codex", output)
        self.assertIn("Serper", output)
        self.assertNotIn("Prime Agent", output)

    async def test_malformed_serper_response_does_not_lose_codex_results(self):
        with patch.object(search, "_resolve_api_key", return_value="synthetic"), \
             patch.object(search, "_fetch_codex", new=AsyncMock(return_value=CODEX)), \
             patch.object(search, "_fetch_serper", new=AsyncMock(return_value={"organic":["invalid"]})):
            output = await search.run("fixture")
        self.assertIn("Codex finding", output)
        self.assertIn("Serper search unavailable", output)

    async def test_output_bounds_and_both_provider_sections(self):
        large_codex = dict(CODEX, summary="finding " * 2000)
        with patch.object(search, "_resolve_api_key", return_value="synthetic"), \
             patch.object(search, "_fetch_codex", new=AsyncMock(return_value=large_codex)), \
             patch.object(search, "_fetch_serper", new=AsyncMock(return_value=SERPER)):
            for limit in [1, 16, 128, 2048]:
                output = await search.run("fixture", max_output=limit)
                self.assertLessEqual(len(output), limit)
                if limit == 2048:
                    self.assertIn("Codex web search", output)
                    self.assertIn("Serper Google search", output)

    async def test_validation_precedes_network(self):
        for kwargs in [{"query":""}, {"query":"x", "provider":"unknown"},
                       {"query":"x", "mode":"other"}, {"query":"x", "max_output":0}]:
            with self.assertRaises(ValueError):
                await search.run(**kwargs)

    def test_serper_key_added_mid_session_is_seen(self):
        self.assertEqual(search._resolve_api_key(), "")
        (Path(self.temp.name) / "auth.json").write_text(json.dumps({"serper":{"type":"api_key","key":"synthetic"}}))
        self.assertEqual(search._resolve_api_key(), "synthetic")

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture; transport logic is shared")
    async def test_helper_uses_stdin_and_supports_executable_paths_with_spaces(self):
        helper = Path(self.temp.name) / "native helper"
        helper.write_text(f"#!{sys.executable}\nimport json,sys\nr=json.load(sys.stdin)\nassert sys.argv[1:] == ['--internal-codex-web-search']\nassert r['query'] == 'fixture'\nprint({json.dumps(json.dumps(CODEX))})\n")
        helper.chmod(0o700)
        with patch.object(search, "_native_binary", return_value=str(helper)):
            result = await search._fetch_codex("fixture", 2, 5, "live", "gpt-6-astra")
        self.assertEqual(result, CODEX)

    async def test_cancel_kills_and_reaps_native_helper(self):
        started = asyncio.Event()

        class Process:
            returncode = None
            killed = False
            waited = False

            async def communicate(self, _):
                started.set()
                await asyncio.Future()

            def kill(self):
                self.killed = True

            async def wait(self):
                self.waited = True
                self.returncode = -9

        process = Process()
        with patch.object(search, "_native_binary", return_value="fixture"), \
             patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            task = asyncio.create_task(search._fetch_codex("fixture", 2, 5, "live", "gpt-6-astra"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(process.killed and process.waited)


if __name__ == "__main__":
    unittest.main()
