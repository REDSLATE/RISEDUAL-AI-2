"""Broker Router — pick the venue for an outbound intent.

V1 policy (per operator spec)
-----------------------------
* Public.com remains the default equity venue.
* MooMoo becomes an operator-selected alternative — never automatic.
* **No fallback** — if the chosen broker rejects, we log the rejection
  and stop. We do NOT silently retry on another broker.

Alpha never picks the broker itself. This module is called from the
existing execution pipeline after Seat/Risk/RoadGuard/EntryTiming pass.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BROKER = "public"
_KNOWN_BROKERS = {"public", "moomoo"}


def default_broker() -> str:
    raw = (os.environ.get("BROKER_DEFAULT") or _DEFAULT_BROKER).strip().lower()
    return raw if raw in _KNOWN_BROKERS else _DEFAULT_BROKER


async def resolve_broker(db: Any, intent: dict) -> str:
    """Select a broker for this intent.

    Precedence:
      1. ``intent["broker"]`` if the operator hard-coded it
      2. Per-bot config: ``bot_id → broker`` (looked up in Mongo)
      3. Global default ``BROKER_DEFAULT``
    """
    forced = str(intent.get("broker") or "").strip().lower()
    if forced in _KNOWN_BROKERS:
        return forced
    bot_id = intent.get("bot_id") or (intent.get("alpha_daytrader") or {}).get("bot_id")
    if db is not None and bot_id:
        try:
            doc = await db.bots.find_one({"bot_id": bot_id}, {"broker": 1})
            if doc and str(doc.get("broker") or "").lower() in _KNOWN_BROKERS:
                return str(doc["broker"]).lower()
        except Exception:  # noqa: BLE001
            pass
    return default_broker()


def status() -> dict:
    """Aggregate broker health for the admin UI."""
    from services import moomoo_market_data_adapter as md
    from services import moomoo_broker_adapter as bk
    return {
        "default": default_broker(),
        "known": sorted(_KNOWN_BROKERS),
        "public": {"enabled": True, "note": "existing REST executor"},
        "moomoo": {
            "market_data": md.status(),
            "broker": bk.status(),
        },
    }


__all__ = ["resolve_broker", "default_broker", "status"]
