"""Shadow-mode hook attaching the RISEDUAL Learning Core to the
adversarial decision payload.

Patent M Phase 2 — observation-only side-channel.

How this fits the contract
--------------------------
The operator directive for Phase 2 is *"wire the core into
Strategist/Auditor input as a confidence/warning side-channel
(still no execution authority)"*. This module does **exactly
that**: it builds a ``LearningCoreDecisionContext`` from the
adversarial signal payload, calls the core's
``evaluate_context``, and attaches the result under
``payload["learning_core"]``. It does NOT:

  * mutate ``payload["decision"]``
  * touch ``payload["risk_multiplier"]``
  * write to Mongo
  * change direction
  * raise on failure

If the env flag ``LEARNING_CORE_SHADOW_ENABLED`` is off (default),
the hook is a no-op. If the env flag is on but anything inside
fails, the hook logs a warning and returns the payload unchanged.

The actual *consumption* of the side-channel by Commander /
Auditor is deliberately deferred to Phase 3 — the operator
wanted the data flowing in shadow first so they can audit the
core's confidence deltas before letting them influence sizing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from services.auto_regime_tagger import RawMacroData
from services.learning_core_service import get_core, reset_singleton_for_tests as _reset_shared
from services.risedual_learning_core import (
    LearningCoreDecisionContext,
)


logger = logging.getLogger(__name__)


# Default feature dimensionality. Phase-1 features are picked
# directly off ``signal`` (confidence, expected_r, vix, etc.) —
# any extra dims the live signal lacks are zero-filled. Must
# match ``services.learning_core_service.DEFAULT_FEATURE_DIM``.
DEFAULT_FEATURE_DIM = 8


def _shadow_enabled() -> bool:
    return os.getenv("LEARNING_CORE_SHADOW_ENABLED", "false").lower() == "true"


def _signal_to_macro(signal: dict[str, Any]) -> RawMacroData:
    """Best-effort extraction of macro fields from the adversarial
    signal payload.

    The live signal carries macro under ``signal["macro"]`` (matching
    the ``predictions.macro`` schema used by the compression gate).
    ``signal["regime"]`` is a string label ("trending", "parabolic",
    etc.) — never a dict. Anything missing falls through to
    ``RawMacroData``'s conservative ``None`` fallbacks.
    """
    macro = signal.get("macro") or {}
    if not isinstance(macro, dict):
        macro = {}
    regime_label = signal.get("regime")
    macro_phase_hint = (
        regime_label if isinstance(regime_label, str) else None
    )
    return RawMacroData(
        date=str(signal.get("timestamp") or macro.get("date") or ""),
        vix=macro.get("vix"),
        yield_2y=macro.get("two_year") or macro.get("yield_2y"),
        yield_10y=macro.get("ten_year") or macro.get("yield_10y"),
        dxy=macro.get("dxy"),
        dxy_60d_avg=macro.get("dxy_60d_avg"),
        hy_oas_bp=macro.get("hy_oas_bp"),
        ig_oas_bp=macro.get("ig_oas_bp"),
        liquidity_z=macro.get("liquidity_z"),
        macro_phase_hint=macro_phase_hint,
    )


def _signal_to_features(signal: dict[str, Any]) -> list[float]:
    """Build the Phase-1 feature vector.

    The list is fixed-length (``DEFAULT_FEATURE_DIM``); missing
    fields are zero-filled. Order is part of the contract — never
    reorder without versioning the singleton.
    """
    macro = signal.get("macro") or {}
    if not isinstance(macro, dict):
        macro = {}
    raw = [
        float(signal.get("confidence") or 0.0),
        float(signal.get("expected_r") or 0.0),
        float(macro.get("vix") or 0.0),
        float(macro.get("ten_year") or macro.get("yield_10y") or 0.0),
        float(macro.get("two_year") or macro.get("yield_2y") or 0.0),
        float(macro.get("dxy") or 0.0),
        float(macro.get("liquidity_z") or 0.0),
        1.0 if signal.get("decision_hint") == "LONG" else 0.0,
    ]
    # Defensive truncate / pad — never let dimension drift produce
    # a NumPy shape error inside ``evaluate_context``.
    if len(raw) < DEFAULT_FEATURE_DIM:
        raw = raw + [0.0] * (DEFAULT_FEATURE_DIM - len(raw))
    return raw[:DEFAULT_FEATURE_DIM]


def attach_learning_core_context(
    payload: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any]:
    """Attach the learning core's evaluate_context output to the
    adversarial payload. Mutates ``payload`` in place AND returns
    it so the caller can chain.

    Failure modes
    -------------
    * Env flag off              → no-op, payload unchanged.
    * Direction not extractable → no-op, payload unchanged.
    * Core raises               → log warning, payload unchanged.
    * Core succeeds             → attach under ``learning_core``.
    """
    if not _shadow_enabled():
        return payload

    direction = (
        payload.get("decision")
        or signal.get("decision_hint")
        or signal.get("direction")
        or "UNKNOWN"
    )
    base_confidence = float(
        payload.get("confidence")
        or signal.get("confidence")
        or 0.5
    )
    symbol = str(signal.get("symbol") or payload.get("symbol") or "?")

    try:
        ctx = LearningCoreDecisionContext(
            symbol=symbol,
            direction=str(direction),
            base_confidence=max(0.0, min(1.0, base_confidence)),
            feature_vector=_signal_to_features(signal),
            label=None,
            macro_data=_signal_to_macro(signal),
            macro_history={},
            macro_series=[],  # caller owns history; hook stays stateless
        )
        result = get_core().evaluate_context(ctx)
        payload["learning_core"] = {
            "direction_canonical": result["direction_canonical"],
            "base_confidence": result["base_confidence"],
            "model_confidence": result["model_confidence"],
            "adjusted_confidence": result["adjusted_confidence"],
            "memory_win_rate": result["memory_win_rate"],
            "similar_memory_count": result["similar_memory_count"],
            "pretell_warning": result["pretell_warning"],
            "current_regime": result["current_regime"],
            "shadow_only": True,
            "notes": [
                "Phase 2 shadow attachment — Commander/Auditor "
                "do NOT consume this field yet. Phase 3 wiring "
                "lands once shadow audit confirms calibration.",
            ],
        }
    except Exception as exc:
        # Hook is best-effort — never break the live decision.
        logger.warning(
            "learning_core_shadow_hook: attach failed for %s: %s",
            symbol, exc,
        )
    return payload


def reset_singleton_for_tests() -> None:
    """Test escape hatch — clears the lazy singleton between tests
    so prototype state from one test doesn't leak into the next.
    Delegates to the shared service singleton so both shadow-hook
    and consumer paths see a clean slate."""
    _reset_shared()
