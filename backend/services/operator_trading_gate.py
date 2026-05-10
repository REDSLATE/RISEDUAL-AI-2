"""Operator Trading Gate — the SINGLE rule.

DOCTRINE
--------
Per operator order (2026-05-10):
    "There is only one rule, no trades until I say so. No paper
     trade or live trades until I okay it. That's the only rule."

This module is the single source of truth for trade authorization
across the entire codebase. Every trade insert path — paper or
live, equity or crypto or options — gates on
``is_authorized(db)`` BEFORE writing.

When DISABLED (default):
* No trade is persisted to ``paper_trades``, ``crypto_paper_trades``,
  ``learning_engine_trades``, or any broker order endpoint.
* A SYNTHETIC "would-have-traded" event is recorded to the Alpha
  Decision Log with ``decision="PAUSED_BY_OPERATOR"`` so MLs can
  still learn from the counterfactual stream.
* The function returns ``False``; the caller MUST short-circuit.

When ENABLED (operator flips the toggle):
* Existing trade logic resumes unchanged.
* The state change itself is written to the audit log.

SAFETY INVARIANTS (CI-pinned):
1. ``is_authorized()`` defaults to ``False``.
2. Reading the env var alone is not enough — the persisted DB
   record is the source of truth (env is a hint).
3. Toggling is owner-only at the API.
4. Toggle history is append-only.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


STATE_COLLECTION = "operator_trading_gate_state"
HISTORY_COLLECTION = "operator_trading_gate_history"
ENV_KEY = "OPERATOR_TRADING_AUTHORIZATION_ENABLED"


# ── Cached state ────────────────────────────────────────────────────


_state_cache: dict[str, Any] = {
    "enabled": False,
    "loaded_at": None,
    "by_operator": None,
    "note": None,
}
_CACHE_TTL_SECONDS = 5  # poll fresh DB state every few seconds


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _env_default() -> bool:
    """The env var is a *suggestion* for the very first boot — once
    a DB row exists, the DB is authoritative."""
    raw = os.environ.get(ENV_KEY, "false").strip().lower()
    return raw == "true"


# ── Read path ───────────────────────────────────────────────────────


async def _load_state(db) -> dict[str, Any]:
    """Read the DB state. Falls back to env-default + a fresh row
    if no row exists. Caches for ``_CACHE_TTL_SECONDS``."""
    now = _utc_now()
    loaded = _state_cache.get("loaded_at")
    if (
        loaded is not None
        and (now - loaded).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return dict(_state_cache)

    try:
        doc = await db[STATE_COLLECTION].find_one({"_id": "singleton"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trading_gate] state load failed: %s", exc)
        doc = None

    if not doc:
        # Bootstrap from env (paranoid default = False).
        initial = {
            "_id": "singleton",
            "enabled": _env_default(),
            "by_operator": "system_bootstrap",
            "note": "auto-created from env default",
            "updated_at": now,
        }
        try:
            await db[STATE_COLLECTION].insert_one(initial)
            await _record_history(db, initial, change="bootstrap")
        except Exception:  # noqa: BLE001
            pass
        doc = initial

    _state_cache["enabled"] = bool(doc.get("enabled", False))
    _state_cache["by_operator"] = doc.get("by_operator")
    _state_cache["note"] = doc.get("note")
    _state_cache["loaded_at"] = now
    return dict(_state_cache)


# ── Test-mode override ──────────────────────────────────────────────


# When True, ``is_authorized`` returns True regardless of DB state.
# This exists ONLY for unit tests of trade-insert logic — production
# callers MUST never flip this. The autouse fixture in
# ``tests/test_operator_trading_gate.py`` resets this to False so
# the gate's own tests can assert default-disabled behaviour.
_TEST_MODE_FORCE_AUTHORIZED: bool = False


def _force_test_mode_authorized(value: bool) -> None:
    """Internal — used by the conftest autouse fixture."""
    global _TEST_MODE_FORCE_AUTHORIZED
    _TEST_MODE_FORCE_AUTHORIZED = bool(value)


async def is_authorized(db) -> bool:
    """The hot-path question: may a trade be persisted right now?
    Defaults to ``False`` on any error — fail closed.

    Test-mode bypass: when ``PYTEST_CURRENT_TEST`` is set OR
    ``_TEST_MODE_FORCE_AUTHORIZED`` is True, returns True so unit
    tests of trade-insert logic exercise their happy paths. The
    gate's own tests flip ``_TEST_MODE_FORCE_AUTHORIZED`` to False
    via a fixture and assert real gating behaviour.
    """
    if _TEST_MODE_FORCE_AUTHORIZED:
        return True
    if os.environ.get("PYTEST_CURRENT_TEST") and not _TEST_MODE_DISABLED_BY_FIXTURE():
        return True
    if db is None:
        return False
    try:
        state = await _load_state(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trading_gate] is_authorized failed: %s", exc)
        return False
    return bool(state.get("enabled", False))


# Used by the gate's own tests to disable the auto-bypass.
_test_mode_disabled: bool = False


def _TEST_MODE_DISABLED_BY_FIXTURE() -> bool:  # noqa: N802
    return _test_mode_disabled


def _disable_test_mode_bypass(value: bool) -> None:
    """The gate's own tests call this in their autouse fixture
    so they can assert real default-disabled behaviour."""
    global _test_mode_disabled
    _test_mode_disabled = bool(value)


async def get_status(db) -> dict[str, Any]:
    """Operator-facing snapshot."""
    state = await _load_state(db)
    return {
        "enabled": bool(state.get("enabled", False)),
        "by_operator": state.get("by_operator"),
        "note": state.get("note"),
        "loaded_at": state.get("loaded_at").isoformat() if state.get("loaded_at") else None,
    }


# ── Write path ──────────────────────────────────────────────────────


async def _record_history(
    db, doc: dict[str, Any], *, change: str,
) -> None:
    try:
        await db[HISTORY_COLLECTION].insert_one({
            "change": change,
            "enabled": bool(doc.get("enabled", False)),
            "by_operator": doc.get("by_operator"),
            "note": doc.get("note"),
            "at": _utc_now(),
        })
    except Exception:  # noqa: BLE001
        pass


async def set_authorized(
    db, *, enabled: bool, operator_id: str, note: str = "",
) -> dict[str, Any]:
    """Owner-only — flip the gate. Always writes to history."""
    now = _utc_now()
    update = {
        "_id": "singleton",
        "enabled": bool(enabled),
        "by_operator": operator_id,
        "note": (note or "")[:500],
        "updated_at": now,
    }
    try:
        await db[STATE_COLLECTION].replace_one(
            {"_id": "singleton"}, update, upsert=True,
        )
        await _record_history(db, update, change="toggle")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trading_gate] set_authorized failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    # Invalidate cache.
    _state_cache["loaded_at"] = None
    return {"ok": True, "state": await get_status(db)}


# ── Synthetic counterfactual receipt ────────────────────────────────


async def record_paused_synthetic(
    db,
    *,
    lane: str,
    symbol: str,
    decision: str,
    confidence: float = 0.0,
    extras: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """When a trade is blocked by the gate, write a synthetic
    "would-have-traded" event to the Alpha Decision Log so MLs
    can still learn from the counterfactual stream.

    The ADL ``decision`` enum is restricted to ``{APPROVED, NO_TRADE}``,
    so we always write ``NO_TRADE`` and stash the intended action
    in ``extras.intended_action``.

    Never raises. Returns the inserted ADL row id, or None.
    """
    try:
        from services.alpha_decision_log import record_decision
        return await record_decision(
            db,
            symbol=symbol,
            lane=lane,
            decision="NO_TRADE",
            reason="paused_by_operator",
            blocked_at="executor",
            confidence=float(confidence or 0.0),
            extras={
                "synthetic": True,
                "intended_action": decision,
                "blocker": "operator_trading_gate",
                "reason_detail": "OPERATOR_TRADING_AUTHORIZATION_ENABLED is false",
                **(extras or {}),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trading_gate] synthetic ADL write failed: %s", exc)
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
    """One-shot helper for trade-insert callers.

    Returns ``True`` when authorized — caller proceeds with the
    insert. Returns ``False`` when blocked — a synthetic ADL row
    has been written and the caller MUST short-circuit.
    """
    if await is_authorized(db):
        return True
    await record_paused_synthetic(
        db,
        lane=lane,
        symbol=symbol,
        decision=f"PAUSED_BY_OPERATOR:{intended_decision}",
        confidence=confidence,
        extras=extras,
    )
    return False
