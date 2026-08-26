"""Alpha Day Trader — intent-producing intraday strategy.

Design principle
----------------
Alpha *discovers* the strongest 1-2 stocks each day, then **tracks the
setup continuously** rather than rescanning-and-rejecting on every tick.
When the actual trigger price is crossed, Alpha immediately:

1. Freezes the ``confirmation_price``
2. Combines the setup score with an optional Level 2 modifier (0.50
   neutral when depth is unavailable — L2 is NEVER a hard gate)
3. Calls the existing ``public_equity_live_executor.maybe_route_live``
   so the trade goes through Seat → Risk → RoadGuard → Entry Timing → broker

Hard safety remains with the existing pipeline. This module never
vetoes a trade for signal-quality reasons; it only produces intents.

Feature flags
-------------
* ``RISEDUAL_ALPHA_DAYTRADER_SCAN=1``    — run discovery + setup tracking
* ``RISEDUAL_ALPHA_DAYTRADER_EXECUTE=1`` — allow intent creation to fire

Both default OFF so a redeploy never silently activates a new trader.
Independent switches keep observation & execution decoupled — an
operator can run SCAN alone to observe scoring, then flip EXECUTE
when comfortable, without editing code.

Collections
-----------
* ``alpha_active_setups``       — live setups (state, trigger_price,
  invalidation, score, detected_at, triggered_at)
* ``alpha_setup_observations``  — per-tick lifecycle rows for post-mortem
* ``alpha_outcomes``            — one row per resolved setup, including
  no-trade paths (Alpha saw the move but did not fire → we still record it)
* ``alpha_daytrader_counters``  — daily rolling counts for the UI:
  candidates_seen, setups_created, setups_armed, triggers, intents_created,
  seat_pass, risk_pass, roadguard_pass, broker_submitted, filled
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Env switches ─────────────────────────────────────────────────


def _scan_enabled() -> bool:
    return (os.environ.get("RISEDUAL_ALPHA_DAYTRADER_SCAN") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _execute_enabled() -> bool:
    return (os.environ.get("RISEDUAL_ALPHA_DAYTRADER_EXECUTE") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _effective_flags(db: Any) -> tuple[bool, bool]:
    """Return (scan_enabled, execute_enabled) after applying operator overrides.

    Operator toggles in ``alpha_runtime_state`` take precedence over
    the env vars, so the Mission Control ON/OFF switches actually
    change runtime behaviour instead of just displaying env values.
    """
    try:
        from services.alpha_runtime_state import get_state
        state = await get_state(db)
        return bool(state["scan_enabled"]), bool(state["execute_enabled"])
    except Exception:  # noqa: BLE001
        return _scan_enabled(), _execute_enabled()


def _max_active_setups() -> int:
    raw = (os.environ.get("RISEDUAL_ALPHA_DAYTRADER_MAX_SETUPS") or "").strip()
    if not raw:
        return 2
    try:
        return max(1, min(10, int(float(raw))))
    except (TypeError, ValueError):
        return 2


def _min_opportunity_score() -> float:
    raw = (os.environ.get("RISEDUAL_ALPHA_DAYTRADER_MIN_SCORE") or "").strip()
    if not raw:
        return 0.60
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.60


# ─── Enums / dataclasses ──────────────────────────────────────────


class SetupType(str, Enum):
    # ── Momentum family (arm best in trending / risk-on regimes) ──
    BREAKOUT = "breakout"
    PULLBACK = "pullback"
    VWAP_RECLAIM = "vwap_reclaim"
    HOD_BREAK = "high_of_day_break"
    MOMENTUM_REACCELERATION = "momentum_reacceleration"
    # ── Mean-reversion family (arm best in chop regimes) ──
    # Added 2026-02: previously Alpha only understood momentum, so
    # ``session_chop``/``choppy_meanrevert`` regimes produced zero
    # setups (bot sat idle for days). These patterns give Alpha a
    # play for every regime — long-only (Public.com is cash-only).
    VWAP_FADE_LONG = "vwap_fade_long"
    RANGE_LOW_BOUNCE = "range_low_bounce"
    OPENING_DRIVE_FADE = "opening_drive_fade"


# Patterns that thrive in chop regimes (used by the score gain/penalty
# below). Kept as module-level constants so both the detector and the
# executor floor logic can share the same taxonomy.
MEAN_REVERT_PATTERNS: frozenset[str] = frozenset({
    SetupType.VWAP_FADE_LONG.value,
    SetupType.RANGE_LOW_BOUNCE.value,
    SetupType.OPENING_DRIVE_FADE.value,
    SetupType.PULLBACK.value,  # pullback straddles both families
})

MOMENTUM_PATTERNS: frozenset[str] = frozenset({
    SetupType.BREAKOUT.value,
    SetupType.VWAP_RECLAIM.value,
    SetupType.HOD_BREAK.value,
    SetupType.MOMENTUM_REACCELERATION.value,
})

# Regime labels that indicate a chop / mean-reversion market state.
# Kept lowercase-substring-matched so upstream label renames don't
# silently disable the gate.
CHOP_REGIME_TOKENS: tuple[str, ...] = (
    "chop",
    "meanrevert",
    "range",
)


def _is_chop_regime(*labels: Optional[str]) -> bool:
    """Return True if any of the provided regime labels reads as chop."""
    for lbl in labels:
        if not lbl:
            continue
        low = str(lbl).lower()
        for tok in CHOP_REGIME_TOKENS:
            if tok in low:
                return True
    return False


class SetupState(str, Enum):
    WATCHING = "watching"
    ARMED = "armed"
    TRIGGERED = "triggered"
    EXECUTED = "executed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    volume: float
    avg_volume: float
    relative_volume: float
    open_price: float
    high: float
    low: float
    vwap: float           # daily typical-price proxy (see _snapshot_symbol)
    bid: float
    ask: float
    spread_bps: float
    pct_change: float
    volume_acceleration: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Level2Snapshot:
    symbol: str
    bid_size: float
    ask_size: float
    book_imbalance: float
    tape_delta: float
    cancel_rate_bid: float
    cancel_rate_ask: float
    imbalance_persistence_ms: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ActiveSetup:
    setup_id: str
    symbol: str
    setup_type: SetupType
    state: SetupState
    detected_at: datetime
    reference_price: float
    trigger_price: float
    invalidation_price: float
    score: float
    dedup_key: str = ""
    confirmation_price: Optional[float] = None
    triggered_at: Optional[datetime] = None


@dataclass
class TradeIntent:
    intent_id: str
    setup_id: str
    symbol: str
    side: str
    confidence: float
    confirmation_price: float
    max_chase_pct: float
    stop_price: float
    target_price: float
    created_at: datetime
    reason: dict


# ─── Data sourcing ────────────────────────────────────────────────


async def _snapshot_symbol(symbol: str) -> Optional[MarketSnapshot]:
    """Build a ``MarketSnapshot`` from daily bars + a single quote.

    VWAP is approximated as today's daily-bar typical price
    ``(open+high+low+close)/4``. This is a well-known cheap proxy —
    accurate enough for VWAP-reclaim pattern detection without a
    separate intraday-bars subscription. When real intraday bars are
    wired we can switch this without any downstream signature change.
    """
    try:
        from services.market_data_pool import market_daily, market_quote
    except Exception:  # noqa: BLE001
        return None

    try:
        bars = await market_daily(symbol, outputsize="compact")
    except Exception:  # noqa: BLE001
        bars = None
    if not isinstance(bars, list) or len(bars) < 2:
        return None

    try:
        today = bars[-1]
        prev = bars[-2]
        open_ = float(today.get("open") or 0.0)
        high = float(today.get("high") or 0.0)
        low = float(today.get("low") or 0.0)
        close = float(today.get("close") or 0.0)
        volume = float(today.get("volume") or 0.0)
    except (TypeError, ValueError):
        return None
    if open_ <= 0 or close <= 0:
        return None

    # 20-bar avg volume from the compact window (excluding today).
    try:
        vols = [float(b.get("volume") or 0.0) for b in bars[-21:-1]]
        avg_volume = sum(vols) / len(vols) if vols else 0.0
    except (TypeError, ValueError):
        avg_volume = 0.0
    rel_vol = volume / avg_volume if avg_volume > 0 else 0.0

    # Very cheap volume-acceleration proxy: today's volume vs
    # yesterday's. When intraday bars are wired we can replace this
    # with (last-5-min-avg / today's rolling avg).
    try:
        prev_vol = float(prev.get("volume") or 0.0)
    except (TypeError, ValueError):
        prev_vol = 0.0
    vol_accel = volume / prev_vol if prev_vol > 0 else 1.0

    try:
        prev_close = float(prev.get("close") or 0.0)
    except (TypeError, ValueError):
        prev_close = 0.0
    pct_change = ((close - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0

    vwap = (open_ + high + low + close) / 4.0

    price = close
    bid = 0.0
    ask = 0.0
    spread_bps = 0.0
    try:
        q = await market_quote(symbol)
    except Exception:  # noqa: BLE001
        q = None
    if isinstance(q, dict):
        try:
            price = float(q.get("price") or q.get("last") or close)
            bid = float(q.get("bid") or 0.0)
            ask = float(q.get("ask") or 0.0)
        except (TypeError, ValueError):
            pass
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else price
        if bid > 0 and ask > 0 and mid > 0 and ask > bid:
            spread_bps = (ask - bid) / mid * 10_000.0

    return MarketSnapshot(
        symbol=symbol.upper(),
        price=price,
        volume=volume,
        avg_volume=avg_volume,
        relative_volume=rel_vol,
        open_price=open_,
        high=high,
        low=low,
        vwap=vwap,
        bid=bid,
        ask=ask,
        spread_bps=spread_bps,
        pct_change=pct_change,
        volume_acceleration=vol_accel,
    )


# ─── Ranker ────────────────────────────────────────────────────────


class AlphaOpportunityScanner:
    """Ranking score, NOT a hard gate. Strong movement + volume rise to the top."""

    def score(self, m: MarketSnapshot) -> float:
        s = 0.0
        s += min(m.relative_volume / 5.0, 1.0) * 0.30
        s += min(abs(m.pct_change) / 10.0, 1.0) * 0.25
        s += min(m.volume_acceleration / 3.0, 1.0) * 0.20
        if m.spread_bps <= 20:
            s += 0.15
        elif m.spread_bps <= 50:
            s += 0.10
        elif m.spread_bps <= 100:
            s += 0.04
        if m.vwap > 0 and m.price > m.vwap:
            s += 0.10
        return min(s, 1.0)

    def rank(
        self, universe: list[MarketSnapshot], *, top_n: int = 2,
    ) -> list[tuple[MarketSnapshot, float]]:
        scored = [(m, self.score(m)) for m in universe]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_n]


# ─── Pattern engine ───────────────────────────────────────────────


class AlphaPatternEngine:
    """Detects candidate setups. Never gates — produces or returns None.

    ``detect`` accepts an optional regime context so mean-reversion
    patterns can be armed during chop and momentum patterns can be
    softly de-emphasized (never suppressed) in the same regime.
    Missing regime → treated as neutral; every pattern is evaluated.
    """

    # Score bonuses/penalties applied AFTER a pattern matches so we
    # keep the raw setup shape checks unchanged and only bias the
    # confidence signal. Bounded so a pattern's score always stays
    # in [0, 1].
    CHOP_BOOST_MEAN_REVERT: float = 0.10
    CHOP_PENALTY_MOMENTUM: float = 0.05

    def _regime_bias(self, setup: "ActiveSetup", *,
                     slow_regime: Optional[str],
                     fast_regime: Optional[str]) -> float:
        chop = _is_chop_regime(slow_regime, fast_regime)
        if not chop:
            return 0.0
        if setup.setup_type.value in MEAN_REVERT_PATTERNS:
            return self.CHOP_BOOST_MEAN_REVERT
        if setup.setup_type.value in MOMENTUM_PATTERNS:
            return -self.CHOP_PENALTY_MOMENTUM
        return 0.0

    def detect(
        self,
        m: MarketSnapshot,
        *,
        slow_regime: Optional[str] = None,
        fast_regime: Optional[str] = None,
    ) -> Optional[ActiveSetup]:
        now = m.timestamp
        chop = _is_chop_regime(slow_regime, fast_regime)

        # ── Mean-reversion family — evaluated FIRST during chop so a
        # legitimate mean-revert signal isn't shadowed by a weak
        # momentum match on the same bar. Outside chop these patterns
        # still fire when the raw shape is present, they just don't
        # get the score boost.
        if m.vwap > 0 and m.price > 0:
            vwap_stretch = (m.vwap - m.price) / m.vwap  # positive when price BELOW vwap
            # ── VWAP fade long: price stretched ≥0.4% below VWAP,
            # volume tapering (rvol modest), red bar exhaustion.
            if (vwap_stretch >= 0.004 and vwap_stretch <= 0.03
                    and m.relative_volume >= 0.8 and m.relative_volume <= 2.5
                    and m.pct_change <= -0.3):
                base = 0.62
                setup = ActiveSetup(
                    setup_id=str(uuid.uuid4()),
                    symbol=m.symbol,
                    setup_type=SetupType.VWAP_FADE_LONG,
                    state=SetupState.WATCHING,
                    detected_at=now,
                    reference_price=m.price,
                    # Trigger on reclaim toward VWAP: small move up
                    trigger_price=m.price * 1.003,
                    # Invalidate on a fresh new low below current bar
                    invalidation_price=m.low * 0.997 if m.low > 0 else m.price * 0.99,
                    score=min(1.0, base + self._regime_bias_by_type(SetupType.VWAP_FADE_LONG.value, chop)),
                )
                return setup

        # ── Range low bounce: price at intraday low with low rvol
        # (sellers exhausted). Common in session_chop.
        if m.high > 0 and m.low > 0 and m.price > 0 and m.high > m.low:
            depth = (m.high - m.price) / (m.high - m.low)
            if (depth >= 0.75  # bottom quarter of the day's range
                    and m.relative_volume <= 1.5
                    and m.pct_change >= -3.0  # not in freefall
                    and m.pct_change <= -0.2):
                base = 0.60
                return ActiveSetup(
                    setup_id=str(uuid.uuid4()),
                    symbol=m.symbol,
                    setup_type=SetupType.RANGE_LOW_BOUNCE,
                    state=SetupState.WATCHING,
                    detected_at=now,
                    reference_price=m.price,
                    trigger_price=m.price * 1.004,
                    invalidation_price=m.low * 0.995,
                    score=min(1.0, base + self._regime_bias_by_type(SetupType.RANGE_LOW_BOUNCE.value, chop)),
                )

        # ── Opening-drive fade: strong AM move that stalled — price
        # reverting back toward open. Long-only side: gap-down that
        # is being bought back through the open.
        if (m.open_price > 0 and m.price > 0 and m.pct_change < 0
                and m.price >= m.open_price * 0.992
                and m.price <= m.open_price * 1.002
                and m.relative_volume >= 1.0):
            base = 0.63
            return ActiveSetup(
                setup_id=str(uuid.uuid4()),
                symbol=m.symbol,
                setup_type=SetupType.OPENING_DRIVE_FADE,
                state=SetupState.WATCHING,
                detected_at=now,
                reference_price=m.price,
                trigger_price=m.open_price * 1.002,
                invalidation_price=m.low * 0.997 if m.low > 0 else m.price * 0.99,
                score=min(1.0, base + self._regime_bias_by_type(SetupType.OPENING_DRIVE_FADE.value, chop)),
            )

        # ── VWAP reclaim ──
        if m.vwap > 0:
            vwap_distance = (m.price - m.vwap) / m.vwap
            if 0 <= vwap_distance <= 0.004 and m.relative_volume >= 1.5 and m.volume_acceleration >= 1.1:
                base = 0.65
                return ActiveSetup(
                    setup_id=str(uuid.uuid4()),
                    symbol=m.symbol,
                    setup_type=SetupType.VWAP_RECLAIM,
                    state=SetupState.WATCHING,
                    detected_at=now,
                    reference_price=m.price,
                    trigger_price=m.price * 1.002,
                    invalidation_price=m.vwap * 0.995,
                    score=max(0.0, min(1.0, base + self._regime_bias_by_type(SetupType.VWAP_RECLAIM.value, chop))),
                )
        # ── High-of-day breakout ──
        if m.price > 0:
            distance_to_high = (m.high - m.price) / m.price
            if 0 <= distance_to_high <= 0.005 and m.relative_volume >= 2.0 and m.volume_acceleration >= 1.25:
                base = 0.72
                return ActiveSetup(
                    setup_id=str(uuid.uuid4()),
                    symbol=m.symbol,
                    setup_type=SetupType.HOD_BREAK,
                    state=SetupState.ARMED,
                    detected_at=now,
                    reference_price=m.price,
                    trigger_price=m.high * 1.001,
                    invalidation_price=max(m.vwap, m.price * 0.98),
                    score=max(0.0, min(1.0, base + self._regime_bias_by_type(SetupType.HOD_BREAK.value, chop))),
                )
        # ── Breakout above prior-bar high with rising volume ──
        if m.high > 0 and m.price >= m.high * 0.998 and m.relative_volume >= 2.5:
            base = 0.70
            return ActiveSetup(
                setup_id=str(uuid.uuid4()),
                symbol=m.symbol,
                setup_type=SetupType.BREAKOUT,
                state=SetupState.ARMED,
                detected_at=now,
                reference_price=m.price,
                trigger_price=m.high * 1.002,
                invalidation_price=max(m.vwap * 0.99, m.price * 0.97),
                score=max(0.0, min(1.0, base + self._regime_bias_by_type(SetupType.BREAKOUT.value, chop))),
            )
        # ── Pullback: price above VWAP + intraday down move ≥ 1% + volume steady ──
        if m.vwap > 0 and m.price > m.vwap and -3.0 <= m.pct_change <= -0.5 and m.relative_volume >= 1.2:
            base = 0.62
            return ActiveSetup(
                setup_id=str(uuid.uuid4()),
                symbol=m.symbol,
                setup_type=SetupType.PULLBACK,
                state=SetupState.WATCHING,
                detected_at=now,
                reference_price=m.price,
                trigger_price=m.price * 1.005,
                invalidation_price=m.vwap * 0.99,
                score=max(0.0, min(1.0, base + self._regime_bias_by_type(SetupType.PULLBACK.value, chop))),
            )
        # ── Momentum re-acceleration: gap up + volume spike ──
        if m.pct_change >= 3.0 and m.volume_acceleration >= 1.5 and m.price >= m.open_price:
            base = 0.68
            return ActiveSetup(
                setup_id=str(uuid.uuid4()),
                symbol=m.symbol,
                setup_type=SetupType.MOMENTUM_REACCELERATION,
                state=SetupState.WATCHING,
                detected_at=now,
                reference_price=m.price,
                trigger_price=m.price * 1.003,
                invalidation_price=m.open_price * 0.99,
                score=max(0.0, min(1.0, base + self._regime_bias_by_type(SetupType.MOMENTUM_REACCELERATION.value, chop))),
            )
        return None

    def _regime_bias_by_type(self, setup_type_value: str, chop: bool) -> float:
        if not chop:
            return 0.0
        if setup_type_value in MEAN_REVERT_PATTERNS:
            return self.CHOP_BOOST_MEAN_REVERT
        if setup_type_value in MOMENTUM_PATTERNS:
            return -self.CHOP_PENALTY_MOMENTUM
        return 0.0


# ─── Trigger watcher ──────────────────────────────────────────────


class AlphaTriggerWatcher:
    """Watches an ACTIVE setup and marks it TRIGGERED when the price crosses."""

    def triggered(self, setup: ActiveSetup, m: MarketSnapshot) -> bool:
        if m.price <= setup.invalidation_price:
            setup.state = SetupState.INVALIDATED
            return False
        if m.price >= setup.trigger_price:
            setup.state = SetupState.TRIGGERED
            setup.triggered_at = m.timestamp
            setup.confirmation_price = m.price
            return True
        return False


# ─── Level 2 modifier (never a hard gate) ─────────────────────────


class Level2Confirmation:
    """Confidence *modifier*, not permission.

    When no L2 snapshot exists we do NOT skip — the caller uses a
    neutral 0.50 fallback so missing depth never blocks an intent.
    """

    def score(self, l2: Optional[Level2Snapshot]) -> float:
        if l2 is None:
            return 0.50
        s = 0.50
        if l2.book_imbalance > 0.15:
            s += 0.12
        elif l2.book_imbalance < -0.15:
            s -= 0.12
        if l2.tape_delta > 0.20:
            s += 0.15
        elif l2.tape_delta < -0.20:
            s -= 0.15
        if l2.imbalance_persistence_ms >= 750:
            s += 0.10
        if l2.cancel_rate_bid > 0.70:
            s -= 0.10
        return max(0.0, min(s, 1.0))


# ─── Intent factory ───────────────────────────────────────────────


def _build_intent_dict(intent: TradeIntent) -> dict:
    """Map the Alpha Day Trader intent into the executor's intent shape.

    The executor (``public_equity_live_executor.maybe_route_live``)
    expects a plain dict with ``symbol``, ``direction``, ``confidence``,
    ``strategy_id``, ``source_signal``, etc. Everything else on the
    dataclass is passed through under ``reason`` for post-mortem.
    """
    return {
        "symbol": intent.symbol,
        "direction": intent.side,
        "confidence": intent.confidence,
        "raw_confidence": intent.confidence,
        "calibrated_confidence": intent.confidence,
        "strategy_id": f"alpha_daytrader:{intent.reason.get('setup', 'unknown')}",
        "source_signal": "alpha_daytrader:v1",
        "prediction_id": intent.intent_id,
        "scan_id": None,
        # Propagate regime + setup_type up to the executor so the
        # confidence floor gate can be regime-aware (chop regimes use
        # a lower floor because mean-reversion setups naturally
        # score lower than momentum setups).
        "regime": intent.reason.get("regime"),
        "fast_regime": intent.reason.get("fast_regime"),
        "setup_type": intent.reason.get("setup"),
        "predicted_move_pct": None,
        "sovereign_decision_id": None,
        # Extended payload for auditing.
        "alpha_daytrader": {
            "setup_id": intent.setup_id,
            "confirmation_price": intent.confirmation_price,
            "max_chase_pct": intent.max_chase_pct,
            "stop_price": intent.stop_price,
            "target_price": intent.target_price,
            "reason": intent.reason,
            "created_at": intent.created_at.isoformat(),
        },
    }


def create_alpha_intent(
    setup: ActiveSetup,
    m: MarketSnapshot,
    l2: Optional[Level2Snapshot],
    l2_engine: Level2Confirmation,
) -> TradeIntent:
    l2_score = l2_engine.score(l2)  # 0.50 when l2 is None
    combined = setup.score * 0.75 + l2_score * 0.25
    confirmation_price = setup.confirmation_price or m.price
    risk_per_share = max(0.0, confirmation_price - setup.invalidation_price)
    target = confirmation_price + 2.0 * risk_per_share
    return TradeIntent(
        intent_id=str(uuid.uuid4()),
        setup_id=setup.setup_id,
        symbol=setup.symbol,
        side="BUY",
        confidence=combined,
        confirmation_price=confirmation_price,
        max_chase_pct=0.02,
        stop_price=setup.invalidation_price,
        target_price=target,
        created_at=datetime.now(timezone.utc),
        reason={
            "source": "alpha_daytrader",
            "setup": setup.setup_type.value,
            "setup_score": setup.score,
            "level2_score": l2_score,
            "level2_available": l2 is not None,
            "relative_volume": m.relative_volume,
            "volume_acceleration": m.volume_acceleration,
            "spread_bps": m.spread_bps,
        },
    )


# ─── Dedup + persistence ──────────────────────────────────────────


def _price_band(price: float) -> int:
    """Round to a coarse band so one underlying market move maps to a
    single setup even if the price fluctuates during observation.
    ~5% bands catch typical drift within one setup without merging
    genuinely different opportunities."""
    if price <= 0:
        return 0
    return int(price * 20)  # 5% bucket


def _setup_dedup_key(symbol: str, setup_type: SetupType, price: float) -> str:
    """One setup per (symbol × setup_type × price-band × trading-day).

    This is the fix for PUMP being examined 227× yielding 227 losses:
    the same underlying opportunity now maps to a single setup_id.
    """
    today = date.today().isoformat()
    return f"{symbol.upper()}:{setup_type.value}:{_price_band(price)}:{today}"


async def _get_or_create_setup(db: Any, setup: ActiveSetup) -> tuple[ActiveSetup, bool]:
    """Upsert on dedup_key. Returns (setup, is_new)."""
    if db is None:
        return setup, True
    key = _setup_dedup_key(setup.symbol, setup.setup_type, setup.reference_price)
    setup.dedup_key = key
    existing = await db.alpha_active_setups.find_one({"dedup_key": key, "state": {
        "$in": [SetupState.WATCHING.value, SetupState.ARMED.value, SetupState.TRIGGERED.value],
    }})
    if existing:
        return _setup_from_doc(existing), False
    doc = _setup_to_doc(setup)
    await db.alpha_active_setups.insert_one(dict(doc))
    return setup, True


def _setup_to_doc(setup: ActiveSetup) -> dict:
    d = asdict(setup)
    d["setup_type"] = setup.setup_type.value
    d["state"] = setup.state.value
    return d


def _setup_from_doc(doc: dict) -> ActiveSetup:
    return ActiveSetup(
        setup_id=doc["setup_id"],
        symbol=doc["symbol"],
        setup_type=SetupType(doc["setup_type"]),
        state=SetupState(doc["state"]),
        detected_at=doc["detected_at"] if isinstance(doc.get("detected_at"), datetime) else datetime.now(timezone.utc),
        reference_price=float(doc.get("reference_price") or 0.0),
        trigger_price=float(doc.get("trigger_price") or 0.0),
        invalidation_price=float(doc.get("invalidation_price") or 0.0),
        score=float(doc.get("score") or 0.0),
        dedup_key=doc.get("dedup_key", ""),
        confirmation_price=doc.get("confirmation_price"),
        triggered_at=doc.get("triggered_at"),
    )


async def _record_observation(db: Any, setup_id: str, event: str, payload: dict) -> None:
    """Route lifecycle events to the SQLite hot store, NOT to Mongo.

    Mongo receives only compact rollups + one row per resolved setup
    via ``_record_outcome`` — the raw high-frequency lifecycle stream
    lives locally so ``alpha_setup_observations`` never grows.
    """
    try:
        from services import alpha_hot_store
        alpha_hot_store.record_event(setup_id, event, payload=payload,
                                      symbol=str(payload.get("symbol") or ""),
                                      stage=str(payload.get("stage") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] hot-store write failed: %s", exc)


async def _bump_counter(db: Any, field_name: str, delta: int = 1) -> None:
    """Daily rolling counters — one doc per (session_date)."""
    if db is None:
        return
    session_date = date.today().isoformat()
    try:
        await db.alpha_daytrader_counters.update_one(
            {"session_date": session_date},
            {"$inc": {field_name: delta}, "$set": {"updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] counter bump failed: %s", exc)


async def _update_setup_state(db: Any, setup: ActiveSetup) -> None:
    if db is None:
        return
    try:
        await db.alpha_active_setups.update_one(
            {"setup_id": setup.setup_id},
            {"$set": _setup_to_doc(setup)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] setup update failed: %s", exc)


async def _record_outcome(db: Any, *, setup: ActiveSetup, intent_id: Optional[str],
                          submitted: bool, filled: bool,
                          reject_reason: Optional[str],
                          regime: Optional[str] = None,
                          fast_regime: Optional[str] = None,
                          time_bucket: Optional[str] = None,
                          rvol_bucket: Optional[str] = None,
                          spread_bucket: Optional[str] = None,
                          edge_modifier: Optional[float] = None,
                          edge_state: Optional[str] = None) -> None:
    if db is None:
        return
    try:
        await db.alpha_outcomes.insert_one({
            "setup_id": setup.setup_id,
            "intent_id": intent_id,
            "symbol": setup.symbol,
            "setup_type": setup.setup_type.value,
            "detected_price": setup.reference_price,
            "confirmation_price": setup.confirmation_price,
            "trigger_price": setup.trigger_price,
            "invalidation_price": setup.invalidation_price,
            "triggered": setup.state == SetupState.TRIGGERED or filled,
            "intent_created": intent_id is not None,
            "order_submitted": submitted,
            "filled": filled,
            "reject_reason": reject_reason,
            "regime": regime,
            "fast_regime": fast_regime,
            "time_bucket": time_bucket,
            "rvol_bucket": rvol_bucket,
            "spread_bucket": spread_bucket,
            "edge_modifier": edge_modifier,
            "edge_state": edge_state,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] outcome write failed: %s", exc)


# ─── Universe source ──────────────────────────────────────────────


async def _candidate_universe(db: Any, *, lookback_minutes: int = 60) -> list[str]:
    """Pull the symbols Alpha has produced recent predictions on.

    Reuses the same source as ``day_trade_scanner`` so we don't run
    two parallel screeners with different tastes. ``AlphaOpportunityScanner``
    then does its own ranking on freshly-fetched market data.
    """
    if db is None:
        return []
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).isoformat()
    try:
        cursor = db.predictions.aggregate([
            {"$match": {"feature": {"$in": ["signal_dispatcher", "paper_trading"]},
                        "timestamp": {"$gte": since}}},
            {"$group": {"_id": "$symbol"}},
            {"$limit": 50},
        ])
        return [row["_id"].upper() for row in await cursor.to_list(length=50) if row.get("_id")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] universe read failed: %s", exc)
        return []


# ─── Main tick ────────────────────────────────────────────────────


def _l2_source() -> str:
    """Which market-data source Alpha uses for Level 2 depth.

    Default is ``none`` (missing depth → neutral 0.50, unchanged).
    Set ``RISEDUAL_ALPHA_L2_SOURCE=moomoo`` to pull depth from the
    MooMoo market-data adapter when OpenD is connected. The fallback
    to neutral 0.50 is preserved whenever the source is unavailable —
    L2 must never gate a trade.
    """
    return (os.environ.get("RISEDUAL_ALPHA_L2_SOURCE") or "none").strip().lower()


def _fetch_l2_snapshot(symbol: str) -> Optional[Level2Snapshot]:
    """Pull an L2 snapshot from the configured source, or return None.

    Returning None triggers the ``Level2Confirmation`` 0.50 neutral
    fallback per V1 policy — depth is a *modifier*, never a gate.
    """
    src = _l2_source()
    if src == "moomoo":
        try:
            from services.moomoo_market_data_adapter import to_level2_snapshot
            data = to_level2_snapshot(symbol)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict):
            return None
        try:
            return Level2Snapshot(
                symbol=str(data.get("symbol") or symbol),
                bid_size=float(data.get("bid_size") or 0.0),
                ask_size=float(data.get("ask_size") or 0.0),
                book_imbalance=float(data.get("book_imbalance") or 0.0),
                tape_delta=float(data.get("tape_delta") or 0.0),
                cancel_rate_bid=float(data.get("cancel_rate_bid") or 0.0),
                cancel_rate_ask=float(data.get("cancel_rate_ask") or 0.0),
                imbalance_persistence_ms=int(data.get("imbalance_persistence_ms") or 0),
            )
        except (TypeError, ValueError):
            return None
    return None


async def run_alpha_day_trader_tick(db: Any) -> dict:
    """One scheduler tick. Returns a summary dict for logging.

    Full lifecycle capture: every stage transition (candidate → setup →
    armed → triggered → intent → seat → risk → roadguard → entry_timing
    → broker → fill) is written to the SQLite hot store with latency
    samples. Only compact resolved-outcome docs go to Mongo.
    """
    scan_on, exec_on = await _effective_flags(db)
    if not scan_on:
        return {"skipped": True, "reason": "scan_disabled"}
    if db is None:
        return {"skipped": True, "reason": "no_db"}

    try:
        from services import alpha_hot_store
        alpha_hot_store.init()
    except Exception:  # noqa: BLE001
        pass

    scanner = AlphaOpportunityScanner()
    patterns = AlphaPatternEngine()
    watcher = AlphaTriggerWatcher()
    l2_engine = Level2Confirmation()

    universe_symbols = await _candidate_universe(db)
    snapshots: list[MarketSnapshot] = []
    for sym in universe_symbols[:20]:  # cap fetches per tick
        snap = await _snapshot_symbol(sym)
        if snap is not None:
            snapshots.append(snap)
    await _bump_counter(db, "candidates_seen", len(snapshots))
    ranked = scanner.rank(snapshots, top_n=_max_active_setups())
    min_score = _min_opportunity_score()

    # ── Regime context (fetched ONCE per tick, best-effort) ──
    # Fetched BEFORE pattern detection so mean-reversion setups can
    # arm during chop and momentum setups can be softly de-emphasized
    # in the same regime. Regime labels are non-blocking — missing
    # data becomes UNKNOWN and every pattern is evaluated as usual.
    tick_slow_regime: Optional[str] = None
    tick_fast_regime: Optional[str] = None
    try:
        from services.market_regime import get_current as _get_regime
        regime_doc = await _get_regime(db)
        tick_slow_regime = str(regime_doc.get("label") or "UNKNOWN")
    except Exception:  # noqa: BLE001
        tick_slow_regime = "UNKNOWN"
    try:
        from services.fast_intraday_regime import get_current as _get_fast
        fast_doc = await _get_fast(db)
        tick_fast_regime = str(fast_doc.get("label") or "UNKNOWN")
    except Exception:  # noqa: BLE001
        tick_fast_regime = "UNKNOWN"

    # ── discovery + pattern detection ──
    new_setups = 0
    for snap, opp_score in ranked:
        if opp_score < min_score:
            continue
        candidate = patterns.detect(
            snap,
            slow_regime=tick_slow_regime,
            fast_regime=tick_fast_regime,
        )
        if candidate is None:
            continue
        setup, is_new = await _get_or_create_setup(db, candidate)
        if is_new:
            new_setups += 1
            await _bump_counter(db, "setups_created", 1)
            if setup.state == SetupState.ARMED:
                await _bump_counter(db, "setups_armed", 1)
            await _record_observation(db, setup.setup_id, "setup_detected", {
                "symbol": setup.symbol,
                "stage": "detected",
                "state": setup.state.value,
                "opportunity_score": opp_score,
                "setup_score": setup.score,
                "trigger_price": setup.trigger_price,
                "invalidation_price": setup.invalidation_price,
                "detected_ns": time.time_ns(),
            })

    # ── trigger loop on all ACTIVE setups ──
    triggered_intents = 0
    submitted = 0
    active_cursor = db.alpha_active_setups.find({
        "state": {"$in": [SetupState.WATCHING.value, SetupState.ARMED.value]},
    })
    async for doc in active_cursor:
        setup = _setup_from_doc(doc)
        snap = await _snapshot_symbol(setup.symbol)
        if snap is None:
            continue
        if not watcher.triggered(setup, snap):
            if setup.state == SetupState.INVALIDATED:
                await _update_setup_state(db, setup)
                await _record_observation(db, setup.setup_id, "invalidated", {
                    "symbol": setup.symbol,
                    "stage": "invalidated",
                    "price": snap.price,
                    "invalidation": setup.invalidation_price,
                })
                await _record_outcome(db, setup=setup, intent_id=None, submitted=False,
                                       filled=False, reject_reason="invalidated_before_trigger")
            continue

        # ── TRIGGERED ──
        trigger_ns = time.time_ns()
        detected_ns = int(setup.detected_at.timestamp() * 1e9) if setup.detected_at else trigger_ns
        alpha_hot_store.record_latency(setup.setup_id, "signal_to_trigger",
                                        max(0, (trigger_ns - detected_ns) // 1_000_000))
        await _update_setup_state(db, setup)
        await _bump_counter(db, "triggers", 1)
        await _record_observation(db, setup.setup_id, "triggered", {
            "symbol": setup.symbol, "stage": "triggered",
            "confirmation_price": setup.confirmation_price, "price": snap.price,
        })

        # ── regime + edge lookup (both are NON-BLOCKING modifiers) ──
        # Uses the tick-cached regime labels captured before pattern
        # detection so a single tick is evaluated with a consistent
        # regime context end-to-end.
        regime_label = tick_slow_regime or "UNKNOWN"
        fast_regime_label = tick_fast_regime or "UNKNOWN"
        try:
            from services import alpha_edge_engine as _edge
            edge_info = await _edge.lookup(db, pattern=setup.setup_type.value,
                                            regime=regime_label,
                                            fast_regime=fast_regime_label)
            t_bucket = _edge.time_bucket(snap.timestamp)
            r_bucket = _edge.rvol_bucket(snap.relative_volume)
            s_bucket = _edge.spread_bucket(snap.spread_bps)
        except Exception:  # noqa: BLE001
            edge_info = {"state": "DISCOVERING", "modifier": 1.00, "samples": 0}
            t_bucket = r_bucket = s_bucket = "unknown"
        modifier = float(edge_info.get("modifier") or 1.0)

        intent = create_alpha_intent(setup, snap, _fetch_l2_snapshot(setup.symbol), l2_engine)
        intent.reason["l2_source"] = _l2_source()
        # Apply edge modifier (cap at 1.0 per spec). NEVER a hard gate.
        raw_conf = intent.confidence
        intent.confidence = min(1.0, raw_conf * modifier)
        intent.reason.update({
            "regime": regime_label,
            "fast_regime": fast_regime_label,
            "edge_state": edge_info.get("state"),
            "edge_modifier": modifier,
            "edge_samples": edge_info.get("samples"),
            "time_bucket": t_bucket,
            "rvol_bucket": r_bucket,
            "spread_bucket": s_bucket,
        })
        intent_ns = time.time_ns()
        alpha_hot_store.record_latency(setup.setup_id, "trigger_to_intent",
                                        max(0, (intent_ns - trigger_ns) // 1_000_000))
        triggered_intents += 1
        await _bump_counter(db, "intents_created", 1)
        await _record_observation(db, setup.setup_id, "intent_created", {
            "symbol": setup.symbol, "stage": "intent",
            "intent_id": intent.intent_id,
            "confidence_raw": raw_conf, "confidence": intent.confidence,
            "confirmation_price": intent.confirmation_price,
            "stop_price": intent.stop_price, "target_price": intent.target_price,
            "regime": regime_label,
            "fast_regime": fast_regime_label,
            "edge_state": edge_info.get("state"),
            "edge_modifier": modifier,
        })

        _outcome_kwargs = {
            "regime": regime_label,
            "fast_regime": fast_regime_label,
            "time_bucket": t_bucket,
            "rvol_bucket": r_bucket,
            "spread_bucket": s_bucket,
            "edge_modifier": modifier,
            "edge_state": edge_info.get("state"),
        }

        if not exec_on:
            await _record_outcome(db, setup=setup, intent_id=intent.intent_id,
                                   submitted=False, filled=False,
                                   reject_reason="execute_switch_off",
                                   **_outcome_kwargs)
            continue

        # ── cross-scanner execution-boundary lock ──
        # Prevents the old day_trade_scanner + Alpha from double-buying
        # the same underlying move. Fails open on lock-service errors.
        if not alpha_hot_store.try_acquire_symbol_lock(
            setup.symbol, setup_id=setup.setup_id, source="alpha_daytrader",
            ttl_seconds=120,
        ):
            await _record_observation(db, setup.setup_id, "exec_lock_conflict", {
                "symbol": setup.symbol, "stage": "dedup",
                "note": "another scanner holds the symbol lock",
            })
            await _record_outcome(db, setup=setup, intent_id=intent.intent_id,
                                   submitted=False, filled=False,
                                   reject_reason="exec_lock_conflict",
                                   **_outcome_kwargs)
            continue

        # Hand off to the existing execution pipeline. maybe_route_live
        # runs Seat → Risk → RoadGuard → Entry Timing → broker. We do NOT
        # bypass any of that. A dict return means the broker submission
        # attempt happened; None means one of those gates rejected.
        try:
            from services.public_equity_live_executor import maybe_route_live
            row = await maybe_route_live(db, intent=_build_intent_dict(intent))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[alpha_daytrader] executor call failed for %s: %s",
                           setup.symbol, exc)
            row = None
        broker_ns = time.time_ns()
        alpha_hot_store.record_latency(setup.setup_id, "intent_to_broker",
                                        max(0, (broker_ns - intent_ns) // 1_000_000))

        if row:
            submitted += 1
            await _bump_counter(db, "broker_submitted", 1)
            status = row.get("status")
            if status in ("open", "filled", "submitted"):
                await _bump_counter(db, "filled", 1)
            setup.state = SetupState.EXECUTED
            await _update_setup_state(db, setup)
            await _record_observation(db, setup.setup_id, "broker_submitted", {
                "symbol": setup.symbol, "stage": "broker",
                "trade_id": row.get("trade_id"),
                "broker_order_id": row.get("broker_order_id"),
                "status": status,
            })
            await _record_outcome(db, setup=setup, intent_id=intent.intent_id,
                                   submitted=True, filled=bool(status == "filled"),
                                   reject_reason=None,
                                   **_outcome_kwargs)
        else:
            # Ask the intent-skip log why the executor rejected. We do
            # NOT invent a reason locally — the executor is the source
            # of truth for its own gate decisions.
            reject_reason = "executor_rejected"
            try:
                skip = await db.intent_skip_log.find_one(
                    {"prediction_id": intent.intent_id},
                    sort=[("ts", -1)],
                )
                if skip:
                    reject_reason = skip.get("reason") or reject_reason
            except Exception:  # noqa: BLE001
                pass
            await _record_observation(db, setup.setup_id, "executor_rejected", {
                "symbol": setup.symbol, "stage": reject_reason,
                "reject_reason": reject_reason,
            })
            await _record_outcome(db, setup=setup, intent_id=intent.intent_id,
                                   submitted=False, filled=False,
                                   reject_reason=reject_reason,
                                   **_outcome_kwargs)
            # Release the lock on rejection so the next legitimate
            # signal can try again promptly.
            alpha_hot_store.release_symbol_lock(setup.symbol, setup_id=setup.setup_id)

    summary = {
        "candidates_seen": len(snapshots),
        "ranked": len(ranked),
        "new_setups": new_setups,
        "triggers": triggered_intents,
        "broker_submitted": submitted,
        "scan_enabled": True,
        "execute_enabled": exec_on,
    }
    logger.info("[alpha_daytrader] tick %s", summary)
    return summary


async def get_counters(db: Any, *, session_date: Optional[str] = None) -> dict:
    """Return today's rolling counters for the admin UI."""
    if db is None:
        return {}
    session_date = session_date or date.today().isoformat()
    doc = await db.alpha_daytrader_counters.find_one({"session_date": session_date}) or {}
    doc.pop("_id", None)
    if isinstance(doc.get("updated_at"), datetime):
        doc["updated_at"] = doc["updated_at"].isoformat()
    doc["scan_enabled"] = _scan_enabled()
    doc["execute_enabled"] = _execute_enabled()
    try:
        from services.alpha_runtime_state import get_state
        rt = await get_state(db)
        doc["scan_enabled"] = rt["scan_enabled"]
        doc["execute_enabled"] = rt["execute_enabled"]
        doc["runtime"] = rt
    except Exception:  # noqa: BLE001
        pass
    doc["max_active_setups"] = _max_active_setups()
    doc["min_opportunity_score"] = _min_opportunity_score()
    # Conversion ratios — the operator's primary health signal.
    def _ratio(numer_key: str, denom_key: str) -> Optional[float]:
        n = doc.get(numer_key) or 0
        d = doc.get(denom_key) or 0
        return round(n / d, 3) if d else None
    doc["conversion"] = {
        "setup_to_trigger": _ratio("triggers", "setups_created"),
        "trigger_to_intent": _ratio("intents_created", "triggers"),
        "intent_to_broker": _ratio("broker_submitted", "intents_created"),
        "broker_to_fill": _ratio("filled", "broker_submitted"),
    }
    return doc


async def get_active_setups(db: Any, *, limit: int = 50) -> list[dict]:
    """List active/recent setups for the admin UI."""
    if db is None:
        return []
    out: list[dict] = []
    cursor = db.alpha_active_setups.find({}, {"_id": 0}).sort("detected_at", -1).limit(int(limit))
    async for row in cursor:
        for k in ("detected_at", "triggered_at"):
            if isinstance(row.get(k), datetime):
                row[k] = row[k].isoformat()
        out.append(row)
    return out


__all__ = [
    "SetupType",
    "SetupState",
    "MarketSnapshot",
    "Level2Snapshot",
    "ActiveSetup",
    "TradeIntent",
    "AlphaOpportunityScanner",
    "AlphaPatternEngine",
    "AlphaTriggerWatcher",
    "Level2Confirmation",
    "create_alpha_intent",
    "run_alpha_day_trader_tick",
    "get_counters",
    "get_active_setups",
]
