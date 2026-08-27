"""Economic fingerprinting for Alpha intents (2026-02).

Ported from IGNISpilot's ``camaroOpportunityEngine.ts::economic_fingerprint``.
Prevents the "3 GOOGL long intents in 15 min all at $185.20" pile-up
that happens when the same setup re-fires across consecutive scanner
ticks before the first one has cleared the pipeline.

The hash normalises the entry price into an **ATR-scaled zone bucket**
so tiny price wiggles ($185.19 → $185.21) still collide, but a
different real setup ($185.20 → $189.00) does not.

Design invariants:

* Deterministic — same inputs always produce the same hex string
* Case- and whitespace-insensitive on the symbol / strategy fields
* ATR ≤ 0 or NaN falls back to a 1.0 default so bad input doesn't
  cause a hash collision on a wildly different entry
* SHA-256 is used to avoid accidental collisions in the birthday-attack
  sense — this is dedup, not security
"""
from __future__ import annotations

import hashlib
import math
from typing import Optional


def _clean(s: str) -> str:
    return (s or "").strip()


def _finite(x: Optional[float], default: float) -> float:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def economic_fingerprint(
    symbol: str,
    strategy: str,
    direction: str,
    timeframe: str,
    entry_zone: float,
    atr: Optional[float] = 1.0,
) -> str:
    """Return a 64-char SHA-256 hex digest for the intent tuple.

    Matches IGNISpilot's normalisation:

    * ``zone_size = max(atr * 0.25, |entry_zone| * 0.001)`` — the
      bucket width. Uses the larger of "quarter of ATR" or "10 bps
      of price" so both cheap ($3 penny) and expensive ($300 tech)
      stocks get a sane bucket
    * ``normalized_zone = round(entry_zone / zone_size)`` — the bucket
      integer. Any two entries within one bucket hash to the same
      fingerprint

    Missing / non-finite values are coerced to sane defaults so this
    function never raises.
    """
    sym = _clean(symbol).upper()
    strat = _clean(strategy).lower()
    dirn = _clean(direction).lower()
    tf = _clean(timeframe).lower()

    entry = _finite(entry_zone, 0.0)
    a = _finite(atr, 1.0)
    if a <= 0:
        a = 1.0

    zone_size = max(a * 0.25, abs(entry) * 0.001)
    if zone_size <= 0:  # entry=0, atr=0 → still safe
        zone_size = 1.0
    normalized_zone = round(entry / zone_size)

    canonical = f"{sym}|{strat}|{dirn}|{tf}|{normalized_zone}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["economic_fingerprint"]
