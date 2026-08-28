"""Tests for the Alpha Vantage live movers integration.

Two layers:

* ``services/alpha_live_movers.py`` — parse AV response, cache in
  Mongo, respect the TTL / rate-limit response, fall back to the
  cached copy on any error.

* Integration: ``_candidate_universe`` picks the movers up as
  source #2 (right after operator watchlist).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


from services import alpha_live_movers


# ─────────────────────────────────────────────
#  Test scaffolding — fake Mongo
# ─────────────────────────────────────────────
class _FakeCollection:
    """Just enough of the Motor API to back the cache read/write."""

    def __init__(self):
        self._doc = None

    async def find_one(self, _q, _proj=None):
        return dict(self._doc) if self._doc else None

    async def update_one(self, q, update, upsert=False):
        set_ = update.get("$set", {})
        # ``_id`` mimics the singleton doc key used by the service
        merged = {**(self._doc or {}), **set_, "_id": q.get("_id", "singleton")}
        self._doc = merged
        return MagicMock(matched_count=1, upserted_id=None)


class _FakeDB(dict):
    """Attribute + item access to fake collections."""

    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, _FakeCollection())
        return super().__getitem__(name)

    def __getattr__(self, name):
        return self[name]


def _run(coro):
    """Sync driver on a private loop — same pattern as the rest of
    this test bed to avoid touching the thread-default event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────
#  AV response → normalized rows
# ─────────────────────────────────────────────
def _av_shape(*, gainers=None, losers=None, actives=None) -> dict:
    """Minimal Alpha Vantage TOP_GAINERS_LOSERS response shape.

    We keep the field names exactly as AV returns them — string
    values, ``change_percentage`` trailing '%', etc — so the
    parser is exercised against the real wire format.
    """
    return {
        "metadata": "irrelevant",
        "last_updated": "2026-02-27 16:00:00 US/Eastern",
        "top_gainers": gainers or [],
        "top_losers": losers or [],
        "most_actively_traded": actives or [],
    }


def _row(ticker: str, pct: float, price: float = 100.0, volume: int = 1_000_000) -> dict:
    return {
        "ticker": ticker,
        "price": str(price),
        "change_amount": "0.5",
        "change_percentage": f"{pct}%",
        "volume": str(volume),
    }


# ─────────────────────────────────────────────
#  Fetch path
# ─────────────────────────────────────────────
def test_fetch_from_av_parses_all_three_buckets(monkeypatch):
    """AV returns three buckets — gainers, losers, most_actively_
    traded. The parser must surface all three with normalized rows."""
    monkeypatch.setenv("ALPHAVANTAGEAPIKEY", "test-key")

    class _FakeResp:
        def json(self):
            return _av_shape(
                gainers=[_row("AAA", 25.0), _row("BBB", 20.0)],
                losers=[_row("CCC", -18.0)],
                actives=[_row("DDD", 3.0)],
            )

    with patch.object(alpha_live_movers.requests, "get", return_value=_FakeResp()):
        result = alpha_live_movers._fetch_from_av()

    assert result is not None
    assert [r["symbol"] for r in result["gainers"]] == ["AAA", "BBB"]
    assert [r["symbol"] for r in result["losers"]] == ["CCC"]
    assert [r["symbol"] for r in result["actives"]] == ["DDD"]
    # AV strings coerced to floats/ints
    assert isinstance(result["gainers"][0]["price"], float)
    assert isinstance(result["gainers"][0]["change_pct"], float)
    assert isinstance(result["gainers"][0]["volume"], int)


def test_fetch_from_av_returns_none_when_rate_limited(monkeypatch):
    """AV signals rate-limit via ``Information``/``Note`` fields
    (no HTTP 429). Parser must recognize both and return None so
    the cache can serve the old copy."""
    monkeypatch.setenv("ALPHAVANTAGEAPIKEY", "test-key")

    class _RateLimited:
        def json(self):
            return {"Information": "Thank you for using Alpha Vantage!"}

    with patch.object(alpha_live_movers.requests, "get", return_value=_RateLimited()):
        assert alpha_live_movers._fetch_from_av() is None


def test_fetch_from_av_returns_none_when_no_api_key(monkeypatch):
    """No API key → no HTTP call, no exception. Must return None."""
    monkeypatch.delenv("ALPHAVANTAGEAPIKEY", raising=False)
    with patch.object(alpha_live_movers.requests, "get") as mock_get:
        result = alpha_live_movers._fetch_from_av()
        mock_get.assert_not_called()
    assert result is None


def test_fetch_from_av_returns_none_on_network_error(monkeypatch):
    """Timeouts / socket errors must not raise — we serve the old
    cache instead."""
    monkeypatch.setenv("ALPHAVANTAGEAPIKEY", "test-key")

    def _boom(*_args, **_kw):
        raise TimeoutError("simulated network timeout")

    with patch.object(alpha_live_movers.requests, "get", side_effect=_boom):
        assert alpha_live_movers._fetch_from_av() is None


def test_parse_bucket_skips_malformed_rows():
    """Rows missing ticker / with un-parseable numbers are dropped
    quietly. We NEVER let a single bad row kill the whole payload."""
    rows = [
        _row("GOOD", 5.0),
        {"ticker": "", "price": "10"},           # no ticker
        {"ticker": "BAD", "price": "not-a-number"},
    ]
    parsed = alpha_live_movers._parse_bucket(rows, "gainer")
    assert [r["symbol"] for r in parsed] == ["GOOD"]


# ─────────────────────────────────────────────
#  Cache / TTL behaviour
# ─────────────────────────────────────────────
def test_refresh_if_stale_uses_cache_when_fresh(monkeypatch):
    """A cache entry younger than TTL must not trigger a fetch."""
    monkeypatch.setenv("ALPHA_LIVE_MOVERS_TTL_SECONDS", "300")
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    _run(alpha_live_movers._write_cache(db, {
        "gainers": [{"symbol": "AAA", "side": "gainer"}],
        "losers": [], "actives": [],
        "fetched_at": now.isoformat(),
    }))

    fetch_calls: list = []

    def _fake_fetch():
        fetch_calls.append(True)
        return {"gainers": [], "losers": [], "actives": [], "fetched_at": now.isoformat()}

    monkeypatch.setattr(alpha_live_movers, "_fetch_from_av", _fake_fetch)

    result = _run(alpha_live_movers.refresh_if_stale(db))
    assert result is not None
    assert [r["symbol"] for r in result["gainers"]] == ["AAA"]
    assert fetch_calls == [], "must not have hit AV — cache was fresh"


def test_refresh_if_stale_refreshes_when_cache_older_than_ttl(monkeypatch):
    """Cache older than TTL → AV is called and cache is updated."""
    monkeypatch.setenv("ALPHA_LIVE_MOVERS_TTL_SECONDS", "60")
    db = _FakeDB()
    stale = datetime.now(timezone.utc) - timedelta(seconds=200)
    _run(alpha_live_movers._write_cache(db, {
        "gainers": [{"symbol": "OLD", "side": "gainer"}],
        "losers": [], "actives": [],
        "fetched_at": stale.isoformat(),
    }))

    fresh_payload = {
        "gainers": [{"symbol": "NEW", "side": "gainer",
                     "price": 1.0, "change_pct": 5.0, "volume": 1}],
        "losers": [], "actives": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(alpha_live_movers, "_fetch_from_av", lambda: fresh_payload)

    result = _run(alpha_live_movers.refresh_if_stale(db))
    assert result is not None
    assert [r["symbol"] for r in result["gainers"]] == ["NEW"]


def test_refresh_if_stale_falls_back_to_cache_when_fetch_fails(monkeypatch):
    """If the AV call returns None (rate-limit, network, whatever),
    the cache copy is what the caller gets — never an empty
    payload."""
    db = _FakeDB()
    stale = datetime.now(timezone.utc) - timedelta(seconds=999)
    _run(alpha_live_movers._write_cache(db, {
        "gainers": [{"symbol": "SURVIVES", "side": "gainer"}],
        "losers": [], "actives": [],
        "fetched_at": stale.isoformat(),
    }))
    monkeypatch.setattr(alpha_live_movers, "_fetch_from_av", lambda: None)

    result = _run(alpha_live_movers.refresh_if_stale(db))
    assert result is not None
    assert [r["symbol"] for r in result["gainers"]] == ["SURVIVES"]


# ─────────────────────────────────────────────
#  Public API: get_mover_symbols
# ─────────────────────────────────────────────
def test_get_mover_symbols_returns_deduped_ordered_by_side(monkeypatch):
    """Gainers come first, then losers, then actives. Duplicates
    across buckets are collapsed to the first occurrence."""
    payload = {
        "gainers": [{"symbol": "G1"}, {"symbol": "G2"}],
        "losers":  [{"symbol": "L1"}, {"symbol": "G1"}],   # dup
        "actives": [{"symbol": "A1"}, {"symbol": "G2"}],   # dup
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    async def _refresh(_db):
        return payload
    monkeypatch.setattr(alpha_live_movers, "refresh_if_stale", _refresh)

    result = _run(alpha_live_movers.get_mover_symbols(None))
    assert result == ["G1", "G2", "L1", "A1"]


def test_get_mover_symbols_respects_limit(monkeypatch):
    """The ``limit`` argument hard-caps the returned list."""
    payload = {
        "gainers": [{"symbol": f"G{i}"} for i in range(20)],
        "losers": [], "actives": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    async def _refresh(_db):
        return payload
    monkeypatch.setattr(alpha_live_movers, "refresh_if_stale", _refresh)

    result = _run(alpha_live_movers.get_mover_symbols(None, limit=5))
    assert len(result) == 5
    assert result == ["G0", "G1", "G2", "G3", "G4"]


def test_get_mover_symbols_returns_empty_when_no_payload(monkeypatch):
    """The universe pipeline must survive a totally-empty movers
    source (no cache, no AV, no keys)."""
    async def _refresh(_db):
        return None
    monkeypatch.setattr(alpha_live_movers, "refresh_if_stale", _refresh)
    assert _run(alpha_live_movers.get_mover_symbols(None)) == []


# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────
def test_ttl_seconds_env_override(monkeypatch):
    """Operator-tunable via ``ALPHA_LIVE_MOVERS_TTL_SECONDS``."""
    monkeypatch.setenv("ALPHA_LIVE_MOVERS_TTL_SECONDS", "45")
    assert alpha_live_movers._ttl_seconds() == 45


def test_ttl_seconds_clamps_to_sane_range(monkeypatch):
    """An operator entering ``0`` or ``99999`` should not disable
    the poll or send it into orbit — we clamp to 30..3600."""
    monkeypatch.setenv("ALPHA_LIVE_MOVERS_TTL_SECONDS", "5")
    assert alpha_live_movers._ttl_seconds() == 30
    monkeypatch.setenv("ALPHA_LIVE_MOVERS_TTL_SECONDS", "9999999")
    assert alpha_live_movers._ttl_seconds() == 3600


def test_ttl_seconds_falls_back_on_bad_value(monkeypatch):
    """Non-integer envvar → default (2 min). Never crash the
    universe pipeline on a bad config value."""
    monkeypatch.setenv("ALPHA_LIVE_MOVERS_TTL_SECONDS", "banana")
    assert alpha_live_movers._ttl_seconds() == 120
