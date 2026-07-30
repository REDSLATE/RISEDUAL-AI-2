"""Evidence Worker — nightly per-strategy performance attribution.

Reads closed live trades from ``equity_live_trades``, groups by
``strategy_id``, and computes attribution stats (hit rate, average
return, Sharpe-lite, expectancy). Writes one row per strategy into
``strategy_evidence_scores``. The live executor reads that
collection at fire-time and, when ``RISEDUAL_EVIDENCE_ENFORCE=1``,
uses the resulting ``notional_multiplier`` to scale exposure by
demonstrated edge.

Design notes
------------
- **Read-only against ``equity_live_trades``.** The worker never
  mutates the trade log.
- **Rolling window** (default 30d) so a strategy that used to work
  and no longer does gets its evidence score decayed to zero within
  a month, then to untested (0.25×) sizing once the window is
  entirely outside its live period.
- **Cheap Sharpe surrogate**: mean_return / stdev_return over the
  window (no risk-free-rate subtraction, no annualisation). This
  is a *signal-quality* score, not an investment metric.
- **Bucket mapping** is deliberate + auditable:

  ============  ================================  ================
  Bucket        Condition                           multiplier
  ============  ================================  ================
  ``proven``    trades >= min AND sharpe >= 1.0        1.00
  ``ok``        trades >= min AND sharpe >= 0.0        0.50
  ``losing``    trades >= min AND sharpe < 0.0         0.10
  ``untested``  trades < min                            0.25
  ============  ================================  ================

  Untested > losing: an unknown strategy is treated as more
  trustworthy than one that has demonstrably lost money, because
  it still might work; a losing one has cleared the min-trades
  bar and lost.
"""
from __future__ import annotations

import logging
import math
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _window_days() -> int:
    raw = (os.environ.get("RISEDUAL_EVIDENCE_WINDOW_DAYS") or "").strip()
    if not raw:
        return 30
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return 30
    return max(1, min(365, v))


def _min_trades() -> int:
    raw = (os.environ.get("RISEDUAL_EVIDENCE_MIN_TRADES") or "").strip()
    if not raw:
        return 5
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return 5
    return max(1, v)


def _untested_multiplier() -> float:
    raw = (os.environ.get("PUBLIC_LIVE_UNTESTED_NOTIONAL_MULT") or "").strip()
    if not raw:
        return 0.25
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.25
    return max(0.0, min(1.0, v))


def _bucket_multiplier(*, trades: int, sharpe: float, min_trades: int) -> tuple[str, float]:
    """Bucket + multiplier decision table. See module docstring."""
    if trades < min_trades:
        return "untested", _untested_multiplier()
    if sharpe >= 1.0:
        return "proven", 1.00
    if sharpe >= 0.0:
        return "ok", 0.50
    return "losing", 0.10


def _return_pct(entry: float, exit_: float) -> float | None:
    """Return-percent on a closed long trade. Guards against zero-entry."""
    try:
        e = float(entry)
        x = float(exit_)
    except (TypeError, ValueError):
        return None
    if e <= 0.0 or not math.isfinite(e) or not math.isfinite(x):
        return None
    return (x - e) / e


async def compute_evidence(db: Any) -> dict:
    """Aggregate per-strategy attribution and upsert into ``strategy_evidence_scores``.

    Returns a summary dict for the caller (scheduler / admin route).
    """
    if db is None:
        return {"error": "no_db", "strategies": [], "wrote": 0}

    window_days = _window_days()
    min_trades = _min_trades()
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    since_iso = since.isoformat()

    # Pull all closed live trades in the window. Only closed rows can
    # produce return_pct; open positions don't contribute yet.
    query = {
        "broker_id": "public",
        "status": "closed",
        "$or": [
            {"closed_at": {"$gte": since}},
            {"closed_at": {"$gte": since_iso}},
        ],
    }
    per_strat: dict[str, list[float]] = {}
    cursor = db.equity_live_trades.find(query, {
        "strategy_id": 1, "entry_price": 1, "close_price": 1,
        "exit_price": 1, "realized_pnl": 1,
    })
    async for t in cursor:
        strat = (t.get("strategy_id") or "unknown").strip() or "unknown"
        exit_price = t.get("close_price") or t.get("exit_price")
        if exit_price is None:
            continue
        ret = _return_pct(t.get("entry_price"), exit_price)
        if ret is None:
            continue
        per_strat.setdefault(strat, []).append(ret)

    started_at = datetime.now(timezone.utc)
    written = 0
    strategies: list[dict] = []
    for strat, rets in per_strat.items():
        n = len(rets)
        hits = sum(1 for r in rets if r > 0)
        hit_rate = hits / n if n > 0 else 0.0
        mean_ret = statistics.fmean(rets) if rets else 0.0
        stdev_ret = statistics.pstdev(rets) if n >= 2 else 0.0
        sharpe = (mean_ret / stdev_ret) if stdev_ret > 0 else (
            math.copysign(999.0, mean_ret) if mean_ret != 0 else 0.0
        )
        expectancy = mean_ret * n
        bucket, multiplier = _bucket_multiplier(
            trades=n, sharpe=sharpe, min_trades=min_trades,
        )
        doc = {
            "strategy_id": strat,
            "trade_count": n,
            "hit_rate": round(hit_rate, 4),
            "mean_return": round(mean_ret, 6),
            "stdev_return": round(stdev_ret, 6),
            "sharpe": round(sharpe, 4),
            "expectancy": round(expectancy, 6),
            "bucket": bucket,
            "notional_multiplier": multiplier,
            "window_days": window_days,
            "min_trades": min_trades,
            "computed_at": started_at,
        }
        try:
            await db.strategy_evidence_scores.update_one(
                {"strategy_id": strat},
                {"$set": doc},
                upsert=True,
            )
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[evidence_worker] upsert %s failed: %s", strat, exc)
        strategies.append(doc)

    finished_at = datetime.now(timezone.utc)
    summary = {
        "computed_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "window_days": window_days,
        "min_trades": min_trades,
        "strategies_evaluated": len(per_strat),
        "wrote": written,
        "strategies": strategies,
    }
    try:
        await db.strategy_evidence_runs.insert_one({
            "started_at": started_at,
            "finished_at": finished_at,
            "window_days": window_days,
            "min_trades": min_trades,
            "wrote": written,
            "strategies_evaluated": len(per_strat),
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[evidence_worker] run-log write failed: %s", exc)

    logger.info(
        "[evidence_worker] complete window=%dd strategies=%d wrote=%d",
        window_days, len(per_strat), written,
    )
    return summary


async def get_evidence_snapshot(db: Any) -> dict:
    """Return current evidence scores + last-run metadata for the UI."""
    if db is None:
        return {"strategies": [], "last_run": None}
    strategies: list[dict] = []
    async for row in db.strategy_evidence_scores.find({}, {"_id": 0}).sort("sharpe", -1):
        # Serialize datetime → ISO for JSON safety.
        if isinstance(row.get("computed_at"), datetime):
            row["computed_at"] = row["computed_at"].isoformat()
        strategies.append(row)
    last_run = await db.strategy_evidence_runs.find_one(
        {}, sort=[("finished_at", -1)], projection={"_id": 0},
    )
    if last_run:
        for k in ("started_at", "finished_at"):
            if isinstance(last_run.get(k), datetime):
                last_run[k] = last_run[k].isoformat()
    return {
        "strategies": strategies,
        "last_run": last_run,
        "enforcement_enabled": (os.environ.get("RISEDUAL_EVIDENCE_ENFORCE") or "").strip() in (
            "1", "true", "True", "yes", "on",
        ),
        "window_days": _window_days(),
        "min_trades_for_evidence": _min_trades(),
        "untested_multiplier": _untested_multiplier(),
    }
