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
from typing import Optional, Any
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
# Maximum age of a vendor quote we'll accept as a credible drift
# witness. A stale witness cannot credibly disagree with a fresh
# broker quote.
EXECUTION_VENDOR_MAX_AGE_SECS: int = _env_int(
    "EXECUTION_VENDOR_MAX_AGE_SECS", 30,
)

# Providers that return quotes on a SILENT delay (Alpha Vantage
# free tier ships a 15-min-delayed price stamped with a fresh
# receive time). ``fetched_at`` alone can't catch these because
# the delay is upstream of our wire. We hard-code them so their
# quotes never veto a live broker price. They can still serve as
# a FALLBACK when the broker is silent, but not as a drift witness.
# Match on the provider category (``provider`` in the pool config),
# not the ``name`` field, so a rename doesn't accidentally re-arm
# their veto power.
DELAYED_QUOTE_PROVIDERS: frozenset[str] = frozenset({"alphavantage"})


# ─────────────────────────────────────────────
#  ADAPTIVE, SOURCE-RELATIVE FRESHNESS (2026-06)
# ─────────────────────────────────────────────
# The flat ``EXECUTION_FRESHNESS_SECS`` treats every broker + session
# the same. That's wrong: a broker's normal quote lag differs by feed
# (REST vs push) and a quote is naturally OLD overnight (no trades).
# When enabled, the gate sizes its tolerance to the broker's MEASURED
# p95 lag and the current session, and gates on the broker's OWN tick
# timestamp (real staleness) rather than our receive time.
#
# SAFETY: defaults OFF. While off, behaviour is byte-for-byte the
# legacy gate (fetched_at age vs 5s). Measurement (profile recording)
# runs regardless — it never affects a decision.
ADAPTIVE_FRESHNESS_ENABLED: bool = os.environ.get(
    "EXECUTION_ADAPTIVE_FRESHNESS", "0",
).strip().lower() in ("1", "true", "yes", "on")

# Absolute floor for CORE/RTH even when the measured p95 is tiny.
EXECUTION_FRESHNESS_MIN_SECS: int = _env_int("EXECUTION_QUOTE_FRESHNESS_MIN_SECS", 2)

# Wide age BACKSTOPS for sessions where quotes are legitimately old
# (no trades happening). In these sessions age is not the primary
# gate — spread + drift + session context do the work — so the age
# ceiling is deliberately loose. CRYPTO stays tight (24/7 venue).
_SESSION_AGE_CEILING: dict[str, float] = {
    "CORE": 0.0,          # 0 => use adaptive max(min, p95*2)
    "PREMARKET": _env_int("EXECUTION_FRESH_PREMARKET_SECS", 120),
    "AFTER_HOURS": _env_int("EXECUTION_FRESH_AFTERHOURS_SECS", 120),
    "OVERNIGHT": _env_int("EXECUTION_FRESH_OVERNIGHT_SECS", 600),
    "CRYPTO": _env_int("EXECUTION_FRESH_CRYPTO_SECS", 3),
}


def execution_session(symbol: Optional[str] = None) -> str:
    """Current session bucket for freshness policy: CORE / PREMARKET /
    AFTER_HOURS / OVERNIGHT (equities) or CRYPTO. Maps the NYSE session
    phase from :mod:`services.alpha_session_state`."""
    try:
        from services.alpha_session_state import get_session_state  # noqa: PLC0415
        phase = get_session_state().get("phase")
    except Exception:  # noqa: BLE001
        return "CORE"
    return {
        "regular": "CORE",
        "pre_market": "PREMARKET",
        "after_hours": "AFTER_HOURS",
        "closed_overnight": "OVERNIGHT",
        "closed_weekend": "OVERNIGHT",
        "closed_holiday": "OVERNIGHT",
    }.get(phase, "CORE")


def _age_from_timestamp(ts: Any) -> Optional[float]:
    """Age in seconds from a broker tick timestamp that may be epoch
    seconds, epoch millis, or an ISO-8601 string."""
    if ts is None:
        return None
    now = time.time()
    try:
        v = float(ts)
        if v > 1e12:
            v /= 1000.0
        if v > 1e9:
            return max(0.0, now - v)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        s = str(ts).replace("Z", "+00:00")
        dt = _dt.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return max(0.0, now - dt.timestamp())
    except (TypeError, ValueError):
        return None


def _broker_tick_age(quote: Optional[dict]) -> Optional[float]:
    """TRUE staleness of a broker quote: age from the broker's own
    last-tick timestamp when present (Public.com ships ISO
    ``timestamp``), else ``fetched_at`` (MooMoo already stamps
    ``fetched_at`` with its wire time)."""
    if not isinstance(quote, dict):
        return None
    age = _age_from_timestamp(quote.get("timestamp"))
    if age is not None:
        return age
    return _quote_age_seconds(quote)


def compute_freshness_limit(broker: str, session: str) -> float:
    """Source- and session-relative freshness ceiling (seconds).

    ``max(configured_minimum, broker_p95_lag * 2, session_floor)`` for
    CORE; a wide session backstop for pre/after/overnight; legacy 5s
    when the adaptive flag is off.
    """
    if not ADAPTIVE_FRESHNESS_ENABLED:
        return float(EXECUTION_FRESHNESS_SECS)
    session = (session or "CORE").upper()
    if session in ("PREMARKET", "AFTER_HOURS", "OVERNIGHT", "CRYPTO"):
        return float(_SESSION_AGE_CEILING.get(session, EXECUTION_FRESHNESS_SECS))
    # CORE / RTH — adapt to the broker's measured behaviour.
    from services import broker_freshness_profile as _bfp  # noqa: PLC0415
    p95 = _bfp.p95_if_trusted(broker, session)
    limit = float(EXECUTION_FRESHNESS_MIN_SECS)
    if p95 is not None:
        limit = max(limit, p95 * 2.0)
    return limit


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
    # 2026-06 adaptive-freshness diagnostics.
    session: Optional[str] = None
    broker_route: Optional[str] = None
    broker_tick_age_seconds: Optional[float] = None
    freshness_limit_secs: Optional[float] = None
    adaptive_freshness: bool = False
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

    # ── Source-relative freshness: session + broker route + TRUE tick
    # age (from the broker's own timestamp). Measurement always runs;
    # it only DECIDES when the adaptive flag is on.
    session = execution_session(symbol_u)
    broker_route = None
    if isinstance(broker_q, dict):
        broker_route = broker_q.get("provider_name") or broker_q.get("source")
    tick_age = _broker_tick_age(broker_q)
    result.session = session
    result.broker_route = broker_route
    result.broker_tick_age_seconds = tick_age
    result.adaptive_freshness = ADAPTIVE_FRESHNESS_ENABLED
    if tick_age is not None and broker_route:
        try:
            from services import broker_freshness_profile as _bfp  # noqa: PLC0415
            _bfp.record(broker_route, session, tick_age)
        except Exception:  # noqa: BLE001
            pass

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

    # ── Drift check (only when we have a fresh AND non-delayed
    # vendor witness). Alpha Vantage free tier silently ships a
    # 15-min-old price with a fresh receive stamp, so ``fetched_at``
    # alone doesn't catch it. We check both:
    #   * ``vendor_fresh``     — measurable receive-time age
    #   * ``vendor_realtime``  — provider isn't in the known-delayed set
    # A vendor that fails either check still surfaces its price in
    # the response (diagnostic) but cannot veto the live broker.
    if broker_price and vendor_price:
        drift = compute_disagreement_bps(broker_price, vendor_price)
        result.disagreement_bps = drift
        vendor_fresh = (
            vendor_age is not None
            and vendor_age <= EXECUTION_VENDOR_MAX_AGE_SECS
        )
        # ``vendor_src`` may be either a provider category ("alphavantage")
        # or a pool name ("alphavantage-backup"). Normalize by prefix
        # so a rename doesn't accidentally re-arm veto power.
        vendor_src_lc = (vendor_src or "").lower()
        vendor_realtime = not any(
            vendor_src_lc.startswith(p) for p in DELAYED_QUOTE_PROVIDERS
        )
        if drift > EXECUTION_MAX_DRIFT_BPS and vendor_fresh and vendor_realtime:
            result.data_conflict = True

    # ── Pick a price + decide if execution is allowed
    if broker_price is not None:
        # Broker quote wins on the execution path when fresh.
        result.price = broker_price
        result.source = "broker"
        result.age_seconds = broker_age
        # Adaptive path gates on the broker's TRUE tick age against a
        # source/session-relative limit; legacy path is byte-for-byte
        # the old fetched_at-vs-5s check.
        if ADAPTIVE_FRESHNESS_ENABLED:
            limit = compute_freshness_limit(broker_route or "public", session)
            gate_age = tick_age if tick_age is not None else broker_age
            result.freshness_limit_secs = limit
            broker_fresh = gate_age is not None and gate_age <= limit
            stale_reason = (
                f"broker_quote_stale (tick_age={gate_age}s > {limit}s, "
                f"session={session}, route={broker_route})"
            )
        else:
            limit = float(EXECUTION_FRESHNESS_SECS)
            result.freshness_limit_secs = limit
            broker_fresh = broker_age is not None and broker_age <= EXECUTION_FRESHNESS_SECS
            stale_reason = (
                f"broker_quote_stale (age={broker_age}s > {EXECUTION_FRESHNESS_SECS}s)"
            )
        if not broker_fresh:
            result.execution_allowed = False
            result.reason = stale_reason
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
        # Broker is silent — either the symbol isn't in the broker's
        # coverage or the breaker is OPEN (broker degraded). We
        # surface the vendor price so callers can see it, but
        # auto-execute is BLOCKED. The trade path must re-quote the
        # broker before submitting.
        result.price = vendor_price
        result.source = f"vendor:{vendor_src or 'unknown'}"
        result.age_seconds = vendor_age
        result.execution_allowed = False
        # Distinguish "broker circuit open" from "symbol not in
        # broker coverage" so the operator can tell the two apart
        # in the Why-Not-Trade rollup.
        try:
            from services import broker_circuit_breaker as _cb  # noqa: PLC0415
            cb_state = _cb.snapshot().get("state")
        except Exception:  # noqa: BLE001
            cb_state = None
        if cb_state == "open":
            result.reason = "broker_degraded"
        else:
            result.reason = "no_broker_price"
    else:
        result.execution_allowed = False
        result.reason = "no_price"

    return result


__all__ = [
    "PROVIDER_POLICY",
    "EXECUTION_FRESHNESS_SECS",
    "EXECUTION_MAX_DRIFT_BPS",
    "EXECUTION_VENDOR_MAX_AGE_SECS",
    "ADAPTIVE_FRESHNESS_ENABLED",
    "EXECUTION_FRESHNESS_MIN_SECS",
    "ExecutionQuote",
    "compute_disagreement_bps",
    "compute_freshness_limit",
    "execution_session",
    "fetch_execution_quote",
    "get_provider_chain",
]
