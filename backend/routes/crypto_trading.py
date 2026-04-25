"""Crypto Trading Routes — manual trigger for the crypto paper bot.

This is the route layer for the crypto bot lane. It lives separate
from ``routes/crypto_paper.py`` (which handles user-initiated
single fills) because this surface is for OPERATING the autonomous
crypto bot — manual one-shot run, status, and (future) config
mutations.

Endpoint
--------
* ``POST /api/crypto/paper-bot/run`` — admin-gated. Runs one pass
  of :func:`run_crypto_paper_bot` against the configured universe
  and returns the per-symbol open/skipped breakdown. Useful for
  smoke tests and during dev to seed the ``crypto_paper_trades``
  collection without waiting on the 15-minute scheduler tick.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.auth_helpers import get_current_user
from services.crypto_paper_trader import run_crypto_paper_bot
from services.crypto_quotes import get_crypto_quote, get_crypto_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crypto", tags=["crypto-trading"])

# Module-level db handle — set by route_registry on startup.
_db: Any = None


def set_db(database: Any) -> None:
    global _db
    _db = database


def _is_admin(user: dict) -> bool:
    role = (user.get("role") or "").lower()
    return role in ("admin", "owner")


@router.post("/paper-bot/run")
async def run_crypto_bot_once(
    request: Request,
    symbols: str = "BTC,ETH,SOL",
) -> dict:
    """Run one pass of the crypto paper bot.

    Admin-gated to prevent random users from spamming the bot's
    fill stream. ``symbols`` is a comma-separated override; defaults
    to the headline three.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not initialised")

    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    universe = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not universe:
        raise HTTPException(
            status_code=400,
            detail="At least one symbol required (e.g. ?symbols=BTC,ETH)",
        )

    results = await run_crypto_paper_bot(
        db=_db,
        quote_provider=get_crypto_quote,
        history_provider=get_crypto_history,
        symbols=universe,
    )

    opened = sum(1 for r in results if r.get("status") == "open")
    skipped = sum(1 for r in results if r.get("skipped"))

    return {
        "status": "ok",
        "count": len(results),
        "opened": opened,
        "skipped": skipped,
        "results": results,
    }
