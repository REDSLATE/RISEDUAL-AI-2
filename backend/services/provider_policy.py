"""Decision-type-based provider policy for market data.

The old approach was a flat "priority" list — every caller (quote,
daily bars, news, account state) walked the same chain. That is
wrong for live execution. A broker knows what *we* can trade, at
what *live* price, with what buying power, right now. Vendor data
(Alpha Vantage / Finnhub / Polygon) is the right source for
history, news and fallback, but must NEVER outrank the broker on
execution-time truth.

This module encodes the policy:

* ``PROVIDER_POLICY`` — ordered list of provider *categories* per
  decision type (``"broker"``, ``"finnhub"``, ``"polygon"``,
  ``"alphavantage"``). Downstream code should route through
  :func:`get_provider_chain` rather than reading pool priorities.

* :func:`fetch_execution_quote` — the *only* helper allowed on the
  live-execution path. It applies two hard gates:

    1. **Freshness** — a broker quote is trusted only when
       ``age_seconds <= EXECUTION_QUOTE_FRESHNESS_SECS`` (default
       5s). Older than that, we fall back to vendor and mark the
       execution NOT allowed unless the vendor is *also* fresh AND
       the broker is legitimately absent (e.g. a symbol the broker
       doesn't cover).

    2. **Disagreement** — when we have both a broker and a vendor
       price for the same symbol and they disagree by more than
       ``EXECUTION_QUOTE_MAX_DRIFT_BPS`` (default 50 bps), the
       result is flagged ``data_conflict=True`` and auto-execution
       is blocked. Upstream must re-quote the broker (or defer)
       before submitting.

Vendors are *not* removed by this policy. They keep serving
research, history and news, plus they act as freshness/drift
witnesses against the broker on live quotes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging
import os
import time

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  POLICY TABLE
# ─────────────────────────────────────────────
#
# Provider *categories* per decision type. Concrete provider
# entries (with API keys, priorities, etc.) still live in
# ``pool_config.get_market_data_provider_pool`` — this table just
# tells the caller in what order to walk them for a given decision.
#
# Adding a new decision type: pick the categories that make sense
# and drop them in. Anything not listed here will fall back to the
# execution-quote chain, so a typo can't silently strip the broker
# off the front of the list.
PROVIDER_POLICY: dict[str, list[str]] = {
    # Live-execution truth. Broker first, vendors as freshness /
    # drift witnesses AND coverage fallback.
    "execution_quote": ["broker", "finnhub", "polygon", "alphavantage"],
    # Account-level truth. Only the broker knows how much cash /
    # buying power we actually have. Never fall back.
    "account_state": ["broker"],
    "positions": ["broker"],
    "open_orders": ["broker"],
    # Intraday regime uses broker quote first (aligned with what
    # we'll actually trade), vendor bars as fallback when the
    # broker is offline.
    "intraday_regime": ["broker", "finnhub", "polygon"],
    # Research / history / news — vendors first, broker is
    # sometimes used as a "witness" only.
    "daily_history": ["polygon", "alphavantage", "finnhub"],
    "news": ["finnhub", "alphavantage", "polygon"],
}


def _env_int(key: str, default: int) -> int:
    """Read an integer env var, returning ``default`` when unset
    or malformed. We keep the parse tolerant so a rogue value in
    the environment doesn't break the whole trade path."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[provider_policy] bad int for %s=%r; using %d", key, raw, default)
        return default


# Freshness ceiling for a broker quote on the execution path.
# Beyond this age we don't trust the price for sizing/submission.
EXECUTION_FRESHNESS_SECS: int = _env_int("EXECUTION_QUOTE_FRESHNESS_SECS", 5)
# Disagreement ceiling between broker and vendor in basis points.
# Above this we call the two feeds "conflicting" and block auto-execution.
EXECUTION_MAX_DRIFT_BPS: int = _env_int("EXECUTION_QUOTE_MAX_DRIFT_BPS", 50)


def get_provider_chain(decision_type: str) -> list[str]:
    """Return the ordered provider category chain for a decision type.

    Unknown types default to the execution-quote chain (broker
    first, vendors after) so a typo can't accidentally drop the
    broker from a hot path.
    """
    return list(PROVIDER_POLICY.get(decision_type) or PROVIDER_POLICY["execution_quote"])


# ─────────────────────────────────────────────
#  DRIFT CHECK
# ─────────────────────────────────────────────
def compute_disagreement_bps(reference: float, other: float) -> float:
    """Return the absolute basis-points drift of ``other`` from
    ``reference``. Returns 0.0 when the reference is non-positive
    (undefined) so callers can safely compare against a threshold.
    """
    if reference <= 0:
        return 0.0
    return abs(other - reference) / reference * 10_000.0


# ─────────────────────────────────────────────
#  RESULT TYPE
# ─────────────────────────────────────────────
@dataclass
class ExecutionQuote:
    """Result of :func:`fetch_execution_quote`.

    ``execution_allowed`` is the single gate the trade path must
    check. Everything else on this object is diagnostic.
    """
    symbol: str
    # The price we intend to execute against. Always the broker's
    # last price when the broker gate passes, otherwise the vendor's.
    price: Optional[float] = None
    # Which feed produced ``price`` — "broker" / "vendor:finnhub" / etc.
    source: Optional[str] = None
    # Age of ``price`` in seconds at the moment the gate ran.
    age_seconds: Optional[float] = None
    # Independent readings — useful for the drift check and for
    # diagnostics even when only one source responded.
    broker_price: Optional[float] = None
    broker_age_seconds: Optional[float] = None
    vendor_price: Optional[float] = None
    vendor_source: Optional[str] = None
    vendor_age_seconds: Optional[float] = None
    disagreement_bps: Optional[float] = None
    # When True the broker and the vendor disagree beyond the drift
    # ceiling. The trade path must NOT auto-execute; it should
    # re-quote the broker or defer.
    data_conflict: bool = False
    execution_allowed: bool = False
    reason: Optional[str] = None
    raw: dict = field(default_factory=dict)


# ─────────────────────────────────────────────
#  EXECUTION QUOTE
# ─────────────────────────────────────────────
def _quote_age_seconds(quote: Optional[dict]) -> Optional[float]:
    """Compute how old a quote is in seconds.

    ``market_data_pool`` stamps every quote with ``fetched_at``
    (unix seconds) when it comes off the wire. If the stamp is
    missing we treat the quote as "unknown age" — the callers
    then have to decide whether to trust it (freshness gate
    conservatively rejects unknowns for the broker path).
    """
    if not isinstance(quote, dict):
        return None
    ts = quote.get("fetched_at")
    if ts is None:
        return None
    try:
        return max(0.0, time.time() - float(ts))
    except (TypeError, ValueError):
        return None


def _quote_price(quote: Optional[dict]) -> Optional[float]:
    """Extract a positive price from a quote dict, else None."""
    if not isinstance(quote, dict):
        return None
    for k in ("price", "last", "mark"):
        v = quote.get(k)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            return fv
    return None


async def fetch_execution_quote(symbol: str) -> ExecutionQuote:
    """Fetch a live quote and gate it for execution.

    See the module docstring for the policy. Callers on the live
    trade path should use this instead of :func:`market_quote`.
    """
    # Late imports to avoid pulling ``market_data_pool`` at module
    # import time (it wires an httpx client + pool on import).
    from services.market_data_pool import (  # noqa: PLC0415
        fetch_broker_quote,
        fetch_vendor_quote,
    )

    symbol_u = (symbol or "").strip().upper()
    result = ExecutionQuote(symbol=symbol_u)

    # ── Broker: authoritative source of tradable-price truth
    try:
        broker_q = await fetch_broker_quote(symbol_u)
    except Exception as exc:  # noqa: BLE001
        logger.info("[provider_policy] broker quote failed for %s: %s", symbol_u, exc)
        broker_q = None

    broker_price = _quote_price(broker_q)
    broker_age = _quote_age_seconds(broker_q)
    result.broker_price = broker_price
    result.broker_age_seconds = broker_age

    # ── Vendor: independent witness + coverage fallback
    try:
        vendor_q = await fetch_vendor_quote(symbol_u)
    except Exception as exc:  # noqa: BLE001
        logger.info("[provider_policy] vendor quote failed for %s: %s", symbol_u, exc)
        vendor_q = None

    vendor_price = _quote_price(vendor_q)
    vendor_age = _quote_age_seconds(vendor_q)
    vendor_src = None
    if isinstance(vendor_q, dict):
        vendor_src = vendor_q.get("provider_name") or vendor_q.get("source")
    result.vendor_price = vendor_price
    result.vendor_age_seconds = vendor_age
    result.vendor_source = vendor_src

    # ── Drift check (only when we have two independent readings)
    if broker_price and vendor_price:
        drift = compute_disagreement_bps(broker_price, vendor_price)
        result.disagreement_bps = drift
        if drift > EXECUTION_MAX_DRIFT_BPS:
            result.data_conflict = True

    # ── Pick a price + decide if execution is allowed
    if broker_price is not None:
        # Broker quote wins on the execution path when fresh.
        result.price = broker_price
        result.source = "broker"
        result.age_seconds = broker_age
        broker_fresh = broker_age is not None and broker_age <= EXECUTION_FRESHNESS_SECS
        if not broker_fresh:
            result.execution_allowed = False
            result.reason = (
                f"broker_quote_stale (age={broker_age}s > "
                f"{EXECUTION_FRESHNESS_SECS}s)"
            )
        elif result.data_conflict:
            result.execution_allowed = False
            result.reason = (
                f"data_conflict broker={broker_price} vendor={vendor_price} "
                f"drift={result.disagreement_bps:.1f}bps"
            )
        else:
            result.execution_allowed = True
            result.reason = "broker_confirmed"
    elif vendor_price is not None:
        # Broker is silent — treat as "symbol not in broker coverage
        # or broker down". We surface the vendor price so callers
        # can see it, but auto-execute is BLOCKED. The trade path
        # must re-quote the broker before submitting.
        result.price = vendor_price
        result.source = f"vendor:{vendor_src or 'unknown'}"
        result.age_seconds = vendor_age
        result.execution_allowed = False
        result.reason = "no_broker_price"
    else:
        result.execution_allowed = False
        result.reason = "no_price"

    return result


__all__ = [
    "PROVIDER_POLICY",
    "EXECUTION_FRESHNESS_SECS",
    "EXECUTION_MAX_DRIFT_BPS",
    "ExecutionQuote",
    "compute_disagreement_bps",
    "fetch_execution_quote",
    "get_provider_chain",
]
