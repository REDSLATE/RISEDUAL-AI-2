"""Success Fee Service — tracks and calculates performance-based fees for broker-connected users.

Fee structure: 1.5% of profits above a $1,000 monthly threshold.
Only applies to users with active broker connections.
Billing period: Monthly, resets on the 1st.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

db: Any = None
FEE_RATE = 0.015  # 1.5%
PROFIT_THRESHOLD = 1000.0  # $1,000 minimum before fee applies


def set_db(database: Any) -> None:
    global db
    db = database


def _current_period() -> tuple[datetime, datetime]:
    """Return (start, end) datetimes for the current billing period."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # End = first day of next month
    if now.month == 12:
        end = start.replace(year=now.year + 1, month=1)
    else:
        end = start.replace(month=now.month + 1)
    return start, end


def _period_key(dt: datetime) -> str:
    """Return 'YYYY-MM' string for a datetime."""
    return dt.strftime("%Y-%m")


async def _get_broker_portfolio_value(user_id: str) -> float:
    """Get the current total portfolio value across all broker connections."""
    if db is None:
        return 0.0
    cursor = db.portfolio_snapshots.find(
        {"user_id": user_id},
        {"_id": 0, "total_value": 1}
    )
    total = 0.0
    async for snap in cursor:
        total += float(snap.get("total_value", 0))
    return total


async def _has_broker_connection(user_id: str) -> bool:
    """Check if user has any active broker connections."""
    if db is None:
        return False
    conn = await db.broker_connections.find_one(
        {"user_id": user_id, "is_active": True},
        {"_id": 0, "broker_id": 1}
    )
    return conn is not None


async def _get_or_create_fee_record(user_id: str) -> dict:
    """Get or create the current month's fee record."""
    period_start, period_end = _current_period()
    period = _period_key(period_start)

    existing = await db.success_fees.find_one(
        {"user_id": user_id, "period": period},
        {"_id": 0}
    )
    if existing:
        return existing

    # Get current portfolio value as the starting balance
    current_value = await _get_broker_portfolio_value(user_id)

    record = {
        "user_id": user_id,
        "period": period,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "starting_balance": current_value,
        "current_balance": current_value,
        "profit": 0.0,
        "threshold": PROFIT_THRESHOLD,
        "fee_rate": FEE_RATE,
        "fee_amount": 0.0,
        "status": "pending",  # pending | unpaid | paid | waived
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "paid_at": None,
        "notes": "",
    }
    await db.success_fees.insert_one({**record, "_id": None})
    # Remove the _id that MongoDB injects
    return record


def _calculate_fee(profit: float) -> float:
    """Calculate the success fee. 1.5% on gains above threshold only.
    
    No fee on losses or breakeven. Fee only applies to positive profit exceeding $1,000.
    """
    if profit <= 0:
        return 0.0
    if profit <= PROFIT_THRESHOLD:
        return 0.0
    return round((profit - PROFIT_THRESHOLD) * FEE_RATE, 2)


async def get_current_fee(user_id: str) -> dict:
    """Get the current month's fee summary for a user."""
    if db is None:
        return {"eligible": False, "reason": "Database not available"}

    has_broker = await _has_broker_connection(user_id)
    if not has_broker:
        return {
            "eligible": False,
            "reason": "No active broker connections",
            "fee_rate": FEE_RATE,
            "threshold": PROFIT_THRESHOLD,
        }

    record = await _get_or_create_fee_record(user_id)

    # Update current balance from latest portfolio snapshot
    current_value = await _get_broker_portfolio_value(user_id)
    starting = record.get("starting_balance", 0)
    profit = round(current_value - starting, 2)
    fee_amount = _calculate_fee(profit)

    # Update the record
    period_start, _ = _current_period()
    period = _period_key(period_start)
    await db.success_fees.update_one(
        {"user_id": user_id, "period": period},
        {"$set": {
            "current_balance": current_value,
            "profit": profit,
            "fee_amount": fee_amount,
            "status": "unpaid" if fee_amount > 0 else "pending",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )

    # Determine display status — only "unpaid" when there are actual gains above threshold
    if fee_amount > 0 and record.get("status") not in ("paid", "waived"):
        display_status = "unpaid"
    elif record.get("status") in ("paid", "waived"):
        display_status = record["status"]
    else:
        display_status = "no_fee"  # losses or below threshold

    return {
        "eligible": True,
        "period": period,
        "period_start": record.get("period_start"),
        "period_end": record.get("period_end"),
        "starting_balance": starting,
        "current_balance": current_value,
        "profit": profit,
        "is_positive": profit > 0,
        "threshold": PROFIT_THRESHOLD,
        "taxable_profit": max(0, profit - PROFIT_THRESHOLD) if profit > 0 else 0,
        "fee_rate": FEE_RATE,
        "fee_amount": fee_amount,
        "status": display_status,
    }


async def get_fee_history(user_id: str, limit: int = 12) -> list:
    """Get past fee records for a user."""
    if db is None:
        return []

    cursor = db.success_fees.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("period", -1).limit(limit)

    records = []
    async for r in cursor:
        records.append(r)
    return records


async def get_all_fees_admin(status_filter: Optional[str] = None, limit: int = 50) -> list:
    """Admin: get all fee records across all users."""
    if db is None:
        return []

    query = {}
    if status_filter and status_filter != "all":
        query["status"] = status_filter

    cursor = db.success_fees.find(query, {"_id": 0}).sort("period", -1).limit(limit)
    records = []
    async for r in cursor:
        # Enrich with user info
        user = await db.users.find_one(
            {"_id": r["user_id"]} if not isinstance(r["user_id"], str) else None,
            {"_id": 0, "email": 1, "name": 1}
        )
        if user is None and isinstance(r["user_id"], str):
            # Try string-based lookup
            from bson import ObjectId
            try:
                user = await db.users.find_one(
                    {"_id": ObjectId(r["user_id"])},
                    {"_id": 0, "email": 1, "name": 1}
                )
            except Exception:
                pass
        r["user_email"] = user.get("email", "Unknown") if user else "Unknown"
        r["user_name"] = user.get("name", "") if user else ""
        records.append(r)
    return records


async def mark_fee_paid(fee_user_id: str, period: str, admin_note: str = "") -> bool:
    """Admin: mark a fee record as paid."""
    if db is None:
        return False

    result = await db.success_fees.update_one(
        {"user_id": fee_user_id, "period": period},
        {"$set": {
            "status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "notes": admin_note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return result.modified_count > 0


async def waive_fee(fee_user_id: str, period: str, admin_note: str = "") -> bool:
    """Admin: waive a fee record."""
    if db is None:
        return False

    result = await db.success_fees.update_one(
        {"user_id": fee_user_id, "period": period},
        {"$set": {
            "status": "waived",
            "notes": admin_note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return result.modified_count > 0


async def get_fee_stats_admin() -> dict:
    """Admin: get aggregate fee statistics."""
    if db is None:
        return {}

    period_start, _ = _current_period()
    current_period = _period_key(period_start)

    total_records = await db.success_fees.count_documents({})
    unpaid = await db.success_fees.count_documents({"status": "unpaid"})
    paid = await db.success_fees.count_documents({"status": "paid"})
    waived = await db.success_fees.count_documents({"status": "waived"})
    current_month = await db.success_fees.count_documents({"period": current_period})

    # Total revenue from paid fees
    pipeline = [
        {"$match": {"status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$fee_amount"}}}
    ]
    agg = await db.success_fees.aggregate(pipeline).to_list(1)
    total_collected = agg[0]["total"] if agg else 0

    # Total outstanding
    pipeline_outstanding = [
        {"$match": {"status": "unpaid"}},
        {"$group": {"_id": None, "total": {"$sum": "$fee_amount"}}}
    ]
    agg_out = await db.success_fees.aggregate(pipeline_outstanding).to_list(1)
    total_outstanding = agg_out[0]["total"] if agg_out else 0

    return {
        "total_records": total_records,
        "unpaid": unpaid,
        "paid": paid,
        "waived": waived,
        "current_month_records": current_month,
        "total_collected": round(total_collected, 2),
        "total_outstanding": round(total_outstanding, 2),
        "fee_rate": FEE_RATE,
        "threshold": PROFIT_THRESHOLD,
    }
