"""RoadGuard v2 — lane-isolated capital governor pair.

Closed-loop architecture: each executor lane has its OWN dedicated
RoadGuard. EquityRoadGuard sees only equity capital + caps;
CryptoRoadGuard sees only crypto capital + caps. A signal arriving
at the wrong RG fails LANE_MISMATCH at G00.

Public exports:
  * :class:`EquityRoadGuard`  — pair partner for the equity executor
  * :class:`CryptoRoadGuard`  — pair partner for the crypto executor
  * :class:`RoadGuardV2`      — dispatcher kept for back-compat
  * Data shapes: :class:`AccountSnapshot`, :class:`TradeIntent`,
    :class:`RoadGuardVerdict`
"""
from services.ml.roadguard.governor import (
    AccountSnapshot,
    CryptoRoadGuard,
    EquityRoadGuard,
    ROADGUARD_CAN_APPROVE,
    RGDecision,
    RoadGuardV2,
    RoadGuardVerdict,
    TradeIntent,
)

__all__ = [
    "AccountSnapshot",
    "CryptoRoadGuard",
    "EquityRoadGuard",
    "ROADGUARD_CAN_APPROVE",
    "RGDecision",
    "RoadGuardV2",
    "RoadGuardVerdict",
    "TradeIntent",
]
