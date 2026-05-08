"""RoadGuard — shared execution safety governor.

Sits BELOW the lane executors. Equity and Crypto think
independently above; RoadGuard only protects what they share —
capital, broker connectivity, and duplicate-symbol conflicts.

What RoadGuard is NOT:
* Not an ML model
* Not a strategist
* Not another veto layer for signal quality
* Not a lane arbitrator (it never decides "crypto over equity")

What RoadGuard IS:
* A deterministic capital governor:
    - total account exposure cap
    - per-lane exposure caps (equity vs crypto)
    - daily realised-loss cap
    - max open positions (total + per lane)
    - broker connectivity floor
    - duplicate-symbol prevention
* A read-only governor — receives state, returns a decision.
  Never queries the DB, never mutates anything, never takes
  authority for sizing or routing.

Rollout pattern matches Shelly + Fast Veto:

* ``ROADGUARD_SHADOW_ENABLED=true`` (default) — logs every
  decision, but ``enforce=False`` so the executor proceeds
  regardless. Lets the operator watch behaviour for a week
  before any actual blocks.
* ``ROADGUARD_ENFORCE_ENABLED=true`` (operator-gated) — the
  governor's BLOCK / PAUSE_LANE decisions actually short-circuit
  the executor. Until then this layer is observation-only.

Authority guardrail: ``ROADGUARD_CAN_APPROVE`` is hard-coded
``False``. Even when enforce is on, RoadGuard's only authoritative
output is "STOP this trade" — never "promote this trade." Same
shape as Fast Veto.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Env flags ────────────────────────────────────────────────────


ROADGUARD_SHADOW_ENABLED = (
    os.getenv("ROADGUARD_SHADOW_ENABLED", "true").lower() == "true"
)
ROADGUARD_ENFORCE_ENABLED = (
    os.getenv("ROADGUARD_ENFORCE_ENABLED", "false").lower() == "true"
)
# Hard safety: this layer never approves.
ROADGUARD_CAN_APPROVE = False


# ── Threshold defaults (env-tunable) ─────────────────────────────


def _env_float(name: str, default: float) -> float:
    """Read an env var as float; fall back to default on parse fail.
    Belt-and-suspenders so a typo in ``.env`` can't accidentally
    change a capital cap to ``0`` or ``inf``."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[roadguard] invalid value for %s=%r; using default %s",
            name, raw, default,
        )
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[roadguard] invalid value for %s=%r; using default %s",
            name, raw, default,
        )
        return default


MAX_TOTAL_EXPOSURE_USD = _env_float("ROADGUARD_MAX_TOTAL_EXPOSURE_USD", 1500.0)
MAX_EQUITY_EXPOSURE_USD = _env_float("ROADGUARD_MAX_EQUITY_EXPOSURE_USD", 900.0)
MAX_CRYPTO_EXPOSURE_USD = _env_float("ROADGUARD_MAX_CRYPTO_EXPOSURE_USD", 600.0)
MAX_DAILY_LOSS_USD = _env_float("ROADGUARD_MAX_DAILY_LOSS_USD", 100.0)

MAX_OPEN_POSITIONS_TOTAL = _env_int("ROADGUARD_MAX_OPEN_POSITIONS_TOTAL", 8)
MAX_OPEN_POSITIONS_PER_LANE = _env_int("ROADGUARD_MAX_OPEN_POSITIONS_PER_LANE", 5)

BROKER_HEALTH_MIN = _env_float("ROADGUARD_BROKER_HEALTH_MIN", 0.70)

ALLOW_DUPLICATE_SYMBOLS = (
    os.getenv("ROADGUARD_ALLOW_DUPLICATE_SYMBOLS", "false").lower() == "true"
)


# ── Enums ────────────────────────────────────────────────────────


class RoadGuardDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    PAUSE_LANE = "PAUSE_LANE"


class Lane(str, Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    UNKNOWN = "unknown"


# ── Data shapes ──────────────────────────────────────────────────


@dataclass
class RoadGuardRequest:
    symbol: str
    lane: str
    requested_notional_usd: float

    current_total_exposure_usd: float
    current_equity_exposure_usd: float
    current_crypto_exposure_usd: float

    open_positions_total: int
    open_positions_in_lane: int

    daily_realized_pnl_usd: float

    broker_health_score: float

    existing_open_symbols: List[str] = field(default_factory=list)


@dataclass
class RoadGuardResponse:
    decision: str
    allowed: bool
    enforce: bool          # True only when ENFORCE flag is set AND decision != ALLOW
    reason: Optional[str]
    lane: str
    symbol: str
    timestamp: str
    diagnostics: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Core ────────────────────────────────────────────────────────


class RoadGuard:
    """Shared execution safety layer.

    Stateless. Same input always yields the same output — caller
    can replay a request from logs and get the identical verdict.
    """

    def evaluate(self, req: RoadGuardRequest) -> RoadGuardResponse:
        # Cascade order matters — broker health and daily loss
        # supersede everything because they're account-wide kills,
        # not per-trade caps.

        if req.broker_health_score < BROKER_HEALTH_MIN:
            return self._block(req, reason="BROKER_HEALTH_DEGRADED")

        if req.daily_realized_pnl_usd <= -MAX_DAILY_LOSS_USD:
            # PAUSE_LANE rather than BLOCK — daily loss should
            # halt new entries until the next session, but the
            # operator may still want to manage existing positions.
            return self._pause_lane(req, reason="MAX_DAILY_LOSS_REACHED")

        projected_total = (
            req.current_total_exposure_usd + req.requested_notional_usd
        )
        if projected_total > MAX_TOTAL_EXPOSURE_USD:
            return self._block(req, reason="MAX_TOTAL_EXPOSURE")

        if req.lane == Lane.EQUITY.value:
            projected_equity = (
                req.current_equity_exposure_usd + req.requested_notional_usd
            )
            if projected_equity > MAX_EQUITY_EXPOSURE_USD:
                return self._block(req, reason="MAX_EQUITY_EXPOSURE")
        elif req.lane == Lane.CRYPTO.value:
            projected_crypto = (
                req.current_crypto_exposure_usd + req.requested_notional_usd
            )
            if projected_crypto > MAX_CRYPTO_EXPOSURE_USD:
                return self._block(req, reason="MAX_CRYPTO_EXPOSURE")

        if req.open_positions_total >= MAX_OPEN_POSITIONS_TOTAL:
            return self._block(req, reason="MAX_OPEN_POSITIONS_TOTAL")

        if req.open_positions_in_lane >= MAX_OPEN_POSITIONS_PER_LANE:
            return self._block(req, reason="MAX_OPEN_POSITIONS_PER_LANE")

        if (
            not ALLOW_DUPLICATE_SYMBOLS
            and req.symbol in req.existing_open_symbols
        ):
            return self._block(req, reason="DUPLICATE_SYMBOL")

        return RoadGuardResponse(
            decision=RoadGuardDecision.ALLOW.value,
            allowed=True,
            enforce=False,
            reason=None,
            lane=req.lane,
            symbol=req.symbol,
            timestamp=datetime.now(timezone.utc).isoformat(),
            diagnostics={
                "projected_total_exposure_usd": projected_total,
                "daily_realized_pnl_usd": req.daily_realized_pnl_usd,
                "broker_health_score": req.broker_health_score,
                "open_positions_total": req.open_positions_total,
                "open_positions_in_lane": req.open_positions_in_lane,
            },
        )

    # ── Helpers ─────────────────────────────────────────────

    def _block(self, req: RoadGuardRequest, reason: str) -> RoadGuardResponse:
        return self._stop(req, RoadGuardDecision.BLOCK, reason)

    def _pause_lane(self, req: RoadGuardRequest, reason: str) -> RoadGuardResponse:
        return self._stop(req, RoadGuardDecision.PAUSE_LANE, reason)

    def _stop(
        self,
        req: RoadGuardRequest,
        decision: RoadGuardDecision,
        reason: str,
    ) -> RoadGuardResponse:
        # ``enforce`` reflects whether this verdict actually
        # short-circuits the executor at the call site. In shadow
        # mode the verdict is recorded but ``enforce=False`` so
        # the trade proceeds.
        enforce = bool(ROADGUARD_ENFORCE_ENABLED and not ROADGUARD_CAN_APPROVE)
        return RoadGuardResponse(
            decision=decision.value,
            allowed=False,
            enforce=enforce,
            reason=reason,
            lane=req.lane,
            symbol=req.symbol,
            timestamp=datetime.now(timezone.utc).isoformat(),
            diagnostics={
                "requested_notional_usd": req.requested_notional_usd,
                "current_total_exposure_usd": req.current_total_exposure_usd,
                "current_equity_exposure_usd": req.current_equity_exposure_usd,
                "current_crypto_exposure_usd": req.current_crypto_exposure_usd,
                "open_positions_total": req.open_positions_total,
                "open_positions_in_lane": req.open_positions_in_lane,
                "broker_health_score": req.broker_health_score,
                "daily_realized_pnl_usd": req.daily_realized_pnl_usd,
            },
        )


# ── State-collection helper ──────────────────────────────────────


def build_roadguard_request(
    *,
    symbol: str,
    lane: str,
    requested_notional_usd: float,
    open_positions: Optional[List[Dict[str, Any]]] = None,
    daily_realized_pnl_usd: float = 0.0,
    broker_health_score: float = 1.0,
) -> RoadGuardRequest:
    """Derive a :class:`RoadGuardRequest` from the executor's
    existing per-call state.

    ``open_positions`` is the same list the executor already
    threads through for the portfolio-risk engine — each entry is
    a dict with ``size_usd``, ``symbol``, and either ``lane`` or
    ``asset_type`` (the latter for older docs). Missing fields
    fall back to safe defaults (zero exposure, equity lane).

    ``broker_health_score`` defaults to ``1.0`` (healthy) — the
    executor passes a real score when a connectivity probe is
    available. The fail-closed shape lives in
    :class:`RoadGuard.evaluate` so a bogus 0.0 explicitly blocks.
    """
    open_positions = open_positions or []

    total_exposure = 0.0
    equity_exposure = 0.0
    crypto_exposure = 0.0
    open_in_lane = 0
    existing_symbols: List[str] = []

    for pos in open_positions:
        size = float(pos.get("size_usd") or 0.0)
        total_exposure += size

        # Per-lane attribution. Prefer explicit lane tag; fall
        # back to asset_type; default to equity (safer — adds the
        # tighter equity cap).
        pos_lane = (pos.get("lane") or "").lower()
        if not pos_lane:
            asset_type = (pos.get("asset_type") or "").lower()
            pos_lane = "crypto" if asset_type == "crypto" else "equity"

        if pos_lane == "crypto":
            crypto_exposure += size
        else:
            equity_exposure += size

        if pos_lane == lane:
            open_in_lane += 1

        sym = pos.get("symbol")
        if sym:
            existing_symbols.append(str(sym))

    return RoadGuardRequest(
        symbol=symbol,
        lane=lane,
        requested_notional_usd=float(requested_notional_usd),
        current_total_exposure_usd=total_exposure,
        current_equity_exposure_usd=equity_exposure,
        current_crypto_exposure_usd=crypto_exposure,
        open_positions_total=len(open_positions),
        open_positions_in_lane=open_in_lane,
        daily_realized_pnl_usd=float(daily_realized_pnl_usd),
        broker_health_score=float(broker_health_score),
        existing_open_symbols=existing_symbols,
    )


# ── Async shadow log (fire-and-forget) ───────────────────────────


async def log_roadguard_decision(
    db: Any,
    *,
    request: RoadGuardRequest,
    response: RoadGuardResponse,
) -> None:
    """Persist every RoadGuard decision so the operator can audit
    promotion-readiness. Never raises; logging failures must not
    affect the executor."""
    if not ROADGUARD_SHADOW_ENABLED:
        return
    if db is None:
        return

    try:
        doc = {
            "created_at": datetime.now(timezone.utc),
            "decision": response.decision,
            "allowed": response.allowed,
            "enforced": response.enforce,
            "reason": response.reason,
            "lane": response.lane,
            "symbol": response.symbol,
            "request": asdict(request),
            "response": response.as_dict(),
            "schema_version": 1,
        }
        await db.roadguard_decisions.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[roadguard] shadow log insert failed: %s", exc)
