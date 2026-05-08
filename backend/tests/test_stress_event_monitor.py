"""Tests for ``services.stress_event_monitor``.

Pins these invariants:

1. ``run_stress_check`` returns ``status="calm"`` when stressed
   count is below the threshold.
2. Returns ``status="cooldown"`` when a recent event still has
   ``cooldown_until > now``.
3. When triggered AND not in cooldown:
   * Writes a ``stress_events`` row.
   * Auto-flatten only fires when ``STRESS_AUTO_FLATTEN_ENABLED=1``.
   * Fired result includes ``cooldown_until`` for next-tick gate.
4. Equity post-close wide spreads are NOT counted as stress
   (session != rth → ``stressed=False`` for equity).
5. Crypto wide spreads ARE counted regardless of session.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from services import stress_event_monitor as sm


# ── Fake DB ───────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def _g():
            for r in self._rows:
                yield r
        return _g()


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def find_one(self, query, projection=None, sort=None):  # noqa: ARG002
        if not self.rows:
            return None
        rows = list(self.rows)
        if sort == [("fired_at", -1)]:
            rows.sort(key=lambda r: r.get("fired_at") or datetime.min, reverse=True)
        return rows[0]

    def find(self, query, projection=None):  # noqa: ARG002
        return _FakeCursor([
            r for r in self.rows
            if all(r.get(k) == v for k, v in query.items())
        ])

    async def update_one(self, *_args, **_kwargs):
        class R:
            modified_count = 1
        return R()


class _FakeDB:
    def __init__(self):
        self.stress_events = _FakeCollection()
        self.paper_trades = _FakeCollection()
        self.crypto_paper_trades = _FakeCollection()

    def __getitem__(self, name):
        return getattr(self, name)


# ── Helpers ──────────────────────────────────────────────────────


def _force_snapshot(monkeypatch, *, stressed_count: int = 0, session: str = "rth"):
    """Patch ``_gather_spread_snapshot`` to return a deterministic
    snapshot. Avoids hitting Kraken/Alpaca during tests."""
    rows = []
    for i in range(stressed_count):
        rows.append({
            "lane": "crypto" if i % 2 == 0 else "equity",
            "symbol": f"SYM{i}",
            "spread_bps": 200.0,
            "stressed": True,
        })
    rows.append({
        "lane": "crypto", "symbol": "BTC", "spread_bps": 0.5, "stressed": False,
    })
    snapshot = {
        "rows": rows,
        "stressed_count": stressed_count,
        "session": session,
        "threshold_bps": 25.0,
    }

    async def _patched():
        return snapshot

    monkeypatch.setattr(sm, "_gather_spread_snapshot", _patched)


# ── Calm path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calm_when_below_threshold(monkeypatch):
    _force_snapshot(monkeypatch, stressed_count=2)  # below default 3
    db = _FakeDB()
    out = await sm.run_stress_check(db)
    assert out["status"] == "calm"
    assert out["fired"] is False
    assert db.stress_events.rows == []


@pytest.mark.asyncio
async def test_snapshot_failed_returns_status(monkeypatch):
    async def _patched():
        return None
    monkeypatch.setattr(sm, "_gather_spread_snapshot", _patched)
    db = _FakeDB()
    out = await sm.run_stress_check(db)
    assert out["status"] == "snapshot_failed"
    assert out["fired"] is False


# ── Fire + cooldown ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fires_and_writes_row_on_threshold(monkeypatch):
    monkeypatch.setenv("STRESS_AUTO_FLATTEN_ENABLED", "0")
    _force_snapshot(monkeypatch, stressed_count=4)
    db = _FakeDB()
    out = await sm.run_stress_check(db)
    assert out["fired"] is True
    assert out["status"] == "fired"
    assert out["stressed_count"] == 4
    assert out["auto_flatten_enabled"] is False
    # Row written with isoformat datetimes.
    assert len(db.stress_events.rows) == 1


@pytest.mark.asyncio
async def test_cooldown_blocks_consecutive_fires(monkeypatch):
    monkeypatch.setenv("STRESS_AUTO_FLATTEN_ENABLED", "0")
    _force_snapshot(monkeypatch, stressed_count=4)
    db = _FakeDB()
    first = await sm.run_stress_check(db)
    assert first["fired"] is True

    second = await sm.run_stress_check(db)
    assert second["fired"] is False
    assert second["status"] == "cooldown"


@pytest.mark.asyncio
async def test_cooldown_expires_allows_refire(monkeypatch):
    monkeypatch.setenv("STRESS_AUTO_FLATTEN_ENABLED", "0")
    _force_snapshot(monkeypatch, stressed_count=4)
    db = _FakeDB()
    first = await sm.run_stress_check(db)
    assert first["fired"] is True
    # Force the cooldown to be in the past.
    db.stress_events.rows[0]["cooldown_until"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    second = await sm.run_stress_check(db)
    assert second["fired"] is True


# ── Auto-flatten gate ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_flatten_off_writes_event_only(monkeypatch):
    monkeypatch.setenv("STRESS_AUTO_FLATTEN_ENABLED", "0")
    _force_snapshot(monkeypatch, stressed_count=5)
    db = _FakeDB()
    db.paper_trades.rows.append({
        "trade_id": "t1", "status": "open",
        "entry_price": 100, "shares": 1,
    })
    out = await sm.run_stress_check(db)
    assert out["fired"] is True
    assert out["flatten_result"] is None


@pytest.mark.asyncio
async def test_auto_flatten_on_closes_open_trades(monkeypatch):
    monkeypatch.setenv("STRESS_AUTO_FLATTEN_ENABLED", "1")
    _force_snapshot(monkeypatch, stressed_count=5)
    db = _FakeDB()
    db.paper_trades.rows.append({
        "trade_id": "t1", "status": "open",
        "entry_price": 100, "shares": 1, "direction": "LONG",
    })
    db.crypto_paper_trades.rows.append({
        "trade_id": "c1", "status": "open",
        "entry_price": 80000, "position_size_usd": 100, "direction": "LONG",
    })
    out = await sm.run_stress_check(db)
    assert out["fired"] is True
    assert out["flatten_result"] is not None
    # FakeCollection.update_one always reports modified_count=1, so
    # both lanes count one closure.
    assert out["flatten_result"]["equity_closed"] >= 1
    assert out["flatten_result"]["crypto_closed"] >= 1
