"""Tests for the live Alpaca position closer (2026-02-23).

Pins:
  * Master kill-switch (default OFF) and dry-run (default ON when
    enabled) so the closer can't surprise-fire after a deploy.
  * Pure-function exit cascades — equity reuses tier3's
    ``_decide_equity_exit``; options layer adds pre-expiry hard close.
  * OCC symbol detection + expiry parsing handle SPY261218P00485000
    correctly.
  * Idempotency window prevents double-firing on adjacent ticks.
  * Sweep is failure-isolated — one bad position never poisons the
    rest of the batch.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── env switches ──────────────────────────────────────────────────


def test_default_disabled(monkeypatch):
    """Master switch defaults OFF — closer must NOT fire after a
    routine backend restart unless operator explicitly enables."""
    monkeypatch.delenv("ALPACA_POSITION_CLOSER_ENABLED", raising=False)
    from services.alpaca_position_closer import _is_enabled
    assert _is_enabled() is False


def test_dry_run_default_on_when_enabled(monkeypatch):
    """When the master switch flips on, dry-run defaults TRUE so the
    operator first audits the exit tape against live positions."""
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_ENABLED", "true")
    monkeypatch.delenv("ALPACA_POSITION_CLOSER_DRY_RUN", raising=False)
    from services.alpaca_position_closer import _is_enabled, _is_dry_run
    assert _is_enabled() is True
    assert _is_dry_run() is True


def test_dry_run_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_ENABLED", "true")
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_DRY_RUN", "false")
    from services.alpaca_position_closer import _is_dry_run
    assert _is_dry_run() is False


def test_disabled_sweep_is_noop():
    """When master switch is OFF, ``close_due_alpaca_positions`` must
    return a clean stats dict without touching Alpaca."""
    from services.alpaca_position_closer import close_due_alpaca_positions
    out = _run(close_due_alpaca_positions(None))
    assert out["enabled"] is False
    assert out["evaluated"] == 0
    assert out["closed"] == 0


# ── OCC symbol parsing ─────────────────────────────────────────────


def test_is_option_symbol():
    """Equity (≤5 alpha) vs OCC option (≥15 chars with digits + C/P)."""
    from services.alpaca_position_closer import _is_option_symbol
    assert _is_option_symbol("AMZN") is False
    assert _is_option_symbol("NVDA") is False
    assert _is_option_symbol("SPY261218C00510000") is True
    assert _is_option_symbol("SPY261218P00485000") is True
    assert _is_option_symbol("") is False
    # A 5-letter equity ticker (BRK.A, in normalised form "BRKA")
    # should NOT be confused with an option.
    assert _is_option_symbol("BRKA") is False


def test_parse_expiry_from_occ():
    """SPY261218P00485000 → 2026-12-18 expiry."""
    from services.alpaca_position_closer import _parse_expiry_from_occ
    exp = _parse_expiry_from_occ("SPY261218P00485000")
    assert exp is not None
    assert exp.year == 2026
    assert exp.month == 12
    assert exp.day == 18
    assert exp.tzinfo == timezone.utc

    # Equity ticker: unparseable.
    assert _parse_expiry_from_occ("NVDA") is None
    # Garbage: unparseable.
    assert _parse_expiry_from_occ("XXXX") is None
    # Bad date: unparseable but no crash.
    assert _parse_expiry_from_occ("SPY999999C00510000") is None


# ── option exit cascade ────────────────────────────────────────────


def test_option_pre_expiry_takes_priority():
    """4 days before expiry → hard close, regardless of PnL."""
    from services.alpaca_position_closer import _decide_option_exit
    now = datetime(2026, 12, 14, 15, 0, tzinfo=timezone.utc)
    expiry = datetime(2026, 12, 18, 21, 0, tzinfo=timezone.utc)
    cfg = {
        "sl_pct": 1.0, "tp_pct": 6.0, "tp_disabled": True,
        "trail_enabled": True, "trail_trigger_pct": 2.0,
        "trail_giveback_pct": 50.0, "hold_hours": 36,
    }
    reason = _decide_option_exit(
        entry_price=3.00, current_price=3.05,
        peak_price=None,
        opened_at=now - timedelta(hours=1),
        expiry=expiry, cfg=cfg, pre_expiry_days=5, now=now,
    )
    assert reason == "pre_expiry"


def test_option_pre_expiry_does_not_fire_with_plenty_of_time():
    """3 months before expiry → cascade falls through to standard
    SL/TP/trail/hold rules. No pre-expiry trigger."""
    from services.alpaca_position_closer import _decide_option_exit
    now = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    expiry = datetime(2026, 12, 18, 21, 0, tzinfo=timezone.utc)
    cfg = {
        "sl_pct": 1.0, "tp_pct": 6.0, "tp_disabled": True,
        "trail_enabled": True, "trail_trigger_pct": 2.0,
        "trail_giveback_pct": 50.0, "hold_hours": 36,
    }
    # Open recently, price flat → no exit reason fires.
    reason = _decide_option_exit(
        entry_price=3.00, current_price=3.00, peak_price=None,
        opened_at=now - timedelta(hours=1),
        expiry=expiry, cfg=cfg, pre_expiry_days=5, now=now,
    )
    assert reason is None


def test_option_no_expiry_falls_back_to_equity_cascade():
    """Defensive: if OCC parse fails (None expiry), the cascade
    still runs the equity rules — doesn't crash."""
    from services.alpaca_position_closer import _decide_option_exit
    now = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
    cfg = {
        "sl_pct": 1.0, "tp_pct": 6.0, "tp_disabled": True,
        "trail_enabled": True, "trail_trigger_pct": 2.0,
        "trail_giveback_pct": 50.0, "hold_hours": 36,
    }
    # Price down 2% → SL triggers.
    reason = _decide_option_exit(
        entry_price=3.00, current_price=2.94, peak_price=None,
        opened_at=now - timedelta(hours=1),
        expiry=None, cfg=cfg, pre_expiry_days=5, now=now,
    )
    assert reason == "stop_loss"


# ── idempotency ────────────────────────────────────────────────────


def test_idempotency_skips_recent_close_order():
    """If a SELL was submitted < 10min ago, the next tick MUST skip
    that symbol — prevents double-firing while Alpaca's position
    list catches up."""
    from services.alpaca_position_closer import _close_order_in_flight

    class _Coll:
        def __init__(self, has):
            self.has = has
        async def find_one(self, q, proj=None):
            return {"_id": "x"} if self.has else None

    class _DB:
        def __init__(self, has):
            self._c = _Coll(has)
        def __getitem__(self, name):
            return self._c

    assert _run(_close_order_in_flight(_DB(has=True), "NVDA", 600)) is True
    assert _run(_close_order_in_flight(_DB(has=False), "NVDA", 600)) is False


# ── sweep behavioural ──────────────────────────────────────────────


class _AsyncCM:
    """Tiny async-context-manager wrapper around an
    arbitrary inner object — used to stand in for
    ``httpx.AsyncClient`` so the sweep's ``async with`` body
    receives our mock."""
    def __init__(self, inner):
        self.inner = inner
    async def __aenter__(self):
        return self.inner
    async def __aexit__(self, *_):
        return False


def _build_mock_db():
    """Mongo-shaped mock supporting ``find_one`` / ``insert_one`` per
    collection. Records inserts so tests can assert on them.

    ``find_one`` is query-aware to discriminate the two distinct
    ``live_orders`` lookups in the sweep:
      * anchor resolution — matches when query has NO ``side`` filter
      * in-flight idempotency check — matches when query HAS a
        ``side`` filter (returns None by default; tests can pre-
        populate ``in_flight_live_orders`` to simulate a recent
        close).
    """
    inserts: dict[str, list] = {}
    found: dict[str, dict] = {}
    in_flight: dict[str, dict] = {}

    class _Coll:
        def __init__(self, name):
            self.name = name
            inserts.setdefault(name, [])
        async def find_one(self, q, proj=None, sort=None):
            # live_orders has two query shapes — anchor lookup vs
            # in-flight idempotency. Discriminate by the ``side``
            # filter that only the idempotency query sets.
            if self.name == "live_orders" and isinstance(q, dict) \
                    and "side" in q:
                return in_flight.get(self.name)
            return found.get(self.name)
        async def insert_one(self, doc):
            inserts[self.name].append(doc)
            class _R: inserted_id = "x"
            return _R()

    class _DB:
        def __init__(self):
            self._c: dict[str, _Coll] = {}
        def __getitem__(self, name):
            return self._c.setdefault(name, _Coll(name))

    return _DB(), inserts, found, in_flight


def test_sweep_dry_run_does_not_submit_real_order(monkeypatch):
    """In dry-run, the closer must log the decision but NEVER hit
    the real Alpaca orders endpoint."""
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_ENABLED", "true")
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_DRY_RUN", "true")

    db, inserts, found, _in_flight = _build_mock_db()
    # Anchor row resolves entry/opened_at — make it 48h old at 1% SL.
    found["live_orders"] = {
        "ticker": "NVDA", "entry_price": 100.0,
        "submitted_at": datetime.now(timezone.utc) - timedelta(hours=48),
        "peak_price": None,
    }

    from services import alpaca_position_closer as mod
    monkeypatch.setattr(mod, "_alpaca_headers",
                        lambda: {"a": "h"}, raising=False)
    # Mock httpx.AsyncClient
    fake_client = MagicMock()
    fake_client.get = AsyncMock()
    fake_client.post = AsyncMock()
    fake_client.get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[{
            "symbol": "NVDA", "qty": "10",
            "current_price": "95",  # -5%, well past SL
            "avg_entry_price": "100",
        }]),
    )
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **k: _AsyncCM(fake_client),
    )

    out = _run(mod.close_due_alpaca_positions(db))
    assert out["enabled"] is True
    assert out["dry_run"] is True
    assert out["evaluated"] == 1
    assert out["closed"] == 1
    assert out["reasons"]["stop_loss"] == 1
    # CRITICAL: in dry-run, NO real POST to /v2/orders.
    fake_client.post.assert_not_called()
    # Mirror rows still written so the operator can audit the
    # decision tape (live_orders + paper_trades both stamped with
    # ``dry_run=True``).
    assert any(d.get("dry_run") for d in inserts.get("live_orders", []))
    assert any(d.get("dry_run") for d in inserts.get("paper_trades", []))


def test_sweep_skips_position_without_anchor(monkeypatch):
    """If the open anchor row is missing in ``live_orders``, the
    sweep MUST skip (we don't know entry/opened_at → can't safely
    close)."""
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_ENABLED", "true")
    db, _inserts, _found, _in_flight = _build_mock_db()
    # No live_orders row resolved.

    from services import alpaca_position_closer as mod
    monkeypatch.setattr(mod, "_alpaca_headers",
                        lambda: {"a": "h"}, raising=False)
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=MagicMock(return_value=[{
            "symbol": "MYSTERY", "qty": "10",
            "current_price": "95", "avg_entry_price": "100",
        }]),
    ))
    fake_client.post = AsyncMock()
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **k: _AsyncCM(fake_client),
    )

    out = _run(mod.close_due_alpaca_positions(db))
    assert out["evaluated"] == 1
    assert out["skipped_no_anchor"] == 1
    assert out["closed"] == 0
    fake_client.post.assert_not_called()


def test_sweep_per_position_error_does_not_poison_batch(monkeypatch):
    """One position raising mid-sweep MUST NOT stop the others."""
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_ENABLED", "true")
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_DRY_RUN", "true")
    db, _inserts, found, _in_flight = _build_mock_db()
    found["live_orders"] = {
        "ticker": "NVDA", "entry_price": 100.0,
        "submitted_at": datetime.now(timezone.utc) - timedelta(hours=48),
        "peak_price": None,
    }

    from services import alpaca_position_closer as mod
    monkeypatch.setattr(mod, "_alpaca_headers",
                        lambda: {"a": "h"}, raising=False)
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            # First row: malformed (missing symbol) → error path
            {"qty": "10", "current_price": "100"},
            # Second row: valid SL trigger → must still close
            {"symbol": "NVDA", "qty": "10",
             "current_price": "95", "avg_entry_price": "100"},
        ]),
    ))
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **k: _AsyncCM(fake_client),
    )

    out = _run(mod.close_due_alpaca_positions(db))
    # First row hit the error/continue path. Second row succeeded.
    assert out["closed"] == 1
    assert out["reasons"]["stop_loss"] == 1


def test_sweep_skips_when_close_already_in_flight(monkeypatch):
    """If a SELL was just submitted on this symbol, the next sweep
    MUST skip it (idempotency) — even though SL still triggers."""
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_ENABLED", "true")
    monkeypatch.setenv("ALPACA_POSITION_CLOSER_DRY_RUN", "true")
    db, _inserts, found, in_flight = _build_mock_db()
    found["live_orders"] = {
        "ticker": "NVDA", "entry_price": 100.0,
        "submitted_at": datetime.now(timezone.utc) - timedelta(hours=48),
        "peak_price": None,
    }
    # Simulate a recent SELL on NVDA → idempotency check sees it.
    in_flight["live_orders"] = {
        "_id": "recent_sell",
    }

    from services import alpaca_position_closer as mod
    monkeypatch.setattr(mod, "_alpaca_headers",
                        lambda: {"a": "h"}, raising=False)
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=MagicMock(return_value=[{
            "symbol": "NVDA", "qty": "10",
            "current_price": "95", "avg_entry_price": "100",
        }]),
    ))
    fake_client.post = AsyncMock()
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **k: _AsyncCM(fake_client),
    )

    out = _run(mod.close_due_alpaca_positions(db))
    assert out["evaluated"] == 1
    assert out["skipped_idempotent"] == 1
    assert out["closed"] == 0
