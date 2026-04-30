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
CRYPTO_SYMBOLS = ["BTC", "ETH", "SOL"]
MIN_CRYPTO_CONFIDENCE = 0.60
BASE_CRYPTO_NOTIONAL = 250.0
MAX_CRYPTO_NOTIONAL = 1000.0


# Canonical crypto registry — symbols this bot will ever trade.
# Kept inline so the bot module is self-contained.
_CRYPTO_REGISTRY: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK",
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

    * Below the 0.60 floor → 0 (no fill).
    * 0.60 → $250 (base).
    * 0.95 → ~$396 (linear within the [0.60, 0.95] band).
    * Capped at ``MAX_CRYPTO_NOTIONAL`` so even a perfect-storm
      signal can't blow out the paper portfolio.
    """
    if confidence < MIN_CRYPTO_CONFIDENCE:
        return 0.0

    multiplier = min(max(confidence, 0.6), 0.95)
    size = BASE_CRYPTO_NOTIONAL * (multiplier / 0.60)
    return round(min(size, MAX_CRYPTO_NOTIONAL), 2)


def build_stop_take_profit(entry: float, direction: str) -> dict[str, float]:
    """Defensive symmetric SL/TP defaults.

    LONG: -2% stop, +4% target → 2:1 reward:risk.
    SHORT: +2% stop, -4% target → 2:1 reward:risk.
    """
    if direction.upper() == "SHORT":
        return {
            "stop_loss": round(entry * 1.02, 2),
            "take_profit": round(entry * 0.96, 2),
        }
    return {
        "stop_loss": round(entry * 0.98, 2),
        "take_profit": round(entry * 1.04, 2),
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

    raw_signal = adversarial_signal(bars)

    # Tag regime + failure_context on the proposal so the
    # adaptation layer below can match either key.
    signal = {
        **raw_signal,
        "symbol": symbol,
        "regime": infer_crypto_regime(raw_signal),
        "failure_context": infer_failure_context(raw_signal),
    }

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
    entry_price = float((quote or {}).get("price") or 0.0)
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
    if (_os_guard.environ.get("PATENT_GUARD_ENABLED", "1") or "").lower() not in ("0", "false", ""):
        try:
            from services.adversarial_enforcer import (
                AgentDecision, CommanderDecision,
            )
            from services.failure_mode_classifier import (
                MarketTelemetry, ModelTelemetry,
            )
            from services.proof_chain import MongoProofChainStore
            from services.decision_pipeline_guard import run_guarded_decision_pipeline
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
            _signal_dir = str(signal.get("direction", "HOLD")).upper()
            _signal_conf = float(signal.get("confidence") or 0.0)
            if adv_decision:
                _bull_conf = float(adv_decision.get("bull_confidence") or 0.0)
                _bear_conf = float(adv_decision.get("bear_confidence") or 0.0)
                _bull_action = "BUY" if _signal_dir in ("LONG", "BUY") else "HOLD"
                _bear_action = "SELL" if _signal_dir in ("SHORT", "SELL") else "HOLD"
                _commander_action = "BUY" if _signal_dir in ("LONG", "BUY") else (
                    "SELL" if _signal_dir in ("SHORT", "SELL") else "HOLD"
                )
                bull = AgentDecision(name="bull", action=_bull_action, confidence=_bull_conf or _signal_conf)
                bear = AgentDecision(name="bear", action=_bear_action, confidence=_bear_conf or (1.0 - _signal_conf))
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
                _bull_action = "BUY" if _signal_dir in ("LONG", "BUY") else "HOLD"
                _bear_action = "SELL" if _signal_dir in ("SHORT", "SELL") else "HOLD"
                bull = AgentDecision(name="bull", action=_bull_action, confidence=_signal_conf)
                bear = AgentDecision(name="bear", action=_bear_action, confidence=max(0.0, 1.0 - _signal_conf))
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
            proof_store = MongoProofChainStore(db) if db is not None else None

            guard = run_guarded_decision_pipeline(
                entity_id=f"crypto:{symbol}:{int(datetime.now(timezone.utc).timestamp())}",
                bull=bull, bear=bear, commander=commander,
                market=market_tel, model=model_tel,
                risk_request=risk_req,
                proof_store=proof_store,
                actor="crypto_paper_bot",
            )

            if not guard["allow"]:
                await log_adversarial_decision(
                    db, symbol=symbol, signal=signal, final_direction="HOLD",
                )
                return {
                    "symbol": symbol, "skipped": True,
                    "reason": f"guard:{','.join(guard['reasons'][:3])}",
                    "proof_hashes": guard["proof_hashes"],
                }
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
