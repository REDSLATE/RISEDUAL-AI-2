"""Pure, testable options-contract filters.

Two-stage cut on raw chain data before ranking:

    1. ``is_liquid``   — hard floor on volume/OI/spread. Anything below
       this is noise — wide bid/ask, stale quotes, zombie strikes.
    2. ``is_unusual``  — inside the liquid set, we want *directional
       intent*, not evergreen retail churn. Flag contracts where either:
         • today's volume is ≥ 2× the chain mean (institutional splash);
         • volume / open_interest > 1.5 (net new positioning, not roll).

Thresholds are chosen to survive both SPY (deep chain, tight spreads)
and NVDA/TSLA (wider, more volatile) without per-symbol tuning. They're
module-level constants so ops can tweak via monkeypatch in A/B trials.

No imports from services — these are pure functions, safely usable
from ``failure_mode_classifier`` or anywhere else without cycle risk.
"""
from __future__ import annotations

MIN_VOLUME = 500
MIN_OPEN_INTEREST = 1000
MAX_SPREAD_BPS = 75.0
UNUSUAL_VOLUME_MULTIPLIER = 2.0
UNUSUAL_VOL_OI_RATIO = 1.5


def compute_spread_bps(bid: float, ask: float) -> float:
    """Bid-ask spread in basis points of the mid. Returns 9999 (a
    sentinel used elsewhere for "effectively untradeable") when the
    quote is degenerate (zero bid/ask, inverted spread).
    """
    if bid <= 0 or ask <= 0:
        return 9999.0
    if ask < bid:
        return 9999.0
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return 9999.0
    return round((ask - bid) / mid * 10_000, 2)


def is_liquid(contract: dict) -> bool:
    """Hard liquidity gate. All three conditions must hold."""
    vol = contract.get("volume", 0) or 0
    oi = contract.get("open_interest", 0) or 0
    bid = contract.get("bid", 0) or 0
    ask = contract.get("ask", 0) or 0
    return (
        vol > MIN_VOLUME
        and oi > MIN_OPEN_INTEREST
        and compute_spread_bps(bid, ask) < MAX_SPREAD_BPS
    )


def is_unusual(contract: dict, avg_volume: float) -> bool:
    """True when today's volume is ≥ 2× the chain mean volume. Returns
    False (not True) for zero avg_volume — empty chains don't produce
    "unusual" flags, they produce empty results.
    """
    if avg_volume <= 0:
        return False
    vol = contract.get("volume", 0) or 0
    return vol > avg_volume * UNUSUAL_VOLUME_MULTIPLIER


def filter_contracts(chain: list[dict]) -> list[dict]:
    """Apply the two-stage cut, stamp each surviving contract with its
    computed ``spread_bps`` for downstream scoring. Returns a fresh list
    of contract dicts (shallow copies) — caller may mutate freely.
    """
    if not chain:
        return []

    avg_volume = sum((c.get("volume", 0) or 0) for c in chain) / len(chain)

    out: list[dict] = []
    for c in chain:
        if not is_liquid(c):
            continue
        oi = c.get("open_interest", 0) or 0
        vol = c.get("volume", 0) or 0
        vol_oi = vol / max(oi, 1)
        # Liquid contracts admitted iff EITHER unusual volume OR fresh
        # positioning (vol/OI > 1.5 means today's trades dwarf resting
        # open interest — net-new directional flow, not rolls).
        if not (is_unusual(c, avg_volume) or vol_oi > UNUSUAL_VOL_OI_RATIO):
            continue
        enriched = dict(c)
        enriched["spread_bps"] = compute_spread_bps(
            c.get("bid", 0) or 0, c.get("ask", 0) or 0,
        )
        out.append(enriched)

    return out
