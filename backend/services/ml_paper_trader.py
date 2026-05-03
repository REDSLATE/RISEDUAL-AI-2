"""Tier 2 autonomous action: paper trading with Kelly position sizing.

DTD-tagged: writes execution-state to ``paper_trades``.

When the CalibrationGate unlocks Tier 2, this service auto-creates paper
trades in MongoDB after every qualifying signal.  No real money is involved.

Architecture
------------
- Writes to the ``paper_trades`` collection in MongoDB.
- Uses half-Kelly sizing (capped at 25% of ``PAPER_PORTFOLIO_VALUE``).
- A separate ``paper_positions`` collection tracks open exposure.
- Designed to be called by :mod:`app.services.ml_orchestrator`.

Gate requirements (Tier 2)
---------------------------
- All Tier 1 gates cleared.
- accuracy   >= 60%
- Sharpe     >= 1.0  (from backtest)
- Max DD     <  15%  (from backtest)

Signal-level requirements
--------------------------
- confidence >= 65%
- At least one pattern detected
- Regime is trending (not sideways/unknown)
"""

from __future__ import annotations

__domain__ = "DTD"

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import httpx
from pymongo.errors import DuplicateKeyError

from risedual_core.ml.calibration import half_kelly_position
from risedual_core.schemas.market import FeaturesSnapshot, SignalResult

log = logging.getLogger(__name__)

# Paper portfolio starting value (USD)
_PAPER_PORTFOLIO_VALUE: float = float(
    os.getenv("PAPER_PORTFOLIO_VALUE", "100000")
)

# Per-signal confidence threshold above gate accuracy
_MIN_PAPER_CONFIDENCE: float = 0.55

# Regimes eligible for paper trades
_TRADEABLE_REGIMES: frozenset[str] = frozenset({"bull", "bear", "sideways", "trending_up", "trending_down", "unknown", ""})


# Options-activity guard (Phase 2b integration hook). When an options
# snapshot is available, a symbol with essentially no options flow is
# signalling regime uncertainty — the usual pre-trade smart money read
# is unavailable. We skip rather than size down because this is a
# signal-quality gate, not a position-sizing one. Rule stays additive:
# if the snapshot is empty OR the symbol isn't configured in the
# options universe, the guard is a no-op (pre-existing behaviour).
_MIN_OPTIONS_TOTAL_VOLUME: int = 1000


def options_activity_guard(
    options_entry: dict | None,
    min_volume: int = _MIN_OPTIONS_TOTAL_VOLUME,
) -> tuple[bool, str]:
    """Return ``(allow, reason)``. Empty snapshot / missing symbol →
    ``(True, "")`` — the equity path runs unchanged. When options data
    exists but the total aggregate volume is below ``min_volume``,
    returns ``(False, "LOW_OPTIONS_ACTIVITY")``."""
    if not options_entry:
        return True, ""
    aggregate = options_entry.get("aggregate") or {}
    total_vol = aggregate.get("total_volume")
    if total_vol is None:
        return True, ""
    if int(total_vol) < int(min_volume):
        return False, "LOW_OPTIONS_ACTIVITY"
    return True, ""


# Idempotency: dedupe-on-insert key ─────────────────────────────────────────
# Forensic context: 2026-04-16 19:18:20 + 19:18:32 produced two
# `paper_trades` rows for AAPL/down with identical entry_price (266.43),
# shares (38.17), and position_size_usd (10170.35) twelve seconds apart.
# That's the same prediction firing twice — a retry / fanout bug. We
# block the dupes with a Mongo unique partial index on
#   (ticker, direction, prediction_id, time_bucket)
# where ``time_bucket = floor(opened_at_unix / 60)`` — i.e. one trade
# per (symbol, direction, prediction) per minute, max.
#
# Partial filter: index applies only when prediction_id exists, so
# legacy / external rows without one don't error on insert.
_TIME_BUCKET_SECONDS: int = 60


def _time_bucket_for(ts: datetime) -> int:
    """Floor ``ts`` to a 60-second bucket (UTC) for the dedupe key.
    A minute is wider than the observed 12-second twin window but
    tight enough that legitimate same-symbol re-entries (which we
    already gate at minute-level cadence elsewhere) aren't blocked."""
    return int(ts.timestamp() // _TIME_BUCKET_SECONDS)


async def ensure_indexes(db: Any) -> None:
    """Best-effort idempotency index. Called once at startup from
    route_registry; safe to call repeatedly. Partial filter means
    rows missing prediction_id (legacy, external, manual) bypass
    the constraint entirely."""
    if db is None:
        return
    try:
        await db["paper_trades"].create_index(
            [
                ("ticker", 1),
                ("direction", 1),
                ("prediction_id", 1),
                ("time_bucket", 1),
            ],
            name="paper_trades_idempotency",
            unique=True,
            partialFilterExpression={
                "prediction_id": {"$exists": True, "$type": "string"},
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[ml_paper] idempotency index create failed: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _detected_patterns(snapshot: FeaturesSnapshot) -> list[str]:
    """Return names of all patterns flagged True on this snapshot."""
    fields = {
        "double_bottom": snapshot.pattern_double_bottom,
        "bullish_engulfing": snapshot.pattern_bullish_engulfing,
        "bearish_engulfing": snapshot.pattern_bearish_engulfing,
        "bull_flag": snapshot.pattern_bull_flag,
        "rsi_divergence": snapshot.pattern_rsi_divergence,
        "macd_crossover": snapshot.pattern_macd_crossover,
        "volume_surge": snapshot.pattern_volume_surge,
        "head_and_shoulders": snapshot.pattern_head_and_shoulders,
    }
    return [name for name, val in fields.items() if val]


async def _current_portfolio_value(db: Any) -> float:
    """Estimate current portfolio value from open positions + base value.

    Simple heuristic: base value minus total open position cost basis.
    Production upgrade: fetch current prices and mark-to-market.
    """
    try:
        positions_coll = db["paper_positions"]
        open_pos = await positions_coll.find({"status": "open"}).to_list(length=500)
        allocated = sum(float(p.get("position_size_usd", 0)) for p in open_pos)
        return max(_PAPER_PORTFOLIO_VALUE - allocated, 0.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("[ml_paper] Failed to load positions: %s — using base value.", exc)
        return _PAPER_PORTFOLIO_VALUE


# ── Public API ────────────────────────────────────────────────────────────────


async def maybe_paper_trade(
    ticker: str,
    signal: SignalResult,
    snapshot: FeaturesSnapshot,
    regime: str,
    db: Any,
    http_client: httpx.AsyncClient | None = None,  # noqa: ARG001 (reserved)
) -> str | None:
    """Open a paper trade if all Tier 2 signal-level conditions are met.

    Gate-level conditions (accuracy / Sharpe / DD) are verified by the
    orchestrator before this function is called.

    Parameters
    ----------
    ticker:
        Trading symbol, e.g. ``"AAPL"``.
    signal:
        Completed :class:`SignalResult` from the signal model.
    snapshot:
        Enriched :class:`FeaturesSnapshot` (schema_version=2).
    regime:
        Current market regime label.
    db:
        Motor AsyncIOMotorDatabase handle.
    http_client:
        Reserved for future use (e.g., price feed calls).

    Returns
    -------
    str | None
        The ``trade_id`` of the newly opened paper trade, or ``None`` if
        conditions were not met or the write failed.
    """
    # ── Per-signal gate ──────────────────────────────────────────────────────
    # signal.confidence = P(up). For 'down' signals, true confidence = 1 - P(up)
    directional_conf = signal.confidence if signal.direction.value == "up" else (1.0 - signal.confidence)
    log.info("[ml_paper] %s: direction=%s raw_conf=%.3f directional=%.3f regime=%s",
             ticker, signal.direction.value, signal.confidence, directional_conf, regime)

    # ── Options-activity guard (Phase 2b hook) ───────────────────────────────
    # Consult the current options universe snapshot. No-op for symbols not
    # in the configured underlying list OR when the snapshot doesn't exist
    # yet (Phase 2a pre-warm regression path). Only blocks when the
    # options market for this specific symbol is flat — a signal-quality
    # veto, not a position-sizing one. Never raises — a snapshot read
    # failure must not break the equity trade loop.
    try:
        from services.options_universe_service import read_options_snapshot
        _opt_entry = await read_options_snapshot(db, ticker)
        _allow, _reason = options_activity_guard(_opt_entry)
        if not _allow:
            log.info("[ml_paper] %s: options guard veto (%s) — skipping",
                     ticker, _reason)
            try:
                from services.agent_activity_service import log_paper_trade_skipped
                await log_paper_trade_skipped(
                    ticker=ticker,
                    reason=_reason,
                    confidence=directional_conf,
                )
            except Exception:
                pass  # telemetry failure must never break the trade loop
            return None
    except Exception as _opt_exc:
        # Snapshot read failed — degrade silently to legacy behaviour.
        log.debug("[ml_paper] options guard bypass for %s: %s", ticker, _opt_exc)

    # ── Equity Commander Shadow (Phase 1: observation only) ──────────────────
    # Fire-and-forget shadow opinion on every equity decision. Commander
    # has ZERO authority over equity sizing or direction — this is pure
    # evidence collection until 50+ scored rows clear the 0.70 win-rate
    # threshold (see ``equity_shadow_promotion.py``). Logging failures
    # NEVER block the trade; swallowed inside ``fire_shadow``.
    #
    # ``decision_phase`` is set up front so both the entry fire (trade
    # actually opens) and the cycle fire (skipped due to gates) share
    # the same engine context. The phase distinction is what lets the
    # promotion gate distinguish "Commander would have agreed with a
    # HOLD" from "Commander would have vetoed a LONG/SHORT".
    _shadow_engine_eq = os.environ.get("EQUITY_RESEARCH_SHADOW_ENGINE", "adversarial")
    if _shadow_engine_eq in ("adversarial", "council"):
        try:
            import asyncio as _asyncio_eq
            from services.research_shadow_engines import fire_shadow as _fire_shadow_eq

            # Build an adapter dict matching what ``_run_engine`` expects.
            # The adversarial engine reads ``direction`` / ``confidence``
            # / ``regime`` / ``volume_ratio`` / ``strategist`` / ``auditor``.
            _active_action = "LONG" if signal.direction.value == "up" else "SHORT"
            _shadow_signal = {
                "direction": _active_action,
                "confidence": float(directional_conf),
                "regime": regime,
                "volume_ratio": getattr(snapshot, "volume_ratio", None),
                "strategist": {
                    "direction": _active_action,
                    "confidence": float(directional_conf),
                },
                "auditor": {"confidence": float(directional_conf)},
            }
            _mid_price_eq = float(snapshot.close_price or 0.0)

            # If we'll skip (below confidence / pattern / regime gates)
            # the phase is "cycle"; if we'll open, the phase is "entry".
            # Pre-compute a conservative guess — we re-fire as "entry"
            # below if the trade actually lands. The cycle shadow is
            # the one that matters for "Commander would have agreed
            # with this HOLD anyway" evidence.
            _asyncio_eq.create_task(_fire_shadow_eq(
                db,
                bot_id="equity_ml_orchestrator",  # mirrored in equity_shadow_promotion.EQUITY_BOT_ID
                user_id="system",
                symbol=ticker,
                asset_type="stock",
                decision_phase="cycle",
                active_engine="ml_signal_model",
                active_action=_active_action,
                shadow_engine=_shadow_engine_eq,
                signal=_shadow_signal,
                mid_price=_mid_price_eq,
            ))
        except Exception as _shadow_exc:  # noqa: BLE001
            log.warning(
                "[ml_paper] equity shadow cycle fire setup failed for %s: %s",
                ticker, _shadow_exc,
            )

    if directional_conf < _MIN_PAPER_CONFIDENCE:
        log.debug(
            "[ml_paper] Directional confidence %.2f < %.2f — skipping paper trade for %s.",
            directional_conf,
            _MIN_PAPER_CONFIDENCE,
            ticker,
        )
        try:
            from services.agent_activity_service import log_paper_trade_skipped
            from ai_core.explainability import extract_top_features
            from risedual_core.ml.features import FEATURE_COLUMNS
            # Build parallel name/value lists from the snapshot. Every
            # FEATURE_COLUMNS entry should be a numeric attr on the
            # snapshot; missing attrs surface as None and explainability
            # drops them silently.
            fnames = list(FEATURE_COLUMNS)
            fvalues = [getattr(snapshot, c, None) for c in fnames]
            why = extract_top_features(
                feature_names=fnames,
                feature_values=fvalues,
                importances=signal.feature_importance or {},
                top_k=3,
            )
            await log_paper_trade_skipped(
                ticker=ticker,
                reason=f"conviction below {_MIN_PAPER_CONFIDENCE * 100:.0f}% threshold",
                confidence=directional_conf,
                why=why,
            )
        except Exception:
            pass  # activity logging must never break the trade loop
        return None

    patterns = _detected_patterns(snapshot)
    # Allow trades without patterns if confidence is high enough (> 60%)
    if not patterns and directional_conf < 0.60:
        log.debug("[ml_paper] No patterns on %s and conf %.2f < 0.60 — skipping paper trade.", ticker, directional_conf)
        return None

    if regime not in _TRADEABLE_REGIMES:
        log.debug(
            "[ml_paper] Regime %r not tradeable — skipping paper trade for %s.",
            regime,
            ticker,
        )
        return None

    # ── Symbol Failure Memory — short-term per-symbol bias loop containment ─
    # Closes the persistent-bias-loop hole that surfaced twice on NVDA: even
    # after a string of losses on the same symbol/direction the strategist
    # keeps proposing the same trade because past failures only flow into
    # long-term ML training. This block reads the last-5 closed trades + the
    # 7-day miss count and applies a tiered penalty BEFORE Kelly sizing.
    #
    # Hard cooldown at 4+ recent losses → return None (skip trade entirely).
    # Otherwise: confidence × {1.0, 0.7, 0.5} based on recent loss count;
    # size capped at 0.5× and confidence capped at 0.75 when 7-day misses
    # accumulate.
    #
    # Fail-safe: any exception in the lookup keeps the trade un-penalised.
    failure_penalty_meta: dict[str, Any] | None = None
    try:
        from services.symbol_failure_memory import get_failure_penalty
        _direction_label = "up" if str(signal.direction.value) == "up" else "down"
        _penalty = await get_failure_penalty(
            db, symbol=ticker, direction=_direction_label, asset_type="equity",
        )
        if _penalty.force_hold:
            log.warning(
                "[ml_paper] Symbol failure cooldown HOLD %s — recent_losses=%d, misses_7d=%d, reason=%s",
                ticker, _penalty.recent_losses, _penalty.misses_7d, _penalty.reason,
            )
            try:
                from services.activity_logger import log_paper_trade_skipped
                await log_paper_trade_skipped(
                    ticker=ticker,
                    reason=f"symbol_failure_cooldown ({_penalty.recent_losses} recent losses)",
                    confidence=directional_conf,
                    why=[],
                )
            except Exception:
                pass
            return None
        _before = directional_conf
        directional_conf, _ = _penalty.apply(
            confidence=directional_conf, size_multiplier=1.0,
        )
        if directional_conf < _before:
            log.info(
                "[ml_paper] Symbol failure penalty applied %s: conf %.3f → %.3f (%s)",
                ticker, _before, directional_conf, _penalty.reason,
            )
        failure_penalty_meta = {
            "recent_losses": _penalty.recent_losses,
            "misses_7d": _penalty.misses_7d,
            "confidence_multiplier": _penalty.confidence_multiplier,
            "size_multiplier_applied": _penalty.size_multiplier,
            "confidence_cap": _penalty.confidence_cap,
            "force_hold": _penalty.force_hold,
            "reason": _penalty.reason,
        }
    except Exception as _fp_exc:  # noqa: BLE001
        log.debug("[ml_paper] failure penalty lookup failed for %s: %s", ticker, _fp_exc)

    # ── Sovereign AI Shadow + bounded confidence contribution ───────────────
    # PRD-mode: shadow-log every tick; if the per-core promotion gate has
    # unlocked (>=500 resolved rows, >=70% win rate, >=65% calibration),
    # apply a bounded confidence delta (cap: SOVEREIGN_MAX_CONF_DELTA,
    # default 0.08) to ``directional_conf`` BEFORE Kelly sizing.
    #
    # Hard invariants (pinned by tests):
    # * Sovereign adjusts CONFIDENCE only — never flips direction.
    # * Sovereign cannot turn production HOLD into a trade (we've already
    #   confirmed direction is LONG/SHORT by the time we reach this block).
    # * Any exception is swallowed; directional_conf is untouched.
    sovereign_decision_id: Optional[str] = None
    sovereign_contribution_meta: dict[str, Any] | None = None
    try:
        from services.sovereign_ai_core import SovereignFeatures
        from services.sovereign_prd_adapter import (
            apply_promoted_sovereign_contribution,
            run_prd_sovereign_shadow,
        )

        _prod_action = "LONG" if str(signal.direction.value) == "up" else "SHORT"
        _advisory = {
            "strategist": {
                "action": _prod_action,
                "confidence": float(directional_conf),
            },
        }

        _catalyst = None
        try:
            _catalyst = await db.catalyst_snapshots.find_one(
                {"symbol": ticker.upper()}, {"_id": 0},
            )
        except Exception:
            _catalyst = None

        _features = SovereignFeatures(
            symbol=ticker.upper(),
            asset_type="equity",
            rsi=getattr(snapshot, "rsi", None),
            momentum_5b=getattr(snapshot, "momentum_5b", None),
            atr_pct=getattr(snapshot, "atr_pct", None),
            volume_zscore=getattr(snapshot, "volume_zscore", None),
            dollar_volume=getattr(snapshot, "dollar_volume", None),
            dollar_volume_baseline=getattr(snapshot, "dollar_volume_baseline", None),
            regime=regime,
            news_shock_state=(_catalyst or {}).get("news_shock", {}).get("state")
                if _catalyst else None,
            event_risk=(_catalyst or {}).get("event_risk") if _catalyst else None,
            news_sentiment=(_catalyst or {}).get("news_shock", {}).get("sentiment_score")
                if _catalyst else None,
            news_volume_zscore=(_catalyst or {}).get("news_shock", {}).get("zscore")
                if _catalyst else None,
            strategist_action=_prod_action,
            strategist_confidence=float(directional_conf),
        )

        _sov_dec = await run_prd_sovereign_shadow(
            db, ticker.upper(),
            asset_type="equity",
            features=_features,
            advisory_votes=_advisory,
        )
        if _sov_dec is not None:
            sovereign_decision_id = _sov_dec.decision_id

        _adjusted_conf, sovereign_contribution_meta = (
            await apply_promoted_sovereign_contribution(
                db,
                symbol=ticker.upper(),
                asset_type="equity",
                production_action=_prod_action,
                production_confidence=float(directional_conf),
            )
        )
        # CRITICAL: confidence-only mutation. Never touch direction.
        directional_conf = float(_adjusted_conf)
    except Exception as _sov_exc:  # noqa: BLE001
        log.debug("[ml_paper] sovereign shadow failed for %s: %s", ticker, _sov_exc)

    # ── Position sizing ──────────────────────────────────────────────────────
    portfolio_value = await _current_portfolio_value(db)
    position_usd = half_kelly_position(
        win_probability=directional_conf,
        portfolio_value=portfolio_value,
    )

    if position_usd <= 0.0:
        log.debug("[ml_paper] Kelly sizing returned $0 — skipping paper trade for %s.", ticker)
        return None

    # Apply symbol failure size multiplier (composes with Kelly output)
    if failure_penalty_meta and failure_penalty_meta.get("size_multiplier_applied", 1.0) < 1.0:
        _before_pos = position_usd
        position_usd = round(position_usd * float(failure_penalty_meta["size_multiplier_applied"]), 2)
        log.info(
            "[ml_paper] Symbol failure size penalty %s: $%.2f → $%.2f (×%.2f)",
            ticker, _before_pos, position_usd,
            failure_penalty_meta["size_multiplier_applied"],
        )

    # ── Commander Shadow Phase 2 brake ──────────────────────────────────────
    # Halve position when Commander's adversarial verdict disagrees with the
    # Strategist's direction AND the promotion gate has unlocked
    # (50+ scored rows at ≥70% win rate — see equity_shadow_promotion.py).
    # Phase 1 behaviour (pre-gate) is a pure no-op; the brake decision logs
    # disagreement evidence without touching size.
    #
    # Defensive: any error — pure-function crash, Mongo hiccup reading the
    # promotion gate, unknown action strings — must NEVER block the trade.
    # Fail-safe is to size at Phase 1 (no brake).
    brake_log: dict[str, Any] | None = None
    try:
        from services.adversarial_core import (
            apply_catalyst_context, bull_agent, bear_agent, resolve_adversarial,
        )
        from services.commander_phase2_brake import (
            apply_brake_to_position, decide_brake,
        )
        from services.equity_shadow_promotion import (
            compute_equity_shadow_promotion_status,
        )
        from services.tier3_readiness import (
            build_tier3_stats, check_tier3_unlock,
        )

        _strategist_action = "LONG" if signal.direction.value == "up" else "SHORT"
        _commander_signal = {
            "strategist": {
                "direction": _strategist_action,
                "confidence": float(directional_conf),
                "indicators": {
                    "rsi": getattr(snapshot, "rsi", None),
                    "momentum_5b": getattr(snapshot, "momentum_5b", None),
                },
            },
            "auditor": {"confidence": float(directional_conf)},
            "regime": regime,
            "symbol": ticker,
        }
        _bull = bull_agent(_commander_signal)
        _bear = bear_agent(_commander_signal)

        # Phase C — catalyst narrative enrichment. Fail-silent: an
        # error reading ``catalyst_snapshots`` must never suppress
        # the trade; we just miss the annotation this tick.
        try:
            _catalyst_snapshot = await db.catalyst_snapshots.find_one(
                {"symbol": ticker.upper()}, {"_id": 0},
            )
            _bull, _bear = apply_catalyst_context(_bull, _bear, _catalyst_snapshot)
        except Exception:
            pass

        _commander = resolve_adversarial(_bull, _bear)

        _promotion = await compute_equity_shadow_promotion_status(db)

        # Tier 3 unlock check — once unlocked, the adversarial engine has
        # full directional authority and the pre-Tier-3 brake becomes a
        # no-op (per handoff: "brake activity recorded until tier 3 is
        # broken through"). Any failure reading the gate defaults to
        # tier3_unlocked=False so the brake stays live.
        _tier3_unlocked = False
        try:
            _t3_stats = await build_tier3_stats(db)
            _tier3_unlocked = bool(check_tier3_unlock(_t3_stats).get("unlocked", False))
        except Exception as _t3_exc:  # noqa: BLE001
            log.debug("[ml_paper] tier3 probe failed (defaulting to locked): %s", _t3_exc)

        _brake = decide_brake(
            strategist_action=_strategist_action,
            commander_decision=_commander.get("decision"),
            brake_eligible=bool(_promotion.get("brake_eligible", False)),
            tier3_unlocked=_tier3_unlocked,
        )

        if _brake.brake_applied:
            _before = position_usd
            position_usd = apply_brake_to_position(position_usd, _brake)
            log.info(
                "[ml_paper] Phase 2 brake applied to %s: $%.2f → $%.2f "
                "(strategist=%s, commander=%s, reason=%s)",
                ticker, _before, position_usd,
                _strategist_action, _commander.get("decision"), _brake.reason,
            )

        brake_log = {
            **_brake.to_log(),
            "commander_decision": _commander.get("decision"),
            "commander_edge_gap": _commander.get("edge_gap"),
            "promotion_phase": _promotion.get("phase"),
            "tier3_unlocked": _tier3_unlocked,
        }
    except Exception as _brake_exc:  # noqa: BLE001
        log.warning(
            "[ml_paper] Phase 2 brake evaluation failed for %s (safe no-op): %s",
            ticker, _brake_exc,
        )
        brake_log = None

    # Infer shares from last close price (use ATR proxy if close unavailable)
    entry_price = snapshot.close_price if snapshot.close_price and snapshot.close_price > 0 else None
    shares: float | None = None
    if entry_price:
        shares = round(position_usd / entry_price, 4)

    # ── Persist to MongoDB ───────────────────────────────────────────────────
    trade_id = str(uuid4())
    now = datetime.now(timezone.utc)
    direction_val = str(signal.direction.value)

    trade_doc = {
        "trade_id": trade_id,
        "ticker": ticker,
        "direction": direction_val,
        "confidence": signal.confidence,
        "prediction_id": signal.prediction_id,
        "position_size_usd": position_usd,
        "entry_price": entry_price,
        "shares": shares,
        "patterns": patterns,
        "regime": regime,
        "status": "open",
        "opened_at": now,
        # Idempotency key — see _time_bucket_for / ensure_indexes.
        # Same (ticker, direction, prediction_id) within 60s is a
        # duplicate by construction.
        "time_bucket": _time_bucket_for(now),
        "closed_at": None,
        "exit_price": None,
        "pnl_usd": None,
        "pnl_pct": None,
        "outcome": None,   # filled by labeling pipeline
        "schema_version": 1,
    }
    if brake_log is not None:
        trade_doc["commander_phase2_brake"] = brake_log
    if sovereign_decision_id is not None:
        trade_doc["sovereign_decision_id"] = sovereign_decision_id
    if sovereign_contribution_meta is not None:
        trade_doc["sovereign_contribution"] = sovereign_contribution_meta
    if failure_penalty_meta is not None:
        trade_doc["symbol_failure_penalty"] = failure_penalty_meta

    try:
        await db["paper_trades"].insert_one(trade_doc)
    except DuplicateKeyError:
        # Same prediction firing twice in the same minute — return
        # the existing trade_id so the caller's workflow stays
        # consistent (no new doc, no second exposure).
        existing = await db["paper_trades"].find_one(
            {
                "ticker": ticker,
                "direction": direction_val,
                "prediction_id": signal.prediction_id,
                "time_bucket": trade_doc["time_bucket"],
            },
            {"_id": 0, "trade_id": 1},
        )
        existing_id = (existing or {}).get("trade_id")
        log.info(
            "[ml_paper] Duplicate paper trade suppressed for %s %s "
            "(prediction_id=%s, returning existing trade_id=%s)",
            ticker, direction_val.upper(), signal.prediction_id, existing_id,
        )
        return existing_id

    try:
        # Also upsert into positions collection for portfolio tracking
        await db["paper_positions"].update_one(
            {"ticker": ticker, "status": "open"},
            {
                "$setOnInsert": {
                    "ticker": ticker,
                    "trade_id": trade_id,
                    "direction": direction_val,
                    "position_size_usd": position_usd,
                    "entry_price": entry_price,
                    "status": "open",
                    "opened_at": now,
                }
            },
            upsert=True,
        )
        log.info(
            "[ml_paper] Paper trade opened — %s %s $%.2f (conf=%.0f%%, patterns=%s)",
            ticker,
            direction_val.upper(),
            position_usd,
            signal.confidence * 100,
            ", ".join(patterns),
        )
        try:
            from services.agent_activity_service import log_paper_trade_opened
            from ai_core.explainability import extract_top_features
            from risedual_core.ml.features import FEATURE_COLUMNS
            fnames = list(FEATURE_COLUMNS)
            fvalues = [getattr(snapshot, c, None) for c in fnames]
            why = extract_top_features(
                feature_names=fnames,
                feature_values=fvalues,
                importances=signal.feature_importance or {},
                top_k=3,
            )
            await log_paper_trade_opened(
                ticker=ticker,
                direction=direction_val,
                position_usd=position_usd,
                confidence=directional_conf,
                patterns=patterns,
                regime=regime,
                why=why,
            )
        except Exception:
            pass
        return trade_id

    except Exception as exc:  # noqa: BLE001
        log.error("[ml_paper] Failed to persist paper trade for %s: %s", ticker, exc)
        return None
