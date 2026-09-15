"""Stage-2 adaptive-freshness integration tests (iteration 204).

Focus: behaviours NOT already covered by tests/test_adaptive_freshness.py:
  * fetch_execution_quote with ADAPTIVE_FRESHNESS_ENABLED=False must be
    byte-for-byte legacy (broker_age vs 5s) but also populate the new
    diagnostic fields (session / broker_route / broker_tick_age_seconds
    / freshness_limit_secs / adaptive_freshness) and record a sample.
  * alpha_day_trader._snapshot_symbol sets internal_snapshot_stale when
    bar_date < latest_verified_bar_date.
  * market_data_pool._record_daily_hwm only moves forward.
"""
from __future__ import annotations

import asyncio
import importlib
import time
from datetime import datetime, timedelta, timezone

import pytest

import services.broker_freshness_profile as bfp
import services.provider_policy as pp


def _reset_bfp():
    importlib.reload(bfp)


# ─── fetch_execution_quote: flag OFF = legacy, plus diagnostics ────
class TestFetchExecutionQuoteLegacyOff:
    def test_legacy_fresh_broker_allowed(self, monkeypatch):
        _reset_bfp()
        monkeypatch.setattr(pp, "ADAPTIVE_FRESHNESS_ENABLED", False)

        async def fake_broker(sym):
            return {
                "price": 100.0,
                "fetched_at": time.time() - 1.0,   # 1s old
                "provider_name": "public-primary",
                "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            }

        async def fake_vendor(sym):
            return None

        import services.market_data_pool as mdp
        monkeypatch.setattr(mdp, "fetch_broker_quote", fake_broker, raising=False)
        monkeypatch.setattr(mdp, "fetch_vendor_quote", fake_vendor, raising=False)

        r = asyncio.run(pp.fetch_execution_quote("AAPL"))
        assert r.execution_allowed is True
        assert r.reason == "broker_confirmed"
        assert r.freshness_limit_secs == float(pp.EXECUTION_FRESHNESS_SECS)
        assert r.adaptive_freshness is False
        assert r.session in ("CORE", "PREMARKET", "AFTER_HOURS", "OVERNIGHT", "CRYPTO")
        assert r.broker_route == "public-primary"
        assert r.broker_tick_age_seconds is not None
        # Sample recorded into the profile.
        p = bfp.profile("public", r.session)
        assert p["count"] >= 1

    def test_legacy_stale_broker_blocked(self, monkeypatch):
        _reset_bfp()
        monkeypatch.setattr(pp, "ADAPTIVE_FRESHNESS_ENABLED", False)

        async def fake_broker(sym):
            return {
                "price": 100.0,
                "fetched_at": time.time() - 30.0,  # 30s old > legacy 5s
                "provider_name": "public",
            }

        async def fake_vendor(sym):
            return None

        import services.market_data_pool as mdp
        monkeypatch.setattr(mdp, "fetch_broker_quote", fake_broker, raising=False)
        monkeypatch.setattr(mdp, "fetch_vendor_quote", fake_vendor, raising=False)

        r = asyncio.run(pp.fetch_execution_quote("AAPL"))
        assert r.execution_allowed is False
        assert r.reason and "broker_quote_stale" in r.reason
        assert r.adaptive_freshness is False

    def test_adaptive_on_uses_tick_age_not_fetched_at(self, monkeypatch):
        """Sanity: with flag ON, an ISO tick_age of 30s in CORE (no samples)
        must fail against the 2s floor even if fetched_at is fresh."""
        _reset_bfp()
        monkeypatch.setattr(pp, "ADAPTIVE_FRESHNESS_ENABLED", True)
        import services.alpha_session_state as ss
        monkeypatch.setattr(ss, "get_session_state", lambda now=None: {"phase": "regular"})

        old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()

        async def fake_broker(sym):
            return {
                "price": 100.0,
                "fetched_at": time.time(),   # fresh receive
                "timestamp": old,            # but real tick is 30s old
                "provider_name": "public",
            }

        async def fake_vendor(sym):
            return None

        import services.market_data_pool as mdp
        monkeypatch.setattr(mdp, "fetch_broker_quote", fake_broker, raising=False)
        monkeypatch.setattr(mdp, "fetch_vendor_quote", fake_vendor, raising=False)

        r = asyncio.run(pp.fetch_execution_quote("AAPL"))
        assert r.adaptive_freshness is True
        assert r.session == "CORE"
        assert r.execution_allowed is False
        assert r.freshness_limit_secs == float(pp.EXECUTION_FRESHNESS_MIN_SECS)


# ─── market_data_pool HWM ────────────────────────────────────────────
class TestDailyHwm:
    def test_hwm_only_moves_forward(self):
        import services.market_data_pool as mdp
        mdp._daily_hwm.pop("HWMTEST", None)
        mdp._record_daily_hwm("HWMTEST", {"date": "2026-01-10"})
        assert mdp.latest_verified_bar_date("HWMTEST") == "2026-01-10"
        # Older bar must NOT overwrite
        mdp._record_daily_hwm("HWMTEST", {"date": "2026-01-05"})
        assert mdp.latest_verified_bar_date("HWMTEST") == "2026-01-10"
        # Newer bumps
        mdp._record_daily_hwm("HWMTEST", {"date": "2026-01-15"})
        assert mdp.latest_verified_bar_date("HWMTEST") == "2026-01-15"

    def test_hwm_none_when_never_recorded(self):
        import services.market_data_pool as mdp
        mdp._daily_hwm.pop("NEVER_SEEN_SYM", None)
        assert mdp.latest_verified_bar_date("NEVER_SEEN_SYM") is None


# ─── internal_snapshot_stale in alpha_day_trader ────────────────────
class TestInternalSnapshotStale:
    def _bars(self, latest_date: str):
        base = [
            {"date": "2026-01-01", "open": 99, "high": 101, "low": 98, "close": 100, "volume": 1_000_000},
            {"date": "2026-01-02", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1_100_000},
            {"date": latest_date, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1_200_000},
        ]
        return base

    def test_flags_when_bar_older_than_hwm(self, monkeypatch):
        import services.alpha_day_trader as adt
        import services.market_data_pool as mdp

        bars = self._bars("2026-01-03")  # today bar
        invalidated = {"called": False}

        async def fake_market_daily(sym, outputsize="compact"):
            return bars

        async def fake_market_quote(sym):
            return {"price": 102.0, "bid": 101.9, "ask": 102.1, "volume": 1_200_000}

        async def fake_invalidate(sym):
            invalidated["called"] = True

        monkeypatch.setattr(mdp, "market_daily", fake_market_daily, raising=False)
        monkeypatch.setattr(mdp, "market_quote", fake_market_quote, raising=False)
        # HWM is NEWER than the returned bar_date -> stale
        monkeypatch.setattr(mdp, "latest_verified_bar_date",
                            lambda s: "2026-01-05", raising=False)
        monkeypatch.setattr(mdp, "invalidate_daily_cache", fake_invalidate, raising=False)

        snap = asyncio.run(adt._snapshot_symbol("STALE"))
        assert snap is not None
        assert snap.data_degraded is True
        assert "internal_snapshot_stale" in (snap.degraded_reason or "")
        assert invalidated["called"] is True

    def test_healthy_when_bar_equal_or_newer(self, monkeypatch):
        import services.alpha_day_trader as adt
        import services.market_data_pool as mdp

        bars = self._bars("2026-01-10")

        async def fake_market_daily(sym, outputsize="compact"):
            return bars

        async def fake_market_quote(sym):
            return {"price": 102.0, "bid": 101.9, "ask": 102.1, "volume": 1_200_000}

        async def fake_invalidate(sym):
            pytest.fail("invalidate_daily_cache should not be called when healthy")

        monkeypatch.setattr(mdp, "market_daily", fake_market_daily, raising=False)
        monkeypatch.setattr(mdp, "market_quote", fake_market_quote, raising=False)
        # HWM matches bar_date -> healthy
        monkeypatch.setattr(mdp, "latest_verified_bar_date",
                            lambda s: "2026-01-10", raising=False)
        monkeypatch.setattr(mdp, "invalidate_daily_cache", fake_invalidate, raising=False)

        snap = asyncio.run(adt._snapshot_symbol("HEALTHY"))
        assert snap is not None
        assert "internal_snapshot_stale" not in (snap.degraded_reason or "")
