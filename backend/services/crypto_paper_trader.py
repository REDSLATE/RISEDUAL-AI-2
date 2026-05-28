"""Crypto Paper Trader — isolated 24/7 entry executor.

Pipeline per symbol
-------------------
1. ``adversarial_signal()`` — Strategist proposes, Auditor vetoes.
2. ``infer_crypto_regime`` + ``infer_failure_context`` — tag the
   signal with regime/failure metadata so the adaptation layer
   can match against active down-weight rules.
3. ``apply_crypto_adaptations_to_signal`` — runtime confidence
   haircut from any active ``crypto_model_adaptations``.
4. ``log_adversarial_decision`` — every Auditor verdict (CONFIRM,
   VETO, HOLD) hits ``crypto_signal_audit_log`` so the
   ``/api/crypto/strategist-stats`` calibration tile can compute
   the veto rate over rolling windows.
5. ``compute_crypto_position_size`` — confidence-scaled notional
   ($250 base, $1000 cap).
6. ``build_stop_take_profit`` — defensive SL/TP defaults
   (-2% / +4% LONG, +2% / -4% SHORT).
7. Insert into ``crypto_paper_trades`` ONLY.

Architecture rule
-----------------
Reads/writes ONLY ``crypto_paper_trades`` + ``crypto_signal_audit_log``
+ ``crypto_model_adaptations``. Never touches the equity ``paper_trades``
collection or ``ml_paper_trader`` / ``paper_trade_closer`` /
``price_provider.get_quote``.

The architectural firewall is enforced two ways:
* ``is_crypto_symbol()`` filter rejects non-crypto tickers BEFORE
  any quote / DB call (so AAPL passed to this bot can never land
  in the crypto collection).
* AST-verified zero forbidden imports — only ``crypto_strategist``,
  ``crypto_adaptation_service``, ``crypto_signal_audit``,
  ``crypto_memory_writer`` (for the regime classifier).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from services.crypto_strategist import adversarial_signal
from services.crypto_adaptation_service import apply_crypto_adaptations_to_signal
from services.crypto_signal_audit import log_adversarial_decision
from services.research_router import fetch_or_skip as research_fetch_or_skip
from services.web_research_service import get_shadow_verdict as default_shadow_fetcher
from services.adversarial_core import run_adversarial_decision
from services.adversarial_logger import log_adversarial_decision as log_adv_decision

logger = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────────────
# Universe expanded from 3 → 8 on 2026-05-02 to accelerate paper-
# trading throughput. Each symbol is independent — adding one
# costs ~one quote + history fetch every 15 min, ~3% extra per
# symbol. Strategist + adversarial layers do the conviction
# filtering, so adding symbols can never amplify risk per trade
# (only frequency). Higher frequency → more learning samples →
# faster Tier 3 unlock.
#
# Eligibility requires (a) live quote available via crypto_quotes
# (yfinance ``-USD`` fallback), and (b) at least 30 bars of
# 1h history for the indicator stack. All 8 listed symbols were
# verified end-to-end on 2026-05-02 (BNB, XRP, ADA, AVAX, LINK
# join the original BTC, ETH, SOL).
CRYPTO_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK"]

# Lowered from 0.60 → 0.55 on 2026-05-02. The 0.60 gate was
# producing ~48% HOLD decisions in the first week of operation,
# starving the ML pipeline of training samples (only 23 trades
# in the user's 6-day window). 0.55 widens the trade band by
# roughly 2× without changing position sizing — the adversarial
# layer (commander vs strategist veto) still rejects high-
# disagreement signals, so this lowers *frequency floor*, not
# *quality floor*. Revisit once trade volume is healthy.
MIN_CRYPTO_CONFIDENCE = 0.55
BASE_CRYPTO_NOTIONAL = 250.0
MAX_CRYPTO_NOTIONAL = 1000.0


# Canonical crypto registry — symbols this bot will ever trade.
# Kept inline so the bot module is self-contained.
_CRYPTO_REGISTRY: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK",
})


def is_crypto_symbol(symbol: str | None) -> bool:
    """Tolerant crypto check — accepts ``BTC``, ``BTC/USD``, ``btc-usd``."""
    if not symbol:
        return False
    s = str(symbol).strip().upper().replace("/USD", "").replace("-USD", "")
    return s in _CRYPTO_REGISTRY


# ── Position sizing ───────────────────────────────────────────────────────────


def compute_crypto_position_size(confidence: float) -> float:
    """Confidence-scaled paper notional.

    * Below the 0.55 floor → 0 (no fill).
    * 0.55 → ~$229 (linear within the [0.55, 0.95] band, scaled
      so 0.60 maps to BASE_CRYPTO_NOTIONAL for backwards
      compatibility with prior sizing audits).
    * 0.95 → ~$396.
    * Capped at ``MAX_CRYPTO_NOTIONAL`` so even a perfect-storm
      signal can't blow out the paper portfolio.
    """
    if confidence < MIN_CRYPTO_CONFIDENCE:
        return 0.0

    # Clamp into the active band, then scale so a 0.60 reading
    # still maps to BASE_CRYPTO_NOTIONAL (the historical sizing
    # contract). Lower-confidence-but-valid signals (0.55-0.59)
    # therefore size proportionally lower, not equal to 0.60.
    multiplier = min(max(confidence, MIN_CRYPTO_CONFIDENCE), 0.95)
    size = BASE_CRYPTO_NOTIONAL * (multiplier / 0.60)
    return round(min(size, MAX_CRYPTO_NOTIONAL), 2)


def _resolve_sltp_config() -> dict[str, float | bool]:
    """Read SL/TP knobs from the env once per call. Tunable via:

    * ``CRYPTO_SL_PCT`` (default ``1.0`` → 1% from entry; was 2.0)
    * ``CRYPTO_TP_PCT`` (default ``4.0`` — used only when TP is on)
    * ``CRYPTO_DISABLE_TP`` (default ``1`` → hard TP off, trailing
      handles winner exits; legacy behaviour was hard TP at +4%)

    The 2026-Q2 calibration audit found the hard TP firing on only
    1% of closes (10 / 808) while contributing nothing to the
    profit factor. Time-stops and trailing-stops do the actual
    work, so the default is now TP-off.
    """
    def _f(name: str, default: float) -> float:
        try:
            v = float(os.environ.get(name, "") or default)
            return max(0.0, v)
        except ValueError:
            return default

    def _b(name: str, default: bool) -> bool:
        v = os.environ.get(name, "").strip().lower()
        if v == "":
            return default
        return v in {"1", "true", "yes", "on"}

    return {
        "sl_pct": _f("CRYPTO_SL_PCT", 1.0),
        "tp_pct": _f("CRYPTO_TP_PCT", 4.0),
        "tp_disabled": _b("CRYPTO_DISABLE_TP", True),
    }


def build_stop_take_profit(
    entry: float,
    direction: str,
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    tp_disabled: bool | None = None,
) -> dict[str, float | None]:
    """Build defensive SL / (optional) TP defaults.

    LONG: SL = entry × (1 - sl_pct/100)
          TP = entry × (1 + tp_pct/100)  (or None when disabled)
    SHORT: SL = entry × (1 + sl_pct/100)
           TP = entry × (1 - tp_pct/100) (or None when disabled)

    Defaults are read from the env (see ``_resolve_sltp_config``)
    when not explicitly passed — sl_pct=1.0, tp_disabled=True. Hard
    TP is off by default; trailing-stop in ``crypto_closer`` is the
    winner-exit mechanism. ``hold_window_expired`` remains the
    safety floor.
    """
    cfg = _resolve_sltp_config()
    sl = sl_pct if sl_pct is not None else cfg["sl_pct"]
    tp = tp_pct if tp_pct is not None else cfg["tp_pct"]
    tp_off = tp_disabled if tp_disabled is not None else cfg["tp_disabled"]

    sl_mult = sl / 100.0
    tp_mult = tp / 100.0

    if direction.upper() == "SHORT":
        return {
            "stop_loss": round(entry * (1 + sl_mult), 2),
            "take_profit": None if tp_off else round(entry * (1 - tp_mult), 2),
        }
    return {
        "stop_loss": round(entry * (1 - sl_mult), 2),
        "take_profit": None if tp_off else round(entry * (1 + tp_mult), 2),
    }


# ── Regime + failure-context taggers ──────────────────────────────────────────


def infer_crypto_regime(signal: dict[str, Any]) -> str:
    """Tag the signal with regime so the adaptation layer can match
    against ``adaptation.regime`` keys."""
    indicators = signal.get("strategist", {}).get("indicators", {})
    rsi = indicators.get("rsi")
    mom = indicators.get("momentum_5b")

    if isinstance(mom, (int, float)) and abs(mom) >= 0.08:
        return "parabolic"
    if isinstance(rsi, (int, float)) and rsi >= 70:
        return "overbought"
    if isinstance(rsi, (int, float)) and rsi <= 30:
        return "oversold"
    if signal.get("direction") == "LONG":
        return "trend_up"
    if signal.get("direction") == "SHORT":
        return "trend_down"
    return "neutral"


def infer_failure_context(signal: dict[str, Any]) -> dict[str, Any]:
    """Pre-tag the LIKELY failure code for this signal so the
    adaptation layer can match on either ``regime`` OR
    ``failure_context.likely_failure_code``."""
    indicators = signal.get("strategist", {}).get("indicators", {})
    rsi = indicators.get("rsi")
    mom = indicators.get("momentum_5b")

    likely = None
    if isinstance(mom, (int, float)) and abs(mom) >= 0.08:
        likely = "PARABOLIC_EXHAUSTION"
    elif isinstance(rsi, (int, float)) and (rsi >= 70 or rsi <= 30):
        likely = "EXTREME_RSI_FAILURE"

    return {"likely_failure_code": likely}


# ── Per-symbol pipeline ───────────────────────────────────────────────────────


QuoteProvider = Callable[[str], Awaitable[dict]]
HistoryProvider = Callable[[str], Awaitable[list[float]]]


async def run_crypto_symbol(
    db: Any,
    symbol: str,
    bars: list[float],
    quote_provider: QuoteProvider,
) -> dict[str, Any]:
    """Run one crypto symbol through the full pipeline.

    Returns either ``{"opened": True, ...}`` on a fill or
    ``{"skipped": True, "reason": ...}`` on any halt. Never raises
    out — the multi-symbol runner above catches and converts to
    ``errors`` entries.
    """
    if db is None:
        return {"symbol": symbol, "skipped": True, "reason": "db_missing"}

    # Architectural firewall — never let a non-crypto symbol land
    # in crypto_paper_trades.
    if not is_crypto_symbol(symbol):
        return {"symbol": symbol, "skipped": True, "reason": "not_crypto_symbol"}

    if not bars or len(bars) < 30:
        return {"symbol": symbol, "skipped": True, "reason": "insufficient_bars"}

    # ── Ticker Abandonment / Cooldown Gate ──────────────────────────────────
    # FIRST gate by design — bad ticker behaviour reduces attention
    # before consuming capital. Stateless rolling-window decision.
    try:
        from services.ticker_abandonment import decide_ticker_exit
        from services.ticker_abandonment_stats import compute_crypto_inputs
        _abandon_inputs = await compute_crypto_inputs(db, symbol)
        _abandon = decide_ticker_exit(
            symbol=symbol,
            recent_signals=_abandon_inputs.recent_signals,
            recent_rejections=_abandon_inputs.recent_rejections,
            recent_losses=_abandon_inputs.recent_losses,
            recent_wins=_abandon_inputs.recent_wins,
            avg_confidence=_abandon_inputs.avg_confidence,
            avg_rr=_abandon_inputs.avg_rr,
            last_profitable_at=_abandon_inputs.last_profitable_at,
        )
        if _abandon.action in ("COOLDOWN", "ABANDON"):
            logger.info(
                "[crypto_paper] %s: ticker abandonment %s (%s, cooldown=%dm) — skipping",
                symbol, _abandon.action, _abandon.reason,
                _abandon.cooldown_minutes,
            )
            return {
                "symbol": symbol,
                "skipped": True,
                "reason": f"ticker_{_abandon.action.lower()}_{_abandon.reason}",
                "cooldown_minutes": _abandon.cooldown_minutes,
            }
    except Exception as _abandon_exc:  # noqa: BLE001
        logger.debug(
            "[crypto_paper] ticker abandonment gate skipped for %s: %s",
            symbol, _abandon_exc,
        )

    raw_signal = adversarial_signal(bars)

    # Tag regime + failure_context on the proposal so the
    # adaptation layer below can match either key.
    signal = {
        **raw_signal,
        "symbol": symbol,
        "regime": infer_crypto_regime(raw_signal),
        "failure_context": infer_failure_context(raw_signal),
    }

    # ── ADL-2: Shadow receipt (fire-and-forget) ────────────────────────────
    # Captures EVERY crypto-lane decision in ``alpha_decision_log`` so the
    # v2 retrain join has visibility into the crypto path (was 1.40%
    # coverage). Runs OUT-OF-BAND on the event loop — NEVER blocks the
    # caller, NEVER mutates ``signal`` or downstream state, NEVER raises.
    # The shadow pipeline runs its own 8-layer ML chain independently
    # of the bot's gates below, so a single early-fire call covers the
    # APPROVED, HOLD, and gate-blocked paths uniformly.
    try:
        from services.ml.receipt_dispatch import schedule_shadow_receipt
        schedule_shadow_receipt(
            db,
            signal=signal,
            market_data=None,  # let extract_live_features fill live values
            lane="crypto",
            requested_notional_usd=0.0,
            source="crypto_paper_trader",
        )
    except Exception as _adl_exc:  # noqa: BLE001 — defensive belt+braces
        logger.debug(
            "[crypto_paper] ADL receipt schedule skipped for %s: %s",
            symbol, _adl_exc,
        )

    # ── Early HOLD evaluation ─────────────────────────────────────────────
    # NOTE: we do NOT short-circuit on HOLD anymore. The adversarial layer
    # in ``full`` phase can override a HOLD into a trade — that override
    # path was unreachable before this refactor. We still treat HOLD as the
    # default (no entry) and only the adversarial layer below can promote
    # it to a fill.
    starting_direction = raw_signal["direction"]

    # Apply adaptations BEFORE the adversarial layer so the layer sees the
    # post-adaptation confidence/direction (closer to what would actually
    # fire in production).
    if starting_direction != "HOLD":
        signal = await apply_crypto_adaptations_to_signal(db, signal)
        starting_direction = signal["direction"]

    # ── Shadow Web Research ───────────────────────────────────────────────
    # Tavily + LLM narrative classifier. SHADOW ONLY — verdict is attached
    # to ``signal["web_research_shadow_verdict"]`` for downstream logging
    # (audit_log + crypto_paper_trades). It MUST NOT alter direction or
    # confidence. Fully isolated try/except: a Tavily/LLM fault must
    # never block a live fill.
    #
    # Skipped on HOLD signals — no narrative value to gather when there's
    # no proposed trade. This also short-circuits Tavily spend on idle ticks.
    if starting_direction != "HOLD":
        try:
            shadow_verdict = await research_fetch_or_skip(
                db, symbol, signal, fetcher=default_shadow_fetcher,
            )
            if shadow_verdict is not None:
                signal["web_research_shadow_verdict"] = shadow_verdict
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[crypto-bot] shadow web research failed for %s: %s", symbol, exc,
            )

    # ── Adversarial Decision Layer (Bull / Bear / Commander) ──────────────
    # Double-gated: (a) CRYPTO_ADVERSARIAL_ENABLED=1 env flag,
    # (b) ML Tier 3 unlocked. Both default-closed. When either gate is
    # shut, run_adversarial_decision returns None and we no-op here.
    #
    # Phase semantics enforced HERE (not inside adversarial_core, which
    # stays a pure-function module):
    #
    #   shadow    → log only. Trade fires per strategist/auditor.
    #   risk_only → log + scale size_usd by risk_multiplier. Direction
    #               unchanged.
    #   veto      → log + Commander's NO_TRADE blocks the fill.
    #               Commander's LONG/SHORT_OR_AVOID does not yet
    #               override direction (that's `full` only).
    #   full      → log + Commander's decision is authoritative for
    #               BOTH direction AND fill-or-skip. Can promote a
    #               strategist HOLD into a LONG (the override-triggers-
    #               entry case that was unreachable before this refactor).
    adversarial_decision_id: Optional[str] = None
    adv_decision: Optional[dict] = None
    try:
        adv_decision = await run_adversarial_decision(db, signal)
        if adv_decision is not None:
            adversarial_decision_id = await log_adv_decision(db, adv_decision)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[crypto-bot] adversarial decision failed for %s: %s", symbol, exc,
        )
        adv_decision = None

    # Resolve the final fire-or-skip + direction + size based on phase.
    final_direction = starting_direction
    size_multiplier = 1.0
    adversarial_action: Optional[str] = None  # "veto_block" | "full_override" | "full_trigger"

    # ── Symbol Failure Memory — short-term per-symbol bias loop containment ─
    # Applied BEFORE adversarial-phase resolution so size_multiplier and
    # confidence reflect the penalty when the resolution layers run.
    failure_penalty_meta: Optional[dict] = None
    try:
        from services.symbol_failure_memory import get_failure_penalty
        _penalty_crypto = await get_failure_penalty(
            db, symbol=symbol, direction=str(signal.get("direction") or "HOLD"),
            asset_type="crypto",
        )
        if _penalty_crypto.force_hold:
            logger.warning(
                "[crypto-bot] Symbol failure cooldown HOLD %s — recent_losses=%d, misses_7d=%d",
                symbol, _penalty_crypto.recent_losses, _penalty_crypto.misses_7d,
            )
            from services.crypto_signal_audit import log_adversarial_decision as _log_skip
            await _log_skip(db, symbol=symbol, signal=signal, final_direction="HOLD")
            return {
                "symbol": symbol, "skipped": True,
                "reason": "symbol_failure_cooldown",
                "recent_losses": _penalty_crypto.recent_losses,
                "misses_7d": _penalty_crypto.misses_7d,
            }
        # Apply confidence + size multipliers
        _conf_before = float(signal.get("confidence") or 0.0)
        _new_conf, _new_size = _penalty_crypto.apply(
            confidence=_conf_before, size_multiplier=size_multiplier,
        )
        if _new_conf < _conf_before:
            logger.info(
                "[crypto-bot] Symbol failure penalty applied %s: conf %.3f→%.3f size×%.2f",
                symbol, _conf_before, _new_conf, _new_size,
            )
        signal["confidence"] = _new_conf
        size_multiplier = _new_size
        failure_penalty_meta = {
            "recent_losses": _penalty_crypto.recent_losses,
            "misses_7d": _penalty_crypto.misses_7d,
            "confidence_multiplier": _penalty_crypto.confidence_multiplier,
            "size_multiplier_applied": _penalty_crypto.size_multiplier,
            "confidence_cap": _penalty_crypto.confidence_cap,
            "force_hold": _penalty_crypto.force_hold,
            "reason": _penalty_crypto.reason,
        }
        signal["symbol_failure_penalty"] = failure_penalty_meta
    except Exception as _fp_exc:  # noqa: BLE001
        logger.debug("[crypto-bot] failure penalty lookup failed for %s: %s", symbol, _fp_exc)

    # ── Dynamic confidence gate (drawdown / loss-streak / calibration aware) ─
    # Tests POST-PENALTY confidence: the failure-memory layer above may have
    # already trimmed conviction. If we still pass the dynamic threshold,
    # proceed; otherwise short-circuit to HOLD with full audit logging.
    try:
        from services.confidence_gate import get_dynamic_confidence_threshold
        _crypto_thresh = await get_dynamic_confidence_threshold(db, asset_type="crypto")
        _post_penalty_conf = float(signal.get("confidence") or 0.0)
        if (
            starting_direction != "HOLD"
            and final_direction != "HOLD"
            and _post_penalty_conf < _crypto_thresh.threshold
        ):
            logger.info(
                "[crypto-bot] Dynamic confidence gate blocked %s: %.3f < %.3f (delta=%.2f, %s)",
                symbol, _post_penalty_conf, _crypto_thresh.threshold,
                _crypto_thresh.delta, ",".join(_crypto_thresh.reasons),
            )
            from services.crypto_signal_audit import log_adversarial_decision as _log_skip
            await _log_skip(db, symbol=symbol, signal=signal, final_direction="HOLD")
            return {
                "symbol": symbol,
                "skipped": True,
                "reason": "below_dynamic_confidence_threshold",
                "threshold": _crypto_thresh.threshold,
                "confidence": _post_penalty_conf,
                "gate_reasons": _crypto_thresh.reasons,
            }
    except Exception as _cg_exc:  # noqa: BLE001
        logger.debug("[crypto-bot] confidence gate lookup failed for %s: %s", symbol, _cg_exc)

    if adv_decision is not None:
        phase = adv_decision.get("phase") or "shadow"
        commander = (adv_decision.get("decision") or "").upper()
        risk_mult = float(adv_decision.get("risk_multiplier") or 0.0)

        if phase == "risk_only":
            # Direction unchanged. Size scaled by Commander's conviction
            # gap. risk_mult is in [0, 1].
            size_multiplier = max(0.0, min(risk_mult, 1.0))

        elif phase == "veto":
            # Commander can BLOCK a strategist-driven entry, but not
            # override direction. (LONG/SHORT verdicts in veto phase
            # only refine sizing, like risk_only.)
            if commander == "NO_TRADE":
                final_direction = "HOLD"
                adversarial_action = "veto_block"
            else:
                size_multiplier = max(0.0, min(risk_mult, 1.0))

        elif phase == "full":
            # Commander's decision is authoritative for direction.
            # The override-triggers-entry case lives here: a strategist
            # HOLD can be promoted to LONG by Commander conviction.
            if commander == "LONG":
                if final_direction != "LONG":
                    adversarial_action = "full_trigger" if final_direction == "HOLD" else "full_override"
                final_direction = "LONG"
            elif commander == "SHORT_OR_AVOID":
                # SHORT_OR_AVOID is treated as a hard skip (no live
                # crypto-short path on the paper bot today).
                final_direction = "HOLD"
                adversarial_action = "veto_block"
            else:
                final_direction = "HOLD"
                adversarial_action = "veto_block"

    # ── Research Shadow Layer ─────────────────────────────────────────────
    # Champion-challenger: an alternate engine rides along on every
    # cycle and records what it would have done. Fire-and-forget —
    # NEVER block the active path, NEVER raise out. Env-driven for
    # the fleet-wide crypto bot (per-symbol bot docs don't exist on
    # this lane today).
    #
    # Tier-3 firewall: shadow code only writes to
    # ``research_shadow_decisions``. See services/research_shadow.py
    # for the full firewall ruleset.
    import os as _os_shadow
    _shadow_engine = _os_shadow.environ.get("CRYPTO_RESEARCH_SHADOW_ENGINE", "none")
    if _shadow_engine in ("adversarial", "council"):
        try:
            import asyncio as _asyncio_shadow
            from services.research_shadow_engines import fire_shadow as _fire_shadow
            # Use the most recent bar as a price proxy — keeps the HOLD
            # branch from issuing an extra quote call.
            _mid_price_shadow = float(bars[-1]) if bars else 0.0
            _asyncio_shadow.create_task(_fire_shadow(
                db,
                bot_id="crypto_fleet",
                user_id="system",
                symbol=symbol,
                asset_type="crypto",
                decision_phase="entry" if final_direction != "HOLD" else "cycle",
                active_engine="crypto_strategist",
                active_action=final_direction,
                shadow_engine=_shadow_engine,
                signal=signal,
                mid_price=_mid_price_shadow,
            ))
        except Exception as _shadow_exc:  # noqa: BLE001
            logger.warning(
                "[crypto-bot] shadow fire-and-forget setup failed for %s: %s",
                symbol, _shadow_exc,
            )

    # ── Patent J — proof-chain SIGNAL_CREATED (every cycle, HOLD or not)
    # Append a single SIGNAL_CREATED block per cycle so every decision
    # (including HOLDs that short-circuit before the full guard) gets
    # an immutable audit row. The guard pipeline appends the rest of
    # the chain only when a directional signal is actually evaluated.
    import os as _os_signal_log
    if (_os_signal_log.environ.get("PATENT_GUARD_ENABLED", "1") or "").lower() not in ("0", "false", ""):
        try:
            from services.proof_chain import (
                AsyncMongoProofChainStore, ProofEvent, ProofEventType,
                async_append_proof_event,
            )
            from datetime import datetime as _dt_signal_log, timezone as _tz_signal_log
            if db is not None:
                await async_append_proof_event(
                    AsyncMongoProofChainStore(db),
                    ProofEvent(
                        event_type=ProofEventType.SIGNAL_CREATED,
                        entity_id=f"crypto:{symbol}:{int(_dt_signal_log.now(_tz_signal_log.utc).timestamp())}",
                        actor="crypto_paper_bot",
                        payload={
                            "symbol": symbol,
                            "direction": str(signal.get("direction") or "HOLD"),
                            "confidence": float(signal.get("confidence") or 0.0),
                            "regime": signal.get("regime"),
                            "reason": signal.get("reason"),
                            "strategist": signal.get("strategist"),
                            "auditor": signal.get("auditor"),
                        },
                    ),
                )
        except Exception as _e:  # noqa: BLE001
            logger.warning("[patent_j] signal_created proof append failed: %s", _e)

    # ── Sovereign AI Shadow + bounded contribution (crypto core) ─────────────
    # PRD-mode: shadow-log every tick AND (if promoted) nudge the
    # ``signal["confidence"]`` via the bounded contribution path. Runs
    # pre-HOLD short-circuit so both fired and declined ticks generate
    # training rows. Never blocks the trade — fail-silent discipline.
    try:
        from services.sovereign_ai_core import SovereignFeatures
        from services.sovereign_prd_adapter import (
            apply_promoted_sovereign_contribution,
            run_prd_sovereign_shadow,
        )

        _indicators = (signal.get("strategist") or {}).get("indicators", {}) or {}
        # P0 (2026-02-26) — capture entry price so the drift resolver
        # can score the decision via market drift when no paper trade
        # fires (HOLD or gated-out direction). Pulls from the signal's
        # ``current_price`` / ``price`` (set upstream by the strategist),
        # falling back to None for unsourced ticks.
        _entry_price_crypto = (
            signal.get("current_price")
            or signal.get("price")
            or _indicators.get("price")
        )
        try:
            _entry_price_crypto = (
                float(_entry_price_crypto)
                if _entry_price_crypto is not None else None
            )
            if _entry_price_crypto is not None and _entry_price_crypto <= 0:
                _entry_price_crypto = None
        except (TypeError, ValueError):
            _entry_price_crypto = None
        _crypto_features = SovereignFeatures(
            symbol=symbol.upper(),
            asset_type="crypto",
            rsi=_indicators.get("rsi"),
            momentum_5b=_indicators.get("momentum_5b"),
            atr_pct=_indicators.get("atr_pct"),
            volume_zscore=_indicators.get("volume_zscore"),
            regime=signal.get("regime"),
            strategist_action=None,
            strategist_confidence=float(signal.get("confidence") or 0.0),
            entry_price=_entry_price_crypto,
        )
        _advisory_crypto = {
            "strategist": {
                "direction": signal.get("direction"),
                "confidence": float(signal.get("confidence") or 0.0),
            },
        }
        if adv_decision is not None:
            _advisory_crypto["commander"] = {
                "decision": adv_decision.get("decision"),
                "phase": adv_decision.get("phase"),
                "confidence": adv_decision.get("commander_confidence"),
            }

        await run_prd_sovereign_shadow(
            db, symbol.upper(),
            asset_type="crypto",
            features=_crypto_features,
            advisory_votes=_advisory_crypto,
        )

        _prod_action = str(signal.get("direction") or "HOLD").upper()
        _adj_conf, _sov_meta = await apply_promoted_sovereign_contribution(
            db,
            symbol=symbol.upper(),
            asset_type="crypto",
            production_action=_prod_action,
            production_confidence=float(signal.get("confidence") or 0.0),
        )
        # CRITICAL: confidence-only mutation. Direction stays whatever the
        # strategist / adversarial layer already resolved.
        signal["confidence"] = float(_adj_conf)
        signal["sovereign_contribution"] = _sov_meta
    except Exception as _sov_exc:  # noqa: BLE001
        logger.debug("[crypto-bot] sovereign shadow failed for %s: %s", symbol, _sov_exc)

    # ── Final HOLD short-circuit ──────────────────────────────────────────
    # Either the strategist said HOLD and no `full`-phase override
    # triggered, OR a veto-phase Commander blocked the fill.
    if final_direction == "HOLD":
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {
            "symbol": symbol,
            "skipped": True,
            "reason": adversarial_action or signal.get("reason") or "hold",
            "signal": signal,
            "adversarial_action": adversarial_action,
            "adversarial_decision_id": adversarial_decision_id,
        }

    # Mutate the signal so the trade row + downstream audit reflect the
    # ACTUAL fired direction (which may differ from strategist's pick
    # in `full` phase).
    signal["direction"] = final_direction

    # When `full` phase promotes a strategist HOLD into a LONG (the
    # override-triggers-entry case), strategist confidence is 0.0 —
    # which would zero out the position size below. Floor confidence
    # to MIN_CRYPTO_CONFIDENCE so the sizing math is well-defined.
    # The actual size is still scaled down by Commander's
    # ``risk_multiplier`` via ``size_multiplier``, so a low-conviction
    # full_trigger still produces a smaller position than a confluence
    # buy. The trade row records ``adversarial_action="full_trigger"``
    # so post-hoc analysis can attribute these fills correctly.
    if adversarial_action == "full_trigger":
        signal["confidence"] = max(
            float(signal.get("confidence") or 0.0),
            MIN_CRYPTO_CONFIDENCE,
        )

    # Live quote (crypto-only path — never touches equity get_quote).
    quote = await quote_provider(symbol)
    # Realistic fill price — when bid/ask flow through (Kraken
    # primary), LONGs fill at ask + SHORTs fill at bid. Falls back
    # to mid for legacy providers via the slippage module's
    # ``mid_only`` path. Stamped onto the trade row for autopsy.
    from services.slippage_simulator import apply_entry_slippage
    _slip = apply_entry_slippage(quote, signal["direction"])
    entry_price = _slip.fill_price
    if entry_price <= 0:
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {"symbol": symbol, "skipped": True, "reason": "quote_unavailable"}

    size_usd = compute_crypto_position_size(float(signal["confidence"]))
    # Adversarial risk_only / veto-with-confidence phase: scale the
    # position by Commander's conviction gap. multiplier=1.0 in shadow
    # phase preserves identical behaviour to the pre-adversarial code.
    size_usd = round(size_usd * size_multiplier, 2)

    # ── Integrity Mitigation (self-defense layer) ────────────────
    # When a data-integrity alert rule has fired with a mitigation
    # spec, every subsequent paper trade is scaled down (or strong
    # signals are fully suppressed) until the mitigation's TTL
    # elapses. This is the final protection layer of the data-
    # integrity loop: detect drift → alert → activate mitigation →
    # reduce risk → auto-expire → audit trail. Failure of the
    # mitigation read NEVER blocks a trade; a logging hiccup can't
    # paralyse the fleet.
    try:
        from services.integrity_mitigation_service import (
            get_effective_integrity_risk_multiplier,
            should_suppress_strong_signals,
        )
        from services.prediction_tracker import canonical_ai_dir as _canon
        _mitig_multiplier = await get_effective_integrity_risk_multiplier(db)
        _suppress_strong = await should_suppress_strong_signals(db)

        # Strong-signal suppression: a signal is "strong" if its raw
        # direction token is STRONG_BUY/STRONG_SELL OR confidence
        # clears the 0.90 threshold (the bot's working definition of
        # high-conviction). Downgrading to HOLD prevents amplifying
        # conviction during a period where the data is suspect.
        _raw_dir = str(signal.get("direction", "")).upper()
        _is_strong_dir = _raw_dir in ("STRONG_BUY", "STRONG_SELL")
        _is_strong_conf = float(signal.get("confidence") or 0.0) >= 0.90
        if _suppress_strong and (_is_strong_dir or _is_strong_conf) and _canon(_raw_dir) in ("LONG", "SHORT"):
            logger.warning(
                "[integrity_mitigation] SUPPRESSED strong signal symbol=%s "
                "direction=%s confidence=%.2f",
                symbol, _raw_dir, float(signal.get("confidence") or 0.0),
            )
            await log_adversarial_decision(
                db, symbol=symbol, signal=signal, final_direction="HOLD",
            )
            return {
                "symbol": symbol, "skipped": True,
                "reason": "integrity_suppress_strong",
                "mitigation_multiplier": _mitig_multiplier,
            }

        # Multiplier clamp — applied after the strategist + adversarial
        # sizing so degrade mode is visible in the final notional.
        if _mitig_multiplier < 1.0:
            size_usd = round(size_usd * _mitig_multiplier, 2)
            logger.info(
                "[integrity_mitigation] size scaled symbol=%s multiplier=%.2f size_usd=%.2f",
                symbol, _mitig_multiplier, size_usd,
            )
    except Exception as e:  # noqa: BLE001 — never block a trade on mitigation lookup
        logger.warning("[integrity_mitigation] lookup failed, proceeding un-mitigated: %s", e)

    # ── Patent J/K/M/I — Decision Pipeline Guard ─────────────────────
    # Full guard: Patent K (adversarial enforcement), Patent M
    # (failure-mode classification), Patent I (authority-scoped risk
    # budgeting), all proof-chained via Patent J. Behind a feature
    # flag — defaults ON in prod, off in tests so legacy unit tests
    # that pin exact pre-guard sizes don't regress.
    #
    # Best-effort telemetry:
    # * bull/bear/commander synthesised from the existing adversarial
    #   decision (``adv_decision``); fallback to single-signal mapping
    #   when the adversarial layer is gated off.
    # * market.atr_pct from strategist indicators; baselines + news
    #   telemetry default to 0.0 (those failure-mode branches stay
    #   dormant until baseline trackers ship).
    # * model loss_streak / drawdown reuse Patent I's track record.
    import os as _os_guard
    # Captured outside the try block so the trade-doc build below can
    # persist it — closer needs this id to append the
    # OUTCOME_VERIFIED block to the same proof chain.
    proof_chain_entity_id: Optional[str] = None
    if (_os_guard.environ.get("PATENT_GUARD_ENABLED", "1") or "").lower() not in ("0", "false", ""):
        try:
            from services.adversarial_enforcer import (
                AgentDecision, CommanderDecision,
            )
            from services.failure_mode_classifier import (
                MarketTelemetry, ModelTelemetry,
            )
            from services.proof_chain import AsyncMongoProofChainStore
            from services.risedual_ip_logic import (
                CandidateSignal,
                EnforcementPolicy,
                IPDecisionContext,
                run_risedual_ip_decision,
            )
            from services.risk_budget_gateway import (
                build_track_record_for_crypto_bot,
                get_daily_realized_loss,
                mint_authority_for_crypto_bot,
            )
            from services.authority_risk_budget import RiskBudgetRequest

            authority = mint_authority_for_crypto_bot()
            track = await build_track_record_for_crypto_bot()
            daily_loss = await get_daily_realized_loss(asset_class="crypto")

            # Synthesize Bull / Bear / Commander.
            # The adversarial layer (when active) emits structured
            # bull/bear/commander conviction. When gated off we
            # synthesise a single-sided agent pair from the strategist
            # so the guard has SOMETHING to enforce — Patent K's
            # low-dissent rule will then naturally hold the trade
            # unless the strategist is highly confident.
            #
            # Mapping rule: ``signal["confidence"]`` is the conviction
            # in the PROPOSED direction. So when direction=LONG the
            # bull is the high-conviction side; when direction=SHORT
            # the bear is the high-conviction side. Reversing this
            # would make K read every directional trade as
            # ``hold_not_promoted`` (Bull holds with high conviction).
            _signal_dir = str(signal.get("direction", "HOLD")).upper()
            _signal_conf = float(signal.get("confidence") or 0.0)
            # Centralised canonicalisation — pre-fix the local tuples
            # ``("LONG", "BUY")`` / ``("SHORT", "SELL")`` silently
            # excluded STRONG_BUY/WEAK_BUY/BULLISH/UP and their bearish
            # counterparts, mis-routing every adversarial pipeline run
            # for those verdicts. See services.prediction_tracker.
            from services.prediction_tracker import canonical_ai_dir
            _canon_dir = canonical_ai_dir(_signal_dir)
            _is_long = _canon_dir == "LONG"
            _is_short = _canon_dir == "SHORT"
            if adv_decision:
                _adv_bull_conf = float(adv_decision.get("bull_confidence") or 0.0)
                _adv_bear_conf = float(adv_decision.get("bear_confidence") or 0.0)
                # Use real adversarial confidences when present, else
                # fall back to the signal-conviction split.
                if _adv_bull_conf > 0 or _adv_bear_conf > 0:
                    bull_conf = _adv_bull_conf
                    bear_conf = _adv_bear_conf
                else:
                    bull_conf = _signal_conf if _is_long else max(0.0, 1.0 - _signal_conf)
                    bear_conf = _signal_conf if _is_short else max(0.0, 1.0 - _signal_conf)
                _bull_action = "BUY" if _is_long else "HOLD"
                _bear_action = "SELL" if _is_short else "HOLD"
                _commander_action = "BUY" if _is_long else ("SELL" if _is_short else "HOLD")
                bull = AgentDecision(name="bull", action=_bull_action, confidence=bull_conf)
                bear = AgentDecision(name="bear", action=_bear_action, confidence=bear_conf)
                commander = CommanderDecision(
                    action=_commander_action,
                    confidence=float(adv_decision.get("commander_confidence") or _signal_conf),
                    override=bool(adv_decision.get("phase") == "full"),
                    signature_hash=str(adv_decision.get("decision_id") or ""),
                    reason=str(adv_decision.get("decision") or ""),
                )
            else:
                # No adversarial layer active: build a one-sided
                # synthetic so the enforcer can still rule on the trade.
                _bull_action = "BUY" if _is_long else "HOLD"
                _bear_action = "SELL" if _is_short else "HOLD"
                bull_conf = _signal_conf if _is_long else max(0.0, 1.0 - _signal_conf)
                bear_conf = _signal_conf if _is_short else max(0.0, 1.0 - _signal_conf)
                bull = AgentDecision(name="bull", action=_bull_action, confidence=bull_conf)
                bear = AgentDecision(name="bear", action=_bear_action, confidence=bear_conf)
                commander = None

            # Market telemetry — best-effort from strategist indicators.
            # Missing baselines/news/spread default to safe zeros so
            # those failure-mode branches stay dormant rather than
            # firing false positives.
            _indicators = (signal.get("strategist") or {}).get("indicators", {}) or {}
            market_tel = MarketTelemetry(
                symbol=str(symbol),
                asset_type="crypto",
                atr_pct=float(_indicators.get("atr_pct") or 0.0),
                atr_pct_baseline=float(_indicators.get("atr_pct_baseline") or 0.0),
                volume_zscore=float(_indicators.get("volume_zscore") or 0.0),
                spread_bps=float(_indicators.get("spread_bps") or 0.0),
                spread_bps_baseline=float(_indicators.get("spread_bps_baseline") or 0.0),
                news_sentiment_abs=0.0,
                news_volume_zscore=0.0,
                data_missing_ratio=0.0,
            )

            # Model telemetry — pulled from Patent I's track record
            # plus strategist confidence. Calibration/disagreement/
            # entropy default to 0 until real trackers ship.
            model_tel = ModelTelemetry(
                calibration_gap=float(track.calibration_gap or 0.0),
                prediction_entropy=0.0,
                confidence=_signal_conf,
                confidence_baseline=0.65,
                disagreement_score=abs(bull.confidence - bear.confidence),
                recent_error_rate=max(0.0, 1.0 - (track.win_rate or 0.0)),
                loss_streak=int(track.loss_streak or 0),
                max_drawdown=float(track.max_drawdown or 0.0),
            )

            risk_req = RiskBudgetRequest(
                action=_signal_dir if _signal_dir in ("BUY", "SELL") else "BUY",
                base_notional=float(size_usd),
                base_multiplier=float(size_multiplier),
                authority=authority,
                track_record=track,
                daily_realized_loss=daily_loss,
            )

            # Persist proof chain only when DB is available (it is, in
            # production). The store can also be set to None to no-op.
            proof_store = AsyncMongoProofChainStore(db) if db is not None else None

            # Canonical IP contract — single decision-time evaluator.
            # Replaces the older ``run_guarded_decision_pipeline_async``;
            # adds the auditor-calibration veto and explicit
            # authority-validation block to the proof chain. Per-patent
            # enforcement is staged via PATENT_K/M/I_ENFORCE +
            # AUDITOR_ENFORCE + AUTHORITY_ENFORCE env flags (see
            # ``EnforcementPolicy.from_env``).
            ip_signal = CandidateSignal(
                action=_signal_dir if _signal_dir in ("BUY", "SELL") else "HOLD",
                base_notional=float(size_usd),
                base_multiplier=float(size_multiplier),
                bull=bull, bear=bear, commander=commander,
                rolling_accuracy=float(track.win_rate or 0.0),
                calibration_gap=float(track.calibration_gap or 0.0),
            )
            ip_ctx = IPDecisionContext(
                request_id=f"crypto:{symbol}:{int(datetime.now(timezone.utc).timestamp())}",
                actor="crypto_paper_bot",
                asset_class="crypto",
                symbol=str(symbol),
                signal=ip_signal,
                market=market_tel, model=model_tel,
                authority=authority,
                risk_request=risk_req,
                proof_store=proof_store,
                execution_client=None,  # bot does its own DB insert below
                policy=EnforcementPolicy.from_env(),
                dry_run=True,
            )
            guard = await run_risedual_ip_decision(ip_ctx)
            # Translate the contract response to the legacy guard shape
            # the rest of the bot's flow expects.
            guard["allow"] = guard["allowed"]
            guard["reasons"] = guard.get("reasons") or [guard.get("reason") or ""]
            # Capture the contract entity_id so the closer can append a
            # final OUTCOME_VERIFIED block to the same proof chain.
            guard["entity_id"] = ip_ctx.request_id
            proof_chain_entity_id = ip_ctx.request_id

            # ── Shadow mode (Step 5 of the rollout plan) ─────────────
            # When ``GUARD_SHADOW_MODE=1`` the guard runs and writes
            # proof chain + a shadow_log entry, but its verdict is
            # NOT enforced. The bot proceeds with the legacy
            # (pre-guard) size. Lets operators compare "would have
            # blocked" vs "actually executed" for several days before
            # flipping enforcement on.
            from services.guard_shadow_log import (
                is_shadow_mode_enabled,
                log_guard_shadow_decision,
            )
            if is_shadow_mode_enabled():
                await log_guard_shadow_decision(
                    db,
                    source="crypto_bot",
                    entity_id=f"crypto:{symbol}",
                    guard_result=guard,
                    executed_action=_signal_dir,
                    executed_notional=float(size_usd),
                    context={
                        "asset_class": "crypto",
                        "symbol": str(symbol),
                        "phase": "entry",
                    },
                )
                # Fall through to legacy execution with pre-guard size.
            elif not guard["allow"]:
                await log_adversarial_decision(
                    db, symbol=symbol, signal=signal, final_direction="HOLD",
                )
                return {
                    "symbol": symbol, "skipped": True,
                    "reason": f"guard:{','.join(guard['reasons'][:3])}",
                    "proof_hashes": guard["proof_hashes"],
                }
            else:
                # Guard approved — its final notional supersedes ours.
                size_usd = round(float(guard["notional"]), 2)
        except Exception as e:  # noqa: BLE001 — fail-open is unsafe
            # but we MUST NOT crash the live bot. Log and proceed
            # with the pre-guard size. The Patent-I-only legacy
            # path is preserved as a safety net.
            logger.warning("[patent_guard] pipeline unavailable, falling back: %s", e)

    # ── Patent I — Adaptive Authority-Scoped Risk Budgeting (legacy) ─
    # Kept as a fallback for the case where PATENT_GUARD_ENABLED=0
    # but PATENT_I_ENABLED=1 (audit migration window). Once the full
    # guard is the default everywhere, this block can be deleted.
    if (_os_guard.environ.get("PATENT_GUARD_ENABLED", "1") or "").lower() in ("0", "false", "") \
            and (_os_guard.environ.get("PATENT_I_ENABLED", "1") or "").lower() not in ("0", "false", ""):
        try:
            from services.risk_budget_gateway import (
                build_track_record_for_crypto_bot,
                enforce_budget,
                get_daily_realized_loss,
                mint_authority_for_crypto_bot,
            )
            authority = mint_authority_for_crypto_bot()
            track = await build_track_record_for_crypto_bot()
            daily_loss = await get_daily_realized_loss(asset_class="crypto")
            decision = await enforce_budget(
                action=str(signal["direction"]).upper(),
                base_notional=float(size_usd),
                base_multiplier=float(size_multiplier),
                authority=authority,
                track_record=track,
                daily_realized_loss=daily_loss,
                context={"symbol": symbol, "signal_confidence": float(signal["confidence"])},
            )
            if not decision.allowed:
                await log_adversarial_decision(
                    db, symbol=symbol, signal=signal, final_direction="HOLD",
                )
                return {
                    "symbol": symbol, "skipped": True,
                    "reason": f"patent_i:{','.join(decision.reasons[:3])}",
                    "audit_hash": decision.audit_hash,
                }
            size_usd = round(decision.final_notional, 2)
        except Exception as e:  # noqa: BLE001
            logger.warning("[patent_i] gateway unavailable, falling back: %s", e)

    if size_usd <= 0:
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {"symbol": symbol, "skipped": True, "reason": "size_zero"}

    quantity = round(size_usd / entry_price, 8)
    stops = build_stop_take_profit(entry_price, signal["direction"])

    strategist = signal.get("strategist", {}) or {}
    auditor = signal.get("auditor", {}) or {}
    indicators = strategist.get("indicators", {}) or {}

    now = datetime.now(timezone.utc)

    trade = {
        "trade_id": str(uuid4()),
        "asset_class": "crypto",
        "symbol": symbol,
        "pair": f"{symbol}/USD",
        "direction": signal["direction"],
        "confidence": float(signal["confidence"]),
        "entry_price": entry_price,
        "quantity": quantity,
        "size_usd": size_usd,
        "status": "open",
        "opened_at": now,
        # ``opened_day`` (UTC date string) — backs the
        # /api/crypto/tier3-contribution distinct-day count without
        # needing a date-trunc aggregation pipeline.
        "opened_day": now.date().isoformat(),
        "source": "crypto_paper_bot",

        "stop_loss": stops["stop_loss"],
        "take_profit": stops["take_profit"],

        # Slippage audit — what the spread cost us at entry.
        # ``slippage_method`` ∈ ask_fill / bid_fill / mid_only / unknown
        # (see services/slippage_simulator.py).
        "entry_quote_bid": _slip.bid,
        "entry_quote_ask": _slip.ask,
        "entry_quote_mid": _slip.mid,
        "slippage_bps": _slip.slippage_bps,
        "slippage_method": _slip.method,
        "quote_source": (quote or {}).get("source"),

        # Top-level snapshot fields — denormalised so the closer +
        # memory writer + adaptation queries don't need nested-doc
        # paths.
        "rsi": indicators.get("rsi"),
        "ema20": indicators.get("ema20"),
        "momentum_5b": indicators.get("momentum_5b"),
        "volume_ratio": signal.get("volume_ratio"),
        "regime": signal.get("regime"),

        "strategist_conf": strategist.get("confidence"),
        "auditor_conf": auditor.get("confidence"),
        "strategist_reason": strategist.get("reason"),
        "auditor_reason": auditor.get("reason"),

        "crypto_adaptations_applied": signal.get(
            "crypto_adaptations_applied", []
        ),

        # SHADOW ONLY — Tavily + LLM narrative verdict attached at fill
        # time. Persisted on the trade row so post-hoc analysis can
        # correlate exit P&L with the LLM's stance/agreement at entry.
        "web_research_shadow_verdict": signal.get("web_research_shadow_verdict"),

        # SHADOW ONLY — Adversarial (Bull/Bear/Commander) decision_id.
        # Populated only when both gates open (CRYPTO_ADVERSARIAL_ENABLED=1
        # AND ML Tier 3 unlocked). The closer reads this on close to
        # patch ``crypto_adversarial_decision_log`` with final R-multiple
        # so Bull/Bear winner attribution can be computed.
        "adversarial_decision_id": adversarial_decision_id,

        # IP contract entity_id — the same hash-chain anchor used by
        # the entry-side proof events (ADVERSARIAL_DECISION,
        # AUDITOR_VERDICT, AUTHORITY_VALIDATED, FAILURE_MODE_CLASSIFIED,
        # RISK_BUDGET_APPLIED, EXECUTION_ATTEMPTED). The closer reads
        # this on close to append OUTCOME_VERIFIED, completing the
        # proposal-through-realized-P&L story in one chain.
        "proof_chain_entity_id": proof_chain_entity_id,

        # Adversarial action that produced this fill — None for plain
        # confluence buys, "full_trigger" for HOLD-promoted-to-LONG
        # (override-triggers-entry), "full_override" if Commander
        # flipped a non-LONG direction. Lets post-hoc analysis bucket
        # full-phase entries separately from confluence entries.
        "adversarial_action": adversarial_action,

        # agent_agreement block — top-level for direct admin queries
        # without nested lookup.
        "agent_agreement": {
            "strategist_direction": strategist.get("direction"),
            "strategist_confidence": strategist.get("confidence"),
            "auditor_verdict": auditor.get("verdict"),
            "auditor_confidence": auditor.get("confidence"),
            "combined_confidence": signal.get("confidence"),
        },

        "metadata": {
            "lane": "crypto",
            "bot_version": "crypto_v3",
            "agent_agreement": {
                "strategist_direction": strategist.get("direction"),
                "auditor_verdict": auditor.get("verdict"),
                "combined_confidence": signal.get("confidence"),
            },
        },
    }

    # ── Decision reasoning overlay (READ-ONLY) ───────────────────────
    # Stamp the rich gate-trace overlay onto the crypto trade row.
    # The adapter pulls Commander vote + sovereign contribution +
    # failure-penalty + adversarial-action into reason codes the
    # operator can scan at a glance (COMMANDER_DISAGREES,
    # INTEGRITY_MITIGATION_ACTIVE, SMALL_SAMPLE_DISCOUNT, etc.).
    # Pure function; trade row is fully composed by this point and
    # the overlay never touches direction/confidence/sizing. Failure
    # mode: log + skip — the trade still inserts.
    try:
        from services.decision_reasoning_overlay import (
            build_crypto_paper_trade_decision_view, build_reasoning_overlay,
        )
        decision_view = build_crypto_paper_trade_decision_view(
            trade, signal=signal,
        )
        trade["reasoning"] = build_reasoning_overlay(decision_view)
    except Exception as _reason_exc:  # noqa: BLE001
        logger.debug(
            "[crypto_paper] reasoning overlay skipped for %s: %s",
            symbol, _reason_exc,
        )

    # Operator trading gate — single doctrine rule.
    try:
        from services.operator_trading_gate import gate_or_synthetic
        if not await gate_or_synthetic(
            db,
            lane="crypto_paper",
            symbol=symbol,
            intended_decision=str(trade.get("direction") or trade.get("side") or "").upper(),
            confidence=float(trade.get("confidence") or 0.0),
            extras={
                "qty": trade.get("size"),
                "entry_price": trade.get("entry_price"),
            },
        ):
            return {
                "symbol": symbol,
                "skipped": True,
                "reason": "paused_by_operator",
            }
    except Exception:  # noqa: BLE001
        pass

    try:
        await db.crypto_paper_trades.insert_one(trade)
        # Strip Mongo-injected _id (ObjectId not JSON-serializable).
        # Defensive — the function returns a hand-built sub-dict so
        # there's no current leak, but this prevents a future
        # reuse-of-doc bug in any callers that consume `trade` after
        # this point.
        trade.pop("_id", None)
    except Exception as exc:  # noqa: BLE001
        logger.error("[crypto-bot] insert failed for %s: %s", symbol, exc)
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {"symbol": symbol, "skipped": True, "reason": "db_write_failed"}

    await log_adversarial_decision(
        db, symbol=symbol, signal=signal, final_direction=signal["direction"],
    )

    logger.info(
        "[crypto-bot] opened %s %s qty=%.8f @ $%.2f size=$%.2f conf=%.3f",
        symbol, signal["direction"], quantity, entry_price, size_usd,
        signal["confidence"],
    )

    return {
        "symbol": symbol,
        "opened": True,
        "trade_id": trade["trade_id"],
        "direction": trade["direction"],
        "confidence": trade["confidence"],
        "size_usd": trade["size_usd"],
        "entry_price": entry_price,
        "stop_loss": stops["stop_loss"],
        "take_profit": stops["take_profit"],
        "adversarial_action": adversarial_action,
        "adversarial_decision_id": adversarial_decision_id,
    }


async def run_crypto_paper_bot(
    db: Any,
    quote_provider: QuoteProvider,
    history_provider: HistoryProvider,
    symbols: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Multi-symbol crypto paper bot runner.

    One pass through ``symbols`` (default ``["BTC", "ETH", "SOL"]``).
    Each symbol failure is captured into ``errors`` so the scheduler
    tick never crashes on a single-symbol fault.
    """
    syms = symbols or CRYPTO_SYMBOLS

    opened: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for symbol in syms:
        try:
            bars = await history_provider(symbol)
            result = await run_crypto_symbol(
                db=db,
                symbol=symbol,
                bars=bars,
                quote_provider=quote_provider,
            )
            if result.get("opened"):
                opened.append(result)
            else:
                skipped.append(result)
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "symbol": symbol,
                "error": str(exc),
                "type": type(exc).__name__,
            })

    return {
        "opened": opened,
        "skipped": skipped,
        "errors": errors,
        "opened_count": len(opened),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }
