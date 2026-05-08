"""Shelly Shadow Delta Logger.

Patent M (Alpha) — Rollout step 3.

Captures *what the consumer WOULD have done* to confidence and
risk_multiplier if Patent M were wired into decisions, without
actually applying any changes. Builds the audit history that
gates rollout step 5 (gated confidence influence).

Hard contract
-------------
* Reads ``payload["learning_core"]`` (the corridor annotation
  attached at rollout step 2). Skips silently if absent.
* Computes the consumer's hypothetical mutation by deep-copying
  the payload first — the *real* payload is never touched.
* Writes one row per evaluation into Mongo collection
  ``shelly_shadow_deltas`` (best-effort; Mongo failures are
  logged + swallowed).
* Behind env flag ``LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED``
  (default off).
* The collection grows append-only. Operator can curl
  ``/api/admin/learning-core/shadow-deltas`` to review.

Schema (``shelly_shadow_deltas``)
---------------------------------
{
  "ts":                              ISO datetime str,
  "symbol":                          str,
  "decision":                        str  (raw from payload),
  "direction_canonical":             str  (from learning_core),
  "confidence_before":               float,
  "confidence_hypothetical":         float,
  "confidence_delta":                float,
  "risk_multiplier_before":          float,
  "risk_multiplier_hypothetical":    float,
  "risk_multiplier_delta":           float,
  "memory_win_rate":                 float | None,
  "similar_memory_count":            int,
  "pretell_warning_present":         bool,
  "would_have_consumed":             bool,
}
"""

from __future__ import annotations

import copy
import logging
import os
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


COLLECTION_NAME = "shelly_shadow_deltas"


def _enabled() -> bool:
    return os.getenv(
        "LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "false",
    ).lower() == "true"


async def log_shadow_delta(
    db: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Compute and persist a shadow-delta row.

    Returns a small envelope describing what happened. NEVER
    raises — best-effort throughout.

    Returns
    -------
    ``{"logged": True}``      — row written.
    ``{"logged": False, "reason": "..."}`` — short-circuited.
    """
    if not _enabled():
        return {"logged": False, "reason": "env_flag_off"}
    if db is None:
        return {"logged": False, "reason": "db_handle_none"}

    lc = payload.get("learning_core")
    if not isinstance(lc, dict):
        return {"logged": False, "reason": "no_learning_core_field"}

    # Deep-copy the whole payload so the consumer mutation cannot
    # leak back into the live payload. The live decision must NEVER
    # see the hypothetical values — that's the whole point of
    # rollout step 3 vs step 5.
    shadow_payload = copy.deepcopy(payload)

    # We force the consumer ON for the shadow run regardless of its
    # own env flag — the audit IS the consumer's hypothetical view.
    # We do this by calling the consumer's pure helper function
    # directly rather than going through the env-gated wrapper.
    try:
        from services.learning_core_consumer import (
            MAX_CONFIDENCE_DELTA,
            MIN_RISK_MULTIPLIER_FLOOR,
            RISK_DAMPING_ON_PRETELL,
        )
    except Exception as exc:
        logger.debug("shadow_logger: consumer constants unavailable: %s", exc)
        return {"logged": False, "reason": str(exc)}

    direction = lc.get("direction_canonical")
    is_trade_side = direction in ("LONG", "SHORT")

    base_conf = _safe_float(payload.get("confidence"), 0.0)
    adj_conf = _safe_float(lc.get("adjusted_confidence"), base_conf)
    raw_delta = adj_conf - base_conf
    bounded_delta = max(
        -MAX_CONFIDENCE_DELTA,
        min(MAX_CONFIDENCE_DELTA, raw_delta),
    )
    hypo_conf = max(0.0, min(1.0, base_conf + bounded_delta))

    base_rm = _safe_float(payload.get("risk_multiplier"), 1.0)
    pretell = lc.get("pretell_warning")
    hypo_rm = base_rm
    if pretell and is_trade_side:
        damped = base_rm * (1.0 - RISK_DAMPING_ON_PRETELL)
        hypo_rm = max(MIN_RISK_MULTIPLIER_FLOOR, min(base_rm, damped))

    would_have_consumed = (
        is_trade_side and payload.get("decision") is not None
    )

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": payload.get("symbol"),
        "decision": payload.get("decision"),
        "direction_canonical": direction,
        "confidence_before": base_conf,
        "confidence_hypothetical": hypo_conf if would_have_consumed else base_conf,
        "confidence_delta": (hypo_conf - base_conf) if would_have_consumed else 0.0,
        "risk_multiplier_before": base_rm,
        "risk_multiplier_hypothetical": hypo_rm if would_have_consumed else base_rm,
        "risk_multiplier_delta": (hypo_rm - base_rm) if would_have_consumed else 0.0,
        "memory_win_rate": lc.get("memory_win_rate"),
        "similar_memory_count": lc.get("similar_memory_count", 0),
        "pretell_warning_present": bool(pretell),
        "would_have_consumed": would_have_consumed,
    }
    # ``shadow_payload`` was deep-copied above so we can prove via
    # tests that ``payload`` is untouched. We don't persist it
    # (too bulky); the row above carries everything we need.
    _ = shadow_payload

    try:
        await db[COLLECTION_NAME].insert_one(dict(row))
        return {"logged": True}
    except Exception as exc:
        logger.warning("shadow_logger: insert failed: %s", exc)
        return {"logged": False, "reason": str(exc)}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if f != f:  # NaN check without importing math
            return default
        return f
    except (TypeError, ValueError):
        return default
