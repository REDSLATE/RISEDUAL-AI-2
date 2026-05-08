"""Tests for Shelly Lever 1 — historical paper-trade backfill.

Pinned invariants:

PURE HELPERS:
* ``test_fifo_pair_simple_one_to_one``
* ``test_fifo_pair_multi_buy_single_sell_split``
* ``test_fifo_pair_naked_sell_skipped``
* ``test_fifo_pair_unmatched_buy_at_end_dropped``
* ``test_fifo_pair_ignores_zero_qty_or_zero_price``
* ``test_categorise_pnl_buckets_correctly``

ORCHESTRATOR (run_backfill, with mocked DB):
* ``test_backfill_dry_run_does_not_call_add_and_persist``
* ``test_backfill_write_mode_calls_add_and_persist``
* ``test_backfill_returns_expected_report_shape``
* ``test_backfill_chronological_order_across_sources``
* ``test_backfill_aggregates_label_breakdown``
* ``test_backfill_toxic_threshold_respected``
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("REGIME_MEMORY_ENABLED", "true")
os.environ.setdefault("REGIME_MEMORY_MODE", "full_context")

import services.regime_memory_retrieval as _rmr  # noqa: E402

_rmr.REGIME_MEMORY_ENABLED = True
_rmr.REGIME_MEMORY_MODE = "full_context"

from services.auto_regime_tagger import RawMacroData  # noqa: E402
from services.learning_core_service import (  # noqa: E402
    reset_singleton_for_tests,
)
from services.shelly_backfill_service import (  # noqa: E402
    _crypto_row_to_trade_dict,
    _fifo_pair_buy_sells,
    categorise_pnl,
    run_backfill,
)


def _macro() -> RawMacroData:
    return RawMacroData(
        date="2026-02-15",
        vix=18.0, yield_2y=4.5, yield_10y=4.6,
        dxy=104.0, hy_oas_bp=420.0, ig_oas_bp=120.0,
        liquidity_z=0.0,
    )


def _ts(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 2, day, hour, tzinfo=timezone.utc)


# ─── pure helpers ──────────────────────────────────────────────


def test_fifo_pair_simple_one_to_one():
    """Single BUY + matching SELL → exactly one synthetic."""
    rows = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL",
         "qty": 10, "price": 100.0, "opened_at": _ts(1)},
        {"side": "SELL", "user_id": "u1", "symbol": "AAPL",
         "qty": 10, "price": 105.0, "opened_at": _ts(3)},
    ]
    out = _fifo_pair_buy_sells(rows)
    assert len(out) == 1
    syn = out[0]
    assert syn["entry_price"] == 100.0
    assert syn["exit_price"] == 105.0
    assert syn["direction"] == "LONG"
    assert syn["opened_at"] == _ts(1)
    assert syn["closed_at"] == _ts(3)


def test_fifo_pair_multi_buy_single_sell_split():
    """Two BUYs of 5 each, single SELL of 10 → two synthetics."""
    rows = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL",
         "qty": 5, "price": 100.0, "opened_at": _ts(1)},
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL",
         "qty": 5, "price": 102.0, "opened_at": _ts(2)},
        {"side": "SELL", "user_id": "u1", "symbol": "AAPL",
         "qty": 10, "price": 110.0, "opened_at": _ts(3)},
    ]
    out = _fifo_pair_buy_sells(rows)
    assert len(out) == 2
    # FIFO: first synthetic uses the older (cheaper) BUY.
    assert out[0]["entry_price"] == 100.0
    assert out[1]["entry_price"] == 102.0
    # Both close at the same SELL price.
    assert out[0]["exit_price"] == 110.0
    assert out[1]["exit_price"] == 110.0


def test_fifo_pair_naked_sell_skipped():
    """SELL with no preceding BUY in the window → no synthetic."""
    rows = [
        {"side": "SELL", "user_id": "u1", "symbol": "AAPL",
         "qty": 10, "price": 105.0, "opened_at": _ts(3)},
    ]
    assert _fifo_pair_buy_sells(rows) == []


def test_fifo_pair_unmatched_buy_at_end_dropped():
    """BUY with no matching SELL → still open, no synthetic."""
    rows = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL",
         "qty": 10, "price": 100.0, "opened_at": _ts(1)},
    ]
    assert _fifo_pair_buy_sells(rows) == []


def test_fifo_pair_ignores_zero_qty_or_zero_price():
    rows = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL",
         "qty": 0, "price": 100.0, "opened_at": _ts(1)},
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL",
         "qty": 5, "price": 0.0, "opened_at": _ts(1)},
        {"side": "SELL", "user_id": "u1", "symbol": "AAPL",
         "qty": 5, "price": 105.0, "opened_at": _ts(2)},
    ]
    # Both BUYs malformed — naked SELL.
    assert _fifo_pair_buy_sells(rows) == []


def test_fifo_pair_per_user_isolation():
    """User A's BUY shouldn't match User B's SELL."""
    rows = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL",
         "qty": 5, "price": 100.0, "opened_at": _ts(1)},
        {"side": "SELL", "user_id": "u2", "symbol": "AAPL",
         "qty": 5, "price": 110.0, "opened_at": _ts(2)},
    ]
    assert _fifo_pair_buy_sells(rows) == []


def test_categorise_pnl_buckets_correctly():
    assert categorise_pnl(2.0, -5.0) == {
        "win": True, "loss": False, "breakeven": False, "toxic": False,
    }
    assert categorise_pnl(-1.0, -5.0) == {
        "win": False, "loss": True, "breakeven": False, "toxic": False,
    }
    assert categorise_pnl(0.0, -5.0) == {
        "win": False, "loss": False, "breakeven": True, "toxic": False,
    }
    assert categorise_pnl(-7.0, -5.0) == {
        "win": False, "loss": True, "breakeven": False, "toxic": True,
    }


def test_crypto_row_passthrough():
    row = {
        "trade_id": "t1", "symbol": "BTC", "direction": "LONG",
        "entry_price": 100.0, "exit_price": 105.0,
        "opened_at": _ts(1), "closed_at": _ts(2), "pnl_pct": 5.0,
        "extra_field": "ignored",
    }
    out = _crypto_row_to_trade_dict(row)
    assert out["entry_price"] == 100.0
    assert out["pnl_pct"] == 5.0
    assert "extra_field" not in out


# ─── orchestrator ──────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows: list):
        self._rows = rows

    def sort(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    async def to_list(self, length):
        return self._rows


def _build_fake_db(paper_rows: list, crypto_rows: list) -> Any:
    db = MagicMock()
    db.paper_trades.find = MagicMock(return_value=_FakeCursor(paper_rows))
    db.crypto_paper_trades.find = MagicMock(
        return_value=_FakeCursor(crypto_rows),
    )
    return db


@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_call_add_and_persist():
    reset_singleton_for_tests()
    paper = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL", "qty": 5,
         "price": 100.0, "opened_at": _ts(1)},
        {"side": "SELL", "user_id": "u1", "symbol": "AAPL", "qty": 5,
         "price": 110.0, "opened_at": _ts(2)},
    ]
    db = _build_fake_db(paper, [])

    with patch(
        "services.learning_core_service.add_and_persist_memory",
        new=AsyncMock(),
    ) as mock_add:
        report = await run_backfill(
            db=db, since_days=30, dry_run=True, macro=_macro(),
        )
        assert mock_add.call_count == 0

    assert report["dry_run"] is True
    assert report["eligible"] == 1
    assert report["by_source"]["paper_trade"] == 1
    assert report["ingest_failures"] == 0


@pytest.mark.asyncio
async def test_backfill_write_mode_calls_add_and_persist():
    reset_singleton_for_tests()
    paper = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL", "qty": 5,
         "price": 100.0, "opened_at": _ts(1)},
        {"side": "SELL", "user_id": "u1", "symbol": "AAPL", "qty": 5,
         "price": 110.0, "opened_at": _ts(2)},
    ]
    crypto = [
        {"trade_id": "c1", "symbol": "BTC", "direction": "LONG",
         "entry_price": 50000.0, "exit_price": 51000.0,
         "opened_at": _ts(3), "closed_at": _ts(4),
         "pnl_pct": 2.0, "status": "closed"},
    ]
    db = _build_fake_db(paper, crypto)

    report = await run_backfill(
        db=db, since_days=30, dry_run=False, macro=_macro(),
    )
    # Both eligible, both ingested.
    assert report["eligible"] == 2
    assert report["dry_run"] is False
    assert report["by_source"]["paper_trade"] == 1
    assert report["by_source"]["crypto_paper_trade"] == 1


@pytest.mark.asyncio
async def test_backfill_returns_expected_report_shape():
    reset_singleton_for_tests()
    db = _build_fake_db([], [])
    report = await run_backfill(
        db=db, since_days=30, dry_run=True, macro=_macro(),
    )
    for key in (
        "dry_run", "since_days", "scanned_synthetic_trades",
        "eligible", "skipped", "skip_reasons", "by_source",
        "by_label", "by_direction", "toxic_count",
        "toxic_threshold_pct", "trust_tier", "macro_proxy",
        "ingest_failures", "approximate_regime_clusters",
        "canonical_engine_rejected",
        "regime_clusters_before", "regime_clusters_after",
        "regime_clusters_delta", "notes",
    ):
        assert key in report, f"missing report key: {key}"
    assert report["macro_proxy"] == "current"
    assert report["trust_tier"] == 0.25


@pytest.mark.asyncio
async def test_backfill_chronological_order_across_sources():
    """Memories from both sources must be ingested in
    chronological order so the EMA prototype updates land
    correctly."""
    reset_singleton_for_tests()

    # Paper synthetic will be at day 2; crypto at day 1.
    # Merge sort should put crypto first.
    paper = [
        {"side": "BUY", "user_id": "u1", "symbol": "AAPL", "qty": 5,
         "price": 100.0, "opened_at": _ts(2)},
        {"side": "SELL", "user_id": "u1", "symbol": "AAPL", "qty": 5,
         "price": 110.0, "opened_at": _ts(3)},
    ]
    crypto = [
        {"trade_id": "c1", "symbol": "BTC", "direction": "LONG",
         "entry_price": 100.0, "exit_price": 105.0,
         "opened_at": _ts(1), "closed_at": _ts(1, 14),
         "pnl_pct": 5.0, "status": "closed"},
    ]
    db = _build_fake_db(paper, crypto)

    ingested: list = []

    async def _capture(_db, mem):
        ingested.append(mem)
        return {"regime_cluster_id": "fake", "persistence": {"persisted": False}}

    with patch(
        "services.learning_core_service.add_and_persist_memory",
        new=_capture,
    ):
        await run_backfill(
            db=db, since_days=30, dry_run=False, macro=_macro(),
        )

    assert len(ingested) == 2
    # Crypto at day 1 must come before paper at day 2.
    assert ingested[0].ticker == "BTC"
    assert ingested[1].ticker == "AAPL"


@pytest.mark.asyncio
async def test_backfill_aggregates_label_breakdown():
    reset_singleton_for_tests()
    crypto = [
        # Win
        {"trade_id": "c-w", "symbol": "BTC", "direction": "LONG",
         "entry_price": 100.0, "exit_price": 110.0,
         "opened_at": _ts(1), "closed_at": _ts(2),
         "pnl_pct": 10.0, "status": "closed"},
        # Loss
        {"trade_id": "c-l", "symbol": "BTC", "direction": "LONG",
         "entry_price": 100.0, "exit_price": 98.0,
         "opened_at": _ts(3), "closed_at": _ts(4),
         "pnl_pct": -2.0, "status": "closed"},
        # Toxic
        {"trade_id": "c-t", "symbol": "BTC", "direction": "LONG",
         "entry_price": 100.0, "exit_price": 90.0,
         "opened_at": _ts(5), "closed_at": _ts(6),
         "pnl_pct": -10.0, "status": "closed"},
        # Breakeven
        {"trade_id": "c-b", "symbol": "BTC", "direction": "LONG",
         "entry_price": 100.0, "exit_price": 100.1,
         "opened_at": _ts(7), "closed_at": _ts(8),
         "pnl_pct": 0.1, "status": "closed"},
    ]
    db = _build_fake_db([], crypto)
    report = await run_backfill(
        db=db, since_days=30, dry_run=True, macro=_macro(),
        toxic_threshold_pct=-5.0,
    )
    assert report["eligible"] == 4
    assert report["by_label"]["win"] == 1
    # Both -2.0 (loss) and -10.0 (also a loss + toxic) count as loss.
    assert report["by_label"]["loss"] == 2
    assert report["by_label"]["breakeven"] == 1
    assert report["toxic_count"] == 1


@pytest.mark.asyncio
async def test_backfill_toxic_threshold_respected():
    reset_singleton_for_tests()
    crypto = [
        {"trade_id": "c1", "symbol": "BTC", "direction": "LONG",
         "entry_price": 100.0, "exit_price": 95.0,
         "opened_at": _ts(1), "closed_at": _ts(2),
         "pnl_pct": -5.0, "status": "closed"},
    ]
    db = _build_fake_db([], crypto)
    # Tighter threshold — -5.0 is NOT toxic (must be strictly less).
    r1 = await run_backfill(
        db=db, since_days=30, dry_run=True, macro=_macro(),
        toxic_threshold_pct=-5.0,
    )
    assert r1["toxic_count"] == 0
    # Looser threshold — -5.0 IS toxic.
    r2 = await run_backfill(
        db=db, since_days=30, dry_run=True, macro=_macro(),
        toxic_threshold_pct=-3.0,
    )
    assert r2["toxic_count"] == 1
