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
    Score = ``calibrated_confidence`` when stamped, else ``confidence``.
    """
    if db is None:
        return []
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    ).isoformat()
    pipe = [
        {"$match": {
            "feature": "paper_trading",
            "timestamp": {"$gte": since},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$symbol",
            "prediction_id": {"$first": "$prediction_id"},
            "direction": {"$first": "$direction"},
            "confidence": {"$first": "$confidence"},
            "calibrated_confidence": {"$first": "$calibrated_confidence"},
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

        # Score: calibrated when present, raw otherwise. Directional
        # adjustment for "down" predictions (raw confidence ==
        # P(up); for short signals true confidence is 1 - P(up)).
        # Skipped when calibrated since calibration already maps
        # raw confidence to actual win probability.
        direction = (r.get("direction") or "").upper()
        if cal_val is not None:
            score = cal_val
        elif direction in ("DOWN", "SHORT", "STRONG_SELL", "WEAK_SELL", "BEARISH"):
            score = 1.0 - raw_conf
        else:
            score = raw_conf

        out.append(ScanCandidate(
            symbol=sym,
            asset_class=asset_class,
            direction=direction,
            score=round(float(score), 4),
            confidence_raw=round(raw_conf, 4),
            confidence_calibrated=cal_val,
            prediction_id=r.get("prediction_id"),
            prediction_at=r.get("timestamp") or "",
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
        pending = await db[TARGETS_COLLECTION].count_documents({
            "symbol": candidate.symbol, "status": "pending",
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

    # Phase 3: gates. Walk in rank order, first survivor wins.
    blocked = 0
    chosen: ScanCandidate | None = None
    for c in ranked:
        passed, blocker, gate_inputs = await apply_gates(db, c)
        c.gate_inputs = gate_inputs
        if not passed:
            c.gate_passed = False
            c.gate_blocker = blocker
            blocked += 1
            continue
        c.gate_passed = True
        c.gate_blocker = None
        if chosen is None:
            chosen = c
        # Continue iterating so every candidate has its gate verdict
        # recorded in the scan log — but only ONE is chosen.

    # Phase 4: write the target (only if a winner exists).
    if chosen is not None:
        await _write_target(db, chosen, scan_id)

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
