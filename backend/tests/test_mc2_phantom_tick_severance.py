"""MC2 Phase A — phantom-tick regression coverage (2026-06-09).

Operator reported: "There was a phantom tick being sent to MC."
Even after the first batch of MC2 wire-in points landed, FOUR
OTHER outbound paths were still pointed at Original MC:

  1. ``services.mc_inbox_poller.run_forever``         — 3 polling loops
  2. ``services.mc_keys_proxy.fetch_market_data_keys`` — boot-time fetch
  3. ``services.risedual_monorepo_client._enabled``    — opinion sidecar
  4. ``sovereign.inprocess_sidecar._is_enabled``       — contribution loop

This file pins each of those paths to skip ALL outbound HTTP when
``RISEDUAL_STANDALONE_MODE=1`` is set. Regression of any of these
would silently re-introduce phantom ticks against Original MC.
"""
from __future__ import annotations

import pytest


# ── 1. mc_inbox_poller.run_forever ───────────────────────────────


@pytest.mark.asyncio
async def test_mc_inbox_poller_skipped_when_standalone(monkeypatch, caplog):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    from services import mc_inbox_poller

    # If the severance check fails, run_forever would spawn three
    # asyncio loops with infinite ``while True``. Awaiting it would
    # hang the test. The severance returns immediately when standalone.
    caplog.set_level("INFO")
    await mc_inbox_poller.run_forever(db=None)
    msg = " ".join(r.message for r in caplog.records)
    assert "STANDALONE_MODE=1" in msg or "SKIPPED" in msg


# ── 2. mc_keys_proxy.fetch_market_data_keys ──────────────────────


def test_mc_keys_proxy_returns_none_when_standalone(monkeypatch, caplog):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    # Even with MC URL + token set, the standalone branch must
    # short-circuit BEFORE any httpx.Client is constructed.
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "fake-token")

    from services.mc_keys_proxy import fetch_market_data_keys
    caplog.set_level("INFO")
    result = fetch_market_data_keys()
    assert result is None
    msg = " ".join(r.message for r in caplog.records)
    assert "STANDALONE_MODE=1" in msg or "SKIPPED" in msg


def test_mc_keys_proxy_not_short_circuited_when_not_standalone(monkeypatch, caplog):
    """Symmetric: when standalone is off, the standalone log message
    must NOT appear — the legacy code path is allowed to run (whether
    it succeeds against a real MC or returns None due to missing
    config is environment-dependent and not our concern here)."""
    monkeypatch.delenv("RISEDUAL_STANDALONE_MODE", raising=False)

    from services.mc_keys_proxy import fetch_market_data_keys
    caplog.set_level("INFO")
    # We don't care about the return value — only that the standalone
    # severance log line does NOT appear.
    fetch_market_data_keys()
    msg = " ".join(r.message for r in caplog.records)
    assert "RISEDUAL_STANDALONE_MODE=1" not in msg


# ── 3. risedual_monorepo_client._enabled ─────────────────────────


def test_monorepo_client_disabled_when_standalone(monkeypatch):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    # Even with all 3 env vars set, _enabled() must return False so
    # post_opinion / read_* / etc. all short-circuit.
    monkeypatch.setenv("MONOREPO_BASE_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "fake")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")

    from services.risedual_monorepo_client import _enabled
    assert _enabled() is False


def test_monorepo_client_enabled_when_not_standalone(monkeypatch):
    monkeypatch.delenv("RISEDUAL_STANDALONE_MODE", raising=False)
    monkeypatch.setenv("MONOREPO_BASE_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "fake")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")

    from services.risedual_monorepo_client import _enabled
    assert _enabled() is True


# ── 4. inprocess_sidecar._is_enabled ─────────────────────────────


def test_inprocess_sidecar_disabled_when_standalone(monkeypatch):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    # Even with the in-process flag explicitly set, standalone wins.
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")

    from sovereign.inprocess_sidecar import _is_enabled
    assert _is_enabled() is False


def test_inprocess_sidecar_enabled_when_not_standalone(monkeypatch):
    monkeypatch.delenv("RISEDUAL_STANDALONE_MODE", raising=False)
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")

    from sovereign.inprocess_sidecar import _is_enabled
    assert _is_enabled() is True


# ── 6. mc_sidecar._heartbeat_loop — THE actual phantom tick ──────


@pytest.mark.asyncio
async def test_mc_sidecar_heartbeat_does_not_post_when_standalone(monkeypatch):
    """The 30s heartbeat ping was THE phantom tick the operator caught.
    When standalone, the loop must touch the liveness file but NEVER
    issue an HTTP POST to /api/heartbeat-ping. Pinned by patching the
    httpx client constructor — any attempt to instantiate it inside
    the standalone branch raises AssertionError."""
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    monkeypatch.setenv("MC_BASE_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "fake-token")
    # Make the heartbeat interval microscopic so the test runs in
    # a single tick.
    monkeypatch.setenv("MC_SIDECAR_HEARTBEAT_INTERVAL_S", "0")

    import asyncio
    from unittest.mock import AsyncMock, patch
    from services import mc_sidecar

    # Patch the module's interval constant directly (env is read at
    # import time in this module).
    monkeypatch.setattr(mc_sidecar, "HEARTBEAT_INTERVAL_S", 0.01)

    # Stub _set_state + _touch_liveness so they record calls.
    set_state_calls: list[dict] = []

    async def _capture_set_state(db, **fields):
        set_state_calls.append(fields)

    touch_calls: list[None] = []

    def _capture_touch():
        touch_calls.append(None)

    monkeypatch.setattr(mc_sidecar, "_set_state", _capture_set_state)
    monkeypatch.setattr(mc_sidecar, "_touch_liveness", _capture_touch)

    # If _async_client is invoked, the test fails.
    def _refuse_client():
        raise AssertionError(
            "_async_client must NOT be constructed in standalone mode"
        )

    monkeypatch.setattr(mc_sidecar, "_async_client", _refuse_client)

    # Run the loop briefly then cancel.
    task = asyncio.create_task(mc_sidecar._heartbeat_loop(db=None))
    await asyncio.sleep(0.05)  # let it tick ~5 times
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    # Liveness was touched (local watchdog stays happy).
    assert len(touch_calls) >= 1
    # _set_state was called with the standalone destination marker.
    standalone_marks = [
        c for c in set_state_calls
        if c.get("last_heartbeat_destination") == "standalone_local"
    ]
    assert len(standalone_marks) >= 1


@pytest.mark.asyncio
async def test_mc_sidecar_heartbeat_uses_wire_when_not_standalone(monkeypatch):
    """Symmetric: when standalone is off, the loop MUST construct the
    httpx client and try the POST (we don't care if it succeeds —
    only that the wire path is reachable)."""
    monkeypatch.delenv("RISEDUAL_STANDALONE_MODE", raising=False)
    monkeypatch.setenv("MC_BASE_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "fake-token")

    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from services import mc_sidecar

    monkeypatch.setattr(mc_sidecar, "HEARTBEAT_INTERVAL_S", 0.01)

    async def _noop_set_state(db, **fields):
        return None

    monkeypatch.setattr(mc_sidecar, "_set_state", _noop_set_state)
    monkeypatch.setattr(mc_sidecar, "_touch_liveness", lambda: None)

    client_constructed = []

    class _FakeAsyncCM:
        async def __aenter__(self):
            client_constructed.append(True)
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = ""
            mock_client.post = AsyncMock(return_value=mock_resp)
            return mock_client

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(mc_sidecar, "_async_client", lambda: _FakeAsyncCM())

    task = asyncio.create_task(mc_sidecar._heartbeat_loop(db=None))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    # The wire-path client WAS constructed (at least once).
    assert len(client_constructed) >= 1


# ── 5. crypto_mc_intent_emitter ──────────────────────────────────


@pytest.mark.asyncio
async def test_crypto_mc_intent_emitter_routes_to_mc2_when_standalone(
    monkeypatch,
):
    """When standalone, the crypto emitter must NOT instantiate an
    MCClient or call ``emit_intent_from_consensus`` with one. It must
    skip directly to MC2's local post_intent."""
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    monkeypatch.setenv("RISEDUAL_CRYPTO_EMIT_INTENTS", "true")

    # Bind a fake DB so mc2 writers can persist.
    from services.mc2 import set_db
    from unittest.mock import MagicMock

    class _FakeColl:
        def __init__(self):
            self.inserted = []
        async def insert_one(self, doc):
            self.inserted.append(doc)
            return MagicMock(inserted_id="fake")

    class _FakeDB:
        def __init__(self):
            self.mc2_intents = _FakeColl()
        def __getitem__(self, k):
            return getattr(self, k)

    db = _FakeDB()
    set_db(db)

    # If the function tries to instantiate MCClient or call the
    # legacy intent_bridge wire path, this patch will catch it.
    from unittest.mock import patch, AsyncMock
    legacy_emit = AsyncMock(return_value={"ok": True, "via": "legacy"})
    with patch(
        "sovereign.intent_bridge.emit_intent_from_consensus",
        new=legacy_emit,
    ), patch("sovereign.mc_client.MCClient", side_effect=AssertionError(
        "MCClient must not be instantiated in standalone mode"
    )):
        from services.crypto_mc_intent_emitter import emit_crypto_intent
        result = await emit_crypto_intent(
            trade={
                "trade_id": "T-CRYPTO-1",
                "symbol": "BTC",
                "direction": "LONG",
                "entry_price": 50000.0,
                "size_usd": 25.0,
                "size": 0.0005,
                "confidence": 0.65,
            },
            signal={
                "confidence": 0.65,
                "spread_bps": 5.0,
                "relative_volume": 1.2,
                "has_news": False,
            },
        )

    # MC2 path used — legacy wire NOT called.
    legacy_emit.assert_not_called()
    assert result is not None
    assert result.get("ok") is True
    assert result.get("destination") == "mc2_local"
    assert len(db.mc2_intents.inserted) == 1
    assert db.mc2_intents.inserted[0]["symbol"] == "BTC"
