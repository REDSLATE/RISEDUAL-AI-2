"""Pytest coverage for the Block 3 discussion-layer methods appended to
``services.risedual_monorepo_client``.

Covers shape + best-effort fallback behavior; does NOT hit the real
monorepo (uses httpx MockTransport so the tests are deterministic).
"""
from __future__ import annotations

import json

import httpx
import pytest

from services import risedual_monorepo_client as mc


@pytest.fixture(autouse=True)
def _isolate_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with the sidecar reset + a clean client."""
    monkeypatch.setenv("MONOREPO_BASE_URL", "https://monorepo.invalid")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "tok-test")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "true")
    # Reset the module-level singleton client between tests.
    mc._client = None
    yield
    mc._client = None


def _install_mock_transport(handler) -> None:
    """Swap the module's httpx client for one with a MockTransport."""
    transport = httpx.MockTransport(handler)
    mc._client = httpx.AsyncClient(transport=transport, timeout=5.0)


@pytest.mark.asyncio
async def test_post_opinion_hits_ingest_opinion_endpoint() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content.decode())
        seen["headers"] = dict(req.headers)
        return httpx.Response(200, json={"ok": True, "id": "op-1"})

    _install_mock_transport(handler)

    resp = await mc.post_opinion(
        topic="market_state",
        stance="bull",
        body="trend strong on 4h",
        confidence=0.72,
        evidence={"refs": ["sov.weights"]},
        in_reply_to="op-zero",
    )
    assert resp == {"ok": True, "id": "op-1"}
    assert seen["url"].endswith("/api/ingest/opinion")
    assert seen["body"]["runtime"] == "alpha"
    assert seen["body"]["topic"] == "market_state"
    assert seen["body"]["stance"] == "bull"
    assert seen["body"]["confidence"] == 0.72
    assert seen["body"]["may_execute"] is False
    assert seen["body"]["evidence"] == {"refs": ["sov.weights"]}
    assert seen["body"]["in_reply_to"] == "op-zero"
    assert seen["headers"]["x-runtime-token"] == "tok-test"


@pytest.mark.asyncio
async def test_post_opinion_doctrine_may_execute_always_false() -> None:
    """The runtime cannot post an opinion claiming execution authority."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(200, json={"ok": True})

    _install_mock_transport(handler)
    await mc.post_opinion(topic="t", stance="s", body="b")
    assert captured["body"]["may_execute"] is False


@pytest.mark.asyncio
async def test_read_opinions_builds_filter_params() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"items": [], "count": 0})

    _install_mock_transport(handler)
    out = await mc.read_opinions(
        runtime="camaro", topic="market_state", symbol="BTC/USD",
        thread="t-1", since="2026-01-01T00:00:00Z", limit=25,
    )
    assert out == {"items": [], "count": 0}
    assert "/api/runtime-discussion/opinions" in seen["url"]
    assert seen["params"]["caller"] == "alpha"
    assert seen["params"]["runtime"] == "camaro"
    assert seen["params"]["topic"] == "market_state"
    assert seen["params"]["symbol"] == "BTC/USD"
    assert seen["params"]["thread"] == "t-1"
    assert seen["params"]["since"] == "2026-01-01T00:00:00Z"
    assert seen["params"]["limit"] == "25"


@pytest.mark.asyncio
async def test_read_opinions_omits_empty_filters() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"items": []})

    _install_mock_transport(handler)
    await mc.read_opinions(limit=5)
    # Only caller + limit should be present.
    assert set(seen["params"].keys()) == {"caller", "limit"}


@pytest.mark.asyncio
async def test_read_opinions_returns_safe_fallback_on_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    _install_mock_transport(handler)
    out = await mc.read_opinions()
    assert out["items"] == []
    assert out["count"] == 0
    assert "error" in out


@pytest.mark.asyncio
async def test_read_roles_manifest_uses_correct_endpoint() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"items": [{"runtime": "alpha"}], "count": 1})

    _install_mock_transport(handler)
    out = await mc.read_roles_manifest()
    assert out["items"][0]["runtime"] == "alpha"
    assert "/api/runtime-discussion/roles-manifest" in seen["url"]


@pytest.mark.asyncio
async def test_read_my_scorecard_uses_correct_endpoint_and_since() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"runtime": "alpha", "summary": {"correct": 5}})

    _install_mock_transport(handler)
    out = await mc.read_my_scorecard(since="2026-01-01T00:00:00Z")
    assert out["runtime"] == "alpha"
    assert out["summary"]["correct"] == 5
    assert "/api/runtime-discussion/scorecard" in seen["url"]
    assert seen["params"]["caller"] == "alpha"
    assert seen["params"]["since"] == "2026-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_read_my_scorecard_omits_since_when_none() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"runtime": "alpha", "summary": {}})

    _install_mock_transport(handler)
    await mc.read_my_scorecard()
    assert set(seen["params"].keys()) == {"caller"}


@pytest.mark.asyncio
async def test_read_my_scorecard_returns_safe_fallback_on_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    _install_mock_transport(handler)
    out = await mc.read_my_scorecard()
    assert out["runtime"] == "alpha"
    assert out["summary"] == {}
    assert "error" in out


@pytest.mark.asyncio
async def test_discussion_methods_noop_when_sidecar_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """All Block 3 methods must return safe sentinel when the kill-switch is on."""
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "false")

    out1 = await mc.read_opinions()
    out2 = await mc.read_roles_manifest()
    out3 = await mc.read_my_scorecard()
    out4 = await mc.post_opinion(topic="t", stance="s", body="b")

    assert out1["error"] == "sidecar_disabled"
    assert out2["error"] == "sidecar_disabled"
    assert out3["error"] == "sidecar_disabled"
    # post_opinion routes through _post which returns the same sentinel.
    assert out4.get("error") == "sidecar_disabled"
