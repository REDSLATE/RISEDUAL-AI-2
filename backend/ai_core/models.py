"""Lightweight dataclasses used across ai_core.

Kept deliberately minimal — every field has an unambiguous meaning and
no sneaky defaults that could silently zero-out SL/TP. `from_dict`
constructors accept the loose dict shapes our existing routes emit so
we don't break the migration boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Literal


Direction = Literal["LONG", "SHORT"]
Status = Literal["win", "loss", "pending"]
ExecStatus = Literal["filled", "rejected"]


@dataclass
class Signal:
    """A trade intent before execution.

    `entry` is the planned fill price — when the simulator or live
    broker reports a different actual fill, that lives on
    `ExecutionResult.filled_price`. Don't overwrite this one.
    """
    asset: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float = 0.0  # 0-1 normalised
    strategy_id: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @staticmethod
    def _parse_direction(d: dict) -> Direction:
        """Resolve a loose ``direction`` / ``ai_verdict`` / ``side`` input
        into the canonical ``LONG`` / ``SHORT`` enum.

        Delegates unknown verdict tokens to
        ``services.prediction_tracker.canonical_ai_dir``. Pre-2026-05-01
        this logic was inline and silently routed WEAK_BUY / BULLISH /
        UP / WEAK_SELL / BEARISH to SHORT; same bug class as the
        prediction_tracker line 654 fix.
        """
        direction = (d.get("direction") or "").upper()
        if direction in ("LONG", "SHORT"):
            return direction  # type: ignore[return-value]

        from services.prediction_tracker import canonical_ai_dir
        v = d.get("ai_verdict") or d.get("side") or ""
        canonical = canonical_ai_dir(v)
        if canonical == "UNKNOWN":
            # No bullish/bearish signal in the input — fall back to SHORT
            # for backwards compatibility, but log loudly so the upstream
            # emitter can be fixed. Pre-fix this was silent.
            import logging
            logging.getLogger(__name__).warning(
                "[Signal.from_dict] unknown verdict token %r — "
                "defaulting to SHORT for compat. Add the token to "
                "DIRECTION_BULLISH/BEARISH if it should map.",
                v,
            )
            return "SHORT"
        return canonical  # type: ignore[return-value]

    @staticmethod
    def _parse_confidence(d: dict) -> float:
        """Accept both 0-1 floats and 0-100 percentages. Defaults to 0."""
        c = d.get("confidence")
        if c is None:
            c = d.get("ai_confidence", 0)
        if not c:
            return 0.0
        value = float(c)
        return value / 100.0 if value > 1.0 else value

    @classmethod
    def from_dict(cls, d: dict) -> "Signal":
        """Build a Signal from the loose dicts our scanner/dispatcher emits."""
        return cls(
            asset=(d.get("asset") or d.get("symbol") or "").upper(),
            direction=cls._parse_direction(d),
            entry=float(d.get("entry") or d.get("price") or 0),
            stop_loss=float(d.get("stop_loss") or d.get("sl") or 0),
            take_profit=float(d.get("take_profit") or d.get("tp") or 0),
            confidence=cls._parse_confidence(d),
            strategy_id=d.get("strategy_id"),
            timestamp=d.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class Trade:
    """An executed (or about-to-be-executed) trade. Pairs with a Signal."""
    asset: str
    direction: Direction
    entry: float
    size: float
    user_id: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_signal(cls, signal: Signal, size: float, user_id: Optional[str] = None) -> "Trade":
        return cls(
            asset=signal.asset,
            direction=signal.direction,
            entry=signal.entry,
            size=size,
            user_id=user_id,
        )


@dataclass
class ExecutionResult:
    """What an ExecutionClient returns after routing a Trade to a venue."""
    trade_id: str
    filled_price: float
    size: float
    status: ExecStatus
    reason: Optional[str] = None  # populated when status=rejected

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradeResult:
    """Outcome of a simulated or labeled trade.

    `win=None` + `status="pending"` is the third-label state that keeps
    unresolved trades out of the training loop. NEVER coerce pending to
    False downstream — that's the whole point.
    """
    pnl: float
    exit_price: float
    win: Optional[bool]
    status: Status
    r_multiple: float

    def as_dict(self) -> dict:
        return asdict(self)
