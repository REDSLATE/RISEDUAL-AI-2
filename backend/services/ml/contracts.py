"""Canonical data contracts for the 8-ML neuro-symbolic stack.

Every layer in the pipeline communicates through these shapes only.
Hard rule: a layer that cannot serialise its input/output to one of
the dataclasses below MUST NOT be wired into the pipeline.

Why so strict:
* The pipeline runs in shadow mode initially and we need byte-for-byte
  reproducible receipts so the operator can replay a verdict from
  Mongo logs and get the same answer. Free-form dicts kill that.
* ML layers are individually fail-fast (LaneDisabledError) but the
  pipeline as a whole must NEVER abort — it returns a NO_TRADE
  verdict carrying the reason code from whichever gate failed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class Verdict(str, Enum):
    """Canonical pipeline verdicts.

    NO_TRADE is the safe fallback — every disabled lane, missing
    artifact, exception, or shadow-mode block resolves to NO_TRADE.
    BUY / SELL are the only positive intents; the executor lanes
    own short vs long sizing semantics inside their own ML.
    """
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


@dataclass
class FeatureFrame:
    """Per-decision feature payload that flows through every layer.

    Each layer is allowed to ENRICH the frame (add fields under
    ``perception``, ``strategist``, etc.) but must NEVER mutate the
    raw market inputs. The pipeline asserts this with a structural
    check at each handoff.
    """
    symbol: str
    lane: str  # "equity" | "crypto"
    timestamp: str
    market: Dict[str, Any] = field(default_factory=dict)
    perception: Dict[str, Any] = field(default_factory=dict)
    strategist: Dict[str, Any] = field(default_factory=dict)
    auditor: Dict[str, Any] = field(default_factory=dict)
    fast_veto: Dict[str, Any] = field(default_factory=dict)
    shadow: Dict[str, Any] = field(default_factory=dict)
    shelly_recall: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MLVerdict:
    """Per-layer verdict payload.

    ``layer`` is the canonical layer id (perception, strategist,
    auditor, fast_veto, shadow, executor, roadguard, shelly).
    ``decision`` is one of :class:`Verdict`. ``can_approve`` is a
    declarative authority claim — Shadow / FastVeto / RoadGuard
    layers MUST set it to False. The pipeline asserts this on
    receipt.
    """
    layer: str
    decision: str  # Verdict value
    confidence: float
    reason: Optional[str] = None
    can_approve: bool = False
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelBootReceipt:
    """Boot-time fingerprint for a single ML lane.

    Written once at process start. The admin /api/admin/ml/boot-receipts
    endpoint reads this so the operator can verify which lanes are
    active vs disabled BEFORE enabling enforce flags. A missing
    artifact resolves to ``ready=False`` and the lane returns
    NO_TRADE for every call.
    """
    layer: str
    lane: Optional[str]
    ready: bool
    artifact_path: Optional[str]
    artifact_present: bool
    reason: Optional[str]
    can_approve: bool
    shadow_only: bool
    booted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LaneDisabledError(RuntimeError):
    """Raised by a layer when it cannot run safely.

    Caught only by the pipeline itself and converted to a NO_TRADE
    verdict tagged with the layer + reason. NEVER caught silently
    by a lane — that would defeat the fail-fast invariant.
    """
    def __init__(self, layer: str, reason: str, *, lane: Optional[str] = None):
        super().__init__(f"[{layer}] disabled: {reason}")
        self.layer = layer
        self.reason = reason
        self.lane = lane


# Stages used by alpha_decision_log receipts. Order matters — the
# pipeline walks gates in this sequence and a NO_TRADE verdict's
# ``blocked_at`` MUST be one of these.
DECISION_STAGES: List[str] = [
    "shelly_recall",
    "perception",
    "strategist",
    "auditor",
    "fast_veto",
    "shadow",
    "executor",
    "roadguard",
]
