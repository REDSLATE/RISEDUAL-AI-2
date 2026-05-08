"""Tests for services.crypto_sltp_expectancy.

Pure-function paths covered:
- _safe_mean / _win_rate edge cases
- _bucketize honours sample-count sort and missing keys
- _tp_tightening_grid only adjusts take_profit rows
- _sl_tightening_grid only adjusts stop_loss rows, projects -f
- _headline picks the right copy bucket
- compute_sltp_expectancy guards on insufficient data
- _empty payload schema is stable
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from services import crypto_sltp_expectancy as sx


# ── pure helpers ──────────────────────────────────────────────────


def test_safe_mean_empty_zero():
    assert sx._safe_mean([]) == 0.0


def test_safe_mean_rounds_to_4_dp():
    assert sx._safe_mean([1.0, 1.5, 2.0]) == 1.5


def test_win_rate_strict_gt_zero():
    assert sx._win_rate([1.0, -0.5, 0.0, 0.001]) == 0.5  # 0.0 is NOT a win
    assert sx._win_rate([]) == 0.0


def test_bucketize_sorts_by_count_descending():
    rows = [
        {"close_reason": "take_profit", "r_multiple": 1.5},
        {"close_reason": "stop_loss", "r_multiple": -1.0},
        {"close_reason": "take_profit", "r_multiple": 1.2},
        {"close_reason": "manual", "r_multiple": 0.3},
    ]
    out = sx._bucketize(rows, "close_reason")
    counts = [b["count"] for b in out]
    assert counts == sorted(counts, reverse=True)
    assert out[0]["close_reason"] == "take_profit"
    assert out[0]["count"] == 2


def test_bucketize_missing_field_becomes_unknown():
    rows = [
        {"r_multiple": 0.5},
        {"close_reason": "", "r_multiple": -0.2},
    ]
    out = sx._bucketize(rows, "close_reason")
    assert out[0]["close_reason"] == "unknown"
    assert out[0]["count"] == 2


# ── tightening grids ──────────────────────────────────────────────


def test_tp_tightening_only_affects_take_profit_rows():
    rows = [
        {"close_reason": "take_profit", "r_multiple": 2.0},
        {"close_reason": "take_profit", "r_multiple": 2.0},
        {"close_reason": "stop_loss", "r_multiple": -1.0},
        {"close_reason": "manual", "r_multiple": 0.5},
    ]
    grid = sx._tp_tightening_grid(rows)
    # baseline (factor=1.0) preserves expectancy
    base = next(g for g in grid if g["factor"] == 1.0)
    assert base["projected_expectancy_r"] == round((2.0 + 2.0 - 1.0 + 0.5) / 4, 4)
    # f=0.5 — the two TP rows project to 1.0 each
    half = next(g for g in grid if g["factor"] == 0.5)
    assert half["projected_expectancy_r"] == round((1.0 + 1.0 - 1.0 + 0.5) / 4, 4)
    # applied_to should equal the TP count
    assert half["applied_to"] == 2
    assert half["of_total"] == 4


def test_sl_tightening_only_affects_stop_loss_rows():
    rows = [
        {"close_reason": "stop_loss", "r_multiple": -1.0},
        {"close_reason": "stop_loss", "r_multiple": -1.0},
        {"close_reason": "take_profit", "r_multiple": 2.0},
    ]
    grid = sx._sl_tightening_grid(rows)
    half = next(g for g in grid if g["factor"] == 0.5)
    # Two SL rows project to -0.5 each, TP unchanged
    assert half["projected_expectancy_r"] == round((-0.5 - 0.5 + 2.0) / 3, 4)
    assert half["applied_to"] == 2


def test_tightening_grids_handle_no_matching_rows():
    rows = [{"close_reason": "manual", "r_multiple": 0.3}]
    tp = sx._tp_tightening_grid(rows)
    # No TP rows → every factor projects to baseline = 0.3
    assert all(g["projected_expectancy_r"] == 0.3 for g in tp)


# ── headline copy ─────────────────────────────────────────────────


def test_headline_strong_positive():
    out = sx._headline(0.50, [], [], [])
    assert "positive-expectancy" in out


def test_headline_strong_negative():
    out = sx._headline(-0.30, [], [], [])
    assert "losing money" in out


def test_headline_recommends_tighter_sl_when_it_helps():
    sl_grid = [
        {"factor": 0.5, "projected_expectancy_r": 0.30},
        {"factor": 1.0, "projected_expectancy_r": 0.10},
    ]
    out = sx._headline(0.10, [], [], sl_grid)
    assert "tightening SL" in out


def test_headline_break_even_no_winner():
    sl_grid = [{"factor": 1.0, "projected_expectancy_r": 0.0}]
    out = sx._headline(0.0, [], [], sl_grid)
    assert "near break-even" in out


# ── _empty contract ───────────────────────────────────────────────


def test_empty_insufficient_data_message():
    out = sx._empty(30, "insufficient_data", available=10)
    assert out["closed_trades"] == 10
    assert out["status"] == "insufficient_data"
    assert "10" in out["headline"]


def test_empty_other_reason_no_count():
    out = sx._empty(30, "db_unavailable")
    assert out["closed_trades"] == 0
    assert out["status"] == "db_unavailable"


# ── compute_sltp_expectancy integration ───────────────────────────


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    def find(self, flt, projection=None):
        return _FakeCursor(self._docs)


class _FakeDB:
    def __init__(self, docs):
        self._coll = _FakeColl(docs)

    def __getitem__(self, key):
        return self._coll


@pytest.mark.asyncio
async def test_compute_returns_empty_when_db_none():
    out = await sx.compute_sltp_expectancy(None, window_days=30)
    assert out["status"] == "db_unavailable"


@pytest.mark.asyncio
async def test_compute_insufficient_data():
    db = _FakeDB([
        {"r_multiple": 1.0, "close_reason": "take_profit", "direction": "long", "regime": "bull"}
    ] * 5)
    out = await sx.compute_sltp_expectancy(db, window_days=30)
    assert out["status"] == "insufficient_data"
    assert out["closed_trades"] == 5


@pytest.mark.asyncio
async def test_compute_full_payload_with_enough_data():
    docs = (
        [{"r_multiple": 1.5, "close_reason": "take_profit", "direction": "long", "regime": "bull"}] * 12
        + [{"r_multiple": -1.0, "close_reason": "stop_loss", "direction": "short", "regime": "bear"}] * 8
        + [{"r_multiple": 0.3, "close_reason": "manual", "direction": "long", "regime": "sideways"}] * 5
    )
    db = _FakeDB(docs)
    out = await sx.compute_sltp_expectancy(db, window_days=30)
    assert out["status"] == "ok"
    assert out["closed_trades"] == 25
    # expectancy = (12*1.5 + 8*-1.0 + 5*0.3) / 25 = (18 - 8 + 1.5)/25 = 0.46
    assert out["expectancy_r"] == 0.46
    # win_rate = (12 TP wins + 5 manual @ +0.3) / 25 = 17/25 = 0.68
    assert out["win_rate"] == 0.68
    # buckets present
    assert any(b["close_reason"] == "take_profit" for b in out["by_close_reason"])
    assert any(b["direction"] == "long" for b in out["by_direction"])
    assert any(b["regime"] == "bull" for b in out["by_regime"])
    # tightening grids exist with the right shape
    assert len(out["tp_tightening"]) == len(sx._TIGHTEN_GRID)
    assert len(out["sl_tightening"]) == len(sx._TIGHTEN_GRID)
    # headline is non-empty
    assert isinstance(out["headline"], str) and len(out["headline"]) > 10
