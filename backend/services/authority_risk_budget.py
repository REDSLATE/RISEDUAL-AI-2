"""
RISEDUAL Patent I — Adaptive Authority-Scoped Risk Budgeting

Purpose:
    Applies an adaptive risk budget based on the authority scope of a promoted
    policy, operator, model, or trading agent.

Core invariant:
    The system may automatically TIGHTEN risk after poor outcomes, but may not
    automatically LOOSEN risk. Any loosening requires a valid countersignature.

This is intentionally separate from signal generation. It does not decide BUY,
SELL, or HOLD. It only modulates permitted exposure/notional/risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
import hashlib
import json


TradeAction = Literal["BUY", "SELL", "HOLD"]


class AuthorityTier(str, Enum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    SHADOW = "SHADOW"
    LIMITED_LIVE = "LIMITED_LIVE"
    STANDARD_LIVE = "STANDARD_LIVE"
    ELEVATED_LIVE = "ELEVATED_LIVE"


@dataclass(frozen=True)
class AuthorityScope:
    """
    Describes the approved operating scope.

    max_multiplier:
        The highest risk multiplier this authority is allowed to use.

    max_notional:
        Absolute notional cap for this authority.

    max_daily_loss:
        Maximum permitted daily loss before hard clamp.
    """

    authority_id: str
    tier: AuthorityTier
    asset_type: str
    engine: str
    approved_by: str
    expires_at: datetime

    max_multiplier: float
    max_notional: float
    max_daily_loss: float

    signature_hash: str
    countersignature_hash: Optional[str] = None


@dataclass(frozen=True)
class TrackRecord:
    """
    Rolling performance and safety telemetry for this authority scope.
    """

    total_trades: int
    win_rate: float
    realized_pnl: float
    max_drawdown: float
    calibration_gap: float
    loss_streak: int
    veto_count: int
    rejected_signal_count: int
    last_updated: datetime


@dataclass(frozen=True)
class RiskBudgetRequest:
    action: TradeAction
    base_notional: float
    base_multiplier: float
    authority: AuthorityScope
    track_record: TrackRecord
    daily_realized_loss: float
    requested_multiplier: Optional[float] = None
    loosening_countersignature_hash: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskBudgetDecision:
    action: TradeAction
    allowed: bool
    final_notional: float
    final_multiplier: float
    authority_tier: AuthorityTier
    tightened: bool
    loosened: bool
    reasons: list[str]
    audit_hash: str
    created_at: datetime


# Hard Patent-I safety bounds.
ABSOLUTE_MIN_MULTIPLIER = 0.0
ABSOLUTE_MAX_MULTIPLIER = 1.25

# Automatic tightening floors by authority tier.
TIER_MAX_MULTIPLIER = {
    AuthorityTier.OBSERVE_ONLY: 0.0,
    AuthorityTier.SHADOW: 0.0,
    AuthorityTier.LIMITED_LIVE: 0.35,
    AuthorityTier.STANDARD_LIVE: 0.75,
    AuthorityTier.ELEVATED_LIVE: 1.25,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _is_expired(scope: AuthorityScope, now: datetime) -> bool:
    return scope.expires_at <= now


def _has_valid_loosening_authority(
    scope: AuthorityScope,
    provided_hash: Optional[str],
) -> bool:
    """
    Patent I loosening rule:
        Risk may only loosen when the request carries the current valid
        countersignature hash from the authority scope.
    """
    if not scope.countersignature_hash:
        return False

    return provided_hash == scope.countersignature_hash


def compute_automatic_tightening_multiplier(track: TrackRecord) -> tuple[float, list[str]]:
    """
    Computes only downward pressure.

    Returns:
        multiplier_cap, reasons

    This function never increases risk.
    """
    cap = 1.0
    reasons: list[str] = []

    if track.total_trades < 30:
        cap = min(cap, 0.50)
        reasons.append("small_sample_tightening")

    if track.loss_streak >= 2:
        cap = min(cap, 0.75)
        reasons.append("loss_streak_2_plus")

    if track.loss_streak >= 4:
        cap = min(cap, 0.50)
        reasons.append("loss_streak_4_plus")

    if track.loss_streak >= 6:
        cap = min(cap, 0.25)
        reasons.append("loss_streak_6_plus")

    if track.max_drawdown >= 0.05:
        cap = min(cap, 0.75)
        reasons.append("drawdown_5_percent_plus")

    if track.max_drawdown >= 0.10:
        cap = min(cap, 0.50)
        reasons.append("drawdown_10_percent_plus")

    if track.max_drawdown >= 0.20:
        cap = min(cap, 0.0)
        reasons.append("drawdown_20_percent_hard_clamp")

    if track.calibration_gap >= 0.08:
        cap = min(cap, 0.75)
        reasons.append("calibration_gap_high")

    if track.calibration_gap >= 0.15:
        cap = min(cap, 0.50)
        reasons.append("calibration_gap_critical")

    if track.win_rate < 0.45 and track.total_trades >= 30:
        cap = min(cap, 0.60)
        reasons.append("win_rate_below_45_percent")

    if track.veto_count >= 5:
        cap = min(cap, 0.80)
        reasons.append("recent_veto_cluster")

    if track.rejected_signal_count >= 20:
        cap = min(cap, 0.85)
        reasons.append("rejected_signal_cluster")

    return cap, reasons


def apply_authority_scoped_risk_budget(
    request: RiskBudgetRequest,
    now: Optional[datetime] = None,
) -> RiskBudgetDecision:
    """
    Main Patent-I enforcement function.

    Invariants:
        1. HOLD remains HOLD.
        2. OBSERVE_ONLY and SHADOW cannot trade live.
        3. Expired authority cannot trade.
        4. Automatic logic may tighten but not loosen.
        5. Loosening requires countersignature.
        6. Final risk is bounded by authority scope, tier cap, and hard cap.
    """
    now = now or _now_utc()
    reasons: list[str] = []

    authority = request.authority
    track = request.track_record

    base_multiplier = _clamp(
        request.base_multiplier,
        ABSOLUTE_MIN_MULTIPLIER,
        ABSOLUTE_MAX_MULTIPLIER,
    )

    requested_multiplier = (
        request.requested_multiplier
        if request.requested_multiplier is not None
        else base_multiplier
    )

    requested_multiplier = _clamp(
        requested_multiplier,
        ABSOLUTE_MIN_MULTIPLIER,
        ABSOLUTE_MAX_MULTIPLIER,
    )

    if request.action == "HOLD":
        reasons.append("hold_action_no_trade_promotion")
        return _decision(
            request=request,
            allowed=False,
            final_notional=0.0,
            final_multiplier=0.0,
            tightened=True,
            loosened=False,
            reasons=reasons,
            now=now,
        )

    if authority.tier in {AuthorityTier.OBSERVE_ONLY, AuthorityTier.SHADOW}:
        reasons.append(f"{authority.tier.value.lower()}_no_live_execution")
        return _decision(
            request=request,
            allowed=False,
            final_notional=0.0,
            final_multiplier=0.0,
            tightened=True,
            loosened=False,
            reasons=reasons,
            now=now,
        )

    if _is_expired(authority, now):
        reasons.append("authority_expired")
        return _decision(
            request=request,
            allowed=False,
            final_notional=0.0,
            final_multiplier=0.0,
            tightened=True,
            loosened=False,
            reasons=reasons,
            now=now,
        )

    if request.daily_realized_loss >= authority.max_daily_loss:
        reasons.append("daily_loss_limit_reached")
        return _decision(
            request=request,
            allowed=False,
            final_notional=0.0,
            final_multiplier=0.0,
            tightened=True,
            loosened=False,
            reasons=reasons,
            now=now,
        )

    auto_cap, auto_reasons = compute_automatic_tightening_multiplier(track)
    reasons.extend(auto_reasons)

    tier_cap = TIER_MAX_MULTIPLIER[authority.tier]
    authority_cap = authority.max_multiplier

    hard_allowed_cap = min(
        ABSOLUTE_MAX_MULTIPLIER,
        tier_cap,
        authority_cap,
        auto_cap,
    )

    attempted_loosening = requested_multiplier > base_multiplier
    valid_loosening = _has_valid_loosening_authority(
        authority,
        request.loosening_countersignature_hash,
    )

    if attempted_loosening and not valid_loosening:
        reasons.append("loosening_denied_missing_valid_countersignature")
        requested_multiplier = base_multiplier

    elif attempted_loosening and valid_loosening:
        reasons.append("loosening_authorized_by_countersignature")

        # Even authorized loosening cannot exceed tier/scope/hard bounds.
        hard_allowed_cap = min(
            ABSOLUTE_MAX_MULTIPLIER,
            tier_cap,
            authority_cap,
        )

    final_multiplier = min(requested_multiplier, hard_allowed_cap)

    final_multiplier = _clamp(
        final_multiplier,
        ABSOLUTE_MIN_MULTIPLIER,
        ABSOLUTE_MAX_MULTIPLIER,
    )

    final_notional = min(
        request.base_notional * final_multiplier,
        authority.max_notional,
    )

    final_notional = max(0.0, final_notional)

    allowed = final_multiplier > 0.0 and final_notional > 0.0

    if final_multiplier < base_multiplier:
        reasons.append("risk_tightened")

    if not allowed:
        reasons.append("zero_risk_budget")

    return _decision(
        request=request,
        allowed=allowed,
        final_notional=final_notional,
        final_multiplier=final_multiplier,
        tightened=final_multiplier < base_multiplier,
        loosened=attempted_loosening and valid_loosening and final_multiplier > base_multiplier,
        reasons=reasons,
        now=now,
    )


def _decision(
    request: RiskBudgetRequest,
    allowed: bool,
    final_notional: float,
    final_multiplier: float,
    tightened: bool,
    loosened: bool,
    reasons: list[str],
    now: datetime,
) -> RiskBudgetDecision:
    audit_payload = {
        "action": request.action,
        "allowed": allowed,
        "final_notional": final_notional,
        "final_multiplier": final_multiplier,
        "authority_id": request.authority.authority_id,
        "authority_tier": request.authority.tier.value,
        "asset_type": request.authority.asset_type,
        "engine": request.authority.engine,
        "track_record": {
            "total_trades": request.track_record.total_trades,
            "win_rate": request.track_record.win_rate,
            "realized_pnl": request.track_record.realized_pnl,
            "max_drawdown": request.track_record.max_drawdown,
            "calibration_gap": request.track_record.calibration_gap,
            "loss_streak": request.track_record.loss_streak,
            "veto_count": request.track_record.veto_count,
            "rejected_signal_count": request.track_record.rejected_signal_count,
        },
        "reasons": reasons,
        "created_at": now.isoformat(),
    }

    return RiskBudgetDecision(
        action=request.action,
        allowed=allowed,
        final_notional=final_notional,
        final_multiplier=final_multiplier,
        authority_tier=request.authority.tier,
        tightened=tightened,
        loosened=loosened,
        reasons=reasons,
        audit_hash=_stable_hash(audit_payload),
        created_at=now,
    )
