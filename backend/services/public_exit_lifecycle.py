"""Legacy exits share Core v2's durable broker reconciliation.

No live switches are changed here. Callers enforce their existing live/session
and hardware gates before routing. The periodic reconciler is read-only at the
broker: acknowledgement never creates a fictional closed trade.
"""
from datetime import datetime, timezone
import logging

from services.alpha_core_v2.broker import PublicBroker
from services.alpha_core_v2.config import Config
from services.alpha_core_v2.engine import CoreV2Engine
from services.alpha_core_v2.receipts import ReceiptStore

log = logging.getLogger(__name__)


def _engine(client):
    cfg = Config.load()
    return CoreV2Engine(PublicBroker(client), ReceiptStore(cfg.db_path), cfg)


async def reconcile_exits(db, client, *, engine=None):
    engine = engine or _engine(client)
    result = await engine.reconcile_outstanding()
    if not result.get("ok") or db is None:
        return result
    # Only rows linked to an actual durable close receipt are touched.
    cursor = db.equity_live_trades.find({
        "broker_id": "public", "close_receipt_id": {"$exists": True},
        "close_pending": True,
    })
    async for row in cursor:
        receipt = engine.store.get_receipt(row["close_receipt_id"])
        if not receipt:
            continue
        done = bool(receipt.get("position_reconciled"))
        flat = done and receipt.get("position_status") == "reconciled_flat"
        update = {
            "close_filled_qty": receipt.get("broker_reported_fill_qty", 0.0),
            "close_order_status": receipt.get("order_status", "unknown"),
            "close_remaining_qty": receipt.get("reconciled_position_qty", row.get("size", 0)),
            "close_pending": not done,
            "status": "closed" if flat else "open",
        }
        if done:
            update["size"] = receipt.get("reconciled_position_qty", row.get("size", 0))
        if flat:
            update["closed_at"] = datetime.now(timezone.utc)
            update["close_price"] = receipt.get("fill_price") or None
        mutation = {"$set": update}
        if done:
            mutation["$unset"] = {"close_in_flight_at": "", "close_in_flight_client_order_id": ""}
        await db.equity_live_trades.update_one({"_id": row["_id"]}, mutation)
    return result


async def route_close(db, client, symbol, *, kind, reason):
    engine = _engine(client)
    reconciliation = await reconcile_exits(db, client, engine=engine)
    if not reconciliation.get("ok"):
        return None
    # Old in-flight markers are deliberately never aged out. If no durable
    # receipt exists, an operator/broker reconcile must resolve them first.
    row = await db.equity_live_trades.find_one({
        "broker_id": "public", "symbol": symbol, "status": "open",
    }) if db is not None else None
    if row and row.get("close_in_flight_at") and not row.get("close_receipt_id"):
        return None
    receipt = await engine.close_position(
        symbol, reason=reason, expected_side="long" if kind == "close_long" else "short",
    )
    if receipt.outcome.value == "BLOCKED":
        return None
    # Preserve unknown submissions as pending, including timeout before ACK.
    pending = bool(receipt.order_id and not receipt.position_reconciled)
    doc = {
        "close_receipt_id": receipt.receipt_id,
        "close_order_id": receipt.order_id,
        "close_requested_qty": receipt.requested_qty,
        "close_filled_qty": receipt.broker_reported_fill_qty,
        "close_pending": pending,
        "close_reason": reason, "close_intent_kind": kind,
        "close_order_status": receipt.order_status,
    }
    if pending:
        doc["close_in_flight_at"] = datetime.now(timezone.utc)
        doc["close_in_flight_client_order_id"] = receipt.order_id
    if db is not None and row:
        await db.equity_live_trades.update_one({"_id": row["_id"]}, {"$set": doc})
    elif db is not None and pending:
        await db.equity_live_trades.insert_one({
            **doc, "broker_id": "public", "symbol": symbol, "status": "open",
            "direction": "LONG" if kind == "close_long" else "SHORT",
            "size": receipt.broker_qty, "external_position": True,
        })
    return {
        **doc, "symbol": symbol, "broker_id": "public", "intent_kind": kind,
        "status": "close_pending" if pending else "close_rejected",
        "direction": "LONG" if kind == "close_long" else "SHORT",
        "side": "SELL" if kind == "close_long" else "BUY",
        "size": receipt.requested_qty,
        "remaining_qty": receipt.broker_qty,
        "filled_qty": receipt.broker_reported_fill_qty,
        "broker_order_id": receipt.order_id, "closed_at": None,
        "outcome": receipt.outcome.value,
    }
