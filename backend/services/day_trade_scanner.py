"""
Day-Trade Scanner — disciplined intraday loop.

Strict invariant
────────────────
``scan_universe → score → rank → apply_gates → execute top candidate``

NEVER execute while scanning. ALWAYS scan all symbols first.
THEN rank. THEN decide. THEN execute exactly one (per asset class).

Why a separate module
─────────────────────
The existing ``trading_bot_service.run_signal_bot_dispatcher`` and
``crypto_paper_trader.run_crypto_paper_bot`` both iterate symbols
and execute as they go — fine for those bots but not for an
intraday day-trade discipline where we want the BEST candidate of
the entire universe, not just "the first one whose gates passed".

This module is read-mostly:

* Phases 1-3 (scan / rank / gate) issue ZERO writes to
  ``paper_trades`` / ``crypto_paper_trades``. They read latest
  predictions + ticker-abandonment inputs, compute scores, and
  filter.
* Phase 4 issues exactly one write: the ``day_trade_targets``
  doc representing the chosen top-1 entry. The companion
  ``services.day_trade_executor`` is what actually places the
  paper trade — kept in a separate module so the scanner and
  the writer can be tested independently.
* Every tick is fully audited: one row per scan into
  ``day_trade_scan_log`` recording the candidate list, ranks,
  gate verdicts, and the chosen winner (or "all_blocked").

Asset scope
───────────
Both equity and crypto, in two parallel loops sharing the same
helper functions. ``run_scan(db, asset_class)`` is the single
entry point per lane.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

AssetClass = Literal["equity", "crypto"]

# Window for "latest prediction per symbol". The model emits
# predictions on its own cadence; the scanner reads the freshest
# row per symbol within this window. Older rows are stale and
# excluded. 15min is wider than the model's typical 10min cadence
# so we never starve on a single missed tick.
PREDICTION_FRESHNESS_MIN: int = 15

# Minimum directional confidence (calibrated when available) to
# even consider firing. Below this is "no opinion" — never a top
# candidate. Tuned to the same 0.55 floor sizing already uses;
# env-tunable for tightening.
import os
MIN_SCORE_THRESHOLD: float = float(os.environ.get("DAY_TRADE_MIN_SCORE", 0.55))

SCAN_LOG_COLLECTION = "day_trade_scan_log"
TARGETS_COLLECTION = "day_trade_targets"


@dataclass
class ScanCandidate:
    symbol: str
    asset_class: AssetClass
    direction: str            # canonical or up/down — the executor normalises
    score: float              # primary ranking signal (0-1)
    confidence_raw: float
    confidence_calibrated: float | None
    prediction_id: str | None
    prediction_at: str        # ISO timestamp string from predictions.timestamp
    strategy_id: str = "signal_dispatcher:v1"  # brain/model identity for evidence attribution
    rank: int = -1            # filled in after rank pass
    gate_passed: bool = False
    gate_blocker: str | None = None
    gate_inputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanResult:
    scan_id: str
    asset_class: AssetClass
    started_at: datetime
    finished_at: datetime
    candidates: list[ScanCandidate]
    chosen: ScanCandidate | None
    blocked_count: int
    total_scanned: int


def _is_crypto_symbol(symbol: str) -> bool:
    """Inline mirror of ``crypto_paper_trader.is_crypto_symbol``
    to avoid importing that whole module just for this check."""
    s = (symbol or "").upper()
    return s in {
        "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK",
        "DOGE", "DOT", "MATIC",
    }


# ── Phase 1: scan ──────────────────────────────────────────────────


async def scan_universe(
    db: Any, asset_class: AssetClass, *,
    lookback_minutes: int = PREDICTION_FRESHNESS_MIN,
) -> list[ScanCandidate]:
    """Read latest fresh prediction per symbol for the asset class.

    Pure read — never writes. Returns one candidate per (symbol)
    using the most recent prediction within ``lookback_minutes``.
    Score = ``calibrated_confidence`` when stamped **and** the
    calibrator's ``applies_to`` scope includes the scanner; otherwise
    the raw ``confidence`` is used. The scope check is critical —
    the isotonic calibrator historically had ``applies_to =
    ['tier3_readiness_only']`` but every downstream consumer,
    including this scanner, was blindly reading ``calibrated_confidence``.
    A degenerate 4-knot calibrator then mapped every raw >= 0.47 to a
    single 0.9167 sink, causing every live fire to record confidence
    ≈ 0.92 (see prod tape 2026-07 forensic).
    """
    if db is None:
        return []
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    ).isoformat()
    pipe = [
        {"$match": {
            # 2026-06-26 — Alpha emits predictions tagged
            # ``feature: "signal_dispatcher"`` from
            # ``trading_bot_service.py``. The legacy ``paper_trading``
            # tag stopped being written when ``PAPER_TRADING_ENABLED=
            # false`` was flipped, which silently starved the scanner
            # (3,229 fresh signal_dispatcher predictions vs. zero
            # paper_trading predictions). Accept BOTH so a future re-
            # enable of paper trading slots back in cleanly.
            "feature": {"$in": ["signal_dispatcher", "paper_trading"]},
            # ``timestamp`` is the prediction's first-seen time and is
            # the canonical TTL window (15 min × 3 recalls — designed
            # to throttle market-data provider hits). DO NOT filter on
            # ``last_seen_at`` — that would extend stale signals past
            # their intended cache lifetime and defeat the producer's
            # rate-limiting contract.
            "timestamp": {"$gte": since},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$symbol",
            "prediction_id": {"$first": "$prediction_id"},
            "direction": {"$first": "$direction"},
            "confidence": {"$first": "$confidence"},
            "calibrated_confidence": {"$first": "$calibrated_confidence"},
            "calibration_applies_to": {"$first": "$calibration_applies_to"},
            "model_version": {"$first": "$model_version"},
            "timestamp": {"$first": "$timestamp"},
        }},
    ]
    try:
        cursor = db.predictions.aggregate(pipe)
        rows = await cursor.to_list(length=2_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[day-trade-scan] predictions read failed: %s", exc)
        return []

    out: list[ScanCandidate] = []
    for r in rows:
        sym = (r.get("_id") or "").upper()
        if not sym:
            continue
        is_crypto = _is_crypto_symbol(sym)
        if asset_class == "crypto" and not is_crypto:
            continue
        if asset_class == "equity" and is_crypto:
            continue
        try:
            raw_conf = float(r.get("confidence") or 0.0)
        except (TypeError, ValueError):
            raw_conf = 0.0
        cal_conf = r.get("calibrated_confidence")
        try:
            cal_val = float(cal_conf) if cal_conf is not None else None
        except (TypeError, ValueError):
            cal_val = None

        # Normalise raw to 0-1 if it's on the 0-100 scale.
        if raw_conf > 1.01:
            raw_conf = raw_conf / 100.0

        # Score selection with SCOPE CHECK on the calibrator.
        #
        # Historical bug: the isotonic calibrator was stamped with
        # ``calibration_applies_to: ['tier3_readiness_only']`` but the
        # scanner read ``calibrated_confidence`` unconditionally.
        # Combined with a 4-knot calibrator whose curve mapped every
        # raw >= 0.47 to 0.9167, every live fire recorded confidence
        # ≈ 0.92. Fix: only use the calibrated value when the scope
        # explicitly authorises this consumer. Scanner is authorised
        # when scope is empty (legacy — assume all), missing, or
        # contains one of ``day_trade_scanner`` / ``all`` / ``*``.
        applies_to = r.get("calibration_applies_to") or []
        if isinstance(applies_to, str):
            applies_to = [applies_to]
        scope_authorised = (
            not applies_to
            or any(
                (s or "").lower() in {"day_trade_scanner", "all", "*"}
                for s in applies_to
            )
        )
        # Directional adjustment for "down" predictions (raw
        # confidence == P(up); for short signals true confidence
        # is 1 - P(up)). Skipped when we use the calibrated value
        # because calibration already maps to actual win probability.
        direction = (r.get("direction") or "").upper()
        if scope_authorised and cal_val is not None:
            score = cal_val
        elif direction in ("DOWN", "SHORT", "STRONG_SELL", "WEAK_SELL", "BEARISH"):
            score = 1.0 - raw_conf
        else:
            score = raw_conf

        # ``strategy_id`` = brain-model identity for evidence
        # attribution. Falls back through model_version → prediction
        # feature → static v1 tag so every downstream row is tagged.
        strategy_id = (
            (r.get("model_version") or "").strip()
            or "signal_dispatcher:v1"
        )

        out.append(ScanCandidate(
            symbol=sym,
            asset_class=asset_class,
            direction=direction,
            score=round(float(score), 4),
            confidence_raw=round(raw_conf, 4),
            confidence_calibrated=cal_val,
            prediction_id=r.get("prediction_id"),
            prediction_at=r.get("timestamp") or "",
            strategy_id=strategy_id,
        ))
    return out


# ── Phase 2: rank ──────────────────────────────────────────────────


def rank_candidates(candidates: list[ScanCandidate]) -> list[ScanCandidate]:
    """Pure deterministic sort. Primary key score desc, tiebreak
    alphabetical. Stamps ``rank`` (1-indexed) on each."""
    ranked = sorted(
        candidates, key=lambda c: (-c.score, c.symbol),
    )
    for i, c in enumerate(ranked, start=1):
        c.rank = i
    return ranked


# ── Phase 3: gates ─────────────────────────────────────────────────


async def apply_gates(
    db: Any, candidate: ScanCandidate,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Read-only gate sequence. Returns ``(passed, blocker, inputs)``.

    Gates in order — first failure short-circuits:

    1. Min-score floor (DAY_TRADE_MIN_SCORE).
    2. Direction parseable (no NEUTRAL/HOLD/UNKNOWN).
    3. Ticker abandonment gate (KEEP only).
    4. Already holding (skip if there's an open day-trade or
       paper-trade row on this symbol).
    """
    inputs: dict[str, Any] = {}

    if candidate.score < MIN_SCORE_THRESHOLD:
        return False, "below_min_score", inputs

    direction = (candidate.direction or "").upper()
    if direction in ("HOLD", "NEUTRAL", "UNKNOWN", ""):
        return False, "non_directional_prediction", inputs

    # NOTE (2026-06-26): SELL/SHORT signals are no longer filtered at
    # the scanner level. The Public.com live executor now handles
    # both sides — BUY → open long, SELL → close existing long. When
    # the symbol isn't held, the executor short-circuits to a clean
    # skip (cash account; can't open new shorts without margin).

    # Ticker abandonment.
    try:
        if candidate.asset_class == "crypto":
            from services.ticker_abandonment_stats import compute_crypto_inputs
            ai = await compute_crypto_inputs(db, candidate.symbol)
        else:
            from services.ticker_abandonment_stats import compute_equity_inputs
            ai = await compute_equity_inputs(db, candidate.symbol)
        from services.ticker_abandonment import decide_ticker_exit
        decision = decide_ticker_exit(
            symbol=candidate.symbol,
            recent_signals=ai.recent_signals,
            recent_rejections=ai.recent_rejections,
            recent_losses=ai.recent_losses,
            recent_wins=ai.recent_wins,
            avg_confidence=ai.avg_confidence,
            avg_rr=ai.avg_rr,
            last_profitable_at=ai.last_profitable_at,
        )
        inputs["abandonment_action"] = decision.action
        inputs["abandonment_reason"] = decision.reason
        if decision.action != "KEEP":
            return (
                False,
                f"ticker_{decision.action.lower()}_{decision.reason}",
                inputs,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[day-trade-scan] abandonment gate skipped for %s: %s",
            candidate.symbol, exc,
        )

    # Already holding — no double-up.
    try:
        coll = (
            "crypto_paper_trades"
            if candidate.asset_class == "crypto" else "paper_trades"
        )
        sym_field = "symbol" if candidate.asset_class == "crypto" else "ticker"
        already = await db[coll].count_documents({
            sym_field: candidate.symbol, "status": "open",
        })
        inputs["open_positions"] = already
        if already > 0:
            return False, "already_holding", inputs
        # Also block if a target was already queued and not yet
        # consumed by the executor — prevents re-queuing on the next
        # 5-min tick before the executor has fired.
        # 2026-06-26 — Only block on RECENT pending rows (last 10 min)
        # so a stuck row never permanently kills a symbol. Phase 4b
        # transitions the row to ``routed``/``executor_skipped`` post-
        # call, but this is the belt-and-suspenders backstop in case
        # that transition fails (Mongo write error, process crash mid-
        # scan, etc.).
        recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        pending = await db[TARGETS_COLLECTION].count_documents({
            "symbol": candidate.symbol, "status": "pending",
            "queued_at": {"$gte": recent_cutoff},
        })
        if pending > 0:
            return False, "target_pending", inputs
    except Exception as exc:  # noqa: BLE001
        logger.debug("[day-trade-scan] holding-check skipped: %s", exc)

    return True, None, inputs


# ── Phase 4: select top + audit log ───────────────────────────────


async def _write_scan_log(db: Any, result: ScanResult) -> None:
    if db is None:
        return
    try:
        chosen = (
            asdict(result.chosen) if result.chosen is not None else None
        )
        await db[SCAN_LOG_COLLECTION].insert_one({
            "scan_id": result.scan_id,
            "asset_class": result.asset_class,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "total_scanned": result.total_scanned,
            "blocked_count": result.blocked_count,
            "candidates": [asdict(c) for c in result.candidates],
            "chosen": chosen,
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[day-trade-scan] log write failed: %s", exc)


async def _write_target(db: Any, candidate: ScanCandidate, scan_id: str) -> str:
    """Write the day-trade target row that the executor consumes.

    Returns the target_id. Idempotent against (scan_id, symbol)
    via a unique compound index — duplicate calls in the same
    scan are silently coalesced by the upsert.
    """
    target_id = str(uuid4())
    now = datetime.now(timezone.utc)
    # Hard EOD close: 21:00 UTC = 5pm ET (EDT) / 4pm ET (EST). Same
    # cadence the existing "post-close warm" job uses. Crypto also
    # honors this — we deliberately want a hard intraday discipline.
    eod = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if eod <= now:
        eod = eod + timedelta(days=1)
    doc = {
        "target_id": target_id,
        "scan_id": scan_id,
        "asset_class": candidate.asset_class,
        "symbol": candidate.symbol,
        "direction": candidate.direction,
        "score": candidate.score,
        "confidence_raw": candidate.confidence_raw,
        "confidence_calibrated": candidate.confidence_calibrated,
        "prediction_id": candidate.prediction_id,
        "status": "pending",
        "max_hold_until": eod,
        "queued_at": now,
    }
    try:
        await db[TARGETS_COLLECTION].update_one(
            {"scan_id": scan_id, "symbol": candidate.symbol},
            {"$setOnInsert": doc},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[day-trade-scan] target write failed: %s", exc)
    return target_id


async def run_scan(db: Any, asset_class: AssetClass) -> ScanResult:
    """End-to-end orchestrator. Phases 1-4 strictly sequenced.

    Returns the ScanResult regardless of whether a candidate was
    chosen — the empty/all-blocked cases are first-class outcomes
    and still get an audit log entry.
    """
    scan_id = str(uuid4())
    started = datetime.now(timezone.utc)

    # Phase 1: scan (read-only).
    candidates = await scan_universe(db, asset_class)
    total_scanned = len(candidates)

    # Phase 2: rank.
    ranked = rank_candidates(candidates)

    # Phase 3: gates. Walk in rank order — collect ALL survivors so
    # Phase 4b can fall through to the next one if the executor
    # skips the top winner (e.g. SELL signal on unheld symbol on a
    # cash account). ``chosen`` stays = top survivor for back-compat
    # with the scan_log + ADL receipt fields.
    blocked = 0
    chosen: ScanCandidate | None = None
    survivors: list[ScanCandidate] = []
    for c in ranked:
        passed, blocker, gate_inputs = await apply_gates(db, c)
        c.gate_inputs = gate_inputs
        if not passed:
            c.gate_passed = False
            c.gate_blocker = blocker
            blocked += 1
        else:
            c.gate_passed = True
            c.gate_blocker = None
            survivors.append(c)
            if chosen is None:
                chosen = c

        # ── ADL-3: Shadow receipt (fire-and-forget) ──────────────
        # Every candidate gets one alpha_decision_log receipt
        # tagged ``source="day_trade"``. APPROVED candidates
        # (gate_passed=True) AND skipped/blocked candidates
        # (gate_passed=False) both produce a receipt. The shadow
        # pipeline runs OUT-OF-BAND; never mutates ``c`` or the
        # scan flow, never raises out. Pre-signal early returns
        # (``scan_universe`` returns []) skip the loop entirely so
        # no receipt is logged when no candidate exists.
        try:
            from services.ml.receipt_dispatch import (
                schedule_shadow_receipt,
            )
            _scanner_signal = {
                "symbol": c.symbol,
                "direction": c.direction,
                "confidence": float(c.score),
                "score": float(c.score),
                "rank": c.rank,
                "asset_class": c.asset_class,
                "gate_passed": c.gate_passed,
                "gate_blocker": c.gate_blocker,
                "prediction_id": c.prediction_id,
                "scan_id": scan_id,
                "source_layer": "day_trade_scanner",
            }
            schedule_shadow_receipt(
                db,
                signal=_scanner_signal,
                market_data=None,
                lane="equity",
                requested_notional_usd=0.0,
                source="day_trade",
            )
        except Exception as _adl_exc:  # noqa: BLE001
            logger.debug(
                "[day-trade-scan] ADL receipt schedule skipped "
                "for %s: %s",
                c.symbol, _adl_exc,
            )

    # Phase 4: write the target row for the top survivor (kept for
    # audit / back-compat). Then Phase 4b iterates ALL survivors in
    # rank order and routes the FIRST one the executor accepts. This
    # avoids losing a scan cycle when the top winner is unactionable
    # (e.g. STRONG_SELL on a symbol we don't hold — cash account
    # can't open short, so the executor correctly skips).
    if chosen is not None:
        await _write_target(db, chosen, scan_id)

        # 2026-06-26 — Phase 4b: actually fire the trade.
        if asset_class == "equity":
            executed = False
            for candidate in survivors:
                try:
                    from services.public_equity_live_executor import (
                        maybe_route_live as _public_route_live,
                    )
                    # 2026-08-11 — Cross-scanner execution-boundary lock.
                    # Alpha Day Trader and this scanner must not double-buy
                    # the same underlying move. Fails open on lock-service
                    # errors so a lock outage never suppresses trades.
                    try:
                        from services import alpha_hot_store
                        alpha_hot_store.init()
                        _lock_ok = alpha_hot_store.try_acquire_symbol_lock(
                            candidate.symbol,
                            setup_id=str(candidate.prediction_id or scan_id),
                            source="day_trade_scanner",
                            ttl_seconds=120,
                        )
                    except Exception:  # noqa: BLE001
                        _lock_ok = True
                    if not _lock_ok:
                        # Alpha (or another scanner) already holds it.
                        logger.info(
                            "[day_trade_scanner] symbol=%s skipped — exec lock held by another scanner",
                            candidate.symbol,
                        )
                        continue
                    _direction = (candidate.direction or "").upper()
                    _side = (
                        "BUY" if _direction in {
                            "BUY", "STRONG_BUY", "WEAK_BUY", "UP", "BULLISH",
                        }
                        else "SELL"
                    )
                    _intent = {
                        "symbol": candidate.symbol,
                        "direction": _side,
                        "confidence": float(candidate.score),
                        "raw_confidence": float(candidate.confidence_raw),
                        "calibrated_confidence": candidate.confidence_calibrated,
                        "strategy_id": candidate.strategy_id,
                        "source_signal": "alpha:day_trade_scanner:v1",
                        "scan_id": scan_id,
                        "prediction_id": candidate.prediction_id,
                    }
                    _row = await _public_route_live(db, intent=_intent)
                    # For the WINNER, transition the target row out
                    # of ``pending`` so future scans can re-evaluate.
                    # For non-winner candidates (those we tried via
                    # the fallback), no target row exists — skip the
                    # transition write.
                    _final_status = "routed" if _row else "executor_skipped"
                    if candidate is chosen:
                        try:
                            await db[TARGETS_COLLECTION].update_one(
                                {"scan_id": scan_id, "symbol": candidate.symbol},
                                {"$set": {
                                    "status": _final_status,
                                    "routed_at": datetime.now(timezone.utc),
                                    "broker_order_id": (_row or {}).get("broker_order_id"),
                                    "executor_returned_row": bool(_row),
                                }},
                            )
                        except Exception as _upd_exc:  # noqa: BLE001
                            logger.warning(
                                "[day_trade_scan] target status update "
                                "failed for %s: %s",
                                candidate.symbol, _upd_exc,
                            )
                    if _row:
                        logger.info(
                            "[day_trade_scan] EXECUTED %s %s conf=%.3f "
                            "broker_order_id=%s%s",
                            candidate.symbol, _side, candidate.score,
                            _row.get("broker_order_id"),
                            "" if candidate is chosen else " (fallback)",
                        )
                        executed = True
                        break  # Stop on first successful route.
                    else:
                        logger.info(
                            "[day_trade_scan] %s %s conf=%.3f — "
                            "executor returned None (try next)",
                            candidate.symbol, _side, candidate.score,
                        )
                except Exception as _exec_exc:  # noqa: BLE001
                    logger.warning(
                        "[day_trade_scan] Public.com route failed "
                        "for %s: %s", candidate.symbol, _exec_exc,
                    )
            if not executed and survivors:
                logger.info(
                    "[day_trade_scan] no actionable candidate this "
                    "scan — %d survivor(s) all skipped by executor",
                    len(survivors),
                )

    finished = datetime.now(timezone.utc)
    result = ScanResult(
        scan_id=scan_id,
        asset_class=asset_class,
        started_at=started,
        finished_at=finished,
        candidates=ranked,
        chosen=chosen,
        blocked_count=blocked,
        total_scanned=total_scanned,
    )
    await _write_scan_log(db, result)
    return result
