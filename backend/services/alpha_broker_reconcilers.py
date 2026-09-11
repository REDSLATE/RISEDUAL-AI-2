"""Public.com + MooMoo reconcilers for the broker event watchdog.

Both reconcilers implement the same contract:

    def reconcile(entry: WatchdogEntry) -> {
        "outcome": "FILLED"|"OPEN"|"CANCELED"|"REJECTED"|"ABSENT"|"UNREACHABLE",
        "broker_order_id": Optional[str],
        "detail": Optional[str],
    }

They query the broker's authoritative order list / order details endpoint.
Any exception is downgraded to ``UNREACHABLE`` so the watchdog freezes the
intent as ``BROKER_STATE_UNKNOWN`` rather than assuming failure.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from services.alpha_broker_event_watchdog import WatchdogEntry, register_reconciler

logger = logging.getLogger(__name__)


# ── Public.com reconciler ──────────────────────────────────────────

def _public_client() -> Any:
    """Instantiate the Public.com trading client using operator creds."""
    api_key = os.environ.get("PUBLIC_API_KEY") or ""
    account_id = os.environ.get("PUBLIC_ACCOUNT_ID") or ""
    if not api_key or not account_id:
        return None
    try:
        from services.broker_service import PublicTradingService
        return PublicTradingService(api_key=api_key, api_secret=account_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[watchdog:public] client init failed: %s", exc)
        return None


def _map_public_status(status: str) -> str:
    """Public.com REST returns a small set of status strings."""
    s = (status or "").upper()
    if s in ("FILLED", "CLOSED", "COMPLETED", "EXECUTED"):
        return "FILLED"
    if s in ("PARTIALLY_FILLED", "PARTIAL"):
        return "OPEN"      # still working; treat as OPEN for reconciliation
    if s in ("CANCELED", "CANCELLED", "CANCELLED_BY_USER", "EXPIRED"):
        return "CANCELED" if "CANCEL" in s else "CANCELED"
    if s in ("REJECTED", "REPLACED_REJECTED", "REJECT"):
        return "REJECTED"
    if s in ("PENDING", "ACCEPTED", "NEW", "WORKING", "OPEN", "ACKNOWLEDGED"):
        return "OPEN"
    return ""  # unknown → caller returns UNREACHABLE


def reconcile_public(entry: WatchdogEntry) -> dict:
    client = _public_client()
    if client is None:
        return {"outcome": "UNREACHABLE", "detail": "public_client_unavailable"}

    order_id = entry.broker_order_id or ""
    try:
        if order_id and hasattr(client, "get_order"):
            row = client.get_order(order_id)
            if row is None:
                # Broker cleanly said "no such order" → ABSENT.
                return {"outcome": "ABSENT", "detail": "get_order_returned_none"}
            outcome = _map_public_status(row.get("status") or "")
            if not outcome:
                return {
                    "outcome": "UNREACHABLE",
                    "detail": f"unknown_status:{row.get('status')}",
                }
            return {
                "outcome": outcome,
                "broker_order_id": order_id,
                "detail": f"status={row.get('status')}",
            }

        # No broker order id — scan recent orders for our client_order_id.
        orders = client.get_orders(status="all", limit=100) or []
        for o in orders:
            if str(o.get("client_order_id") or "") == entry.client_order_id:
                outcome = _map_public_status(o.get("status") or "")
                if outcome:
                    return {
                        "outcome": outcome,
                        "broker_order_id": str(o.get("id") or ""),
                        "detail": f"status={o.get('status')}",
                    }
        return {"outcome": "ABSENT", "detail": "not_in_recent_orders"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[watchdog:public] reconcile raised: %s", exc)
        return {"outcome": "UNREACHABLE", "detail": f"exception:{exc.__class__.__name__}"}


# ── MooMoo reconciler ──────────────────────────────────────────────

def _map_moomoo_status(status: str) -> str:
    """MooMoo OpenD returns SDK enum names as strings."""
    s = (status or "").upper()
    if s in ("FILLED_ALL", "FILLED_PART", "FILLED"):
        # FILLED_PART is still active — treat as OPEN.
        return "FILLED" if s in ("FILLED_ALL", "FILLED") else "OPEN"
    if s in ("SUBMITTED", "SUBMITTING", "WAITING_SUBMIT", "OPEN"):
        return "OPEN"
    if s in ("CANCELLED_ALL", "CANCELLED_PART", "CANCELED", "CANCELLED"):
        return "CANCELED"
    if s in ("FAILED", "SUBMIT_FAILED", "REJECTED", "DISABLED", "DELETED"):
        return "REJECTED"
    return ""


def reconcile_moomoo(entry: WatchdogEntry) -> dict:
    try:
        from services import moomoo_broker_adapter
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "UNREACHABLE", "detail": f"import_failed:{exc.__class__.__name__}"}
    try:
        rows = moomoo_broker_adapter.orders()
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "UNREACHABLE", "detail": f"orders_raised:{exc.__class__.__name__}"}
    if rows is None:
        return {"outcome": "UNREACHABLE", "detail": "orders_returned_none"}

    for row in rows:
        row_remark = str(row.get("remark") or "")
        row_oid = str(row.get("order_id") or "")
        if row_remark == entry.client_order_id or (
            entry.broker_order_id and row_oid == entry.broker_order_id
        ):
            outcome = _map_moomoo_status(row.get("order_status") or "")
            if outcome:
                return {
                    "outcome": outcome,
                    "broker_order_id": row_oid or entry.broker_order_id,
                    "detail": f"order_status={row.get('order_status')}",
                }
            return {"outcome": "UNREACHABLE",
                    "detail": f"unknown_status:{row.get('order_status')}"}
    return {"outcome": "ABSENT", "detail": "not_in_moomoo_order_list"}


# ── Bootstrap ───────────────────────────────────────────────────────

def register_all() -> None:
    """Register both reconcilers with the watchdog. Idempotent."""
    register_reconciler("public", reconcile_public)
    register_reconciler("moomoo", reconcile_moomoo)
