"""Offline admission regressions; no GPU or semantic-transfer claim."""
import pytest

from occ3_sglang.attachment_contract import AttachmentError, BackendReadiness
from test_chat_attachment import call, harness
from test_chat_source_capture import source_call, source_harness


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("outcome", ["ready", "denied", "still_stale", "refresh_failed"])
async def test_stale_readiness_refresh_follows_identity(source, outcome, request):
    h = request.getfixturevalue("source_harness" if source else "harness")
    calls = []
    ready = h.backend.readiness()
    fresh = False
    h.backend.readiness = lambda: ready if fresh else BackendReadiness()
    original_authorize = h.adapter.authorize

    async def authorize(raw):
        calls.append("authorize")
        if outcome == "denied":
            raise AttachmentError("identity_mismatch", 401)
        return await original_authorize(raw)

    async def refresh():
        nonlocal fresh
        calls.append("refresh")
        if outcome == "refresh_failed":
            raise AttachmentError("unsupported_attachment", 503)
        fresh = outcome == "ready"

    h.adapter.authorize = authorize
    h.backend.refresh_readiness = refresh
    response, _, _ = await (source_call(h) if source else call(h))
    if outcome == "ready":
        assert response.status_code == 200, response.body
        assert calls == ["authorize", "refresh"]
        assert len(h.tm.calls) == 1
    else:
        assert response.status_code == (401 if outcome == "denied" else 503)
        assert calls == (["authorize"] if outcome == "denied" else ["authorize", "refresh"])
        assert not h.tm.calls and not h.chat.converted
