"""Fire a consensus receipt to MC as an intent.

This is the one-call wiring point — given a doctrine receipt from
``_weighted_consensus``, emit a properly-shaped intent POST to MC's
``/api/intents`` with the full honesty trailer attached.

Doctrine guard rails baked in:

  * Only directional verdicts (BUY / SELL / SHORT / COVER) trigger an
    emission. ``ALL_HOLD`` consensus passes through silently because
    "I have no opinion" is not an intent — emitting it would pollute
    MC's intent feed with no-ops.
  * RISEDUAL is doctrinally headless under V3 — every intent is
    stamped ``execution_decision="OBSERVE_ONLY"`` so MC's executor
    seat can route at will without thinking we're claiming execution
    authority.
  * Quantity is unit-scale (``1.0``) by default — Alpha is an
    advisor/decider, not a sizer. MC's executor multiplies by its
    own sizing model. Callers that DO know a desired size can pass
    ``qty=`` explicitly.
  * Fire-and-forget: emission is a sync call wrapped in
    ``run_in_executor`` from async callers, and exceptions are
    swallowed with a log line. A flaky MC must never block a brain
    from producing the next consensus.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from sovereign.intent_receipt import consensus_receipt_to_intent_fields
from sovereign.mc_client import MCClient, MCClientError

logger = logging.getLogger(__name__)


# Verdicts the bridge will emit. HOLD / NEUTRAL / unknown verdicts
# are not intents — they're opinions and stay in the local receipt
# only. (`ALLOWED_ACTIONS` over in mc_client.py also includes HOLD
# for the schema's sake; this set is stricter on purpose.)
_DIRECTIONAL = frozenset({"BUY", "SELL", "SHORT", "COVER"})


def _build_emission_kwargs(
    receipt: Mapping[str, Any], *, qty: float, notes: str,
) -> dict[str, Any] | None:
    """Compose post_intent kwargs from a doctrine receipt.

    Returns None when the receipt is non-directional (caller should
    skip emission). Returns a kwargs dict ready to splat into
    ``MCClient.post_intent`` otherwise.
    """
    raw = str(receipt.get("raw_action") or receipt.get("market_decision") or "").upper()
    if raw not in _DIRECTIONAL:
        return None

    # Final confidence sits on the receipt as percent. Clamp + scale.
    final_pct = receipt.get("final_confidence", receipt.get("confidence", 0))
    try:
        final_unit = max(0.0, min(1.0, float(final_pct) / 100.0))
    except (TypeError, ValueError):
        final_unit = 0.5

    symbol = str(receipt.get("symbol") or "").upper()
    if not symbol:
        return None

    # 2026-05-17 Operator Override: under Doctrine V3 the brain
    # *requests* execution from MC instead of stamping OBSERVE_ONLY.
    # MC's executor seat still owns the final yes/no; the brain
    # simply stops pre-filing every intent as advisory-only.
    receipt_for_bridge = dict(receipt)
    receipt_for_bridge.setdefault("execution_decision", "ALLOW")

    honesty = consensus_receipt_to_intent_fields(receipt_for_bridge)

    return {
        "symbol": symbol,
        "side": raw,
        "qty": float(qty),
        "confidence": final_unit,
        "notes": notes,
        **honesty,
    }


def emit_intent_sync(
    client: MCClient,
    receipt: Mapping[str, Any],
    *,
    qty: float = 1.0,
    notes: str = "",
) -> dict[str, Any] | None:
    """Synchronous emit — returns MC's response, ``None`` if skipped.

    Doctrine guard rails apply: non-directional verdicts return None
    without contacting MC. MC errors are re-raised so test code can
    assert on them; the async wrapper below swallows them.
    """
    kwargs = _build_emission_kwargs(receipt, qty=qty, notes=notes)
    if kwargs is None:
        return None
    return client.post_intent(**kwargs)


async def emit_intent_from_consensus(
    client: MCClient,
    receipt: Mapping[str, Any],
    *,
    qty: float = 1.0,
    notes: str = "alpha consensus tick",
) -> dict[str, Any] | None:
    """Async fire-and-forget wrapper around :func:`emit_intent_sync`.

    This is the one call brain runtimes need to make after a
    consensus tick. Five lines at the call site:

    .. code-block:: python

        from sovereign.intent_bridge import emit_intent_from_consensus
        from sovereign.mc_client import MCClient

        mc = MCClient(base_url=..., brain="alpha", runtime_token=...)
        receipt = await generate_hypothesis(..., model="consensus")
        await emit_intent_from_consensus(mc, receipt)

    MC failures are logged at WARNING and swallowed — the next tick
    will retry on its own. Non-directional verdicts are skipped
    silently and return ``None``.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: emit_intent_sync(client, receipt, qty=qty, notes=notes),
        )
    except MCClientError as exc:
        logger.warning(
            "emit_intent_from_consensus failed (non-fatal): symbol=%s err=%s",
            receipt.get("symbol"), exc,
        )
        return None


__all__ = [
    "emit_intent_from_consensus",
    "emit_intent_sync",
]
