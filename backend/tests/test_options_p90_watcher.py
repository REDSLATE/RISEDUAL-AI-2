"""Regression tests for ``services.options_p90_watcher``.

Detector logic is pure and testable without mocking external services.
Coverage:
    * detect_p90_spike — positive path, no-op paths, malformed data
    * record_p90_history — writes one row per symbol with complete aggs
    * scan_for_p90_spikes — ties detector + dedupe + persistence
    * get_recent_alerts — newest-first ordering
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_top_universe_service import _FakeDB  # noqa: E402


# ═══════════════ detect_p90_spike (pure) ═══════════════


def test_detect_p90_spike_fires_on_widening_tails_stable_avg():
    """p90 +60% over 15 min with avg drifting just +5% → alert."""
    from services.options_p90_watcher import detect_p90_spike
    # newest-first
    history = [
        {"p90_spread_bps": 160.0, "avg_spread_bps": 52.0},   # now
        {"p90_spread_bps": 120.0, "avg_spread_bps": 51.0},
        {"p90_spread_bps": 100.0, "avg_spread_bps": 50.0},   # baseline
    ]
    detail = detect_p90_spike(history)
    assert detail is not None
    # p90: (160-100)/100 = 60%, avg: (52-50)/50 = 4%
    assert detail["p90_change_pct"] == 60.0
    assert detail["avg_change_pct"] == 4.0
    assert detail["p90_now"] == 160.0
    assert detail["p90_baseline"] == 100.0


def test_detect_p90_spike_suppressed_when_avg_also_climbs():
    """p90 AND avg both climb together → generic liquidity event, not
    the tail-stress precursor we're after. No alert."""
    from services.options_p90_watcher import detect_p90_spike
    history = [
        {"p90_spread_bps": 160.0, "avg_spread_bps": 80.0},
        {"p90_spread_bps": 120.0, "avg_spread_bps": 65.0},
        {"p90_spread_bps": 100.0, "avg_spread_bps": 50.0},   # avg +60%
    ]
    assert detect_p90_spike(history) is None


def test_detect_p90_spike_suppressed_when_p90_below_threshold():
    """p90 only +30% → below threshold, no alert."""
    from services.options_p90_watcher import detect_p90_spike
    history = [
        {"p90_spread_bps": 130.0, "avg_spread_bps": 51.0},
        {"p90_spread_bps": 115.0, "avg_spread_bps": 50.5},
        {"p90_spread_bps": 100.0, "avg_spread_bps": 50.0},
    ]
    assert detect_p90_spike(history) is None


def test_detect_p90_spike_insufficient_history_is_noop():
    """< MIN_HISTORY_POINTS → None, not alert."""
    from services.options_p90_watcher import detect_p90_spike
    assert detect_p90_spike([]) is None
    assert detect_p90_spike([{"p90_spread_bps": 100, "avg_spread_bps": 50}]) is None
    assert detect_p90_spike([
        {"p90_spread_bps": 200, "avg_spread_bps": 50},
        {"p90_spread_bps": 100, "avg_spread_bps": 50},
    ]) is None


def test_detect_p90_spike_zero_baseline_is_noop():
    """Divide-by-zero guard — p90_base=0 must return None cleanly."""
    from services.options_p90_watcher import detect_p90_spike
    history = [
        {"p90_spread_bps": 100.0, "avg_spread_bps": 50.0},
        {"p90_spread_bps": 50.0, "avg_spread_bps": 25.0},
        {"p90_spread_bps": 0.0,  "avg_spread_bps": 0.0},
    ]
    assert detect_p90_spike(history) is None


def test_detect_p90_spike_malformed_row_is_noop():
    """Missing keys / non-numeric values must not crash."""
    from services.options_p90_watcher import detect_p90_spike
    history = [
        {"p90_spread_bps": "bad"},
        {"p90_spread_bps": 100.0, "avg_spread_bps": 50.0},
        {"p90_spread_bps": 100.0, "avg_spread_bps": 50.0},
    ]
    assert detect_p90_spike(history) is None


# ═══════════════ record_p90_history ═══════════════


class _FakeDBWithInsertMany(_FakeDB):
    pass


# Insert_many is needed; add it to the base _FakeCollection via monkeypatch
# in tests that need it.
async def _insert_many(self, docs):  # pragma: no cover - used via monkeypatch
    for d in docs:
        self.docs.append(dict(d))


@pytest.mark.asyncio
async def test_record_p90_history_writes_one_row_per_symbol(monkeypatch):
    from services.options_p90_watcher import (
        record_p90_history, P90_HISTORY_COLLECTION,
    )
    from tests.test_top_universe_service import _FakeCollection
    monkeypatch.setattr(_FakeCollection, "insert_many", _insert_many,
                        raising=False)

    db = _FakeDB()
    now = datetime.now(timezone.utc)
    universe = [
        {"symbol": "SPY", "has_hot_flow": True,
         "aggregate": {"avg_spread_bps": 30.0, "p90_spread_bps": 55.0,
                       "flow_imbalance": 0.1, "total_volume": 100_000}},
        {"symbol": "QQQ", "has_hot_flow": False,
         "aggregate": {"avg_spread_bps": 80.0, "p90_spread_bps": 150.0,
                       "flow_imbalance": -0.3, "total_volume": 50_000}},
        # Missing aggregates — must be skipped cleanly, not crash
        {"symbol": "IWM", "has_hot_flow": False, "aggregate": {}},
    ]
    count = await record_p90_history(db, now, universe)
    assert count == 2
    rows = db[P90_HISTORY_COLLECTION].docs
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"SPY", "QQQ"}
    # Each row carries the core fields + the ts stamp
    for r in rows:
        assert "ts" in r
        assert "avg_spread_bps" in r
        assert "p90_spread_bps" in r


@pytest.mark.asyncio
async def test_record_p90_history_empty_universe_noop():
    from services.options_p90_watcher import record_p90_history
    assert await record_p90_history(_FakeDB(), datetime.now(timezone.utc), []) == 0


# ═══════════════ scan_for_p90_spikes ═══════════════


def _seed_history(db, symbol: str, sequence: list[tuple[float, float]], now: datetime):
    """Seed per-symbol rows: sequence is oldest→newest of (p90, avg) pairs.
    Each tick placed 5 min apart, ending at ``now``."""
    from services.options_p90_watcher import P90_HISTORY_COLLECTION
    for idx, (p90, avg) in enumerate(reversed(sequence)):
        # idx=0 → newest (now); idx=len-1 → oldest
        ts = now - timedelta(minutes=5 * idx)
        db[P90_HISTORY_COLLECTION].docs.append({
            "symbol": symbol,
            "ts": ts.isoformat(),
            "p90_spread_bps": p90,
            "avg_spread_bps": avg,
        })


@pytest.mark.asyncio
async def test_scan_for_p90_spikes_fires_and_persists(monkeypatch):
    """A symbol with the widening-tails pattern produces exactly one
    persisted alert row."""
    from services.options_p90_watcher import (
        scan_for_p90_spikes, P90_ALERTS_COLLECTION,
    )
    # Never call Slack in tests
    async def _no_slack(*a, **k):
        return None
    monkeypatch.setattr(
        "services.options_p90_watcher._notify_slack_p90_spike", _no_slack,
    )

    db = _FakeDB()
    now = datetime.now(timezone.utc)
    # oldest → newest: p90 doubles while avg drifts +4%
    _seed_history(db, "SPY",
                  [(100.0, 50.0), (130.0, 51.0), (200.0, 52.0)],
                  now)

    universe = [{"symbol": "SPY", "aggregate": {}}]
    alerts = await scan_for_p90_spikes(db, now, universe)
    assert alerts == 1

    alert_rows = db[P90_ALERTS_COLLECTION].docs
    assert len(alert_rows) == 1
    assert alert_rows[0]["symbol"] == "SPY"
    assert alert_rows[0]["alert_type"] == "p90_spread_widening"
    assert alert_rows[0]["detail"]["p90_change_pct"] >= 50.0


@pytest.mark.asyncio
async def test_scan_for_p90_spikes_dedupes_within_window(monkeypatch):
    """Second scan within DEDUPE_MINUTES must not produce a duplicate row."""
    from services.options_p90_watcher import (
        scan_for_p90_spikes, P90_ALERTS_COLLECTION, DEDUPE_MINUTES,
    )
    async def _no_slack(*a, **k):
        return None
    monkeypatch.setattr(
        "services.options_p90_watcher._notify_slack_p90_spike", _no_slack,
    )

    db = _FakeDB()
    now = datetime.now(timezone.utc)
    _seed_history(db, "SPY",
                  [(100.0, 50.0), (130.0, 51.0), (200.0, 52.0)],
                  now)

    universe = [{"symbol": "SPY", "aggregate": {}}]
    first = await scan_for_p90_spikes(db, now, universe)
    second = await scan_for_p90_spikes(
        db, now + timedelta(minutes=DEDUPE_MINUTES - 5), universe,
    )
    assert first == 1
    assert second == 0
    assert len(db[P90_ALERTS_COLLECTION].docs) == 1


@pytest.mark.asyncio
async def test_scan_for_p90_spikes_fires_again_after_dedupe_window(monkeypatch):
    from services.options_p90_watcher import (
        scan_for_p90_spikes, P90_ALERTS_COLLECTION, DEDUPE_MINUTES,
    )
    async def _no_slack(*a, **k):
        return None
    monkeypatch.setattr(
        "services.options_p90_watcher._notify_slack_p90_spike", _no_slack,
    )

    db = _FakeDB()
    now = datetime.now(timezone.utc)
    _seed_history(db, "SPY",
                  [(100.0, 50.0), (130.0, 51.0), (200.0, 52.0)],
                  now)
    universe = [{"symbol": "SPY", "aggregate": {}}]

    await scan_for_p90_spikes(db, now, universe)
    # Re-seed a fresh widening pattern past the dedupe window
    later = now + timedelta(minutes=DEDUPE_MINUTES + 5)
    _seed_history(db, "SPY",
                  [(200.0, 52.0), (250.0, 53.0), (400.0, 54.0)],
                  later)
    await scan_for_p90_spikes(db, later, universe)
    assert len(db[P90_ALERTS_COLLECTION].docs) == 2


@pytest.mark.asyncio
async def test_scan_for_p90_spikes_silent_for_stable_universe(monkeypatch):
    """No widening pattern anywhere → no alerts, no Slack calls."""
    from services.options_p90_watcher import (
        scan_for_p90_spikes, P90_ALERTS_COLLECTION,
    )
    slack_calls = {"n": 0}
    async def _count_slack(*a, **k):
        slack_calls["n"] += 1
    monkeypatch.setattr(
        "services.options_p90_watcher._notify_slack_p90_spike", _count_slack,
    )

    db = _FakeDB()
    now = datetime.now(timezone.utc)
    _seed_history(db, "SPY",
                  [(100.0, 50.0), (102.0, 50.5), (101.0, 50.2)],
                  now)
    universe = [{"symbol": "SPY", "aggregate": {}}]
    alerts = await scan_for_p90_spikes(db, now, universe)
    assert alerts == 0
    assert slack_calls["n"] == 0
    assert db[P90_ALERTS_COLLECTION].docs == []


@pytest.mark.asyncio
async def test_get_recent_alerts_newest_first_with_limit():
    from services.options_p90_watcher import (
        get_recent_alerts, P90_ALERTS_COLLECTION,
    )
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    for i in range(5):
        db[P90_ALERTS_COLLECTION].docs.append({
            "symbol": f"SYM{i}",
            "fired_at": (now - timedelta(minutes=i)).isoformat(),
            "alert_type": "p90_spread_widening",
            "detail": {"p90_change_pct": 60 + i},
        })
    rows = await get_recent_alerts(db, limit=3)
    assert len(rows) == 3
    # Newest first
    assert rows[0]["symbol"] == "SYM0"
    assert rows[-1]["symbol"] == "SYM2"
