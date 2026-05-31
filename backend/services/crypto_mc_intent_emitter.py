"""Crypto → MC intent emitter (2026-05-31).

Phase B of the "all Alpha actions auditable on MC" doctrine: every
crypto paper trade opened by ``crypto_paper_trader.run_crypto_symbol``
now emits an intent envelope to MC's ``/api/intents`` endpoint via
``sovereign/intent_bridge``.

Architectural posture
---------------------
* **Fire-and-forget.** A failed MC POST NEVER blocks or reverts the
  local crypto paper trade. The trade already landed in
  ``crypto_paper_trades``; this emission is pure audit lineage.
* **Same dry-run gate chain.** MC sees the intent with
  ``may_execute=False, requires_gate_pass=True`` (per MC's brain
  contract). The intent reflects what Alpha *did* — not a request
  for execution. MC's gate scoring becomes observability, not a
  blocker.
* **Doctrine pin.** Crypto remains its own lane on MC's dashboard
  (`lane="crypto"`); intents from this path are tagged
  ``stack="alpha"`` like equity intents.

Why isolated
------------
The crypto_paper_trader was originally documented as an "isolated
24/7 entry executor". This module is the *bridge*, not a replacement
— if MC is unreachable for a week, the crypto bot keeps trading
locally. The audit trail just gaps until MC is back.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


_DIRECTION_TO_ACTION: dict[str, str] = {
    "LONG": "BUY",
    "BUY": "BUY",
    "SHORT": "SELL",   # wire-level: SHORT crypto = SELL spot/short-margin
    "SELL": "SELL",
    "COVER": "BUY",    # closing a SHORT = BUY back
}


def _to_action(direction: str) -> Optional[str]:
    """Map crypto trade directions to MC's wire vocabulary."""
    if not direction:
        return None
    return _DIRECTION_TO_ACTION.get(direction.strip().upper())


def _build_receipt(
    trade: Mapping[str, Any], signal: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Construct an intent_bridge-compatible receipt from a crypto trade.

    Returns ``None`` when the trade isn't directional (HOLD etc.) —
    intent_bridge would skip it anyway, but short-circuiting here
    keeps the MC round-trip from happening.
    """
    direction = (trade.get("direction") or "").strip().upper()
    action = _to_action(direction)
    if action is None:
        return None

    symbol = str(trade.get("symbol") or "").upper()
    entry_price = trade.get("entry_price")
    try:
        entry_f = float(entry_price) if entry_price is not None else None
    except (TypeError, ValueError):
        entry_f = None

    try:
        confidence = float(trade.get("confidence") or signal.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    receipt: dict[str, Any] = {
        "symbol": symbol,
        "verdict": action,           # intent_bridge maps this to action
        "direction": action,
        "confidence": confidence,
        "lane": "crypto",
        "entry_price": entry_f,
        "snapshot": {
            "price": entry_f,
            "spread_bps": signal.get("spread_bps"),
            "relative_volume": signal.get("relative_volume"),
            "has_news": bool(signal.get("has_news")),
        },
        # Honesty trailer — surface the council's raw signal so MC's
        # honesty scorer can audit upstream chain.
        "raw_action": action,
        "market_decision": action,
        "trade_id": trade.get("trade_id"),
        "size_usd": trade.get("size_usd"),
        "qty": trade.get("size") or trade.get("qty"),
    }
    return receipt


async def emit_crypto_intent(
    trade: Mapping[str, Any],
    signal: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Emit a crypto-trade intent to MC. Fail-soft; never raises.

    Returns MC's response dict on success, ``None`` on any skip path
    (non-directional verdict, MC unreachable, missing token, etc.).
    """
    if (os.environ.get("RISEDUAL_CRYPTO_EMIT_INTENTS") or "true").strip().lower() in (
        "false", "0", "no", "off",
    ):
        return None

    receipt = _build_receipt(trade, signal)
    if receipt is None:
        return None

    try:
        from sovereign.intent_bridge import emit_intent_from_consensus
        from sovereign.mc_client import MCClient
    except Exception as exc:  # noqa: BLE001
        logger.debug("[crypto-mc-emit] bridge import failed: %s", exc)
        return None

    mc_url = (
        os.environ.get("RISEDUAL_MC_URL")
        or os.environ.get("MC_URL")
        or os.environ.get("MC_BASE_URL")
        or ""
    ).strip()
    token = (
        os.environ.get("ALPHA_MC_INGEST_TOKEN")
        or os.environ.get("ALPHA_INGEST_TOKEN")
        or ""
    ).strip()
    if not mc_url or not token:
        logger.debug(
            "[crypto-mc-emit] skipped — missing %s",
            "mc_url" if not mc_url else "token",
        )
        return None

    try:
        client = MCClient(base_url=mc_url, brain="alpha", runtime_token=token)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[crypto-mc-emit] client init failed: %s", exc)
        return None

    qty_raw = trade.get("size") or trade.get("qty") or 1.0
    try:
        qty = float(qty_raw)
        if not (qty > 0):
            qty = 1.0
    except (TypeError, ValueError):
        qty = 1.0

    try:
        response = await emit_intent_from_consensus(
            client, receipt, qty=qty, notes="alpha crypto bot tick",
        )
        if response is not None:
            logger.info(
                "[crypto-mc-emit] %s %s emitted to MC trade_id=%s",
                receipt["symbol"], receipt["direction"], trade.get("trade_id"),
            )
        return response
    except Exception as exc:  # noqa: BLE001
        # emit_intent_from_consensus already swallows MCClientError;
        # this catches anything more exotic (DNS, asyncio loop issues).
        logger.warning(
            "[crypto-mc-emit] non-fatal emit failure: symbol=%s err=%s",
            receipt.get("symbol"), exc,
        )
        return None


__all__ = ["emit_crypto_intent"]
