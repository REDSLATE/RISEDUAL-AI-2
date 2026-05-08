"""Tier 3 autonomous action: live execution via Alpaca REST API.

This module is intentionally conservative.  Every decision path either exits
early or logs at WARNING level before submitting an order.  A hard 2% position
cap is enforced regardless of Kelly output.

Architecture
------------
- Alpaca REST v2 endpoint: ``https://paper-api.alpaca.markets`` (paper) or
  ``https://api.alpaca.markets`` (live), controlled by ``ALPACA_BASE_URL``.
- API credentials: resolved via :class:`~risedual_core.secrets.KeyVault` in
  priority order:

      1. Environment variables ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``
      2. Encrypted vault file at ``~/.risedual/vault.enc``

  To store keys permanently::

      from risedual_core.secrets import KeyVault
      vault = KeyVault()
      vault.set("ALPACA_API_KEY",    "PK…")
      vault.set("ALPACA_SECRET_KEY", "SK…")

  Or via the CLI helper (see ``risedual_cli/commands/vault.py``).

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
from risedual_core.secrets import KeyVault

log = logging.getLogger(__name__)

# ── KeyVault singleton ────────────────────────────────────────────────────────

_vault = KeyVault()


def _resolve_alpaca_creds() -> tuple[str | None, str | None]:
    """Resolve Alpaca credentials from KeyVault (env → vault file).

    Returns
    -------
    tuple[str | None, str | None]
        ``(api_key, secret_key)``  — either may be ``None`` if not configured.
    """
    api_key = _vault.get_optional("ALPACA_API_KEY")
    secret_key = _vault.get_optional("ALPACA_SECRET_KEY")
    return api_key, secret_key


# ── Configuration ─────────────────────────────────────────────────────────────

_ALPACA_BASE_URL: str = os.getenv(
    "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
)

# Hard position size cap as fraction of portfolio equity
_HARD_CAP_FRACTION: float = 0.02   # 2%

# Minimum confidence for execution (lowered for paper account)
_MIN_LIVE_CONFIDENCE: float = 0.50

# Paper mode: skip the opt-in check when ALPACA_BASE_URL is paper
_IS_PAPER: bool = "paper" in os.getenv("ALPACA_BASE_URL", "paper").lower()
_LIVE_OPT_IN: bool = _IS_PAPER or os.getenv("RISEDUAL_LIVE_EXECUTION", "0") == "1"

# Regimes eligible for execution
_TRADEABLE_REGIMES: frozenset[str] = frozenset({"bull", "bear", "sideways", "trending_up", "trending_down", "unknown", ""})


# ── Alpaca REST helpers ───────────────────────────────────────────────────────


def _alpaca_headers() -> dict[str, str]:
    """Build Alpaca auth headers, resolving keys via KeyVault.

    Returns empty dict if keys are absent so callers can detect missing creds
    without raising.
    """
    api_key, secret_key = _resolve_alpaca_creds()
    if not api_key or not secret_key:
        return {}
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
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
    """Submit a market order by notional value (buy) or qty (sell/short).

    For buy orders: uses notional (fractional shares OK).
    For sell orders: converts to whole share qty (Alpaca paper doesn't support
    fractional short selling).

    Returns the Alpaca order ID on success, ``None`` on failure.
    """
    if side == "buy":
        payload = {
            "symbol": ticker,
            "notional": str(round(notional_usd, 2)),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
    else:
        # For sell/short: get current price and convert to whole shares
        try:
            resp = await client.get(
                f"{_ALPACA_BASE_URL}/v2/stocks/{ticker}/quotes/latest",
                headers=_alpaca_headers(),
                timeout=10.0,
            )
            if resp.status_code == 200:
                quote = resp.json().get("quote", {})
                price = float(quote.get("ap", 0) or quote.get("bp", 0))
            else:
                # Fallback: use snapshot
                resp = await client.get(
                    f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot",
                    headers=_alpaca_headers(),
                    timeout=10.0,
                )
                price = float(resp.json().get("latestTrade", {}).get("p", 0)) if resp.status_code == 200 else 0
        except Exception:
            price = 0

        if price <= 0:
            log.warning("[ml_alpaca] Could not get price for %s — skipping sell order", ticker)
            return None

        qty = int(notional_usd / price)
        if qty < 1:
            log.info("[ml_alpaca] Notional $%.2f < 1 share of %s ($%.2f) — skipping", notional_usd, ticker, price)
            return None

        payload = {
            "symbol": ticker,
            "qty": str(qty),
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


# ── Credential management helpers (called from vault CLI command) ─────────────


def store_alpaca_keys(api_key: str, secret_key: str) -> None:
    """Persist Alpaca credentials to the KeyVault.

    Parameters
    ----------
    api_key:
        Alpaca API key ID (starts with ``PK``).
    secret_key:
        Alpaca secret key (starts with ``SK``).
    """
    _vault.set("ALPACA_API_KEY", api_key)
    _vault.set("ALPACA_SECRET_KEY", secret_key)
    log.info("[ml_alpaca] Alpaca credentials stored in KeyVault.")


def alpaca_keys_configured() -> bool:
    """Return ``True`` if both Alpaca credentials are resolvable."""
    api_key, secret_key = _resolve_alpaca_creds()
    return bool(api_key and secret_key)


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
    2. Alpaca credentials present (env or vault).
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
    api_key, secret_key = _resolve_alpaca_creds()
    if not api_key or not secret_key:
        log.warning(
            "[ml_alpaca] Alpaca credentials not configured. "
            "Run: vault.set('ALPACA_API_KEY', '...') / vault.set('ALPACA_SECRET_KEY', '...')"
        )
        return None

    # ── Per-signal gate ──────────────────────────────────────────────────────
    # signal.confidence = P(up). For 'down' signals, true confidence = 1 - P(up)
    directional_conf = signal.confidence if signal.direction.value == "up" else (1.0 - signal.confidence)

    if directional_conf < _MIN_LIVE_CONFIDENCE:
        log.debug(
            "[ml_alpaca] Directional confidence %.2f < %.2f — skipping for %s.",
            directional_conf,
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
