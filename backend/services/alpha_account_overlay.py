"""Alpha Account-Aware Decision Overlay — state, maturation and gating.

This module wraps the raw patch pieces (`alpha_broker_account_context`,
`alpha_account_decision_overlay`, `alpha_prompt_context`) with the
operational discipline the operator explicitly asked for:

    * Starts in SHADOW mode
    * Counts exactly 7 completed U.S. trading days (weekdays that are
      NOT NYSE holidays)
    * Persists ``overlay_started_at``, ``trading_days_completed``,
      ``mode``, ``hard_gate_at`` so a redeploy cannot silently reset
      the seven-day clock
    * Auto-transitions SHADOW → HARD_GATE at the end of the 7th
      counted trading day — no operator flag required
    * If the routed broker's account cannot be read reliably, marks
      the overlay FAULTED and refuses to activate the hard gate
      (never silently extends shadow either — the clock keeps its
      state and the operator is surfaced the exact fault reason)
    * Per-broker fit check: Public → Public account, MooMoo → MooMoo
      account. NEVER unions the two.
    * Every shadow-mode evaluation is journalled so a Day-7 transition
      report can honestly say what would have happened.

Mongo collections
-----------------
    alpha_overlay_state      : singleton {_id: "overlay", ...}
    alpha_overlay_shadow     : one doc per evaluated intent

The state doc is authoritative. Nothing about the maturation depends
on process uptime — a restart re-reads state and picks up the same
counter.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    _NY = timezone.utc  # last-resort — never blocks module import

from services.alpha_broker_account_context import (
    AccountContext,
    AccountContextService,
    _normalize_account,
    _normalize_open_order,
    _normalize_position,
)
from services.alpha_account_decision_overlay import (
    AccountFit,
    decision_payload,
    evaluate_account_fit,
)

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────
STATE_COLL = "alpha_overlay_state"
SHADOW_COLL = "alpha_overlay_shadow"
STATE_ID = "overlay"

MODE_SHADOW = "SHADOW"
MODE_HARD = "HARD_GATE"
MODE_FAULTED = "FAULTED"

TRADING_DAYS_REQUIRED = 7

# US NYSE market holidays we care about across the possible 7-day
# shadow window. Kept as a small fixed set — no external dep. When
# the shadow starts, only the next 30 days matter, so a rolling
# ~24-month window is more than sufficient.
_NYSE_HOLIDAYS_2026 = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}
_NYSE_HOLIDAYS_2027 = {
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 3, 26),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),
}
_NYSE_HOLIDAYS: set[date] = _NYSE_HOLIDAYS_2026 | _NYSE_HOLIDAYS_2027


def _now_ny() -> datetime:
    return datetime.now(tz=timezone.utc).astimezone(_NY)


def _ny_date_from(dt: Optional[datetime] = None) -> date:
    dt = dt or _now_ny()
    return dt.date()


def is_trading_day(d: date) -> bool:
    """True when ``d`` is a U.S. weekday and not an NYSE holiday."""
    if d.weekday() >= 5:  # Sat/Sun
        return False
    return d not in _NYSE_HOLIDAYS


def _trading_day_finished_ny(now: Optional[datetime] = None) -> Optional[date]:
    """Return today's NY date IF the trading session has closed
    (>= 16:00 ET) AND today is a trading day. Otherwise None.

    We only count a day when the session has fully closed so the
    counter never advances mid-session on a partial day."""
    now = now or _now_ny()
    today = now.date()
    if not is_trading_day(today):
        return None
    # After 16:00 ET the session is closed.
    close_time = dtime(16, 0)
    if now.time() >= close_time:
        return today
    return None


# ── broker adapters ─────────────────────────────────────────────────
class _SyncBrokerWrapper:
    """Duck-typed adapter that lets AccountContextService (async, generic)
    consume the sync PublicTradingService / MooMoo helpers."""

    def __init__(self, get_account_fn, get_positions_fn, get_orders_fn):
        self._get_account = get_account_fn
        self._get_positions = get_positions_fn
        self._get_orders = get_orders_fn

    async def get_account(self):
        return await asyncio.to_thread(self._get_account)

    async def list_positions(self):
        v = await asyncio.to_thread(self._get_positions)
        return v or []

    async def list_open_orders(self):
        v = await asyncio.to_thread(self._get_orders)
        # Only keep open/working orders.
        out = []
        for o in (v or []):
            status = str(o.get("status") or o.get("state") or "").lower()
            if status in ("filled", "cancelled", "canceled", "rejected", "expired"):
                continue
            out.append(o)
        return out


def _build_public_broker_wrapper(secret_key: str, account_id: str):
    from services.broker_service import PublicTradingService
    client = PublicTradingService(api_key=secret_key, api_secret=account_id)
    return _SyncBrokerWrapper(
        get_account_fn=client.get_account,
        get_positions_fn=client.get_positions,
        get_orders_fn=lambda: client.get_orders(status="open", limit=50),
    )


def _build_moomoo_broker_wrapper():
    from services import moomoo_broker_adapter as mm
    return _SyncBrokerWrapper(
        get_account_fn=mm.account_info,
        get_positions_fn=mm.positions,
        get_orders_fn=mm.orders,
    )


# Module-level TTL caches — one per broker.
_CACHES: dict[str, AccountContextService] = {}


def _svc_for(broker: str, wrapper) -> AccountContextService:
    if broker not in _CACHES or _CACHES[broker]._broker is not wrapper:  # noqa: SLF001
        _CACHES[broker] = AccountContextService(
            wrapper, broker_name=broker, ttl_seconds=10.0,
        )
    return _CACHES[broker]


async def get_account_context_for(broker: str, db=None) -> Optional[AccountContext]:
    """Return the live AccountContext for the routed broker.

    Returns ``None`` when the broker cannot be reached — the caller must
    treat this as a health signal (mark FAULTED, do not proceed with a
    hard-gate transition on this broker).
    """
    broker = (broker or "").lower()
    try:
        if broker == "public":
            # Grab creds via the executor's helper so we don't duplicate
            # the vault lookup logic here.
            from services.public_equity_live_executor import _aresolve_connect_creds
            creds = await _aresolve_connect_creds(db)
            if not creds:
                return None
            wrapper = _build_public_broker_wrapper(creds[0], creds[1])
        elif broker == "moomoo":
            wrapper = _build_moomoo_broker_wrapper()
        else:
            return None
        return await _svc_for(broker, wrapper).get()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[overlay] get_account_context_for(%s) failed: %s", broker, exc)
        return None


# ── state persistence ───────────────────────────────────────────────
async def _load_state(db) -> dict:
    if db is None:
        return {"mode": MODE_SHADOW, "trading_days_completed": 0}
    try:
        doc = await db[STATE_COLL].find_one({"_id": STATE_ID}, {"_id": 0})
    except Exception:  # noqa: BLE001
        doc = None
    if doc is None:
        doc = {
            "mode": MODE_SHADOW,
            "overlay_started_at": datetime.now(timezone.utc).isoformat(),
            "trading_days_completed": 0,
            "last_trading_day_counted": None,
            "hard_gate_at": None,
            "faulted": False,
            "fault_reason": None,
            "fault_since": None,
        }
        try:
            await db[STATE_COLL].insert_one({"_id": STATE_ID, **doc})
        except Exception:  # noqa: BLE001
            pass
    return doc


async def _save_state(db, patch: dict) -> None:
    if db is None:
        return
    try:
        await db[STATE_COLL].update_one(
            {"_id": STATE_ID}, {"$set": patch}, upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[overlay] state save failed: %s", exc)


async def _advance_trading_day_counter(db, state: dict) -> dict:
    """If a NY trading day just finished, increment the counter.
    Idempotent — repeated calls on the same day do not double-count."""
    today_finished = _trading_day_finished_ny()
    if today_finished is None:
        return state
    last = state.get("last_trading_day_counted")
    if last and str(last) >= today_finished.isoformat():
        return state  # already counted today

    days = int(state.get("trading_days_completed", 0)) + 1
    patch: dict = {
        "trading_days_completed": days,
        "last_trading_day_counted": today_finished.isoformat(),
    }
    # Auto-transition — only when not faulted.
    if (days >= TRADING_DAYS_REQUIRED
            and state.get("mode") == MODE_SHADOW
            and not state.get("faulted")):
        patch["mode"] = MODE_HARD
        patch["hard_gate_at"] = datetime.now(timezone.utc).isoformat()
    await _save_state(db, patch)
    return {**state, **patch}


async def _mark_fault(db, state: dict, reason: str) -> dict:
    if state.get("faulted") and state.get("fault_reason") == reason:
        return state
    patch = {
        "faulted": True,
        "fault_reason": reason,
        "fault_since": datetime.now(timezone.utc).isoformat(),
    }
    await _save_state(db, patch)
    return {**state, **patch}


async def _clear_fault(db, state: dict) -> dict:
    if not state.get("faulted"):
        return state
    patch = {"faulted": False, "fault_reason": None, "fault_since": None}
    await _save_state(db, patch)
    return {**state, **patch}


# ── shadow journalling ─────────────────────────────────────────────
async def _record_shadow(db, entry: dict) -> Optional[str]:
    if db is None:
        return None
    try:
        r = await db[SHADOW_COLL].insert_one({
            **entry,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return str(r.inserted_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[overlay] shadow record failed: %s", exc)
        return None


async def attach_shadow_outcome(db, shadow_id: str, outcome: dict) -> None:
    """Attach realized fill / P&L to a shadow record after the trade
    resolves. Used by the Day-7 transition report."""
    if db is None or not shadow_id:
        return
    from bson import ObjectId
    try:
        await db[SHADOW_COLL].update_one(
            {"_id": ObjectId(shadow_id)},
            {"$set": {"outcome": outcome,
                      "outcome_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[overlay] outcome attach failed: %s", exc)


# ── public API used by the executor ────────────────────────────────
class OverlayDecision:
    __slots__ = ("mode", "faulted", "verdict", "reasons", "size_multiplier",
                 "original_notional", "proposed_notional", "shadow_id",
                 "account_broker")

    def __init__(self, *, mode: str, faulted: bool, verdict: str,
                 reasons: tuple, size_multiplier: float,
                 original_notional: float, proposed_notional: float,
                 shadow_id: Optional[str], account_broker: str):
        self.mode = mode
        self.faulted = faulted
        self.verdict = verdict
        self.reasons = reasons
        self.size_multiplier = size_multiplier
        self.original_notional = original_notional
        self.proposed_notional = proposed_notional
        self.shadow_id = shadow_id
        self.account_broker = account_broker

    @property
    def should_block(self) -> bool:
        """Only enforce in HARD_GATE mode. SHADOW records only."""
        return self.mode == MODE_HARD and self.verdict == "BLOCK"

    @property
    def enforced_notional(self) -> float:
        """The notional the caller should actually route. In SHADOW,
        always the original. In HARD_GATE, the reduced amount."""
        if self.mode == MODE_HARD:
            return self.proposed_notional
        return self.original_notional


async def evaluate_intent(
    db,
    *,
    broker: str,
    symbol: str,
    side: str,
    notional: float,
) -> OverlayDecision:
    """Central entrypoint. Called by the executor before place_order.

    Steps:
        1. Load persistent state
        2. Advance the trading-day counter if today's session closed
        3. Read the ROUTED broker's account (never the other one)
        4. Run the deterministic fit overlay
        5. Journal the shadow record (mode + verdict + reasons)
        6. Return an OverlayDecision the executor uses to gate or size

    If the account read fails, the overlay is marked FAULTED and the
    caller is told to fall through to the pre-existing execution/risk
    path — never silently blocks.
    """
    state = await _load_state(db)
    state = await _advance_trading_day_counter(db, state)

    account_broker = (broker or "").lower()
    ctx = await get_account_context_for(account_broker, db=db)

    if ctx is None:
        state = await _mark_fault(
            db, state,
            reason=f"broker_read_failed({account_broker})",
        )
        # Still journal that we couldn't evaluate so the transition
        # report shows broker-read failures honestly.
        shadow_id = await _record_shadow(db, {
            "broker": account_broker,
            "symbol": symbol,
            "side": side,
            "original_notional": float(notional),
            "proposed_notional": float(notional),
            "verdict": "FAULTED_READ",
            "reasons": ["broker_read_failed"],
            "mode_at_time": state.get("mode", MODE_SHADOW),
            "faulted": True,
        })
        return OverlayDecision(
            mode=state.get("mode", MODE_SHADOW),
            faulted=True,
            verdict="FAULTED_READ",
            reasons=("broker_read_failed",),
            size_multiplier=1.0,
            original_notional=float(notional),
            proposed_notional=float(notional),
            shadow_id=shadow_id,
            account_broker=account_broker,
        )

    # Broker read succeeded — clear any prior fault.
    if state.get("faulted"):
        state = await _clear_fault(db, state)

    fit = evaluate_account_fit(
        context=ctx, symbol=symbol, side=side.upper(), desired_notional=float(notional),
    )
    proposed = round(max(0.0, float(notional) * fit.size_multiplier), 2)

    shadow_id = await _record_shadow(db, {
        "broker": account_broker,
        "symbol": symbol,
        "side": side,
        "original_notional": float(notional),
        "proposed_notional": proposed,
        "verdict": fit.verdict,
        "reasons": list(fit.reasons),
        "size_multiplier": fit.size_multiplier,
        "mode_at_time": state.get("mode", MODE_SHADOW),
        "faulted": False,
    })

    return OverlayDecision(
        mode=state.get("mode", MODE_SHADOW),
        faulted=False,
        verdict=fit.verdict,
        reasons=fit.reasons,
        size_multiplier=fit.size_multiplier,
        original_notional=float(notional),
        proposed_notional=proposed,
        shadow_id=shadow_id,
        account_broker=account_broker,
    )


# ── state read for UI ──────────────────────────────────────────────
async def status(db) -> dict:
    """Everything the admin UI needs: mode, day counter, fault flags."""
    state = await _load_state(db)
    state = await _advance_trading_day_counter(db, state)
    days = int(state.get("trading_days_completed", 0))
    return {
        "mode": state.get("mode", MODE_SHADOW),
        "faulted": bool(state.get("faulted", False)),
        "fault_reason": state.get("fault_reason"),
        "fault_since": state.get("fault_since"),
        "overlay_started_at": state.get("overlay_started_at"),
        "trading_days_completed": days,
        "trading_days_required": TRADING_DAYS_REQUIRED,
        "days_remaining": max(0, TRADING_DAYS_REQUIRED - days),
        "hard_gate_at": state.get("hard_gate_at"),
        "last_trading_day_counted": state.get("last_trading_day_counted"),
    }


async def transition_report(db) -> dict:
    """Aggregate every shadow record since ``overlay_started_at`` and
    return the day-7 report the operator asked for."""
    state = await _load_state(db)
    started_at = state.get("overlay_started_at")
    q: dict = {}
    if started_at:
        q["created_at"] = {"$gte": started_at}
    if db is None:
        return {"started_at": started_at, "counts": {}, "records": []}
    try:
        cur = db[SHADOW_COLL].find(q, {"_id": 0}).sort("created_at", -1)
        records = [r async for r in cur]
    except Exception:  # noqa: BLE001
        records = []
    counts = {"PASS": 0, "REDUCE": 0, "BLOCK": 0, "HOLD": 0,
              "FAULTED_READ": 0, "other": 0}
    total_sizing_delta = 0.0
    avoided_losses = 0.0
    blocked_winners = 0.0
    read_failures = 0
    for r in records:
        v = r.get("verdict", "other")
        counts[v] = counts.get(v, 0) + 1
        if v == "FAULTED_READ":
            read_failures += 1
        orig = float(r.get("original_notional") or 0.0)
        prop = float(r.get("proposed_notional") or 0.0)
        if orig > prop:
            total_sizing_delta += (orig - prop)
        outcome = r.get("outcome") or {}
        realized = float(outcome.get("realized_pl") or 0.0)
        if v == "BLOCK":
            if realized < 0:
                avoided_losses += abs(realized)
            elif realized > 0:
                blocked_winners += realized
    return {
        "started_at": started_at,
        "trading_days_completed": int(state.get("trading_days_completed", 0)),
        "mode": state.get("mode"),
        "faulted": bool(state.get("faulted", False)),
        "counts": counts,
        "total_sizing_delta_usd": round(total_sizing_delta, 2),
        "avoided_losses_usd": round(avoided_losses, 2),
        "blocked_winners_usd": round(blocked_winners, 2),
        "broker_read_failures": read_failures,
        "total_records": len(records),
        "records": records[:200],  # cap payload
    }


async def ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        await db[SHADOW_COLL].create_index([("created_at", -1)])
        await db[SHADOW_COLL].create_index([("broker", 1), ("created_at", -1)])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[overlay] ensure_indexes failed: %s", exc)


__all__ = [
    "MODE_SHADOW", "MODE_HARD", "MODE_FAULTED",
    "TRADING_DAYS_REQUIRED",
    "OverlayDecision",
    "evaluate_intent",
    "status",
    "transition_report",
    "attach_shadow_outcome",
    "ensure_indexes",
    "is_trading_day",
    "get_account_context_for",
]
