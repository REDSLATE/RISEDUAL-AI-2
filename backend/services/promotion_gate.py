"""
RISEDUAL Promotion Gate v1
==========================

Purpose
-------
Prevent "NOT_READY forever" without weakening safety.

Principles:
1. Hard safety failures remain absolute blockers.
2. Performance goals never auto-relax.
3. Performance criteria can be flagged NEEDS_RECALIBRATION when evidence says
   the threshold, not the strategy, is likely the blocker.
4. Current readiness is evaluated on the current evaluation epoch + rolling window.
5. Lifetime history is retained for regression/reference, but does not poison a new
   materially changed execution epoch forever.
6. Measured execution cost replaces assumed cost only after enough real live fills.
7. Kernel/promotion logic does not veto individual trades. It grades readiness.

The module is deliberately dependency-free and can be wired into FastAPI/Convex-backed
Mission Control without creating a new broker or execution path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from math import inf
from statistics import mean
from typing import Iterable, Optional, Sequence


class PromotionState(str, Enum):
    PASS = "PASS"
    NEAR_PASS = "NEAR_PASS"
    NEEDS_RECALIBRATION = "NEEDS_RECALIBRATION"
    FAIL = "FAIL"
    HARD_STOP = "HARD_STOP"


class CriterionKind(str, Enum):
    HARD_SAFETY = "hard_safety"
    PERFORMANCE = "performance"
    SAMPLE = "sample"


@dataclass(frozen=True)
class ExecutionFill:
    """
    One real execution leg.

    fee_pct:
        Fee as percent of notional for this leg, e.g. 0.08 means 0.08%.
    reference_price:
        Signal/confirmation price for entry, or intended exit reference for exit.
        Used to measure slippage.
    fill_price:
        Actual broker/exchange fill price.
    side:
        BUY or SELL.
    liquidity:
        maker / taker / unknown.
    """
    fill_id: str
    trade_id: str
    timestamp: datetime
    side: str
    fill_price: float
    quantity: float
    fee_pct: float
    reference_price: Optional[float] = None
    liquidity: str = "unknown"
    epoch_id: str = "default"

    @property
    def slippage_pct(self) -> float:
        if not self.reference_price or self.reference_price <= 0 or self.fill_price <= 0:
            return 0.0
        side = self.side.upper()
        if side == "BUY":
            return ((self.fill_price - self.reference_price) / self.reference_price) * 100.0
        if side == "SELL":
            return ((self.reference_price - self.fill_price) / self.reference_price) * 100.0
        return abs(self.fill_price - self.reference_price) / self.reference_price * 100.0

    @property
    def effective_cost_pct(self) -> float:
        # Beneficial price improvement is allowed to offset fee cost, but the reported
        # execution cost is floored at zero for conservative readiness accounting.
        return max(0.0, self.fee_pct + self.slippage_pct)


@dataclass(frozen=True)
class EvaluationObservation:
    """
    A resolved strategy observation/trade expressed in percent return.

    gross_return_pct:
        Strategy return before execution costs.
    realized_net_return_pct:
        Optional broker-truth net return. When present it is retained for diagnostics,
        but normalized evaluation uses the selected cost model consistently.
    """
    observation_id: str
    resolved_at: datetime
    gross_return_pct: float
    symbol: str = ""
    strategy: str = ""
    epoch_id: str = "default"
    realized_net_return_pct: Optional[float] = None
    risk_violation: bool = False
    execution_failure: bool = False
    data_integrity_failure: bool = False
    invalid_order_behavior: bool = False


@dataclass(frozen=True)
class CostEstimate:
    source: str  # "measured" or "assumed"
    round_trip_cost_pct: float
    eligible_fill_count: int
    maker_fill_count: int
    taker_fill_count: int
    avg_leg_cost_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PromotionConfig:
    # Current-evidence window
    recent_window_observations: int = 300
    min_observations: int = 100
    min_elapsed_hours: float = 24.0

    # Cost model
    assumed_round_trip_cost_pct: float = 0.30
    min_measured_fills: int = 30

    # Performance targets (percent returns, not fractions)
    min_net_expectancy_pct: float = 0.0
    min_profit_factor: float = 1.05
    max_drawdown_per_100_obs_pct: float = 10.0

    # "Near" band allows the UI to distinguish close misses from genuine failure.
    near_expectancy_margin_pct: float = 0.05
    near_profit_factor_margin: float = 0.10
    near_drawdown_multiplier: float = 1.25

    # Feasibility diagnostics. These do NOT mutate thresholds.
    recalibration_min_observations: int = 200
    recalibration_positive_expectancy_pct: float = 0.0
    recalibration_drawdown_multiple: float = 2.0
    recalibration_pf_floor: float = 1.0

    # Hard safety. Counts are evaluated in the active recent window.
    max_risk_violations: int = 0
    max_execution_failures: int = 0
    max_data_integrity_failures: int = 0
    max_invalid_order_behaviors: int = 0


@dataclass(frozen=True)
class CriterionResult:
    name: str
    kind: CriterionKind
    state: PromotionState
    actual: float | int | str | None
    target: str
    explanation: str


@dataclass
class PromotionDecision:
    state: PromotionState
    epoch_id: str
    evaluated_observations: int
    lifetime_observations: int
    window_started_at: Optional[datetime]
    window_ended_at: Optional[datetime]
    cost: CostEstimate
    gross_expectancy_pct: float
    net_expectancy_pct: float
    profit_factor: float
    max_drawdown_pct: float
    max_drawdown_per_100_obs_pct: float
    criteria: list[CriterionResult] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    recalibration_candidates: list[str] = field(default_factory=list)
    lifetime_reference: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["cost"] = self.cost.to_dict()
        d["criteria"] = [
            {
                **asdict(c),
                "kind": c.kind.value,
                "state": c.state.value,
            }
            for c in self.criteria
        ]
        for key in ("window_started_at", "window_ended_at"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d


class MeasuredCostFeed:
    """
    Chooses measured live execution cost only after a minimum real-fill sample.
    Until then, the configured assumed round-trip cost remains authoritative.

    To avoid pretending a single entry leg is a round trip, measured round-trip cost
    is estimated as 2 * average effective leg cost across eligible fills.
    If your execution store already links entry+exit fills, replace this calculation
    with exact paired-trade round-trip cost; the PromotionGate API does not change.
    """

    def __init__(self, assumed_round_trip_cost_pct: float, min_measured_fills: int = 30):
        if assumed_round_trip_cost_pct < 0:
            raise ValueError("assumed_round_trip_cost_pct must be >= 0")
        if min_measured_fills < 1:
            raise ValueError("min_measured_fills must be >= 1")
        self.assumed = assumed_round_trip_cost_pct
        self.min_measured_fills = min_measured_fills

    def estimate(self, fills: Sequence[ExecutionFill], epoch_id: str) -> CostEstimate:
        eligible = [
            f for f in fills
            if f.epoch_id == epoch_id and f.fill_price > 0 and f.quantity > 0
        ]
        maker = sum(1 for f in eligible if f.liquidity.lower() == "maker")
        taker = sum(1 for f in eligible if f.liquidity.lower() == "taker")

        if len(eligible) < self.min_measured_fills:
            return CostEstimate(
                source="assumed",
                round_trip_cost_pct=self.assumed,
                eligible_fill_count=len(eligible),
                maker_fill_count=maker,
                taker_fill_count=taker,
                avg_leg_cost_pct=None,
            )

        avg_leg = mean(f.effective_cost_pct for f in eligible)
        return CostEstimate(
            source="measured",
            round_trip_cost_pct=max(0.0, avg_leg * 2.0),
            eligible_fill_count=len(eligible),
            maker_fill_count=maker,
            taker_fill_count=taker,
            avg_leg_cost_pct=avg_leg,
        )


def _profit_factor(net_returns: Sequence[float]) -> float:
    wins = sum(r for r in net_returns if r > 0)
    losses = abs(sum(r for r in net_returns if r < 0))
    if losses == 0:
        return inf if wins > 0 else 0.0
    return wins / losses


def _max_drawdown_pct(net_returns: Sequence[float]) -> float:
    """
    Additive return curve drawdown in percentage points.

    This deliberately avoids pretending independent observations compound as an
    account equity curve. If your Edge Slicer currently scores counterfactual
    observations, this is safer than compounding duplicated observations.
    """
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in net_returns:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _drawdown_per_100(max_drawdown_pct: float, n: int) -> float:
    if n <= 0:
        return 0.0
    return max_drawdown_pct * (100.0 / n)


def _elapsed_hours(obs: Sequence[EvaluationObservation]) -> float:
    if len(obs) < 2:
        return 0.0
    return max(0.0, (obs[-1].resolved_at - obs[0].resolved_at).total_seconds() / 3600.0)


class PromotionGate:
    def __init__(self, config: PromotionConfig | None = None):
        self.config = config or PromotionConfig()

    def evaluate(
        self,
        observations: Sequence[EvaluationObservation],
        fills: Sequence[ExecutionFill],
        epoch_id: str,
    ) -> PromotionDecision:
        cfg = self.config
        lifetime = sorted(observations, key=lambda o: o.resolved_at)
        epoch_obs = [o for o in lifetime if o.epoch_id == epoch_id]
        recent = epoch_obs[-cfg.recent_window_observations:]

        cost = MeasuredCostFeed(
            cfg.assumed_round_trip_cost_pct,
            cfg.min_measured_fills,
        ).estimate(fills, epoch_id)

        gross = [o.gross_return_pct for o in recent]
        net = [r - cost.round_trip_cost_pct for r in gross]

        gross_exp = mean(gross) if gross else 0.0
        net_exp = mean(net) if net else 0.0
        pf = _profit_factor(net)
        max_dd = _max_drawdown_pct(net)
        dd100 = _drawdown_per_100(max_dd, len(net))

        criteria: list[CriterionResult] = []
        blockers: list[str] = []
        recalibration: list[str] = []

        def add(
            name: str,
            kind: CriterionKind,
            state: PromotionState,
            actual,
            target: str,
            explanation: str,
        ):
            criteria.append(CriterionResult(name, kind, state, actual, target, explanation))
            if state in (PromotionState.FAIL, PromotionState.HARD_STOP):
                blockers.append(name)
            if state == PromotionState.NEEDS_RECALIBRATION:
                recalibration.append(name)

        # ---------- hard safety ----------
        safety_checks = (
            ("risk_violations", sum(o.risk_violation for o in recent), cfg.max_risk_violations),
            ("execution_failures", sum(o.execution_failure for o in recent), cfg.max_execution_failures),
            ("data_integrity_failures", sum(o.data_integrity_failure for o in recent), cfg.max_data_integrity_failures),
            ("invalid_order_behavior", sum(o.invalid_order_behavior for o in recent), cfg.max_invalid_order_behaviors),
        )
        for name, actual, allowed in safety_checks:
            state = PromotionState.PASS if actual <= allowed else PromotionState.HARD_STOP
            add(
                name, CriterionKind.HARD_SAFETY, state, actual, f"<= {allowed}",
                "Hard safety condition. This criterion is never auto-recalibrated."
            )

        # ---------- sample sufficiency ----------
        n = len(recent)
        if n >= cfg.min_observations:
            sample_state = PromotionState.PASS
        elif n >= max(1, int(cfg.min_observations * 0.75)):
            sample_state = PromotionState.NEAR_PASS
        else:
            sample_state = PromotionState.FAIL
        add(
            "minimum_observations", CriterionKind.SAMPLE, sample_state, n,
            f">= {cfg.min_observations}",
            "Readiness uses the current epoch rolling window, not lifetime history."
        )

        hours = _elapsed_hours(recent)
        if hours >= cfg.min_elapsed_hours:
            elapsed_state = PromotionState.PASS
        elif hours >= cfg.min_elapsed_hours * 0.75:
            elapsed_state = PromotionState.NEAR_PASS
        else:
            elapsed_state = PromotionState.FAIL
        add(
            "minimum_elapsed_time", CriterionKind.SAMPLE, elapsed_state, round(hours, 3),
            f">= {cfg.min_elapsed_hours} hours",
            "Prevents a burst of correlated observations from satisfying promotion immediately."
        )

        # ---------- performance ----------
        if net_exp >= cfg.min_net_expectancy_pct:
            exp_state = PromotionState.PASS
        elif net_exp >= cfg.min_net_expectancy_pct - cfg.near_expectancy_margin_pct:
            exp_state = PromotionState.NEAR_PASS
        else:
            exp_state = PromotionState.FAIL
        add(
            "net_expectancy", CriterionKind.PERFORMANCE, exp_state, round(net_exp, 6),
            f">= {cfg.min_net_expectancy_pct:.6f}%",
            f"Uses {cost.source} round-trip cost of {cost.round_trip_cost_pct:.6f}%."
        )

        if pf >= cfg.min_profit_factor:
            pf_state = PromotionState.PASS
        elif pf >= max(0.0, cfg.min_profit_factor - cfg.near_profit_factor_margin):
            pf_state = PromotionState.NEAR_PASS
        else:
            pf_state = PromotionState.FAIL
        add(
            "profit_factor", CriterionKind.PERFORMANCE, pf_state,
            "inf" if pf == inf else round(pf, 6),
            f">= {cfg.min_profit_factor:.4f}",
            "Computed from net observation returns under the same selected cost model."
        )

        if dd100 <= cfg.max_drawdown_per_100_obs_pct:
            dd_state = PromotionState.PASS
        elif dd100 <= cfg.max_drawdown_per_100_obs_pct * cfg.near_drawdown_multiplier:
            dd_state = PromotionState.NEAR_PASS
        else:
            dd_state = PromotionState.FAIL

        # Goal-feasibility diagnostic:
        # positive net edge + non-losing PF + substantial sample + a wildly missed
        # drawdown threshold => surface the threshold for operator recalibration.
        # DO NOT silently pass it and DO NOT change the target.
        if (
            dd_state == PromotionState.FAIL
            and n >= cfg.recalibration_min_observations
            and net_exp > cfg.recalibration_positive_expectancy_pct
            and pf >= cfg.recalibration_pf_floor
            and dd100 > cfg.max_drawdown_per_100_obs_pct * cfg.recalibration_drawdown_multiple
        ):
            dd_state = PromotionState.NEEDS_RECALIBRATION

        add(
            "drawdown_per_100_observations",
            CriterionKind.PERFORMANCE,
            dd_state,
            round(dd100, 6),
            f"<= {cfg.max_drawdown_per_100_obs_pct:.6f}%",
            (
                "Normalized additive observation-curve drawdown. "
                "NEEDS_RECALIBRATION means the target is suspect given a sufficiently "
                "large positive-edge sample; operator approval is still required."
            ),
        )

        # ---------- final state ----------
        states = [c.state for c in criteria]
        if PromotionState.HARD_STOP in states:
            final_state = PromotionState.HARD_STOP
        elif PromotionState.NEEDS_RECALIBRATION in states:
            final_state = PromotionState.NEEDS_RECALIBRATION
        elif PromotionState.FAIL in states:
            final_state = PromotionState.FAIL
        elif PromotionState.NEAR_PASS in states:
            final_state = PromotionState.NEAR_PASS
        else:
            final_state = PromotionState.PASS

        lifetime_gross = [o.gross_return_pct for o in lifetime]
        lifetime_reference = {
            "observations": len(lifetime),
            "gross_expectancy_pct": mean(lifetime_gross) if lifetime_gross else 0.0,
            "note": (
                "Lifetime history is retained for reference/regression detection only. "
                "It does not gate the current evaluation epoch."
            ),
        }

        return PromotionDecision(
            state=final_state,
            epoch_id=epoch_id,
            evaluated_observations=n,
            lifetime_observations=len(lifetime),
            window_started_at=recent[0].resolved_at if recent else None,
            window_ended_at=recent[-1].resolved_at if recent else None,
            cost=cost,
            gross_expectancy_pct=gross_exp,
            net_expectancy_pct=net_exp,
            profit_factor=pf,
            max_drawdown_pct=max_dd,
            max_drawdown_per_100_obs_pct=dd100,
            criteria=criteria,
            blockers=blockers,
            recalibration_candidates=recalibration,
            lifetime_reference=lifetime_reference,
        )


@dataclass(frozen=True)
class EvaluationEpoch:
    epoch_id: str
    started_at: datetime
    code_revision: str
    reason: str
    execution_policy_hash: str = ""


def begin_evaluation_epoch(
    code_revision: str,
    reason: str,
    execution_policy_hash: str = "",
    now: Optional[datetime] = None,
) -> EvaluationEpoch:
    """
    Call this when execution economics materially change: e.g. maker ladder,
    broker adapter, fill policy, timing logic, or fee model.
    """
    now = now or datetime.now(timezone.utc)
    compact = now.strftime("%Y%m%dT%H%M%SZ")
    rev = (code_revision or "unknown")[:12]
    return EvaluationEpoch(
        epoch_id=f"{compact}:{rev}",
        started_at=now,
        code_revision=code_revision,
        reason=reason,
        execution_policy_hash=execution_policy_hash,
    )
