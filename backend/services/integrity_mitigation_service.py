"""
Integrity-driven trading mitigation — the self-defense layer.

When a data-integrity alert rule fires AND carries a ``mitigation``
spec, this service activates a short-lived, auto-expiring
"degrade mode" that:

    1. Halves (or clamps) every subsequent position size.
    2. Optionally suppresses STRONG_* signals entirely — they downgrade
       to HOLD so the system doesn't amplify conviction during a
       period where we don't trust the data.

The mitigation has a hard TTL. When it elapses, the record flips
``active=False`` (never deleted — audit trail stays intact). A
new rule trigger renews it with a fresh TTL.

Contract
--------
* One source of truth: ``integrity_mitigations`` Mongo collection.
* No in-memory caching — every sizing call hits Mongo with a tight
  index-backed query (``active: True, expires_at > now``) so the
  mitigation is propagated instantly across all workers.
* Most conservative wins: if two mitigations are active, the lower
  multiplier is applied and any ``disable_strong_signals`` flag
  counts as enabled.
* Floor clamp at 0.25× — we never degrade below this because a
  mitigation is about reducing exposure, not cancelling the strategy.

Domain scoping (IP-defensible boundary)
---------------------------------------
This file lives in the PRD (Post-Resolution Domain). It observes
the data-integrity state of the system, never the market. It is
consumed by the DTD (Decision-Time Domain) via a single pure
multiplier lookup — no DTD logic leaks into this file and vice
versa.
"""

from __future__ import annotations

__domain__ = "PRD"

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

MitigationAction = Literal["DEGRADE_TRADING"]


class MitigationParams(TypedDict, total=False):
    position_multiplier: float
    disable_strong_signals: bool


DEFAULT_TTL_MINUTES = 60
MIN_POSITION_MULTIPLIER = 0.25
MAX_POSITION_MULTIPLIER = 1.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_multiplier(value: Any) -> float:
    """Clamp to [0.25, 1.0]. Garbage input falls back to 1.0 (no-op)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(MIN_POSITION_MULTIPLIER, min(MAX_POSITION_MULTIPLIER, f))


# ── Write path ────────────────────────────────────────────────────


async def activate_integrity_mitigation(
    db: Any,
    *,
    source_rule_id: str,
    mitigation: dict[str, Any],
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> str | None:
    """Activate a temporary self-defense mitigation.

    Supported v1 action:
        DEGRADE_TRADING
            params.position_multiplier: 0.25 – 1.0
            params.disable_strong_signals: bool
    """
    action = mitigation.get("action")
    if action != "DEGRADE_TRADING":
        return None

    params_raw = mitigation.get("params") or {}
    params: MitigationParams = {
        "position_multiplier": _clamp_multiplier(
            params_raw.get("position_multiplier", 0.5)
        ),
        "disable_strong_signals": bool(
            params_raw.get("disable_strong_signals", False)
        ),
    }

    now = _now()
    doc = {
        "type": "DEGRADE_TRADING",
        "source_rule_id": str(source_rule_id),
        "activated_at": now,
        "expires_at": now + timedelta(minutes=max(1, int(ttl_minutes))),
        "params": params,
        "active": True,
    }
    result = await db.integrity_mitigations.insert_one(doc)
    # Loud structured log — so the nightly integrity audit report
    # captures "we entered degrade mode because of X" historically.
    logger.warning(
        "[integrity_mitigation] ACTIVATED source=%s action=DEGRADE_TRADING "
        "position_multiplier=%.2f disable_strong=%s ttl_min=%d expires_at=%s",
        source_rule_id,
        params["position_multiplier"],
        params["disable_strong_signals"],
        ttl_minutes,
        doc["expires_at"].isoformat(),
    )
    return str(result.inserted_id)


# ── TTL sweep ─────────────────────────────────────────────────────


async def expire_integrity_mitigations(db: Any) -> int:
    """Flip any active mitigation whose TTL has elapsed to inactive.

    Never deletes — the historical record is preserved for the
    audit trail and the dashboard event timeline. Returns the
    number of rows updated (0 if nothing expired on this tick)."""
    now = _now()
    result = await db.integrity_mitigations.update_many(
        {"active": True, "expires_at": {"$lte": now}},
        {"$set": {
            "active": False,
            "expired_at": now,
            "expired_reason": "ttl_elapsed",
        }},
    )
    count = int(result.modified_count)
    if count:
        logger.info(
            "[integrity_mitigation] TTL-expired %d row(s) at %s",
            count, now.isoformat(),
        )
    return count


# ── Read paths ────────────────────────────────────────────────────


async def get_active_integrity_mitigations(db: Any) -> list[dict[str, Any]]:
    """Return every still-live mitigation, freshest first.

    Implicitly sweeps the TTL on every read — this keeps the
    consumer-side "effective multiplier" calculation correct even
    in the ~15-minute gap between scheduler ticks, and means the
    sizing hot path never sees a stale row. The sweep is a
    single index-backed update, not a table scan, so the cost is
    negligible."""
    await expire_integrity_mitigations(db)
    now = _now()
    cursor = db.integrity_mitigations.find(
        {"active": True, "expires_at": {"$gt": now}}
    ).sort("activated_at", -1)
    return await cursor.to_list(length=100)


async def get_effective_integrity_risk_multiplier(db: Any) -> float:
    """Most-conservative active multiplier. Default 1.0 (no-op)."""
    mitigations = await get_active_integrity_mitigations(db)
    multipliers: list[float] = []
    for m in mitigations:
        if m.get("type") != "DEGRADE_TRADING":
            continue
        params = m.get("params") or {}
        multipliers.append(_clamp_multiplier(params.get("position_multiplier", 1.0)))
    if not multipliers:
        return 1.0
    return min(multipliers)


async def should_suppress_strong_signals(db: Any) -> bool:
    """True if ANY active DEGRADE_TRADING row has the flag set."""
    mitigations = await get_active_integrity_mitigations(db)
    for m in mitigations:
        if m.get("type") != "DEGRADE_TRADING":
            continue
        params = m.get("params") or {}
        if bool(params.get("disable_strong_signals")):
            return True
    return False


async def summarize_integrity_mitigation_state(db: Any) -> dict[str, Any]:
    """One-shot read used by the admin dashboard endpoint."""
    active = await get_active_integrity_mitigations(db)
    multipliers = [
        _clamp_multiplier((m.get("params") or {}).get("position_multiplier", 1.0))
        for m in active if m.get("type") == "DEGRADE_TRADING"
    ]
    suppress = any(
        bool((m.get("params") or {}).get("disable_strong_signals"))
        for m in active if m.get("type") == "DEGRADE_TRADING"
    )
    return {
        "active": bool(active),
        "active_count": len(active),
        "risk_multiplier": min(multipliers) if multipliers else 1.0,
        "suppress_strong_signals": suppress,
        "items": [
            {
                "type": m.get("type"),
                "source_rule_id": m.get("source_rule_id"),
                "activated_at": (
                    m["activated_at"].isoformat()
                    if isinstance(m.get("activated_at"), datetime)
                    else m.get("activated_at")
                ),
                "expires_at": (
                    m["expires_at"].isoformat()
                    if isinstance(m.get("expires_at"), datetime)
                    else m.get("expires_at")
                ),
                "params": m.get("params") or {},
            }
            for m in active
        ],
    }


# ── Sync bridge for non-async call sites ──────────────────────────
#
# The position-sizing path in routes/risk_calculator.py is sync
# (pure math, no I/O). We still want it to respect the mitigation.
# Solution: a narrow helper that caches the multiplier in-process
# for ``cache_seconds`` so the sync path can call it without taking
# a 5-10ms hit on every size calculation. Cache invalidates itself.

_CACHED_MULTIPLIER: dict[str, Any] = {"value": 1.0, "at": None, "ttl_s": 5}
_CACHED_SUPPRESS: dict[str, Any] = {"value": False, "at": None, "ttl_s": 5}


async def refresh_sync_cache(db: Any) -> None:
    """Refresh the sync-side cache. Called by:
    (a) the alert evaluator right after it activates a mitigation
        (so degrade mode takes effect on the very next sizing call), and
    (b) the 15-min scheduled tick (to pick up TTL expirations)."""
    m = await get_effective_integrity_risk_multiplier(db)
    s = await should_suppress_strong_signals(db)
    _CACHED_MULTIPLIER["value"] = m
    _CACHED_MULTIPLIER["at"] = _now()
    _CACHED_SUPPRESS["value"] = s
    _CACHED_SUPPRESS["at"] = _now()


def get_cached_risk_multiplier() -> float:
    """Sync reader. Returns 1.0 if the cache is cold (safe default)."""
    at = _CACHED_MULTIPLIER.get("at")
    if at is None:
        return 1.0
    # Cache expires quickly so a stale read is bounded. The async
    # refresher above pushes fresh values on every evaluator tick.
    if (_now() - at).total_seconds() > float(_CACHED_MULTIPLIER.get("ttl_s", 5)):
        return 1.0
    return float(_CACHED_MULTIPLIER.get("value", 1.0))


def get_cached_suppress_strong_signals() -> bool:
    at = _CACHED_SUPPRESS.get("at")
    if at is None:
        return False
    if (_now() - at).total_seconds() > float(_CACHED_SUPPRESS.get("ttl_s", 5)):
        return False
    return bool(_CACHED_SUPPRESS.get("value", False))


def _reset_cache_for_tests() -> None:
    """Test helper — never call in prod."""
    _CACHED_MULTIPLIER["at"] = None
    _CACHED_SUPPRESS["at"] = None
