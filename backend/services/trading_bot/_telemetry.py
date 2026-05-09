"""Telemetry / receipt helpers for the trading-bot service.

Step 4B extraction (2026-05-09). Verbatim moves of two file-private
helpers from ``services/trading_bot_service.py``:

  * ``fire_equity_shadow`` — Research Shadow Layer fire-and-forget
    alternative-engine logger. Kicks off
    ``services.research_shadow_engines.fire_shadow`` as a background
    task. Tier-3 firewall enforced inside that callee
    (``research_shadow_decisions`` is the only collection it writes
    to). Never blocks, never raises out — setup failures are logged
    at warning level.
  * ``record_kill_switch_outcome`` — Step 8 of execute_signal. Feeds
    broker outcome into the kill-switch error window. ``{"error":
    ...}`` dicts count as failures alongside raised exceptions.

Both are receipt-side only: no broker calls, no decision logic, no
sizing, no DB queries, no response-shape changes.

The shim in ``trading_bot_service`` re-exports these under the
original ``_fire_equity_shadow`` / ``_record_kill_switch_outcome``
private names so call sites don't change.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def fire_equity_shadow(
    *, synthetic_bot: dict, signal: dict, symbol: str, price: float,
) -> None:
    """Research Shadow Layer — fire-and-forget alternative-engine
    logger for equities. Per-bot config: ``shadow_engine`` +
    ``shadow_paused`` on the bot doc. Tier-3 firewall is enforced
    inside :mod:`services.research_shadow_logger` (it only writes
    to ``research_shadow_decisions``).

    Never blocks, never raises out — any setup failure is logged
    at warning level and swallowed.
    """
    shadow_engine = synthetic_bot.get("shadow_engine") or "none"
    if shadow_engine not in ("adversarial", "council"):
        return

    try:
        import asyncio as _asyncio_eq_shadow
        # Deferred import to break the circular dep — by the time
        # this callback runs, ``trading_bot_service`` is fully
        # initialised. ``_resolve_db_for_shadow`` keeps the DB
        # lookup inside that module so this helper never touches
        # the ``_db`` module global directly.
        from services.trading_bot_service import _resolve_db_for_shadow
        from services.research_shadow_engines import fire_shadow as _fire_shadow_eq
        _asyncio_eq_shadow.create_task(_fire_shadow_eq(
            _resolve_db_for_shadow(),
            bot_id=str(
                synthetic_bot.get("_id")
                or synthetic_bot.get("bot_id")
                or "equity_bot"
            ),
            user_id=str(synthetic_bot.get("user_id") or "system"),
            symbol=symbol,
            asset_type=signal.get("asset_type") or "stock",
            decision_phase="entry",
            active_engine="confluence",
            active_action=signal.get("direction") or "LONG",
            shadow_engine=shadow_engine,
            signal=signal,
            mid_price=float(price),
            shadow_paused=bool(synthetic_bot.get("shadow_paused")),
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[equity-bot] shadow fire-and-forget setup failed for %s: %s",
            symbol, exc,
        )


def record_kill_switch_outcome(order: Any) -> None:
    """Step 8 of execute_signal — feed broker outcome into the
    kill-switch error window. ``{"error": ...}`` dicts count as
    failures alongside raised exceptions, so the error-rate trip
    fires on 4xx/5xx storms, not just uncaught Python errors.
    """
    from ai_core.kill_switch import kill_switch

    is_failure = isinstance(order, dict) and order.get("error") is not None
    kill_switch.record_result(success=not is_failure)
    if is_failure:
        trip, trip_reason = kill_switch.should_trip()
        if trip:
            kill_switch.activate(trip_reason)
