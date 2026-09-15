"""broker_freshness_profile — per-(broker, session) quote-lag profile.

The measurement plumbing behind source-relative, adaptive execution
freshness. Every execution-path broker quote records its TRUE age
(derived from the broker's own tick timestamp, not our receive time)
here, bucketed by market session. The adaptive freshness gate reads
p95 to size its tolerance to each broker's real behaviour instead of
a single universal hardcoded ceiling.

Design
------
* In-memory rolling window — last ``_MAX_SAMPLES`` per (broker,
  session). Resets on restart and re-accumulates within a session;
  the adaptive gate falls back to the configured minimum until a
  key has ``_MIN_TRUSTED`` samples, so a cold start can never widen
  tolerance on thin data.
* Session-keyed so a naturally-old overnight quote never pollutes
  the CORE/RTH profile.
* ``assess_route_health`` implements the ROUTE-WIDE (not one-symbol)
  ``broker_data_limited`` signal: a degraded/thin broker subscription
  is labelled distinctly from a normal stale quote.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional

_MAX_SAMPLES = 500
_MIN_TRUSTED = 30

_LOCK = threading.Lock()
_samples: dict[tuple[str, str], deque] = {}


def route_key(provider_or_source: Optional[str]) -> str:
    """Normalise a provider/source string to a short route key so
    ``public-primary`` / ``public`` / ``moomoo-opend`` collapse to the
    execution route that will actually fill the order."""
    s = (provider_or_source or "unknown").lower()
    if "moomoo" in s:
        return "moomoo"
    if "public" in s:
        return "public"
    if "kraken" in s:
        return "kraken"
    return s


def _key(broker: str, session: str) -> tuple[str, str]:
    return (route_key(broker), (session or "UNKNOWN").upper())


def record(broker: str, session: str, age_seconds: Optional[float]) -> None:
    """Record one broker-tick age sample. No-ops on unknown/negative."""
    if age_seconds is None or age_seconds < 0:
        return
    with _LOCK:
        k = _key(broker, session)
        dq = _samples.get(k)
        if dq is None:
            dq = deque(maxlen=_MAX_SAMPLES)
            _samples[k] = dq
        dq.append(float(age_seconds))


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def profile(broker: str, session: str) -> dict:
    with _LOCK:
        dq = _samples.get(_key(broker, session))
        vals = sorted(dq) if dq else []
    n = len(vals)
    return {
        "broker": route_key(broker),
        "session": (session or "UNKNOWN").upper(),
        "count": n,
        "trusted": n >= _MIN_TRUSTED,
        "min_trusted_samples": _MIN_TRUSTED,
        "p50": round(_percentile(vals, 50), 2) if n else None,
        "p95": round(_percentile(vals, 95), 2) if n else None,
        "p99": round(_percentile(vals, 99), 2) if n else None,
        "max": round(vals[-1], 2) if n else None,
    }


def p95_if_trusted(broker: str, session: str) -> Optional[float]:
    """Return the p95 lag only when the key has enough samples to be
    credible; otherwise ``None`` so the caller uses its floor."""
    p = profile(broker, session)
    return p["p95"] if p["trusted"] else None


def all_profiles() -> list[dict]:
    with _LOCK:
        keys = list(_samples.keys())
    return [profile(b, s) for (b, s) in keys]


def assess_route_health(
    broker: str,
    session: str,
    observations: list[dict],
    *,
    stale_multiple: float = 5.0,
    limited_fraction: float = 0.5,
) -> dict:
    """ROUTE-WIDE broker health from a batch of per-symbol observations.

    Each observation: ``{"age_seconds": float|None, "has_book": bool}``.
    A symbol is "bad" when it is missing a live book OR its broker-tick
    age exceeds ``stale_multiple`` × the route's measured p95. When most
    (>= ``limited_fraction``) probed symbols are bad we flag
    ``broker_data_limited`` — a degraded/thin subscription — which is
    DISTINCT from a normal single stale quote. Callers should block
    fresh entries on this but still allow exits / flatten / cancels.
    """
    n = len(observations)
    p95 = p95_if_trusted(broker, session)
    bad = 0
    missing_book = 0
    stale = 0
    for o in observations:
        age = o.get("age_seconds")
        has_book = bool(o.get("has_book"))
        is_bad = False
        if not has_book:
            missing_book += 1
            is_bad = True
        if p95 is not None and age is not None and age > stale_multiple * max(p95, 0.001):
            stale += 1
            is_bad = True
        if is_bad:
            bad += 1
    frac = (bad / n) if n else 0.0
    limited = n > 0 and frac >= limited_fraction
    return {
        "broker": route_key(broker),
        "session": (session or "UNKNOWN").upper(),
        "broker_data_limited": limited,
        "symbols_probed": n,
        "symbols_bad": bad,
        "missing_book": missing_book,
        "stale_vs_p95": stale,
        "limited_fraction": round(frac, 3),
        "p95_used": p95,
        "reason": (
            "most probed symbols missing book or far beyond p95 lag"
            if limited else "route healthy"
        ),
    }


__all__ = [
    "record",
    "profile",
    "p95_if_trusted",
    "all_profiles",
    "assess_route_health",
    "route_key",
]
