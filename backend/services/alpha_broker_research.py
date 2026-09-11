"""alpha_broker_research — normalized BrokerResearchSnapshot.

Operator design (2026-09-11): the funnel's re-rank stage must use
broker-truth data, but each broker adapter exposes a different
subset of the ideal snapshot. This module defines the ONE contract
adapters produce, so downstream re-rank code sees the same shape
regardless of source, and never has to guess whether a missing
field means "unsupported" vs "adapter crashed."

Contract
--------
* Every field is present on every snapshot.
* Numeric fields default to ``None`` when the adapter cannot
  produce them; string fields default to ``"not_available"``.
* We NEVER synthesize a value. A missing bid is ``None``, not a
  copy of the last price or a mid computed from vendor data.
* Snapshots carry ``supported_fields`` — a set of field names the
  adapter *can* provide (whether or not this particular symbol
  produced them). Downstream code uses this to distinguish "this
  broker doesn't do positions yet" from "positions call errored."

v1 supported (thin path)
------------------------
Fresh quote, bid, ask, mid, spread_bps, broker timestamp/age,
signal price, current price, drift bps. Everything else is
declared as a schema slot but returned as ``not_available``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

logger = logging.getLogger(__name__)


NOT_AVAILABLE = "not_available"


@dataclass
class BrokerResearchSnapshot:
    # Identity
    symbol: str
    broker: str
    fetched_at_ns: int

    # Freshness / drift
    signal_price: Optional[float] = None
    current_price: Optional[float] = None
    drift_bps: Optional[float] = None
    broker_quote_age_seconds: Optional[float] = None

    # Quote quality
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread_bps: Optional[float] = None

    # v1 not_available slots (schema-visible, adapter fills over time)
    recent_bars: Any = NOT_AVAILABLE
    volume_confirmation: Any = NOT_AVAILABLE
    position_qty: Any = NOT_AVAILABLE
    open_orders_qty: Any = NOT_AVAILABLE
    buying_power_usd: Any = NOT_AVAILABLE

    # Rejection reasons the adapter surfaced during research —
    # empty when the snapshot is clean.
    hard_block_reasons: list[str] = field(default_factory=list)

    # Fields the *broker adapter itself* claims to support. Lets
    # downstream code know whether "position_qty=not_available"
    # means "not yet implemented" vs "supported but no position."
    supported_fields: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["supported_fields"] = sorted(self.supported_fields)
        return d


async def perform_research(
    *, symbol: str, signal_price: float,
) -> BrokerResearchSnapshot:
    """v1 thin implementation. Uses the same broker-first quote
    path the executor uses (``market_data_pool.fetch_broker_quote``,
    which prefers MooMoo OpenD then falls back to Public.com).

    Returns a fully-populated snapshot with the v1 supported fields
    computed and the rest marked ``not_available``. NEVER raises —
    on adapter failure the snapshot lands with an empty quote block
    and one ``hard_block_reasons`` entry so the re-rank layer can
    see why the score didn't move.
    """
    snap = BrokerResearchSnapshot(
        symbol=(symbol or "").upper(),
        broker="unknown",
        fetched_at_ns=time.time_ns(),
        signal_price=float(signal_price) if signal_price else None,
        supported_fields={
            "signal_price", "current_price", "drift_bps",
            "broker_quote_age_seconds", "bid", "ask", "mid", "spread_bps",
        },
    )
    try:
        from services.market_data_pool import fetch_broker_quote
        quote = await fetch_broker_quote(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.info("[broker_research] fetch failed %s: %s", symbol, exc)
        snap.hard_block_reasons.append(f"broker_fetch_failed:{type(exc).__name__}")
        return snap
    if not quote or not quote.get("price"):
        snap.hard_block_reasons.append("no_broker_price")
        return snap

    snap.broker = str(quote.get("provider_name") or "unknown")
    snap.current_price = float(quote["price"])
    bid = quote.get("bid")
    ask = quote.get("ask")
    snap.bid = float(bid) if bid else None
    snap.ask = float(ask) if ask else None
    if snap.bid and snap.ask and snap.ask > snap.bid:
        snap.mid = round((snap.bid + snap.ask) / 2.0, 6)
        snap.spread_bps = round((snap.ask - snap.bid) / snap.mid * 10_000.0, 3)
        if snap.ask < snap.bid:
            snap.hard_block_reasons.append("crossed_quote")

    fetched_at = quote.get("fetched_at")
    try:
        snap.broker_quote_age_seconds = round(time.time() - float(fetched_at), 3) if fetched_at else None
    except (TypeError, ValueError):
        snap.broker_quote_age_seconds = None

    if snap.signal_price and snap.current_price:
        drift = (snap.current_price - snap.signal_price) / snap.signal_price * 10_000.0
        snap.drift_bps = round(drift, 2)

    return snap


def compute_research_delta(
    snap: BrokerResearchSnapshot,
    *,
    max_spread_bps: float = 40.0,
    max_drift_bps: float = 75.0,
    max_quote_age_seconds: float = 30.0,
) -> tuple[float, list[str], bool]:
    """Turn a research snapshot into a ``(score_delta, reasons, hard_block)``
    tuple. Positive delta = confirming; negative = deteriorating.

    Hard-block flags — per operator rule — are limited to
    OBJECTIVE problems that no re-rank should paper over:

    * ``no_broker_price`` / ``crossed_quote`` / ``broker_fetch_failed``
    * excessive spread (> ``max_spread_bps``)
    * excessive drift from signal price (> ``max_drift_bps``)
    * stale broker quote (> ``max_quote_age_seconds``)

    Everything else is a delta, never a block. A confidence of
    0.69 vs 0.70 is a rerank input, not an execution veto.
    """
    reasons: list[str] = []
    delta = 0.0
    hard_block = False

    for r in snap.hard_block_reasons:
        reasons.append(f"broker: {r}")
        hard_block = True

    if snap.spread_bps is not None:
        if snap.spread_bps > max_spread_bps:
            reasons.append(f"spread_over_cap:{snap.spread_bps:.1f}bps")
            hard_block = True
        elif snap.spread_bps <= 5:
            delta += 0.05
            reasons.append("tight_spread")
        elif snap.spread_bps <= 15:
            delta += 0.02

    if snap.drift_bps is not None:
        abs_drift = abs(snap.drift_bps)
        if abs_drift > max_drift_bps:
            reasons.append(f"drift_over_cap:{snap.drift_bps:+.1f}bps")
            hard_block = True
        elif abs_drift <= 10:
            delta += 0.04
            reasons.append("minimal_drift")
        elif abs_drift >= 40:
            delta -= 0.03
            reasons.append(f"noticeable_drift:{snap.drift_bps:+.1f}bps")

    if snap.broker_quote_age_seconds is not None:
        if snap.broker_quote_age_seconds > max_quote_age_seconds:
            reasons.append(f"stale_broker_quote:{snap.broker_quote_age_seconds:.1f}s")
            hard_block = True
        elif snap.broker_quote_age_seconds <= 5:
            delta += 0.02

    # Broker actively confirmed the signal → small positive bump.
    if snap.current_price and not hard_block:
        delta += 0.03
        reasons.append("broker_confirmed")

    return round(delta, 4), reasons, hard_block


__all__ = [
    "BrokerResearchSnapshot", "NOT_AVAILABLE",
    "perform_research", "compute_research_delta",
]
