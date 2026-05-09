"""Trading Bot Service — Grid Bot, Signal Bot, TradingView Webhook Bot.

All bots default to OFF. Each has an independent enabled toggle.
Grid Bot runs on the APScheduler interval. Signal Bot triggers from scanner results.
Webhook Bot receives external POST requests.
"""
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any


from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)

_db: Any = None


def set_db(database: Any) -> None:
    global _db
    _db = database


def _resolve_db_for_shadow() -> Any:
    """Internal accessor for the shadow hook — keeps the call sites
    one-liner and isolates the module-level _db reference."""
    return _db


# Pure constants live in ``services.trading_bot._constants`` (Step 4A
# extraction). Re-imported here so ``trading_bot_service.MAX_*`` /
# ``trading_bot_service.ADAPTIVE_SIZING_ENABLED`` etc. stay in this
# module's globals — preserves both external attribute access AND the
# monkeypatch pattern used by ``test_trading_bot_adaptive_sizing.py``
# (bare-name reads inside this module resolve via this module's
# globals, which a monkeypatch on ``tbs.X`` mutates).
from services.trading_bot._constants import (  # noqa: F401
    ADAPTIVE_SIZING_ENABLED,
    _MIN_SCALED_QTY,
    MAX_POSITION_USD,
    MAX_PORTFOLIO_EXPOSURE,
    MAX_CONCURRENT_TRADES,
    MAX_SECTOR_EXPOSURE_PCT,
    BOT_TYPES,
)


# Pure decision rules live in ``services.trading_bot._decision_rules``
# (Step 4D extraction). Re-imported here so external attribute access
# (``tbs.apply_portfolio_constraints`` etc., used by
# ``test_portfolio_risk_engine.py``) and in-module bare-name calls
# both still work. No behaviour change — pure functions, no DB, no
# broker, no mutation. Skip/block reasons unchanged.
from services.trading_bot._decision_rules import (  # noqa: F401
    get_total_exposure,
    get_open_trade_count,
    get_sector_exposure,
    apply_portfolio_constraints,
)


def _check_kill_switch_and_drawdown(
    equity_curve: list[float] | None,
) -> dict | None:
    """Step 0 of execute_signal — fleet-wide circuit breakers.

    Fires before any sizing math so a tripped switch can never
    leak an order through. The drawdown trip applies even when
    the per-bot allocator (step 3c) is skipped because
    ``bot_capital`` is ``None``.

    Returns a ready-to-return ``{"skipped": True, ...}`` dict
    when either gate trips, or ``None`` to continue.
    """
    from ai_core.kill_switch import kill_switch
    from ai_core import compute_drawdown

    if kill_switch.is_active():
        ks_status = kill_switch.status()
        return {
            "skipped": True,
            "reason": "kill switch active",
            "cooldown_remaining_seconds": ks_status["cooldown_remaining_seconds"],
            "last_reason": ks_status["last_reason"],
        }

    if equity_curve:
        dd = compute_drawdown(equity_curve)
        should_trip, trip_reason = kill_switch.should_trip(drawdown=dd)
        if should_trip:
            kill_switch.activate(trip_reason)
            return {"skipped": True, "reason": f"kill switch tripped: {trip_reason}"}

    return None


def _compute_adjusted_size(
    *,
    base_size: float,
    signal: dict,
    tier3_readiness: dict,
    open_positions: list[dict] | None,
    equity_curve: list[float] | None,
    bot_capital: float | None,
) -> tuple[float, str | None]:
    """Steps 2 + 3a + 3b + 3c + 4 of execute_signal — the full
    sizing chain.

    Pipeline:
      2.  Tier 3 readiness × confidence sizing.
      3a. Low-confidence / risk-filter zero check.
      3b. Portfolio-level constraints (opt-in via open_positions).
      3c. Drawdown + allocator throttle (opt-in via curve+capital).
      4.  Hard cap at :data:`MAX_POSITION_USD`.

    Returns ``(adjusted_size, skip_reason)``. ``skip_reason`` is
    ``None`` on success; otherwise the orchestrator returns
    ``{"skipped": True, "reason": skip_reason}`` immediately.
    """
    from ai_core import apply_per_trade_sizing

    adjusted_size = apply_per_trade_sizing(
        base_size=base_size,
        readiness=tier3_readiness,
        prediction=signal,
    )

    if adjusted_size <= 0:
        return 0.0, "low confidence / risk filter"

    if open_positions is not None:
        adjusted_size = apply_portfolio_constraints(
            adjusted_size, open_positions, signal_sector=signal.get("sector"),
        )
        if adjusted_size <= 0:
            return 0.0, "portfolio limits reached"

    if equity_curve is not None and bot_capital is not None:
        from ai_core import apply_global_risk_controls

        adjusted_size = apply_global_risk_controls(
            adjusted_size, equity_curve, bot_capital,
        )
        if adjusted_size <= 0:
            return 0.0, "risk control"

    adjusted_size = min(adjusted_size, MAX_POSITION_USD)
    return adjusted_size, None


def _resolve_qty(
    adjusted_size: float, signal: dict, market_data: dict | None,
) -> tuple[float, float, str | None]:
    """Step 5 of execute_signal — convert USD notional to share count.

    Returns ``(qty, price, skip_reason)``. ``qty`` and ``price``
    are 0.0 on a skip. ``skip_reason`` is ``None`` on success.
    """
    price = signal.get("entry") or (market_data or {}).get("price")
    if not price or price <= 0:
        return 0.0, 0.0, "invalid price"

    qty = round(adjusted_size / price, 6)
    if qty <= 0:
        return 0.0, float(price), "size too small"

    return qty, float(price), None


# Telemetry helpers live in ``services.trading_bot._telemetry`` (Step
# 4B extraction). Re-imported under the original private names so the
# in-module call sites (``_fire_equity_shadow(...)``,
# ``_record_kill_switch_outcome(...)``) don't change. No behaviour
# change — these helpers are receipt-side only (kill-switch
# accounting + research-shadow fire-and-forget).
from services.trading_bot._telemetry import (  # noqa: F401
    fire_equity_shadow as _fire_equity_shadow,
    record_kill_switch_outcome as _record_kill_switch_outcome,
)


# ── ADL-5: blocked-decision receipt helper ──────────────────────────
# Fire-and-forget hook for upstream gate-blocked decisions (kill
# switch / drawdown / fast veto enforce / sizing chain / RoadGuard
# enforce). Each blocked branch produces exactly ONE receipt before
# returning; the success-path receipt (line ~430) also fires exactly
# ONCE — no duplicates. The helper tags the signal payload with
# ``blocked_at`` (gate stage) and ``block_reason`` (preserved skip
# reason) on a COPY of the caller's dict — never mutates the
# caller's signal. Receipt-write failure NEVER alters the
# ``execute_signal`` return value (defensive belt+braces).
def _schedule_blocked_receipt(
    *,
    signal: dict,
    market_data: dict | None,
    lane: str,
    blocked_at: str,
    reason: str,
    requested_notional_usd: float = 0.0,
    open_positions: list[dict] | None = None,
    equity_curve: list[float] | None = None,
    bot_capital: float | None = None,
) -> None:
    try:
        from services.ml.receipt_dispatch import schedule_shadow_receipt
        sig = dict(signal or {})
        sig["blocked_at"] = blocked_at
        sig["block_reason"] = reason
        schedule_shadow_receipt(
            _resolve_db_for_shadow(),
            signal=sig,
            market_data=market_data,
            lane=lane,
            requested_notional_usd=float(requested_notional_usd or 0.0),
            open_positions=open_positions,
            equity_curve=equity_curve,
            bot_capital=float(bot_capital or 0.0),
            source="execute_signal_blocked",
        )
    except Exception as _adl_exc:  # noqa: BLE001
        logger.debug(
            "[execute_signal] ADL blocked receipt skipped (%s): %s",
            blocked_at, _adl_exc,
        )


async def execute_signal(
    signal: dict,
    market_data: dict,
    tier3_readiness: dict,
    config: Any,
    open_positions: list[dict] | None = None,
    equity_curve: list[float] | None = None,
    bot_capital: float | None = None,
    lane: str | None = None,
) -> dict:
    """USD-notional execution path for signal bots.

    Alternative to `process_signal_for_bots` for callers that
    already think in dollars (`config.trade_size`) rather than
    shares (`config.qty`). The two paths are compatible — pick one
    per bot, don't mix.

    **Lane parameter (added 2026-05-07):** ``lane`` is the
    deterministic-constraint half of the neuro-symbolic split
    (see ``services.executors._shared``). When set to ``"equity"``
    or ``"crypto"`` it selects per-lane Fast Veto thresholds and
    tags the shadow-delta log so analytics can disaggregate. When
    ``None``, this function ALSO acts as a router: it auto-detects
    the lane from ``signal["asset_type"]`` / symbol heuristic, and
    if the signal is equity it also enforces the market-hours gate.
    Direct callers (``execute_equity_signal`` / ``execute_crypto_signal``)
    pre-set ``lane`` and skip the routing branch.

    When `open_positions` is supplied, portfolio-level caps
    (:data:`MAX_PORTFOLIO_EXPOSURE`, :data:`MAX_CONCURRENT_TRADES`)
    are enforced. Pass `None` or `[]` to skip the portfolio check
    — useful for unit tests and backtests where the portfolio is
    tracked elsewhere.

    When both `equity_curve` and `bot_capital` are supplied, the
    drawdown + allocator layer (:func:`ai_core.apply_global_risk_controls`)
    runs. This taper-throttles the trade as the fleet equity curve
    drops and caps it at the per-bot capital envelope assigned by
    :func:`ai_core.allocate_capital`. Either arg `None` → that
    layer is skipped.

    Orchestrates five private helpers, each owning one concern:
      0. :func:`_check_kill_switch_and_drawdown` — fleet circuit breaker
      1. :func:`_extract_trade_size` — base USD size from config
      2. :func:`_compute_adjusted_size` — Tier3 × confidence × portfolio × allocator × cap
      3. :func:`_resolve_qty` — USD → share count via signal/market price
      4. :func:`_fire_equity_shadow` — alt-engine logger (fire-and-forget)
      5. :func:`_record_kill_switch_outcome` — feed result into circuit breaker

    Never raises on execution errors — the broker branch of
    `_execute_bot_trade` returns `{"error": ...}` dicts and we
    surface those to the caller.
    """
    # ── Lane routing (neuro-symbolic split: deterministic gates) ──
    # When ``lane`` is None we're being called as the legacy public
    # entry point. Auto-detect the lane and dispatch into the lane
    # executor so the equity market-hours gate fires before any
    # broker-side work. Direct callers (``execute_equity_signal``
    # / ``execute_crypto_signal``) pre-set ``lane`` and skip this
    # branch — they've already done the gate check.
    if lane is None:
        from services.executors._shared import detect_lane
        detected = detect_lane(signal)
        if detected == "equity":
            from services.executors.equity_executor import execute_equity_signal
            return await execute_equity_signal(
                signal=signal,
                market_data=market_data,
                tier3_readiness=tier3_readiness,
                config=config,
                open_positions=open_positions,
                equity_curve=equity_curve,
                bot_capital=bot_capital,
            )
        # detected == "crypto"
        from services.executors.crypto_executor import execute_crypto_signal
        return await execute_crypto_signal(
            signal=signal,
            market_data=market_data,
            tier3_readiness=tier3_readiness,
            config=config,
            open_positions=open_positions,
            equity_curve=equity_curve,
            bot_capital=bot_capital,
        )

    # Lane is set — resolve its config once for the rest of the
    # body to read thresholds + shadow-tag from.
    from services.executors._shared import lane_config_for
    _lane_config = lane_config_for(lane)

    skip = _check_kill_switch_and_drawdown(equity_curve)
    if skip is not None:
        # ADL-5: receipt for kill_switch / drawdown blocks.
        _schedule_blocked_receipt(
            signal=signal,
            market_data=market_data,
            lane=lane,
            blocked_at="kill_switch_or_drawdown",
            reason=str(skip.get("reason") or ""),
            requested_notional_usd=0.0,
            open_positions=open_positions,
            equity_curve=equity_curve,
            bot_capital=bot_capital,
        )
        return skip

    # ── Tier 1: Fast Veto Shadow Layer ──
    # Sub-millisecond classical-ML veto that observes every signal
    # after the hard kill-switch but before Council/Risk-Modulator.
    # In shadow mode (default) it logs hypothetical vetoes only; in
    # enforce mode (operator-gated env flag) it can short-circuit
    # the Council path with NO_TRADE. Veto-only by construction —
    # ``FAST_VETO_CAN_APPROVE`` is hard-coded False.
    fv_result = None
    try:
        from services.fast_veto_layer import (
            FAST_VETO_SHADOW_ENABLED,
            evaluate_fast_veto,
            log_fast_veto_delta,
        )
        market_state_for_veto = {
            "spread_bps": (market_data or {}).get("spread_bps"),
            "volatility_score": (market_data or {}).get("volatility_score")
                or (signal or {}).get("volatility_score"),
            "drawdown_pct": (
                abs(min(0.0, equity_curve[-1] - max(equity_curve)) / max(equity_curve) * 100.0)
                if equity_curve and max(equity_curve) > 0 else 0.0
            ),
            "liquidity_score": (market_data or {}).get("liquidity_score", 1.0),
        }
        fv_result = evaluate_fast_veto(
            signal=signal,
            market_state=market_state_for_veto,
            models=None,  # add trained sklearn models post-shadow validation
            thresholds=_lane_config.fast_veto_thresholds,
        )
        if fv_result.enforce_veto:
            # Shadow logging on the enforce path too — proves promotion.
            db_for_log = _resolve_db_for_shadow()
            if db_for_log is not None and FAST_VETO_SHADOW_ENABLED:
                import asyncio as _asyncio
                _asyncio.create_task(log_fast_veto_delta(
                    db_for_log,
                    signal=signal,
                    market_state=market_state_for_veto,
                    result=fv_result,
                    council_result={"action": "NO_TRADE", "source": "fast_veto"},
                    lane=lane,
                ))
            # ADL-5: receipt for fast_veto ENFORCE blocks.
            _schedule_blocked_receipt(
                signal=signal,
                market_data=market_data,
                lane=lane,
                blocked_at="fast_veto_enforce",
                reason=str(fv_result.reason or ""),
                requested_notional_usd=0.0,
                open_positions=open_positions,
                equity_curve=equity_curve,
                bot_capital=bot_capital,
            )
            return {
                "skipped": True,
                "reason": fv_result.reason,
                "lane": lane,
                "fast_veto": {
                    "would_veto": fv_result.would_veto,
                    "enforce_veto": fv_result.enforce_veto,
                    "reason": fv_result.reason,
                    "latency_us": fv_result.latency_us,
                },
            }
    except Exception as _fv_exc:  # noqa: BLE001
        # Fast Veto must never crash the executor. Log and proceed.
        logger.warning("[fast-veto] evaluation failed (shadow-safe): %s", _fv_exc)
        fv_result = None

    base_size = _extract_trade_size(config)
    if base_size is None or base_size <= 0:
        return {"skipped": True, "reason": "invalid trade_size"}

    adjusted_size, skip_reason = _compute_adjusted_size(
        base_size=base_size,
        signal=signal,
        tier3_readiness=tier3_readiness,
        open_positions=open_positions,
        equity_curve=equity_curve,
        bot_capital=bot_capital,
    )
    if skip_reason is not None:
        # ADL-5: receipt for sizing-chain blocks. The skip_reason
        # discriminates between portfolio caps (sector_cap /
        # max_concurrent_trades / max_portfolio_exposure all map
        # to "portfolio limits reached"), drawdown allocator
        # ("risk control"), and confidence-floor ("low confidence
        # / risk filter").
        _schedule_blocked_receipt(
            signal=signal,
            market_data=market_data,
            lane=lane,
            blocked_at="size_chain",
            reason=str(skip_reason),
            requested_notional_usd=float(adjusted_size or 0.0),
            open_positions=open_positions,
            equity_curve=equity_curve,
            bot_capital=bot_capital,
        )
        return {"skipped": True, "reason": skip_reason}

    qty, price, qty_skip = _resolve_qty(adjusted_size, signal, market_data)
    if qty_skip is not None:
        # ADL-5: receipt for quote / price-resolution blocks.
        _schedule_blocked_receipt(
            signal=signal,
            market_data=market_data,
            lane=lane,
            blocked_at="resolve_qty",
            reason=str(qty_skip),
            requested_notional_usd=float(adjusted_size or 0.0),
            open_positions=open_positions,
            equity_curve=equity_curve,
            bot_capital=bot_capital,
        )
        return {"skipped": True, "reason": qty_skip}

    symbol = signal["symbol"]
    side = "buy" if str(signal.get("direction", "LONG")).upper() == "LONG" else "sell"
    synthetic_bot = _bot_from_config(config, symbol)

    # ── RoadGuard — shared capital / broker / exposure governor ──
    # Sits BELOW the lane executors. Equity and crypto each ran
    # their own gates above (market hours, fast veto, sizing,
    # portfolio constraints) but RoadGuard is the only layer that
    # enforces cross-lane shared limits — total exposure cap,
    # daily loss limit, broker-health floor, and duplicate-symbol
    # prevention. Authority is veto-only; ``ROADGUARD_CAN_APPROVE``
    # is hard-coded False.
    rg_response = None
    try:
        from services.roadguard import (
            ROADGUARD_ENFORCE_ENABLED,
            ROADGUARD_SHADOW_ENABLED,
            RoadGuard,
            build_roadguard_request,
            log_roadguard_decision,
        )
        rg_request = build_roadguard_request(
            symbol=symbol,
            lane=lane,
            requested_notional_usd=float(adjusted_size),
            open_positions=open_positions,
            # ``daily_realized_pnl_usd`` and ``broker_health_score``
            # default to safe values (0 PnL, 1.0 health) until the
            # executor wires real telemetry probes. The cascade is
            # designed to stay quiet on missing data, not block.
            daily_realized_pnl_usd=0.0,
            broker_health_score=1.0,
        )
        rg_response = RoadGuard().evaluate(rg_request)

        # Shadow log every decision (fire-and-forget) so the
        # operator can audit promotion-readiness without waiting
        # for an enforce flip.
        if ROADGUARD_SHADOW_ENABLED:
            db_for_log = _resolve_db_for_shadow()
            if db_for_log is not None:
                import asyncio as _asyncio
                _asyncio.create_task(log_roadguard_decision(
                    db_for_log,
                    request=rg_request,
                    response=rg_response,
                ))

        if rg_response.enforce:
            # Enforce mode + non-ALLOW decision = short-circuit
            # the executor before the broker call.
            # ADL-5: receipt for RoadGuard ENFORCE blocks.
            _schedule_blocked_receipt(
                signal=signal,
                market_data=market_data,
                lane=lane,
                blocked_at="roadguard_enforce",
                reason=str(rg_response.reason or ""),
                requested_notional_usd=float(adjusted_size or 0.0),
                open_positions=open_positions,
                equity_curve=equity_curve,
                bot_capital=bot_capital,
            )
            return {
                "skipped": True,
                "reason": rg_response.reason,
                "lane": lane,
                "roadguard": rg_response.as_dict(),
            }
    except Exception as _rg_exc:  # noqa: BLE001
        # RoadGuard must never crash the executor. Log and proceed.
        logger.warning("[roadguard] evaluation failed (shadow-safe): %s", _rg_exc)
        rg_response = None

    # ── Phase 5a: receipt-only ML pipeline observation ──
    # Runs the new RisedualMLPipeline + RoadGuardV2 against the same
    # signal and writes ONE alpha_decision_log receipt + a
    # roadguard_v2_decisions row. NEVER affects routing — fire-and-
    # forget. The legacy execute_signal flow continues regardless.
    # ADL-1 (2026-05-09): the inline asyncio.create_task pattern
    # was extracted into ``services.ml.receipt_dispatch.schedule_shadow_receipt``
    # so other executors can call the same helper. Behaviour is
    # byte-equivalent — same args, same fire-and-forget semantics,
    # same swallowed-warning failure mode.
    from services.ml.receipt_dispatch import schedule_shadow_receipt
    schedule_shadow_receipt(
        _resolve_db_for_shadow(),
        signal=signal,
        market_data=market_data,
        lane=lane,
        requested_notional_usd=float(adjusted_size),
        open_positions=open_positions,
        equity_curve=equity_curve,
        bot_capital=bot_capital or 0.0,
        source="execute_signal",
    )

    _fire_equity_shadow(
        synthetic_bot=synthetic_bot,
        signal=signal,
        symbol=symbol,
        price=price,
    )

    order = await _execute_bot_trade(
        synthetic_bot,
        symbol,
        side.upper(),
        qty,
        float(price),
        stop_loss=signal.get("stop_loss") or signal.get("sl"),
        take_profit=signal.get("take_profit") or signal.get("tp"),
    )

    logger.info(
        "[signal-bot/usd] %s %s: base=$%s adjusted=$%s qty=%s "
        "conf=%s readiness=%s open_positions=%s",
        side.upper(), symbol,
        round(base_size, 2), round(adjusted_size, 2), qty,
        signal.get("confidence"),
        tier3_readiness.get("confidence_score"),
        get_open_trade_count(open_positions) if open_positions is not None else None,
    )

    _record_kill_switch_outcome(order)

    # Fast Veto shadow log — fire-and-forget so we record what the
    # veto layer WOULD have done versus what the executor actually
    # did. Drives the promotion-evidence collection in
    # ``fast_veto_shadow_deltas``.
    if fv_result is not None:
        try:
            from services.fast_veto_layer import (
                FAST_VETO_SHADOW_ENABLED,
                log_fast_veto_delta,
            )
            db_for_log = _resolve_db_for_shadow()
            if db_for_log is not None and FAST_VETO_SHADOW_ENABLED:
                import asyncio as _asyncio
                _asyncio.create_task(log_fast_veto_delta(
                    db_for_log,
                    signal=signal,
                    market_state=market_state_for_veto,
                    result=fv_result,
                    council_result={
                        "action": side.upper(),
                        "confidence": signal.get("confidence"),
                        "qty": qty,
                        "filled": isinstance(order, dict) and order.get("error") is None,
                    },
                    lane=lane,
                ))
        except Exception as _fv_log_exc:  # noqa: BLE001
            logger.warning("[fast-veto] shadow log dispatch failed: %s", _fv_log_exc)

    return {
        "order": order,
        "size_usd": round(adjusted_size, 2),
        "qty": qty,
        "confidence": signal.get("confidence"),
        "readiness_score": tier3_readiness.get("confidence_score"),
        "base_size": round(base_size, 2),
        "lane": lane,
        "roadguard": rg_response.as_dict() if rg_response is not None else None,
    }


def _extract_trade_size(config: Any) -> float | None:
    """Read `trade_size` from either an object-style config
    (``config.trade_size``) or a dict-style one
    (``config["trade_size"]``). Returns `None` when missing."""
    if config is None:
        return None
    if hasattr(config, "trade_size"):
        val = getattr(config, "trade_size", None)
    elif isinstance(config, dict):
        val = config.get("trade_size")
    else:
        return None
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _bot_from_config(config: Any, symbol: str) -> dict:
    """Minimal bot dict suitable for `_execute_bot_trade`. Prefers
    a pre-built bot doc hung off `config._bot` when the caller has
    one; otherwise synthesises a paper-mode stub.

    This keeps the USD-path compatible with the existing mode /
    user_id / risk-guard plumbing without forcing the caller to
    hand-roll a full bot record.
    """
    if hasattr(config, "_bot") and isinstance(getattr(config, "_bot"), dict):
        return config._bot
    if isinstance(config, dict) and isinstance(config.get("_bot"), dict):
        return config["_bot"]
    # Fallback: synthesise. Without a user_id we can't run the risk
    # pre-flight but the order will still execute in paper mode.
    mode = "paper"
    user_id = None
    name = "usd-exec"
    if hasattr(config, "mode"):
        mode = getattr(config, "mode", "paper")
    elif isinstance(config, dict):
        mode = config.get("mode", "paper")
    if hasattr(config, "user_id"):
        user_id = getattr(config, "user_id", None)
    elif isinstance(config, dict):
        user_id = config.get("user_id")
    if hasattr(config, "name"):
        name = getattr(config, "name", name)
    elif isinstance(config, dict):
        name = config.get("name", name)
    return {"_id": f"usd-bot-{symbol}", "user_id": user_id, "name": name, "mode": mode}


# ═══════════════════════════════════════════════════════════════════════════════
# END — USD-notional execution path
# ═══════════════════════════════════════════════════════════════════════════════


# ── Bot CRUD ──
# Bot CRUD helpers live in ``services.trading_bot._data_access`` (Step
# 4C extraction). Re-imported here so external callers (``routes/
# trading_bots.py`` does ``from services.trading_bot_service import
# create_bot`` etc.) continue to work unchanged. No behaviour change
# — every Mongo query, update shape, and returned dict shape is
# byte-equivalent. The moved helpers fetch the live ``_db`` via the
# same ``_resolve_db_for_shadow`` getter pattern this file already
# uses internally.
from services.trading_bot._data_access import (  # noqa: F401
    create_bot,
    _build_config,
    toggle_bot,
    update_bot_config,
    delete_bot,
    get_user_bots,
)


# ── Grid Bot Engine ──

async def run_grid_bots() -> None:
    """Background: Check all enabled grid bots and place/fill orders."""
    if _db is None:
        return

    bots = await _db.trading_bots.find({"type": "grid", "enabled": True}).to_list(length=50)
    if not bots:
        return

    from services.price_provider import get_quote, get_crypto_quote
    CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK", "BNB"}

    now = datetime.now(timezone.utc).isoformat()
    for bot in bots:
        try:
            cfg = bot.get("config", {})
            symbol = cfg.get("symbol", "")
            if not symbol:
                continue

            q = await get_crypto_quote(symbol) if symbol in CRYPTO else await get_quote(symbol)
            if not q or not q.get("price"):
                continue
            price = float(q["price"])

            upper = cfg.get("upper_price", 0)
            lower = cfg.get("lower_price", 0)
            levels = cfg.get("grid_levels", 5)
            qty = cfg.get("qty_per_grid", 0.01)

            if upper <= lower or levels < 2:
                continue

            step = (upper - lower) / (levels - 1)
            active = cfg.get("active_orders", [])

            # Initialize grid if empty
            if not active:
                active = []
                for i in range(levels):
                    grid_price = round(lower + step * i, 6)
                    side = "buy" if grid_price < price else "sell"
                    active.append({
                        "grid_price": grid_price,
                        "side": side,
                        "qty": qty,
                        "filled": False,
                        "filled_at": None,
                    })
                cfg["active_orders"] = active

            # Check fills
            trades_made = 0
            for order in active:
                if order["filled"]:
                    continue
                if order["side"] == "buy" and price <= order["grid_price"]:
                    result = await _execute_bot_trade(bot, symbol, "buy", qty, order["grid_price"])
                    if result and result.get("error"):
                        log_warning(logger, {
                            "error": str(result['error']),
                            "type": type(result['error']).__name__,
                            "context": "trading_bot",
                            "note": "Grid bot <expr> buy failed",
                        })
                        continue
                    order["filled"] = True
                    order["filled_at"] = now
                    order["side"] = "sell"  # Flip to sell at next grid above
                    trades_made += 1
                elif order["side"] == "sell" and price >= order["grid_price"]:
                    result = await _execute_bot_trade(bot, symbol, "sell", qty, order["grid_price"])
                    if result and result.get("error"):
                        log_warning(logger, {
                            "error": str(result['error']),
                            "type": type(result['error']).__name__,
                            "context": "trading_bot",
                            "note": "Grid bot <expr> sell failed",
                        })
                        continue
                    order["filled"] = True
                    order["filled_at"] = now
                    order["side"] = "buy"  # Flip to buy at next grid below
                    trades_made += 1

            # Reset filled orders for continuous grid
            for order in active:
                if order["filled"]:
                    order["filled"] = False
                    order["filled_at"] = None

            if trades_made > 0:
                stats = bot.get("stats", {})
                stats["trades"] = stats.get("trades", 0) + trades_made
                await _db.trading_bots.update_one(
                    {"_id": bot["_id"]},
                    {"$set": {"config": cfg, "stats": stats, "last_run": now, "updated_at": now}}
                )
        except Exception as e:
            logger.debug(f"Grid bot error {bot.get('name')}: {e}")


# ── Signal Bot Engine ──

async def process_signal_for_bots(user_id: str, signal: dict) -> list[dict]:
    """Process a scanner signal through all enabled signal bots for a user.
    Returns list of executed trades."""
    if _db is None:
        return []

    bots = await _db.trading_bots.find({
        "user_id": user_id, "type": "signal", "enabled": True,
    }).to_list(length=10)

    # Fetch the Tier 3 readiness snapshot ONCE per signal (not per
    # bot) — it's a single 30-day aggregation and the result is
    # identical for every bot this user owns. Falls back to None on
    # any error so the sizing short-circuits to the legacy path.
    readiness_snap = None
    if ADAPTIVE_SIZING_ENABLED:
        try:
            from services.tier3_readiness import tier3_readiness_snapshot
            readiness = await tier3_readiness_snapshot(_db, days=30)
            # `tier3_readiness_snapshot` returns {stats, unlock, ...}.
            # Flatten so `compute_position_multiplier` sees the
            # `confidence_score` + `stats` shape it expects.
            readiness_snap = {
                "confidence_score": (readiness.get("unlock") or {}).get(
                    "confidence_score", 0.0
                ),
                "stats": readiness.get("stats") or {},
            }
        except Exception as exc:
            logger.warning("[signal-bot] adaptive sizing disabled this run: %s", exc)
            readiness_snap = None

    results = []
    for bot in bots:
        cfg = bot.get("config", {})
        min_conf = cfg.get("min_confidence", 70)
        allowed_strategies = cfg.get("strategies", [])
        allowed_symbols = cfg.get("symbols", [])
        side_filter = cfg.get("side", "both")

        # Check filters
        confidence = signal.get("ai_confidence", 0)
        if confidence < min_conf:
            continue

        strategy = signal.get("strategy_id", "")
        if allowed_strategies and strategy not in allowed_strategies:
            continue

        symbol = signal.get("symbol", "")
        if allowed_symbols and symbol not in allowed_symbols:
            continue

        # Determine side from verdict via the central canonicaliser.
        # Pre-2026-05-01 the local tuple ``("strong_buy", "buy")``
        # silently excluded WEAK_BUY/BULLISH/LONG/UP — bots ignored
        # weak signals entirely. Same bug class as the prediction-
        # tracker line 654 fix.
        from services.prediction_tracker import canonical_ai_dir
        verdict = signal.get("ai_verdict", "hold")
        canon = canonical_ai_dir(verdict)
        if canon == "LONG":
            side = "buy"
        elif canon == "SHORT":
            side = "sell"
        else:
            continue

        if side_filter != "both" and side != side_filter:
            continue

        # Per-bot daily-trade cap — zeros (or missing cap) short-circuit
        # the check entirely. The cap resets at UTC day rollover by
        # comparing `last_trade_date` to today's date string. We check
        # before execution so a busy scanner pass can't burst past the
        # cap on a single sweep.
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        last_trade_date = cfg.get("last_trade_date")
        trades_today = int(cfg.get("trades_today", 0))
        if last_trade_date != today_str:
            trades_today = 0  # new UTC day — reset counter before check
        max_per_day = int(cfg.get("max_trades_per_day", 0) or 0)
        if max_per_day > 0 and trades_today >= max_per_day:
            logger.info(
                f"[signal-bot] {bot.get('name')} skipped {symbol} — "
                f"daily cap reached ({trades_today}/{max_per_day})"
            )
            # ADL-5: receipt for max_trades_per_day cap at the
            # dispatcher layer (pre-execute_signal block).
            try:
                from services.executors._shared import detect_lane
                _dispatch_lane = detect_lane(signal) or "equity"
            except Exception:  # noqa: BLE001
                _dispatch_lane = "equity"
            _schedule_blocked_receipt(
                signal=signal,
                market_data=None,
                lane=_dispatch_lane,
                blocked_at="max_trades_per_day",
                reason=(
                    f"daily cap reached ({trades_today}/{max_per_day})"
                ),
                requested_notional_usd=0.0,
            )
            continue

        # Execute via Smart Order or direct paper trade
        now = datetime.now(timezone.utc).isoformat()
        qty = cfg.get("qty", 1)
        price = signal.get("price", 0)

        # Adaptive sizing: shrink the configured qty when Tier 3
        # readiness is below 100/100 or the signal confidence is
        # below the trade gate. When the feature flag is off, this
        # whole block is a no-op. When on, a `sizing_meta` field is
        # stamped onto the result so admin UIs can show "fired at
        # 0.8x of config" without re-fetching the readiness snapshot.
        sizing_meta: dict | None = None
        if readiness_snap is not None:
            try:
                from ai_core.sizing import (
                    MIN_CONFIDENCE_TO_TRADE,
                    compute_confidence_multiplier,
                    compute_position_multiplier,
                )
                r_mult = compute_position_multiplier(readiness_snap)
                c_mult = compute_confidence_multiplier(confidence)
                # Below the hard trade-gate → skip the trade entirely.
                if confidence < MIN_CONFIDENCE_TO_TRADE:
                    logger.info(
                        "[signal-bot] %s skipped %s — conf %s below trade gate %s",
                        bot.get("name"), symbol, confidence, MIN_CONFIDENCE_TO_TRADE,
                    )
                    continue
                final_mult = max(min(r_mult * c_mult, 2.0), 0.1)
                original_qty = qty
                scaled_qty = max(
                    _MIN_SCALED_QTY,
                    round(float(original_qty) * final_mult, 4),
                )
                qty = scaled_qty
                sizing_meta = {
                    "original_qty": original_qty,
                    "scaled_qty": scaled_qty,
                    "readiness_mult": r_mult,
                    "confidence_mult": c_mult,
                    "final_mult": round(final_mult, 3),
                }
                logger.info(
                    "[signal-bot] %s adaptive-sized %s %s: %s → %s "
                    "(r=%s × c=%s = %s)",
                    bot.get("name"), side, symbol,
                    original_qty, scaled_qty, r_mult, c_mult, round(final_mult, 3),
                )
            except Exception as exc:
                # Any failure in the sizing path must NOT block a trade —
                # fall back to the configured qty.
                logger.warning("[signal-bot] adaptive sizing fallback: %s", exc)

        # Derive SL/TP from bot config so BOTH the smart-order and
        # direct-paper paths stamp them on the trade. Needed for
        # r_multiple on the eventual SELL. Previously only the
        # smart-order path used auto_sl_pct/auto_tp_pct — the direct
        # path dropped them, which blinded the learning loop to risk.
        sl_pct = cfg.get("auto_sl_pct", 3) / 100
        tp_pct = cfg.get("auto_tp_pct", 6) / 100
        if price > 0:
            sl_price = (
                round(price * (1 - sl_pct), 4) if side == "buy"
                else round(price * (1 + sl_pct), 4)
            )
            tp_price = (
                round(price * (1 + tp_pct), 4) if side == "buy"
                else round(price * (1 - tp_pct), 4)
            )
        else:
            sl_price = None
            tp_price = None

        trade_filled = False
        trade_result: dict | None = None

        if cfg.get("use_smart_order"):
            from services.smart_order_service import create_smart_order
            order_result = await create_smart_order(user_id, {
                "symbol": symbol, "side": side, "qty": qty,
                "mode": bot.get("mode", "paper"), "order_type": "market",
                "stop_loss": {"price": sl_price, "trailing": True, "trailing_pct": cfg.get("auto_sl_pct", 3)},
                "take_profits": [{"price": tp_price, "pct_of_qty": 100}],
            })
            # Smart order create returns a complex shape; treat the
            # presence of an order id (or lack of an `error` key) as
            # acceptance. Fills are reconciled by the smart-order
            # monitor job, not here.
            trade_filled = bool(order_result) and not order_result.get("error")
            trade_result = order_result
            res_payload = {"bot": bot.get("name"), "symbol": symbol, "side": side, "result": "smart_order", "order": order_result}
            if sizing_meta:
                res_payload["sizing"] = sizing_meta
            results.append(res_payload)
        else:
            trade_result = await _execute_bot_trade(
                bot, symbol, side, qty, price,
                stop_loss=sl_price, take_profit=tp_price,
            )
            trade_filled = bool(trade_result) and trade_result.get("status") == "filled"
            res_payload = {
                "bot": bot.get("name"), "symbol": symbol, "side": side,
                "result": "paper_trade", "status": (trade_result or {}).get("status"),
                "r_multiple": (trade_result or {}).get("r_multiple"),
            }
            if sizing_meta:
                res_payload["sizing"] = sizing_meta
            results.append(res_payload)

        # Gate stats + cap counter on an actual FILL. Rejections (no
        # position to sell, insufficient cash, broker reject) must not
        # burn a daily-cap slot or falsely bump the trade counter —
        # that was the "silent data poison" concern: unfilled attempts
        # shouldn't train the learning loop either.
        if not trade_filled:
            logger.info(
                f"[signal-bot] {bot.get('name')} {symbol} {side} NOT filled "
                f"({(trade_result or {}).get('error') or 'unknown'}) — no stats update"
            )
            continue

        # Log the pending trade to the canonical ai_core LearningEngine
        # so the fleet-wide win/loss/expectancy roll-up stays in sync.
        # The outcome stays `pending` until prediction_labeler verifies
        # it on the delayed cron — this is the whole point of the
        # three-label system: no false losses for unresolved trades.
        try:
            from ai_core import LearningEngine, Signal as _Sig, Trade as _Tr
            from ai_core.models import TradeResult as _TR
            filled_price = float((trade_result or {}).get("price") or price or 0)
            ai_sig = _Sig.from_dict({
                "symbol": symbol,
                "direction": "LONG" if side == "buy" else "SHORT",
                "entry": filled_price,
                "stop_loss": sl_price or filled_price,
                "take_profit": tp_price or filled_price,
                "confidence": signal.get("ai_confidence", 0),
                "strategy_id": signal.get("strategy_id"),
            })
            ai_trade = _Tr.from_signal(ai_sig, size=qty, user_id=user_id)
            await LearningEngine(_db).log_trade(
                ai_trade,
                _TR(pnl=0.0, exit_price=filled_price,
                    win=None, status="pending", r_multiple=0.0),
                signal=ai_sig,
            )
        except Exception as e:
            # LearningEngine is a best-effort sink; never block trade
            # execution on its availability.
            log_warning(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "trading_bot",
                "note": "[signal-bot] learning-engine log failed",
            })

        # Update stats + daily cap counters. We persist cap state on the
        # config (not stats) so it survives `update_bot_config` merges
        # and is visible in the admin bot list without an extra field.
        stats = bot.get("stats", {})
        stats["signals_received"] = stats.get("signals_received", 0) + 1
        stats["signals_executed"] = stats.get("signals_executed", 0) + 1
        stats["trades"] = stats.get("trades", 0) + 1

        new_cfg = {**cfg,
                   "trades_today": trades_today + 1,
                   "last_trade_date": today_str}
        await _db.trading_bots.update_one(
            {"_id": bot["_id"]},
            {"$set": {"stats": stats, "config": new_cfg,
                      "last_run": now, "updated_at": now}}
        )

    return results


async def run_signal_bot_dispatcher() -> dict:
    """Fan scanner output into all enabled signal bots. Scheduled job.

    Runs every ~5 min via APScheduler. Aggregates the distinct symbol +
    strategy whitelist across every enabled signal bot in the system,
    runs one consolidated `scan_symbols()` pass (so we don't pay the
    technicals cost per-user), then for each strategy match builds a
    normalised signal dict and hands it to `process_signal_for_bots`
    for every user who has a matching bot.

    Design notes:
      * We scan ONCE for the union of symbols, even if multiple users
        watch the same name. Per-bot filters inside
        `process_signal_for_bots` already handle the fan-out.
      * Strategies with `signal="neutral"` (bollinger_squeeze,
        volume_spike) are skipped — no verdict to act on.
      * `strength` (0-100) is mapped into `ai_confidence`. Strength ≥80
        upgrades the verdict to strong_buy / strong_sell so the bot's
        side filter sees the magnitude.
      * Dispatcher is idempotent per-bot via the daily cap — a signal
        that matches the same bot twice in one dispatch pass still
        respects `max_trades_per_day`.
    """
    if _db is None:
        logger.warning("[signal-dispatcher] DB handle missing, skipping run")
        return {"skipped": True, "reason": "no_db"}

    # Collect every enabled signal bot's symbol + strategy whitelists,
    # grouped by user so we can fan the signals back out correctly.
    cursor = _db.trading_bots.find(
        {"type": "signal", "enabled": True},
        {"_id": 0, "user_id": 1, "config": 1, "name": 1},
    )
    bots_by_user: dict[str, list[dict]] = {}
    union_symbols: set[str] = set()
    union_strategies: set[str] = set()
    async for b in cursor:
        uid = b.get("user_id")
        if not uid:
            continue
        bots_by_user.setdefault(uid, []).append(b)
        cfg = b.get("config") or {}
        for s in cfg.get("symbols") or []:
            if s:
                union_symbols.add(s.upper())
        for sid in cfg.get("strategies") or []:
            if sid:
                union_strategies.add(sid)

    if not union_symbols:
        logger.debug("[signal-dispatcher] no enabled signal bots with symbols, skipping")
        return {"dispatched": 0, "reason": "no_symbols"}

    from services.scanner_service import scan_symbols, STRATEGIES
    strategies = sorted(union_strategies) if union_strategies else None
    scan = await scan_symbols(sorted(union_symbols), strategies=strategies)

    # ── Collect, then dedupe by symbol ──────────────────────────────────
    # The scanner can legitimately return both a bullish and a bearish
    # match for the same ticker in a single pass (e.g. near_52w_high
    # + rsi_overbought). Without dedup, a bot with `side=both` would
    # receive a BUY and a SELL on the same price, same minute — net
    # PnL zero but burns a daily-cap slot and creates misleading
    # trade-count numbers. We collapse to the highest-strength signal
    # per symbol BEFORE fan-out. Ties are broken deterministically by
    # strategy id so replays give the same winner.
    raw_signals: list[dict] = []
    for sid, block in (scan.get("strategies") or {}).items():
        meta = STRATEGIES.get(sid) or {}
        bias = meta.get("signal", "neutral")
        if bias == "neutral":
            continue
        for m in block.get("matches") or []:
            strength = float(m.get("strength") or 0)
            if strength <= 0:
                continue
            if bias == "bullish":
                verdict = "strong_buy" if strength >= 80 else "buy"
            else:
                verdict = "strong_sell" if strength >= 80 else "sell"
            raw_signals.append({
                "ai_confidence": strength,
                "ai_verdict": verdict,
                "strategy_id": sid,
                "symbol": m.get("symbol", "").upper(),
                "price": m.get("price") or 0,
                "source": "signal_bot_dispatcher",
                "detail": m.get("detail", ""),
            })

    # Keep highest-strength per symbol. Secondary sort by strategy_id
    # gives stable tie-breaking across replays.
    best_by_symbol: dict[str, dict] = {}
    for s in raw_signals:
        sym = s["symbol"]
        prev = best_by_symbol.get(sym)
        if (
            prev is None
            or s["ai_confidence"] > prev["ai_confidence"]
            or (
                s["ai_confidence"] == prev["ai_confidence"]
                and s["strategy_id"] < prev["strategy_id"]
            )
        ):
            best_by_symbol[sym] = s
    signals = list(best_by_symbol.values())
    suppressed = len(raw_signals) - len(signals)

    # ── Log each deduped signal as a prediction ─────────────────────────
    # Decouples "what the scanner thinks" (persisted for calibration)
    # from "what the bot executed" (persisted in paper_trades). Every
    # prediction auto-gets the composite conviction score via
    # `log_prediction`, so the admin Conviction panel starts reflecting
    # automated fleet activity — not just user-driven War Room calls.
    # We use `user_id=None` so these rows are attributable to the
    # dispatcher itself, distinct from per-user War Room/Hypothesis
    # predictions. Conviction's calibration input falls back to 0.5
    # neutral (no user trailing win-rate to look up), which is correct.
    from services.prediction_tracker import log_prediction as _log_pred
    predictions_logged = 0
    for s in signals:
        try:
            await _log_pred(
                _db,
                "signal_dispatcher",
                s["symbol"],
                s["ai_verdict"],
                # Normalise 0-100 strength to 0-1 confidence for the
                # prediction schema (matches the hypothesis/war_room
                # convention — they pass 0-1 floats).
                float(s["ai_confidence"]) / 100.0,
                user_id=None,
            )
            predictions_logged += 1
        except Exception as e:
            logger.warning(
                f"[signal-dispatcher] prediction log failed for "
                f"{s['symbol']}/{s['strategy_id']}: {e}"
            )

    # ── Fan deduped signals out to every matching user's bots ──────────
    dispatched = 0
    for signal in signals:
        for uid in bots_by_user:
            try:
                results = await process_signal_for_bots(uid, signal)
                dispatched += len(results or [])
            except Exception as e:
                logger.warning(
                    f"[signal-dispatcher] fan-out failed for user {uid} "
                    f"on {signal['symbol']}/{signal['strategy_id']}: {e}"
                )

    logger.info(
        f"[signal-dispatcher] scanned {len(union_symbols)} symbols, "
        f"{len(signals)} deduped signals (suppressed {suppressed} conflicting), "
        f"{predictions_logged} predictions logged, "
        f"fanned to {len(bots_by_user)} users, {dispatched} trades executed"
    )
    return {
        "symbols_scanned": len(union_symbols),
        "strategies_checked": len((scan.get("strategies") or {})),
        "users": len(bots_by_user),
        "signals": len(signals),
        "suppressed": suppressed,
        "predictions_logged": predictions_logged,
        "dispatched": dispatched,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Webhook Bot Engine ──

async def process_webhook(user_id: str, bot_id: str, webhook_secret: str, payload: dict) -> dict:
    """Process an incoming TradingView webhook."""
    from bson import ObjectId
    bot = await _db.trading_bots.find_one({"_id": ObjectId(bot_id), "user_id": user_id, "type": "webhook"})
    if not bot:
        return {"error": "Bot not found"}
    if not bot.get("enabled"):
        return {"error": "Bot is disabled"}
    if bot.get("webhook_secret") != webhook_secret:
        return {"error": "Invalid webhook secret"}

    cfg = bot.get("config", {})
    now = datetime.now(timezone.utc)

    # Rate limit
    today = now.strftime("%Y-%m-%d")
    if cfg.get("last_trade_date") != today:
        cfg["trades_today"] = 0
        cfg["last_trade_date"] = today
    if cfg["trades_today"] >= cfg.get("max_trades_per_day", 10):
        # ADL-5: receipt for webhook max_trades_per_day cap.
        try:
            from services.executors._shared import detect_lane
            _wh_signal = {
                "symbol": str(payload.get("symbol")
                              or payload.get("ticker") or "").upper(),
                "direction": str(payload.get("action") or "").upper(),
            }
            _wh_lane = detect_lane(_wh_signal) or "equity"
        except Exception:  # noqa: BLE001
            _wh_signal = {
                "symbol": str(payload.get("symbol")
                              or payload.get("ticker") or "").upper(),
            }
            _wh_lane = "equity"
        _schedule_blocked_receipt(
            signal=_wh_signal,
            market_data=None,
            lane=_wh_lane,
            blocked_at="max_trades_per_day",
            reason=(
                f"webhook daily cap reached "
                f"({cfg['trades_today']}/"
                f"{cfg.get('max_trades_per_day', 10)})"
            ),
            requested_notional_usd=0.0,
        )
        return {"error": "Daily trade limit reached"}

    # Parse webhook payload
    action = payload.get("action", "").lower()
    symbol = payload.get("symbol", payload.get("ticker", "")).upper()
    qty = float(payload.get("qty", payload.get("quantity", cfg.get("default_qty", 1))))

    if action not in cfg.get("accepted_actions", ["buy", "sell"]):
        return {"error": f"Action '{action}' not accepted"}
    if cfg.get("require_symbol") and not symbol:
        return {"error": "Symbol required"}

    # Execute
    user_id_str = bot["user_id"]
    if cfg.get("use_smart_order", False):
        from services.smart_order_service import create_smart_order
        order_result = await create_smart_order(user_id_str, {
            "symbol": symbol, "side": action, "qty": qty,
            "mode": bot.get("mode", "paper"), "order_type": "market",
        })
        result: dict[str, Any] = {"status": "executed", "type": "smart_order", "order": order_result}
    else:
        await _execute_bot_trade(bot, symbol, action, qty, 0)
        result = {"status": "executed", "type": "paper_trade", "symbol": symbol, "side": action, "qty": qty}

    # Update stats
    cfg["trades_today"] = cfg.get("trades_today", 0) + 1
    stats = bot.get("stats", {})
    stats["signals_received"] = stats.get("signals_received", 0) + 1
    stats["signals_executed"] = stats.get("signals_executed", 0) + 1
    stats["trades"] = stats.get("trades", 0) + 1
    await _db.trading_bots.update_one(
        {"_id": bot["_id"]},
        {"$set": {"config": cfg, "stats": stats, "last_run": now.isoformat(), "updated_at": now.isoformat()}}
    )

    return result


async def _apply_bot_risk_guards(bot: dict, user_id: str, qty: float) -> tuple[float, dict]:
    """Pre-flight risk check for bot-initiated trades.

    Mirrors the circuit breaker the `/api/risk-calc` endpoint already
    returns to the UI so human-initiated trades and bot-initiated trades
    obey the same auto-de-risk rules. If the user's recent prediction
    outcomes indicate a losing streak >= 3 or drawdown >= 10%, bot
    position size is halved before hitting the broker.

    Returns `(adjusted_qty, context_dict)`. Never raises — on any lookup
    error we fall through to the original qty so the bot keeps trading
    (fail-open is the correct default: we never want a bot frozen by a
    Mongo hiccup, only by a real losing streak).
    """
    ctx = {"risk_reduced": False, "original_qty": qty, "adjusted_qty": qty, "reason": None}
    if _db is None:
        return qty, ctx
    try:
        from routes.risk_calculator import _compute_risk_context, _get_account_value

        account_value = await _get_account_value(user_id)
        risk_ctx = await _compute_risk_context(user_id, account_value)

        if risk_ctx.get("risk_reduced"):
            new_qty = max(1.0, round(qty * risk_ctx.get("reduction_factor", 0.5), 4))
            ctx.update({
                "risk_reduced": True,
                "adjusted_qty": new_qty,
                "reason": risk_ctx.get("reason"),
                "losing_streak": risk_ctx.get("losing_streak"),
                "current_drawdown": risk_ctx.get("current_drawdown"),
            })
            logger.warning(
                f"[bot-guard] {bot.get('name')} qty {qty} → {new_qty} "
                f"(reason: {risk_ctx.get('reason')})"
            )
            # Record the de-risk for audit (not a veto — just a reduction).
            try:
                from services.rejection_log import log_rejected
                await log_rejected(
                    asset=(bot.get("symbol") or "").upper(),
                    direction=None,
                    reason=f"bot qty reduced {qty} → {new_qty}: {risk_ctx.get('reason')}",
                    source="risk_circuit_breaker",
                    meta={
                        "bot_name": bot.get("name"),
                        "bot_type": bot.get("type"),
                        "mode": bot.get("mode"),
                        "reduction_factor": risk_ctx.get("reduction_factor"),
                    },
                    user_id=user_id,
                )
            except Exception:
                pass
            return new_qty, ctx
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "trading_bot",
            "note": "[bot-guard] check failed (failing-open)",
        })
    return qty, ctx


async def _execute_bot_trade(
    bot: dict,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> dict:
    """Execute a trade for a bot.

    - mode=paper: routes through paper_trading_service
    - mode=live:  routes through the user's connected broker (Alpaca for
                  equities, Kraken for crypto). Mirrors the live path used
                  by smart_order_service._execute_fill() so behaviour stays
                  consistent between grid/signal/webhook bots and Smart
                  Orders. Returns the broker order id on success so the
                  caller can reconcile fills.

    Pre-flight: runs the same circuit-breaker the UI risk-calc respects.
    When streak/drawdown thresholds are tripped the bot's qty is halved
    (never skipped entirely — grid/DCA strategies need continuity).

    `stop_loss` / `take_profit` flow through to paper_trading_service so
    the trade record can compute `r_multiple` on the eventual SELL.
    They are NOT forwarded to the live broker here because the broker
    SL/TP goes through bracket orders (smart_order_service._execute_fill),
    not the raw market-order path that the `mode=live` branch uses.
    """
    mode = bot.get("mode", "paper")
    user_id = bot["user_id"]

    # Circuit-breaker pre-flight — fails open on any DB/lookup error.
    qty, guard_ctx = await _apply_bot_risk_guards(bot, user_id, qty)

    if mode == "paper":
        from services.paper_trading_service import execute_trade
        return await execute_trade(
            user_id, symbol, side.upper(), qty,
            stop_loss=stop_loss, take_profit=take_profit,
        )

    if mode == "live":
        try:
            if _db is None:
                logger.error("Live bot trade attempted with no DB handle")
                return {"error": "DB unavailable"}
            broker_conn = await _db.broker_connections.find_one(
                {"user_id": user_id}, {"_id": 0, "broker_id": 1},
            )
            if not broker_conn:
                return {"error": "No broker connected for live trading"}

            from routes.broker import _get_or_refresh_client, _get_user_broker
            import asyncio as _asyncio

            conn = await _get_user_broker(user_id, broker_conn["broker_id"])
            client = await _get_or_refresh_client(user_id, broker_conn["broker_id"], conn)
            result = await _asyncio.to_thread(
                client.place_order,
                symbol=symbol, qty=qty, side=side.lower(),
                order_type="market", time_in_force="day",
            )
            if not result:
                return {"error": "Broker rejected order"}
            logger.info(
                f"Live bot trade filled: {bot.get('name')} {side} {qty} {symbol} "
                f"(broker_order_id={result.get('id')})"
            )
            return {"status": "filled", "broker_order_id": result.get("id"),
                    "symbol": symbol, "side": side, "qty": qty}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "trading_bot",
                "note": "Live bot trade failed (<expr> <symbol>)",
                "symbol": symbol,
            })
            return {"error": f"Live execution failed: {e}"}

    return {"error": f"Unknown bot mode: {mode}"}
