"""Lane-separated executor — crypto lane.

24/7 lane: NO market-hours gate. Crypto venues are continuous; an
equity-style "is the session open?" check would produce false skips
during normal weekend trading.

Symbolic gates (deterministic, run BEFORE Fast Veto / Council):
  1. (Future) Per-pair maintenance / halted-pair lookup
  2. (Future) Per-jurisdiction blocked-symbol lookup

For now there is no blocking gate — the lane runs through to the
shared executor body with ``lane="crypto"`` so the Fast Veto layer
applies crypto-tuned thresholds and the shadow log is tagged.

Tracing (2026-05-17): emits ``CRYPTO_ADAPTER_REACHED`` so the
operator can confirm a routed intent actually hit this lane vs.
being silently routed elsewhere (the symptom that prompted the
Phase A diagnostic instrumentation).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from services.executors._shared import CRYPTO_LANE
from shared.runtime.platform_survival import broker_verify_receipt

logger = logging.getLogger(__name__)


_REQUIRE_RECEIPT = os.environ.get("RISEDUAL_REQUIRE_MC_RECEIPT", "0") == "1"


async def execute_crypto_signal(
    signal: dict,
    market_data: dict,
    tier3_readiness: dict,
    config: Any,
    open_positions: Optional[List[dict]] = None,
    equity_curve: Optional[List[float]] = None,
    bot_capital: Optional[float] = None,
) -> Dict[str, Any]:
    """Crypto-lane executor entry point.

    Same signature as the legacy ``execute_signal`` so callers can
    swap directly. The lane runs 24/7 — no market-hours pre-gate.
    """
    # Pull or mint a trace_id so this entry shows up in the operator's
    # grep alongside ALPHA_CRYPTO_INTENT_CREATED / MC_CRYPTO_POST_SENT.
    tid = str(signal.get("trace_id") or uuid.uuid4().hex[:8])
    logger.info(
        "[%s] CRYPTO_ADAPTER_REACHED symbol=%s direction=%s confidence=%s",
        tid,
        signal.get("symbol"), signal.get("direction"), signal.get("confidence"),
    )

    # ── Survival-layer broker-side verification ──────────────────
    # If the upstream emission carried an mc_receipt, verify the HMAC
    # signature against the shared secret. This is the brake that
    # prevents a sidecar with hidden authority from coercing the
    # broker — the broker accepts ONLY what MC signed.
    #
    # Mode is governed by ``RISEDUAL_REQUIRE_MC_RECEIPT``:
    #   * 1   → unverified / missing receipts return SKIPPED
    #   * 0/unset → graceful degrade (log + proceed) so flow keeps
    #               running while the env var rolls out.
    receipt = signal.get("mc_receipt")
    if receipt:
        verdict = broker_verify_receipt(receipt)
        if not verdict["ok"]:
            logger.warning(
                "[%s] CRYPTO_RECEIPT_INVALID reason=%s require=%s",
                tid, verdict["reason"], _REQUIRE_RECEIPT,
            )
            if _REQUIRE_RECEIPT:
                return {
                    "skipped": True,
                    "reason": f"RECEIPT_{verdict['reason']}",
                    "lane": CRYPTO_LANE.name,
                    "trace_id": tid,
                }
        else:
            logger.info(
                "[%s] CRYPTO_RECEIPT_VERIFIED symbol=%s",
                tid, verdict.get("symbol"),
            )
    elif _REQUIRE_RECEIPT:
        logger.warning(
            "[%s] CRYPTO_RECEIPT_MISSING — refusing submit "
            "(RISEDUAL_REQUIRE_MC_RECEIPT=1)", tid,
        )
        return {
            "skipped": True,
            "reason": "RECEIPT_MISSING",
            "lane": CRYPTO_LANE.name,
            "trace_id": tid,
        }

    # Lazy import to avoid a circular import at module load
    # (trading_bot_service imports executors.__init__).
    from services.trading_bot_service import execute_signal as _core_execute
    result = await _core_execute(
        signal=signal,
        market_data=market_data,
        tier3_readiness=tier3_readiness,
        config=config,
        open_positions=open_positions,
        equity_curve=equity_curve,
        bot_capital=bot_capital,
        lane=CRYPTO_LANE.name,
    )
    # Tag the result with the trace id so upstream loggers can chain.
    if isinstance(result, dict):
        result.setdefault("trace_id", tid)
        outcome = "SUBMITTED" if not result.get("skipped") and not result.get("error") else "SKIPPED"
        logger.info(
            "[%s] CRYPTO_BROKER_%s reason=%s qty=%s",
            tid, outcome,
            result.get("reason") or result.get("error") or "ok",
            result.get("qty"),
        )
    return result
