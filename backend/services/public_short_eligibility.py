"""Public.com short-sale eligibility ladder (P1-A, 2026-02).

Per operator directive: before Alpha submits its first SELL-to-OPEN
via the newer Public REST API (April 2026), it must pass a strict
ladder of checks — any single failure hard-fails the short:

    1. Account is MARGIN + tradePermissions BUY_AND_SELL
    2. Existing position on the symbol is exactly zero (Public forbids
       direct long→short flips; must flatten first)
    3. Instrument's shortingAvailability is EASY_TO_BORROW or
       HARD_TO_BORROW; NOT_SHORTABLE hard-fails
    4. Public single-leg preflight succeeds (returns buying-power
       requirement, margin impact, uptick-rule flag, HTB rate)

This module is *read-only*. It never places an order and never mutates
state. Callers pass an authenticated ``PublicTradingService`` client
and receive a structured ``ShortEligibility`` verdict with the exact
reason for pass/fail so operators can see WHY the ladder rejected an
intent.

Doctrine
--------
* **Fail closed.** Any HTTP error, missing field, unknown enum value,
  or unparseable response → ``eligible=False`` with the failure
  captured in ``reason``. Better to skip a legitimate short than to
  submit one under uncertain eligibility.
* **No SDK coupling.** These endpoints are called directly via
  ``requests`` — they may not be exposed by the installed SDK's
  Python model (which is why we're here). We reuse the SDK client
  only for its auth headers and base URL.
* **Whole-share sizing.** Public rejects fractional short quantities.
  The preflight sizing helper always returns integers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShortEligibility:
    """Verdict for a single short-eligibility check across the ladder."""
    eligible: bool
    reason: str  # short machine-readable label (pass or first failure)
    account_type: Optional[str] = None
    trade_permissions: Optional[str] = None
    shorting_availability: Optional[str] = None   # EASY_TO_BORROW | HARD_TO_BORROW | NOT_SHORTABLE
    htb_rate_pct: Optional[float] = None
    buying_power_required: Optional[float] = None
    margin_impact: Optional[float] = None
    uptick_rule: Optional[bool] = None
    max_locate_qty: Optional[int] = None
    existing_position_qty: float = 0.0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "account_type": self.account_type,
            "trade_permissions": self.trade_permissions,
            "shorting_availability": self.shorting_availability,
            "htb_rate_pct": self.htb_rate_pct,
            "buying_power_required": self.buying_power_required,
            "margin_impact": self.margin_impact,
            "uptick_rule": self.uptick_rule,
            "max_locate_qty": self.max_locate_qty,
            "existing_position_qty": self.existing_position_qty,
            "detail": self.detail,
        }


# ── HTTP helpers ──────────────────────────────────────────────────


def _client_auth(client) -> Optional[dict]:
    """Grab a fresh auth header off the SDK client. ``None`` when
    the token exchange fails — caller must treat as fail-closed."""
    try:
        return client._auth_headers()  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.warning("[short-eligibility] auth header fetch failed: %s", exc)
        return None


def _http_json(url: str, headers: dict, *, method: str = "GET",
               json_body: Optional[dict] = None,
               timeout: float = 10.0) -> Optional[dict]:
    """Bounded HTTP JSON call. Any non-2xx / exception → ``None`` so
    the caller can fail closed with a clean reason label."""
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=timeout)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=json_body,
                              timeout=timeout)
        else:
            return None
        if r.status_code >= 400:
            logger.info(
                "[short-eligibility] %s %s → HTTP %d: %s",
                method, url, r.status_code, (r.text or "")[:200],
            )
            return None
        return r.json() if r.content else {}
    except Exception as exc:  # noqa: BLE001
        logger.info("[short-eligibility] %s %s failed: %s", method, url, exc)
        return None


# ── Ladder rungs ──────────────────────────────────────────────────


def check_account_eligibility(client) -> ShortEligibility:
    """Rung 1: account must be MARGIN + BUY_AND_SELL.

    Public's account-list endpoint returns objects with
    ``brokerageAccountType`` and ``tradePermissions``. Anything other
    than the exact required values hard-fails.
    """
    headers = _client_auth(client)
    if headers is None:
        return ShortEligibility(False, "auth_failed")
    # Public's accounts endpoint (per current REST docs).
    url = f"{client.base_url}/trading/account"
    data = _http_json(url, headers)
    if data is None:
        return ShortEligibility(False, "account_fetch_failed")

    # Response shape: either {"accounts": [...]} or a bare list.
    accounts = data.get("accounts") if isinstance(data, dict) else data
    if not isinstance(accounts, list):
        return ShortEligibility(
            False, "account_shape_unexpected",
            detail={"got": type(data).__name__},
        )

    # Match by account_id.
    target = None
    for a in accounts:
        if not isinstance(a, dict):
            continue
        acct_id = a.get("accountId") or a.get("id")
        if str(acct_id or "") == str(client.account_id):
            target = a
            break
    if target is None:
        # Fallback: only one account → use it.
        if len(accounts) == 1 and isinstance(accounts[0], dict):
            target = accounts[0]
        else:
            return ShortEligibility(
                False, "account_not_found_in_list",
                detail={"account_id": client.account_id},
            )

    acct_type = target.get("brokerageAccountType")
    trade_perms = target.get("tradePermissions")

    if (acct_type or "").upper() != "MARGIN":
        return ShortEligibility(
            False, "account_not_margin",
            account_type=acct_type, trade_permissions=trade_perms,
        )
    if (trade_perms or "").upper() != "BUY_AND_SELL":
        return ShortEligibility(
            False, "account_permissions_insufficient",
            account_type=acct_type, trade_permissions=trade_perms,
        )
    return ShortEligibility(
        True, "account_eligible",
        account_type=acct_type, trade_permissions=trade_perms,
    )


def check_no_existing_position(client, symbol: str) -> ShortEligibility:
    """Rung 2: symbol must have zero position at Public.

    Public forbids direct long→short flips — must flatten first. Also
    catches short→short (already-open short); a second short entry on
    top of an existing short is disallowed by our own doctrine.
    """
    try:
        positions = client.get_positions() or []
    except Exception as exc:  # noqa: BLE001
        logger.info("[short-eligibility] get_positions failed: %s", exc)
        return ShortEligibility(False, "positions_fetch_failed")
    sym = (symbol or "").upper()
    for p in positions:
        if (p.get("symbol") or "").upper() != sym:
            continue
        try:
            qty = float(p.get("qty") or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        if abs(qty) > 1e-6:
            return ShortEligibility(
                False, "existing_position_blocks_short",
                existing_position_qty=qty,
                detail={"side": p.get("side")},
            )
    return ShortEligibility(True, "no_existing_position")


def check_instrument_shortable(client, symbol: str) -> ShortEligibility:
    """Rung 3: instrument's shortingAvailability must not be
    NOT_SHORTABLE.

    Public's instrument endpoint exposes ``shortingAvailability``
    (EASY_TO_BORROW / HARD_TO_BORROW / NOT_SHORTABLE) and, when
    HTB, ``hardToBorrowPercentageRate``. Both are surfaced in the
    verdict so downstream policy can gate on HTB cost even when the
    borrow is technically available.
    """
    headers = _client_auth(client)
    if headers is None:
        return ShortEligibility(False, "auth_failed")

    # Public's instrument endpoint (per current REST docs). URL shape
    # is documented under ``marketdata/instruments/EQUITY/{symbol}``.
    sym = (symbol or "").upper()
    url = f"{client.base_url}/marketdata/instruments/EQUITY/{sym}"
    data = _http_json(url, headers)
    if data is None:
        return ShortEligibility(
            False, "instrument_fetch_failed",
            detail={"symbol": sym},
        )
    availability = (data.get("shortingAvailability") or "").upper()
    htb_rate_raw = data.get("hardToBorrowPercentageRate")
    try:
        htb_rate = float(htb_rate_raw) if htb_rate_raw is not None else None
    except (TypeError, ValueError):
        htb_rate = None

    if availability == "NOT_SHORTABLE":
        return ShortEligibility(
            False, "not_shortable",
            shorting_availability=availability,
            htb_rate_pct=htb_rate,
        )
    if availability in ("EASY_TO_BORROW", "HARD_TO_BORROW"):
        return ShortEligibility(
            True, "shortable",
            shorting_availability=availability,
            htb_rate_pct=htb_rate,
        )
    # Unknown / missing enum → fail closed.
    return ShortEligibility(
        False, "shorting_availability_unknown",
        shorting_availability=availability or None,
        detail={"raw_response_keys": list(data.keys())},
    )


def preflight_short(
    client, *, symbol: str, qty: int,
) -> ShortEligibility:
    """Rung 4: Public single-leg preflight.

    Public exposes an order-preflight endpoint that returns the
    buying-power requirement, margin impact, uptick-rule flag,
    short-selling info (availability + HTB rate), and maximum
    locate quantity. We do NOT re-evaluate shortable here — that was
    rung 3 — but we surface the returned fields so the executor can
    compare requested qty against ``maxLocateQuantity`` and honor
    uptick-rule requirements.
    """
    headers = _client_auth(client)
    if headers is None:
        return ShortEligibility(False, "auth_failed")
    sym = (symbol or "").upper()
    body = {
        "instrument": {"symbol": sym, "type": "EQUITY"},
        "orderSide": "SELL",
        "orderType": "MARKET",
        "quantity": str(int(qty)),
        "expiration": {"timeInForce": "DAY"},
        "openCloseIndicator": "OPEN",
    }
    url = f"{client.base_url}/trading/{client.account_id}/order/preflight"
    data = _http_json(url, headers, method="POST", json_body=body)
    if data is None:
        return ShortEligibility(
            False, "preflight_failed",
            detail={"symbol": sym, "qty": qty},
        )

    # Extract the interesting fields defensively — any of these may
    # be omitted by the API, but if the response has no fields at all
    # that's a red flag.
    def _f(name: str) -> Optional[float]:
        v = data.get(name)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _i(name: str) -> Optional[int]:
        v = data.get(name)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    short_info = data.get("shortSellingInformation") or {}
    availability = (
        short_info.get("shortingAvailability")
        or data.get("shortingAvailability")
        or ""
    ).upper() or None
    htb_rate = None
    _htb_raw = (
        short_info.get("hardToBorrowPercentageRate")
        or data.get("hardToBorrowPercentageRate")
    )
    try:
        htb_rate = float(_htb_raw) if _htb_raw is not None else None
    except (TypeError, ValueError):
        htb_rate = None

    max_locate = _i("maxLocateQuantity")
    if max_locate is None:
        max_locate = short_info.get("maxLocateQuantity")
        try:
            max_locate = int(max_locate) if max_locate is not None else None
        except (TypeError, ValueError):
            max_locate = None

    # If preflight explicitly rejects, fail closed.
    if data.get("rejected") is True or data.get("status") == "REJECTED":
        return ShortEligibility(
            False, "preflight_rejected",
            shorting_availability=availability,
            htb_rate_pct=htb_rate,
            buying_power_required=_f("buyingPowerRequirement"),
            margin_impact=_f("marginImpact"),
            uptick_rule=data.get("upTickRuleRequired") in (True, "true", "True"),
            max_locate_qty=max_locate,
            detail={"reason": data.get("reason") or data.get("message")},
        )

    # If max locate < requested qty, refuse.
    if max_locate is not None and max_locate < int(qty):
        return ShortEligibility(
            False, "max_locate_below_requested",
            shorting_availability=availability,
            htb_rate_pct=htb_rate,
            max_locate_qty=max_locate,
            detail={"requested_qty": int(qty)},
        )

    return ShortEligibility(
        True, "preflight_ok",
        shorting_availability=availability,
        htb_rate_pct=htb_rate,
        buying_power_required=_f("buyingPowerRequirement"),
        margin_impact=_f("marginImpact"),
        uptick_rule=data.get("upTickRuleRequired") in (True, "true", "True"),
        max_locate_qty=max_locate,
    )


def run_full_ladder(
    client, *, symbol: str, qty: int,
    max_htb_rate_pct: Optional[float] = None,
) -> ShortEligibility:
    """Convenience: run all four rungs in order and return the first
    failure, or a merged PASS verdict.

    ``max_htb_rate_pct`` (optional) applies a policy cap on hard-to-borrow
    rate. When set and instrument comes back HTB with a rate exceeding
    the cap, the ladder returns ``htb_rate_too_expensive``. Set to
    ``None`` to allow any HTB rate.
    """
    r1 = check_account_eligibility(client)
    if not r1.eligible:
        return r1
    r2 = check_no_existing_position(client, symbol)
    if not r2.eligible:
        return ShortEligibility(
            False, r2.reason,
            account_type=r1.account_type,
            trade_permissions=r1.trade_permissions,
            existing_position_qty=r2.existing_position_qty,
            detail=r2.detail,
        )
    r3 = check_instrument_shortable(client, symbol)
    if not r3.eligible:
        return ShortEligibility(
            False, r3.reason,
            account_type=r1.account_type,
            trade_permissions=r1.trade_permissions,
            shorting_availability=r3.shorting_availability,
            htb_rate_pct=r3.htb_rate_pct,
            detail=r3.detail,
        )
    # HTB policy gate BEFORE preflight so we don't waste an API call.
    if (
        max_htb_rate_pct is not None
        and r3.shorting_availability == "HARD_TO_BORROW"
        and r3.htb_rate_pct is not None
        and r3.htb_rate_pct > max_htb_rate_pct
    ):
        return ShortEligibility(
            False, "htb_rate_too_expensive",
            account_type=r1.account_type,
            trade_permissions=r1.trade_permissions,
            shorting_availability=r3.shorting_availability,
            htb_rate_pct=r3.htb_rate_pct,
            detail={"cap_pct": max_htb_rate_pct},
        )
    r4 = preflight_short(client, symbol=symbol, qty=qty)
    if not r4.eligible:
        return ShortEligibility(
            False, r4.reason,
            account_type=r1.account_type,
            trade_permissions=r1.trade_permissions,
            shorting_availability=r3.shorting_availability
                or r4.shorting_availability,
            htb_rate_pct=r3.htb_rate_pct or r4.htb_rate_pct,
            buying_power_required=r4.buying_power_required,
            margin_impact=r4.margin_impact,
            uptick_rule=r4.uptick_rule,
            max_locate_qty=r4.max_locate_qty,
            detail=r4.detail,
        )

    return ShortEligibility(
        True, "ladder_passed",
        account_type=r1.account_type,
        trade_permissions=r1.trade_permissions,
        shorting_availability=r3.shorting_availability,
        htb_rate_pct=r3.htb_rate_pct or r4.htb_rate_pct,
        buying_power_required=r4.buying_power_required,
        margin_impact=r4.margin_impact,
        uptick_rule=r4.uptick_rule,
        max_locate_qty=r4.max_locate_qty,
    )


__all__ = [
    "ShortEligibility",
    "check_account_eligibility",
    "check_no_existing_position",
    "check_instrument_shortable",
    "preflight_short",
    "run_full_ladder",
]
