"""RisedualMLPipeline — the canonical 8-ML decision flow.

Order:
  market data (FeatureFrame)
    -> Shelly recall (memory consult; observation only)
    -> Perception ML (6 sklearn + symbolic engine)
    -> Strategist ML
    -> Auditor ML
    -> Fast Veto ML (veto-only)
    -> Shadow ML (observe-only)
    -> Executor ML (lane-routed: EquityExecutorML or CryptoExecutorML)
    -> Shelly remember (verdict persisted as pending episode)
    -> [RoadGuard runs OUTSIDE this pipeline, called by the caller
       with the executor verdict + AccountSnapshot]

The pipeline NEVER calls a broker. NEVER places an order. NEVER
mutates account state. Its sole output is a :class:`PipelineDecision`
with the cumulative verdict trail — the caller (Phase 5a wiring)
is responsible for writing alpha_decision_log receipts and routing
the verdict to RoadGuard.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.ml.auditor import AuditorML
from services.ml.contracts import (
    DECISION_STAGES,
    FeatureFrame,
    MLVerdict,
    Verdict,
)
from services.ml.executors import CryptoExecutorML, EquityExecutorML
from services.ml.fast_veto import FastVetoMLLayer
from services.ml import executor_heartbeat
from services.ml.perception import PerceptionML
from services.ml.shadow import ShadowML
from services.ml.shelly import ShellyClient
from services.ml.strategist import StrategistML

logger = logging.getLogger(__name__)


@dataclass
class PipelineDecision:
    """Cumulative output of one pipeline run.

    ``final`` is the resolved verdict the caller should act on (after
    fast_veto / executor sequencing). ``trail`` is the per-layer
    verdict list in execution order so receipts can be written at the
    correct stage.
    """
    symbol: str
    lane: str
    final: MLVerdict
    trail: List[MLVerdict] = field(default_factory=list)
    blocked_at: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "lane": self.lane,
            "final": self.final.as_dict(),
            "trail": [v.as_dict() for v in self.trail],
            "blocked_at": self.blocked_at,
            "timestamp": self.timestamp,
        }


class RisedualMLPipeline:
    """Wires all 8 layers + Shelly into a single decide() call.

    Holds layer instances on the pipeline (one Perception engine per
    process, both executors instantiated lazily at boot — they remain
    artifact-gated even if the pipeline is constructed).
    """

    def __init__(self):
        self.shelly = ShellyClient()
        self.perception = PerceptionML()
        self.strategist = StrategistML()
        self.auditor = AuditorML()
        self.fast_veto = FastVetoMLLayer()
        self.shadow = ShadowML()
        self.equity_executor = EquityExecutorML()
        self.crypto_executor = CryptoExecutorML()
        # Force boot so receipts register at construction.
        for layer in (
            self.perception, self.strategist, self.auditor,
            self.fast_veto, self.shadow,
            self.equity_executor, self.crypto_executor,
        ):
            layer.boot()

    def _executor_for(self, lane: str):
        if lane == "crypto":
            return self.crypto_executor
        return self.equity_executor

    # ── Main entry ──────────────────────────────────────────

    def decide(self, frame: FeatureFrame) -> PipelineDecision:
        """Run the full 8-layer chain. Always returns a decision —
        a NO_TRADE verdict is the safe fallback at every gate."""
        trail: List[MLVerdict] = []

        # ── 1. Shelly recall (observation; never aborts the chain) ──
        recall = self.shelly.recall(frame)
        frame.shelly_recall = recall.as_dict()
        # Shelly recall is not a verdict — we don't add it to ``trail``.
        # It enriches the frame for downstream layers.

        # ── 2. Perception ──
        perception_verdict = self.perception.decide(frame)
        trail.append(perception_verdict)
        if self._is_block(perception_verdict):
            return self._finalise(frame, trail, perception_verdict, blocked_at="perception")

        # ── 3. Strategist ──
        # Strategist reads frame.perception (already enriched).
        frame.strategist = perception_verdict.as_dict()
        # Override "decision" / "reason" / "confidence" the Strategist
        # actually sees on the frame — it expects a dict-shaped lookup
        # with these keys. We mirror them at the top level for ergonomics.
        frame.strategist.update({
            "decision": perception_verdict.decision,
            "reason": perception_verdict.reason,
            "confidence": perception_verdict.confidence,
        })
        # Pre-strategist frame.strategist is only the perception copy;
        # after the call we replace with the strategist's own verdict.
        strategist_verdict = self.strategist.decide(frame)
        trail.append(strategist_verdict)
        if self._is_block(strategist_verdict):
            frame.strategist = strategist_verdict.as_dict()
            return self._finalise(frame, trail, strategist_verdict, blocked_at="strategist")
        frame.strategist = strategist_verdict.as_dict()

        # ── 4. Auditor ──
        auditor_verdict = self.auditor.decide(frame)
        trail.append(auditor_verdict)
        if self._is_block(auditor_verdict):
            frame.auditor = auditor_verdict.as_dict()
            return self._finalise(frame, trail, auditor_verdict, blocked_at="auditor")
        frame.auditor = auditor_verdict.as_dict()

        # ── 5. Fast Veto ML ──
        fv_verdict = self.fast_veto.decide(frame)
        trail.append(fv_verdict)
        # Fast veto's NO_TRADE is a real veto only if the reason is not
        # the passthrough sentinel. Passthrough means "no veto applied".
        if (
            fv_verdict.decision == Verdict.NO_TRADE.value
            and fv_verdict.reason != "FAST_VETO_ML_PASSTHROUGH"
        ):
            frame.fast_veto = fv_verdict.as_dict()
            return self._finalise(frame, trail, fv_verdict, blocked_at="fast_veto")
        frame.fast_veto = fv_verdict.as_dict()

        # ── 6. Shadow ML (observe-only) ──
        shadow_verdict = self.shadow.decide(frame)
        trail.append(shadow_verdict)
        frame.shadow = shadow_verdict.as_dict()
        # Shadow always returns NO_TRADE/SHADOW_OBSERVATION — we never
        # treat it as a block, just persist the observation.

        # ── 7. Executor ML (lane-routed) ──
        executor = self._executor_for(frame.lane)
        executor_verdict = executor.decide(frame)
        trail.append(executor_verdict)
        if self._is_block(executor_verdict):
            return self._finalise(frame, trail, executor_verdict, blocked_at="executor")

        # ── 8. Shelly remember (organic episode) ──
        # Fire-and-safe; failure is logged and ignored.
        self.shelly.remember(frame, executor_verdict)

        return self._finalise(frame, trail, executor_verdict, blocked_at=None)

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_block(verdict: MLVerdict) -> bool:
        return verdict.decision == Verdict.NO_TRADE.value

    @staticmethod
    def _finalise(
        frame: FeatureFrame,
        trail: List[MLVerdict],
        final: MLVerdict,
        *,
        blocked_at: Optional[str],
    ) -> PipelineDecision:
        # ``blocked_at`` must be one of DECISION_STAGES or None.
        if blocked_at is not None and blocked_at not in DECISION_STAGES:
            logger.warning("[ml.pipeline] unknown blocked_at=%r; coercing to None", blocked_at)
            blocked_at = None
        # Heartbeat — record every run so a frozen lane is visible.
        try:
            fh = (frame.extra or {}).get("feature_health_score")
            executor_heartbeat.record_pipeline_run(
                lane=frame.lane,
                decision=final.decision,
                blocked_at=blocked_at,
                reason=final.reason,
                feature_health=float(fh) if isinstance(fh, (int, float)) else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ml.pipeline] heartbeat record failed: %s", exc)
        return PipelineDecision(
            symbol=frame.symbol,
            lane=frame.lane,
            final=final,
            trail=trail,
            blocked_at=blocked_at,
        )


# ── Singleton accessor ───────────────────────────────────────────


_pipeline: Optional[RisedualMLPipeline] = None


def get_pipeline() -> RisedualMLPipeline:
    """Process-wide singleton. Constructed on first access so import
    of the module is cheap."""
    global _pipeline
    if _pipeline is None:
        _pipeline = RisedualMLPipeline()
    return _pipeline
