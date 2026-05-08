"""Tests for adaptive-sizing wiring inside `process_signal_for_bots`.

Exercises the three key outcomes with the feature flag on:
  1. Low-confidence signal → trade SKIPPED before execution.
  2. High-confidence signal → qty scaled down by readiness + confidence
     multiplier, `sizing_meta` stamped on the result.
  3. Readiness snapshot failure → falls back to configured qty (no crash).

Also asserts that with the flag OFF, the legacy path fires unchanged.
"""
from __future__ import annotations

from typing import Any

import pytest

from services import trading_bot_service as tbs


# ────────────────────────────────────────────────────────────────────────────────
# Tiny fakes: mimic just enough of Motor + supporting services
# ────────────────────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = list(rows)

    async def to_list(self, length: int | None = None):
        return self._rows if length is None else self._rows[:length]


class _FakeBotsCollection:
    def __init__(self, bots: list[dict]):
        self._bots = bots
        self.updates: list[tuple[dict, dict]] = []
        self.inserts: list[dict] = []

    def find(self, query: dict) -> _FakeCursor:  # noqa: ARG002
        return _FakeCursor(self._bots)

    async def update_one(self, query: dict, update: dict) -> None:
        self.updates.append((query, update))

    async def insert_one(self, doc: dict) -> Any:
        self.inserts.append(doc)
        class _Res:
            inserted_id = "fake"
        return _Res()

    async def find_one(self, query: dict, projection: dict | None = None):  # noqa: ARG002
        return None


class _FakeDB:
    def __init__(self, bots: list[dict]):
        self.trading_bots = _FakeBotsCollection(bots)
        # ai_core.learning_engine writes to a `trade_outcomes`
        # collection — provide a permissive stub for that too.
        self._misc = _FakeBotsCollection([])

    def __getitem__(self, name: str):
        if name == "trading_bots":
            return self.trading_bots
        return self._misc


def _bot(name: str, qty: int = 10, min_conf: int = 60) -> dict:
    return {
        "_id": f"bot-{name}",
        "user_id": "u1",
        "name": name,
        "type": "signal",
        "enabled": True,
        "mode": "paper",
        "config": {
            "qty": qty,
            "min_confidence": min_conf,
            "use_smart_order": False,
            "auto_sl_pct": 3,
            "auto_tp_pct": 6,
        },
    }


def _signal(confidence: int = 85) -> dict:
    return {
        "symbol": "AAPL",
        "price": 180.0,
        "ai_confidence": confidence,
        "ai_verdict": "buy",
        "strategy_id": "rsi_divergence",
    }


# ────────────────────────────────────────────────────────────────────────────────
# Fixtures — patch the readiness snapshot + trade executor per test
# ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def healthy_readiness(monkeypatch):
    """All safety throttles pass; score at 100 → readiness_mult = 1.0."""
    async def _fake_snap(db, days=30):  # noqa: ARG001
        return {
            "stats": {
                "days": 30, "total_trades": 150, "high_conf_trades": 60,
                "strong_miss_rate": 0.05, "clamp_total": 0,
                "high_conf_win_rate": 0.8, "last_7d_win_rate": 0.8,
                "overall_win_rate": 0.75, "avg_confidence": 70.0,
            },
            "unlock": {"confidence_score": 100.0, "unlocked": True, "reasons": []},
        }
    import services.tier3_readiness as tr
    monkeypatch.setattr(tr, "tier3_readiness_snapshot", _fake_snap)


@pytest.fixture
def typical_readiness(monkeypatch):
    """Matches preview DB — 81.33 score, high_conf throttle fires (×0.75)."""
    async def _fake_snap(db, days=30):  # noqa: ARG001
        return {
            "stats": {
                "days": 7, "total_trades": 82, "high_conf_trades": 5,
                "strong_miss_rate": 0.032, "clamp_total": 0,
                "high_conf_win_rate": 1.0, "last_7d_win_rate": 1.0,
                "overall_win_rate": 0.816, "avg_confidence": 70.8,
            },
            "unlock": {"confidence_score": 81.33, "unlocked": False, "reasons": ["x"]},
        }
    import services.tier3_readiness as tr
    monkeypatch.setattr(tr, "tier3_readiness_snapshot", _fake_snap)


@pytest.fixture
def capture_trades(monkeypatch):
    """Patch `_execute_bot_trade` to capture the qty each bot fires."""
    calls: list[dict] = []

    async def _fake_exec(bot, symbol, side, qty, price, **kw):  # noqa: ARG001
        calls.append({"bot": bot.get("name"), "symbol": symbol, "side": side, "qty": qty, "price": price})
        return {"status": "filled", "r_multiple": 1.0, "filled_qty": qty, "filled_price": price}

    monkeypatch.setattr(tbs, "_execute_bot_trade", _fake_exec)
    return calls


@pytest.fixture(autouse=True)
def reset_flag(monkeypatch):
    """Default: flag OFF. Tests that need it ON override explicitly."""
    monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", False)


# ════════════════════════════════════════════════════════════════════════════════
# Flag-off path: legacy behaviour preserved
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_flag_off_fires_at_configured_qty(capture_trades, monkeypatch):
    monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", False)
    monkeypatch.setattr(tbs, "_db", _FakeDB([_bot("alpha", qty=10)]))
    # Also stub the prediction logger + bot update path
    async def _noop(*args, **kw): pass  # noqa: ARG001
    async def _noop_log(*args, **kw): return "pid"  # noqa: ARG001
    # log_prediction lives in services.prediction_tracker; patched at
    # the call site via dynamic import, so stubbing the module fn is
    # enough.
    import services.prediction_tracker as pt
    monkeypatch.setattr(pt, "log_prediction", _noop_log)

    results = await tbs.process_signal_for_bots("u1", _signal(confidence=85))

    assert len(results) == 1
    assert len(capture_trades) == 1
    assert capture_trades[0]["qty"] == 10
    # No sizing metadata when flag is off.
    assert "sizing" not in results[0]


# ════════════════════════════════════════════════════════════════════════════════
# Flag-on path: low confidence skipped
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_flag_on_skips_below_trade_gate(
    healthy_readiness, capture_trades, monkeypatch
):
    monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", True)
    monkeypatch.setattr(tbs, "_db", _FakeDB([_bot("alpha", qty=10, min_conf=50)]))
    async def _noop(*args, **kw): pass  # noqa: ARG001

    # Confidence 52 passes the bot's per-config min_confidence=50 filter,
    # but fails the ai_core MIN_CONFIDENCE_TO_TRADE=55 gate.
    results = await tbs.process_signal_for_bots("u1", _signal(confidence=52))

    assert capture_trades == [], "no trade should fire below the 55 gate"
    assert results == []


# ════════════════════════════════════════════════════════════════════════════════
# Flag-on path: high confidence scaled DOWN by readiness throttle
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_flag_on_scales_down_for_throttled_readiness(
    typical_readiness, capture_trades, monkeypatch
):
    monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", True)
    monkeypatch.setattr(tbs, "_db", _FakeDB([_bot("alpha", qty=10)]))
    async def _noop(*args, **kw): pass  # noqa: ARG001
    import services.prediction_tracker as pt
    async def _noop_log(*args, **kw): return "pid"  # noqa: ARG001
    monkeypatch.setattr(pt, "log_prediction", _noop_log)

    # Confidence 92 → conf_mult ≈ 1.308; readiness score 81.33 with
    # the high_conf throttle firing → readiness_mult = min(0.81, 1.0) × 0.75 = 0.61.
    # Final mult ≈ 0.798 → qty=10 scaled to ~7.98.
    results = await tbs.process_signal_for_bots("u1", _signal(confidence=92))

    assert len(capture_trades) == 1
    scaled_qty = capture_trades[0]["qty"]
    assert 7.0 < scaled_qty < 9.0, f"expected ~8, got {scaled_qty}"

    # Sizing metadata stamped on the result for admin observability.
    assert "sizing" in results[0]
    meta = results[0]["sizing"]
    assert meta["original_qty"] == 10
    assert abs(meta["scaled_qty"] - scaled_qty) < 0.01
    assert 0.7 < meta["final_mult"] < 0.9


# ════════════════════════════════════════════════════════════════════════════════
# Flag-on path: healthy readiness + high confidence scales UP
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_flag_on_scales_up_for_perfect_readiness_and_high_conf(
    healthy_readiness, capture_trades, monkeypatch
):
    monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", True)
    monkeypatch.setattr(tbs, "_db", _FakeDB([_bot("alpha", qty=10)]))
    async def _noop(*args, **kw): pass  # noqa: ARG001
    import services.prediction_tracker as pt
    async def _noop_log(*args, **kw): return "pid"  # noqa: ARG001
    monkeypatch.setattr(pt, "log_prediction", _noop_log)

    # Perfect readiness (r_mult=1.0) + conf=100 (c_mult=1.5) → final=1.5 → qty=15
    results = await tbs.process_signal_for_bots("u1", _signal(confidence=100))

    assert len(capture_trades) == 1
    scaled_qty = capture_trades[0]["qty"]
    assert scaled_qty == pytest.approx(15.0, abs=0.05)
    assert results[0]["sizing"]["final_mult"] == pytest.approx(1.5, abs=0.05)


# ════════════════════════════════════════════════════════════════════════════════
# Flag-on path: readiness fetch throws → fallback to configured qty
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_flag_on_readiness_failure_falls_back_to_configured_qty(
    capture_trades, monkeypatch
):
    monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", True)

    async def _boom(db, days=30):  # noqa: ARG001
        raise RuntimeError("mongo down")
    import services.tier3_readiness as tr
    monkeypatch.setattr(tr, "tier3_readiness_snapshot", _boom)

    monkeypatch.setattr(tbs, "_db", _FakeDB([_bot("alpha", qty=10)]))
    async def _noop(*args, **kw): pass  # noqa: ARG001
    import services.prediction_tracker as pt
    async def _noop_log(*args, **kw): return "pid"  # noqa: ARG001
    monkeypatch.setattr(pt, "log_prediction", _noop_log)

    results = await tbs.process_signal_for_bots("u1", _signal(confidence=85))

    # Readiness fetch failed → we must NOT skip the trade. Legacy qty=10 fires.
    assert len(capture_trades) == 1
    assert capture_trades[0]["qty"] == 10
    assert "sizing" not in results[0]
