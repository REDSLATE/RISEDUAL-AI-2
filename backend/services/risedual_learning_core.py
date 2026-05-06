"""
RISEDUAL Learning Core — orchestrator.

Patent M — Regime-Aware Confusion Learning Core.

Combines (without rebuilding) three IP layers:

* CACL embedding/confusion engine (``cacl_original_ml``)
* Auto regime tagger          (``auto_regime_tagger``)
* Regime clustering layer     (``regime_clustering_layer``,
                                shim over the canonical
                                ``regime_memory_retrieval`` engine)

The core returns *learning context, regime warnings, and
adjusted-confidence metadata*. It does NOT:

* place trades,
* call any broker / Mongo write path,
* override Strategist / Auditor / Commander direction,
* promote ``HOLD`` or ``UNKNOWN`` into a trade.

These invariants are enforced inline in ``evaluate_context`` and
exercised by ``tests/test_risedual_learning_core.py``. They are
load-bearing for the IP claim ("learning/context layer, not
execution authority") so do not relax them.

Hard contract (and corresponding test names)
--------------------------------------------
1. ``evaluate_context`` returns a dict only — no side effects on
   the broker, Mongo, or any service singleton.
   (``test_evaluate_context_has_no_side_effects``)
2. Direction is always passed through
   ``services.prediction_tracker.canonical_ai_dir`` before any
   downstream lookup or boost.
   (``test_unknown_direction_does_not_get_positive_boost``)
3. ``HOLD`` and ``UNKNOWN`` cannot receive a positive
   confidence boost from memory win rate.
   (``test_hold_does_not_get_positive_boost``)
4. The total delta between ``adjusted_confidence`` and the input
   ``base_confidence`` is bounded to ``[-0.15, +0.15]``.
   (``test_confidence_delta_capped_at_plus_minus_15``)
5. ``adjusted_confidence`` is always in ``[0.0, 1.0]``.
   (``test_adjusted_confidence_clamped_0_to_1``)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from services.cacl_original_ml import ConfusionAwareEmbeddingNetwork
from services.auto_regime_tagger import (
    RegimeTagger,
    RawMacroData,
)
from services.regime_clustering_layer import (
    RegimeClusteringEngine,
    RegimeLabeledMemory,
    RegimeFingerprint,
)
from services.prediction_tracker import canonical_ai_dir


# Confidence-delta hard cap — invariant #4. Exposed as a constant
# so a future admin tile can render it without bytecode-walking,
# and so any change leaves a visible diff.
MAX_CONFIDENCE_DELTA = 0.15


@dataclass
class LearningCoreDecisionContext:
    """Per-decision input envelope.

    Keep this dataclass dumb — no behaviour, just shape.
    """

    symbol: str
    direction: str                    # raw token, canonicalised inside
    base_confidence: float            # caller's own confidence, [0,1]
    feature_vector: List[float]
    label: Optional[int]              # not used at evaluate-time;
                                      # kept for symmetry with
                                      # train-side payload.
    macro_data: RawMacroData
    macro_history: Dict[str, List[float]]
    macro_series: List[RawMacroData]


class RisedualLearningCore:
    """Orchestrator.

    Construction is cheap (NumPy weights only) so per-process
    instantiation is fine. The shim's ``RegimeClusteringEngine``
    spins up its own canonical engine by default; pass one in if
    you want all cores in a process to share clusters.
    """

    def __init__(
        self,
        input_dim: int,
        n_classes: int,
        embedding_dim: int = 128,
        regime_similarity_threshold: float = 0.70,
        regime_engine: Optional[RegimeClusteringEngine] = None,
        cacl_engine: Optional[ConfusionAwareEmbeddingNetwork] = None,
    ) -> None:
        self.embedding_core = cacl_engine or ConfusionAwareEmbeddingNetwork(
            input_dim=input_dim,
            embedding_dim=embedding_dim,
            n_prototypes=n_classes,
        )
        self.regime_tagger = RegimeTagger()
        self.regime_memory = regime_engine or RegimeClusteringEngine(
            similarity_threshold=regime_similarity_threshold,
        )

    # ─── train-side ─────────────────────────────────────────────

    def train_batch(
        self,
        features: np.ndarray,
        labels: np.ndarray,
    ) -> Dict[str, Any]:
        """Pure CACL update — no regime/memory side effects."""
        return self.embedding_core.training_step(features, labels)

    def add_resolved_memory(
        self, memory: RegimeLabeledMemory,
    ) -> Dict[str, Any]:
        """Route a resolved trade memory into regime clusters.

        Returns the cluster ids assigned (or ``None`` if the
        canonical engine refused — disabled, missing pnl, or
        non-canonical direction). The cluster report is included
        for convenience.
        """
        regime_cluster_id = self.regime_memory.assign_regime_cluster(memory)
        pretell_cluster_id = self.regime_memory.detect_pretell_cluster(memory)
        return {
            "regime_cluster_id": regime_cluster_id,
            "pretell_cluster_id": pretell_cluster_id,
            "cluster_report": self.regime_memory.get_cluster_report(),
        }

    # ─── runtime path ───────────────────────────────────────────

    def evaluate_context(
        self, ctx: LearningCoreDecisionContext,
    ) -> Dict[str, Any]:
        """Main runtime entry.

        Order of operations:

        1. Canonicalise direction (load-bearing for invariants 2/3).
        2. Tag the current macro snapshot → ``RegimeFingerprint``.
        3. Tag the snapshot from ``days_back`` ago → pretell.
        4. Retrieve historical regime memories matching the
           canonical direction (the canonical engine already
           returns ``[]`` for non-trade directions).
        5. Check pretell warning if we have one.
        6. Embed the current feature vector and read off the
           model's class probabilities.
        7. Blend confidences with the bounded delta.
        """
        canonical_direction = canonical_ai_dir(ctx.direction)

        current_regime = self.regime_tagger.generate_fingerprint(
            ctx.macro_data,
            ctx.macro_history,
        )
        current_pretell = self.regime_tagger.generate_pretell(
            ctx.macro_data.date,
            ctx.macro_series,
            days_back=30,
        )

        similar_memories = self.regime_memory.retrieve_regime_context(
            current_regime=current_regime,
            ticker=ctx.symbol,
            direction=canonical_direction,
            n_results=5,
        )

        pretell_warning = None
        if current_pretell is not None:
            pretell_warning = self.regime_memory.check_pretell_warning(
                current_regime=current_regime,
                current_pretell=current_pretell.fingerprint,
            )

        # CACL embed + class probs.
        x = np.array(ctx.feature_vector, dtype=float).reshape(1, -1)
        embedding = self.embedding_core.embed(x)
        model_probs = self.embedding_core.predict_proba(embedding)[0]
        model_confidence = float(np.max(model_probs))

        memory_win_rate = self._memory_win_rate(similar_memories)

        adjusted_confidence = self._adjust_confidence(
            base_confidence=ctx.base_confidence,
            model_confidence=model_confidence,
            memory_win_rate=memory_win_rate,
            pretell_warning=pretell_warning,
            canonical_direction=canonical_direction,
        )

        return {
            "symbol": ctx.symbol,
            "direction_raw": ctx.direction,
            "direction_canonical": canonical_direction,
            "base_confidence": float(ctx.base_confidence),
            "model_confidence": model_confidence,
            "memory_win_rate": memory_win_rate,
            "adjusted_confidence": adjusted_confidence,
            "current_regime": _fingerprint_to_dict(current_regime),
            "pretell_warning": pretell_warning,
            "similar_memory_count": len(similar_memories),
            "similar_memories": similar_memories,
            "notes": [
                "Learning core returned context only.",
                "Execution, sizing, veto, and risk gates "
                "remain outside this module.",
            ],
        }

    # ─── confidence math ────────────────────────────────────────

    @staticmethod
    def _memory_win_rate(
        memories: List[Dict[str, Any]],
    ) -> Optional[float]:
        if not memories:
            return None
        # Canonical engine returns dicts with ``pnl_pct``.
        wins = sum(
            1 for m in memories
            if (m.get("pnl_pct") or 0.0) > 0
        )
        return wins / len(memories)

    @staticmethod
    def _adjust_confidence(
        base_confidence: float,
        model_confidence: float,
        memory_win_rate: Optional[float],
        pretell_warning: Optional[Dict[str, Any]],
        canonical_direction: str,
    ) -> float:
        """Compute ``adjusted_confidence`` honouring all invariants.

        Math:
        * desired = 0.60 * base + 0.40 * model.
        * positive memory boost (+0.03) gated to LONG/SHORT only.
        * negative memory penalty (-0.05) applies regardless
          (honesty about a bad regime should still flow through
          for HOLD because it informs *future* decisions, but it
          can never *promote* HOLD into a trade — the caller owns
          that gate, and ``adjusted_confidence`` is purely
          metadata for HOLD).
        * pretell warning: -0.07 regardless of direction.
        * delta from base bounded to ±MAX_CONFIDENCE_DELTA.
        * final clamped to [0, 1].
        """
        desired = 0.60 * base_confidence + 0.40 * model_confidence
        delta = desired - base_confidence

        is_trade_side = canonical_direction in {"LONG", "SHORT"}

        if memory_win_rate is not None:
            if memory_win_rate >= 0.60 and is_trade_side:
                delta += 0.03
            elif memory_win_rate <= 0.40:
                delta -= 0.05

        if pretell_warning is not None:
            delta -= 0.07

        # Hard cap on the delta — invariant #4.
        if delta > MAX_CONFIDENCE_DELTA:
            delta = MAX_CONFIDENCE_DELTA
        elif delta < -MAX_CONFIDENCE_DELTA:
            delta = -MAX_CONFIDENCE_DELTA

        # Final clamp to [0, 1] — invariant #5.
        result = base_confidence + delta
        return max(0.0, min(1.0, result))


# ─── small helpers ──────────────────────────────────────────────


def _fingerprint_to_dict(fp: RegimeFingerprint) -> Dict[str, str]:
    return {
        "vix_level": fp.vix_level,
        "yield_curve": fp.yield_curve,
        "dxy_trend": fp.dxy_trend,
        "credit_spreads": fp.credit_spreads,
        "liquidity": fp.liquidity,
        "macro_phase": fp.macro_phase,
    }
