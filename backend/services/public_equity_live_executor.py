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
from datetime import datetime, timezone, timedelta
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


# ── Adapter capability flags ─────────────────────────────────────────
#
# 2026-09-11 (Foundation v2.1 port): explicit short-sale capability.
# Public.com does NOT support short sales through this adapter (long-
# only per the doctrine pin at the top of this file). The executor
# will refuse to submit any ``open_short`` intent while this flag is
# False, log ``short_signal_only``, and let the compact authority
# receipt attribute the block to ``roadguard``. When a broker adapter
# that supports shorts is wired in, override this via
# ``PUBLIC_LIVE_SUPPORTS_SHORTS=1`` — you'll also need to teach
# ``_maybe_route_live`` how to translate ``open_short`` into the
# broker's short-sale order type.
SUPPORTS_SHORT_SALES: bool = os.environ.get(
    "PUBLIC_LIVE_SUPPORTS_SHORTS", "0",
).strip().lower() in ("1", "true", "on", "yes")


# ── Short-execution env knobs (P1-A, 2026-02) ─────────────────────
#
# Two-flag ladder as agreed with the operator:
#
#   ENABLE_SHORT_SIGNALS   → routers/scanners may EMIT short intents
#                            (this is the "learning" mode; default ON)
#   ENABLE_SHORT_EXECUTION → the REST short executor may SUBMIT them
#                            to Public (default OFF)
#
# When ENABLE_SHORT_EXECUTION=0, ``open_short`` / ``close_short``
# intent_kinds still classify correctly through the router, but the
# executor logs a ``short_execution_disabled`` skip instead of hitting
# the broker. This lets us gather signal-quality data on the short
# side without any real exposure until the ladder + eligibility path
# is proven in prod.
#
# The first-fire notional is capped separately from the long path so
# Alpha's first live short is a small canary, NOT the standard $350.
def _short_execution_enabled() -> bool:
    return (os.environ.get("ENABLE_SHORT_EXECUTION") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _short_first_fire_notional_usd() -> float:
    """Canary size for the first live short. Default $25 — same
    conservative bound as the initial long canary. Operator can raise
    via ``PUBLIC_LIVE_SHORT_FIRST_NOTIONAL_USD`` once the first
    round-trip short reconciles cleanly.

    Whole shares only — the REST short path never sends fractional
    quantities, so this floor is expressed in USD but converted via
    ``compute_whole_share_qty`` at sizing time.
    """
    raw = (os.environ.get("PUBLIC_LIVE_SHORT_FIRST_NOTIONAL_USD") or "").strip()
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


def _short_max_htb_rate_pct() -> Optional[float]:
    """Optional policy cap on hard-to-borrow rate. Unset → no cap;
    set to a float → any HTB rate above the cap fails the ladder
    with reason ``htb_rate_too_expensive``."""
    raw = (os.environ.get("PUBLIC_LIVE_SHORT_MAX_HTB_PCT") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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


def _live_confidence_floor_chop() -> float:
    """Confidence floor override when the current regime is chop.

    2026-02: Alpha now has a mean-reversion pattern family that
    arms during chop regimes (see ``alpha_day_trader.MEAN_REVERT_PATTERNS``).
    Mean-reversion setups naturally score lower than momentum setups
    (thinner risk-reward, no volume expansion tailwind), so the 0.65
    momentum-era floor silently prevented every chop-day trade from
    firing. Default 0.55 keeps a real floor while letting the chop
    playbook actually fire. Override via ``PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP``.
    """
    raw = (os.environ.get("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP") or "").strip()
    if not raw:
        return 0.55
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.55
    if v < 0.0:
        return 0.0
    if v > 0.95:
        return 0.95
    return v


def _effective_confidence_floor(intent: Mapping[str, Any]) -> tuple[float, str]:
    """Return (floor, label) — label is ``chop`` or ``default``.

    Reads the intent's ``regime`` / ``fast_regime`` tags stamped by
    Alpha and picks the chop-relaxed floor when either matches a chop
    token. Missing regime tags → default floor (no downside vs. the
    prior behaviour).
    """
    def _has_chop(*labels: Any) -> bool:
        for lbl in labels:
            if not lbl:
                continue
            low = str(lbl).lower()
            if "chop" in low or "meanrevert" in low or "range" in low:
                return True
        return False

    if _has_chop(intent.get("regime"), intent.get("fast_regime")):
        return _live_confidence_floor_chop(), "chop"
    return _live_confidence_floor(), "default"


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
    """Convenience wrapper: return just the signed move % (or None).

    See :func:`_intraday_move_detail` for the audit-rich version.
    """
    detail = await _intraday_move_detail(symbol)
    if detail is None:
        return None
    return detail[0]


async def _intraday_move_detail(symbol: str) -> Optional[tuple[float, float, float]]:
    """Return ``(move_pct, previous_close, today_close)`` so callers
    can log the actual reference anchors used by the chasing filter,
    or ``None`` when the data providers are unavailable.

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
    return move_pct, prev_close, today_close


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
    now_ts = datetime.now(timezone.utc)
    dedup_window_seconds = 300  # 5-minute rolling window
    direction = (intent.get("direction") if intent else None) or None
    dedup_key = {
        "symbol": symbol or "",
        "reason": reason,
        "direction": direction,
    }
    # Upsert into a bounded rolling window. Same (symbol, reason,
    # direction) within 5 min → increment ``refire_count`` and update
    # ``last_seen`` + latest ``detail``. Different window → new row.
    # The audit endpoint reads unique rows for headline numbers;
    # ``refire_count`` surfaces amplification separately.
    try:
        cutoff = now_ts - timedelta(seconds=dedup_window_seconds)
        existing = await db.intent_skip_log.find_one({
            **dedup_key,
            "last_seen": {"$gte": cutoff},
        }, sort=[("last_seen", -1)])
    except Exception:  # noqa: BLE001
        existing = None

    if existing is not None:
        try:
            await db.intent_skip_log.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "last_seen": now_ts,
                        "ts": now_ts,   # keep ts fresh for compat with old readers
                        "detail": dict(detail or {}),
                        "prediction_id": (intent or {}).get("prediction_id"),
                    },
                    "$inc": {"refire_count": 1},
                },
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("[public-live] intent_skip_log upsert failed: %s", exc)
            # Fall through to insert as a new row.

    doc: dict[str, Any] = {
        "ts": now_ts,
        "first_seen": now_ts,
        "last_seen": now_ts,
        "refire_count": 0,
        "symbol": symbol or "",
        "reason": reason,
        "direction": direction,
        "detail": dict(detail or {}),
    }
    if intent:
        doc["strategy_id"] = intent.get("strategy_id")
        doc["scan_id"] = intent.get("scan_id")
        doc["prediction_id"] = intent.get("prediction_id")
        doc["source_signal"] = intent.get("source_signal")
        doc["confidence"] = intent.get("confidence")
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

    # 2026-09-11 (Foundation v2.1) — hardware kill switch first.
    # A corrupt state file / repeated errors / drawdown breach must
    # halt the broker path even if every other gate looks fine.
    # Fail-closed: the switch reads its own state through a schema
    # validator that returns TRIPPED with FAIL_CLOSED:... on any
    # malformed row, so the operator sees WHY execution paused.
    try:
        from services import alpha_hardware_kill_switch as _hw
        tripped, hw_reason = _hw.check()
    except Exception as exc:  # noqa: BLE001
        # If even the switch subsystem crashes, fail closed — the
        # authority path is not allowed to swallow safety failures.
        tripped, hw_reason = True, f"FAIL_CLOSED:kill_switch_exception:{type(exc).__name__}"
    if tripped:
        await _log_skip(
            db, symbol=(intent.get("symbol") or "").upper(),
            reason="hw_kill_switch_tripped", intent=intent,
            detail={"kill_reason": hw_reason or "unknown"},
        )
        return None

    # 2026-09-11 (Foundation v2.1) — short-sale capability gate.
    # If Alpha emits an ``open_short`` intent but the current
    # broker adapter doesn't support short sales, we must refuse
    # to submit AND we must not silently map to a plain SELL
    # (which would either be a no-op on a no-position account or
    # close an unrelated existing long). Log the intent as
    # ``short_signal_only`` so the panel can count it separately.
    #
    # 2026-02 (P1-B) — this gate protects OPEN_SHORT only. Explicit
    # BUY_TO_COVER / CLOSE_SHORT actions are always allowed to reach
    # the router below because covering an existing short is an
    # *exit*, not a new short exposure, and must never be blocked by
    # a capability gate on short *entries*.
    _explicit_close = (intent.get("intent_action") or "").upper() in (
        "SELL_TO_CLOSE", "CLOSE_LONG", "BUY_TO_COVER", "CLOSE_SHORT",
    )
    if (
        (intent.get("direction") or "").upper() in ("SHORT", "SELL_SHORT", "OPEN_SHORT")
        and not _explicit_close
    ):
        if not SUPPORTS_SHORT_SALES:
            await _log_skip(
                db, symbol=(intent.get("symbol") or "").upper(),
                reason="short_signal_only", intent=intent,
                detail={"broker": "public.com",
                        "note": "adapter has supports_short_sales=False"},
            )
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

    # Direction + position classification (P1-B position-aware exit router).
    #
    # The router centralises the "SELL against long → close_long, never
    # open_short" / "BUY against short → close_short, never open_long"
    # safety property. It is broker-position-aware, so we need a fast
    # broker-position probe first when the intent could be an exit
    # (SELL, BUY_TO_COVER, or explicit exit_only).
    #
    # Fast path — for pure BUY signals with no ``exit_only`` and no
    # explicit action, we skip the broker probe (position defaults to
    # ``None`` and the router returns ``open_long``). The idempotency
    # block below still guards against duplicate open rows.
    from services.alpha_exit_router import classify as _classify_intent
    exit_only_flag = bool(intent.get("exit_only"))
    intent_action_raw = intent.get("intent_action")
    intent_action = (intent_action_raw or "").upper() if intent_action_raw else None
    _direction_upper = (intent.get("direction") or intent.get("action") or "").upper()
    # Decide whether to probe the broker up-front. Probe when:
    #  - ``exit_only=True`` (must not open, must confirm the close target exists)
    #  - explicit CLOSE_LONG / CLOSE_SHORT / SELL_TO_CLOSE / BUY_TO_COVER
    #  - direction is SELL-family (may close a long)
    #  - we hold ANY open position for this symbol (Mongo hint) — the
    #    position may be a SHORT that a plain BUY should cover, so we
    #    must resolve before the router can classify safely.
    #  Otherwise (fresh BUY with no open row) we take the fast path
    #  and let the existing idempotency check catch reversal cases.
    _need_early_position = (
        exit_only_flag
        or intent_action in (
            "SELL_TO_CLOSE", "CLOSE_LONG", "BUY_TO_COVER", "CLOSE_SHORT",
        )
        or _direction_upper in ("SELL", "STRONG_SELL", "WEAK_SELL",
                                 "DOWN", "BEARISH")
    )
    # Cheap Mongo probe: if any open row exists for this symbol, we
    # need broker truth before the router runs.
    if not _need_early_position and db is not None:
        try:
            _mongo_open = await db.equity_live_trades.find_one(
                {"symbol": symbol, "status": "open", "broker_id": "public"},
                {"_id": 1, "direction": 1},
            )
            if _mongo_open is not None:
                _need_early_position = True
        except Exception:  # noqa: BLE001
            _mongo_open = None
    _early_broker_position_side: Optional[str] = None
    _early_broker_position_qty: float = 0.0
    if _need_early_position:
        try:
            _early_creds = await _aresolve_connect_creds(db)
            if _early_creds is not None:
                _sk, _acct = _early_creds
                _client_probe = _public_client(_sk, _acct)
                if _client_probe is not None:
                    _positions = _client_probe.get_positions() or []
                    for _p in _positions:
                        if (_p.get("symbol") or "").upper() == symbol:
                            try:
                                _qty = float(_p.get("qty") or 0.0)
                            except (TypeError, ValueError):
                                _qty = 0.0
                            _side = (_p.get("side") or "").lower()
                            if _side not in ("long", "short"):
                                # Alpaca-style adapters report side by qty sign.
                                _side = "long" if _qty >= 0 else "short"
                            _early_broker_position_side = _side
                            _early_broker_position_qty = abs(_qty)
                            break
        except Exception as _pos_exc:  # noqa: BLE001
            logger.debug(
                "[public-live] early-position probe failed for %s: %s",
                symbol, _pos_exc,
            )

    _cls = _classify_intent(
        direction=_direction_upper,
        exit_only=exit_only_flag,
        intent_action=intent_action,
        broker_position_side=_early_broker_position_side,
        broker_position_qty=_early_broker_position_qty,
    )
    if _cls.kind == "no_op":
        logger.info(
            "[public-live] symbol=%s SKIPPED — exit-router no_op (%s, exit_only=%s)",
            symbol, _cls.reason, exit_only_flag,
        )
        await _log_skip(
            db, symbol=symbol, reason=f"router_{_cls.reason}",
            intent=intent,
            detail={
                "router_kind": _cls.kind,
                "router_reason": _cls.reason,
                "exit_only": exit_only_flag,
                "intent_action": intent_action,
                "direction": _direction_upper,
                "broker_position_side": _early_broker_position_side,
                "broker_position_qty": _early_broker_position_qty,
            },
        )
        return None
    intent_kind = _cls.kind
    # Router-derived close qty (broker-authoritative). Only meaningful
    # for close_* kinds; opens overwrite this via notional sizing below.
    router_close_qty: float = float(_cls.close_qty or 0.0)

    # P1-A — open_short is now supported via a direct-REST path
    # (``services.public_short_executor``) that speaks Public's current
    # (April 2026) short-sale contract with ``openCloseIndicator=OPEN``.
    # We still hard-gate on ``ENABLE_SHORT_EXECUTION``: when the flag
    # is unset, we log a ``short_execution_disabled`` skip so signal
    # analytics can still count the emission without any real exposure.
    if intent_kind == "open_short" and not _short_execution_enabled():
        logger.info(
            "[public-live] symbol=%s SKIPPED — open_short intent but "
            "ENABLE_SHORT_EXECUTION=false (signal recorded, no submit)",
            symbol,
        )
        await _log_skip(
            db, symbol=symbol, reason="short_execution_disabled", intent=intent,
            detail={
                "broker": "public.com",
                "intent_kind": intent_kind,
                "router_reason": _cls.reason,
                "note": "P1-A ladder ready; flip ENABLE_SHORT_EXECUTION=1 to arm",
            },
        )
        return None

    # 2026-06-18: Confidence floor gate. Refuses live execution when
    # Alpha's own conviction is below ``PUBLIC_LIVE_CONFIDENCE_FLOOR``
    # (default 0.65). With the multi-brain peer veto severed in
    # standalone mode, this floor is the cheapest substitute. The 95%
    # saturation cap from earlier today caps the upper end; this
    # floor sets the lower end.
    confidence = float(intent.get("confidence") or 0.0)
    floor, floor_label = _effective_confidence_floor(intent)
    if confidence < floor:
        logger.info(
            "[public-live] symbol=%s SKIPPED — confidence %.2f below floor %.2f (%s)",
            symbol, confidence, floor, floor_label,
        )
        await _log_skip(db, symbol=symbol, reason="confidence_floor",
                        intent=intent,
                        detail={"confidence": confidence, "floor": floor,
                                "floor_label": floor_label,
                                "regime": intent.get("regime"),
                                "fast_regime": intent.get("fast_regime")})
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

    # 2026-07-30 — Per-symbol re-fire cooldown. Applies to OPEN paths
    # only (open_long AND open_short); close paths bypass because
    # closing a stale position must never be gated by a cooldown.
    if intent_kind in ("open_long", "open_short"):
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
        #
        # 2026-02 update — pattern-aware. The original filter used
        # ``abs(move_pct)`` which also blocked buying the *dip* on
        # mean-reversion setups. That killed the entire
        # ``SHORT_SIDE_EXHAUSTION`` family (fires on -3% to -12%
        # moves, so ANY qualifying candidate was ≥ 4% down and thus
        # over the cap). Now:
        #   * Momentum setups     → block on positive ≥ cap moves
        #                            (the original "chasing top" case)
        #   * Mean-revert setups  → allow negative moves; only block
        #                            the extreme catch-a-knife case
        #                            (< -2× cap, e.g. -8% at cap 4%)
        #   * Unknown setup type  → default to abs() safety
        max_move = _max_intraday_move_pct()
        if max_move > 0:
            move_detail = await _intraday_move_detail(symbol)
            if move_detail is None:
                logger.info(
                    "[public-live] symbol=%s chasing-filter data unavailable — "
                    "allowing fire (fail-open)",
                    symbol,
                )
            else:
                move_pct, prev_close, today_close = move_detail
                setup_type = (intent.get("setup_type") or "").strip().lower()
                # Local imports to avoid a circular dep at module load.
                from services.alpha_day_trader import (  # noqa: PLC0415
                    CLASSICAL_PATTERNS,
                    MEAN_REVERT_PATTERNS,
                    MOMENTUM_PATTERNS,
                    SetupType,
                )
                # 2026-09-03 fix — Classical bullish reversal patterns
                # (double_bottom, inverse_head_and_shoulders,
                # falling_wedge) are dip-buys by construction. Before
                # this fix they fell into the ``else`` branch below
                # and were blocked by ``abs(move_pct) >= max_move``,
                # i.e. any -4% dip killed the exact setup the pattern
                # is designed to catch. Treat them the same as
                # mean-revert for the chasing filter.
                is_dip_buy = (
                    setup_type in MEAN_REVERT_PATTERNS
                    or setup_type in CLASSICAL_PATTERNS
                )
                is_momentum = setup_type in MOMENTUM_PATTERNS
                # SHORT_SIDE_EXHAUSTION explicitly fires on -3% to
                # -12% moves. The generic -2× knife guard (-8% at
                # cap 4%) cuts it off before its designed sweet
                # spot, so widen the guard to -3× for that pattern.
                if setup_type == SetupType.SHORT_SIDE_EXHAUSTION.value:
                    knife_mult = 3.0
                else:
                    knife_mult = 2.0
                blocked = False
                if is_momentum:
                    # Only block a legit "buying the top" case:
                    # positive move already past the cap.
                    blocked = move_pct >= max_move
                elif is_dip_buy:
                    # Dip-buy patterns need the negative side open,
                    # but still guard against catching a knife
                    # (deep collapses well beyond the cap).
                    blocked = (
                        move_pct >= max_move
                        or move_pct <= -knife_mult * max_move
                    )
                else:
                    # Unknown — keep the historical abs() behaviour
                    # so we don't accidentally widen the gate for
                    # something we haven't explicitly reasoned about.
                    blocked = abs(move_pct) >= max_move
                if blocked:
                    logger.info(
                        "[public-live] symbol=%s SKIPPED — chasing filter "
                        "(intraday move %.2f%%, cap %.2f%%, setup=%s)",
                        symbol, move_pct, max_move, setup_type or "unknown",
                    )
                    # Rich audit payload: expose EVERY anchor the
                    # chasing filter used so a 100% rejection rate
                    # can be forensically classified into
                    # "clearly extended" vs "marginally over cap" vs
                    # "stale signal / late evaluation" vs
                    # "reference-price anomaly".
                    ad = intent.get("alpha_daytrader") or {}
                    signal_price = None
                    signal_age_seconds = None
                    move_at_signal_pct = None
                    adverse_drift_since_signal_pct = None
                    try:
                        signal_price = float(ad.get("confirmation_price") or 0.0) or None
                    except (TypeError, ValueError):
                        signal_price = None
                    try:
                        from datetime import datetime as _dt, timezone as _tz
                        _created_iso = ad.get("created_at")
                        if _created_iso:
                            _created = _dt.fromisoformat(_created_iso.replace("Z", "+00:00"))
                            signal_age_seconds = round(
                                (_dt.now(_tz.utc) - _created).total_seconds(), 1,
                            )
                    except Exception:  # noqa: BLE001
                        signal_age_seconds = None
                    if signal_price and prev_close > 0:
                        move_at_signal_pct = round(
                            (signal_price - prev_close) / prev_close * 100.0, 3,
                        )
                        adverse_drift_since_signal_pct = round(
                            (today_close - signal_price) / signal_price * 100.0, 3,
                        )
                    excess_over_cap = round(abs(move_pct) - max_move, 3)

                    # Extreme-move validation (operator directive):
                    # any move ≥ EXTREME_MOVE_THRESHOLD_PCT gets checked
                    # for reference-price integrity BEFORE we let it
                    # count as an ordinary chasing_filter rejection.
                    # The trade stays blocked either way — this is
                    # purely diagnostic tagging.
                    extreme_verdict = None
                    try:
                        from services.alpha_extreme_move_validator import (
                            validate_extreme_move,
                        )
                        _v = await validate_extreme_move(
                            symbol=symbol,
                            move_pct=move_pct,
                            prev_close=prev_close,
                            today_close=today_close,
                        )
                        if _v is not None:
                            extreme_verdict = _v.as_dict()
                    except Exception as _exc:  # noqa: BLE001
                        logger.debug(
                            "[public-live] extreme-move validator failed: %s", _exc,
                        )

                    await _log_skip(
                        db, symbol=symbol, reason="chasing_filter",
                        intent=intent,
                        detail={
                            "move_pct": round(move_pct, 3),
                            "cap_pct": max_move,
                            "excess_over_cap": excess_over_cap,
                            "setup_type": setup_type or None,
                            "direction_class": (
                                "momentum" if is_momentum
                                else "dip_buy" if is_dip_buy
                                else "unknown"
                            ),
                            "knife_mult": knife_mult,
                            "reference_price": round(prev_close, 4),
                            "today_close": round(today_close, 4),
                            "signal_price": signal_price,
                            "move_at_signal_pct": move_at_signal_pct,
                            "adverse_drift_since_signal_pct":
                                adverse_drift_since_signal_pct,
                            "signal_age_seconds": signal_age_seconds,
                            "extreme_move_verdict": extreme_verdict,
                        },
                    )
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

    # ── Council modulator (shadow-first) ──
    # Consults sovereign + multi-model brains and derives a size
    # multiplier in [0.25, 1.0]. When
    # ``RISEDUAL_COUNCIL_MODULATE_ENABLED=1`` the modulator is applied
    # to the notional; otherwise the shadow value is logged only. The
    # council can only shrink size, never veto direction, never block.
    # See services/council_consultation.py doctrine pins.
    council_result: dict[str, Any] = {}
    try:
        from services.council_consultation import consult_council
        council_result = await consult_council(
            db,
            symbol=symbol,
            alpha_direction=(
                "BUY" if intent_kind in ("open_long", "close_short")
                else "SELL"
            ),
            alpha_confidence=intent.get("confidence"),
            strategy_id=strategy_id_raw,
        )
        _council_mult = float(council_result.get("modulator") or 1.0)
        _council_shadow = float(council_result.get("shadow_modulator") or 1.0)
        _council_enforced = bool(council_result.get("enforced"))
        if _council_enforced and _council_mult < 1.0:
            _new_notional = max(1.0, notional * _council_mult)
            logger.info(
                "[public-live] symbol=%s council dissent=%.2f cons=%s "
                "mult=%.2f notional %.2f → %.2f (enforced)",
                symbol, council_result.get("dissent_ratio", 0.0),
                council_result.get("consensus", "unknown"),
                _council_mult, notional, _new_notional,
            )
            notional = _new_notional
        elif _council_shadow < 1.0:
            logger.info(
                "[public-live] symbol=%s council dissent=%.2f cons=%s "
                "shadow_mult=%.2f (SHADOW — not applied; set "
                "RISEDUAL_COUNCIL_MODULATE_ENABLED=1 to apply)",
                symbol, council_result.get("dissent_ratio", 0.0),
                council_result.get("consensus", "unknown"),
                _council_shadow,
            )
    except Exception as _council_exc:  # noqa: BLE001
        logger.debug("[public-live] council consult failed (non-fatal): %s", _council_exc)

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
    # For CLOSE_LONG / CLOSE_SHORT: require an actual open position on
    # the matching side (either tracked by us in ``equity_live_trades``
    # OR live on the broker — operator may have manually opened it).
    #
    # P1-B: close_in_flight idempotency guard. When a close order is
    # in flight (submitted but not yet reconciled by the fill writer),
    # a re-fired SELL/BUY_TO_COVER intent within the stale window is
    # rejected to avoid over-closing. The 120s stale window balances
    # slow broker ACKs vs. wedged flags — after 120s we assume the
    # prior close never happened / already reconciled and let the new
    # intent proceed.
    _CLOSE_IN_FLIGHT_STALE_SECONDS = 120
    current_qty = 0.0
    existing_row = None
    if db is not None:
        try:
            # Match either an open LONG (BUY entry) or open SHORT
            # (SELL_TO_OPEN entry). The router already told us which
            # side to close; we filter by direction to match.
            _expected_direction = None
            if intent_kind == "close_long":
                _expected_direction = "LONG"
            elif intent_kind == "close_short":
                _expected_direction = "SHORT"
            _query = {"symbol": symbol, "status": "open", "broker_id": "public"}
            if _expected_direction is not None:
                _query["direction"] = _expected_direction
            existing_row = await db.equity_live_trades.find_one(
                _query,
                {"_id": 1, "size": 1, "trade_id": 1, "direction": 1,
                 "close_in_flight_at": 1},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[public-live] idempotency check failed: %s", exc)

    if intent_kind == "open_long":
        # For an open_long, ANY existing open row on this symbol (long
        # or short) blocks — never stack exposure atop an existing
        # position, and never open a long while a short is out.
        try:
            _any_open = await db.equity_live_trades.find_one(
                {"symbol": symbol, "status": "open", "broker_id": "public"},
                {"_id": 1, "direction": 1},
            ) if db is not None else None
        except Exception:  # noqa: BLE001
            _any_open = None
        if _any_open is not None:
            logger.info(
                "[public-live] symbol=%s already has open live row (%s) — "
                "skip dupe/reverse",
                symbol, _any_open.get("direction"),
            )
            await _log_skip(
                db, symbol=symbol, reason="dup_open_row", intent=intent,
                detail={"intent_kind": intent_kind,
                        "existing_row_id": str(_any_open.get("_id") or ""),
                        "existing_direction": _any_open.get("direction")},
            )
            return None

    if intent_kind in ("close_long", "close_short"):
        # Idempotency: close_in_flight guard.
        if existing_row is not None:
            _cif = existing_row.get("close_in_flight_at")
            if _cif is not None:
                try:
                    _cif_age = (
                        datetime.now(timezone.utc) - _cif
                    ).total_seconds()
                except Exception:  # noqa: BLE001
                    _cif_age = 999.0
                if _cif_age < _CLOSE_IN_FLIGHT_STALE_SECONDS:
                    logger.info(
                        "[public-live] symbol=%s close already in flight "
                        "(%.1fs ago) — skip dupe close",
                        symbol, _cif_age,
                    )
                    await _log_skip(
                        db, symbol=symbol, reason="close_in_flight",
                        intent=intent,
                        detail={"intent_kind": intent_kind,
                                "age_seconds": round(_cif_age, 1),
                                "stale_seconds": _CLOSE_IN_FLIGHT_STALE_SECONDS},
                    )
                    return None

        # Ask the broker for the current position. Don't rely on the
        # Mongo row alone — the operator may have an untracked
        # position from before tracking started (SPCX/VRPX-style).
        # Broker qty wins over Alpha's belief per the P1-B directive:
        # if Alpha thinks 2.3 shares but Public reports 1.8, we close
        # 1.8 and record the discrepancy.
        _wanted_side = "long" if intent_kind == "close_long" else "short"
        _mongo_believed_qty = 0.0
        if existing_row is not None:
            try:
                _mongo_believed_qty = float(existing_row.get("size") or 0.0)
            except (TypeError, ValueError):
                _mongo_believed_qty = 0.0
        try:
            client_pos = _public_client(secret_key, account_id)
            if client_pos is not None:
                positions = client_pos.get_positions() or []
                for p in positions:
                    if (p.get("symbol") or "").upper() != symbol:
                        continue
                    try:
                        _raw_qty = float(p.get("qty") or 0.0)
                    except (TypeError, ValueError):
                        _raw_qty = 0.0
                    _p_side = (p.get("side") or "").lower()
                    if _p_side not in ("long", "short"):
                        _p_side = "long" if _raw_qty >= 0 else "short"
                    if _p_side != _wanted_side:
                        continue
                    current_qty = abs(_raw_qty)
                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[public-live] positions lookup failed (non-fatal): %s", exc,
            )
        if current_qty <= 0:
            _no_pos_reason = (
                "sell_no_position" if intent_kind == "close_long"
                else "cover_no_short"
            )
            logger.info(
                "[public-live] symbol=%s SKIPPED — %s intent but no "
                "matching %s position at broker to close",
                symbol, intent_kind.upper(), _wanted_side,
            )
            await _log_skip(
                db, symbol=symbol, reason=_no_pos_reason, intent=intent,
                detail={"intent_kind": intent_kind,
                        "current_qty": current_qty,
                        "mongo_believed_qty": _mongo_believed_qty,
                        "wanted_side": _wanted_side},
            )
            return None
        # Broker-vs-Mongo discrepancy log — never blocks, always audits.
        if (
            _mongo_believed_qty > 0
            and abs(_mongo_believed_qty - current_qty) > 1e-6
        ):
            logger.warning(
                "[public-live] symbol=%s broker/mongo qty discrepancy: "
                "broker=%.6f mongo=%.6f — using broker qty",
                symbol, current_qty, _mongo_believed_qty,
            )

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
        await _log_skip(
            db, symbol=symbol, reason="no_mark_price", intent=intent,
            detail={"mark_returned": mark},
        )
        return None

    # ── LULD RoadGuard (equity) — narrow safety gate ────────────
    # Blocks on explicit halt / reopening / LULD proximity signals only.
    # Fails open on unknown state to avoid recreating the overblocking
    # regression.
    try:
        from services.luld_roadguard import check_luld
        _luld_ctx = intent.get("luld") if isinstance(intent, Mapping) else None
        _luld_verdict = check_luld(
            symbol=symbol, mark_price=mark,
            context=_luld_ctx if isinstance(_luld_ctx, Mapping) else None,
        )
        if not _luld_verdict.allowed and _luld_verdict.enforce:
            logger.info(
                "[public-live] symbol=%s SKIPPED — LULD RoadGuard "
                "reason=%s source=%s",
                symbol, _luld_verdict.reason, _luld_verdict.source,
            )
            await _log_skip(
                db, symbol=symbol, reason="luld_roadguard",
                intent=intent, detail=_luld_verdict.as_dict(),
            )
            return None
    except Exception as _luld_exc:  # noqa: BLE001
        logger.debug("[public-live] LULD check failed: %s", _luld_exc)
    if intent_kind in ("close_long", "close_short"):
        qty = current_qty
    elif intent_kind == "open_short":
        # P1-A — Public rejects fractional shorts. Whole-share qty only.
        # Use the short-canary notional (typically smaller than the
        # long notional) so Alpha's first live short is a small canary.
        from services.public_short_executor import compute_whole_share_qty
        short_notional = min(notional, _short_first_fire_notional_usd())
        qty = compute_whole_share_qty(
            notional_usd=short_notional, mark_price=mark,
        )
    else:
        # OPEN_LONG sizing — math.ceil to 4 dp ensures
        # qty * mark > notional (clears Public.com's $1.00 minimum
        # even after fractional rounding).
        import math
        qty = math.ceil((notional / mark) * 10000.0) / 10000.0
    if qty <= 0:
        logger.warning(
            "[public-live] symbol=%s SKIPPED — computed qty %.6f ≤ 0",
            symbol, qty,
        )
        await _log_skip(
            db, symbol=symbol, reason="qty_zero", intent=intent,
            detail={"qty": qty, "mark": mark, "notional": notional,
                    "intent_kind": intent_kind},
        )
        return None

    # Execute
    client = _public_client(secret_key, account_id)
    if client is None:
        await _log_skip(
            db, symbol=symbol, reason="client_init_failed", intent=intent,
            detail={"has_secret_key": bool(secret_key),
                    "has_account_id": bool(account_id)},
        )
        return None
    # Order side mapping — router-derived, unambiguous:
    #  open_long   → buy       (BUY_TO_OPEN)
    #  close_long  → sell      (SELL_TO_CLOSE)
    #  open_short  → sell      (SELL_TO_OPEN)   [gated by SUPPORTS_SHORT_SALES]
    #  close_short → buy       (BUY_TO_COVER)   [always allowed; exit]
    _kind_to_side = {
        "open_long": "buy",
        "close_long": "sell",
        "open_short": "sell",
        "close_short": "buy",
    }
    order_side = _kind_to_side.get(intent_kind, "buy")
    client_order_id = str(uuid.uuid4())

    # ── Broker order-event watchdog: refuse to resubmit while a prior
    # order for this (broker, symbol, account) is stuck in an unknown
    # state. Never assume the missing ACK means "failed" — a live order
    # may still exist. Operator must reconcile before re-arming.
    try:
        from services import alpha_broker_event_watchdog as _watchdog
        from services import alpha_broker_reconcilers as _reconcilers
        _reconcilers.register_all()
        if _watchdog.is_frozen("public", symbol, account_id):
            logger.warning(
                "[public-live] symbol=%s SKIPPED — prior submission frozen "
                "(broker_state_unknown or broker_event_stale). Reconcile before retrying.",
                symbol,
            )
            await _log_skip(
                db, symbol=symbol, reason="broker_watchdog_frozen",
                intent=intent,
                detail={"account_id": account_id},
            )
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[public-live] watchdog freeze-check failed: %s", exc)

    # ── Atlas: fire-and-forget observational claim. Runs in a
    # background task with a hard 100 ms budget — Atlas is
    # structurally incapable of blocking or slowing this trade.
    # NEVER gates, NEVER awaits synchronously. See
    # /app/docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md rule 8.
    _atlas_intent_id: Optional[str] = None
    try:
        from services import atlas_bridge as _atlas
        _atlas_direction = "BUY" if intent_kind in ("open_long", "close_short") else "SELL"
        _atlas_intent_id = _atlas.observe_intent_async({
            **intent,
            "symbol": symbol,
            "direction": _atlas_direction,
            "intent_kind": intent_kind,
            "strategy_id": strategy_id_raw,
        })
    except Exception:  # noqa: BLE001
        _atlas_intent_id = None

    # P1-B — Mark existing row as close_in_flight BEFORE submitting so
    # a concurrent re-fire is rejected by the idempotency guard above.
    # This is best-effort; the flag is cleared in the success/failure
    # branches below so it can't wedge forever without stale-guard rescue.
    if (
        intent_kind in ("close_long", "close_short")
        and db is not None
        and existing_row is not None
    ):
        try:
            await db.equity_live_trades.update_one(
                {"_id": existing_row["_id"]},
                {"$set": {
                    "close_in_flight_at": datetime.now(timezone.utc),
                    "close_in_flight_client_order_id": client_order_id,
                }},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[public-live] close_in_flight flag set failed (non-fatal): %s", exc,
            )

    # P1-A — Short-path eligibility ladder for OPEN_SHORT only.
    # Runs the 4-rung ladder (account MARGIN/BUY_AND_SELL, no existing
    # position, instrument shortable, Public single-leg preflight)
    # before we ever call the REST short-open endpoint. Any failure
    # logs the exact rung + reason so operators see WHY a short was
    # blocked. Close_short does NOT run the ladder — covering is not
    # a new short exposure, and rung 2 would (correctly) fail because
    # we DO have an existing short.
    short_eligibility_verdict: Optional[dict] = None
    if intent_kind == "open_short":
        try:
            from services.public_short_eligibility import run_full_ladder
            _ladder = run_full_ladder(
                client, symbol=symbol, qty=int(qty),
                max_htb_rate_pct=_short_max_htb_rate_pct(),
            )
            short_eligibility_verdict = _ladder.as_dict()
            if not _ladder.eligible:
                logger.info(
                    "[public-live] symbol=%s SKIPPED — short ladder failed: %s",
                    symbol, _ladder.reason,
                )
                await _log_skip(
                    db, symbol=symbol,
                    reason=f"short_ladder_{_ladder.reason}",
                    intent=intent,
                    detail=short_eligibility_verdict,
                )
                return None
        except Exception as _exc:  # noqa: BLE001
            logger.warning(
                "[public-live] short eligibility ladder crashed for %s: %s",
                symbol, _exc,
            )
            await _log_skip(
                db, symbol=symbol, reason="short_ladder_exception",
                intent=intent,
                detail={"exception_class": _exc.__class__.__name__,
                        "message": str(_exc)[:200]},
            )
            return None

    _submit_start_ns = time.time_ns()
    # Register with the watchdog immediately BEFORE the HTTP call so
    # that a hung/dropped request still starts the 5s stale timer.
    try:
        from services import alpha_broker_event_watchdog as _watchdog
        _watchdog.register_submission(
            broker="public",
            client_order_id=client_order_id,
            symbol=symbol,
            account_id=account_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[public-live] watchdog register failed: %s", exc)
    try:
        if intent_kind in ("open_short", "close_short"):
            # P1-A — Short paths use the direct-REST helper because
            # the installed SDK's ``OrderRequest`` doesn't know about
            # the ``openCloseIndicator`` / ``useMargin`` fields yet.
            from services.public_short_executor import submit_short_order
            resp = submit_short_order(
                client,
                symbol=symbol,
                qty=int(qty),
                side="SELL" if intent_kind == "open_short" else "BUY",
                open_close="OPEN" if intent_kind == "open_short" else "CLOSE",
                use_margin=True,
                client_order_id=client_order_id,
            )
        else:
            resp = client.place_order(
                symbol=symbol, qty=qty, side=order_side, order_type="market",
            )
    except Exception as exc:  # noqa: BLE001
        _ack_ms = (time.time_ns() - _submit_start_ns) // 1_000_000
        logger.error(
            "[public-live] CRITICAL — place_order raised symbol=%s "
            "kind=%s: %s", symbol, intent_kind, exc,
        )
        # P1-B — clear the close_in_flight flag on submission failure so
        # the next re-fire can proceed once the operator's diagnosed it.
        if (
            intent_kind in ("close_long", "close_short")
            and db is not None
            and existing_row is not None
        ):
            try:
                await db.equity_live_trades.update_one(
                    {"_id": existing_row["_id"]},
                    {"$unset": {
                        "close_in_flight_at": "",
                        "close_in_flight_client_order_id": "",
                    }},
                )
            except Exception:  # noqa: BLE001
                pass
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
        try:
            from services import atlas_bridge as _atlas
            _atlas.transition_async(_atlas_intent_id, "rejected",
                                    reason_code=f"exception:{exc.__class__.__name__}")
        except Exception:  # noqa: BLE001
            pass
        await _log_skip(
            db, symbol=symbol, reason="place_order_exception", intent=intent,
            detail={"exception_class": exc.__class__.__name__,
                    "message": str(exc)[:200],
                    "intent_kind": intent_kind,
                    "qty": qty, "mark": mark},
        )
        return None
    _ack_ms = (time.time_ns() - _submit_start_ns) // 1_000_000
    if not resp:
        logger.warning(
            "[public-live] symbol=%s place_order returned empty — "
            "Public.com rejected (check vault token + connect)",
            symbol,
        )
        # P1-B — clear close_in_flight so the next attempt isn't blocked.
        if (
            intent_kind in ("close_long", "close_short")
            and db is not None
            and existing_row is not None
        ):
            try:
                await db.equity_live_trades.update_one(
                    {"_id": existing_row["_id"]},
                    {"$unset": {
                        "close_in_flight_at": "",
                        "close_in_flight_client_order_id": "",
                    }},
                )
            except Exception:  # noqa: BLE001
                pass
        # Synchronous empty response = broker explicitly rejected.
        # Record REJECTED so the watchdog doesn't false-freeze on stale timer.
        try:
            from services import alpha_broker_event_watchdog as _watchdog
            _watchdog.record_event(
                broker="public", client_order_id=client_order_id,
                event=_watchdog.Event.REJECTED, detail="empty_response",
            )
        except Exception:  # noqa: BLE001
            pass
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
        try:
            from services import atlas_bridge as _atlas
            _atlas.transition_async(_atlas_intent_id, "rejected",
                                    reason_code="empty_response")
        except Exception:  # noqa: BLE001
            pass
        await _log_skip(
            db, symbol=symbol, reason="broker_empty_response", intent=intent,
            detail={"intent_kind": intent_kind, "qty": qty, "mark": mark},
        )
        return None

    order_id = resp.get("id") or ""
    # Map Public.com's sync status into a normalized watchdog event.
    # Cancels the 5s stale timer — sync ACK means the broker received it.
    try:
        from services import alpha_broker_event_watchdog as _watchdog
        _status_lc = str(resp.get("status") or "accepted").lower()
        if _status_lc in ("filled", "closed", "completed", "executed"):
            _evt = _watchdog.Event.FILLED
        elif _status_lc in ("rejected", "canceled", "cancelled", "expired"):
            _evt = _watchdog.Event.REJECTED
        else:
            _evt = _watchdog.Event.ACKNOWLEDGED
        _watchdog.record_event(
            broker="public", client_order_id=client_order_id,
            event=_evt, broker_order_id=str(order_id) if order_id else None,
            detail=f"public_status={_status_lc}",
        )
    except Exception:  # noqa: BLE001
        pass
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
    try:
        from services import atlas_bridge as _atlas
        _atlas.transition_async(_atlas_intent_id, "submitted",
                                broker_order_id=str(order_id) if order_id else None)
    except Exception:  # noqa: BLE001
        pass
    trade_id = str(uuid.uuid4())
    if intent_kind in ("close_long", "close_short"):
        # CLOSE lifecycle (P1-B):
        # - close_long  : SELL_TO_CLOSE on a LONG position
        # - close_short : BUY_TO_COVER on a SHORT position
        #
        # Partial-fill detection: Public's synchronous response carries
        # ``filled_qty`` / ``filledQty`` / ``qty`` fields depending on
        # adapter version. If the fill is short of ``qty``, we mark the
        # row as ``partial_closed`` and keep ``status=open`` with a
        # residual quantity so the fill writer / reconciler can pick up
        # the remainder on the next sweep. This avoids the historical
        # bug where a partial close silently marked the row as fully
        # closed even though real exposure remained at the broker.
        try:
            _filled_qty_raw = (
                resp.get("filled_qty")
                or resp.get("filledQty")
                or resp.get("fillQuantity")
                or resp.get("qty")
                or qty
            )
            _filled_qty = float(_filled_qty_raw or 0.0)
        except (TypeError, ValueError):
            _filled_qty = qty
        # Cap filled_qty at requested qty (defensive — a broker can't
        # over-fill an order).
        if _filled_qty > qty:
            _filled_qty = qty
        _remaining_qty = max(qty - _filled_qty, 0.0)
        _is_partial = _remaining_qty > 1e-6 and _filled_qty > 0

        _close_reason = (
            "alpha_sell_signal" if intent_kind == "close_long"
            else "alpha_cover_signal"
        )
        _row_direction = "LONG" if intent_kind == "close_long" else "SHORT"
        _row_side = "SELL" if intent_kind == "close_long" else "BUY"

        close_doc: dict[str, Any] = {
            "close_price": mark,
            "close_order_id": order_id,
            "close_reason": _close_reason,
            "close_intent_kind": intent_kind,
            "close_filled_qty": _filled_qty,
            "close_requested_qty": qty,
        }
        if _is_partial:
            close_doc.update({
                "status": "partial_closed",
                "close_partial": True,
                "close_remaining_qty": _remaining_qty,
            })
        else:
            close_doc.update({
                "status": "closed",
                "closed_at": datetime.now(timezone.utc),
                "close_partial": False,
            })

        if db is not None and existing_row is not None:
            try:
                # Clear the in-flight flag as part of the same update
                # so a concurrent re-fire can't slip in while we write.
                await db.equity_live_trades.update_one(
                    {"_id": existing_row["_id"]},
                    {
                        "$set": close_doc,
                        "$unset": {
                            "close_in_flight_at": "",
                            "close_in_flight_client_order_id": "",
                        },
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[public-live] CRITICAL — %s order placed but Mongo "
                    "close-update failed symbol=%s order_id=%s: %s",
                    intent_kind, symbol, order_id, exc,
                )
        logger.info(
            "[public-live] %s symbol=%s filled=%.6f/%.6f @ $%.2f "
            "order_id=%s%s",
            "PARTIAL_CLOSE" if _is_partial else "CLOSE",
            symbol, _filled_qty, qty, mark, order_id,
            (" (residual=%.6f)" % _remaining_qty) if _is_partial else "",
        )
        try:
            from services import atlas_bridge as _atlas
            _atlas.transition_async(
                _atlas_intent_id, "terminal",
                reason_code="close_partial" if _is_partial else "close_filled",
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "trade_id": existing_row.get("trade_id") if existing_row else trade_id,
            "broker_id": "public",
            "symbol": symbol,
            "direction": _row_direction,
            "side": _row_side,
            "intent_kind": intent_kind,
            "size": qty,
            "filled_qty": _filled_qty,
            "remaining_qty": _remaining_qty,
            "close_price": mark,
            "status": "partial_closed" if _is_partial else "closed",
            "broker_order_id": order_id,
            "closed_at": (
                None if _is_partial else datetime.now(timezone.utc)
            ),
            "confidence": float(intent.get("confidence") or 0.0),
            "source_signal": intent.get("source_signal"),
        }

    # OPEN path — open_long or open_short (v2.2 P1-A). Row schema
    # mirrors the historical open_long shape; direction/side vary.
    _open_direction = "LONG" if intent_kind == "open_long" else "SHORT"
    _open_side = "BUY" if intent_kind == "open_long" else "SELL"
    row = {
        "trade_id": trade_id,
        "broker_id": "public",
        "symbol": symbol,
        "direction": _open_direction,
        "side": _open_side,
        "intent_kind": intent_kind,
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
        # Round-Trip Proof — carry the alpha_daytrader payload
        # (setup_id, stop_price, target_price, confirmation_price)
        # onto the row so the fill writer can join back to
        # alpha_outcomes and compute realized_r from the actual risk
        # distance. Without this, outcomes silently never resolve.
        "alpha_daytrader": intent.get("alpha_daytrader") or None,
        # Snapshot stop / target at open time so the outcome resolver
        # can compute realized_r even if the caller's intent shape
        # changes later.
        "stop_price": (
            float((intent.get("alpha_daytrader") or {}).get("stop_price") or 0.0)
            or None
        ),
        "target_price": (
            float((intent.get("alpha_daytrader") or {}).get("target_price") or 0.0)
            or None
        ),
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
    try:
        from services import atlas_bridge as _atlas
        _atlas.transition_async(_atlas_intent_id, "terminal",
                                reason_code="open_filled")
    except Exception:  # noqa: BLE001
        pass
    return row


__all__ = [
    "maybe_route_live",
    "_live_exec_enabled",
    "_fixed_notional_usd",
    "_allowed_symbols",
]
