"""Tradier Options adapter — Phase 2.

Today this adapter's primary job is **real bid/ask quotes** for the
Smart Order Router. Full order-submission is deferred to a later
phase (Tradier's order API has a distinct multi-leg envelope that
deserves dedicated test coverage). Until then the adapter:

  * `is_options_enabled` — `True` only if `TRADIER_API_TOKEN` is set.
    Tradier doesn't expose a per-user options-level field over their
    public account API; we rely on the token-provisioned scope.
  * `try_get_spread` — hits Tradier's `/v1/markets/options/quotes`
    endpoint with the OCC symbol, returns `ask - bid` in dollars.
    Used by `SmartOrderRouter._estimate_spread` to out-rank brokers
    that only return the sentinel fallback.
  * All other adapter methods raise `BrokerNotImplementedError` so
    the route layer cleanly maps to 501 until we wire the full
    account/orders API.

Env vars (all read at instantiation time, never cached across
instances — matches the per-call factory pattern the registry uses):

  TRADIER_API_TOKEN        required for any real call
  TRADIER_BASE_URL         defaults to Tradier's sandbox host; set
                           this to `https://api.tradier.com` for
                           production live data.

The sandbox host returns delayed quotes with no auth cost, good for
dev. Switching to the production host requires the paid market-data
add-on on the Tradier account.

Why a separate adapter instead of extending Alpaca:
  * Tradier runs its own wire format (JSON shape + symbol casing)
    distinct from Alpaca's; pushing broker-specific translation
    into the base class pollutes the interface.
  * This keeps `smart_router._estimate_spread` broker-agnostic — it
    just calls `try_get_spread` on whoever's in the registry.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from services.brokers.options_adapter import (
    BrokerNotImplementedError,
    BrokerOptionsAdapter,
    OptionLeg,
    OptionOrder,
    OptionPosition,
    OptionsBuyingPower,
    OptionsEnabledStatus,
)
from services.structured_log import log_warning

logger = logging.getLogger(__name__)

DEFAULT_TRADIER_BASE = "https://sandbox.tradier.com"


def _tradier_symbol(occ_canonical: str) -> str:
    """Tradier accepts the compact OCC form (no space-padding in
    the root) — same convention as Alpaca. Strip trailing spaces
    from the 6-char root slice only.

    Canonical: `AAPL  261218C00200000` (21 chars)
    Tradier:   `AAPL261218C00200000`   (variable, here 19 chars)
    """
    if len(occ_canonical) != 21:
        return occ_canonical
    return occ_canonical[:6].rstrip() + occ_canonical[6:]


async def fetch_tradier_option_quote(
    occ_symbol: str,
    *,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> Optional[dict[str, Any]]:
    """Fetch a single option quote from Tradier. Returns a dict with
    `bid`, `ask`, `last`, `volume`, `open_interest`, `mid`, `spread`
    — or `None` if the endpoint is unreachable or the symbol isn't
    found. Never raises on network/HTTP errors (the smart router's
    fallback path depends on this).

    The Tradier response shape has edge cases:
      * `quotes` may be `{"quotes": "null"}` (literal string!) when
        the symbol doesn't resolve — we treat as missing.
      * `quote` is a single dict for one symbol, a list for many.
      * `bid` / `ask` can be `0` for contracts with no recent quotes
        (pre-market, illiquid strikes) — we still return them but
        the resulting spread is the full ask which is usually a
        sentinel-level score anyway.
    """
    tok = token or os.environ.get("TRADIER_API_TOKEN", "")
    base = (base_url or os.environ.get("TRADIER_BASE_URL") or DEFAULT_TRADIER_BASE).rstrip("/")
    if not tok:
        return None

    url = f"{base}/v1/markets/quotes"
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/json",
    }
    params = {"symbols": _tradier_symbol(occ_symbol), "greeks": "false"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        log_warning(logger, {
            "context": "tradier_quote",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": f"quote fetch failed for {occ_symbol}",
        })
        return None

    if resp.status_code >= 400:
        log_warning(logger, {
            "context": "tradier_quote",
            "type": "HTTPError",
            "error": f"HTTP {resp.status_code}",
            "note": f"{occ_symbol} → {resp.text[:200]}",
        })
        return None

    try:
        body = resp.json()
    except ValueError:
        return None

    # Tradier nests as { "quotes": { "quote": {...} | [...] } }.
    quotes_env = body.get("quotes") if isinstance(body, dict) else None
    if not isinstance(quotes_env, dict):
        return None
    quote = quotes_env.get("quote")
    if isinstance(quote, list):
        # One symbol requested; take the first.
        quote = quote[0] if quote else None
    if not isinstance(quote, dict):
        return None

    bid = float(quote.get("bid") or 0)
    ask = float(quote.get("ask") or 0)
    mid = round((bid + ask) / 2.0, 4) if (bid or ask) else 0.0
    spread = max(round(ask - bid, 4), 0.0) if ask >= bid else 0.0

    return {
        "bid": bid,
        "ask": ask,
        "last": float(quote.get("last") or 0),
        "volume": int(quote.get("volume") or 0),
        "open_interest": int(quote.get("open_interest") or 0),
        "mid": mid,
        "spread": spread,
        "symbol": quote.get("symbol"),
    }


async def fetch_tradier_option_chain(
    underlying: str,
    expiration: str,
    *,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 15.0,
) -> Optional[list[dict[str, Any]]]:
    """Fetch the full option chain for an underlying at a given expiration.

    Returns a list of contract dicts (calls + puts, one row per strike/type)
    with normalised keys:
        occ_symbol, underlying, expiry, strike, contract_type ("call"|"put"),
        bid, ask, last, volume, open_interest, implied_volatility

    Returns ``None`` if the token is missing, the chain is empty, or the
    endpoint is unreachable. Errors are swallowed (universe warmers rely
    on ``None`` to fall back to yfinance).

    ``expiration`` must be YYYY-MM-DD.
    """
    tok = token or os.environ.get("TRADIER_API_TOKEN", "")
    base = (base_url or os.environ.get("TRADIER_BASE_URL") or DEFAULT_TRADIER_BASE).rstrip("/")
    if not tok:
        return None

    url = f"{base}/v1/markets/options/chains"
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    params = {"symbol": underlying.upper(), "expiration": expiration, "greeks": "true"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        log_warning(logger, {
            "context": "tradier_chain",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": f"chain fetch failed for {underlying} {expiration}",
        })
        return None

    if resp.status_code >= 400:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None

    options_env = body.get("options") if isinstance(body, dict) else None
    if not isinstance(options_env, dict):
        return None
    rows = options_env.get("option")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return None

    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        bid = float(r.get("bid") or 0)
        ask = float(r.get("ask") or 0)
        opt_type = str(r.get("option_type") or "").lower()
        if opt_type not in ("call", "put"):
            continue
        greeks = r.get("greeks") if isinstance(r.get("greeks"), dict) else {}
        iv = greeks.get("mid_iv") or greeks.get("bid_iv") or greeks.get("ask_iv")
        try:
            iv_val = float(iv) if iv is not None else None
        except (TypeError, ValueError):
            iv_val = None
        out.append({
            "occ_symbol": r.get("symbol") or "",
            "underlying": underlying.upper(),
            "expiry": expiration,
            "strike": float(r.get("strike") or 0),
            "contract_type": opt_type,
            "bid": bid,
            "ask": ask,
            "last": float(r.get("last") or 0),
            "volume": int(r.get("volume") or 0),
            "open_interest": int(r.get("open_interest") or 0),
            "implied_volatility": iv_val,
        })
    return out or None


async def fetch_tradier_expirations(
    underlying: str,
    *,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> Optional[list[str]]:
    """List available expirations for an underlying (YYYY-MM-DD strings,
    ascending). ``None`` if token missing or endpoint unreachable.
    """
    tok = token or os.environ.get("TRADIER_API_TOKEN", "")
    base = (base_url or os.environ.get("TRADIER_BASE_URL") or DEFAULT_TRADIER_BASE).rstrip("/")
    if not tok:
        return None

    url = f"{base}/v1/markets/options/expirations"
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    params = {"symbol": underlying.upper(), "includeAllRoots": "true"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None

    env = body.get("expirations") if isinstance(body, dict) else None
    if not isinstance(env, dict):
        return None
    dates = env.get("date")
    if isinstance(dates, str):
        dates = [dates]
    if not isinstance(dates, list):
        return None
    return sorted(str(d) for d in dates if isinstance(d, str))


class TradierOptionsAdapter(BrokerOptionsAdapter):
    """Tradier adapter — Phase 2 surface is quote-only.

    Order placement is deferred so we can write focused tests around
    Tradier's distinct multi-leg order envelope in a follow-up patch.
    Until then the adapter self-reports `enabled=true` (with a
    `"quote-only"` detail string) only when a token is set, so the
    Smart Order Router picks it up in parallel probes but still
    surfaces a clean 501 on attempted order submission.
    """

    provider = "tradier"

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._token = token or os.environ.get("TRADIER_API_TOKEN", "")
        self._base = (base_url or os.environ.get("TRADIER_BASE_URL") or DEFAULT_TRADIER_BASE).rstrip("/")

    async def is_options_enabled(self) -> OptionsEnabledStatus:
        # No token → not usable. Don't probe the network.
        if not self._token:
            return OptionsEnabledStatus(
                enabled=False, level=0, provider=self.provider,
                details="TRADIER_API_TOKEN not set",
            )
        # Token set → quote-mode only. Phase 2 doesn't place orders.
        # `level=0` signals "no order routing yet" so the UI can
        # render a "quotes only" badge until phase 3.
        return OptionsEnabledStatus(
            enabled=True, level=0, provider=self.provider,
            details="quote-only (spread source for smart router)",
        )

    async def try_get_spread(self, occ_symbol: str) -> Optional[float]:
        """Return the bid-ask spread in dollars for the given OCC
        symbol. Returns `None` if the quote couldn't be fetched —
        the smart router's sentinel path handles that.
        """
        quote = await fetch_tradier_option_quote(
            occ_symbol, token=self._token, base_url=self._base,
        )
        if not quote:
            return None
        spread = quote.get("spread")
        return float(spread) if spread is not None else None

    # ── Unimplemented surface ─────────────────────────────────────

    def _not_impl(self, what: str) -> BrokerNotImplementedError:
        return BrokerNotImplementedError(
            f"tradier options {what} not yet implemented "
            "(Phase 2 ships quote-only; full order routing in a later phase)"
        )

    async def get_options_buying_power(self) -> OptionsBuyingPower:
        raise self._not_impl("buying power")

    async def get_option_positions(self) -> list[OptionPosition]:
        raise self._not_impl("positions")

    async def place_option_order(
        self,
        legs: list[OptionLeg],
        *,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
    ) -> OptionOrder:
        raise self._not_impl("order placement")

    async def cancel_option_order(self, order_id: str) -> bool:
        raise self._not_impl("order cancel")
