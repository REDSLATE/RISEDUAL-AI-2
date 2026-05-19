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
import os
import uuid
from typing import Any, Mapping

from shared.runtime.platform_survival import (
    mc_canonical_gate as _survival_gate,
    sidecar_build_intent as _survival_build_intent,
)
from sovereign.intent_receipt import consensus_receipt_to_intent_fields
from sovereign.mc_client import MCClient, MCClientError

logger = logging.getLogger(__name__)


# Verdicts the bridge will emit. HOLD / NEUTRAL / unknown verdicts
# are not intents — they're opinions and stay in the local receipt
# only. (`ALLOWED_ACTIONS` over in mc_client.py also includes HOLD
# for the schema's sake; this set is stricter on purpose.)
_DIRECTIONAL = frozenset({"BUY", "SELL", "SHORT", "COVER"})

# Survival layer wiring (2026-05-17 Phase A2):
#
# Every emission now runs through ``mc_canonical_gate`` locally as a
# pre-flight mirror of what the remote MC will decide. The kernel
# attaches a stamped runtime envelope (env / git_sha / policy_hash /
# local_execution_authority=False) and either approves with an
# HMAC-signed receipt or surfaces an explicit reason string.
#
# Mode is governed by ``RISEDUAL_SURVIVAL_ENFORCE``:
#   * unset/0  → soft-warn (current default). Pre-flight failures log
#                a `SURVIVAL_PREFLIGHT_DENY` line but emission still
#                fires. Lets us observe the kernel in prod without
#                gating real flow.
#   * 1        → hard-block. Failed pre-flight short-circuits the
#                emission and returns None — same effect as a non-
#                directional verdict.
#
# Broker-side verification lives in the lane executors; this is the
# brain-side half of the wiring.
_SURVIVAL_ENFORCE = os.environ.get("RISEDUAL_SURVIVAL_ENFORCE", "0") == "1"


# Crypto-vs-equity lane classifier. Used only to tag the trace ID so
# the operator can grep CRYPTO vs EQUITY across the pipeline without
# touching anything else in the council math.
_CRYPTO_HINTS = ("/USD", "/USDT", "/USDC", "-USD", "BTC", "ETH", "SOL", "XRP",
                 "DOGE", "ADA", "BNB", "AVAX", "LINK", "MATIC", "DOT")


def _classify_lane(symbol: str) -> str:
    s = (symbol or "").upper()
    if any(h in s for h in _CRYPTO_HINTS):
        return "CRYPTO"
    return "EQUITY"


def _new_trace_id() -> str:
    """8-char trace id — short enough to grep, long enough to be unique."""
    return uuid.uuid4().hex[:8]


def _build_emission_kwargs(
    receipt: Mapping[str, Any], *, qty: float, notes: str,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Compose post_intent kwargs from a doctrine receipt.

    Returns None when the receipt is non-directional (caller should
    skip emission). Returns a kwargs dict ready to splat into
    ``MCClient.post_intent`` otherwise.

    A ``trace_id`` is auto-generated if not supplied and stamped onto
    the payload so the operator can follow one intent through
    Alpha → MC → executor → broker logs end-to-end.
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
    tid = trace_id or _new_trace_id()
    lane = _classify_lane(symbol)

    # First trace boundary — the brain has decided to emit. Anything
    # downstream that doesn't echo this trace_id is the failure point.
    logger.info(
        "[%s] ALPHA_%s_INTENT_CREATED symbol=%s side=%s conf=%.3f exec=%s",
        tid, lane, symbol, raw, final_unit,
        receipt_for_bridge.get("execution_decision", "?"),
    )

    # ── Survival-layer pre-flight ─────────────────────────────────
    # Local mirror of mc_canonical_gate. Builds a runtime envelope
    # (env / git_sha / policy_hash / local_execution_authority=False),
    # runs the kernel, attaches the signed receipt to the outgoing
    # body. The receipt rides on the wire so the broker adapter — in
    # this service or any sibling service — can re-verify.
    envelope = _survival_build_intent(
        brain_id="alpha",
        lane=lane.lower(),
        symbol=symbol,
        direction=raw,
        confidence=final_unit,
        room_id=os.environ.get("RISEDUAL_SIDECAR_ROOM", "alpha"),
    )
    survival_verdict = _survival_gate(envelope)

    if not survival_verdict["accepted"]:
        reason = survival_verdict["reason"]
        if _SURVIVAL_ENFORCE:
            logger.warning(
                "[%s] SURVIVAL_PREFLIGHT_BLOCK lane=%s symbol=%s reason=%s "
                "errors=%s",
                tid, lane, symbol, reason, survival_verdict.get("errors", []),
            )
            return None
        # Soft mode: warn but proceed. Lets us observe the kernel
        # without gating real flow.
        logger.warning(
            "[%s] SURVIVAL_PREFLIGHT_SOFT_DENY lane=%s symbol=%s reason=%s "
            "(set RISEDUAL_SURVIVAL_ENFORCE=1 to hard-block)",
            tid, lane, symbol, reason,
        )
    else:
        logger.info(
            "[%s] SURVIVAL_PREFLIGHT_OK lane=%s symbol=%s policy_hash=%s",
            tid, lane, symbol,
            survival_verdict["receipt"].get("mc_policy_hash", "?")[:8],
        )

    return {
        "symbol": symbol,
        "side": raw,
        "qty": float(qty),
        "confidence": final_unit,
        "notes": notes,
        "trace_id": tid,
        "mc_receipt": survival_verdict["receipt"],
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

    2026-05-19 doctrine update: every directional emission is now
    enriched with a normalized market snapshot before going on the
    wire (see ``services.intent_enrichment``). Brains MUST NOT POST
    ``snapshot:{}`` — MC reads missing fields as sentinel values
    and the doctrine score collapses. Enrichment is best-effort and
    always populates the seven canonical keys; sentinel values fill
    in when the upstream quote provider is unavailable.
    """
    # Build the emission kwargs first — non-directional verdicts
    # short-circuit here without contacting MC or fetching a quote.
    kwargs = _build_emission_kwargs(receipt, qty=qty, notes=notes)
    if kwargs is None:
        return None

    # Enrich with a normalized snapshot. The helper logs a
    # SNAPSHOT_ENRICHED line so the operator can grep emissions and
    # see which carry real data vs sentinel fills.
    try:
        from services.intent_enrichment import enrich_intent_with_snapshot
        kwargs = await enrich_intent_with_snapshot(kwargs)
    except Exception as exc:  # noqa: BLE001
        # Never let the snapshot fetcher block an emission — MC's
        # classifier handles missing snapshots gracefully.
        logger.warning(
            "[%s] SNAPSHOT_ENRICH_FAILED symbol=%s err=%s",
            kwargs.get("trace_id", "--------"),
            kwargs.get("symbol"), exc,
        )

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: client.post_intent(**kwargs),
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
