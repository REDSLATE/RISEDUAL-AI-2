"""Tier 3 autonomous action: live execution via Alpaca REST API.

This module is intentionally conservative.  Every decision path either exits
early or logs at WARNING level before submitting an order.  A hard 2% position
cap is enforced regardless of Kelly output.

Architecture
------------
- Alpaca REST v2 endpoint: ``https://paper-api.alpaca.markets`` (paper) or
  ``https://api.alpaca.markets`` (live), controlled by ``ALPACA_BASE_URL``.
- API credentials: ``ALPACA_API_KEY`` + ``ALPACA_SECRET_KEY`` env vars.
  Credentials are NEVER logged.
- Called by :mod:`app.services.ml_orchestrator` after Tier 2 paper trade.

Gate requirements (Tier 3)
---------------------------
- All Tier 2 gates cleared.
- accuracy   >= 62%
- Sharpe     >= 1.2  (30-day live paper period)
- Max DD     <  12%
- live_days  >= 30
- user_opted_in == True (explicit env var ``RISEDUAL_LIVE_EXECUTION=1``)

Safety controls
---------------
- Hard position cap: 2% of portfolio equity (NOT Kelly-derived).
- Market orders only — no limit/stop logic in this version.
- Duplicate prevention: checks for an existing open position before ordering.
- All order errors are caught and logged; they NEVER propagate to HTTP responses.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from risedual_core.schemas.market import FeaturesSnapshot, SignalResult

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_ALPACA_API_KEY: str | None = os.getenv("ALPACA_API_KEY")
_ALPACA_SECRET_KEY: str | None = os.getenv("ALPACA_SECRET_KEY")
_ALPACA_BASE_URL: str = os.getenv(
    "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
)

# Hard position size cap as fraction of portfolio equity
_HARD_CAP_FRACTION: float = 0.02   # 2%

# Minimum confidence for live execution
_MIN_LIVE_CONFIDENCE: float = 0.70

# User must explicitly set RISEDUAL_LIVE_EXECUTION=1
_LIVE_OPT_IN: bool = os.getenv("RISEDUAL_LIVE_EXECUTION", "0") == "1"

# Regimes eligible for live execution (same as paper trading)
_TRADEABLE_REGIMES: frozenset[str] = frozenset({"trending_up", "trending_down"})


# ── Alpaca REST helpers ───────────────────────────────────────────────────────


def _alpaca_headers() -> dict[str, str]:
    """Build Alpaca auth headers.  Returns empty dict if keys are absent."""
    if not _ALPACA_API_KEY or not _ALPACA_SECRET_KEY:
        return {}
    return {
        "APCA-API-KEY-ID": _ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": _ALPACA_SECRET_KEY,
        "Content-Type": "application/json",
    }


async def _get_account(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Fetch Alpaca account object.  Returns ``None`` on error."""
    try:
        resp = await client.get(
            f"{_ALPACA_BASE_URL}/v2/account",
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
        log.warning("[ml_alpaca] /v2/account returned %d", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        log.warning("[ml_alpaca] Account fetch failed: %s", exc)
    return None


async def _get_open_position(
    client: httpx.AsyncClient, ticker: str
) -> dict[str, Any] | None:
    """Return existing open position for ``ticker``, or ``None``."""
    try:
        resp = await client.get(
            f"{_ALPACA_BASE_URL}/v2/positions/{ticker}",
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None   # no position — expected
        log.warning("[ml_alpaca] /v2/positions/%s returned %d", ticker, resp.status_code)
    except Exception as exc:  # noqa: BLE001
        log.warning("[ml_alpaca] Position check failed for %s: %s", ticker, exc)
    return None


async def _submit_market_order(
    client: httpx.AsyncClient,
    ticker: str,
    side: str,          # "buy" or "sell"
    notional_usd: float,
) -> str | None:
    """Submit a market order by notional value.

    Returns the Alpaca order ID on success, ``None`` on failure.
    """
    payload = {
        "symbol": ticker,
        "notional": str(round(notional_usd, 2)),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    try:
        resp = await client.post(
            f"{_ALPACA_BASE_URL}/v2/orders",
            headers=_alpaca_headers(),
            json=payload,
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            order = resp.json()
            order_id: str = order.get("id", "unknown")
            log.info(
                "[ml_alpaca] Order submitted — ticker=%s side=%s notional=$%.2f order_id=%s",
                ticker,
                side,
                notional_usd,
                order_id,
            )
            return order_id
        log.error(
            "[ml_alpaca] Order rejected — status=%d body=%s",
            resp.status_code,
            resp.text[:200],
        )
    except Exception as exc:  # noqa: BLE001
        log.error("[ml_alpaca] Order submission raised: %s", exc)
    return None


# ── Public API ────────────────────────────────────────────────────────────────


async def maybe_execute_live(
    ticker: str,
    signal: SignalResult,
    snapshot: FeaturesSnapshot,
    regime: str,
    db: Any,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Submit a live market order if all Tier 3 conditions are met.

    Gate-level conditions are verified by the orchestrator before this
    function is called.  This function checks:

    1. ``RISEDUAL_LIVE_EXECUTION=1`` env var is set (user opt-in).
    2. Alpaca credentials present.
    3. Confidence >= 70%.
    4. No duplicate open position for this ticker.
    5. Account equity available.

    Parameters
    ----------
    ticker:
        Trading symbol.
    signal:
        :class:`SignalResult` from signal model.
    snapshot:
        Enriched :class:`FeaturesSnapshot`.
    regime:
        Current market regime label.
    db:
        Motor AsyncIOMotorDatabase (used to log the order record).
    http_client:
        Optional shared httpx client.

    Returns
    -------
    str | None
        Alpaca order ID on success, ``None`` otherwise.
    """
    # ── Opt-in guard ─────────────────────────────────────────────────────────
    if not _LIVE_OPT_IN:
        log.debug(
            "[ml_alpaca] RISEDUAL_LIVE_EXECUTION not set — live execution disabled."
        )
        return None

    # ── Credential guard ─────────────────────────────────────────────────────
    if not _ALPACA_API_KEY or not _ALPACA_SECRET_KEY:
        log.warning(
            "[ml_alpaca] ALPACA_API_KEY / ALPACA_SECRET_KEY not configured."
        )
        return None

    # ── Per-signal gate ──────────────────────────────────────────────────────
    if signal.confidence < _MIN_LIVE_CONFIDENCE:
        log.debug(
            "[ml_alpaca] Confidence %.2f < %.2f — skipping live execution for %s.",
            signal.confidence,
            _MIN_LIVE_CONFIDENCE,
            ticker,
        )
        return None

    if regime not in _TRADEABLE_REGIMES:
        log.debug(
            "[ml_alpaca] Regime %r not tradeable — skipping live execution for %s.",
            regime,
            ticker,
        )
        return None

    # ── Execute ──────────────────────────────────────────────────────────────
    _own_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        # Duplicate position check
        existing = await _get_open_position(client, ticker)
        if existing:
            log.info(
                "[ml_alpaca] Open position already exists for %s — skipping.",
                ticker,
            )
            return None

        # Fetch account equity for position sizing
        account = await _get_account(client)
        if not account:
            log.warning(
                "[ml_alpaca] Cannot fetch account equity — aborting for %s.", ticker
            )
            return None

        equity = float(account.get("equity", 0))
        if equity <= 0:
            log.warning("[ml_alpaca] Account equity $0 — aborting order for %s.", ticker)
            return None

        notional = round(equity * _HARD_CAP_FRACTION, 2)
        direction_val = str(signal.direction.value)
        side = "buy" if direction_val == "up" else "sell"

        order_id = await _submit_market_order(
            client=client,
            ticker=ticker,
            side=side,
            notional_usd=notional,
        )

        if order_id:
            # Persist execution record to MongoDB
            try:
                await db["live_orders"].insert_one(
                    {
                        "order_id": order_id,
                        "ticker": ticker,
                        "direction": direction_val,
                        "side": side,
                        "notional_usd": notional,
                        "equity_at_order": equity,
                        "confidence": signal.confidence,
                        "prediction_id": signal.prediction_id,
                        "regime": regime,
                        "submitted_at": datetime.now(timezone.utc),
                        "alpaca_base_url": _ALPACA_BASE_URL,
                        "schema_version": 1,
                    }
                )
            except Exception as db_exc:  # noqa: BLE001
                log.warning(
                    "[ml_alpaca] Order submitted but DB write failed: %s", db_exc
                )

        return order_id

    finally:
        if _own_client:
            await client.aclose()
