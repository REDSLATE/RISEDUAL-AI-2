"""Public.com equity live executor (2026-06-12).

Operator directive: drop Alpaca, route equity intents to Public.com.

Pattern mirrors ``services/crypto_live_executor.py`` for symmetry:
  * Operator-armed via env knob (default OFF)
  * Fixed notional cap (default $25) to bound first-connect blast radius
  * Persists rows into a dedicated ``equity_live_trades`` collection
    so live fills NEVER pollute the paper-trade ML training set
  * Fire-and-forget; never raises into Alpha's consensus loop

Doctrine pins
-------------
* **Live-only** — Public.com has no paper sandbox. Paper-equity
  training data continues to come from the existing paper pipeline
  (which DOES NOT call this module).
* **LONG-only** — same constraint as crypto. Shorts would require
  margin awareness this scaffold doesn't implement.
* **Market orders only** at Phase B; bracket SL/TP can layer in
  Phase C when we know how Public.com's order types behave in prod.
* **Connect-state aware** — refuses to fire if the Public.com
  connection record in ``broker_connections`` is absent or stale.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


# ── Env knobs ────────────────────────────────────────────────────────


def _live_exec_enabled() -> bool:
    """Master kill switch for Public.com live equity. Default OFF.

    Operator flips ``RISEDUAL_PUBLIC_LIVE_EXEC=1`` in prod's
    ``backend/.env`` after redeploy + connect. Unset → every call
    to :func:`maybe_route_live` returns ``None`` without touching
    Public.com's API.
    """
    return (os.environ.get("RISEDUAL_PUBLIC_LIVE_EXEC") or "").strip() in (
        "1", "true", "True", "yes", "on",
    )


def _fixed_notional_usd() -> float:
    """Per-trade USD cap. Default $25 — same as the crypto live wire.

    Bounded to [1, 1000] so a stray env typo can't blow up sizing.
    Override with ``PUBLIC_LIVE_NOTIONAL_USD`` env var.
    """
    raw = (os.environ.get("PUBLIC_LIVE_NOTIONAL_USD") or "").strip()
    if not raw:
        return 25.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 25.0
    if v < 1.0:
        return 1.0
    if v > 1000.0:
        return 1000.0
    return v


def _allowed_symbols() -> Optional[set[str]]:
    """Optional symbol allowlist. ``PUBLIC_LIVE_SYMBOLS=AAPL,MSFT,NVDA``
    restricts live execution to that comma-separated list. Unset →
    no allowlist (any symbol from Alpha's consensus is eligible)."""
    raw = (os.environ.get("PUBLIC_LIVE_SYMBOLS") or "").strip()
    if not raw:
        return None
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _live_confidence_floor() -> float:
    """Minimum signal confidence for live execution.

    2026-06-18: With peer-brain veto severed (MC2 standalone), the
    operator wants a hard confidence floor as the cheapest substitute.
    Default 0.65 — refuse to put real money on signals where Alpha's
    own conviction is below this line. Operator can tighten via
    ``PUBLIC_LIVE_CONFIDENCE_FLOOR=0.7`` etc.
    """
    raw = (os.environ.get("PUBLIC_LIVE_CONFIDENCE_FLOOR") or "").strip()
    if not raw:
        return 0.65
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.65
    # Bound to [0, 0.95] — anything ≥0.95 collides with the
    # toxic-spike saturation cap and silently disables live trading.
    if v < 0.0:
        return 0.0
    if v > 0.95:
        return 0.95
    return v


def _symbol_cooldown_min() -> int:
    """Per-symbol re-fire cooldown in minutes. Default 60.

    Prevents the same-ticker re-buy pattern seen on the prod tape
    (JPM fired 3× in the same window). Cooldown counts from the
    ``opened_at`` of the last live BUY into this symbol regardless
    of whether the position is still open or already closed.
    Override via ``PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN``.
    """
    raw = (os.environ.get("PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN") or "").strip()
    if not raw:
        return 60
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return 60
    if v < 0:
        return 0
    if v > 1440:  # 24h ceiling
        return 1440
    return v


def _max_intraday_move_pct() -> float:
    """Refuse BUYs when the symbol has already run this far today.

    2026-07-30 forensic: operator's prod P&L calendar showed a
    -$56 / -$35 / -$25 sequence over three consecutive days. Root
    cause per operator: "It sees it but doesn't enter until it's
    ended" — the bot chases the top of moves. This gate blocks any
    OPEN_LONG on a symbol whose intraday move (current quote vs
    previous close) already exceeds a threshold, forcing the bot
    to skip late entries. Signals that arrive DURING a fresh move
    still fire; ones that arrive AFTER a completed run are refused.

    Default 4.0% — chosen empirically as "already-mooned" territory
    for a mid-cap on a normal day. Set to 0 or negative to disable.
    Override via ``PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT``.
    """
    raw = (os.environ.get("PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT") or "").strip()
    if not raw:
        return 4.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 4.0
    return v


async def _intraday_move_pct(symbol: str) -> Optional[float]:
    """Best-effort measure of today's move on ``symbol``.

    Returns ``(today_close - previous_close) / previous_close * 100`` as
    a signed float, or ``None`` when the data providers are all
    unavailable / return garbage. Callers treat ``None`` as "don't gate
    on this" — a data outage must never *silently* re-arm chasing.

    2026-08-11 forensic: the previous implementation compared a live
    quote from one provider against the previous close from another
    provider. This produced impossible readings (PLTR reported +27.6%,
    MSFT +23.2%) whenever the two providers disagreed on a split or
    ex-dividend adjustment, blocking 100% of Alpha's live intents.
    Both legs are now sourced from the SAME market_daily response so
    provider-mismatch spikes are structurally impossible. If the daily
    bars provider is down we return None (fail-open) instead of the
    quote+bars fallback that was producing the bad readings.
    """
    try:
        from services.market_data_pool import market_daily
    except Exception:
        return None

    try:
        bars = await market_daily(symbol, outputsize="compact")
    except Exception:
        bars = None

    if not isinstance(bars, list) or len(bars) < 2:
        return None

    try:
        today_close = float(bars[-1].get("close"))
        prev_close = float(bars[-2].get("close"))
    except (TypeError, ValueError, AttributeError):
        return None

    if prev_close <= 0 or today_close <= 0:
        return None
    move_pct = (today_close - prev_close) / prev_close * 100.0
    # Hard sanity cap. A single-session move above ±50% almost always
    # means split/dividend adjustment desync, not real market action.
    # Returning None here fails open rather than nuking a legit signal.
    if abs(move_pct) > 50.0:
        logger.info(
            "[public-live] symbol=%s chasing-filter reading %.2f%% looks "
            "corrupt (split/dividend?) — treating as unavailable",
            symbol, move_pct,
        )
        return None
    return move_pct


def _rth_only_enabled() -> bool:
    """Master switch for the RTH-only session gate. Default ON.

    Public.com's broker API does not accept fractional/notional orders
    outside 9:30-16:00 ET. Any BUY submitted in extended hours gets
    rejected server-side, so we skip cleanly here with a dedicated
    ``market_closed`` reason. Set ``PUBLIC_LIVE_RTH_ONLY=0`` to disable
    (only useful once we add whole-share extended-hours support).
    """
    raw = (os.environ.get("PUBLIC_LIVE_RTH_ONLY") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _in_regular_session(now: Optional[datetime] = None) -> bool:
    """True during US regular trading hours (Mon-Fri, 9:30-16:00 ET).

    Holiday calendar is intentionally not implemented — the broker
    itself rejects holiday orders and we log those as ``market_closed``
    on the next signal without a bespoke calendar service.
    """
    now = now or datetime.now(timezone.utc)
    # US Eastern = UTC-5 in EST, UTC-4 in EDT. Approximation: use
    # UTC-4 mid-March → early Nov, UTC-5 otherwise. This is a
    # tolerant classifier — the broker rejection is the source of
    # truth if we get within ~1h of a boundary.
    month = now.month
    day = now.day
    is_dst = (
        (month > 3 or (month == 3 and day >= 8)) and
        (month < 11 or (month == 11 and day <= 7))
    )
    offset_hours = 4 if is_dst else 5
    et_hour = (now.hour - offset_hours) % 24
    et_minute = now.minute
    # Weekday check in ET (may shift by one day near midnight UTC)
    # Compute ET weekday by rolling UTC clock back the offset.
    from datetime import timedelta as _td
    et_now = now - _td(hours=offset_hours)
    if et_now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    minutes_since_midnight = et_hour * 60 + et_minute
    return 570 <= minutes_since_midnight < 960  # 9:30 → 16:00 ET


def _evidence_enforce_enabled() -> bool:
    """When true, the evidence-worker notional multiplier is APPLIED
    to live fires. When false (default), the multiplier is logged
    but ``notional`` is not reduced — shadow mode so the operator
    can observe scores before enforcement.
    """
    return (os.environ.get("RISEDUAL_EVIDENCE_ENFORCE") or "").strip() in (
        "1", "true", "True", "yes", "on",
    )


async def _last_symbol_fire_at(db: Any, symbol: str) -> Optional[datetime]:
    """Look up the ``opened_at`` of the most recent BUY into ``symbol``.

    Reads from ``equity_live_trades`` and returns the datetime (any
    status — open or closed). ``None`` when the symbol has never fired
    or the read fails.
    """
    if db is None:
        return None
    try:
        row = await db.equity_live_trades.find_one(
            {"symbol": symbol, "broker_id": "public", "side": "BUY"},
            {"opened_at": 1},
            sort=[("opened_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[public-live] cooldown lookup failed: %s", exc)
        return None
    if not row:
        return None
    ts = row.get("opened_at")
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


async def _log_skip(
    db: Any,
    *,
    symbol: str,
    reason: str,
    intent: Mapping[str, Any] | None = None,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """Persist a structured skip event so the operator can see WHY
    the executor rejected an intent.

    Every ``return None`` path in :func:`maybe_route_live` should
    precede itself with a call here. Reads to ``intent_skip_log`` power
    the ``/api/admin/intent-audit`` endpoints — before this helper
    existed, ~100% of scanner targets ended up ``executor_skipped``
    with an empty ``executor_skipped_reason``, blinding the operator
    to which gate was blocking live trades.

    Non-blocking: any Mongo write failure is swallowed with a
    debug log — an observability write must never take down a
    trading gate.
    """
    if db is None:
        return
    doc: dict[str, Any] = {
        "ts": datetime.now(timezone.utc),
        "symbol": symbol or "",
        "reason": reason,
        "detail": dict(detail or {}),
    }
    if intent:
        doc["strategy_id"] = intent.get("strategy_id")
        doc["scan_id"] = intent.get("scan_id")
        doc["prediction_id"] = intent.get("prediction_id")
        doc["source_signal"] = intent.get("source_signal")
        doc["confidence"] = intent.get("confidence")
        doc["direction"] = intent.get("direction")
    try:
        await db.intent_skip_log.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[public-live] intent_skip_log write failed: %s", exc)


async def _evidence_multiplier(db: Any, strategy_id: str) -> tuple[float, dict]:
    """Look up the notional multiplier for a strategy from the
    Evidence Worker's ``strategy_evidence_scores`` collection.

    Returns ``(multiplier, meta)``. When no row exists (strategy
    untested), the caller gets ``UNTESTED_NOTIONAL_MULT`` (default
    0.25) — a hard-limited exposure until the worker has scored the
    strategy. Any read failure or missing collection falls back to
    ``1.0`` (no reduction) so a Mongo hiccup can never inflate the
    trade above its baseline sizing.

    ``meta`` carries the raw stats so ``equity_live_trades`` rows
    are self-describing (``evidence_hit_rate``, ``evidence_sharpe``,
    ``evidence_trade_count``, ``evidence_bucket``) — critical for
    post-mortems.
    """
    default_untested = 0.25
    try:
        raw = (os.environ.get("PUBLIC_LIVE_UNTESTED_NOTIONAL_MULT") or "").strip()
        if raw:
            default_untested = max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        pass

    if db is None:
        return 1.0, {"bucket": "no_db"}
    try:
        row = await db.strategy_evidence_scores.find_one(
            {"strategy_id": strategy_id}, {"_id": 0},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[public-live] evidence lookup failed: %s", exc)
        return 1.0, {"bucket": "lookup_error"}
    if not row:
        return default_untested, {"bucket": "untested"}
    return float(row.get("notional_multiplier") or default_untested), {
        "bucket": row.get("bucket") or "unknown",
        "hit_rate": row.get("hit_rate"),
        "sharpe": row.get("sharpe"),
        "trade_count": row.get("trade_count"),
    }


# ── Public.com client construction ──────────────────────────────────


def _resolve_connect_creds(db: Any) -> Optional[tuple[str, str]]:
    """Fetch Public.com (secret_key, account_id) from the broker
    connection vault. Returns ``None`` when no row exists OR the
    operator hasn't connected Public yet.

    Synchronous wrapper — Mongo motor calls have to be awaited
    by the caller. We keep this as a coroutine for parity with
    other live-exec helpers.
    """
    return None  # placeholder; real lookup in async helper below


async def _aresolve_connect_creds(db: Any) -> Optional[tuple[str, str]]:
    """Async version of :func:`_resolve_connect_creds`. Pulls the most
    recent active Public.com connection record.
    """
    if db is None:
        return None
    try:
        row = await db.broker_connections.find_one(
            {"broker_id": "public", "status": "connected"},
            {"_id": 0, "api_key": 1, "api_secret": 1},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[public-live] broker_connections lookup failed: %s", exc,
        )
        return None
    if not row:
        return None
    secret_key = (row.get("api_key") or "").strip()
    account_id = (row.get("api_secret") or "").strip()
    if not secret_key or not account_id:
        return None
    return secret_key, account_id


def _public_client(secret_key: str, account_id: str):
    """Instantiate :class:`services.broker_service.PublicTradingService`.

    The service handles the secret-→-JWT exchange + caching, so we
    just hand it the same field shape the broker-connect panel uses.
    """
    try:
        from services.broker_service import PublicTradingService
        return PublicTradingService(api_key=secret_key, api_secret=account_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[public-live] PublicTradingService init failed: %s", exc)
        return None


# ── Quote helper ────────────────────────────────────────────────────


async def _fetch_mark_price(symbol: str) -> Optional[float]:
    """Best-effort live quote for sizing. Falls back to Alpaca's
    equity quotes service when wired (since we're keeping Alpaca
    quotes alive). Never raises.
    """
    try:
        from services.price_provider import get_quote
        q = await get_quote(symbol)
        if q and q.get("price"):
            return float(q["price"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[public-live] mark-price probe failed: %s", exc)
    return None


# ── Main routing entry point ────────────────────────────────────────


async def maybe_route_live(
    db: Any,
    *,
    intent: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Place a Public.com live equity order if all gates pass.

    Called from Alpha's equity decision path. Best-effort by doctrine
    — returns ``None`` for every skip case below and NEVER raises out:

      * ``RISEDUAL_PUBLIC_LIVE_EXEC`` unset
      * Operator hasn't connected Public yet
      * Symbol not in allowlist (when set)
      * Quote provider unavailable
      * Public.com auth fails (PublicTradingService swallows + returns None)
      * Duplicate open row for this symbol (idempotency)

    Two-sided routing (2026-06-26 — Alpaca removed from the stack):

      * **BUY/LONG** signal + no current position → OPEN long
      * **BUY/LONG** signal + already long → idempotency skip (dedup)
      * **SELL/SHORT** signal + currently long → CLOSE long (sell qty)
      * **SELL/SHORT** signal + no position → SKIP (Public is a cash
        broker — opening new shorts requires margin/locate which the
        $194 cash buying-power account doesn't support today)

    On success, returns the inserted ``equity_live_trades`` row dict
    (with the Public order id) so the orchestrator can log lineage.
    """
    if not _live_exec_enabled():
        await _log_skip(db, symbol=(intent.get("symbol") or "").upper(),
                        reason="live_exec_disabled", intent=intent)
        return None

    # 2026-08-11 — Session gate. Public.com's broker API rejects
    # fractional / notional-based orders outside 9:30-16:00 ET
    # (extended hours requires whole-share LIMIT orders with an
    # explicit equityMarketSession=EXTENDED flag). Firing our
    # fractional MARKET orders after hours guarantees a broker
    # rejection. Skip cleanly with market_closed instead.
    if _rth_only_enabled() and not _in_regular_session():
        await _log_skip(db, symbol=(intent.get("symbol") or "").upper(),
                        reason="market_closed", intent=intent,
                        detail={"gate": "rth_only",
                                "note": "Public.com fractional orders require RTH (9:30-16:00 ET)."})
        return None

    symbol = (intent.get("symbol") or "").upper()
    direction = (intent.get("direction") or intent.get("action") or "").upper()
    if not symbol:
        await _log_skip(db, symbol="", reason="empty_symbol", intent=intent)
        return None

    # Direction classification → side + intent_kind.
    if direction in ("BUY", "LONG", "STRONG_BUY", "WEAK_BUY", "UP", "BULLISH"):
        intent_kind = "open_long"
    elif direction in ("SELL", "SHORT", "STRONG_SELL", "WEAK_SELL",
                       "DOWN", "BEARISH"):
        intent_kind = "close_long"
    else:
        logger.info(
            "[public-live] symbol=%s SKIPPED — non-directional signal: %r",
            symbol, direction,
        )
        await _log_skip(db, symbol=symbol, reason="non_directional",
                        intent=intent, detail={"direction": direction})
        return None

    # 2026-06-18: Confidence floor gate. Refuses live execution when
    # Alpha's own conviction is below ``PUBLIC_LIVE_CONFIDENCE_FLOOR``
    # (default 0.65). With the multi-brain peer veto severed in
    # standalone mode, this floor is the cheapest substitute. The 95%
    # saturation cap from earlier today caps the upper end; this
    # floor sets the lower end.
    confidence = float(intent.get("confidence") or 0.0)
    floor = _live_confidence_floor()
    if confidence < floor:
        logger.info(
            "[public-live] symbol=%s SKIPPED — confidence %.2f below floor %.2f",
            symbol, confidence, floor,
        )
        await _log_skip(db, symbol=symbol, reason="confidence_floor",
                        intent=intent,
                        detail={"confidence": confidence, "floor": floor})
        return None

    allow = _allowed_symbols()
    if allow is not None and symbol not in allow:
        logger.info(
            "[public-live] symbol=%s not in PUBLIC_LIVE_SYMBOLS allowlist — skip",
            symbol,
        )
        await _log_skip(db, symbol=symbol, reason="not_in_allowlist",
                        intent=intent, detail={"allowlist_size": len(allow)})
        return None

    # 2026-07-30 — Per-symbol re-fire cooldown. Applies to OPEN_LONG
    # only; SELL/close paths bypass because closing a stale position
    # must never be gated by a cooldown. Preview forensic showed
    # JPM/UNH/SPY fired 3× each within the same day.
    if intent_kind == "open_long":
        cooldown_min = _symbol_cooldown_min()
        if cooldown_min > 0:
            last_at = await _last_symbol_fire_at(db, symbol)
            if last_at is not None:
                delta_min = (datetime.now(timezone.utc) - last_at).total_seconds() / 60.0
                if delta_min < cooldown_min:
                    logger.info(
                        "[public-live] symbol=%s SKIPPED — cooldown "
                        "(%.1f min since last fire, need %d)",
                        symbol, delta_min, cooldown_min,
                    )
                    await _log_skip(db, symbol=symbol, reason="symbol_cooldown",
                                    intent=intent,
                                    detail={"delta_min": round(delta_min, 1),
                                            "required_min": cooldown_min})
                    return None

        # 2026-07-30 — Chasing filter. If the symbol has already run
        # this far today, we're buying the top. Skip. Data outage
        # (returns None) does NOT block the trade — we can't punish
        # a legit signal for a provider hiccup — but the miss is
        # logged so the operator can catch systemic outages.
        max_move = _max_intraday_move_pct()
        if max_move > 0:
            move_pct = await _intraday_move_pct(symbol)
            if move_pct is None:
                logger.info(
                    "[public-live] symbol=%s chasing-filter data unavailable — "
                    "allowing fire (fail-open)",
                    symbol,
                )
            elif abs(move_pct) >= max_move:
                logger.info(
                    "[public-live] symbol=%s SKIPPED — chasing filter "
                    "(intraday move %.2f%%, cap %.2f%%)",
                    symbol, move_pct, max_move,
                )
                await _log_skip(db, symbol=symbol, reason="chasing_filter",
                                intent=intent,
                                detail={"move_pct": round(move_pct, 2),
                                        "cap_pct": max_move})
                return None

    # Connect-state gate
    creds = await _aresolve_connect_creds(db)
    if creds is None:
        logger.warning(
            "[public-live] symbol=%s SKIPPED — no active Public.com "
            "connection (operator must connect via /api/broker/connect)",
            symbol,
        )
        await _log_skip(db, symbol=symbol, reason="no_broker_creds", intent=intent)
        return None
    secret_key, account_id = creds

    # 2026-06-18: Pre-trade cash check. Refuses live execution when
    # account doesn't have enough settled buying power. Prevents
    # noisy place_order rejections from Public.com when Alpha's
    # signal fans out faster than settled cash. Best-effort — if
    # the account-fetch fails for any reason we fall through to the
    # broker's own rejection (worst case = a logged 4xx).
    notional_baseline = _fixed_notional_usd()

    # 2026-07-30 — Ring 3: Evidence-based notional multiplier.
    # Untested strategies fire at 0.25× baseline; strategies with a
    # positive Sharpe from the nightly Evidence Worker scale up to
    # 1.0×. When ``RISEDUAL_EVIDENCE_ENFORCE`` is unset (default —
    # shadow mode) the multiplier is computed and recorded in the
    # trade row for post-hoc analysis, but ``notional`` is not
    # reduced. Flip the env flag to 1 to enforce.
    strategy_id_raw = (intent.get("strategy_id") or "signal_dispatcher:v1").strip()
    evidence_mult, evidence_meta = await _evidence_multiplier(db, strategy_id_raw)
    enforce_evidence = _evidence_enforce_enabled()
    notional = notional_baseline * evidence_mult if enforce_evidence else notional_baseline
    # Never let the multiplier bring notional below Public's $1 floor.
    if notional < 1.0:
        notional = 1.0
    if enforce_evidence and evidence_mult < 1.0:
        logger.info(
            "[public-live] symbol=%s strategy=%s evidence mult=%.2f "
            "notional %.2f → %.2f (enforced)",
            symbol, strategy_id_raw, evidence_mult,
            notional_baseline, notional,
        )
    elif evidence_mult < 1.0:
        logger.info(
            "[public-live] symbol=%s strategy=%s evidence mult=%.2f "
            "notional %.2f (SHADOW — not reduced; set "
            "RISEDUAL_EVIDENCE_ENFORCE=1 to apply)",
            symbol, strategy_id_raw, evidence_mult, notional_baseline,
        )

    try:
        client_pre = _public_client(secret_key, account_id)
        if client_pre is not None:
            acct = client_pre.get_account()
            if acct is not None:
                bp = float(acct.get("buying_power") or 0.0)
                if bp < notional:
                    logger.warning(
                        "[public-live] symbol=%s SKIPPED — buying_power $%.2f "
                        "< notional $%.2f", symbol, bp, notional,
                    )
                    await _log_skip(db, symbol=symbol,
                                    reason="insufficient_buying_power",
                                    intent=intent,
                                    detail={"buying_power": bp,
                                            "notional_required": notional})
                    return None
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[public-live] pre-trade account check failed (non-fatal): %s",
            exc,
        )

    # Idempotency / direction-aware position check.
    # For OPEN_LONG: refuse to open a duplicate live row.
    # For CLOSE_LONG: require an actual open long position to close
    # (either tracked by us in ``equity_live_trades`` OR live on the
    # broker — operator may have manually bought a position).
    current_qty = 0.0
    existing_row = None
    if db is not None:
        try:
            existing_row = await db.equity_live_trades.find_one(
                {"symbol": symbol, "status": "open", "broker_id": "public"},
                {"_id": 1, "size": 1, "trade_id": 1},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[public-live] idempotency check failed: %s", exc)

    if intent_kind == "open_long" and existing_row is not None:
        logger.info(
            "[public-live] symbol=%s already has open live row — skip dupe",
            symbol,
        )
        return None

    if intent_kind == "close_long":
        # Ask the broker for the current position. Don't rely on the
        # Mongo row alone — the operator may have an untracked
        # position from before tracking started (SPCX/VRPX-style).
        try:
            client_pos = _public_client(secret_key, account_id)
            if client_pos is not None:
                positions = client_pos.get_positions() or []
                for p in positions:
                    if (p.get("symbol") or "").upper() == symbol:
                        try:
                            current_qty = float(p.get("qty") or 0.0)
                        except (TypeError, ValueError):
                            current_qty = 0.0
                        break
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[public-live] positions lookup failed (non-fatal): %s", exc,
            )
        if current_qty <= 0:
            logger.info(
                "[public-live] symbol=%s SKIPPED — SELL signal but no "
                "long position to close (Public cash account; opening "
                "new shorts not supported)", symbol,
            )
            return None

    # Sizing.
    # OPEN_LONG: Use $notional → qty = ceil(notional / mark, 4 dp).
    #   Public.com enforces a $1.00 minimum order amount. Plain
    #   ``round()`` can produce ``qty * mark < $1.00`` when the
    #   fractional rounding truncates downward (e.g. $1.00 / $332.69
    #   = 0.003006 rounded to 0.003 = $0.998 — rejected). We round UP
    #   to the nearest 0.0001 share to guarantee we clear the floor.
    # CLOSE_LONG: sell the full current position (qty already known
    # from the broker positions check above).
    mark = await _fetch_mark_price(symbol)
    if not mark or mark <= 0:
        logger.warning("[public-live] symbol=%s SKIPPED — no mark price", symbol)
        return None
    if intent_kind == "close_long":
        qty = current_qty
    else:
        # math.ceil to 4 dp ensures qty * mark > notional (clears
        # Public.com's $1.00 minimum even after fractional rounding).
        import math
        qty = math.ceil((notional / mark) * 10000.0) / 10000.0
    if qty <= 0:
        logger.warning(
            "[public-live] symbol=%s SKIPPED — computed qty %.6f ≤ 0",
            symbol, qty,
        )
        return None

    # Execute
    client = _public_client(secret_key, account_id)
    if client is None:
        return None
    order_side = "buy" if intent_kind == "open_long" else "sell"
    client_order_id = str(uuid.uuid4())
    _submit_start_ns = time.time_ns()
    try:
        resp = client.place_order(
            symbol=symbol, qty=qty, side=order_side, order_type="market",
        )
    except Exception as exc:  # noqa: BLE001
        _ack_ms = (time.time_ns() - _submit_start_ns) // 1_000_000
        logger.error(
            "[public-live] CRITICAL — place_order raised symbol=%s "
            "kind=%s: %s", symbol, intent_kind, exc,
        )
        try:
            from services import broker_comparison_service
            broker_comparison_service.record_public_submit(
                client_order_id=client_order_id, broker_order_id="",
                symbol=symbol, side=order_side.upper(), qty=qty,
                limit_price=float(mark or 0.0),
                submit_latency_ms=int(_ack_ms), ack_latency_ms=int(_ack_ms),
                fill_price=None, status="exception", error=exc.__class__.__name__,
            )
        except Exception:  # noqa: BLE001
            pass
        return None
    _ack_ms = (time.time_ns() - _submit_start_ns) // 1_000_000
    if not resp:
        logger.warning(
            "[public-live] symbol=%s place_order returned empty — "
            "Public.com rejected (check vault token + connect)",
            symbol,
        )
        try:
            from services import broker_comparison_service
            broker_comparison_service.record_public_submit(
                client_order_id=client_order_id, broker_order_id="",
                symbol=symbol, side=order_side.upper(), qty=qty,
                limit_price=float(mark or 0.0),
                submit_latency_ms=int(_ack_ms), ack_latency_ms=int(_ack_ms),
                fill_price=None, status="rejected", error="empty_response",
            )
        except Exception:  # noqa: BLE001
            pass
        return None

    order_id = resp.get("id") or ""
    try:
        from services import broker_comparison_service
        broker_comparison_service.record_public_submit(
            client_order_id=client_order_id, broker_order_id=str(order_id),
            symbol=symbol, side=order_side.upper(), qty=qty,
            limit_price=float(mark or 0.0),
            submit_latency_ms=int(_ack_ms), ack_latency_ms=int(_ack_ms),
            fill_price=float(resp.get("fillPrice") or resp.get("filled_avg_price") or 0.0) or None,
            status=str(resp.get("status") or "accepted"), error=None,
        )
    except Exception:  # noqa: BLE001
        pass
    trade_id = str(uuid.uuid4())
    if intent_kind == "close_long":
        # CLOSE: update the existing open row (if any) to ``closed``.
        # If there was no Mongo row but the position was real, still
        # log a synthetic close row so PnL can be reconciled later.
        close_doc = {
            "closed_at": datetime.now(timezone.utc),
            "close_price": mark,
            "close_order_id": order_id,
            "status": "closed",
            "close_reason": "alpha_sell_signal",
        }
        if db is not None and existing_row is not None:
            try:
                await db.equity_live_trades.update_one(
                    {"_id": existing_row["_id"]},
                    {"$set": close_doc},
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[public-live] CRITICAL — sell order placed but Mongo "
                    "close-update failed symbol=%s order_id=%s: %s",
                    symbol, order_id, exc,
                )
        logger.info(
            "[public-live] CLOSE symbol=%s qty=%.6f @ $%.2f order_id=%s "
            "(SELL signal — closed long position)",
            symbol, qty, mark, order_id,
        )
        return {
            "trade_id": existing_row.get("trade_id") if existing_row else trade_id,
            "broker_id": "public",
            "symbol": symbol,
            "direction": "LONG",
            "side": "SELL",
            "intent_kind": "close_long",
            "size": qty,
            "close_price": mark,
            "status": "closed",
            "broker_order_id": order_id,
            "closed_at": datetime.now(timezone.utc),
            "confidence": float(intent.get("confidence") or 0.0),
            "source_signal": intent.get("source_signal"),
        }

    # OPEN_LONG path — original behaviour preserved.
    row = {
        "trade_id": trade_id,
        "broker_id": "public",
        "symbol": symbol,
        "direction": "LONG",
        "side": "BUY",
        "intent_kind": "open_long",
        "size": qty,
        # 2026-07-30 — persist both the historical field name
        # (``live_notional_usd``) and the normalized ``notional``
        # so tape queries stop mysteriously returning $0.00.
        "live_notional_usd": notional,
        "notional": notional,
        "notional_baseline": notional_baseline,
        "evidence_multiplier": evidence_mult,
        "evidence_enforced": enforce_evidence,
        "entry_price": mark,
        "status": "open",
        "broker_order_id": order_id,
        "opened_at": datetime.now(timezone.utc),
        # 2026-07-30 — full confidence provenance stored on the row
        # so post-mortems don't need to re-join predictions.
        "confidence": float(intent.get("confidence") or 0.0),
        "raw_confidence": float(intent.get("raw_confidence") or 0.0),
        "calibrated_confidence": intent.get("calibrated_confidence"),
        "strategy_id": strategy_id_raw,
        "regime": intent.get("regime"),
        "predicted_move_pct": intent.get("predicted_move_pct"),
        "sovereign_decision_id": intent.get("sovereign_decision_id"),
        "prediction_id": intent.get("prediction_id"),
        "source_signal": intent.get("source_signal"),
        **{f"evidence_{k}": v for k, v in evidence_meta.items()},
    }
    if db is not None:
        try:
            await db.equity_live_trades.insert_one(dict(row))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[public-live] CRITICAL — order placed on Public.com but "
                "Mongo insert failed symbol=%s order_id=%s qty=%.6f: %s",
                symbol, order_id, qty, exc,
            )
    logger.info(
        "[public-live] FILL symbol=%s qty=%.6f @ $%.2f notional=$%.2f "
        "order_id=%s trade_id=%s",
        symbol, qty, mark, notional, order_id, trade_id,
    )
    return row


__all__ = [
    "maybe_route_live",
    "_live_exec_enabled",
    "_fixed_notional_usd",
    "_allowed_symbols",
]
