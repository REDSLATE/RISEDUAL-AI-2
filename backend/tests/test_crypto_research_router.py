"""Tests for the cost-aware research router (`services.research_router`).

What this file pins down
------------------------
* The trigger gate (`should_run_web_research`) only fires for
  high-conviction trades or narrative-sensitive regimes — both rules
  documented in the source.
* HOLD signals always skip (no narrative value to gather).
* The cache (`web_research_cache`) returns within TTL, drops past TTL,
  and survives missing / malformed entries without raising.
* The kill-switch env var (`CRYPTO_SHADOW_RESEARCH_DISABLED=1`) returns
  ``None`` from `fetch_or_skip` BEFORE the gate runs — the cheapest
  possible exit.
* `fetch_or_skip` swallows fetcher exceptions and never re-raises into
  the bot's tick path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.research_router import (
    CACHE_TTL_SECONDS,
    HIGH_CONVICTION_CONFIDENCE,
    NARRATIVE_REGIMES,
    fetch_or_skip,
    get_cached_verdict,
    set_cached_verdict,
    should_run_web_research,
)


# ── Trigger gate ──────────────────────────────────────────────────────────────


def test_gate_skips_hold_direction():
    fire, reason = should_run_web_research(
        {"direction": "HOLD", "confidence": 0.95, "regime": "parabolic"},
    )
    assert fire is False
    assert reason == "direction_not_actionable"


def test_gate_fires_on_high_conviction_long():
    fire, reason = should_run_web_research(
        {"direction": "LONG", "confidence": 0.80, "regime": "trend_up"},
    )
    assert fire is True
    assert reason == "high_conviction"


def test_gate_fires_at_exact_threshold():
    fire, _ = should_run_web_research(
        {"direction": "LONG", "confidence": HIGH_CONVICTION_CONFIDENCE,
         "regime": "trend_up"},
    )
    assert fire is True


def test_gate_skips_low_conviction_neutral_regime():
    fire, reason = should_run_web_research(
        {"direction": "LONG", "confidence": 0.61, "regime": "trend_up"},
    )
    assert fire is False
    assert "low_conviction" in reason


def test_gate_fires_on_narrative_regime_even_at_low_confidence():
    for regime in NARRATIVE_REGIMES:
        fire, reason = should_run_web_research(
            {"direction": "SHORT", "confidence": 0.61, "regime": regime},
        )
        assert fire is True, f"regime {regime} should trigger"
        assert regime in reason


def test_gate_handles_missing_or_invalid_confidence():
    fire, _ = should_run_web_research(
        {"direction": "LONG", "confidence": None, "regime": "trend_up"},
    )
    assert fire is False
    fire, _ = should_run_web_research(
        {"direction": "LONG", "confidence": "not-a-number", "regime": "trend_up"},
    )
    assert fire is False


# ── Cache ─────────────────────────────────────────────────────────────────────


class _FakeCacheDB:
    """Minimal Motor stub for the ``web_research_cache`` collection."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

        outer = self

        class _Coll:
            async def find_one(self, query, _proj=None):
                return outer.docs.get(query.get("symbol"))

            async def update_one(self, query, update, upsert=False):
                sym = query.get("symbol")
                set_payload = update.get("$set") or {}
                outer.docs[sym] = dict(set_payload)

            async def create_index(self, *_a, **_kw):
                return None

        self._coll = _Coll()

    def __getitem__(self, _key):
        return self._coll


@pytest.mark.asyncio
async def test_cache_returns_none_when_db_missing():
    out = await get_cached_verdict(None, "BTC")
    assert out is None


@pytest.mark.asyncio
async def test_cache_round_trip_within_ttl():
    db = _FakeCacheDB()
    verdict = {"stance": "BULLISH", "rationale": "ETF inflows", "confidence": 0.7}
    await set_cached_verdict(db, "BTC", verdict)

    cached = await get_cached_verdict(db, "BTC")
    assert cached is not None
    assert cached["stance"] == "BULLISH"
    # Cache hits MUST flip the cached flag so downstream logs are honest.
    assert cached["cached"] is True


@pytest.mark.asyncio
async def test_cache_drops_past_ttl():
    db = _FakeCacheDB()
    db.docs["BTC"] = {
        "symbol": "BTC",
        "verdict": {"stance": "BEARISH"},
        "cached_at": datetime.now(timezone.utc)
        - timedelta(seconds=CACHE_TTL_SECONDS + 60),
    }
    cached = await get_cached_verdict(db, "BTC")
    assert cached is None


@pytest.mark.asyncio
async def test_cache_returns_none_on_malformed_entry():
    db = _FakeCacheDB()
    db.docs["BTC"] = {"symbol": "BTC", "verdict": "not a dict",
                       "cached_at": datetime.now(timezone.utc)}
    cached = await get_cached_verdict(db, "BTC")
    assert cached is None


# ── fetch_or_skip orchestrator ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_or_skip_short_circuits_when_kill_switch_set(monkeypatch):
    monkeypatch.setenv("CRYPTO_SHADOW_RESEARCH_DISABLED", "1")
    fetcher = AsyncMock()
    out = await fetch_or_skip(
        _FakeCacheDB(),
        "BTC",
        {"direction": "LONG", "confidence": 0.95, "regime": "parabolic"},
        fetcher=fetcher,
    )
    assert out is None
    fetcher.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_or_skip_skips_low_conviction(monkeypatch):
    monkeypatch.delenv("CRYPTO_SHADOW_RESEARCH_DISABLED", raising=False)
    fetcher = AsyncMock()
    out = await fetch_or_skip(
        _FakeCacheDB(),
        "BTC",
        {"direction": "LONG", "confidence": 0.61, "regime": "trend_up"},
        fetcher=fetcher,
    )
    assert out is None
    fetcher.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_or_skip_calls_fetcher_on_high_conviction(monkeypatch):
    monkeypatch.delenv("CRYPTO_SHADOW_RESEARCH_DISABLED", raising=False)
    db = _FakeCacheDB()

    async def fake_fetcher(symbol, _signal):
        return {
            "stance": "BULLISH",
            "rationale": "spot ETF inflows",
            "confidence": 0.7,
            "agreement": "agree",
            "sources": [],
            "tavily_answer": "",
            "query": "q",
            "elapsed_ms": 1,
            "cached": False,
            "shadow_only": True,
            "error": None,
        }

    out = await fetch_or_skip(
        db,
        "BTC",
        {"direction": "LONG", "confidence": 0.85, "regime": "trend_up"},
        fetcher=fake_fetcher,
    )
    assert out is not None
    assert out["stance"] == "BULLISH"
    assert out["trigger_reason"] == "high_conviction"
    # Persisted in cache so the next tick gets the cached path.
    assert "BTC" in db.docs


@pytest.mark.asyncio
async def test_fetch_or_skip_uses_cache_on_repeat_calls(monkeypatch):
    monkeypatch.delenv("CRYPTO_SHADOW_RESEARCH_DISABLED", raising=False)
    db = _FakeCacheDB()
    calls = {"n": 0}

    async def fake_fetcher(_sym, _signal):
        calls["n"] += 1
        return {
            "stance": "NEUTRAL", "rationale": "mixed", "confidence": 0.4,
            "agreement": "neutral", "sources": [], "tavily_answer": "",
            "query": "q", "elapsed_ms": 1, "cached": False,
            "shadow_only": True, "error": None,
        }

    sig = {"direction": "LONG", "confidence": 0.85, "regime": "trend_up"}
    first = await fetch_or_skip(db, "BTC", sig, fetcher=fake_fetcher)
    second = await fetch_or_skip(db, "BTC", sig, fetcher=fake_fetcher)

    assert first is not None and second is not None
    assert calls["n"] == 1, "second call must be served from cache"
    assert second["cached"] is True


@pytest.mark.asyncio
async def test_fetch_or_skip_swallows_fetcher_exception(monkeypatch):
    monkeypatch.delenv("CRYPTO_SHADOW_RESEARCH_DISABLED", raising=False)

    async def broken_fetcher(*_a, **_kw):
        raise RuntimeError("Tavily down")

    out = await fetch_or_skip(
        _FakeCacheDB(),
        "BTC",
        {"direction": "LONG", "confidence": 0.85, "regime": "trend_up"},
        fetcher=broken_fetcher,
    )
    assert out is None
