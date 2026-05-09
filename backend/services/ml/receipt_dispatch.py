"""Shared fire-and-forget helper for ADL-receipt scheduling.

ADL-1 (operator-mandated 2026-05-09). Centralises the
``asyncio.create_task(run_shadow_pipeline(...))`` boilerplate
that was previously inlined in ``trading_bot_service.execute_signal``
so other executor entry points (crypto_paper_trader, day-trade
pipeline, options paper bot) can adopt it without copy-pasting.

Authority-boundary contract
---------------------------
* Caller's execution path is NEVER blocked by receipt-write failure.
* Receipt-write failures emit a single ``logger.warning`` and are
  swallowed.
* No broker imports, no order placement, no position mutation.
* No env reads, no env mutation.
* This module does NOT issue verdicts — ``run_shadow_pipeline``
  decides what receipts (if any) get written.

Usage::

    from services.ml.receipt_dispatch import schedule_shadow_receipt

    schedule_shadow_receipt(
        db=db,
        signal=signal_dict,
        market_data=market_data_dict_or_None,
        lane="crypto",
        requested_notional_usd=float(notional),
        open_positions=open_positions,
        equity_curve=equity_curve,
        bot_capital=bot_capital_usd,
    )

The call returns immediately. The receipt write happens on the
event loop in the background. Caller continues unblocked.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def schedule_shadow_receipt(
    db: Any,
    *,
    signal: Dict[str, Any],
    market_data: Optional[Dict[str, Any]],
    lane: str,
    requested_notional_usd: float,
    open_positions: Optional[List[Dict[str, Any]]] = None,
    equity_curve: Optional[List[float]] = None,
    bot_capital: float = 0.0,
    source: str = "executor",
) -> bool:
    """Fire-and-forget the Phase 5a shadow pipeline.

    Wraps the ``asyncio.create_task(run_shadow_pipeline(...))``
    pattern with the surrounding try/except. Returns ``True`` if
    the task was scheduled, ``False`` on any failure (no event
    loop, no Mongo handle, broken import, etc.) — the caller's
    execution path is identical either way.

    Args:
        db: Mongo handle. ``None`` short-circuits with ``False``.
        signal: signal dict — at minimum a `symbol`. ``run_shadow_pipeline``
            owns the validation; we don't pre-check beyond presence.
        market_data: optional pre-fetched market dict; ``None`` lets
            the pipeline fill live values via ``extract_live_features``.
        lane: ``"equity"`` / ``"crypto"`` / ``"options"`` / etc.
            Tagged onto every receipt.
        requested_notional_usd: USD-notional the caller intended to
            trade if approved. May be 0.0 for read-only / scan
            decisions.
        open_positions / equity_curve / bot_capital: pre-flight
            risk-context inputs. Passed through verbatim.
        source: free-form tag used only in the failure log message
            so we can tell which executor failed to schedule a receipt.

    Returns:
        ``True`` if the task was scheduled successfully, ``False`` on
        ANY failure. Caller does NOT branch on the return value for
        execution decisions — it's purely diagnostic.
    """
    if db is None:
        return False
    try:
        from services.ml.shadow_wiring import run_shadow_pipeline
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[adl-receipt] shadow_wiring import failed (%s): %s",
            source, exc,
        )
        return False

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller is sync (e.g. CLI / cron). The
            # receipt-write requires the live FastAPI event loop, so
            # we silently skip rather than spin up a one-shot loop
            # (which would leak resources and block the caller).
            return False
        loop.create_task(run_shadow_pipeline(
            db,
            signal=signal,
            market_data=market_data,
            lane=lane,
            requested_notional_usd=float(requested_notional_usd),
            open_positions=open_positions,
            equity_curve=equity_curve,
            bot_capital=float(bot_capital or 0.0),
        ))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[adl-receipt] schedule failed (%s, lane=%s): %s",
            source, lane, exc,
        )
        return False
