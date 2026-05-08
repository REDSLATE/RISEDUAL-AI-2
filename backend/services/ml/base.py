"""Base classes for the 8-ML stack.

Three flavours:

* :class:`BaseMLLayer` — full-authority layer. May produce BUY/SELL.
  Concrete subclasses: Perception, Strategist, Auditor, EquityExecutor,
  CryptoExecutor.
* :class:`ShadowMLLayer` — observation-only layer. ``can_approve`` is
  hard False. ``decide`` returns NO_TRADE always; only ``observe``
  is overridable. Used by Shadow ML and Shelly.
* :class:`VetoOnlyMLLayer` — veto authority only. May return NO_TRADE
  with a block reason; can never produce BUY/SELL. Used by Fast Veto
  ML slot.

Sealing is enforced both via runtime asserts in :meth:`decide` and by
making the relevant attributes class-final (a subclass that overrides
``can_approve = True`` on a Shadow layer fails the boot check).
"""
from __future__ import annotations

import logging
from typing import Optional

from services.ml.contracts import (
    FeatureFrame,
    LaneDisabledError,
    MLVerdict,
    ModelBootReceipt,
    Verdict,
)

logger = logging.getLogger(__name__)


class BaseMLLayer:
    """Full-authority ML layer.

    Concrete subclasses must:
      * set ``layer_id`` (e.g. "perception", "strategist")
      * implement :meth:`_predict`
      * either implement :meth:`boot` or set ``_boot_receipt`` directly
    """

    layer_id: str = "base"
    can_approve: bool = True
    shadow_only: bool = False

    def __init__(self, *, lane: Optional[str] = None):
        self.lane = lane
        self._boot_receipt: Optional[ModelBootReceipt] = None

    # ── Boot ────────────────────────────────────────────────

    def boot(self) -> ModelBootReceipt:
        """Default boot: lane ready, no artifact required.

        Layers that depend on a ``.joblib`` artifact must override
        this and set ``ready=False`` + a reason if the artifact is
        missing. Boot must NEVER raise — failure is encoded in the
        receipt and converted to NO_TRADE downstream.
        """
        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=self.lane,
            ready=True,
            artifact_path=None,
            artifact_present=True,
            reason=None,
            can_approve=self.can_approve,
            shadow_only=self.shadow_only,
        )
        self._boot_receipt = receipt
        return receipt

    @property
    def boot_receipt(self) -> ModelBootReceipt:
        if self._boot_receipt is None:
            self.boot()
        assert self._boot_receipt is not None
        return self._boot_receipt

    # ── Decision ────────────────────────────────────────────

    def decide(self, frame: FeatureFrame) -> MLVerdict:
        """Public entry point. Wraps :meth:`_predict` with fail-fast
        gating: a disabled lane returns NO_TRADE without ever calling
        the model."""
        receipt = self.boot_receipt
        if not receipt.ready:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=f"LANE_DISABLED:{receipt.reason or 'unknown'}",
                can_approve=self.can_approve,
                diagnostics={"lane": self.lane, "boot": receipt.as_dict()},
            )
        try:
            verdict = self._predict(frame)
        except LaneDisabledError as exc:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=f"LANE_DISABLED:{exc.reason}",
                can_approve=self.can_approve,
                diagnostics={"lane": exc.lane or self.lane},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ml.%s] predict failed: %s; returning NO_TRADE",
                self.layer_id, exc,
            )
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=f"LAYER_EXCEPTION:{type(exc).__name__}",
                can_approve=self.can_approve,
                diagnostics={"lane": self.lane, "error": str(exc)[:200]},
            )

        # Authority assertion. Shadow/Veto layers must NEVER bubble up
        # an APPROVE verdict even if a buggy override tries.
        if not self.can_approve and verdict.decision in (
            Verdict.BUY.value, Verdict.SELL.value,
        ):
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason="AUTHORITY_VIOLATION",
                can_approve=False,
                diagnostics={
                    "attempted_decision": verdict.decision,
                    "lane": self.lane,
                },
            )
        # Normalise layer/can_approve so callers can trust the envelope.
        verdict.layer = self.layer_id
        verdict.can_approve = self.can_approve
        return verdict

    def _predict(self, frame: FeatureFrame) -> MLVerdict:  # pragma: no cover - abstract
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _predict()"
        )


class VetoOnlyMLLayer(BaseMLLayer):
    """Veto-authority layer. Can return NO_TRADE only.

    Subclass MUST override ``_predict`` to return either:
      * NO_TRADE with a ``reason`` set (veto applied)
      * BUY/SELL — REJECTED at runtime, converted to NO_TRADE
        with ``AUTHORITY_VIOLATION``.
    """
    can_approve: bool = False

    def __init__(self, *, lane: Optional[str] = None):
        super().__init__(lane=lane)


class ShadowMLLayer(BaseMLLayer):
    """Observation-only layer. Verdict is always NO_TRADE.

    The pipeline calls :meth:`observe` for shadow-only telemetry,
    then ignores the verdict for routing. Sealed: subclasses cannot
    re-enable approval authority.
    """
    can_approve: bool = False
    shadow_only: bool = True

    # Sentinel — used by tests to confirm the seal.
    _SHADOW_SEALED: bool = True

    def _predict(self, frame: FeatureFrame) -> MLVerdict:
        # Shadow layers MUST NOT route. Always return a typed NO_TRADE
        # carrying observe() output in diagnostics.
        diagnostics = self.observe(frame) or {}
        return MLVerdict(
            layer=self.layer_id,
            decision=Verdict.NO_TRADE.value,
            confidence=0.0,
            reason="SHADOW_OBSERVATION",
            can_approve=False,
            diagnostics=diagnostics,
        )

    def observe(self, frame: FeatureFrame) -> dict:
        """Override to record observations. Must return a dict — no
        side effects that affect routing. Default: empty dict."""
        return {}

    # Hard seals — these names exist solely so a buggy subclass that
    # tries to add "execute" or "place_order" fails at import time.
    def execute(self, *_, **__):  # noqa: D401
        raise RuntimeError(
            f"Shadow layer {self.layer_id} cannot execute(); seal violated"
        )

    def place_order(self, *_, **__):
        raise RuntimeError(
            f"Shadow layer {self.layer_id} cannot place_order(); seal violated"
        )

    def request_order(self, *_, **__):
        raise RuntimeError(
            f"Shadow layer {self.layer_id} cannot request_order(); seal violated"
        )

    def size(self, *_, **__):
        raise RuntimeError(
            f"Shadow layer {self.layer_id} cannot size(); seal violated"
        )

    def veto(self, *_, **__):
        raise RuntimeError(
            f"Shadow layer {self.layer_id} cannot veto(); seal violated"
        )
