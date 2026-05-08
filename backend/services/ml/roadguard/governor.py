"""RoadGuard v2 — lane-isolated 10-gate deterministic capital governor.

Architecture (per user spec):
  * One RoadGuard PER executor lane.
  * EquityRoadGuard sees ONLY equity exposure / equity caps.
  * CryptoRoadGuard sees ONLY crypto exposure / crypto caps.
  * A signal that arrives at the wrong lane RG fails LANE_MISMATCH
    immediately — the pair is a closed loop.
  * Each pair writes to its own decision log collection so the
    operator can audit equity vs crypto promotion-readiness
    independently.

Authority invariant: ``ROADGUARD_CAN_APPROVE = False``. Returns one
of ``PASS``, ``REDUCE``, or ``BLOCK`` — never ``APPROVE``.

10 gates per RG (cascade order — first failure short-circuits):
  G00 Lane match                              (BLOCK)
  G01 Broker connectivity floor               (BLOCK)
  G02 Daily realised-loss cap                 (BLOCK)
  G09 Min ticket size sanity                  (BLOCK)
  G03 Account total exposure cap              (REDUCE / BLOCK)
  G04 Lane exposure cap                       (REDUCE / BLOCK)
  G05 Max open positions total                (BLOCK)
  G06 Max open positions per lane             (BLOCK)
  G07 Duplicate-symbol prevention             (BLOCK)
  G08 Cash floor                              (REDUCE / BLOCK)
  G10 Kill-switch (BROKER_LIVE_ORDER_ENABLED) (BLOCK if would-be live)

Stateless. Same input always returns the same verdict.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Hard authority pin (pinned by tests) ──────────────────────────


ROADGUARD_CAN_APPROVE = False  # NEVER flip this.


# ── Env helpers ───────────────────────────────────────────────────


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("[roadguard.v2] bad float for %s=%r; using %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("[roadguard.v2] bad int for %s=%r; using %s", name, raw, default)
        return default


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() == "true"


# ── Shapes ────────────────────────────────────────────────────────


class RGDecision(str, Enum):
    PASS = "PASS"
    REDUCE = "REDUCE"
    BLOCK = "BLOCK"


@dataclass
class TradeIntent:
    symbol: str
    lane: str   # "equity" | "crypto"
    side: str   # "BUY" | "SELL"
    requested_notional_usd: float
    will_hit_live_broker: bool = False  # G10 only fires when this is True


@dataclass
class AccountSnapshot:
    cash_usd: float
    equity_value_usd: float
    daily_realized_pnl_usd: float
    broker_health_score: float
    total_exposure_usd: float
    equity_exposure_usd: float
    crypto_exposure_usd: float
    open_positions_total: int
    open_positions_in_lane: int
    existing_open_symbols: List[str] = field(default_factory=list)


@dataclass
class RoadGuardVerdict:
    decision: str             # RGDecision value
    reason: Optional[str]
    gate: Optional[str]       # which gate fired (G00..G10) or None on PASS
    adjusted_notional_usd: Optional[float]   # set on REDUCE
    can_approve: bool
    lane: str                 # which lane RG produced this verdict
    diagnostics: Dict[str, Any]
    timestamp: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Per-lane threshold config ─────────────────────────────────────


@dataclass
class _LaneThresholds:
    """Resolved threshold table for a single lane RG. Read fresh
    from the environment on every evaluate() call so an operator
    can tune via env without restarting the process."""
    lane: str
    max_total_exposure_usd: float
    max_lane_exposure_usd: float
    max_daily_loss_usd: float
    max_open_positions_total: int
    max_open_positions_per_lane: int
    broker_health_min: float
    min_ticket_usd: float
    cash_floor_usd: float
    allow_duplicate_symbols: bool
    broker_live_order_enabled: bool
    enforce_enabled: bool

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Defaults are intentionally tighter for equity (regulated venue,
# tight spreads) and looser for crypto (24/7, wider spreads, larger
# tick sizes acceptable). Each is independently env-tunable; a
# misconfigured equity env DOES NOT affect crypto and vice versa.

_EQUITY_DEFAULTS = {
    "max_lane_exposure":      900.0,
    "max_total_exposure":     1500.0,
    "max_daily_loss":         100.0,
    "max_positions_total":    8,
    "max_positions_per_lane": 5,
    "broker_health_min":      0.70,
    "min_ticket":             1.0,
    "cash_floor":             0.0,
}

_CRYPTO_DEFAULTS = {
    "max_lane_exposure":      600.0,
    "max_total_exposure":     1500.0,
    "max_daily_loss":         100.0,
    "max_positions_total":    8,
    "max_positions_per_lane": 5,
    "broker_health_min":      0.65,   # crypto exchanges run with more jitter
    "min_ticket":             5.0,    # minimum crypto ticket sized for fees
    "cash_floor":             0.0,
}


def _lane_thresholds(lane: str) -> _LaneThresholds:
    """Resolve env-tunable thresholds for one lane. The env-var
    naming convention is ``ROADGUARD_<LANE>_<KEY>`` — e.g.
    ``ROADGUARD_EQUITY_MAX_LANE_EXPOSURE_USD``.

    A v1 backwards-compat alias is read first so an operator using
    legacy ``ROADGUARD_V2_*`` settings doesn't lose them.
    """
    if lane == "equity":
        d = _EQUITY_DEFAULTS
        prefix = "ROADGUARD_EQUITY_"
        legacy = "ROADGUARD_V2_MAX_EQUITY_EXPOSURE_USD"
    elif lane == "crypto":
        d = _CRYPTO_DEFAULTS
        prefix = "ROADGUARD_CRYPTO_"
        legacy = "ROADGUARD_V2_MAX_CRYPTO_EXPOSURE_USD"
    else:
        raise ValueError(f"unknown lane {lane!r}")

    return _LaneThresholds(
        lane=lane,
        max_total_exposure_usd=_env_float(f"{prefix}MAX_TOTAL_EXPOSURE_USD",
            _env_float("ROADGUARD_V2_MAX_TOTAL_EXPOSURE_USD", d["max_total_exposure"])),
        max_lane_exposure_usd=_env_float(f"{prefix}MAX_LANE_EXPOSURE_USD",
            _env_float(legacy, d["max_lane_exposure"])),
        max_daily_loss_usd=_env_float(f"{prefix}MAX_DAILY_LOSS_USD",
            _env_float("ROADGUARD_V2_MAX_DAILY_LOSS_USD", d["max_daily_loss"])),
        max_open_positions_total=_env_int(f"{prefix}MAX_OPEN_POSITIONS_TOTAL",
            _env_int("ROADGUARD_V2_MAX_OPEN_POSITIONS_TOTAL", d["max_positions_total"])),
        max_open_positions_per_lane=_env_int(f"{prefix}MAX_OPEN_POSITIONS_PER_LANE",
            _env_int("ROADGUARD_V2_MAX_OPEN_POSITIONS_PER_LANE", d["max_positions_per_lane"])),
        broker_health_min=_env_float(f"{prefix}BROKER_HEALTH_MIN",
            _env_float("ROADGUARD_V2_BROKER_HEALTH_MIN", d["broker_health_min"])),
        min_ticket_usd=_env_float(f"{prefix}MIN_TICKET_USD",
            _env_float("ROADGUARD_V2_MIN_TICKET_USD", d["min_ticket"])),
        cash_floor_usd=_env_float(f"{prefix}CASH_FLOOR_USD",
            _env_float("ROADGUARD_V2_CASH_FLOOR_USD", d["cash_floor"])),
        allow_duplicate_symbols=_env_bool(f"{prefix}ALLOW_DUPLICATE_SYMBOLS", "false"),
        broker_live_order_enabled=_env_bool("BROKER_LIVE_ORDER_ENABLED", "false"),
        enforce_enabled=_env_bool(f"{prefix}ENFORCE_ENABLED", "false"),
    )


# ── Base governor ────────────────────────────────────────────────


class _BaseRoadGuard:
    """Stateless 10-gate evaluator pinned to a single lane.

    Subclasses set ``LANE`` to either ``"equity"`` or ``"crypto"``
    and the concrete classes :class:`EquityRoadGuard` and
    :class:`CryptoRoadGuard` are what callers instantiate.
    """
    LANE: str = "base"

    def evaluate(
        self,
        intent: TradeIntent,
        snapshot: AccountSnapshot,
    ) -> RoadGuardVerdict:
        # Hard pin — even if a buggy override flips the constant.
        assert ROADGUARD_CAN_APPROVE is False, (
            "RoadGuard authority invariant broken — cannot approve"
        )

        ts = datetime.now(timezone.utc).isoformat()
        T = _lane_thresholds(self.LANE)

        # G00 — lane match. Closed loop: an equity executor MUST NOT
        # be able to ask the crypto roadguard for a verdict. This
        # gate is what makes the pair "closer" than the v1 unified
        # governor.
        if intent.lane != self.LANE:
            return self._block(
                intent, "G00", f"LANE_MISMATCH:expected_{self.LANE}_got_{intent.lane}",
                ts, T,
            )

        # G01 — broker connectivity
        if snapshot.broker_health_score < T.broker_health_min:
            return self._block(intent, "G01", "BROKER_HEALTH_DEGRADED", ts, T)

        # G02 — daily loss cap
        if snapshot.daily_realized_pnl_usd <= -T.max_daily_loss_usd:
            return self._block(intent, "G02", "DAILY_LOSS_CAP_HIT", ts, T)

        # G09 — min ticket sanity (cheap pre-check)
        if intent.requested_notional_usd < T.min_ticket_usd:
            return self._block(intent, "G09", "TICKET_TOO_SMALL", ts, T)

        # G03 — account total exposure
        projected_total = snapshot.total_exposure_usd + intent.requested_notional_usd
        if projected_total > T.max_total_exposure_usd:
            headroom = max(0.0, T.max_total_exposure_usd - snapshot.total_exposure_usd)
            if headroom >= T.min_ticket_usd:
                return self._reduce(intent, "G03", "TOTAL_EXPOSURE_REDUCE",
                                    headroom, ts, T)
            return self._block(intent, "G03", "TOTAL_EXPOSURE_FULL", ts, T)

        # G04 — lane exposure (THIS RG only checks ITS OWN lane).
        lane_exp = (
            snapshot.equity_exposure_usd if self.LANE == "equity"
            else snapshot.crypto_exposure_usd
        )
        projected_lane = lane_exp + intent.requested_notional_usd
        if projected_lane > T.max_lane_exposure_usd:
            headroom = max(0.0, T.max_lane_exposure_usd - lane_exp)
            if headroom >= T.min_ticket_usd:
                return self._reduce(intent, "G04", "LANE_EXPOSURE_REDUCE",
                                    headroom, ts, T)
            return self._block(intent, "G04", "LANE_EXPOSURE_FULL", ts, T)

        # G05 — max positions total
        if snapshot.open_positions_total >= T.max_open_positions_total:
            return self._block(intent, "G05", "MAX_POSITIONS_TOTAL", ts, T)

        # G06 — max positions per lane
        if snapshot.open_positions_in_lane >= T.max_open_positions_per_lane:
            return self._block(intent, "G06", "MAX_POSITIONS_PER_LANE", ts, T)

        # G07 — duplicate symbols
        if (
            not T.allow_duplicate_symbols
            and intent.symbol in (snapshot.existing_open_symbols or [])
        ):
            return self._block(intent, "G07", "DUPLICATE_SYMBOL", ts, T)

        # G08 — cash floor (BUY only)
        if intent.side == "BUY":
            projected_cash = snapshot.cash_usd - intent.requested_notional_usd
            if projected_cash < T.cash_floor_usd:
                affordable = max(0.0, snapshot.cash_usd - T.cash_floor_usd)
                if affordable >= T.min_ticket_usd:
                    return self._reduce(intent, "G08", "CASH_FLOOR_REDUCE",
                                        affordable, ts, T)
                return self._block(intent, "G08", "CASH_FLOOR_BLOCK", ts, T)

        # G10 — kill-switch
        if intent.will_hit_live_broker and not T.broker_live_order_enabled:
            return self._block(intent, "G10", "BROKER_LIVE_ORDER_DISABLED", ts, T)

        # All clear — PASS.
        return RoadGuardVerdict(
            decision=RGDecision.PASS.value,
            reason=None,
            gate=None,
            adjusted_notional_usd=None,
            can_approve=False,
            lane=self.LANE,
            diagnostics={
                "projected_total_exposure_usd": projected_total,
                "projected_lane_exposure_usd": projected_lane,
                "thresholds": T.as_dict(),
            },
            timestamp=ts,
        )

    # ── Helpers ──────────────────────────────────────────────

    def _block(self, intent, gate, reason, ts, T):
        return RoadGuardVerdict(
            decision=RGDecision.BLOCK.value,
            reason=reason,
            gate=gate,
            adjusted_notional_usd=None,
            can_approve=False,
            lane=self.LANE,
            diagnostics={"intent": asdict(intent), "thresholds": T.as_dict()},
            timestamp=ts,
        )

    def _reduce(self, intent, gate, reason, adjusted, ts, T):
        return RoadGuardVerdict(
            decision=RGDecision.REDUCE.value,
            reason=reason,
            gate=gate,
            adjusted_notional_usd=float(adjusted),
            can_approve=False,
            lane=self.LANE,
            diagnostics={
                "intent": asdict(intent),
                "thresholds": T.as_dict(),
                "original_notional_usd": intent.requested_notional_usd,
            },
            timestamp=ts,
        )


# ── Concrete lane RGs ────────────────────────────────────────────


class EquityRoadGuard(_BaseRoadGuard):
    """Capital governor pinned to the equity executor.

    Only accepts intents with lane=="equity"; anything else fails
    LANE_MISMATCH at G00. Reads ``ROADGUARD_EQUITY_*`` env vars.
    """
    LANE = "equity"

    DECISIONS_COLLECTION = "roadguard_equity_decisions"


class CryptoRoadGuard(_BaseRoadGuard):
    """Capital governor pinned to the crypto executor.

    Only accepts intents with lane=="crypto"; anything else fails
    LANE_MISMATCH at G00. Reads ``ROADGUARD_CRYPTO_*`` env vars.
    """
    LANE = "crypto"

    DECISIONS_COLLECTION = "roadguard_crypto_decisions"


# ── Backwards-compat dispatcher ──────────────────────────────────


class RoadGuardV2:
    """Thin dispatcher kept for backwards compatibility.

    Existing tests, admin endpoints, and the v1 wiring point at
    ``RoadGuardV2().evaluate(intent, snapshot)``. Internally this
    routes to :class:`EquityRoadGuard` or :class:`CryptoRoadGuard`
    based on ``intent.lane``. Unknown lanes fail BLOCK at G04.

    New code should use ``EquityRoadGuard()`` / ``CryptoRoadGuard()``
    directly so the closed-loop pairing is explicit at the call site.
    """

    def __init__(self):
        self.equity = EquityRoadGuard()
        self.crypto = CryptoRoadGuard()

    def evaluate(self, intent: TradeIntent, snapshot: AccountSnapshot) -> RoadGuardVerdict:
        if intent.lane == "equity":
            return self.equity.evaluate(intent, snapshot)
        if intent.lane == "crypto":
            return self.crypto.evaluate(intent, snapshot)
        # Unknown lane — fail BLOCK with G04 to mirror v1 behaviour.
        ts = datetime.now(timezone.utc).isoformat()
        return RoadGuardVerdict(
            decision=RGDecision.BLOCK.value,
            reason=f"UNKNOWN_LANE:{intent.lane}",
            gate="G04",
            adjusted_notional_usd=None,
            can_approve=False,
            lane=intent.lane or "unknown",
            diagnostics={"intent": asdict(intent)},
            timestamp=ts,
        )
