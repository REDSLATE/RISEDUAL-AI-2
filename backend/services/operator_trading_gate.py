"""Operator Trading Gate — MC-OR-NOTHING DOCTRINE (2026-05-20).

═══════════════════════════════════════════════════════════════════
                  MC-OR-NOTHING SAFETY DOCTRINE
═══════════════════════════════════════════════════════════════════

RISEDUAL is a headless brain — Mission Control owns execution. But
there are four LOCAL trade-insert chokepoints that historically
wrote rows directly to Mongo without ever consulting MC:

    * ml_paper_trader.maybe_paper_trade           → paper_trades
    * crypto_paper_trader.run_crypto_symbol       → crypto_paper_trades
    * paper_options_service                       → paper_trades
    * paper_trading_service.execute_signal        → paper_trades

This module is the kill switch for those four paths. When the
"local trades blocked" doctrine is engaged, every call to
``gate_or_synthetic`` returns False — the chokepoint skips the
write, optionally records a synthetic counterfactual receipt for
the MLs to learn from, and the trade does not happen.

The MC-routed flow (consensus → ``emit_intent_from_consensus`` →
MC ``/api/intents``) does NOT pass through this gate and is
unaffected.

State precedence (highest → lowest):
1. Mongo runtime override (``operator_trading_gate_state`` doc) —
   set via owner-only ``POST /api/admin/trading-gate/toggle``.
2. ``RISEDUAL_LOCAL_TRADES_BLOCKED`` env flag — when ``true``
   (default), local trades are blocked at boot until an operator
   explicitly flips the runtime override.

The legacy ``OPERATOR_TRADING_AUTHORIZATION_ENABLED`` env key is
still read as a back-compat alias when the new key is absent.

═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


STATE_COLLECTION = "operator_trading_gate_state"
HISTORY_COLLECTION = "operator_trading_gate_history"
SYNTHETIC_COLLECTION = "operator_trading_gate_synthetic"

# New canonical env key. When unset, falls back to the legacy
# OPERATOR_TRADING_AUTHORIZATION_ENABLED for back-compat.
ENV_KEY_BLOCKED = "RISEDUAL_LOCAL_TRADES_BLOCKED"
ENV_KEY_LEGACY = "OPERATOR_TRADING_AUTHORIZATION_ENABLED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _env_default_enabled() -> bool:
    """True if local trading is permitted at boot (no Mongo override).

    Default: ``RISEDUAL_LOCAL_TRADES_BLOCKED=true`` → boot DENIED.
    """
    blocked = os.environ.get(ENV_KEY_BLOCKED)
    if blocked is not None:
        return blocked.strip().lower() not in ("true", "1", "yes", "on")
    legacy = os.environ.get(ENV_KEY_LEGACY)
    if legacy is not None:
        return legacy.strip().lower() in ("true", "1", "yes", "on")
    # Default to BLOCKED when no env knob present.
    return False


# ── Test-mode shims (back-compat with existing pytest fixtures) ─────

_TEST_MODE_FORCE_AUTHORIZED: bool = True
_test_mode_disabled: bool = False


def _force_test_mode_authorized(value: bool) -> None:
    global _TEST_MODE_FORCE_AUTHORIZED
    _TEST_MODE_FORCE_AUTHORIZED = bool(value)


def _TEST_MODE_DISABLED_BY_FIXTURE() -> bool:  # noqa: N802
    return _test_mode_disabled


def _disable_test_mode_bypass(value: bool) -> None:
    global _test_mode_disabled
    _test_mode_disabled = bool(value)


def _in_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


# ── State read ──────────────────────────────────────────────────────


async def _read_runtime_override(db) -> Optional[bool]:
    if db is None:
        return None
    try:
        doc = await db[STATE_COLLECTION].find_one({"_id": "singleton"}, {"_id": 0})
        if doc and "enabled" in doc:
            return bool(doc["enabled"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trading-gate] state read failed (failing closed): %s", exc)
    return None


async def is_authorized(db) -> bool:
    """Return True iff a local trade-insert chokepoint may proceed.

    Order:
      * pytest bypass (when ``_TEST_MODE_FORCE_AUTHORIZED`` AND not
        ``_test_mode_disabled``)
      * Mongo runtime override
      * env default (RISEDUAL_LOCAL_TRADES_BLOCKED)
    Fails closed on any DB error.
    """
    if _in_pytest() and _TEST_MODE_FORCE_AUTHORIZED and not _test_mode_disabled:
        return True

    override = await _read_runtime_override(db)
    if override is not None:
        return override
    return _env_default_enabled()


async def get_status(db) -> dict[str, Any]:
    enabled = await is_authorized(db)
    override = await _read_runtime_override(db)
    return {
        "enabled": bool(enabled),
        "source": "runtime_override" if override is not None else "env_default",
        "env_default_enabled": _env_default_enabled(),
        "by_operator": "mc_or_nothing_doctrine",
        "note": (
            "MC-or-nothing doctrine: the four local trade-insert "
            "chokepoints are blocked. MC-routed intent emissions "
            "are unaffected."
        ),
        "loaded_at": _utc_now().isoformat(),
    }


# ── State write ─────────────────────────────────────────────────────


async def set_authorized(
    db,
    *,
    enabled: bool,
    operator_id: str,
    note: str = "",
) -> dict[str, Any]:
    """Owner-only runtime override. Persists in Mongo + appends to
    history. Fails closed if DB unavailable."""
    if db is None:
        raise RuntimeError("db_not_ready")
    now = _utc_now()
    await db[STATE_COLLECTION].update_one(
        {"_id": "singleton"},
        {"$set": {
            "enabled": bool(enabled),
            "updated_at": now,
            "updated_by": operator_id,
            "note": note or "",
        }},
        upsert=True,
    )
    await db[HISTORY_COLLECTION].insert_one({
        "enabled": bool(enabled),
        "operator_id": operator_id,
        "note": note or "",
        "at": now,
    })
    logger.warning(
        "[trading-gate] enabled=%s by=%s note=%s",
        bool(enabled), operator_id, note or "<none>",
    )
    return {"ok": True, "state": await get_status(db)}


# ── Synthetic counterfactual (still valuable for ML learning) ──────


async def record_paused_synthetic(
    db,
    *,
    lane: str,
    symbol: str,
    decision: str,
    confidence: float = 0.0,
    extras: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Record that a blocked-by-gate trade WOULD have been opened.
    Best-effort; failure must not propagate."""
    if db is None:
        return None
    try:
        doc = {
            "lane": lane,
            "symbol": symbol,
            "decision": "NO_TRADE",
            "reason": "paused_by_operator_mc_or_nothing",
            "confidence": float(confidence or 0.0),
            "at": _utc_now(),
            "extras": {
                "synthetic": True,
                "intended_action": f"PAUSED_BY_OPERATOR:{(decision or '').upper()}",
                "blocker": "operator_trading_gate",
                **(extras or {}),
            },
        }
        res = await db[SYNTHETIC_COLLECTION].insert_one(doc)
        return str(res.inserted_id) if res and getattr(res, "inserted_id", None) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[trading-gate] synthetic write failed (non-fatal): %s", exc)
        return None


async def gate_or_synthetic(
    db,
    *,
    lane: str,
    symbol: str,
    intended_decision: str,
    confidence: float = 0.0,
    extras: Optional[dict[str, Any]] = None,
) -> bool:
    """Single chokepoint API: returns True if the caller may proceed,
    False if the caller must abort. On block, writes a synthetic
    counterfactual receipt before returning False."""
    if await is_authorized(db):
        return True
    await record_paused_synthetic(
        db,
        lane=lane,
        symbol=symbol,
        decision=intended_decision,
        confidence=confidence,
        extras=extras,
    )
    return False
