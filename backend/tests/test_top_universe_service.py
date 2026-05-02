"""Regression tests for `services.top_universe_service`.

Covers:
    * Seed loading (success, malformed, missing)
    * Tier assignment boundaries
    * Market-cap parsing of AV's string-typed OVERVIEW fields
    * Technical indicator calculation (RSI/MACD/SMA)
    * Rebuild — mocks AV overview, asserts rank + tier + stats
    * Warm — mocks price_provider, asserts tier-specific fetch contract
    * Status endpoint shape
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ───────────────── helpers ─────────────────


class _FakeCollection:
    """Minimal Motor-compatible collection mock for the helper functions."""

    def __init__(self):
        self.docs: list[dict] = []
        self.updates: list[tuple[dict, dict, bool]] = []

    def find(self, query=None, projection=None):
        matched = [d for d in self.docs if _matches(d, query or {})]
        return _FakeCursor(matched)

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [d for d in self.docs if _matches(d, query or {})]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda r: r.get(key) or "", reverse=(direction == -1))
        return matched[0] if matched else None

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _matches(d, query))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                d.update(update.get("$set", {}))
                self.updates.append((query, update, upsert))
                return
        if upsert:
            new = dict(update.get("$set", {}))
            # Upsert key comes from the query; prefer "symbol" if present.
            for k, v in query.items():
                if k not in new and not isinstance(v, dict):
                    new[k] = v
            self.docs.append(new)
        self.updates.append((query, update, upsert))

    async def update_many(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                d.update(update.get("$set", {}))

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class _FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, key, direction=1):
        self.rows.sort(key=lambda r: r.get(key) or 0, reverse=(direction == -1))
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length):
        return self.rows[:length]


def _matches(doc: dict, query: dict) -> bool:
    for k, v in query.items():
        dv = doc.get(k)
        if isinstance(v, dict):
            if "$in" in v and dv not in v["$in"]:
                return False
            if "$nin" in v and dv in v["$nin"]:
                return False
            if "$ne" in v and dv == v["$ne"]:
                return False
        elif dv != v:
            return False
    return True


class _FakeDB:
    def __init__(self):
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]

    # Mirror attribute-style access used by `db.top_universe` in prod code
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


# ───────────────── seed + ranking unit tests ─────────────────


def test_load_seed_tickers_normalizes():
    from services.top_universe_service import load_seed_tickers
    tickers = load_seed_tickers()
    # Real file ships with the curated S&P 500-adjacent list.
    assert len(tickers) >= 100
    # All upper-cased, no duplicates
    assert all(t == t.upper() for t in tickers)
    assert len(tickers) == len(set(tickers))


def test_assign_tier_boundaries():
    from services.top_universe_service import (
        TIER_A_SIZE, TIER_B_SIZE, _assign_tier,
    )
    assert _assign_tier(1) == "A"
    assert _assign_tier(TIER_A_SIZE) == "A"
    assert _assign_tier(TIER_A_SIZE + 1) == "B"
    assert _assign_tier(TIER_A_SIZE + TIER_B_SIZE) == "B"
    assert _assign_tier(TIER_A_SIZE + TIER_B_SIZE + 1) == "C"


def test_parse_market_cap_handles_string_and_missing():
    from services.top_universe_service import _parse_market_cap
    assert _parse_market_cap({"MarketCapitalization": "3050000000000"}) == 3.05e12
    assert _parse_market_cap({"MarketCapitalization": "None"}) == 0.0
    assert _parse_market_cap({}) == 0.0
    assert _parse_market_cap({"MarketCapitalization": "abc"}) == 0.0


# ───────────────── technicals ─────────────────


def _fake_bars(n: int = 210, start_price: float = 100.0) -> list[dict]:
    """Generate newest-first daily bars with a gentle uptrend. `n` bars."""
    rows = []
    for i in range(n):
        price = start_price + i * 0.5  # monotonic up — RSI ≈ 100, MACD positive
        rows.append({
            "date": f"2025-01-{(i % 28) + 1:02d}",
            "open": price, "high": price + 1, "low": price - 1,
            "close": price, "volume": 1_000_000,
        })
    return list(reversed(rows))  # price_provider returns newest first


def test_compute_technicals_uptrend():
    from services.universe_technicals import compute_technicals
    tech = compute_technicals(_fake_bars(250))
    assert tech["bar_count"] == 250
    assert tech["rsi14"] is not None and tech["rsi14"] > 90   # near-pure uptrend
    assert tech["macd"] is not None and tech["macd"] > 0
    assert tech["sma20"] is not None
    assert tech["sma50"] is not None
    assert tech["sma200"] is not None
    assert tech["sma20"] > tech["sma50"] > tech["sma200"]    # uptrend alignment


def test_compute_technicals_too_short_returns_empty():
    from services.universe_technicals import compute_technicals
    assert compute_technicals(_fake_bars(5)) == {}
    assert compute_technicals([]) == {}


def test_compute_technicals_partial_history_returns_nones_not_raises():
    """25 bars → RSI/MACD/SMA20 OK, SMA50/SMA200 must be None not crash."""
    from services.universe_technicals import compute_technicals
    tech = compute_technicals(_fake_bars(25))
    assert tech["rsi14"] is not None
    assert tech["sma20"] is not None
    assert tech["sma50"] is None
    assert tech["sma200"] is None


# ───────────────── rebuild ─────────────────


@pytest.mark.asyncio
async def test_rebuild_universe_ranks_by_market_cap_and_assigns_tiers(monkeypatch):
    """Mock OVERVIEW responses for a tiny seed → assert rank order + tier
    assignment + persistence behaviour."""
    from services.top_universe_service import (
        TIER_A_SIZE, rebuild_universe, UNIVERSE_COLLECTION,
        WARM_STATS_COLLECTION,
    )

    # Seed with exactly 5 tickers: 3 big, 1 small, 1 that fails OVERVIEW.
    monkeypatch.setattr(
        "services.top_universe_service.load_seed_tickers",
        lambda: ["BIG1", "BIG2", "BIG3", "SMALL", "BROKEN"],
    )

    overviews = {
        "BIG1":   {"Symbol": "BIG1", "MarketCapitalization": "3000000000000",
                    "Sector": "Tech", "Name": "Big One"},
        "BIG2":   {"Symbol": "BIG2", "MarketCapitalization": "2000000000000",
                    "Sector": "Tech", "Name": "Big Two"},
        "BIG3":   {"Symbol": "BIG3", "MarketCapitalization": "1000000000000",
                    "Sector": "Energy", "Name": "Big Three"},
        "SMALL":  {"Symbol": "SMALL", "MarketCapitalization": "5000000000",
                    "Sector": "Tech", "Name": "Small"},
        "BROKEN": None,  # simulates AV miss
    }

    def _fake_overview_sync(symbol):
        return overviews.get(symbol.upper())

    monkeypatch.setattr(
        "services.price_provider.get_overview_sync",
        _fake_overview_sync,
    )

    db = _FakeDB()
    stats = await rebuild_universe(db)

    assert stats["status"] == "success"
    assert stats["symbols_attempted"] == 5
    assert stats["symbols_succeeded"] == 4
    assert stats["failures"] == 1
    assert "wall_seconds" in stats

    # All 4 ranked rows are persisted — in market-cap order
    rows = sorted(db[UNIVERSE_COLLECTION].docs, key=lambda r: r["rank"])
    assert [r["symbol"] for r in rows] == ["BIG1", "BIG2", "BIG3", "SMALL"]
    assert rows[0]["tier"] == "A"  # rank 1 → Tier A regardless of absolute count
    # Rank 2 is also Tier A when TIER_A_SIZE=100
    assert rows[1]["tier"] == "A"
    assert all(r["active"] is True for r in rows)
    assert all(r["asset_type"] == "equity" for r in rows)
    assert all("last_ranked_at" in r for r in rows)
    assert rows[0]["market_cap"] == 3.0e12

    # Warm-stats row was written
    assert len(db[WARM_STATS_COLLECTION].docs) == 1
    assert db[WARM_STATS_COLLECTION].docs[0]["run_type"] == "rebuild"


@pytest.mark.asyncio
async def test_rebuild_universe_with_empty_seed(monkeypatch):
    """Malformed/missing seed → error stats, no crash."""
    from services.top_universe_service import rebuild_universe

    monkeypatch.setattr(
        "services.top_universe_service.load_seed_tickers",
        lambda: [],
    )
    db = _FakeDB()
    stats = await rebuild_universe(db)
    assert stats["status"] == "error"
    assert stats["error"] == "empty_seed"
    assert stats["symbols_attempted"] == 0


# ───────────────── warm ─────────────────


@pytest.mark.asyncio
async def test_warm_post_close_respects_tier_contract(monkeypatch):
    """Tier A gets daily+overview+quote+tech; Tier B gets daily+tech only."""
    from services.top_universe_service import warm_universe, UNIVERSE_COLLECTION

    db = _FakeDB()
    db[UNIVERSE_COLLECTION].docs.extend([
        {"symbol": "AAA", "tier": "A", "rank": 1, "active": True},
        {"symbol": "BBB", "tier": "B", "rank": 101, "active": True},
    ])

    calls = {"daily": [], "quote": [], "overview": []}

    async def fake_daily(symbol, outputsize="compact"):
        calls["daily"].append(symbol)
        return _fake_bars(50)

    async def fake_quote(symbol):
        calls["quote"].append(symbol)
        return {"price": 100.0, "source": "test"}

    def fake_overview(symbol):
        calls["overview"].append(symbol)
        return {"Symbol": symbol, "MarketCapitalization": "1000000000"}

    monkeypatch.setattr("services.price_provider.get_daily_history", fake_daily)
    monkeypatch.setattr("services.price_provider.get_quote", fake_quote)
    monkeypatch.setattr("services.price_provider.get_overview_sync", fake_overview)

    stats = await warm_universe(db, run_type="post_close")

    assert stats["status"] == "success"
    assert stats["symbols_attempted"] == 2
    assert stats["symbols_succeeded"] == 2
    # Tier A gets everything; Tier B is daily + technicals only
    assert sorted(calls["daily"]) == ["AAA", "BBB"]
    assert calls["quote"] == ["AAA"]
    assert calls["overview"] == ["AAA"]


@pytest.mark.asyncio
async def test_warm_pre_open_only_tier_a(monkeypatch):
    """Pre-open run: Tier A quote+technicals only, Tier B skipped entirely."""
    from services.top_universe_service import warm_universe, UNIVERSE_COLLECTION

    db = _FakeDB()
    db[UNIVERSE_COLLECTION].docs.extend([
        {"symbol": "AAA", "tier": "A", "rank": 1, "active": True},
        {"symbol": "BBB", "tier": "B", "rank": 101, "active": True},
    ])

    calls = {"daily": [], "quote": [], "overview": []}

    async def fake_daily(symbol, outputsize="compact"):
        calls["daily"].append(symbol)
        return _fake_bars(50)

    async def fake_quote(symbol):
        calls["quote"].append(symbol)
        return {"price": 100.0}

    def fake_overview(symbol):
        calls["overview"].append(symbol)
        return None  # shouldn't be called anyway

    monkeypatch.setattr("services.price_provider.get_daily_history", fake_daily)
    monkeypatch.setattr("services.price_provider.get_quote", fake_quote)
    monkeypatch.setattr("services.price_provider.get_overview_sync", fake_overview)

    stats = await warm_universe(db, run_type="pre_open")

    # Tier A only; quote + technicals (technicals require daily bars → still fetched)
    assert stats["symbols_attempted"] == 1
    assert calls["quote"] == ["AAA"]
    assert calls["overview"] == []  # overview is not refreshed pre-open


@pytest.mark.asyncio
async def test_warm_with_empty_universe_skips_cleanly():
    """No active rows → skipped status, no crash, stats row still written."""
    from services.top_universe_service import warm_universe, WARM_STATS_COLLECTION

    db = _FakeDB()
    stats = await warm_universe(db, run_type="post_close")

    assert stats["status"] == "skipped"
    assert "no_active_universe" in stats["reason"]
    assert len(db[WARM_STATS_COLLECTION].docs) == 1


# ───────────────── status ─────────────────


@pytest.mark.asyncio
async def test_get_status_shape():
    from services.top_universe_service import (
        UNIVERSE_COLLECTION, WARM_STATS_COLLECTION, get_status,
    )

    db = _FakeDB()
    # 3 active rows (A, A, B)
    db[UNIVERSE_COLLECTION].docs.extend([
        {"symbol": "AAA", "tier": "A", "rank": 1, "active": True,
         "market_cap": 3e12, "sector": "Tech"},
        {"symbol": "BBB", "tier": "A", "rank": 2, "active": True,
         "market_cap": 2e12, "sector": "Tech"},
        {"symbol": "CCC", "tier": "B", "rank": 101, "active": True,
         "market_cap": 1e10, "sector": "Energy"},
        {"symbol": "DDD", "tier": "B", "rank": 150, "active": False},  # demoted
    ])
    db[WARM_STATS_COLLECTION].docs.extend([
        {"run_type": "rebuild", "started_at": "2026-02-01T00:00:00+00:00",
         "status": "success", "symbols_succeeded": 300},
        {"run_type": "post_close", "started_at": "2026-02-02T21:05:00+00:00",
         "status": "success", "symbols_attempted": 200, "symbols_succeeded": 198},
        {"run_type": "pre_open", "started_at": "2026-02-02T13:00:00+00:00",
         "status": "success", "symbols_attempted": 100, "symbols_succeeded": 100},
    ])

    out = await get_status(db)

    assert out["active_total"] == 3
    assert out["by_tier"] == {"A": 2, "B": 1, "C": 0}
    assert out["tier_sizes"] == {"A": 100, "B": 100, "C": 100}
    assert out["last_rebuild"]["run_type"] == "rebuild"
    assert out["last_warm"]["run_type"] == "post_close"  # newest warm
    assert out["recent_coverage"] is not None
    assert 0.0 <= out["recent_coverage"] <= 1.0
    assert len(out["sample"]) == 3  # all 3 active rows ≤ limit
