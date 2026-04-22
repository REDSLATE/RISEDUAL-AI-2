"""Alpaca Options live adapter — Phase 1 of the multi-broker options
rollout.

Uses the same `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` / `ALPACA_BASE_URL`
credentials the existing equity flow already uses; no new env vars.

Scope choices for Phase 1:
  * Single-leg BTO + STC only. Multi-leg spreads are defined at the
    interface level but not implemented here — the broker's
    `/v2/orders` endpoint already supports multi-leg via a `legs`
    array, so adding them later is additive not disruptive.
  * Market orders by default; limit-price passthrough is wired but
    untested in production. We'll start with market + short TIF
    (day) to keep the risk surface small.
  * No streaming updates. Order state is polled via the same
    `/v2/orders/{id}` endpoint. Websocket subscription is a
    follow-up.

Why raw httpx instead of the `alpaca-trade-api` SDK:
  * Our existing broker flows already use httpx with the same
    APCA-* header pattern — one auth / one retry policy across the
    codebase.
  * The SDK's async story was iffy in 3.x; httpx async is native.
  * We can log failures through `services.structured_log` consistently.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from services.brokers.occ_symbol import parse_occ_symbol
from services.brokers.options_adapter import (
    BrokerOptionsAdapter,
    OptionLeg,
    OptionOrder,
    OptionPosition,
    OptionType,
    OptionsBuyingPower,
    OptionsEnabledStatus,
    OrderSide,
    OrderStatus,
)
from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)

# Map Alpaca's free-form status strings onto our canonical enum.
# Unknown statuses fall through to `OrderStatus.ACCEPTED` which is
# deliberate: an unknown-but-live order is better than mis-reporting
# it as rejected. Polling will eventually land on a terminal state.
_STATUS_MAP: dict[str, OrderStatus] = {
    "new": OrderStatus.NEW,
    "accepted": OrderStatus.ACCEPTED,
    "pending_new": OrderStatus.ACCEPTED,
    "accepted_for_bidding": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "pending_cancel": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED,
    "expired": OrderStatus.EXPIRED,
    "done_for_day": OrderStatus.EXPIRED,
}


def _map_status(raw: str) -> OrderStatus:
    return _STATUS_MAP.get((raw or "").lower(), OrderStatus.ACCEPTED)


def _leg_side_to_alpaca(side: OrderSide) -> str:
    """OCC side → Alpaca side + position_intent mapping.
    Alpaca's options endpoint takes `side` = buy/sell and
    `position_intent` = to_open/to_close."""
    if side in (OrderSide.BUY_TO_OPEN, OrderSide.BUY_TO_CLOSE):
        return "buy"
    return "sell"


def _leg_position_intent(side: OrderSide) -> str:
    if side in (OrderSide.BUY_TO_OPEN, OrderSide.SELL_TO_OPEN):
        return "to_open"
    return "to_close"


def _to_alpaca_symbol(occ_symbol: str) -> str:
    """Translate canonical 21-char OCC (space-padded root) to
    Alpaca's compact variant (no space padding). Canonical roots
    < 6 chars get right-padded with spaces by our builder; Alpaca
    wants the bare root, so strip them.

    Canonical: `AAPL  261218C00200000` (21 chars)
    Alpaca:    `AAPL261218C00200000`   (variable, here 19 chars)
    """
    # Canonical layout is root(6) + YYMMDD(6) + type(1) + strike(8).
    # Strip any trailing spaces from the 6-char root slice only —
    # never touch the numeric tail or we'd corrupt the strike.
    if len(occ_symbol) != 21:
        return occ_symbol  # non-canonical input, pass through unchanged
    return occ_symbol[:6].rstrip() + occ_symbol[6:]


class AlpacaOptionsAdapter(BrokerOptionsAdapter):
    provider = "alpaca"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self._base = (base_url or os.environ.get("ALPACA_BASE_URL", "")).rstrip("/")

        if not (self._api_key and self._secret and self._base):
            # Soft fail — raises only when methods are actually called,
            # so importing the module on an unconfigured environment
            # doesn't break server startup.
            log_warning(logger, {
                "context": "alpaca_options",
                "type": "ConfigMissing",
                "error": "alpaca credentials incomplete",
                "note": "adapter methods will raise on use",
            })

    # ── Low-level request ──────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        if not (self._api_key and self._secret and self._base):
            raise RuntimeError("Alpaca credentials not configured")

        url = f"{self._base}{path}"
        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.request(
                    method, url, headers=headers, json=json_body, params=params,
                )
            except httpx.HTTPError as exc:
                log_error(logger, {
                    "context": "alpaca_options",
                    "type": type(exc).__name__,
                    "error": str(exc),
                    "note": f"{method} {path} network failure",
                })
                raise

        if resp.status_code == 403:
            # Alpaca uses 403 for "not options-enabled" — surface it
            # with a clean message the route layer can forward.
            raise PermissionError(
                f"Alpaca rejected request: {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            log_warning(logger, {
                "context": "alpaca_options",
                "type": "HTTPError",
                "error": f"HTTP {resp.status_code}",
                "note": f"{method} {path} → {resp.text[:200]}",
            })
            raise RuntimeError(
                f"Alpaca {method} {path} failed: "
                f"HTTP {resp.status_code} {resp.text[:200]}"
            )
        return resp.json() if resp.content else {}

    # ── Public adapter surface ─────────────────────────────────────

    async def is_options_enabled(self) -> OptionsEnabledStatus:
        acct = await self._request("GET", "/v2/account")
        approved_level = int(acct.get("options_approved_level") or 0)
        trading_level = int(acct.get("options_trading_level") or 0)
        # Account is options-trading-ready when trading_level ≥ 1.
        # Some accounts have approved_level > 0 but trading_level = 0
        # while a level-up request is pending.
        enabled = trading_level >= 1
        return OptionsEnabledStatus(
            enabled=enabled,
            level=trading_level,
            provider=self.provider,
            details=(
                "options trading active"
                if enabled
                else f"trading_level={trading_level}, approved_level={approved_level}"
            ),
            raw={
                "options_approved_level": approved_level,
                "options_trading_level": trading_level,
                "options_buying_power": acct.get("options_buying_power"),
                "account_blocked": acct.get("account_blocked"),
                "trading_blocked": acct.get("trading_blocked"),
            },
        )

    async def get_options_buying_power(self) -> OptionsBuyingPower:
        acct = await self._request("GET", "/v2/account")
        # Alpaca exposes `options_buying_power` separately from
        # `buying_power` (equity). Fall back to equity buying power
        # if the account predates the split (pre-2024 accounts).
        opt_bp = acct.get("options_buying_power")
        eq_bp = acct.get("buying_power", 0.0)
        return OptionsBuyingPower(
            cash=float(acct.get("cash", 0.0)),
            options_buying_power=float(opt_bp) if opt_bp is not None else float(eq_bp),
            multiplier=100,
            currency=acct.get("currency", "USD"),
        )

    async def get_option_positions(self) -> list[OptionPosition]:
        # `/v2/positions` returns both equity + option; filter on
        # asset_class rather than relying on symbol shape so we pick
        # up multi-leg legs correctly.
        raw = await self._request("GET", "/v2/positions")
        out: list[OptionPosition] = []
        for p in raw if isinstance(raw, list) else []:
            if p.get("asset_class") != "us_option":
                continue
            try:
                # Alpaca returns compact-form symbols; parse_occ_symbol
                # expects the 21-char canonical. Right-pad the root
                # back to 6 chars before parsing.
                raw_sym = p.get("symbol", "")
                # Compact form is root + 15-char suffix (6+1+8). So
                # the root is everything before the last 15 chars.
                if len(raw_sym) >= 15:
                    canonical = raw_sym[:-15].ljust(6) + raw_sym[-15:]
                else:
                    canonical = raw_sym
                parsed = parse_occ_symbol(canonical)
            except ValueError:
                # Broker returned a non-OCC symbol; skip rather than
                # crash the whole listing call.
                continue
            out.append(OptionPosition(
                occ_symbol=p["symbol"],
                underlying=parsed["underlying"],
                strike=parsed["strike"],
                expiration=parsed["expiration"],
                option_type=OptionType(parsed["option_type"]),
                qty=float(p.get("qty", 0)),
                avg_fill_price=float(p.get("avg_entry_price", 0)),
                unrealized_pnl=float(p.get("unrealized_pl", 0)),
            ))
        return out

    async def place_option_order(
        self,
        legs: list[OptionLeg],
        *,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
    ) -> OptionOrder:
        if not legs:
            raise ValueError("legs cannot be empty")

        # Single-leg path (Phase 1). Alpaca's `/v2/orders` single-leg
        # endpoint takes `symbol` + `side` + `position_intent` flat;
        # multi-leg uses `order_class=mleg` + `legs` array. We route
        # on leg count to keep both future-compatible.
        if len(legs) == 1:
            leg = legs[0]
            # Single-leg: Alpaca infers open/close from current
            # position state when `position_intent` is omitted.
            # Recent API versions 422 when the field is sent
            # explicitly — dropping it fixes the regression without
            # losing functionality (you can still cancel an STC via
            # DELETE if you want to reverse an auto-close).
            body = {
                "symbol": _to_alpaca_symbol(leg.occ_symbol),
                "qty": str(int(leg.qty)),
                "side": _leg_side_to_alpaca(leg.side),
                "type": order_type,
                "time_in_force": time_in_force,
            }
            if limit_price is not None and order_type in ("limit", "stop_limit"):
                body["limit_price"] = str(round(float(limit_price), 2))
        else:
            # Reserved for multi-leg (Phase 2+). Fail explicitly so
            # we don't accidentally submit a half-configured order.
            raise NotImplementedError(
                "multi-leg option orders not yet supported"
            )

        raw = await self._request("POST", "/v2/orders", json_body=body)
        return self._parse_order(raw, legs, time_in_force, limit_price)

    async def cancel_option_order(self, order_id: str) -> bool:
        try:
            await self._request("DELETE", f"/v2/orders/{order_id}")
            return True
        except RuntimeError as exc:
            # 422 "already filled" → False, not a raise. Anything
            # else bubbles up so the route returns 500.
            if "422" in str(exc) or "404" in str(exc):
                return False
            raise

    # ── Helpers ────────────────────────────────────────────────────

    def _parse_order(
        self,
        raw: dict[str, Any],
        legs: list[OptionLeg],
        time_in_force: str,
        limit_price: Optional[float],
    ) -> OptionOrder:
        created_raw = raw.get("created_at", "")
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            created = datetime.now(timezone.utc)
        return OptionOrder(
            order_id=str(raw.get("id") or raw.get("client_order_id") or ""),
            status=_map_status(raw.get("status", "")),
            legs=[
                {"occ_symbol": leg.occ_symbol, "qty": leg.qty, "side": leg.side.value}
                for leg in legs
            ],
            qty=int(raw.get("qty", legs[0].qty)),
            filled_qty=int(float(raw.get("filled_qty", 0))),
            limit_price=float(raw["limit_price"]) if raw.get("limit_price") else limit_price,
            time_in_force=time_in_force,
            created_at=created,
            raw=raw,
        )
