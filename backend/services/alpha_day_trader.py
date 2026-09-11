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
    """Max candidates fed to pattern detection per tick.

    2026-02: Bumped default from 2 → 10. At the old default of 2,
    the opportunity ranker culled 48 of every 50 candidates *before*
    they ever reached pattern detection — a massive throughput
    bottleneck upstream of every other loosening we did. Ten is a
    tuned middle-ground: broad enough that mean-reversion + classical
    patterns actually get evaluated across the market, tight enough
    that broker rate-limit budget stays healthy.
    Bounded 1..25 to prevent runaway. Env: RISEDUAL_ALPHA_DAYTRADER_MAX_SETUPS.
    """
    raw = (os.environ.get("RISEDUAL_ALPHA_DAYTRADER_MAX_SETUPS") or "").strip()
    if not raw:
        return 10
    try:
        return max(1, min(25, int(float(raw))))
    except (TypeError, ValueError):
        return 10


def _min_opportunity_score() -> float:
    """Minimum opportunity-ranker score required before a candidate
    reaches pattern detection.

    2026-02: Lowered default from 0.60 → 0.50 as part of the pattern
    sensitivity loosening. At 0.60, the ranker was culling 48 of every
    50 candidates so pattern detection only saw the top 2 — massive
    starvation. Under `ALPHA_PATTERN_SENSITIVITY=1.25` the floor also
    scales down proportionally so operators can tune both in lockstep.
    """
    raw = (os.environ.get("RISEDUAL_ALPHA_DAYTRADER_MIN_SCORE") or "").strip()
    if not raw:
        base = 0.50
    else:
        try:
            base = max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            base = 0.50
    # Scale by sensitivity — higher sens = lower floor. Kept as a
    # mild scalar (1/sqrt) so we don't accidentally open the flood
    # gate. Bounded 0.30..0.75 as sanity rails.
    from math import sqrt
    sens_raw = (os.environ.get("ALPHA_PATTERN_SENSITIVITY") or "").strip()
    try:
        sens = float(sens_raw) if sens_raw else 1.25
    except (TypeError, ValueError):
        sens = 1.25
    sens = max(0.5, min(2.5, sens))
    scaled = base / sqrt(sens)
    return max(0.30, min(0.75, scaled))


# ─── selective opportunity floor ─────────────────────────────────
#
# The Why-Not-Trade diagnostic (2026-02) showed 8 of every 10 ranked
# candidates were being culled by the global floor even though their
# features (up 4-8%, rvol 2.5x) are exactly the moves Alpha should
# be trading. A single flat floor treats an NVDA momentum candle
# and an unloved microcap sitting at flat volume identically.
#
# ``_family_floor`` classifies each snapshot into one of four
# operator-defined families and returns the appropriate floor. The
# families and floors are 1:1 with the values the operator specified
# in-thread — kept as constants so tuning is one edit, not a hunt.

_FLOOR_LARGE_CAP_MOMO: float = 0.35
_FLOOR_PENNY_BREAKOUT: float = 0.38
_FLOOR_SHORT_BREAKDOWN: float = 0.36
_FLOOR_LOW_VOL_NO_NEWS: float = 0.447   # default (matches sqrt(0.2))


def _family_floor(snap: "MarketSnapshot", *, default_floor: float) -> tuple[float, str]:
    """Classify the candidate and return ``(floor, family_tag)``.

    ``default_floor`` is the global ``_min_opportunity_score()`` and
    is used verbatim for anything that doesn't fit a specific
    family — no candidate ever gets a LOOSER floor than a family
    it doesn't qualify for. That preserves the original safety net:
    a candidate with no interesting features never gets waved
    through by a family floor it doesn't match.
    """
    price = getattr(snap, "price", 0.0) or 0.0
    pct = getattr(snap, "pct_change", 0.0) or 0.0
    rvol = getattr(snap, "relative_volume", 0.0) or 0.0

    # Order matters — a penny-stock breakout is also a "positive move
    # with high rvol" so we detect it FIRST and let the more general
    # large-cap-momo case pick up everything else.
    if 0.0 < price <= 5.0 and pct >= 3.0 and rvol >= 2.0:
        return _FLOOR_PENNY_BREAKOUT, "penny_breakout"

    # Short-side breakdown: sharp down move with real participation.
    # Applies to any price band — a large-cap breakdown is just as
    # tradeable as a microcap one via the SHORT_SIDE_EXHAUSTION long.
    if pct <= -2.0 and rvol >= 0.5:
        return _FLOOR_SHORT_BREAKDOWN, "short_breakdown"

    # Large-cap momentum: real name, real move, real participation.
    # 2026-02 update — loosened after live production data (see
    # PRD) showed ADBE ($286, opp=0.4267), AXP (opp=0.4114), and
    # BLK all being misclassified as ``low_vol_no_news`` because
    # they missed one of the three criteria by a hair. The old
    # thresholds (0.5% / 1.5×) required the whole triangle to
    # light up simultaneously; the new ones (0.3% / 1.2×) match
    # what a real mid-morning large-cap grind actually looks
    # like. Price floor stays at $20 so a $3 microcap still lands
    # in penny_breakout first.
    if price >= 20.0 and pct >= 0.3 and rvol >= 1.2:
        return _FLOOR_LARGE_CAP_MOMO, "large_cap_momo"

    # Default — the historical global floor. Applies to low-volume /
    # no-news setups that we don't want firing on weak signals.
    return max(_FLOOR_LOW_VOL_NO_NEWS, default_floor), "low_vol_no_news"


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
    # Short-side exhaustion long: PLTR-shaped -7.5% moves where the
    # current bar is stabilizing (price holding near open, rvol not
    # in freefall). Added after the Why-Not-Trade diagnostic showed
    # these moves were dying at ``no_pattern_match``. Long-only
    # entry (buy the bounce) — we do NOT open shorts on Public.com.
    SHORT_SIDE_EXHAUSTION = "short_side_exhaustion"
    # ── Classical multi-bar chart patterns (bullish only —
    # Public.com is cash-only). Bearish classical patterns are used
    # as *invalidation gates* on the same symbol via
    # ``services/alpha_classical_patterns.has_bearish_veto``, not as
    # tradeable setups. Ported from IGNISpilot 2026-08.
    DOUBLE_BOTTOM = "double_bottom"
    INVERSE_HEAD_SHOULDERS = "inverse_head_and_shoulders"
    FALLING_WEDGE = "falling_wedge"


# Patterns that thrive in chop regimes (used by the score gain/penalty
# below). Kept as module-level constants so both the detector and the
# executor floor logic can share the same taxonomy.
MEAN_REVERT_PATTERNS: frozenset[str] = frozenset({
    SetupType.VWAP_FADE_LONG.value,
    SetupType.RANGE_LOW_BOUNCE.value,
    SetupType.OPENING_DRIVE_FADE.value,
    SetupType.SHORT_SIDE_EXHAUSTION.value,
    SetupType.PULLBACK.value,  # pullback straddles both families
})

MOMENTUM_PATTERNS: frozenset[str] = frozenset({
    SetupType.BREAKOUT.value,
    SetupType.VWAP_RECLAIM.value,
    SetupType.HOD_BREAK.value,
    SetupType.MOMENTUM_REACCELERATION.value,
})

# Multi-bar classical patterns. Not regime-boosted — they carry
# their own confidence numbers from the IGNISpilot rules.
CLASSICAL_PATTERNS: frozenset[str] = frozenset({
    SetupType.DOUBLE_BOTTOM.value,
    SetupType.INVERSE_HEAD_SHOULDERS.value,
    SetupType.FALLING_WEDGE.value,
})

# Map classical-pattern names to SetupType so the detector can
# translate an ``alpha_classical_patterns.PatternAssessment`` into
# an ``ActiveSetup`` without a big switch.
_CLASSICAL_NAME_TO_SETUP: dict[str, "SetupType"] = {
    "double_bottom": SetupType.DOUBLE_BOTTOM,
    "inverse_head_and_shoulders": SetupType.INVERSE_HEAD_SHOULDERS,
    "falling_wedge": SetupType.FALLING_WEDGE,
}

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
    # 2026-02: last N daily bars (OHLCV dicts) — used by the
    # classical multi-bar chart-pattern detectors ported from
    # IGNISpilot (double-bottom, H&S, falling wedge, etc.). Empty
    # list is a valid state (older snapshots) — detectors just
    # return no-match rather than crashing.
    recent_bars: list[dict] = field(default_factory=list)


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
    rel_vol_daily = volume / avg_volume if avg_volume > 0 else 0.0

    # 2026-09-11 — Time-of-day RVOL upgrade (Foundation v2.1 fix).
    # The daily-avg ratio above is a legacy fallback; it produces
    # false-positive setups for midday RVOL because it compares
    # today's cumulative to yesterday's total. The time-of-day
    # baseline compares THIS bar to prior dates at the SAME UTC
    # slot. Warm-up honesty: when we don't have ``min_samples``
    # historical rows yet, the baseline returns ``None`` and we
    # keep the legacy fallback so downstream gates don't regress.
    # As the baseline table fills, RVOL sharpens automatically.
    rel_vol = rel_vol_daily
    try:
        from services import alpha_volume_baseline
        now_utc = datetime.now(timezone.utc)
        alpha_volume_baseline.observe(symbol, now_utc, volume)
        tod_rvol, tod_samples = alpha_volume_baseline.rvol(symbol, now_utc, volume)
        if tod_rvol is not None:
            rel_vol = float(tod_rvol)
        else:
            logger.debug(
                "[alpha_daytrader] rvol warmup %s samples=%d fallback=%.2f",
                symbol, tod_samples, rel_vol_daily,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] rvol baseline failed for %s: %s", symbol, exc)

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

    # Slice the last ~10 daily bars for the classical chart-pattern
    # detectors (double-bottom needs 5, inverse H&S needs 7). Keep
    # a bit of headroom; detectors slice the tail themselves.
    recent_bars: list[dict] = []
    try:
        for b in bars[-10:]:
            recent_bars.append({
                "open": float(b.get("open") or 0.0),
                "high": float(b.get("high") or 0.0),
                "low": float(b.get("low") or 0.0),
                "close": float(b.get("close") or 0.0),
                "volume": float(b.get("volume") or 0.0),
                "date": b.get("date") or b.get("datetime") or "",
            })
    except (TypeError, ValueError):
        recent_bars = []

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
        recent_bars=recent_bars,
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

    # 2026-02: Sensitivity scalar. Default 1.25 loosens the gates from
    # the original conservative values (0 fills in 14 days on the live
    # tape). Applied to:
    #   * relative-volume floors — DIVIDED by sensitivity, so higher
    #     sensitivity requires LESS rvol
    #   * pct-change / distance windows — MULTIPLIED by sensitivity,
    #     so higher sensitivity WIDENS the window
    #   * volume-acceleration floors — DIVIDED by sensitivity
    #
    # Env: ``ALPHA_PATTERN_SENSITIVITY`` (float, bounded 0.5..2.5).
    # 1.0 = original strict defaults, 1.25 = current relaxed default,
    # 1.5+ = aggressive detection (more setups, more noise), 0.75 =
    # tighten again.
    @staticmethod
    def _sensitivity() -> float:
        raw = (os.environ.get("ALPHA_PATTERN_SENSITIVITY") or "").strip()
        if not raw:
            return 1.25
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return 1.25
        return max(0.5, min(2.5, v))

    def _rvol_floor(self, base: float) -> float:
        """Divide by sensitivity — higher sens = lower rvol required."""
        return base / self._sensitivity()

    def _distance_window(self, base: float) -> float:
        """Multiply by sensitivity — higher sens = wider window."""
        return base * self._sensitivity()

    def _pct_floor(self, base: float) -> float:
        """For move-magnitude floors (e.g. 3% for momentum reaccel).
        Divide so higher sensitivity requires a SMALLER move."""
        return base / self._sensitivity()

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

        # ── Classical multi-bar patterns FIRST ─────────────────
        # 1) Bearish patterns act as invalidation gates — if a
        # confirmed H&S / rising wedge / double top is live on the
        # same symbol, we refuse to arm any bullish setup this tick.
        # 2) Confirmed bullish patterns beat everything else on
        # score (0.74-0.81) so they take precedence over the
        # single-bar setups when both fire on the same bar.
        classical_setup = self._detect_classical(m, now)
        if classical_setup == "veto":
            return None
        if classical_setup is not None:
            return classical_setup

        # ── Mean-reversion family — evaluated FIRST during chop so a
        # legitimate mean-revert signal isn't shadowed by a weak
        # momentum match on the same bar. Outside chop these patterns
        # still fire when the raw shape is present, they just don't
        # get the score boost.
        if m.vwap > 0 and m.price > 0:
            vwap_stretch = (m.vwap - m.price) / m.vwap  # positive when price BELOW vwap
            # ── VWAP fade long: price stretched ≥0.4% below VWAP,
            # volume tapering (rvol modest), red bar exhaustion.
            if (vwap_stretch >= self._pct_floor(0.004) and vwap_stretch <= 0.03
                    and m.relative_volume >= self._rvol_floor(0.8) and m.relative_volume <= 2.5
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
            if (depth >= self._pct_floor(0.75)  # bottom quarter of the day's range
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
                and m.relative_volume >= self._rvol_floor(1.0)):
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

        # ── Short-side exhaustion long: sharp decline that's
        # STABILIZING. Fires on PLTR-shaped moves the operator saw
        # dying at ``no_pattern_match``:
        #   * pct_change -3% .. -12%       (sharp but not free-fall)
        #   * relative_volume 0.5 .. 3.0   (not still panicking)
        #   * current bar showing hold     (price ≥ low * 1.003 OR
        #                                    price ≥ open * 0.995)
        # Public.com is cash-only, so we enter LONG (buy the bounce)
        # — never open a short. Kept in the mean-reversion family
        # so it gets the chop-regime score boost.
        if (m.price > 0
                and -12.0 <= m.pct_change <= -3.0
                and self._rvol_floor(0.5) <= m.relative_volume <= 3.0):
            price_stable = False
            if m.low > 0 and m.price >= m.low * 1.003:
                price_stable = True
            elif m.open_price > 0 and m.price >= m.open_price * 0.995:
                price_stable = True
            if price_stable:
                base = 0.58
                return ActiveSetup(
                    setup_id=str(uuid.uuid4()),
                    symbol=m.symbol,
                    setup_type=SetupType.SHORT_SIDE_EXHAUSTION,
                    state=SetupState.WATCHING,
                    detected_at=now,
                    reference_price=m.price,
                    # Trigger on a small reclaim — we want CONFIRMATION
                    # that buyers are pushing through the current bar,
                    # not just a dead-cat bounce.
                    trigger_price=m.price * 1.005,
                    # Invalidate on a fresh low. If we don't have a
                    # session low, fall back to a hard 1.5% stop.
                    invalidation_price=(m.low * 0.997) if m.low > 0 else (m.price * 0.985),
                    score=min(1.0, base + self._regime_bias_by_type(
                        SetupType.SHORT_SIDE_EXHAUSTION.value, chop)),
                )

        # ── VWAP reclaim ──
        if m.vwap > 0:
            vwap_distance = (m.price - m.vwap) / m.vwap
            if 0 <= vwap_distance <= self._distance_window(0.004) and m.relative_volume >= self._rvol_floor(1.5) and m.volume_acceleration >= self._rvol_floor(1.1):
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
            if 0 <= distance_to_high <= self._distance_window(0.005) and m.relative_volume >= self._rvol_floor(2.0) and m.volume_acceleration >= self._rvol_floor(1.25):
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
        if m.high > 0 and m.price >= m.high * 0.998 and m.relative_volume >= self._rvol_floor(2.5):
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
        if m.vwap > 0 and m.price > m.vwap and -3.0 <= m.pct_change <= -0.5 and m.relative_volume >= self._rvol_floor(1.2):
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
        if m.pct_change >= self._pct_floor(3.0) and m.volume_acceleration >= self._rvol_floor(1.5) and m.price >= m.open_price:
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

    def _detect_classical(self, m: MarketSnapshot, now: datetime):
        """Evaluate multi-bar classical chart patterns.

        Returns one of:
        * ``"veto"``    — a confirmed bearish pattern is active on
                          this symbol; caller must not arm any
                          bullish setup this tick
        * ``ActiveSetup`` — a confirmed bullish pattern to arm
        * ``None``      — no classical signal; caller may fall
                          through to single-bar detectors

        Bars come from ``MarketSnapshot.recent_bars`` (last ~10
        daily bars). Missing / too-few bars → return None cleanly.
        """
        bars = getattr(m, "recent_bars", None) or []
        if len(bars) < 5:  # smallest classical pattern needs 5 bars
            return None
        try:
            from services.alpha_classical_patterns import (
                best_bullish, has_bearish_veto,
            )
        except Exception:  # noqa: BLE001
            return None

        # Bearish veto wins: cancel any long attempt this tick.
        veto = has_bearish_veto(bars)
        if veto is not None:
            return "veto"

        bull = best_bullish(bars)
        if bull is None:
            return None
        setup_type = _CLASSICAL_NAME_TO_SETUP.get(bull.pattern)
        if setup_type is None:
            return None

        # Invalidation level from the pattern; trigger price is the
        # neckline/resistance level (breakout confirmation). Fall
        # back to price-derived values if the pattern didn't stamp
        # them (defensive — shouldn't happen for confirmed states).
        trigger = bull.neckline_or_support or (m.price * 1.002)
        invalid = bull.invalidation_level or (m.price * 0.97)

        return ActiveSetup(
            setup_id=str(uuid.uuid4()),
            symbol=m.symbol,
            setup_type=setup_type,
            state=SetupState.ARMED,   # confirmed = already broken out
            detected_at=now,
            reference_price=m.price,
            trigger_price=float(trigger),
            invalidation_price=float(invalid),
            score=float(bull.confidence),
        )


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

    # 2026-09-07 — Compact Authority Receipt.
    # After every resolved outcome (submit, block, or invalidate)
    # regenerate the setup's authority chain receipt so the operator
    # can audit the whole gate stack from a single Mongo doc. Details
    # live in the SQLite hot store; this is the compact summary.
    # Fire-and-forget: a receipt-side crash must never affect the
    # trade path.
    try:
        from services.alpha_authority_receipt import build_and_persist
        await build_and_persist(db, setup_id=setup.setup_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] authority receipt build failed: %s", exc)


# Module-level Wave Intelligence singleton — the machine holds a
# per-symbol LRU state cache for mode hysteresis. Fresh evaluation of
# the same closed bars is idempotent, so a single machine is safe to
# share across tick calls.
_WAVE_MACHINE_INSTANCE = None


def _wave_machine():
    global _WAVE_MACHINE_INSTANCE
    if _WAVE_MACHINE_INSTANCE is None:
        from services.wave_intelligence import WaveIntelligenceMachine
        _WAVE_MACHINE_INSTANCE = WaveIntelligenceMachine()
    return _WAVE_MACHINE_INSTANCE


# 2026-02: Seeded 50-symbol floor — mega-caps + sector reps used as
# a fail-safe when ``top_universe`` is empty (e.g. the weekly rebuild
# job crashed) or when the operator has zero live signal_dispatcher
# predictions AND no watchlist entries. Prevents the "collapse to
# nothing" failure mode operators reported when Alpha stood down for
# 14 straight days. Kept in code (not env) so it's always present.
SEEDED_50_SYMBOLS: tuple[str, ...] = (
    # Mega-cap tech (the workhorse names Alpha's patterns are tuned on)
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    "AVGO", "TSM", "ORCL", "CRM", "AMD", "ADBE", "NFLX",
    # Financials
    "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP",
    # Healthcare / consumer defensives
    "LLY", "UNH", "JNJ", "PFE", "MRK", "ABBV",
    "WMT", "COST", "PG", "KO", "PEP", "MCD",
    # Energy / industrials
    "XOM", "CVX", "CAT", "GE", "BA", "HON",
    # Communication / other
    "DIS", "CMCSA", "T", "VZ",
    # Broad-market / vol vehicles (benchmarks — analysed too, not just for context)
    "SPY", "QQQ", "IWM", "DIA",
    # Semiconductor / secular growth
    "SMH",
)


def _max_symbols_per_tick() -> int:
    raw = (os.environ.get("ALPHA_MAX_SYMBOLS_PER_TICK") or "").strip()
    if not raw:
        return 50
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 50
    # Bound: 1 ≤ n ≤ 100. Anything above 100 risks broker-rate-limit
    # exhaustion for the market-data pool inside a 5-min tick.
    return max(1, min(100, n))



# ─── Universe source ──────────────────────────────────────────────


async def _candidate_universe(db: Any, *, lookback_minutes: int = 60,
                              cap: int = 50) -> list[str]:
    """Return the union of every symbol source Alpha should scan this tick.

    Historically this only pulled ``signal_dispatcher`` / ``paper_trading``
    predictions from the last 60 minutes. In practice those upstream
    producers only fire during a narrow window right after the open,
    so Alpha's universe was starving for 22+ hours a day and every
    tick returned zero candidates. See:
        https://github.com/risedual-ai/RISEDUAL/pull/xxx (2026-02)

    The union is ordered by priority so the 50-symbol cap keeps the
    most operator-intent-heavy symbols even under contention:

    1. **Operator watchlist** — symbols the human explicitly added
       via the /admin/operator-watchlist UI. Zero-cost, high-signal.
    2. **Recent signal_dispatcher / paper_trading predictions** —
       symbols an upstream brain flagged in the last ``lookback_minutes``.
    3. **top_universe** — the weekly A/B-tier universe rebuild (300+
       symbols). This is the "always-on" baseline so Alpha keeps
       scanning even when upstream producers are quiet.

    Missing collections are treated as empty, never as errors — Alpha
    must keep ticking even when Mongo hiccups on one source.
    """
    if db is None:
        return []
    from datetime import timedelta

    seen: set[str] = set()
    ordered: list[str] = []

    def _add(sym: Any) -> None:
        if not sym:
            return
        s = str(sym).upper().strip()
        if not s or s in seen:
            return
        seen.add(s)
        ordered.append(s)

    # 1) Operator watchlist — highest priority (the human said so).
    try:
        cursor = db.operator_watchlist.find(
            {"active": {"$ne": False}},
            {"_id": 0, "symbol": 1},
        ).limit(cap)
        async for row in cursor:
            _add(row.get("symbol"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] operator_watchlist read failed: %s", exc)

    # 2) Live intraday movers (Alpha Vantage TOP_GAINERS_LOSERS).
    #    Added 2026-02 so Alpha sees NVDA-type moves and small-cap
    #    breakouts as they happen — the static watchlists have no
    #    idea a stock spiked 12% on a headline five minutes ago.
    #    Best-effort: an AV rate limit / down endpoint just skips.
    try:
        from services.alpha_live_movers import get_mover_symbols
        mover_syms = await get_mover_symbols(db, limit=min(30, cap))
        for sym in mover_syms:
            _add(sym)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] live_movers read failed: %s", exc)

    # 3) Recent signal_dispatcher / paper_trading predictions.
    since = (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).isoformat()
    try:
        cursor = db.predictions.aggregate([
            {"$match": {"feature": {"$in": ["signal_dispatcher", "paper_trading"]},
                        "timestamp": {"$gte": since}}},
            {"$group": {"_id": "$symbol"}},
            {"$limit": cap},
        ])
        for row in await cursor.to_list(length=cap):
            _add(row.get("_id"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] predictions universe read failed: %s", exc)

    # 4) top_universe — always-on baseline. Prefer A-tier first so
    # the cap doesn't get consumed by lower-conviction B-tier names.
    if len(ordered) < cap:
        remaining = cap - len(ordered)
        try:
            cursor = db.top_universe.find(
                {},
                {"_id": 0, "symbol": 1, "tier": 1},
            ).sort([("tier", 1), ("symbol", 1)]).limit(remaining * 3)
            async for row in cursor:
                if len(ordered) >= cap:
                    break
                _add(row.get("symbol"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_daytrader] top_universe read failed: %s", exc)

    # 5) Seeded 50-symbol floor — always applied last so Alpha still
    # has a real pool even when ``top_universe`` is empty (weekly
    # rebuild job crashed, fresh DB, etc.). This is the fail-safe
    # against the "collapse to nothing" mode operators reported.
    if len(ordered) < cap:
        for sym in SEEDED_50_SYMBOLS:
            if len(ordered) >= cap:
                break
            _add(sym)

    return ordered[:cap]


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


async def run_alpha_day_trader_tick(
    db: Any,
    *,
    symbols_only: Optional[set[str]] = None,
    tick_tag: Optional[str] = None,
) -> dict:
    """One scheduler tick. Returns a summary dict for logging.

    Full lifecycle capture: every stage transition (candidate → setup →
    armed → triggered → intent → seat → risk → roadguard → entry_timing
    → broker → fill) is written to the SQLite hot store with latency
    samples. Only compact resolved-outcome docs go to Mongo.

    2026-09-03 — added ``symbols_only`` parameter. When non-empty, the
    universe scan + ranking phase is skipped entirely; the tick runs
    ONLY the active-setup trigger loop, restricted to those symbols.
    This is how the 60s ``alpha_top10_stream`` job re-uses the exact
    same execution path (fingerprint dedup, execution-quote gate,
    seat / risk / roadguard, chasing filter) as the 5-min tick, but
    on a faster cadence for the operator's hottest names.

    ``tick_tag`` is a free-form string echoed into log lines and the
    top-10 mirror doc so the operator can tell 5-min and 60s ticks
    apart.
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

    # Streaming-mode: skip discovery + ranking + wave veto entirely.
    # The 5-min tick that seeded the top-10 already did all that. This
    # branch exists so the 60s stream can re-evaluate active setups
    # against fresh broker quotes without duplicating discovery work.
    is_streaming = bool(symbols_only)
    if is_streaming:
        snapshots: list[MarketSnapshot] = []
        ranked: list[tuple[MarketSnapshot, float]] = []
        min_score = _min_opportunity_score()
        # Cache regime lookups so the trigger loop still has a
        # consistent regime label for edge lookup / observation.
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
        new_setups = 0
    else:
        universe_symbols = await _candidate_universe(db, cap=_max_symbols_per_tick())
        snapshots = []
        max_per_tick = _max_symbols_per_tick()
        for sym in universe_symbols[:max_per_tick]:  # env-tunable cap
            snap = await _snapshot_symbol(sym)
            if snap is not None:
                snapshots.append(snap)
        await _bump_counter(db, "candidates_seen", len(snapshots))
        ranked = scanner.rank(snapshots, top_n=_max_active_setups())
        min_score = _min_opportunity_score()

        # 2026-09-03 — write top-10 (by opportunity score, before
        # family_floor culling) so the 60s stream has a fresh
        # watchlist for its next tick. We record ALL ranked
        # candidates, up to MAX_TOP_N. See ``alpha_top10_state``.
        try:
            from services import alpha_top10_state
            top_entries = [
                {
                    "symbol": s.symbol,
                    "score": float(score),
                    "price": float(s.price),
                    "pct_change": float(s.pct_change),
                    "relative_volume": float(s.relative_volume),
                }
                for (s, score) in ranked[: alpha_top10_state.MAX_TOP_N]
            ]
            alpha_top10_state.set_top10(top_entries, source_tick=tick_tag or "5min")
            await alpha_top10_state.mirror_to_mongo(db)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_daytrader] top-10 seed failed (non-fatal): %s", exc)

        # 2026-09-11 — Funnel orchestrator (operator design).
        # Wrap the ranked discovery output with discovery →
        # preliminary → broker research → deep discernment →
        # ARMED promotion. The funnel's ARMED list becomes the
        # 60s stream's watchlist so ACTIONABLE never persists
        # across cycles — it must be re-earned each 60s tick.
        try:
            from services import alpha_funnel
            funnel_input = [
                {
                    "symbol": s.symbol,
                    "score": float(score),
                    "signal_price": float(s.price),
                }
                for (s, score) in ranked
            ]
            await alpha_funnel.run_funnel_cycle(db, ranked_discovery=funnel_input)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_daytrader] funnel cycle failed (non-fatal): %s", exc)

        # ── Regime context (fetched ONCE per tick, best-effort) ──
        # Fetched BEFORE pattern detection so mean-reversion setups can
        # arm during chop and momentum setups can be softly de-emphasized
        # in the same regime. Regime labels are non-blocking — missing
        # data becomes UNKNOWN and every pattern is evaluated as usual.
        tick_slow_regime = None
        tick_fast_regime = None
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
        # Selective floor by candidate family. See ``_family_floor``
        # for the rules — the Why-Not-Trade diagnostic proved a
        # single flat floor was culling ~80% of ranked candidates
        # including large-cap momo names the operator wanted traded.
        family_floor, family_tag = _family_floor(snap, default_floor=min_score)

        # Synthetic setup_id for pre-setup rejections so the
        # ``why-not-trade`` aggregator can group them. Real setups
        # get real IDs generated by ``AlphaPatternEngine.detect``
        # once they clear these gates.
        pretick_id = f"pretick:{snap.symbol}:{time.time_ns()}"

        if opp_score < family_floor:
            await _record_observation(db, pretick_id, "opportunity_score_rejected", {
                "symbol": snap.symbol,
                "stage": "pre_pattern",
                "reason": "score_below_floor",
                "opp_score": round(float(opp_score), 4),
                "family_floor": round(float(family_floor), 4),
                "family_tag": family_tag,
                "min_score": min_score,
            })
            continue

        # 2026-02: Persist ALL six classical-pattern assessments
        # (bullish + bearish, including blocked/forming) so we can
        # later train models on which patterns actually pay.
        # Fire-and-forget — never blocks the tick.
        try:
            if snap.recent_bars and len(snap.recent_bars) >= 5:
                from services.alpha_classical_patterns import (
                    assess_bullish_patterns, assess_bearish_patterns,
                )
                from services.alpha_pattern_research import (
                    record_assessments as _record_research,
                )
                all_assessments = (
                    assess_bullish_patterns(snap.recent_bars)
                    + assess_bearish_patterns(snap.recent_bars)
                )
                await _record_research(
                    db, symbol=snap.symbol, assessments=all_assessments,
                    timeframe="daily",
                )
        except Exception:  # noqa: BLE001
            pass

        # 2026-02: Wave Intelligence per-symbol regime + DANGER veto.
        # Runs on the same ``recent_bars`` we already fetch. If the
        # symbol is in DANGER_PAUSE (volatility expansion, price
        # shock, wide spread), skip the tick entirely — this is the
        # per-symbol safety layer Alpha's SPY-level regime can't see.
        wave_mode: Optional[str] = None
        try:
            if snap.recent_bars and len(snap.recent_bars) >= 5:
                _machine = _wave_machine()
                wave_obs = _machine.evaluate(
                    symbol=snap.symbol,
                    lane="equity",
                    timeframe="daily",
                    bars=snap.recent_bars,
                    spread_bps=snap.spread_bps,
                )
                wave_mode = wave_obs.mode.value
                # Fire-and-forget persistence for later analysis.
                try:
                    from services.alpha_wave_persistence import (
                        record_observation as _record_wave,
                    )
                    await _record_wave(db, observation=wave_obs.to_dict())
                except Exception:  # noqa: BLE001
                    pass
                if wave_mode == "DANGER_PAUSE":
                    await _bump_counter(db, "wave_danger_vetoes", 1)
                    logger.info(
                        "[alpha_daytrader] symbol=%s VETO — wave DANGER_PAUSE (danger=%.2f)",
                        snap.symbol, wave_obs.scores.danger,
                    )
                    await _record_observation(db, pretick_id, "wave_danger_pause", {
                        "symbol": snap.symbol,
                        "stage": "pre_pattern",
                        "reason": "wave_danger_pause",
                        "danger_score": round(float(wave_obs.scores.danger), 3),
                        "wave_mode": wave_mode,
                    })
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_daytrader] wave eval failed for %s: %s",
                         snap.symbol, exc)

        candidate = patterns.detect(
            snap,
            slow_regime=tick_slow_regime,
            fast_regime=tick_fast_regime,
        )
        if candidate is None:
            # The pattern engine returned no setup — this is the
            # single biggest silent reject in Alpha and we couldn't
            # see it before this observation was added. We dump the
            # snap features the pattern engine uses so the operator
            # can eyeball whether the sensitivity gates are too tight.
            await _record_observation(db, pretick_id, "no_pattern_match", {
                "symbol": snap.symbol,
                "stage": "pattern_engine",
                "reason": "no_pattern_match",
                "opp_score": round(float(opp_score), 4),
                "price": snap.price,
                "vwap": snap.vwap,
                "pct_change": round(float(snap.pct_change), 3),
                "relative_volume": round(float(snap.relative_volume), 3),
                "spread_bps": snap.spread_bps,
                "slow_regime": tick_slow_regime,
                "fast_regime": tick_fast_regime,
            })
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
    active_query: dict[str, Any] = {
        "state": {"$in": [SetupState.WATCHING.value, SetupState.ARMED.value]},
    }
    if is_streaming:
        # Streaming-mode: restrict to the top-10 watchlist symbols so
        # the 60s cadence doesn't accidentally re-evaluate the entire
        # active-setup population on every 60s tick.
        active_query["symbol"] = {"$in": sorted(symbols_only)}
    active_cursor = db.alpha_active_setups.find(active_query)
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

        # 2026-02: Economic-fingerprint dedup — hash the (symbol,
        # setup_type, direction, timeframe, ATR-scaled entry zone)
        # tuple and refuse to emit a second intent for the same
        # fingerprint within the dedup window. Prevents 3+ GOOGL
        # long intents from stacking up on consecutive scanner ticks.
        try:
            from services.alpha_fingerprint import economic_fingerprint
            fp = economic_fingerprint(
                symbol=setup.symbol,
                strategy=setup.setup_type.value,
                direction=("long" if setup.direction == "BUY" else "short"),
                timeframe="intraday",
                entry_zone=float(setup.trigger_price or snap.price),
                atr=abs(float(setup.trigger_price or snap.price)
                        - float(setup.invalidation_price or snap.price * 0.99)),
            )
        except Exception:  # noqa: BLE001
            fp = None

        if fp:
            try:
                from datetime import timedelta as _td
                dedup_window_min = int(
                    os.environ.get("ALPHA_INTENT_DEDUP_WINDOW_MIN") or 15
                )
                since = (
                    datetime.now(timezone.utc) - _td(minutes=dedup_window_min)
                )
                prior = await db.alpha_intent_fingerprints.find_one({
                    "fingerprint": fp,
                    "created_at": {"$gte": since},
                })
            except Exception:  # noqa: BLE001
                prior = None
            if prior:
                await _bump_counter(db, "intents_deduplicated", 1)
                await _record_observation(db, setup.setup_id, "intent_deduplicated", {
                    "symbol": setup.symbol,
                    "stage": "dedup",
                    "reason": "economic_fingerprint",
                    "fingerprint": fp,
                    "prior_intent_id": prior.get("intent_id"),
                    "window_min": dedup_window_min,
                })
                logger.info(
                    "[alpha_daytrader] symbol=%s DEDUPED — fingerprint %s matches recent intent %s",
                    setup.symbol, fp[:12], prior.get("intent_id"),
                )
                continue

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
            "economic_fingerprint": fp,
        })
        intent_ns = time.time_ns()
        alpha_hot_store.record_latency(setup.setup_id, "trigger_to_intent",
                                        max(0, (intent_ns - trigger_ns) // 1_000_000))
        triggered_intents += 1
        await _bump_counter(db, "intents_created", 1)

        # 2026-02: Persist the fingerprint so the NEXT tick's dedup
        # check sees this intent. Kept in a TTL-scoped collection so
        # old fingerprints self-expire (see ``ensure_indexes``).
        if fp:
            try:
                await db.alpha_intent_fingerprints.insert_one({
                    "fingerprint": fp,
                    "intent_id": intent.intent_id,
                    "symbol": setup.symbol,
                    "setup_type": setup.setup_type.value,
                    "trigger_price": setup.trigger_price,
                    "invalidation_price": setup.invalidation_price,
                    "created_at": datetime.now(timezone.utc),
                })
            except Exception as exc:  # noqa: BLE001
                logger.debug("[alpha_daytrader] fingerprint write failed: %s", exc)
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

        # ── EXECUTION-QUOTE GATE ──
        # Broker data is the source of truth for tradable-price
        # right-now context. Re-quote the symbol via the
        # decision-type policy (broker first, vendor as witness)
        # and REFUSE to submit when:
        #   * the broker quote is stale (age > freshness ceiling)
        #   * broker and vendor disagree > drift ceiling
        #   * the broker has no price at all (symbol not covered /
        #     broker offline) — in that case we defer this tick so
        #     the next scan can retry against a healthier broker.
        # These gates are DIAGNOSTIC-heavy: every reject writes a
        # ``data_conflict`` / ``broker_quote_missing`` observation
        # so we can audit exactly why Alpha stood down.
        broker_confirmed_price: Optional[float] = None
        try:
            from services.provider_policy import fetch_execution_quote
            xq = await fetch_execution_quote(setup.symbol)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[alpha_daytrader] execution-quote gate errored for %s: %s",
                setup.symbol, exc,
            )
            xq = None
        if xq is not None:
            gate_payload = {
                "symbol": setup.symbol,
                "stage": "execution_quote",
                "source": xq.source,
                "price": xq.price,
                "age_seconds": xq.age_seconds,
                "broker_price": xq.broker_price,
                "vendor_price": xq.vendor_price,
                "vendor_source": xq.vendor_source,
                "disagreement_bps": xq.disagreement_bps,
                "data_conflict": xq.data_conflict,
                "reason": xq.reason,
            }
            if not xq.execution_allowed:
                await _record_observation(
                    db, setup.setup_id, "execution_quote_blocked", gate_payload,
                )
                await _record_outcome(
                    db, setup=setup, intent_id=intent.intent_id,
                    submitted=False, filled=False,
                    reject_reason=xq.reason or "execution_quote_blocked",
                    **_outcome_kwargs,
                )
                alpha_hot_store.release_symbol_lock(
                    setup.symbol, setup_id=setup.setup_id,
                )
                continue
            broker_confirmed_price = xq.price
            await _record_observation(
                db, setup.setup_id, "execution_quote_confirmed", gate_payload,
            )
            intent.reason["broker_confirmed_price"] = broker_confirmed_price
            intent.reason["execution_quote_source"] = xq.source
            intent.reason["execution_quote_age_seconds"] = xq.age_seconds
            if xq.disagreement_bps is not None:
                intent.reason["execution_quote_drift_bps"] = xq.disagreement_bps

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
        "streaming": is_streaming,
        "tick_tag": tick_tag or ("stream" if is_streaming else "5min"),
    }
    logger.info("[alpha_daytrader] tick %s", summary)

    # Fail-visible invariant: sweep any intent that lingered without a
    # terminal event so it can't silently disappear from why-not-trade.
    try:
        from services import alpha_orphan_reaper
        await alpha_orphan_reaper.sweep_orphaned_intents(db)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_daytrader] orphan reaper skipped: %s", exc)

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
