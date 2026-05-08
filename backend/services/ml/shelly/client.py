"""Shelly client — synchronous adapter around the legacy
:mod:`services.market_memory_service`.

Authority: OBSERVATION + MEMORY only.

  * :meth:`recall` reads similar past episodes from market_memory and
    returns a :class:`ShellyRecall` summary that the Strategist /
    Auditor / Shadow layers can read but not mutate.
  * :meth:`remember` persists the verdict as a pending episode so the
    next decision sees richer recall.

Hard seals (asserted in tests):
  * Shelly cannot ``veto``, ``size``, ``execute``, ``place_order``,
    ``request_order`` — these are not methods on the class. A buggy
    caller that tries any of them via ``getattr`` raises
    AttributeError.
  * Both ``recall`` and ``remember`` are wrapped in their own
    try/except — failures degrade to empty recall / no-op
    persistence and NEVER abort the pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.ml.contracts import FeatureFrame, MLVerdict

logger = logging.getLogger(__name__)


@dataclass
class ShellyRecall:
    """Summary of past similar episodes.

    Used by Strategist / Auditor as a hint, never as a hard gate.
    """
    episodes_found: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    summary: Optional[str] = None
    raw: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ShellyClient:
    """Synchronous, async-safe wrapper around the legacy memory
    service.

    The legacy :mod:`services.market_memory_service` exposes async
    helpers; we adapt them via ``asyncio.run_coroutine_threadsafe``
    when called from a sync context. The pipeline calls into Shelly
    fire-and-safe — exceptions on either path are swallowed and
    logged, never raised.
    """

    def __init__(self):
        # Lazy-import the legacy service so unit tests can stub it.
        self._service = None

    def _ensure_service(self) -> None:
        if self._service is not None:
            return
        try:
            from services import market_memory_service  # type: ignore
            self._service = market_memory_service
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[shelly] market_memory_service unavailable: %s; "
                "recall/remember will degrade to no-op", exc,
            )
            self._service = False  # sentinel: tried + failed

    # ── Recall ──────────────────────────────────────────────

    def recall(self, frame: FeatureFrame) -> ShellyRecall:
        """Best-effort similarity recall. NEVER raises."""
        try:
            self._ensure_service()
            if not self._service:
                return ShellyRecall(summary="memory_service_unavailable")

            # The legacy module exposes ``get_prediction_context`` as
            # an async coroutine. We try to extract a regime-shaped
            # snapshot and synchronously fetch the result.
            snapshot = {
                "symbol": frame.symbol,
                "lane": frame.lane,
                **(frame.market or {}),
            }
            episodes = _safe_call_recall(self._service, snapshot)
            if not episodes:
                return ShellyRecall(episodes_found=0)
            pos = sum(1 for e in episodes if _episode_outcome(e) == "positive")
            neg = sum(1 for e in episodes if _episode_outcome(e) == "negative")
            neu = sum(1 for e in episodes if _episode_outcome(e) == "neutral")
            return ShellyRecall(
                episodes_found=len(episodes),
                positive_count=pos,
                negative_count=neg,
                neutral_count=neu,
                summary=f"{pos}+/{neg}-/{neu}~ from {len(episodes)} episodes",
                raw=episodes[:5],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[shelly] recall failed (degrading): %s", exc)
            return ShellyRecall(summary=f"recall_error:{type(exc).__name__}")

    # ── Remember ────────────────────────────────────────────

    def remember(self, frame: FeatureFrame, verdict: MLVerdict) -> bool:
        """Best-effort persistence. NEVER raises. Returns True on
        success, False otherwise (so callers can log if they care)."""
        try:
            self._ensure_service()
            if not self._service:
                return False
            payload = {
                "symbol": frame.symbol,
                "lane": frame.lane,
                "decision": verdict.decision,
                "reason": verdict.reason,
                "confidence": verdict.confidence,
                "perception": frame.perception,
                "strategist": frame.strategist,
                "auditor": frame.auditor,
                "fast_veto": frame.fast_veto,
                "shadow": frame.shadow,
                "source": "organic",
                "first_seen_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": 1,
            }
            return _safe_call_remember(self._service, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[shelly] remember failed (degrading): %s", exc)
            return False


# ── Helpers ──────────────────────────────────────────────────────


def _episode_outcome(ep: Dict[str, Any]) -> str:
    """Best-effort outcome classification across legacy schemas."""
    outcome = (ep.get("outcome") or ep.get("label") or ep.get("result") or "").lower()
    if "pos" in outcome or "win" in outcome or outcome == "true":
        return "positive"
    if "neg" in outcome or "loss" in outcome or outcome == "false":
        return "negative"
    return "neutral"


def _safe_call_recall(service, snapshot) -> List[Dict[str, Any]]:
    """Try a few legacy entrypoints. None of them are guaranteed to
    exist; we return [] if all fail. NEVER raises — the caller
    already wraps us, this is just defensive belt-and-suspenders."""
    try:
        # Try sync entrypoint first if present.
        fn = getattr(service, "recall_similar_episodes_sync", None)
        if callable(fn):
            res = fn(snapshot, limit=10)
            return list(res or [])
        # Fallback: query the Mongo collection directly via a sync
        # helper if the service exposes one.
        fn = getattr(service, "get_recent_episodes", None)
        if callable(fn):
            res = fn(snapshot.get("symbol"), limit=10)
            return list(res or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[shelly] recall fallback raised: %s", exc)
    return []


def _safe_call_remember(service, payload) -> bool:
    try:
        fn = getattr(service, "remember_episode_sync", None)
        if callable(fn):
            fn(payload)
            return True
        fn = getattr(service, "save_pending_episode", None)
        if callable(fn):
            fn(payload)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[shelly] remember fallback raised: %s", exc)
    return False
