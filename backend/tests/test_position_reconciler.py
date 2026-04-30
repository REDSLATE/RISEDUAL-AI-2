"""Tests for ``services.position_reconciler``.

Covers the close-detection logic in isolation and the end-to-end
sweep with a mock broker / Mongo. The actual broker API is mocked
— we're testing the reconciliation contract, not Alpaca's HTTP shape.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.position_reconciler import (
    _is_equity_position_closed,
    _is_option_position_closed,
    reconcile_equity_positions,
    reconcile_options_positions,
)


# ── _is_equity_position_closed ──────────────────────────────────────


@pytest.mark.asyncio
async def test_equity_still_open_when_position_present():
    opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    out = await _is_equity_position_closed(
        symbol="AAPL", side="buy", qty=10, opened_at=opened_at,
        broker_orders=[],
        broker_positions=[
            {"symbol": "AAPL", "qty": 10, "side": "long"},
        ],
    )
    assert out is None  # still open


@pytest.mark.asyncio
async def test_equity_closed_when_position_absent_and_opposite_fill_exists():
    opened_at = datetime.now(timezone.utc) - timedelta(days=2)
    closed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    out = await _is_equity_position_closed(
        symbol="AAPL", side="buy", qty=10, opened_at=opened_at,
        broker_positions=[],  # position is gone
        broker_orders=[
            {
                "symbol": "AAPL", "side": "sell", "status": "filled",
                "filled_avg_price": 200.0, "filled_at": closed_at.isoformat(),
            },
        ],
    )
    assert out is not None
    assert out["exit_price"] == 200.0
    assert out["close_reason"] == "broker_position_closed"


@pytest.mark.asyncio
async def test_equity_position_absent_but_no_close_fill_returns_none():
    """False-positive guard: if the position is gone but we can't find
    the exit fill, we MUST wait — never invent a price."""
    opened_at = datetime.now(timezone.utc) - timedelta(days=2)
    out = await _is_equity_position_closed(
        symbol="AAPL", side="buy", qty=10, opened_at=opened_at,
        broker_positions=[],
        broker_orders=[
            # opposite-side fill but BEFORE our open → not the close
            {
                "symbol": "AAPL", "side": "sell", "status": "filled",
                "filled_avg_price": 200.0,
                "filled_at": (opened_at - timedelta(days=1)).isoformat(),
            },
        ],
    )
    assert out is None


@pytest.mark.asyncio
async def test_equity_short_position_closed_by_buy_fill():
    opened_at = datetime.now(timezone.utc) - timedelta(days=1)
    closed_at = datetime.now(timezone.utc) - timedelta(hours=2)
    out = await _is_equity_position_closed(
        symbol="TSLA", side="sell", qty=5, opened_at=opened_at,
        broker_positions=[],
        broker_orders=[
            {
                "symbol": "TSLA", "side": "buy", "status": "filled",
                "filled_avg_price": 250.0, "filled_at": closed_at.isoformat(),
            },
        ],
    )
    assert out is not None
    assert out["exit_price"] == 250.0


# ── _is_option_position_closed ──────────────────────────────────────


@pytest.mark.asyncio
async def test_option_still_open_when_occ_in_positions():
    opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    out = await _is_option_position_closed(
        occ_symbol="AAPL250117C00200000",
        side="buy_to_open", qty=1, opened_at=opened_at,
        option_positions=[
            {"symbol": "AAPL250117C00200000", "qty": 1},
        ],
    )
    assert out is None


@pytest.mark.asyncio
async def test_option_closed_when_occ_absent():
    opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    out = await _is_option_position_closed(
        occ_symbol="AAPL250117C00200000",
        side="buy_to_open", qty=1, opened_at=opened_at,
        option_positions=[],
    )
    assert out is not None
    assert out["close_reason"] == "broker_option_closed_or_expired"


# ── _is_spread_closed ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spread_open_when_any_leg_remains():
    """The conservative rule: ONE remaining leg means the spread is
    still partially in the market, so we don't close the chain."""
    from services.position_reconciler import _is_spread_closed
    legs = [
        {"occ_symbol": "AAPL250117C00200000"},
        {"occ_symbol": "AAPL250117C00210000"},
    ]
    out = await _is_spread_closed(
        legs=legs,
        option_positions=[
            {"symbol": "AAPL250117C00210000", "qty": -1},
        ],
    )
    assert out is None


@pytest.mark.asyncio
async def test_spread_closed_when_all_legs_absent():
    from services.position_reconciler import _is_spread_closed
    legs = [
        {"occ_symbol": "AAPL250117C00200000"},
        {"occ_symbol": "AAPL250117C00210000"},
    ]
    out = await _is_spread_closed(legs=legs, option_positions=[])
    assert out is not None
    assert out["close_reason"] == "broker_spread_closed_or_expired"


@pytest.mark.asyncio
async def test_spread_returns_none_for_legs_without_occ():
    """Defensive: leg lacks an OCC → can't verify closed → don't
    risk a false-positive OUTCOME_VERIFIED block."""
    from services.position_reconciler import _is_spread_closed
    out = await _is_spread_closed(
        legs=[{"occ_symbol": ""}],
        option_positions=[],
    )
    assert out is None


@pytest.mark.asyncio
async def test_spread_zero_qty_position_treated_as_closed():
    """Brokers sometimes return position rows with qty=0 right after
    a close fill. That should not block the spread close."""
    from services.position_reconciler import _is_spread_closed
    legs = [
        {"occ_symbol": "AAPL250117C00200000"},
        {"occ_symbol": "AAPL250117C00210000"},
    ]
    out = await _is_spread_closed(
        legs=legs,
        option_positions=[
            {"symbol": "AAPL250117C00200000", "qty": 0},
            {"symbol": "AAPL250117C00210000", "qty": 0},
        ],
    )
    assert out is not None


@pytest.mark.asyncio
async def test_reconcile_options_appends_outcome_on_spread_close():
    """End-to-end multi-leg spread: pending row with all legs absent
    from broker positions → OUTCOME_VERIFIED block + outcome_appended
    flag set on the spread row, including outcome_leg_count."""
    from unittest.mock import patch
    from services.position_reconciler import reconcile_options_positions

    opened_at = datetime.now(timezone.utc) - timedelta(days=1)
    spread_row = {
        "_id": "spread1",
        "user_id": "u1",
        "provider": "alpaca",
        "is_spread": True,
        "occ_symbol": None,
        "limit_price": 1.5,
        "order_id": "ord-spread-1",
        "proof_chain_entity_id": "entity-spread-1",
        "outcome_appended": False,
        "created_at": opened_at,
        "legs": [
            {"occ_symbol": "AAPL250117C00200000", "qty": 1, "side": "buy_to_open"},
            {"occ_symbol": "AAPL250117C00210000", "qty": 1, "side": "sell_to_open"},
        ],
    }

    db = MagicMock()
    db.option_orders.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[spread_row]
    )
    db.option_orders.update_one = AsyncMock()

    fake_adapter = MagicMock()
    fake_adapter.list_option_positions = AsyncMock(return_value=[])

    with patch(
        "services.brokers.registry.get_options_adapter",
        return_value=fake_adapter,
    ), patch(
        "services.manual_order_guard.record_manual_order_outcome",
        new=AsyncMock(return_value="block-hash-spread"),
    ):
        summary = await reconcile_options_positions(db)

    assert summary["users"] == 1
    assert summary["closed"] == 1
    assert summary["errors"] == 0

    db.option_orders.update_one.assert_called_once()
    set_doc = db.option_orders.update_one.call_args[0][1]["$set"]
    assert set_doc["outcome_appended"] is True
    assert set_doc["outcome_block_hash"] == "block-hash-spread"
    assert set_doc["outcome_leg_count"] == 2




# ── End-to-end sweep ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_equity_skips_when_no_pending_rows():
    db = MagicMock()
    db.trade_orders.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    out = await reconcile_equity_positions(db)
    assert out == {"users": 0, "processed": 0, "closed": 0, "errors": 0}


@pytest.mark.asyncio
async def test_reconcile_equity_handles_db_none():
    out = await reconcile_equity_positions(None)
    assert out == {"users": 0, "processed": 0, "closed": 0, "errors": 0}


@pytest.mark.asyncio
async def test_reconcile_options_handles_db_none():
    out = await reconcile_options_positions(None)
    assert out == {"users": 0, "processed": 0, "closed": 0, "errors": 0}


@pytest.mark.asyncio
async def test_reconcile_equity_appends_outcome_on_close():
    """End-to-end: pending row + closed broker position →
    OUTCOME_VERIFIED proof block + outcome_appended flag set."""
    opened_at = datetime.now(timezone.utc) - timedelta(days=2)
    closed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    pending_row = {
        "_id": "row1",
        "user_id": "u1",
        "broker_id": "alpaca",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 10,
        "limit_price": 180.0,
        "broker_order_id": "ord-1",
        "proof_chain_entity_id": "entity-1",
        "outcome_appended": False,
        "created_at": opened_at,
    }

    db = MagicMock()
    db.trade_orders.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[pending_row]
    )
    db.trade_orders.update_one = AsyncMock()

    fake_client = MagicMock()
    fake_client.get_orders = MagicMock(return_value=[
        {
            "id": "ord-2", "symbol": "AAPL", "side": "sell", "status": "filled",
            "filled_avg_price": 200.0, "filled_at": closed_at.isoformat(),
        },
    ])
    fake_client.get_positions = MagicMock(return_value=[])

    with patch("routes.broker._get_user_broker", new=AsyncMock(return_value={})), \
         patch("routes.broker._get_or_refresh_client", new=AsyncMock(return_value=fake_client)), \
         patch(
            "services.manual_order_guard.record_manual_order_outcome",
            new=AsyncMock(return_value="block-hash-abc"),
        ):
        summary = await reconcile_equity_positions(db)

    assert summary["users"] == 1
    assert summary["processed"] == 1
    assert summary["closed"] == 1
    assert summary["errors"] == 0
    # Mongo was updated to mark outcome_appended True
    db.trade_orders.update_one.assert_called_once()
    update_call = db.trade_orders.update_one.call_args
    set_doc = update_call[0][1]["$set"]
    assert set_doc["outcome_appended"] is True
    assert set_doc["outcome_block_hash"] == "block-hash-abc"
    assert set_doc["outcome_exit_price"] == 200.0
    # 10 contracts × ($200 - $180) × +1 (long) = $200 P&L
    assert set_doc["outcome_pnl"] == pytest.approx(200.0)
