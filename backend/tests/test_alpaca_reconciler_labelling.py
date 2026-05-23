"""Reconciliation labelling tests (2026-05-22).

Pins the operator hard rule: every backfilled Alpaca fill MUST
carry a label. The matcher walks ``sovereign_decisions`` first
(richest provenance), then ``predictions``, within a 10-minute
look-back window. Anything that fails to match is explicitly
tagged ``unattributed_real_fill`` — never given a fake confidence.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from services import backfill_signal_matcher as recon


_SCRIPT_PATH = Path("/app/backend/scripts/reconcile_alpaca_orders.py")


# ── _direction_matches table ───────────────────────────────────────


def test_direction_matches_buy_side_aliases():
    """``canonical_ai_dir`` recognises up/long/buy as LONG."""
    for d in ("up", "long", "buy", "UP", "LONG"):
        assert recon.direction_matches(d, "buy") is True
        assert recon.direction_matches(d, "sell") is False


def test_direction_matches_sell_side_aliases():
    """``canonical_ai_dir`` recognises down/short/sell as SHORT."""
    for d in ("down", "short", "sell"):
        assert recon.direction_matches(d, "sell") is True
        assert recon.direction_matches(d, "buy") is False


def test_direction_matches_unknowns_fail_closed():
    assert recon.direction_matches("", "buy") is False
    assert recon.direction_matches(None, "buy") is False  # type: ignore[arg-type]
    assert recon.direction_matches("up", "") is False


# ── In-memory Mongo stand-in ───────────────────────────────────────


class _Coll:
    def __init__(self, rows: list[dict]):
        self.rows = list(rows)

    def find(self, q=None, proj=None):
        matched = [r for r in self.rows if _match(r, q or {})]
        return _Cursor(matched)

    async def find_one(self, q=None, proj=None):
        for r in self.rows:
            if _match(r, q or {}):
                return dict(r)
        return None


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, field, order):
        rev = order == -1
        self.rows = sorted(self.rows, key=lambda r: r.get(field) or "",
                            reverse=rev)
        return self

    def limit(self, n):
        self.rows = self.rows[: int(n)]
        return self

    def __aiter__(self):
        self._it = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _match(d, q):
    for k, v in q.items():
        got = d.get(k)
        if isinstance(v, dict):
            if "$gte" in v and (got is None or got < v["$gte"]):
                return False
            if "$lte" in v and (got is None or got > v["$lte"]):
                return False
        else:
            if got != v:
                return False
    return True


class _DB:
    def __init__(self, **collections):
        self._cs = {k: _Coll(v) for k, v in collections.items()}

    def __getitem__(self, name):
        return self._cs.setdefault(name, _Coll([]))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── _match_signal: sovereign decisions take priority ──────────────


def test_match_signal_returns_sovereign_decision_when_in_window():
    when = datetime(2026, 5, 15, 14, 30, 0, tzinfo=timezone.utc)
    db = _DB(
        sovereign_decisions=[{
            "decision_id": "sov-1",
            "symbol": "AAPL",
            "action": "BUY",
            "confidence": 0.72,
            "conviction_tier": "HIGH",
            "created_at": when - timedelta(minutes=3),
            "feature_snapshot": {"regime": "TRENDING_UP"},
        }],
    )
    out = _run(recon.match_signal(
        db, symbol="AAPL", side="buy", when=when,
    ))
    assert out is not None
    assert out["source_signal"] == "sovereign_decision"
    assert out["sovereign_decision_id"] == "sov-1"
    assert out["confidence"] == 0.72
    assert out["conviction_tier"] == "HIGH"
    assert out["regime"] == "TRENDING_UP"
    assert out["labelled"] is True


def test_match_signal_falls_back_to_predictions_when_no_sovereign():
    when = datetime(2026, 5, 15, 14, 30, 0, tzinfo=timezone.utc)
    pred_at = (when - timedelta(minutes=4)).isoformat()
    db = _DB(
        sovereign_decisions=[],
        predictions=[{
            "prediction_id": "pred-1",
            "symbol": "NVDA",
            "direction": "up",
            "confidence": 65.0,  # 0-100 scale
            "timestamp": pred_at,
            "feature": "ema_cross",
        }],
    )
    out = _run(recon.match_signal(
        db, symbol="NVDA", side="buy", when=when,
    ))
    assert out is not None
    assert out["source_signal"] == "prediction"
    assert out["prediction_id"] == "pred-1"
    # Confidence normalised to 0-1
    assert out["confidence"] == 0.65
    assert out["feature"] == "ema_cross"


def test_match_signal_returns_none_when_no_signal_in_window():
    when = datetime(2026, 5, 15, 14, 30, 0, tzinfo=timezone.utc)
    db = _DB(sovereign_decisions=[], predictions=[])
    out = _run(recon.match_signal(
        db, symbol="ZZZ", side="buy", when=when,
    ))
    assert out is None


def test_match_signal_rejects_wrong_direction():
    """A SELL Alpaca order must NOT match a BUY sovereign decision."""
    when = datetime(2026, 5, 15, 14, 30, 0, tzinfo=timezone.utc)
    db = _DB(
        sovereign_decisions=[{
            "decision_id": "sov-buy",
            "symbol": "MSFT",
            "action": "BUY",
            "confidence": 0.6,
            "created_at": when - timedelta(minutes=2),
        }],
    )
    out = _run(recon.match_signal(
        db, symbol="MSFT", side="sell", when=when,
    ))
    assert out is None


def test_match_signal_window_excludes_old_signals():
    """A signal 30 minutes before the fill is too old."""
    when = datetime(2026, 5, 15, 14, 30, 0, tzinfo=timezone.utc)
    db = _DB(
        sovereign_decisions=[{
            "decision_id": "sov-stale",
            "symbol": "GOOGL",
            "action": "BUY",
            "confidence": 0.7,
            "created_at": when - timedelta(minutes=30),
        }],
    )
    out = _run(recon.match_signal(
        db, symbol="GOOGL", side="buy", when=when,
    ))
    assert out is None


def test_match_signal_window_excludes_future_signals():
    """A signal AFTER the fill is logically impossible — must not match."""
    when = datetime(2026, 5, 15, 14, 30, 0, tzinfo=timezone.utc)
    db = _DB(
        sovereign_decisions=[{
            "decision_id": "sov-future",
            "symbol": "TSLA",
            "action": "BUY",
            "confidence": 0.7,
            "created_at": when + timedelta(minutes=5),
        }],
    )
    out = _run(recon.match_signal(
        db, symbol="TSLA", side="buy", when=when,
    ))
    assert out is None


# ── Static authority firewall ──────────────────────────────────────


def test_reconcile_stats_track_labelling_breakdown():
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    for k in ("labelled_sovereign", "labelled_prediction", "unattributed"):
        assert f'"{k}"' in src, f"stats key missing: {k}"


def test_unattributed_fills_carry_distinct_receipt_type():
    """Operator hard rule: an unmatched fill must NOT be labelled
    ``real_fill`` (would lump it with attributed fills downstream)."""
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert '"unattributed_real_fill"' in src
    # And ``labelled`` is False for unmatched.
    assert '"labelled": bool(signal_match)' in src


def test_attributed_fills_carry_signal_provenance_fields():
    """Every labelled row stamps the signal's ID + confidence
    + tier + regime so Stage 3 / Tier 3 can read them."""
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    for field in (
        '"source_signal":',
        '"sovereign_decision_id":',
        '"prediction_id":',
        '"conviction_tier":',
        '"signal_at":',
    ):
        assert field in src, f"missing provenance field: {field}"


def test_window_constant_is_documented():
    """The 10-minute window lives in the shared matcher module."""
    matcher_src = Path("/app/backend/services/backfill_signal_matcher.py").read_text(encoding="utf-8")
    assert "SIGNAL_MATCH_WINDOW = timedelta(minutes=10)" in matcher_src
