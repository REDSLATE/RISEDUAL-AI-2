"""Tripwire coverage for the crypto live closer (orphan-leg
auto-canceller).

Doctrine pinned here:
  1. ``classify_open_state`` returns the right state for each of
     the 5 possible (sl_resting, tp_resting) combinations.
  2. No Kraken cancel ever fires when both legs still rest on the book.
  3. SL gone, TP resting → TP is cancelled, row closed with
     ``closed_reason="sl_hit"`` and exit ≈ stop_loss_price.
  4. TP gone, SL resting → SL is cancelled, row closed with
     ``closed_reason="tp_hit"`` and exit ≈ take_profit_price.
  5. Both gone → row closed with ``closed_reason="manual_or_race"``,
     no further cancel attempted.
  6. Missing leg IDs (e.g. entry-time SL placement failed) →
     row stays open, classifier returns ``missing_ids``.
  7. Live wire disabled → closer is a strict no-op (no Mongo reads,
     no Kraken reads).
  8. Phantom empty open-orders set (Kraken API blip) → closer
     skips and retries; does NOT mark every open row as orphaned.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.crypto_live_closer import (
    classify_open_state,
    run_crypto_live_closer_pass,
)


# ── classify_open_state — the truth table ─────────────────────────────


@pytest.mark.parametrize("sl_id,tp_id,open_ids,expected", [
    ("SL-1", "TP-1", {"SL-1", "TP-1"}, "both_resting"),
    ("SL-1", "TP-1", {"TP-1"},          "sl_hit"),
    ("SL-1", "TP-1", {"SL-1"},          "tp_hit"),
    ("SL-1", "TP-1", set(),             "both_gone"),
    ("",     "",     set(),             "missing_ids"),
    # Partial: SL was never placed (entry-time SL failure), TP rests.
    # Treat the SL side as unmanaged so we don't misclassify as sl_hit.
    ("",     "TP-1", {"TP-1"},          "both_resting"),
    # Partial: TP never placed, SL rests.
    ("SL-1", "",     {"SL-1"},          "both_resting"),
    # Partial: TP never placed, SL gone → genuine sl_hit (with no
    # orphan TP to cancel).
    ("SL-1", "",     set(),             "sl_hit"),
])
def test_classify_open_state_truth_table(sl_id, tp_id, open_ids, expected):
    assert classify_open_state(sl_id, tp_id, open_ids) == expected


# ── run_crypto_live_closer_pass — integration ─────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    async def to_list(self, _n):
        return self._rows


class _FakeColl:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.updates = []
    def find(self, _q, _p=None):
        return _FakeCursor(self._rows)
    async def update_one(self, query, update):
        self.updates.append((query, update))
        return MagicMock(modified_count=1)


class _FakeDB:
    def __init__(self, rows=None):
        self.crypto_live_trades = _FakeColl(rows)


@pytest.mark.asyncio
async def test_closer_no_op_when_live_disabled(monkeypatch):
    """Master switch off → not a single Mongo or Kraken call."""
    monkeypatch.delenv("RISEDUAL_CRYPTO_LIVE_EXEC", raising=False)
    db = MagicMock()  # any access would raise on AttributeError trip
    out = await run_crypto_live_closer_pass(db)
    assert out == {
        "scanned": 0, "sl_hit": 0, "tp_hit": 0,
        "skipped": 0, "errors": 0, "time_based_exit": 0,
    }
    # No find() call.
    db.crypto_live_trades.find.assert_not_called()


@pytest.mark.asyncio
async def test_closer_no_op_when_no_open_rows(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB(rows=[])
    out = await run_crypto_live_closer_pass(db)
    assert out["scanned"] == 0
    assert out["sl_hit"] == 0
    assert out["tp_hit"] == 0


@pytest.mark.asyncio
async def test_closer_skips_when_kraken_keys_missing(monkeypatch):
    """Live wire armed but keys not loaded → skip; do NOT mark
    every row as orphaned just because we can't talk to Kraken."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    db = _FakeDB(rows=[{"_id": "x", "symbol": "BTC"}])
    out = await run_crypto_live_closer_pass(db)
    assert out["scanned"] == 1
    assert out["skipped"] == 1
    assert out["sl_hit"] == 0
    assert out["tp_hit"] == 0
    # Row must NOT have been closed.
    assert db.crypto_live_trades.updates == []


@pytest.mark.asyncio
async def test_closer_skips_when_kraken_returns_none(monkeypatch):
    """Kraken API briefly down → get_orders returns None → closer
    skips and retries next tick. Critical safety: a phantom empty
    set would otherwise mark every open position as orphaned."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.get_orders.side_effect = Exception("API down")

    rows = [{"_id": "x", "symbol": "BTC", "stop_loss_order_id": "SL-1",
             "take_profit_order_id": "TP-1", "entry_price": 60000.0}]
    db = _FakeDB(rows=rows)

    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["scanned"] == 1
    assert out["skipped"] == 1
    assert out["errors"] == 1
    assert db.crypto_live_trades.updates == []


@pytest.mark.asyncio
async def test_closer_cancels_orphan_tp_when_sl_fired(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    # SL fired → only TP-1 left resting on Kraken.
    fake_client.get_orders.return_value = [{"id": "TP-1"}]
    fake_client.cancel_order.return_value = True

    rows = [{
        "_id": "row-1", "symbol": "BTC", "direction": "LONG",
        "entry_price": 60000.0, "live_notional_usd": 25.0,
        "stop_loss_order_id": "SL-1", "stop_loss_price": 58200.0,
        "take_profit_order_id": "TP-1", "take_profit_price": 62400.0,
    }]
    db = _FakeDB(rows=rows)

    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["sl_hit"] == 1
    assert out["tp_hit"] == 0
    # TP orphan was cancelled.
    fake_client.cancel_order.assert_called_once_with("TP-1")
    # Row was closed at SL price with sl_hit reason.
    assert len(db.crypto_live_trades.updates) == 1
    update_set = db.crypto_live_trades.updates[0][1]["$set"]
    assert update_set["status"] == "closed"
    assert update_set["closed_reason"] == "sl_hit"
    assert update_set["exit_price"] == 58200.0
    # pnl_pct = (58200 - 60000) / 60000 * 100 = -3.0
    assert update_set["pnl_pct"] == pytest.approx(-3.0)
    # pnl_usd = 25.0 * (-3.0 / 100) = -0.75
    assert update_set["pnl_usd"] == pytest.approx(-0.75)


@pytest.mark.asyncio
async def test_closer_cancels_orphan_sl_when_tp_fired(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    # TP fired → only SL-1 left resting on Kraken.
    fake_client.get_orders.return_value = [{"id": "SL-1"}]
    fake_client.cancel_order.return_value = True

    rows = [{
        "_id": "row-2", "symbol": "ETH", "direction": "LONG",
        "entry_price": 3000.0, "live_notional_usd": 25.0,
        "stop_loss_order_id": "SL-1", "stop_loss_price": 2910.0,
        "take_profit_order_id": "TP-1", "take_profit_price": 3120.0,
    }]
    db = _FakeDB(rows=rows)

    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["tp_hit"] == 1
    assert out["sl_hit"] == 0
    fake_client.cancel_order.assert_called_once_with("SL-1")
    update_set = db.crypto_live_trades.updates[0][1]["$set"]
    assert update_set["closed_reason"] == "tp_hit"
    assert update_set["exit_price"] == 3120.0
    assert update_set["pnl_pct"] == pytest.approx(4.0)
    assert update_set["pnl_usd"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_closer_handles_both_gone(monkeypatch):
    """Both legs missing from Kraken → operator manual close or
    race. Mark row closed with closed_reason=manual_or_race; no
    further cancel attempt."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.get_orders.return_value = []
    rows = [{
        "_id": "row-3", "symbol": "BTC", "direction": "LONG",
        "entry_price": 60000.0, "live_notional_usd": 25.0,
        "stop_loss_order_id": "SL-1", "stop_loss_price": 58200.0,
        "take_profit_order_id": "TP-1", "take_profit_price": 62400.0,
    }]
    db = _FakeDB(rows=rows)

    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["skipped"] == 1  # both_gone counted as skipped
    # No cancel attempted — both already gone.
    fake_client.cancel_order.assert_not_called()
    update_set = db.crypto_live_trades.updates[0][1]["$set"]
    assert update_set["closed_reason"] == "manual_or_race"


@pytest.mark.asyncio
async def test_closer_leaves_both_resting_alone(monkeypatch):
    """Sanity: if both SL and TP are still on the book, closer
    must take ZERO action on that row."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.get_orders.return_value = [{"id": "SL-1"}, {"id": "TP-1"}]
    rows = [{
        "_id": "row-4", "symbol": "BTC", "direction": "LONG",
        "entry_price": 60000.0, "live_notional_usd": 25.0,
        "stop_loss_order_id": "SL-1", "take_profit_order_id": "TP-1",
        "stop_loss_price": 58200.0, "take_profit_price": 62400.0,
    }]
    db = _FakeDB(rows=rows)
    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["sl_hit"] == 0
    assert out["tp_hit"] == 0
    assert out["skipped"] == 1
    fake_client.cancel_order.assert_not_called()
    assert db.crypto_live_trades.updates == []


@pytest.mark.asyncio
async def test_closer_handles_mixed_batch(monkeypatch):
    """Batch with 1 sl_hit + 1 tp_hit + 1 both_resting → each
    classified independently, only 2 cancellations fire."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    # Kraken still has TP-A (row 1, SL fired), SL-B (row 2, TP fired),
    # and both legs of row 3.
    fake_client.get_orders.return_value = [
        {"id": "TP-A"},
        {"id": "SL-B"},
        {"id": "SL-C"}, {"id": "TP-C"},
    ]
    fake_client.cancel_order.return_value = True

    rows = [
        {"_id": "a", "symbol": "BTC", "direction": "LONG",
         "entry_price": 60000.0, "live_notional_usd": 25.0,
         "stop_loss_order_id": "SL-A", "stop_loss_price": 58200.0,
         "take_profit_order_id": "TP-A", "take_profit_price": 62400.0},
        {"_id": "b", "symbol": "ETH", "direction": "LONG",
         "entry_price": 3000.0, "live_notional_usd": 25.0,
         "stop_loss_order_id": "SL-B", "stop_loss_price": 2910.0,
         "take_profit_order_id": "TP-B", "take_profit_price": 3120.0},
        {"_id": "c", "symbol": "BTC", "direction": "LONG",
         "entry_price": 61000.0, "live_notional_usd": 25.0,
         "stop_loss_order_id": "SL-C", "stop_loss_price": 59170.0,
         "take_profit_order_id": "TP-C", "take_profit_price": 63440.0},
    ]
    db = _FakeDB(rows=rows)

    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["scanned"] == 3
    assert out["sl_hit"] == 1
    assert out["tp_hit"] == 1
    assert out["skipped"] == 1  # row-c stays open
    # Two orphan cancels fired (TP-A from row-a's sl_hit, SL-B from row-b's tp_hit).
    cancelled = [c.args[0] for c in fake_client.cancel_order.call_args_list]
    assert set(cancelled) == {"TP-A", "SL-B"}


@pytest.mark.asyncio
async def test_closer_handles_db_read_error(monkeypatch):
    """If Mongo blows up reading open rows, return errors counter
    without raising."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")

    class _BoomColl:
        def find(self, *_a, **_kw):
            raise RuntimeError("mongo down")
    class _BoomDB:
        crypto_live_trades = _BoomColl()
    out = await run_crypto_live_closer_pass(_BoomDB())
    assert out["errors"] == 1
    assert out["scanned"] == 0


# ── Time-based stale-exit branch (2026-06) ────────────────────────────


@pytest.mark.asyncio
async def test_time_based_exit_disabled_by_default(monkeypatch):
    """CRYPTO_LIVE_MAX_HOLD_HOURS unset → no time-based action even
    on a 30-day-old position. Default-OFF safety."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    monkeypatch.delenv("CRYPTO_LIVE_MAX_HOLD_HOURS", raising=False)

    old = datetime.now(timezone.utc) - timedelta(days=30)
    rows = [{
        "_id": "r1", "symbol": "BTC", "direction": "LONG",
        "entry_price": 60000.0, "size": 0.0004, "live_notional_usd": 25.0,
        "stop_loss_order_id": "SL-1", "stop_loss_price": 58200.0,
        "take_profit_order_id": "TP-1", "take_profit_price": 62400.0,
        "opened_at": old,
    }]
    fake_client = MagicMock()
    # SL+TP both still resting — neither hit.
    fake_client.get_orders.return_value = [{"id": "SL-1"}, {"id": "TP-1"}]

    db = _FakeDB(rows=rows)
    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["time_based_exit"] == 0
    # No SELL placed; no row closed.
    fake_client.place_order.assert_not_called()
    assert db.crypto_live_trades.updates == []


@pytest.mark.asyncio
async def test_time_based_exit_fires_when_stale(monkeypatch):
    """Row past CRYPTO_LIVE_MAX_HOLD_HOURS → SL+TP cancelled,
    market SELL placed, row closed with reason=time_based_exit."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    monkeypatch.setenv("CRYPTO_LIVE_MAX_HOLD_HOURS", "24")

    old = datetime.now(timezone.utc) - timedelta(hours=48)
    rows = [{
        "_id": "r2", "symbol": "BTC", "direction": "LONG",
        "entry_price": 60000.0, "size": 0.0004, "live_notional_usd": 25.0,
        "stop_loss_order_id": "SL-2", "stop_loss_price": 58200.0,
        "take_profit_order_id": "TP-2", "take_profit_price": 62400.0,
        "opened_at": old,
    }]
    fake_client = MagicMock()
    # Both legs still resting — confirms the time-based path runs
    # BEFORE the SL/TP classifier (otherwise this row would be
    # classified as "both_resting" and skipped).
    fake_client.get_orders.return_value = [{"id": "SL-2"}, {"id": "TP-2"}]
    fake_client.cancel_order.return_value = True
    fake_client.place_order.return_value = {"id": "TIME-SELL-1"}
    fake_client.get_ticker.return_value = {"last": 61000.0}

    db = _FakeDB(rows=rows)
    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["time_based_exit"] == 1
    # Both legs cancelled.
    cancelled = {c.args[0] for c in fake_client.cancel_order.call_args_list}
    assert cancelled == {"SL-2", "TP-2"}
    # Market SELL placed for the row's size.
    fake_client.place_order.assert_called_once()
    kw = fake_client.place_order.call_args.kwargs
    assert kw["side"] == "sell"
    assert kw["order_type"] == "market"
    assert kw["qty"] == 0.0004
    # Row closed with time_based_exit reason.
    assert len(db.crypto_live_trades.updates) == 1
    update_set = db.crypto_live_trades.updates[0][1]["$set"]
    assert update_set["closed_reason"] == "time_based_exit"
    assert update_set["status"] == "closed"
    # Exit at the mark price probe, not entry.
    assert update_set["exit_price"] == 61000.0


@pytest.mark.asyncio
async def test_time_based_exit_skips_short_holds(monkeypatch):
    """Row younger than CRYPTO_LIVE_MAX_HOLD_HOURS → no force-close."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    monkeypatch.setenv("CRYPTO_LIVE_MAX_HOLD_HOURS", "24")

    young = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = [{
        "_id": "r3", "symbol": "BTC", "direction": "LONG",
        "entry_price": 60000.0, "size": 0.0004,
        "stop_loss_order_id": "SL-3", "stop_loss_price": 58200.0,
        "take_profit_order_id": "TP-3", "take_profit_price": 62400.0,
        "opened_at": young,
    }]
    fake_client = MagicMock()
    fake_client.get_orders.return_value = [{"id": "SL-3"}, {"id": "TP-3"}]

    db = _FakeDB(rows=rows)
    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["time_based_exit"] == 0
    fake_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_time_based_exit_falls_back_to_entry_on_quote_fail(monkeypatch):
    """If the mark-price probe fails, exit_price falls back to
    entry so pnl_pct = 0 (honest 'no broker mark, don't lie')."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    monkeypatch.setenv("CRYPTO_LIVE_MAX_HOLD_HOURS", "12")

    old = datetime.now(timezone.utc) - timedelta(hours=20)
    rows = [{
        "_id": "r4", "symbol": "ETH", "direction": "LONG",
        "entry_price": 3000.0, "size": 0.01,
        "stop_loss_order_id": "SL-4", "stop_loss_price": 2910.0,
        "take_profit_order_id": "TP-4", "take_profit_price": 3120.0,
        "opened_at": old,
    }]
    fake_client = MagicMock()
    fake_client.get_orders.return_value = [{"id": "SL-4"}, {"id": "TP-4"}]
    fake_client.cancel_order.return_value = True
    fake_client.place_order.return_value = {"id": "TIME-SELL-2"}
    fake_client.get_ticker.side_effect = Exception("quote down")

    db = _FakeDB(rows=rows)
    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["time_based_exit"] == 1
    update_set = db.crypto_live_trades.updates[0][1]["$set"]
    assert update_set["closed_reason"] == "time_based_exit"
    assert update_set["exit_price"] == 3000.0
    assert update_set["pnl_pct"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_time_based_exit_refuses_zero_qty(monkeypatch):
    """Missing/zero qty → refuse the SELL (we'd flat the wrong size)."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    monkeypatch.setenv("CRYPTO_LIVE_MAX_HOLD_HOURS", "24")

    old = datetime.now(timezone.utc) - timedelta(hours=48)
    rows = [{
        "_id": "r5", "symbol": "BTC", "direction": "LONG",
        "entry_price": 60000.0, "size": 0.0,  # missing/zero qty
        "stop_loss_order_id": "SL-5",
        "take_profit_order_id": "TP-5",
        "opened_at": old,
    }]
    fake_client = MagicMock()
    fake_client.get_orders.return_value = [{"id": "SL-5"}, {"id": "TP-5"}]

    db = _FakeDB(rows=rows)
    with patch(
        "services.crypto_live_closer._kraken_client",
        return_value=fake_client,
    ):
        out = await run_crypto_live_closer_pass(db)
    assert out["time_based_exit"] == 0
    fake_client.place_order.assert_not_called()

