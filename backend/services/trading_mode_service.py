"""Trading-mode service — per-user PAPER vs LIVE selector.

A single global toggle stored on the user document that downstream order
flows consult to decide whether to hit paper-trading endpoints or real
broker endpoints. The service itself is concerned only with:

* canonical persistence of the mode flag on `users.trading_mode`
* enforcing a cooldown between switches so users can't flap accidentally
* writing an immutable audit row to ``trading_mode_switches`` for every
  flip (compliance + post-mortem)

Routing the actual order traffic is the caller's job — they simply call
``is_live_mode(user_id)`` and pick the right endpoint / broker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

# 30-second floor between switches. Tuned to be long enough to break a
# panicky "click click click" loop but short enough that intentional
# re-flips during a session aren't annoying.
COOLDOWN_SECONDS = 30

VALID_MODES = ("paper", "live")
DEFAULT_MODE = "paper"

_db: Optional[AsyncIOMotorDatabase] = None


def set_db(database: AsyncIOMotorDatabase) -> None:
    global _db
    _db = database


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_user_id(user: Any) -> Optional[str]:
    """Accept either a user-id string or the user dict from get_current_user."""
    if user is None:
        return None
    if isinstance(user, str):
        return user
    if isinstance(user, dict):
        return user.get("id") or (
            str(user["_id"]) if user.get("_id") is not None else None
        )
    return None


async def get_user_trading_mode(user: Any) -> str:
    """Return the user's current mode, defaulting to ``paper``."""
    if _db is None:
        return DEFAULT_MODE
    user_id = _coerce_user_id(user)
    if not user_id:
        return DEFAULT_MODE
    from bson import ObjectId
    try:
        doc = await _db.users.find_one(
            {"_id": ObjectId(user_id)}, {"trading_mode": 1, "_id": 0}
        )
    except Exception:
        doc = None
    mode = (doc or {}).get("trading_mode") or DEFAULT_MODE
    return mode if mode in VALID_MODES else DEFAULT_MODE


async def is_live_mode(user: Any) -> bool:
    return (await get_user_trading_mode(user)) == "live"


async def get_mode_state(user: Any) -> dict:
    """Bundle the mode + cooldown info the UI needs in a single read."""
    user_id = _coerce_user_id(user)
    if _db is None or not user_id:
        return {
            "mode": DEFAULT_MODE,
            "last_switch_at": None,
            "cooldown_remaining_s": 0,
            "cooldown_total_s": COOLDOWN_SECONDS,
        }
    from bson import ObjectId
    try:
        doc = await _db.users.find_one(
            {"_id": ObjectId(user_id)},
            {"trading_mode": 1, "trading_mode_last_switch_at": 1, "_id": 0},
        )
    except Exception:
        doc = None
    doc = doc or {}
    mode = doc.get("trading_mode") or DEFAULT_MODE
    last = doc.get("trading_mode_last_switch_at")

    cooldown_remaining = 0
    last_iso: Optional[str] = None
    if isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (_now() - last).total_seconds()
        cooldown_remaining = max(0, int(COOLDOWN_SECONDS - elapsed))
        last_iso = last.isoformat()
    elif isinstance(last, str):
        last_iso = last

    return {
        "mode": mode if mode in VALID_MODES else DEFAULT_MODE,
        "last_switch_at": last_iso,
        "cooldown_remaining_s": cooldown_remaining,
        "cooldown_total_s": COOLDOWN_SECONDS,
    }


class TradingModeError(ValueError):
    """Raised by ``switch_user_trading_mode`` for user-correctable errors."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


async def switch_user_trading_mode(
    user: Any,
    *,
    new_mode: str,
    confirm_text: Optional[str] = None,
    request_meta: Optional[dict] = None,
) -> dict:
    """Flip the user's trading mode.

    Validation:
      * ``new_mode`` must be one of ``VALID_MODES``.
      * Switching TO ``live`` requires ``confirm_text == "LIVE"``.
      * Cooldown of ``COOLDOWN_SECONDS`` enforced from last switch.

    On success writes ``trading_mode`` + ``trading_mode_last_switch_at``
    on the user document and an audit row in ``trading_mode_switches``.
    """
    if _db is None:
        raise TradingModeError("db_unavailable", "Database not wired")

    user_id = _coerce_user_id(user)
    if not user_id:
        raise TradingModeError("auth_required", "User id missing")

    if new_mode not in VALID_MODES:
        raise TradingModeError(
            "invalid_mode", f"mode must be one of {VALID_MODES}"
        )

    state = await get_mode_state(user_id)
    current_mode = state["mode"]
    cooldown_remaining = state["cooldown_remaining_s"]

    if new_mode == current_mode:
        return {
            "ok": True,
            "no_op": True,
            "mode": current_mode,
            "cooldown_remaining_s": cooldown_remaining,
        }

    if cooldown_remaining > 0:
        raise TradingModeError(
            "cooldown",
            f"Mode switch cooling down — try again in {cooldown_remaining}s",
        )

    if new_mode == "live" and (confirm_text or "").strip().upper() != "LIVE":
        raise TradingModeError(
            "confirm_required",
            'Switching to LIVE requires confirm_text == "LIVE"',
        )

    now = _now()

    # Update the user document by ObjectId. update_one is idempotent.
    update_doc = {
        "$set": {
            "trading_mode": new_mode,
            "trading_mode_last_switch_at": now,
        }
    }
    from bson import ObjectId
    try:
        await _db.users.update_one({"_id": ObjectId(user_id)}, update_doc)
    except Exception:
        # Soft-fail — the audit row will still capture the attempt.
        pass

    # Audit log — never fail the switch on log-write errors, just degrade.
    try:
        await _db.trading_mode_switches.insert_one(
            {
                "user_id": user_id,
                "from_mode": current_mode,
                "to_mode": new_mode,
                "confirmed": bool(confirm_text),
                "switched_at": now,
                "ip": (request_meta or {}).get("ip"),
                "user_agent": (request_meta or {}).get("user_agent"),
            }
        )
    except Exception:
        pass

    return {
        "ok": True,
        "no_op": False,
        "mode": new_mode,
        "previous_mode": current_mode,
        "switched_at": now.isoformat(),
        "cooldown_remaining_s": COOLDOWN_SECONDS,
    }


async def ensure_indexes() -> None:
    """Create indexes for the audit log. Idempotent."""
    if _db is None:
        return
    try:
        await _db.trading_mode_switches.create_index("user_id")
        await _db.trading_mode_switches.create_index("switched_at")
    except Exception:
        pass
